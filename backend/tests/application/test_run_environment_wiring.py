"""P2-01·P2-03: `environment` 가 preview·trace·run 을 통과하는 경로.

1.2 부터 실행 설정은 요청만 싣는다(문서 브리지 없음). 그래서 이 파일이 고정하는 것은 두 가지다.
(1) 요청이 실은 값이 관측 조회·tape·엔진·매니페스트까지 **한 축으로** 내려간다. 한 곳이라도
문서 값으로 되돌아가면 매니페스트가 실행하지 않은 설정을 기록한다. (2) 실행 설정이 없으면 세
경로가 같은 코드로 거절한다 — 기본값을 지어내지 않는다.

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
    MissingBacktestRunEnvironmentError,
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
    RunEnvironment,
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
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import wait_for_terminal_run

WINDOW = (date(2024, 1, 8), date(2024, 1, 12))


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
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
        factors=(momentum,),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _environment() -> RunEnvironment:
    return RunEnvironment(start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock")


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


def test_preview_without_an_environment_is_a_coded_request_error() -> None:
    """P2-03: 문서에 기간·유니버스가 없으므로 되돌아갈 기본값이 없다."""
    with pytest.raises(InvalidPortfolioRequestError) as info:
        _portfolio().run_pipeline(PortfolioPreviewRequest(_spec()))

    issues = info.value.validation.issues
    assert [issue.code for issue in issues] == ["run_environment.required"]
    assert issues[0].path == "environment"


def test_preflight_without_an_environment_is_refused_too() -> None:
    """엔진 능력 판정이 참여율을 읽으므로 preflight 도 문서만으로는 끝나지 않는다."""
    with pytest.raises(InvalidPortfolioRequestError) as info:
        _portfolio().preflight(PortfolioPreviewRequest(_spec()))

    assert [issue.code for issue in info.value.validation.issues] == ["run_environment.required"]


def test_trace_without_an_environment_is_refused() -> None:
    traces = StrategyTraceService(_portfolio(), InMemoryStrategyRepository())

    with pytest.raises(InvalidStrategyTraceRequestError, match="run_environment.required"):
        traces.trace(
            StrategyTraceRequest(
                strategy_source=InlineDraft(_spec(), "inline_draft", "a" * 64),
                security_ids=("005930",),
                factor_id="momentum_3",
            )
        )


def test_run_without_an_environment_is_refused_before_it_is_queued(tmp_path: Path) -> None:
    runs = _runs(_portfolio(), tmp_path, "no-environment-run")

    with pytest.raises(MissingBacktestRunEnvironmentError, match="run_environment.required"):
        runs.start(BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON))


def test_explicit_environment_narrows_the_observation_window() -> None:
    spec = _spec()
    narrowed = replace(_environment(), end=date(2024, 1, 10))

    full = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    cut = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=narrowed))

    assert max(item.as_of for item in cut.observations) <= date(2024, 1, 10)
    assert len(cut.preview.tape.frames) < len(full.preview.tape.frames)


def test_trace_reads_the_explicit_environment_range() -> None:
    spec = _spec()
    traces = StrategyTraceService(_portfolio(), InMemoryStrategyRepository())
    source = InlineDraft(spec, "inline_draft", "a" * 64)
    narrowed = replace(_environment(), end=date(2024, 1, 10))

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
    environment = _environment()
    runs = _runs(_portfolio(), tmp_path, "explicit-run")

    accepted = runs.start(
        BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=environment)
    )
    state = wait_for_terminal_run(runs, accepted.run.run_id)

    assert state.status.value == "completed", state
    manifest = runs.result(accepted.run.run_id).manifest
    assert manifest.environment == environment
    assert manifest.environment_hash == environment_hash(environment)
    assert manifest.run_spec.environment == environment


def test_explicit_costs_reach_the_engine_and_the_run_fingerprint(tmp_path: Path) -> None:
    spec = _spec()
    cheap_env = _environment()
    dearer = replace(cheap_env, fee_bps=250.0, slippage_bps=250.0)

    cheap = _runs(_portfolio(), tmp_path / "cheap", "cheap-run")
    expensive = _runs(_portfolio(), tmp_path / "dear", "dear-run")
    cheap.start(BacktestRunSpec(strategy=spec, core=ExecutionCore.PYTHON, environment=cheap_env))
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
    widened = replace(_environment(), end=date(2024, 3, 29))
    window = MetricWindow(scope=MetricScope.OUT_OF_SAMPLE, start=WINDOW[1], end=date(2024, 3, 1))
    runs = _runs(_portfolio(), tmp_path, "window-run")

    # 창이 넓힌 실행 기간 안이므로 접수된다. 그리고 실제로 그 구간까지 리밸런싱한다 — 접수만
    # 되고 tape 가 좁은 구간에서 멈추면 OOS 지표가 전략이 한 번도 매매하지 않은 구간 위에서
    # 계산된다(P2-01 리뷰 P0).
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
        _runs(_portfolio(), tmp_path / "narrow", "narrow-window-run").start(
            BacktestRunSpec(
                strategy=spec,
                core=ExecutionCore.PYTHON,
                environment=_environment(),
                metric_windows=(window,),
            )
        )


def test_explicit_environment_extends_the_tape_past_the_narrow_range() -> None:
    """명시 `environment` 가 tape 파이프라인까지 가야 한다. 안 가면 매니페스트·데이터셋은 넓은
    구간을, tape 는 좁은 구간을 쓰고 뒷구간이 신호 없는 buy-and-hold 가 된다(P2-01 리뷰 P0)."""
    spec = _spec()
    widened = replace(_environment(), end=date(2024, 3, 29))

    narrow = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=_environment()))
    extended = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=widened))

    assert max(frame.signal_as_of for frame in narrow.preview.tape.frames) <= WINDOW[1]
    assert max(frame.signal_as_of for frame in extended.preview.tape.frames) > WINDOW[1]


def test_run_with_an_unknown_universe_fails_the_way_preview_does(tmp_path: Path) -> None:
    """미리보기가 거부하는 실행 설정을 run 이 completed 로 기록하면 매니페스트가 허위가 된다.
    같은 환경이면 두 경로가 같은 사유로 실패해야 한다(P2-01 리뷰 P0)."""
    spec = _spec()
    bogus = replace(_environment(), universe_id="totally.bogus.universe")

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
    dropped = _environment()
    zeroed = replace(dropped, missing=MissingPolicy.ZERO)

    default = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=dropped))
    explicit = _portfolio().run_pipeline(PortfolioPreviewRequest(spec, environment=zeroed))

    default_plan = default.factor_evaluations[0].plan
    explicit_plan = explicit.factor_evaluations[0].plan
    assert (default_plan.missing_policy, explicit_plan.missing_policy) == ("drop", "zero")
    # 결측 처리만 다른 두 실행이 같은 팩터 행렬 캐시 키를 공유하면 두 번째가 첫 결과를 재사용한다.
    assert default_plan.plan_hash != explicit_plan.plan_hash
    assert default_plan.graph_hash == explicit_plan.graph_hash


def test_partial_fill_is_declared_from_the_environment_not_the_document() -> None:
    """P2-03 잔여 이관: 참여율의 owner 가 실행 설정이다.

    틀리는 방향이 과소 선언이라 그쪽으로 고정한다 — 참여율 1.0 짜리 요구 집합을 쓰면서 실제로는
    0.1 로 체결하면 능력 게이트가 조용히 약해진다.
    """
    spec = _spec()
    full = replace(_environment(), participation_rate=1.0)
    partial = replace(_environment(), participation_rate=0.1)
    bridge = BacktestEnginePortfolioAdapter()

    assert "partial_fill" not in {item.value for item in bridge.requirements(spec, full).features}
    assert "partial_fill" in {item.value for item in bridge.requirements(spec, partial).features}
