"""피보나치 되돌림 기준 스윙 고르기의 owner(설계 문서 4.4).

레벨 가격은 `RetracementGeometry`가 정한다.
"""

from __future__ import annotations

from enum import Enum

from strategy_workbench.domain.chart_analysis._geometry import RetracementGeometry
from strategy_workbench.domain.chart_analysis._series import ChartSeries, PivotKind, Window


class SwingChoice(Enum):
    RANGE_EXTREMES = "range_extremes"  # 구간 최저 저점 피벗 ↔ 최고 고점 피벗(후보용)
    LATEST_MAJOR = "latest_major"  # 구조 판정용(큰 스윙) 마지막 피벗 2개
    LATEST_CONFIRMED = "latest_confirmed"  # 후보용 마지막 피벗 2개


def retracement_swing(
    series: ChartSeries, window: Window, choice: SwingChoice
) -> RetracementGeometry | None:
    """구간 안에서 구간 끝까지 확정된 피벗으로 기준 스윙을 고른다.

    고를 피벗이 없으면 None이다(실패 사유는 그 하나뿐이다). 두 점을 직접 지정할 때는
    `RetracementGeometry`를 바로 만든다.
    """
    if choice is SwingChoice.RANGE_EXTREMES:
        pivots = series.confirmed_in(window)
        highs = [p for p in pivots if p.kind is PivotKind.HIGH]
        lows = [p for p in pivots if p.kind is PivotKind.LOW]
        if not highs or not lows:
            return None
        # 같은 값이면 먼저 나온 피벗(max·min은 첫 극값을 돌려준다)
        pair = sorted(
            (max(highs, key=lambda p: p.price), min(lows, key=lambda p: p.price)),
            key=lambda p: p.index,
        )
    else:
        pair = list(series.confirmed_in(window, major=choice is SwingChoice.LATEST_MAJOR)[-2:])
        if len(pair) < 2:
            return None
    return RetracementGeometry(pair[0].anchor, pair[1].anchor)
