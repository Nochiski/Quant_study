from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.factor.facade.evaluation import FactorObservation


@dataclass(frozen=True)
class FactorObservationQuery:
    required_field_ids: tuple[str, ...]
    start: date
    end: date
    minimum_history_sessions: int


class FactorObservationPort(Protocol):
    """PIT observations used by preview; the future Equity DB adapter implements this."""

    def load_factor_observations(
        self, query: FactorObservationQuery
    ) -> tuple[FactorObservation, ...]: ...
