"""피해야 할 상태(연구 6차 A06·C03·B06) — 손으로 만든 봉열."""

import numpy as np

from technical_analysis import avoid


def test_breakout_below_80pct_of_high_is_flagged() -> None:
    close = np.concatenate([np.full(20, 60.0), [100.0], np.full(230, 60.0), [65.0]])  # 250봉 안 고점 100, 마지막 날 20일 신고가 65 < 80
    f = avoid.flags(close, close, np.full(close.shape, 1000.0))
    assert f["A06"][-1] and not f["A06"][:-1].any()
    near = np.concatenate([np.full(250, 60.0), [65.0]])  # 250봉 고점이 65 자신 → 80% 아래 아님
    assert not avoid.flags(near, near, np.full(near.shape, 1000.0))["A06"][-1]


def test_volume_crash_needs_three_times_volume_and_minus_five_pct() -> None:
    close = np.full(30, 100.0)
    vol = np.full(30, 1000.0)
    close[25], vol[25] = 94.0, 3500.0  # −6%, 거래량 3.5배
    close[28], vol[28] = 96.0, 5000.0  # 전날 100 대비 −4% 는 아님
    f = avoid.flags(close, close, vol)
    assert np.flatnonzero(f["C03"]).tolist() == [25]


def test_gap_down_uses_open_vs_previous_close() -> None:
    close = np.array([100.0, 100.0, 100.0])
    open_ = np.array([100.0, 96.0, 98.0])  # 1: −4% 갭, 2: −2%
    assert np.flatnonzero(avoid.flags(open_, close, np.ones(3))["B06"]).tolist() == [1]
