"""피보나치 되돌림 기준 스윙 고르기와 레벨 가격의 owner(설계 문서 4.4).

방향은 순서로 정한다: 먼저 점 = 레벨 1, 나중 점 = 레벨 0(TradingView 관례).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.chart_analysis._series import ChartSeries, PivotKind, Window

# 되돌림·채널의 기본 레벨. 선마다 저장된 레벨이 있으면 그것이 우선이다.
DEFAULT_FIB_LEVELS: tuple[float, ...] = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


class SwingChoice(Enum):
    RANGE_EXTREMES = "range_extremes"  # 구간 최저 저점 피벗 ↔ 최고 고점 피벗(후보용)
    LATEST_MAJOR = "latest_major"  # 구조 판정용(큰 스윙) 마지막 피벗 2개
    LATEST_CONFIRMED = "latest_confirmed"  # 후보용 마지막 피벗 2개


@dataclass(frozen=True)
class Anchor:
    index: int
    price: float


@dataclass(frozen=True)
class Retracement:
    """`first`(먼저 점, 레벨 1)와 `second`(나중 점, 레벨 0). 두 점 직접 지정도 이 타입이다."""

    first: Anchor
    second: Anchor

    def __post_init__(self) -> None:
        if self.first.index >= self.second.index:
            raise ValueError(
                f"retracement anchors must be in bar order — first={self.first} "
                f"second={self.second}"
            )

    def price_at(self, level: float) -> float:
        """`price(r) = P_0 + r·(P_1 − P_0)`."""
        return self.second.price + level * (self.first.price - self.second.price)


def retracement_swing(
    series: ChartSeries, window: Window, choice: SwingChoice
) -> Retracement | None:
    """구간 안에서 구간 끝까지 확정된 피벗으로 기준 스윙을 고른다.

    고를 피벗이 없으면 None이다(실패 사유는 그 하나뿐이다).
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
    return Retracement(Anchor(pair[0].index, pair[0].price), Anchor(pair[1].index, pair[1].price))
