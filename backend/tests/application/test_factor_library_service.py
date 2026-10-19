"""팩터 라이브러리 목록의 "지금 쓸 수 있는가" 판정(US-CS-08)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from strategy_workbench.application.factor_library.facade.library import (
    FactorLibraryService,
    SaveLibraryFactorRequest,
)
from strategy_workbench.application.factor_library.facade.ports import (
    CompiledLibraryDocument,
    FieldAvailability,
    LibraryFactorNotFoundError,
)
from strategy_workbench.domain.factor_library.facade.library import (
    LibraryFactor,
    LibraryFactorVersion,
)


class _Repository:
    def __init__(self) -> None:
        self.entries: dict[str, LibraryFactor] = {}

    def create(self, entry: LibraryFactor) -> LibraryFactor:
        self.entries[entry.library_id] = entry
        return entry

    def append_version(  # pragma: no cover - 이 파일은 버전을 쌓지 않는다
        self, library_id: str, version: LibraryFactorVersion, *, expected_latest: int
    ) -> LibraryFactor:
        raise NotImplementedError

    def get(self, library_id: str) -> LibraryFactor:
        if library_id not in self.entries:
            raise LibraryFactorNotFoundError(library_id)
        return self.entries[library_id]

    def list(self) -> tuple[LibraryFactor, ...]:
        return tuple(self.entries.values())

    def set_hidden(self, library_id: str, hidden: bool) -> LibraryFactor:  # pragma: no cover
        raise NotImplementedError


class _EchoCompiler:
    """받은 JSON 문서를 그대로 정규 spec 으로 돌려준다.

    검증은 통합 테스트가 실제 compile 로 본다.
    """

    def compile(self, source: str, *, format: Literal["yaml", "json"]) -> CompiledLibraryDocument:
        document: Mapping[str, object] = json.loads(source)
        return CompiledLibraryDocument(canonical=document, diagnostics=())


class _Fields:
    def __init__(self, available: set[str], reasons: dict[str, str]) -> None:
        self.value = FieldAvailability(frozenset(available), reasons)

    def field_availability(self) -> FieldAvailability:
        return self.value


def _service(fields: _Fields) -> FactorLibraryService:
    return FactorLibraryService(
        _Repository(),
        _EchoCompiler(),
        fields,
        new_id=lambda: "lib-1",
        now=lambda: datetime(2026, 10, 19, tzinfo=UTC),
    )


def _request() -> SaveLibraryFactorRequest:
    return SaveLibraryFactorRequest(
        name="외국인 순매수 대비 거래대금",
        description="",
        factor={
            "factor_id": "flow_ratio",
            "graph": {
                "nodes": [
                    {"node_id": "flow", "kind": "field", "field_id": "flow.foreign_net"},
                    {"node_id": "value", "kind": "field", "field_id": "price.trading_value"},
                ],
                "output_node_id": "flow",
            },
        },
        parameters=[],
    )


def test_factor_reading_a_field_the_adapter_does_not_provide_is_marked_unavailable() -> None:
    fields = _Fields({"price.trading_value"}, {"flow.foreign_net": "원장에 수급 원천이 없습니다."})
    service = _service(fields)
    service.save(_request())

    [view] = service.list()

    assert view.availability.available is False
    assert view.availability.missing_fields == ("flow.foreign_net",)
    assert view.availability.reasons == ("원장에 수급 원천이 없습니다.",)


def test_availability_follows_the_currently_connected_adapter() -> None:
    fields = _Fields({"price.trading_value"}, {})
    service = _service(fields)
    service.save(_request())
    fields.value = FieldAvailability(frozenset({"price.trading_value", "flow.foreign_net"}), {})

    [view] = service.list()

    assert view.availability.available is True
    assert view.availability.missing_fields == ()
