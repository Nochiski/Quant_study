"""P1-05 schema/contract API: ETag, 304, registry versions, derived contract rows."""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def test_schema_endpoint_serves_the_runtime_schema_with_its_hash_as_etag() -> None:
    client = TestClient(build_http_app())

    response = client.get("/api/v1/strategy-documents/schema")

    assert response.status_code == 200, response.text
    body = response.json()
    canonical = json.dumps(
        body["schema"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert body["schema_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert response.headers["ETag"] == f'"{body["schema_hash"]}"'
    assert body["schema_version"] == "1.1"
    assert body["schema"]["properties"]["schema_version"] == {
        "type": "string",
        "const": "1.1",
        "x-description-key": "strategy.section.schema_version",
    }
    assert body["schema"]["additionalProperties"] is False


def test_schema_endpoint_returns_304_for_a_matching_etag() -> None:
    client = TestClient(build_http_app())
    etag = client.get("/api/v1/strategy-documents/schema").headers["ETag"]

    cached = client.get("/api/v1/strategy-documents/schema", headers={"If-None-Match": etag})
    weak = client.get("/api/v1/strategy-documents/schema", headers={"If-None-Match": f"W/{etag}"})
    stale = client.get("/api/v1/strategy-documents/schema", headers={"If-None-Match": '"nope"'})

    assert cached.status_code == 304 and cached.headers["ETag"] == etag
    assert weak.status_code == 304
    assert stale.status_code == 200


def test_contract_endpoint_pairs_fields_with_registry_versions_and_links() -> None:
    client = TestClient(build_http_app())

    response = client.get("/api/v1/strategy-documents/contract")

    assert response.status_code == 200, response.text
    body = response.json()
    contract = body["contract"]
    assert (
        contract["schema_hash"]
        == client.get("/api/v1/strategy-documents/schema").json()["schema_hash"]
    )
    assert (
        contract["factor_registry_version"]
        == client.get("/api/v1/factors/catalog").json()["registry_version"]
    )
    assert contract["dataset_snapshot_id"]
    assert body["factor_catalog_url"] == "/api/v1/factors/catalog"
    assert body["equity_catalog_url"] == "/api/v1/equity/catalog"
    by_pointer = {row["pointer"]: row for row in contract["fields"]}
    row = by_pointer["/execution/fee_bps"]
    assert (row["type"], row["unit"], row["display_unit"], row["applied_stage"]) == (
        "number",
        "bps",
        "bp",
        "execution",
    )
    assert row["minimum"] == 0 and row["default"] == 15.0 and row["example"] == 15.0
    assert by_pointer["/data/start"]["format"] == "date"
    assert by_pointer["/portfolio/rebalance"]["enum"]


def test_contract_etag_covers_registry_pairing_and_honours_if_none_match() -> None:
    client = TestClient(build_http_app())

    schema = client.get("/api/v1/strategy-documents/schema")
    contract = client.get("/api/v1/strategy-documents/contract")

    assert contract.headers["ETag"] != schema.headers["ETag"]
    assert (
        contract.json()["contract"]["contract_hash"] != contract.json()["contract"]["schema_hash"]
    )
    cached = client.get(
        "/api/v1/strategy-documents/contract", headers={"If-None-Match": contract.headers["ETag"]}
    )
    assert cached.status_code == 304
    star = client.get("/api/v1/strategy-documents/schema", headers={"If-None-Match": "*"})
    assert star.status_code == 304


def test_operator_catalog_endpoint_serves_every_schema_operator_with_its_keys() -> None:
    """P1-03: 팔레트·노드 라벨이 읽는 유일한 연산자 목록. 스키마 enum과 같은 집합이어야 한다."""
    client = TestClient(build_http_app())

    response = client.get("/api/v1/strategy-documents/operators")

    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["ETag"] == f'"{body["catalog_hash"]}"'
    schema = client.get("/api/v1/strategy-documents/schema").json()["schema"]
    published = {(definition["kind"], definition["operator"]) for definition in body["operators"]}
    from_schema = {
        (node["properties"]["kind"]["const"], value)
        for node in schema["$defs"].values()
        if "operator" in node.get("properties", {}) and "kind" in node["properties"]
        for value in node["properties"]["operator"].get("enum", ())
    }
    assert published == from_schema

    momentum = next(
        definition
        for definition in body["operators"]
        if (definition["kind"], definition["operator"]) == ("time_series", "momentum")
    )
    assert momentum["arity"] == 1
    assert momentum["availability"] == "available"
    assert momentum["output_type_rule"] == "numeric_series"
    assert momentum["description_key"] == "strategy.operator.time_series.momentum"
    assert momentum["formula_key"] == f"{momentum['description_key']}.formula"
    assert {parameter["property_name"] for parameter in momentum["params"]} == {"window", "lag"}
    # 노드 property의 `x-operator`가 같은 키를 가리킨다: 소비자가 값에서 키를 조립하지 않는다.
    assert (
        schema["$defs"]["TimeSeriesNode"]["properties"]["operator"]["x-operator"]["momentum"]
        == momentum["description_key"]
    )


def test_operator_catalog_endpoint_returns_304_for_a_matching_etag() -> None:
    client = TestClient(build_http_app())
    etag = client.get("/api/v1/strategy-documents/operators").headers["ETag"]

    cached = client.get("/api/v1/strategy-documents/operators", headers={"If-None-Match": etag})

    assert cached.status_code == 304 and cached.headers["ETag"] == etag
