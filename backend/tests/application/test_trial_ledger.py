"""시도 원장 — 새로 고를 거리가 생긴 실행만 N 에 센다(검증 랩 spec D2, V1-05).

실제 run 서비스·연구 DB 파일·mock 데이터로 돌린다. 원장이 파일에 남아 재시작 뒤에도 같은지,
미리 계산이 실제 기록과 같은지, 봉인 겹침 거절이 봉인 원장에만 남는지를 본다.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestParameterValueError,
    BacktestRunService,
    BacktestRunSpec,
    InlineDraft,
    InvalidBacktestRunError,
    RunStatus,
    SavedRevisionReference,
    StrategyReferenceNotFoundError,
    TrialLedger,
    TrialLineageAlreadyMergedError,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationPort
from strategy_workbench.application.strategy_design.facade.design import (
    SavedStrategy,
    StrategyDesignService,
)
from strategy_workbench.application.strategy_design.facade.ports import StrategyNotFoundError
from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import (
    ResearchWindowViolationError,
    RunEnvironment,
)
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore, MetricWindow
from strategy_workbench.domain.backtest.facade.trials import TrialRunRole, representative_sharpe
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.strategy.facade.specification import (
    FloatParameter,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import RawLoadBarrier, join_run_thread, wait_for_terminal_run

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


class _Lab:
    """전략 저장소 하나와 연구 DB 파일 하나를 나눠 쓰는 run 서비스들."""

    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "research.sqlite3"
        self._tmp_path = tmp_path
        self._strategies = InMemoryStrategyRepository()
        self._design = StrategyDesignService(
            self._strategies, new_id=iter(f"s-{index}" for index in range(1, 9)).__next__
        )
        self._run_ids = iter(f"run-{index}" for index in range(1, 99))
        self.runs = self.service()

    def service(self, raw: RawObservationPort | None = None) -> BacktestRunService:
        return BacktestRunService(
            PortfolioDesignService(
                raw or MockEquityDataAdapter.demo(),
                BacktestEnginePortfolioAdapter(),
                factor_metadata=MockEquityDataAdapter.demo(),
                factor_registry_version="test-registry",
            ),
            self._strategies,
            MockEquityDataAdapter.demo(),
            BacktestEngineExecutorAdapter(build_default_metric_registry()),
            LocalArtifactStore(self._tmp_path / "artifacts"),
            run_repository=SQLiteBacktestRunRepository(self.path),
            new_id=self._run_ids.__next__,
        )

    def save(self, spec: StrategySpec) -> SavedStrategy:
        return self._design.create(spec)

    def revise(self, saved: SavedStrategy, spec: StrategySpec) -> SavedStrategy:
        return self._design.revise(
            saved.spec.identity.strategy_id, spec, expected_revision=saved.spec.identity.revision
        )


def _spec(*, weight: float = 1.0) -> StrategySpec:
    template = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    return replace(
        template,
        factors=tuple(replace(factor, weight=weight) for factor in template.factors),
        # 해소된 파라미터 값이 시도 키를 가르는지 보려는 파라미터(그래프가 읽지 않아도 키에 든다).
        parameters=(FloatParameter("tilt", 0.5, 0.0, 1.0, kind="float"),),
        # 세션마다 리밸런싱해야 짧은 구간에서도 포지션이 생긴다.
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _saved_run(saved: SavedStrategy, **environment: object) -> BacktestRunSpec:
    identity = saved.spec.identity
    return BacktestRunSpec(
        strategy_source=SavedRevisionReference(
            identity.strategy_id, identity.revision, saved.spec_hash, "saved_revision"
        ),
        core=ExecutionCore.PYTHON,
        environment=replace(_ENVIRONMENT, **environment),
    )


def _finish(runs: BacktestRunService, request: BacktestRunSpec) -> str:
    run_id = runs.start(request).run.run_id
    wait_for_terminal_run(runs, run_id)
    return run_id


def _roles(ledger: TrialLedger) -> list[list[tuple[str, TrialRunRole]]]:
    return [[(run.run_id, run.role) for run in trial.runs] for trial in ledger.trials]


@pytest.fixture
def lab(tmp_path: Path) -> _Lab:
    return _Lab(tmp_path)


def test_reruns_extensions_and_unfavourable_costs_are_one_trial_and_the_ledger_survives_restart(
    lab: _Lab,
) -> None:
    saved = lab.save(_spec())
    runs = lab.runs
    first = _finish(runs, _saved_run(saved))
    _finish(runs, _saved_run(saved))
    _finish(runs, _saved_run(saved, end=date(2024, 1, 19)))
    _finish(runs, _saved_run(saved, fee_bps=40.0))
    # 제목만 바꿔 저장한 리비전도 같은 시도다.
    renamed = lab.revise(saved, replace(saved.spec, title="이름만 바꾼 리비전"))
    _finish(runs, _saved_run(renamed))

    ledger = runs.trial_ledger("s-1")

    assert ledger.trial_count == 1
    assert _roles(ledger) == [
        [
            ("run-1", TrialRunRole.COUNTED),
            ("run-2", TrialRunRole.RECHECK),
            ("run-3", TrialRunRole.RECHECK),
            ("run-4", TrialRunRole.RECHECK),
            ("run-5", TrialRunRole.RECHECK),
        ]
    ]
    (trial,) = ledger.trials
    full_sharpe = next(
        metric.value
        for metric in runs.result(first).metrics
        if metric.metric_id == "sharpe" and metric.scope is MetricScope.FULL
    )
    assert full_sharpe is not None
    assert trial.representative_sharpe == pytest.approx(full_sharpe / math.sqrt(252))
    assert trial.metric_registry_version == runs.result(first).manifest.metric_registry_version
    assert lab.service().trial_ledger("s-1") == ledger


def test_a_different_parameter_value_is_a_new_trial_and_the_preview_names_its_key(
    lab: _Lab,
) -> None:
    saved = lab.save(_spec())
    runs = lab.runs
    _finish(runs, _saved_run(saved))
    tilted = replace(_saved_run(saved), parameter_values={"tilt": 0.7})
    # 기본값을 명시한 요청은 생략한 요청과 같은 시도다.
    explicit_default = replace(_saved_run(saved), parameter_values={"tilt": 0.5})

    preview = runs.preview_trial(tilted)
    _finish(runs, tilted)
    _finish(runs, explicit_default)

    ledger = runs.trial_ledger("s-1")
    assert ledger.trial_count == 2
    assert [trial.trial_key for trial in ledger.trials][1] == preview.trial_key
    assert _roles(ledger) == [
        [("run-1", TrialRunRole.COUNTED), ("run-3", TrialRunRole.RECHECK)],
        [("run-2", TrialRunRole.COUNTED)],
    ]


def test_the_representative_sharpe_is_the_full_range_session_sharpe(lab: _Lab) -> None:
    saved = lab.save(_spec())
    request = replace(
        _saved_run(saved),
        annualization_days=250,
        metric_windows=(
            MetricWindow(MetricScope.OUT_OF_SAMPLE, date(2024, 1, 10), _ENVIRONMENT.end, "OOS"),
        ),
    )
    run_id = _finish(lab.runs, request)

    result = lab.runs.result(run_id)
    metrics = result.metrics
    by_scope = {metric.scope: metric.value for metric in metrics if metric.metric_id == "sharpe"}
    full = by_scope[MetricScope.FULL]
    assert full is not None and by_scope[MetricScope.OUT_OF_SAMPLE] != full
    (trial,) = lab.runs.trial_ledger("s-1").trials
    # 결과 샤프는 250일로 연율화됐다. 세션 단위로 되돌리려면 같은 250일로 나눈다.
    assert trial.representative_sharpe == pytest.approx(full / math.sqrt(250))
    # 지표 순서와 무관하게 전체 구간 샤프만 고른다.
    reordered = replace(result, metrics=tuple(reversed(metrics)))
    assert representative_sharpe(reordered) == pytest.approx(full / math.sqrt(250))


def test_the_preview_matches_what_the_run_then_records(lab: _Lab) -> None:
    saved = lab.save(_spec())
    runs = lab.runs
    _finish(runs, _saved_run(saved))
    cheaper = _saved_run(saved, fee_bps=5.0)

    before = runs.preview_trial(cheaper)
    _finish(runs, cheaper)
    after = runs.preview_trial(cheaper)

    ledger = runs.trial_ledger("s-1")
    assert (before.new_trial, before.trial_count, before.trial_count_after) == (True, 1, 2)
    assert ledger.trial_count == before.trial_count_after
    assert ledger.trials[-1].trial_key == before.trial_key == after.trial_key
    assert (after.new_trial, after.trial_count_after) == (False, 2)


def test_runs_without_a_result_are_recorded_but_not_counted(lab: _Lab) -> None:
    saved = lab.save(_spec())
    failing = RawLoadBarrier(
        MockEquityDataAdapter.demo(),
        failure=RawObservationUnavailableError(DataLoadStatus.NO_DATA, "no members"),
    )
    failing.release.set()
    _finish(lab.service(failing), _saved_run(saved))

    ledger = lab.runs.trial_ledger("s-1")

    assert ledger.trial_count == 0
    assert _roles(ledger) == [[("run-1", TrialRunRole.NO_RESULT)]]
    assert lab.runs.preview_trial(_saved_run(saved)).new_trial is True


def test_a_sealed_window_run_is_blocked_into_the_seal_ledger_but_a_preview_is_not(
    lab: _Lab,
) -> None:
    saved = lab.save(_spec())
    sealed = _saved_run(saved, start=date(2019, 6, 3))

    with pytest.raises(ResearchWindowViolationError):
        lab.runs.preview_trial(sealed)
    with pytest.raises(ResearchWindowViolationError):
        lab.runs.start(sealed)

    ledger = lab.service().trial_ledger("s-1")
    (blocked,) = ledger.blocked
    assert (blocked.lineage_id, blocked.start, blocked.spec_hash) == (
        "s-1",
        date(2019, 6, 3),
        saved.spec_hash,
    )
    assert (ledger.trial_count, ledger.trials) == (0, ())


def test_unresolvable_parameter_values_are_refused_before_any_ledger_record(
    lab: _Lab,
) -> None:
    saved = lab.save(_spec())
    unknown = replace(_saved_run(saved), parameter_values={"missing": 1.0})

    # 미리 계산은 시작과 같은 코드로 거절한다(`backtest.run.parameter_invalid`).
    with pytest.raises(BacktestParameterValueError, match="parameter_id=missing"):
        lab.runs.preview_trial(unknown)
    # 봉인 겹침과 해소 실패가 겹치면 봉인 겹침으로 거절하되, 전략이 확정되지 않았으니 남기지 않는다.
    with pytest.raises(ResearchWindowViolationError):
        lab.runs.start(replace(unknown, environment=replace(_ENVIRONMENT, start=date(2019, 6, 3))))

    assert lab.runs.trial_ledger("s-1").blocked == ()


def test_inline_drafts_join_a_lineage_only_when_they_name_one(lab: _Lab) -> None:
    saved = lab.save(_spec())
    other = lab.save(_spec(weight=2.0))
    draft = BacktestRunSpec(
        strategy_source=InlineDraft(saved.spec, "inline_draft", source_hash="a" * 64),
        core=ExecutionCore.PYTHON,
        environment=_ENVIRONMENT,
    )

    unlinked = lab.runs.preview_trial(draft)
    _finish(lab.runs, draft)
    _finish(lab.runs, replace(draft, lineage_strategy_id="s-1"))

    assert (unlinked.lineage_id, unlinked.new_trial) == (None, False)
    assert _roles(lab.runs.trial_ledger("s-1")) == [[("run-2", TrialRunRole.COUNTED)]]
    with pytest.raises(InvalidBacktestRunError, match="lineage_strategy_id=s-2"):
        lab.runs.start(
            replace(_saved_run(saved), lineage_strategy_id=other.spec.identity.strategy_id)
        )
    with pytest.raises(StrategyReferenceNotFoundError, match="lineage_strategy_id=s-9"):
        lab.runs.start(replace(draft, lineage_strategy_id="s-9"))


def test_merging_lineages_pools_their_trials_and_cannot_be_repeated(lab: _Lab) -> None:
    first = lab.save(_spec())
    second = lab.save(_spec(weight=2.0))
    _finish(lab.runs, _saved_run(first))
    _finish(lab.runs, _saved_run(second))
    _finish(lab.runs, _saved_run(second, fee_bps=5.0))

    merged = lab.runs.merge_lineages("s-2", "s-1")

    assert (merged.lineage_id, merged.merged_lineage_ids, merged.trial_count) == (
        "s-1",
        ("s-2",),
        3,
    )
    assert lab.runs.trial_ledger("s-2") == merged
    assert lab.runs.preview_trial(_saved_run(first, fee_bps=5.0)).new_trial is True
    with pytest.raises(TrialLineageAlreadyMergedError):
        lab.runs.merge_lineages("s-1", "s-2")
    with pytest.raises(StrategyNotFoundError):
        lab.runs.merge_lineages("s-9", "s-1")


def test_a_run_status_that_never_finished_stays_out_of_n_after_restart(lab: _Lab) -> None:
    saved = lab.save(_spec())
    held = RawLoadBarrier(MockEquityDataAdapter.demo())
    try:
        lab.service(held).start(_saved_run(saved))
        assert held.entered.wait(timeout=10)
        # 재시작: 끝나지 않은 run 은 `interrupted` 로 닫히고 원장에서 결과 없음이다. 앞 서비스의 run
        # 스레드가 풀려 파일을 다시 쓰기 전에 단언한다.
        reopened = lab.service()

        assert reopened.state("run-1").status is RunStatus.FAILED
        assert _roles(reopened.trial_ledger("s-1")) == [[("run-1", TrialRunRole.NO_RESULT)]]
    finally:
        held.release.set()
        join_run_thread("run-1")
