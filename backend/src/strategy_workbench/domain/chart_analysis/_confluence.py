"""중첩(컨플루언스) 입력 조립과 찾기의 owner(설계 문서 4.6).

`bands_of`가 선 하나(채널·되돌림 기하, 수평선 가격, 수평 구간)를 마지막 완성 봉 기준 가격 구간으로
바꾸고(레벨 가격 ≤ 0·되돌림 레벨 0·1을 빼는 규칙 포함), `find_confluences`가 종류마다 넓힌 구간의
합집합을 만든 뒤 가격축을 경계점으로 쪼개 서로 다른 종류 2개 이상이 덮는 조각을 이어 하나의 중첩으로
합친다. 같은 종류(자동 수평 구간과 내 수평선)는 1종이다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from strategy_workbench.domain.chart_analysis._geometry import (
    ChannelGeometry,
    RetracementGeometry,
    levels_or_default,
)
from strategy_workbench.domain.chart_analysis._line_record import LineKind
from strategy_workbench.domain.chart_analysis._series import ChartSeries
from strategy_workbench.domain.chart_analysis._zones import HorizontalZone

# 되돌림 레벨 0·1은 스윙 기준점 자체라 중첩에 넣지 않는다.
_RETRACEMENT_ANCHOR_LEVELS = (0.0, 1.0)
# 선마다 가격 구간을 넓히는 비율(±).
DEFAULT_CONFLUENCE_TOLERANCE = 0.005


@dataclass(frozen=True)
class PriceBand:
    """보이는 선 하나의 가격 구간. 수평 구간은 [lo, hi], 레벨 선은 lo = hi. `bands_of`로 만든다.

    `ref`는 호출자가 원래 선으로 되돌아가기 위한 식별자이고, `level`은 되돌림·채널의 레벨 r이다.
    """

    kind: LineKind
    low: float
    high: float
    ref: str
    level: float | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.low) and math.isfinite(self.high) and 0 < self.low <= self.high):
            raise ValueError(
                f"price band must satisfy 0 < low <= high — ref={self.ref} "
                f"low={self.low} high={self.high}"
            )


@dataclass(frozen=True)
class Confluence:
    """넓힌 좌표의 중첩 구간 [low, high], 덮는 종류, 그 구간과 겹치는 선(같은 종류 포함)."""

    low: float
    high: float
    kinds: frozenset[LineKind]
    members: tuple[PriceBand, ...]


def bands_of(
    series: ChartSeries,
    line: ChannelGeometry | RetracementGeometry | HorizontalZone | tuple[float, float] | float,
    ref: str,
    levels: Sequence[float] | None = None,
) -> tuple[PriceBand, ...]:
    """선 하나를 중첩 입력으로 바꾼다. `series`는 그 선을 만든 주기의 계열이고, 기준 봉은 그 마지막
    완성 봉이다. `line`이 실수면 수평선 가격이고, (low, high) 쌍이면 저장된 수평 띠(자동 수평
    구간을 기준점 두 개로 저장한 선)다. `levels`는 채널·되돌림에만 쓰고, None이면 종류별 기본
    (`levels_or_default`)이다.

    레벨 가격이 0 이하인 레벨(가파른 하락 채널의 아래 레벨 등)과 되돌림 레벨 0·1은 넣지 않는다.
    """
    end = len(series.candles) - 1
    spans: list[tuple[LineKind, float, float, float | None]]
    if isinstance(line, ChannelGeometry):
        spans = [
            (LineKind.FIB_CHANNEL, p, p, r)
            for r in levels_or_default(LineKind.FIB_CHANNEL, levels)
            for p in [line.price_at(end, r)]
        ]
    elif isinstance(line, RetracementGeometry):
        spans = [
            (LineKind.FIB_RETRACEMENT, p, p, r)
            for r in levels_or_default(LineKind.FIB_RETRACEMENT, levels)
            if r not in _RETRACEMENT_ANCHOR_LEVELS
            for p in [line.price_at(r)]
        ]
    elif isinstance(line, HorizontalZone):
        spans = [(LineKind.HLINE, line.low, line.high, None)]
    elif isinstance(line, tuple):
        spans = [(LineKind.HLINE, min(line), max(line), None)]
    else:
        spans = [(LineKind.HLINE, line, line, None)]
    return tuple(PriceBand(kind, lo, hi, ref, r) for kind, lo, hi, r in spans if lo > 0)


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def find_confluences(
    bands: Sequence[PriceBand], tolerance: float = DEFAULT_CONFLUENCE_TOLERANCE
) -> tuple[Confluence, ...]:
    """각 선을 ±`tolerance`(비율) 넓혀 서로 다른 종류 2개 이상이 덮는 구간을 낮은
    가격부터 돌려준다."""
    if not (math.isfinite(tolerance) and 0 < tolerance < 1):
        # 0이면 레벨 선(점 구간)끼리는 길이 0으로만 겹쳐 조용히 빠진다
        raise ValueError(f"tolerance must be in (0, 1) — tolerance={tolerance}")
    widened = [(band, band.low * (1 - tolerance), band.high * (1 + tolerance)) for band in bands]
    unions = {
        kind: _union([(lo, hi) for band, lo, hi in widened if band.kind is kind])
        for kind in {band.kind for band, _, _ in widened}
    }
    edges = sorted({x for spans in unions.values() for span in spans for x in span})
    regions: list[tuple[float, float, set[LineKind]]] = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        mid = (lo + hi) / 2
        covering = {kind for kind, spans in unions.items() if any(a <= mid <= b for a, b in spans)}
        if len(covering) < 2:
            continue
        if regions and regions[-1][1] == lo:
            regions[-1] = (regions[-1][0], hi, regions[-1][2] | covering)
        else:
            regions.append((lo, hi, covering))
    return tuple(
        Confluence(
            low=lo,
            high=hi,
            kinds=frozenset(kinds),
            members=tuple(band for band, a, b in widened if a < hi and b > lo),
        )
        for lo, hi, kinds in regions
    )
