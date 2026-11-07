"""개별종목차트 API: 종목·지수 검색, 캔들, 국면 판정·채널·되돌림·수평 구간·중첩 후보, 선 저장.

값·문장·좌표 변환은 `application.stock_chart`(규칙은 `domain.chart_analysis`)가 정한다. 여기서는
응답 모양으로 옮기기만 한다 — 가격은 모두 화면 가격(저장 좌표 × k), 날짜는 ISO, 판정·사유는 영문
코드와 backend가 완성한 한글 문장. 레벨 선은 레벨마다 (기준 봉, 마지막 완성 봉) 두 점을 싣는다 —
frontend는 두 점을 잇고 오른쪽으로 연장만 하며 가격을 계산하지 않는다(설계 문서 6장). 거절은
404·422 `{detail: {code, message}}`이고 요청 칸 검증 실패도 `chart.request_invalid`로 코드화한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException, Query, status
from pydantic import Field

from strategy_workbench.application.stock_chart.facade.chart import (
    MAX_CHANNEL_CANDIDATES,
    MAX_SEARCH_RESULTS,
    CandleView,
    ChannelView,
    ChartContext,
    ChartErrorCode,
    ChartLineDraft,
    ChartLines,
    ChartLineView,
    ChartPoint,
    ChartRequestError,
    ConfluenceLine,
    ConfluenceView,
    InstrumentNotFoundError,
    InstrumentSearch,
    LevelPath,
    LineDrawing,
    LineEdit,
    LineIssue,
    LineShape,
    PivotPoint,
    RegimeView,
    RetracementView,
    StockChartService,
    SwingSettings,
    WindowRequest,
    WindowView,
    ZonesView,
)
from strategy_workbench.application.stock_chart.facade.ports import (
    AutoRecord,
    ChartInstrument,
    ChartLineNotFoundError,
    InstrumentKind,
    InstrumentRef,
    ParamValue,
    RegimeRecord,
)
from strategy_workbench.domain.chart_analysis.facade.bars import Timeframe
from strategy_workbench.domain.chart_analysis.facade.confluence import (
    DEFAULT_CONFLUENCE_TOLERANCE,
)
from strategy_workbench.domain.chart_analysis.facade.geometry import (
    DEFAULT_CHANNEL_LEVELS,
    DEFAULT_FIB_LEVELS,
    LEVEL_CHOICES,
)
from strategy_workbench.domain.chart_analysis.facade.levels import (
    AnchorIssue,
    SwingChoice,
    TrendDirection,
    ZoneSide,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.price_tick import TICK_STEPS
from strategy_workbench.domain.chart_analysis.facade.regime import (
    RegimeReason,
    RegimeVerdict,
    Structure,
)
from strategy_workbench.domain.chart_analysis.facade.series import PivotKind

from ._backtest_contract import CodedBodyValidationRoute

_T = TypeVar("_T")

STOCK_CHART_PATH = "/api/v1/stock-chart"

ChartHttpErrorCode = (
    ChartErrorCode
    | Literal["chart.request_invalid", "chart.instrument.not_found", "chart.line.not_found"]
)


# ── 공통 모양 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChartErrorDetail:
    code: ChartHttpErrorCode
    message: str


@dataclass(frozen=True)
class ChartErrorResponse:
    detail: ChartErrorDetail


@dataclass(frozen=True)
class ChartTickStepBody:
    below: Annotated[
        float | None, Field(description="이 화면 가격 미만에 쓰는 호가. null이면 그 위 전부.")
    ]
    tick: float


@dataclass(frozen=True)
class ChartInstrumentBody:
    kind: InstrumentKind
    code: Annotated[
        str,
        Field(description="종목은 티커, 지수는 `<index_class>:<index_name>`(예: KOSPI:코스피)."),
    ]
    name: str
    group: Annotated[
        str | None,
        Field(description="종목은 증권 종류(common·preferred·etf …), 지수는 시장 구분(KOSPI …)."),
    ]
    tick_steps: Annotated[
        list[ChartTickStepBody],
        Field(description="한 호가 표(가격 구간 오름차순). 키보드로 선을 옮길 때 화면이 읽는다."),
    ]


@dataclass(frozen=True)
class ChartContextBody:
    """응답 공통 머리. `notice`는 연습용 데이터 안내 문장이다(실데이터면 null)."""

    instrument: ChartInstrumentBody
    as_of: Annotated[date, Field(description="원장의 최신 거래일(진행 중 봉 판정의 기준일).")]
    practice_data: bool
    notice: str | None
    current_price: Annotated[
        float | None,
        Field(
            description="현재가 — 기준일까지 원장 마지막 일봉 종가(화면 가격). 보이는 캔들 범위와 "
            "무관하다. 일봉이 없으면 null."
        ),
    ]


@dataclass(frozen=True)
class ChartPointBody:
    bar_date: Annotated[date, Field(description="그 주기 봉의 시작일.")]
    price: Annotated[float, Field(description="화면 가격(저장 좌표 × k).")]


@dataclass(frozen=True)
class ChartLevelPathBody:
    level: Annotated[float | None, Field(description="되돌림·채널 레벨 r. 수평선·띠는 null.")]
    start: Annotated[ChartPointBody, Field(description="기준 봉(첫 기준점)의 점.")]
    end: Annotated[
        ChartPointBody,
        Field(description="마지막 완성 봉의 점. `end.price`가 마지막 완성 봉의 레벨 가격이다."),
    ]


@dataclass(frozen=True)
class ChartDrawingBody:
    anchors: Annotated[
        list[ChartPointBody],
        Field(description="선을 저장할 때 그대로 보내는 기준점(채널은 a·b·폭 기준점 순)."),
    ]
    paths: list[ChartLevelPathBody]


@dataclass(frozen=True)
class ChartWindowBody:
    start: date
    end: date
    bars: int
    source: WindowSource


@dataclass(frozen=True)
class ChartWindowSuggestionBody:
    bars: int
    start: date
    end: date
    default: bool


@dataclass(frozen=True)
class ChartPivotBody:
    kind: PivotKind
    bar_date: date
    price: float
    confirmed_date: Annotated[date, Field(description="이 피벗이 확정된 봉의 날짜.")]


@dataclass(frozen=True)
class ChartRegimeRecordBody:
    verdict: RegimeVerdict
    strength: float | None
    structure: Structure


@dataclass(frozen=True)
class ChartAutoRecordBody:
    """자동 선의 기록. 후보 응답의 `record`를 선 저장 요청에 그대로 싣는다."""

    algorithm_version: str
    params: dict[str, ParamValue]
    regime_window_start: date | None = None
    regime_window_end: date | None = None
    regime: ChartRegimeRecordBody | None = None
    chosen_by: ChosenBy | None = None
    window_source: WindowSource | None = None


# ── 응답 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChartInstrumentSearchBody:
    practice_data: bool
    notice: str | None
    instruments: list[ChartInstrumentBody]


@dataclass(frozen=True)
class ChartCandleBody:
    start: Annotated[date, Field(description="기간의 첫 거래일(봉 날짜).")]
    end: Annotated[date, Field(description="기간의 마지막 거래일.")]
    open: float
    high: float
    low: float
    close: float
    in_progress: Annotated[
        bool, Field(description="기준일이 기간 중간이라 아직 끝나지 않은 봉. 표시만 한다.")
    ]
    change_pct: Annotated[
        float | None,
        Field(description="직전 봉 종가 대비 등락률(%). 계열의 첫 봉은 null."),
    ]


@dataclass(frozen=True)
class ChartCandlesBody:
    context: ChartContextBody
    timeframe: Timeframe
    candles: list[ChartCandleBody]
    last_completed: Annotated[date | None, Field(description="마지막 완성 봉의 날짜.")]


@dataclass(frozen=True)
class ChartRegimeBody:
    context: ChartContextBody
    timeframe: Timeframe
    window: ChartWindowBody
    suggestions: Annotated[
        list[ChartWindowSuggestionBody], Field(description="자동 제안 구간 칩(짧은·기본·긴 순).")
    ]
    verdict: RegimeVerdict
    reason: RegimeReason | None
    strength: Annotated[float | None, Field(description="강도 S. 판정 불가면 null.")]
    box_ratio: Annotated[float | None, Field(description="박스 비율 B. 판정 불가면 null.")]
    structure: Structure
    structure_highs: list[ChartPivotBody]
    structure_lows: list[ChartPivotBody]
    sentence: Annotated[str, Field(description="판정 한 문장(backend가 완성).")]
    starts_near_extreme: bool
    ends_near_extreme: bool
    warnings: list[str]
    channel_direction: Annotated[
        TrendDirection | None, Field(description="추천 채널 방향. 상승·하락 판정만 있다.")
    ]
    rules: Annotated[list[str], Field(description="판정 규칙 설명 문장(backend가 완성).")]
    pivot_rule: Annotated[str, Field(description="피벗 확정 규칙 문장(backend가 완성).")]


@dataclass(frozen=True)
class ChartChannelCandidateBody:
    drawing: ChartDrawingBody
    outside_ratio: Annotated[float, Field(description="기준점~구간 끝 종가 중 채널 밖 비율(0~1).")]
    span_bars: int
    width_atr: Annotated[float, Field(description="채널 폭 ÷ 구간 끝 ATR.")]
    created: Annotated[
        date, Field(description="세 기준점이 모두 확정된 봉의 날짜. 그 앞은 옅게 그린다.")
    ]


@dataclass(frozen=True)
class ChartChannelsBody:
    context: ChartContextBody
    timeframe: Timeframe
    window: ChartWindowBody
    direction: TrendDirection
    recommended_direction: TrendDirection | None
    candidates: Annotated[
        list[ChartChannelCandidateBody],
        Field(description=f"순위순 앞 {MAX_CHANNEL_CANDIDATES}개까지."),
    ]
    total_candidates: int
    anchor_issue: AnchorIssue | None
    anchor_issue_message: str | None
    record: ChartAutoRecordBody
    anchor_choices: Annotated[
        list[ChartPivotBody],
        Field(
            description="첫 점(`first_anchor`)으로 고를 수 있는 점 — 구간 안에서 구간 끝까지 "
            "확정된 그 방향의 피벗(up: 저점, down: 고점)."
        ),
    ]


@dataclass(frozen=True)
class ChartRetracementBody:
    context: ChartContextBody
    timeframe: Timeframe
    window: ChartWindowBody
    swing: SwingChoice
    drawing: Annotated[
        ChartDrawingBody | None,
        Field(description="기준점은 먼저 점(레벨 1)·나중 점(레벨 0) 순. 고를 스윙이 없으면 null."),
    ]
    created: Annotated[
        date | None, Field(description="두 기준점이 모두 확정된 봉의 날짜. 그 앞은 옅게 그린다.")
    ]
    message: str | None
    record: ChartAutoRecordBody


@dataclass(frozen=True)
class ChartZoneBody:
    drawing: Annotated[
        ChartDrawingBody,
        Field(description="수평 띠 선(기준점 둘: 하단·상단, 기준 봉 = 가장 이른 구성 피벗)."),
    ]
    low: float
    high: float
    touches: int
    formed: Annotated[date, Field(description="구간이 생긴 봉의 날짜. 그 앞은 옅게 그린다.")]
    held: int
    broken: int
    side: Annotated[
        ZoneSide,
        Field(
            description="현재가(기준일 종가) 기준 구분. 판정 구간을 과거로 옮겨도 "
            "지금 가격으로 정한다."
        ),
    ]
    members: list[ChartPivotBody]


@dataclass(frozen=True)
class ChartZonesBody:
    context: ChartContextBody
    timeframe: Timeframe
    window: ChartWindowBody
    zones: Annotated[list[ChartZoneBody], Field(description="위쪽 구간(상단 내림차순)부터.")]
    basis: str
    record: ChartAutoRecordBody
    rules: Annotated[
        list[str], Field(description="묶기·접촉·구분·생긴 날 규칙 문장(backend가 완성).")
    ]


@dataclass(frozen=True)
class ChartConfluenceMemberBody:
    ref: Annotated[str, Field(description="저장 선이면 line_id, 요청에 실은 선이면 그 ref.")]
    kind: LineKind
    level: float | None
    low: float
    high: float


@dataclass(frozen=True)
class ChartConfluenceGroupBody:
    low: Annotated[float, Field(description="넓힌 좌표의 중첩 구간 하단(화면 가격).")]
    high: float
    kinds: list[LineKind]
    members: list[ChartConfluenceMemberBody]


@dataclass(frozen=True)
class ChartConfluenceBody:
    context: ChartContextBody
    tolerance: float
    groups: Annotated[list[ChartConfluenceGroupBody], Field(description="낮은 가격부터.")]
    caution: str
    rules: Annotated[list[str], Field(description="중첩을 세는 규칙 문장(backend가 완성).")]
    skipped_line_ids: Annotated[
        list[str], Field(description="기준점이 지금 계열 밖이라 넣지 못한 저장 선.")
    ]


@dataclass(frozen=True)
class ChartLineBody:
    line_id: str
    instrument_kind: InstrumentKind
    instrument_code: str
    timeframe: Annotated[Timeframe, Field(description="선을 만든 주기. 기하는 이 주기로 계산한다.")]
    kind: LineKind
    source: LineSource
    anchors: list[ChartPointBody]
    levels: Annotated[list[float] | None, Field(description="null이면 기본 레벨.")]
    extend_right: bool
    memo: str
    locked: bool
    visible: bool
    auto: ChartAutoRecordBody | None
    created_at: datetime
    updated_at: datetime
    paths: list[ChartLevelPathBody]
    issue: Annotated[
        LineIssue | None, Field(description="지금 계열로 그릴 수 없는 사유(그때 paths가 빈다).")
    ]
    created: Annotated[
        date | None,
        Field(
            description="자동 선의 생긴 날 — 저장한 판정 구간·파라미터로 다시 계산한다(그 앞은 "
            "옅게). 수동 선·기준점을 옮긴 복사본·지금과 다른 알고리즘 버전의 기록은 null."
        ),
    ]


@dataclass(frozen=True)
class ChartLinesBody:
    context: ChartContextBody
    lines: list[ChartLineBody]
    level_choices: Annotated[
        list[float],
        Field(description="화면이 켜고 끌 수 있게 보이는 레벨 칸(기본 레벨 + 선택 레벨 2.618)."),
    ]


# ── 요청 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChartLineEditBody:
    anchors: Annotated[
        list[ChartPointBody],
        Field(description="화면 좌표 기준점. 수평선 1~2개(2개는 띠), 되돌림 2개, 채널 3개."),
    ]
    extend_right: bool = True
    memo: str = ""
    locked: bool = False
    visible: bool = True
    levels: list[float] | None = None


@dataclass(frozen=True)
class ChartLinePreviewBody:
    """저장 전 미리보기 — 저장 요청과 같은 초안의 기하 칸. 저장하지 않는다."""

    instrument_kind: InstrumentKind
    instrument_code: Annotated[str, Field(min_length=1)]
    timeframe: Timeframe
    kind: LineKind
    anchors: list[ChartPointBody]
    levels: list[float] | None = None


@dataclass(frozen=True)
class CreateChartLineBody:
    instrument_kind: InstrumentKind
    instrument_code: Annotated[str, Field(min_length=1)]
    timeframe: Timeframe
    kind: LineKind
    source: LineSource
    anchors: list[ChartPointBody]
    extend_right: bool = True
    memo: str = ""
    locked: bool = False
    visible: bool = True
    levels: list[float] | None = None
    auto: ChartAutoRecordBody | None = None


@dataclass(frozen=True)
class ChartConfluenceLineBody:
    ref: Annotated[str, Field(min_length=1)]
    timeframe: Timeframe
    kind: LineKind
    anchors: list[ChartPointBody]
    levels: list[float] | None = None


@dataclass(frozen=True)
class ChartConfluenceRequestBody:
    """저장 선 중 보이는 것은 서버가 넣는다. `lines`에는 저장하지 않은 보이는 선(자동 후보)만
    싣는다 — 저장한 후보를 다시 실으면(ref = line_id) 그 요청 선은 빠진다."""

    instrument_kind: InstrumentKind
    instrument_code: Annotated[str, Field(min_length=1)]
    lines: list[ChartConfluenceLineBody]
    tolerance: float = DEFAULT_CONFLUENCE_TOLERANCE
    include_mine: Annotated[
        bool,
        Field(description="거짓이면 저장 선 중 내 선(source ≠ auto)을 빼고 자동 선만 넣는다."),
    ] = True


# ── 변환 ─────────────────────────────────────────────────────────────────────


def _instrument(item: ChartInstrument) -> ChartInstrumentBody:
    return ChartInstrumentBody(
        item.ref.kind,
        item.ref.code,
        item.name,
        item.group,
        [ChartTickStepBody(step.below, step.tick) for step in TICK_STEPS[item.tick]],
    )


def _context(context: ChartContext) -> ChartContextBody:
    return ChartContextBody(
        _instrument(context.instrument),
        context.as_of,
        context.practice_data,
        context.notice,
        context.current_price,
    )


def _point(point: ChartPoint) -> ChartPointBody:
    return ChartPointBody(point.bar_date, point.price)


def _paths(paths: Sequence[LevelPath]) -> list[ChartLevelPathBody]:
    return [ChartLevelPathBody(p.level, _point(p.start), _point(p.end)) for p in paths]


def _drawing(drawing: LineDrawing) -> ChartDrawingBody:
    return ChartDrawingBody([_point(a) for a in drawing.anchors], _paths(drawing.paths))


def _window(window: WindowView) -> ChartWindowBody:
    return ChartWindowBody(window.start, window.end, window.bars, window.source)


def _pivot(pivot: PivotPoint) -> ChartPivotBody:
    return ChartPivotBody(pivot.kind, pivot.bar_date, pivot.price, pivot.confirmed_date)


def _record_body(record: AutoRecord) -> ChartAutoRecordBody:
    regime = record.regime
    window = record.regime_window
    return ChartAutoRecordBody(
        algorithm_version=record.algorithm_version,
        params=dict(record.params),
        regime_window_start=None if window is None else window[0],
        regime_window_end=None if window is None else window[1],
        regime=None
        if regime is None
        else ChartRegimeRecordBody(regime.verdict, regime.strength, regime.structure),
        chosen_by=record.chosen_by,
        window_source=record.window_source,
    )


def _record(body: ChartAutoRecordBody) -> AutoRecord:
    start, end = body.regime_window_start, body.regime_window_end
    regime = body.regime
    params: Mapping[str, ParamValue] = dict(body.params)
    return AutoRecord(
        algorithm_version=body.algorithm_version,
        params=params,
        regime_window=None if start is None or end is None else (start, end),
        regime=None
        if regime is None
        else RegimeRecord(regime.verdict, regime.strength, regime.structure),
        chosen_by=body.chosen_by,
        window_source=body.window_source,
    )


def _points(points: Sequence[ChartPointBody]) -> tuple[ChartPoint, ...]:
    return tuple(ChartPoint(point.bar_date, point.price) for point in points)


def _line(view: ChartLineView) -> ChartLineBody:
    line, content = view.line, view.line.content
    return ChartLineBody(
        line_id=line.line_id,
        instrument_kind=line.instrument.kind,
        instrument_code=line.instrument.code,
        timeframe=line.timeframe,
        kind=line.kind,
        source=line.source,
        anchors=[_point(a) for a in view.anchors],
        levels=None if content.levels is None else list(content.levels),
        extend_right=content.extend_right,
        memo=content.memo,
        locked=content.locked,
        visible=content.visible,
        auto=None if line.auto is None else _record_body(line.auto),
        created_at=line.created_at,
        updated_at=line.updated_at,
        paths=_paths(view.paths),
        issue=view.issue,
        created=view.created,
    )


def _search(found: InstrumentSearch) -> ChartInstrumentSearchBody:
    return ChartInstrumentSearchBody(
        found.practice_data, found.notice, [_instrument(i) for i in found.instruments]
    )


def _candles(view: CandleView) -> ChartCandlesBody:
    return ChartCandlesBody(
        context=_context(view.context),
        timeframe=view.timeframe,
        candles=[
            ChartCandleBody(c.start, c.end, c.open, c.high, c.low, c.close, c.in_progress, change)
            for c, change in zip(view.candles, view.change_pct, strict=True)
        ],
        last_completed=view.last_completed,
    )


def _regime(view: RegimeView) -> ChartRegimeBody:
    return ChartRegimeBody(
        context=_context(view.context),
        timeframe=view.timeframe,
        window=_window(view.window),
        suggestions=[
            ChartWindowSuggestionBody(s.bars, s.start, s.end, s.default) for s in view.suggestions
        ],
        verdict=view.judgement.verdict,
        reason=view.judgement.reason,
        strength=view.judgement.strength,
        box_ratio=view.judgement.box_ratio,
        structure=view.judgement.structure,
        structure_highs=[_pivot(p) for p in view.structure_highs],
        structure_lows=[_pivot(p) for p in view.structure_lows],
        sentence=view.sentence,
        starts_near_extreme=view.judgement.starts_near_extreme,
        ends_near_extreme=view.judgement.ends_near_extreme,
        warnings=list(view.warnings),
        channel_direction=view.judgement.channel_direction,
        rules=list(view.rules),
        pivot_rule=view.pivot_rule,
    )


def _channels(view: ChannelView) -> ChartChannelsBody:
    return ChartChannelsBody(
        context=_context(view.context),
        timeframe=view.timeframe,
        window=_window(view.window),
        direction=view.direction,
        recommended_direction=view.recommended_direction,
        candidates=[
            ChartChannelCandidateBody(
                _drawing(c.drawing),
                c.candidate.outside_ratio,
                c.candidate.span_bars,
                c.candidate.width_atr,
                c.created,
            )
            for c in view.candidates
        ],
        total_candidates=view.total_candidates,
        anchor_issue=view.anchor_issue,
        anchor_issue_message=view.anchor_issue_message,
        record=_record_body(view.record),
        anchor_choices=[_pivot(p) for p in view.anchor_choices],
    )


def _retracement(view: RetracementView) -> ChartRetracementBody:
    return ChartRetracementBody(
        context=_context(view.context),
        timeframe=view.timeframe,
        window=_window(view.window),
        swing=view.choice,
        drawing=None if view.drawing is None else _drawing(view.drawing),
        created=view.created,
        message=view.message,
        record=_record_body(view.record),
    )


def _zones(view: ZonesView) -> ChartZonesBody:
    return ChartZonesBody(
        context=_context(view.context),
        timeframe=view.timeframe,
        window=_window(view.window),
        zones=[
            ChartZoneBody(
                drawing=_drawing(z.drawing),
                low=z.low,
                high=z.high,
                touches=z.zone.touches,
                formed=z.formed,
                held=z.zone.held,
                broken=z.zone.broken,
                side=z.side,
                members=[_pivot(p) for p in z.members],
            )
            for z in view.zones
        ],
        basis=view.basis,
        record=_record_body(view.record),
        rules=list(view.rules),
    )


def _confluence(view: ConfluenceView) -> ChartConfluenceBody:
    return ChartConfluenceBody(
        context=_context(view.context),
        tolerance=view.tolerance,
        groups=[
            ChartConfluenceGroupBody(
                g.low,
                g.high,
                list(g.kinds),
                [
                    ChartConfluenceMemberBody(m.ref, m.kind, m.level, m.low, m.high)
                    for m in g.members
                ],
            )
            for g in view.groups
        ],
        caution=view.caution,
        rules=list(view.rules),
        skipped_line_ids=list(view.skipped_line_ids),
    )


def _lines(view: ChartLines) -> ChartLinesBody:
    return ChartLinesBody(
        context=_context(view.context),
        lines=[_line(line) for line in view.lines],
        level_choices=list(LEVEL_CHOICES),
    )


def _edit(body: ChartLineEditBody | CreateChartLineBody) -> LineEdit:
    return LineEdit(
        anchors=_points(body.anchors),
        levels=None if body.levels is None else tuple(body.levels),
        extend_right=body.extend_right,
        memo=body.memo,
        locked=body.locked,
        visible=body.visible,
    )


# ── 라우트 ───────────────────────────────────────────────────────────────────


class _ChartRoute(CodedBodyValidationRoute):
    @staticmethod
    def coded_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        # reason: FastAPI 검증 오류 hook의 공개 JSON 계약을 그대로 받는다.
        first = next(iter(issues), {})
        location = ".".join(str(part) for part in first.get("loc", ()))
        return {
            "code": "chart.request_invalid",
            "message": f"{first.get('msg', 'invalid request')} — field={location or '-'} "
            f"issues={len(issues)}",
        }


def _rejected(error: ChartRequestError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": error.code, "message": str(error)}
    )


def _missing(code: str, error: LookupError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": code, "message": str(error)})


InstrumentKindQuery = Annotated[InstrumentKind, Query(description="stock 또는 index.")]
CodeQuery = Annotated[str, Query(min_length=1, description="종목 티커 또는 지수 코드.")]
WindowStartQuery = Annotated[date | None, Query(description="판정·분석 구간 시작 봉 날짜.")]
WindowEndQuery = Annotated[date | None, Query(description="판정·분석 구간 끝 봉 날짜.")]
LevelsQuery = Annotated[
    list[float] | None,
    Query(
        description="그릴 레벨 목록(되풀이 칸). 없으면 종류별 기본 — 채널 "
        f"{list(DEFAULT_CHANNEL_LEVELS)}, 되돌림 {list(DEFAULT_FIB_LEVELS)}."
    ),
]
SensitivityQuery = Annotated[
    float | None, Query(description="피벗 민감도 k(× ATR). 범위는 domain이 검사한다.")
]


def register_chart_routes(app: FastAPI, service: StockChartService) -> None:
    router = APIRouter(prefix=STOCK_CHART_PATH, route_class=_ChartRoute)
    responses: dict[int | str, dict[str, Any]] = {
        404: {"model": ChartErrorResponse},
        422: {"model": ChartErrorResponse},
    }

    def guarded(call: Callable[[], _T]) -> _T:
        try:
            return call()
        except InstrumentNotFoundError as error:
            raise _missing("chart.instrument.not_found", error) from error
        except ChartLineNotFoundError as error:
            raise _missing("chart.line.not_found", error) from error
        except ChartRequestError as error:
            raise _rejected(error) from error

    @router.get("/instruments", operation_id="searchChartInstruments", responses=responses)
    def search_instruments(
        query: Annotated[str, Query(description="이름·티커·지수명(포함 검색).")],
        limit: Annotated[int, Query(description=f"1 ~ {MAX_SEARCH_RESULTS}.")] = 20,
    ) -> ChartInstrumentSearchBody:
        return _search(guarded(lambda: service.search(query, limit)))

    @router.get("/candles", operation_id="getChartCandles", responses=responses)
    def get_candles(
        kind: InstrumentKindQuery,
        code: CodeQuery,
        timeframe: Timeframe,
        start: date | None = None,
        end: date | None = None,
    ) -> ChartCandlesBody:
        ref = InstrumentRef(kind, code)
        return _candles(guarded(lambda: service.candles(ref, timeframe, start, end)))

    @router.get("/regime", operation_id="getChartRegime", responses=responses)
    def get_regime(
        kind: InstrumentKindQuery,
        code: CodeQuery,
        timeframe: Timeframe,
        window_start: WindowStartQuery = None,
        window_end: WindowEndQuery = None,
        candidate_sensitivity: SensitivityQuery = None,
        structure_sensitivity: SensitivityQuery = None,
    ) -> ChartRegimeBody:
        ref = InstrumentRef(kind, code)
        window = WindowRequest(window_start, window_end)
        swing = SwingSettings(candidate_sensitivity, structure_sensitivity)
        return _regime(guarded(lambda: service.regime(ref, timeframe, window, swing)))

    @router.get("/channels", operation_id="getChartChannels", responses=responses)
    def get_channels(
        kind: InstrumentKindQuery,
        code: CodeQuery,
        timeframe: Timeframe,
        direction: TrendDirection,
        first_anchor: Annotated[
            date | None, Query(description="첫 점(기준점 a) 봉 날짜. 없으면 모든 쌍을 찾는다.")
        ] = None,
        window_start: WindowStartQuery = None,
        window_end: WindowEndQuery = None,
        candidate_sensitivity: SensitivityQuery = None,
        structure_sensitivity: SensitivityQuery = None,
        levels: LevelsQuery = None,
    ) -> ChartChannelsBody:
        ref = InstrumentRef(kind, code)
        window = WindowRequest(window_start, window_end)
        swing = SwingSettings(candidate_sensitivity, structure_sensitivity)
        chosen = None if levels is None else tuple(levels)
        return _channels(
            guarded(
                lambda: service.channels(
                    ref, timeframe, direction, window, first_anchor, swing, chosen
                )
            )
        )

    @router.get("/retracement", operation_id="getChartRetracement", responses=responses)
    def get_retracement(
        kind: InstrumentKindQuery,
        code: CodeQuery,
        timeframe: Timeframe,
        swing: SwingChoice,
        window_start: WindowStartQuery = None,
        window_end: WindowEndQuery = None,
        candidate_sensitivity: SensitivityQuery = None,
        structure_sensitivity: SensitivityQuery = None,
        levels: LevelsQuery = None,
    ) -> ChartRetracementBody:
        ref = InstrumentRef(kind, code)
        window = WindowRequest(window_start, window_end)
        settings = SwingSettings(candidate_sensitivity, structure_sensitivity)
        chosen = None if levels is None else tuple(levels)
        return _retracement(
            guarded(lambda: service.retracement(ref, timeframe, swing, window, settings, chosen))
        )

    @router.get("/zones", operation_id="getChartZones", responses=responses)
    def get_zones(
        kind: InstrumentKindQuery,
        code: CodeQuery,
        timeframe: Timeframe,
        window_start: WindowStartQuery = None,
        window_end: WindowEndQuery = None,
        width_atr: Annotated[
            float | None, Query(description="묶는 폭 k_z(tol = k_z·ATR/종가).")
        ] = None,
        min_touches: int | None = None,
        candidate_sensitivity: SensitivityQuery = None,
        structure_sensitivity: SensitivityQuery = None,
    ) -> ChartZonesBody:
        ref = InstrumentRef(kind, code)
        window = WindowRequest(window_start, window_end)
        swing = SwingSettings(candidate_sensitivity, structure_sensitivity)
        return _zones(
            guarded(lambda: service.zones(ref, timeframe, window, width_atr, min_touches, swing))
        )

    @router.post("/confluence", operation_id="findChartConfluence", responses=responses)
    def find_confluence(body: ChartConfluenceRequestBody) -> ChartConfluenceBody:
        ref = InstrumentRef(body.instrument_kind, body.instrument_code)
        lines = tuple(
            ConfluenceLine(
                line.ref,
                line.timeframe,
                line.kind,
                _points(line.anchors),
                None if line.levels is None else tuple(line.levels),
            )
            for line in body.lines
        )
        return _confluence(
            guarded(
                lambda: service.confluence(
                    ref, lines, body.tolerance, include_mine=body.include_mine
                )
            )
        )

    @router.post("/lines/preview", operation_id="previewChartLine", responses=responses)
    def preview_line(body: ChartLinePreviewBody) -> ChartDrawingBody:
        """저장 전 미리보기: 저장과 같은 검사·봉 시작일 정규화와 그릴 점. 저장하지 않는다 —
        수동 그리기 중 끄는 동안 frontend가 가격을 계산하지 않고 이것을 부른다."""
        shape = LineShape(
            InstrumentRef(body.instrument_kind, body.instrument_code),
            body.timeframe,
            body.kind,
            _points(body.anchors),
            None if body.levels is None else tuple(body.levels),
        )
        return _drawing(guarded(lambda: service.preview_line(shape)))

    @router.get("/lines", operation_id="listChartLines", responses=responses)
    def list_lines(kind: InstrumentKindQuery, code: CodeQuery) -> ChartLinesBody:
        ref = InstrumentRef(kind, code)
        return _lines(guarded(lambda: service.lines(ref)))

    @router.post(
        "/lines",
        operation_id="createChartLine",
        status_code=status.HTTP_201_CREATED,
        responses=responses,
    )
    def create_line(body: CreateChartLineBody) -> ChartLineBody:
        draft = ChartLineDraft(
            instrument=InstrumentRef(body.instrument_kind, body.instrument_code),
            timeframe=body.timeframe,
            kind=body.kind,
            source=body.source,
            edit=_edit(body),
            auto=None if body.auto is None else _record(body.auto),
        )
        return _line(guarded(lambda: service.create_line(draft)))

    @router.put("/lines/{line_id}", operation_id="updateChartLine", responses=responses)
    def update_line(line_id: str, body: ChartLineEditBody) -> ChartLineBody:
        return _line(guarded(lambda: service.update_line(line_id, _edit(body))))

    @router.delete(
        "/lines/{line_id}",
        operation_id="deleteChartLine",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=responses,
    )
    def delete_line(line_id: str) -> None:
        guarded(lambda: service.delete_line(line_id))

    app.include_router(router)
