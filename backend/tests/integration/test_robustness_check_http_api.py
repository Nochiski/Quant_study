"""튼튼한지 확인하기(검증 랩 V5-02, US-DM-11)의 HTTP 계약 — 실제 bootstrap 과 mock 실행으로 돈다."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

from ..backtest_run_wait import wait_for_terminal_state
from .test_trial_ledger_http_api import _saved_run


def _completed(client: TestClient, request: dict[str, Any]) -> str:
    accepted = client.post("/api/v1/backtests", json=request)
    assert accepted.status_code == 202, accepted.text
    run_id = str(accepted.json()["run"]["run_id"])
    assert wait_for_terminal_state(client, run_id, timeout_s=60.0)["status"] == "completed"
    return run_id


def test_a_saved_run_previews_creates_and_finds_its_robustness_check() -> None:
    client = TestClient(build_http_app())
    request = _saved_run(client)
    request["environment"] = {**request["environment"], "start": "2021-01-04", "end": "2022-12-30"}
    run_id = _completed(client, request)
    path = f"/api/v1/backtests/{run_id}/robustness-check"

    preview = client.post(path + "/preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    # 템플릿 전략은 파라미터가 없어 칸 하나로 워크포워드만 돈다. 기반 실행이 이미 센 시도의
    # 재확인이라 N 이 그대로다.
    assert body["combination_count"] == 1
    assert (body["trial_count"], body["new_trial_count"]) == (1, 0)
    (window,) = body["design"]["windows"]
    assert (window["test_start"], window["test_end"]) == ("2022-01-04", "2022-12-30")
    assert client.get(path + "s").json() == []

    created = client.post(path)
    assert created.status_code == 202, created.text
    experiment = created.json()
    assert experiment["record"]["source_run_id"] == run_id
    assert experiment["record"]["split"] == {
        "mode": "rolling",
        "train_years": 1,
        "test_years": 1,
        "embargo_sessions": 5,
        "selection_rule": "neighbor_mean_sharpe_max",
    }
    listed = client.get(path + "s").json()
    assert [item["record"]["experiment_id"] for item in listed] == [
        experiment["record"]["experiment_id"]
    ]


def test_refusals_name_their_reason() -> None:
    client = TestClient(build_http_app())
    short = _saved_run(client)
    short["environment"] = {**short["environment"], "start": "2021-01-04", "end": "2021-12-30"}
    short_run = _completed(client, short)
    inline = {
        "strategy": client.get("/api/v1/strategies/template").json(),
        "environment": short["environment"],
        "core": "python",
    }
    inline_run = _completed(client, inline)

    too_short = client.post(f"/api/v1/backtests/{short_run}/robustness-check/preview")
    unsaved = client.post(f"/api/v1/backtests/{inline_run}/robustness-check")
    missing = client.post("/api/v1/backtests/run-gone/robustness-check/preview")

    assert too_short.status_code == 422
    assert too_short.json()["detail"]["code"] == "experiment.robustness.period_too_short"
    assert unsaved.status_code == 422
    assert unsaved.json()["detail"]["code"] == "experiment.base.unsaved"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "experiment.robustness.run_not_found"
    assert client.get(f"/api/v1/backtests/{inline_run}/robustness-checks").json() == []
