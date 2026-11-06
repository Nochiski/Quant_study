"""판정 구간의 국면 판정(채널 추천)과 주기별 기본·제안 판정 창 상수의 owner(설계 문서 4.2).

판정은 상호배타·완전하다: 판정 불가(봉 부족·퇴화) → |S| < `FLAT_STRENGTH`(박스권·방향 미확인) →
|S| < `TREND_STRENGTH`(보류) → 그 이상(구조가 같으면 상승·하락, 아니면 보류). 판정·사유 코드에서
사용자 대면 문장을 만드는 owner도 이 노드다(PR2, 설계 문서 4.2).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

import numpy as np

from strategy_workbench.domain.chart_analysis._bars import Timeframe
from strategy_workbench.domain.chart_analysis._channel import TrendDirection
from strategy_workbench.domain.chart_analysis._series import ChartSeries, Pivot, PivotKind, Window

MIN_REGIME_BARS = 20
FLAT_STRENGTH = 1.0
TREND_STRENGTH = 2.0
# 박스 비율 임계 B*. 근거 표는 설계 문서 4.2, 표를 만든 시뮬레이션과 회귀는
# tests/domain/test_chart_analysis_regime_simulation.py다.
BOX_RATIO_MAX = 0.73


@dataclass(frozen=True)
class WindowPreset:
    """주기별 자동 제안 칩(짧은·기본·긴 순). 기본("오늘") 판정 창은 가운데 칩이다."""

    suggested_bars: tuple[int, int, int]

    @property
    def default_bars(self) -> int:
        return self.suggested_bars[1]


WINDOW_PRESETS: Mapping[Timeframe, WindowPreset] = MappingProxyType(
    {
        Timeframe.DAY: WindowPreset((60, 120, 250)),
        Timeframe.WEEK: WindowPreset((26, 52, 104)),
        Timeframe.MONTH: WindowPreset((12, 36, 60)),
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


class RegimeReason(Enum):
    """판정 불가·보류의 사유."""

    TOO_FEW_BARS = "too_few_bars"  # N < MIN_REGIME_BARS
    DEGENERATE = "degenerate"  # 종가가 전부 같거나(s_r = 0) 구간 끝 ATR이 0
    WEAK_STRENGTH = "weak_strength"  # FLAT_STRENGTH ≤ |S| < TREND_STRENGTH
    STRUCTURE_MISMATCH = "structure_mismatch"  # |S| ≥ TREND_STRENGTH 인데 구조가 다르거나 부족


_CHANNEL_DIRECTIONS = {RegimeVerdict.UP: TrendDirection.UP, RegimeVerdict.DOWN: TrendDirection.DOWN}


@dataclass(frozen=True)
class RegimeJudgement:
    """국면 판정. `strength`·`box_ratio`는 판정 불가일 때만 None이다.

    `reason`은 판정 불가·보류일 때만 있다.
    """

    window: Window
    verdict: RegimeVerdict
    reason: RegimeReason | None
    strength: float | None
    box_ratio: float | None
    structure: Structure
    structure_highs: tuple[Pivot, ...]
    structure_lows: tuple[Pivot, ...]
    # 시작·끝 봉이 확정 피벗(후보용) ±R봉 안이면 극값에서 시작·끝나 치우친
    # 구간이다(window_source와 별개).
    starts_near_extreme: bool
    ends_near_extreme: bool

    @property
    def channel_direction(self) -> TrendDirection | None:
        """추천 채널 방향. 상승·하락 판정만 채널을 추천하고 나머지는 None이다."""
        return _CHANNEL_DIRECTIONS.get(self.verdict)


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
    verdict = RegimeVerdict.UNDECIDABLE
    reason: RegimeReason | None = None
    strength: float | None = None
    box: float | None = None
    sl = slice(window.start, window.end + 1)
    close = series.close[sl]
    atr_end = float(series.atr[window.end])
    spread = 0.0
    if window.bars >= MIN_REGIME_BARS:
        spread = float(np.std(np.diff(np.log(close)), ddof=1))
    if window.bars < MIN_REGIME_BARS:
        reason = RegimeReason.TOO_FEW_BARS
    elif spread == 0 or atr_end == 0:
        reason = RegimeReason.DEGENERATE
    else:
        # S = ln(C_끝/C_시작) / (s_r·√m): 평균 로그수익률의 t값과 같은 식
        strength = float(np.log(close[-1] / close[0])) / (spread * math.sqrt(window.bars - 1))
        box = float(
            (series.high[sl].max() - series.low[sl].min()) / (atr_end * math.sqrt(window.bars))
        )
        if abs(strength) < FLAT_STRENGTH:
            verdict = RegimeVerdict.BOX if box <= BOX_RATIO_MAX else RegimeVerdict.UNCLEAR
        elif abs(strength) < TREND_STRENGTH:
            verdict, reason = RegimeVerdict.HOLD, RegimeReason.WEAK_STRENGTH
        else:
            direction = Structure.UP if strength > 0 else Structure.DOWN
            if structure is direction:
                verdict = RegimeVerdict.UP if strength > 0 else RegimeVerdict.DOWN
            else:
                verdict, reason = RegimeVerdict.HOLD, RegimeReason.STRUCTURE_MISMATCH
    return RegimeJudgement(
        window=window,
        verdict=verdict,
        reason=reason,
        strength=strength,
        box_ratio=box,
        structure=structure,
        structure_highs=highs,
        structure_lows=lows,
        starts_near_extreme=any(abs(window.start - i) <= r for i in extremes),
        ends_near_extreme=any(abs(window.end - i) <= r for i in extremes),
    )
