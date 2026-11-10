"""개별종목차트 서비스 테스트의 포트 대역(`ChartPricePort`·`ChartDrawingStore`).

실제 구현과 같은 계약으로 동작한다 — 저장소는 종목별로 걸러 읽고, 없는 선의 수정·삭제는
`ChartLineNotFoundError`다. 서비스 단위 테스트와 알고리즘 golden이 함께 쓴다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from strategy_workbench.application.stock_chart.facade.ports import (
    ChartHistory,
    ChartInstrument,
    ChartLine,
    ChartLineContent,
    ChartLineNotFoundError,
    InstrumentRef,
)


class SeriesPrices:
    """대상 목록·이력·기준일을 받아 답하는 가격 포트. `history`는 테스트가 바꿔 끼울 수 있다."""

    def __init__(
        self,
        instruments: tuple[ChartInstrument, ...],
        history: dict[InstrumentRef, ChartHistory],
        as_of: date,
    ) -> None:
        self.instruments = instruments
        self.history = history
        self.as_of = as_of

    def confirmed_session(self) -> date | None:
        return self.as_of

    def chart_instruments(self) -> tuple[ChartInstrument, ...]:
        return self.instruments

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
