"""5a 공매도: 음수 포지션 회계, 차입 비용, 라우터의 음수 목표 허용, 엔진 골든.

엔진 골든 (fee 0, 초기 현금 10,000, 차입 연 252bp = 세션당 1bp):
  D1 close 100  전략: QuantityTarget(-10) → D2 시가 100 공매도 10주: 현금 11,000, 수량 −10
  D2 close  90  평가 −900, 차입비 900 × 0.0001 = 0.09 → 현금 10,999.91, equity 10,099.91
  D3 open   80  전략(D2): QuantityTarget(0) → 환매 10주 @80: 현금 10,199.91, flat
  D3 close  85  equity 10,199.91 (비용 없음)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.errors import NegativePositionError, UnsupportedActionValue
from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    ExecutionPolicy,
    LiquidatePosition,
    LiquidationPersistence,
    QuantityDelta,
    QuantityTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import CostAccrued, CostKind, FillEvent, StrategyEvent
from backtest_engine.types.market import Bar, PriceField
from backtest_engine.types.orders import MarketOrderRequest, OrderCore, Side, TimeInForce
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_bar, make_instrument, make_ohlc, make_snapshot
from tests.test_router import make_router, portfolio_with

INSTRUMENT = make_instrument()


def fill(side: Side, quantity: int, price: float, seq: int = 1, fee: float = 0.0) -> FillEvent:
    return FillEvent(
        fill_id=f"F-{seq:06d}",
        order_id="O-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        price=price,
        fee=fee,
        slippage_per_share=0.0,
    )


# --- Portfolio ---------------------------------------------------------------


class TestShortAccounting:
    def test_short_entry_credits_cash_and_makes_quantity_negative(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        portfolio.apply(fill(Side.SELL, 10, 100.0))
        assert portfolio.cash == pytest.approx(11_000.0)
        assert portfolio.held_qty(INSTRUMENT) == Decimal(-10)
        snapshot = portfolio.snapshot(day(1))
        assert snapshot.positions[0].average_price == pytest.approx(100.0)

    def test_cover_keeps_average_and_reduces_short(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        portfolio.apply(fill(Side.SELL, 10, 100.0))
        portfolio.apply(fill(Side.BUY, 4, 90.0, seq=2))
        assert portfolio.held_qty(INSTRUMENT) == Decimal(-6)
        assert portfolio.cash == pytest.approx(11_000.0 - 360.0)
        assert portfolio.snapshot(day(1)).positions[0].average_price == pytest.approx(100.0)

    def test_crossing_from_long_to_short_resets_average_to_fill_price(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        portfolio.apply(fill(Side.BUY, 5, 100.0))
        portfolio.apply(fill(Side.SELL, 8, 120.0, seq=2))
        assert portfolio.held_qty(INSTRUMENT) == Decimal(-3)
        assert portfolio.snapshot(day(1)).positions[0].average_price == pytest.approx(120.0)
        assert portfolio.cash == pytest.approx(10_000.0 - 500.0 + 960.0)

    def test_adding_to_short_averages_entry_price(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        portfolio.apply(fill(Side.SELL, 10, 100.0))
        portfolio.apply(fill(Side.SELL, 10, 120.0, seq=2))
        assert portfolio.snapshot(day(1)).positions[0].average_price == pytest.approx(110.0)

    def test_short_mark_to_market_signs(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        portfolio.apply(fill(Side.SELL, 10, 100.0))
        portfolio.mark(make_snapshot(day(2), make_bar(day(2), INSTRUMENT, 90.0, 90.0)))
        snapshot = portfolio.snapshot(day(2))
        position = snapshot.positions[0]
        assert position.market_value == pytest.approx(-900.0)
        assert position.unrealized_pnl == pytest.approx(100.0)  # (100 − 90) × 10
        assert snapshot.equity == pytest.approx(11_000.0 - 900.0)
        assert snapshot.gross_exposure == pytest.approx(900.0 / 10_100.0)

    def test_short_rejected_when_not_allowed(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0)
        with pytest.raises(NegativePositionError):
            portfolio.apply(fill(Side.SELL, 1, 100.0))

    def test_charge_cost_reduces_cash(self) -> None:
        portfolio = Portfolio(initial_cash=10_000.0, allow_short=True)
        cost = CostAccrued(
            ts=day(2), kind=CostKind.SHORT_BORROW, instrument=INSTRUMENT, amount=0.09
        )
        portfolio.charge(cost)
        assert portfolio.cash == pytest.approx(9_999.91)
        with pytest.raises(ValueError, match="amount"):
            portfolio.charge(
                CostAccrued(ts=day(2), kind=CostKind.SHORT_BORROW, instrument=None, amount=-1.0)
            )


# --- Router -------------------------------------------------------------------

SHORT_FEATURES = frozenset({EngineFeature.SHORT_SELLING})


def policy() -> ExecutionPolicy:
    return ExecutionPolicy.market_next_open()


class TestRouterAllowsNegativeTargetsWhenDeclared:
    def test_negative_weight_and_quantity_targets(self) -> None:
        router = make_router(features=SHORT_FEATURES)
        from tests.test_router import market

        weight = StrategyDecision.of(
            day(1), SetPositionTarget(target=WeightTarget(INSTRUMENT, -0.3), execution=policy())
        )
        (order,) = router.route(weight, "D-000001", portfolio_with(100_000), market()).orders
        assert (order.side, order.quantity) == (Side.SELL, Decimal(300))

        quantity = StrategyDecision.of(
            day(1),
            SetPositionTarget(target=QuantityTarget(INSTRUMENT, Decimal(-10)), execution=policy()),
        )
        (order,) = router.route(quantity, "D-000002", portfolio_with(100_000), market()).orders
        assert (order.side, order.quantity) == (Side.SELL, Decimal(10))

    def test_delta_and_submit_beyond_held(self) -> None:
        from tests.test_router import market

        router = make_router(features=SHORT_FEATURES)
        held5 = portfolio_with(0.0, {INSTRUMENT: (5, 100.0)})
        delta = StrategyDecision.of(
            day(1),
            AdjustPosition(
                instrument=INSTRUMENT, delta=QuantityDelta(Decimal(-8)), execution=policy()
            ),
        )
        (order,) = router.route(delta, "D-000001", held5, market()).orders
        assert (order.side, order.quantity) == (Side.SELL, Decimal(8))

        submit = StrategyDecision.of(
            day(1),
            SubmitOrder(
                request=MarketOrderRequest(
                    core=OrderCore(INSTRUMENT, Side.SELL, Decimal(11), TimeInForce.DAY)
                )
            ),
        )
        (order,) = router.route(submit, "D-000002", held5, market()).orders
        assert order.quantity == Decimal(11)

    def test_liquidating_a_short_buys_back(self) -> None:
        from tests.test_router import market

        router = make_router(features=SHORT_FEATURES)
        short = portfolio_with(11_000.0, {INSTRUMENT: (-10, 100.0)})
        decision = StrategyDecision.of(
            day(1),
            LiquidatePosition(
                instrument=INSTRUMENT,
                execution=policy(),
                cancel_open_orders=False,
                persistence=LiquidationPersistence.ONCE,
            ),
        )
        (order,) = router.route(decision, "D-000001", short, market()).orders
        assert (order.side, order.quantity) == (Side.BUY, Decimal(10))

    def test_still_rejected_without_declaration(self) -> None:
        from tests.test_router import market

        router = make_router(features=frozenset())
        decision = StrategyDecision.of(
            day(1),
            SetPositionTarget(target=QuantityTarget(INSTRUMENT, Decimal(-1)), execution=policy()),
        )
        with pytest.raises(UnsupportedActionValue, match="SHORT_SELLING"):
            router.route(decision, "D-000001", portfolio_with(100_000), market())


# --- Engine golden -------------------------------------------------------------

BARS: tuple[Bar, ...] = (
    make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
    make_ohlc(day(2), INSTRUMENT, 100.0, 100.0, 90.0, 90.0),
    make_ohlc(day(3), INSTRUMENT, 80.0, 85.0, 80.0, 85.0),
)


class ShortStrategy:
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
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_POSITION_TARGET}),
            features=frozenset({EngineFeature.SHORT_SELLING}),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now)
        return StrategyDecision.of(ctx.now, action)


def target(quantity: int) -> SetPositionTarget:
    return SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(quantity)), execution=policy()
    )


def run_short() -> tuple[BacktestEngine, BacktestResult]:
    config = RunConfig(
        run_id="short", initial_cash=10_000.0, fee_bps=0.0, short_borrow_bps_annual=252.0
    )
    engine = BacktestEngine(config)
    result = engine.run(ShortStrategy((target(-10), target(0))), DataFeed(BARS))
    return engine, result


def replay(
    initial_cash: float,
    fills: tuple[FillEvent, ...],
    costs: tuple[CostAccrued, ...],
    snapshots: tuple[PortfolioSnapshot, ...],
) -> list[float]:
    """Bar(스냅샷의 mark)·Fill·Cost만으로 세션별 equity를 재계산한다."""
    cash = initial_cash
    quantity = Decimal(0)
    equities: list[float] = []
    for snapshot in snapshots:
        for f in fills:
            if f.ts == snapshot.ts:
                signed = f.quantity if f.side is Side.BUY else -f.quantity
                cash -= float(signed) * f.price + f.fee
                quantity += signed
        for c in costs:
            if c.ts == snapshot.ts:
                cash -= c.amount
        mark = snapshot.position(INSTRUMENT)
        price = mark.market_price if mark is not None else 0.0
        equities.append(cash + float(quantity) * price)
    return equities


class TestShortGolden:
    def test_cash_position_and_costs_hand_computed(self) -> None:
        engine, result = run_short()
        d2, d3 = result.snapshots[1], result.snapshots[2]
        assert d2.position_qty(INSTRUMENT) == Decimal(-10)
        assert d2.cash == pytest.approx(10_999.91)
        assert d2.equity == pytest.approx(10_099.91)
        assert d3.position_qty(INSTRUMENT) == Decimal(0)
        assert d3.cash == pytest.approx(10_199.91)
        costs = engine.event_store.costs()
        assert [(c.ts, c.kind, c.amount) for c in costs] == [
            (day(2), CostKind.SHORT_BORROW, pytest.approx(0.09))
        ]

    def test_snapshots_replayable_from_fills_and_costs(self) -> None:
        engine, result = run_short()
        replayed = replay(10_000.0, result.fills, engine.event_store.costs(), result.snapshots)
        assert replayed == pytest.approx([s.equity for s in result.snapshots])

    def test_no_borrow_cost_when_rate_is_zero(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="short0", initial_cash=10_000.0, fee_bps=0.0))
        result = engine.run(ShortStrategy((target(-10), target(0))), DataFeed(BARS))
        assert engine.event_store.costs() == ()
        assert result.snapshots[1].cash == pytest.approx(11_000.0)
