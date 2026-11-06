"""개별종목차트 서비스: 날짜 ↔ 봉 index, 진행 중 봉, 화면 ↔ 저장 가격(k), 선 저장 왕복, 중첩 입력.

손 픽스처: 2025-09-01(월) ~ 2025-10-30(목) 평일 44개, 조정 종가 = 100 + i, 시가 = 종가 − 0.5, 고가
= 종가 + 1, 저가 = 종가 − 1. 마지막 실제 종가는 조정 종가의 절반이라 k = 0.5다(화면 = 저장 × 0.5).
기준일 10-30이 목요일이라 주봉의 마지막 주(10-27~)는 진행 중이다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from strategy_workbench.application.stock_chart.facade.chart import (
    ChartLineDraft,
    ChartPoint,
    ChartRequestError,
    ConfluenceLine,
    InstrumentNotFoundError,
    LineEdit,
    StockChartService,
    WindowRequest,
)
from strategy_workbench.application.stock_chart.facade.ports import (
    AutoRecord,
    ChartHistory,
    ChartInstrument,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar, Timeframe
from strategy_workbench.domain.chart_analysis.facade.levels import TrendDirection
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeReason, RegimeVerdict

STOCK = InstrumentRef(InstrumentKind.STOCK, "005930")
UPTREND = InstrumentRef(InstrumentKind.STOCK, "000660")
AS_OF = date(2025, 10, 30)
_START = date(2025, 9, 1)
SESSIONS = tuple(
    day
    for offset in range((AS_OF - _START).days + 1)
    if (day := _START + timedelta(days=offset)).weekday() < 5
)


def _bars(closes: list[float]) -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(day, close - 0.5, close + 1.0, close - 1.0, close)
        for day, close in zip(SESSIONS, closes, strict=True)
    )


def _zigzag() -> list[float]:
    """7봉 +2·3봉 −2 반복 — 높아지는 고점·저점이 큰 스윙(2×ATR)으로 확정된다."""
    closes, close = [], 100.0
    for i in range(len(SESSIONS)):
        close += 2.0 if i % 10 < 7 else -2.0
        closes.append(close)
    return closes


class FakePrices:
    def __init__(self) -> None:
        self.history = {
            STOCK: ChartHistory(_bars([100.0 + i for i in range(len(SESSIONS))]), 143.0 / 2),
            UPTREND: ChartHistory(_bars(_zigzag()), _zigzag()[-1]),
        }

    def latest_session(self) -> date | None:
        return AS_OF

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        return (
            ChartInstrument(STOCK, "삼성전자", "common"),
            ChartInstrument(UPTREND, "SK하이닉스", "common"),
            ChartInstrument(
                InstrumentRef(InstrumentKind.STOCK, "005935"), "삼성전자우", "preferred"
            ),
            ChartInstrument(
                InstrumentRef(InstrumentKind.STOCK, "035420"), "NAVER", "common", ("네이버보통주",)
            ),
            ChartInstrument(InstrumentRef(InstrumentKind.INDEX, "KOSPI:코스피 200"), "코스피 200"),
            ChartInstrument(InstrumentRef(InstrumentKind.INDEX, "KOSPI:코스피"), "코스피", "KOSPI"),
        )

    def chart_history(self, instrument: InstrumentRef, end: date) -> ChartHistory | None:
        return self.history.get(instrument)


class FakeDrawings:
    def __init__(self) -> None:
        self.lines: dict[str, ChartLine] = {}

    def list_lines(self, instrument: InstrumentRef) -> tuple[ChartLine, ...]:
        return tuple(line for line in self.lines.values() if line.instrument == instrument)

    def get_line(self, line_id: str) -> ChartLine | None:
        return self.lines.get(line_id)

    def add_line(self, line: ChartLine) -> None:
        self.lines[line.line_id] = line

    def update_content(
        self, line_id: str, content: ChartLineContent, updated_at: datetime
    ) -> ChartLine:
        if line_id not in self.lines:
            raise ChartLineNotFoundError(line_id)
        self.lines[line_id] = replace(self.lines[line_id], content=content, updated_at=updated_at)
        return self.lines[line_id]

    def delete_line(self, line_id: str) -> None:
        if self.lines.pop(line_id, None) is None:
            raise ChartLineNotFoundError(line_id)


def _service(drawings: FakeDrawings | None = None) -> StockChartService:
    ids = iter(f"line-{n}" for n in range(1, 100))
    return StockChartService(
        FakePrices(),
        drawings or FakeDrawings(),
        practice_data=False,
        new_id=lambda: next(ids),
        now=lambda: datetime(2025, 10, 31, tzinfo=UTC),
    )


def _hline(price: float, day: date = date(2025, 9, 3), *, visible: bool = True) -> ChartLineDraft:
    return ChartLineDraft(
        STOCK,
        Timeframe.DAY,
        LineKind.HLINE,
        LineSource.MANUAL,
        LineEdit((ChartPoint(day, price),), visible=visible),
    )


def test_candles_and_lines_show_stored_prices_times_k() -> None:
    drawings = FakeDrawings()
    service = _service(drawings)

    candles = service.candles(STOCK, Timeframe.DAY).candles
    assert (candles[0].open, candles[0].high) == (49.75, 50.5)
    assert candles[-1].close == 71.5

    view = service.create_line(_hline(60.0))
    # 화면 60 → 저장 120(÷k). 다시 읽으면 화면 60이다.
    assert drawings.lines["line-1"].content.anchors[0].price == 120.0
    assert view.anchors == (ChartPoint(date(2025, 9, 3), 60.0),)
    (path,) = service.lines(STOCK).lines[0].paths
    assert (path.level, path.start, path.end) == (
        None,
        ChartPoint(date(2025, 9, 3), 60.0),
        ChartPoint(AS_OF, 60.0),
    )


def test_the_week_in_progress_is_shown_but_not_analysed() -> None:
    service = _service()
    view = service.candles(STOCK, Timeframe.WEEK)
    assert [c.start for c in view.candles][-2:] == [date(2025, 10, 20), date(2025, 10, 27)]
    assert view.candles[-1].in_progress and not view.candles[-2].in_progress
    assert view.last_completed == date(2025, 10, 20)

    # 기본 판정 창(52주)은 마지막 완성 주에서 끝나고 계열 앞에서 잘린다 — 완성 주 8개
    regime = service.regime(STOCK, Timeframe.WEEK)
    assert (regime.window.start, regime.window.end, regime.window.bars) == (
        date(2025, 9, 1),
        date(2025, 10, 20),
        8,
    )
    assert regime.window.source is WindowSource.PRESET
    assert (regime.verdict, regime.reason, regime.strength) == (
        RegimeVerdict.UNDECIDABLE,
        RegimeReason.TOO_FEW_BARS,
        None,
    )
    assert "(N=8)" in regime.sentence
    # 진행 중 주의 날짜는 판정 창이 될 수 없다
    with pytest.raises(ChartRequestError) as error:
        service.regime(STOCK, Timeframe.WEEK, WindowRequest(date(2025, 9, 1), date(2025, 10, 28)))
    assert error.value.code == "chart.window.outside_series"


def test_window_dates_map_to_the_bar_that_holds_them() -> None:
    service = _service()
    # 토요일 09-06은 그 앞 봉(금 09-05)이다. 39봉이라 제안 칩 길이가 아니다 — 사용자 구간
    view = service.regime(
        STOCK, Timeframe.DAY, WindowRequest(date(2025, 9, 6), date(2025, 10, 29))
    ).window
    assert (view.start, view.end, view.bars, view.source) == (
        date(2025, 9, 5),
        date(2025, 10, 29),
        39,
        WindowSource.USER,
    )
    # 계열 전체(44봉)는 120봉 칩이 앞에서 잘린 모양과 같다 — 제안 칩
    whole = service.regime(STOCK, Timeframe.DAY, WindowRequest(date(2025, 9, 1), AS_OF)).window
    assert (whole.bars, whole.source) == (44, WindowSource.PRESET)

    for window, code in (
        (WindowRequest(date(2025, 9, 1), None), "chart.window.incomplete"),
        (WindowRequest(date(2025, 8, 29), AS_OF), "chart.window.outside_series"),
        (WindowRequest(date(2025, 10, 1), date(2025, 9, 30)), "chart.window.reversed"),
    ):
        with pytest.raises(ChartRequestError) as error:
            service.regime(STOCK, Timeframe.DAY, window)
        assert error.value.code == code


def test_channel_direction_records_whether_the_user_followed_the_recommendation() -> None:
    service = _service()
    assert service.regime(UPTREND, Timeframe.DAY).channel_direction is TrendDirection.UP

    followed = service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP)
    overridden = service.channels(UPTREND, Timeframe.DAY, TrendDirection.DOWN)
    assert followed.record.chosen_by is ChosenBy.RECOMMENDATION
    assert overridden.record.chosen_by is ChosenBy.USER_OVERRIDE
    assert followed.record.algorithm_version == ALGORITHM_VERSION
    assert followed.record.regime_window == (date(2025, 9, 1), AS_OF)
    assert followed.candidates
    # 계열 밖 첫 점은 구간 밖이다
    outside = service.channels(
        UPTREND, Timeframe.DAY, TrendDirection.UP, first_anchor=date(2025, 8, 1)
    )
    assert (outside.candidates, outside.anchor_issue_message is not None) == ((), True)


def test_line_round_trip_normalizes_anchor_dates_and_keeps_editable_fields() -> None:
    drawings = FakeDrawings()
    service = _service(drawings)
    draft = ChartLineDraft(
        STOCK,
        Timeframe.WEEK,
        LineKind.FIB_RETRACEMENT,
        LineSource.MANUAL,
        # 수요일 날짜는 그 주 봉의 시작일(월)로 저장된다
        LineEdit((ChartPoint(date(2025, 9, 3), 50.0), ChartPoint(date(2025, 10, 8), 70.0))),
    )
    created = service.create_line(draft)
    stored = drawings.lines[created.line.line_id].content.anchors
    assert [(a.bar_date, a.price) for a in stored] == [
        (date(2025, 9, 1), 100.0),
        (date(2025, 10, 6), 140.0),
    ]
    # 레벨 0.5 = P0 + 0.5·(P1 − P0) = 70 + 0.5·(50 − 70) = 60. 주봉의 마지막 완성 봉까지 잇는다
    half = next(p for p in created.paths if p.level == 0.5)
    assert (half.start, half.end) == (
        ChartPoint(date(2025, 9, 1), 60.0),
        ChartPoint(date(2025, 10, 20), 60.0),
    )

    edit = LineEdit(created.anchors, levels=(0.5,), memo="눌림 목표", visible=False)
    updated = service.update_line(created.line.line_id, edit)
    assert (updated.line.content.memo, updated.line.content.visible) == ("눌림 목표", False)
    assert [p.level for p in updated.paths] == [0.5]

    service.delete_line(created.line.line_id)
    assert service.lines(STOCK).lines == ()
    with pytest.raises(ChartLineNotFoundError):
        service.delete_line(created.line.line_id)
    with pytest.raises(ChartLineNotFoundError):
        service.update_line(created.line.line_id, edit)


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (
            ChartLineDraft(
                STOCK,
                Timeframe.DAY,
                LineKind.FIB_RETRACEMENT,
                LineSource.MANUAL,
                LineEdit((ChartPoint(date(2025, 9, 3), 50.0),)),
            ),
            "chart.line.anchor_count",
        ),
        (
            ChartLineDraft(
                STOCK,
                Timeframe.DAY,
                LineKind.FIB_RETRACEMENT,
                LineSource.MANUAL,
                LineEdit((ChartPoint(date(2025, 10, 1), 50.0), ChartPoint(date(2025, 9, 3), 70.0))),
            ),
            "chart.line.anchor_order",
        ),
        (_hline(60.0, date(2025, 8, 29)), "chart.line.anchor_outside_series"),
        (_hline(0.0), "chart.line.price_invalid"),
        (
            replace(_hline(60.0), auto=AutoRecord(algorithm_version=ALGORITHM_VERSION, params={})),
            "chart.line.auto_record",
        ),
        (replace(_hline(60.0), source=LineSource.AUTO), "chart.line.auto_record"),
    ],
)
def test_lines_that_cannot_be_drawn_are_rejected(draft: ChartLineDraft, code: str) -> None:
    drawings = FakeDrawings()
    with pytest.raises(ChartRequestError) as error:
        _service(drawings).create_line(draft)
    assert error.value.code == code
    assert drawings.lines == {}


def test_confluence_reads_visible_saved_lines_and_inline_candidates() -> None:
    service = _service()
    shown = service.create_line(_hline(60.0)).line.line_id
    # 숨긴 선은 되돌림 레벨 0.236(= 70 − 20·0.236 = 65.28)과 겹쳐도 넣지 않는다
    service.create_line(_hline(65.28, visible=False))
    candidate = ConfluenceLine(
        "auto-1",
        Timeframe.DAY,
        LineKind.FIB_RETRACEMENT,
        (ChartPoint(date(2025, 9, 1), 50.0), ChartPoint(date(2025, 10, 1), 70.0)),
    )

    (group,) = service.confluence(STOCK, (candidate,)).groups
    # 레벨 0.5 = 60과 수평선 60이 ±0.5%로 넓혀져 [59.7, 60.3]에서 겹친다(화면 가격)
    assert (group.low, group.high) == (pytest.approx(59.7), pytest.approx(60.3))
    assert group.kinds == (LineKind.HLINE, LineKind.FIB_RETRACEMENT)
    assert sorted((m.ref, m.level) for m in group.members) == [
        ("auto-1", 0.5),
        (shown, None),
    ]
    with pytest.raises(ChartRequestError) as error:
        service.confluence(STOCK, (candidate, candidate))
    assert error.value.code == "chart.confluence.duplicate_ref"


def test_search_ranks_index_name_then_ticker_then_exact_name_then_contains() -> None:
    service = _service()

    def codes(query: str) -> list[str]:
        return [item.ref.code for item in service.search(query).instruments]

    assert codes("코스피") == ["KOSPI:코스피", "KOSPI:코스피 200"]
    assert codes("005930") == ["005930"]
    # 정확한 이름이 먼저, 포함은 그 뒤(같은 순위에서는 보통주 → 우선주)
    assert codes("삼성전자") == ["005930", "005935"]
    assert codes("삼성") == ["005930", "005935"]
    assert codes("네이버보통주") == ["035420"]
    assert codes("naver") == ["035420"]
    assert codes("  ") == []


def test_unknown_instrument_is_not_found() -> None:
    with pytest.raises(InstrumentNotFoundError):
        _service().candles(InstrumentRef(InstrumentKind.STOCK, "999999"), Timeframe.DAY)
