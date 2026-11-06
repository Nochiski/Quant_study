"""박스 비율 임계 B*의 근거: 무작위 보행에서 봉 수 N이 달라도 박스권 비율이 비슷하다(설계 문서 4.2).

봉 하나는 16단계 가우스 경로(시가 = 직전 종가, 갭 없음)이고, ATR이 자리 잡도록 앞에 60봉을 더 둔다.
문서의 표는 같은 모형의 20,000 경로 결과다. 여기서는 판정 함수 자체로 경로 수를 줄여 다시 확인한다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from strategy_workbench.domain.chart_analysis.facade.bars import Candle
from strategy_workbench.domain.chart_analysis.facade.regime import (
    RegimeVerdict,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.swings import Window, build_series

STEPS_PER_BAR, WARMUP_BARS, PATHS = 16, 60, 1500
D0 = date(2000, 1, 3)


def _random_walk_candles(rng: np.random.Generator, bars: int) -> list[Candle]:
    path = np.cumsum(rng.normal(0, 0.01 / math.sqrt(STEPS_PER_BAR), (bars, STEPS_PER_BAR)), axis=1)
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


def test_box_share_of_random_walks_is_similar_across_window_lengths() -> None:
    rng = np.random.default_rng(20261106)
    shares = {}
    for bars in (20, 52, 104):
        boxes = 0
        for _ in range(PATHS):
            series = build_series(_random_walk_candles(rng, WARMUP_BARS + bars))
            end = len(series.candles) - 1
            boxes += judge_regime(series, Window.ending_at(end, bars)).verdict is RegimeVerdict.BOX
        shares[bars] = boxes / PATHS

    # 20,000 경로 표: N=20·52·104 모두 약 12%. 1,500 경로의 표본 오차(약 0.9%p) 안에서 같은지 본다
    assert max(shares.values()) - min(shares.values()) <= 0.04, shares
    assert all(0.09 <= share <= 0.16 for share in shares.values()), shares
