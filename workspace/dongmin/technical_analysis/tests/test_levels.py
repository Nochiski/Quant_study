"""매물대(일봉 근사)와 고전 피봇 포인트."""

import numpy as np
import pytest

from technical_analysis import pivot_points as pp
from technical_analysis import volume_profile as vp


def test_volume_spreads_over_overlapped_bins_and_point_bar_goes_to_one_bin() -> None:
    # 범위 10~12 를 4칸(0.5 간격): 첫날 10~12 에 100 → 25씩, 둘째 날 11~11 에 50 → 11 은 칸 [11, 11.5)
    prof = vp.profile(np.array([12.0, 11]), np.array([10.0, 11]), np.array([100.0, 50]), bins=4)
    np.testing.assert_allclose(prof.volume, [25, 25, 75, 25])
    np.testing.assert_allclose(prof.edges, [10, 10.5, 11, 11.5, 12])
    assert prof.poc == pytest.approx(11.25)


def test_bar_partially_overlapping_bins_splits_by_length() -> None:
    # 범위 0~4 를 4칸, 하루 1~2.5 에 30 → 칸1 [1,2) 에 20, 칸2 [2,3) 에 10
    prof = vp.profile(np.array([4.0, 2.5]), np.array([0.0, 1.0]), np.array([0.0, 30.0]), bins=4)
    np.testing.assert_allclose(prof.volume, [0, 20, 10, 0])


def test_poc_tie_takes_lower_bin() -> None:
    prof = vp.profile(np.array([2.0]), np.array([0.0]), np.array([10.0]), bins=2)
    assert prof.poc == pytest.approx(0.5)


def test_share_above_counts_bins_whose_centre_is_above_price() -> None:
    prof = vp.profile(np.array([12.0, 11]), np.array([10.0, 11]), np.array([100.0, 50]), bins=4)
    # 칸 가운데 11.25·11.75 > 11.1 → (75+25)/150
    assert prof.share_above(11.1) == pytest.approx(100 / 150)


def test_rolling_poc_uses_previous_window_only() -> None:
    high = np.array([2.0, 2, 10, 10])
    low = np.array([0.0, 0, 8, 8])
    vol = np.array([1.0, 1, 5, 5])
    poc = vp.rolling_poc(high, low, vol, window=2, bins=4, include_today=False)
    # idx2 의 창 = idx0~1 → 0~2 를 4칸, 고르게 → 동률 → 가장 낮은 칸 가운데 0.25
    assert np.isnan(poc[:2]).all()
    assert poc[2] == pytest.approx(0.25)
    # idx3 의 창 = idx1~2 → 범위 0~10, 칸 2.5 간격, 거래량 5 가 [8,10] 에 → 칸3 가운데 8.75
    assert poc[3] == pytest.approx(8.75)


def test_classic_pivot_points() -> None:
    lv = pp.classic(12.0, 8.0, 10.0)
    assert (lv.p, lv.r1, lv.s1, lv.r2, lv.s2, lv.r3, lv.s3) == pytest.approx((10, 12, 8, 14, 6, 16, 4))


def test_period_pivots_use_previous_calendar_week() -> None:
    dates = np.array(
        ["2026-10-12", "2026-10-13", "2026-10-16", "2026-10-19", "2026-10-20"], dtype="datetime64[D]"
    )
    high = np.array([10.0, 12, 11, 20, 21])
    low = np.array([8.0, 9, 7, 18, 19])
    close = np.array([9.0, 10, 8, 19, 20])
    lv = pp.period_levels(dates, high, low, close, period="W")
    # 첫 주는 직전 주가 없어 NaN, 둘째 주(10-19~) 는 직전 주 고 12·저 7·종 8
    assert np.isnan(lv.p[:3]).all()
    expected_p = (12 + 7 + 8) / 3
    np.testing.assert_allclose(lv.p[3:], [expected_p, expected_p])
    np.testing.assert_allclose(lv.range_[3:], [5, 5])


def test_daily_pivots_use_previous_bar() -> None:
    dates = np.array(["2026-10-12", "2026-10-13"], dtype="datetime64[D]")
    lv = pp.period_levels(dates, np.array([12.0, 1]), np.array([8.0, 1]), np.array([10.0, 1]), period="D")
    assert np.isnan(lv.p[0])
    assert lv.p[1] == pytest.approx(10)
