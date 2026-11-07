"""수평 지지·저항 구간 찾기의 owner(설계 문서 4.5).

어떤 점을 묶을지는 판정 구간 끝 봉의 폭으로 고르고, 생긴 날은 그날의 폭으로도 한 구간인 첫 봉이다.
지킴·뚫림은 생긴 날 뒤 판정 구간 안의 재접촉만 센다. 저항·지지 구분은 구간 찾기와 따로 현재가로
정한다(`zone_side`) — 판정 구간을 과거로 옮겨도 구분은 지금 가격 기준이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.chart_analysis._series import ChartSeries, Pivot, Window

# 이 봉 수 안에 붙은 피벗은 접촉 1회다.
TOUCH_GAP_BARS = 2


@dataclass(frozen=True)
class ZoneParams:
    """`width_atr`는 묶는 폭 k_z(tol = k_z·ATR_T/C_T), `min_touches`는 최소 접촉 수."""

    width_atr: float = 0.5
    min_touches: int = 2

    def __post_init__(self) -> None:
        if not (math.isfinite(self.width_atr) and self.width_atr > 0) or self.min_touches < 1:
            raise ValueError(
                "width_atr must be finite and positive and min_touches >= 1 — "
                f"width_atr={self.width_atr} min_touches={self.min_touches}"
            )


class ZoneSide(Enum):
    RESISTANCE = "resistance"
    SUPPORT = "support"
    STRADDLE = "straddle"


@dataclass(frozen=True)
class HorizontalZone:
    members: tuple[Pivot, ...]
    low: float
    high: float
    touches: int
    formed_index: int
    held: int
    broken: int


def _side(price: float, lo: float, hi: float) -> int:
    """가격이 구간 위면 1, 아래면 −1, 구간 안(경계 포함)이면 0."""
    return 1 if price > hi else -1 if price < lo else 0


# 현재가가 구간 아래(−1)면 그 구간은 저항, 위(1)면 지지, 안이면 걸침이다.
_ZONE_SIDES = {-1: ZoneSide.RESISTANCE, 1: ZoneSide.SUPPORT, 0: ZoneSide.STRADDLE}


def zone_side(zone: HorizontalZone, current: float) -> ZoneSide:
    """현재가(`current`, 구간과 같은 가격 공간)에 대한 구간의 구분. 경계에 닿으면 걸침이다."""
    return _ZONE_SIDES[_side(current, zone.low, zone.high)]


def _touches(members: list[Pivot]) -> int:
    count, last = 0, -math.inf
    for i in sorted(p.index for p in members):
        if i - last > TOUCH_GAP_BARS:
            count += 1
        last = i
    return count


def find_zones(
    series: ChartSeries, window: Window, params: ZoneParams | None = None
) -> tuple[HorizontalZone, ...]:
    """구간의 확정 피벗(후보용)을 로그 가격 순으로 훑어 첫 점 대비 `ln(p/p_first) ≤ tol`인 동안
    묶는다 (완전 연결 — 구간 폭 ≤ tol). 접촉 수가 모자란 묶음은 버리고 위쪽 구간부터 돌려준다."""
    params = params or ZoneParams()
    high, low, close = series.high, series.low, series.close
    end = window.end

    def tolerance(t: int) -> float:
        return params.width_atr * float(series.atr[t]) / float(close[t])

    groups: list[list[Pivot]] = []
    for p in sorted(series.confirmed_in(window), key=lambda p: (p.price, p.index)):
        if groups and math.log(p.price / groups[-1][0].price) <= tolerance(end):
            groups[-1].append(p)
        else:
            groups.append([p])
    zones: list[HorizontalZone] = []
    for group in groups:
        touches = _touches(group)
        if touches < params.min_touches:
            continue
        lo, hi = group[0].price, group[-1].price
        formed = max(p.confirmed_index for p in group)
        # 구간 끝(end)에서는 묶기 조건과 같은 식이라 반드시 멈춘다.
        while math.log(hi / lo) > tolerance(formed):
            formed += 1
        held = broken = 0
        t = formed + 1
        while t <= end:
            if not (low[t] <= hi and high[t] >= lo):
                t += 1
                continue
            e = t
            while e + 1 <= end and low[e + 1] <= hi and high[e + 1] >= lo:
                e += 1
            if e + 1 <= end:
                # 묶음 다음 봉은 구간에 닿지 않아 after는 ±1이다.
                # 직전 종가가 구간 안(0)이면 지킴도 뚫림도 아니다.
                before = _side(float(close[t - 1]), lo, hi)
                after = _side(float(close[e + 1]), lo, hi)
                held += after == before
                broken += after == -before
            t = e + 2
        zones.append(
            HorizontalZone(
                members=tuple(group),
                low=lo,
                high=hi,
                touches=touches,
                formed_index=formed,
                held=held,
                broken=broken,
            )
        )
    zones.sort(key=lambda z: -z.high)
    return tuple(zones)
