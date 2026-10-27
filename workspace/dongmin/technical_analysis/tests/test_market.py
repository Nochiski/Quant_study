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
