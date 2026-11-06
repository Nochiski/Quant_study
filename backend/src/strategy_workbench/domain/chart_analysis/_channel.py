"""피보나치 채널 후보 찾기의 owner(설계 문서 4.3).

기준선 `L(t) = p_a + m(t − i_a)`, 레벨 r의 가격 `C_r(t) = L(t) − r·d`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.chart_analysis._series import ChartSeries, Pivot, PivotKind, Window

# 뒤의 같은 쪽 확정 피벗이 기준선 너머로 이만큼(× 구간 끝 ATR) 넘게 나가면 버린다.
OVERSHOOT_ATR = 0.25
# 기준점부터 구간 끝까지 이 봉 수보다 짧은 후보는 순위 뒤로 보낸다.
MIN_CHANNEL_BARS = 20


class TrendDirection(Enum):
    UP = "up"
    DOWN = "down"


class AnchorIssue(Enum):
    OUTSIDE_WINDOW = "outside_window"
    NOT_A_PIVOT = "not_a_pivot"  # 그 방향의 확정 피벗이 아니다(구간 끝 뒤에 확정된 것 포함)


@dataclass(frozen=True)
class ChannelCandidate:
    direction: TrendDirection
    first: Pivot
    second: Pivot
    width_pivot: Pivot
    slope: float
    depth: float  # d: 상승은 음수라 r이 클수록 위
    outside_closes: int
    span_bars: int
    width_atr: float
    created_index: int  # 세 기준점 확정 봉의 최댓값. 그 앞은 옅게 그린다

    def price_at(self, index: int, level: float) -> float:
        return self.first.price + self.slope * (index - self.first.index) - level * self.depth


@dataclass(frozen=True)
class ChannelSearch:
    """후보(순위순)와, 첫 점을 지정했는데 쓸 수 없을 때 그 이유."""

    candidates: tuple[ChannelCandidate, ...]
    anchor_issue: AnchorIssue | None = None


def find_channels(
    series: ChartSeries,
    window: Window,
    direction: TrendDirection,
    first_anchor: int | None = None,
) -> ChannelSearch:
    """구간 안 같은 방향 확정 피벗 2개(up: 높아지는 저점, down: 낮아지는 고점)로 기준선을 잡고,
    그 뒤 반대쪽 확정 피벗 중 기준선에서 가장 먼 점으로 폭을 정한다.

    순위: 20봉 미만 후보는 뒤 → 기준점~구간 끝 종가 중 채널 밖 비율 오름차순 → 폭/ATR 오름차순.
    `first_anchor`(봉 index)를 주면 그 피벗을 첫 점으로 고정하고 둘째 점·폭만 찾는다.
    """
    up = direction is TrendDirection.UP
    pivots = series.confirmed_in(window)
    base = [p for p in pivots if p.kind is (PivotKind.LOW if up else PivotKind.HIGH)]
    opposite = [p for p in pivots if p.kind is (PivotKind.HIGH if up else PivotKind.LOW)]
    if first_anchor is not None:
        if not window.start <= first_anchor <= window.end:
            return ChannelSearch((), AnchorIssue.OUTSIDE_WINDOW)
        if all(p.index != first_anchor for p in base):
            return ChannelSearch((), AnchorIssue.NOT_A_PIVOT)
    atr_end = float(series.atr[window.end])
    sign = 1.0 if up else -1.0  # 기준선 바깥(up: 아래)으로 나간 거리 = sign·(L − p)
    out: list[ChannelCandidate] = []
    for x, a in enumerate(base):
        if first_anchor is not None and a.index != first_anchor:
            continue
        for b in base[x + 1 :]:
            rise = b.price - a.price
            if sign * rise <= 0 or abs(rise) < atr_end:
                continue
            slope = rise / (b.index - a.index)

            def line(t: int, a: Pivot = a, slope: float = slope) -> float:
                return a.price + slope * (t - a.index)

            if any(
                sign * (line(q.index) - q.price) > OVERSHOOT_ATR * atr_end
                for q in base
                if q.index >= a.index
            ):
                continue
            far = [(sign * (q.price - line(q.index)), q) for q in opposite if q.index > a.index]
            far = [item for item in far if item[0] > 0]
            if not far:
                continue
            gap, w = max(far, key=lambda item: item[0])
            depth = -sign * gap
            outside = sum(
                not min(line(t), line(t) - depth)
                <= series.close[t]
                <= max(line(t), line(t) - depth)
                for t in range(a.index, window.end + 1)
            )
            out.append(
                ChannelCandidate(
                    direction=direction,
                    first=a,
                    second=b,
                    width_pivot=w,
                    slope=slope,
                    depth=depth,
                    outside_closes=outside,
                    span_bars=window.end - a.index + 1,
                    width_atr=gap / atr_end,
                    created_index=max(a.confirmed_index, b.confirmed_index, w.confirmed_index),
                )
            )
    out.sort(
        key=lambda c: (c.span_bars < MIN_CHANNEL_BARS, c.outside_closes / c.span_bars, c.width_atr)
    )
    return ChannelSearch(tuple(out))
