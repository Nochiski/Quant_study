from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class DataLoadStatus(Enum):
    OK = "ok"
    NO_DATA = "no_data"
    INVALID_QUERY = "invalid_query"
    CONFIRMATION_REQUIRED = "confirmation_required"


class CellKind(Enum):
    """A numeric zero and unavailable data must never collapse into one value."""

    OBSERVED = "observed"
    SOURCE_OMITTED_ZERO = "source_omitted_zero"
    MISSING = "missing"
    NOT_COLLECTED = "not_collected"
    COVERAGE_GAP = "coverage_gap"


class FieldValueType(Enum):
    PRICE = "price"
    AMOUNT = "amount"
    RATIO = "ratio"
    COUNT = "count"


@dataclass(frozen=True)
class DatasetRevision:
    dataset_id: str
    revision: str
    as_of: date


@dataclass(frozen=True)
class FieldCoverageCapability:
    starts_on: date
    ends_on: date
    venues: tuple[str, ...]
    estimated_coverage_pct: float
    supported_cell_kinds: tuple[CellKind, ...]
    point_in_time: bool
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if self.starts_on > self.ends_on:
            raise ValueError(
                "field coverage start must be <= end — "
                f"starts_on={self.starts_on} ends_on={self.ends_on}"
            )
        if not 0 <= self.estimated_coverage_pct <= 100:
            raise ValueError(
                "field coverage percentage must be within [0, 100] — "
                f"estimated_coverage_pct={self.estimated_coverage_pct}"
            )
        if not self.venues:
            raise ValueError("field coverage requires at least one venue — venues=()")


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    schema_version: str
    built_at: datetime
    source: str
    point_in_time: bool
    dataset_revisions: tuple[DatasetRevision, ...]


@dataclass(frozen=True)
class DatasetFieldProfile:
    field_id: str
    dataset_id: str
    label: str
    unit: str
    value_type: FieldValueType
    frequency: str
    available_date_basis: str
    recommended_lag_sessions: int
    description: str
    disclosure_basis: str
    evidence: str
    coverage: FieldCoverageCapability

    def __post_init__(self) -> None:
        if not self.field_id or not self.dataset_id:
            raise ValueError(
                "dataset field identifiers must not be empty — "
                f"field_id={self.field_id!r} dataset_id={self.dataset_id!r}"
            )
        if self.recommended_lag_sessions < 0:
            raise ValueError(
                "recommended lag must be >= 0 — "
                f"field_id={self.field_id} lag_sessions={self.recommended_lag_sessions}"
            )


@dataclass(frozen=True)
class SecurityRef:
    security_id: str
    ticker: str
    name: str
    venue: str


@dataclass(frozen=True)
class UniversePoint:
    session: date
    members: tuple[SecurityRef, ...]
    coverage: CellKind = CellKind.OBSERVED


@dataclass(frozen=True)
class UniverseHistoryQuery:
    venue: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"universe start must be <= end — start={self.start} end={self.end}")


@dataclass(frozen=True)
class UniverseHistoryResult:
    points: tuple[UniversePoint, ...]
    status: DataLoadStatus
    snapshot_id: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK


@dataclass(frozen=True)
class FieldLag:
    field_id: str
    sessions: int

    def __post_init__(self) -> None:
        if self.sessions < 0:
            raise ValueError(
                f"field lag must be >= 0 — field_id={self.field_id} sessions={self.sessions}"
            )


@dataclass(frozen=True)
class ResearchPanelQuery:
    start: date
    end: date
    security_ids: tuple[str, ...]
    field_ids: tuple[str, ...]
    lag_overrides: tuple[FieldLag, ...] = ()

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"panel start must be <= end — start={self.start} end={self.end}")
        if not self.security_ids:
            raise ValueError("panel query requires at least one security_id")
        if not self.field_ids:
            raise ValueError("panel query requires at least one field_id")
        for label, values in (
            ("security_ids", self.security_ids),
            ("field_ids", self.field_ids),
            ("lag_overrides", tuple(item.field_id for item in self.lag_overrides)),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"panel query has duplicate {label} — values={values}")


@dataclass(frozen=True)
class ResearchPanelCell:
    as_of: date
    security_id: str
    field_id: str
    source_effective_date: date
    available_date: date
    value: float | None
    kind: CellKind

    def __post_init__(self) -> None:
        if self.kind in (CellKind.OBSERVED, CellKind.SOURCE_OMITTED_ZERO):
            if self.value is None:
                raise ValueError(
                    "observed panel cell requires a value — "
                    f"security_id={self.security_id} field_id={self.field_id} as_of={self.as_of}"
                )
        elif self.value is not None:
            raise ValueError(
                "unavailable panel cell must not carry a value — "
                f"security_id={self.security_id} field_id={self.field_id} "
                f"kind={self.kind.value} value={self.value}"
            )


@dataclass(frozen=True)
class ResearchPanelResult:
    cells: tuple[ResearchPanelCell, ...]
    status: DataLoadStatus
    snapshot_id: str
    warnings: tuple[str, ...] = ()
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK
