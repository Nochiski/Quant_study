"""개별종목차트 유스케이스: 검색·캔들·국면 판정·채널·되돌림·수평 구간·중첩·선 미리보기·저장.

규칙은 `domain.chart_analysis`가 정한다. 여기서는 일봉을 포트로 읽어 묶고
(`aggregate_candles`), 판정 창 날짜를 봉 index로 옮기고(`ChartSeries.index_of`), 저장
좌표(전방 조정 공간)와 화면 가격 사이를 배율 k로 오간다(`DisplayScale`). 응답 가격은 모두
화면 가격이고 저장 가격은 화면 가격 ÷ k다. 묶은 계열은 대상·주기·민감도마다 캐시한다(포트는
사는 동안 같은 답을 준다) — 수동 그리기 중 미리보기가 원장을 다시 읽지 않는다.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from threading import Lock
from typing import Generic, TypeVar

from strategy_workbench.application.stock_chart._errors import (
    ChartRequestError,
    InstrumentNotFoundError,
    LineIssue,
)
from strategy_workbench.application.stock_chart._lines import (
    ChartPoint,
    LineGeometry,
    check_levels,
    check_shape,
    geometry,
    paths,
    resolve,
)
from strategy_workbench.application.stock_chart._provenance import (
    AutoParams,
    channel_anchors,
    created_index,
    retracement_anchors,
    retracement_created,
    same,
    zone_anchors,
)
from strategy_workbench.application.stock_chart._views import (
    DEFAULT_SWING,
    DEFAULT_WINDOW,
    CandleView,
    ChannelCandidateView,
    ChannelView,
    ChartContext,
    ChartLineDraft,
    ChartLines,
    ChartLineView,
    ConfluenceGroup,
    ConfluenceLine,
    ConfluenceMember,
    ConfluenceView,
    InstrumentSearch,
    LineDrawing,
    LineEdit,
    LineShape,
    PivotPoint,
    RegimeView,
    RetracementView,
    SwingSettings,
    WindowRequest,
    WindowSuggestion,
    WindowView,
    ZonesView,
    ZoneView,
)
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import (
    AutoRecord,
    ChartDrawingStore,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    LineAnchor,
    RegimeRecord,
)
from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import (
    PRACTICE_CODE_PREFIX,
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
from strategy_workbench.domain.chart_analysis.facade.geometry import Anchor
from strategy_workbench.domain.chart_analysis.facade.levels import (
    AnchorIssue,
    ChannelSearch,
    SwingChoice,
    TrendDirection,
    ZoneParams,
    find_channels,
    find_zones,
    first_anchor_choices,
    retracement_pivots,
    zone_side,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
    chosen_by,
    levels_or_default,
)
from strategy_workbench.domain.chart_analysis.facade.regime import (
    WINDOW_PRESETS,
    RegimeJudgement,
    judge_regime,
)
from strategy_workbench.domain.chart_analysis.facade.series import (
    ChartSeries,
    Pivot,
    SwingParams,
    Window,
    build_series,
)
from strategy_workbench.domain.chart_analysis.facade.wording import (
    CONFLUENCE_CAUTION,
    ENDS_NEAR_EXTREME_WARNING,
    NO_SWING_MESSAGE,
    REGIME_RULES,
    STARTS_NEAR_EXTREME_WARNING,
    ZONE_BASIS,
    anchor_issue_message,
    confluence_rules,
    pivot_rule,
    regime_sentence,
    zone_rules,
)

# 데이터 원천 안내라 분석 규칙(domain)이 아니라 이 유스케이스가 문장의 owner다.
PRACTICE_DATA_NOTICE = (
    "연습용 데이터예요. 실제 시세가 아니라 화면을 익히려고 만든 가격이라 분석 결과를 "
    "투자 판단에 쓰지 마세요."
)
# 검색 결과 상한, 채널 후보 상한(순위순 앞에서 자른다), 메모 글자 수 상한.
MAX_SEARCH_RESULTS = 50
MAX_CHANNEL_CANDIDATES = 10
MAX_MEMO_CHARS = 500
# 묶은 계열 캐시의 칸 수(대상 × 주기 × 민감도). 넘치면 가장 오래 안 쓴 것부터 버린다.
SERIES_CACHE_SIZE = 32
# 이름 포함 검색의 같은 순위 안 순서: 지수 → 보통주 → 우선주 → ETF → 그 밖.
_GROUP_ORDER = {"common": 0, "preferred": 1, "etf": 2}


@dataclass(frozen=True, eq=False)
class _Loaded:
    context: ChartContext
    ref: InstrumentRef
    scale: DisplayScale
    daily: tuple[PriceBar, ...]
    # 현재가(기준일까지 원장 마지막 일봉 종가)의 저장 공간 값.
    # 응답 머리 `current_price`는 이 값의 화면 가격이다.
    current_close: float | None


@dataclass(frozen=True, eq=False)
class _Frame:
    """한 주기의 분석 계열과, 있으면 진행 중 봉(기준점·판정 창이 될 수 없는 기간)."""

    series: ChartSeries
    in_progress: Candle | None


_K = TypeVar("_K")
_V = TypeVar("_V")
_T = TypeVar("_T")
_P = TypeVar("_P", SwingParams, ZoneParams)


class _Lru(Generic[_K, _V]):
    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[_K, _V] = OrderedDict()
        self._lock = Lock()

    def get(self, key: _K, build: Callable[[], _V]) -> _V:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = build()  # 같은 키를 두 스레드가 함께 만들어도 결과가 같다(포트가 같은 답을 준다)
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return value


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
        self._loaded: _Lru[InstrumentRef, _Loaded] = _Lru(SERIES_CACHE_SIZE)
        self._frames: _Lru[tuple[InstrumentRef, Timeframe, SwingParams], _Frame | None] = _Lru(
            SERIES_CACHE_SIZE
        )

    # ── 검색·캔들 ────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> InstrumentSearch:
        """순위는 `rank_instruments`가 정한다. `limit`은 1 ~ `MAX_SEARCH_RESULTS`."""
        if not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ChartRequestError(
                "chart.params.invalid",
                f"limit out of range — limit={limit} allowed=1..{MAX_SEARCH_RESULTS}",
            )
        found = rank_instruments(self._instruments(), query)
        return InstrumentSearch(self._practice, self._notice(), found[:limit])

    def candles(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        start: date | None = None,
        end: date | None = None,
    ) -> CandleView:
        if start is not None and end is not None and start > end:
            raise ChartRequestError(
                "chart.window.reversed", f"candle range start after end — start={start} end={end}"
            )
        loaded = self._load(ref)
        candles = aggregate_candles(loaded.daily, timeframe, loaded.context.as_of)
        completed = [c for c in candles if not c.in_progress]
        shown = [
            index
            for index, c in enumerate(candles)
            if (start is None or c.end >= start) and (end is None or c.start <= end)
        ]
        return CandleView(
            loaded.context,
            timeframe,
            tuple(_display_candle(loaded.scale, candles[index]) for index in shown),
            completed[-1].start if completed else None,
            tuple(_change_pct(candles, index) for index in shown),
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
        series = self._required_frame(loaded, timeframe, swing).series
        span, view = _window(series, timeframe, window)
        judgement = judge_regime(series, span)
        return RegimeView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            suggestions=tuple(
                WindowSuggestion(
                    bars,
                    series.bar_date(w.start),
                    series.bar_date(w.end),
                    bars == WINDOW_PRESETS[timeframe].default_bars,
                )
                for bars, w in _presets(series, timeframe)
            ),
            judgement=judgement,
            structure_highs=_pivots(series, loaded.scale, judgement.structure_highs),
            structure_lows=_pivots(series, loaded.scale, judgement.structure_lows),
            sentence=regime_sentence(judgement),
            warnings=_warnings(judgement),
            rules=REGIME_RULES,
            pivot_rule=pivot_rule(series.params),
        )

    def channels(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        direction: TrendDirection,
        window: WindowRequest = DEFAULT_WINDOW,
        first_anchor: date | None = None,
        swing: SwingSettings = DEFAULT_SWING,
        levels: tuple[float, ...] | None = None,
    ) -> ChannelView:
        check_levels(levels)
        loaded = self._load(ref)
        series = self._required_frame(loaded, timeframe, swing).series
        span, view = _window(series, timeframe, window)
        judgement = judge_regime(series, span)
        if first_anchor is None:
            search = find_channels(series, span, direction)
        elif (anchor_index := series.index_of(first_anchor)) is None:
            # 계열 밖 날짜는 구간 밖이기도 하다(구간은 계열 안에 있다).
            search = ChannelSearch((), AnchorIssue.OUTSIDE_WINDOW)
        else:
            search = find_channels(series, span, direction, anchor_index)
        issue = search.anchor_issue
        return ChannelView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            direction=direction,
            recommended_direction=judgement.channel_direction,
            candidates=tuple(
                ChannelCandidateView(
                    _drawing(
                        series, loaded.scale, LineKind.FIB_CHANNEL, channel_anchors(c), levels
                    ),
                    c,
                    series.bar_date(c.created_index),
                )
                for c in search.candidates[:MAX_CHANNEL_CANDIDATES]
            ),
            total_candidates=len(search.candidates),
            anchor_issue=issue,
            anchor_issue_message=None if issue is None else anchor_issue_message(issue),
            record=_record(
                view,
                replace(
                    _auto_params(series.params), direction=direction, first_anchor=first_anchor
                ),
                regime=RegimeRecord(judgement.verdict, judgement.strength, judgement.structure),
                chosen=chosen_by(judgement.channel_direction, direction),
            ),
            anchor_choices=_pivots(
                series, loaded.scale, first_anchor_choices(series, span, direction)
            ),
        )

    def retracement(
        self,
        ref: InstrumentRef,
        timeframe: Timeframe,
        choice: SwingChoice,
        window: WindowRequest = DEFAULT_WINDOW,
        swing: SwingSettings = DEFAULT_SWING,
        levels: tuple[float, ...] | None = None,
    ) -> RetracementView:
        check_levels(levels)
        loaded = self._load(ref)
        series = self._required_frame(loaded, timeframe, swing).series
        span, view = _window(series, timeframe, window)
        pair = retracement_pivots(series, span, choice)
        return RetracementView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            choice=choice,
            drawing=None
            if pair is None
            else _drawing(
                series, loaded.scale, LineKind.FIB_RETRACEMENT, retracement_anchors(pair), levels
            ),
            created=None if pair is None else series.bar_date(retracement_created(pair)),
            message=NO_SWING_MESSAGE if pair is None else None,
            record=_record(view, replace(_auto_params(series.params), swing=choice)),
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
        series = self._required_frame(loaded, timeframe, swing).series
        span, view = _window(series, timeframe, window)
        params = _domain_params(ZoneParams(), width_atr=width_atr, min_touches=min_touches)
        scale = loaded.scale
        # 구간과 같은 저장 가격 공간에서 현재가와 비교한다
        current = loaded.current_close
        if current is None:
            # 완성 봉이 있는 계열(`_required_frame`)이면 일봉도 있다 — 적재 불변식이 깨진 것이다
            raise RuntimeError(f"chart has completed bars but no current close — ref={ref}")
        zones = tuple(
            ZoneView(
                drawing=_drawing(series, scale, LineKind.HLINE, zone_anchors(zone)),
                zone=zone,
                low=scale.to_display(zone.low),
                high=scale.to_display(zone.high),
                formed=series.bar_date(zone.formed_index),
                members=_pivots(series, scale, zone.members),
                side=zone_side(zone, current),
            )
            for zone in find_zones(series, span, params)
        )
        return ZonesView(
            context=loaded.context,
            timeframe=timeframe,
            window=view,
            zones=zones,
            basis=ZONE_BASIS,
            rules=zone_rules(params, series.params),
            record=_record(
                view,
                replace(
                    _auto_params(series.params),
                    width_atr=params.width_atr,
                    min_touches=params.min_touches,
                ),
            ),
        )

    def confluence(
        self,
        ref: InstrumentRef,
        lines: Sequence[ConfluenceLine] = (),
        tolerance: float = DEFAULT_CONFLUENCE_TOLERANCE,
        *,
        include_mine: bool = True,
    ) -> ConfluenceView:
        """보이는 선(저장 선 중 `visible`인 것 + 요청에 실은 저장 안 한 선)의 중첩.

        `include_mine`이 거짓이면 저장 선 중 내 선(손으로 그었거나 복사한 선 — `source`가 auto가
        아닌 것)은 빼고 자동 선만 넣는다. 보이기는 저장 칸이라 "내 선 빼고 보기"를 위해 선을 숨겨
        저장하지 않게 하려는 칸이다.

        보이기는 저장 칸이라 저장 선은 서버가 고른다. 요청 ref가 저장 선 id와 같으면(저장한 후보를
        다시 실었다) 그 요청 선은 빼서 두 번 세지 않는다. 계산은 저장 좌표에서 하고(비율 넓히기라
        배율과 무관하다) 결과를 화면 가격으로 옮긴다.
        """
        refs = [line.ref for line in lines]
        if len(set(refs)) != len(refs):
            raise ChartRequestError(
                "chart.confluence.duplicate_ref", f"inline line refs must be unique — refs={refs}"
            )
        loaded = self._load(ref)
        saved = self._drawings.list_lines(ref)
        saved_ids = {line.line_id for line in saved}
        bands: list[PriceBand] = []
        skipped: list[str] = []
        for line in saved:
            mine = line.source is not LineSource.AUTO
            if not line.content.visible or (mine and not include_mine):
                continue
            frame = self._frame(loaded, line.timeframe, SwingParams())
            resolved = (
                LineIssue.NO_COMPLETED_BARS if frame is None else _resolved(frame.series, line)
            )
            if frame is None or isinstance(resolved, LineIssue):
                skipped.append(line.line_id)
                continue
            shape, _ = resolved
            bands += bands_of(
                frame.series,
                shape,
                line.line_id,
                line.content.levels,
            )
        for inline in lines:
            if inline.ref in saved_ids:
                continue
            frame = self._required_frame(loaded, inline.timeframe)
            shape, _, _ = self._shape(
                loaded, frame, inline.kind, inline.anchors, inline.levels, inline.ref
            )
            bands += bands_of(frame.series, shape, inline.ref, inline.levels)
        found = _domain_call(lambda: find_confluences(bands, tolerance))
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
            rules=confluence_rules(tolerance),
            skipped_line_ids=tuple(skipped),
        )

    # ── 선 미리보기·저장 ──────────────────────────────────────────────────────

    def preview_line(self, shape: LineShape) -> LineDrawing:
        """저장 전 미리보기 — 저장과 같은 검사·정규화(봉 시작일)·그릴 점, 저장소는 거치지 않는다.

        수동 그리기 중 끄는 동안 frontend가 가격을 계산하지 않고 이것을 부른다.
        """
        loaded = self._load(shape.instrument)
        frame = self._required_frame(loaded, shape.timeframe)
        geometry_, stored, indexed = self._shape(
            loaded, frame, shape.kind, shape.anchors, shape.levels, "preview"
        )
        return LineDrawing(
            _display_points(loaded.scale, stored),
            paths(
                frame.series,
                loaded.scale,
                geometry_,
                indexed,
                levels_or_default(shape.kind, shape.levels),
            ),
        )

    def lines(self, ref: InstrumentRef) -> ChartLines:
        loaded = self._load(ref)
        return ChartLines(
            loaded.context,
            tuple(self._line_view(loaded, line) for line in self._drawings.list_lines(ref)),
        )

    def create_line(self, draft: ChartLineDraft) -> ChartLineView:
        _check_record(draft.kind, draft.source, draft.auto)
        loaded = self._load(draft.instrument)
        frame = self._required_frame(loaded, draft.timeframe)
        _, stored, _ = self._shape(
            loaded, frame, draft.kind, draft.edit.anchors, draft.edit.levels, "draft"
        )
        content = self._content(stored, draft.edit)
        if draft.source is LineSource.AUTO:
            same_line = self._same_auto_line(draft, content)
            if same_line is not None:
                if not same_line.content.visible:
                    same_line = self._drawings.update_content(
                        same_line.line_id, content, self._now()
                    )
                return self._line_view(loaded, same_line)
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
        return self._line_view(loaded, line)

    def update_line(self, line_id: str, edit: LineEdit) -> ChartLineView:
        """`source = auto` 선의 기준점은 바꿀 수 없다 — 기준점이 자동 기록(알고리즘·구간·파라미터)의
        결과라 옮기면 기록이 거짓이 된다. 옮기려면 `auto_copied`로 새로 저장한다."""
        current = self._owned(line_id)
        loaded = self._load(current.instrument)
        frame = self._required_frame(loaded, current.timeframe)
        _, stored, indexed = self._shape(
            loaded, frame, current.kind, edit.anchors, edit.levels, line_id
        )
        if current.source is LineSource.AUTO:
            before = resolve(frame.series, current.content.anchors)
            if isinstance(before, date) or not same(before, indexed):
                raise ChartRequestError(
                    "chart.line.auto_anchors_fixed",
                    f"auto line anchors cannot move — line_id={line_id} stored="
                    f"{[(a.bar_date.isoformat(), a.price) for a in current.content.anchors]}",
                )
        content = self._content(stored, edit)
        line = self._drawings.update_content(line_id, content, self._now())
        return self._line_view(loaded, line)

    def _same_auto_line(self, draft: ChartLineDraft, content: ChartLineContent) -> ChartLine | None:
        """저장 내용이 완전히 같은 자동 선. 주기·종류·기준점·레벨·연장·메모·잠금·자동 기록
        (알고리즘·파라미터·판정 구간·판정·방향 출처) 전부가 같고 보이기만 다를 수 있다.

        묶음 추가를 다시 보내도(부분 실패 뒤 재시도) 같은 띠가 두 줄 생기지 않게 그 선을 돌려준다.
        하나라도 다르면 None — 레벨이나 조건을 바꿔 다시 만든 선은 새 선이다."""
        shown = replace(content, visible=True)
        return next(
            (
                line
                for line in self._drawings.list_lines(draft.instrument)
                if line.source is LineSource.AUTO
                and line.timeframe is draft.timeframe
                and line.kind is draft.kind
                and line.auto == draft.auto
                and replace(line.content, visible=True) == shown
            ),
            None,
        )

    def delete_line(self, line_id: str) -> None:
        self._drawings.delete_line(self._owned(line_id).line_id)

    def _owned(self, line_id: str) -> ChartLine:
        """이 모드(연습·실데이터)의 선. 다른 모드 대상의 선은 없는 선과 같다 — 보지도 고치지도
        지우지도 못한다."""
        line = self._drawings.get_line(line_id)
        if line is None or not self._mine(line.instrument):
            raise ChartLineNotFoundError(line_id)
        return line

    def _mine(self, ref: InstrumentRef) -> bool:
        return ref.code.startswith(PRACTICE_CODE_PREFIX) == self._practice

    # ── 조립 ─────────────────────────────────────────────────────────────────

    def _notice(self) -> str | None:
        return PRACTICE_DATA_NOTICE if self._practice else None

    def _instruments(self) -> tuple[ChartInstrument, ...]:
        """이 모드의 대상만 — 연습용 접두가 모드와 맞지 않는 대상은 원천이 실어도 보이지 않는다."""
        return tuple(item for item in self._prices.chart_instruments() if self._mine(item.ref))

    def _load(self, ref: InstrumentRef) -> _Loaded:
        return self._loaded.get(ref, lambda: self._read(ref))

    def _read(self, ref: InstrumentRef) -> _Loaded:
        instrument = next((item for item in self._instruments() if item.ref == ref), None)
        if instrument is None:
            raise InstrumentNotFoundError(ref)
        as_of = self._prices.confirmed_session()
        if as_of is None:
            raise ChartRequestError(
                "chart.series.no_completed_bars",
                f"ledger has no confirmed session — kind={ref.kind.value} code={ref.code}",
            )
        history = self._prices.chart_history(ref, as_of)
        if history is None:
            raise InstrumentNotFoundError(ref)
        bars = history.bars
        scale = DisplayScale(1.0)
        if ref.kind is InstrumentKind.STOCK and bars and history.last_actual_close is not None:
            scale = DisplayScale.from_last_close(history.last_actual_close, bars[-1].close)
        current_close = bars[-1].close if bars else None
        current = None if current_close is None else scale.to_display(current_close)
        return _Loaded(
            ChartContext(instrument, as_of, self._practice, self._notice(), current),
            ref,
            scale,
            bars,
            current_close,
        )

    def _frame(self, loaded: _Loaded, timeframe: Timeframe, params: SwingParams) -> _Frame | None:
        """완성 봉이 없으면 None."""

        def build() -> _Frame | None:
            candles = aggregate_candles(loaded.daily, timeframe, loaded.context.as_of)
            if all(candle.in_progress for candle in candles):
                return None
            last = candles[-1]
            return _Frame(build_series(candles, params), last if last.in_progress else None)

        return self._frames.get((loaded.ref, timeframe, params), build)

    def _required_frame(
        self, loaded: _Loaded, timeframe: Timeframe, swing: SwingSettings = DEFAULT_SWING
    ) -> _Frame:
        params = _domain_params(
            SwingParams(),
            candidate_sensitivity=swing.candidate_sensitivity,
            structure_sensitivity=swing.structure_sensitivity,
        )
        frame = self._frame(loaded, timeframe, params)
        if frame is None:
            raise ChartRequestError(
                "chart.series.no_completed_bars",
                f"no completed candles — kind={loaded.ref.kind.value} code={loaded.ref.code} "
                f"timeframe={timeframe.value}",
            )
        return frame

    def _shape(
        self,
        loaded: _Loaded,
        frame: _Frame,
        kind: LineKind,
        points: Sequence[ChartPoint],
        levels: tuple[float, ...] | None,
        label: str,
    ) -> tuple[LineGeometry, tuple[LineAnchor, ...], tuple[Anchor, ...]]:
        """화면 좌표 기준점을 검사해 (기하, 봉 시작일로 맞춘 저장 기준점, 봉 index 기준점)으로."""
        series = frame.series
        anchors = tuple(LineAnchor(p.bar_date, loaded.scale.to_stored(p.price)) for p in points)
        check_shape(kind, anchors, levels)
        indexed = resolve(series, anchors)
        if isinstance(indexed, date):
            if frame.in_progress is not None and indexed >= frame.in_progress.period_start:
                raise ChartRequestError(
                    "chart.line.anchor_in_progress",
                    f"anchor date is in the bar still in progress — line={label} "
                    f"bar_date={indexed} in_progress_start={frame.in_progress.start}",
                )
            raise ChartRequestError(
                "chart.line.anchor_outside_series",
                f"anchor date outside completed candles — line={label} "
                f"bar_date={indexed} first={series.bar_date(0)} "
                f"last={series.bar_date(len(series.candles) - 1)}",
            )
        shape = geometry(kind, indexed)
        stored = tuple(LineAnchor(series.bar_date(a.index), a.price) for a in indexed)
        return shape, stored, indexed

    @staticmethod
    def _content(stored: tuple[LineAnchor, ...], edit: LineEdit) -> ChartLineContent:
        if len(edit.memo) > MAX_MEMO_CHARS:
            raise ChartRequestError(
                "chart.line.memo_too_long",
                f"memo too long — chars={len(edit.memo)} max={MAX_MEMO_CHARS}",
            )
        return ChartLineContent(
            anchors=stored,
            levels=edit.levels,
            extend_right=edit.extend_right,
            memo=edit.memo,
            locked=edit.locked,
            visible=edit.visible,
        )

    def _line_view(self, loaded: _Loaded, line: ChartLine) -> ChartLineView:
        scale = loaded.scale
        anchors = _display_points(scale, line.content.anchors)
        frame = self._frame(loaded, line.timeframe, SwingParams())
        if frame is None:
            return ChartLineView(line, anchors, (), LineIssue.NO_COMPLETED_BARS, None)
        resolved = _resolved(frame.series, line)
        if isinstance(resolved, LineIssue):
            return ChartLineView(line, anchors, (), resolved, None)
        shape, indexed = resolved
        return ChartLineView(
            line,
            anchors,
            paths(
                frame.series,
                scale,
                shape,
                indexed,
                levels_or_default(line.kind, line.content.levels),
            ),
            None,
            self._created(loaded, line, indexed),
        )

    def _created(
        self, loaded: _Loaded, line: ChartLine, indexed: tuple[Anchor, ...]
    ) -> date | None:
        """자동 선의 생긴 날 — 저장한 민감도·판정 구간으로 다시 찾아 같은 후보의 확정 봉."""
        auto = line.auto
        # 다른 알고리즘 버전의 기록은 이 판의 규칙으로 다시 찾으면 다른 결과가 나올 수 있다
        if (
            auto is None
            or auto.regime_window is None
            or auto.algorithm_version != ALGORITHM_VERSION
        ):
            return None
        params = AutoParams.read(auto.params)
        if params is None:
            return None
        frame = self._frame(loaded, line.timeframe, params.swing_params)
        if frame is None:
            return None
        series = frame.series
        start, end = (series.index_of(day) for day in auto.regime_window)
        if start is None or end is None or start > end:
            return None
        found = created_index(series, Window(start, end), line.kind, indexed, params)
        return None if found is None else series.bar_date(found)


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
        code = item.ref.code.removeprefix(PRACTICE_CODE_PREFIX)
        if item.ref.kind is InstrumentKind.STOCK and code.casefold() == needle:
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
    return tuple(item for _, item in ranked)


def _domain_params(defaults: _P, **values: float | int | None) -> _P:
    """None이 아닌 값만 domain 기본값 위에 얹는다. 범위 검사는 domain 값 타입 하나가 한다."""
    return _domain_call(
        lambda: replace(defaults, **{k: v for k, v in values.items() if v is not None})
    )


def _domain_call(call: Callable[[], _T]) -> _T:
    """domain 파라미터 범위 위반(`ValueError`)을 `chart.params.invalid`로."""
    try:
        return call()
    except ValueError as error:
        raise ChartRequestError("chart.params.invalid", str(error)) from error


def _presets(series: ChartSeries, timeframe: Timeframe) -> tuple[tuple[int, Window], ...]:
    """주기별 제안 칩 길이와 마지막 완성 봉에서 끝나는 그 구간."""
    last = len(series.candles) - 1
    return tuple(
        (bars, Window.ending_at(last, bars)) for bars in WINDOW_PRESETS[timeframe].suggested_bars
    )


def _window(
    series: ChartSeries, timeframe: Timeframe, request: WindowRequest
) -> tuple[Window, WindowView]:
    """구간 날짜 → 봉 index. 한쪽만 오면 거절한다(조용히 기본 구간으로 바꾸지 않는다)."""
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
    source = (
        WindowSource.PRESET
        if any(span == w for _, w in _presets(series, timeframe))
        else WindowSource.USER
    )
    return span, WindowView(
        series.bar_date(span.start), series.bar_date(span.end), span.bars, source
    )


def _warnings(judgement: RegimeJudgement) -> tuple[str, ...]:
    return tuple(
        text
        for flagged, text in (
            (judgement.starts_near_extreme, STARTS_NEAR_EXTREME_WARNING),
            (judgement.ends_near_extreme, ENDS_NEAR_EXTREME_WARNING),
        )
        if flagged
    )


def _change_pct(candles: Sequence[Candle], index: int) -> float | None:
    """직전 봉 종가 대비 등락률(%). 배율 k는 비율에서 지워지므로 저장 좌표로 계산한다."""
    if index == 0 or candles[index - 1].close == 0:
        return None
    return (candles[index].close / candles[index - 1].close - 1) * 100


def _display_candle(scale: DisplayScale, candle: Candle) -> Candle:
    return replace(
        candle,
        open=scale.to_display(candle.open),
        high=scale.to_display(candle.high),
        low=scale.to_display(candle.low),
        close=scale.to_display(candle.close),
    )


def _display_points(scale: DisplayScale, anchors: Sequence[LineAnchor]) -> tuple[ChartPoint, ...]:
    return tuple(ChartPoint(a.bar_date, scale.to_display(a.price)) for a in anchors)


def _resolved(
    series: ChartSeries, line: ChartLine
) -> tuple[LineGeometry, tuple[Anchor, ...]] | LineIssue:
    indexed = resolve(series, line.content.anchors)
    if isinstance(indexed, date):
        return LineIssue.ANCHOR_OUTSIDE_SERIES
    try:
        return geometry(line.kind, indexed), indexed
    except ChartRequestError:
        # 저장 뒤 봉 묶음이 바뀌어 두 기준점이 같은 봉·역순이 됐다 — 그릴 수 없다.
        return LineIssue.ANCHOR_ORDER


def _drawing(
    series: ChartSeries,
    scale: DisplayScale,
    kind: LineKind,
    anchors: Sequence[Anchor],
    levels: tuple[float, ...] | None = None,
) -> LineDrawing:
    """자동 후보의 저장용 화면 기준점과 요청 레벨(없으면 종류별 기본)의 그릴 점."""
    return LineDrawing(
        tuple(ChartPoint(series.bar_date(a.index), scale.to_display(a.price)) for a in anchors),
        paths(series, scale, geometry(kind, anchors), anchors, levels_or_default(kind, levels)),
    )


def _auto_params(swing: SwingParams) -> AutoParams:
    return AutoParams(swing.candidate_sensitivity, swing.structure_sensitivity)


def _pivots(
    series: ChartSeries, scale: DisplayScale, pivots: Sequence[Pivot]
) -> tuple[PivotPoint, ...]:
    return tuple(
        PivotPoint(
            p.kind,
            series.bar_date(p.index),
            scale.to_display(p.price),
            series.bar_date(p.confirmed_index),
        )
        for p in pivots
    )


def _record(
    view: WindowView,
    params: AutoParams,
    *,
    regime: RegimeRecord | None = None,
    chosen: ChosenBy | None = None,
) -> AutoRecord:
    """자동 후보의 기록 — 판정 구간과 파라미터(저장 요청에 그대로 싣는다)."""
    return AutoRecord(
        algorithm_version=ALGORITHM_VERSION,
        params=params.to_params(),
        regime_window=(view.start, view.end),
        regime=regime,
        chosen_by=chosen,
        window_source=view.source,
    )


def _check_record(kind: LineKind, source: LineSource, auto: AutoRecord | None) -> None:
    """자동 기록은 자동 선에만 있고, 수치 칸은 유한해야 한다(NaN·inf는 저장·JSON이 받지 못한다)."""
    if (source is LineSource.MANUAL) != (auto is None):
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record must be present exactly for auto lines — source={source.value} "
            f"auto={'present' if auto is not None else 'missing'}",
        )
    if auto is None:
        return
    if auto.algorithm_version != ALGORITHM_VERSION:
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record from another algorithm version — got={auto.algorithm_version!r} "
            f"current={ALGORITHM_VERSION!r}",
        )
    # 판정(국면)과 방향 출처는 채널 추천의 기록이다 — 되돌림·수평 구간에는 없다
    if kind is not LineKind.FIB_CHANNEL and (auto.regime is not None or auto.chosen_by is not None):
        raise ChartRequestError(
            "chart.line.auto_record",
            f"regime and chosen_by belong to channel records only — kind={kind.value}",
        )
    bad = [
        key
        for key, value in auto.params.items()
        if isinstance(value, float) and not math.isfinite(value)
    ]
    strength = None if auto.regime is None else auto.regime.strength
    if bad or (strength is not None and not math.isfinite(strength)):
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record numbers must be finite — params={bad} regime_strength={strength}",
        )
    if auto.regime_window is not None and auto.regime_window[0] > auto.regime_window[1]:
        raise ChartRequestError(
            "chart.line.auto_record", f"regime window reversed — window={auto.regime_window}"
        )
