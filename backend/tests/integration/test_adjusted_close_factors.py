"""이슈 #214: 가격 변화를 재는 팩터는 수정주가 `price.adj_close`를 읽는다.

원주가 `price.close`는 분할·증자·병합 날 끊긴다. mock 어댑터의 `sec-005930-1`은 2018-05-04에
50:1 액면분할을 겪는 종목이라(실제 삼성전자 사건을 본떴다) 원주가 수익률은 그날 약 −98%를 찍고,
전방 조정 수정주가는 이어진다. 이 파일은 그 차이를 mock 계약으로 고정하고, 레지스트리의 가격 계열
팩터가 그 사건에 오염되지 않는지 파이프라인으로 확인한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.strategy.facade.specification import (
    DataStep,
    FactorDirection,
    FactorSignal,
    Market,
    RebalanceFrequency,
    StrategySpec,
)

SPLIT_SECURITY = "sec-005930-1"
SPLIT_DATE = date(2018, 5, 4)
SPLIT_RATIO = 50.0
# 사건 뒤 두 달 — 12-1 모멘텀(252 + 21 세션)과 60일 변동성 창이 모두 분할일을 품는다
WINDOW = (date(2018, 7, 2), date(2018, 7, 6))
AS_OF = WINDOW[1]

# 가격 변화(수익률·모멘텀·이평·변동성·낙폭·고점 거리·베타)를 재는 레지스트리 팩터
PRICE_CHANGE_FACTORS = (
    "price.momentum_12_1",
    "price.momentum_6_1",
    "price.reversal_1m",
    "price.volatility_60d",
    "price.beta_252d",
    "price.max_drawdown_252d",
    "price.distance_52w_high",
    "price.overnight_return_20d",
    "price.liquidity_amihud_20d",
)
# 같은 날 두 값을 나누는 절대 가격 비율 — 원주가끼리 견줘야 맞다
SAME_DAY_RAW_PRICE_FACTORS = (
    "price.intraday_return_20d",
    "consensus.target_price_upside",
    "event.dividend_yield_event",
)


def _raw(field_ids: tuple[str, ...], start: date, end: date) -> dict[tuple[date, str], float]:
    result = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market="KRX",
            universe_id="krx.common-stock",
            start=start,
            end=end,
            field_ids=field_ids,
        )
    )
    assert result.status.value == "ok", result.detail
    values: dict[tuple[date, str], float] = {}
    for observation in result.observations:
        if observation.security_id != SPLIT_SECURITY:
            continue
        for field in observation.fields:
            assert isinstance(field.value, float)
            values[(observation.as_of, field.field_id)] = field.value
    return values


def test_mock_split_breaks_raw_close_but_adjusted_close_stays_continuous() -> None:
    before, on = date(2018, 5, 3), SPLIT_DATE
    values = _raw(("price.close", "price.adj_close"), before, on)

    raw_return = values[(on, "price.close")] / values[(before, "price.close")] - 1
    adjusted_return = values[(on, "price.adj_close")] / values[(before, "price.adj_close")] - 1

    assert raw_return == pytest.approx(1 / SPLIT_RATIO - 1, abs=0.001)  # 약 −98%
    assert abs(adjusted_return) < 0.01
    # 전방 조정: 첫 관측 수준을 고정하고 사건 뒤 원주가에 계수를 곱한다
    assert values[(before, "price.adj_close")] == values[(before, "price.close")]
    assert values[(on, "price.adj_close")] == values[(on, "price.close")] * SPLIT_RATIO


def test_mock_adjusted_close_equals_raw_close_for_names_without_events() -> None:
    result = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market="KRX",
            universe_id="krx.common-stock",
            start=date(2018, 5, 3),
            end=date(2018, 5, 4),
            field_ids=("price.close", "price.adj_close"),
        )
    )
    assert result.status.value == "ok", result.detail
    others = [item for item in result.observations if item.security_id != SPLIT_SECURITY]
    assert others
    for observation in others:
        by_field = {field.field_id: field.value for field in observation.fields}
        assert by_field["price.adj_close"] == by_field["price.close"]


def test_mock_panel_serves_adjusted_close_inside_the_fixture_calendar() -> None:
    catalog = {profile.field_id for profile in MockEquityDataAdapter.demo().list_fields()}
    assert "price.adj_close" in catalog
    values = _raw(("price.close", "price.adj_close"), date(2024, 1, 8), date(2024, 1, 8))
    assert values[(date(2024, 1, 8), "price.adj_close")] == pytest.approx(
        values[(date(2024, 1, 8), "price.close")] * SPLIT_RATIO
    )


def test_registry_price_change_factors_read_adjusted_close() -> None:
    registry = build_default_factor_registry()
    for factor_id in PRICE_CHANGE_FACTORS:
        fields = registry.get(factor_id).required_field_ids
        assert "price.adj_close" in fields, factor_id
    momentum_graph = registry.get("price.momentum_12_1").default_graph
    assert momentum_graph is not None
    assert {node.field_id for node in momentum_graph.nodes if isinstance(node, FieldNode)} == {
        "price.adj_close"
    }


def test_same_day_price_ratios_keep_raw_close() -> None:
    registry = build_default_factor_registry()
    for factor_id in SAME_DAY_RAW_PRICE_FACTORS:
        fields = registry.get(factor_id).required_field_ids
        assert "price.close" in fields and "price.adj_close" not in fields, factor_id


def _spec(signal: FactorSignal) -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: AS_OF
    ).template()
    return replace(
        template,
        data=DataStep(
            market=Market.KRX, start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock"
        ),
        factors=(signal,),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _factor_value(graph: FactorGraph) -> float:
    registry = build_default_factor_registry()
    adapter = MockEquityDataAdapter.demo()
    service = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version=registry.version,
    )
    signal = FactorSignal(
        factor_id="probe",
        label="분할 검산",
        direction=FactorDirection.HIGH,
        weight=1.0,
        graph=graph,
    )
    result = service.run_pipeline(PortfolioPreviewRequest(_spec(signal)))
    value = next(
        item.value
        for item in result.factor_evaluations[0].values
        if (item.as_of, item.security_id) == (AS_OF, SPLIT_SECURITY)
    )
    assert value is not None
    return value


def test_registry_momentum_is_not_contaminated_by_a_split() -> None:
    """12-1 모멘텀 기본 그래프의 모멘텀 노드. 원주가라면 분할 하나로 약 −98%가 된다."""
    graph = build_default_factor_registry().get("price.momentum_12_1").default_graph
    assert graph is not None
    momentum_only = FactorGraph(
        nodes=tuple(node for node in graph.nodes if node.node_id != "rank"),
        output_node_id="momentum",
    )

    momentum = _factor_value(momentum_only)

    # mock 수정주가는 완만한 우상향 선형 추세라 12-1 수익률이 작은 양수다
    assert 0 < momentum < 0.5


def test_registry_volatility_input_is_not_contaminated_by_a_split() -> None:
    """60일 변동성: 레지스트리가 선언한 가격 필드의 일간 수익률 표준편차."""
    fields = build_default_factor_registry().get("price.volatility_60d").required_field_ids
    graph = FactorGraph(
        nodes=(
            FieldNode("px", fields[0], "field"),
            TimeSeriesNode("ret", TimeSeriesOperator.MOMENTUM, "px", 2, "time_series"),
            TimeSeriesNode("vol", TimeSeriesOperator.STANDARD_DEVIATION, "ret", 60, "time_series"),
        ),
        output_node_id="vol",
    )

    volatility = _factor_value(graph)

    # 원주가라면 −98% 수익률 하루가 창에 들어가 표준편차가 0.1을 넘는다
    assert volatility < 0.01
