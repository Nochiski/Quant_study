"""6d: 스냅샷·포트폴리오 조회가 종목 수에 선형으로 늘지 않도록 인덱스를 둔다 — 동작은 불변."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backtest_engine.engine.context import HistoryStore
from backtest_engine.errors import InstrumentNotInSnapshot
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.portfolio import PortfolioSnapshot, Position
from backtest_engine.types.requirements import HistoryRequest

TS = datetime(2024, 1, 2, tzinfo=UTC)


def _inst(symbol: str) -> InstrumentId:
    return InstrumentId(venue="XKRX", symbol=symbol, asset_class=AssetClass.EQUITY, currency="KRW")


def _bar(symbol: str, close: float) -> Bar:
    return Bar(
        ts=TS, instrument=_inst(symbol), open=close, high=close, low=close, close=close, volume=10
    )


def test_snapshot_lookup_uses_value_equality_not_identity() -> None:
    snapshot = MarketSnapshot(ts=TS, bars=tuple(_bar(f"{i:06d}", float(i)) for i in range(1, 50)))
    assert snapshot.bar(_inst("000007")).close == 7.0
    assert snapshot.has(_inst("000049"))
    assert not snapshot.has(_inst("999999"))
    with pytest.raises(InstrumentNotInSnapshot, match="requested=999999"):
        snapshot.bar(_inst("999999"))


def test_snapshot_equality_and_hash_ignore_index() -> None:
    a = MarketSnapshot(ts=TS, bars=(_bar("A", 1.0), _bar("B", 2.0)))
    b = MarketSnapshot(ts=TS, bars=(_bar("A", 1.0), _bar("B", 2.0)))
    a.bar(_inst("A"))  # 인덱스가 만들어져도 값 동등성·해시는 그대로
    assert a == b
    assert hash(a) == hash(b)


def test_portfolio_snapshot_position_lookup() -> None:
    positions = tuple(
        Position(
            instrument=_inst(f"{i:06d}"),
            quantity=Decimal(i),
            average_price=1.0,
            market_price=1.0,
            market_value=float(i),
            unrealized_pnl=0.0,
        )
        for i in range(1, 40)
    )
    snapshot = PortfolioSnapshot(
        ts=TS, cash=0.0, positions=positions, equity=780.0, gross_exposure=1.0
    )
    assert snapshot.position_qty(_inst("000013")) == Decimal(13)
    assert snapshot.position(_inst("000000")) is None
    assert snapshot == PortfolioSnapshot(
        ts=TS, cash=0.0, positions=positions, equity=780.0, gross_exposure=1.0
    )


def test_history_store_keeps_session_alignment_per_instrument() -> None:
    store = HistoryStore()
    store.append(MarketSnapshot(ts=TS, bars=(_bar("A", 1.0),)))
    later = datetime(2024, 1, 3, tzinfo=UTC)
    store.append(
        MarketSnapshot(
            ts=later,
            bars=(
                Bar(ts=later, instrument=_inst("A"), open=2, high=2, low=2, close=2.0, volume=1),
                Bar(ts=later, instrument=_inst("B"), open=5, high=5, low=5, close=5.0, volume=1),
            ),
        )
    )
    window = store.window(
        HistoryRequest(instruments=(_inst("A"), _inst("B")), field=PriceField.CLOSE, lookback=2),
        end=later,
    )
    assert window.values[1].tolist() == [2.0, 5.0]
    assert window.values[0, 0] == 1.0
    assert window.values[0, 1] != window.values[0, 1]  # B는 첫 세션 결측 → NaN


@pytest.mark.parametrize("core_name", ["python", "rust"])
def test_portfolio_snapshot_is_reused_until_state_changes(core_name: str) -> None:
    from backtest_engine.engine.core import core_available, make_portfolio
    from backtest_engine.types.events import FillEvent
    from backtest_engine.types.orders import Side

    if core_name == "rust" and not core_available("rust"):
        pytest.skip("rust core not built")
    portfolio = make_portfolio(core_name, 1_000.0, allow_short=False, allow_margin=False)
    bars = MarketSnapshot(ts=TS, bars=(_bar("A", 10.0),))
    portfolio.mark(bars)
    first = portfolio.snapshot(TS)
    assert portfolio.snapshot(TS) is first  # 상태 변화 없음 → 같은 객체
    later = datetime(2024, 1, 3, tzinfo=UTC)
    assert portfolio.snapshot(later).ts == later  # ts가 다르면 새 스냅샷
    portfolio.apply(
        FillEvent(
            ts=TS,
            fill_id="f1",
            order_id="o1",
            instrument=_inst("A"),
            side=Side.BUY,
            quantity=Decimal(2),
            price=10.0,
            fee=0.0,
            slippage_per_share=0.0,
        )
    )
    changed = portfolio.snapshot(TS)
    assert changed is not first
    assert changed.position_qty(_inst("A")) == Decimal(2)
    assert changed.cash == 980.0
