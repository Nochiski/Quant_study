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
    assert body["schema_version"] == "1.0"
    assert body["schema"]["properties"]["schema_version"] == {"type": "string", "const": "1.0"}
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
