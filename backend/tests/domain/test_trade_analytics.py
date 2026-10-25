"""원장 순서·부분 청산·창 귀속의 작은 독립 계산 회귀."""

from datetime import date

import pytest

from strategy_workbench.domain.analytics.facade.metrics import AnalysisPoint, TradeOutcome
from strategy_workbench.domain.backtest.facade.runs import (
    RawArtifactBundle,
    RawCorporateAction,
    RawCost,
    RawFill,
)
from strategy_workbench.domain.backtest.facade.trade_analytics import (
    analytics_input,
    closed_trades,
    slice_analytics,
)


def _fill(
    fill_id: str, day: int, side: str, quantity: str, price: float, fee: float = 0
) -> RawFill:
    return RawFill(fill_id, "order", date(2024, 1, day), "A", side, quantity, price, fee, 0)


def _split(day: int, ratio: str, old: str, new: str, cash_paid: float = 0.0) -> RawCorporateAction:
    return RawCorporateAction(date(2024, 1, day), "A", ratio, old, new, cash_paid)


def test_split_before_a_sell_rescales_the_open_trade_instead_of_booking_a_fake_loss() -> None:
    # 100주를 100원에 사고, 2:1 분할로 200주(평균 50원)가 된 뒤 200주를 50원에 판다.
    # 경제적 손익은 0이다.
    fills = (_fill("1", 1, "buy", "100", 100), _fill("2", 3, "sell", "200", 50))

    trades = closed_trades(fills, (), actions=(_split(2, "2", "100", "200"),))

    assert [(t.side, t.quantity, t.entry_price, t.exit_price, t.pnl) for t in trades] == [
        ("long", "200", 50, 50, 0)
    ]


def test_reverse_split_cash_in_lieu_is_a_closed_fraction_and_pnl_matches_cash_flow() -> None:
    # 3주(10원)를 1:2 병합 → 1.5주 중 0.5주는 정산가 24원으로 현금 12원, 남은 1주를 24원에 판다.
    fills = (_fill("1", 1, "buy", "3", 10), _fill("2", 3, "sell", "1", 24))

    trades = closed_trades(fills, (), actions=(_split(2, "0.5", "3", "1", cash_paid=12.0),))

    assert [(t.quantity, t.entry_price, t.exit_price, t.closed_on.day) for t in trades] == [
        ("0.5", 20, 24, 2),
        ("1", 20, 24, 3),
    ]
    # 비용이 없으면 실현 손익 합 = 받은 현금(12 + 24) − 낸 현금(30).
    assert sum(t.pnl for t in trades) == pytest.approx(6)


@pytest.mark.parametrize(
    ("fills", "action"),
    [
        # 재구성한 보유(10주)와 엔진이 적용한 수량(7주)이 다르다.
        ((_fill("1", 1, "buy", "10", 100),), _split(2, "2", "7", "14")),
        # 다 판 뒤에는 엔진이 적용 기록을 남기지 않는다. 기록이 있으면 원장이 어긋난 것이다.
        (
            (_fill("1", 1, "buy", "10", 100), _fill("2", 2, "sell", "10", 100)),
            _split(3, "2", "10", "20"),
        ),
    ],
)
def test_action_that_disagrees_with_the_reconstructed_holding_is_rejected(
    fills: tuple[RawFill, ...], action: RawCorporateAction
) -> None:
    with pytest.raises(ValueError, match="old_quantity"):
        closed_trades(fills, (), actions=(action,))


def test_fill_id_width_does_not_reorder_same_session_execution() -> None:
    fills = (
        _fill("F-999999", 2, "buy", "10", 100),
        _fill("F-1000000", 2, "sell", "10", 120),
    )
    (trade,) = closed_trades(fills, (), actions=())
    assert (trade.side, trade.entry_price, trade.exit_price, trade.pnl) == ("long", 100, 120, 200)


def test_partial_exit_and_reversal_allocate_each_fill_fee_once() -> None:
    fills = (
        _fill("1", 1, "buy", "10", 100, 10),
        _fill("2", 2, "sell", "4", 110, 4),
        _fill("3", 3, "sell", "9", 120, 9),
        _fill("4", 4, "buy", "3", 100, 3),
    )
    trades = closed_trades(fills, (), actions=())
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
    trades = closed_trades(fills, costs, actions=())
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


def test_actual_fill_tax_is_authoritative_even_if_cost_rows_are_reordered() -> None:
    from dataclasses import replace

    fills = (
        replace(_fill("1", 1, "buy", "2", 100), sell_tax=0.0),
        replace(_fill("2", 2, "sell", "1", 110), sell_tax=2.0),
        replace(_fill("3", 2, "sell", "1", 120), sell_tax=0.0),
    )
    costs = (RawCost(date(2024, 1, 2), "sell_tax", "A", 999),)
    trades = closed_trades(fills, costs, actions=())
    assert [(t.fees, t.pnl) for t in trades] == [(2.0, 8.0), (0.0, 20.0)]


def test_legacy_fill_without_tax_field_retains_fifo_cost_compatibility() -> None:
    fills = (_fill("1", 1, "buy", "2", 100), _fill("2", 2, "sell", "2", 110))
    (trade,) = closed_trades(fills, (RawCost(date(2024, 1, 2), "sell_tax", "A", 2.0),), actions=())
    assert (trade.fees, trade.pnl) == (2.0, 18.0)


@pytest.mark.parametrize("taxes", [(2.0, None), (None, 3.0), (2.0, 3.0)])
def test_mixed_actual_and_legacy_tax_consumes_each_cost_row_once(
    taxes: tuple[float | None, float | None],
) -> None:
    from dataclasses import replace

    fills = (
        replace(_fill("1", 1, "buy", "2", 100), sell_tax=0.0),
        replace(_fill("2", 2, "sell", "1", 110), sell_tax=taxes[0]),
        replace(_fill("3", 2, "sell", "1", 120), sell_tax=taxes[1]),
    )
    costs = tuple(RawCost(date(2024, 1, 2), "sell_tax", "A", tax) for tax in (2.0, 3.0))
    assert [(t.fees, t.pnl) for t in closed_trades(fills, costs, actions=())] == [
        (2.0, 8.0),
        (3.0, 17.0),
    ]


def test_explicit_zero_tax_does_not_consume_the_next_legacy_sell_cost() -> None:
    from dataclasses import replace

    fills = (
        _fill("1", 1, "buy", "2", 100),
        replace(_fill("2", 2, "sell", "1", 110), sell_tax=0.0),
        _fill("3", 2, "sell", "1", 120),
    )
    costs = (RawCost(date(2024, 1, 2), "sell_tax", "A", 3.0),)
    assert [(t.fees, t.pnl) for t in closed_trades(fills, costs, actions=())] == [
        (0.0, 10.0),
        (3.0, 17.0),
    ]
