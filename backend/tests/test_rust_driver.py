"""Rust 세션 루프 드라이버(`PersistentEngine.drive()`) 경계 계약.

trace/ID/체결 패리티는 `test_core_parity.py`가 고정한다. 여기서는 드라이버가 Python 경로와
같은 시점에 같은 도메인 예외를 내는지, 콜백 프레임이 콜백 시점 상태를 고정하는지 검사한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.core import core_available, instrument_key
from backtest_engine.errors import EquityWipedOut
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.strategy import StrategyContext
from tests import test_short_selling
from tests.conftest import day, make_bar, make_instrument
from tests.test_engine_golden import GOLDEN_BARS, ScriptedStrategy, target_70pct

RUST_ONLY = pytest.mark.skipif(not core_available("rust"), reason="rust core not built")
INSTRUMENT = make_instrument()


@RUST_ONLY
def test_equity_wiped_out_is_raised_at_the_same_close_with_the_same_message() -> None:
    """숏 포지션이 급등하면 Python과 Rust 모두 같은 세션 종가에서 EquityWipedOut을 낸다."""
    bars = (
        make_bar(day(1), INSTRUMENT, 100.0, 100.0),
        make_bar(day(2), INSTRUMENT, 100.0, 100.0),
        make_bar(day(3), INSTRUMENT, 400.0, 400.0),
    )
    outcomes: list[tuple[str, bytes]] = []
    for core in ("python", "rust"):
        engine = BacktestEngine(
            RunConfig(run_id="wipe-out", initial_cash=2_000.0, fee_bps=0.0), core=core
        )
        with pytest.raises(EquityWipedOut) as caught:
            engine.run(
                test_short_selling.ShortStrategy((test_short_selling.target(-10),)),
                DataFeed(bars),
            )
        outcomes.append((str(caught.value), engine.event_store.trace_bytes()))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].startswith("equity fell below zero at session close — ts=")
    assert "positions=[('005930', '-10')]" in outcomes[0][0]


@RUST_ONLY
def test_runtime_stays_failed_after_a_session_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT-R01: 세션 마감 도메인 오류 뒤 같은 runtime을 재사용해도 조용히 이어지지 않는다."""
    from typing import Any

    from backtest_engine.engine import loop as loop_module

    real_factory = loop_module.make_persistent_runtime
    runtimes: list[Any] = []

    def capturing(*args: Any, **kwargs: Any) -> Any:
        runtime = real_factory(*args, **kwargs)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(loop_module, "make_persistent_runtime", capturing)
    bars = (
        make_bar(day(1), INSTRUMENT, 100.0, 100.0),
        make_bar(day(2), INSTRUMENT, 100.0, 100.0),
        make_bar(day(3), INSTRUMENT, 400.0, 400.0),
    )
    engine = BacktestEngine(
        RunConfig(run_id="wipe-out-reuse", initial_cash=2_000.0, fee_bps=0.0), core="rust"
    )
    with pytest.raises(EquityWipedOut):
        engine.run(
            test_short_selling.ShortStrategy((test_short_selling.target(-10),)), DataFeed(bars)
        )
    (runtime,) = runtimes
    assert runtime.lifecycle_state() == "failed"
    assert "equity_wiped_out: " in (runtime.failure_detail or "")
    with pytest.raises(ValueError, match="persistent runtime is failed"):
        runtime.drive()
    with pytest.raises(ValueError, match="cannot finish failed"):
        runtime.finish()


@RUST_ONLY
def test_retained_context_keeps_callback_time_portfolio() -> None:
    """전략이 ctx를 보관했다가 run 종료 후 읽어도 콜백 시점 포트폴리오를 본다."""
    contexts: list[StrategyContext] = []

    class RetainingStrategy(ScriptedStrategy):
        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            contexts.append(ctx)
            return super().on_event(ctx, event)

    engine = BacktestEngine(
        RunConfig(run_id="retained", initial_cash=100_000.0, fee_bps=10.0), core="rust"
    )
    engine.run(RetainingStrategy(script=(target_70pct(), None, None, None)), DataFeed(GOLDEN_BARS))

    assert [ctx.position_qty(INSTRUMENT) for ctx in contexts] == [
        Decimal(0),
        Decimal(700),
        Decimal(700),
        Decimal(700),
    ]
    assert contexts[0].cash() == 100_000.0
    assert contexts[1].portfolio_value() == contexts[1].cash() + 700 * 120.0
    assert contexts[0].open_orders() == ()


@RUST_ONLY
def test_python_strategy_sees_feed_snapshot_objects_for_market_callbacks() -> None:
    """market 콜백 이벤트는 feed의 MarketSnapshot 객체 그대로다 (복사·재조립 없음)."""
    seen: list[MarketSnapshot] = []

    class Observer(ScriptedStrategy):
        def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
            assert isinstance(event, MarketSnapshot)
            seen.append(event)
            return super().on_event(ctx, event)

    feed = DataFeed(GOLDEN_BARS)
    engine = BacktestEngine(RunConfig(run_id="feed-objects", initial_cash=100_000.0), core="rust")
    engine.run(Observer(script=(None, None, None, None)), feed)
    assert [event is snapshot for event, snapshot in zip(seen, feed.snapshots(), strict=True)] == [
        True
    ] * len(GOLDEN_BARS)


@RUST_ONLY
def test_rust_feed_rejects_two_instruments_that_share_one_key() -> None:
    """`instrument_key`가 필드를 ':'로 이어 붙이므로 필드 안의 ':'가 두 종목을 한 key로 합친다.

    `("KRX", "A:B")`와 `("KRX:A", "B")`가 둘 다 `KRX:A:B:equity:KRW`를 낸다. 그대로 적재하면
    원장·마크·심볼 표가 두 종목을 한 종목으로 섞으므로 feed 적재에서 거부해야 한다.
    """
    colliding = (
        InstrumentId(venue="KRX", symbol="A:B", asset_class=AssetClass.EQUITY, currency="KRW"),
        InstrumentId(venue="KRX:A", symbol="B", asset_class=AssetClass.EQUITY, currency="KRW"),
    )
    assert instrument_key(colliding[0]) == instrument_key(colliding[1])
    bars = tuple(
        make_bar(day(index), instrument, 100.0, 100.0)
        for index in (1, 2)
        for instrument in colliding
    )

    engine = BacktestEngine(RunConfig(run_id="key-collision", initial_cash=100_000.0), core="rust")
    with pytest.raises(ValueError) as caught:
        engine.run(ScriptedStrategy(script=(None, None)), DataFeed(bars))
    message = str(caught.value)
    assert message.startswith("instrument key collision — key='KRX:A:B:equity:KRW'")
    assert "venue='KRX', symbol='A:B'" in message
    assert "venue='KRX:A', symbol='B'" in message

    # python 코어는 key 개념이 없어 같은 feed를 그대로 완주한다 — 코어 간 거부 통일은 후속 과제.
    python_engine = BacktestEngine(
        RunConfig(run_id="key-collision-python", initial_cash=100_000.0), core="python"
    )
    python_engine.run(ScriptedStrategy(script=(None, None)), DataFeed(bars))


@RUST_ONLY
def test_partial_trace_keeps_decision_when_submit_fails_after_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT-001: Rust가 DECISION을 기록한 뒤 submit이 실패해도 partial trace가 결정을 복원한다."""
    from typing import Any

    from backtest_engine.engine import loop as loop_module
    from backtest_engine.engine.store import RecordKind

    real_factory = loop_module.make_persistent_runtime

    class FailingRuntime:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def submit_decision(self, *args: object) -> object:
            # Rust 쪽 기록(DECISION)까지 끝난 뒤 Python이 decision_id를 받지 못하는 상황을 만든다.
            self.inner.submit_decision(*args)
            raise ValueError("submit exploded after recording")

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    monkeypatch.setattr(
        loop_module,
        "make_persistent_runtime",
        lambda *args, **kwargs: FailingRuntime(real_factory(*args, **kwargs)),
    )
    engine = BacktestEngine(RunConfig(run_id="submit-fails", initial_cash=100_000.0), core="rust")
    with pytest.raises(ValueError, match="submit exploded"):
        engine.run(ScriptedStrategy(script=(target_70pct(),)), DataFeed(GOLDEN_BARS))
    kinds = [record.kind for record in engine.event_store.records]
    assert kinds == [RecordKind.MARKET, RecordKind.SNAPSHOT, RecordKind.DECISION]
    decision = engine.event_store.decisions()[0]
    assert decision.decision_id == "D-000001"
    assert decision.decision.actions == (target_70pct(),)
