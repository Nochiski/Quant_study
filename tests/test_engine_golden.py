"""엔진 골든 테스트: 아주 짧은 수동 예제로 cash, position, equity가
손계산과 일치하는지 확인한다. 상태 전이·계약 테스트도 함께 둔다.

골든 시나리오 (fee_bps=10, 초기 현금 100,000):
  D1  open 100 close 100  전략: 70% 목표  → 700주 매수 주문 (체결 없음)
  D2  open 110 close 120  체결: 700주 @110, 수수료 77 → 현금 22,923
  D3  open 130 close 125  전략: 긴급 청산 → 700주 매도 주문
  D4  open  90 close  80  체결: 700주 @90, 수수료 63 → 현금 85,860, flat
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.errors import CapabilityNotImplemented, UndeclaredActionReturned
from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    ExecutionPolicy,
    LiquidatePosition,
    LiquidationPersistence,
    QuantityDelta,
    SetPortfolioTarget,
    StrategyAction,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OrderStatus, StrategyEvent
from backtest_engine.types.market import Bar, PriceField
from backtest_engine.types.orders import Side
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_bar, make_instrument

INSTRUMENT = make_instrument()

GOLDEN_BARS: tuple[Bar, ...] = (
    make_bar(day(1), INSTRUMENT, 100.0, 100.0),
    make_bar(day(2), INSTRUMENT, 110.0, 120.0),
    make_bar(day(3), INSTRUMENT, 130.0, 125.0),
    make_bar(day(4), INSTRUMENT, 90.0, 80.0),
)


class ScriptedStrategy:
    """호출 순서대로 미리 정한 Action을 반환하는 테스트 전략."""

    def __init__(
        self,
        script: tuple[StrategyAction | None, ...],
        lookback: int = 1,
        declared_actions: frozenset[ActionKind] | None = None,
    ) -> None:
        self._script = script
        self._calls = 0
        self.prices = HistoryRequest(
            instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=lookback
        )
        self._declared_actions = declared_actions or frozenset(
            {ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET, ActionKind.LIQUIDATE_POSITION}
        )

    @property
    def calls(self) -> int:
        return self._calls

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(self.prices,),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=self._declared_actions,
            features=frozenset(),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now, "scripted_idle")
        return StrategyDecision.of(ctx.now, action, f"scripted_{index}")


def target_70pct() -> StrategyAction:
    return SetPortfolioTarget(
        targets=(WeightTarget(INSTRUMENT, 0.7),),
        scope=TargetScope.PATCH,
        execution=ExecutionPolicy.market_next_open(),
    )


def liquidate() -> StrategyAction:
    return LiquidatePosition(
        instrument=INSTRUMENT,
        execution=ExecutionPolicy.market_next_available(),
        cancel_open_orders=True,
        persistence=LiquidationPersistence.UNTIL_FLAT,
    )


def adjust_by(quantity: int) -> StrategyAction:
    return AdjustPosition(
        instrument=INSTRUMENT,
        delta=QuantityDelta(Decimal(quantity)),
        execution=ExecutionPolicy.market_next_open(),
    )


def run_golden() -> tuple[BacktestEngine, ScriptedStrategy, BacktestResult]:
    engine = BacktestEngine(RunConfig(run_id="golden", initial_cash=100_000.0, fee_bps=10.0))
    strategy = ScriptedStrategy(script=(target_70pct(), None, liquidate(), None))
    result = engine.run(strategy, DataFeed(GOLDEN_BARS))
    return engine, strategy, result


class TestGoldenRun:
    def test_orders_and_fills_hand_computed(self) -> None:
        _, _, result = run_golden()

        assert [(o.side, int(o.quantity)) for o in result.orders] == [
            (Side.BUY, 700),
            (Side.SELL, 700),
        ]
        buy_fill, sell_fill = result.fills
        # 판단은 D1 종가(100)에서 했지만 체결은 D2 시가(110)다 — look-ahead 차단.
        assert buy_fill.ts == day(2)
        assert buy_fill.price == pytest.approx(110.0)
        assert buy_fill.fee == pytest.approx(77.0)
        assert sell_fill.ts == day(4)
        assert sell_fill.price == pytest.approx(90.0)
        assert sell_fill.fee == pytest.approx(63.0)

    def test_snapshots_hand_computed(self) -> None:
        _, _, result = run_golden()
        d1, d2, d3, d4 = result.snapshots

        # D1: 주문만 만들어졌고 자산은 그대로 (상태 전이 불변조건).
        assert d1.cash == pytest.approx(100_000.0)
        assert d1.positions == ()
        assert d1.equity == pytest.approx(100_000.0)

        # D2: 700주 @110 체결 + 수수료 77 → 현금 22,923, 종가 120 평가.
        assert d2.cash == pytest.approx(22_923.0)
        assert d2.positions[0].quantity == 700
        assert d2.positions[0].average_price == pytest.approx(110.0)
        assert d2.positions[0].market_value == pytest.approx(84_000.0)
        assert d2.equity == pytest.approx(106_923.0)

        # D3: 체결 없음, 종가 125 평가만 갱신.
        assert d3.cash == pytest.approx(22_923.0)
        assert d3.equity == pytest.approx(110_423.0)

        # D4: 긴급 청산 체결 700주 @90 - 수수료 63 → flat.
        assert d4.cash == pytest.approx(85_860.0)
        assert d4.positions == ()
        assert d4.equity == pytest.approx(85_860.0)

    def test_metrics_hand_computed(self) -> None:
        _, _, result = run_golden()
        assert result.metrics.total_return == pytest.approx(85_860.0 / 100_000.0 - 1.0)
        # 고점 110,423 → 85,860
        assert result.metrics.max_drawdown == pytest.approx(85_860.0 / 110_423.0 - 1.0)

    def test_event_store_traces_full_chain(self) -> None:
        engine, _, _ = run_golden()
        store = engine.event_store
        assert len(store.decisions()) == 4  # 매 세션 호출
        assert len(store.orders()) == 2
        assert len(store.fills()) == 2
        assert len(store.snapshots()) == 4
        # Order → Fill 추적 고리 확인
        order_ids = {order.order_id for order in store.orders()}
        assert {fill.order_id for fill in store.fills()} == order_ids


class TestEngineContracts:
    def test_capability_gate_rejects_before_loop(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="gate", initial_cash=100_000.0))
        # requirements에 미구현 BASKET + SHORT_SELLING을 요구하도록 재구성

        class Rejected(ScriptedStrategy):
            def requirements(self) -> StrategyRequirements:
                base = super().requirements()
                return StrategyRequirements(
                    histories=base.histories,
                    schedule=base.schedule,
                    events=base.events,
                    actions=frozenset({ActionKind.BASKET}),
                    features=frozenset({EngineFeature.SHORT_SELLING}),
                )

        strategy = Rejected(script=(), declared_actions=frozenset({ActionKind.BASKET}))

        with pytest.raises(CapabilityNotImplemented, match="short_selling"):
            engine.run(strategy, DataFeed(GOLDEN_BARS))
        assert strategy.calls == 0  # 데이터 루프 전에 거절됐다

    def test_undeclared_action_rejected_at_runtime(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="undeclared", initial_cash=100_000.0))
        strategy = ScriptedStrategy(
            script=(liquidate(),),
            declared_actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
        )
        with pytest.raises(UndeclaredActionReturned, match="liquidate_position"):
            engine.run(strategy, DataFeed(GOLDEN_BARS))

    def test_warmup_delays_first_strategy_call(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="warmup", initial_cash=100_000.0))
        strategy = ScriptedStrategy(script=(None, None, None, None), lookback=3)
        result = engine.run(strategy, DataFeed(GOLDEN_BARS))
        # 4세션 중 lookback=3을 채운 D3부터 호출 → 2회
        assert strategy.calls == 2
        # 스냅샷은 워밍업 중에도 매 세션 기록된다
        assert len(result.snapshots) == 4

    def test_cash_limited_buy_records_order_update(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="cash-cap", initial_cash=100_000.0, fee_bps=10.0))
        full_target = SetPortfolioTarget(
            targets=(WeightTarget(INSTRUMENT, 1.0),),
            scope=TargetScope.PATCH,
            execution=ExecutionPolicy.market_next_open(),
        )
        strategy = ScriptedStrategy(script=(full_target, None, None, None))
        result = engine.run(strategy, DataFeed(GOLDEN_BARS))

        # D1 종가 100 기준 1,000주 주문 → D2 시가 110에서는 908주만 현금으로 가능
        assert int(result.orders[0].quantity) == 1_000
        assert int(result.fills[0].quantity) == 908
        updates = engine.event_store.order_updates()
        assert any(update.status is OrderStatus.PARTIALLY_FILLED for update in updates)
        # 잔여 수량은 이월되지 않는다 (DAY) — 이후 체결 없음
        assert len(result.fills) == 1


class TestAdjustPositionGolden:
    """4a 골든: AdjustPosition(+10) → (-4). fee_bps=10, 초기 현금 100,000.

    D1 +10주 주문 → D2 시가 110 체결, 수수료 1.1 → 현금 98,898.9
    D3 -4주 주문 → D4 시가 90 체결, 수수료 0.36 → 현금 99,258.54, 보유 6주
    """

    def _run(self) -> BacktestResult:
        engine = BacktestEngine(RunConfig(run_id="adjust", initial_cash=100_000.0, fee_bps=10.0))
        declared = frozenset({ActionKind.NO_ACTION, ActionKind.ADJUST_POSITION})
        strategy = ScriptedStrategy(
            script=(adjust_by(10), None, adjust_by(-4), None), declared_actions=declared
        )
        return engine.run(strategy, DataFeed(GOLDEN_BARS))

    def test_fills_hand_computed(self) -> None:
        result = self._run()
        assert [(f.ts, f.side, int(f.quantity), f.price) for f in result.fills] == [
            (day(2), Side.BUY, 10, 110.0),
            (day(4), Side.SELL, 4, 90.0),
        ]
        assert result.fills[0].fee == pytest.approx(1.1)
        assert result.fills[1].fee == pytest.approx(0.36)

    def test_final_snapshot_hand_computed(self) -> None:
        result = self._run()
        final = result.snapshots[-1]
        assert final.cash == pytest.approx(99_258.54)
        assert int(final.position_qty(INSTRUMENT)) == 6
        assert final.equity == pytest.approx(99_258.54 + 6 * 80.0)
