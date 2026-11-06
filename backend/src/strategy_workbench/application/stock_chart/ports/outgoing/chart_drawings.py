"""Outgoing port: 개별종목차트의 선 저장(설계 문서 4.7).

선 하나 = 대상·만든 주기·종류·출처·기준점[(봉 날짜, 저장 가격)]·레벨·오른쪽 연장·메모·잠금·보이기,
자동이면 자동 기록(알고리즘 버전·파라미터·판정 구간·판정·방향 출처·구간 출처). 저장 가격은 전방 조정
공간(화면 가격 ÷ k)이고, 레벨 가격·중첩 같은 파생값은 저장하지 않는다(기준점에서 다시 계산).
어휘(`LineKind`·`LineSource`·`ChosenBy`·`WindowSource`·판정 어휘)의 owner는
`domain.chart_analysis`다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import InstrumentRef
from strategy_workbench.domain.chart_analysis.facade.bars import Timeframe
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ChosenBy,
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeVerdict, Structure

__all__ = [
    "AutoRecord",
    "ChartDrawingStore",
    "ChartLine",
    "ChartLineContent",
    "ChartLineNotFoundError",
    "LineAnchor",
    "ParamValue",
    "RegimeRecord",
]

ParamValue = str | float | int | bool | None


class ChartLineNotFoundError(LookupError):
    def __init__(self, line_id: str) -> None:
        super().__init__(f"chart line not found — line_id={line_id}")
        self.line_id = line_id


@dataclass(frozen=True)
class LineAnchor:
    """기준점. `bar_date`는 만든 주기 봉의 시작일(`Candle.start`), `price`는 저장 좌표다."""

    bar_date: date
    price: float


@dataclass(frozen=True)
class RegimeRecord:
    verdict: RegimeVerdict
    strength: float | None
    structure: Structure


@dataclass(frozen=True)
class AutoRecord:
    """자동 선의 기록. `regime_window`는 판정 구간의 (시작 봉 날짜, 끝 봉 날짜)다."""

    algorithm_version: str
    params: Mapping[str, ParamValue]
    regime_window: tuple[date, date] | None = None
    regime: RegimeRecord | None = None
    chosen_by: ChosenBy | None = None
    window_source: WindowSource | None = None


@dataclass(frozen=True)
class ChartLineContent:
    """만든 뒤에도 고칠 수 있는 칸. `levels`가 None이면 기본 레벨(`DEFAULT_FIB_LEVELS`)이다."""

    anchors: tuple[LineAnchor, ...]
    levels: tuple[float, ...] | None = None
    extend_right: bool = True
    memo: str = ""
    locked: bool = False
    visible: bool = True


@dataclass(frozen=True)
class ChartLine:
    line_id: str
    instrument: InstrumentRef
    timeframe: Timeframe
    kind: LineKind
    source: LineSource
    content: ChartLineContent
    auto: AutoRecord | None
    created_at: datetime
    updated_at: datetime


class ChartDrawingStore(Protocol):
    def list_lines(self, instrument: InstrumentRef) -> tuple[ChartLine, ...]:
        """그 대상의 선 전부(주기와 무관), 만든 순서."""
        ...

    def get_line(self, line_id: str) -> ChartLine | None: ...

    def add_line(self, line: ChartLine) -> None: ...

    def update_content(
        self, line_id: str, content: ChartLineContent, updated_at: datetime
    ) -> ChartLine:
        """고칠 수 있는 칸만 바꾼다. 없으면 `ChartLineNotFoundError`."""
        ...

    def delete_line(self, line_id: str) -> None:
        """없으면 `ChartLineNotFoundError`."""
        ...
