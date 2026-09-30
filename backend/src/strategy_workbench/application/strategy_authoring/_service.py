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
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import cached_property
from typing import Any

from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_from_retired_settings,
    run_environment_schema,
    run_environment_schema_hash,
)
from strategy_workbench.domain.factor.facade.expression import FieldMetadata
from strategy_workbench.domain.factor.facade.operators import (
    OperatorDefinition,
    operator_definitions,
)
from strategy_workbench.domain.strategy.facade.document import (
    ENVIRONMENT_UNAVAILABLE_CODE,
    LEGACY_SHAPE_CODE,
    NotUpgradeableDocumentError,
    RetiredExecutionSettings,
    UpgradeUnsupportedNodeError,
    UpgradeWarning,
    hydrate_strategy_document,
    upgrade_document,
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
    DiagnosticAnchor,
    DiagnosticKind,
    DiagnosticSeverity,
    DocumentCodecPort,
    ParsedDocument,
    SourceDiagnostic,
    SourceFormat,
)
from .ports.outgoing.field_catalog import FieldCatalogPort

logger = logging.getLogger(__name__)

DRAFT_IDENTITY = StrategyIdentity(strategy_id="draft", revision=0)


@dataclass(frozen=True)
class CompileRequest:
    source: str
    format: SourceFormat


@dataclass(frozen=True)
class UpgradedDocument:
    """은퇴 schema 원문을 현재 버전으로 다시 쓴 결과와 그 원문의 compile 결과(spec D3·D7).

    `environment` 는 옛 문서의 `data`·`execution`·`missing_policy` 로 만든 실행 설정이다. 옮기지
    못했으면(값이 없거나 읽히지 않음) 비어 있고, 그 사유는 `warnings` 가 자리와 함께 짚는다 —
    기본값으로 지어내지 않는다. 화면은 이 값으로 실행 설정을 채운다(P3-02).
    """

    format: SourceFormat
    source: str
    source_hash: str
    compiled: CompiledDocument
    environment: RunEnvironment | None
    warnings: tuple[UpgradeWarning, ...]


class DocumentNotUpgradeableError(ValueError):
    """원문은 읽히지만 적용할 업그레이드 체인이 없다.

    현재 버전이거나 모르는 버전이거나, 단계가 옛 모양을 남겼다.
    """

    def __init__(self, schema_version: object, detail: str) -> None:
        super().__init__(detail)
        self.schema_version = schema_version


class DocumentUpgradeUnsupportedNodeError(ValueError):
    """1.2 에 없는 `saved_*` 노드가 있어 업그레이드를 거절한다(spec D7)."""

    def __init__(self, pointer: str, detail: str) -> None:
        super().__init__(detail)
        self.pointer = pointer


class DocumentUpgradeSyntaxError(ValueError):
    """The source does not parse; the compile outcome carries the syntax diagnostics."""

    def __init__(self, compiled: CompiledDocument) -> None:
        codes = [diagnostic.code for diagnostic in compiled.diagnostics[:3]]
        super().__init__(
            "source has syntax errors and cannot be upgraded — "
            f"format={compiled.format.value} source_hash={compiled.source_hash} codes={codes}"
        )
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
class RunEnvironmentSchema:
    """실행 설정(`RunEnvironment`)의 런타임 JSON Schema; `schema_hash` 가 ETag 다.

    전략 authoring 문서 스키마(`StrategyDocumentSchema`)와 별개 산출물이다 — 문서에는
    `schema_version` 이 있고 실행 설정에는 없다. 프론트 실행 설정 패널이 기본값·enum 을 손으로
    적지 않게 하는 경로다(spec D6).
    """

    schema_hash: str
    schema: dict[str, Any]  # reason: JSON Schema 는 DTO 가 아니라 열린 문서다


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


@dataclass(frozen=True)
class StrategyOperatorCatalog:
    """그래프 노드 연산자 정의 전부 (P1-03, spec D8). `catalog_hash`가 ETag다.

    문장은 담지 않는다. 소비자는 `description_key`·`formula_key`를 자기 로케일 사전에서 찾고,
    연산자 목록·arity·가용성을 손으로 적지 않는다. `availability` 는 연결된 어댑터 capability 로
    판정하므로(P2-07) 해시도 어댑터에 따라 다르다 — 어댑터를 바꾸면 ETag 가 바뀐다.
    """

    catalog_hash: str
    operators: tuple[OperatorDefinition, ...]


def operator_catalog_hash(operators: tuple[OperatorDefinition, ...]) -> str:
    """정의 전부의 canonical JSON sha256. 정의가 하나라도 바뀌면 ETag가 바뀐다."""
    material = json.dumps(
        [asdict(definition) for definition in operators],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
        field_catalog: FieldCatalogPort | None = None,
    ) -> None:
        """`field_catalog` 는 연결된 equity 어댑터다(P2-07, spec D5).

        주면 compile 이 그 필드 계약으로 없는 `field_id` 와 연산자 capability 를 판정하고, 연산자
        카탈로그의 `availability` 도 같은 capability 로 답한다. 어댑터가 없는 컨텍스트(CLI·테스트,
        저장소 무결성 검사)는 None 이고 계약 없이 검증한다.
        """
        self._codec = codec
        self._factor_registry_version = factor_registry_version
        # The equity port owns the snapshot fact: read it per call, never copy it at bootstrap.
        self._dataset_snapshot_id = dataset_snapshot_id
        # 필드 계약도 어댑터가 소유한다 — 매 호출 읽고 복사해 두지 않는다.
        self._field_catalog = field_catalog

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
    def _run_environment_schema(self) -> RunEnvironmentSchema:
        schema = run_environment_schema()
        return RunEnvironmentSchema(schema_hash=run_environment_schema_hash(schema), schema=schema)

    def run_environment_schema(self) -> RunEnvironmentSchema:
        """실행 설정의 런타임 스키마(`domain/backtest` 소유 모델에서 유도, 순수·캐시)."""
        return self._run_environment_schema

    def operators(self) -> StrategyOperatorCatalog:
        """연산자 정의 카탈로그. 가용성은 연결된 어댑터 capability 로 판정한다(P2-07)."""
        fields = self._fields_or_none()
        operators = operator_definitions(
            None if fields is None else {field.value_type for field in fields}
        )
        return StrategyOperatorCatalog(
            catalog_hash=operator_catalog_hash(operators), operators=operators
        )

    def _fields_or_none(self) -> tuple[FieldMetadata, ...] | None:
        if self._field_catalog is None:
            return None
        return self._field_catalog.factor_field_catalog()

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
        """은퇴 schema 원문을 현재 버전으로 다시 쓴다.

        주석은 남기고, 두 변환 경로가 어긋나면 거절한다.
        """
        parsed = self._codec.parse(request.source, format=request.format)
        if not parsed.ok or parsed.tree is None:
            raise DocumentUpgradeSyntaxError(_rejected(parsed, None, parsed.diagnostics))
        try:
            outcome = upgrade_document(parsed.tree)
        except UpgradeUnsupportedNodeError as error:
            raise DocumentUpgradeUnsupportedNodeError(error.pointer, str(error)) from error
        except NotUpgradeableDocumentError as error:
            raise DocumentNotUpgradeableError(error.schema_version, str(error)) from error
        expected = outcome.tree
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
        environment, environment_warnings = _environment_of(outcome.environment)
        return UpgradedDocument(
            format=request.format,
            source=upgraded,
            source_hash=compiled.source_hash,
            compiled=compiled,
            environment=environment,
            warnings=(*outcome.warnings, *environment_warnings),
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
                _structural_diagnostic(parsed, issue.code, issue.pointer, issue.message)
                for issue in hydration.issues
            )
            return _rejected(parsed, schema_version, diagnostics)

        spec = hydration.spec
        # 문서에 명시된 pointer만 넘긴다: 적용 불가 경고는 작성된 값에만 해당한다 (spec D4).
        validation = validate_strategy(
            spec, written_pointers=parsed.key_ranges.keys(), fields=self._fields_or_none()
        )
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
                anchor=DiagnosticAnchor.VALUE,
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


# 값이 아니라 키 자체를 가리켜야 하는 구조 진단. 모르는 키도, 1.0 문법 힌트도 고칠 곳이 키다.
_KEY_RANGE_CODES = frozenset({"structure.unknown_key", LEGACY_SHAPE_CODE})


def _structural_diagnostic(
    parsed: ParsedDocument, code: str, pointer: str, message: str
) -> SourceDiagnostic:
    anchor = DiagnosticAnchor.KEY if code in _KEY_RANGE_CODES else DiagnosticAnchor.VALUE
    key_range = parsed.key_ranges.get(pointer) if anchor is DiagnosticAnchor.KEY else None
    return SourceDiagnostic(
        code=code,
        kind=DiagnosticKind.STRUCTURAL,
        pointer=pointer,
        message=message,
        severity=DiagnosticSeverity.ERROR,
        range=key_range or parsed.locate(pointer),
        anchor=anchor,
    )


def _environment_of(
    settings: RetiredExecutionSettings | None,
) -> tuple[RunEnvironment | None, tuple[UpgradeWarning, ...]]:
    """옛 문서에서 떼어 낸 실행 설정 원문을 `RunEnvironment` 로. 못 옮긴 자리는 warning 으로."""
    if settings is None:
        return None, ()
    result = environment_from_retired_settings(settings)
    warnings = tuple(
        UpgradeWarning(ENVIRONMENT_UNAVAILABLE_CODE, problem.pointer, problem.message)
        for problem in result.problems
    )
    return result.environment, warnings


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
