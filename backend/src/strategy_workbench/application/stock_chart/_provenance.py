"""자동 후보의 기준점·생긴 날 규칙과 자동 기록 파라미터의 owner.

후보(채널·되돌림 스윙·수평 구간) → 저장할 기준점, 후보의 생긴 날(확정 봉), 자동 기록 `params`
칸의 쓰기·읽기를 여기 하나가 정한다. 저장된 자동 선의 생긴 날은 파생값이라 저장하지 않는다(설계
문서 4.7) — 저장한 판정 구간·파라미터로 같은 domain 찾기(`find_channels`·`retracement_pivots`·
`find_zones`)를 다시 돌려 기준점이 같은 후보의 확정 봉을 고른다. 규칙(후보 찾기)은 domain이
소유하고 여기서는 맞는 후보를 고르기만 한다. 기준점을 옮긴 복사본이나 원장이 바뀌어 후보가 사라진
선, 다른 알고리즘 버전의 기록은 None이다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date

from strategy_workbench.application.stock_chart._lines import same_price
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import ParamValue
from strategy_workbench.domain.chart_analysis.facade.geometry import Anchor
from strategy_workbench.domain.chart_analysis.facade.levels import (
    ChannelCandidate,
    HorizontalZone,
    SwingChoice,
    TrendDirection,
    ZoneParams,
    find_channels,
    find_zones,
    retracement_pivots,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import LineKind
from strategy_workbench.domain.chart_analysis.facade.series import (
    ChartSeries,
    Pivot,
    SwingParams,
    Window,
)


def channel_anchors(candidate: ChannelCandidate) -> tuple[Anchor, Anchor, Anchor]:
    """채널 후보 → (a, b, 폭 기준점 w)."""
    return candidate.first.anchor, candidate.second.anchor, candidate.width_pivot.anchor


def retracement_anchors(pair: tuple[Pivot, Pivot]) -> tuple[Anchor, Anchor]:
    """되돌림 스윙 → (먼저 점 = 레벨 1, 나중 점 = 레벨 0)."""
    return pair[0].anchor, pair[1].anchor


def retracement_created(pair: tuple[Pivot, Pivot]) -> int:
    """되돌림 스윙의 생긴 날 = 두 피벗 확정 봉의 나중."""
    return max(pivot.confirmed_index for pivot in pair)


def zone_anchors(zone: HorizontalZone) -> tuple[Anchor, Anchor]:
    """수평 구간 → 띠 선(기준점 둘: 하단·상단). 기준 봉은 가장 이른 구성 피벗이다."""
    start = min(pivot.index for pivot in zone.members)
    return Anchor(start, zone.low), Anchor(start, zone.high)


# 종류별 찾기가 쓰는 도구 칸(`AutoParams.fits`, 필드 이름). 피벗 민감도 둘은 모든 종류에 있다.
_SENSITIVITIES = {"candidate_sensitivity", "structure_sensitivity"}
_TOOL_PARAMS = {
    LineKind.FIB_CHANNEL: {"direction"},
    LineKind.FIB_RETRACEMENT: {"swing"},
    LineKind.HLINE: {"width_atr", "min_touches"},
}


@dataclass(frozen=True)
class AutoParams:
    """자동 기록 `params` 칸의 값 — 피벗 민감도와 도구별 파라미터(쓰지 않는 칸은 None)."""

    candidate_sensitivity: float
    structure_sensitivity: float
    direction: TrendDirection | None = None
    first_anchor: date | None = None
    swing: SwingChoice | None = None
    width_atr: float | None = None
    min_touches: int | None = None

    def fits(self, kind: LineKind) -> bool:
        """이 종류의 찾기가 쓰는 도구 칸이 모두 있고 다른 도구의 칸은 없는가. 채널은 방향(첫 점은
        없어도 된다), 되돌림은 기준 스윙, 수평 구간은 묶는 폭·최소 접촉이다."""
        # 칸 이름은 필드 이름이다(`to_params` 키와 같다). 민감도 둘은 모든 종류에 있다
        present = {
            f.name for f in fields(self) if getattr(self, f.name) is not None
        } - _SENSITIVITIES
        return present - {"first_anchor"} == _TOOL_PARAMS[kind] and (
            "first_anchor" not in present or kind is LineKind.FIB_CHANNEL
        )

    @property
    def swing_params(self) -> SwingParams:
        return SwingParams(
            candidate_sensitivity=self.candidate_sensitivity,
            structure_sensitivity=self.structure_sensitivity,
        )

    def to_params(self) -> dict[str, ParamValue]:
        values: dict[str, ParamValue] = {
            "candidate_sensitivity": self.candidate_sensitivity,
            "structure_sensitivity": self.structure_sensitivity,
            "direction": None if self.direction is None else self.direction.value,
            "first_anchor": None if self.first_anchor is None else self.first_anchor.isoformat(),
            "swing": None if self.swing is None else self.swing.value,
            "width_atr": self.width_atr,
            "min_touches": self.min_touches,
        }
        # 채널의 `first_anchor`는 None도 뜻이 있다(모든 쌍) — 채널 기록에는 늘 싣는다
        keep = {"first_anchor"} if self.direction is not None else set()
        return {k: v for k, v in values.items() if v is not None or k in keep}

    @classmethod
    def read(cls, params: Mapping[str, ParamValue]) -> AutoParams | None:
        """저장된 칸을 읽는다. 이 판의 형식·어휘·범위로 읽히지 않으면 None 하나다."""
        direction, first, swing = (params.get(k) for k in ("direction", "first_anchor", "swing"))
        width = params.get("width_atr")
        try:
            parsed = cls(
                candidate_sensitivity=_number(params["candidate_sensitivity"]),
                structure_sensitivity=_number(params["structure_sensitivity"]),
                direction=None if direction is None else TrendDirection(direction),
                first_anchor=None if first is None else date.fromisoformat(str(first)),
                swing=None if swing is None else SwingChoice(swing),
                width_atr=None if width is None else _number(width),
                min_touches=_integer(params.get("min_touches")),
            )
            parsed.swing_params  # noqa: B018  # reason: 민감도 범위를 domain 값 타입이 검사한다
        except (KeyError, TypeError, ValueError):
            return None
        return parsed


def _number(value: ParamValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"expected a finite number — got={value!r}")
    return float(value)


def _integer(value: ParamValue) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an integer — got={value!r}")
    return value


def created_index(
    series: ChartSeries,
    window: Window,
    kind: LineKind,
    anchors: Sequence[Anchor],
    params: AutoParams,
) -> int | None:
    """`series`는 저장한 피벗 민감도로 묶은 계열, `window`는 저장한 판정 구간이다."""
    if kind is LineKind.FIB_CHANNEL:
        if params.direction is None:
            return None
        first = None if params.first_anchor is None else series.index_of(params.first_anchor)
        if params.first_anchor is not None and first is None:
            return None
        search = find_channels(series, window, params.direction, first)
        return next(
            (c.created_index for c in search.candidates if same(channel_anchors(c), anchors)),
            None,
        )
    if kind is LineKind.FIB_RETRACEMENT:
        if params.swing is None:
            return None
        pair = retracement_pivots(series, window, params.swing)
        if pair is None or not same(retracement_anchors(pair), anchors):
            return None
        return retracement_created(pair)
    defaults = ZoneParams()
    try:
        zone_params = ZoneParams(
            defaults.width_atr if params.width_atr is None else params.width_atr,
            defaults.min_touches if params.min_touches is None else params.min_touches,
        )
    except ValueError:
        return None
    return next(
        (
            zone.formed_index
            for zone in find_zones(series, window, zone_params)
            if same(zone_anchors(zone), anchors)
        ),
        None,
    )


def same(left: Sequence[Anchor], right: Sequence[Anchor]) -> bool:
    """같은 봉·같은 가격(화면 ↔ 저장 왕복의 마지막 자리 차이는 같다고 본다)."""
    return len(left) == len(right) and all(
        a.index == b.index and same_price(a.price, b.price)
        for a, b in zip(left, right, strict=True)
    )
