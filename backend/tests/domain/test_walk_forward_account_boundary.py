"""S04 합성 수량·비용 장부로 창 수익률 연결과 연속 계좌의 차이를 입증한다."""
from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.analytics.facade.metrics import EquityCurvePoint
from strategy_workbench.domain.experiment.facade.design import stitch_out_of_sample


@pytest.mark.parametrize(
    ("fee_krw", "expected_restart_growth", "expected_continuous_equity_krw",
     "expected_next_shares"),
    [(0, 1.21, 1210, 11), (10, 144 / 121, 1160, 10)],
)
def test_window_returns_do_not_measure_one_account_with_boundary_trades(
    fee_krw: int,
    expected_restart_growth: float,
    expected_continuous_equity_krw: int,
    expected_next_shares: int,
) -> None:
    # 각 창은 초기자본 1000원, 매수가 100원→종가 110원, 주문당 고정 비용이다.
    # 서로 다른 종목 A→B로 바꾸므로 연속 계좌 경계에는 A 매도와 B 매수가 각각 필요하다.
    initial_cash_krw = 1000
    entry_price_krw, exit_price_krw = 100, 110
    restart_shares = (initial_cash_krw - fee_krw) // entry_price_krw
    restart_cash_krw = initial_cash_krw - restart_shares * entry_price_krw - fee_krw
    first_snapshot_krw = restart_cash_krw + restart_shares * entry_price_krw
    window_end_krw = restart_cash_krw + restart_shares * exit_price_krw
    curve = stitch_out_of_sample([
        (EquityCurvePoint(date(2024, 1, 2), first_snapshot_krw, None),
         EquityCurvePoint(date(2024, 1, 3), window_end_krw, None)),
        (EquityCurvePoint(date(2024, 1, 4), first_snapshot_krw, None),
         EquityCurvePoint(date(2024, 1, 5), window_end_krw, None)),
    ])
    assert curve[-1].equity == pytest.approx(expected_restart_growth)
    assert [point.session for point in curve] == [
        date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5),
    ]

    # 비교 정답은 독립된 손계산 장부이며 제품의 연속 실행기로 사용하지 않는다.
    after_sale_cash_krw = restart_cash_krw + restart_shares * exit_price_krw - fee_krw
    next_shares = (after_sale_cash_krw - fee_krw) // entry_price_krw
    carried_cash_krw = after_sale_cash_krw - next_shares * entry_price_krw - fee_krw
    continuous_equity_krw = carried_cash_krw + next_shares * exit_price_krw
    assert next_shares == expected_next_shares
    assert continuous_equity_krw == expected_continuous_equity_krw
    if fee_krw:
        assert first_snapshot_krw == 990
        assert window_end_krw == 1080
        assert carried_cash_krw == 60
        assert curve[-1].equity != pytest.approx(continuous_equity_krw / initial_cash_krw)
    else:
        assert curve[-1].equity == pytest.approx(continuous_equity_krw / initial_cash_krw)
