"""판정 구간의 owner — 주기별 제안 칩 구간, 요청 날짜 → 봉 구간, 구간 출처(preset/user)."""

from __future__ import annotations

from strategy_workbench.application.stock_chart._errors import ChartRequestError
from strategy_workbench.application.stock_chart._views import (
    WindowRequest,
    WindowView,
)
from strategy_workbench.domain.chart_analysis.facade.bars import Timeframe
from strategy_workbench.domain.chart_analysis.facade.line_record import WindowSource
from strategy_workbench.domain.chart_analysis.facade.regime import WINDOW_PRESETS
from strategy_workbench.domain.chart_analysis.facade.series import (
    ChartSeries,
    Window,
)


def presets(series: ChartSeries, timeframe: Timeframe) -> tuple[tuple[int, Window], ...]:
    """주기별 제안 칩 길이와 마지막 완성 봉에서 끝나는 그 구간."""
    last = len(series.candles) - 1
    return tuple(
        (bars, Window.ending_at(last, bars)) for bars in WINDOW_PRESETS[timeframe].suggested_bars
    )


def resolve_window(
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
    return span, WindowView(
        series.bar_date(span.start),
        series.bar_date(span.end),
        span.bars,
        window_source(series, timeframe, span),
    )


def saved_window_source(
    series: ChartSeries, timeframe: Timeframe, span: Window, requested: WindowSource | None
) -> WindowSource:
    """저장할 기록의 구간 출처. 구간이 지금 마지막 완성 봉에서 끝나면 서버가 정한다
    (`window_source`). 그보다 앞서 끝나는 구간은 기준일이 지난 기록의 되살리기·복사라 그때의
    출처(요청 값)를 따르되, preset은 그 끝에서 잰 제안 칩 구간(계열 앞에서 잘린 것 포함)일 때만
    받는다."""
    if span.end == len(series.candles) - 1:
        return window_source(series, timeframe, span)
    chip = any(
        span == Window.ending_at(span.end, bars)
        for bars in WINDOW_PRESETS[timeframe].suggested_bars
    )
    return WindowSource.PRESET if requested is WindowSource.PRESET and chip else WindowSource.USER


def window_source(series: ChartSeries, timeframe: Timeframe, span: Window) -> WindowSource:
    """제안 칩 길이 그대로(마지막 완성 봉에서 끝남)면 `preset`, 아니면 `user`(끌기·날짜 입력·
    화면 기간)."""
    return (
        WindowSource.PRESET
        if any(span == w for _, w in presets(series, timeframe))
        else WindowSource.USER
    )
