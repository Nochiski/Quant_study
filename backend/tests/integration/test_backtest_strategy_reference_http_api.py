"""P1-09 saved-revision backtest references and run-manifest provenance.

Runs use the python core so the suite works without the Rust build; the executor consumes the
same tape regardless of core.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for _ in range(400):
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        if state["status"] in {"completed", "failed", "cancelled"}:
            return state
        time.sleep(0.025)
    return state


def _saved_template(client: TestClient) -> dict[str, Any]:
    """Save the JSON template as a document revision so the run can reference it."""
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.0"
    source = json.dumps(template, ensure_ascii=False, indent=2, default=str)
    created = client.post("/api/v1/strategy-documents", json={"source": source, "format": "json"})
    assert created.status_code == 201, created.text
    return created.json()


def test_run_by_saved_revision_records_the_exact_revision_in_the_manifest() -> None:
    client = TestClient(build_http_app())
    document = _saved_template(client)
    reference = {
        "kind": "saved_revision",
        "strategy_id": document["strategy_id"],
        "revision": 1,
        "expected_spec_hash": document["spec_hash"],
    }

    accepted = client.post(
        "/api/v1/backtests", json={"strategy_source": reference, "core": "python"}
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]
    assert _wait(client, run_id)["status"] == "completed"

    manifest = client.get(f"/api/v1/backtests/{run_id}/result").json()["manifest"]
    assert manifest["strategy_provenance"] == {
        "kind": "saved_revision",
        "spec_hash": document["spec_hash"],
        "schema_version": "1.0",
        "strategy_id": document["strategy_id"],
        "revision": 1,
        "source_hash": document["source_hash"],
    }
    assert manifest["strategy_hash"] == document["spec_hash"]
    assert manifest["run_spec"]["strategy"]["identity"]["strategy_id"] == document["strategy_id"]
    assert manifest["run_spec"]["strategy_source"] == reference
    assert manifest["schema_version"] == "backtest-run-v2"


def test_stale_or_missing_reference_fails_before_a_run_exists() -> None:
    client = TestClient(build_http_app())
    document = _saved_template(client)
    base = {
        "kind": "saved_revision",
        "strategy_id": document["strategy_id"],
        "revision": 1,
    }

    stale = client.post(
        "/api/v1/backtests",
        json={"strategy_source": {**base, "expected_spec_hash": "0" * 64}, "core": "python"},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "backtest.strategy.stale"
    assert document["spec_hash"] in stale.json()["detail"]["message"]

    missing = client.post(
        "/api/v1/backtests",
        json={
            "strategy_source": {**base, "revision": 9, "expected_spec_hash": document["spec_hash"]},
            "core": "python",
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "backtest.strategy.not_found"


def test_inline_draft_and_legacy_inline_spec_record_inline_provenance() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()

    legacy = client.post("/api/v1/backtests", json={"strategy": template, "core": "python"})
    assert legacy.status_code == 202, legacy.text
    draft = client.post(
        "/api/v1/backtests",
        json={
            "strategy_source": {"kind": "inline_draft", "spec": template, "source_hash": "ab" * 32},
            "core": "python",
        },
    )
    assert draft.status_code == 202, draft.text

    for response, source_hash in ((legacy, None), (draft, "ab" * 32)):
        run_id = response.json()["run"]["run_id"]
        assert _wait(client, run_id)["status"] == "completed"
        provenance = client.get(f"/api/v1/backtests/{run_id}/result").json()["manifest"][
            "strategy_provenance"
        ]
        assert provenance["kind"] == "inline_draft"
        assert provenance["strategy_id"] is None and provenance["revision"] is None
        assert provenance["source_hash"] == source_hash
        assert provenance["spec_hash"]

    both = client.post(
        "/api/v1/backtests",
        json={
            "strategy": template,
            "strategy_source": {"kind": "inline_draft", "spec": template},
            "core": "python",
        },
    )
    neither = client.post("/api/v1/backtests", json={"core": "python"})
    assert both.status_code == 422 and neither.status_code == 422


def test_both_and_neither_return_the_same_error_shape() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()

    both = client.post(
        "/api/v1/backtests",
        json={
            "strategy": template,
            "strategy_source": {"kind": "inline_draft", "spec": template},
            "core": "python",
        },
    )
    neither = client.post("/api/v1/backtests", json={"core": "python"})

    for response in (both, neither):
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "backtest.run.invalid"
        assert "strategy" in response.json()["detail"]["message"]


def test_reference_to_another_revisions_hash_is_stale() -> None:
    client = TestClient(build_http_app())
    document = _saved_template(client)
    revised = client.post(
        f"/api/v1/strategy-documents/{document['strategy_id']}/revisions",
        json={
            "source": json.dumps(
                {**json.loads(document["source"]), "title": "v2"}, ensure_ascii=False
            ),
            "format": "json",
            "expected_revision": 1,
        },
    ).json()
    assert revised["spec_hash"] != document["spec_hash"]

    stale = client.post(
        "/api/v1/backtests",
        json={
            "strategy_source": {
                "kind": "saved_revision",
                "strategy_id": document["strategy_id"],
                "revision": 1,
                "expected_spec_hash": revised["spec_hash"],
            },
            "core": "python",
        },
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "backtest.strategy.stale"
