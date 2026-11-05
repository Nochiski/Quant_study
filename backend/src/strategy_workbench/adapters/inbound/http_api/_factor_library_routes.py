"""사용자 팩터 라이브러리 API(US-CS-08).

저장·새 버전·목록·상세·숨기기와, 전략 문서의 팩터를 라이브러리와 맞추는 `matches`. 검증과 비교는
application 이 compile 로 하고 이 모듈은 요청·응답 모양과 오류 코드만 옮긴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException

from strategy_workbench.application.factor_library.facade.library import (
    AddLibraryVersionRequest,
    FactorLibraryMatches,
    FactorLibraryService,
    LibraryFactorInvalidError,
    LibraryFactorUnchangedError,
    LibraryFactorView,
    SaveLibraryFactorRequest,
)
from strategy_workbench.application.factor_library.facade.ports import (
    LibraryDiagnostic,
    LibraryFactorKeyConflictError,
    LibraryFactorNotFoundError,
    LibraryFactorVersionConflictError,
)
from strategy_workbench.domain.factor_library.facade.library import FactorMatchStatus

from ._backtest_contract import CodedBodyValidationRoute

LibraryErrorCode = Literal[
    "library.request_invalid",
    "library.factor.invalid",
    "library.factor.not_found",
    "library.factor.key_exists",
    "library.version.unchanged",
    "library.version.conflict",
]


@dataclass(frozen=True)
class LibraryDiagnosticBody:
    code: str
    message: str
    pointer: str
    severity: Literal["error", "warning", "info"]


@dataclass(frozen=True)
class LibraryErrorDetail:
    code: LibraryErrorCode
    message: str
    existing_library_id: str | None = None
    current_version: int | None = None
    diagnostics: list[LibraryDiagnosticBody] | None = None


@dataclass(frozen=True)
class LibraryErrorResponse:
    detail: LibraryErrorDetail


@dataclass(frozen=True)
class SaveLibraryFactorBody:
    name: str
    # reason: 전략 문서의 팩터 항목·파라미터 정의는 runtime schema 가 소유하는 열린 JSON 트리다.
    factor: dict[str, Any]
    description: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=lambda: [])
    note: str | None = None


@dataclass(frozen=True)
class AddLibraryVersionBody:
    # reason: 위와 같다 — 팩터 항목은 열린 JSON 트리다.
    factor: dict[str, Any]
    expected_latest: int
    parameters: list[dict[str, Any]] = field(default_factory=lambda: [])
    note: str | None = None


@dataclass(frozen=True)
class LibraryVisibilityBody:
    hidden: bool


@dataclass(frozen=True)
class LibraryMatchesBody:
    format: Literal["yaml", "json"]
    source: str


@dataclass(frozen=True)
class LibraryFactorVersionBody:
    version: int
    identity: str
    # reason: 꺼내 쓸 때 전략 문서에 그대로 복사하는 열린 JSON 트리다.
    factor: dict[str, Any]
    parameters: list[dict[str, Any]]
    created_at: datetime
    note: str | None


@dataclass(frozen=True)
class LibraryFactorAvailabilityBody:
    available: bool
    missing_fields: list[str]
    reasons: list[str]


@dataclass(frozen=True)
class LibraryFactorBody:
    library_id: str
    key: str
    name: str
    description: str
    hidden: bool
    created_at: datetime
    latest_version: int
    versions: list[LibraryFactorVersionBody]
    availability: LibraryFactorAvailabilityBody


@dataclass(frozen=True)
class LibraryFactorList:
    items: list[LibraryFactorBody]


@dataclass(frozen=True)
class LibraryFactorMatchBody:
    factor_index: int
    factor_id: str
    library_id: str
    status: FactorMatchStatus
    matched_version: int | None
    latest_version: int


@dataclass(frozen=True)
class LibraryFactorMatchesBody:
    compiled: bool
    matches: list[LibraryFactorMatchBody]


class _LibraryBodyRoute(CodedBodyValidationRoute):
    @staticmethod
    def coded_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        # reason: FastAPI 검증 오류 hook의 공개 JSON 계약을 그대로 받는다.
        location = next(iter(issues), {}).get("loc", ())
        path = "/".join(str(part) for part in location if part != "body")
        return {
            "code": "library.request_invalid",
            "message": f"요청 본문을 읽지 못했습니다. 칸을 확인해 주세요(field=/{path}).",
        }


def _tree(value: Mapping[str, object]) -> dict[str, Any]:
    return dict(value)


def _body(view: LibraryFactorView) -> LibraryFactorBody:
    entry = view.entry
    return LibraryFactorBody(
        library_id=entry.library_id,
        key=entry.key,
        name=entry.name,
        description=entry.description,
        hidden=entry.hidden,
        created_at=entry.created_at,
        latest_version=entry.latest.version,
        versions=[
            LibraryFactorVersionBody(
                version=version.version,
                identity=version.identity,
                factor=_tree(version.factor),
                parameters=[_tree(parameter) for parameter in version.parameters],
                created_at=version.created_at,
                note=version.note,
            )
            for version in entry.versions
        ],
        availability=LibraryFactorAvailabilityBody(
            available=view.availability.available,
            missing_fields=list(view.availability.missing_fields),
            reasons=list(view.availability.reasons),
        ),
    )


def _matches(result: FactorLibraryMatches) -> LibraryFactorMatchesBody:
    return LibraryFactorMatchesBody(
        compiled=result.compiled,
        matches=[
            LibraryFactorMatchBody(
                factor_index=item.factor_index,
                factor_id=item.factor_id,
                library_id=item.match.library_id,
                status=item.match.status,
                matched_version=item.match.matched_version,
                latest_version=item.match.latest_version,
            )
            for item in result.matches
        ],
    )


def _diagnostic(diagnostic: LibraryDiagnostic) -> dict[str, str]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "pointer": diagnostic.pointer,
        "severity": diagnostic.severity,
    }


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, LibraryFactorInvalidError):
        return HTTPException(
            422,
            detail={
                "code": "library.factor.invalid",
                "message": "팩터가 전략 검증을 통과하지 못했습니다. 진단을 확인해 주세요.",
                "diagnostics": [_diagnostic(item) for item in error.diagnostics],
            },
        )
    if isinstance(error, LibraryFactorKeyConflictError):
        return HTTPException(
            409,
            detail={
                "code": "library.factor.key_exists",
                "message": (
                    "같은 팩터 id 로 저장한 내 팩터가 있습니다. "
                    "새 버전으로 저장할 수 있습니다."
                ),
                "existing_library_id": error.existing_library_id,
            },
        )
    if isinstance(error, LibraryFactorUnchangedError):
        return HTTPException(
            409,
            detail={
                "code": "library.version.unchanged",
                "message": "최신 버전과 계산식이 같아 새 버전을 만들지 않았습니다.",
                "current_version": error.version,
            },
        )
    if isinstance(error, LibraryFactorVersionConflictError):
        return HTTPException(
            409,
            detail={
                "code": "library.version.conflict",
                "message": "그사이 다른 새 버전이 저장됐습니다. 다시 불러온 뒤 저장해 주세요.",
                "current_version": error.current_version,
            },
        )
    return HTTPException(
        404,
        detail={
            "code": "library.factor.not_found",
            "message": "요청한 내 팩터를 찾을 수 없습니다.",
        },
    )


_ERRORS = (
    LibraryFactorInvalidError,
    LibraryFactorKeyConflictError,
    LibraryFactorUnchangedError,
    LibraryFactorVersionConflictError,
    LibraryFactorNotFoundError,
)


def register_factor_library_routes(app: FastAPI, service: FactorLibraryService) -> None:
    responses: dict[int | str, dict[str, object]] = {
        404: {"model": LibraryErrorResponse},
        409: {"model": LibraryErrorResponse},
        422: {"model": LibraryErrorResponse},
    }

    @app.get(
        "/api/v1/factor-library",
        operation_id="listLibraryFactors",
        responses=responses,
    )
    def list_factors(include_hidden: bool = False) -> LibraryFactorList:
        return LibraryFactorList(
            items=[_body(view) for view in service.list(include_hidden=include_hidden)]
        )

    @app.get(
        "/api/v1/factor-library/{library_id}",
        operation_id="getLibraryFactor",
        responses=responses,
    )
    def get_factor(library_id: str) -> LibraryFactorBody:
        try:
            return _body(service.get(library_id))
        except _ERRORS as error:
            raise _translate(error) from error

    def save(body: SaveLibraryFactorBody) -> LibraryFactorBody:
        try:
            return _body(
                service.save(
                    SaveLibraryFactorRequest(
                        name=body.name,
                        description=body.description,
                        factor=body.factor,
                        parameters=body.parameters,
                        note=body.note,
                    )
                )
            )
        except _ERRORS as error:
            raise _translate(error) from error

    def add_version(library_id: str, body: AddLibraryVersionBody) -> LibraryFactorBody:
        try:
            return _body(
                service.add_version(
                    library_id,
                    AddLibraryVersionRequest(
                        factor=body.factor,
                        parameters=body.parameters,
                        expected_latest=body.expected_latest,
                        note=body.note,
                    ),
                )
            )
        except _ERRORS as error:
            raise _translate(error) from error

    def set_visibility(library_id: str, body: LibraryVisibilityBody) -> LibraryFactorBody:
        try:
            return _body(service.set_hidden(library_id, body.hidden))
        except _ERRORS as error:
            raise _translate(error) from error

    def matches(body: LibraryMatchesBody) -> LibraryFactorMatchesBody:
        return _matches(service.matches(body.source, format=body.format))

    for path, endpoint, method, operation_id, status_code in (
        ("/api/v1/factor-library", save, "POST", "saveLibraryFactor", 201),
        (
            "/api/v1/factor-library/{library_id}/versions",
            add_version,
            "POST",
            "addLibraryFactorVersion",
            201,
        ),
        (
            "/api/v1/factor-library/{library_id}",
            set_visibility,
            "PATCH",
            "setLibraryFactorVisibility",
            200,
        ),
        ("/api/v1/factor-library/matches", matches, "POST", "matchLibraryFactors", 200),
    ):
        app.router.add_api_route(
            path,
            endpoint,
            methods=[method],
            operation_id=operation_id,
            status_code=status_code,
            responses=responses,
            route_class_override=_LibraryBodyRoute,
        )
