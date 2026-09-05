"""Outbound port owned by the strategy design use case: immutable strategy revisions.

WORKFLOW P1-06. A revision is stored as an envelope (`StrategyRevisionRecord`), not as a bare
StrategySpec: the typed spec (execution SoT), its `spec_hash`, the exact authoring source text
with its format and `source_hash` when the revision came from a document, and provenance.

- Revisions are immutable: revision N never changes after revision N+1 exists.
- A DOCUMENT record's `spec` must be what its `source` compiles to. Only the document use case
  builds such records, from a compile result of that exact text; never pair them by hand.
- `source is None` marks a legacy revision created through the JSON spec API; the document API
  (P1-07) regenerates a canonical source for it and reports the provenance.
- Identity (`strategy_id`, `revision`) lives on the spec's `StrategyIdentity`; it is assigned by
  the use case, never parsed from the source (authoring ADR D3).
- Optimistic concurrency: `append` requires the caller's `expected_revision` to equal the latest
  stored revision, otherwise `StrategyRevisionConflictError`.
- `list_strategies` / `history` are deterministic and paginated: strategies ascend by
  `strategy_id`, revisions ascend by `revision`; `Page.total` is the unpaginated count.

Every adapter must pass `tests/contract/test_strategy_repository.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Generic, Protocol, TypeVar

from strategy_workbench.domain.strategy.facade.document import SourceFormat, source_hash_of
from strategy_workbench.domain.strategy.facade.specification import (
    StrategySpec,
    strategy_spec_hash,
)

T = TypeVar("T")


class StrategyNotFoundError(LookupError):
    pass


class StrategyRevisionConflictError(RuntimeError):
    """Optimistic revision conflict with the repository-owned latest revision, when known."""

    def __init__(self, message: str, *, latest_revision: int | None = None) -> None:
        super().__init__(message)
        self.latest_revision = latest_revision


class RevisionOrigin(StrEnum):
    DOCUMENT = "document"  # compiled from an exact YAML/JSON source (P1-03 compile)
    LEGACY_JSON = "legacy_json"  # created through the JSON StrategySpec API (Quick/Advanced)


@dataclass(frozen=True)
class RevisionSource:
    """Exact authoring text of a revision; `source_hash` is derived from `text`, never supplied."""

    format: SourceFormat
    text: str
    source_hash: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError(f"revision source text must not be empty — format={self.format.value}")
        expected = source_hash_of(self.text)
        if self.source_hash != expected:
            raise ValueError(
                "revision source hash does not match its text — "
                f"format={self.format.value} given={self.source_hash} expected={expected}"
            )


@dataclass(frozen=True)
class RevisionProvenance:
    """Adapters normalise clocks with `astimezone(UTC)` before building a record."""

    origin: RevisionOrigin
    created_at: datetime
    change_note: str | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError(
                f"revision created_at must be timezone-aware UTC — got={self.created_at!r}"
            )


@dataclass(frozen=True)
class StrategyRevisionRecord:
    """One immutable revision: typed spec + hash + optional exact source + provenance."""

    spec: StrategySpec
    spec_hash: str
    source: RevisionSource | None
    provenance: RevisionProvenance

    @property
    def strategy_id(self) -> str:
        return self.spec.identity.strategy_id

    @property
    def revision(self) -> int:
        return self.spec.identity.revision

    def __post_init__(self) -> None:
        expected = strategy_spec_hash(self.spec)
        if self.spec_hash != expected:
            raise ValueError(
                "revision spec_hash does not match its spec — "
                f"strategy_id={self.strategy_id} revision={self.revision} "
                f"given={self.spec_hash} expected={expected}"
            )
        if self.source is not None and self.provenance.origin is not RevisionOrigin.DOCUMENT:
            raise ValueError(
                "a revision with source text must have document provenance — "
                f"strategy_id={self.strategy_id} revision={self.revision} "
                f"origin={self.provenance.origin.value}"
            )
        if self.source is None and self.provenance.origin is RevisionOrigin.DOCUMENT:
            raise ValueError(
                "document provenance requires the source text — "
                f"strategy_id={self.strategy_id} revision={self.revision}"
            )


@dataclass(frozen=True)
class PageRequest:
    offset: int = 0
    limit: int = 50
    MAX_LIMIT: ClassVar[int] = 500

    def __post_init__(self) -> None:
        if self.offset < 0 or not 1 <= self.limit <= self.MAX_LIMIT:
            raise ValueError(
                f"page request out of range — offset={self.offset} limit={self.limit} "
                f"max_limit={self.MAX_LIMIT}"
            )


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class StrategySummary:
    strategy_id: str
    title: str
    latest_revision: int
    spec_hash: str
    updated_at: datetime


@dataclass(frozen=True)
class RevisionSummary:
    strategy_id: str
    revision: int
    spec_hash: str
    origin: RevisionOrigin
    created_at: datetime
    source_format: SourceFormat | None
    source_hash: str | None
    change_note: str | None = None


class StrategyRepositoryPort(Protocol):
    def add(self, record: StrategyRevisionRecord) -> None:
        """Store revision 1 of a new strategy; conflict if the strategy exists."""
        ...

    def append(self, record: StrategyRevisionRecord, *, expected_revision: int) -> None:
        """Store the next revision; conflict unless the latest stored revision is expected."""
        ...

    def get(self, strategy_id: str, revision: int | None = None) -> StrategyRevisionRecord:
        """Return one revision (latest when None); StrategyNotFoundError otherwise."""
        ...

    def list_strategies(self, page: PageRequest) -> Page[StrategySummary]: ...

    def history(self, strategy_id: str, page: PageRequest) -> Page[RevisionSummary]: ...
