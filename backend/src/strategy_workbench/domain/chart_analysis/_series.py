"""분석 입력 계열·ATR(Wilder)·확정 피벗의 owner(설계 문서 4.1).

모든 값은 봉 t까지의 정보로만 정한다. 피벗은 원장 전체 계열에서 한 번 계산하고 판정 구간으로 자른다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np
import numpy.typing as npt

from strategy_workbench.domain.chart_analysis._bars import Candle

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class SwingParams:
    """ATR 기간, 피벗 민감도(후보용·구조 판정용 k), 최소 확인 봉 R."""

    atr_period: int = 14
    candidate_sensitivity: float = 1.0
    structure_sensitivity: float = 2.0
    confirm_bars: int = 2

    def __post_init__(self) -> None:
        if self.atr_period < 1 or self.confirm_bars < 1:
            raise ValueError(
                "atr_period and confirm_bars must be >= 1 — "
                f"atr_period={self.atr_period} confirm_bars={self.confirm_bars}"
            )
        for name, value in (
            ("candidate_sensitivity", self.candidate_sensitivity),
            ("structure_sensitivity", self.structure_sensitivity),
        ):
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be finite and positive — {name}={value}")


class PivotKind(Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class Pivot:
    """봉 `index`의 극값 `price`가 봉 `confirmed_index`에서 확정됐다."""

    kind: PivotKind
    index: int
    price: float
    confirmed_index: int


@dataclass(frozen=True)
class Window:
    """완성 봉 index의 닫힌 구간 [start, end]. `end`가 기준일 봉이다."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.end:
            raise ValueError(f"window must satisfy 0 <= start <= end — {self}")

    @property
    def bars(self) -> int:
        return self.end - self.start + 1

    @classmethod
    def ending_at(cls, end: int, bars: int) -> Window:
        """기준일 봉 `end`에서 끝나는 `bars`봉 구간(계열 앞에서 잘리면 짧아진다)."""
        return cls(max(0, end - bars + 1), end)


@dataclass(frozen=True, eq=False)
class ChartSeries:
    """완성 봉(진행 중 봉 제외)의 배열과 ATR·피벗. `build_series`로만 만든다."""

    candles: tuple[Candle, ...]
    high: FloatArray
    low: FloatArray
    close: FloatArray
    atr: FloatArray
    params: SwingParams
    pivots: tuple[Pivot, ...]
    major_pivots: tuple[Pivot, ...]

    def confirmed_in(self, window: Window, *, major: bool = False) -> tuple[Pivot, ...]:
        """극값이 구간 안에 있고 구간 끝까지 확정된 피벗(후보용 또는 구조 판정용)."""
        if window.end >= len(self.candles):
            raise ValueError(f"window end beyond series — window={window} bars={len(self.candles)}")
        pivots = self.major_pivots if major else self.pivots
        return tuple(
            p for p in pivots if p.index >= window.start and p.confirmed_index <= window.end
        )


def _average_true_range(
    high: FloatArray, low: FloatArray, close: FloatArray, period: int
) -> FloatArray:
    """True Range의 Wilder 평활. 처음 `period`봉은 그때까지의 단순 평균이다."""
    true_range = high - low
    true_range[1:] = np.maximum.reduce(
        [true_range[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])]
    )
    atr = np.empty_like(true_range)
    head = min(period, len(true_range))
    atr[:head] = np.cumsum(true_range[:head]) / np.arange(1, head + 1)
    for i in range(head, len(true_range)):
        atr[i] = (atr[i - 1] * (period - 1) + true_range[i]) / period
    return atr


def _find_pivots(
    high: Sequence[float], low: Sequence[float], atr: Sequence[float], sensitivity: float, r: int
) -> tuple[Pivot, ...]:
    """ATR 스윙 피벗. 고점 후보 i는 더 높은 고가가 나오면 바뀌고, `t ≥ i+R` 이고
    `H_i − min(L_(i,t]) ≥ k·ATR_t` 인 첫 봉 t에서 확정된다(저점은 대칭). 확정 뒤에는 반대 방향
    추적을 (i, t]의 극값에서 다시 시작한다. 방향이 정해지기 전에는 고점을 먼저 본다."""
    out: list[Pivot] = []
    direction = 0
    hi_i, hi_p, lo_i, lo_p = 0, high[0], 0, low[0]
    run_lo, run_hi = math.inf, -math.inf
    for t in range(1, len(high)):
        if direction >= 0:
            if high[t] > hi_p:
                hi_i, hi_p, run_lo = t, high[t], math.inf
            else:
                run_lo = min(run_lo, low[t])
        if direction <= 0:
            if low[t] < lo_p:
                lo_i, lo_p, run_hi = t, low[t], -math.inf
            else:
                run_hi = max(run_hi, high[t])
        threshold = sensitivity * atr[t]
        if direction >= 0 and t >= hi_i + r and hi_p - run_lo >= threshold:
            out.append(Pivot(PivotKind.HIGH, hi_i, hi_p, t))
            direction = -1
            lo_i = min(range(hi_i + 1, t + 1), key=lambda j: low[j])
            lo_p = low[lo_i]
            run_hi = max(high[lo_i + 1 : t + 1], default=-math.inf)
        elif direction <= 0 and t >= lo_i + r and run_hi - lo_p >= threshold:
            out.append(Pivot(PivotKind.LOW, lo_i, lo_p, t))
            direction = 1
            hi_i = max(range(lo_i + 1, t + 1), key=lambda j: high[j])
            hi_p = high[hi_i]
            run_lo = min(low[hi_i + 1 : t + 1], default=math.inf)
    return tuple(out)


def build_series(candles: Sequence[Candle], params: SwingParams | None = None) -> ChartSeries:
    """진행 중 봉을 빼고 ATR·후보용 피벗·구조 판정용 피벗을 계산한다.

    Raises:
        ValueError: 완성 봉이 없거나 가격이 양의 유한값이 아니거나 고가·저가가 시가·종가를 감싸지
        않을 때.
    """
    params = params or SwingParams()
    done = tuple(c for c in candles if not c.in_progress)
    if not done:
        raise ValueError(f"no completed candles — candles={len(candles)}")
    ohlc = np.array([(c.open, c.high, c.low, c.close) for c in done], dtype=np.float64)
    o, h, lo, c = ohlc.T
    bad = (
        ~np.isfinite(ohlc).all(axis=1)
        | (lo <= 0)
        | (lo > np.minimum(o, c))
        | (h < np.maximum(o, c))
    )
    if bad.any():
        first = int(np.argmax(bad))
        raise ValueError(
            "candle prices must be positive and bracketed by high/low — "
            f"index={first} start={done[first].start} ohlc={tuple(ohlc[first])} "
            f"bad_rows={int(bad.sum())}"
        )
    high, low, close = h.copy(), lo.copy(), c.copy()
    atr = _average_true_range(high, low, close, params.atr_period)
    high_list, low_list, atr_list = high.tolist(), low.tolist(), atr.tolist()
    return ChartSeries(
        candles=done,
        high=high,
        low=low,
        close=close,
        atr=atr,
        params=params,
        pivots=_find_pivots(
            high_list, low_list, atr_list, params.candidate_sensitivity, params.confirm_bars
        ),
        major_pivots=_find_pivots(
            high_list, low_list, atr_list, params.structure_sensitivity, params.confirm_bars
        ),
    )
