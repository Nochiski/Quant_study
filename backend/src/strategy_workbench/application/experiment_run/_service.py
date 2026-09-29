from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from threading import RLock, Thread

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    EquityCurvePoint,
    MetricRegistry,
    compute_analytics,
    session_sharpe,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    BacktestRunState,
    RunStatus,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import preview_trial, representative_sharpe
from strategy_workbench.domain.experiment.facade.design import (
    ExperimentDesign,
    ExperimentNotFoundError,
    ExperimentStateError,
    ExperimentTrial,
    GridIndex,
    InvalidExperimentSpecError,
    SplitSpec,
    build_search_spec,
    experiment_trial_key,
    pick_window_cell,
    stitch_out_of_sample,
    walk_forward_retention,
)
from strategy_workbench.domain.experiment.facade.trial import (
    ExperimentControls,
    ExperimentStatus,
    TrialStatus,
    awaiting_recovery,
    experiment_status,
    trial_status,
)
from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from .ports.outgoing.experiment_repository import (
    ExperimentRecord,
    ExperimentRepositoryPort,
    ExperimentSelection,
    TrialAttempt,
    WindowPick,
)
from .ports.outgoing.trial_runs import AdmittedRun, TrialRunPort, TrialRunRejectedError

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


@dataclass(frozen=True)
class Experiment:
    record: ExperimentRecord
    status: ExperimentStatus
    # trial 상태별 수. 대기열 화면이 셈을 다시 하지 않게 싣는다.
    trial_counts: dict[TrialStatus, int]
    selections: tuple[ExperimentSelection, ...]


@dataclass(frozen=True)
class WalkForwardReport:
    """워크포워드 결과(V3-05). 창마다 자동으로 고른 칸과 검증 구간만 이어 붙인 곡선·유지율이다.

    곡선·표본 밖 샤프·유지율은 모든 창의 검증 실행이 끝나야 채워진다.
    """

    picks: tuple[WindowPick, ...]
    curve: tuple[EquityCurvePoint, ...]
    # 이어 붙인 곡선의 세션 단위(연율화 전) 샤프 — 학습 점수(대표 샤프)와 같은 단위다.
    out_of_sample_sharpe: float | None
    retention: float | None


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
        admitted, design = self._design(request)
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
        record = ExperimentRecord(
            experiment_id=self._new_id(),
            created_at=self._now(),
            run=request.run,
            split=request.split,
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
        """실패·취소로 끝난 trial 을 새 attempt 로 다시 넘긴다. 앞 attempt 는 그대로 남는다."""
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
        """끝난 trial 을 후보로 고른 기록을 남긴다(spec D9)."""
        record = self._repository.get(experiment_id)
        state = self._trial_state(record, trial_index)
        if state.status is not TrialStatus.COMPLETED:
            raise ExperimentStateError(
                "experiment.selection.not_completed",
                "완료된 trial 만 후보로 고를 수 있습니다: "
                f"experiment_id={experiment_id} trial_index={trial_index} status={state.status}",
            )
        source = _base_source(record.run)
        selection = ExperimentSelection(
            experiment_id=experiment_id,
            trial_index=trial_index,
            strategy_id=source.strategy_id,
            revision=source.revision,
            parameter_values=state.trial.parameter_values,
            reason=reason,
            selected_at=self._now(),
        )
        self._repository.add_selection(selection)
        return selection

    def _design(self, request: ExperimentRequest) -> tuple[AdmittedRun, ExperimentDesign]:
        run = request.run
        source = _base_source(run)
        if run.metric_windows:
            raise InvalidExperimentSpecError(
                "experiment.base.invalid",
                "실험의 측정 창은 분할 규칙이 정합니다. 기반 실행 요청에서 지표 창을 빼세요: "
                f"metric_windows={len(run.metric_windows)}",
            )
        admitted = self._runs.admit(run)
        strategy, environment = admitted.run.strategy, admitted.run.environment
        if strategy is None or environment is None:  # pragma: no cover - 접수 판정이 채운다
            raise RuntimeError(f"admitted experiment base run is unresolved — source={source}")
        return admitted, ExperimentDesign(
            search=build_search_spec(strategy.parameters, request.search),
            parameter_values=admitted.run.parameter_values,
            windows=request.split.measured_windows(
                environment.start,
                environment.end,
                self._runs.sessions(environment.start, environment.end),
            ),
        )

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
        trial 이다. 끝난 실험은 넘길 trial 이 없다.
        """
        after: str | None = None
        while records := self._repository.list(after=after, limit=100):
            for record in records:
                if record.cancelled_at is None:
                    controls = record.controls
                    self._runs.schedule(
                        record.experiment_id, paused=controls.paused, priority=controls.priority
                    )
                    self._spawn(lambda record=record: self._submit(record))
            after = records[-1].experiment_id

    def _submit(self, record: ExperimentRecord) -> None:
        """attempt 가 없거나 실행이 재시작으로 중단된 trial 을 전개 순서대로 넘긴다."""
        try:
            pending = [
                state
                for state in self._trial_states(record)
                if not state.attempts or state.awaiting_recovery
            ]
            base = self._runs.admit(record.run).run if pending else None
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
        않는다(V3-03 P2-1). 재시작으로 중단된 검증 실행은 같은 칸으로 다시 넘긴다.
        """
        with self._lock:
            record = self._repository.get(experiment_id)
            if record.cancelled_at is not None:
                return True
            states = self._trial_states(record)
            open_windows, _ = self._open_windows(record, states)
            base = self._runs.admit(record.run).run if open_windows else None
            for index, window_states, pick in open_windows:
                if pick is None:
                    trial_index, sharpe = self._choose(record, window_states)
                else:
                    trial_index, sharpe = pick.trial_index, pick.train_sharpe
                self._pick(record, base, index, trial_index, sharpe, pick)
            return all(state.status.is_terminal and not state.awaiting_recovery for state in states)

    def walk_forward(self, experiment_id: str) -> WalkForwardReport:
        """창별 선택과, 모든 창의 검증 실행이 끝났으면 이어 붙인 곡선·표본 밖 샤프·유지율.

        표본 밖 샤프는 곡선을 지표 레지스트리(`compute_analytics`)로 잰 샤프를 세션 단위로 바꾼
        값이다.
        """
        record = self._repository.get(experiment_id)
        picks = tuple(_latest_by_window(self._repository.picks(experiment_id)).values())
        runs = self._runs.states({pick.run_id for pick in picks if pick.run_id is not None})
        if len(picks) < len(record.design.windows) or not all(
            pick.run_id is not None and runs[pick.run_id].status is RunStatus.COMPLETED
            for pick in picks
        ):
            return WalkForwardReport(picks, (), None, None)
        curve = stitch_out_of_sample(
            [self._runs.result(pick.run_id or "").series.equity for pick in picks],
            record.run.initial_cash,
        )
        environment = record.run.environment
        if environment is None:  # pragma: no cover - `_design` 이 검사한 요청만 저장한다
            raise RuntimeError(f"experiment base run has no run environment — id={experiment_id}")
        # 곡선의 기준점은 첫 검증 창 바로 앞 세션이다(구간 지표의 경계일 규칙, #274).
        first_test = record.design.windows[0].test_start
        base = self._runs.sessions(environment.start, first_test - timedelta(days=1))[-1]
        report = compute_analytics(
            AnalyticsInput(
                points=tuple(AnalysisPoint(p.session, p.equity, 0.0, 0.0) for p in curve),
                traded_notional=0.0,
                base=AnalysisPoint(base, 1.0, 0.0, 0.0),
            ),
            self._registry,
            annualization_days=record.run.annualization_days,
        )
        sharpe = next(m.value for m in report.metrics if m.metric_id == "sharpe")
        oos = None if sharpe is None else session_sharpe(sharpe, record.run.annualization_days)
        train = [pick.train_sharpe for pick in picks if pick.train_sharpe is not None]
        return WalkForwardReport(picks, curve, oos, walk_forward_retention(oos, train))

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

    def _choose(
        self, record: ExperimentRecord, window_states: list[ExperimentTrialState]
    ) -> tuple[int | None, float | None]:
        """창의 학습 trial 가운데 대표 샤프로 칸을 고른다(`pick_window_cell`)."""
        scores: dict[GridIndex, float] = {}
        for state in window_states:
            run_id = state.attempts[-1].run_id if state.attempts else None
            if state.status is TrialStatus.COMPLETED and run_id is not None:
                sharpe = representative_sharpe(self._runs.result(run_id))
                if sharpe is not None:
                    scores[state.trial.grid_index] = sharpe
        cell = pick_window_cell(scores, record.design.search.shape, record.split.selection_rule)
        if cell is None:
            return None, None
        chosen = next(state for state in window_states if state.trial.grid_index == cell)
        return chosen.trial.index, scores[cell]

    def _pick(
        self,
        record: ExperimentRecord,
        base: BacktestRunSpec | None,
        window_index: int,
        trial_index: int | None,
        train_sharpe: float | None,
        previous: WindowPick | None,
    ) -> None:
        run_id: str | None = None
        error_code: str | None = None
        error: str | None = None
        if trial_index is not None and base is not None:
            trial = record.design.trials()[trial_index]
            window = trial.window
            try:
                run_id = self._runs.start(
                    _run_request(record.run, trial, window.test_start, window.test_end),
                    trial_key=experiment_trial_key(base, trial),
                    owner=record.experiment_id,
                )
            except TrialRunRejectedError as rejected:
                error_code, error = rejected.code, str(rejected)
        self._repository.add_pick(
            WindowPick(
                experiment_id=record.experiment_id,
                window_index=window_index,
                attempt=1 if previous is None else previous.attempt + 1,
                trial_index=trial_index,
                train_sharpe=train_sharpe,
                created_at=self._now(),
                run_id=run_id,
                error_code=error_code,
                error=error,
            )
        )

    def _start(
        self,
        record: ExperimentRecord,
        base: BacktestRunSpec,
        trial: ExperimentTrial,
        *,
        attempt: int,
    ) -> None:
        """`base` 는 실행 서비스가 해소한 기반 실행 spec 이다(시도 키를 낸다)."""
        run_id: str | None = None
        error_code: str | None = None
        error: str | None = None
        try:
            run_id = self._runs.start(
                _run_request(record.run, trial, trial.window.train_start, trial.window.train_end),
                trial_key=experiment_trial_key(base, trial),
                owner=record.experiment_id,
            )
        except TrialRunRejectedError as rejected:
            error_code, error = rejected.code, str(rejected)
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
    return ExperimentTrialState(trial, status, attempts, awaiting_recovery(run))


def _base_source(run: BacktestRunSpec) -> SavedRevisionReference:
    """실험 기반은 저장한 리비전뿐이다(spec D5). 인라인 초안·요청 안 전략은 거절한다.

    출처를 둘 다 실은 요청은 실행 접수 검사(`TrialRunPort.validate`)가 거절한다.
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
    )


def _latest_by_window(picks: tuple[WindowPick, ...]) -> dict[int, WindowPick]:
    """창마다 마지막 attempt 의 선택(저장소가 창·attempt 순으로 준다)."""
    return {pick.window_index: pick for pick in picks}
