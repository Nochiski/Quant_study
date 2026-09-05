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
  Every observation contains at most one non-blank `field_id`; duplicate identities are invalid
  because evaluators cannot consistently choose which raw fact is authoritative.
  Numeric values and `previous_weight` are always finite; NaN/±Infinity is an adapter contract
  violation and is rejected before factor or portfolio calculation rather than normalized.
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
- ``RawObservationPort`` preserves the original preview/backtest call contract. Adapters that can
  cooperatively cancel a long trace additionally implement ``CancellableRawObservationPort``;
  the application negotiates that capability before metadata or raw calculation starts. Those
  adapters pass the same callback as ``validation_checkpoint`` when constructing the result, and
  the application passes it again when revalidating at the consumer boundary.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import InitVar, dataclass
from datetime import date
from typing import Protocol, TypeVar, runtime_checkable

from strategy_workbench.domain.equity.facade.research_data import CellKind, DataLoadStatus

RawFieldValueType = float | str | bool | None
_T = TypeVar("_T")
_CHECKPOINT_BATCH = 256


class RawObservationContractViolation(ValueError):
    """An adapter attempted to publish a structurally or numerically invalid raw snapshot."""


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _noop_checkpoint() -> None:
    pass


def _checkpointed(items: Iterable[_T], checkpoint: Callable[[], None]) -> Iterator[_T]:
    for index, item in enumerate(items):
        if index % _CHECKPOINT_BATCH == 0:
            checkpoint()
        yield item


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
    kind: CellKind = CellKind.OBSERVED

    def __post_init__(self) -> None:
        if not isinstance(self.field_id, str) or not self.field_id.strip():
            raise RawObservationContractViolation(
                "raw field_id must not be blank — "
                f"field_id={self.field_id!r} available_date={self.available_date}"
            )
        if self.kind in (CellKind.OBSERVED, CellKind.SOURCE_OMITTED_ZERO):
            if self.value is None:
                raise RawObservationContractViolation(
                    "observed raw field requires a value — "
                    f"field_id={self.field_id!r} available_date={self.available_date} "
                    f"kind={self.kind.value!r}"
                )
        elif self.value is not None:
            raise RawObservationContractViolation(
                "unavailable raw field must not carry a value — "
                f"field_id={self.field_id!r} available_date={self.available_date} "
                f"kind={self.kind.value!r} value={self.value!r}"
            )


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
    validation_checkpoint: InitVar[Callable[[], None] | None] = None

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK

    def __post_init__(self, validation_checkpoint: Callable[[], None] | None) -> None:
        self.validate_contract(checkpoint=validation_checkpoint or _noop_checkpoint)

    def validate_contract(self, *, checkpoint: Callable[[], None] = _noop_checkpoint) -> None:
        """Validate at construction and consumer boundaries with bounded cancellation checks."""
        checkpoint()
        if list(self.sessions) != sorted(set(self.sessions)):
            raise RawObservationContractViolation(
                f"raw observation sessions must ascend — sessions={self.sessions}"
            )
        if list(self.history_sessions) != sorted(set(self.history_sessions)):
            raise RawObservationContractViolation(
                f"raw observation history must ascend — history={self.history_sessions}"
            )
        overlaps = bool(self.sessions and self.history_sessions)
        if overlaps and self.history_sessions[-1] >= self.sessions[0]:
            raise RawObservationContractViolation(
                "raw observation history must precede the requested range — "
                f"last_history={self.history_sessions[-1]} first_session={self.sessions[0]}"
            )
        keys = [
            (item.as_of, item.security_id) for item in _checkpointed(self.observations, checkpoint)
        ]
        if keys != sorted(set(keys)):
            raise RawObservationContractViolation(
                "raw observations must be ordered by (as_of, security_id) and unique — "
                f"count={len(keys)}"
            )
        # The evaluator counts lag and rolling windows by row position, not by calendar, so an
        # undeclared date silently shifts every window behind it (D-003). Fail closed instead.
        declared = set(self.sessions) | set(self.history_sessions)
        undeclared = sorted(
            {item.as_of for item in _checkpointed(self.observations, checkpoint)} - declared
        )
        if undeclared:
            raise RawObservationContractViolation(
                "raw observations carry dates that are neither a session nor warm-up history — "
                f"undeclared={undeclared[:5]} undeclared_count={len(undeclared)} "
                f"declared_sessions={len(self.sessions)} "
                f"declared_history={len(self.history_sessions)} "
                f"snapshot={self.data_snapshot_id!r}"
            )
        for observation in _checkpointed(self.observations, checkpoint):
            if not _finite_number(observation.previous_weight):
                raise RawObservationContractViolation(
                    "raw observation previous_weight must be finite — "
                    f"as_of={observation.as_of} security_id={observation.security_id!r} "
                    f"value={observation.previous_weight!r} snapshot={self.data_snapshot_id!r}"
                )
            field_ids: set[str] = set()
            for field in _checkpointed(observation.fields, checkpoint):
                if not isinstance(field.field_id, str) or not field.field_id.strip():
                    raise RawObservationContractViolation(
                        "raw field_id must not be blank — "
                        f"as_of={observation.as_of} security_id={observation.security_id!r} "
                        f"field_id={field.field_id!r} snapshot={self.data_snapshot_id!r}"
                    )
                if field.field_id in field_ids:
                    raise RawObservationContractViolation(
                        "raw observation field_ids must be unique — "
                        f"as_of={observation.as_of} security_id={observation.security_id!r} "
                        f"field_id={field.field_id!r} snapshot={self.data_snapshot_id!r}"
                    )
                field_ids.add(field.field_id)
                value = field.value
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if not _finite_number(value):
                        raise RawObservationContractViolation(
                            "raw numeric field value must be finite — "
                            f"as_of={observation.as_of} security_id={observation.security_id!r} "
                            f"field_id={field.field_id!r} value={value!r} "
                            f"snapshot={self.data_snapshot_id!r}"
                        )


class RawObservationPort(Protocol):
    def load_raw_observations(
        self,
        query: RawObservationQuery,
    ) -> RawObservationSet: ...


@runtime_checkable
class CancellableRawObservationPort(Protocol):
    """Optional trace capability; callback exception policy remains application-owned."""

    def load_raw_observations_cancellable(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
    ) -> RawObservationSet: ...
