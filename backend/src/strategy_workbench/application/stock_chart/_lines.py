"""저장된 선(기준점) ↔ domain 기하와 레벨 선을 그릴 점의 조립.

기하 식은 `domain.chart_analysis`(`ChannelGeometry`·`RetracementGeometry`)가 소유한다.
여기서는 기준점 날짜를 만든 주기 계열의 봉 index로 옮기고(`ChartSeries.index_of`), 레벨마다
(기준 봉, 마지막 완성 봉) 두 점을 화면 가격으로 낸다 — frontend는 두 점을 잇고 오른쪽으로
연장만 한다(설계 문서 6장).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from strategy_workbench.application.stock_chart._errors import ChartRequestError
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import LineAnchor
from strategy_workbench.domain.chart_analysis.facade.bars import DisplayScale
from strategy_workbench.domain.chart_analysis.facade.geometry import (
    Anchor,
    ChannelGeometry,
    RetracementGeometry,
    TrendLine,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import LineKind
from strategy_workbench.domain.chart_analysis.facade.series import ChartSeries

# 수평선은 기준점 하나(선) 또는 둘(자동 수평 구간을 저장한 띠 [low, high]).
ANCHOR_COUNTS: dict[LineKind, tuple[int, ...]] = {
    LineKind.HLINE: (1, 2),
    LineKind.FIB_RETRACEMENT: (2,),
    LineKind.FIB_CHANNEL: (3,),
}

LineGeometry = ChannelGeometry | RetracementGeometry | tuple[float, float] | float


def check_levels(levels: Sequence[float] | None) -> None:
    """빈 레벨 목록은 그릴 선이 없다 — 기본 레벨은 None(칸 비움)으로 고른다."""
    if levels is not None and not (levels and all(math.isfinite(level) for level in levels)):
        raise ChartRequestError(
            "chart.line.levels_invalid",
            f"levels must be non-empty and finite — levels={list(levels)}",
        )


@dataclass(frozen=True)
class ChartPoint:
    """화면 좌표의 점(봉 날짜, 화면 가격)."""

    bar_date: date
    price: float


@dataclass(frozen=True)
class LevelPath:
    """레벨 선 하나를 그릴 두 점. `start`는 기준 봉(첫 기준점), `end`는 그 주기의 마지막 완성 봉이고
    `end.price`가 마지막 완성 봉의 레벨 가격이다. 수평선·띠는 `level`이 None이다."""

    level: float | None
    start: ChartPoint
    end: ChartPoint


def check_shape(
    kind: LineKind, anchors: Sequence[LineAnchor], levels: Sequence[float] | None
) -> None:
    """기준점 수·가격·레벨 값 검사(봉 index와 무관한 것)."""
    if len(anchors) not in ANCHOR_COUNTS[kind]:
        raise ChartRequestError(
            "chart.line.anchor_count",
            f"wrong anchor count — kind={kind.value} anchors={len(anchors)} "
            f"expected={ANCHOR_COUNTS[kind]}",
        )
    for anchor in anchors:
        if not (math.isfinite(anchor.price) and anchor.price > 0):
            raise ChartRequestError(
                "chart.line.price_invalid",
                f"anchor price must be finite and positive — bar_date={anchor.bar_date} "
                f"price={anchor.price}",
            )
    check_levels(levels)


def resolve(series: ChartSeries, anchors: Sequence[LineAnchor]) -> tuple[Anchor, ...] | date:
    """기준점 날짜 → 그 주기 완성 봉 index. 계열 밖 날짜가 있으면 그 첫 날짜를 돌려준다."""
    resolved: list[Anchor] = []
    for anchor in anchors:
        index = series.index_of(anchor.bar_date)
        if index is None:
            return anchor.bar_date
        resolved.append(Anchor(index, anchor.price))
    return tuple(resolved)


def geometry(kind: LineKind, anchors: Sequence[Anchor]) -> LineGeometry:
    """봉 index 기준점 → domain 기하. 채널은 (a, b, 폭 기준점 w) 순서다.

    Raises:
        ChartRequestError: 되돌림·채널 기준선의 두 점이 봉 순서가 아닐 때
            (`chart.line.anchor_order`).
    """
    try:
        if kind is LineKind.FIB_CHANNEL:
            first, second, width = anchors
            return ChannelGeometry(TrendLine(first, second), width)
        if kind is LineKind.FIB_RETRACEMENT:
            first, second = anchors
            return RetracementGeometry(first, second)
    except ValueError as error:
        raise ChartRequestError("chart.line.anchor_order", str(error)) from error
    if len(anchors) == 2:
        return (anchors[0].price, anchors[1].price)
    return anchors[0].price


def paths(
    series: ChartSeries,
    scale: DisplayScale,
    shape: LineGeometry,
    anchors: Sequence[Anchor],
    levels: Sequence[float],
) -> tuple[LevelPath, ...]:
    """레벨마다 (기준 봉, 화면 가격) → (마지막 완성 봉, 화면 가격). `levels`는
    domain `levels_or_default`로 고른 것이다."""
    end = len(series.candles) - 1
    end_date = series.bar_date(end)

    def path(
        level: float | None, start_index: int, start_price: float, end_price: float
    ) -> LevelPath:
        return LevelPath(
            level,
            ChartPoint(series.bar_date(start_index), scale.to_display(start_price)),
            ChartPoint(end_date, scale.to_display(end_price)),
        )

    if isinstance(shape, ChannelGeometry):
        start = shape.line.first.index
        return tuple(
            path(r, start, shape.price_at(start, r), shape.price_at(end, r)) for r in levels
        )
    if isinstance(shape, RetracementGeometry):
        start = shape.first.index
        return tuple(path(r, start, shape.price_at(r), shape.price_at(r)) for r in levels)
    return tuple(path(None, anchor.index, anchor.price, anchor.price) for anchor in anchors)
