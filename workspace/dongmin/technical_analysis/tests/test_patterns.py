"""차트 패턴 검출(PREREG4 2절) — 손으로 만든 피봇·가격열."""

import numpy as np

from technical_analysis import patterns as pt
from technical_analysis import zigzag as zz

H, L = zz.HIGH, zz.LOW


def piv(rows: list[tuple[int, int, float, int]]) -> zz.Pivots:
    a = np.array(rows, dtype=float)
    return zz.Pivots(kind=a[:, 0].astype(np.int64), idx=a[:, 1].astype(np.int64), price=a[:, 2], confirm_idx=a[:, 3].astype(np.int64))


HS = [(H, 0, 100.0, 3), (L, 10, 90.0, 13), (H, 20, 110.0, 23), (L, 30, 91.0, 33), (H, 40, 101.0, 43)]


def kinds(found: list[pt.Pattern], kind: str) -> list[pt.Pattern]:
    return [p for p in found if p.kind == kind]


def test_head_and_shoulders_completes_on_neckline_break() -> None:
    close = np.full(70, 95.0)
    close[46] = 88.0
    got = kinds(pt.pivot_patterns(close, piv(HS)), "H1")
    assert len(got) == 1 and got[0].complete == 46
    assert got[0].level == 90 + (46 - 10) / 20  # 넥라인(10,90)–(30,91)의 46 일 값


def test_head_and_shoulders_ignores_break_before_right_shoulder_confirmed() -> None:
    """확정일(43) 전에만 넥라인 아래였다면 그 패턴은 그날 알 수 없다 — 검출하지 않는다."""
    close = np.full(70, 95.0)
    close[41] = 85.0
    close[42] = 85.0
    assert kinds(pt.pivot_patterns(close, piv(HS)), "H1") == []


def test_head_and_shoulders_rejects_uneven_shoulders_and_new_pivot() -> None:
    close = np.full(70, 95.0)
    close[46] = 88.0
    uneven = [*HS[:4], (H, 40, 110.0 * 0.99, 43)]  # 오른 어깨가 머리와 거의 같음 → 어깨/머리 비율 밖
    assert kinds(pt.pivot_patterns(close, piv(uneven)), "H1") == []
    renewed = [*HS, (L, 44, 92.0, 45)]  # 돌파 전에 다음 피봇이 확정 → 무효
    assert kinds(pt.pivot_patterns(close, piv(renewed)), "H1") == []


def test_double_bottom_needs_gap_and_breaks_middle_peak() -> None:
    close = np.full(60, 55.0)
    close[35] = 61.0
    got = kinds(pt.pivot_patterns(close, piv([(L, 0, 50.0, 2), (H, 12, 60.0, 14), (L, 30, 51.0, 33)])), "H4")
    assert len(got) == 1 and got[0].complete == 35 and got[0].level == 60.0
    near = piv([(L, 0, 50.0, 2), (H, 10, 60.0, 12), (L, 20, 51.0, 23)])  # 간격 20봉 ≤ 22
    assert kinds(pt.pivot_patterns(close, near), "H4") == []


def test_box_breakout_and_triangle_breakout() -> None:
    close = np.full(60, 105.0)
    close[28] = 112.0
    box = kinds(pt.pivot_patterns(close, piv([(H, 0, 110.0, 2), (L, 8, 100.0, 10), (H, 16, 111.0, 18), (L, 24, 101.0, 26)])), "H5")
    assert len(box) == 1 and box[0].complete == 28 and box[0].level == 111.0
    close = np.full(60, 105.0)
    close[40] = 108.5
    tri = piv([(H, 0, 120.0, 3), (L, 8, 100.0, 11), (H, 16, 115.0, 19), (L, 24, 104.0, 27), (H, 32, 110.0, 35)])
    got = kinds(pt.pivot_patterns(close, tri), "H6")
    assert len(got) == 1 and got[0].complete == 40


def test_bull_flag_one_event_per_day() -> None:
    n = 30
    close = np.full(n, 100.0)
    high, low = close + 1, close - 1
    close[12], high[12] = 120.0, 121.0
    close[13:20], high[13:20], low[13:20] = 114.0, 118.0, 112.0
    close[20], high[20] = 119.0, 120.0
    got = kinds(pt.flags(high, low, close), "H7")
    assert [p.complete for p in got] == [20] and got[0].level == 118.0


def test_bull_flag_breaks_when_flag_falls_too_far() -> None:
    n = 30
    close = np.full(n, 100.0)
    high, low = close + 1, close - 1
    close[12], high[12] = 120.0, 121.0
    close[13:20], high[13:20], low[13:20] = 114.0, 118.0, 112.0
    low[15] = 105.0  # 깃대 절반(110) 아래로 → 깃발 아님
    close[20] = 119.0
    assert kinds(pt.flags(high, low, close), "H7") == []


def test_candles_and_gaps() -> None:
    o = np.array([111, 109, 107, 105, 103, 101, 98.5, 100, 101, 110, 104.0])
    c = np.array([110, 108, 106, 104, 102, 99, 102, 100, 103, 110, 100.0])
    h = np.maximum(o, c) + 0.5
    l = np.minimum(o, c) - 0.5
    o[9], l[9], h[9], c[9] = 108.0, 107.5, 111.0, 110.0  # 9: 상승 갭(시가 ≥ 103·1.03, 저가 > 전날 고가 103.5)
    o[10], h[10], l[10], c[10] = 104.0, 105.0, 99.0, 100.0  # 10: 하락 갭(시가 ≤ 110·0.97, 고가 < 전날 저가 107.5)
    atr = np.full(o.shape[0], 2.0)
    got = pt.candles_gaps(o, h, l, c, atr)
    assert [p.complete for p in kinds(got, "C1")] == [6]  # 5: 음봉(101→99), 6: 98.5→102 감싸기, 직전 5일 하락
    assert [p.complete for p in kinds(got, "G1")] == [9]
    assert [p.complete for p in kinds(got, "G2")] == [10]
    o2 = np.array([100.0, 100.0])
    c2 = np.array([100.0, 104.5])  # 몸통 4.5 ≥ 2·ATR(2)
    h2, l2 = np.array([100.5, 104.6]), np.array([99.5, 99.9])
    assert [p.complete for p in kinds(pt.candles_gaps(o2, h2, l2, c2, np.full(2, 2.0)), "C2")] == [1]
