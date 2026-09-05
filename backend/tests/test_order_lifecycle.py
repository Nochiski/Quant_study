"""주문 생명주기 골든·상태 전이 테스트 (4b·4c).

시나리오는 전부 손계산이며, EventStore의 order_updates를 order_id별로 재생해
허용 전이표 밖의 전이가 없는지도 검사한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.types.actions import (
    ActionKind,
    CancelOrder,
    ExecutionPolicy,
    QuantityTarget,
    ReplaceOrder,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import FillEvent, OrderStatus, OrderUpdateEvent, StrategyEvent
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCore,
    OrderRequest,
    Side,
    StopOrderRequest,
    TimeInForce,
)
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_instrument, make_ohlc

INSTRUMENT = make_instrument()
OTHER = make_instrument("000660")

# D1 100 flat, D2 갭 상승, D3 저가 94까지 하락, D4 소폭 반등
BARS: tuple[Bar, ...] = (
    make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
    make_ohlc(day(2), INSTRUMENT, 110.0, 120.0, 105.0, 115.0),
    make_ohlc(day(3), INSTRUMENT, 100.0, 100.0, 94.0, 96.0),
    make_ohlc(day(4), INSTRUMENT, 97.0, 99.0, 95.0, 98.0),
)

ALLOWED_TRANSITIONS: dict[OrderStatus | None, set[OrderStatus]] = {
    None: {
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.TRIGGERED,
        OrderStatus.REPLACED,
        OrderStatus.REJECTED,
    },
    OrderStatus.OPEN: {
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.TRIGGERED,
        OrderStatus.REPLACED,
    },
    OrderStatus.TRIGGERED: {
        OrderStatus.OPEN,
        OrderStatus.FILLED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REPLACED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REPLACED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.REPLACED: set(),
}


def assert_transitions_valid(updates: tuple[OrderUpdateEvent, ...]) -> None:
    by_order: dict[str, list[OrderStatus]] = defaultdict(list)
    for update in updates:
        by_order[update.order_id].append(update.status)
    for order_id, statuses in by_order.items():
        previous: OrderStatus | None = None
        for status in statuses:
            assert status in ALLOWED_TRANSITIONS[previous], (
                f"illegal transition {previous} -> {status} for {order_id}: {statuses}"
            )
            previous = status


def statuses_of(engine: BacktestEngine, order_id: str) -> list[OrderStatus]:
    return [u.status for u in engine.event_store.order_updates() if u.order_id == order_id]


class OrderScript:
    """호출 순서대로 미리 정한 Action을 반환하는 주문 전략."""

    def __init__(self, script: tuple[StrategyAction | None, ...]) -> None:
        self._script = script
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(
                HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=1),
            ),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset(
                {ActionKind.NO_ACTION, ActionKind.SUBMIT_ORDER, ActionKind.SET_POSITION_TARGET}
            ),
            features=frozenset(
                {EngineFeature.LIMIT_ORDER, EngineFeature.STOP_ORDER, EngineFeature.PARTIAL_FILL}
            ),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now)
        return StrategyDecision.of(ctx.now, action)


def submit(request: OrderRequest) -> SubmitOrder:
    return SubmitOrder(request=request)


def limit_buy(price: float, tif: TimeInForce, quantity: int = 10) -> SubmitOrder:
    core = OrderCore(INSTRUMENT, Side.BUY, Decimal(quantity), tif)
    return submit(LimitOrderRequest(core=core, limit_price=Decimal(str(price))))


def stop_sell(price: float, tif: TimeInForce, quantity: int = 10) -> SubmitOrder:
    core = OrderCore(INSTRUMENT, Side.SELL, Decimal(quantity), tif)
    return submit(StopOrderRequest(core=core, stop_price=Decimal(str(price))))


def buy_shares(quantity: int) -> SetPositionTarget:
    return SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(quantity)),
        execution=ExecutionPolicy.market_next_open(),
    )


def run(
    script: tuple[StrategyAction | None, ...],
    bars: tuple[Bar, ...] = BARS,
    max_participation: float | None = None,
    slippage: FixedBpsSlippage | None = None,
) -> tuple[BacktestEngine, BacktestResult]:
    engine = BacktestEngine(
        RunConfig(run_id="lifecycle", initial_cash=100_000.0, fee_bps=0.0),
        max_participation=max_participation,
        slippage=slippage,
    )
    result = engine.run(OrderScript(script), DataFeed(bars))
    assert_transitions_valid(engine.event_store.order_updates())
    return engine, result


class TestGtcLimit:
    def test_fills_at_limit_when_low_touches_two_sessions_later(self) -> None:
        engine, result = run((limit_buy(95.0, TimeInForce.GTC),))
        assert [(f.ts, f.price, int(f.quantity)) for f in result.fills] == [(day(3), 95.0, 10)]
        assert statuses_of(engine, "O-000001") == [OrderStatus.FILLED]
        assert result.snapshots[-1].cash == pytest.approx(100_000.0 - 950.0)

    def test_order_creation_does_not_touch_cash_or_position(self) -> None:
        _, result = run((limit_buy(95.0, TimeInForce.GTC),))
        d1, d2 = result.snapshots[0], result.snapshots[1]
        assert d1.cash == d2.cash == 100_000.0
        assert d1.positions == d2.positions == ()

    def test_fill_happens_strictly_after_order(self) -> None:
        _, result = run((limit_buy(95.0, TimeInForce.GTC),))
        assert result.fills[0].ts > result.orders[0].ts

    def test_unfilled_gtc_is_cancelled_when_run_ends(self) -> None:
        engine, result = run((limit_buy(50.0, TimeInForce.GTC),))
        assert result.fills == ()
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [u.status for u in updates] == [OrderStatus.CANCELLED]
        assert updates[0].ts == day(4)
        assert "run ended with GTC" in (updates[0].detail or "")

    def test_gtc_survives_session_without_bar(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), OTHER, 50.0, 50.0, 50.0, 50.0),  # INSTRUMENT 거래정지
            make_ohlc(day(3), INSTRUMENT, 100.0, 100.0, 94.0, 96.0),
        )
        engine, result = run((limit_buy(95.0, TimeInForce.GTC),), bars)
        assert [(f.ts, f.price) for f in result.fills] == [(day(3), 95.0)]
        assert statuses_of(engine, "O-000001") == [OrderStatus.FILLED]


class TestDayLimit:
    def test_unfilled_day_order_expires_same_session(self) -> None:
        engine, result = run((limit_buy(95.0, TimeInForce.DAY),))
        assert result.fills == ()
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [(u.ts, u.status) for u in updates] == [(day(2), OrderStatus.CANCELLED)]
        assert "day order expired" in (updates[0].detail or "")

    def test_day_order_without_bar_is_cancelled(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), OTHER, 50.0, 50.0, 50.0, 50.0),
            make_ohlc(day(3), INSTRUMENT, 100.0, 100.0, 94.0, 96.0),
        )
        engine, result = run((limit_buy(95.0, TimeInForce.DAY),), bars)
        assert result.fills == ()
        assert statuses_of(engine, "O-000001") == [OrderStatus.CANCELLED]


class TestStopSell:
    def test_gap_down_through_stop_fills_at_open(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 110.0, 120.0, 105.0, 115.0),
            make_ohlc(day(3), INSTRUMENT, 90.0, 95.0, 85.0, 88.0),  # 갭 하락
        )
        # D1: 10주 매수 → D2 시가 110 체결. D2: STOP 100 매도 → D3 시가 90 (갭) 체결.
        engine, result = run((buy_shares(10), stop_sell(100.0, TimeInForce.GTC)), bars)
        assert [(f.ts, f.side, f.price) for f in result.fills] == [
            (day(2), Side.BUY, 110.0),
            (day(3), Side.SELL, 90.0),
        ]
        assert statuses_of(engine, "O-000002") == [OrderStatus.FILLED]
        final = result.snapshots[-1]
        assert final.cash == pytest.approx(100_000.0 - 1_100.0 + 900.0)
        assert final.position_qty(INSTRUMENT) == 0


class TestMarketOrderRecordsFilled:
    def test_full_fill_records_filled_update(self) -> None:
        engine, _ = run((buy_shares(10),))
        assert statuses_of(engine, "O-000001") == [OrderStatus.FILLED]


# --- 4c: 취소·정정·이벤트 전달 -------------------------------------------------

Handler = Callable[[StrategyContext, StrategyEvent], StrategyAction | None]


class CallbackStrategy:
    """이벤트마다 handler를 호출하고, 받은 이벤트를 전부 기록하는 전략."""

    def __init__(self, handler: Handler, events: frozenset[EventKind]) -> None:
        self._handler = handler
        self._events = events
        self.received: list[StrategyEvent] = []

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(
                HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=1),
            ),
            schedule=EverySession(),
            events=self._events,
            actions=frozenset(
                {
                    ActionKind.NO_ACTION,
                    ActionKind.SUBMIT_ORDER,
                    ActionKind.SET_POSITION_TARGET,
                    ActionKind.CANCEL_ORDER,
                    ActionKind.REPLACE_ORDER,
                }
            ),
            features=frozenset({EngineFeature.LIMIT_ORDER, EngineFeature.STOP_ORDER}),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.received.append(event)
        action = self._handler(ctx, event)
        if action is None:
            return StrategyDecision.no_action(ctx.now)
        return StrategyDecision.of(ctx.now, action)


def run_callback(
    handler: Handler,
    events: frozenset[EventKind] = frozenset({EventKind.MARKET}),
    bars: tuple[Bar, ...] = BARS,
) -> tuple[BacktestEngine, CallbackStrategy, BacktestResult]:
    engine = BacktestEngine(RunConfig(run_id="lifecycle-4c", initial_cash=100_000.0, fee_bps=0.0))
    strategy = CallbackStrategy(handler, events)
    result = engine.run(strategy, DataFeed(bars))
    assert_transitions_valid(engine.event_store.order_updates())
    return engine, strategy, result


class TestCancel:
    def test_strategy_cancels_gtc_order_seen_via_open_orders(self) -> None:
        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            if ctx.now == day(1):
                return limit_buy(50.0, TimeInForce.GTC)
            if ctx.now == day(3):
                (order,) = ctx.open_orders(INSTRUMENT)
                return CancelOrder(order_id=order.order_id)
            return None

        engine, _, result = run_callback(handler)
        assert result.fills == ()
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [(u.ts, u.status) for u in updates] == [(day(3), OrderStatus.CANCELLED)]
        assert all(s.cash == 100_000.0 for s in result.snapshots)

    def test_open_orders_is_empty_before_placement_and_after_fill(self) -> None:
        seen: dict[int, int] = {}

        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            seen[ctx.now.day] = len(ctx.open_orders())
            return buy_shares(10) if ctx.now == day(1) else None

        run_callback(handler)
        # D1: 주문 전 0개, D2: D2 시가에 체결됐으므로 0개
        assert seen == {1: 0, 2: 0, 3: 0, 4: 0}


class TestReplace:
    def test_replace_moves_limit_and_new_order_fills(self) -> None:
        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            if ctx.now == day(1):
                return limit_buy(50.0, TimeInForce.GTC)
            if ctx.now == day(2):
                (order,) = ctx.open_orders()
                core = OrderCore(INSTRUMENT, Side.BUY, Decimal(10), TimeInForce.GTC)
                return ReplaceOrder(
                    order_id=order.order_id,
                    replacement=LimitOrderRequest(core=core, limit_price=Decimal(95)),
                )
            return None

        engine, _, result = run_callback(handler)
        assert [(f.order_id, f.ts, f.price) for f in result.fills] == [("O-000002", day(3), 95.0)]
        assert statuses_of(engine, "O-000001") == [OrderStatus.REPLACED]
        assert statuses_of(engine, "O-000002") == [OrderStatus.FILLED]


class TestEventDelivery:
    def test_fill_events_delivered_only_when_declared(self) -> None:
        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            return (
                buy_shares(10) if isinstance(event, MarketSnapshot) and ctx.now == day(1) else None
            )

        engine, declared, _ = run_callback(handler, frozenset({EventKind.MARKET, EventKind.FILL}))
        fills = [e for e in declared.received if isinstance(e, FillEvent)]
        assert fills == list(engine.event_store.fills())

        _, undeclared, _ = run_callback(handler)
        assert all(isinstance(e, MarketSnapshot) for e in undeclared.received)

    def test_fill_event_sees_portfolio_after_fill(self) -> None:
        seen: list[Decimal] = []

        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            if isinstance(event, FillEvent):
                seen.append(ctx.position_qty(INSTRUMENT))
                return None
            return buy_shares(10) if ctx.now == day(1) else None

        run_callback(handler, frozenset({EventKind.MARKET, EventKind.FILL}))
        assert seen == [Decimal(10)]

    def test_decision_on_fill_event_executes_next_session(self) -> None:
        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            if isinstance(event, FillEvent) and event.side is Side.BUY:
                core = OrderCore(INSTRUMENT, Side.SELL, Decimal(10), TimeInForce.DAY)
                return submit(MarketOrderRequest(core=core))
            return buy_shares(10) if ctx.now == day(1) else None

        _, _, result = run_callback(handler, frozenset({EventKind.MARKET, EventKind.FILL}))
        # D2 매수 체결 알림 → 즉시 매도 주문 → D3 시가 100 체결 (같은 세션 체결 없음)
        assert [(f.ts, f.side, f.price) for f in result.fills] == [
            (day(2), Side.BUY, 110.0),
            (day(3), Side.SELL, 100.0),
        ]

    def test_order_update_events_delivered_when_declared(self) -> None:
        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            return limit_buy(50.0, TimeInForce.DAY) if ctx.now == day(1) else None

        _, strategy, _ = run_callback(
            handler, frozenset({EventKind.MARKET, EventKind.ORDER_UPDATE})
        )
        updates = [e for e in strategy.received if isinstance(e, OrderUpdateEvent)]
        assert [(u.ts, u.status) for u in updates] == [(day(2), OrderStatus.CANCELLED)]


# --- 4d: 부분체결·IOC/FOK·슬리피지 --------------------------------------------


def market_buy(quantity: int, tif: TimeInForce) -> SubmitOrder:
    core = OrderCore(INSTRUMENT, Side.BUY, Decimal(quantity), tif)
    return submit(MarketOrderRequest(core=core))


class TestPartialFill:
    def test_gtc_fills_across_sessions_until_complete(self) -> None:
        # volume 1,000 × 10% = 100주/세션. 250주 → D2 100, D3 100, D4 50
        engine, result = run((market_buy(250, TimeInForce.GTC),), max_participation=0.1)
        assert [(f.ts, int(f.quantity), f.price) for f in result.fills] == [
            (day(2), 100, 110.0),
            (day(3), 100, 100.0),
            (day(4), 50, 97.0),
        ]
        assert statuses_of(engine, "O-000001") == [
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        ]
        assert sum(f.quantity for f in result.fills) == Decimal(250)
        assert result.snapshots[-1].position_qty(INSTRUMENT) == Decimal(250)

    def test_ioc_fills_partially_then_cancels_remainder(self) -> None:
        engine, result = run((market_buy(250, TimeInForce.IOC),), max_participation=0.1)
        assert [(f.ts, int(f.quantity)) for f in result.fills] == [(day(2), 100)]
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [(u.ts, u.status) for u in updates] == [
            (day(2), OrderStatus.PARTIALLY_FILLED),
            (day(2), OrderStatus.CANCELLED),
        ]
        assert "remaining=150" in (updates[1].detail or "")

    def test_fok_cancels_without_fill_when_liquidity_short(self) -> None:
        engine, result = run((market_buy(250, TimeInForce.FOK),), max_participation=0.1)
        assert result.fills == ()
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [(u.ts, u.status) for u in updates] == [(day(2), OrderStatus.CANCELLED)]
        assert "fok" in (updates[0].detail or "").lower()

    def test_day_order_partial_then_expires(self) -> None:
        engine, result = run((market_buy(250, TimeInForce.DAY),), max_participation=0.1)
        assert [(f.ts, int(f.quantity)) for f in result.fills] == [(day(2), 100)]
        assert statuses_of(engine, "O-000001") == [
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCELLED,
        ]


class TestSlippageAccounting:
    def test_slipped_price_flows_into_cash(self) -> None:
        # 10주 매수 D2 시가 110 + 10bp(0.11) = 110.11 → 현금 100,000 − 1,101.1
        _, result = run((buy_shares(10),), slippage=FixedBpsSlippage(bps=10.0))
        fill = result.fills[0]
        assert fill.price == pytest.approx(110.11)
        assert fill.slippage_per_share == pytest.approx(0.11)
        assert result.snapshots[-1].cash == pytest.approx(100_000.0 - 1_101.1)


# --- 리뷰 결함 회귀 (4단계 리뷰) ------------------------------------------------


class TestReviewRegressions:
    def test_partially_filled_stop_keeps_trigger_state(self) -> None:
        """DEFECT-001: 발동 후 부분체결된 STOP/STOP_LIMIT 잔량은 STOP 재평가 없이 체결된다."""
        from backtest_engine.types.orders import StopLimitOrderRequest

        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0, volume=1_000),
            make_ohlc(day(2), INSTRUMENT, 110.0, 120.0, 105.0, 115.0, volume=1_000),
            make_ohlc(day(3), INSTRUMENT, 90.0, 95.0, 85.0, 88.0, volume=1_000),
            make_ohlc(day(4), INSTRUMENT, 90.0, 95.0, 85.0, 88.0, volume=1_000),
        )
        core = OrderCore(INSTRUMENT, Side.BUY, Decimal(250), TimeInForce.GTC)
        stop_limit = submit(
            StopLimitOrderRequest(core=core, stop_price=Decimal(105), limit_price=Decimal(120))
        )
        engine, result = run((stop_limit,), bars, max_participation=0.1)
        # D2: 발동(110 ≥ 105) + 캡 100주. D3·D4: 발동 유지 → 지정가 120 이내 시가 90에 체결
        assert [(f.ts, int(f.quantity), f.price) for f in result.fills] == [
            (day(2), 100, 110.0),
            (day(3), 100, 90.0),
            (day(4), 50, 90.0),
        ]
        assert statuses_of(engine, "O-000001")[-1] is OrderStatus.FILLED

    def test_partially_filled_plain_stop_fills_remainder_at_next_open(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0, volume=1_000),
            make_ohlc(day(2), INSTRUMENT, 110.0, 120.0, 105.0, 115.0, volume=1_000),
            make_ohlc(day(3), INSTRUMENT, 90.0, 95.0, 85.0, 88.0, volume=1_000),
        )
        core = OrderCore(INSTRUMENT, Side.BUY, Decimal(150), TimeInForce.GTC)
        stop = submit(StopOrderRequest(core=core, stop_price=Decimal(105)))
        _, result = run((stop,), bars, max_participation=0.1)
        assert [(f.ts, int(f.quantity), f.price) for f in result.fills] == [
            (day(2), 100, 110.0),
            (day(3), 50, 90.0),
        ]

    def test_two_sell_actions_in_one_decision_cannot_oversell(self) -> None:
        """DEFECT-102: 같은 Decision의 매도 액션들은 보유 수량을 누적 차감해 검증한다."""
        from backtest_engine.errors import UnsupportedActionValue
        from backtest_engine.types.decision import StrategyDecision

        class TwoSells(OrderScript):
            def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
                if ctx.now == day(1):
                    return StrategyDecision.of(ctx.now, buy_shares(10))
                if ctx.now == day(2):
                    sell = OrderCore(INSTRUMENT, Side.SELL, Decimal(10), TimeInForce.GTC)
                    return StrategyDecision(
                        schema_version=1,
                        as_of=ctx.now,
                        actions=(
                            submit(MarketOrderRequest(core=sell)),
                            submit(MarketOrderRequest(core=sell)),
                        ),
                    )
                return StrategyDecision.no_action(ctx.now)

        engine = BacktestEngine(RunConfig(run_id="oversell", initial_cash=100_000.0))
        with pytest.raises(UnsupportedActionValue, match="held=10 sell=10 already_routed=10"):
            engine.run(TwoSells(()), DataFeed(BARS))

    def test_open_sell_orders_count_against_held_quantity(self) -> None:
        """대기 중인 매도 주문이 있으면 그만큼은 다시 팔 수 없다 (GTC 지정가 매도 + 시장가 매도)."""
        from backtest_engine.errors import UnsupportedActionValue

        def sell_limit_then_market() -> tuple[StrategyAction | None, ...]:
            limit = OrderCore(INSTRUMENT, Side.SELL, Decimal(10), TimeInForce.GTC)
            market = OrderCore(INSTRUMENT, Side.SELL, Decimal(10), TimeInForce.DAY)
            return (
                buy_shares(10),
                submit(LimitOrderRequest(core=limit, limit_price=Decimal(500))),
                submit(MarketOrderRequest(core=market)),
            )

        with pytest.raises(UnsupportedActionValue, match="open_sell=10"):
            run(sell_limit_then_market())

    def test_open_orders_expose_remaining_after_partial_fill(self) -> None:
        """DEFECT-003: 전략이 보는 대기 주문은 원 수량이 아니라 잔량이다."""
        seen: list[tuple[str, Decimal]] = []

        def handler(ctx: StrategyContext, event: StrategyEvent) -> StrategyAction | None:
            seen.extend((o.order_id, o.remaining) for o in ctx.open_orders())
            return market_buy(250, TimeInForce.GTC) if ctx.now == day(1) else None

        engine = BacktestEngine(
            RunConfig(run_id="remaining", initial_cash=100_000.0, fee_bps=0.0),
            max_participation=0.1,
        )
        engine.run(CallbackStrategy(handler, frozenset({EventKind.MARKET})), DataFeed(BARS))
        assert seen == [("O-000001", Decimal(150)), ("O-000001", Decimal(50))]

    def test_gtc_buy_survives_session_it_cannot_afford(self) -> None:
        """DEFECT-004/007: 여력 0·유동성 0 세션은 OPEN(사유)만 남기고 GTC는 대기한다."""
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 500.0, 500.0, 500.0, 500.0),
            make_ohlc(day(3), INSTRUMENT, 50.0, 50.0, 50.0, 50.0, volume=0),
            make_ohlc(day(4), INSTRUMENT, 50.0, 50.0, 50.0, 50.0),
        )
        engine = BacktestEngine(
            RunConfig(run_id="nocash", initial_cash=300.0, fee_bps=0.0), max_participation=0.1
        )
        result = engine.run(OrderScript((market_buy(2, TimeInForce.GTC),)), DataFeed(bars))
        assert_transitions_valid(engine.event_store.order_updates())
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert [(u.ts, u.status) for u in updates] == [
            (day(2), OrderStatus.OPEN),
            (day(3), OrderStatus.OPEN),
            (day(4), OrderStatus.FILLED),
        ]
        assert "cannot afford" in (updates[0].detail or "")
        assert "no liquidity" in (updates[1].detail or "")
        assert [(f.ts, int(f.quantity), f.price) for f in result.fills] == [(day(4), 2, 50.0)]

    def test_fill_notification_precedes_its_order_update(self) -> None:
        """DEFECT-006: FILL 알림이 그 체결의 FILLED 알림보다 먼저 도착한다."""
        _, strategy, _ = run_callback(
            lambda ctx, event: buy_shares(10) if ctx.now == day(1) else None,
            frozenset({EventKind.MARKET, EventKind.FILL, EventKind.ORDER_UPDATE}),
        )
        kinds = [type(e).__name__ for e in strategy.received if not isinstance(e, MarketSnapshot)]
        assert kinds == ["FillEvent", "OrderUpdateEvent"]

    def test_day_order_on_last_session_is_labelled_expired(self) -> None:
        """DEFECT-008: 마지막 세션에 낸 DAY 주문은 'run ended'가 아니라 당일 만료다."""
        bars = BARS[:2]
        engine, _ = run((None, limit_buy(50.0, TimeInForce.DAY)), bars)
        (update,) = [u for u in engine.event_store.order_updates() if u.order_id == "O-000001"]
        assert update.status is OrderStatus.CANCELLED
        assert "day order expired at last session" in (update.detail or "")

    def test_sell_side_participation_cap_partial_fill(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(3), INSTRUMENT, 100.0, 100.0, 100.0, 100.0, volume=40),
            make_ohlc(day(4), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        )
        sell = OrderCore(INSTRUMENT, Side.SELL, Decimal(10), TimeInForce.GTC)
        _, result = run(
            (buy_shares(10), submit(MarketOrderRequest(core=sell))), bars, max_participation=0.1
        )
        assert [(f.ts, f.side, int(f.quantity)) for f in result.fills] == [
            (day(2), Side.BUY, 10),
            (day(3), Side.SELL, 4),
            (day(4), Side.SELL, 6),
        ]
