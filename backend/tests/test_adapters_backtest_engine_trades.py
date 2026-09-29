"""청산 거래(`RawArtifactBundle.trades`) 수량 직렬화 — issue #135 회귀 방지.

`_closed_trades`는 fill 수량을 float으로 계산해 `Decimal(str(700.0)).normalize()` → `7E+2`가 됐고
`str()`이 지수 표기를 냈다. 같은 번들의 fills·positions는 "700"이라 한 응답 안에서 표현이 갈렸다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.result_tables import FillTotals, ResultTables
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _closed_trades

_SAMSUNG = InstrumentId(venue="KRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW")


def _tables(
    *fills: tuple[str, str, int, int, str, int, float, float, float],
    costs: tuple[tuple[int, str, int | None, float], ...] = (),
) -> ResultTables:
    sessions = tuple(datetime(2026, 1, day, tzinfo=UTC) for day in (5, 6, 7))
    return ResultTables(
        sessions=sessions,
        instruments=(_SAMSUNG,),
        snapshots=(),
        positions=(),
        orders=(),
        fills=fills,
        costs=costs,
        fill_totals=FillTotals(traded_notional=0.0, total_fees=0.0, total_slippage_cost=0.0),
    )


def _quantities(tables: ResultTables) -> list[str]:
    session_dates = tuple(ts.date() for ts in tables.sessions)
    security_ids = tuple(instrument.symbol for instrument in tables.instruments)
    return [trade.quantity for trade in _closed_trades(tables, session_dates, security_ids)]


def test_closed_trade_quantity_is_a_plain_integer_string_like_fills() -> None:
    # 700주 매수 → 전량 청산: 예전에는 "7E+2".
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 700, 100.0, 0.0, 0.0),
        ("f2", "o2", 1, 0, "sell", 700, 110.0, 0.0, 0.0),
    )
    assert _quantities(tables) == ["700"]


def test_closed_trade_quantity_keeps_partial_exits_and_round_numbers_plain() -> None:
    # 1000주 중 100주 → 900주 순서로 청산: 둘 다 정수 문자열이고 10의 거듭제곱도 지수 표기가 아니다.
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 1000, 100.0, 0.0, 0.0),
        ("f2", "o2", 1, 0, "sell", 100, 110.0, 0.0, 0.0),
        ("f3", "o3", 2, 0, "sell", 900, 120.0, 0.0, 0.0),
    )
    assert _quantities(tables) == ["100", "900"]
    for quantity in _quantities(tables):
        assert quantity.isdigit(), quantity


def test_closed_trade_costs_include_the_sell_tax_recorded_after_each_sell() -> None:
    """매도 거래세(V2-01)는 체결의 `fee` 가 아니라 비용 기록이다. 거래 단위 손익은 그 세금까지 뺀다.

    100주 @100 매수 → 같은 세션에 40주 @110, 60주 @120 매도. 세금은 체결 순서대로 8.8, 14.4.
    """
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 100, 100.0, 0.0, 0.0),
        ("f2", "o2", 1, 0, "sell", 40, 110.0, 1.0, 0.0),
        ("f3", "o3", 1, 0, "sell", 60, 120.0, 1.0, 0.0),
        costs=((1, "sell_tax", 0, 8.8), (1, "sell_tax", 0, 14.4), (2, "short_borrow", 0, 5.0)),
    )
    session_dates = tuple(ts.date() for ts in tables.sessions)
    trades = _closed_trades(tables, session_dates, ("005930",))

    assert [(trade.fees, trade.pnl) for trade in trades] == [
        (pytest.approx(9.8), pytest.approx(400.0 - 9.8)),
        (pytest.approx(15.4), pytest.approx(1200.0 - 15.4)),
    ]
