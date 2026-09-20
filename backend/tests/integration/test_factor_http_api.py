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


def _momentum_graph(missing_policy: str | None = None) -> dict[str, object]:
    graph: dict[str, object] = {
        "nodes": [
            {"node_id": "close", "field_id": "price.close", "kind": "field"},
            {
                "node_id": "mom",
                "operator": "momentum",
                "input_node_id": "close",
                "window": 3,
                "kind": "time_series",
            },
        ],
        "output_node_id": "mom",
    }
    if missing_policy is not None:
        graph["missing_policy"] = missing_policy
    return graph


def test_explain_falls_back_to_the_document_missing_policy() -> None:
    """P2-02 리뷰 P1: 요청이 `missing` 을 생략하면 1.1 문서 값으로 떨어진다.

    편집 화면의 실행 플랜 패널이 이 경로를 쓴다. 기본값으로 고정하면 패널이 실제 실행과 다른
    결측 정책과 다른 `plan_hash` 를 보인다.
    """
    client = TestClient(build_http_app())

    plans = {
        policy: client.post(
            "/api/v1/factors/explain", json={"graph": _momentum_graph(policy)}
        ).json()["plan"]
        for policy in ("drop", "zero", "cross_sectional_median")
    }

    assert {policy: plan["missing_policy"] for policy, plan in plans.items()} == {
        "drop": "drop",
        "zero": "zero",
        "cross_sectional_median": "cross_sectional_median",
    }
    # 정책마다 다른 plan 이어야 팩터 행렬 캐시 키가 섞이지 않는다.
    assert len({plan["plan_hash"] for plan in plans.values()}) == 3


def test_explain_prefers_the_explicit_missing_over_the_document() -> None:
    client = TestClient(build_http_app())

    explicit = client.post(
        "/api/v1/factors/explain",
        json={"graph": _momentum_graph("zero"), "missing": "drop"},
    )
    omitted = client.post("/api/v1/factors/explain", json={"graph": _momentum_graph()})

    assert explicit.json()["plan"]["missing_policy"] == "drop"
    # 문서에 값이 없으면 모델 기본값(`drop`)이다.
    assert omitted.json()["plan"]["missing_policy"] == "drop"
    assert (
        explicit.json()["plan"]["plan_hash"]
        != (
            client.post("/api/v1/factors/explain", json={"graph": _momentum_graph("zero")}).json()[
                "plan"
            ]["plan_hash"]
        )
    )
