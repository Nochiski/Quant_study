"""5b MARGIN: 총노출 한도 기반 매수 여력, 음수 현금 이자, 자본 잠식 거절.

엔진 골든 (fee 0, 초기 현금 10,000, 레버리지 2.0, 이자 연 252bp = 세션당 1bp):
  D1 close 100  전략: QuantityTarget(150) → D2 시가 100 매수 150주 (여력 20,000 ≥ 15,000)
                현금 −5,000, 수량 150
  D2 close 110  평가 16,500, equity 11,500, 이자 5,000 × 0.0001 = 0.5 → 현금 −5,000.5
  D3 open  105  전략(D2): QuantityTarget(0) → 매도 150주 @105 = 15,750 → 현금 10,749.5
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.broker import BrokerSim, ExecutionStatus
from backtest_engine.engine.orders import OpenOrder
from backtest_engine.errors import EquityWipedOut, UndeclaredFeatureUsed
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    NoAction,
    QuantityTarget,
    SetPositionTarget,
    StrategyAction,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import CostKind, OrderEvent, StrategyEvent
from backtest_engine.types.market import Bar, PriceField
from backtest_engine.types.orders import OrderType, Side, TimeInForce
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
BAR = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0)


def market_buy(quantity: int) -> OpenOrder:
    order = OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=Side.BUY,
        source_action=NoAction(),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    return OpenOrder(order=order, remaining=Decimal(quantity))


class TestBrokerBuyingPower:
    def test_buying_power_caps_fill_and_names_leverage(self) -> None:
        # 여력 20,000 → 200주까지
        outcome = BrokerSim(fee_bps=0.0).execute(market_buy(250), BAR, 20_000.0, "F-1")
        assert outcome.status is ExecutionStatus.CASH_LIMITED
        assert outcome.fill is not None
        assert outcome.fill.quantity == Decimal(200)
        assert "buying_power" in (outcome.detail or "")


class MarginStrategy:
    def __init__(
        self, script: tuple[StrategyAction | None, ...], declare_margin: bool = True
    ) -> None:
        self._script = script
        self._declare_margin = declare_margin
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(
                HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=1),
            ),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_POSITION_TARGET}),
            features=frozenset({EngineFeature.MARGIN}) if self._declare_margin else frozenset(),
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
        target=QuantityTarget(INSTRUMENT, Decimal(quantity)),
        execution=ExecutionPolicy.market_next_open(),
    )


BARS: tuple[Bar, ...] = (
    make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
    make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 100.0, 110.0),
    make_ohlc(day(3), INSTRUMENT, 105.0, 105.0, 100.0, 100.0),
)


def config(leverage: float = 2.0, interest_bps: float = 252.0) -> RunConfig:
    return RunConfig(
        run_id="margin",
        initial_cash=10_000.0,
        fee_bps=0.0,
        max_gross_leverage=leverage,
        margin_interest_bps_annual=interest_bps,
    )


def run_margin(script: tuple[StrategyAction | None, ...]) -> tuple[BacktestEngine, BacktestResult]:
    engine = BacktestEngine(config())
    return engine, engine.run(MarginStrategy(script), DataFeed(BARS))


class TestMarginGolden:
    def test_leveraged_buy_negative_cash_and_interest(self) -> None:
        engine, result = run_margin((target(150), target(0)))
        d2, d3 = result.snapshots[1], result.snapshots[2]
        assert d2.position_qty(INSTRUMENT) == Decimal(150)
        assert d2.cash == pytest.approx(-5_000.5)
        assert d2.equity == pytest.approx(-5_000.5 + 16_500.0)
        assert [(c.ts, c.kind, c.amount) for c in engine.event_store.costs()] == [
            (day(2), CostKind.MARGIN_INTEREST, pytest.approx(0.5))
        ]
        assert d3.position_qty(INSTRUMENT) == Decimal(0)
        assert d3.cash == pytest.approx(10_749.5)

    def test_buying_power_shrinks_with_existing_exposure(self) -> None:
        # 150주 @110 평가 16,500, equity 11,499.5 → 여력 2×11,499.5 − 16,500 = 6,499 → @105 61주
        engine, result = run_margin((target(150), target(400)))
        fills = result.fills
        assert [(f.ts, int(f.quantity)) for f in fills] == [(day(2), 150), (day(3), 61)]

    def test_leverage_above_one_requires_margin_declaration(self) -> None:
        engine = BacktestEngine(config())
        with pytest.raises(UndeclaredFeatureUsed, match="max_gross_leverage"):
            engine.run(MarginStrategy((target(1),), declare_margin=False), DataFeed(BARS))

    def test_without_margin_cash_is_the_limit(self) -> None:
        engine = BacktestEngine(config(leverage=1.0, interest_bps=0.0))
        result = engine.run(MarginStrategy((target(150),), declare_margin=False), DataFeed(BARS))
        assert int(result.fills[0].quantity) == 100
        assert engine.event_store.costs() == ()

    def test_equity_wipeout_aborts_run(self) -> None:
        crash: tuple[Bar, ...] = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(
                day(3), INSTRUMENT, 30.0, 30.0, 30.0, 30.0
            ),  # 150주 × 30 = 4,500 < 5,000 부채
        )
        engine = BacktestEngine(config(interest_bps=0.0))
        with pytest.raises(EquityWipedOut, match="equity"):
            engine.run(MarginStrategy((target(150),)), DataFeed(crash))
