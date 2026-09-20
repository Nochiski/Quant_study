"""`POST /api/v1/strategy-documents/upgrade` (spec D3, P1-04)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._strategy_document_contract import (
    StrategyDocumentUpgrade422Response,
)
from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_upgrade_returns_rewritten_source_and_its_compile_outcome() -> None:
    client = TestClient(build_http_app())

    response = client.post(
        "/api/v1/strategy-documents/upgrade",
        json={"source": _read("quality_momentum.v1_0.commented.yaml"), "format": "yaml"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "yaml"
    assert body["source"] == _read("quality_momentum.v1_1.commented.yaml")
    assert body["compiled"]["schema_version"] == "1.1"
    assert body["source_hash"] == body["compiled"]["source_hash"]

    # 현재 버전은 1.2 인데 이 엔드포인트는 아직 1.1 까지만 올린다 — 1.1 → 1.2 step 과 응답의
    # `environment` 는 P2-09 다(spec D7). 그래서 돌려준 원문은 아직 저장할 수 없고, 진단이
    # 은퇴 버전을 지목한다. 이 단언이 바뀌는 시점이 P2-09 다(WORKFLOW P2-03 제약사항).
    assert body["compiled"]["spec_hash"] is None
    assert [item["code"] for item in body["compiled"]["diagnostics"]] == [
        "structure.unsupported_schema_version"
    ]
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": body["source"], "format": "yaml"}
    )
    assert saved.status_code == 422, saved.text


def test_upgrade_of_a_current_version_document_is_422_not_upgradeable() -> None:
    client = TestClient(build_http_app())

    response = client.post(
        "/api/v1/strategy-documents/upgrade",
        json={"source": _read("quality_momentum.yaml"), "format": "yaml"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_document.not_upgradeable"
    assert detail["schema_version"] == "1.2"
    TypeAdapter(StrategyDocumentUpgrade422Response).validate_python(response.json())


def test_upgrade_of_a_syntax_invalid_document_is_422_with_diagnostics() -> None:
    client = TestClient(build_http_app())

    response = client.post(
        "/api/v1/strategy-documents/upgrade",
        json={"source": _read("quality_momentum.invalid.yaml"), "format": "yaml"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_document.invalid"
    assert detail["diagnostics"] and detail["diagnostics"][0]["kind"] == "syntax"
    TypeAdapter(StrategyDocumentUpgrade422Response).validate_python(response.json())


def test_upgrade_json_source_keeps_json_format() -> None:
    client = TestClient(build_http_app())
    source = _read("canonical_payload.v1_0.json")

    response = client.post(
        "/api/v1/strategy-documents/upgrade", json={"source": source, "format": "json"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "json"
    assert '"schema_version": "1.1"' in body["source"]
    assert '"factors": [' in body["source"]
    # 1.1 은 은퇴 버전이라 결과가 아직 컴파일되지 않는다(P2-09 까지의 중간 상태).
    assert body["compiled"]["spec_hash"] is None


def test_openapi_declares_the_upgrade_operation_and_its_422_union() -> None:
    client = TestClient(build_http_app())

    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/strategy-documents/upgrade"]["post"]
    assert operation["operationId"] == "upgradeStrategyDocument"
    assert "422" in operation["responses"]
    assert "UpgradedDocument" in schema["components"]["schemas"]
