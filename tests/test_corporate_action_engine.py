"""엔진의 자본변동 처리 골든 (D1): 확인된 분할만 포지션을 조정하고 기록·알림은 모두 남긴다.

시나리오 (fee 0, 초기 현금 10,000):
  D1 close 100  전략: 7주 목표 → D2 시가 100 체결 (현금 9,300, 7주 @100)
  D2 close 105  equity 9,300 + 735 = 10,035
  D3 분할 세션: 시가·종가는 분할 후 가격
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.errors import CorporateActionWithoutBar
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    QuantityTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CorporateActionType,
    OrderStatus,
    StrategyEvent,
)
from backtest_engine.types.market import Bar, PriceField
from backtest_engine.types.orders import LimitOrderRequest, OrderCore, Side, TimeInForce
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


def bars_with_split(post_open: float, post_close: float) -> tuple[Bar, ...]:
    return (
        make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        make_ohlc(day(2), INSTRUMENT, 100.0, 106.0, 99.0, 105.0),
        make_ohlc(
            day(3),
            INSTRUMENT,
            post_open,
            max(post_open, post_close),
            min(post_open, post_close),
            post_close,
        ),
        make_ohlc(day(4), INSTRUMENT, post_close, post_close, post_close, post_close),
    )


def split(
    ratio: str, kind: CorporateActionType = CorporateActionType.SPLIT
) -> CorporateActionEvent:
    return CorporateActionEvent(
        ts=day(3), instrument=INSTRUMENT, action_type=kind, ratio=Decimal(ratio), detail="test"
    )


class Strategy:
    def __init__(
        self,
        script: tuple[StrategyAction | None, ...],
        events: frozenset[EventKind] = frozenset({EventKind.MARKET}),
    ) -> None:
        self._script = script
        self._events = events
        self.received: list[StrategyEvent] = []
        self._calls = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(
                HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=1),
            ),
            schedule=EverySession(),
            events=self._events,
            actions=frozenset(
                {ActionKind.NO_ACTION, ActionKind.SET_POSITION_TARGET, ActionKind.SUBMIT_ORDER}
            ),
            features=frozenset({EngineFeature.LIMIT_ORDER}),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.received.append(event)
        index = self._calls
        self._calls += 1
        action = self._script[index] if index < len(self._script) else None
        if action is None:
            return StrategyDecision.no_action(ctx.now)
        return StrategyDecision.of(ctx.now, action)


def buy(quantity: int) -> SetPositionTarget:
    return SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(quantity)),
        execution=ExecutionPolicy.market_next_open(),
    )


def gtc_limit_buy(price: float) -> SubmitOrder:
    core = OrderCore(INSTRUMENT, Side.BUY, Decimal(1), TimeInForce.GTC)
    return SubmitOrder(request=LimitOrderRequest(core=core, limit_price=Decimal(str(price))))


def run(
    bars: tuple[Bar, ...],
    actions: tuple[CorporateActionEvent, ...],
    script: tuple[StrategyAction | None, ...] = (buy(7),),
    events: frozenset[EventKind] = frozenset({EventKind.MARKET}),
) -> tuple[BacktestEngine, Strategy, BacktestResult]:
    engine = BacktestEngine(RunConfig(run_id="split", initial_cash=10_000.0, fee_bps=0.0))
    strategy = Strategy(script, events)
    result = engine.run(strategy, DataFeed(bars), corporate_actions=actions)
    return engine, strategy, result


class TestConfirmedSplit:
    def test_whole_ratio_scales_quantity_and_average_price(self) -> None:
        engine, _, result = run(bars_with_split(20.0, 21.0), (split("5"),))
        d2, d3 = result.snapshots[1], result.snapshots[2]
        assert d2.position_qty(INSTRUMENT) == Decimal(7)
        assert d3.position_qty(INSTRUMENT) == Decimal(35)
        position = d3.position(INSTRUMENT)
        assert position is not None
        assert position.average_price == pytest.approx(20.0)
        assert d3.cash == pytest.approx(9_300.0)
        applied = engine.event_store.corporate_actions_applied()
        assert [(a.old_quantity, a.new_quantity, a.cash_paid) for a in applied] == [
            (Decimal(7), Decimal(35), 0.0)
        ]

    def test_fractional_shares_paid_in_cash_at_split_session_open(self) -> None:
        # 7 × 1.5 = 10.5 → 10주 + 0.5주 × 시가 60 = 30 현금
        engine, _, result = run(bars_with_split(60.0, 70.0), (split("1.5"),))
        d3 = result.snapshots[2]
        assert d3.position_qty(INSTRUMENT) == Decimal(10)
        assert d3.cash == pytest.approx(9_330.0)
        assert engine.event_store.corporate_actions_applied()[0].cash_paid == pytest.approx(30.0)

    def test_equity_is_continuous_across_split(self) -> None:
        # D2 equity 9,300 + 7×105 = 10,035. D3 종가 70(=105/1.5): 9,330 + 10×70 = 10,030
        # 차이 5는 단주 0.5주를 시가 60에 정산했기 때문 (0.5 × (70 − 60))
        _, _, result = run(bars_with_split(60.0, 70.0), (split("1.5"),))
        assert result.snapshots[1].equity == pytest.approx(10_035.0)
        assert result.snapshots[2].equity == pytest.approx(10_030.0)

    def test_open_orders_for_instrument_are_cancelled(self) -> None:
        # D2 세션 종료에 GTC 지정가 제출 → D3 분할 세션에 취소
        engine, _, result = run(
            bars_with_split(20.0, 21.0), (split("5"),), script=(buy(7), gtc_limit_buy(50.0))
        )
        assert len(result.fills) == 1
        updates = [u for u in engine.event_store.order_updates() if u.order_id == "O-000002"]
        assert [(u.ts, u.status) for u in updates] == [(day(3), OrderStatus.CANCELLED)]
        assert "corporate action" in (updates[0].detail or "")

    def test_reverse_split_floors_quantity(self) -> None:
        # 7주 × 0.1 = 0.7 → 0주, 0.7주 × 시가 1,000 = 700 현금
        engine, _, result = run(
            bars_with_split(1_000.0, 1_050.0), (split("0.1", CorporateActionType.REVERSE_SPLIT),)
        )
        d3 = result.snapshots[2]
        assert d3.position_qty(INSTRUMENT) == Decimal(0)
        assert d3.cash == pytest.approx(9_300.0 + 700.0)

    def test_no_position_records_nothing_but_still_notifies(self) -> None:
        engine, strategy, _ = run(
            bars_with_split(20.0, 21.0),
            (split("5"),),
            script=(None,),
            events=frozenset({EventKind.MARKET, EventKind.CORPORATE_ACTION}),
        )
        assert engine.event_store.corporate_actions_applied() == ()
        assert [e for e in strategy.received if isinstance(e, CorporateActionEvent)] == [split("5")]


class TestUnconfirmedAndDelivery:
    def test_share_count_change_leaves_position_untouched(self) -> None:
        engine, _, result = run(
            bars_with_split(100.0, 100.0),
            (split("50", CorporateActionType.SHARE_COUNT_CHANGE),),
        )
        assert result.snapshots[2].position_qty(INSTRUMENT) == Decimal(7)
        assert engine.event_store.corporate_actions_applied() == ()
        assert len(engine.event_store.corporate_actions()) == 1

    def test_event_delivered_only_when_declared(self) -> None:
        _, declared, _ = run(
            bars_with_split(20.0, 21.0),
            (split("5"),),
            events=frozenset({EventKind.MARKET, EventKind.CORPORATE_ACTION}),
        )
        assert [e for e in declared.received if isinstance(e, CorporateActionEvent)] == [split("5")]
        _, undeclared, _ = run(bars_with_split(20.0, 21.0), (split("5"),))
        assert not any(isinstance(e, CorporateActionEvent) for e in undeclared.received)

    def test_event_delivered_after_position_adjustment(self) -> None:
        seen: list[Decimal] = []

        class Probe(Strategy):
            def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
                if isinstance(event, CorporateActionEvent):
                    seen.append(ctx.position_qty(INSTRUMENT))
                return super().on_event(ctx, event)

        engine = BacktestEngine(RunConfig(run_id="split", initial_cash=10_000.0, fee_bps=0.0))
        engine.run(
            Probe((buy(7),), frozenset({EventKind.MARKET, EventKind.CORPORATE_ACTION})),
            DataFeed(bars_with_split(20.0, 21.0)),
            corporate_actions=(split("5"),),
        )
        assert seen == [Decimal(35)]

    def test_action_without_bar_aborts_run(self) -> None:
        bars = bars_with_split(20.0, 21.0)
        orphan = CorporateActionEvent(
            ts=datetime(2026, 8, 10),
            instrument=INSTRUMENT,
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal(5),
            detail="no session",
        )
        with pytest.raises(CorporateActionWithoutBar, match="2026-08-10"):
            run(bars, (orphan,))

    def test_applied_record_type_is_exposed(self) -> None:
        engine, _, _ = run(bars_with_split(20.0, 21.0), (split("5"),))
        (applied,) = engine.event_store.corporate_actions_applied()
        assert isinstance(applied, CorporateActionApplied)
        assert applied.ts == day(3)
        assert applied.instrument == INSTRUMENT


class TestReviewRegressions:
    def test_action_on_halted_session_settles_at_next_traded_open(self) -> None:
        """DEFECT-201: 사건 세션이 거래정지로 feed에 없으면 다음 거래 세션 시가로 정산한다."""
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 100.0, 106.0, 99.0, 105.0),
            # day(3): 거래정지 → bar 없음 (사건 세션)
            make_ohlc(day(4), INSTRUMENT, 20.0, 21.0, 20.0, 21.0),
        )
        engine, _, result = run(bars, (split("5"),))
        (applied,) = engine.event_store.corporate_actions_applied()
        assert applied.ts == day(4)
        assert applied.new_quantity == Decimal(35)
        assert result.snapshots[-1].position_qty(INSTRUMENT) == Decimal(35)

    def test_share_count_change_on_halted_session_is_recorded_not_fatal(self) -> None:
        bars = (
            make_ohlc(day(1), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
            make_ohlc(day(2), INSTRUMENT, 100.0, 106.0, 99.0, 105.0),
            make_ohlc(day(4), INSTRUMENT, 100.0, 100.0, 100.0, 100.0),
        )
        engine, _, result = run(bars, (split("50", CorporateActionType.SHARE_COUNT_CHANGE),))
        assert len(engine.event_store.corporate_actions()) == 1
        assert result.snapshots[-1].position_qty(INSTRUMENT) == Decimal(7)

    def test_action_after_last_session_aborts(self) -> None:
        late = CorporateActionEvent(
            ts=day(9),
            instrument=INSTRUMENT,
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal(5),
            detail="after feed",
        )
        with pytest.raises(CorporateActionWithoutBar, match="2026-08-09"):
            run(bars_with_split(20.0, 21.0), (late,))

    def test_non_terminating_ratio_does_not_lose_a_whole_share(self) -> None:
        """DEFECT-202: 30주 × (1/3) = 10주여야 한다 (9.999… → 9 금지)."""
        from backtest_engine.engine.portfolio import Portfolio
        from backtest_engine.types.events import FillEvent

        portfolio = Portfolio(1_000_000.0)
        portfolio.apply(
            FillEvent(
                fill_id="F-1",
                order_id="O-1",
                ts=day(1),
                instrument=INSTRUMENT,
                quantity=Decimal(30),
                side=Side.BUY,
                price=1_000.0,
                fee=0.0,
                slippage_per_share=0.0,
            )
        )
        ratio = Decimal(100_000_000) / Decimal(300_000_000)
        action = CorporateActionEvent(
            ts=day(2),
            instrument=INSTRUMENT,
            action_type=CorporateActionType.REVERSE_SPLIT,
            ratio=ratio,
            detail="3:1",
        )
        applied = portfolio.apply_corporate_action(action, 3_000.0)
        assert applied is not None
        assert applied.new_quantity == Decimal(10)
        assert applied.cash_paid == pytest.approx(0.0)
