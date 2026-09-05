from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.bootstrap.facade.http import build_http_app


def _saved_document(client: TestClient) -> dict[str, Any]:
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.0"
    response = client.post(
        "/api/v1/strategy-documents",
        json={
            "format": "json",
            "source": json.dumps(template, ensure_ascii=False, indent=2, default=str),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_backtest_history_is_newest_first_paginated_and_filterable() -> None:
    client = TestClient(build_http_app())
    document = _saved_document(client)
    saved_source = {
        "kind": "saved_revision",
        "strategy_id": document["strategy_id"],
        "revision": document["revision"],
        "expected_spec_hash": document["spec_hash"],
    }
    saved = client.post(
        "/api/v1/backtests",
        json={"strategy_source": saved_source, "core": "python"},
    )
    inline_source_hash = "ab" * 32
    inline = client.post(
        "/api/v1/backtests",
        json={
            "strategy_source": {
                "kind": "inline_draft",
                "spec": document["spec"],
                "source_hash": inline_source_hash,
            },
            "core": "python",
        },
    )
    assert saved.status_code == inline.status_code == 202

    history = client.get("/api/v1/backtests", params={"offset": 0, "limit": 50})
    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["total"] == 2
    assert payload["offset"] == 0 and payload["limit"] == 50
    assert [item["run"]["run_id"] for item in payload["items"]] == [
        inline.json()["run"]["run_id"],
        saved.json()["run"]["run_id"],
    ]
    assert payload["items"][0]["strategy_provenance"] == {
        "kind": "inline_draft",
        "spec_hash": document["spec_hash"],
        "schema_version": "1.0",
        "strategy_id": None,
        "revision": None,
        "source_hash": inline_source_hash,
    }
    assert payload["items"][1]["strategy_provenance"] == {
        "kind": "saved_revision",
        "spec_hash": document["spec_hash"],
        "schema_version": "1.0",
        "strategy_id": document["strategy_id"],
        "revision": document["revision"],
        "source_hash": document["source_hash"],
    }

    second_page = client.get("/api/v1/backtests", params={"offset": 1, "limit": 1})
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 2
    assert [item["run"]["run_id"] for item in second_page.json()["items"]] == [
        saved.json()["run"]["run_id"]
    ]

    filtered = client.get(
        "/api/v1/backtests",
        params={"strategy_id": document["strategy_id"]},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["run"]["run_id"] == saved.json()["run"]["run_id"]
    assert client.get(
        "/api/v1/backtests", params={"strategy_id": "missing"}
    ).json()["items"] == []


def test_backtest_history_uses_shared_page_bounds_and_rejected_runs_do_not_appear() -> None:
    client = TestClient(build_http_app())
    maximum = client.get(
        "/api/v1/backtests",
        params={"offset": PageRequest.MAX_OFFSET, "limit": PageRequest.MAX_LIMIT},
    )
    assert maximum.status_code == 200
    assert maximum.json() == {
        "items": [],
        "total": 0,
        "offset": PageRequest.MAX_OFFSET,
        "limit": PageRequest.MAX_LIMIT,
    }
    assert (
        client.get(
            "/api/v1/backtests",
            params={"offset": PageRequest.MAX_OFFSET + 1},
        ).status_code
        == 422
    )
    assert client.get("/api/v1/backtests", params={"offset": "true"}).status_code == 422
    assert client.get("/api/v1/backtests", params={"strategy_id": ""}).status_code == 422

    rejected = client.post("/api/v1/backtests", json={"core": "python"})
    assert rejected.status_code == 422
    assert client.get("/api/v1/backtests").json()["total"] == 0


def test_all_page_endpoints_reject_noncanonical_integer_wire_forms() -> None:
    client = TestClient(build_http_app())
    document = _saved_document(client)
    endpoints = (
        "/api/v1/backtests",
        "/api/v1/strategies",
        f"/api/v1/strategies/{document['strategy_id']}/revisions",
    )

    for endpoint in endpoints:
        for value in ("1.0", "01", "+1", " 1 ", "9_0"):
            response = client.get(endpoint, params={"offset": value})
            assert response.status_code == 422, (endpoint, value, response.text)
        response = client.get(endpoint, params={"limit": "1.0"})
        assert response.status_code == 422, (endpoint, response.text)


def test_all_page_endpoints_accept_canonical_integer_bounds() -> None:
    client = TestClient(build_http_app())
    document = _saved_document(client)
    endpoints = (
        "/api/v1/backtests",
        "/api/v1/strategies",
        f"/api/v1/strategies/{document['strategy_id']}/revisions",
    )

    for endpoint in endpoints:
        for offset in (0, 1, PageRequest.MAX_OFFSET):
            response = client.get(endpoint, params={"offset": str(offset)})
            assert response.status_code == 200, (endpoint, offset, response.text)
            assert response.json()["offset"] == offset
        for limit in (1, PageRequest.MAX_LIMIT):
            response = client.get(endpoint, params={"limit": str(limit)})
            assert response.status_code == 200, (endpoint, limit, response.text)
            assert response.json()["limit"] == limit
