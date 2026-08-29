"""5c Basket: 그룹 라우팅, 정책별 그룹 체결(BEST_EFFORT / ALL_OR_NONE / PROPORTIONAL), serde.

페어 골든 (fee 0, 초기 현금 100,000, 브로커 참여율 10%):
  D1  전략: B 10주 목표 → D2 시가 50 체결 (B 10주 보유, 현금 99,500)
  D2  전략: Basket[A 매수 10 @시장가, B 매도 10 @시장가]
  D3  A 시가 100 (거래량 1,000 → 캡 100), B 시가 60 (거래량 60 → 캡 6)
      BEST_EFFORT  → A 10, B 6 (B 잔량 4 만료)
      ALL_OR_NONE  → 체결 없음, 두 leg CANCELLED
      PROPORTIONAL → scale 0.6 → A 6, B 6, 잔량 CANCELLED
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.orders import OrderManager
from backtest_engine.engine.router import DecisionRouter
from backtest_engine.errors import UndeclaredFeatureUsed
from backtest_engine.types.actions import (
    ActionKind,
    BasketAction,
    ExecutionPolicy,
    GroupPolicy,
    QuantityTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OrderEvent, OrderStatus, StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.orders import MarketOrderRequest, OrderCore, Side, TimeInForce
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.serde import order_event_from_dict, order_event_to_dict
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_instrument, make_ohlc, make_snapshot
from tests.test_order_lifecycle import assert_transitions_valid
from tests.test_router import portfolio_with

A = make_instrument("005930")
B = make_instrument("000660")
ALL_FEATURES = frozenset({EngineFeature.PROPORTIONAL_BASKET})
DECLARED = frozenset(
    {
        ActionKind.NO_ACTION,
        ActionKind.SET_POSITION_TARGET,
        ActionKind.SUBMIT_ORDER,
        ActionKind.BASKET,
        ActionKind.LIQUIDATE_POSITION,
    }
)


def market_leg(instrument: InstrumentId, side: Side, quantity: int) -> SubmitOrder:
    core = OrderCore(instrument, side, Decimal(quantity), TimeInForce.DAY)
    return SubmitOrder(request=MarketOrderRequest(core=core))


def pair(policy: GroupPolicy) -> BasketAction:
    return BasketAction(
        legs=(market_leg(A, Side.BUY, 10), market_leg(B, Side.SELL, 10)), group_policy=policy
    )


# --- Router -------------------------------------------------------------------


def snapshot_ab() -> MarketSnapshot:
    return make_snapshot(
        day(2),
        make_ohlc(day(2), A, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), B, 50.0, 50.0, 50.0, 50.0),
    )


class TestRouting:
    def test_legs_share_group_id_and_policy_is_returned(self) -> None:
        manager = OrderManager()
        router = DecisionRouter(DECLARED, manager, ALL_FEATURES)
        decision = StrategyDecision.of(day(2), pair(GroupPolicy.PROPORTIONAL))
        result = router.route(
            decision, "D-000001", portfolio_with(99_500.0, {B: (10, 50.0)}), snapshot_ab()
        )
        assert [o.group_id for o in result.orders] == ["G-000001", "G-000001"]
        (group,) = result.groups
        assert group.group_id == "G-000001"
        assert group.policy is GroupPolicy.PROPORTIONAL
        assert group.order_ids == tuple(o.order_id for o in result.orders)

    def test_proportional_requires_feature(self) -> None:
        router = DecisionRouter(DECLARED, OrderManager(), frozenset())
        decision = StrategyDecision.of(day(2), pair(GroupPolicy.PROPORTIONAL))
        with pytest.raises(UndeclaredFeatureUsed, match="proportional_basket"):
            router.route(
                decision, "D-000001", portfolio_with(99_500.0, {B: (10, 50.0)}), snapshot_ab()
            )

    def test_best_effort_needs_no_feature(self) -> None:
        router = DecisionRouter(DECLARED, OrderManager(), frozenset())
        decision = StrategyDecision.of(day(2), pair(GroupPolicy.BEST_EFFORT))
        result = router.route(
            decision, "D-000001", portfolio_with(99_500.0, {B: (10, 50.0)}), snapshot_ab()
        )
        assert len(result.orders) == 2

    def test_duplicate_instrument_in_group_rejected(self) -> None:
        router = DecisionRouter(DECLARED, OrderManager(), frozenset())
        basket = BasketAction(
            legs=(market_leg(A, Side.BUY, 10), market_leg(A, Side.BUY, 5)),
            group_policy=GroupPolicy.BEST_EFFORT,
        )
        with pytest.raises(ValueError, match="duplicate instrument in basket"):
            router.route(
                StrategyDecision.of(day(2), basket),
                "D-000001",
                portfolio_with(100_000.0),
                snapshot_ab(),
            )

    def test_non_basket_orders_have_no_group(self) -> None:
        router = DecisionRouter(DECLARED, OrderManager(), frozenset())
        decision = StrategyDecision.of(day(2), market_leg(A, Side.BUY, 1))
        (order,) = router.route(
            decision, "D-000001", portfolio_with(100_000.0), snapshot_ab()
        ).orders
        assert order.group_id is None


def test_group_id_round_trips_and_defaults_to_none() -> None:
    order = OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(2),
        instrument=A,
        quantity=Decimal(1),
        side=Side.BUY,
        source_action=market_leg(A, Side.BUY, 1),
        group_id="G-000007",
    )
    assert order_event_from_dict(order_event_to_dict(order)) == order
    data = order_event_to_dict(order)
    assert isinstance(data, dict)
    data.pop("group_id")
    assert order_event_from_dict(data).group_id is None


# --- Engine golden -------------------------------------------------------------

BARS: tuple[Bar, ...] = (
    make_ohlc(day(1), A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
    make_ohlc(day(1), B, 50.0, 50.0, 50.0, 50.0, volume=1_000),
    make_ohlc(day(2), A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
    make_ohlc(day(2), B, 50.0, 50.0, 50.0, 50.0, volume=1_000),
    make_ohlc(day(3), A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
    make_ohlc(day(3), B, 60.0, 60.0, 60.0, 60.0, volume=60),
    make_ohlc(day(4), A, 100.0, 100.0, 100.0, 100.0, volume=1_000),
    make_ohlc(day(4), B, 60.0, 60.0, 60.0, 60.0, volume=1_000),
)


class BasketStrategy:
    def __init__(self, script: tuple[StrategyAction | None, ...]) -> None:
        self._script = script
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(HistoryRequest(instruments=(A, B), field=PriceField.CLOSE, lookback=1),),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=DECLARED,
            features=frozenset({EngineFeature.PROPORTIONAL_BASKET, EngineFeature.PARTIAL_FILL}),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now)
        return StrategyDecision.of(ctx.now, action)


def hold_b(quantity: int) -> SetPositionTarget:
    return SetPositionTarget(
        target=QuantityTarget(B, Decimal(quantity)), execution=ExecutionPolicy.market_next_open()
    )


def run_pair(policy: GroupPolicy) -> tuple[BacktestEngine, BacktestResult]:
    engine = BacktestEngine(
        RunConfig(run_id="basket", initial_cash=100_000.0, fee_bps=0.0), max_participation=0.1
    )
    result = engine.run(BasketStrategy((hold_b(10), pair(policy))), DataFeed(BARS))
    assert_transitions_valid(engine.event_store.order_updates())
    return engine, result


def d3_fills(result: BacktestResult) -> list[tuple[str, Side, int]]:
    return [(f.instrument.symbol, f.side, int(f.quantity)) for f in result.fills if f.ts == day(3)]


def statuses(engine: BacktestEngine, order_id: str) -> list[OrderStatus]:
    return [u.status for u in engine.event_store.order_updates() if u.order_id == order_id]


class TestPairGolden:
    def test_best_effort_fills_each_leg_independently(self) -> None:
        engine, result = run_pair(GroupPolicy.BEST_EFFORT)
        assert d3_fills(result) == [("000660", Side.SELL, 6), ("005930", Side.BUY, 10)]
        assert statuses(engine, "O-000002") == [OrderStatus.FILLED]  # A
        assert statuses(engine, "O-000003") == [OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED]

    def test_all_or_none_cancels_everything_when_a_leg_is_short(self) -> None:
        engine, result = run_pair(GroupPolicy.ALL_OR_NONE)
        assert d3_fills(result) == []
        for order_id in ("O-000002", "O-000003"):
            (update,) = [u for u in engine.event_store.order_updates() if u.order_id == order_id]
            assert update.status is OrderStatus.CANCELLED
            assert "all_or_none" in (update.detail or "")
        assert result.snapshots[-1].position_qty(B) == Decimal(10)
        assert result.snapshots[-1].position_qty(A) == Decimal(0)

    def test_proportional_scales_all_legs_to_the_tightest(self) -> None:
        engine, result = run_pair(GroupPolicy.PROPORTIONAL)
        assert d3_fills(result) == [("000660", Side.SELL, 6), ("005930", Side.BUY, 6)]
        for order_id in ("O-000002", "O-000003"):
            assert statuses(engine, order_id) == [
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCELLED,
            ]
            cancel = [u for u in engine.event_store.order_updates() if u.order_id == order_id][-1]
            assert "proportional" in (cancel.detail or "")
        final = result.snapshots[-1]
        assert final.position_qty(A) == Decimal(6)
        assert final.position_qty(B) == Decimal(4)
        assert final.cash == pytest.approx(100_000.0 - 500.0 - 600.0 + 360.0)

    def test_sell_leg_proceeds_fund_buy_leg_in_same_session(self) -> None:
        # 현금 0: B 매도 6주 @60 = 360 → A 매수 여력 360 → 3주 (BEST_EFFORT)
        engine = BacktestEngine(
            RunConfig(run_id="basket-cash", initial_cash=500.0, fee_bps=0.0), max_participation=0.1
        )
        result = engine.run(
            BasketStrategy((hold_b(10), pair(GroupPolicy.BEST_EFFORT))), DataFeed(BARS)
        )
        assert d3_fills(result) == [("000660", Side.SELL, 6), ("005930", Side.BUY, 3)]
