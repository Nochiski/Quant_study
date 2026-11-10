"""개별종목차트 요청 거절의 코드 어휘와 예외, 저장 선을 그릴 수 없는 사유 어휘.

HTTP는 404·422 `{code, message}`로 그대로 옮기고 frontend는 `code`로 문구를 고른다(`message`는
진단용 원문이다).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import InstrumentRef

ChartErrorCode = Literal[
    "chart.ledger.no_confirmed_session",
    "chart.series.no_completed_bars",
    "chart.window.incomplete",
    "chart.window.outside_series",
    "chart.window.reversed",
    "chart.params.invalid",
    "chart.line.anchor_count",
    "chart.line.price_invalid",
    "chart.line.levels_invalid",
    "chart.line.anchor_outside_series",
    "chart.line.anchor_in_progress",
    "chart.line.anchor_order",
    "chart.line.auto_record",
    "chart.line.auto_anchors_fixed",
    "chart.line.memo_too_long",
    "chart.confluence.duplicate_ref",
]


class LineIssue(StrEnum):
    """저장 선을 지금 계열로 그릴 수 없는 사유(그때 `paths`가 빈다)."""

    NO_COMPLETED_BARS = "no_completed_bars"  # 그 주기 계열에 완성 봉이 없다
    ANCHOR_OUTSIDE_SERIES = "anchor_outside_series"  # 기준점 날짜가 지금 계열 밖이다
    ANCHOR_ORDER = "anchor_order"  # 다시 묶은 봉에서 기준선 두 점이 같은 봉·역순이 됐다


class ChartRequestError(ValueError):
    """요청대로 계산·저장할 수 없다(HTTP 422)."""

    def __init__(self, code: ChartErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ChartErrorCode = code


class InstrumentNotFoundError(LookupError):
    def __init__(self, ref: InstrumentRef) -> None:
        super().__init__(f"chart instrument not found — kind={ref.kind.value} code={ref.code}")
        self.ref = ref
