"""Compile use case: authoring source → typed StrategySpec with one diagnostic list.

WORKFLOW P1-03. Pipeline (authoring ADR D1/D4):

    source text ─codec─▶ tree + source map ─hydrate─▶ StrategySpec ─validate─▶ semantic issues
                 │ syntax                    │ structural                     │ semantic/capability
                 └──────────── every diagnostic carries a JSON Pointer and, when the source has a
                               node there, a SourceRange ─────────────────────────────────────────

`spec`, `canonical_json` and `spec_hash` are only returned when no error-severity diagnostic
exists; an invalid or stale document never yields something executable.

The compiled spec carries the placeholder identity `draft/0` (`DRAFT_IDENTITY`): identity is
excluded from `spec_hash`, and the save flow (P1-06/P1-07) assigns the real strategy id / revision.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from strategy_workbench.domain.strategy.facade.document import (
    hydrate_strategy_document,
    is_legacy_document,
    upgrade_document_1_0,
)
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

logger = logging.getLogger(__name__)

DRAFT_IDENTITY = StrategyIdentity(strategy_id="draft", revision=0)


@dataclass(frozen=True)
class CompileRequest:
    source: str
    format: SourceFormat


@dataclass(frozen=True)
class UpgradedDocument:
    """A 1.0 source rewritten as 1.1 text plus what that text compiles to (spec D3)."""

    format: SourceFormat
    source: str
    source_hash: str
    compiled: CompiledDocument


class DocumentNotUpgradeableError(ValueError):
    """The source parses but is not a schema 1.0 document, so no upgrade rule applies."""

    def __init__(self, schema_version: object) -> None:
        super().__init__(
            "only schema 1.0 documents can be upgraded — "
            f"schema_version={schema_version!r} expected='1.0'"
        )
        self.schema_version = schema_version


class DocumentUpgradeSyntaxError(ValueError):
    """The source does not parse; the compile outcome carries the syntax diagnostics."""

    def __init__(self, compiled: CompiledDocument) -> None:
        super().__init__("source has syntax errors and cannot be upgraded")
        self.compiled = compiled


class DocumentUpgradeDriftError(RuntimeError):
    """The rewritten text does not parse to the dict-path upgrade: the two paths disagree."""

    def __init__(self, pointer: str, detail: str) -> None:
        super().__init__(
            "upgraded source drifts from the domain upgrade transform — "
            f"pointer={pointer!r} {detail}"
        )
        self.pointer = pointer


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
    contract_hash: str  # covers schema hash + registry version + snapshot id (ETag)
    factor_registry_version: str
    dataset_snapshot_id: str
    fields: tuple[FieldContract, ...]


def contract_hash(schema_hash: str, factor_registry_version: str, dataset_snapshot_id: str) -> str:
    """Identity of one contract representation: the schema plus the catalogs it pairs with."""
    material = f"{schema_hash}\n{factor_registry_version}\n{dataset_snapshot_id}".encode()
    return hashlib.sha256(material).hexdigest()


class StrategyAuthoringService:
    def __init__(
        self,
        codec: DocumentCodecPort,
        *,
        factor_registry_version: str,
        dataset_snapshot_id: Callable[[], str],
    ) -> None:
        self._codec = codec
        self._factor_registry_version = factor_registry_version
        # The equity port owns the snapshot fact: read it per call, never copy it at bootstrap.
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

    @cached_property
    def _fields(self) -> tuple[FieldContract, ...]:
        return strategy_field_contracts()

    def contract(self) -> StrategyDocumentContract:
        schema = self._schema
        snapshot_id = self._dataset_snapshot_id()
        return StrategyDocumentContract(
            schema_version=schema.schema_version,
            schema_hash=schema.schema_hash,
            contract_hash=contract_hash(
                schema.schema_hash, self._factor_registry_version, snapshot_id
            ),
            factor_registry_version=self._factor_registry_version,
            dataset_snapshot_id=snapshot_id,
            fields=self._fields,
        )

    def upgrade(self, request: CompileRequest) -> UpgradedDocument:
        """Rewrite a 1.0 source as 1.1 with comments kept, fail-closed against rule drift."""
        parsed = self._codec.parse(request.source, format=request.format)
        if not parsed.ok or parsed.tree is None:
            raise DocumentUpgradeSyntaxError(_rejected(parsed, None, parsed.diagnostics))
        if not is_legacy_document(parsed.tree):
            raise DocumentNotUpgradeableError(parsed.tree.get("schema_version"))
        expected = upgrade_document_1_0(parsed.tree)
        try:
            upgraded = self._codec.upgrade_source(request.source, format=request.format)
        except Exception as error:  # noqa: BLE001  # reason: 아래 설명대로 어떤 어댑터 실패든 drift다
            # 어댑터의 전제 위반(rt loader가 safe parse와 다르게 읽는 문서, ruamel이 특정 주석
            # 배치에서 던지는 IndexError 등)은 untrusted input에 대한 500이 아니라 drift로 강등한다.
            # safe parse는 이미 통과했으므로 두 경로가 같은 문서를 다르게 봤다는 뜻이고, 응답은
            # 422 계약 안에 있다.
            logger.warning(
                "document upgrade rewrite failed in the codec adapter — treating as drift "
                "(format=%s, error=%s: %s)",
                request.format.value,
                type(error).__name__,
                error,
            )
            raise DocumentUpgradeDriftError(
                "", f"rewrite failed: {type(error).__name__}: {error}"
            ) from error
        reparsed = self._codec.parse(upgraded, format=request.format)
        if not reparsed.ok or reparsed.tree is None:
            detail = ", ".join(f"{d.code}@{d.pointer}" for d in reparsed.diagnostics[:3])
            raise DocumentUpgradeDriftError("", f"rewritten text does not parse: {detail}")
        mismatch = _first_mismatch(expected, reparsed.tree, "")
        if mismatch is not None:
            raise DocumentUpgradeDriftError(*mismatch)
        compiled = self._compile_parsed(reparsed)
        return UpgradedDocument(
            format=request.format,
            source=upgraded,
            source_hash=compiled.source_hash,
            compiled=compiled,
        )

    def compile(self, request: CompileRequest) -> CompiledDocument:
        return self._compile_parsed(self._codec.parse(request.source, format=request.format))

    def _compile_parsed(self, parsed: ParsedDocument) -> CompiledDocument:
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
        # 문서에 명시된 pointer만 넘긴다: 적용 불가 경고는 작성된 값에만 해당한다 (spec D4).
        validation = validate_strategy(spec, written_pointers=parsed.key_ranges.keys())
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


def _first_mismatch(expected: object, actual: object, pointer: str) -> tuple[str, str] | None:
    """Deepest JSON Pointer where two parsed trees differ, or None when they are equal."""
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key in sorted(set(expected) | set(actual)):
            child = f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key not in expected or key not in actual:
                side = "dict-path only" if key in expected else "rewritten text only"
                return child, f"key present in {side}"
            found = _first_mismatch(expected[key], actual[key], child)
            if found is not None:
                return found
        return None
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            return pointer, f"length dict-path={len(expected)} rewritten={len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            found = _first_mismatch(left, right, f"{pointer}/{index}")
            if found is not None:
                return found
        return None
    if expected != actual or type(expected) is not type(actual):
        return pointer, f"dict-path={expected!r} rewritten={actual!r}"
    return None


def _pointer_from_path(path: str) -> str:
    return "" if not path else "/" + "/".join(path.split("."))


def _semantic_kind(kind: ValidationKind) -> DiagnosticKind:
    if kind is ValidationKind.CAPABILITY:
        return DiagnosticKind.CAPABILITY
    if kind is ValidationKind.SYNTAX:  # pragma: no cover - validator never emits syntax today
        return DiagnosticKind.STRUCTURAL
    return DiagnosticKind.SEMANTIC
