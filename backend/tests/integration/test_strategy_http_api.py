from __future__ import annotations

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def test_strategy_revision_flow_and_openapi_contract() -> None:
    client = TestClient(build_http_app())
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "StrategySpec" in openapi.json()["components"]["schemas"]

    template_response = client.get("/api/v1/strategies/template")
    assert template_response.status_code == 200
    draft = template_response.json()

    validation = client.post("/api/v1/strategies/validate", json=draft)
    assert validation.status_code == 200
    assert validation.json() == {"valid": True, "issues": []}

    created = client.post("/api/v1/strategies", json=draft)
    assert created.status_code == 201
    saved = created.json()
    strategy_id = saved["spec"]["identity"]["strategy_id"]
    assert saved["spec"]["identity"]["revision"] == 1
    assert len(saved["spec_hash"]) == 64

    revised_draft = saved["spec"] | {"title": "저변동성 전략"}
    revised = client.post(
        f"/api/v1/strategies/{strategy_id}/revisions",
        json={"expected_revision": 1, "spec": revised_draft},
    )
    assert revised.status_code == 201
    assert revised.json()["spec"]["identity"]["revision"] == 2
    assert (
        client.get(f"/api/v1/strategies/{strategy_id}?revision=1").json()["spec"]["title"]
        == "새 팩터 전략"
    )


def test_http_adapter_exposes_real_mock_equity_catalog() -> None:
    client = TestClient(build_http_app())
    response = client.get("/api/v1/equity/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["source"] == "deterministic-memory-fixture"
    assert {field["field_id"] for field in payload["fields"]} >= {
        "price.close",
        "financial.book_equity",
    }
