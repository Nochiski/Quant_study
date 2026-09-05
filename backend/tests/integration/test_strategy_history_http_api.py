from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def _template(client: TestClient, title: str) -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["title"] = title
    return spec


def test_strategy_list_is_deterministic_paginated_and_projects_latest_revision() -> None:
    client = TestClient(build_http_app())
    created = [
        client.post("/api/v1/strategies", json=_template(client, title)).json()
        for title in ("Second", "First")
    ]
    expected_ids = sorted(item["spec"]["identity"]["strategy_id"] for item in created)
    revised_id = expected_ids[0]
    latest = client.get(f"/api/v1/strategies/{revised_id}").json()
    latest["spec"]["title"] = "Latest title"
    revised = client.post(
        f"/api/v1/strategies/{revised_id}/revisions",
        json={"expected_revision": 1, "spec": latest["spec"]},
    )
    assert revised.status_code == 201, revised.text

    first_page = client.get("/api/v1/strategies", params={"offset": 0, "limit": 1})
    second_page = client.get("/api/v1/strategies", params={"offset": 1, "limit": 1})
    assert first_page.status_code == second_page.status_code == 200
    revision_history = client.get(
        f"/api/v1/strategies/{revised_id}/revisions",
        params={"offset": 1, "limit": 1},
    ).json()
    assert first_page.json() == {
        "items": [
            {
                "strategy_id": revised_id,
                "title": "Latest title",
                "latest_revision": 2,
                "spec_hash": revised.json()["spec_hash"],
                "updated_at": revision_history["items"][0]["created_at"],
            }
        ],
        "total": 2,
        "offset": 0,
        "limit": 1,
    }
    assert [item["strategy_id"] for item in second_page.json()["items"]] == expected_ids[1:]


def test_strategy_list_returns_canonical_empty_page_and_rejects_invalid_bounds() -> None:
    client = TestClient(build_http_app())
    client.post("/api/v1/strategies", json=_template(client, "Only strategy"))

    beyond_end = client.get("/api/v1/strategies", params={"offset": 20, "limit": 20})
    assert beyond_end.status_code == 200
    assert beyond_end.json() == {"items": [], "total": 1, "offset": 20, "limit": 20}
    assert client.get("/api/v1/strategies", params={"offset": -1}).status_code == 422
    assert client.get("/api/v1/strategies", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/strategies", params={"limit": 501}).status_code == 422
