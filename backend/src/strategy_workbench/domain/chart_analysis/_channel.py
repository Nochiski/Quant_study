"""피보나치 채널 후보 찾기와 채널 방향 어휘의 owner(설계 문서 4.3).

기하(기준선·폭·레벨 가격)는 `ChannelGeometry` 하나가 정한다. 여기서는 어떤 피벗 셋을 후보로
둘지와 순위만 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.chart_analysis._geometry import ChannelGeometry, TrendLine
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
    geometry: ChannelGeometry
    outside_closes: int
    span_bars: int
    width_atr: float
    created_index: int  # 세 기준점 확정 봉의 최댓값. 그 앞은 옅게 그린다


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
    a 이후 반대쪽 확정 피벗 중 기준선에서 가장 먼 점으로 폭을 정한다.

    순위: `MIN_CHANNEL_BARS`봉 미만 후보는 뒤 → 기준점~구간 끝 종가 중 채널 밖 비율 오름차순 →
    폭/ATR 오름차순. `first_anchor`(봉 index)를 주면 그 피벗을 첫 점으로 고정하고 둘째 점·폭만
    찾는다.
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
            line = TrendLine(a.anchor, b.anchor)
            if any(
                sign * (line.at(q.index) - q.price) > OVERSHOOT_ATR * atr_end
                for q in base
                if q.index >= a.index
            ):
                continue
            far = [
                (gap, q)
                for q in opposite
                if q.index > a.index and (gap := sign * (q.price - line.at(q.index))) > 0
            ]
            if not far:
                continue
            gap, w = max(far, key=lambda item: item[0])
            geometry = ChannelGeometry(line, w.anchor)
            outside = 0
            for t in range(a.index, window.end + 1):
                edges = (geometry.price_at(t, 0.0), geometry.price_at(t, 1.0))
                outside += not min(edges) <= series.close[t] <= max(edges)
            out.append(
                ChannelCandidate(
                    direction=direction,
                    first=a,
                    second=b,
                    width_pivot=w,
                    geometry=geometry,
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
