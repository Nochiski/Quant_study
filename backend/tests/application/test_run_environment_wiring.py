"""P2-01·P2-03: `environment` 가 preview·trace·run 을 통과하는 경로.

1.2 부터 실행 설정은 요청만 싣는다(문서 브리지 없음). 그래서 이 파일이 고정하는 것은 두 가지다.
(1) 요청이 실은 값이 관측 조회·tape·엔진·매니페스트까지 **한 축으로** 내려간다. 한 곳이라도
문서 값으로 되돌아가면 매니페스트가 실행하지 않은 설정을 기록한다. (2) 실행 설정이 없으면 세
경로가 같은 코드로 거절한다 — 기본값을 지어내지 않는다.

P2-02 부터 `environment.missing` 도 같은 경로를 탄다: 실행 설정의 결측 정책이 팩터 실행 plan 과
`plan_hash` 까지 내려간다.

검증 랩 V1-01 부터 연구 구간 잠금도 같은 관문을 지난다: 측정 시작일이 2020-01-02 앞이면 세 경로가
`run_environment.research_window` 로 거절하고, 워밍업 관측 읽기는 막지 않는다(spec D1). V2-02 의
참여 기준(`participation_basis`)도 데이터 질의의 워밍업과 엔진 캡까지 같은 축으로 내려간다. V2-03 의
√ 시장충격(`impact_model`)도 워밍업과 엔진 체결가까지 같은 축이다.
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
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
    MarketBarRecord,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestResultNotReadyError,
    BacktestRunService,
    BacktestRunSpec,
    InvalidBacktestRunError,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
    RawObservationUnavailableError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
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
    ImpactModel,
    MissingRunEnvironmentError,
    ParticipationBasis,
    ResearchWindowViolationError,
    RunEnvironment,
    SellTax,
    environment_hash,
    impact_scales,
    participation_volumes,
    settlement_multipliers,
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
        run_repository=SQLiteBacktestRunRepository(),
        new_id=lambda: run_id,
    )


def test_preview_without_an_environment_is_a_coded_request_error() -> None:
    """P2-03: 문서에 기간·유니버스가 없으므로 되돌아갈 기본값이 없다."""
    with pytest.raises(MissingRunEnvironmentError) as info:
        _portfolio().run_pipeline(PortfolioPreviewRequest(_spec()))

    assert info.value.code == "run_environment.required"


def test_preflight_without_an_environment_is_refused_too() -> None:
    """엔진 능력 판정이 참여율을 읽으므로 preflight 도 문서만으로는 끝나지 않는다."""
    with pytest.raises(MissingRunEnvironmentError):
        _portfolio().preflight(PortfolioPreviewRequest(_spec()))


def test_trace_without_an_environment_is_refused_like_preview() -> None:
    """세 경로가 같은 domain 오류로 거절하고, inbound 가 같은 접수 거절 코드로 낸다(#351)."""
    traces = StrategyTraceService(_portfolio(), InMemoryStrategyRepository())

    with pytest.raises(MissingRunEnvironmentError, match="requested_by=strategy.trace"):
        traces.trace(
            StrategyTraceRequest(
                strategy_source=InlineDraft(_spec(), "inline_draft", "a" * 64),
                security_ids=("005930",),
                factor_id="momentum_3",
            )
        )


def test_run_without_an_environment_is_refused_before_it_is_queued(tmp_path: Path) -> None:
    runs = _runs(_portfolio(), tmp_path, "no-environment-run")

    with pytest.raises(MissingRunEnvironmentError, match="run_environment.required"):
        runs.start(BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON))


SEALED_LAST_DAY = date(2019, 12, 31)
RESEARCH_FLOOR = date(2020, 1, 2)


def test_preview_and_trace_measuring_the_sealed_window_are_coded_request_errors() -> None:
    """봉인 구간 마지막 날부터 측정하면 preview·trace 가 같은 domain 오류로 거절한다(spec D1)."""
    sealed = replace(_environment(), start=SEALED_LAST_DAY)
    traces = StrategyTraceService(_portfolio(), InMemoryStrategyRepository())

    with pytest.raises(ResearchWindowViolationError) as preview:
        _portfolio().run_pipeline(PortfolioPreviewRequest(_spec(), environment=sealed))
    with pytest.raises(ResearchWindowViolationError) as trace:
        traces.trace(
            StrategyTraceRequest(
                strategy_source=InlineDraft(_spec(), "inline_draft", "a" * 64),
                security_ids=("005930",),
                factor_id="momentum_3",
                environment=sealed,
            )
        )

    for info in (preview, trace):
        assert info.value.code == "run_environment.research_window"
        assert "got=start=2019-12-31" in str(info.value)


def test_run_measuring_the_sealed_window_is_refused_before_it_is_queued(tmp_path: Path) -> None:
    runs = _runs(_portfolio(), tmp_path, "sealed-run")
    sealed = replace(_environment(), start=SEALED_LAST_DAY)

    request = BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON, environment=sealed)

    with pytest.raises(ResearchWindowViolationError, match="research_window"):
        runs.start(request)


def test_metric_window_cannot_reach_back_into_the_sealed_window(tmp_path: Path) -> None:
    """metric window 는 실행 기간 안에만 있을 수 있고 실행 시작일은 연구 하한 이후다. 그래서 창
    시작일이 봉인 구간이면 실행 기간 밖이라 따로 판정하지 않아도 거절된다(spec D1 방어 검사)."""
    runs = _runs(_portfolio(), tmp_path, "sealed-window-run")
    window = MetricWindow(scope=MetricScope.OUT_OF_SAMPLE, start=SEALED_LAST_DAY, end=WINDOW[1])

    with pytest.raises(InvalidBacktestRunError, match="metric window exceeds the run range"):
        runs.start(
            BacktestRunSpec(
                strategy=_spec(),
                core=ExecutionCore.PYTHON,
                environment=replace(_environment(), start=RESEARCH_FLOOR),
                metric_windows=(window,),
            )
        )


class _QueryRecorded(Exception):
    pass


class _RecordingRawPort:
    """파이프라인이 만든 관측 조회를 기록하고 멈춘다. 데이터 없이 잠금 통과·워밍업 요청만 본다."""

    def __init__(self) -> None:
        self.queries: list[RawObservationQuery] = []

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        self.queries.append(query)
        raise _QueryRecorded


def test_warmup_before_the_research_floor_is_read_not_measured() -> None:
    """3세션 모멘텀은 as_of 를 포함해 3세션이 필요하므로 시작일 앞 2세션(2019-12-27·12-30, 봉인
    구간)을 읽는다. 워밍업은 측정이 아니므로 잠금이 막지 않는다(spec D1)."""
    source = _RecordingRawPort()
    metadata = MockEquityDataAdapter.demo()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=metadata,
        factor_registry_version="test-registry",
    )

    with pytest.raises(_QueryRecorded):
        portfolio.run_pipeline(
            PortfolioPreviewRequest(
                _spec(), environment=replace(_environment(), start=RESEARCH_FLOOR)
            )
        )

    (query,) = source.queries
    assert (query.start, query.history_sessions_before_start) == (RESEARCH_FLOOR, 2)


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


@pytest.mark.parametrize("core", [ExecutionCore.PYTHON, ExecutionCore.RUST])
def test_sell_tax_reaches_the_engine_and_is_reported_apart_from_fees(
    tmp_path: Path, core: ExecutionCore
) -> None:
    """실행 설정의 거래세(V2-01)가 엔진까지 내려가 매도 체결마다 비용 기록을 남기고, 지표는 수수료·
    대차 비용과 따로 센다. 매 세션 리밸런싱이고 1월 한 달이면 창 안에 매도가 있다."""
    environment = replace(
        _environment(), end=date(2024, 1, 31), sell_tax=SellTax.CUSTOM, sell_tax_bps=50.0
    )
    runs = _runs(_portfolio(), tmp_path, "taxed-run")
    runs.start(BacktestRunSpec(strategy=_spec(), core=core, environment=environment))
    assert wait_for_terminal_run(runs, "taxed-run").status.value == "completed"

    result = runs.result("taxed-run")
    sells = [fill for fill in result.artifacts.fills if fill.side == "sell"]
    taxes = [cost for cost in result.artifacts.costs if cost.kind == "sell_tax"]
    assert sells
    assert [(cost.session, cost.security_id) for cost in taxes] == [
        (fill.session, fill.security_id) for fill in sells
    ]
    assert [cost.amount for cost in taxes] == pytest.approx(
        [float(fill.quantity) * fill.price * 0.005 for fill in sells]
    )
    full = {m.metric_id: m.value for m in result.metrics if m.scope is MetricScope.FULL}
    assert full["total_taxes"] == pytest.approx(sum(cost.amount for cost in taxes))
    assert full["total_fees"] == pytest.approx(sum(fill.fee for fill in result.artifacts.fills))
    assert full["total_carry_cost"] == 0.0


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


class _ShrunkWarmupValue:
    """mock 워밍업 bar 의 거래대금만 1% 로 줄인다 — 워밍업을 ADV 에 넣었는지에 따라 첫 체결 세션의
    캡이 크게 갈린다. 받은 질의와 돌려준 dataset 을 남긴다."""

    def __init__(self) -> None:
        self._inner = MockEquityDataAdapter.demo()
        self.loads: list[tuple[BacktestDataQuery, BacktestDataset]] = []

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        dataset = self._inner.load_backtest_dataset(query)
        dataset = replace(
            dataset,
            history_bars=tuple(
                replace(bar, trading_value=(bar.trading_value or 0) * 0.01)
                for bar in dataset.history_bars
            ),
        )
        self.loads.append((query, dataset))
        return dataset


def _adv_caps(
    environment: RunEnvironment, bars: tuple[MarketBarRecord, ...], rate: float
) -> dict[tuple[date, str], int]:
    volumes = participation_volumes(
        environment,
        ((bar.session, bar.security_id, bar.close, bar.trading_value) for bar in bars),
    )
    assert volumes is not None
    return {key: math.floor(volume * rate) for key, volume in volumes.items()}


@pytest.mark.parametrize("core", [ExecutionCore.PYTHON, ExecutionCore.RUST])
def test_adv20_reads_twenty_warmup_sessions_and_caps_fills_by_them(
    tmp_path: Path, core: ExecutionCore
) -> None:
    """`adv20`(V2-02)이면 실행이 start 앞 20세션을 워밍업으로 읽고, 체결은 워밍업 행까지 포함해 센
    기준 거래량 × 참여율을 넘지 않는다. 첫 체결 세션의 체결량은 워밍업을 넣은 캡과 같고 뺀 캡과
    다르다 — 엔진 어댑터가 워밍업 bar 를 빠뜨리면 여기서 걸린다."""
    rate = 1e-4
    environment = replace(
        _environment(), participation_basis=ParticipationBasis.ADV20, participation_rate=rate
    )
    data = _ShrunkWarmupValue()
    runs = BacktestRunService(
        _portfolio(),
        InMemoryStrategyRepository(),
        data,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        run_repository=SQLiteBacktestRunRepository(),
        new_id=lambda: "adv-run",
    )
    runs.start(BacktestRunSpec(strategy=_spec(), core=core, environment=environment))
    assert wait_for_terminal_run(runs, "adv-run").status.value == "completed"

    ((query, dataset),) = data.loads
    assert query.history_sessions_before_start == 20
    assert len({bar.session for bar in dataset.history_bars}) == 20
    warmed = _adv_caps(environment, (*dataset.history_bars, *dataset.bars), rate)
    cold = _adv_caps(environment, dataset.bars, rate)
    fills = runs.result("adv-run").artifacts.fills
    assert fills
    assert all(int(fill.quantity) <= warmed[(fill.session, fill.security_id)] for fill in fills)
    first = [fill for fill in fills if fill.session == fills[0].session]
    assert [int(fill.quantity) for fill in first] == [
        warmed[(fill.session, fill.security_id)] for fill in first
    ]
    assert all(
        warmed[(fill.session, fill.security_id)] != cold[(fill.session, fill.security_id)]
        for fill in first
    )


class _WarmupSplit(_ShrunkWarmupValue):
    """워밍업 가운데 세션에 종목마다 자본변동을 하나 싣는다 — σ 가 그 수익률을 빼는지 본다."""

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        dataset = super().load_backtest_dataset(query)
        middle = sorted({bar.session for bar in dataset.history_bars})[10]
        dataset = replace(
            dataset,
            history_corporate_actions=tuple(
                CorporateActionRecord(middle, security_id, "split", "2.0", f"{security_id}:split")
                for security_id in sorted({bar.security_id for bar in dataset.history_bars})
            ),
        )
        self.loads[-1] = (query, dataset)
        return dataset


def _impact(
    environment: RunEnvironment,
    bars: tuple[MarketBarRecord, ...],
    actions: tuple[CorporateActionRecord, ...] = (),
) -> dict[tuple[date, str], float]:
    rows = [(bar.session, bar.security_id, bar.close, bar.trading_value) for bar in bars]
    confirmed = ("split", "reverse_split")
    scales = impact_scales(
        environment,
        rows,
        settlement_multipliers(
            rows,
            (
                (
                    action.session,
                    action.security_id,
                    float(action.ratio) if action.action_type in confirmed else 1.0,
                )
                for action in actions
            ),
        ),
    )
    assert scales is not None
    return scales


@pytest.mark.parametrize("core", [ExecutionCore.PYTHON, ExecutionCore.RUST])
def test_sqrt_impact_reads_warmup_and_prices_fills_with_the_domain_scale(
    tmp_path: Path, core: ExecutionCore
) -> None:
    """`sqrt`(V2-03)이면 실행이 start 앞 21세션을 워밍업으로 읽고, 체결가는 시가에서 시가 × 척도 ×
    √수량만큼 밀린다(이 창은 매수뿐이고 방향은 `test_sqrt_impact.py` 가 덮는다). `slippage_bps` 는
    쓰지 않는다. 척도는 워밍업 행까지 포함해 domain 이 센 값이고, 워밍업 bar 나 워밍업 자본변동을 뺀
    척도와 첫 체결 세션에서 다르다 — 엔진 어댑터가 둘 중 하나라도 빠뜨리면 여기서 걸린다."""
    environment = replace(_environment(), impact_model=ImpactModel.SQRT, slippage_bps=500.0)
    data = _WarmupSplit()
    runs = BacktestRunService(
        _portfolio(),
        InMemoryStrategyRepository(),
        data,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        run_repository=SQLiteBacktestRunRepository(),
        new_id=lambda: "impact-run",
    )
    runs.start(BacktestRunSpec(strategy=_spec(), core=core, environment=environment))
    assert wait_for_terminal_run(runs, "impact-run").status.value == "completed"

    ((query, dataset),) = data.loads
    assert query.history_sessions_before_start == 21
    history = (*dataset.history_bars, *dataset.bars)
    warmed = _impact(environment, history, dataset.history_corporate_actions)
    unsplit = _impact(environment, history)
    cold = _impact(environment, dataset.bars)
    opens = {(bar.session, bar.security_id): bar.open for bar in dataset.bars}
    fills = runs.result("impact-run").artifacts.fills
    assert fills
    for fill in fills:
        key = (fill.session, fill.security_id)
        slip = opens[key] * warmed[key] * math.sqrt(float(fill.quantity))
        assert slip > 0
        assert fill.slippage_per_share == pytest.approx(slip)
        assert fill.price == pytest.approx(opens[key] + (slip if fill.side == "buy" else -slip))
    first = (fills[0].session, fills[0].security_id)
    assert warmed[first] != cold[first]
    assert warmed[first] != unsplit[first]
