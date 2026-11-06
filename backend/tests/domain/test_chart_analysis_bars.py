"""개별종목차트 묶기(일·주·월)·진행 중 봉·표시 배율 규칙(설계 문서 3장)."""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.chart_analysis.facade.bars import (
    Candle,
    DisplayScale,
    PriceBar,
    Timeframe,
    aggregate_candles,
)


def _bar(session: date, o: float, h: float, low: float, c: float) -> PriceBar:
    return PriceBar(session, o, h, low, c)


WEEK_BARS = (
    _bar(date(2026, 11, 2), 10, 12, 9, 11),  # 월
    _bar(date(2026, 11, 4), 11, 15, 10, 14),
    _bar(date(2026, 11, 6), 14, 14, 8, 9),  # 금
    _bar(date(2026, 11, 9), 9, 10, 7, 8),  # 다음 주 월
    _bar(date(2026, 11, 10), 8, 11, 8, 10),
)


def test_weekly_candle_takes_first_open_extremes_last_close_and_flags_unfinished_week() -> None:
    candles = aggregate_candles(WEEK_BARS, Timeframe.WEEK, as_of=date(2026, 11, 10))

    assert candles == (
        Candle(date(2026, 11, 2), date(2026, 11, 6), 10, 15, 8, 9, in_progress=False),
        Candle(date(2026, 11, 9), date(2026, 11, 10), 9, 11, 7, 10, in_progress=True),
    )


def test_week_without_a_friday_session_is_in_progress_while_as_of_is_the_last_session() -> None:
    # as_of는 원장의 최신 거래일이다(PR2가 넘긴다).
    # 목요일이 최신이면 금요일이 휴장이든 아직이든 진행 중이다.
    bars = (*WEEK_BARS[3:], _bar(date(2026, 11, 12), 10, 12, 9, 11))

    on_thursday = aggregate_candles(bars, Timeframe.WEEK, as_of=date(2026, 11, 12))
    # 원장 최신 거래일이 금요일인데 이 종목만 거래가 없었으면(정지) 그 주는 끝났다
    halted_on_friday = aggregate_candles(bars, Timeframe.WEEK, as_of=date(2026, 11, 13))

    assert on_thursday[-1].in_progress is True
    assert halted_on_friday[-1].in_progress is False


def test_week_key_is_iso_week_so_a_week_spanning_new_year_is_one_candle() -> None:
    bars = (
        _bar(date(2025, 12, 29), 10, 11, 9, 10),  # 2026-W01 월
        _bar(date(2025, 12, 31), 10, 12, 9, 11),
        _bar(date(2026, 1, 2), 11, 13, 10, 12),  # 2026-W01 금
    )

    candles = aggregate_candles(bars, Timeframe.WEEK, as_of=date(2026, 1, 2))

    assert candles == (Candle(date(2025, 12, 29), date(2026, 1, 2), 10, 13, 9, 12),)


def test_month_in_progress_until_last_weekday_of_month() -> None:
    # 2026-10-31은 토요일이라 10월의 마지막 평일은 10-30(금)이다.
    bars = (
        _bar(date(2026, 10, 29), 10, 11, 9, 10),
        _bar(date(2026, 10, 30), 10, 12, 8, 11),
    )

    assert aggregate_candles(bars[:1], Timeframe.MONTH, date(2026, 10, 29))[-1].in_progress
    assert not aggregate_candles(bars, Timeframe.MONTH, date(2026, 10, 30))[-1].in_progress
    with_november = aggregate_candles(
        (*bars, _bar(date(2026, 11, 2), 11, 11, 10, 10)), Timeframe.MONTH, date(2026, 11, 2)
    )
    assert [(c.start, c.end, c.in_progress) for c in with_november] == [
        (date(2026, 10, 29), date(2026, 10, 30), False),
        (date(2026, 11, 2), date(2026, 11, 2), True),
    ]


def test_daily_candles_are_never_in_progress() -> None:
    candles = aggregate_candles(WEEK_BARS, Timeframe.DAY, as_of=date(2026, 11, 10))

    assert [c.start for c in candles] == [bar.session for bar in WEEK_BARS]
    assert not any(c.in_progress for c in candles)


@pytest.mark.parametrize(
    ("bars", "as_of"),
    [
        ((WEEK_BARS[1], WEEK_BARS[0]), date(2026, 11, 10)),  # 역순
        ((WEEK_BARS[0], WEEK_BARS[0]), date(2026, 11, 10)),  # 중복
        (WEEK_BARS, date(2026, 11, 9)),  # 기준일 뒤 일봉(룩어헤드)
    ],
)
def test_aggregate_rejects_unordered_or_future_bars(
    bars: tuple[PriceBar, ...], as_of: date
) -> None:
    with pytest.raises(ValueError):
        aggregate_candles(bars, Timeframe.WEEK, as_of)


def test_display_scale_maps_forward_adjusted_space_to_screen_and_back() -> None:
    scale = DisplayScale.from_last_close(actual_close=50_000, adjusted_close=25_000)

    assert scale.factor == 2.0
    assert scale.to_display(1_000) == 2_000
    assert scale.to_stored(2_000) == 1_000


@pytest.mark.parametrize(("actual", "adjusted"), [(0.0, 100.0), (100.0, 0.0)])
def test_display_scale_rejects_non_positive_closes(actual: float, adjusted: float) -> None:
    with pytest.raises(ValueError):
        DisplayScale.from_last_close(actual, adjusted)
