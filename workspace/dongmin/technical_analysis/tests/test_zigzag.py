"""지그재그 피봇 — 손으로 따라간 예제, 미래 정보 없음, 유의 피봇·극값 기간."""

import numpy as np

from technical_analysis import zigzag as zz

LOW = np.array([10.0, 11, 12, 13, 14, 12, 10.9, 11, 12, 13, 14.5, 15])
HIGH = LOW + 0.5
ONE_ATR = np.ones(LOW.shape[0])


def as_rows(p: zz.Pivots) -> list[tuple[int, int, float, int]]:
    return [(int(k), int(i), float(v), int(c)) for k, i, v, c in zip(p.kind, p.idx, p.price, p.confirm_idx, strict=True)]


def test_hand_traced_example() -> None:
    # t3: 고가 13.5 ≥ 후보저점 10 + 3 → 저점(0) 확정
    # t6: 저가 10.9 ≤ 후보고점 14.5 − 3 → 고점(4) 확정
    # t10: 고가 15 ≥ 후보저점 10.9 + 3 → 저점(6) 확정
    p = zz.zigzag(HIGH, LOW, ONE_ATR, 3.0)
    assert as_rows(p) == [(-1, 0, 10.0, 3), (1, 4, 14.5, 6), (-1, 6, 10.9, 10)]


def test_reversal_is_checked_before_the_extreme_is_updated() -> None:
    # 한 봉이 새 고점을 만들면서 3ATR 아래까지 빠져도, 비교 대상은 그 전 봉까지의 후보고점이다
    low = np.array([10.0, 11, 14, 10.5])
    high = np.array([10.5, 11.5, 14.5, 18.0])
    p = zz.zigzag(high, low, np.ones(4), 3.0)
    # t2: 고가 14.5 ≥ 10+3 → 저점(0) 확정, 후보고점 14.5(2)
    # t3: 저가 10.5 ≤ 14.5−3 → 고점(2) 확정. 18 은 그 다음에야 후보가 될 수 있다
    assert as_rows(p)[:2] == [(-1, 0, 10.0, 2), (1, 2, 14.5, 3)]


def test_starts_at_first_defined_atr() -> None:
    atr = ONE_ATR.copy()
    atr[:2] = np.nan
    p = zz.zigzag(HIGH, LOW, atr, 3.0)
    assert int(p.idx[0]) >= 2


def test_prefix_run_matches_full_run_up_to_that_day() -> None:
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, 400))
    high = close + rng.uniform(0, 1, 400)
    low = close - rng.uniform(0, 1, 400)
    atr = np.full(400, 1.5)
    full = zz.zigzag(high, low, atr, 3.0)
    for t in (50, 123, 250, 399):
        part = zz.zigzag(high[: t + 1], low[: t + 1], atr[: t + 1], 3.0)
        mask = full.confirm_idx <= t
        assert as_rows(part) == as_rows(zz.Pivots(full.kind[mask], full.idx[mask], full.price[mask], full.confirm_idx[mask]))


def test_significant_low_needs_no_lower_low_on_both_sides() -> None:
    low = np.array([5.0, 4, 3, 2, 3, 4, 5, 1.5, 6])
    high = low + 1
    piv = zz.Pivots(np.array([-1]), np.array([3]), np.array([2.0]), np.array([4]))
    # 앞뒤 3봉: idx0~2 와 4~6 에 2 보다 낮은 저가 없음 → 유의
    assert zz.significant(piv, high, low, window=3, as_of=6).tolist() == [True]
    # 뒤 3봉이 아직 다 지나지 않았으면 쓸 수 없다
    assert zz.significant(piv, high, low, window=3, as_of=5).tolist() == [False]
    # 앞 봉이 모자라면 유의가 아니다
    assert zz.significant(piv, high, low, window=4, as_of=8).tolist() == [False]


def test_significant_low_fails_when_window_holds_a_lower_low() -> None:
    low = np.array([5.0, 5, 4, 3, 2, 3, 4, 5, 1.5, 6])
    high = low + 1
    piv = zz.Pivots(np.array([-1]), np.array([4]), np.array([2.0]), np.array([5]))
    assert zz.significant(piv, high, low, window=3, as_of=9).tolist() == [True]
    # 뒤 4봉(idx5~8)에 1.5 가 있다
    assert zz.significant(piv, high, low, window=4, as_of=9).tolist() == [False]


def test_significant_high_is_symmetric() -> None:
    high = np.array([1.0, 2, 3, 5, 3, 2, 1])
    piv = zz.Pivots(np.array([1]), np.array([3]), np.array([5.0]), np.array([4]))
    assert zz.significant(piv, high, high - 1, window=3, as_of=6).tolist() == [True]


def test_extreme_span_counts_bars_until_a_more_extreme_price() -> None:
    low = np.array([1.0, 4, 3, 2, 3, 4, 5, 1.5, 6])
    high = low + 1
    piv = zz.Pivots(np.array([-1]), np.array([3]), np.array([2.0]), np.array([4]))
    span = zz.extreme_span(piv, high, low, as_of=8)
    # 앞으로: idx2,1 은 더 높고 idx0(1.0) 이 더 낮다 → 2봉. 뒤로: idx4~6 다음 idx7(1.5) → 3봉
    assert span.back.tolist() == [2]
    assert span.forward.tolist() == [3]
    assert span.forward_open.tolist() == [False]


def test_extreme_span_marks_forward_as_open_when_not_yet_broken() -> None:
    low = np.array([3.0, 2, 3, 4])
    piv = zz.Pivots(np.array([-1]), np.array([1]), np.array([2.0]), np.array([3]))
    span = zz.extreme_span(piv, low + 1, low, as_of=3)
    assert span.back.tolist() == [1]
    assert span.back_open.tolist() == [True]
    assert span.forward.tolist() == [2]
    assert span.forward_open.tolist() == [True]
