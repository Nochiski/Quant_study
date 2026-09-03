from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.equity.facade.research_data import (
    DatasetFieldProfile,
    DataSnapshot,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
    UniverseHistoryResult,
)

from .ports.outgoing.equity_data import EquityDataPort


@dataclass(frozen=True)
class ResearchCatalog:
    snapshot: DataSnapshot
    fields: tuple[DatasetFieldProfile, ...]


@dataclass(frozen=True)
class ResearchPreview:
    universe: UniverseHistoryResult
    panel: ResearchPanelResult


class EquityWorkspaceService:
    def __init__(self, equity_data: EquityDataPort) -> None:
        self._equity_data = equity_data

    def catalog(self) -> ResearchCatalog:
        return ResearchCatalog(
            snapshot=self._equity_data.snapshot(),
            fields=self._equity_data.list_fields(),
        )

    def preview(self, query: ResearchPanelQuery, *, venue: str = "XKRX") -> ResearchPreview:
        universe = self._equity_data.load_universe(
            UniverseHistoryQuery(venue=venue, start=query.start, end=query.end)
        )
        panel = self._equity_data.load_panel(query)
        return ResearchPreview(universe=universe, panel=panel)
