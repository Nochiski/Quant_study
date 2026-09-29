from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock, Thread

from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import preview_trial
from strategy_workbench.domain.experiment.facade.design import (
    ExperimentDesign,
    ExperimentNotFoundError,
    ExperimentStateError,
    ExperimentTrial,
    InvalidExperimentSpecError,
    SplitSpec,
    build_search_spec,
    experiment_trial_key,
)
from strategy_workbench.domain.experiment.facade.trial import (
    ExperimentStatus,
    TrialStatus,
    experiment_status,
    trial_status,
)
from strategy_workbench.domain.strategy.facade.specification import ParameterValue

from .ports.outgoing.experiment_repository import (
    ExperimentRecord,
    ExperimentRepositoryPort,
    ExperimentSelection,
    TrialAttempt,
)
from .ports.outgoing.trial_runs import AdmittedRun, TrialRunPort, TrialRunRejectedError

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class Experiment:
    record: ExperimentRecord
    status: ExperimentStatus
    selections: tuple[ExperimentSelection, ...]


def _spawn(work: Callable[[], None]) -> None:
    Thread(target=work, name="experiment-submit", daemon=True).start()


class ExperimentRunService:
    """실험을 설계·저장하고 trial 을 `TrialRunPort` 로 넘긴다(spec D5·D6).

    trial 은 만든 순서대로 실행 서비스에 넘기기만 하고, 슬롯·순서는 실행 서비스의 대기열이 정한다.
    실험 사이 공정 분배·재시작 복구·일시정지는 V3-04 가 이 제출을 스케줄러로 옮기며 더한다.
    """

    def __init__(
        self,
        repository: ExperimentRepositoryPort,
        runs: TrialRunPort,
        *,
        new_id: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        spawn: Callable[[Callable[[], None]], None] = _spawn,
    ) -> None:
        self._repository = repository
        self._runs = runs
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
        record = self._repository.get(experiment_id)
        states = self._trial_states(record)
        return Experiment(
            record=record,
            status=experiment_status(
                [state.status for state in states], cancelled=record.cancelled_at is not None
            ),
            selections=self._repository.selections(experiment_id),
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
                for attempt in self._repository.attempts(experiment_id):
                    if attempt.run_id is not None:
                        self._runs.cancel(attempt.run_id)
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
            windows=request.split.windows(environment.start, environment.end),
        )

    def _submit(self, record: ExperimentRecord) -> None:
        try:
            base = self._runs.admit(record.run).run
            for trial in record.design.trials():
                with self._lock:
                    if record.experiment_id in self._cancelled:
                        return
                    self._start(record, base, trial, attempt=1)
        except Exception:
            # 남은 trial 은 대기로 남는다(재시작 복구와 함께 V3-04 가 다시 넘긴다).
            logger.exception(
                "experiment trial submission stopped — experiment_id=%s", record.experiment_id
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
                _trial_run(record.run, trial), trial_key=experiment_trial_key(base, trial)
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

    def _trial_states(self, record: ExperimentRecord) -> tuple[ExperimentTrialState, ...]:
        attempts: dict[int, list[TrialAttempt]] = {}
        for attempt in self._repository.attempts(record.experiment_id):
            attempts.setdefault(attempt.trial_index, []).append(attempt)
        cancelled = record.cancelled_at is not None
        return tuple(
            _state(trial, tuple(attempts.get(trial.index, ())), cancelled, self._runs)
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
    trial: ExperimentTrial, attempts: tuple[TrialAttempt, ...], cancelled: bool, runs: TrialRunPort
) -> ExperimentTrialState:
    latest = attempts[-1] if attempts else None
    run_status = None if latest is None or latest.run_id is None else runs.status(latest.run_id)
    status = trial_status(run_status, attempted=latest is not None, experiment_cancelled=cancelled)
    return ExperimentTrialState(trial, status, attempts)


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


def _trial_run(base: BacktestRunSpec, trial: ExperimentTrial) -> BacktestRunSpec:
    """trial 의 실행 요청 — 창의 학습 구간을 해소된 파라미터 값으로 돌린다.

    엠바고를 뺀 학습 측정 끝과 검증 창 실행은 워크포워드 실행(V3-05)이 붙인다.
    """
    environment = base.environment
    if environment is None:  # pragma: no cover - `_design` 이 검사한 요청만 저장한다
        raise RuntimeError(f"experiment base run has no run environment — trial={trial.index}")
    return replace(
        base,
        environment=replace(
            environment, start=trial.window.train_start, end=trial.window.train_end
        ),
        parameter_values=trial.parameter_values,
    )
