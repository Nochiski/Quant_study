"""피보나치 채널 — 기하, 선 행렬, 자동 후보·점수(사전등록 3.3 절)."""

import numpy as np
import pytest

from technical_analysis import fib_channel as fc
from technical_analysis import zigzag as zz


def test_channel_geometry_uses_bar_index() -> None:
    ch = fc.Channel(xa=0, ya=10.0, xb=10, yb=20.0, xc=5, yc=25.0)
    assert ch.slope == pytest.approx(1.0)
    assert ch.width == pytest.approx(10.0)  # c 위치 기준선 15 → 25 − 15
    assert ch.level(0.5, 20) == pytest.approx(10 + 20 + 5)
    assert ch.direction == fc.UP


def test_down_channel_has_negative_slope_and_width() -> None:
    ch = fc.Channel(xa=0, ya=20.0, xb=10, yb=10.0, xc=5, yc=5.0)
    assert ch.direction == fc.DOWN
    assert ch.width == pytest.approx(-10.0)


def test_grid_lines_are_half_width_apart_and_cover_price_range() -> None:
    ch = fc.Channel(xa=0, ya=10.0, xb=10, yb=10.0, xc=5, yc=14.0)  # 평평, 폭 4 → 간격 2
    x = np.arange(3)
    g = fc.grid_lines(ch, x, lo=9.0, hi=21.0, offset=0.0)
    row = g.values[0]
    assert np.all(np.diff(row) == pytest.approx(2.0))
    assert row.min() <= 9.0 and row.max() >= 21.0
    # 선 번호 → 비율: 10 은 0, 12 는 0.5, 14 는 1
    k_of_10 = int(np.flatnonzero(np.isclose(row, 10.0))[0])
    assert g.ratios[k_of_10] == pytest.approx(0.0)
    assert g.ratios[k_of_10 + 2] == pytest.approx(1.0)


def test_grid_offset_shifts_every_line() -> None:
    ch = fc.Channel(xa=0, ya=10.0, xb=10, yb=10.0, xc=5, yc=14.0)
    g = fc.grid_lines(ch, np.arange(2), lo=9.0, hi=21.0, offset=0.7)
    assert np.any(np.isclose(g.values[0], 10.7))


def test_fib_lines_follow_ratios() -> None:
    ch = fc.Channel(xa=0, ya=10.0, xb=10, yb=10.0, xc=5, yc=14.0)
    m = fc.ratio_lines(ch, np.arange(2), (0.0, 0.618, 1.0), offset=0.0)
    np.testing.assert_allclose(m[0], [10, 10 + 0.618 * 4, 14])


def piv(rows: list[tuple[int, int, float]]) -> zz.Pivots:
    kind, idx, price = zip(*rows, strict=True)
    return zz.Pivots(np.array(kind), np.array(idx), np.array(price, dtype=float), np.array(idx))


def test_up_candidates_need_rising_lows_gap_and_third_point_above() -> None:
    p = piv([(-1, 0, 10.0), (1, 30, 30.0), (-1, 70, 12.0), (-1, 100, 9.0), (1, 120, 15.0)])
    sig = np.ones(5, dtype=bool)
    cands = fc.candidates(p, sig, fc.UP, min_gap=60, recent=8)
    triples = {(c.xa, c.xb, c.xc) for c in cands}
    # 저점 (0,70) 만 오르면서 60봉 이상. 고점 30·120 둘 다 그 선 위
    assert triples == {(0, 70, 30), (0, 70, 120)}


def test_down_candidates_mirror() -> None:
    p = piv([(1, 0, 30.0), (-1, 20, 5.0), (1, 80, 25.0), (-1, 90, 8.0)])
    cands = fc.candidates(p, np.ones(4, dtype=bool), fc.DOWN, min_gap=60, recent=8)
    assert {(c.xa, c.xb, c.xc) for c in cands} == {(0, 80, 20), (0, 80, 90)}


def test_candidates_skip_non_significant_pivots() -> None:
    p = piv([(-1, 0, 10.0), (-1, 70, 12.0), (1, 80, 30.0)])
    sig = np.array([True, False, True])
    assert fc.candidates(p, sig, fc.UP, min_gap=60, recent=8) == []


def bouncing_series(n: int, ch: fc.Channel, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """채널 등간격 선 사이를 오가며 선마다 꺾이는 가짜 가격."""
    rng = np.random.default_rng(seed)
    x = np.arange(n)
    base = ch.level(0.0, x)
    step = 0.5 * ch.width
    # 0~0.5 선 사이를 삼각파로 오가되 꼭짓점의 고가·저가가 선 바로 앞(0.25 ATR 안)까지 닿고 꺾인다
    phase = (x % 20) / 10.0
    tri = np.where(phase <= 1, phase, 2 - phase)
    close = base + step * (0.06 + 0.88 * tri) + rng.normal(0, 0.002 * step, n)
    high = close + 0.05 * step
    low = close - 0.05 * step
    atr = np.full(n, 0.1 * step)
    return close, high, low, atr


def test_score_beats_random_shift_for_series_that_turns_on_the_lines() -> None:
    ch = fc.Channel(xa=0, ya=100.0, xb=100, yb=110.0, xc=50, yc=125.0)
    close, high, low, atr = bouncing_series(400, ch)
    s = fc.score(ch, close, high, low, atr, as_of=399, n_shift=20, seed=1)
    assert s.turns > 0
    assert s.score > 0
    assert s.score == pytest.approx(s.turns - s.expected_turns)


def test_score_ignores_data_after_as_of() -> None:
    ch = fc.Channel(xa=0, ya=100.0, xb=100, yb=110.0, xc=50, yc=125.0)
    close, high, low, atr = bouncing_series(400, ch)
    a = fc.score(ch, close, high, low, atr, as_of=300, n_shift=10, seed=3)
    close2 = close.copy()
    close2[301:] += 50
    b = fc.score(ch, close2, high, low, atr, as_of=300, n_shift=10, seed=3)
    assert (a.turns, a.expected_turns) == (b.turns, b.expected_turns)


def test_score_requires_min_bars_after_third_point() -> None:
    ch = fc.Channel(xa=0, ya=100.0, xb=100, yb=110.0, xc=350, yc=135.0)
    close, high, low, atr = bouncing_series(400, ch)
    assert fc.score(ch, close, high, low, atr, as_of=399, n_shift=5, seed=0) is None


def test_auto_select_ranks_by_score_then_recency() -> None:
    a = fc.Scored(fc.Channel(0, 1.0, 70, 2.0, 30, 5.0), turns=5, expected_turns=2.0, pierces=1, last_turn_day=-1, last_turn_ratio=float("nan"))
    b = fc.Scored(fc.Channel(0, 1.0, 70, 2.0, 40, 5.0), turns=6, expected_turns=3.0, pierces=1, last_turn_day=-1, last_turn_ratio=float("nan"))
    c = fc.Scored(fc.Channel(0, 1.0, 70, 2.0, 50, 5.0), turns=9, expected_turns=2.0, pierces=1, last_turn_day=-1, last_turn_ratio=float("nan"))
    ranked = fc.rank([a, b, c])
    assert [s.channel.xc for s in ranked] == [50, 40, 30]
