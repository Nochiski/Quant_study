"""chart_analysis 테스트의 봉·계열 만들기."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np

from strategy_workbench.domain.chart_analysis.facade.bars import Candle
from strategy_workbench.domain.chart_analysis.facade.series import ChartSeries, Pivot, SwingParams

D0 = date(2020, 1, 1)


def candles(rows: Sequence[tuple[float, float, float, float]]) -> list[Candle]:
    """(시가, 고가, 저가, 종가) 행을 날짜만 하루씩 늘린 봉으로 만든다."""
    return [
        Candle(D0 + timedelta(days=i), D0 + timedelta(days=i), o, h, low, c)
        for i, (o, h, low, c) in enumerate(rows)
    ]


def candles_from_closes(closes: Sequence[float], width: float) -> list[Candle]:
    """고가 = max(직전 종가, 종가), 저가 = 고가 − width.

    직전 종가가 늘 봉 안이라 TR = ATR = width다.
    """
    rows = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        if abs(c - prev) > width:
            raise AssertionError(f"step larger than width at {i}")
        high = max(prev, c)
        rows.append((prev, high, high - width, c))
    return candles(rows)


def series_with(
    close: Sequence[float],
    atr: Sequence[float],
    pivots: Sequence[Pivot] = (),
    *,
    high: Sequence[float] | None = None,
    low: Sequence[float] | None = None,
    major: Sequence[Pivot] = (),
) -> ChartSeries:
    """피벗 검출과 떼어 규칙만 보려고 피벗·종가·ATR을 직접 넣은 계열."""
    days = [D0 + timedelta(days=i) for i in range(len(close))]
    return ChartSeries(
        candles=tuple(Candle(d, d, c, c, c, c) for d, c in zip(days, close, strict=True)),
        high=np.array(high if high is not None else close, dtype=np.float64),
        low=np.array(low if low is not None else close, dtype=np.float64),
        close=np.array(close, dtype=np.float64),
        atr=np.array(atr, dtype=np.float64),
        params=SwingParams(),
        pivots=tuple(sorted(pivots, key=lambda p: p.index)),
        major_pivots=tuple(major),
    )
