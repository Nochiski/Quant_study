from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.equity.facade.research_data import (
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
    UniverseHistoryResult,
)

from ._catalog import FieldCatalogQuery, ResearchCatalog, build_research_catalog
from ._panel_preview import (
    ResearchPanelPreview,
    ResearchPanelPreviewRequest,
    preview_research_panel,
)
from ._universe_preview import UniversePreview, summarize_universe
from .ports.outgoing.equity_data import EquityDataPort


@dataclass(frozen=True)
class ResearchPreview:
    universe: UniverseHistoryResult
    panel: ResearchPanelResult


class EquityWorkspaceService:
    def __init__(self, equity_data: EquityDataPort) -> None:
        self._equity_data = equity_data

    def catalog(self, query: FieldCatalogQuery | None = None) -> ResearchCatalog:
        return build_research_catalog(
            snapshot=self._equity_data.snapshot(),
            all_fields=self._equity_data.list_fields(),
            query=query or FieldCatalogQuery(),
        )

    def preview_universe(self, query: UniverseHistoryQuery) -> UniversePreview:
        return summarize_universe(self._equity_data.load_universe(query))

    def preview_panel(self, request: ResearchPanelPreviewRequest) -> ResearchPanelPreview:
        return preview_research_panel(self._equity_data, request)

    def preview(self, query: ResearchPanelQuery, *, venue: str = "XKRX") -> ResearchPreview:
        universe = self._equity_data.load_universe(
            UniverseHistoryQuery(venue=venue, start=query.start, end=query.end)
        )
        panel = self._equity_data.load_panel(query)
        return ResearchPreview(universe=universe, panel=panel)
