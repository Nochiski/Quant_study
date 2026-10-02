from __future__ import annotations

import re

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import DEFAULT_MISSING_POLICY


def _snapshot_id(client: TestClient) -> str:
    """어댑터가 정한 데이터 스냅샷 id — "fixture 데이터의 판:필드 계약 판"(#235)."""
    snapshot_id = client.get("/api/v1/equity/catalog").json()["snapshot"]["snapshot_id"]
    assert re.fullmatch(r"mock-equity-v0\.2-[0-9a-f]{16}:[0-9a-f]{16}", snapshot_id)
    return snapshot_id


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
    assert explanation.json()["data_snapshot_id"] == _snapshot_id(client)
    assert explanation.json()["plan"]["as_of_policy"] == "available_date_lte_as_of"
    assert len(explanation.json()["plan"]["plan_hash"]) == 64
    # 팩터 연구에서 쓴 그래프에는 compile 이 붙인 노드가 없다.
    assert explanation.json()["synthesized_nodes"] == []


def test_explain_marks_the_boolean_promotion_nodes_of_a_compiled_graph() -> None:
    """실행 계획 화면이 승격 노드 이름 규칙을 복제하지 않게 wire 가 표식을 싣는다(감사 #13).

    요청 그래프는 compile 이 승격한 모양 그대로다. 문서가 예약 접두사를 쓰면 compile 이 거절하므로
    (DEFECT-232-01) 화면이 보내는 이 모양의 그래프는 compile 산출물뿐이다.
    """
    client = TestClient(build_http_app())
    graph = {
        "nodes": [
            {"kind": "field", "node_id": "close", "field_id": "price.close"},
            {"kind": "constant", "node_id": "threshold", "value": 100.0},
            {
                "kind": "comparison",
                "node_id": "above",
                "operator": "gt",
                "left_node_id": "close",
                "right_node_id": "threshold",
            },
            {"kind": "constant", "node_id": "__promote_above_one", "value": 1.0},
            {"kind": "constant", "node_id": "__promote_above_zero", "value": 0.0},
            {
                "kind": "conditional",
                "node_id": "__promote_above",
                "predicate_node_id": "above",
                "true_node_id": "__promote_above_one",
                "false_node_id": "__promote_above_zero",
            },
        ],
        "output_node_id": "__promote_above",
    }

    response = client.post("/api/v1/factors/explain", json={"graph": graph})

    assert response.status_code == 200
    assert response.json()["synthesized_nodes"] == [
        {"node_id": "__promote_above_one", "origin": "promotion", "role": "promotion_constant"},
        {"node_id": "__promote_above_zero", "origin": "promotion", "role": "promotion_constant"},
        {"node_id": "__promote_above", "origin": "promotion", "role": "promoted_output"},
    ]
    steps = [step["node_id"] for step in response.json()["plan"]["steps"]]
    assert set(steps) >= {"__promote_above_one", "__promote_above_zero", "__promote_above"}


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


def _momentum_graph() -> dict[str, object]:
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
    return graph


def test_explain_reads_the_requested_missing_policy() -> None:
    """요청의 `missing` 이 plan 과 `plan_hash` 를 가른다.

    schema 1.2 그래프에는 결측 정책이 없으므로 sandbox 는 요청 값만 읽는다(P2-03). 정책마다
    다른 plan 이어야 팩터 행렬 캐시 키가 섞이지 않는다.
    """
    client = TestClient(build_http_app())

    plans = {
        policy: client.post(
            "/api/v1/factors/explain", json={"graph": _momentum_graph(), "missing": policy}
        ).json()["plan"]
        for policy in ("drop", "zero", "cross_sectional_median")
    }

    assert {policy: plan["missing_policy"] for policy, plan in plans.items()} == {
        "drop": "drop",
        "zero": "zero",
        "cross_sectional_median": "cross_sectional_median",
    }
    assert len({plan["plan_hash"] for plan in plans.values()}) == 3


def test_explain_without_a_missing_policy_uses_the_run_default() -> None:
    """생략하면 실행 설정과 같은 기본값이다 — 편집 화면 플랜 패널이 실제 실행과 같아야 한다."""
    client = TestClient(build_http_app())

    omitted = client.post("/api/v1/factors/explain", json={"graph": _momentum_graph()})
    explicit = client.post(
        "/api/v1/factors/explain", json={"graph": _momentum_graph(), "missing": "drop"}
    )

    assert omitted.json()["plan"]["missing_policy"] == DEFAULT_MISSING_POLICY.value
    assert omitted.json()["plan"]["plan_hash"] == explicit.json()["plan"]["plan_hash"]


def test_retired_factor_preview_does_not_offer_a_second_calculation_path() -> None:
    client = TestClient(build_http_app())
    assert client.post("/api/v1/factors/preview", json={}).status_code == 404
    assert (
        client.post("/api/v1/factors/validate", json={"graph": _momentum_graph()}).status_code
        == 200
    )
