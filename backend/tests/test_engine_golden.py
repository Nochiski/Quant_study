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
from backtest_engine.engine.store import EventStore, RecordKind
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
from backtest_engine.types.events import CostAccrued, CostKind, OrderStatus, StrategyEvent
from backtest_engine.types.market import Bar, PriceField
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot, Position
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from tests.conftest import day, make_bar, make_instrument, make_snapshot

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

    def test_record_kind_is_traced_by_its_name(self) -> None:
        """trace의 kind 표기는 wire 계약이다.

        python·rust trace 비교는 같은 직렬화기를 쓰므로 두 쪽이 함께 바뀌면 아무 테스트도
        깨지지 않는다. RecordKind에 wire 코드를 붙이면서 `value`가 튜플로 새어 나가는 회귀를
        여기서 막는다.
        """
        engine, _, _ = run_golden()
        trace = engine.event_store.normalized_trace()
        assert trace[0]["kind"] == {
            "$enum": "backtest_engine.engine.store.RecordKind",
            "value": "market",
        }
        assert {entry["kind"]["value"] for entry in trace} == {
            "market",
            "decision",
            "order",
            "order_update",
            "fill",
            "snapshot",
        }


class TestEngineContracts:
    def test_capability_gate_rejects_before_loop(self) -> None:
        engine = BacktestEngine(RunConfig(run_id="gate", initial_cash=100_000.0))
        # 미구현 TIMER 이벤트를 요구하도록 재구성 (5단계 이후 남은 NOT_IMPLEMENTED 축)

        class Rejected(ScriptedStrategy):
            def requirements(self) -> StrategyRequirements:
                base = super().requirements()
                return StrategyRequirements(
                    histories=base.histories,
                    schedule=base.schedule,
                    events=frozenset({EventKind.MARKET, EventKind.TIMER}),
                    actions=base.actions,
                    features=base.features,
                )

        strategy = Rejected(script=())

        with pytest.raises(CapabilityNotImplemented, match="timer"):
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


class TestResultTables:
    """`EventStore.result_tables()` — 공개 객체를 거치지 않는 결과 집계 계약.

    골든 시나리오의 손계산 값을 그대로 쓰되, 여기서는 행이 primitive인지와 집계 결합
    순서가 `sum` 표현식과 bit 동일한지를 고정한다.
    """

    def test_lookup_tables_follow_feed_and_bar_order(self) -> None:
        engine, _, _ = run_golden()
        tables = engine.event_store.result_tables()

        assert tables.sessions == (day(1), day(2), day(3), day(4))
        assert tables.instruments == (INSTRUMENT,)

    def test_snapshot_rows_are_hand_computed_primitives(self) -> None:
        engine, _, _ = run_golden()
        tables = engine.event_store.result_tables()

        sessions = [row[0] for row in tables.snapshots]
        assert sessions == [0, 1, 2, 3]
        d1, d2, d3, d4 = tables.snapshots
        # (session_index, cash, equity, gross_exposure, positions_value)
        assert d1[1] == pytest.approx(100_000.0)
        assert d1[4] == 0.0
        assert d2[1] == pytest.approx(22_923.0)
        assert d2[2] == pytest.approx(106_923.0)
        assert d2[4] == pytest.approx(84_000.0)
        assert d3[2] == pytest.approx(110_423.0)
        assert d4[1] == pytest.approx(85_860.0)
        assert d4[4] == 0.0
        # 스냅샷 4건 중 보유가 있는 D2·D3만 position 행을 남긴다.
        assert [row[0] for row in tables.positions] == [1, 2]
        session_index, instrument_index, quantity, average_price, _, market_value, _ = (
            tables.positions[0]
        )
        assert (session_index, instrument_index) == (1, 0)
        assert quantity == 700
        assert type(quantity) is int
        assert average_price == pytest.approx(110.0)
        assert market_value == pytest.approx(84_000.0)

    def test_order_and_fill_rows_carry_enum_wire_values(self) -> None:
        engine, _, result = run_golden()
        tables = engine.event_store.result_tables()

        assert [(row[2], row[3], row[4], row[5], row[6], row[7]) for row in tables.orders] == [
            (0, 0, "buy", 700, "market", "day"),
            (2, 0, "sell", 700, "market", "day"),
        ]
        assert [row[0] for row in tables.orders] == [order.order_id for order in result.orders]
        assert [(row[2], row[3], row[4], row[5], row[6]) for row in tables.fills] == [
            (1, 0, "buy", 700, 110.0),
            (3, 0, "sell", 700, 90.0),
        ]
        assert [row[0] for row in tables.fills] == [fill.fill_id for fill in result.fills]
        assert [row[1] for row in tables.fills] == [fill.order_id for fill in result.fills]
        assert tables.costs == ()

    def test_fill_totals_match_the_sum_expressions_bit_for_bit(self) -> None:
        engine, _, result = run_golden()
        totals = engine.event_store.result_tables().fill_totals

        assert totals.traded_notional == sum(
            float(fill.quantity) * fill.price for fill in result.fills
        )
        assert totals.total_fees == sum(fill.fee for fill in result.fills)
        assert totals.total_slippage_cost == sum(
            float(fill.quantity) * abs(fill.slippage_per_share) for fill in result.fills
        )

    def test_positions_value_matches_the_sum_expression_bit_for_bit(self) -> None:
        engine, _, result = run_golden()
        tables = engine.event_store.result_tables()

        assert [row[4] for row in tables.snapshots] == [
            sum(position.market_value for position in snapshot.positions)
            for snapshot in result.snapshots
        ]

    def test_repeated_calls_reuse_the_built_tables(self) -> None:
        engine, _, _ = run_golden()
        store = engine.event_store
        assert store.result_tables() is store.result_tables()


class TestResultTableRejections:
    """레코드가 테이블 계약을 못 채우면 조용한 값 대체 대신 거부한다."""

    @staticmethod
    def _store_with_one_session() -> EventStore:
        store = EventStore()
        store.append(
            day(1),
            RecordKind.MARKET,
            make_snapshot(day(1), make_bar(day(1), INSTRUMENT, 100.0, 100.0)),
        )
        return store

    def test_fractional_quantity_is_refused_instead_of_truncated(self) -> None:
        store = self._store_with_one_session()
        store.append(
            day(1),
            RecordKind.SNAPSHOT,
            PortfolioSnapshot(
                ts=day(1),
                cash=0.0,
                positions=(
                    Position(
                        instrument=INSTRUMENT,
                        quantity=Decimal("1.5"),
                        average_price=100.0,
                        market_price=100.0,
                        market_value=150.0,
                        unrealized_pnl=0.0,
                    ),
                ),
                equity=150.0,
                gross_exposure=1.0,
            ),
        )
        with pytest.raises(ValueError, match=r"integral share quantities only.*quantity=1.5"):
            store.result_tables()

    def test_timestamp_outside_the_feed_sessions_is_refused(self) -> None:
        store = self._store_with_one_session()
        store.append(
            day(9),
            RecordKind.COST,
            CostAccrued(ts=day(9), kind=CostKind.MARGIN_INTEREST, instrument=None, amount=1.0),
        )
        with pytest.raises(ValueError, match=r"not a feed session — record=cost"):
            store.result_tables()

    def test_instrument_without_a_market_bar_is_refused(self) -> None:
        store = self._store_with_one_session()
        other = make_instrument("000660")
        store.append(
            day(1),
            RecordKind.COST,
            CostAccrued(ts=day(1), kind=CostKind.SHORT_BORROW, instrument=other, amount=1.0),
        )
        with pytest.raises(ValueError, match=r"never appeared in a market bar.*000660"):
            store.result_tables()

    def test_cost_rows_keep_kind_wire_value_and_optional_instrument(self) -> None:
        store = self._store_with_one_session()
        store.append(
            day(1),
            RecordKind.COST,
            CostAccrued(ts=day(1), kind=CostKind.SHORT_BORROW, instrument=INSTRUMENT, amount=2.5),
        )
        store.append(
            day(1),
            RecordKind.COST,
            CostAccrued(ts=day(1), kind=CostKind.MARGIN_INTEREST, instrument=None, amount=0.5),
        )
        assert store.result_tables().costs == (
            (0, "short_borrow", 0, 2.5),
            (0, "margin_interest", None, 0.5),
        )

    def test_new_records_invalidate_the_cached_tables(self) -> None:
        store = self._store_with_one_session()
        assert store.result_tables().costs == ()
        store.append(
            day(1),
            RecordKind.COST,
            CostAccrued(ts=day(1), kind=CostKind.MARGIN_INTEREST, instrument=None, amount=0.5),
        )
        assert store.result_tables().costs == ((0, "margin_interest", None, 0.5),)
