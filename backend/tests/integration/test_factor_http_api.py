from __future__ import annotations

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def test_factor_catalog_validate_and_explain_contract() -> None:
    client = TestClient(build_http_app())
    catalog_response = client.get(
        "/api/v1/factors/catalog",
        params={"availability": "implemented", "page_size": 100},
    )

    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["total"] == 7
    assert len(catalog["facets"]["categories"]) == 7
    factor = next(item for item in catalog["factors"] if item["category"] == "financial")
    body = {"graph": factor["default_graph"]}

    validation = client.post("/api/v1/factors/validate", json=body)
    explanation = client.post("/api/v1/factors/explain", json=body)

    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert explanation.status_code == 200
    assert explanation.json()["registry_version"] == catalog["registry_version"]
    assert explanation.json()["data_snapshot_id"] == "mock-equity-v0.2-20260903"
    assert explanation.json()["plan"]["as_of_policy"] == "available_date_lte_as_of"
    assert len(explanation.json()["plan"]["plan_hash"]) == 64


def test_explain_resolves_numeric_and_group_metadata_inside_the_backend() -> None:
    client = TestClient(build_http_app())
    graph = {
        "nodes": [
            {"node_id": "close", "field_id": "price.close", "kind": "field"},
            {
                "node_id": "neutral",
                "operator": "neutralize",
                "input_node_id": "close",
                "group_field_id": "classification.sector",
                "kind": "group",
            },
        ],
        "output_node_id": "neutral",
    }

    valid = client.post("/api/v1/factors/explain", json={"graph": graph})
    invalid = client.post(
        "/api/v1/factors/explain",
        json={
            "graph": graph
            | {
                "nodes": [
                    graph["nodes"][0],
                    graph["nodes"][1] | {"group_field_id": "price.market_cap"},
                ]
            },
            # Legacy/untrusted client metadata cannot override the backend data adapter.
            "fields": [
                {
                    "field_id": "price.market_cap",
                    "unit": "category",
                    "value_type": "group_series",
                }
            ],
        },
    )

    assert valid.status_code == 200
    assert valid.json()["validation"]["valid"] is True
    assert valid.json()["plan"] is not None
    assert valid.json()["plan"]["required_field_ids"] == [
        "classification.sector",
        "price.close",
    ]
    assert invalid.status_code == 200
    assert invalid.json()["plan"] is None
    assert invalid.json()["registry_version"] == "factor-registry-v1"
    assert {issue["code"] for issue in invalid.json()["validation"]["issues"]} == {
        "factor.graph.group_field_type"
    }


def test_factor_preview_is_deterministic_and_returns_research_diagnostics() -> None:
    client = TestClient(build_http_app())
    catalog = client.get(
        "/api/v1/factors/catalog",
        params={"search": "short.short_balance_ratio"},
    ).json()
    graph = catalog["factors"][0]["default_graph"]
    body = {
        "graph": graph,
        "as_of_start": "2024-01-02",
        "as_of_end": "2024-01-10",
    }

    first = client.post("/api/v1/factors/preview", json=body)
    second = client.post("/api/v1/factors/preview", json=body)

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    # The adapter, not the client, names the snapshot; the cache key carries the same id.
    assert payload["data_snapshot_id"] == "mock-equity-v0.2-20260903"
    assert payload["cache_key"]["data_snapshot_id"] == payload["data_snapshot_id"]
    assert payload["analytics"]["coverage"] == 1.0
    assert payload["analytics"]["information_coefficient"] is not None
    assert len(payload["cache_key"]["fingerprint"]) == 64
    assert payload["evaluation"]["values"]


def test_invalid_factor_preview_returns_structured_validation() -> None:
    client = TestClient(build_http_app())
    response = client.post(
        "/api/v1/factors/preview",
        json={
            "graph": {
                "nodes": [
                    {
                        "node_id": "cycle",
                        "operator": "negate",
                        "input_node_id": "cycle",
                        "kind": "unary",
                    }
                ],
                "output_node_id": "cycle",
            },
            "as_of_start": "2024-01-02",
            "as_of_end": "2024-01-03",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "factor.graph.invalid"
    assert response.json()["detail"]["validation"]["issues"][0]["code"] == "factor.graph.cycle"


def test_factor_preview_fails_closed_when_the_expected_snapshot_differs() -> None:
    client = TestClient(build_http_app())
    catalog = client.get(
        "/api/v1/factors/catalog",
        params={"search": "short.short_balance_ratio"},
    ).json()
    body = {
        "graph": catalog["factors"][0]["default_graph"],
        "expected_data_snapshot_id": "stale-snapshot-from-an-old-catalog",
        "as_of_start": "2024-01-02",
        "as_of_end": "2024-01-10",
    }

    response = client.post("/api/v1/factors/preview", json=body)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "factor.snapshot_mismatch"
    assert detail["expected_data_snapshot_id"] == "stale-snapshot-from-an-old-catalog"
    assert detail["actual_data_snapshot_id"] == "mock-equity-v0.2-20260903"

    matching = client.post(
        "/api/v1/factors/preview",
        json=body | {"expected_data_snapshot_id": "mock-equity-v0.2-20260903"},
    )
    assert matching.status_code == 200
