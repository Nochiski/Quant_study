"""사용자 팩터 라이브러리 유스케이스(US-CS-08, 2026-10-19 결정).

저장할 때 팩터 항목을 담은 최소 전략 문서를 전략 compile 에 넣어 같은 규칙으로 검증하고, compile
이 낸 정규 그래프로 버전 신원을 정한다. 전략은 라이브러리를 참조하지 않고 복사하므로 이 서비스는
전략 저장소· `spec_hash`·시도 원장을 건드리지 않는다. 저장·수정은 실행이 아니라 계열 시도 수에도
들지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from strategy_workbench.domain.factor_library.facade.library import (
    FactorMatch,
    JsonObject,
    LibraryFactor,
    LibraryFactorVersion,
    match_factor,
    read_field_ids,
    read_parameter_ids,
    version_identity,
)
from strategy_workbench.domain.strategy.facade.document import CURRENT_SCHEMA_VERSION

from .ports.outgoing.document_compiler import (
    CompiledLibraryDocument,
    FactorDocumentCompilerPort,
    LibraryDiagnostic,
)
from .ports.outgoing.field_availability import FieldAvailability, FieldAvailabilityPort
from .ports.outgoing.library_repository import (
    FactorLibraryRepositoryPort,
    LibraryFactorVersionConflictError,
)


class LibraryFactorInvalidError(ValueError):
    """팩터 항목이 전략 compile 을 통과하지 못했다. 진단은 compile 이 완성한 문장 그대로다."""

    def __init__(self, diagnostics: tuple[LibraryDiagnostic, ...]) -> None:
        codes = [diagnostic.code for diagnostic in diagnostics[:3]]
        super().__init__(f"library factor does not compile — codes={codes}")
        self.diagnostics = diagnostics


class LibraryFactorUnchangedError(ValueError):
    """새 버전의 그래프가 최신 버전과 같다. 같은 정의를 버전으로 쌓지 않는다."""

    def __init__(self, library_id: str, version: int) -> None:
        super().__init__(
            f"library factor graph is unchanged — library_id={library_id} latest={version}"
        )
        self.library_id = library_id
        self.version = version


@dataclass(frozen=True)
class SaveLibraryFactorRequest:
    name: str
    description: str
    factor: JsonObject
    """전략 문서의 팩터 항목 그대로(`factor_id`·`label`·`direction`·`graph` …)."""
    parameters: Sequence[JsonObject]
    """문서의 파라미터 정의. 그래프가 읽는 것만 함께 저장한다."""
    note: str | None = None


@dataclass(frozen=True)
class AddLibraryVersionRequest:
    factor: JsonObject
    parameters: Sequence[JsonObject]
    expected_latest: int
    note: str | None = None


@dataclass(frozen=True)
class LibraryFactorAvailability:
    """지금 연결된 데이터로 최신 버전을 계산할 수 있는가. 판정은 어댑터가 주는 필드 목록이다."""

    available: bool
    missing_fields: tuple[str, ...]
    reasons: tuple[str, ...]
    """어댑터가 그 필드를 주지 않는 사유 문장(있으면)."""


@dataclass(frozen=True)
class LibraryFactorView:
    entry: LibraryFactor
    availability: LibraryFactorAvailability


@dataclass(frozen=True)
class DocumentFactorMatch:
    factor_index: int
    factor_id: str
    match: FactorMatch


@dataclass(frozen=True)
class FactorLibraryMatches:
    """전략 문서의 팩터 항목마다의 라이브러리 관계. 문서가 compile 되지 않으면 비어 있다."""

    compiled: bool
    matches: tuple[DocumentFactorMatch, ...]


class FactorLibraryService:
    def __init__(
        self,
        repository: FactorLibraryRepositoryPort,
        compiler: FactorDocumentCompilerPort,
        fields: FieldAvailabilityPort,
        *,
        new_id: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._compiler = compiler
        self._fields = fields
        self._new_id = new_id
        self._now = now

    def save(self, request: SaveLibraryFactorRequest) -> LibraryFactorView:
        factor_id = request.factor.get("factor_id")
        version = self._version(
            1, request.factor, request.parameters, title=request.name, note=request.note
        )
        entry = LibraryFactor(
            library_id=self._new_id(),
            key=str(factor_id),
            name=request.name.strip(),
            description=request.description,
            hidden=False,
            created_at=version.created_at,
            versions=(version,),
        )
        return self._view(self._repository.create(entry))

    def add_version(self, library_id: str, request: AddLibraryVersionRequest) -> LibraryFactorView:
        current = self._repository.get(library_id)
        if request.expected_latest != current.latest.version:
            # 다른 쓰기가 먼저 버전을 올렸다. 옛 기준으로 만든 정의를 그 위에 쌓지 않는다.
            raise LibraryFactorVersionConflictError(
                "library factor version conflict -- "
                f"library_id={library_id} expected_latest={request.expected_latest} "
                f"current={current.latest.version}",
                current_version=current.latest.version,
            )
        # 꺼내 쓸 키는 항목이 정한다. 새 버전의 factor_id 가 달라도 항목의 키로 저장한다.
        factor = {**request.factor, "factor_id": current.key}
        version = self._version(
            current.latest.version + 1,
            factor,
            request.parameters,
            title=current.name,
            note=request.note,
        )
        if version.identity == current.latest.identity:
            raise LibraryFactorUnchangedError(library_id, current.latest.version)
        return self._view(
            self._repository.append_version(
                library_id, version, expected_latest=request.expected_latest
            )
        )

    def get(self, library_id: str) -> LibraryFactorView:
        return self._view(self._repository.get(library_id))

    def list(self, *, include_hidden: bool = False) -> tuple[LibraryFactorView, ...]:
        availability = self._fields.field_availability()
        return tuple(
            self._view(entry, availability)
            for entry in self._repository.list()
            if include_hidden or not entry.hidden
        )

    def set_hidden(self, library_id: str, hidden: bool) -> LibraryFactorView:
        return self._view(self._repository.set_hidden(library_id, hidden))

    def matches(self, source: str, *, format: Literal["yaml", "json"]) -> FactorLibraryMatches:
        """문서의 `factors` 항목마다 같은 버전·예전 버전·고쳐 씀을 판정한다. 비교는 여기서만
        한다."""
        compiled = self._compiler.compile(source, format=format)
        if compiled.canonical is None:
            return FactorLibraryMatches(compiled=False, matches=())
        entries = self._repository.list()
        parameters = _objects(compiled.canonical.get("parameters"))
        found: list[DocumentFactorMatch] = []
        for index, factor in enumerate(_objects(compiled.canonical.get("factors"))):
            factor_id = factor.get("factor_id")
            graph = factor.get("graph")
            if not isinstance(factor_id, str) or not isinstance(graph, Mapping):
                continue
            match = match_factor(factor_id, version_identity(graph, parameters), entries)
            if match is not None:
                found.append(DocumentFactorMatch(index, factor_id, match))
        return FactorLibraryMatches(compiled=True, matches=tuple(found))

    def _version(
        self,
        number: int,
        factor: JsonObject,
        parameters: Sequence[JsonObject],
        *,
        title: str,
        note: str | None,
    ) -> LibraryFactorVersion:
        graph = factor.get("graph")
        read = set(read_parameter_ids(graph)) if isinstance(graph, Mapping) else set()
        used = tuple(parameter for parameter in parameters if parameter.get("parameter_id") in read)
        document = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "title": title.strip() or "library factor",
            "factors": [dict(factor)],
            "parameters": [dict(parameter) for parameter in used],
        }
        compiled = self._compiler.compile(json.dumps(document, ensure_ascii=False), format="json")
        canonical_factor = _single_factor(compiled)
        compiled_graph = canonical_factor.get("graph")
        if not isinstance(compiled_graph, Mapping):  # pragma: no cover - compile 은 그래프를 낸다
            raise RuntimeError(
                f"compiled factor has no graph — factor_id={factor.get('factor_id')}"
            )
        identity = version_identity(
            compiled_graph,
            _objects(compiled.canonical.get("parameters") if compiled.canonical else None),
        )
        return LibraryFactorVersion(
            version=number,
            identity=identity,
            factor=dict(factor),
            parameters=used,
            created_at=self._now(),
            note=note,
        )

    def _view(
        self, entry: LibraryFactor, fields: FieldAvailability | None = None
    ) -> LibraryFactorView:
        fields = self._fields.field_availability() if fields is None else fields
        graph = entry.latest.factor.get("graph")
        read = read_field_ids(graph) if isinstance(graph, Mapping) else ()
        missing = tuple(field for field in read if field not in fields.available)
        reasons = tuple(
            dict.fromkeys(
                fields.unavailable_reasons[field]
                for field in missing
                if field in fields.unavailable_reasons
            )
        )
        return LibraryFactorView(
            entry=entry,
            availability=LibraryFactorAvailability(
                available=not missing, missing_fields=missing, reasons=reasons
            ),
        )


def _objects(value: object) -> tuple[JsonObject, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _single_factor(compiled: CompiledLibraryDocument) -> JsonObject:
    errors = tuple(
        diagnostic for diagnostic in compiled.diagnostics if diagnostic.severity == "error"
    )
    if compiled.canonical is None or errors:
        raise LibraryFactorInvalidError(errors or compiled.diagnostics)
    factors = _objects(compiled.canonical.get("factors"))
    if len(factors) != 1:  # pragma: no cover - 문서에 팩터를 하나만 넣었다
        raise RuntimeError(f"library document must compile to one factor — got={len(factors)}")
    return factors[0]
