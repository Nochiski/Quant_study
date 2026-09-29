"""시도 원장 HTTP — 원장 조회·계열 합치기·실행 전 미리 계산(검증 랩 spec D2, V1-05)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app
from tests.backtest_run_wait import wait_for_terminal_state


def _saved_run(client: TestClient, **environment: str) -> dict[str, Any]:
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.2"
    saved = client.post(
        "/api/v1/strategy-documents",
        json={"format": "json", "source": json.dumps(template, default=str)},
    ).json()
    return {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": saved["strategy_id"],
            "revision": saved["revision"],
            "expected_spec_hash": saved["spec_hash"],
        },
        "core": "python",
        # 템플릿 기본 리밸런싱이 월간이라 구간이 한 달을 넘어야 tape 에 프레임이 생긴다.
        "environment": {
            "start": "2025-01-02",
            "end": "2025-03-31",
            "universe_id": "krx.common-stock",
        }
        | environment,
    }


def test_the_ledger_counts_a_run_once_and_the_preview_agrees() -> None:
    client = TestClient(build_http_app())
    request = _saved_run(client)
    lineage = request["strategy_source"]["strategy_id"]

    before = client.post("/api/v1/backtests/trial-preview", json=request)
    run = client.post("/api/v1/backtests", json=request).json()["run"]
    assert wait_for_terminal_state(client, run["run_id"])["status"] == "completed"
    ledger = client.get(f"/api/v1/strategies/{lineage}/trials").json()
    after = client.post("/api/v1/backtests/trial-preview", json=request).json()

    assert before.status_code == 200, before.text
    expected = {"lineage_id": lineage, "trial_key": ledger["trials"][0]["trial_key"]}
    assert before.json() == expected | {
        "trial_count": 0,
        "new_trial": True,
        "trial_count_after": 1,
        "reason": "new_trial",
    }
    assert after == expected | {
        "trial_count": 1,
        "new_trial": False,
        "trial_count_after": 1,
        "reason": "recheck",
    }
    assert ledger["trial_count"] == 1
    assert ledger["trials"][0]["runs"][0] | {"created_at": None} == {
        "run_id": run["run_id"],
        "status": "completed",
        "created_at": None,
        "role": "counted",
    }


def test_a_sealed_run_is_blocked_with_the_start_contract_and_the_preview_leaves_no_record() -> None:
    client = TestClient(build_http_app())
    request = _saved_run(client, start="2019-06-03")
    lineage = request["strategy_source"]["strategy_id"]

    preview = client.post("/api/v1/backtests/trial-preview", json=request)
    blocked_after_preview = client.get(f"/api/v1/strategies/{lineage}/trials").json()["blocked"]
    start = client.post("/api/v1/backtests", json=request)
    blocked = client.get(f"/api/v1/strategies/{lineage}/trials").json()["blocked"]

    assert preview.status_code == start.status_code == 422
    assert preview.json()["detail"]["code"] == start.json()["detail"]["code"]
    assert start.json()["detail"]["code"] == "backtest.run.research_window_violation"
    assert (blocked_after_preview, [item["start"] for item in blocked]) == ([], ["2019-06-03"])


def test_merging_a_lineage_twice_or_an_unknown_one_is_refused() -> None:
    client = TestClient(build_http_app())
    first = _saved_run(client)["strategy_source"]["strategy_id"]
    second = _saved_run(client)["strategy_source"]["strategy_id"]

    merged = client.post(
        f"/api/v1/strategies/{first}/trials/merge", json={"source_strategy_id": second}
    )
    again = client.post(
        f"/api/v1/strategies/{second}/trials/merge", json={"source_strategy_id": first}
    )
    unknown = client.post(
        f"/api/v1/strategies/{first}/trials/merge", json={"source_strategy_id": "missing"}
    )

    assert merged.status_code == 200, merged.text
    assert (merged.json()["lineage_id"], merged.json()["merged_lineage_ids"]) == (first, [second])
    assert (again.status_code, again.json()["detail"]["code"]) == (
        409,
        "backtest.lineage.already_merged",
    )
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "strategy.not_found")
    missing = client.get("/api/v1/strategies/missing/trials")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "strategy.not_found")
