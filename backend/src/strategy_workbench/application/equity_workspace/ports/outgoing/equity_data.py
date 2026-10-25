from __future__ import annotations

from datetime import date
from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import (
    DatasetFieldProfile,
    DataSnapshot,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseDefinition,
    UniverseHistoryQuery,
    UniverseHistoryResult,
)


class EquityDataPort(Protocol):
    """Stable PIT research contract implemented by mock and future Equity DB adapters."""

    def snapshot(self) -> DataSnapshot: ...

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]: ...

    def list_universes(self) -> tuple[UniverseDefinition, ...]:
        """실행 설정 `universe_id` 로 고를 수 있는 유니버스 전부(id 오름차순).

        화면 드롭다운의 출처다.
        """
        ...

    def load_universe(self, query: UniverseHistoryQuery) -> UniverseHistoryResult: ...

    def load_panel(self, query: ResearchPanelQuery) -> ResearchPanelResult: ...

    def trading_sessions(self, start: date, end: date) -> tuple[date, ...]:
        """양끝을 포함한 기간의 거래 세션(오름차순). 워크포워드 엠바고가 세션 수를 센다."""
        ...
