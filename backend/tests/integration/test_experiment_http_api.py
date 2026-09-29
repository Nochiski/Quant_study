"""실험 HTTP(V3-03) — 미리 계산과 실제 원장 증가, 기반 리비전 제약, 코드화된 거절(spec D2·D5)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._experiment_routes import (
    ExperimentAdmissionErrorResponse,
    ExperimentErrorResponse,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from tests.frozen_revision_rows import FROZEN_SPEC_HASH, seed_frozen_rows

_SPLIT = {"mode": "rolling", "train_years": 1, "test_years": 1, "embargo_sessions": 0}


def _experiment(client: TestClient, **environment: str) -> dict[str, Any]:
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.2"
    template["parameters"] = [
        {
            "parameter_id": "scale",
            "default": 1.0,
            "minimum": -1.0,
            "maximum": 1.0,
            "step": 1.0,
            "kind": "float",
        }
    ]
    saved = client.post(
        "/api/v1/strategy-documents",
        json={"format": "json", "source": json.dumps(template, default=str)},
    ).json()
    run = {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": saved["strategy_id"],
            "revision": saved["revision"],
            "expected_spec_hash": saved["spec_hash"],
        },
        "core": "python",
        "environment": {
            "start": "2021-01-04",
            "end": "2023-06-30",
            "universe_id": "krx.common-stock",
        }
        | environment,
    }
    return {"run": run, "search": {"scale": None}, "split": _SPLIT}


def _wait_until_finished(client: TestClient, experiment_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        experiment = client.get(f"/api/v1/experiments/{experiment_id}").json()
        if experiment["status"] in ("completed", "cancelled"):
            return experiment
        time.sleep(0.2)
    raise AssertionError(f"experiment did not finish — experiment_id={experiment_id}")


def test_the_preview_equals_the_ledger_growth_after_the_experiment() -> None:
    client = TestClient(build_http_app())
    request = _experiment(client)
    lineage = request["run"]["strategy_source"]["strategy_id"]

    preview = client.post("/api/v1/experiments/preview", json=request)
    created = client.post("/api/v1/experiments", json=request)
    experiment = _wait_until_finished(client, created.json()["record"]["experiment_id"])
    trials = client.get(f"/api/v1/experiments/{experiment['record']['experiment_id']}/trials")
    ledger = client.get(f"/api/v1/strategies/{lineage}/trials").json()

    assert preview.status_code == 200, preview.text
    assert created.status_code == 202, created.text
    # scale 격자 -1, 0, 1 × 롤링 창 둘 = 실행 6. 창은 분할 설계가 정한 평가 구간이라 시도 키는 실험
    # 기반 실행 설정으로 낸다 — 한 칸의 두 창이 한 시도이고 N 은 0 → 3 이다.
    assert {key: preview.json()[key] for key in ("combination_count", "run_count")} == {
        "combination_count": 3,
        "run_count": 6,
    }
    assert (preview.json()["trial_count"], preview.json()["trial_count_after"]) == (0, 3)
    assert experiment["status"] == "completed"
    assert [trial["status"] for trial in trials.json()] == ["completed"] * 6
    assert ledger["trial_count"] == preview.json()["trial_count_after"]
    assert [len(group["runs"]) for group in ledger["trials"]] == [2, 2, 2]
    listed = client.get("/api/v1/experiments", params={"limit": 1}).json()
    assert [item["record"]["experiment_id"] for item in listed["items"]] == [
        experiment["record"]["experiment_id"]
    ]
    assert listed["next_after"] is None
    blank = client.post(
        f"/api/v1/experiments/{experiment['record']['experiment_id']}/selections",
        json={"trial_index": 0, "reason": "   "},
    )
    assert blank.status_code == 422, blank.text
    assert {run["run_id"] for group in ledger["trials"] for run in group["runs"]} == {
        trial["attempts"][0]["run_id"] for trial in trials.json()
    }


def test_design_and_lookup_rejections_are_coded() -> None:
    client = TestClient(build_http_app())
    request = _experiment(client)
    sealed = _experiment(client, start="2019-06-03")
    inline = request | {
        "run": request["run"]
        | {"strategy_source": None, "strategy": client.get("/api/v1/strategies/template").json()}
    }

    responses = {
        "inline": client.post("/api/v1/experiments", json=inline),
        "split": client.post(
            "/api/v1/experiments/preview", json=request | {"split": _SPLIT | {"train_years": 0}}
        ),
        "search": client.post(
            "/api/v1/experiments/preview", json=request | {"search": {"missing": [1]}}
        ),
        "field": client.post(
            "/api/v1/experiments/preview",
            json=request | {"run": request["run"] | {"initial_cash": -1}},
        ),
        "sealed": client.post("/api/v1/experiments", json=sealed),
    }
    missing = client.get("/api/v1/experiments/missing")

    assert {name: response.json()["detail"]["code"] for name, response in responses.items()} == {
        "inline": "experiment.base.unsaved",
        "split": "experiment.split.invalid",
        "search": "experiment.search.unknown_parameter",
        "field": "backtest.run.field_invalid",
        "sealed": "backtest.run.research_window_violation",
    }
    for response in responses.values():
        assert response.status_code == 422, response.text
        TypeAdapter(ExperimentAdmissionErrorResponse).validate_python(response.json())
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "experiment.not_found")
    TypeAdapter(ExperimentErrorResponse).validate_python(missing.json())
    # 실험 기반 검사는 실행 요청이 아니므로 봉인 원장에 차단 기록을 남기지 않는다.
    sealed_lineage = sealed["run"]["strategy_source"]["strategy_id"]
    assert client.get(f"/api/v1/strategies/{sealed_lineage}/trials").json()["blocked"] == []


def test_a_frozen_revision_cannot_be_the_base(tmp_path: Path) -> None:
    path = tmp_path / "frozen-http.sqlite3"
    seed_frozen_rows(path)
    client = TestClient(build_http_app(strategy_repository_path=path))
    run = {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": "frozen-doc",
            "revision": 1,
            "expected_spec_hash": FROZEN_SPEC_HASH,
        },
        "environment": {
            "start": "2021-01-04",
            "end": "2023-06-30",
            "universe_id": "krx.common-stock",
        },
    }

    response = client.post("/api/v1/experiments", json={"run": run, "search": {}, "split": _SPLIT})

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "backtest.strategy.requires_upgrade"


def test_a_held_experiment_can_be_paused_streamed_and_cancelled() -> None:
    """V3-04: e2e 훅이 trial 을 붙잡은 동안 일시정지·우선순위·취소와 진행 스트림을 본다."""
    client = TestClient(build_http_app(trial_hold_seconds=30))
    experiment_id = client.post("/api/v1/experiments", json=_experiment(client)).json()["record"][
        "experiment_id"
    ]
    base = f"/api/v1/experiments/{experiment_id}"

    deadline = time.monotonic() + 30
    while "running" not in [trial["status"] for trial in client.get(f"{base}/trials").json()]:
        assert time.monotonic() < deadline, "no trial was held running"
        time.sleep(0.1)
    prioritised = client.patch(f"{base}/controls", json={"priority": 2})
    paused = client.patch(f"{base}/controls", json={"paused": True})
    refused = client.patch(f"{base}/controls", json={"priority": 99})
    cancelled = client.post(f"{base}/cancel")
    with client.stream("GET", f"{base}/events") as stream:
        frames = [line for line in stream.iter_lines() if line.startswith("data:")]
    missing = client.get("/api/v1/experiments/missing/events")

    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["record"]["controls"] == {"paused": True, "priority": 2}
    assert paused.json()["trial_counts"] == {"running": 1, "queued": 5}
    assert (refused.status_code, refused.json()["detail"]["code"]) == (
        422,
        "backtest.run.field_invalid",
    )
    assert cancelled.json()["status"] == "cancelled"
    # 끝난 실험의 스트림은 마지막 진행 한 번을 보내고 닫힌다.
    # 도는 trial 이 멈출 때까지 스트림은 열려 있고, 마지막 프레임이 최종 수다.
    final = json.loads(frames[-1][len("data:") :])
    assert final["status"] == "cancelled"
    assert set(final["trial_counts"]) == {"cancelled"}
    assert prioritised.json()["record"]["controls"] == {"paused": False, "priority": 2}
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "experiment.not_found")
