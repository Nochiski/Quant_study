"""기술적 점수 → 20거래일 뒤 상승·하락 확률(과거 빈도)."""

import numpy as np

from technical_analysis.tool import odds

TABLE = {
    "horizon_days": 20,
    "all": {"n": 100, "up": 0.43, "down": 0.56, "beat_kospi": 0.41, "mean_ret": -0.001},
    "deciles": [
        {"lo": d / 10, "hi": (d + 1) / 10, "n": 10, "up": 0.35 + d / 100, "down": 0.6, "beat_kospi": 0.4, "mean_ret": 0.0}
        for d in range(10)
    ],
}


def test_decile_odds_picks_score_bucket() -> None:
    o = odds.decile_odds(TABLE, 0.84)
    assert o is not None
    assert o.label == "80~90" and o.up == 0.43 and o.base_up == 0.43
    top = odds.decile_odds(TABLE, 1.0)
    assert top is not None and top.label == "90~100"
    assert odds.decile_odds(TABLE, float("nan")) is None


def test_trend_state_four_cases() -> None:
    close = np.array([10.0, 10.0, 10.0, 10.0, 10.0])
    sma200 = np.array([np.nan, 12.0, 12.0, 8.0, 8.0])
    ema20 = np.array([1.0, 1.0, 3.0, 1.0, 3.0])
    ema50 = np.array([2.0, 2.0, 2.0, 2.0, 2.0])
    assert odds.trend_state(close, ema20, ema50, sma200).tolist() == [-1, 0, 1, 2, 3]


def test_state_odds_counts_same_state_and_skips_holdout() -> None:
    dates = np.array(["2015-12-01", "2015-12-02", "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"], dtype="datetime64[D]")
    close = np.array([10.0, 10.0, 10.0, 12.0, 9.0, 11.0])
    state = np.array([3, 3, 3, 3, 3, 3])
    # 지평 2봉: 표본 t = 0..3. t=0·1 은 t+2 가 2020 이지만 창이 홀드아웃(2016–2019)을 덮어 뺀다 → t=2(10→9 하락)·t=3(12→11 하락)
    o = odds.state_odds(dates, close, state, None, horizon=2)
    assert o is not None
    assert o.n == 2 and o.up == 0.0 and o.down == 1.0 and o.beat is None
    bench = np.array([10.0, 10.0, 10.0, 10.0, 8.0, 12.0])
    ob = odds.state_odds(dates, close, state, bench, horizon=2)
    assert ob is not None and ob.beat == 0.5  # t=2: −10% > −20%, t=3: −8.3% < +20%
