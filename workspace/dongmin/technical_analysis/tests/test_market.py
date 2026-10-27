"""시장 단면 지표 — 눌림목 점수(52주 고점 근접 + 20일 눌림), 투매일 비율, 전진 관찰 기록."""

from pathlib import Path

import numpy as np
import pytest

from technical_analysis import market as mk


def test_pct_rank_uses_average_rank_within_mask() -> None:
    x = np.array([3.0, 1.0, 2.0, 2.0, np.nan, 9.0])
    mask = np.array([True, True, True, True, True, False])
    np.testing.assert_allclose(mk.pct_rank(x, mask)[:4], [1.0, 0.0, 0.5, 0.5])
    assert np.isnan(mk.pct_rank(x, mask)[4]) and np.isnan(mk.pct_rank(x, mask)[5])


def test_pullback_score_prefers_near_high_and_recent_dip() -> None:
    # 종목 0: 고점 근접 최고 + 20일 수익 최저(눌림) → 상위, 종목 3: 반대 → 하위
    p52 = np.array([0.99, 0.90, 0.80, 0.70])
    r20 = np.array([-0.10, 0.00, 0.05, 0.20])
    s = mk.pullback_score(p52, r20, np.ones(4, dtype=bool))
    assert s[0] == pytest.approx(1.0) and s[3] == pytest.approx(0.0)
    assert list(np.argsort(-s)) == [0, 1, 2, 3]


def test_outside_stock_is_placed_against_universe_distribution() -> None:
    p52 = np.array([0.99, 0.90, 0.80, 0.70])
    r20 = np.array([-0.10, 0.00, 0.05, 0.20])
    uni = np.ones(4, dtype=bool)
    # 고점 근접은 0.95(2등과 1등 사이), 20일은 −0.20(가장 많이 눌림) → 점수 상위권
    pos = mk.place(0.95, -0.20, p52, r20, uni)
    assert pos > 0.7


def test_panic_fraction_counts_rsi_below_30_in_universe() -> None:
    rsi = np.array([25.0, 29.9, 30.0, 50.0, np.nan, 10.0])
    mask = np.array([True, True, True, True, True, False])
    assert mk.panic_fraction(rsi, mask) == pytest.approx(2 / 4)


def test_forward_log_appends_once_per_day(tmp_path: Path) -> None:
    log = tmp_path / "p2.csv"
    mk.log_panic(log, "2026-10-27", 0.31, ["005930", "000660"])
    mk.log_panic(log, "2026-10-27", 0.31, ["005930", "000660"])
    mk.log_panic(log, "2026-10-28", 0.26, ["035720"])
    rows = log.read_text().strip().splitlines()
    assert rows[0].startswith("date,")
    assert [r.split(",")[0] for r in rows[1:]] == ["2026-10-27", "2026-10-28"]


@pytest.mark.parametrize(
    ("score", "label"),
    [(0.95, "눌림목 상위 20%"), (0.80, "눌림목 상위 20%"), (0.5, "중간"), (0.30, "하위 30% — 피할 쪽"), (float("nan"), "계산 불가")],
)
def test_pullback_label(score: float, label: str) -> None:
    assert mk.pullback_label(score) == label


def test_price_composite_averages_percentiles_in_good_direction() -> None:
    # 종목 0: 고점 근접·저변동·최근 눌림·낮은 MAX·높은 MAD → 모든 성분 1등 → 1.0
    p52 = np.array([0.99, 0.9, 0.8])
    r20 = np.array([-0.1, 0.0, 0.1])
    vol60 = np.array([0.01, 0.02, 0.03])
    max20 = np.array([0.02, 0.05, 0.10])
    mad = np.array([1.2, 1.0, 0.8])
    s = mk.price_composite(p52, r20, vol60, max20, mad, np.ones(3, dtype=bool))
    np.testing.assert_allclose(s, [1.0, 0.5, 0.0])


def test_price_composite_needs_every_component() -> None:
    nan = float("nan")
    s = mk.price_composite(
        np.array([0.9, 0.8, nan]), np.array([0.0, 0.1, 0.0]), np.array([0.01, 0.02, 0.01]),
        np.array([0.02, 0.05, 0.02]), np.array([1.0, 0.9, 1.0]), np.ones(3, dtype=bool),
    )
    assert np.isnan(s[2]) and not np.isnan(s[0])


@pytest.mark.parametrize(("score", "label"), [(0.85, "상위 20%"), (0.5, "중간"), (0.15, "하위 20%"), (float("nan"), "계산 불가")])
def test_quintile_label(score: float, label: str) -> None:
    assert mk.quintile_label(score) == label


def test_log_observe_writes_once_per_date(tmp_path) -> None:
    path = tmp_path / "forward_r6.csv"
    rows = [("REGIME", ["bear", "breadth=0.223"]), ("A06", ["000001", "000002"]), ("F01", [])]
    assert mk.log_observe(path, "2026-10-23", rows)
    assert not mk.log_observe(path, "2026-10-23", rows)
    text = path.read_text().splitlines()
    assert text[0] == "date,signal,n,tickers" and len(text) == 4
    assert text[2] == "2026-10-23,A06,2,000001 000002"


def test_regime_score_switches_signal_by_regime() -> None:
    near = np.array([0.95, 0.60, 0.80])
    r20 = np.array([0.10, -0.20, 0.00])
    mask = np.array([True, True, True])
    assert mk.regime_score(True, near, r20, mask).tolist() == [1.0, 0.0, 0.5]   # 상승장: 고점 근접 높을수록
    assert mk.regime_score(False, near, r20, mask).tolist() == [0.0, 1.0, 0.5]  # 하락장: 많이 빠질수록


def test_trailing_pct_last_counts_ties_half_and_needs_min_points() -> None:
    x = np.array([1.0, 2.0, 3.0, 2.0])
    assert mk.trailing_pct_last(x, win=750, minp=3) == (1 + 0.5 * 2) / 4  # 작은 값 1개 + 같은 값 2개(자신 포함) 절반
    assert np.isnan(mk.trailing_pct_last(x, win=750, minp=5))
    assert mk.trailing_pct_last(np.array([9.0, 1.0, 2.0]), win=2, minp=2) == 0.75  # 창 밖(9)은 안 셈
