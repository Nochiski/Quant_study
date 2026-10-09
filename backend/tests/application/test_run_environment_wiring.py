"""P2-01: optional `environment` 가 preview·trace·run 을 통과하는 경로.

두 가지를 고정한다. (1) 브리지 동등성 — `environment` 를 주지 않은 1.1 요청은 브리지가 만든
값을 명시한 요청과 같은 결과를 낸다. (2) 우선순위 — 명시한 `environment` 가 문서의
`data`·`execution` 보다 먼저 읽힌다.

P2-02 부터 `environment.missing` 도 같은 경로를 탄다: 실행 설정의 결측 정책이 팩터 실행 plan 과
`plan_hash` 까지 내려간다.
"""

from __future__ import annotations

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
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestResultNotReadyError,
    BacktestRunService,
    BacktestRunSpec,
    InvalidBacktestRunError,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPreviewRequest,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    InvalidStrategyTraceRequestError,
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import (
    MetricScope,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import (
    environment_from_legacy_spec,
    environment_hash,
)
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore, MetricWindow
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft
from strategy_workbench.domain.strategy.facade.specification import (
    DataStep,
    FactorDirection,
    FactorSignal,
    Market,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import wait_for_terminal_run

WINDOW = (date(2024, 1, 8), date(2024, 1, 12))


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: WINDOW[1]
    ).template()
    momentum = FactorSignal(
        factor_id="momentum_3",
        label="3세션 모멘텀",
        direction=FactorDirection.HIGH,
        weight=1.0,
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
            ),
            output_node_id="mom",
        ),
    )
    return replace(
        template,
        data=DataStep(
            market=Market.KRX, start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock"
        ),
        factors=(momentum,),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _portfolio() -> PortfolioDesignService:
    adapter = MockEquityDataAdapter.demo()
    return PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )


def _runs(portfolio: PortfolioDesignService, tmp_path: Path, run_id: str) -> BacktestRunService:
    return BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        MockEquityDataAdapter.demo(),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=lambda: run_id,
    )


def test_bridged_and_explicit_environment_preview_identically() -> None:
    spec = _spec()

    bridged = _portfolio().run_pipeline(PortfolioPreviewRequest(spec))
    explicit = _portfolio().run_pipeline(
        PortfolioPreviewRequest(spec, environment=environment_from_legacy_spec(spec))
    )

    assert bridged.preview.tape.frames
    assert bridged.preview.tape.tape_hash == explicit.preview.tape.tape_hash
    assert bridged.observations == explicit.observations


def test_explicit_environment_narrows_the_observation_window() -> None:
    spec = _spec()
    narrowed = replace(environment_from_legacy_spec(spec), end=date(2024, 1, 10))

    full = _portfolio().run_pipeline(PortfolioPreviewRequest(spec))
    cut = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=narrowed))

    assert max(item.as_of for item in cut.observations) <= date(2024, 1, 10)
    assert len(cut.preview.tape.frames) < len(full.preview.tape.frames)


def test_trace_reads_the_explicit_environment_range() -> None:
    spec = _spec()
    portfolio = _portfolio()
    traces = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    source = InlineDraft(spec, "inline_draft", "a" * 64)
    narrowed = replace(environment_from_legacy_spec(spec), end=date(2024, 1, 10))

    with pytest.raises(InvalidStrategyTraceRequestError, match="outside the run range"):
        traces.trace(
            StrategyTraceRequest(
                strategy_source=source,
                security_ids=("005930",),
                factor_id="momentum_3",
                as_of=WINDOW[1],
                environment=narrowed,
            )
        )


def test_manifest_records_the_environment_it_ran_with(tmp_path: Path) -> None:
    spec = _spec()
    runs = _runs(_portfolio(), tmp_path, "bridged-run")

    accepted = runs.start(BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON))
    state = wait_for_terminal_run(runs, accepted.run.run_id)

    assert state.status.value == "completed", state
    manifest = runs.result(accepted.run.run_id).manifest
    expected = environment_from_legacy_spec(spec)
    assert manifest.environment == expected
    assert manifest.environment_hash == environment_hash(expected)
    assert manifest.run_spec.environment == expected


def test_explicit_costs_reach_the_engine_and_the_run_fingerprint(tmp_path: Path) -> None:
    spec = _spec()
    dearer = replace(environment_from_legacy_spec(spec), fee_bps=250.0, slippage_bps=250.0)

    cheap = _runs(_portfolio(), tmp_path / "cheap", "cheap-run")
    expensive = _runs(_portfolio(), tmp_path / "dear", "dear-run")
    cheap.start(BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON))
    expensive.start(BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=dearer))
    assert wait_for_terminal_run(cheap, "cheap-run").status.value == "completed"
    assert wait_for_terminal_run(expensive, "dear-run").status.value == "completed"

    cheap_manifest = cheap.result("cheap-run").manifest
    dear_manifest = expensive.result("dear-run").manifest
    assert dear_manifest.environment_hash == environment_hash(dearer)
    assert (dear_manifest.fee_bps, dear_manifest.slippage_bps) == (250.0, 250.0)
    assert dear_manifest.run_fingerprint != cheap_manifest.run_fingerprint
    # 같은 전략을 다른 실행 설정으로 돌렸다: 전략 hash 는 그대로, 실행 설정 hash 만 갈린다.
    assert dear_manifest.strategy_hash == cheap_manifest.strategy_hash
    assert dear_manifest.environment_hash != cheap_manifest.environment_hash


def test_metric_window_is_checked_against_the_explicit_environment(tmp_path: Path) -> None:
    spec = _spec()
    widened = replace(environment_from_legacy_spec(spec), end=date(2024, 3, 29))
    window = MetricWindow(scope=MetricScope.OUT_OF_SAMPLE, start=WINDOW[1], end=date(2024, 3, 1))
    runs = _runs(_portfolio(), tmp_path, "window-run")

    # 창이 문서의 `data.end` 는 넘지만 명시한 실행 기간 안이므로 접수된다. 그리고 실제로 그
    # 구간까지 리밸런싱한다 — 접수만 되고 tape 가 문서 구간에서 멈추면 OOS 지표가 전략이 한
    # 번도 매매하지 않은 구간 위에서 계산된다(리뷰 P0).
    accepted = runs.start(
        BacktestRunSpec(
            strategy=spec,
            core=ExecutionCore.PYTHON,
            environment=widened,
            metric_windows=(window,),
        )
    )
    assert accepted.run.run_id == "window-run"
    assert wait_for_terminal_run(runs, "window-run").status.value == "completed"
    manifest = runs.result("window-run").manifest
    assert manifest.environment.end == date(2024, 3, 29)

    with pytest.raises(InvalidBacktestRunError, match="metric window exceeds"):
        _runs(_portfolio(), tmp_path / "bridged", "bridged-window-run").start(
            BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, metric_windows=(window,))
        )


def test_explicit_environment_extends_the_tape_past_the_document_range() -> None:
    """명시 `environment` 가 tape 파이프라인까지 가야 한다. 안 가면 매니페스트·데이터셋은 넓은
    구간을, tape 는 문서 구간을 쓰고 뒷구간이 신호 없는 buy-and-hold 가 된다(리뷰 P0)."""
    spec = _spec()
    widened = replace(environment_from_legacy_spec(spec), end=date(2024, 3, 29))

    bridged = _portfolio().run_pipeline(PortfolioPreviewRequest(spec))
    extended = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=widened))

    assert max(frame.signal_as_of for frame in bridged.preview.tape.frames) <= WINDOW[1]
    assert max(frame.signal_as_of for frame in extended.preview.tape.frames) > WINDOW[1]


def test_run_with_an_unknown_universe_fails_the_way_preview_does(tmp_path: Path) -> None:
    """미리보기가 거부하는 실행 설정을 run 이 completed 로 기록하면 매니페스트가 허위가 된다.
    같은 환경이면 두 경로가 같은 사유로 실패해야 한다(리뷰 P0)."""
    spec = _spec()
    bogus = replace(environment_from_legacy_spec(spec), universe_id="totally.bogus.universe")

    with pytest.raises(RawObservationUnavailableError):
        _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=bogus))

    runs = _runs(_portfolio(), tmp_path, "bogus-universe-run")
    runs.start(BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=bogus))
    state = wait_for_terminal_run(runs, "bogus-universe-run")

    assert state.status.value == "failed", state
    assert state.error_code == "portfolio.data.unavailable"
    with pytest.raises(BacktestResultNotReadyError):
        runs.result("bogus-universe-run")


def test_environment_missing_reaches_the_factor_execution_plan() -> None:
    """실행 설정의 결측 정책이 plan 과 `plan_hash` 로 내려간다(캐시 키 회귀)."""
    spec = _spec()
    zeroed = replace(environment_from_legacy_spec(spec), missing=MissingPolicy.ZERO)

    bridged = _portfolio().run_pipeline(PortfolioPreviewRequest(spec))
    explicit = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=zeroed))

    bridged_plan = bridged.factor_evaluations[0].plan
    explicit_plan = explicit.factor_evaluations[0].plan
    assert (bridged_plan.missing_policy, explicit_plan.missing_policy) == ("drop", "zero")
    # 결측 처리만 다른 두 실행이 같은 팩터 행렬 캐시 키를 공유하면 두 번째가 첫 결과를 재사용한다.
    assert bridged_plan.plan_hash != explicit_plan.plan_hash
    assert bridged_plan.graph_hash == explicit_plan.graph_hash


def test_conflicting_legacy_missing_policies_are_refused_as_an_invalid_request() -> None:
    """팩터마다 결측 정책이 다른 1.1 문서는 조용히 하나를 고르지 않고 거부된다."""
    spec = _spec()
    conflicting = replace(
        spec,
        factors=(
            spec.factors[0],
            replace(
                spec.factors[0],
                factor_id="momentum_3_zero",
                graph=replace(spec.factors[0].graph, missing_policy=MissingPolicy.ZERO),
            ),
        ),
    )

    with pytest.raises(InvalidPortfolioRequestError) as info:
        _portfolio().preflight(PortfolioPreviewRequest(conflicting))

    codes = [issue.code for issue in info.value.validation.issues]
    assert codes == ["run_environment.missing_policy_conflict"]
