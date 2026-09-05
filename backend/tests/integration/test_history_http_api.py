from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def _short_template(client: TestClient, title: str) -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["title"] = title
    spec["data"].update({"start": "2026-01-02", "end": "2026-02-20"})
    spec["portfolio"].update(
        {
            "selection_count": 2,
            "rebalance": "every_n_sessions",
            "rebalance_every_n_sessions": 5,
        }
    )
    spec["risk"].update({"max_name_weight": 0.6, "max_sector_weight": 1.0})
    return spec


def test_strategy_list_is_deterministic_paginated_and_links_latest_revision() -> None:
    client = TestClient(build_http_app())
    saved = [
        client.post("/api/v1/strategies", json=_short_template(client, title)).json()
        for title in ("Second", "First")
    ]
    expected_ids = sorted(item["spec"]["identity"]["strategy_id"] for item in saved)

    page = client.get("/api/v1/strategies", params={"offset": 0, "limit": 1})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2
    assert [item["strategy_id"] for item in page.json()["items"]] == expected_ids[:1]
    all_items = client.get("/api/v1/strategies").json()["items"]
    assert [item["strategy_id"] for item in all_items] == expected_ids
    assert all(item["latest_revision"] == 1 for item in all_items)


def test_backtest_history_is_newest_first_paginated_and_filterable_by_strategy() -> None:
    client = TestClient(build_http_app())
    first_spec = _short_template(client, "Saved history")
    saved = client.post("/api/v1/strategies", json=first_spec).json()
    identity = saved["spec"]["identity"]
    saved_request = {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": identity["strategy_id"],
            "revision": identity["revision"],
            "expected_spec_hash": saved["spec_hash"],
        },
        "core": "python",
    }
    inline_request = {
        "strategy_source": {
            "kind": "inline_draft",
            "spec": _short_template(client, "Inline history"),
        },
        "core": "python",
    }
    first = client.post("/api/v1/backtests", json=saved_request)
    second = client.post("/api/v1/backtests", json=inline_request)
    assert first.status_code == second.status_code == 202

    history = client.get("/api/v1/backtests").json()
    assert history["total"] == 2
    assert [item["run"]["run_id"] for item in history["items"]] == [
        second.json()["run"]["run_id"],
        first.json()["run"]["run_id"],
    ]
    assert history["items"][0]["strategy_provenance"]["kind"] == "inline_draft"
    assert history["items"][1]["strategy_provenance"]["strategy_id"] == identity["strategy_id"]

    filtered = client.get(
        "/api/v1/backtests",
        params={"strategy_id": identity["strategy_id"], "limit": 1},
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["run"]["run_id"] == first.json()["run"]["run_id"]
