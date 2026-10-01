"""`POST /api/v1/strategy-documents/upgrade` (spec D3·D7, P1-04 → P2-09)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._strategy_document_contract import (
    StrategyDocumentUpgrade422Response,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.strategy.facade.document import CURRENT_SCHEMA_VERSION

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
LF = chr(10)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_upgrade_returns_current_source_its_environment_and_a_savable_compile() -> None:
    client = TestClient(build_http_app())

    response = client.post(
        "/api/v1/strategy-documents/upgrade",
        json={"source": _read("quality_momentum.v1_0.commented.yaml"), "format": "yaml"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "yaml"
    assert body["source"] == _read("quality_momentum.v1_2.commented.yaml")
    assert body["compiled"]["schema_version"] == CURRENT_SCHEMA_VERSION
    assert body["source_hash"] == body["compiled"]["source_hash"]
    assert body["compiled"]["spec_hash"] is not None
    # 옛 골든의 모멘텀은 원주가를 읽는다 — 업그레이드는 필드를 바꾸지 않으므로 원주가 warning 하나만
    # 남는다(BACKLOG-018). 저장을 막지 않는다.
    assert [item["code"] for item in body["compiled"]["diagnostics"]] == [
        "strategy.field.unadjusted_price"
    ]
    # 옛 문서의 실행 설정은 응답으로 돌아온다 — 화면이 실행 설정 패널을 채운다(P3-02).
    assert body["environment"] == {
        "market": "KRX",
        "frequency": "daily",
        "start": "2021-01-01",
        "end": "2026-08-31",
        "universe_id": "krx.common-stock",
        "timing": "next_open",
        "participation_rate": 0.1,
        "participation_basis": "adv20",
        "fee_bps": 15.0,
        "slippage_bps": 10.0,
        "impact_model": "fixed_bps",
        "impact_coefficient": 1.0,
        "sell_tax": "krx_statutory",
        "sell_tax_bps": None,
        "missing": "drop",
    }
    assert body["warnings"] == []

    # P2-09 전에는 결과가 1.1 이라 저장이 422 였다. 이제 그대로 새 revision 이 된다.
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": body["source"], "format": "yaml"}
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["spec_hash"] == body["compiled"]["spec_hash"]


def test_upgrade_warnings_carry_code_pointer_and_message() -> None:
    client = TestClient(build_http_app())
    source = _read("quality_momentum.v1_1.yaml").replace('end: "2026-08-31"', 'end: "someday"')

    response = client.post(
        "/api/v1/strategy-documents/upgrade", json={"source": source, "format": "yaml"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["environment"] is None
    assert [(w["code"], w["pointer"]) for w in body["warnings"]] == [
        ("strategy_document.upgrade_environment_unavailable", "/data/end")
    ]
    assert "someday" in body["warnings"][0]["message"]


def test_upgrade_of_a_current_version_document_is_422_not_upgradeable() -> None:
    client = TestClient(build_http_app())

    response = client.post(
        "/api/v1/strategy-documents/upgrade",
        json={"source": _read("quality_momentum.yaml"), "format": "yaml"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_document.not_upgradeable"
    assert detail["schema_version"] == CURRENT_SCHEMA_VERSION
    assert "already current" in detail["message"]
    TypeAdapter(StrategyDocumentUpgrade422Response).validate_python(response.json())


def test_upgrade_of_a_saved_reference_node_is_422_unsupported_node() -> None:
    client = TestClient(build_http_app())
    source = _read("quality_momentum.v1_1.yaml").replace(
        f"          window: 252{LF}",
        f"          window: 252{LF}        - kind: saved_subgraph{LF}          node_id: ref{LF}",
    )

    response = client.post(
        "/api/v1/strategy-documents/upgrade", json={"source": source, "format": "yaml"}
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_document.upgrade_unsupported_node"
    assert detail["pointer"] == "/factors/0/graph/nodes/2/kind"
    assert "saved_subgraph" in detail["message"]
    TypeAdapter(StrategyDocumentUpgrade422Response).validate_python(response.json())


def test_a_current_document_with_1_0_syntax_is_a_fix_in_place_error_not_an_upgrade() -> None:
    """Phase 2 감사 NB-1: 문서가 선언한 버전을 믿는다.

    버전 줄이 현재 판인데 본문에 1.0 문법이 섞인 문서는 compile 이 `structure.legacy_shape` 로
    제자리에서 고칠 방법을 말하고(업그레이드를 시키지 않는다), 업그레이드 endpoint 는 422
    `not_upgradeable` 로 거절한다. 진단이 시키는 일과 endpoint 판정이 여전히 어긋나지 않는다
    (P1-05 DEFECT-P105-001 의 계약).
    """
    client = TestClient(build_http_app())
    source = _read("quality_momentum.v1_0.commented.yaml").replace(
        'schema_version: "1.0"', f'schema_version: "{CURRENT_SCHEMA_VERSION}"'
    )
    assert f'schema_version: "{CURRENT_SCHEMA_VERSION}"' in source

    compiled = client.post(
        "/api/v1/strategy-documents/compile", json={"source": source, "format": "yaml"}
    )
    assert compiled.status_code == 200, compiled.text
    legacy = [d for d in compiled.json()["diagnostics"] if d["code"] == "structure.legacy_shape"]
    assert legacy and all("업그레이드" not in d["message"] for d in legacy)
    # 1.0 문법 힌트도 고칠 곳이 키다. 편집기는 이 표식으로 키를 짚는다(#418 리뷰 P3-1).
    assert all(d["anchor"] == "key" for d in legacy)

    upgraded = client.post(
        "/api/v1/strategy-documents/upgrade", json={"source": source, "format": "yaml"}
    )

    assert upgraded.status_code == 422, upgraded.text
    detail = upgraded.json()["detail"]
    assert detail["code"] == "strategy_document.not_upgradeable"
    assert "fix them in place" in detail["message"]
    TypeAdapter(StrategyDocumentUpgrade422Response).validate_python(upgraded.json())


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
    assert f'"schema_version": "{CURRENT_SCHEMA_VERSION}"' in body["source"]
    assert '"factors": [' in body["source"]
    assert '"data"' not in body["source"] and '"execution"' not in body["source"]
    assert body["compiled"]["spec_hash"] is not None
    assert body["environment"] is not None


def test_openapi_declares_the_upgrade_operation_and_its_422_union() -> None:
    client = TestClient(build_http_app())

    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/strategy-documents/upgrade"]["post"]
    assert operation["operationId"] == "upgradeStrategyDocument"
    assert "422" in operation["responses"]
    components = schema["components"]["schemas"]
    assert "UpgradedDocument" in components
    assert {"environment", "warnings"} <= set(components["UpgradedDocument"]["properties"])
    assert "StrategyDocumentUpgradeUnsupportedNodeDetail" in components
