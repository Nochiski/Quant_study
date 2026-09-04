"""Compile use case: authoring source → typed StrategySpec with one diagnostic list.

WORKFLOW P1-03. Pipeline (authoring ADR D1/D4):

    source text ─codec─▶ tree + source map ─hydrate─▶ StrategySpec ─validate─▶ semantic issues
                 │ syntax                    │ structural                     │ semantic/capability
                 └──────────── every diagnostic carries a JSON Pointer and, when the source has a
                               node there, a SourceRange ─────────────────────────────────────────

`spec`, `canonical_json` and `spec_hash` are only returned when no error-severity diagnostic
exists; an invalid or stale document never yields something executable.

The compiled spec carries the placeholder identity `draft/0` (`DRAFT_IDENTITY`): identity is
excluded from `spec_hash`, and the save flow (P2-02) assigns the real strategy id / revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from strategy_workbench.domain.strategy.facade.document import hydrate_strategy_document
from strategy_workbench.domain.strategy.facade.schema import (
    FieldContract,
    strategy_document_schema,
    strategy_document_schema_hash,
    strategy_field_contracts,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
    strategy_spec_hash,
)
from strategy_workbench.domain.strategy.facade.validation import (
    ValidationKind,
    ValidationSeverity,
    validate_strategy,
)

from .ports.outgoing.document_codec import (
    DiagnosticKind,
    DiagnosticSeverity,
    DocumentCodecPort,
    ParsedDocument,
    SourceDiagnostic,
    SourceFormat,
    SourceRange,
)

DRAFT_IDENTITY = StrategyIdentity(strategy_id="draft", revision=0)


@dataclass(frozen=True)
class CompileRequest:
    source: str
    format: SourceFormat


@dataclass(frozen=True)
class CompiledDocument:
    format: SourceFormat
    source_hash: str
    schema_version: str | None
    spec: StrategySpec | None
    canonical_json: str | None
    spec_hash: str | None
    diagnostics: tuple[SourceDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.spec is not None


@dataclass(frozen=True)
class StrategyDocumentSchema:
    """Runtime JSON Schema of the authoring document; `schema_hash` is the ETag."""

    schema_version: str
    schema_hash: str
    schema: dict[str, Any]  # reason: JSON Schema is an open document, not a DTO


@dataclass(frozen=True)
class StrategyDocumentContract:
    """Per-field authoring contract plus the registry versions the schema was built against.

    `factor_registry_version` and `dataset_snapshot_id` identify the catalogs an editor should
    pair with this schema (field ids, factor ids); the HTTP layer adds their links.
    """

    schema_version: str
    schema_hash: str
    factor_registry_version: str
    dataset_snapshot_id: str
    fields: tuple[FieldContract, ...]


class StrategyAuthoringService:
    def __init__(
        self,
        codec: DocumentCodecPort,
        *,
        factor_registry_version: str,
        dataset_snapshot_id: str,
    ) -> None:
        self._codec = codec
        self._factor_registry_version = factor_registry_version
        self._dataset_snapshot_id = dataset_snapshot_id

    @cached_property
    def _schema(self) -> StrategyDocumentSchema:
        schema = strategy_document_schema()
        version = schema["properties"]["schema_version"]
        return StrategyDocumentSchema(
            schema_version=version.get("const") or max(version["enum"]),
            schema_hash=strategy_document_schema_hash(schema),
            schema=schema,
        )

    def schema(self) -> StrategyDocumentSchema:
        """Runtime schema derived from the model and the constraint catalog (pure, cached)."""
        return self._schema

    def contract(self) -> StrategyDocumentContract:
        schema = self._schema
        return StrategyDocumentContract(
            schema_version=schema.schema_version,
            schema_hash=schema.schema_hash,
            factor_registry_version=self._factor_registry_version,
            dataset_snapshot_id=self._dataset_snapshot_id,
            fields=strategy_field_contracts(),
        )

    def compile(self, request: CompileRequest) -> CompiledDocument:
        parsed = self._codec.parse(request.source, format=request.format)
        if not parsed.ok or parsed.tree is None:
            return _rejected(parsed, None, parsed.diagnostics)

        raw_version = parsed.tree.get("schema_version")
        schema_version = raw_version if isinstance(raw_version, str) else None
        hydration = hydrate_strategy_document(parsed.tree, identity=DRAFT_IDENTITY)
        if not hydration.ok or hydration.spec is None:
            diagnostics = tuple(
                SourceDiagnostic(
                    code=issue.code,
                    kind=DiagnosticKind.STRUCTURAL,
                    pointer=issue.pointer,
                    message=issue.message,
                    severity=DiagnosticSeverity.ERROR,
                    range=_structural_range(parsed, issue.code, issue.pointer),
                )
                for issue in hydration.issues
            )
            return _rejected(parsed, schema_version, diagnostics)

        spec = hydration.spec
        validation = validate_strategy(spec)
        diagnostics = tuple(
            SourceDiagnostic(
                code=issue.code,
                kind=_semantic_kind(issue.kind),
                pointer=_pointer_from_path(issue.path),
                message=issue.message,
                range=parsed.locate(_pointer_from_path(issue.path)),
                severity=(
                    DiagnosticSeverity.ERROR
                    if issue.severity is ValidationSeverity.ERROR
                    else DiagnosticSeverity.WARNING
                ),
                node_id=issue.node_id,
            )
            for issue in validation.issues
        )
        if not validation.valid:
            return _rejected(parsed, schema_version, diagnostics)
        return CompiledDocument(
            format=parsed.format,
            source_hash=parsed.source_hash,
            schema_version=spec.identity.schema_version,
            spec=spec,
            canonical_json=canonical_strategy_json(spec),
            spec_hash=strategy_spec_hash(spec),
            diagnostics=diagnostics,
        )


def _rejected(
    parsed: ParsedDocument,
    schema_version: str | None,
    diagnostics: tuple[SourceDiagnostic, ...],
) -> CompiledDocument:
    return CompiledDocument(
        format=parsed.format,
        source_hash=parsed.source_hash,
        schema_version=schema_version,
        spec=None,
        canonical_json=None,
        spec_hash=None,
        diagnostics=diagnostics,
    )


def _structural_range(parsed: ParsedDocument, code: str, pointer: str) -> SourceRange | None:
    # An unknown key exists in the source: point at the key itself, not its value.
    if code == "structure.unknown_key" and pointer in parsed.key_ranges:
        return parsed.key_ranges[pointer]
    return parsed.locate(pointer)


def _pointer_from_path(path: str) -> str:
    return "" if not path else "/" + "/".join(path.split("."))


def _semantic_kind(kind: ValidationKind) -> DiagnosticKind:
    if kind is ValidationKind.CAPABILITY:
        return DiagnosticKind.CAPABILITY
    if kind is ValidationKind.SYNTAX:  # pragma: no cover - validator never emits syntax today
        return DiagnosticKind.STRUCTURAL
    return DiagnosticKind.SEMANTIC
