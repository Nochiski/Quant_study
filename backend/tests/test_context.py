"""Context 계약 테스트: 미선언 조회 거절, look-ahead 차단, 읽기 전용."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backtest_engine.engine.context import (
    EngineStrategyContext,
    HistoryStore,
    RustStrategyContext,
)
from backtest_engine.engine.store import PersistentEventStore
from backtest_engine.errors import (
    InsufficientHistoryError,
    UndeclaredDataAccess,
    UniverseNotProvided,
)
from backtest_engine.types.actions import NoAction
from backtest_engine.types.events import OpenOrderSnapshot, OrderEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import PriceField
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import HistoryRequest
from tests.conftest import day, make_bar, make_instrument, make_snapshot

INSTRUMENT = make_instrument()


def build_store(*closes: float) -> HistoryStore:
    store = HistoryStore()
    for offset, close in enumerate(closes):
        ts = day(1 + offset)
        store.append(make_snapshot(ts, make_bar(ts, INSTRUMENT, close, close)))
    return store


def empty_portfolio(ts_day: int, cash: float = 1_000_000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        ts=day(ts_day), cash=cash, positions=(), equity=cash, gross_exposure=0.0
    )


def make_context(
    store: HistoryStore, ts_day: int, declared: frozenset[HistoryRequest]
) -> EngineStrategyContext:
    return EngineStrategyContext(
        now=day(ts_day), snapshot=empty_portfolio(ts_day), history_store=store, declared=declared
    )


def make_rust_context(
    store: HistoryStore, ts_day: int, declared: frozenset[HistoryRequest]
) -> RustStrategyContext:
    """Rust 경로 컨텍스트. 거절 경로는 frame·store를 읽지 않으므로 최소 구성으로 만든다."""
    return RustStrategyContext(
        now=day(ts_day),
        frame=SimpleNamespace(snapshot=(1_000_000.0, [], 1_000_000.0, 0.0), open_orders=[]),
        store=PersistentEventStore(SimpleNamespace()),
        history_store=store,
        declared=declared,
    )


REQUEST = HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=3)


def test_undeclared_history_request_rejected() -> None:
    context = make_context(build_store(100, 101, 102), 3, declared=frozenset())
    with pytest.raises(UndeclaredDataAccess, match="not declared"):
        context.history(REQUEST)


def test_window_excludes_future_sessions() -> None:
    """end=now 이후의 세션은 window에 절대 섞이지 않는다 (look-ahead 차단)."""
    store = build_store(100, 101, 102, 103, 104)  # day1..day5
    context = make_context(store, 3, declared=frozenset({REQUEST}))
    window = context.history(REQUEST)
    assert window.timestamps == (day(1), day(2), day(3))
    assert list(window.column(INSTRUMENT)) == [100.0, 101.0, 102.0]


def test_window_requires_full_lookback() -> None:
    context = make_context(build_store(100, 101), 2, declared=frozenset({REQUEST}))
    with pytest.raises(InsufficientHistoryError, match="lookback=3"):
        context.history(REQUEST)


def test_window_values_read_only() -> None:
    context = make_context(build_store(100, 101, 102), 3, declared=frozenset({REQUEST}))
    window = context.history(REQUEST)
    with pytest.raises(ValueError):
        window.values[0, 0] = 999.0


def test_missing_instrument_sessions_become_nan() -> None:
    other = make_instrument("000660")
    store = HistoryStore()
    store.append(make_snapshot(day(1), make_bar(day(1), INSTRUMENT, 100, 100)))
    store.append(
        make_snapshot(
            day(2),
            make_bar(day(2), INSTRUMENT, 101, 101),
            make_bar(day(2), other, 50, 50),
        )
    )
    request = HistoryRequest(instruments=(INSTRUMENT, other), field=PriceField.CLOSE, lookback=2)
    window = store.window(request, end=day(2))
    assert window.is_complete(INSTRUMENT)
    assert not window.is_complete(other)


def test_portfolio_reads_come_from_snapshot() -> None:
    store = build_store(100, 101, 102)
    context = make_context(store, 3, declared=frozenset())
    assert context.cash() == 1_000_000.0
    assert context.portfolio_value() == 1_000_000.0
    assert context.current_weight(INSTRUMENT) == 0.0
    assert context.position_qty(INSTRUMENT) == 0


def _open_order(order_id: str, instrument: InstrumentId = INSTRUMENT) -> OpenOrderSnapshot:
    order = OrderEvent(
        order_id=order_id,
        decision_id="D-000001",
        ts=day(1),
        instrument=instrument,
        quantity=Decimal(1),
        side=Side.BUY,
        source_action=NoAction(),
    )
    return OpenOrderSnapshot(order=order, remaining=Decimal(1))


def test_open_orders_returns_snapshot_filtered_by_instrument() -> None:
    other = make_instrument("000660")
    orders = (_open_order("O-000001"), _open_order("O-000002", other))
    context = EngineStrategyContext(
        now=day(3),
        snapshot=empty_portfolio(3),
        history_store=build_store(100, 101, 102),
        declared=frozenset(),
        open_orders_snapshot=orders,
    )
    assert context.open_orders() == orders
    assert context.open_orders(other) == (orders[1],)
    assert context.open_orders(make_instrument("999999")) == ()


def test_open_orders_defaults_to_empty() -> None:
    context = make_context(build_store(100), 1, declared=frozenset())
    assert context.open_orders() == ()


def test_both_contexts_reject_undeclared_history_with_the_same_message() -> None:
    """엔진 경로와 Rust 경로의 거절 메시지가 갈라지면 같은 전략이 코어에 따라 다른 진단을
    받는다. 두 구현이 같은 조회 메서드를 쓰는지 여기서 고정한다."""
    engine_context = make_context(build_store(100, 101, 102), 3, declared=frozenset())
    rust_context = make_rust_context(build_store(100, 101, 102), 3, declared=frozenset())

    with pytest.raises(UndeclaredDataAccess) as engine_error:
        engine_context.history(REQUEST)
    with pytest.raises(UndeclaredDataAccess) as rust_error:
        rust_context.history(REQUEST)

    assert str(engine_error.value) == str(rust_error.value)


def test_both_contexts_reject_missing_universe_with_the_same_message() -> None:
    engine_context = make_context(build_store(100), 1, declared=frozenset())
    rust_context = make_rust_context(build_store(100), 1, declared=frozenset())

    with pytest.raises(UniverseNotProvided) as engine_error:
        engine_context.universe()
    with pytest.raises(UniverseNotProvided) as rust_error:
        rust_context.universe()

    assert str(engine_error.value) == str(rust_error.value)


def test_both_contexts_read_the_portfolio_the_same_way() -> None:
    engine_context = make_context(build_store(100), 1, declared=frozenset())
    rust_context = make_rust_context(build_store(100), 1, declared=frozenset())

    assert engine_context.cash() == rust_context.cash()
    assert engine_context.portfolio_value() == rust_context.portfolio_value()
    assert engine_context.current_weight(INSTRUMENT) == rust_context.current_weight(INSTRUMENT)
    assert engine_context.position_qty(INSTRUMENT) == rust_context.position_qty(INSTRUMENT)
    assert engine_context.open_orders() == rust_context.open_orders() == ()
