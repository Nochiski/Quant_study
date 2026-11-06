"""박스 비율 임계 B*의 근거: 무작위 보행의 박스권 비율이 N과 관계없이 목표 범위에 든다(문서 4.2).

봉 하나는 `STEPS_PER_BAR`단계 가우스 경로(시가 = 직전 종가, 갭 없음, 봉 σ 1%)이고, ATR이 자리 잡도록
앞에 `WARMUP_BARS`봉을 더 둔다. 문서 4.2의 표는 이 파일을 스크립트로 돌린 결과다
(`uv run python tests/domain/test_chart_analysis_regime_simulation.py`, N마다 20,000 경로).
테스트는 같은 모형을 경로 수만 줄여 판정 함수로 다시 확인한다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from strategy_workbench.domain.chart_analysis.facade.bars import Candle
from strategy_workbench.domain.chart_analysis.facade.regime import (
    BOX_RATIO_MAX,
    RegimeJudgement,
    RegimeVerdict,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.series import Window, build_series

STEPS_PER_BAR, WARMUP_BARS, SEED = 256, 60, 20261106
WINDOW_BARS = (20, 52, 104)
D0 = date(2000, 1, 3)


def _random_walk_candles(rng: np.random.Generator, bars: int) -> list[Candle]:
    step = 0.01 / math.sqrt(STEPS_PER_BAR)
    path = np.cumsum(rng.normal(0, step, (bars, STEPS_PER_BAR)), axis=1)
    close_log = np.cumsum(path[:, -1])
    open_log = np.concatenate(([0.0], close_log[:-1]))
    high = 100 * np.exp(open_log + np.maximum(path.max(axis=1), 0))
    low = 100 * np.exp(open_log + np.minimum(path.min(axis=1), 0))
    o, c = 100 * np.exp(open_log), 100 * np.exp(close_log)
    days = [D0 + timedelta(days=i) for i in range(bars)]
    return [
        Candle(d, d, float(oi), float(hi), float(lo), float(ci))
        for d, oi, hi, lo, ci in zip(days, o, high, low, c, strict=True)
    ]


def _judgements(paths: int, window_bars: int, rng: np.random.Generator) -> list[RegimeJudgement]:
    out = []
    for _ in range(paths):
        series = build_series(_random_walk_candles(rng, WARMUP_BARS + window_bars))
        out.append(judge_regime(series, Window.ending_at(len(series.candles) - 1, window_bars)))
    return out


def test_box_share_of_random_walks_stays_in_target_band_for_every_window_length() -> None:
    rng = np.random.default_rng(SEED)
    shares = {}
    for bars in WINDOW_BARS:
        judged = _judgements(500, bars, rng)
        shares[bars] = sum(j.verdict is RegimeVerdict.BOX for j in judged) / len(judged)

    # 20,000 경로 표의 B* 행은 N마다 12~15%. 500 경로의 표본 오차(약 1.5%p)를 더한 띠 안인지 본다
    assert max(shares.values()) - min(shares.values()) <= 0.05, shares
    assert all(0.09 <= share <= 0.18 for share in shares.values()), shares


if __name__ == "__main__":
    grid = [0.6, 0.65, 0.7, 0.71, 0.72, 0.73, 0.74, 0.75, 0.8, 0.9, 1.0]
    rows = {}
    for bars in WINDOW_BARS:
        judged = _judgements(20_000, bars, np.random.default_rng(SEED + bars))
        s = np.array([j.strength for j in judged])
        b = np.array([j.box_ratio for j in judged])
        flat = np.abs(s) < 1
        rows[bars] = [float(np.mean(flat & (b <= g))) for g in grid]
        print(
            f"N={bars} P(|S|<1)={flat.mean():.3f} P(|S|>=2)={np.mean(np.abs(s) >= 2):.3f} "
            f"B|flat q10/25/50={np.quantile(b[flat], [0.1, 0.25, 0.5]).round(3)}"
        )
    print(f"B* now {BOX_RATIO_MAX}")
    for i, g in enumerate(grid):
        print(f"B*={g:.2f} " + " ".join(f"{rows[n][i]:.3f}" for n in WINDOW_BARS))
