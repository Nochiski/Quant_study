"""개별종목차트 유스케이스: 검색·캔들·국면 판정·채널·되돌림·수평 구간·중첩·선 저장.

규칙은 `domain.chart_analysis`가 정한다. 여기서는 일봉을 포트로 읽어 묶고
(`aggregate_candles`), 판정 창 날짜를 봉 index로 옮기고(`ChartSeries.index_of`), 저장
좌표(전방 조정 공간)와 화면 가격 사이를 배율 k로 오간다(`DisplayScale`). 응답 가격은 모두
화면 가격이고 저장 가격은 화면 가격 ÷ k다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from strategy_workbench.application.stock_chart._errors import (
    ChartRequestError,
    InstrumentNotFoundError,
)
from strategy_workbench.application.stock_chart._lines import (
    ChartPoint,
    LevelPath,
    LineGeometry,
    check_shape,
    geometry,
    paths,
    resolve,
)
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import (
    AutoRecord,
    ChartDrawingStore,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    LineAnchor,
    ParamValue,
    RegimeRecord,
)
from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import (
    ChartInstrument,
    ChartPricePort,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import (
    Candle,
    DisplayScale,
    PriceBar,
    Timeframe,
    aggregate_candles,
)
from strategy_workbench.domain.chart_analysis.facade.confluence import (
    DEFAULT_CONFLUENCE_TOLERANCE,
    PriceBand,
    bands_of,
    find_confluences,
)
from strategy_workbench.domain.chart_analysis.facade.geometry import DEFAULT_FIB_LEVELS, Anchor
from strategy_workbench.domain.chart_analysis.facade.levels import (
    AnchorIssue,
    ChannelSearch,
    SwingChoice,
    TrendDirection,
    ZoneParams,
    ZoneSide,
    find_channels,
    find_zones,
    retracement_swing,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import (
    WINDOW_PRESETS,
    RegimeReason,
    RegimeVerdict,
    Structure,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.series import (
    ChartSeries,
    Pivot,
    PivotKind,
    SwingParams,
    Window,
    build_series,
)
from strategy_workbench.domain.chart_analysis.facade.wording import (
    CONFLUENCE_CAUTION,
    ENDS_NEAR_EXTREME_WARNING,
    NO_SWING_MESSAGE,
    PRACTICE_DATA_NOTICE,
    STARTS_NEAR_EXTREME_WARNING,
    ZONE_BASIS,
    anchor_issue_message,
    regime_sentence,
)

# 검색 결과 상한과 채널 후보 상한(순위순 앞에서 자른다).
MAX_SEARCH_RESULTS = 50
MAX_CHANNEL_CANDIDATES = 10
# 이름 포함 검색의 같은 순위 안 순서: 지수 → 보통주 → 우선주 → ETF → 그 밖.
_GROUP_ORDER = {"common": 0, "preferred": 1, "etf": 2}


@dataclass(frozen=True)
class ChartContext:
    """응답 공통 머리. `as_of`는 원장 최신 거래일, `notice`는 연습용 데이터 안내다."""

    instrument: ChartInstrument
    as_of: date
    practice_data: bool
    notice: str | None


@dataclass(frozen=True)
class InstrumentSearch:
    practice_data: bool
    notice: str | None
    instruments: tuple[ChartInstrument, ...]


@dataclass(frozen=True)
class CandleView:
    """화면 가격 캔들. 마지막 봉이 진행 중일 수 있다(`Candle.in_progress`, 표시만)."""

    context: ChartContext
    timeframe: Timeframe
    candles: tuple[Candle, ...]
    last_completed: date | None


@dataclass(frozen=True)
class SwingSettings:
    """피벗 민감도(후보용·구조 판정용 k). None이면 domain 기본값이다."""

    candidate_sensitivity: float | None = None
    structure_sensitivity: float | None = None


@dataclass(frozen=True)
class WindowRequest:
    """판정·분석 구간의 시작·끝 봉 날짜. 둘 다 None이면 주기별 기본 구간(가운데 제안 칩)이다."""

    start: date | None = None
    end: date | None = None


DEFAULT_WINDOW = WindowRequest()
DEFAULT_SWING = SwingSettings()


@dataclass(frozen=True)
class WindowView:
    start: date
    end: date
    bars: int
    source: WindowSource


@dataclass(frozen=True)
class WindowSuggestion:
    bars: int
    start: date
    end: date
    default: bool


@dataclass(frozen=True)
class PivotPoint:
    kind: PivotKind
    bar_date: date
    price: float
    confirmed_date: date


@dataclass(frozen=True)
class RegimeView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    suggestions: tuple[WindowSuggestion, ...]
    verdict: RegimeVerdict
    reason: RegimeReason | None
    strength: float | None
    box_ratio: float | None
    structure: Structure
    structure_highs: tuple[PivotPoint, ...]
    structure_lows: tuple[PivotPoint, ...]
    sentence: str
    starts_near_extreme: bool
    ends_near_extreme: bool
    warnings: tuple[str, ...]
    channel_direction: TrendDirection | None


@dataclass(frozen=True)
class LineDrawing:
    """자동 후보 한 줄: 저장에 그대로 쓸 화면 좌표 기준점과 레벨 선을 그릴 점."""

    anchors: tuple[ChartPoint, ...]
    paths: tuple[LevelPath, ...]


@dataclass(frozen=True)
class ChannelCandidateView:
    drawing: LineDrawing
    outside_ratio: float
    span_bars: int
    width_atr: float
    created: date


@dataclass(frozen=True)
class ChannelView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    direction: TrendDirection
    recommended_direction: TrendDirection | None
    candidates: tuple[ChannelCandidateView, ...]
    total_candidates: int
    anchor_issue: AnchorIssue | None
    anchor_issue_message: str | None
    record: AutoRecord


@dataclass(frozen=True)
class RetracementView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    choice: SwingChoice
    drawing: LineDrawing | None
    message: str | None
    record: AutoRecord


@dataclass(frozen=True)
class ZoneView:
    drawing: LineDrawing
    low: float
    high: float
    touches: int
    formed: date
    held: int
    broken: int
    side: ZoneSide
    members: tuple[PivotPoint, ...]


@dataclass(frozen=True)
class ZonesView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    zones: tuple[ZoneView, ...]
    basis: str
    record: AutoRecord


@dataclass(frozen=True)
class LineEdit:
    """선의 고칠 수 있는 칸. 기준점은 화면 좌표(봉 날짜, 화면 가격)다 — 서비스가 ÷k 해서
    저장한다."""

    anchors: tuple[ChartPoint, ...]
    levels: tuple[float, ...] | None = None
    extend_right: bool = True
    memo: str = ""
    locked: bool = False
    visible: bool = True


@dataclass(frozen=True)
class ChartLineDraft:
    instrument: InstrumentRef
    timeframe: Timeframe
    kind: LineKind
    source: LineSource
    edit: LineEdit
    auto: AutoRecord | None = None


@dataclass(frozen=True)
class ChartLineView:
    """저장된 선과 화면 좌표. 기준점이 지금 계열 밖으로 나가 그릴 수 없으면 `issue`가 있고
    그릴 점이 없다."""

    line: ChartLine
    anchors: tuple[ChartPoint, ...]
    paths: tuple[LevelPath, ...]
    issue: str | None


@dataclass(frozen=True)
class ChartLines:
    context: ChartContext
    lines: tuple[ChartLineView, ...]


@dataclass(frozen=True)
class ConfluenceLine:
    """저장하지 않은 보이는 선(자동 후보 등). `ref`는 응답에서 이 선을 가리키는 이름이다."""

    ref: str
    timeframe: Timeframe
    kind: LineKind
    anchors: tuple[ChartPoint, ...]
    levels: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ConfluenceMember:
    ref: str
    kind: LineKind
    level: float | None
    low: float
    high: float


@dataclass(frozen=True)
class ConfluenceGroup:
    low: float
    high: float
    kinds: tuple[LineKind, ...]
    members: tuple[ConfluenceMember, ...]


@dataclass(frozen=True)
class ConfluenceView:
    context: ChartContext
    tolerance: float
    groups: tuple[ConfluenceGroup, ...]
    caution: str
    # 기준점이 지금 계열 밖이라 넣지 못한 저장 선
    skipped_line_ids: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class _Loaded:
    context: ChartContext
    ref: InstrumentRef
    scale: DisplayScale
    bars_as_of: date
    daily: tuple[PriceBar, ...]

    def candles(self, timeframe: Timeframe) -> tuple[Candle, ...]:
        return aggregate_candles(self.daily, timeframe, self.bars_as_of)


class StockChartService:
    def __init__(
        self,
        prices: ChartPricePort,
        drawings: ChartDrawingStore,
        *,
        practice_data: bool,
        new_id: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self._prices = prices
        self._drawings = drawings
        self._practice = practice_data
        self._new_id = new_id
        self._now = now

    # ── 검색·캔들 ────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> InstrumentSearch:
        """순위는 `rank_instruments`가 정한다."""
        found = rank_instruments(self._prices.chart_instruments(), query)
        return InstrumentSearch(self._practice, self._notice(), found[: max(0, limit)])

    def candles(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        start: date | None = None,
        end: date | None = None,
    ) -> CandleView:
        loaded = self._load(ref)
        candles = loaded.candles(timeframe)
        completed = [c for c in candles if not c.in_progress]
        shown = tuple(
            _display_candle(loaded.scale, c)
            for c in candles
            if (start is None or c.end >= start) and (end is None or c.start <= end)
        )
        return CandleView(
            loaded.context, timeframe, shown, completed[-1].start if completed else None
        )

    # ── 분석 ─────────────────────────────────────────────────────────────────

    def regime(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        window: WindowRequest = DEFAULT_WINDOW,
        swing: SwingSettings = DEFAULT_SWING,
    ) -> RegimeView:
        loaded = self._load(ref)
        series = self._series(loaded, timeframe, swing)
        span, view = self._window(series, timeframe, window)
        judgement = judge_regime(series, span)
        warnings = (STARTS_NEAR_EXTREME_WARNING,) * judgement.starts_near_extreme + (
            ENDS_NEAR_EXTREME_WARNING,
        ) * judgement.ends_near_extreme
        return RegimeView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            suggestions=_suggestions(series, timeframe),
            verdict=judgement.verdict,
            reason=judgement.reason,
            strength=judgement.strength,
            box_ratio=judgement.box_ratio,
            structure=judgement.structure,
            structure_highs=tuple(
                _pivot(series, loaded.scale, p) for p in judgement.structure_highs
            ),
            structure_lows=tuple(_pivot(series, loaded.scale, p) for p in judgement.structure_lows),
            sentence=regime_sentence(judgement),
            starts_near_extreme=judgement.starts_near_extreme,
            ends_near_extreme=judgement.ends_near_extreme,
            warnings=warnings,
            channel_direction=judgement.channel_direction,
        )

    def channels(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        direction: TrendDirection,
        window: WindowRequest = DEFAULT_WINDOW,
        first_anchor: date | None = None,
        swing: SwingSettings = DEFAULT_SWING,
    ) -> ChannelView:
        loaded = self._load(ref)
        series = self._series(loaded, timeframe, swing)
        span, view = self._window(series, timeframe, window)
        judgement = judge_regime(series, span)
        recommended = judgement.channel_direction
        if first_anchor is None:
            search = find_channels(series, span, direction)
        elif (anchor_index := series.index_of(first_anchor)) is None:
            # 계열 밖 날짜는 구간 밖이기도 하다(구간은 계열 안에 있다).
            search = ChannelSearch((), AnchorIssue.OUTSIDE_WINDOW)
        else:
            search = find_channels(series, span, direction, anchor_index)
        candidates = tuple(
            ChannelCandidateView(
                drawing=_drawing(
                    series,
                    loaded.scale,
                    LineKind.FIB_CHANNEL,
                    (c.first.anchor, c.second.anchor, c.width_pivot.anchor),
                ),
                outside_ratio=c.outside_closes / c.span_bars,
                span_bars=c.span_bars,
                width_atr=c.width_atr,
                created=series.bar_date(c.created_index),
            )
            for c in search.candidates[:MAX_CHANNEL_CANDIDATES]
        )
        issue = search.anchor_issue
        params: dict[str, ParamValue] = {
            "direction": direction.value,
            "first_anchor": None if first_anchor is None else first_anchor.isoformat(),
            **_swing_params(series.params),
        }
        return ChannelView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            direction=direction,
            recommended_direction=recommended,
            candidates=candidates,
            total_candidates=len(search.candidates),
            anchor_issue=issue,
            anchor_issue_message=None if issue is None else anchor_issue_message(issue),
            record=AutoRecord(
                algorithm_version=ALGORITHM_VERSION,
                params=params,
                regime_window=(view.start, view.end),
                regime=RegimeRecord(judgement.verdict, judgement.strength, judgement.structure),
                chosen_by=(
                    ChosenBy.RECOMMENDATION if direction is recommended else ChosenBy.USER_OVERRIDE
                ),
                window_source=view.source,
            ),
        )

    def retracement(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        choice: SwingChoice,
        window: WindowRequest = DEFAULT_WINDOW,
        swing: SwingSettings = DEFAULT_SWING,
    ) -> RetracementView:
        loaded = self._load(ref)
        series = self._series(loaded, timeframe, swing)
        span, view = self._window(series, timeframe, window)
        found = retracement_swing(series, span, choice)
        drawing = (
            None
            if found is None
            else _drawing(
                series, loaded.scale, LineKind.FIB_RETRACEMENT, (found.first, found.second)
            )
        )
        return RetracementView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            choice=choice,
            drawing=drawing,
            message=NO_SWING_MESSAGE if found is None else None,
            record=_record(view, {"swing": choice.value, **_swing_params(series.params)}),
        )

    def zones(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        window: WindowRequest = DEFAULT_WINDOW,
        width_atr: float | None = None,
        min_touches: int | None = None,
        swing: SwingSettings = DEFAULT_SWING,
    ) -> ZonesView:
        loaded = self._load(ref)
        series = self._series(loaded, timeframe, swing)
        span, view = self._window(series, timeframe, window)
        defaults = ZoneParams()
        try:
            params = ZoneParams(
                defaults.width_atr if width_atr is None else width_atr,
                defaults.min_touches if min_touches is None else min_touches,
            )
        except ValueError as error:
            raise ChartRequestError("chart.params.invalid", str(error)) from error
        scale = loaded.scale
        zones = tuple(
            ZoneView(
                drawing=_drawing(
                    series,
                    scale,
                    LineKind.HLINE,
                    _band_anchors(zone.members, zone.low, zone.high),
                ),
                low=scale.to_display(zone.low),
                high=scale.to_display(zone.high),
                touches=zone.touches,
                formed=series.bar_date(zone.formed_index),
                held=zone.held,
                broken=zone.broken,
                side=zone.side,
                members=tuple(_pivot(series, scale, p) for p in zone.members),
            )
            for zone in find_zones(series, span, params)
        )
        return ZonesView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            zones=zones,
            basis=ZONE_BASIS,
            record=_record(
                view,
                {
                    "width_atr": params.width_atr,
                    "min_touches": params.min_touches,
                    **_swing_params(series.params),
                },
            ),
        )

    def confluence(
        self,
        ref: InstrumentRef,
        lines: Sequence[ConfluenceLine] = (),
        tolerance: float = DEFAULT_CONFLUENCE_TOLERANCE,
    ) -> ConfluenceView:
        """보이는 선(저장 선 중 `visible`인 것 + 요청에 실은 저장 안 한 선)의 중첩.

        보이기는 저장 칸이라 저장 선은 서버가 고른다. 계산은 저장 좌표에서 하고(비율 넓히기라 배율과
        무관하다) 결과를 화면 가격으로 옮긴다.
        """
        refs = [line.ref for line in lines]
        if len(set(refs)) != len(refs):
            raise ChartRequestError(
                "chart.confluence.duplicate_ref", f"inline line refs must be unique — refs={refs}"
            )
        loaded = self._load(ref)
        series_by: dict[Timeframe, ChartSeries | None] = {}
        bands: list[PriceBand] = []
        skipped: list[str] = []
        for saved in self._drawings.list_lines(ref):
            if not saved.content.visible:
                continue
            series = self._cached_series(loaded, saved.timeframe, series_by)
            resolved = None if series is None else _resolved(series, saved)
            if series is None or resolved is None:
                skipped.append(saved.line_id)
                continue
            shape, _ = resolved
            bands += bands_of(series, shape, saved.line_id, _levels(saved.content.levels))
        for inline in lines:
            series = self._cached_series(loaded, inline.timeframe, series_by)
            if series is None:
                raise ChartRequestError(
                    "chart.series.no_completed_bars",
                    f"no completed candles — ref={inline.ref} timeframe={inline.timeframe.value}",
                )
            anchors = _stored_anchors(loaded.scale, inline.anchors)
            shape, _ = self._shape(series, inline.kind, anchors, inline.levels, inline.ref)
            bands += bands_of(series, shape, inline.ref, _levels(inline.levels))
        try:
            found = find_confluences(bands, tolerance)
        except ValueError as error:
            raise ChartRequestError("chart.params.invalid", str(error)) from error
        scale = loaded.scale
        return ConfluenceView(
            context=loaded.context,
            tolerance=tolerance,
            groups=tuple(
                ConfluenceGroup(
                    low=scale.to_display(group.low),
                    high=scale.to_display(group.high),
                    kinds=tuple(kind for kind in LineKind if kind in group.kinds),
                    members=tuple(
                        ConfluenceMember(
                            band.ref,
                            band.kind,
                            band.level,
                            scale.to_display(band.low),
                            scale.to_display(band.high),
                        )
                        for band in group.members
                    ),
                )
                for group in found
            ),
            caution=CONFLUENCE_CAUTION,
            skipped_line_ids=tuple(skipped),
        )

    # ── 선 저장 ───────────────────────────────────────────────────────────────

    def lines(self, ref: InstrumentRef) -> ChartLines:
        loaded = self._load(ref)
        series_by: dict[Timeframe, ChartSeries | None] = {}
        return ChartLines(
            loaded.context,
            tuple(
                self._line_view(loaded, line, series_by) for line in self._drawings.list_lines(ref)
            ),
        )

    def create_line(self, draft: ChartLineDraft) -> ChartLineView:
        _check_record(draft.source, draft.auto)
        loaded = self._load(draft.instrument)
        series = self._series(loaded, draft.timeframe)
        content = self._content(loaded, series, draft.kind, draft.edit)
        now = self._now()
        line = ChartLine(
            line_id=self._new_id(),
            instrument=draft.instrument,
            timeframe=draft.timeframe,
            kind=draft.kind,
            source=draft.source,
            content=content,
            auto=draft.auto,
            created_at=now,
            updated_at=now,
        )
        self._drawings.add_line(line)
        return self._line_view(loaded, line, {draft.timeframe: series})

    def update_line(self, line_id: str, edit: LineEdit) -> ChartLineView:
        current = self._drawings.get_line(line_id)
        if current is None:
            raise ChartLineNotFoundError(line_id)
        loaded = self._load(current.instrument)
        series = self._series(loaded, current.timeframe)
        content = self._content(loaded, series, current.kind, edit)
        line = self._drawings.update_content(line_id, content, self._now())
        return self._line_view(loaded, line, {current.timeframe: series})

    def delete_line(self, line_id: str) -> None:
        self._drawings.delete_line(line_id)

    # ── 조립 ─────────────────────────────────────────────────────────────────

    def _notice(self) -> str | None:
        return PRACTICE_DATA_NOTICE if self._practice else None

    def _load(self, ref: InstrumentRef) -> _Loaded:
        instrument = next(
            (item for item in self._prices.chart_instruments() if item.ref == ref), None
        )
        if instrument is None:
            raise InstrumentNotFoundError(ref)
        as_of = self._prices.latest_session()
        history = None if as_of is None else self._prices.chart_history(ref, as_of)
        if as_of is None or history is None:
            raise InstrumentNotFoundError(ref)
        bars = history.bars
        scale = DisplayScale(1.0)
        if ref.kind is InstrumentKind.STOCK and bars and history.last_actual_close is not None:
            scale = DisplayScale.from_last_close(history.last_actual_close, bars[-1].close)
        return _Loaded(
            ChartContext(instrument, as_of, self._practice, self._notice()), ref, scale, as_of, bars
        )

    def _series(
        self, loaded: _Loaded, timeframe: Timeframe, swing: SwingSettings = DEFAULT_SWING
    ) -> ChartSeries:
        defaults = SwingParams()
        try:
            params = SwingParams(
                candidate_sensitivity=_or(
                    swing.candidate_sensitivity, defaults.candidate_sensitivity
                ),
                structure_sensitivity=_or(
                    swing.structure_sensitivity, defaults.structure_sensitivity
                ),
            )
        except ValueError as error:
            raise ChartRequestError("chart.params.invalid", str(error)) from error
        candles = loaded.candles(timeframe)
        if not any(not candle.in_progress for candle in candles):
            raise ChartRequestError(
                "chart.series.no_completed_bars",
                f"no completed candles — kind={loaded.ref.kind.value} code={loaded.ref.code} "
                f"timeframe={timeframe.value} candles={len(candles)}",
            )
        return build_series(candles, params)

    def _cached_series(
        self, loaded: _Loaded, timeframe: Timeframe, cache: dict[Timeframe, ChartSeries | None]
    ) -> ChartSeries | None:
        if timeframe not in cache:
            try:
                cache[timeframe] = self._series(loaded, timeframe)
            except ChartRequestError:
                cache[timeframe] = None
        return cache[timeframe]

    def _window(
        self, series: ChartSeries, timeframe: Timeframe, request: WindowRequest
    ) -> tuple[Window, WindowView]:
        last = len(series.candles) - 1
        if request.start is None and request.end is None:
            span = Window.ending_at(last, WINDOW_PRESETS[timeframe].default_bars)
        elif request.start is None or request.end is None:
            raise ChartRequestError(
                "chart.window.incomplete",
                f"window needs both start and end — start={request.start} end={request.end}",
            )
        else:
            start, end = series.index_of(request.start), series.index_of(request.end)
            if start is None or end is None:
                raise ChartRequestError(
                    "chart.window.outside_series",
                    f"window date outside completed candles — start={request.start} "
                    f"end={request.end} first={series.bar_date(0)} last={series.bar_date(last)} "
                    f"timeframe={timeframe.value}",
                )
            if start > end:
                raise ChartRequestError(
                    "chart.window.reversed",
                    f"window start after end — start={request.start} end={request.end}",
                )
            span = Window(start, end)
        presets = {
            Window.ending_at(last, bars) for bars in WINDOW_PRESETS[timeframe].suggested_bars
        }
        source = WindowSource.PRESET if span in presets else WindowSource.USER
        return span, WindowView(
            series.bar_date(span.start), series.bar_date(span.end), span.bars, source
        )

    def _shape(
        self,
        series: ChartSeries,
        kind: LineKind,
        anchors: tuple[LineAnchor, ...],
        levels: tuple[float, ...] | None,
        label: str,
    ) -> tuple[LineGeometry, tuple[LineAnchor, ...]]:
        """기준점을 검사해 domain 기하와 봉 시작일로 맞춘 저장 기준점을 돌려준다."""
        check_shape(kind, anchors, levels)
        resolved = resolve(series, anchors)
        if resolved is None:
            raise ChartRequestError(
                "chart.line.anchor_outside_series",
                f"anchor date outside completed candles — line={label} "
                f"anchors={[a.bar_date.isoformat() for a in anchors]} "
                f"first={series.bar_date(0)} last={series.bar_date(len(series.candles) - 1)}",
            )
        shape = geometry(kind, resolved)
        normalized = tuple(LineAnchor(series.bar_date(a.index), a.price) for a in resolved)
        return shape, normalized

    def _content(
        self, loaded: _Loaded, series: ChartSeries, kind: LineKind, edit: LineEdit
    ) -> ChartLineContent:
        anchors = _stored_anchors(loaded.scale, edit.anchors)
        _, normalized = self._shape(series, kind, anchors, edit.levels, "draft")
        return ChartLineContent(
            anchors=normalized,
            levels=edit.levels,
            extend_right=edit.extend_right,
            memo=edit.memo,
            locked=edit.locked,
            visible=edit.visible,
        )

    def _line_view(
        self, loaded: _Loaded, line: ChartLine, cache: dict[Timeframe, ChartSeries | None]
    ) -> ChartLineView:
        scale = loaded.scale
        anchors = tuple(
            ChartPoint(a.bar_date, scale.to_display(a.price)) for a in line.content.anchors
        )
        series = self._cached_series(loaded, line.timeframe, cache)
        resolved = None if series is None else _resolved(series, line)
        if series is None or resolved is None:
            return ChartLineView(line, anchors, (), "anchor_outside_series")
        shape, indexed = resolved
        return ChartLineView(
            line, anchors, paths(series, scale, shape, indexed, line.content.levels), None
        )


def rank_instruments(
    instruments: Sequence[ChartInstrument], query: str
) -> tuple[ChartInstrument, ...]:
    """검색 순위: 지수명 정확 → 티커 정확 → 이름(정식 이름 포함) 정확 → 이름 포함. 대소문자 무시."""
    needle = query.strip().casefold()
    if not needle:
        return ()

    def names(item: ChartInstrument) -> tuple[str, ...]:
        return tuple(name.casefold() for name in (item.name, *item.aliases))

    def tier(item: ChartInstrument) -> int | None:
        if item.ref.kind is InstrumentKind.INDEX and item.name.casefold() == needle:
            return 0
        if item.ref.kind is InstrumentKind.STOCK and item.ref.code.casefold() == needle:
            return 1
        if needle in names(item):
            return 2
        if any(needle in name for name in names(item)):
            return 3
        return None

    ranked = [(t, item) for item in instruments if (t := tier(item)) is not None]
    ranked.sort(
        key=lambda pair: (
            pair[0],
            pair[1].ref.kind is not InstrumentKind.INDEX,
            _GROUP_ORDER.get(pair[1].group or "", len(_GROUP_ORDER)),
            pair[1].name,
            pair[1].ref.code,
        )
    )
    return tuple(item for _, item in ranked[:MAX_SEARCH_RESULTS])


def _display_candle(scale: DisplayScale, candle: Candle) -> Candle:
    return Candle(
        candle.start,
        candle.end,
        scale.to_display(candle.open),
        scale.to_display(candle.high),
        scale.to_display(candle.low),
        scale.to_display(candle.close),
        candle.in_progress,
    )


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


def _stored_anchors(scale: DisplayScale, points: Sequence[ChartPoint]) -> tuple[LineAnchor, ...]:
    return tuple(LineAnchor(point.bar_date, scale.to_stored(point.price)) for point in points)


def _resolved(
    series: ChartSeries, line: ChartLine
) -> tuple[LineGeometry, tuple[Anchor, ...]] | None:
    indexed = resolve(series, line.content.anchors)
    if indexed is None:
        return None
    try:
        return geometry(line.kind, indexed), indexed
    except ChartRequestError:
        # 저장 뒤 봉 묶음이 바뀌어 두 기준점이 같은 봉이 됐다 — 그릴 수 없다.
        return None


def _levels(levels: tuple[float, ...] | None) -> tuple[float, ...]:
    return DEFAULT_FIB_LEVELS if levels is None else levels


def _drawing(
    series: ChartSeries, scale: DisplayScale, kind: LineKind, anchors: Sequence[Anchor]
) -> LineDrawing:
    """자동 후보의 저장용 화면 기준점과 기본 레벨의 그릴 점."""
    return LineDrawing(
        tuple(ChartPoint(series.bar_date(a.index), scale.to_display(a.price)) for a in anchors),
        paths(series, scale, geometry(kind, anchors), anchors, None),
    )


def _band_anchors(members: Sequence[Pivot], low: float, high: float) -> tuple[Anchor, Anchor]:
    """수평 구간을 띠 선(기준점 둘)으로 — 기준 봉은 가장 이른 구성 피벗이다."""
    start = min(p.index for p in members)
    return Anchor(start, low), Anchor(start, high)


def _pivot(series: ChartSeries, scale: DisplayScale, pivot: Pivot) -> PivotPoint:
    return PivotPoint(
        pivot.kind,
        series.bar_date(pivot.index),
        scale.to_display(pivot.price),
        series.bar_date(pivot.confirmed_index),
    )


def _suggestions(series: ChartSeries, timeframe: Timeframe) -> tuple[WindowSuggestion, ...]:
    last = len(series.candles) - 1
    preset = WINDOW_PRESETS[timeframe]
    return tuple(
        WindowSuggestion(
            bars,
            series.bar_date((span := Window.ending_at(last, bars)).start),
            series.bar_date(span.end),
            bars == preset.default_bars,
        )
        for bars in preset.suggested_bars
    )


def _swing_params(params: SwingParams) -> dict[str, ParamValue]:
    return {
        "candidate_sensitivity": params.candidate_sensitivity,
        "structure_sensitivity": params.structure_sensitivity,
    }


def _record(view: WindowView, params: Mapping[str, ParamValue]) -> AutoRecord:
    return AutoRecord(
        algorithm_version=ALGORITHM_VERSION,
        params=dict(params),
        regime_window=(view.start, view.end),
        window_source=view.source,
    )


def _check_record(source: LineSource, auto: AutoRecord | None) -> None:
    if (source is LineSource.MANUAL) != (auto is None):
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record must be present exactly for auto lines — source={source.value} "
            f"auto={'present' if auto is not None else 'missing'}",
        )
