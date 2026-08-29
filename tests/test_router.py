"""DecisionRouter 단위 테스트: 검증, 수량 변환, REPLACE/PATCH, 청산."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.orders import OrderManager
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.errors import (
    SchemaVersionMismatch,
    UndeclaredActionReturned,
    UndeclaredFeatureUsed,
    UnsupportedActionValue,
)
from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    CancelOrder,
    ExecutionPolicy,
    ExecutionStyle,
    ExecutionTiming,
    LiquidatePosition,
    LiquidationPersistence,
    NotionalDelta,
    NotionalTarget,
    QuantityDelta,
    QuantityTarget,
    SetPortfolioTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.instruments import InstrumentId, Money
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCore,
    OrderType,
    Side,
    StopLimitOrderRequest,
    TimeInForce,
)
from backtest_engine.types.portfolio import PortfolioSnapshot, Position
from backtest_engine.types.requirements import EngineFeature
from tests.conftest import day, make_bar, make_instrument, make_snapshot

INSTRUMENT = make_instrument()
OTHER = make_instrument("000660")
DECLARED = frozenset(
    {
        ActionKind.NO_ACTION,
        ActionKind.SET_PORTFOLIO_TARGET,
        ActionKind.LIQUIDATE_POSITION,
        ActionKind.SET_POSITION_TARGET,
        ActionKind.ADJUST_POSITION,
        ActionKind.SUBMIT_ORDER,
    }
)
DECLARED_FEATURES = frozenset({EngineFeature.LIMIT_ORDER, EngineFeature.STOP_ORDER})


def make_router(
    order_manager: OrderManager | None = None,
    features: frozenset[EngineFeature] = DECLARED_FEATURES,
) -> DecisionRouter:
    return DecisionRouter(DECLARED, order_manager or OrderManager(), features)


def krw(amount: float) -> Money:
    return Money(Decimal(str(amount)), "KRW")


def route_one(
    action: StrategyAction, portfolio: PortfolioSnapshot, price: float = 100.0
) -> tuple[OrderEvent, ...]:
    decision = StrategyDecision.of(day(1), action)
    return make_router().route(decision, "D-000001", portfolio, market(price)).orders


def portfolio_with(
    cash: float, holdings: dict[InstrumentId, tuple[int, float]] | None = None
) -> PortfolioSnapshot:
    """holdings: instrument → (수량, 현재가)."""
    positions = tuple(
        Position(
            instrument=instrument,
            quantity=Decimal(quantity),
            average_price=price,
            market_price=price,
            market_value=quantity * price,
            unrealized_pnl=0.0,
        )
        for instrument, (quantity, price) in (holdings or {}).items()
    )
    equity = cash + sum(position.market_value for position in positions)
    return PortfolioSnapshot(
        ts=day(1),
        cash=cash,
        positions=positions,
        equity=equity,
        gross_exposure=sum(p.market_value for p in positions) / equity if equity else 0.0,
    )


def market(price: float = 100.0) -> MarketSnapshot:
    return make_snapshot(day(1), make_bar(day(1), INSTRUMENT, price, price))


def weight_decision(weight: float, scope: TargetScope = TargetScope.PATCH) -> StrategyDecision:
    return StrategyDecision.of(
        day(1),
        SetPortfolioTarget(
            targets=(WeightTarget(INSTRUMENT, weight),),
            scope=scope,
            execution=ExecutionPolicy.market_next_open(),
        ),
    )


def test_schema_version_mismatch_rejected() -> None:
    decision = StrategyDecision(schema_version=99, as_of=day(1), actions=())
    with pytest.raises(SchemaVersionMismatch, match="got 99"):
        make_router().route(decision, "D-000001", portfolio_with(100_000), market())


def test_undeclared_action_kind_rejected() -> None:
    decision = StrategyDecision.of(day(1), CancelOrder(order_id="O-000001"))
    with pytest.raises(UndeclaredActionReturned, match="cancel_order"):
        make_router().route(decision, "D-000001", portfolio_with(100_000), market())


def test_buy_quantity_floors_to_integer_shares() -> None:
    # 70% of 100,000 = 70,000 → 70,000/9,999 = 7.0007주 → 7주
    result = make_router().route(
        weight_decision(0.7), "D-000001", portfolio_with(100_000), market(9_999.0)
    )
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.side is Side.BUY
    assert order.quantity == 7


def test_sell_capped_at_held_quantity() -> None:
    portfolio = portfolio_with(0.0, {INSTRUMENT: (10, 100.0)})
    result = make_router().route(weight_decision(0.0), "D-000001", portfolio, market())
    assert len(result.orders) == 1
    assert result.orders[0].side is Side.SELL
    assert result.orders[0].quantity == 10


def test_replace_scope_flattens_unlisted_positions() -> None:
    portfolio = portfolio_with(0.0, {OTHER: (5, 200.0)})
    decision = StrategyDecision.of(
        day(1),
        SetPortfolioTarget(
            targets=(WeightTarget(INSTRUMENT, 0.0),),
            scope=TargetScope.REPLACE,
            execution=ExecutionPolicy.market_next_open(),
        ),
    )
    snapshot = make_snapshot(
        day(1),
        make_bar(day(1), INSTRUMENT, 100.0, 100.0),
        make_bar(day(1), OTHER, 200.0, 200.0),
    )
    result = make_router().route(decision, "D-000001", portfolio, snapshot)
    # REPLACE: 목록에 없는 OTHER 보유분에 암묵적 0 목표 → 전량 매도
    assert [(o.instrument.symbol, o.side, o.quantity) for o in result.orders] == [
        ("000660", Side.SELL, Decimal(5))
    ]


def test_patch_scope_ignores_unlisted_positions() -> None:
    portfolio = portfolio_with(0.0, {OTHER: (5, 200.0)})
    result = make_router().route(weight_decision(0.0), "D-000001", portfolio, market())
    assert result.orders == ()


def test_negative_weight_requires_short_selling() -> None:
    with pytest.raises(UnsupportedActionValue, match="SHORT_SELLING"):
        make_router().route(weight_decision(-0.3), "D-000001", portfolio_with(100_000), market())


def test_non_market_style_rejected() -> None:
    decision = StrategyDecision.of(
        day(1),
        SetPortfolioTarget(
            targets=(WeightTarget(INSTRUMENT, 0.5),),
            scope=TargetScope.PATCH,
            execution=ExecutionPolicy(
                ExecutionStyle.VWAP, ExecutionTiming.NEXT_OPEN, TimeInForce.DAY
            ),
        ),
    )
    with pytest.raises(UnsupportedActionValue, match="style"):
        make_router().route(decision, "D-000001", portfolio_with(100_000), market())


def test_portfolio_target_accepts_quantity_and_notional_targets() -> None:
    # QuantityTarget 10주 (flat → BUY 10), NotionalTarget 3,000원 @100 (flat → BUY 30)
    decision = StrategyDecision.of(
        day(1),
        SetPortfolioTarget(
            targets=(
                QuantityTarget(INSTRUMENT, Decimal(10)),
                NotionalTarget(OTHER, krw(3_000)),
            ),
            scope=TargetScope.PATCH,
            execution=ExecutionPolicy.market_next_open(),
        ),
    )
    snapshot = make_snapshot(
        day(1),
        make_bar(day(1), INSTRUMENT, 100.0, 100.0),
        make_bar(day(1), OTHER, 100.0, 100.0),
    )
    orders = make_router().route(decision, "D-000001", portfolio_with(100_000), snapshot).orders
    assert [(o.instrument, o.side, o.quantity) for o in orders] == [
        (INSTRUMENT, Side.BUY, Decimal(10)),
        (OTHER, Side.BUY, Decimal(30)),
    ]


# --- 4a: SetPositionTarget / AdjustPosition ----------------------------------


def position_target(target: QuantityTarget | NotionalTarget | WeightTarget) -> SetPositionTarget:
    return SetPositionTarget(target=target, execution=ExecutionPolicy.market_next_open())


def adjust(delta: QuantityDelta | NotionalDelta) -> AdjustPosition:
    return AdjustPosition(
        instrument=INSTRUMENT, delta=delta, execution=ExecutionPolicy.market_next_open()
    )


@pytest.mark.parametrize(
    ("held", "target", "expected"),
    [
        (0, 10, (Side.BUY, 10)),
        (10, 4, (Side.SELL, 6)),
        (10, 10, None),
    ],
)
def test_quantity_target_orders_difference_from_held(
    held: int, target: int, expected: tuple[Side, int] | None
) -> None:
    portfolio = portfolio_with(100_000, {INSTRUMENT: (held, 100.0)} if held else None)
    orders = route_one(position_target(QuantityTarget(INSTRUMENT, Decimal(target))), portfolio)
    if expected is None:
        assert orders == ()
    else:
        assert [(o.side, o.quantity) for o in orders] == [(expected[0], Decimal(expected[1]))]


def test_notional_target_uses_session_close_as_reference() -> None:
    # 5,000원 목표 @ 종가 100 → 50주; 보유 60주면 10주 매도
    orders = route_one(
        position_target(NotionalTarget(INSTRUMENT, krw(5_000))), portfolio_with(100_000)
    )
    assert [(o.side, o.quantity) for o in orders] == [(Side.BUY, Decimal(50))]
    orders = route_one(
        position_target(NotionalTarget(INSTRUMENT, krw(5_000))),
        portfolio_with(0.0, {INSTRUMENT: (60, 100.0)}),
    )
    assert [(o.side, o.quantity) for o in orders] == [(Side.SELL, Decimal(10))]


def test_weight_position_target_matches_portfolio_target() -> None:
    # 50% of 100,000 @ 100 → 500주
    orders = route_one(position_target(WeightTarget(INSTRUMENT, 0.5)), portfolio_with(100_000))
    assert [(o.side, o.quantity) for o in orders] == [(Side.BUY, Decimal(500))]


def test_quantity_delta_adjusts_relative_to_held() -> None:
    orders = route_one(adjust(QuantityDelta(Decimal(10))), portfolio_with(100_000))
    assert [(o.side, o.quantity) for o in orders] == [(Side.BUY, Decimal(10))]
    orders = route_one(
        adjust(QuantityDelta(Decimal(-4))), portfolio_with(0.0, {INSTRUMENT: (10, 100.0)})
    )
    assert [(o.side, o.quantity) for o in orders] == [(Side.SELL, Decimal(4))]


def test_notional_delta_floors_to_shares() -> None:
    # -450원 @ 100 → 4.5주 → 4주 매도
    orders = route_one(
        adjust(NotionalDelta(krw(-450))), portfolio_with(0.0, {INSTRUMENT: (10, 100.0)})
    )
    assert [(o.side, o.quantity) for o in orders] == [(Side.SELL, Decimal(4))]


def test_zero_delta_is_noop() -> None:
    assert route_one(adjust(QuantityDelta(Decimal(0))), portfolio_with(100_000)) == ()


@pytest.mark.parametrize(
    "action",
    [
        position_target(QuantityTarget(INSTRUMENT, Decimal(-10))),
        adjust(QuantityDelta(Decimal(-8))),
        adjust(NotionalDelta(krw(-800))),
        position_target(NotionalTarget(INSTRUMENT, krw(-100))),
    ],
)
def test_explicit_quantity_below_zero_is_rejected_not_clamped(action: StrategyAction) -> None:
    portfolio = portfolio_with(0.0, {INSTRUMENT: (5, 100.0)})
    with pytest.raises(UnsupportedActionValue, match="SHORT_SELLING"):
        route_one(action, portfolio)


def test_fractional_quantity_rejected() -> None:
    with pytest.raises(UnsupportedActionValue, match="integer"):
        route_one(
            position_target(QuantityTarget(INSTRUMENT, Decimal("1.5"))), portfolio_with(100_000)
        )


def test_notional_currency_must_match_instrument() -> None:
    with pytest.raises(UnsupportedActionValue, match="currency"):
        route_one(
            position_target(NotionalTarget(INSTRUMENT, Money(Decimal(100), "USD"))),
            portfolio_with(100_000),
        )


def test_position_target_execution_policy_validated() -> None:
    action = SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(1)),
        execution=ExecutionPolicy(ExecutionStyle.VWAP, ExecutionTiming.NEXT_OPEN, TimeInForce.DAY),
    )
    with pytest.raises(UnsupportedActionValue, match="style"):
        route_one(action, portfolio_with(100_000))


def test_liquidation_sells_full_position_and_cancels_orders() -> None:
    order_manager = OrderManager()
    stale_order = OrderEvent(
        order_id=order_manager.next_order_id(),
        decision_id="D-000000",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(3),
        side=Side.BUY,
        source_action=weight_decision(0.5).actions[0],
    )
    order_manager.place(stale_order)

    decision = StrategyDecision.of(
        day(1),
        LiquidatePosition(
            instrument=INSTRUMENT,
            execution=ExecutionPolicy.market_next_available(),
            cancel_open_orders=True,
            persistence=LiquidationPersistence.UNTIL_FLAT,
        ),
    )
    portfolio = portfolio_with(0.0, {INSTRUMENT: (8, 100.0)})
    result = make_router(order_manager).route(decision, "D-000001", portfolio, market())

    assert result.cancelled == (stale_order,)
    assert order_manager.open_orders() == ()
    assert len(result.orders) == 1
    assert result.orders[0].side is Side.SELL
    assert result.orders[0].quantity == 8


def test_liquidation_of_flat_position_is_noop() -> None:
    decision = StrategyDecision.of(
        day(1),
        LiquidatePosition(
            instrument=INSTRUMENT,
            execution=ExecutionPolicy.market_next_available(),
            cancel_open_orders=False,
            persistence=LiquidationPersistence.ONCE,
        ),
    )
    result = make_router().route(decision, "D-000001", portfolio_with(50_000), market())
    assert result.orders == ()


# --- 4b: SubmitOrder ---------------------------------------------------------


def core(side: Side, quantity: int = 10, tif: TimeInForce = TimeInForce.DAY) -> OrderCore:
    return OrderCore(INSTRUMENT, side, Decimal(quantity), tif)


def test_submit_limit_order_maps_fields_onto_order_event() -> None:
    action = SubmitOrder(
        request=LimitOrderRequest(core=core(Side.BUY, tif=TimeInForce.GTC), limit_price=Decimal(95))
    )
    (order,) = route_one(action, portfolio_with(100_000))
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == Decimal(95)
    assert order.stop_price is None
    assert order.time_in_force is TimeInForce.GTC
    assert (order.side, order.quantity) == (Side.BUY, Decimal(10))
    assert order.source_action is action


def test_submit_stop_limit_order_keeps_both_prices() -> None:
    action = SubmitOrder(
        request=StopLimitOrderRequest(
            core=core(Side.SELL), stop_price=Decimal(92), limit_price=Decimal(91)
        )
    )
    (order,) = route_one(action, portfolio_with(0.0, {INSTRUMENT: (10, 100.0)}))
    assert order.order_type is OrderType.STOP_LIMIT
    assert (order.stop_price, order.limit_price) == (Decimal(92), Decimal(91))


def test_submit_market_order_needs_no_feature() -> None:
    action = SubmitOrder(request=MarketOrderRequest(core=core(Side.BUY)))
    decision = StrategyDecision.of(day(1), action)
    router = make_router(features=frozenset())
    (order,) = router.route(decision, "D-000001", portfolio_with(100_000), market()).orders
    assert order.order_type is OrderType.MARKET


def test_limit_order_requires_declared_feature() -> None:
    action = SubmitOrder(request=LimitOrderRequest(core=core(Side.BUY), limit_price=Decimal(95)))
    decision = StrategyDecision.of(day(1), action)
    router = make_router(features=frozenset())
    with pytest.raises(UndeclaredFeatureUsed, match="limit_order"):
        router.route(decision, "D-000001", portfolio_with(100_000), market())


def test_submit_sell_beyond_held_rejected() -> None:
    action = SubmitOrder(request=MarketOrderRequest(core=core(Side.SELL, quantity=11)))
    with pytest.raises(UnsupportedActionValue, match="SHORT_SELLING"):
        route_one(action, portfolio_with(0.0, {INSTRUMENT: (10, 100.0)}))


@pytest.mark.parametrize("tif", [TimeInForce.IOC, TimeInForce.FOK])
def test_ioc_fok_not_implemented_until_partial_fill(tif: TimeInForce) -> None:
    action = SubmitOrder(request=MarketOrderRequest(core=core(Side.BUY, tif=tif)))
    with pytest.raises(UnsupportedActionValue, match="PARTIAL_FILL"):
        route_one(action, portfolio_with(100_000))
