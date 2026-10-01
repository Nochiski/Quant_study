"""실험 유스케이스(V3-03): 기반 리비전·trial 전개·attempt·상태 파생·취소·선택(spec D5·D6·D9).

trial 실행은 가짜 `TrialRunPort` 로 받는다 — 유스케이스가 실행 서비스를 직접 부르지 않고 포트로만
넘기는지, 실행 상태만으로 trial 상태가 정해지는지를 본다. 저장소는 실제 research DB adapter 다.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections.abc import Callable, Collection
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteExperimentRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.experiment_run.facade.experiments import (
    CapacitySweepRequest,
    ExperimentRequest,
    ExperimentRunService,
)
from strategy_workbench.application.experiment_run.facade.ports import (
    AdmittedRun,
    RunSlotUsage,
    TrialResultUnreadableError,
    TrialRunRejectedError,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import (
    EquityCurvePoint,
    MetricScope,
    MetricValue,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestRunState,
    InlineDraft,
    MetricWindow,
    RawArtifactBundle,
    RawFill,
    RawOrder,
    RawRounding,
    RunFailureCode,
    RunStatus,
    SavedRevisionReference,
)
from strategy_workbench.domain.backtest.facade.trials import (
    TrialLedger,
    TrialLedgerEntry,
    representative_sharpe,
    summarize_trial_ledger,
    trial_key,
)
from strategy_workbench.domain.experiment.facade.design import (
    CellVerdict,
    ExperimentKind,
    ExperimentNotFoundError,
    ExperimentStateError,
    InvalidExperimentSpecError,
    SplitMode,
    SplitSpec,
    WalkForwardGap,
    WindowSelectionRule,
)
from strategy_workbench.domain.experiment.facade.statistics import CapacityGap, CapacityLimit
from strategy_workbench.domain.experiment.facade.trial import (
    DEFAULT_EXPERIMENT_CONTROLS,
    ExperimentControls,
    ExperimentStatus,
    TrialStatus,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    ParameterValue,
)

from ..assistant_result_samples import sample_backtest_result

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
_SAMPLE = sample_backtest_result()


def _result(
    sharpe: float | None = None,
    equity: tuple[tuple[date, float], ...] = (),
    standard_error: float | None = None,
) -> BacktestRunResult:
    """완료 결과. `sharpe`·`standard_error` 는 세션 단위라 실행 지표처럼 연율화(√252)해 싣는다."""
    metrics = tuple(
        MetricValue(metric_id, value * math.sqrt(252), MetricScope.FULL, 10)
        for metric_id, value in (("sharpe", sharpe), ("sharpe_standard_error", standard_error))
        if value is not None
    )
    return replace(
        _SAMPLE,
        metrics=metrics,
        series=replace(
            _SAMPLE.series,
            equity=tuple(EquityCurvePoint(session, value, None) for session, value in equity),
        ),
    )


class _FakeRuns:
    """`TrialRunPort` 가짜. 접수한 요청과 시도 키를 순서대로 적고 실행 상태는 테스트가 정한다.

    `admit` 은 실행 서비스처럼 전략과 파라미터 값을 해소한 기반 spec 을 돌려준다.
    """

    def __init__(self) -> None:
        self.started: list[BacktestRunSpec] = []
        self.keys: list[str] = []
        self.run_statuses: dict[str, RunStatus] = {}
        self.cancelled: list[tuple[str, str]] = []
        self.owners: set[str] = set()
        self.schedules: list[tuple[str, bool, int]] = []
        self.error_codes: dict[str, RunFailureCode] = {}
        # 다음 `admit` 앞에서 한 번 부른다(복구 제출과 겹치는 조작을 재현한다).
        self.before_admit: Callable[[], None] | None = None
        self.ledger_entries: list[TrialLedgerEntry] = []
        self.reject = False
        self.results: dict[str, BacktestRunResult] = {}
        # 결과 파일을 읽을 수 없는 실행.
        self.unreadable: set[str] = set()
        # `trial_ledger` 를 읽을 때마다 부른다(락 밖에서 읽는지 본다).
        self.on_ledger: Callable[[], None] | None = None

    def admit(self, request: BacktestRunSpec) -> AdmittedRun:
        if self.before_admit is not None:
            hook, self.before_admit = self.before_admit, None
            hook()
        resolved = replace(
            request,
            strategy=_STRATEGY,
            parameter_values={"scale": 1.0, "mode": "a", **request.parameter_values},
        )
        return AdmittedRun(resolved, summarize_trial_ledger("s-1", (), self.ledger_entries, ()))

    def trial_ledger(self, request: BacktestRunSpec) -> TrialLedger:
        if self.on_ledger is not None:
            self.on_ledger()
        return summarize_trial_ledger("s-1", (), self.ledger_entries, ())

    def start(self, request: BacktestRunSpec, *, trial_key: str, owner: str) -> str:
        self.owners.add(owner)
        if self.reject:
            raise TrialRunRejectedError(
                "backtest.run.invalid", "strategy exceeds engine capabilities — core=rust"
            )
        run_id = f"run-{len(self.started)}"
        self.started.append(request)
        self.keys.append(trial_key)
        self.run_statuses[run_id] = RunStatus.QUEUED
        return run_id

    def complete_all(self) -> None:
        """접수한 실행이 모두 결과를 냈다고 원장에 적는다(실행 서비스가 하는 일)."""
        for run_id, key in zip(self.run_statuses, self.keys, strict=True):
            self.run_statuses[run_id] = RunStatus.COMPLETED
            self.ledger_entries.append(TrialLedgerEntry(run_id, key, RunStatus.COMPLETED, _AT, _AT))

    def result(self, run_id: str) -> BacktestRunResult:
        if run_id in self.unreadable:
            raise TrialResultUnreadableError(f"result file is unreadable — run_id={run_id}")
        return self.results[run_id]

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        """평일 세션."""
        days = (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        return tuple(day for day in days if day.weekday() < 5)

    def states(self, run_ids: Collection[str]) -> dict[str, BacktestRunState]:
        return {
            run_id: BacktestRunState(
                run_id=run_id,
                status=self.run_statuses[run_id],
                progress=0.0,
                stage="queued",
                message="",
                created_at=_AT,
                updated_at=_AT,
                error_code=self.error_codes.get(run_id),
            )
            for run_id in run_ids
        }

    def slot_usage(self) -> RunSlotUsage:
        return RunSlotUsage(total=3, running=2)

    def schedule(self, owner: str, *, paused: bool, priority: int) -> None:
        self.schedules.append((owner, paused, priority))

    def cancel(self, run_id: str, *, owner: str) -> None:
        self.cancelled.append((run_id, owner))


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
        metric_registry=build_default_metric_registry(),
        new_id=lambda: next(ids),
        now=lambda: _AT,
        spawn=spawn,
        # 워크포워드는 테스트가 `advance` 로 한 걸음씩 민다.
        poll_seconds=None,
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

    runs.run_statuses.update(
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
    runs.run_statuses["run-1"] = RunStatus.FAILED

    # 재시도·선택 가능 여부는 trial 상태에 실려 화면이 규칙을 다시 세지 않는다(V5-01).
    assert [(state.retryable, state.selectable) for state in service.trials(experiment_id)] == [
        (False, False),
        (True, False),
        (False, False),
        (False, False),
    ]
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
    assert (first.run_id, first.error_code, first.error) == (
        None,
        "backtest.run.invalid",
        "strategy exceeds engine capabilities — core=rust",
    )
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
    # 취소한 실험의 trial 은 다시 실행할 수 없다.
    assert not any(state.retryable for state in service.trials(experiment_id))


def test_cancelling_a_completed_experiment_is_refused_and_leaves_it_completed() -> None:
    """#402 리뷰 P3-3: 목록이 아직 도는 것으로 보인 틈에 누른 취소가 결과가 다 나온 실험을 취소로
    바꾸지 않고 `experiment.cancel.completed` 로 거절한다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(runs, {f"run-{index}": _result(0.1 * (index + 1)) for index in range(4)})
    assert service.advance(experiment_id) is True
    runs.complete_all()
    assert service.get(experiment_id).status is ExperimentStatus.COMPLETED

    with pytest.raises(ExperimentStateError) as raised:
        service.cancel(experiment_id)
    after = service.get(experiment_id)

    assert raised.value.code == "experiment.cancel.completed"
    assert (after.status, after.finished, after.record.cancelled_at, runs.cancelled) == (
        ExperimentStatus.COMPLETED,
        True,
        None,
        [],
    )


def test_cancel_requests_cancellation_of_every_submitted_run_once() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id

    first = service.cancel(experiment_id)
    second = service.cancel(experiment_id)

    # 실행 서비스에 이 실험이 빠진다고 알린다 — 다른 소유자가 남은 run 은 멈추지 않는다.
    assert runs.cancelled == [(f"run-{index}", experiment_id) for index in range(4)]
    assert runs.owners == {experiment_id}
    assert first.record.cancelled_at == second.record.cancelled_at == _AT
    with pytest.raises(ExperimentStateError):
        service.retry(experiment_id, 0)


def test_only_a_completed_trial_can_be_selected_and_the_record_stays() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id

    with pytest.raises(ExperimentStateError) as raised:
        service.select(experiment_id, 2, "이웃 평균 샤프가 가장 높다")
    _finish(runs, {f"run-{index}": _result() for index in range(4)})
    assert service.trials(experiment_id)[2].selectable
    selection = service.select(experiment_id, 2, "이웃 평균 샤프가 가장 높다")

    assert raised.value.code == "experiment.selection.not_completed"
    assert (selection.strategy_id, selection.revision, selection.parameter_values) == (
        "s-1",
        3,
        {"scale": 1.0, "mode": "b"},
    )
    assert service.get(experiment_id).selections == (selection,)


def test_a_selection_keeps_the_lineage_snapshot_it_was_made_with() -> None:
    """V4-02: 고를 때의 N·대표 샤프·DSR 을 남기고, 나중에 N 이 늘어도 기록은 그대로다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id
    keys = runs.keys
    _finish(
        runs,
        {f"run-{index}": _result() for index in range(4)}
        | {"run-2": _result(0.06, standard_error=0.03)},
    )
    # 실행 서비스가 원장에 적은 행 대신, 완료 순서를 정한 원장을 둔다.
    # 칸 (0,) 의 두 창(run-0 먼저 완료, run-1 재확인)과 칸 (1,) 의 두 창(run-3 이 먼저 완료돼 대표).
    runs.ledger_entries = [
        TrialLedgerEntry("run-0", keys[0], RunStatus.COMPLETED, _AT, _AT + timedelta(1), 0.02),
        TrialLedgerEntry("run-1", keys[1], RunStatus.COMPLETED, _AT, _AT + timedelta(3), 0.09),
        TrialLedgerEntry("run-2", keys[2], RunStatus.COMPLETED, _AT, _AT + timedelta(2), 0.06),
        TrialLedgerEntry("run-3", keys[3], RunStatus.COMPLETED, _AT, _AT + timedelta(1), 0.04),
    ]

    selection = service.select(experiment_id, 2, "학습 샤프가 가장 높다")
    runs.ledger_entries.append(
        TrialLedgerEntry("run-9", "other", RunStatus.COMPLETED, _AT, _AT + timedelta(4), 0.5)
    )

    # N = 2(재확인 run-1 은 세지 않는다). 고른 trial 의 시도 대표는 먼저 완료된 run-3 의 0.04 다.
    # V = 표본분산(0.02, 0.04) = 0.0002, SR₀ = √V × γΦ⁻¹(1 − 1/(2e)) = 0.014142 × 0.51976
    # = 0.0073505.
    # DSR = Φ((0.06 − 0.0073505)/0.03) = Φ(1.7550) = 0.96037 — 고른 run-2 의 샤프로 잰다.
    assert (selection.trial_count, selection.ledger_representative_sharpe) == (2, 0.04)
    assert selection.deflated_sharpe == pytest.approx(0.96037, abs=1e-5)
    assert service.get(experiment_id).selections == (selection,)


def test_a_selection_recorded_before_the_snapshot_reads_without_one(tmp_path: Path) -> None:
    """V4-02 이전 서버가 쓴 선택 기록(스냅숏 칸 없음)은 칸이 비어 읽힌다."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    service = _service(runs, repository=SQLiteExperimentRepository(path))
    experiment_id = service.create(_request()).record.experiment_id
    _finish(runs, {f"run-{index}": _result() for index in range(4)})
    service.select(experiment_id, 2, "이유")
    with sqlite3.connect(path) as connection:
        (stored,) = connection.execute(
            "SELECT selection_json FROM experiment_selections"
        ).fetchone()
        document = json.loads(stored)
        for field in ("trial_count", "ledger_representative_sharpe", "deflated_sharpe"):
            del document[field]
        connection.execute(
            "UPDATE experiment_selections SET selection_json = ?", (json.dumps(document),)
        )

    reopened = _service(runs, repository=SQLiteExperimentRepository(path))
    (selection,) = reopened.get(experiment_id).selections

    assert (
        selection.trial_count,
        selection.ledger_representative_sharpe,
        selection.deflated_sharpe,
    ) == (None, None, None)


def test_a_candidate_is_chosen_only_after_the_experiment_has_finished() -> None:
    """V4-02 리뷰 P3-5: 대기·도는 trial 이 남으면 설계한 조합이 다 돌기 전의 N 으로 스냅숏이 굳지
    않게 거절한다. 취소한 실험은 끝났다(돌지 않은 조합은 시도가 아니다)."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id
    _finish(runs, {"run-2": _result()})
    # 화면이 읽는 `selectable` 도 같은 판정이다 — 완료한 trial 이라도 실험이 끝나기 전에는 거짓이다.
    unfinished_selectable = service.trials(experiment_id)[2].selectable

    with pytest.raises(ExperimentStateError) as raised:
        service.select(experiment_id, 2, "학습 샤프가 가장 높다")
    service.cancel(experiment_id)
    assert service.trials(experiment_id)[2].selectable
    selection = service.select(experiment_id, 2, "학습 샤프가 가장 높다")

    assert not unfinished_selectable

    assert raised.value.code == "experiment.selection.not_finished"
    assert "unfinished_trials=3" in str(raised.value)
    assert selection.trial_index == 2


def test_the_preview_counts_each_grid_cell_once_whatever_the_windows() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    rolling = _request(scale=None)

    before = service.preview(rolling)
    service.create(rolling)
    runs.complete_all()
    after = service.preview(rolling)
    anchored = service.preview(
        _request(split=replace(_ROLLING, mode=SplitMode.ANCHORED), scale=None)
    )

    # scale 격자 -1, 0, 1 × 롤링 창 둘 = 실행 6. 창은 분할 설계가 정한 평가 구간이라 한 칸의 두 창이
    # 한 시도다 → N 0 → 3. 제출이 원장에 적은 키로 다시 물으면 셋 다 이미 센 시도다.
    assert (before.combination_count, before.run_count) == (3, 6)
    assert (before.trial_count, before.new_trial_count, before.trial_count_after) == (0, 3, 3)
    assert len(set(runs.keys)) == 3
    assert (after.trial_count, after.new_trial_count, after.trial_count_after) == (3, 0, 3)
    assert (anchored.new_trial_count, anchored.trial_count_after) == (0, 3)


def test_an_experiment_survives_reopening_the_research_database(tmp_path: Path) -> None:
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    created = _service(runs, repository=SQLiteExperimentRepository(path)).create(_request())
    runs.run_statuses["run-0"] = RunStatus.FAILED

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


def test_experiments_are_listed_newest_first_a_page_at_a_time() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    created = [service.create(_request()).record.experiment_id for _ in range(3)]

    first = service.list(after=None, limit=2)
    second = service.list(after=first.next_after, limit=2)

    assert [item.record.experiment_id for item in first.items] == created[:0:-1]
    assert (first.next_after, second.next_after) == (created[1], None)
    assert [item.record.experiment_id for item in second.items] == created[:1]
    assert service.list(after="missing", limit=2).items == ()
    # 대기열 화면의 슬롯 사용량과 우선순위 상한은 목록 응답이 싣는다(V5-01).
    assert (first.slots, first.max_priority) == (RunSlotUsage(total=3, running=2), 5)


def test_controls_pause_the_experiment_lane_and_come_back_after_a_restart(
    tmp_path: Path,
) -> None:
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    service = _service(runs, repository=SQLiteExperimentRepository(path))
    experiment_id = service.create(_request()).record.experiment_id

    service.control(experiment_id, priority=3)
    paused = service.control(experiment_id, paused=True)  # 보내지 않은 우선순위는 그대로다
    _service(runs, repository=SQLiteExperimentRepository(path)).recover()

    assert (paused.status, paused.trial_counts) == (
        ExperimentStatus.PAUSED,
        {TrialStatus.QUEUED: 4},
    )
    assert paused.record.controls == ExperimentControls(paused=True, priority=3)
    # 대기열에 알리고, 재시작하면 저장된 조작을 다시 알린다.
    assert runs.schedules == [
        (experiment_id, False, 3),
        (experiment_id, True, 3),
        (experiment_id, True, 3),
    ]


def test_recover_resubmits_unsubmitted_and_interrupted_trials_only(tmp_path: Path) -> None:
    """spec D6·V3-03 인계 (2): 재시작하면 넘기지 못한 trial 과 중단된 trial 만 다시 넘긴다."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    pending: list[Callable[[], None]] = []
    before = _service(runs, repository=SQLiteExperimentRepository(path), spawn=pending.append)
    submitted = before.create(_request()).record.experiment_id
    pending[0]()
    # 제출 스레드가 돌지 못한 실험(예상 밖 오류로 멈춘 경우와 같다)과 취소한 실험.
    unsubmitted = before.create(_request()).record.experiment_id
    cancelled = before.create(_request()).record.experiment_id
    before.cancel(cancelled)
    runs.run_statuses.update(
        {
            "run-0": RunStatus.FAILED,
            "run-1": RunStatus.COMPLETED,
            "run-2": RunStatus.FAILED,
            "run-3": RunStatus.FAILED,
        }
    )
    runs.error_codes.update(
        {
            "run-0": "backtest.run.interrupted",
            "run-2": "backtest.run.invalid",
            "run-3": "backtest.run.interrupted",
        }
    )

    after = _service(runs, repository=SQLiteExperimentRepository(path))
    after.recover()

    assert [len(state.attempts) for state in after.trials(submitted)] == [2, 1, 1, 2]
    assert [state.status for state in after.trials(submitted)] == [
        TrialStatus.QUEUED,
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.QUEUED,
    ]
    assert [len(state.attempts) for state in after.trials(unsubmitted)] == [1, 1, 1, 1]
    assert [state.status for state in after.trials(cancelled)] == [TrialStatus.CANCELLED] * 4
    assert len(runs.started) == 4 + 4 + 2


def test_recover_skips_finished_and_unreadable_experiments(tmp_path: Path) -> None:
    """#381 DEFECT-V3D-04: 지금 모델로 디코드되지 않는 실험 하나가 부팅을 막지 않는다. #384
    DEFECT-V3D-10: 끝난 실험에는 제출 스레드·대기열 조작을 다시 두지 않는다."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    submissions: list[Callable[[], None]] = []
    service = _service(runs, repository=SQLiteExperimentRepository(path), spawn=submissions.append)
    finished = service.create(_request(split=_BEST)).record.experiment_id
    submissions[0]()
    _finish(runs, {f"run-{index}": _result(0.1) for index in range(4)})
    service.advance(finished)
    _finish(runs, {"run-4": _result(), "run-5": _result()})
    # 제출 스레드가 돌기 전에 재시작한 두 실험. 하나는 저장된 설계를 지금 모델로 읽을 수 없다.
    broken = service.create(_request()).record.experiment_id
    waiting = service.create(_request()).record.experiment_id
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE experiments SET design_json = '{}' WHERE experiment_id = ?", (broken,)
        )
    pending: list[Callable[[], None]] = []

    _service(runs, repository=SQLiteExperimentRepository(path), spawn=pending.append).recover()

    assert service.get(finished).status is ExperimentStatus.COMPLETED
    # 끝난 실험도 조작은 되살린다(#390 리뷰 P3-3) — 제출 스레드만 없다.
    assert [owner for owner, _paused, _priority in runs.schedules] == [finished, waiting]
    assert len(pending) == 1


def _interrupted_restart(tmp_path: Path) -> tuple[_FakeRuns, Path, str]:
    """trial 넷을 넘긴 뒤 재시작해 0·3 이 중단(interrupted)으로 닫힌 상태."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    experiment_id = (
        _service(runs, repository=SQLiteExperimentRepository(path))
        .create(_request())
        .record.experiment_id
    )
    for run_id in ("run-0", "run-3"):
        runs.run_statuses[run_id] = RunStatus.FAILED
        runs.error_codes[run_id] = "backtest.run.interrupted"
    runs.run_statuses["run-1"] = runs.run_statuses["run-2"] = RunStatus.COMPLETED
    return runs, path, experiment_id


def test_a_retry_while_recovery_submits_does_not_repeat_an_attempt(tmp_path: Path) -> None:
    """#348 리뷰 P2-1: 복구가 남은 trial 을 고른 뒤 사용자가 재시도하면 그 trial 은 건너뛴다."""
    runs, path, experiment_id = _interrupted_restart(tmp_path)
    after = _service(runs, repository=SQLiteExperimentRepository(path))
    runs.before_admit = lambda: after.retry(experiment_id, 0) and None

    after.recover()

    assert [len(state.attempts) for state in after.trials(experiment_id)] == [2, 1, 1, 2]
    # 재시도 하나와 복구 하나 — 어떤 attempt 에도 걸리지 않은 run 이 없다.
    assert len(runs.started) == 4 + 2
    attempted = {a.run_id for s in after.trials(experiment_id) for a in s.attempts}
    assert attempted == set(runs.run_statuses)


def test_an_interrupted_experiment_is_not_completed_until_recovery_resubmits(
    tmp_path: Path,
) -> None:
    """#348 리뷰 P3-2: 복구가 넘기기 전에는 완료로 보이지 않는다."""
    runs, path, experiment_id = _interrupted_restart(tmp_path)
    after = _service(runs, repository=SQLiteExperimentRepository(path), spawn=lambda work: None)

    after.recover()  # 제출 스레드가 아직 돌지 않았다

    assert after.get(experiment_id).status is ExperimentStatus.RUNNING
    assert [state.awaiting_recovery for state in after.trials(experiment_id)] == [
        True,
        False,
        False,
        True,
    ]


def test_a_refused_recovery_records_the_rejection_instead_of_hanging(tmp_path: Path) -> None:
    """#380 DEFECT-V3D-02: 재시작 뒤 기반 재검사가 거절되면 남은 trial 마다 거절 attempt 를 남겨
    실험이 끝난다. 재시도는 막지 않는다."""
    runs, path, experiment_id = _interrupted_restart(tmp_path)
    after = _service(runs, repository=SQLiteExperimentRepository(path))

    def refused() -> None:
        raise TrialRunRejectedError("backtest.strategy.requires_upgrade", "frozen revision")

    runs.before_admit = refused
    after.recover()

    trials = after.trials(experiment_id)
    assert [(len(state.attempts), state.awaiting_recovery) for state in trials] == [
        (2, False),
        (1, False),
        (1, False),
        (2, False),
    ]
    assert [trials[index].attempts[-1].error_code for index in (0, 3)] == [
        "backtest.strategy.requires_upgrade"
    ] * 2
    assert len(runs.started) == 4
    # 창을 고를 점수가 원장에 없어 두 창 모두 칸 없는 선택으로 끝난다.
    assert after.advance(experiment_id) is True
    assert after.get(experiment_id).status is ExperimentStatus.COMPLETED
    assert after.retry(experiment_id, 0).status is TrialStatus.QUEUED


def test_a_retry_refused_by_the_base_recheck_leaves_no_attempt() -> None:
    """#380 DEFECT-V3D-02: 재시도의 기반 재검사 거절은 코드를 실은 오류로 올라간다(HTTP 는 실행
    시작과 같은 코드로 옮긴다). attempt 는 늘지 않는다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request()).record.experiment_id
    runs.run_statuses["run-0"] = RunStatus.FAILED

    def refused() -> None:
        raise TrialRunRejectedError("backtest.strategy.stale", "revision hash changed")

    runs.before_admit = refused
    with pytest.raises(TrialRunRejectedError) as raised:
        service.retry(experiment_id, 0)

    assert raised.value.code == "backtest.strategy.stale"
    assert len(service.trials(experiment_id)[0].attempts) == 1


def test_a_version_3_research_file_upgrades_with_default_controls(tmp_path: Path) -> None:
    """#348 리뷰 P3-4: 판본 3 파일(조작 표 없음)을 열면 실험 행은 그대로이고 조작은 기본값이다."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    experiment_id = (
        _service(runs, repository=SQLiteExperimentRepository(path))
        .create(_request())
        .record.experiment_id
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE experiment_window_picks")
        connection.execute("DROP TABLE experiment_controls")
        connection.execute("PRAGMA user_version = 3")

    reopened = _service(runs, repository=SQLiteExperimentRepository(path))
    before = reopened.get(experiment_id)
    after = reopened.control(experiment_id, paused=True)

    assert before.record.controls == DEFAULT_EXPERIMENT_CONTROLS
    assert [len(state.attempts) for state in reopened.trials(experiment_id)] == [1, 1, 1, 1]
    assert after.record.controls == ExperimentControls(paused=True, priority=1)


# 워크포워드(V3-05). 칸 두 개(scale -1 → (0,), 1 → (1,)) × 롤링 창 두 개라 trial 0·1 이 칸 (0,) 의
# 창 0·1, trial 2·3 이 칸 (1,) 의 창 0·1 이다. 창 0 검증은 2022-01-04 ~ 2023-01-03, 창 1 검증은
# 2023-01-04 ~ 2023-06-30 이다.
_BEST = replace(_ROLLING, selection_rule=WindowSelectionRule.TRAIN_SHARPE_MAX)


def _finish(runs: _FakeRuns, results: dict[str, BacktestRunResult]) -> None:
    """실행 서비스처럼 결과를 남기고 그 세션 샤프를 원장에 적는다(창 고르기는 원장을 읽는다)."""
    for run_id, result in results.items():
        runs.run_statuses[run_id] = RunStatus.COMPLETED
        runs.results[run_id] = result
        runs.ledger_entries.append(
            TrialLedgerEntry(
                run_id,
                runs.keys[int(run_id.removeprefix("run-"))],
                RunStatus.COMPLETED,
                _AT,
                _AT,
                representative_sharpe(result),
            )
        )


def test_each_window_runs_its_best_train_cell_on_the_test_window_as_a_recheck() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id

    assert service.advance(experiment_id) is False  # 학습이 하나도 끝나지 않았다
    # 창 0: 칸 (0,) 0.2 > 칸 (1,) 0.1 → trial 0. 창 1 학습은 아직 돈다.
    _finish(runs, {"run-0": _result(0.2), "run-2": _result(0.1)})
    assert service.advance(experiment_id) is False
    # 창 1: 칸 (1,) 0.3 > 칸 (0,) 0.05 → trial 3.
    _finish(runs, {"run-1": _result(0.05), "run-3": _result(0.3)})
    assert service.advance(experiment_id) is True  # 모든 창을 골랐다
    assert service.advance(experiment_id) is True  # 검증 실행이 돌 동안 다시 고르지 않는다

    tested = runs.started[4:]
    assert len(tested) == 2
    assert [
        (request.environment and (request.environment.start, request.environment.end))
        for request in tested
    ] == [(date(2022, 1, 4), date(2023, 1, 3)), (date(2023, 1, 4), date(2023, 6, 30))]
    assert [request.parameter_values for request in tested] == [
        {"scale": -1.0, "mode": "b"},
        {"scale": 1.0, "mode": "b"},
    ]
    # 검증 실행은 고른 칸의 시도 키로 적혀 원장에서 재확인이다 — 창이 N 을 늘리지 않는다.
    assert runs.keys[4:] == [runs.keys[0], runs.keys[3]]
    assert runs.owners == {experiment_id}
    report = service.walk_forward(experiment_id)
    picks = [window.pick for window in report.windows]
    assert [(p.window_index, p.attempt, p.trial_index, p.run_id) for p in picks] == [
        (0, 1, 0, "run-4"),
        (1, 1, 3, "run-5"),
    ]
    assert report.gap is WalkForwardGap.PENDING
    assert [p.train_sharpe for p in picks] == pytest.approx([0.2, 0.3])
    # 학습 trial 은 모두 끝났지만 검증 실행이 남아 완료가 아니다.
    assert service.get(experiment_id).status is ExperimentStatus.RUNNING
    runs.complete_all()
    assert service.preview(_request(split=_BEST)).trial_count == 2


def test_the_stitched_curve_chains_only_test_window_returns() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(
        runs,
        {
            "run-0": _result(0.2),
            "run-1": _result(0.05),
            "run-2": _result(0.1),
            "run-3": _result(0.3),
        },
    )
    service.advance(experiment_id)
    assert service.walk_forward(experiment_id).curve == ()  # 검증 실행이 끝나기 전
    cash = _BASE.initial_cash
    _finish(
        runs,
        {
            "run-4": _result(
                equity=(
                    (date(2022, 1, 4), cash * 1.01),
                    (date(2022, 1, 5), cash * 1.00),
                    (date(2022, 1, 6), cash * 1.02),
                )
            ),
            "run-5": _result(
                equity=(
                    (date(2023, 1, 4), cash * 1.02),
                    (date(2023, 1, 5), cash * 1.0302),
                    (date(2023, 1, 6), cash * 1.0302 * 0.99),
                )
            ),
        },
    )

    assert service.advance(experiment_id) is True
    report = service.walk_forward(experiment_id)

    # 학습 점수처럼 창마다 첫 스냅숏부터 센다 — 진입일(초기 자본 → 첫 세션) 수익률은 빠지고, 창 1
    # 첫 세션 2023-01-04 점은 없이 1.02/1.01 에서 1.0302/1.02 로 이어진다.
    assert [(point.session, point.equity) for point in report.curve] == [
        (date(2022, 1, 4), pytest.approx(1.0)),
        (date(2022, 1, 5), pytest.approx(1 / 1.01)),
        (date(2022, 1, 6), pytest.approx(1.02 / 1.01)),
        (date(2023, 1, 5), pytest.approx(1.02)),
        (date(2023, 1, 6), pytest.approx(1.0098)),
    ]
    # 세션 샤프 = 초과 수익률 평균 ÷ 세션 수익률 표본 표준편차. 초과 수익률 = 세션 수익률 − 직전
    # 세션 기준금리 × 달력 일수 / 365 (2022-01-04·05·06 은 1.00%, 2023-01-05 는 3.25%).
    returns = [1 / 1.01 - 1, 0.02, 0.01, -0.01]
    rates = [0.01 * 1 / 365, 0.01 * 1 / 365, 0.01 * 364 / 365, 0.0325 * 1 / 365]
    mean = sum(r - rate for r, rate in zip(returns, rates, strict=True)) / 4
    average = sum(returns) / 4
    deviation = math.sqrt(sum((r - average) ** 2 for r in returns) / 3)
    assert report.out_of_sample_sharpe == pytest.approx(mean / deviation)
    # 유지율 = 표본 밖 세션 샤프 ÷ 고른 칸 학습 샤프 평균 (0.2 + 0.3) / 2.
    assert report.retention == pytest.approx(mean / deviation / 0.25)
    assert report.gap is None
    assert service.get(experiment_id).status is ExperimentStatus.COMPLETED


def test_a_failed_test_window_empties_the_summary_with_its_reason() -> None:
    """#365 리뷰 P2-1: 파산한 검증 창을 빼고 남은 창만으로 표본 밖 샤프를 내지 않는다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(
        runs,
        {
            "run-0": _result(0.2),
            "run-1": _result(0.05),
            "run-2": _result(0.1),
            "run-3": _result(0.3),
        },
    )
    service.advance(experiment_id)
    cash = _BASE.initial_cash
    _finish(runs, {"run-4": _result(equity=((date(2022, 1, 4), cash), (date(2022, 1, 5), cash)))})
    runs.run_statuses["run-5"] = RunStatus.FAILED
    runs.error_codes["run-5"] = "backtest.run.equity_wiped_out"

    assert service.advance(experiment_id) is True
    report = service.walk_forward(experiment_id)

    assert [
        (window.run_status, window.run_error_code, window.gap) for window in report.windows
    ] == [
        (RunStatus.COMPLETED, None, None),
        (RunStatus.FAILED, "backtest.run.equity_wiped_out", WalkForwardGap.TEST_FAILED),
    ]
    assert (report.curve, report.out_of_sample_sharpe, report.retention, report.gap) == (
        (),
        None,
        None,
        WalkForwardGap.TEST_FAILED,
    )
    assert len(runs.started) == 6  # 실패한 검증 창은 다시 넘기지 않는다
    assert service.get(experiment_id).status is ExperimentStatus.COMPLETED


def test_a_rejected_base_recheck_records_the_windows_instead_of_hanging() -> None:
    """#365 리뷰 P3-2: 검증 실행 전 기반 재검사가 거절되면 창 선택에 거절 코드를 남기고 끝낸다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(
        runs,
        {
            "run-0": _result(0.2),
            "run-1": _result(0.05),
            "run-2": _result(0.1),
            "run-3": _result(0.3),
        },
    )

    def refused() -> None:
        raise TrialRunRejectedError("backtest.strategy.requires_upgrade", "frozen revision")

    runs.before_admit = refused

    assert service.advance(experiment_id) is True
    report = service.walk_forward(experiment_id)

    assert len(runs.started) == 4
    assert [
        (window.pick.trial_index, window.pick.run_id, window.pick.error_code, window.gap)
        for window in report.windows
    ] == [
        (0, None, "backtest.strategy.requires_upgrade", WalkForwardGap.TEST_FAILED),
        (3, None, "backtest.strategy.requires_upgrade", WalkForwardGap.TEST_FAILED),
    ]
    assert report.gap is WalkForwardGap.TEST_FAILED
    assert service.get(experiment_id).status is ExperimentStatus.COMPLETED


def test_train_scores_are_read_outside_the_service_lock() -> None:
    """#365 리뷰 P3-1: 학습 점수(원장)를 읽는 동안 다른 실험의 조작이 서비스 락을 잡을 수 있다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(runs, {"run-0": _result(0.2), "run-2": _result(0.1)})
    free: list[bool] = []

    def probe() -> None:
        lock = service._lock  # pyright: ignore[reportPrivateUsage]  # reason: 락 점유 확인

        def other() -> None:
            if acquired := lock.acquire(blocking=False):
                lock.release()
            free.append(acquired)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()

    runs.on_ledger = probe
    service.advance(experiment_id)

    assert free == [True]
    assert len(runs.started) == 5


def test_windows_are_picked_from_ledger_scores_without_reading_result_files() -> None:
    """#377 V3-AUDIT-02: 학습 결과 파일을 읽을 수 없어도 원장에 적힌 점수로 창을 고른다. 검증
    결과를 읽을 수 없으면 곡선 대신 이유를 싣는다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(
        runs,
        {
            "run-0": _result(0.2),
            "run-1": _result(0.05),
            "run-2": _result(0.1),
            "run-3": _result(0.3),
        },
    )
    runs.unreadable |= {"run-0", "run-1", "run-2", "run-3"}

    assert service.advance(experiment_id) is True
    _finish(runs, {"run-4": _result(), "run-5": _result()})
    runs.unreadable.add("run-5")
    report = service.walk_forward(experiment_id)

    assert [window.pick.trial_index for window in report.windows] == [0, 3]
    assert (report.curve, report.gap) == ((), WalkForwardGap.RESULT_UNREADABLE)


def test_an_interrupted_test_run_is_resubmitted_with_the_same_cell() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(runs, {"run-0": _result(0.2), "run-2": _result(0.1)})
    service.advance(experiment_id)
    runs.run_statuses["run-4"] = RunStatus.FAILED
    runs.error_codes["run-4"] = "backtest.run.interrupted"
    # 창 0 학습 결과가 바뀌어도 다시 고르지 않는다 — 같은 칸으로 다시 넘긴다.
    runs.results["run-2"] = _result(0.9)

    service.advance(experiment_id)

    assert runs.started[5] == runs.started[4]
    assert runs.keys[5] == runs.keys[4]
    windows = service.walk_forward(experiment_id).windows
    assert [
        (w.pick.window_index, w.pick.attempt, w.pick.trial_index, w.run_status) for w in windows
    ] == [
        (0, 2, 0, RunStatus.QUEUED),
    ]


def test_a_window_without_a_scored_train_cell_is_recorded_without_a_test_run() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    # 창 0 학습은 하나는 실패, 하나는 샤프가 비었다. 창 1 학습은 둘 다 실패했다.
    runs.run_statuses.update(
        {"run-0": RunStatus.FAILED, "run-1": RunStatus.FAILED, "run-3": RunStatus.FAILED}
    )
    _finish(runs, {"run-2": _result()})

    assert service.advance(experiment_id) is True
    report = service.walk_forward(experiment_id)

    assert len(runs.started) == 4
    assert [
        (w.pick.window_index, w.pick.trial_index, w.pick.train_sharpe, w.pick.run_id, w.gap)
        for w in report.windows
    ] == [
        (0, None, None, None, WalkForwardGap.NO_CELL),
        (1, None, None, None, WalkForwardGap.NO_CELL),
    ]
    assert (report.curve, report.out_of_sample_sharpe, report.retention, report.gap) == (
        (),
        None,
        None,
        WalkForwardGap.NO_CELL,
    )
    assert service.get(experiment_id).status is ExperimentStatus.COMPLETED


def test_cancel_also_cancels_test_runs_and_stops_picking() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = service.create(_request(split=_BEST)).record.experiment_id
    _finish(runs, {"run-0": _result(0.2), "run-2": _result(0.1)})
    service.advance(experiment_id)

    service.cancel(experiment_id)
    _finish(runs, {"run-1": _result(0.05), "run-3": _result(0.3)})

    assert ("run-4", experiment_id) in runs.cancelled
    assert service.advance(experiment_id) is True
    assert len(runs.started) == 5
    # #377 V3-AUDIT-01: 남은 창은 더 고르지 않으므로 "진행 중" 이 아니라 취소가 이유다.
    assert service.walk_forward(experiment_id).gap is WalkForwardGap.CANCELLED


def test_an_experiment_from_a_version_4_file_keeps_its_old_meaning(tmp_path: Path) -> None:
    """#365 리뷰 P3-4: V3-05 이전 실험(엠바고를 적용하지 않은 창)은 판본 5 로 올린 뒤 재시작해도
    검증 실행을 시작하지 않고 학습 trial 이 끝나면 완료다."""
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    experiment_id = (
        _service(runs, repository=SQLiteExperimentRepository(path))
        .create(_request(split=_BEST))
        .record.experiment_id
    )
    with sqlite3.connect(path) as connection:
        # 판본 4 서버가 쓴 행에는 `measured` 가 없다.
        (stored,) = connection.execute("SELECT design_json FROM experiments").fetchone()
        document = json.loads(stored)
        del document["design"]["measured"]
        connection.execute("UPDATE experiments SET design_json = ?", (json.dumps(document),))
        connection.execute("DROP TABLE experiment_window_picks")
        connection.execute("PRAGMA user_version = 4")
    runs.complete_all()

    reopened = _service(runs, repository=SQLiteExperimentRepository(path))
    reopened.recover()

    assert reopened.advance(experiment_id) is True
    assert len(runs.started) == 4
    assert reopened.get(experiment_id).status is ExperimentStatus.COMPLETED
    report = reopened.walk_forward(experiment_id)
    assert (report.windows, report.gap) == ((), WalkForwardGap.LEGACY_DESIGN)


def test_the_parameter_map_averages_ledger_train_scores_and_floors_a_bankrupt_cell() -> None:
    """V4-03: 칸 점수는 창별 원장 학습 점수의 평균이고, 파산한 칸은 최하 점수로 이웃에 든다."""
    runs = _FakeRuns()
    service = _service(runs)
    # scale 격자 -1, 0, 1 × 롤링 창 둘. trial 2k·2k+1 이 칸 (k,) 의 두 창이다.
    experiment_id = service.create(_request(scale=None)).record.experiment_id
    _finish(
        runs,
        {
            "run-0": _result(0.2),
            "run-1": _result(0.4),
            "run-3": _result(0.6),
            "run-4": _result(0.15),
            "run-5": _result(0.25),
        },
    )
    runs.run_statuses["run-2"] = RunStatus.FAILED
    runs.error_codes["run-2"] = "backtest.run.equity_wiped_out"

    cells = service.parameter_map(experiment_id).cells

    # 칸 점수 (0,)=0.3, (2,)=0.2. 파산 칸 (1,) 은 최하 점수 min(0.2, 0) = 0 이라 두 칸의 이웃 평균이
    # 모두 0 — 동점이면 좌표가 앞선 칸이 추천이다. 민감도: -1×0.8=-0.8 쪽 가장 가까운 격자값 0 은
    # 파산이라 하락 1.0, 1×0.8=0.8 쪽도 0 이라 1.0. -1×1.2·1×1.2 쪽은 축 끝이다.
    assert [
        (cell.grid_index, cell.verdict, cell.score, cell.plateau_score, cell.sensitivity)
        for cell in cells
    ] == [
        ((0,), CellVerdict.RECOMMENDED, pytest.approx(0.3), 0.0, pytest.approx(1.0)),
        ((1,), CellVerdict.FAILED, None, None, None),
        ((2,), CellVerdict.SCORED, pytest.approx(0.2), 0.0, pytest.approx(1.0)),
    ]
    # 비전략 실패(엔진 내부 오류)는 실패로 보이기만 하고 이웃 평균에서 빠진다.
    runs.error_codes["run-2"] = "backtest.run.internal"
    assert [
        (cell.verdict, cell.plateau_score) for cell in service.parameter_map(experiment_id).cells
    ] == [
        (CellVerdict.RECOMMENDED, None),
        (CellVerdict.FAILED, None),
        (CellVerdict.SCORED, None),
    ]


# 용량 스윕(V4-04). 금액 1억·2억·3억 → trial 0·1·2, run-0·1·2.
_AMOUNTS = [3e8, 1e8, 2e8]


def _counted_base(runs: _FakeRuns) -> None:
    """기반 설정으로 돌린 백테스트가 계열 원장에 결과를 냈다(결과 화면에서 연 스윕)."""
    key = trial_key(runs.admit(_BASE).run)
    runs.ledger_entries.append(
        TrialLedgerEntry("run-base", key, RunStatus.COMPLETED, _AT, _AT, 0.03)
    )


def _sweep(runs: _FakeRuns, service: ExperimentRunService) -> str:
    _counted_base(runs)
    request = CapacitySweepRequest(run=_BASE, initial_cash=_AMOUNTS)
    return service.create_capacity(request).record.experiment_id


def _capacity_result(sharpe: float, ordered: str, filled: str) -> BacktestRunResult:
    """1000원에 `filled` 주 체결(주당 슬리피지 1원, 기준 거래량 1,000주), DAY 주문 `ordered` 주."""
    order = RawOrder("o", "d", date(2021, 1, 5), "KRX:005930", "buy", ordered, "market", "day")
    fill = RawFill("f", "o", date(2021, 1, 5), "KRX:005930", "buy", filled, 1000.0, 0.0, 1.0, 1_000)
    # 목표 Δ 100,000 원을 1주 단위로 99,000 원까지만 샀다.
    rounding = RawRounding(date(2021, 1, 4), "KRX:005930", 100_000.0, 99_000.0)
    artifacts = RawArtifactBundle((), (), (order,), (fill,), (), (), (rounding,))
    return replace(_result(sharpe), artifacts=artifacts)


def test_a_capacity_sweep_needs_a_counted_base_trial_and_never_adds_to_n() -> None:
    """#410 리뷰 P2-3: 기반 시도가 원장에 결과를 냈어야 한다 — 스윕은 늘 그 시도의 재확인이다."""
    runs = _FakeRuns()
    service = _service(runs)
    request = CapacitySweepRequest(run=_BASE, initial_cash=_AMOUNTS)

    for call in (service.preview_capacity, service.create_capacity):
        with pytest.raises(InvalidExperimentSpecError) as refused:
            call(request)
        assert refused.value.code == "experiment.capacity.base_not_run"
    assert runs.started == []
    _counted_base(runs)
    preview = service.preview_capacity(request)
    experiment = service.create_capacity(request)

    assert (preview.run_count, preview.new_trial_count, preview.trial_count_after) == (3, 0, 1)
    assert experiment.record.design.kind is ExperimentKind.CAPACITY_SWEEP
    assert experiment.record.split is None
    # 금액 순으로 기반 실행 구간 전체를 돈다. 초기 자본은 시도 키 밖이라 세 실행이 기반과 한 시도다.
    assert [(r.initial_cash, r.environment and r.environment.start) for r in runs.started] == [
        (1e8, date(2021, 1, 4)),
        (2e8, date(2021, 1, 4)),
        (3e8, date(2021, 1, 4)),
    ]
    assert {r.environment and r.environment.end for r in runs.started} == {date(2023, 6, 30)}
    assert [r.parameter_values for r in runs.started] == [{"scale": 1.0, "mode": "b"}] * 3
    assert set(runs.keys) == {runs.ledger_entries[0].trial_key}
    runs.complete_all()
    assert runs.trial_ledger(_BASE).trial_count == 1
    assert service.advance(experiment.record.experiment_id) is True
    assert service.get(experiment.record.experiment_id).status is ExperimentStatus.COMPLETED


def test_the_capacity_report_settles_the_limit_only_when_every_amount_ran() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = _sweep(runs, service)
    _finish(
        runs,
        {
            "run-0": _capacity_result(0.04, "100", "100"),
            "run-1": _capacity_result(0.05, "100", "80"),
        },
    )

    pending = service.capacity(experiment_id)

    # 3억은 아직 돈다 — 한계 금액을 확정하지 않는다. 충격 1원/1000원 = 10bp, 세션 미체결 1 − 80/100.
    assert [
        (p.initial_cash, p.status, p.sharpe, p.impact_cost_bps, p.session_unfilled_ratio)
        for p in pending.points
    ] == [
        (1e8, TrialStatus.COMPLETED, pytest.approx(0.04), pytest.approx(10.0), 0.0),
        (2e8, TrialStatus.COMPLETED, pytest.approx(0.05), pytest.approx(10.0), pytest.approx(0.2)),
        (3e8, TrialStatus.QUEUED, None, None, None),
    ]
    assert pending.limit == CapacityLimit(None, None, None, CapacityGap.PENDING)
    # 참여율 = 체결 100·80주 ÷ 기준 거래량 1,000주, 반올림 오차 = 1,000 / 100,000.
    assert [p.participation_rate for p in pending.points] == [0.1, 0.08, None]
    assert [p.rounding_error for p in pending.points] == [
        pytest.approx(0.01),
        pytest.approx(0.01),
        None,
    ]
    # 재시작으로 중단돼 복구를 기다리는 금액도 아직 돈다.
    runs.run_statuses["run-2"] = RunStatus.FAILED
    runs.error_codes["run-2"] = "backtest.run.interrupted"
    assert service.capacity(experiment_id).limit.gap is CapacityGap.PENDING
    # 3억은 파산했다 — 기준 밑이라 한계는 2억이다. 결과 파일을 못 읽는 금액은 비용만 빈다(샤프는
    # 원장에서 읽는다).
    runs.run_statuses["run-2"] = RunStatus.FAILED
    runs.error_codes["run-2"] = "backtest.run.equity_wiped_out"
    runs.unreadable.add("run-0")
    finished = service.capacity(experiment_id)
    assert finished.limit == CapacityLimit(2e8, 2e8, pytest.approx(0.025), None)  # pyright: ignore[reportArgumentType]  # reason: 기준선은 부동소수 곱이다
    assert (finished.points[0].sharpe, finished.points[0].impact_cost_bps) == (
        pytest.approx(0.04),
        None,
    )
    assert finished.points[2].status is TrialStatus.FAILED


def test_a_cancelled_amount_leaves_the_capacity_limit_unsettled() -> None:
    """#410 리뷰 P2-2: 돌지 않은 금액에서 곡선이 꺾였다고 하지 않는다."""
    runs = _FakeRuns()
    service = _service(runs)
    experiment_id = _sweep(runs, service)
    _finish(
        runs,
        {
            "run-0": _capacity_result(0.05, "100", "100"),
            "run-1": _capacity_result(0.049, "100", "100"),
        },
    )
    service.cancel(experiment_id)
    runs.run_statuses["run-2"] = RunStatus.CANCELLED

    report = service.capacity(experiment_id)

    assert report.points[2].status is TrialStatus.CANCELLED
    assert report.limit == CapacityLimit(None, None, None, CapacityGap.CANCELLED)


def test_kind_specific_results_are_refused_for_the_other_kind() -> None:
    runs = _FakeRuns()
    service = _service(runs)
    sweep_id = _sweep(runs, service)
    search = service.create(_request())
    runs.complete_all()

    calls = {
        "walk_forward": lambda: service.walk_forward(sweep_id),
        "select": lambda: service.select(sweep_id, 0, "이유"),
        "parameter_map": lambda: service.parameter_map(sweep_id),
        "capacity": lambda: service.capacity(search.record.experiment_id),
    }
    for name, call in calls.items():
        with pytest.raises(ExperimentStateError) as refused:
            call()
        assert refused.value.code == "experiment.kind.mismatch", name


@pytest.mark.parametrize(
    "amounts",
    [
        [1e8, 2e8],
        [1e8, 1e8, 2e8],
        [1e8, 2e8, -1.0],
        [1e8, 2e8, math.inf],
        [1e8 * n for n in range(1, 14)],
    ],
)
def test_capacity_amounts_outside_the_rule_are_refused(amounts: list[float]) -> None:
    with pytest.raises(InvalidExperimentSpecError) as refused:
        _service(_FakeRuns()).preview_capacity(
            CapacitySweepRequest(run=_BASE, initial_cash=amounts)
        )
    assert refused.value.code == "experiment.capacity.invalid_amounts"


def test_a_capacity_sweep_survives_reopening_the_research_database(tmp_path: Path) -> None:
    runs = _FakeRuns()
    path = tmp_path / "research.sqlite3"
    _counted_base(runs)
    created = _service(runs, repository=SQLiteExperimentRepository(path)).create_capacity(
        CapacitySweepRequest(run=_BASE, initial_cash=_AMOUNTS)
    )

    reopened = _service(runs, repository=SQLiteExperimentRepository(path))

    assert reopened.get(created.record.experiment_id).record == created.record
    assert [t.initial_cash for t in created.record.design.trials()] == [1e8, 2e8, 3e8]
