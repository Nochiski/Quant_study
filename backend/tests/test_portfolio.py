"""Portfolio 원장 단위 테스트: 평균단가, 수수료, 방어 불변조건."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.errors import NegativeCashError, NegativePositionError
from backtest_engine.types.events import FillEvent
from backtest_engine.types.orders import Side
from tests.conftest import day, make_bar, make_instrument, make_snapshot

INSTRUMENT = make_instrument()


def fill(
    side: Side, quantity: int, price: float, fee: float = 0.0, fill_seq: int = 1
) -> FillEvent:
    return FillEvent(
        fill_id=f"F-{fill_seq:06d}",
        order_id="O-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        price=price,
        fee=fee,
        slippage_per_share=0.0,
    )


def test_buy_updates_average_price_and_cash() -> None:
    portfolio = Portfolio(initial_cash=1_000_000.0)
    portfolio.apply(fill(Side.BUY, 10, 100.0, fee=10.0))
    portfolio.apply(fill(Side.BUY, 10, 200.0, fee=20.0, fill_seq=2))

    # 평균단가에는 수수료를 섞지 않는다: (10*100 + 10*200) / 20 = 150
    assert portfolio.held_qty(INSTRUMENT) == 20
    snapshot = portfolio.snapshot(day(1))
    position = snapshot.positions[0]
    assert position.average_price == pytest.approx(150.0)
    # 현금은 수수료까지 차감: 1,000,000 - 1,000 - 10 - 2,000 - 20
    assert portfolio.cash == pytest.approx(996_970.0)


def test_sell_keeps_average_price_and_credits_cash() -> None:
    portfolio = Portfolio(initial_cash=100_000.0)
    portfolio.apply(fill(Side.BUY, 10, 100.0))
    portfolio.apply(fill(Side.SELL, 4, 120.0, fee=5.0, fill_seq=2))

    assert portfolio.held_qty(INSTRUMENT) == 6
    snapshot = portfolio.snapshot(day(1))
    assert snapshot.positions[0].average_price == pytest.approx(100.0)
    # 100,000 - 1,000 + 480 - 5
    assert portfolio.cash == pytest.approx(99_475.0)


def test_full_exit_removes_position() -> None:
    portfolio = Portfolio(initial_cash=100_000.0)
    portfolio.apply(fill(Side.BUY, 10, 100.0))
    portfolio.apply(fill(Side.SELL, 10, 100.0, fill_seq=2))
    assert portfolio.held_qty(INSTRUMENT) == 0
    assert portfolio.snapshot(day(1)).positions == ()


def test_oversell_rejected() -> None:
    portfolio = Portfolio(initial_cash=100_000.0)
    portfolio.apply(fill(Side.BUY, 10, 100.0))
    with pytest.raises(NegativePositionError, match="sell=11"):
        portfolio.apply(fill(Side.SELL, 11, 100.0, fill_seq=2))


def test_overspend_rejected() -> None:
    portfolio = Portfolio(initial_cash=500.0)
    with pytest.raises(NegativeCashError, match="cash=500.0"):
        portfolio.apply(fill(Side.BUY, 10, 100.0))


def test_snapshot_equity_identity() -> None:
    """equity = cash + Σ market_value 불변조건."""
    portfolio = Portfolio(initial_cash=100_000.0)
    portfolio.apply(fill(Side.BUY, 10, 100.0, fee=100.0))
    portfolio.mark(make_snapshot(day(2), make_bar(day(2), INSTRUMENT, 110.0, 120.0)))
    snapshot = portfolio.snapshot(day(2))

    assert snapshot.cash == pytest.approx(98_900.0)
    assert snapshot.positions[0].market_value == pytest.approx(1_200.0)
    assert snapshot.equity == pytest.approx(snapshot.cash + snapshot.positions[0].market_value)
    assert snapshot.positions[0].unrealized_pnl == pytest.approx(200.0)
    assert snapshot.weight(INSTRUMENT) == pytest.approx(1_200.0 / snapshot.equity)
