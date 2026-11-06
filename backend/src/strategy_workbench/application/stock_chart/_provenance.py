"""저장된 자동 선의 생긴 날(확정 봉)을 저장한 입력으로 다시 계산한다.

생긴 날은 파생값이라 저장하지 않는다(설계 문서 4.7). 자동 기록(판정 구간·파라미터)으로 같은 domain
찾기를 다시 돌려, 저장된 기준점과 같은 후보의 확정 봉을 돌려준다. 규칙은 domain(`find_channels`·
`retracement_pivots`·`find_zones`)이 소유하고 여기서는 맞는 후보를 고르기만 한다. 기준점을 옮긴
복사본(`auto_copied`)이나 원장이 바뀌어 후보가 사라진 선은 None이다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from strategy_workbench.application.stock_chart._lines import band_anchors, same_anchors
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import ParamValue
from strategy_workbench.domain.chart_analysis.facade.geometry import Anchor
from strategy_workbench.domain.chart_analysis.facade.levels import (
    SwingChoice,
    TrendDirection,
    ZoneParams,
    find_channels,
    find_zones,
    retracement_pivots,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import LineKind
from strategy_workbench.domain.chart_analysis.facade.series import ChartSeries, Window


def finite_number(value: ParamValue) -> float | None:
    """파라미터 칸의 유한한 수. 수가 아니거나(불리언 포함) NaN·inf면 None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def created_index(
    series: ChartSeries,
    window: Window,
    kind: LineKind,
    anchors: Sequence[Anchor],
    params: Mapping[str, ParamValue],
) -> int | None:
    """`series`는 저장한 피벗 민감도로 묶은 계열, `window`는 저장한 판정 구간이다."""
    try:
        if kind is LineKind.FIB_CHANNEL:
            first = params.get("first_anchor")
            first_index = None if first is None else series.index_of(date.fromisoformat(str(first)))
            if first is not None and first_index is None:
                return None
            search = find_channels(series, window, TrendDirection(params["direction"]), first_index)
            return next(
                (
                    c.created_index
                    for c in search.candidates
                    if same_anchors(
                        (c.first.anchor, c.second.anchor, c.width_pivot.anchor), anchors
                    )
                ),
                None,
            )
        if kind is LineKind.FIB_RETRACEMENT:
            pair = retracement_pivots(series, window, SwingChoice(params["swing"]))
            if pair is None or not same_anchors((pair[0].anchor, pair[1].anchor), anchors):
                return None
            return max(p.confirmed_index for p in pair)
        defaults = ZoneParams()
        width = finite_number(params.get("width_atr", defaults.width_atr))
        touches = params.get("min_touches", defaults.min_touches)
        if width is None or isinstance(touches, bool) or not isinstance(touches, int):
            return None
        zone_params = ZoneParams(width, touches)
    except (KeyError, ValueError):
        # 저장 칸이 이 판의 어휘·범위로 읽히지 않는다 — 다시 계산할 수 없다
        return None
    return next(
        (
            zone.formed_index
            for zone in find_zones(series, window, zone_params)
            if same_anchors(
                band_anchors(min(p.index for p in zone.members), zone.low, zone.high), anchors
            )
        ),
        None,
    )
