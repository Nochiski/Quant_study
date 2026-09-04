"""Outgoing port: raw point-in-time observations for the truthful preview/backtest pipeline.

WORKFLOW P1.5-03. The pipeline (P1.5-04) evaluates FactorGraphs itself, so the adapter must hand
over only *raw* facts, never factor values: snapshot id, sessions, historical universe membership,
raw field values with their availability date, sector/group, previous weight, and enough warm-up
history for the factor graph's minimum history. Every field value returned for `as_of` satisfies
`available_date <= as_of`; a value published later is not visible earlier (no look-ahead).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

RawFieldValueType = float | str | bool | None


@dataclass(frozen=True)
class RawObservationQuery:
    start: date
    end: date
    field_ids: tuple[str, ...]
    minimum_history_sessions: int = 0

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(
                f"raw observation start must be <= end — start={self.start} end={self.end}"
            )
        if self.minimum_history_sessions < 0:
            raise ValueError(
                "raw observation minimum_history_sessions must be >= 0 — "
                f"got={self.minimum_history_sessions}"
            )


@dataclass(frozen=True)
class RawFieldValue:
    field_id: str
    value: RawFieldValueType
    available_date: date


@dataclass(frozen=True)
class RawObservation:
    as_of: date
    security_id: str
    universe_member: bool
    fields: tuple[RawFieldValue, ...]
    sector_id: str | None = None
    previous_weight: float = 0.0


@dataclass(frozen=True)
class RawObservationSet:
    """Observations for `sessions` (in range) plus `history_sessions` warm-up before `start`."""

    data_snapshot_id: str
    sessions: tuple[date, ...]
    history_sessions: tuple[date, ...]
    observations: tuple[RawObservation, ...]


class RawObservationPort(Protocol):
    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet: ...
