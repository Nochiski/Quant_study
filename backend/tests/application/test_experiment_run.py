"""실험 유스케이스(V3-03): 기반 리비전·trial 전개·attempt·상태 파생·취소·선택(spec D5·D6·D9).

trial 실행은 가짜 `TrialRunPort` 로 받는다 — 유스케이스가 실행 서비스를 직접 부르지 않고 포트로만
넘기는지, 실행 상태만으로 trial 상태가 정해지는지를 본다. 저장소는 실제 research DB adapter 다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteExperimentRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.experiment_run.facade.experiments import (
    ExperimentRequest,
    ExperimentRunService,
)
from strategy_workbench.application.experiment_run.facade.ports import TrialRunRejectedError
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    InlineDraft,
    MetricWindow,
    RunStatus,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import (
    TrialLedger,
    TrialLedgerEntry,
    summarize_trial_ledger,
    trial_key,
)
from strategy_workbench.domain.experiment.facade.design import (
    ExperimentNotFoundError,
    ExperimentStateError,
    InvalidExperimentSpecError,
    SplitMode,
    SplitSpec,
)
from strategy_workbench.domain.experiment.facade.trial import ExperimentStatus, TrialStatus
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    ParameterValue,
    StrategySpec,
)

_AT = datetime(2026, 9, 30, tzinfo=UTC)
_STRATEGY = replace(
    StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "unused").template(),
    parameters=(
        FloatParameter("scale", 1.0, -1.0, 1.0, kind="float", step=1.0),
        ChoiceParameter("mode", "a", ("a", "b"), kind="choice"),
    ),
)
_BASE = BacktestRunSpec(
    strategy_source=SavedRevisionReference("s-1", 3, "a" * 64, "saved_revision"),
    environment=RunEnvironment(
        start=date(2021, 1, 4), end=date(2023, 6, 30), universe_id="krx.common-stock"
    ),
    parameter_values={"mode": "b"},
)
_ROLLING = SplitSpec(mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=0)


class _FakeRuns:
    """`TrialRunPort` 가짜. 접수한 요청을 순서대로 적고 실행 상태는 테스트가 정한다."""

    def __init__(self) -> None:
        self.started: list[BacktestRunSpec] = []
        self.statuses: dict[str, RunStatus] = {}
        self.cancelled: list[str] = []
        self.ledger_entries: list[TrialLedgerEntry] = []
        self.reject = False

    def validate(self, request: BacktestRunSpec) -> None:
        if request.environment is None:
            raise AssertionError("base run without environment reached the port")

    def strategy(self, source: SavedRevisionReference) -> StrategySpec:
        return _STRATEGY

    def trial_ledger(self, lineage_id: str) -> TrialLedger:
        return summarize_trial_ledger(lineage_id, (), self.ledger_entries, ())

    def start(self, request: BacktestRunSpec) -> str:
        if self.reject:
            raise TrialRunRejectedError("strategy exceeds engine capabilities — core=rust")
        run_id = f"run-{len(self.started)}"
        self.started.append(request)
        self.statuses[run_id] = RunStatus.QUEUED
        return run_id

    def status(self, run_id: str) -> RunStatus:
        return self.statuses[run_id]

    def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


def _service(
    runs: _FakeRuns,
    *,
    repository: SQLiteExperimentRepository | None = None,
    spawn: Callable[[Callable[[], None]], None] = lambda work: work(),
) -> ExperimentRunService:
    ids = iter(f"exp-{number}" for number in range(100))
    return ExperimentRunService(
        repository or SQLiteExperimentRepository(),
        runs,
        new_id=lambda: next(ids),
        now=lambda: _AT,
        spawn=spawn,
    )


def _request(
    run: BacktestRunSpec = _BASE,
    split: SplitSpec = _ROLLING,
    **search: list[ParameterValue] | None,
) -> ExperimentRequest:
    return ExperimentRequest(run=run, search=dict(search) or {"scale": [1.0, -1.0]}, split=split)


@pytest.mark.parametrize(
    ("run", "code"),
    [
        (replace(_BASE, strategy_source=None, strategy=_STRATEGY), "experiment.base.unsaved"),
        (
            replace(_BASE, strategy_source=InlineDraft(_STRATEGY, "inline_draft")),
            "experiment.base.unsaved",
        ),
        (
            replace(
                _BASE,
                metric_windows=(
                    MetricWindow(MetricScope.WINDOW, date(2021, 2, 1), date(2021, 3, 1)),
                ),
            ),
            "experiment.base.invalid",
        ),
    ],
)
def test_only_a_saved_revision_without_metric_windows_can_be_the_base(
    run: BacktestRunSpec, code: str
) -> None:
    runs = _FakeRuns()
    service = _service(runs)

    for call in (service.preview, service.create):
        with pytest.raises(InvalidExperimentSpecError) as raised:
            call(_request(run))
        assert raised.value.code == code
    assert runs.started == []


def test_trials_are_submitted_in_expansion_order_with_the_window_and_resolved_values() -> None:
    runs = _FakeRuns()

    experiment = _service(runs).create(_request())

    # 칸 두 개(scale -1, 1) × 롤링 창 두 개. 실행 구간은 창의 학습 구간이고 탐색하지 않은
    # 파라미터(mode)는 기반 요청 값이다.
    assert [
        (
            request.environment and (request.environment.start, request.environment.end),
            request.parameter_values,
        )
        for request in runs.started
    ] == [
        ((date(2021, 1, 4), date(2022, 1, 3)), {"scale": -1.0, "mode": "b"}),
        ((date(2022, 1, 4), date(2023, 1, 3)), {"scale": -1.0, "mode": "b"}),
        ((date(2021, 1, 4), date(2022, 1, 3)), {"scale": 1.0, "mode": "b"}),
        ((date(2022, 1, 4), date(2023, 1, 3)), {"scale": 1.0, "mode": "b"}),
    ]
    assert all(request.strategy_source == _BASE.strategy_source for request in runs.started)
    assert experiment.status is ExperimentStatus.QUEUED


def test_trial_status_is_derived_from_the_latest_attempt_run() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id

    runs.statuses.update(
        {
            "run-0": RunStatus.COMPLETED,
            "run-1": RunStatus.CANCEL_REQUESTED,
            "run-2": RunStatus.FAILED,
        }
    )

    assert [state.status for state in service.trials(experiment_id)] == [
        TrialStatus.COMPLETED,
        TrialStatus.RUNNING,
        TrialStatus.FAILED,
        TrialStatus.QUEUED,
    ]
    assert service.get(experiment_id).status is ExperimentStatus.RUNNING


def test_a_failed_trial_is_kept_and_a_retry_is_a_new_attempt() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id
    runs.statuses["run-1"] = RunStatus.FAILED

    retried = service.retry(experiment_id, 1)

    assert [(attempt.attempt, attempt.run_id) for attempt in retried.attempts] == [
        (1, "run-1"),
        (2, "run-4"),
    ]
    assert retried.status is TrialStatus.QUEUED
    assert runs.started[4] == runs.started[1]
    with pytest.raises(ExperimentStateError) as raised:
        service.retry(experiment_id, 1)
    assert raised.value.code == "experiment.trial.not_retryable"
    with pytest.raises(ExperimentNotFoundError) as missing:
        service.retry(experiment_id, 4)
    assert missing.value.code == "experiment.trial.not_found"


def test_a_rejected_submission_is_a_failed_attempt_with_the_reason() -> None:
    runs = _FakeRuns()
    runs.reject = True
    service = _service(runs)

    experiment_id = service.create(_request()).record.experiment_id
    runs.reject = False
    retried = service.retry(experiment_id, 0)

    first = retried.attempts[0]
    assert (first.run_id, first.error) == (None, "strategy exceeds engine capabilities — core=rust")
    assert (retried.attempts[1].run_id, retried.status) == ("run-0", TrialStatus.QUEUED)
    assert [state.status for state in service.trials(experiment_id)][1:] == [TrialStatus.FAILED] * 3


def test_cancel_stops_submission_and_cancels_submitted_runs() -> None:
    runs = _FakeRuns()
    pending: list[Callable[[], None]] = []
    service = _service(runs, spawn=pending.append)
    experiment_id = service.create(_request()).record.experiment_id
    assert [state.status for state in service.trials(experiment_id)] == [TrialStatus.QUEUED] * 4

    cancelled = service.cancel(experiment_id)
    pending[0]()

    assert runs.started == []
    assert cancelled.status is ExperimentStatus.CANCELLED
    assert [state.status for state in service.trials(experiment_id)] == [TrialStatus.CANCELLED] * 4


def test_cancel_requests_cancellation_of_every_submitted_run_once() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id

    first = service.cancel(experiment_id)
    second = service.cancel(experiment_id)

    assert runs.cancelled == ["run-0", "run-1", "run-2", "run-3"]
    assert first.record.cancelled_at == second.record.cancelled_at == _AT
    with pytest.raises(ExperimentStateError):
        service.retry(experiment_id, 0)


def test_only_a_completed_trial_can_be_selected_and_the_record_stays() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id

    with pytest.raises(ExperimentStateError) as raised:
        service.select(experiment_id, 2, "이웃 평균 샤프가 가장 높다")
    runs.statuses["run-2"] = RunStatus.COMPLETED
    selection = service.select(experiment_id, 2, "이웃 평균 샤프가 가장 높다")

    assert raised.value.code == "experiment.selection.not_completed"
    assert (selection.strategy_id, selection.revision, selection.parameter_values) == (
        "s-1",
        3,
        {"scale": 1.0, "mode": "b"},
    )
    assert service.get(experiment_id).selections == (selection,)


def test_the_preview_counts_new_trial_keys_once_against_the_ledger() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    anchored = replace(_ROLLING, mode=SplitMode.ANCHORED)

    before = service.preview(_request(split=anchored))
    service.create(_request(split=anchored))
    runs.ledger_entries = [
        TrialLedgerEntry(
            run_id,
            trial_key(replace(request, strategy=_STRATEGY)),
            RunStatus.COMPLETED,
            _AT,
            _AT,
        )
        for run_id, request in zip(runs.statuses, runs.started, strict=True)
    ]
    after = service.preview(_request(split=anchored))

    # 앵커드 창은 시작일이 같아 한 칸의 두 창이 한 시도다(종료일은 시도 키 밖). 4 실행 → 2 시도.
    assert (before.combination_count, before.run_count) == (2, 4)
    assert (before.trial_count, before.new_trial_count, before.trial_count_after) == (0, 2, 2)
    assert (after.trial_count, after.new_trial_count, after.trial_count_after) == (2, 0, 2)
    rolling = service.preview(_request())
    assert (rolling.new_trial_count, rolling.trial_count_after) == (2, 4)


def test_an_experiment_survives_reopening_the_research_database(tmp_path: Path) -> None:
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    created = _service(runs, repository=SQLiteExperimentRepository(path)).create(_request())
    runs.statuses["run-0"] = RunStatus.FAILED

    reopened = _service(runs, repository=SQLiteExperimentRepository(path))

    assert reopened.get(created.record.experiment_id).record == created.record
    assert [state.status for state in reopened.trials(created.record.experiment_id)] == [
        TrialStatus.FAILED,
        TrialStatus.QUEUED,
        TrialStatus.QUEUED,
        TrialStatus.QUEUED,
    ]
    with pytest.raises(ExperimentNotFoundError) as missing:
        reopened.get("missing")
    assert missing.value.code == "experiment.not_found"
