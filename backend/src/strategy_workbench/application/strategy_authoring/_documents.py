"""Document save/get/history use case (WORKFLOW P1-07).

Only a source that compiles without error-severity diagnostics is stored, and it is stored
exactly (text, format, `source_hash`) inside the revision envelope (P1-06). Identity is assigned
here, outside the source (authoring ADR D3). JSON spec API 로 만든 legacy revision 은 원문이
없어, `get` 이 승격을 걷은 현재 판 문서(`authoring_document`)를 만들어 주고 그 사실을 알린다
(`generated=True`, `origin=legacy_json`) — 편집기가 그 글을 작성자의 원문으로 오해하지 않게 한다.
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
    StrategySummary,
)
from strategy_workbench.domain.strategy.facade.diff import DiffEntry, diff_strategy_specs
from strategy_workbench.domain.strategy.facade.document import authoring_document
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
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

    `generated` 는 revision 이 문서 저작 이전(legacy JSON API)이라 보이는 원문이 작성자가 쓴 글이
    아니라 저장된 spec 에서 만든 문서일 때 참이다 — 승격을 걷은 현재 판 문서
    (`authoring_document`)다.
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
    # 은퇴한 schema 버전으로 저장된 동결 revision (spec D2). 편집기는 업그레이드 후 새 revision
    # 으로만 실행할 수 있다고 안내하고, saved-reference 실행은 422로 거부된다.
    requires_upgrade: bool


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

    def list_strategies(self, page: PageRequest) -> Page[StrategySummary]:
        return self._repository.list_strategies(page)

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
        # 원문 없는 legacy JSON revision 은 spec 에서 사용자 문서를 다시 만든다 — 현재 판이고
        # compile 이 붙인 승격 노드가 없다(`authoring_document`, spec D2·#353).
        source = authoring_document(record.spec)
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
        requires_upgrade=record.requires_upgrade,
    )
