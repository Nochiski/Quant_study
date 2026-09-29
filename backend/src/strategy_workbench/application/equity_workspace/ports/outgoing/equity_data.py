from __future__ import annotations

from datetime import date
from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import (
    DatasetFieldProfile,
    DataSnapshot,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
    UniverseHistoryResult,
)


class EquityDataPort(Protocol):
    """Stable PIT research contract implemented by mock and future Equity DB adapters."""

    def snapshot(self) -> DataSnapshot: ...

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]: ...

    def load_universe(self, query: UniverseHistoryQuery) -> UniverseHistoryResult: ...

    def load_panel(self, query: ResearchPanelQuery) -> ResearchPanelResult: ...

    def trading_sessions(self, start: date, end: date) -> tuple[date, ...]:
        """양끝을 포함한 기간의 거래 세션(오름차순). 워크포워드 엠바고가 세션 수를 센다."""
        ...
