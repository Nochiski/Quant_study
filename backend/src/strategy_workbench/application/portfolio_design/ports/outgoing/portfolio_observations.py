from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.portfolio.facade.construction import PortfolioObservation


@dataclass(frozen=True)
class PortfolioObservationQuery:
    start: date
    end: date
    factor_ids: tuple[str, ...]
    field_ids: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioObservationSet:
    data_snapshot_id: str
    sessions: tuple[date, ...]
    observations: tuple[PortfolioObservation, ...]


class PortfolioObservationPort(Protocol):
    def load_portfolio_observations(
        self, query: PortfolioObservationQuery
    ) -> PortfolioObservationSet: ...
