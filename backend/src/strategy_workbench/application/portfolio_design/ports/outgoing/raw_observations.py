"""Outgoing port: raw point-in-time observations for the truthful preview/backtest pipeline.

WORKFLOW P1.5-03. The pipeline (P1.5-04) evaluates FactorGraphs itself, so the adapter hands over
only *raw* facts, never factor values: snapshot id, sessions, historical universe membership, raw
field values with their publication date, sector/group, and warm-up history before `start`.

Contract:

- `available_date` is the date the value became public (source publication date). Every field
  returned for `as_of` satisfies `available_date <= as_of`; a value published later is omitted,
  never leaked. The application layer treats a violation as an adapter bug (fail-closed).
- A field is *omitted* when nothing was published as of `as_of`; `value=None` means the source
  observed a missing/uncollected cell (the value exists as a fact, and it is "no number").
- `universe_member` is a fact of (as_of, security) alone: the same pair answers the same way
  regardless of the query window or history length (window invariance).
- `history_sessions_before_start` counts sessions strictly before `start` (as_of is not one of
  them). The adapter is responsible for field lag: a lagged field must still be present on the
  first history session when the source has data there.
- `previous_weight` is the book the strategy carries into the *first* rebalance frame. From
  the second frame on the portfolio compiler folds the previous frame's targets forward and
  ignores this value entirely, so an adapter that cannot answer returns 0.0 rather than a
  guess. `universe_member` and `sector_id` have no `available_date`: answering them with the
  as_of vintage (no retroactive reclassification or index reconstitution) is the adapter's
  responsibility and the application layer cannot verify it.
- Failures are values: `status != OK` with `detail` (unknown universe/field, no data), never a
  synthesised observation.
- Long-running adapters invoke the supplied cancellation checkpoint at bounded row batches. The
  application owns the exception raised by that callback; the adapter only yields cooperatively.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus

RawFieldValueType = float | str | bool | None


@dataclass(frozen=True)
class RawObservationQuery:
    market: str
    universe_id: str
    start: date
    end: date
    field_ids: tuple[str, ...]
    history_sessions_before_start: int = 0

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(
                f"raw observation start must be <= end — start={self.start} end={self.end}"
            )
        if self.history_sessions_before_start < 0:
            raise ValueError(
                "raw observation history_sessions_before_start must be >= 0 — "
                f"got={self.history_sessions_before_start}"
            )
        if not self.market or not self.universe_id:
            raise ValueError(
                "raw observation query needs market and universe_id — "
                f"market={self.market!r} universe_id={self.universe_id!r}"
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
    """Observations for `sessions` (in range) plus `history_sessions` warm-up before `start`.

    Observations are ordered by (as_of, security_id) and unique per pair; sessions ascend and
    every history session precedes the first requested session; every observation date is one of
    the declared sessions or history sessions. Any adapter gets these checks for free through
    `__post_init__`.
    """

    status: DataLoadStatus
    data_snapshot_id: str
    sessions: tuple[date, ...]
    history_sessions: tuple[date, ...]
    observations: tuple[RawObservation, ...]
    detail: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK

    def __post_init__(self) -> None:
        if list(self.sessions) != sorted(set(self.sessions)):
            raise ValueError(f"raw observation sessions must ascend — sessions={self.sessions}")
        if list(self.history_sessions) != sorted(set(self.history_sessions)):
            raise ValueError(
                f"raw observation history must ascend — history={self.history_sessions}"
            )
        overlaps = bool(self.sessions and self.history_sessions)
        if overlaps and self.history_sessions[-1] >= self.sessions[0]:
            raise ValueError(
                "raw observation history must precede the requested range — "
                f"last_history={self.history_sessions[-1]} first_session={self.sessions[0]}"
            )
        keys = [(item.as_of, item.security_id) for item in self.observations]
        if keys != sorted(set(keys)):
            raise ValueError(
                "raw observations must be ordered by (as_of, security_id) and unique — "
                f"count={len(keys)}"
            )
        # The evaluator counts lag and rolling windows by row position, not by calendar, so an
        # undeclared date silently shifts every window behind it (D-003). Fail closed instead.
        declared = set(self.sessions) | set(self.history_sessions)
        undeclared = sorted({item.as_of for item in self.observations} - declared)
        if undeclared:
            raise ValueError(
                "raw observations carry dates that are neither a session nor warm-up history — "
                f"undeclared={undeclared[:5]} undeclared_count={len(undeclared)} "
                f"declared_sessions={len(self.sessions)} "
                f"declared_history={len(self.history_sessions)} "
                f"snapshot={self.data_snapshot_id!r}"
            )


class RawObservationPort(Protocol):
    def load_raw_observations(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None] = lambda: None,
    ) -> RawObservationSet: ...
