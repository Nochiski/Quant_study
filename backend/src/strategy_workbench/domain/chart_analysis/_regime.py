"""판정 구간의 국면 판정(채널 추천)과 주기별 기본·제안 판정 창 상수의 owner(설계 문서 4.2).

판정은 상호배타·완전하다: 판정 불가 → |S| < 1(박스권·방향 미확인) → 1 ≤ |S| < 2(보류) → |S| ≥
2(구조가 같으면 상승·하락, 아니면 보류).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

import numpy as np

from strategy_workbench.domain.chart_analysis._bars import Timeframe
from strategy_workbench.domain.chart_analysis._series import ChartSeries, Pivot, PivotKind, Window

MIN_REGIME_BARS = 20
FLAT_STRENGTH = 1.0
TREND_STRENGTH = 2.0
# 박스 비율 임계 B*. 무작위 보행에서 N=20·52·104의 박스권 비율이 비슷해지는 값(근거 표는 설계 문서
# 4.2, 회귀는 tests/domain/test_chart_analysis_regime_simulation.py).
BOX_RATIO_MAX = 0.8


@dataclass(frozen=True)
class WindowPreset:
    default_bars: int
    suggested_bars: tuple[int, int, int]


WINDOW_PRESETS: Mapping[Timeframe, WindowPreset] = MappingProxyType(
    {
        Timeframe.DAY: WindowPreset(120, (60, 120, 250)),
        Timeframe.WEEK: WindowPreset(52, (26, 52, 104)),
        Timeframe.MONTH: WindowPreset(36, (12, 36, 60)),
    }
)


class Structure(Enum):
    UP = "up"  # HH·HL
    DOWN = "down"  # LH·LL
    MIXED = "mixed"  # 그 밖(같은 값 포함)
    INSUFFICIENT = "insufficient"  # 확정 고점 2·저점 2가 없다


class RegimeVerdict(Enum):
    UP = "up"
    DOWN = "down"
    BOX = "box"
    UNCLEAR = "unclear"
    HOLD = "hold"
    UNDECIDABLE = "undecidable"


class HoldReason(Enum):
    WEAK_STRENGTH = "weak_strength"  # 1 ≤ |S| < 2
    STRUCTURE_MISMATCH = "structure_mismatch"  # |S| ≥ 2 인데 구조가 다르거나 부족


@dataclass(frozen=True)
class RegimeJudgement:
    """국면 판정. `strength`·`box_ratio`는 판정 불가(봉 부족)일 때만 None이다."""

    window: Window
    verdict: RegimeVerdict
    hold_reason: HoldReason | None
    strength: float | None
    box_ratio: float | None
    structure: Structure
    structure_highs: tuple[Pivot, ...]
    structure_lows: tuple[Pivot, ...]
    # 시작·끝 봉이 확정 피벗(후보용) ±R봉 안이면 극값에서 시작·끝나 치우친 구간이다.
    starts_near_extreme: bool
    ends_near_extreme: bool


def _strength(close: np.ndarray) -> float:
    """S = ln(C_끝/C_시작) / (s_r·√m). 평균 로그수익률의 t값과 같은 식이다."""
    returns = np.diff(np.log(close))
    change = float(np.log(close[-1] / close[0]))
    spread = float(np.std(returns, ddof=1))
    if spread == 0:
        return 0.0 if change == 0 else math.copysign(math.inf, change)
    return change / (spread * math.sqrt(len(returns)))


def _structure(highs: tuple[Pivot, ...], lows: tuple[Pivot, ...]) -> Structure:
    if len(highs) < 2 or len(lows) < 2:
        return Structure.INSUFFICIENT
    (h0, h1), (l0, l1) = highs, lows
    if h1.price > h0.price and l1.price > l0.price:
        return Structure.UP
    if h1.price < h0.price and l1.price < l0.price:
        return Structure.DOWN
    return Structure.MIXED


def judge_regime(series: ChartSeries, window: Window) -> RegimeJudgement:
    """판정 구간 `window`의 국면을 봉 `window.end`까지의 정보로 판정한다."""
    major = series.confirmed_in(window, major=True)
    highs = tuple(p for p in major if p.kind is PivotKind.HIGH)[-2:]
    lows = tuple(p for p in major if p.kind is PivotKind.LOW)[-2:]
    structure = _structure(highs, lows)
    r = series.params.confirm_bars
    extremes = [p.index for p in series.pivots if p.confirmed_index <= window.end]
    verdict: RegimeVerdict
    hold: HoldReason | None = None
    strength: float | None = None
    box: float | None = None
    if window.bars < MIN_REGIME_BARS:
        verdict = RegimeVerdict.UNDECIDABLE
    else:
        sl = slice(window.start, window.end + 1)
        strength = _strength(series.close[sl])
        box = float(
            (series.high[sl].max() - series.low[sl].min())
            / (series.atr[window.end] * math.sqrt(window.bars))
        )
        if abs(strength) < FLAT_STRENGTH:
            verdict = RegimeVerdict.BOX if box <= BOX_RATIO_MAX else RegimeVerdict.UNCLEAR
        elif abs(strength) < TREND_STRENGTH:
            verdict, hold = RegimeVerdict.HOLD, HoldReason.WEAK_STRENGTH
        else:
            direction = Structure.UP if strength > 0 else Structure.DOWN
            if structure is direction:
                verdict = RegimeVerdict.UP if strength > 0 else RegimeVerdict.DOWN
            else:
                verdict, hold = RegimeVerdict.HOLD, HoldReason.STRUCTURE_MISMATCH
    return RegimeJudgement(
        window=window,
        verdict=verdict,
        hold_reason=hold,
        strength=strength,
        box_ratio=box,
        structure=structure,
        structure_highs=highs,
        structure_lows=lows,
        starts_near_extreme=any(abs(window.start - i) <= r for i in extremes),
        ends_near_extreme=any(abs(window.end - i) <= r for i in extremes),
    )
