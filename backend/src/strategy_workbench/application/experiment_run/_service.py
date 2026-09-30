from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from threading import RLock, Thread

from strategy_workbench.domain.analytics.facade.metrics import (
    EquityCurvePoint,
    MetricRegistry,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    AdmissionRejectionCode,
    BacktestRunSpec,
    BacktestRunState,
    RunFailureCode,
    RunStatus,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import bankrupt, preview_trial, trial_key
from strategy_workbench.domain.experiment.facade.design import (
    CellPlateau,
    ExperimentDesign,
    ExperimentKind,
    ExperimentNotFoundError,
    ExperimentStateError,
    ExperimentTrial,
    GridIndex,
    InvalidExperimentSpecError,
    SearchSpec,
    SplitSpec,
    WalkForwardGap,
    WalkForwardWindow,
    build_search_spec,
    cell_outcomes,
    experiment_trial_key,
    out_of_sample_sharpe,
    pick_window_cell,
    plateau_map,
    stitch_out_of_sample,
    walk_forward_gap,
    walk_forward_retention,
    window_gap,
)
from strategy_workbench.domain.experiment.facade.statistics import (
    CapacityLimit,
    capacity_amounts,
    capacity_limit,
    execution_costs,
    run_deflated_sharpe,
)
from strategy_workbench.domain.experiment.facade.trial import (
    ExperimentControls,
    ExperimentStatus,
    TrialStatus,
    awaiting_recovery,
    experiment_status,
    trial_status,
)
from strategy_workbench.domain.strategy.facade.specification import ParameterValue, StrategySpec

from .ports.outgoing.experiment_repository import (
    ExperimentRecord,
    ExperimentRepositoryPort,
    ExperimentSelection,
    TrialAttempt,
    WindowPick,
)
from .ports.outgoing.trial_runs import (
    AdmittedRun,
    TrialResultUnreadableError,
    TrialRunPort,
    TrialRunRejectedError,
)

logger = logging.getLogger(__name__)

# 제출 스레드가 워크포워드를 밀어 주는 간격(초). 창의 학습이 끝나면 늦어도 이만큼 뒤 검증 실행이
# 뜬다.
_POLL_SECONDS = 2.0
_SETTLED_RUNS = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


@dataclass(frozen=True)
class ExperimentRequest:
    """실험 만들기·미리 계산 요청."""

    # trial 마다 기간(창의 학습 구간)과 파라미터 값만 바꿔 실행할 기반 요청.
    run: BacktestRunSpec
    # parameter_id → 탐색 값. None 이면 정의가 허용하는 격자 값 전체다(`build_search_spec`).
    search: dict[str, list[ParameterValue] | None]
    split: SplitSpec


@dataclass(frozen=True)
class CapacitySweepRequest:
    """용량 스윕 만들기·미리 계산 요청(V4-04). 기반 요청을 초기 자본만 바꿔 전체 구간으로 돌린다."""

    run: BacktestRunSpec
    # 돌릴 초기 자본(원). 서로 다른 양수 `MIN_CAPACITY_AMOUNTS`~`MAX_CAPACITY_AMOUNTS` 개다.
    initial_cash: list[float]


@dataclass(frozen=True)
class ExperimentPreview:
    """시작 전 미리 계산 — 무엇을 몇 번 돌리고 계열 시도 수 N 이 얼마가 되나(spec D2)."""

    design: ExperimentDesign
    combination_count: int
    run_count: int
    lineage_id: str
    trial_count: int
    # 결과가 나오면 계열에 새로 드는 시도 수. 한 칸의 창들은 한 시도라 새 칸 수다
    # (`experiment_trial_key`).
    new_trial_count: int
    trial_count_after: int


@dataclass(frozen=True)
class ExperimentTrialState:
    trial: ExperimentTrial
    # 최신 attempt 에서 파생한다. attempt 가 없으면 대기(실험을 취소했으면 취소)다.
    status: TrialStatus
    attempts: tuple[TrialAttempt, ...]
    # 최신 실행이 재시작으로 중단돼 복구가 다시 넘기기를 기다린다(`awaiting_recovery`).
    awaiting_recovery: bool
    # 최신 실행이 파산으로 끝났다(`bankrupt`) — 비전략 실패와 달리 전략의 결과다.
    bankrupt: bool = False


@dataclass(frozen=True)
class Experiment:
    record: ExperimentRecord
    status: ExperimentStatus
    # trial 상태별 수. 대기열 화면이 셈을 다시 하지 않게 싣는다.
    trial_counts: dict[TrialStatus, int]
    selections: tuple[ExperimentSelection, ...]


@dataclass(frozen=True)
class WalkForwardWindowResult:
    """창 하나의 자동 선택과 그 검증 실행 결과."""

    pick: WindowPick
    # 검증 실행 상태·실패 코드. 실행이 없으면(고를 칸 없음, 접수 거절 — 거절 코드는
    # `pick.error_code`) None 이다.
    run_status: RunStatus | None
    run_error_code: RunFailureCode | None
    # 이 창이 곡선에 들지 못하는 이유. 검증 실행이 완료됐으면 None.
    gap: WalkForwardGap | None


@dataclass(frozen=True)
class WalkForwardReport:
    """워크포워드 결과(V3-05). 창마다 자동으로 고른 칸과 검증 구간만 이어 붙인 곡선·유지율이다.

    곡선·표본 밖 샤프·유지율은 모든 창의 검증 실행이 완료돼야 채워지고, 아니면 비우고 `gap` 에
    이유를 싣는다 — 실패한 창을 빼고 남은 창만 이으면 낙관 쪽으로 빠진다.
    """

    windows: tuple[WalkForwardWindowResult, ...]
    curve: tuple[EquityCurvePoint, ...]
    # 이어 붙인 곡선의 세션 단위(연율화 전) 샤프 — 학습 점수(대표 샤프)와 같은 단위다.
    out_of_sample_sharpe: float | None
    retention: float | None
    gap: WalkForwardGap | None


@dataclass(frozen=True)
class ParameterMap:
    """파라미터 지도(V4-03). 칸 판정 규칙은 `domain/experiment/_plateau.py` 다."""

    # 그리드 칸 좌표 순.
    cells: tuple[CellPlateau, ...]


@dataclass(frozen=True)
class CapacityPoint:
    """용량 스윕 금액 하나의 결과."""

    trial_index: int
    initial_cash: float
    status: TrialStatus
    # 원장에 적힌 전체 구간 세션 샤프(비용 후, 연율화 전). 완료되지 않았으면 None.
    sharpe: float | None
    # 가격 충격(슬리피지 포함) ÷ 체결 금액(bp), 리밸런스 주문 수량 대비 그 세션 미체결 비율
    # (`ExecutionCosts`). 완료되지 않았거나 결과 파일을 읽을 수 없거나 체결·주문이 없으면 None.
    impact_cost_bps: float | None
    session_unfilled_ratio: float | None
    # 체결 수량 ÷ 체결한 날의 유동성 캡 기준 거래량(거래량 가중 평균). 기준 거래량이 없는 옛 결과면
    # None.
    participation_rate: float | None


@dataclass(frozen=True)
class CapacityReport:
    """용량 스윕 결과(V4-04). 한계 금액 규칙은 `domain/experiment/_capacity.py` 다."""

    points: tuple[CapacityPoint, ...]
    # 도는 금액·취소된 금액이 있으면 확정하지 않고 이유(`gap`)만 싣는다.
    limit: CapacityLimit


@dataclass(frozen=True)
class ExperimentPage:
    items: tuple[Experiment, ...]
    # 다음 쪽을 물을 때 `after` 로 넘길 값. 마지막 쪽이면 None.
    next_after: str | None


def _spawn(work: Callable[[], None]) -> None:
    Thread(target=work, name="experiment-submit", daemon=True).start()


class ExperimentRunService:
    """실험을 설계·저장하고 trial 을 `TrialRunPort` 로 넘긴다(spec D5·D6).

    trial 은 만든 순서대로 실행 서비스에 넘기기만 하고, 슬롯·배정 순서·일시정지·우선순위는 실행
    서비스의 대기열이 정한다. 재시작하면 `recover` 가 끝나지 않은 실험의 남은 trial 을 다시 넘긴다.
    """

    def __init__(
        self,
        repository: ExperimentRepositoryPort,
        runs: TrialRunPort,
        *,
        metric_registry: MetricRegistry,
        new_id: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        spawn: Callable[[Callable[[], None]], None] = _spawn,
        poll_seconds: float | None = _POLL_SECONDS,
    ) -> None:
        """`poll_seconds` 는 제출 스레드가 워크포워드를 한 걸음씩(`advance`) 밀어 주는 간격이다.
        None 이면 밀지 않는다(테스트가 직접 부른다)."""
        self._repository = repository
        self._runs = runs
        self._registry = metric_registry
        self._poll_seconds = poll_seconds
        self._new_id = new_id
        self._now = now
        self._spawn = spawn
        # 제출·취소·재시도를 한 줄로 세워 취소 뒤에 새 실행이 생기지 않게 한다.
        self._lock = RLock()
        self._cancelled: set[str] = set()

    def preview(self, request: ExperimentRequest) -> ExperimentPreview:
        """제출과 같은 시도 키(`experiment_trial_key`)에 원장 규칙(`preview_trial`)을 쓴다."""
        return self._preview(*self._design(request))

    def preview_capacity(self, request: CapacitySweepRequest) -> ExperimentPreview:
        """용량 스윕의 미리 계산. 기반 시도가 원장에 있어야 해서(`_capacity_design`) 새 시도 수는
        늘 0 이다."""
        return self._preview(*self._capacity_design(request))

    def _preview(self, admitted: AdmittedRun, design: ExperimentDesign) -> ExperimentPreview:
        ledger = admitted.ledger
        trials = design.trials()
        keys = {experiment_trial_key(admitted.run, trial) for trial in trials}
        new_trials = sum(preview_trial(ledger, key).new_trial for key in keys)
        return ExperimentPreview(
            design=design,
            combination_count=len(design.search.indices()),
            run_count=len(trials),
            lineage_id=ledger.lineage_id,
            trial_count=ledger.trial_count,
            new_trial_count=new_trials,
            trial_count_after=ledger.trial_count + new_trials,
        )

    def create(self, request: ExperimentRequest) -> Experiment:
        _, design = self._design(request)
        return self._create(request.run, request.split, design)

    def create_capacity(self, request: CapacitySweepRequest) -> Experiment:
        """용량 스윕을 만들고 금액마다 기반 실행 구간 전체를 대기열에 넘긴다."""
        _, design = self._capacity_design(request)
        return self._create(request.run, None, design)

    def _create(
        self, run: BacktestRunSpec, split: SplitSpec | None, design: ExperimentDesign
    ) -> Experiment:
        record = ExperimentRecord(
            experiment_id=self._new_id(),
            created_at=self._now(),
            run=run,
            split=split,
            design=design,
        )
        self._repository.add(record)
        self._spawn(lambda: self._submit(record))
        return self.get(record.experiment_id)

    def get(self, experiment_id: str) -> Experiment:
        return self._experiment(self._repository.get(experiment_id))

    def list(self, *, after: str | None, limit: int) -> ExperimentPage:
        """최근에 만든 순. `after` 는 앞 쪽의 `next_after` 다."""
        records = self._repository.list(after=after, limit=limit + 1)
        return ExperimentPage(
            items=tuple(self._experiment(record) for record in records[:limit]),
            next_after=records[limit - 1].experiment_id if len(records) > limit else None,
        )

    def trials(self, experiment_id: str) -> tuple[ExperimentTrialState, ...]:
        return self._trial_states(self._repository.get(experiment_id))

    def cancel(self, experiment_id: str) -> Experiment:
        """아직 넘기지 않은 trial 은 넘기지 않고, 넘긴 실행은 취소를 요청한다. 되돌릴 수 없다."""
        with self._lock:
            record = self._repository.get(experiment_id)
            if record.cancelled_at is None:
                self._repository.cancel(experiment_id, cancelled_at=self._now())
                self._cancelled.add(experiment_id)
                for run in (
                    *self._repository.attempts(experiment_id),
                    *self._repository.picks(experiment_id),
                ):
                    if run.run_id is not None:
                        self._runs.cancel(run.run_id, owner=experiment_id)
        return self.get(experiment_id)

    def retry(self, experiment_id: str, trial_index: int) -> ExperimentTrialState:
        """실패·취소로 끝난 trial 을 새 attempt 로 다시 넘긴다. 앞 attempt 는 그대로 남는다.

        Raises:
            TrialRunRejectedError: 기반 요청이 이제 접수되지 않는다(attempt 를 남기지 않는다).
        """
        with self._lock:
            record = self._repository.get(experiment_id)
            state = self._trial_state(record, trial_index)
            if record.cancelled_at is not None or state.status not in (
                TrialStatus.FAILED,
                TrialStatus.CANCELLED,
            ):
                raise ExperimentStateError(
                    "experiment.trial.not_retryable",
                    "실패하거나 취소된 trial 만 다시 실행할 수 있고 취소한 실험은 다시 실행하지 "
                    f"않습니다: experiment_id={experiment_id} trial_index={trial_index} "
                    f"status={state.status} experiment_cancelled={record.cancelled_at is not None}",
                )
            base = self._runs.admit(record.run).run
            self._start(record, base, state.trial, attempt=len(state.attempts) + 1)
            return self._trial_state(record, trial_index)

    def select(self, experiment_id: str, trial_index: int, reason: str) -> ExperimentSelection:
        """끝난 trial 을 후보로 고른 기록을 남긴다(spec D9).

        고를 때의 계열 N·원장 대표 샤프·DSR 을 함께 적는다(V4-02, `run_deflated_sharpe`). 실험이
        끝나기 전(대기·도는 trial 이 있다)에는 거절한다 — 설계한 조합이 다 돌기 전의 N 으로 스냅숏이
        굳지 않게 한다. 취소한 실험은 끝났다(돌지 않은 조합은 시도가 아니다).
        """
        record = self._repository.get(experiment_id)
        _require_kind(record, ExperimentKind.PARAMETER_SEARCH)
        state = self._trial_state(record, trial_index)
        if state.status is not TrialStatus.COMPLETED:
            raise ExperimentStateError(
                "experiment.selection.not_completed",
                "완료된 trial 만 후보로 고를 수 있습니다: "
                f"experiment_id={experiment_id} trial_index={trial_index} status={state.status}",
            )
        unfinished = sum(
            not (other.status.is_terminal and not other.awaiting_recovery)
            for other in self._trial_states(record)
        )
        if unfinished and record.cancelled_at is None:
            raise ExperimentStateError(
                "experiment.selection.not_finished",
                "실험의 trial 이 모두 끝난 뒤에 후보를 고를 수 있습니다: "
                f"experiment_id={experiment_id} unfinished_trials={unfinished}",
            )
        source = _base_source(record.run)
        run_id = state.attempts[-1].run_id or ""
        ledger = self._runs.trial_ledger(record.run)
        trial = next((t for t in ledger.trials if any(r.run_id == run_id for r in t.runs)), None)
        selection = ExperimentSelection(
            experiment_id=experiment_id,
            trial_index=trial_index,
            strategy_id=source.strategy_id,
            revision=source.revision,
            parameter_values=state.trial.parameter_values,
            reason=reason,
            selected_at=self._now(),
            trial_count=ledger.trial_count,
            ledger_representative_sharpe=None if trial is None else trial.representative_sharpe,
            deflated_sharpe=run_deflated_sharpe(self._runs.result(run_id), ledger),
        )
        self._repository.add_selection(selection)
        return selection

    def _design(self, request: ExperimentRequest) -> tuple[AdmittedRun, ExperimentDesign]:
        run = request.run
        _base_source(run)
        if run.metric_windows:
            raise InvalidExperimentSpecError(
                "experiment.base.invalid",
                "실험의 측정 창은 분할 규칙이 정합니다. 기반 실행 요청에서 지표 창을 빼세요: "
                f"metric_windows={len(run.metric_windows)}",
            )
        admitted, strategy, environment = self._base(run)
        return admitted, ExperimentDesign(
            search=build_search_spec(strategy.parameters, request.search),
            parameter_values=admitted.run.parameter_values,
            windows=request.split.measured_windows(
                environment.start,
                environment.end,
                self._runs.sessions(environment.start, environment.end),
            ),
            measured=True,
        )

    def _capacity_design(
        self, request: CapacitySweepRequest
    ) -> tuple[AdmittedRun, ExperimentDesign]:
        """기반 요청을 실험 기반 규칙(`_base`)으로 검사하고 금액 목록을 편다. 창·탐색 축은 없다.

        기반 시도가 계열 원장에 결과를 낸 시도(COUNTED)여야 한다 — 스윕 실행은 초기 자본만 달라 늘
        그 시도의 재확인이라 N·대표 샤프가 바뀌지 않는다. 없으면 먼저 한 번 실행하라고 거절한다.
        """
        amounts = capacity_amounts(request.initial_cash)
        admitted, _strategy, _environment = self._base(request.run)
        base = preview_trial(admitted.ledger, trial_key(admitted.run))
        if base.new_trial:
            raise InvalidExperimentSpecError(
                "experiment.capacity.base_not_run",
                "용량 확인은 이 설정으로 돌린 백테스트 결과가 있어야 합니다. 먼저 이 설정으로 "
                "백테스트를 한 번 실행하세요: "
                f"lineage_id={base.lineage_id} trial_key={base.trial_key}",
            )
        return admitted, ExperimentDesign(
            search=SearchSpec(()),
            parameter_values=admitted.run.parameter_values,
            windows=(),
            kind=ExperimentKind.CAPACITY_SWEEP,
            initial_cash=amounts,
        )

    def _base(self, run: BacktestRunSpec) -> tuple[AdmittedRun, StrategySpec, RunEnvironment]:
        """실험 기반 요청을 검사한다 — 저장 리비전만 되고(spec D5) 실행 시작과 같은 접수 판정을
        탄다."""
        source = _base_source(run)
        admitted = self._runs.admit(run)
        strategy, environment = admitted.run.strategy, admitted.run.environment
        if strategy is None or environment is None:  # pragma: no cover - 접수 판정이 채운다
            raise RuntimeError(f"admitted experiment base run is unresolved — source={source}")
        return admitted, strategy, environment

    def control(
        self, experiment_id: str, *, paused: bool | None = None, priority: int | None = None
    ) -> Experiment:
        """실험을 일시정지·재개하거나 우선순위를 바꾼다. 주지 않은 칸은 그대로다. 도는 trial 은
        끝까지 돈다."""
        with self._lock:
            current = self._repository.get(experiment_id).controls
            controls = ExperimentControls(
                current.paused if paused is None else paused,
                current.priority if priority is None else priority,
            )
            self._repository.set_controls(experiment_id, controls)
            self._runs.schedule(experiment_id, paused=controls.paused, priority=controls.priority)
        return self.get(experiment_id)

    def recover(self) -> None:
        """재시작 뒤 취소하지 않은 실험의 대기열 조작을 되살리고 남은 trial 을 다시 넘긴다(spec D6).

        남은 trial 은 넘기지 못한 trial(제출이 예상 밖 오류로 멈춘 경우 포함)과 재시작으로 중단된
        trial 이다. 끝난 실험은 조작만 되살리고(뒤의 재시도가 따른다) 제출 스레드는 띄우지 않는다.
        저장된 실험 하나를 읽지 못하면(지금 모델로 디코드되지 않는다) 그 실험만 로그로 건너뛰고
        부팅은 이어 간다.
        """
        for experiment_id in self._repository.open_ids():
            try:
                record = self._repository.get(experiment_id)
                controls = record.controls
                self._runs.schedule(
                    experiment_id, paused=controls.paused, priority=controls.priority
                )
                if self._experiment(record).status is ExperimentStatus.COMPLETED:
                    continue
            except Exception:
                logger.exception("experiment skipped on recovery — experiment_id=%s", experiment_id)
                continue
            self._spawn(lambda record=record: self._submit(record))

    def _submit(self, record: ExperimentRecord) -> None:
        """attempt 가 없거나 실행이 재시작으로 중단된 trial 을 전개 순서대로 넘긴다.

        기반 요청 재검사가 거절되면 남은 trial 마다 거절 attempt(코드·문장)를 남긴다 — trial 은
        실패로 보이고 실험은 끝난다.
        """
        try:
            pending = [
                state
                for state in self._trial_states(record)
                if not state.attempts or state.awaiting_recovery
            ]
            base = self._admitted(record) if pending else None
            for state in pending:
                with self._lock:
                    if record.experiment_id in self._cancelled:
                        return
                    # 제출하는 동안 사용자가 재시도로 새 attempt 를 넘겼으면 건너뛴다(번호가 겹치면
                    # 고아 run 이 생긴다).
                    attempts = sum(
                        attempt.trial_index == state.trial.index
                        for attempt in self._repository.attempts(record.experiment_id)
                    )
                    if base is not None and attempts == len(state.attempts):
                        self._start(record, base, state.trial, attempt=attempts + 1)
            while self._poll_seconds is not None and not self.advance(record.experiment_id):
                time.sleep(self._poll_seconds)
        except Exception:
            # 남은 trial 은 다음 재시작의 `recover` 가 다시 넘긴다.
            logger.exception(
                "experiment trial submission stopped — experiment_id=%s", record.experiment_id
            )

    def advance(self, experiment_id: str) -> bool:
        """학습이 끝난 창마다 칸을 골라 검증 실행을 넘긴다(V3-05). 모든 창을 골랐으면 참이다 —
        그 뒤 검증 실행이 중단되는 경우는 재시작뿐이라 `recover` 의 제출이 다시 민다.

        고른 칸의 검증 실행은 그 칸의 시도 키로 원장에 적어 재확인이 된다 — 창은 N 을 늘리지
        않는다(V3-03 P2-1). 재시작으로 중단된 검증 실행은 같은 칸으로 다시 넘긴다. 기반 요청
        재검사가 거절되면 그 창들의 선택에 거절 코드를 남긴다. V3-05 이전 설계의 실험은 돌리지
        않는다. 용량 스윕도 창이 없어 돌리지 않는다(`ExperimentDesign.walks_forward`).
        """
        record = self._repository.get(experiment_id)
        if record.cancelled_at is not None or not record.design.walks_forward:
            return True
        states = self._trial_states(record)
        open_windows, _ = self._open_windows(record, states)
        # 원장 읽기는 락 밖에서 한다 — 락은 모든 실험의 취소·재시도·조작이 함께 쓴다.
        scores = self._train_scores(record) if any(p is None for _i, _s, p in open_windows) else {}
        chosen = [
            (index, pick, self._choose(record, window_states, scores) if pick is None else pick)
            for index, window_states, pick in open_windows
        ]
        with self._lock:
            if chosen and self._repository.get(experiment_id).cancelled_at is None:
                latest = _latest_by_window(self._repository.picks(experiment_id))
                base = self._admitted(record)
                for index, pick, choice in chosen:
                    if latest.get(index) == pick:  # 다른 제출이 그사이 고르지 않았다
                        self._pick(record, base, index, choice, pick)
        return all(state.status.is_terminal and not state.awaiting_recovery for state in states)

    def walk_forward(self, experiment_id: str) -> WalkForwardReport:
        """창별 선택·검증 실행 결과와, 모든 창의 검증 실행이 완료됐으면 이어 붙인 곡선·표본 밖
        샤프·유지율(규칙은 `domain/experiment/_walk_forward.py`)."""
        record = self._repository.get(experiment_id)
        _require_kind(record, ExperimentKind.PARAMETER_SEARCH)
        if not record.design.measured:
            return WalkForwardReport((), (), None, None, WalkForwardGap.LEGACY_DESIGN)
        picks = tuple(_latest_by_window(self._repository.picks(experiment_id)).values())
        runs = self._runs.states({pick.run_id for pick in picks if pick.run_id is not None})
        windows = tuple(_window_result(pick, runs) for pick in picks)
        missing = [WalkForwardGap.PENDING] * (len(record.design.windows) - len(picks))
        gap = walk_forward_gap(
            [window.gap for window in windows] + missing, cancelled=record.cancelled_at is not None
        )
        if gap is not None:
            return WalkForwardReport(windows, (), None, None, gap)
        try:
            segments = [self._runs.result(pick.run_id or "").series.equity for pick in picks]
        except TrialResultUnreadableError:
            logger.exception("walk-forward result unreadable — experiment_id=%s", experiment_id)
            return WalkForwardReport(windows, (), None, None, WalkForwardGap.RESULT_UNREADABLE)
        curve = stitch_out_of_sample(segments)
        oos = out_of_sample_sharpe(curve, self._registry, record.run.annualization_days)
        train = [pick.train_sharpe for pick in picks if pick.train_sharpe is not None]
        return WalkForwardReport(windows, curve, oos, walk_forward_retention(oos, train), None)

    def parameter_map(self, experiment_id: str) -> ParameterMap:
        """그리드 칸마다 추천·봉우리·실패 판정과 점수·고원 점수·민감도(V4-03, 규칙은
        `domain/experiment/_plateau.py`). 칸 점수는 창별 학습 점수(원장 세션 샤프)의 평균이고,
        끝나지 않은 칸은 점수 없음이다 — 도는 실험도 그때까지의 지도를 준다."""
        record = self._repository.get(experiment_id)
        _require_kind(record, ExperimentKind.PARAMETER_SEARCH)
        states = self._trial_states(record)
        scores = self._train_scores(record)
        trials = (
            (
                state.trial.grid_index,
                state.status,
                state.awaiting_recovery,
                state.bankrupt,
                scores.get(state.attempts[-1].run_id or "") if state.attempts else None,
            )
            for state in states
        )
        return ParameterMap(plateau_map(record.design.search, cell_outcomes(trials)))

    def capacity(self, experiment_id: str) -> CapacityReport:
        """용량 스윕 금액별 비용 후 샤프·가격 충격·미체결 비율과, 모든 금액이 끝났으면 한계 금액
        (규칙은 `domain/experiment/_capacity.py`). 샤프는 원장에서, 체결 비용은 결과 파일에서
        읽는다."""
        record = self._repository.get(experiment_id)
        _require_kind(record, ExperimentKind.CAPACITY_SWEEP)
        states = self._trial_states(record)
        scores = self._train_scores(record)
        points = tuple(
            self._capacity_point(amount, state, scores)
            for amount, state in zip(record.design.initial_cash, states, strict=True)
        )
        limit = capacity_limit(
            ((point.initial_cash, point.sharpe) for point in points),
            pending=any(
                not state.status.is_terminal or state.awaiting_recovery for state in states
            ),
            cancelled=any(state.status is TrialStatus.CANCELLED for state in states),
        )
        return CapacityReport(points, limit)

    def _capacity_point(
        self, amount: float, state: ExperimentTrialState, scores: Mapping[str, float | None]
    ) -> CapacityPoint:
        latest = state.attempts[-1].run_id if state.attempts else None
        run_id = latest if state.status is TrialStatus.COMPLETED else None
        costs = None
        if run_id is not None:
            try:
                costs = execution_costs(self._runs.result(run_id).artifacts)
            except TrialResultUnreadableError:
                logger.exception("capacity result unreadable — run_id=%s", run_id)
        return CapacityPoint(
            trial_index=state.trial.index,
            initial_cash=amount,
            status=state.status,
            sharpe=None if run_id is None else scores.get(run_id),
            impact_cost_bps=None if costs is None else costs.impact_cost_bps,
            session_unfilled_ratio=None if costs is None else costs.session_unfilled_ratio,
            participation_rate=None if costs is None else costs.participation_rate,
        )

    def _open_windows(
        self, record: ExperimentRecord, states: tuple[ExperimentTrialState, ...]
    ) -> tuple[list[tuple[int, list[ExperimentTrialState], WindowPick | None]], bool]:
        """학습이 끝났는데 검증 실행을 넘길 창(아직 안 골랐거나 검증 실행이 중단됐다)과,
        넘긴 검증 실행 중 끝나지 않은 것이 있는지."""
        picks = _latest_by_window(self._repository.picks(record.experiment_id))
        runs = self._runs.states({pick.run_id for pick in picks.values() if pick.run_id})
        count = len(record.design.windows)
        open_windows: list[tuple[int, list[ExperimentTrialState], WindowPick | None]] = []
        waiting = False
        for index in range(count):
            window_states = list(states[index::count])
            if not all(s.status.is_terminal and not s.awaiting_recovery for s in window_states):
                continue
            pick = picks.get(index)
            run = runs.get(pick.run_id) if pick is not None and pick.run_id else None
            if pick is None or awaiting_recovery(run):
                open_windows.append((index, window_states, pick))
            elif run is not None and run.status not in _SETTLED_RUNS:
                waiting = True
        return open_windows, waiting

    def _train_scores(self, record: ExperimentRecord) -> dict[str, float | None]:
        """실행마다 원장에 적힌 전체 구간 세션 샤프(학습 점수). 파산한 실행은 값이 없어 점수 없는
        칸이다."""
        ledger = self._runs.trial_ledger(record.run)
        return {run.run_id: run.session_sharpe for trial in ledger.trials for run in trial.runs}

    def _choose(
        self,
        record: ExperimentRecord,
        window_states: list[ExperimentTrialState],
        scores: Mapping[str, float | None],
    ) -> _Choice:
        """창의 학습 trial 가운데 학습 점수로 칸을 고른다(`pick_window_cell`). 원장에 점수가 없는
        실행(샤프가 비었거나 원장 기록이 실패했다)은 점수 없는 칸이다."""
        cells: dict[GridIndex, float] = {}
        for state in window_states:
            run_id = state.attempts[-1].run_id if state.attempts else None
            sharpe = None if run_id is None else scores.get(run_id)
            if state.status is TrialStatus.COMPLETED and sharpe is not None:
                cells[state.trial.grid_index] = sharpe
        if record.split is None:  # pragma: no cover - 워크포워드는 분할이 있는 파라미터 탐색만 돈다
            raise RuntimeError(
                f"walk-forward without a split — experiment_id={record.experiment_id}"
            )
        cell = pick_window_cell(cells, record.design.search.shape, record.split.selection_rule)
        if cell is None:
            return _Choice(None, None)
        chosen = next(state for state in window_states if state.trial.grid_index == cell)
        return _Choice(chosen.trial.index, cells[cell])

    def _pick(
        self,
        record: ExperimentRecord,
        base: BacktestRunSpec | TrialRunRejectedError,
        window_index: int,
        choice: _Choice | WindowPick,
        previous: WindowPick | None,
    ) -> None:
        """`choice` 는 이번에 고른 칸이거나 검증 실행이 중단된 앞 선택이다(같은 칸으로 다시)."""
        run_id = error_code = error = None
        if choice.trial_index is not None:
            trial = record.design.trials()[choice.trial_index]
            window = _window(trial)
            run_id, error_code, error = self._started(
                record, base, trial, window.test_start, window.test_end
            )
        self._repository.add_pick(
            WindowPick(
                experiment_id=record.experiment_id,
                window_index=window_index,
                attempt=1 if previous is None else previous.attempt + 1,
                trial_index=choice.trial_index,
                train_sharpe=choice.train_sharpe,
                created_at=self._now(),
                run_id=run_id,
                error_code=error_code,
                error=error,
            )
        )

    def _start(
        self,
        record: ExperimentRecord,
        base: BacktestRunSpec | TrialRunRejectedError,
        trial: ExperimentTrial,
        *,
        attempt: int,
    ) -> None:
        start, end = _train_range(record.run, trial)
        run_id, error_code, error = self._started(record, base, trial, start, end)
        self._repository.add_attempt(
            TrialAttempt(
                experiment_id=record.experiment_id,
                trial_index=trial.index,
                attempt=attempt,
                created_at=self._now(),
                run_id=run_id,
                error_code=error_code,
                error=error,
            )
        )

    def _admitted(self, record: ExperimentRecord) -> BacktestRunSpec | TrialRunRejectedError:
        """실행 서비스가 해소한 기반 실행 spec(시도 키를 낸다). 이제 접수되지 않으면 그 거절."""
        try:
            return self._runs.admit(record.run).run
        except TrialRunRejectedError as rejected:
            return rejected

    def _started(
        self,
        record: ExperimentRecord,
        base: BacktestRunSpec | TrialRunRejectedError,
        trial: ExperimentTrial,
        start: date,
        end: date,
    ) -> tuple[str | None, AdmissionRejectionCode | None, str | None]:
        """칸의 한 구간 실행을 넘긴다. (run_id, 거절 코드, 거절 문장) 가운데 run_id 나 거절 하나."""
        if isinstance(base, TrialRunRejectedError):
            return None, base.code, str(base)
        try:
            run_id = self._runs.start(
                _run_request(record.run, trial, start, end),
                trial_key=experiment_trial_key(base, trial),
                owner=record.experiment_id,
            )
        except TrialRunRejectedError as rejected:
            return None, rejected.code, str(rejected)
        return run_id, None, None

    def _experiment(self, record: ExperimentRecord) -> Experiment:
        states = self._trial_states(record)
        statuses = [state.status for state in states]
        return Experiment(
            record=record,
            status=experiment_status(
                statuses,
                cancelled=record.cancelled_at is not None,
                paused=record.controls.paused,
                pending=any(state.awaiting_recovery for state in states)
                or self._walk_forward_pending(record, states),
            ),
            trial_counts=dict(Counter(statuses)),
            selections=self._repository.selections(record.experiment_id),
        )

    def _walk_forward_pending(
        self, record: ExperimentRecord, states: tuple[ExperimentTrialState, ...]
    ) -> bool:
        """학습이 끝난 창의 검증 실행을 아직 넘기지 않았거나 그 실행이 끝나지 않았다."""
        if not record.design.walks_forward:
            return False
        open_windows, waiting = self._open_windows(record, states)
        return waiting or any(
            pick is not None or any(s.status is TrialStatus.COMPLETED for s in window_states)
            for _index, window_states, pick in open_windows
        )

    def _trial_states(self, record: ExperimentRecord) -> tuple[ExperimentTrialState, ...]:
        attempts: dict[int, list[TrialAttempt]] = {}
        for attempt in self._repository.attempts(record.experiment_id):
            attempts.setdefault(attempt.trial_index, []).append(attempt)
        runs = self._runs.states(
            {group[-1].run_id for group in attempts.values() if group[-1].run_id is not None}
        )
        cancelled = record.cancelled_at is not None
        return tuple(
            _state(trial, tuple(attempts.get(trial.index, ())), cancelled, runs)
            for trial in record.design.trials()
        )

    def _trial_state(self, record: ExperimentRecord, trial_index: int) -> ExperimentTrialState:
        states = self._trial_states(record)
        if not 0 <= trial_index < len(states):
            raise ExperimentNotFoundError(
                "experiment.trial.not_found",
                "실험에 그 trial 이 없습니다: "
                f"experiment_id={record.experiment_id} trial_index={trial_index} "
                f"trials={len(states)}",
            )
        return states[trial_index]


def _state(
    trial: ExperimentTrial,
    attempts: tuple[TrialAttempt, ...],
    cancelled: bool,
    runs: Mapping[str, BacktestRunState],
) -> ExperimentTrialState:
    latest = attempts[-1] if attempts else None
    run = None if latest is None or latest.run_id is None else runs[latest.run_id]
    status = trial_status(
        None if run is None else run.status,
        attempted=latest is not None,
        experiment_cancelled=cancelled,
    )
    return ExperimentTrialState(
        trial,
        status,
        attempts,
        awaiting_recovery(run),
        run is not None and bankrupt(run.status, run.error_code),
    )


def _base_source(run: BacktestRunSpec) -> SavedRevisionReference:
    """실험 기반은 저장한 리비전뿐이다(spec D5). 인라인 초안·요청 안 전략은 거절한다.

    출처를 둘 다 실은 요청은 실행 접수 검사(`TrialRunPort.admit`)가 거절한다.
    """
    source = run.strategy_source
    if not isinstance(source, SavedRevisionReference):
        raise InvalidExperimentSpecError(
            "experiment.base.unsaved",
            "실험은 저장한 전략 리비전으로만 만들 수 있습니다. 전략을 저장한 뒤 그 리비전으로 "
            f"만드세요: strategy_source={getattr(source, 'kind', None)} "
            f"inline_strategy={run.strategy is not None}",
        )
    return source


def _run_request(
    base: BacktestRunSpec, trial: ExperimentTrial, start: date, end: date
) -> BacktestRunSpec:
    """칸의 해소된 파라미터 값으로 한 구간을 돌리는 실행 요청 — 학습(창의 학습 구간, 엠바고를 뺀
    측정 끝까지) 또는 검증(창의 검증 구간). 지표 워밍업은 실행이 구간 앞 관측을 읽어 채우고
    성과는 구간 시작부터 잰다."""
    environment = base.environment
    if environment is None:  # pragma: no cover - `_design` 이 검사한 요청만 저장한다
        raise RuntimeError(f"experiment base run has no run environment — trial={trial.index}")
    return replace(
        base,
        environment=replace(environment, start=start, end=end),
        parameter_values=trial.parameter_values,
        initial_cash=base.initial_cash if trial.initial_cash is None else trial.initial_cash,
    )


def _train_range(base: BacktestRunSpec, trial: ExperimentTrial) -> tuple[date, date]:
    """trial 이 도는 구간 — 창의 학습 구간(엠바고를 뺀 측정 끝까지), 용량 스윕은 기반 실행 구간."""
    if trial.window is not None:
        return trial.window.train_start, trial.window.train_end
    if base.environment is None:  # pragma: no cover - `_base` 가 검사한 요청만 저장한다
        raise RuntimeError(f"experiment base run has no run environment — trial={trial.index}")
    return base.environment.start, base.environment.end


def _window(trial: ExperimentTrial) -> WalkForwardWindow:
    if trial.window is None:  # pragma: no cover - 워크포워드는 창이 있는 파라미터 탐색만 돈다
        raise RuntimeError(f"walk-forward trial has no window — trial={trial.index}")
    return trial.window


def _require_kind(record: ExperimentRecord, kind: ExperimentKind) -> None:
    """워크포워드·후보 선택은 파라미터 탐색에만, 용량 결과는 용량 스윕에만 뜻이 있다."""
    if record.design.kind is not kind:
        raise ExperimentStateError(
            "experiment.kind.mismatch",
            f"이 실험 종류에서는 할 수 없는 요청입니다: experiment_id={record.experiment_id} "
            f"kind={record.design.kind} required={kind}",
        )


@dataclass(frozen=True)
class _Choice:
    """창에서 고른 칸의 학습 trial 과 그 대표 샤프. 고를 칸이 없으면 둘 다 None."""

    trial_index: int | None
    train_sharpe: float | None


def _window_result(
    pick: WindowPick, runs: Mapping[str, BacktestRunState]
) -> WalkForwardWindowResult:
    run = runs.get(pick.run_id) if pick.run_id is not None else None
    return WalkForwardWindowResult(
        pick=pick,
        run_status=None if run is None else run.status,
        run_error_code=None if run is None else run.error_code,
        gap=window_gap(pick.trial_index is not None, run),
    )


def _latest_by_window(picks: tuple[WindowPick, ...]) -> dict[int, WindowPick]:
    """창마다 마지막 attempt 의 선택(저장소가 창·attempt 순으로 준다)."""
    return {pick.window_index: pick for pick in picks}
