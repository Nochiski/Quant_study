"""선 종류와 중첩(컨플루언스) 찾기의 owner(설계 문서 4.6).

종류마다 넓힌 가격 구간의 합집합을 만든 뒤 가격축을 경계점으로 쪼개, 서로 다른 종류 2개 이상이
덮는 조각을 찾고 이어진 조각을 하나의 중첩으로 합친다. 같은 종류(자동 수평 구간과 내 수평선)는
1종이다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


class LineKind(Enum):
    HLINE = "hline"
    FIB_RETRACEMENT = "fib_retracement"
    FIB_CHANNEL = "fib_channel"


# 되돌림 레벨 0·1은 스윙 기준점 자체라 중첩에 넣지 않는다.
_RETRACEMENT_ANCHOR_LEVELS = (0.0, 1.0)


@dataclass(frozen=True)
class PriceBand:
    """보이는 선 하나의 가격 구간. 수평 구간은 [lo, hi], 레벨 선은 lo = hi(채널은 기준일 레벨 가격).

    `ref`는 호출자가 원래 선·레벨로 되돌아가기 위한 식별자이고, `level`은 되돌림·채널의 레벨 r이다.
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


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def find_confluences(
    bands: Sequence[PriceBand], tolerance: float = 0.005
) -> tuple[Confluence, ...]:
    """각 선을 ±`tolerance`(비율, 기본 0.5%) 넓혀 서로 다른 종류 2개 이상이 덮는 구간을 낮은
    가격부터 돌려준다."""
    if not (math.isfinite(tolerance) and 0 <= tolerance < 1):
        raise ValueError(f"tolerance must be in [0, 1) — tolerance={tolerance}")
    widened = [
        (band, band.low * (1 - tolerance), band.high * (1 + tolerance))
        for band in bands
        if not (band.kind is LineKind.FIB_RETRACEMENT and band.level in _RETRACEMENT_ANCHOR_LEVELS)
    ]
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
