"""개별종목차트 서비스: 날짜 ↔ 봉 index, 진행 중 봉, 화면 ↔ 저장 가격(k), 선 저장 왕복, 중첩 입력.

손 픽스처: 2025-09-01(월) ~ 2025-10-30(목) 평일 44개, 조정 종가 = 100 + i, 시가 = 종가 − 0.5, 고가
= 종가 + 1, 저가 = 종가 − 1. 마지막 실제 종가는 조정 종가의 절반이라 k = 0.5다(화면 = 저장 × 0.5).
기준일 10-30이 목요일이라 주봉의 마지막 주(10-27~)는 진행 중이다.
"""

from __future__ import annotations

import math
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
    LineIssue,
    LineShape,
    StockChartService,
    SwingSettings,
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
    LineAnchor,
    RegimeRecord,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar, Timeframe
from strategy_workbench.domain.chart_analysis.facade.levels import (
    SwingChoice,
    TrendDirection,
    ZoneSide,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import (
    BOX_RATIO_MAX,
    RegimeReason,
    RegimeVerdict,
    Structure,
)
from strategy_workbench.domain.chart_analysis.facade.series import PivotKind

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

    def confirmed_session(self) -> date | None:
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


def _service(
    drawings: FakeDrawings | None = None,
    prices: FakePrices | None = None,
    *,
    practice: bool = False,
) -> StockChartService:
    ids = iter(f"line-{n}" for n in range(1, 100))
    return StockChartService(
        prices or FakePrices(),
        drawings or FakeDrawings(),
        practice_data=practice,
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
    judgement = regime.judgement
    assert (judgement.verdict, judgement.reason, judgement.strength) == (
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
    regime = service.regime(UPTREND, Timeframe.DAY)
    assert regime.judgement.channel_direction is TrendDirection.UP
    assert any(f"{BOX_RATIO_MAX:g}" in rule for rule in regime.rules)

    followed = service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP)
    overridden = service.channels(UPTREND, Timeframe.DAY, TrendDirection.DOWN)
    assert followed.record.chosen_by is ChosenBy.RECOMMENDATION
    assert overridden.record.chosen_by is ChosenBy.USER_OVERRIDE
    assert followed.record.algorithm_version == ALGORITHM_VERSION
    assert followed.record.regime_window == (date(2025, 9, 1), AS_OF)
    assert followed.candidates
    # 첫 점 고르기 목록은 그 방향의 확정 피벗(up: 저점)이고, 어느 것을 첫 점으로 보내도 받아들인다
    choices = followed.anchor_choices
    assert choices
    assert {p.kind for p in choices} == {PivotKind.LOW}
    for choice in choices:
        picked = service.channels(
            UPTREND, Timeframe.DAY, TrendDirection.UP, first_anchor=choice.bar_date
        )
        assert picked.anchor_issue is None, choice
        assert all(c.drawing.anchors[0].bar_date == choice.bar_date for c in picked.candidates)
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
        # 레벨을 비운 선은 그릴 것이 없다(기본 레벨은 칸을 비워 고른다)
        (
            ChartLineDraft(
                STOCK,
                Timeframe.DAY,
                LineKind.FIB_RETRACEMENT,
                LineSource.MANUAL,
                LineEdit(
                    (ChartPoint(date(2025, 9, 3), 50.0), ChartPoint(date(2025, 10, 1), 70.0)),
                    levels=(),
                ),
            ),
            "chart.line.levels_invalid",
        ),
        # NaN·inf는 저장·JSON이 받지 못한다 — 저장소까지 가지 않고 422
        (
            replace(
                _hline(60.0),
                source=LineSource.AUTO,
                auto=AutoRecord(ALGORITHM_VERSION, {"width_atr": math.nan}),
            ),
            "chart.line.auto_record",
        ),
        (
            replace(
                _hline(60.0),
                source=LineSource.AUTO,
                auto=AutoRecord(
                    ALGORITHM_VERSION,
                    {},
                    regime=RegimeRecord(RegimeVerdict.UP, math.inf, Structure.UP),
                ),
            ),
            "chart.line.auto_record",
        ),
        # 진행 중인 주(10-27~)의 날짜는 계열 밖과 따로 알린다
        (
            replace(_hline(60.0, date(2025, 10, 28)), timeframe=Timeframe.WEEK),
            "chart.line.anchor_in_progress",
        ),
        (
            replace(
                _hline(60.0),
                edit=LineEdit((ChartPoint(date(2025, 9, 3), 60.0),), memo="가" * 501),
            ),
            "chart.line.memo_too_long",
        ),
    ],
)
def test_lines_that_cannot_be_drawn_are_rejected(draft: ChartLineDraft, code: str) -> None:
    drawings = FakeDrawings()
    with pytest.raises(ChartRequestError) as error:
        _service(drawings).create_line(draft)
    assert error.value.code == code
    assert drawings.lines == {}


def test_confluence_reads_visible_saved_lines_and_inline_candidates() -> None:
    drawings = FakeDrawings()
    service = _service(drawings)
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
    # 저장한 후보를 다시 실으면(ref = line_id) 두 번 세지 않는다 — 혼자 남은 수평선은 중첩이 없다
    assert service.confluence(STOCK, (replace(candidate, ref=shown),)).groups == ()
    # 내 선을 빼면(include_mine=False) 손으로 그은 수평선이 빠져 되돌림 혼자라 중첩이 없다
    assert service.confluence(STOCK, (candidate,), include_mine=False).groups == ()
    # 저장한 자동 선은 내 선을 빼도 남는다 — 같은 60의 자동 띠는 되돌림 0.5와 계속 겹친다.
    # 띠는 저장소에 바로 넣는다(저장 경로의 기록 검사는 따로 본다). 저장 가격 = 화면 ÷ k(0.5)
    shown_line = drawings.lines[shown]
    drawings.add_line(
        replace(
            shown_line,
            line_id="band",
            source=LineSource.AUTO,
            content=ChartLineContent(
                (LineAnchor(date(2025, 9, 3), 119.8), LineAnchor(date(2025, 9, 3), 120.2))
            ),
            auto=AutoRecord(ALGORITHM_VERSION, {}),
        )
    )
    (kept,) = service.confluence(STOCK, (candidate,), include_mine=False).groups
    assert sorted(m.ref for m in kept.members) == sorted(["auto-1", "band"])


def test_saving_the_same_auto_line_again_returns_the_saved_one() -> None:
    # 묶음 추가를 부분 실패 뒤 다시 보내도 저장 내용이 완전히 같은 자동 선은 두 줄 생기지 않는다
    drawings = FakeDrawings()
    service = _service(drawings)
    swing = service.retracement(UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED)
    assert swing.drawing is not None
    retracement = ChartLineDraft(
        UPTREND,
        Timeframe.DAY,
        LineKind.FIB_RETRACEMENT,
        LineSource.AUTO,
        LineEdit(swing.drawing.anchors),
        swing.record,
    )
    first = service.create_line(retracement).line.line_id
    assert service.create_line(retracement).line.line_id == first
    assert len(drawings.lines) == 1
    # 같은 내용인데 숨긴 선이면 다시 보이게 해서 준다
    hidden = service.update_line(first, replace(retracement.edit, visible=False)).line
    assert hidden.content.visible is False
    shown = service.create_line(retracement).line
    assert (shown.line_id, shown.content.visible) == (first, True)
    assert len(drawings.lines) == 1
    # 화면 ↔ 저장 왕복으로 가격 마지막 자리가 다른 본문도 같은 선이다
    nudged = LineEdit(
        tuple(replace(a, price=math.nextafter(a.price, math.inf)) for a in retracement.edit.anchors)
    )
    assert service.create_line(replace(retracement, edit=nudged)).line.line_id == first
    assert len(drawings.lines) == 1
    # 레벨만·연장만·파라미터만 다르면 새 선이다(옛 선을 고치지 않는다).
    # 민감도를 바꿔도 기준점은 같다
    loose = service.retracement(
        UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED, swing=SwingSettings(1.5)
    )
    assert loose.drawing is not None and loose.drawing.anchors == swing.drawing.anchors
    different = (
        replace(retracement, edit=replace(retracement.edit, levels=(0.0, 0.5, 1.0))),
        replace(retracement, edit=replace(retracement.edit, extend_right=False)),
        replace(retracement, auto=loose.record),
    )
    for draft in different:
        made = service.create_line(draft).line
        assert made.line_id != first
        # 서버가 다시 만든 기록은 후보 응답의 기록과 같다
        assert (made.content.levels, made.content.extend_right, made.auto) == (
            draft.edit.levels,
            draft.edit.extend_right,
            draft.auto,
        )
    assert service.lines(UPTREND).lines[0].line.content.levels is None
    assert len(drawings.lines) == 4
    # 출처가 다르면(복사본) 합치지 않는다
    service.create_line(replace(retracement, source=LineSource.AUTO_COPIED))
    assert len(drawings.lines) == 5
    # 주기가 다른 같은 내용의 선과도 합치지 않는다
    weekly_only = FakeDrawings()
    weekly_only.add_line(replace(drawings.lines[first], line_id="weekly", timeframe=Timeframe.WEEK))
    made = _service(weekly_only).create_line(retracement).line
    assert made.line_id != "weekly" and len(weekly_only.lines) == 2
    # 손으로 그은 선은 같은 자리여도 새 선이다
    service.create_line(_hline(60.0))
    service.create_line(_hline(60.0))
    assert len(drawings.lines) == 7


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


class _FundPrices(FakePrices):
    """이름이 같은 순위로 걸리는 리츠·ETF·우선주 셋."""

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        return tuple(
            ChartInstrument(InstrumentRef(InstrumentKind.STOCK, code), name, group)
            for code, name, group in (
                ("900001", "가상 A리츠", "reit"),  # 이름은 ETF·ETN보다 앞이다
                ("900002", "가상 B우", "preferred"),
                ("900003", "가상 ETF", "etf"),
            )
        )


def test_search_orders_by_the_port_group_vocabulary_then_other_kinds() -> None:
    # 같은 순위(이름 포함) 안에서 포트 어휘 차례(우선주 → ETF) 뒤에 그 밖의 종류(리츠)가 온다
    found = _service(prices=_FundPrices()).search("가상").instruments

    assert [item.ref.code for item in found] == ["900002", "900003", "900001"]


class _EmptyLedger(FakePrices):
    def confirmed_session(self) -> date | None:
        return None


def test_a_ledger_without_a_confirmed_session_has_its_own_code() -> None:
    # 원장에 확정일이 없는 것(차트를 열 수 없음)과 그 주기에 완성 봉이 없는 것은 다른 사유다
    with pytest.raises(ChartRequestError) as error:
        _service(prices=_EmptyLedger()).candles(STOCK, Timeframe.DAY)

    assert error.value.code == "chart.ledger.no_confirmed_session"


def test_unknown_instrument_is_not_found() -> None:
    with pytest.raises(InstrumentNotFoundError):
        _service().candles(InstrumentRef(InstrumentKind.STOCK, "999999"), Timeframe.DAY)


def test_preview_checks_and_normalizes_like_saving_but_stores_nothing() -> None:
    drawings = FakeDrawings()
    service = _service(drawings)
    points = (ChartPoint(date(2025, 9, 3), 50.0), ChartPoint(date(2025, 10, 8), 70.0))
    shape = LineShape(STOCK, Timeframe.WEEK, LineKind.FIB_RETRACEMENT, points)

    preview = service.preview_line(shape)
    assert drawings.lines == {}
    saved = service.create_line(
        ChartLineDraft(
            STOCK, Timeframe.WEEK, LineKind.FIB_RETRACEMENT, LineSource.MANUAL, LineEdit(points)
        )
    )
    # 끄는 날짜는 그 주 봉의 시작일로 붙고, 그릴 점은 저장한 선과 같다
    assert [a.bar_date for a in preview.anchors] == [date(2025, 9, 1), date(2025, 10, 6)]
    assert (preview.anchors, preview.paths) == (saved.anchors, saved.paths)
    in_progress = (points[0], ChartPoint(date(2025, 10, 28), 70.0))
    with pytest.raises(ChartRequestError) as error:
        service.preview_line(replace(shape, anchors=in_progress))
    assert error.value.code == "chart.line.anchor_in_progress"


def test_auto_lines_keep_their_anchors_and_report_when_they_formed() -> None:
    service = _service()
    swing = service.retracement(UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED)
    channels = service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP)
    channel = channels.candidates[0]
    assert swing.drawing is not None and swing.created is not None

    def save(kind: LineKind, anchors: tuple[ChartPoint, ...], record: AutoRecord) -> str:
        draft = ChartLineDraft(
            UPTREND, Timeframe.DAY, kind, LineSource.AUTO, LineEdit(anchors), record
        )
        return service.create_line(draft).line.line_id

    swing_id = save(LineKind.FIB_RETRACEMENT, swing.drawing.anchors, swing.record)
    channel_id = save(LineKind.FIB_CHANNEL, channel.drawing.anchors, channels.record)
    created = {view.line.line_id: view.created for view in service.lines(UPTREND).lines}
    # 생긴 날은 저장하지 않고 저장한 구간·파라미터로 다시 찾아 낸다 — 후보 응답과 같다
    assert created == {swing_id: swing.created, channel_id: channel.created}

    # 메모는 고칠 수 있지만 자동 선의 기준점은 옮길 수 없다
    kept = service.update_line(swing_id, LineEdit(swing.drawing.anchors, memo="확인"))
    assert kept.line.content.memo == "확인"
    moved = (swing.drawing.anchors[0], replace(swing.drawing.anchors[1], price=1.0))
    with pytest.raises(ChartRequestError) as error:
        service.update_line(swing_id, LineEdit(moved))
    assert error.value.code == "chart.line.auto_anchors_fixed"
    # 고칠 수 있는 복사본은 옮긴 뒤 생긴 날이 없다(더는 자동 기록의 결과가 아니다)
    copy = service.create_line(
        ChartLineDraft(
            UPTREND,
            Timeframe.DAY,
            LineKind.FIB_RETRACEMENT,
            LineSource.AUTO_COPIED,
            LineEdit(swing.drawing.anchors),
            swing.record,
        )
    )
    assert copy.created == swing.created
    moved_copy = service.update_line(copy.line.line_id, LineEdit(moved))
    assert moved_copy.created is None
    # 옮긴 복사본을 지우고 되돌리면(같은 내용으로 다시 만들기) 다시 저장된다 — 복사본은 후보를
    # 대조하지 않는다
    service.delete_line(copy.line.line_id)
    restored = service.create_line(
        ChartLineDraft(
            UPTREND,
            Timeframe.DAY,
            LineKind.FIB_RETRACEMENT,
            LineSource.AUTO_COPIED,
            LineEdit(moved_copy.anchors),
            moved_copy.line.auto,
        )
    )
    assert (restored.line.content.anchors, restored.created) == (
        moved_copy.line.content.anchors,
        None,
    )


def test_a_preset_record_saved_after_the_ledger_moves_on_stays_preset() -> None:
    # 어제 제안 칩 그대로 만든 기록을 오늘(완성 봉이 하나 는 뒤) 되살리거나 복사해도 그때의
    # 출처를 지킨다. 칩 길이가 아닌 구간의 preset 주장은 받지 않는다
    yesterday = FakePrices()
    full = yesterday.history[UPTREND]
    yesterday.history[UPTREND] = ChartHistory(full.bars[:-1], full.last_actual_close)
    swing = _service(prices=yesterday).retracement(
        UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED
    )
    assert swing.drawing is not None and swing.record.window_source is WindowSource.PRESET
    anchors = swing.drawing.anchors
    service = _service()

    def save(record: AutoRecord) -> WindowSource | None:
        line = service.create_line(
            ChartLineDraft(
                UPTREND,
                Timeframe.DAY,
                LineKind.FIB_RETRACEMENT,
                LineSource.AUTO_COPIED,
                LineEdit(anchors),
                record,
            )
        ).line
        assert line.auto is not None
        return line.auto.window_source

    assert save(swing.record) is WindowSource.PRESET
    assert swing.record.regime_window is not None
    start, end = swing.record.regime_window
    shorter = replace(swing.record, regime_window=(SESSIONS[SESSIONS.index(start) + 1], end))
    assert save(shorter) is WindowSource.USER


def test_analysis_parameter_ranges_are_checked_by_the_domain() -> None:
    service = _service()
    calls = (
        lambda: service.regime(STOCK, Timeframe.DAY, swing=SwingSettings(0.0)),
        lambda: service.regime(STOCK, Timeframe.DAY, swing=SwingSettings(math.nan)),
        lambda: service.zones(STOCK, Timeframe.DAY, min_touches=0),
        lambda: service.confluence(STOCK, (), tolerance=0.0),
        lambda: service.search("삼성", limit=0),
    )
    for call in calls:
        with pytest.raises(ChartRequestError) as error:
            call()
        assert error.value.code == "chart.params.invalid"
    with pytest.raises(ChartRequestError) as reversed_range:
        service.candles(STOCK, Timeframe.DAY, date(2025, 10, 1), date(2025, 9, 1))
    assert reversed_range.value.code == "chart.window.reversed"


def test_zone_side_compares_the_current_price_not_the_window_end_close() -> None:
    # 판정 구간 끝(봉 19) 종가 116은 고점 띠 123 아래지만, 현재가(기준일 종가) 140은
    # 모든 띠 위다 — 다 지지다
    window = WindowRequest(SESSIONS[0], SESSIONS[19])

    view = _service().zones(UPTREND, Timeframe.DAY, window, min_touches=1)

    assert (_zigzag()[19], _zigzag()[-1]) == (116.0, 140.0)
    assert [(z.high, z.side) for z in view.zones] == [
        (123.0, ZoneSide.SUPPORT),
        (115.0, ZoneSide.SUPPORT),
        (107.0, ZoneSide.SUPPORT),
        (101.0, ZoneSide.SUPPORT),
    ]


def test_zone_side_compares_in_the_stored_price_space_when_k_is_not_one() -> None:
    # k = 0.5: 띠는 저장 가격(123 등), 현재가는 저장 140 = 화면 70.
    # 화면 값(70)과 비교하면 띠가 모두 저항이 된다
    prices = FakePrices()
    prices.history[UPTREND] = ChartHistory(_bars(_zigzag()), _zigzag()[-1] / 2)
    window = WindowRequest(SESSIONS[0], SESSIONS[19])

    view = _service(prices=prices).zones(UPTREND, Timeframe.DAY, window, min_touches=1)

    assert view.context.current_price == 70.0
    assert [(z.high, z.side) for z in view.zones] == [
        (61.5, ZoneSide.SUPPORT),
        (57.5, ZoneSide.SUPPORT),
        (53.5, ZoneSide.SUPPORT),
        (50.5, ZoneSide.SUPPORT),
    ]


def test_a_line_that_can_no_longer_be_drawn_can_still_be_hidden_but_not_moved() -> None:
    # 9-03에 그은 수평선. 원장이 바뀌어(재상장 등) 계열이 9-10부터 시작하면 기준점이 계열 밖이다
    drawings = FakeDrawings()
    created = _service(drawings).create_line(_hline(60.0, date(2025, 9, 3)))
    prices = FakePrices()
    full = prices.history[STOCK]
    prices.history[STOCK] = ChartHistory(
        tuple(bar for bar in full.bars if bar.session >= date(2025, 9, 10)), full.last_actual_close
    )
    service = _service(drawings, prices)
    (listed,) = service.lines(STOCK).lines
    assert listed.issue is LineIssue.ANCHOR_OUTSIDE_SERIES

    hidden = service.update_line(
        listed.line.line_id, LineEdit(listed.anchors, memo="재상장 전 선", visible=False)
    )

    assert (hidden.issue, hidden.line.content.visible, hidden.line.content.memo) == (
        LineIssue.ANCHOR_OUTSIDE_SERIES,
        False,
        "재상장 전 선",
    )
    assert hidden.line.content.anchors == created.line.content.anchors
    # 기준점을 옮기는 수정은 여전히 계열로 검사한다
    with pytest.raises(ChartRequestError) as moved:
        service.update_line(listed.line.line_id, LineEdit((ChartPoint(date(2025, 9, 3), 61.0),)))
    assert moved.value.code == "chart.line.anchor_outside_series"


def test_an_auto_line_that_can_no_longer_be_drawn_can_still_be_hidden() -> None:
    # 자동 되돌림을 저장한 뒤 원장이 나중 기준점 앞에서 끝나면(정정·상장폐지) 그릴 수 없다.
    # 기준점이 그대로인 숨기기는 자동 선의 기준점 고정 검사에 걸리지 않는다
    drawings = FakeDrawings()
    swing = _service(drawings).retracement(UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED)
    assert swing.drawing is not None
    anchors = swing.drawing.anchors
    _service(drawings).create_line(
        ChartLineDraft(
            UPTREND,
            Timeframe.DAY,
            LineKind.FIB_RETRACEMENT,
            LineSource.AUTO,
            LineEdit(anchors),
            swing.record,
        )
    )
    prices = FakePrices()
    full = prices.history[UPTREND]
    last_anchor = max(a.bar_date for a in anchors)
    prices.history[UPTREND] = ChartHistory(
        tuple(bar for bar in full.bars if bar.session < last_anchor), full.last_actual_close
    )
    service = _service(drawings, prices)
    (listed,) = service.lines(UPTREND).lines
    assert listed.issue is LineIssue.ANCHOR_OUTSIDE_SERIES

    hidden = service.update_line(listed.line.line_id, LineEdit(listed.anchors, visible=False))

    assert (hidden.issue, hidden.line.content.visible) == (LineIssue.ANCHOR_OUTSIDE_SERIES, False)


def test_current_price_is_the_last_ledger_close_whatever_candle_range() -> None:
    # 과거 범위만 보여도 현재가는 기준일까지 마지막 일봉 종가(화면 가격 143 × k 0.5)다
    view = _service().candles(STOCK, Timeframe.DAY, date(2025, 9, 1), date(2025, 9, 30))

    assert (view.candles[-1].close, view.context.current_price) == (60.5, 71.5)


class _MixedPrices(FakePrices):
    """연습용 접두가 붙은 대상을 하나 더 싣는 원천."""

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        practice = InstrumentRef(InstrumentKind.STOCK, "practice:005930")
        self.history[practice] = self.history[STOCK]
        return (*super().chart_instruments(), ChartInstrument(practice, "삼성전자", "common"))


def test_practice_and_real_instruments_never_cross_modes() -> None:
    real = _service(prices=_MixedPrices())
    practice = _service(prices=_MixedPrices(), practice=True)
    assert [i.ref.code for i in real.search("삼성전자").instruments] == ["005930", "005935"]
    assert [i.ref.code for i in practice.search("삼성전자").instruments] == ["practice:005930"]
    with pytest.raises(InstrumentNotFoundError):
        real.candles(InstrumentRef(InstrumentKind.STOCK, "practice:005930"), Timeframe.DAY)
    with pytest.raises(InstrumentNotFoundError):
        practice.candles(STOCK, Timeframe.DAY)


def test_lines_of_the_other_mode_can_be_neither_edited_nor_deleted() -> None:
    """연습·실데이터 앱이 같은 저장소를 써도 다른 모드의 선은 없는 선이다."""
    drawings = FakeDrawings()
    real_id = _service(drawings, _MixedPrices()).create_line(_hline(60.0)).line.line_id
    practice = _service(drawings, _MixedPrices(), practice=True)

    with pytest.raises(ChartLineNotFoundError):
        practice.update_line(real_id, LineEdit((ChartPoint(date(2025, 9, 3), 50.0),)))
    with pytest.raises(ChartLineNotFoundError):
        practice.delete_line(real_id)
    assert drawings.lines[real_id].content.anchors[0].price == 120.0


def test_auto_records_must_reproduce_the_line_and_the_server_rebuilds_the_verdicts() -> None:
    drawings = FakeDrawings()
    service = _service(drawings)
    swing = service.retracement(UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED)
    assert swing.drawing is not None
    anchors = swing.drawing.anchors
    draft = ChartLineDraft(
        UPTREND,
        Timeframe.DAY,
        LineKind.FIB_RETRACEMENT,
        LineSource.AUTO,
        LineEdit(anchors),
        swing.record,
    )
    params = dict(swing.record.params)
    rejected = (
        # 다른 알고리즘 버전의 기록은 지금 규칙의 결과가 아니다
        replace(draft, auto=replace(swing.record, algorithm_version="chart_analysis@0")),
        # 읽히지 않는 칸·모르는 칸·빠진 칸·다른 도구의 칸
        replace(draft, auto=replace(swing.record, params={**params, "anything": "x"})),
        replace(draft, auto=replace(swing.record, params={"candidate_sensitivity": 1.0})),
        replace(draft, auto=replace(swing.record, params={**params, "direction": "up"})),
        # 판정 구간이 없거나 계열 밖이다
        replace(draft, auto=replace(swing.record, regime_window=None)),
        replace(
            draft,
            auto=replace(swing.record, regime_window=(date(2025, 1, 2), date(2025, 1, 3))),
        ),
        # 기준점이 이 찾기의 결과가 아니다(가격을 바꾼 본문)
        replace(draft, edit=LineEdit((anchors[0], replace(anchors[1], price=123.4)))),
    )
    for bad in rejected:
        with pytest.raises(ChartRequestError) as error:
            service.create_line(bad)
        assert error.value.code == "chart.line.auto_record"
    assert drawings.lines == {}

    # 판정·방향 출처·구간 출처는 요청 값을 믿지 않고 서버가 다시 계산한다
    tampered = replace(
        swing.record,
        regime=RegimeRecord(RegimeVerdict.UP, 2.5, Structure.UP),
        chosen_by=ChosenBy.USER_OVERRIDE,
        window_source=WindowSource.USER,
    )
    saved = service.create_line(replace(draft, auto=tampered))
    assert saved.line.auto == swing.record
    assert saved.created == swing.created
    channels = service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP)
    channel = channels.candidates[0]
    rebuilt = service.create_line(
        ChartLineDraft(
            UPTREND,
            Timeframe.DAY,
            LineKind.FIB_CHANNEL,
            LineSource.AUTO,
            LineEdit(channel.drawing.anchors),
            replace(channels.record, regime=None, chosen_by=None),
        )
    )
    assert rebuilt.line.auto == channels.record

    # 저장 뒤 규칙 판이 바뀌면(옛 버전 기록) 생긴 날을 다시 계산하지 않는다
    line_id = saved.line.line_id
    old = replace(swing.record, algorithm_version="chart_analysis@0")
    drawings.lines[line_id] = replace(drawings.lines[line_id], auto=old)
    assert service.lines(UPTREND).lines[0].created is None


def test_candidates_draw_the_requested_levels_and_channels_extend_to_1_618_by_default() -> None:
    service = _service()
    default = service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP).candidates[0]
    chosen = service.channels(
        UPTREND, Timeframe.DAY, TrendDirection.UP, levels=(0.0, 2.618)
    ).candidates[0]
    swing = service.retracement(UPTREND, Timeframe.DAY, SwingChoice.LATEST_CONFIRMED)

    assert [p.level for p in default.drawing.paths] == [
        0.0,
        0.236,
        0.382,
        0.5,
        0.618,
        0.786,
        1.0,
        1.618,
    ]
    assert [p.level for p in chosen.drawing.paths] == [0.0, 2.618]
    assert swing.drawing is not None
    assert [p.level for p in swing.drawing.paths][-1] == 1.0
    with pytest.raises(ChartRequestError) as error:
        service.channels(UPTREND, Timeframe.DAY, TrendDirection.UP, levels=())
    assert error.value.code == "chart.line.levels_invalid"
