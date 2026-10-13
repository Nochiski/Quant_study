"""원장 순서·부분 청산·창 귀속의 작은 독립 계산 회귀."""

from datetime import date

import pytest

from strategy_workbench.domain.analytics.facade.metrics import AnalysisPoint, TradeOutcome
from strategy_workbench.domain.backtest.facade.runs import RawArtifactBundle, RawCost, RawFill
from strategy_workbench.domain.backtest.facade.trade_analytics import (
    analytics_input,
    closed_trades,
    slice_analytics,
)


def _fill(
    fill_id: str, day: int, side: str, quantity: str, price: float, fee: float = 0
) -> RawFill:
    return RawFill(fill_id, "order", date(2024, 1, day), "A", side, quantity, price, fee, 0)


def test_fill_id_width_does_not_reorder_same_session_execution() -> None:
    fills = (
        _fill("F-999999", 2, "buy", "10", 100),
        _fill("F-1000000", 2, "sell", "10", 120),
    )
    (trade,) = closed_trades(fills, ())
    assert (trade.side, trade.entry_price, trade.exit_price, trade.pnl) == ("long", 100, 120, 200)


def test_partial_exit_and_reversal_allocate_each_fill_fee_once() -> None:
    fills = (
        _fill("1", 1, "buy", "10", 100, 10),
        _fill("2", 2, "sell", "4", 110, 4),
        _fill("3", 3, "sell", "9", 120, 9),
        _fill("4", 4, "buy", "3", 100, 3),
    )
    trades = closed_trades(fills, ())
    assert [(item.side, item.quantity, item.fees, item.pnl) for item in trades] == [
        ("long", "4", 8, 32),
        ("long", "6", 12, 108),
        ("short", "3", 6, 54),
    ]
    assert sum(item.fees for item in trades) == sum(item.fee for item in fills)


def test_full_and_window_share_totals_and_use_previous_session_as_base() -> None:
    points = tuple(AnalysisPoint(date(2024, 1, day), 100 + day, 0, 0) for day in (1, 2, 3))
    fills = (_fill("1", 2, "buy", "2", 10, 1), _fill("2", 3, "sell", "2", 12, 1))
    costs = (RawCost(date(2024, 1, 3), "sell_tax", "A", 0.5),)
    trades = closed_trades(fills, costs)
    outcomes = tuple(
        TradeOutcome(t.security_id, t.closed_on, t.pnl, t.fees, t.slippage_cost) for t in trades
    )
    bundle = RawArtifactBundle((), (), (), fills, costs, trades)
    data = analytics_input(points, fills, costs, outcomes)
    assert slice_analytics(data, bundle, points[0].session, points[-1].session) == data
    window = slice_analytics(data, bundle, points[2].session, points[2].session)
    assert window.base == points[1]
    assert window.traded_notional == 24
    assert window.total_fees == 1
    assert window.total_taxes == pytest.approx(0.5)
    assert window.trades == outcomes
