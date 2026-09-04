"""Document save/get/history use case (WORKFLOW P1-07).

Only a source that compiles without error-severity diagnostics is stored, and it is stored
exactly (text, format, `source_hash`) inside the revision envelope (P1-06). Identity is assigned
here, outside the source (authoring ADR D3). A legacy revision created through the JSON spec API
has no source; `get` regenerates a canonical JSON document for it and says so (`generated=True`,
`origin=legacy_json`) so an editor never mistakes the projection for the author's text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    RevisionSummary,
    StrategyRepositoryPort,
    StrategyRevisionRecord,
)
from strategy_workbench.domain.strategy.facade.diff import DiffEntry, diff_strategy_specs
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
)

from ._service import CompiledDocument, CompileRequest, StrategyAuthoringService
from .ports.outgoing.document_codec import SourceFormat, source_hash_of


@dataclass(frozen=True)
class SaveDocumentRequest:
    source: str
    format: SourceFormat


@dataclass(frozen=True)
class ReviseDocumentRequest:
    source: str
    format: SourceFormat
    expected_revision: int


@dataclass(frozen=True)
class StrategyDocument:
    """A stored revision as an editor sees it: exact source plus what it compiles to.

    `generated` is True when the revision predates document authoring (legacy JSON API) and the
    source shown is a canonical JSON projection of the stored spec, not text an author wrote.
    """

    strategy_id: str
    revision: int
    format: SourceFormat
    source: str
    source_hash: str
    spec: StrategySpec
    spec_hash: str
    schema_version: str
    origin: RevisionOrigin
    created_at: datetime
    generated: bool


@dataclass(frozen=True)
class RevisionDiff:
    """Semantic differences between two stored revisions of one strategy (P1-08).

    Computed over canonical payloads: identity, comments and formatting are invisible;
    equal `spec_hash` implies `changes == ()`.
    """

    strategy_id: str
    base_revision: int
    target_revision: int
    base_spec_hash: str
    target_spec_hash: str
    changes: tuple[DiffEntry, ...]


class InvalidStrategyDocumentError(ValueError):
    """The source did not compile cleanly; `compiled.diagnostics` says where."""

    def __init__(self, compiled: CompiledDocument) -> None:
        super().__init__(
            "strategy document has error diagnostics — "
            f"count={len(compiled.diagnostics)} source_hash={compiled.source_hash}"
        )
        self.compiled = compiled


class StrategyDocumentService:
    def __init__(
        self,
        authoring: StrategyAuthoringService,
        repository: StrategyRepositoryPort,
        *,
        new_id: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._authoring = authoring
        self._repository = repository
        self._new_id = new_id
        self._now = now

    def save(self, request: SaveDocumentRequest) -> StrategyDocument:
        record = self._record(request.source, request.format, StrategyIdentity(self._new_id(), 1))
        self._repository.add(record)
        return _view(record)

    def revise(self, strategy_id: str, request: ReviseDocumentRequest) -> StrategyDocument:
        record = self._record(
            request.source,
            request.format,
            StrategyIdentity(strategy_id, request.expected_revision + 1),
        )
        self._repository.append(record, expected_revision=request.expected_revision)
        return _view(record)

    def get(self, strategy_id: str, revision: int | None = None) -> StrategyDocument:
        return _view(self._repository.get(strategy_id, revision))

    def history(self, strategy_id: str, page: PageRequest) -> Page[RevisionSummary]:
        return self._repository.history(strategy_id, page)

    def diff(self, strategy_id: str, base_revision: int, target_revision: int) -> RevisionDiff:
        base = self._repository.get(strategy_id, base_revision)
        target = self._repository.get(strategy_id, target_revision)
        return RevisionDiff(
            strategy_id=strategy_id,
            base_revision=base.revision,
            target_revision=target.revision,
            base_spec_hash=base.spec_hash,
            target_spec_hash=target.spec_hash,
            changes=diff_strategy_specs(base.spec, target.spec),
        )

    def _record(
        self, source: str, format: SourceFormat, identity: StrategyIdentity
    ) -> StrategyRevisionRecord:
        compiled = self._authoring.compile(CompileRequest(source, format))
        if not compiled.ok or compiled.spec is None or compiled.spec_hash is None:
            raise InvalidStrategyDocumentError(compiled)
        spec = replace(
            compiled.spec,
            identity=replace(identity, schema_version=compiled.spec.identity.schema_version),
        )
        return StrategyRevisionRecord(
            spec=spec,
            spec_hash=compiled.spec_hash,  # identity is excluded from the hash (ADR D1)
            source=RevisionSource(format, source, compiled.source_hash),
            provenance=RevisionProvenance(RevisionOrigin.DOCUMENT, self._now()),
        )


def _view(record: StrategyRevisionRecord) -> StrategyDocument:
    if record.source is not None:
        format, source, source_hash, generated = (
            record.source.format,
            record.source.text,
            record.source.source_hash,
            False,
        )
    else:
        source = canonical_strategy_json(record.spec, indent=2) + "\n"
        format, source_hash, generated = SourceFormat.JSON, source_hash_of(source), True
    return StrategyDocument(
        strategy_id=record.strategy_id,
        revision=record.revision,
        format=format,
        source=source,
        source_hash=source_hash,
        spec=record.spec,
        spec_hash=record.spec_hash,
        schema_version=record.spec.identity.schema_version,
        origin=record.provenance.origin,
        created_at=record.provenance.created_at,
        generated=generated,
    )
