from __future__ import annotations

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
