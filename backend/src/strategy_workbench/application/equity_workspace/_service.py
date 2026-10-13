from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.backtest.facade.environment import require_research_window
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

    # 아래 두 미리보기는 패널 값을 열람하는 측정 경로라 연구 구간 잠금을 받는다(spec D1). 구성 종목
    # 이력만 보이는 `preview_universe` 는 측정이 아니라 받지 않는다.
    def preview_panel(self, request: ResearchPanelPreviewRequest) -> ResearchPanelPreview:
        require_research_window(request.query.start, requested_by="equity.panel_preview")
        return preview_research_panel(self._equity_data, request)

    def preview(self, query: ResearchPanelQuery, *, venue: str = "XKRX") -> ResearchPreview:
        require_research_window(query.start, requested_by="equity.preview")
        universe = self._equity_data.load_universe(
            UniverseHistoryQuery(venue=venue, start=query.start, end=query.end)
        )
        panel = self._equity_data.load_panel(query)
        return ResearchPreview(universe=universe, panel=panel)
