"""분석 입력 계열·판정 창·ATR(Wilder)·확정 피벗의 owner(설계 문서 4.1·4.7).

모든 값은 봉 t까지의 정보로만 정한다. 피벗은 원장 전체 계열에서 한 번 계산하고 판정 구간으로 자른다.
index는 한 주기(일·주·월) 계열의 완성 봉 index이고, 저장 날짜는 그 봉의 `Candle.start`다(좌표 계약).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

import numpy as np
import numpy.typing as npt

from strategy_workbench.domain.chart_analysis._bars import Candle
from strategy_workbench.domain.chart_analysis._geometry import Anchor

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

    @property
    def anchor(self) -> Anchor:
        return Anchor(self.index, self.price)


@dataclass(frozen=True)
class Window:
    """완성 봉 index의 닫힌 구간 [start, end]. `end`는 기준 봉(보통 계열의 마지막 완성 봉)이다."""

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
        """기준 봉 `end`에서 끝나는 `bars`봉 구간(계열 앞에서 잘리면 짧아진다)."""
        return cls(max(0, end - bars + 1), end)


@dataclass(frozen=True, eq=False)
class ChartSeries:
    """한 주기의 완성 봉(진행 중 봉 제외) 배열과 ATR·피벗. 배열은 읽기 전용이다."""

    candles: tuple[Candle, ...]
    high: FloatArray
    low: FloatArray
    close: FloatArray
    atr: FloatArray
    params: SwingParams
    pivots: tuple[Pivot, ...]
    major_pivots: tuple[Pivot, ...]

    def __post_init__(self) -> None:
        for values in (self.high, self.low, self.close, self.atr):
            values.flags.writeable = False

    def bar_date(self, index: int) -> date:
        """완성 봉 `index`의 저장 날짜(`Candle.start`)."""
        if not 0 <= index < len(self.candles):
            raise ValueError(f"bar index out of range — index={index} bars={len(self.candles)}")
        return self.candles[index].start

    def index_of(self, day: date) -> int | None:
        """`day`가 든 완성 봉 index. 그 날짜가 속한 기간(`Candle.period_start`부터)의 봉이다 — 봉
        사이 휴장일·주 중간 날짜도, 첫날이 휴장인 주·달(월요일 휴장 주, 1/1·5/1)의 그 날짜도 그
        기간의 봉이다.

        첫 봉 시작 전이거나 마지막 완성 봉의 끝 거래일 뒤(진행 중 봉 기간·미래 포함)면 None이다.
        계열은 주기를 모르므로 마지막 완성 봉만은 끝 거래일까지다 — 정지로 수요일에 끝난 마지막
        주봉의 목·금은 None이다. 저장 날짜는 늘 `Candle.start`라 저장된 기준점 해석에는 영향이
        없다.
        """
        i = bisect_right(self.candles, day, key=lambda c: c.period_start) - 1
        if i < 0 or (i == len(self.candles) - 1 and day > self.candles[i].end):
            return None
        return i

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
    추적을 (i, t]의 극값에서 다시 시작한다. 방향이 정해지기 전에는 고점을 먼저 본다.

    한 봉에서는 피벗이 많아야 하나 확정된다. ATR_t = 0인 봉(가격이 한 번도 움직이지 않은 계열)에서는
    확정하지 않는다."""
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
        if threshold <= 0:
            continue
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
