from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._backtest_contract import Backtest422Response
from strategy_workbench.adapters.inbound.http_api.facade.api import create_app
from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    ArtifactCommit,
    BacktestDataPort,
)
from strategy_workbench.application.backtest_run.facade.runs import BacktestRunService
from strategy_workbench.bootstrap.facade.container import BackendContainer, build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult


class _CommitBarrierStore:
    """Test adapter that pauses one real local commit at the cancellation race boundary."""

    def __init__(self, delegate: LocalArtifactStore, blocked_run_id: str) -> None:
        self._delegate = delegate
        self._blocked_run_id = blocked_run_id
        self.entered = Event()
        self.release = Event()
        self.discarded: list[str] = []

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        if result.manifest.run_id == self._blocked_run_id:
            self.entered.set()
            if not self.release.wait(timeout=30):
                raise TimeoutError("artifact commit test barrier was not released")
        return self._delegate.commit(result)

    def discard(self, run_id: str) -> None:
        self.discarded.append(run_id)
        self._delegate.discard(run_id)


def _app_with_backtests(container: BackendContainer, backtests: BacktestRunService) -> FastAPI:
    return create_app(
        strategy_design=container.strategy_design,
        strategy_authoring=container.strategy_authoring,
        strategy_documents=container.strategy_documents,
        strategy_drafts=container.strategy_drafts,
        equity_workspace=container.equity_workspace,
        factor_research=container.factor_research,
        portfolio_design=container.portfolio_design,
        strategy_traces=container.strategy_traces,
        backtest_runs=backtests,
    )


def _run_body(client: TestClient, core: str = "rust") -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"].update({"start": "2026-01-02", "end": "2026-02-20"})
    spec["portfolio"].update(
        {
            "selection_count": 2,
            "rebalance": "every_n_sessions",
            "rebalance_every_n_sessions": 5,
        }
    )
    spec["risk"].update({"max_name_weight": 0.6, "max_sector_weight": 1.0})
    return {
        "strategy": spec,
        "core": core,
        "benchmark_security_id": "005930",
        "metric_windows": [
            {
                "scope": "out_of_sample",
                "start": "2026-02-02",
                "end": "2026-02-20",
                "label": "OOS",
            }
        ],
    }


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for _ in range(200):
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        if state["status"] in {"completed", "failed", "cancelled"}:
            return state
        time.sleep(0.025)
    raise AssertionError(f"run did not finish: {state}")


def _execute(client: TestClient, core: str) -> dict[str, Any]:
    accepted = client.post("/api/v1/backtests", json=_run_body(client, core))
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]
    state = _wait(client, run_id)
    assert state["status"] == "completed", state
    response = client.get(f"/api/v1/backtests/{run_id}/result")
    assert response.status_code == 200
    return response.json()


def test_backtest_lifecycle_exposes_progress_result_manifest_and_raw_artifacts() -> None:
    client = TestClient(build_http_app())
    accepted = client.post("/api/v1/backtests", json=_run_body(client))
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["run_id"]

    accepted_request = client.get(f"/api/v1/backtests/{run_id}/request")
    assert accepted_request.status_code == 200
    assert accepted_request.json() == {
        **_run_body(client),
        "annualization_days": 252,
        "initial_cash": 100_000_000.0,
        "strategy_source": None,
    }

    not_ready = client.get(f"/api/v1/backtests/{run_id}/result")
    assert not_ready.status_code in {200, 409}
    state = _wait(client, run_id)

    assert state["status"] == "completed", state
    assert len(state["artifact_sha256"]) == 64
    assert state["artifact_uri"].startswith("file:///")
    result = client.get(f"/api/v1/backtests/{run_id}/result").json()
    assert result["manifest"]["engine_core"] == "rust"
    assert len(result["manifest"]["run_fingerprint"]) == 64
    assert result["manifest"]["run_spec"]["strategy"] == _run_body(client)["strategy"]
    assert result["manifest"]["run_spec"]["benchmark_security_id"] == "005930"
    assert result["manifest"]["metric_registry_version"] == "metric-registry-v1"
    assert {item["code"] for item in result["manifest"]["warnings"]} == {
        "corporate_action_feed_empty",
        "mock_equity_data",
    }
    assert len(result["metric_definitions"]) == 21
    assert len(result["metrics"]) == 42
    assert {item["scope"] for item in result["metrics"]} == {
        "full",
        "out_of_sample",
    }
    assert result["series"]["equity"]
    assert result["series"]["drawdown"]
    assert result["series"]["monthly_returns"]
    assert result["series"]["rolling_sharpe"]
    assert result["artifacts"]["snapshots"]
    assert result["artifacts"]["orders"]
    assert result["artifacts"]["fills"]

    events = client.get(f"/api/v1/backtests/{run_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert '"status":"completed"' in events.text


def test_cancel_accepted_during_artifact_commit_wins_and_exact_request_replays(
    tmp_path: Path,
) -> None:
    first_run_id = "run-cancel-during-artifact"
    replay_run_id = "run-byte-exact-replay"
    run_ids = iter((first_run_id, replay_run_id))
    container = build_container(artifact_root=tmp_path / "unused")
    artifact_root = tmp_path / "artifacts"
    barrier = _CommitBarrierStore(LocalArtifactStore(artifact_root), first_run_id)
    backtests = BacktestRunService(
        container.portfolio_design,
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        barrier,
        new_id=lambda: next(run_ids),
    )
    client = TestClient(_app_with_backtests(container, backtests))
    submitted = _run_body(client, "python")
    accepted = client.post("/api/v1/backtests", json=submitted)
    assert accepted.status_code == 202
    assert accepted.json()["run"]["run_id"] == first_run_id
    assert barrier.entered.wait(timeout=30), "run never entered artifact commit"

    accepted_request = client.get(f"/api/v1/backtests/{first_run_id}/request")
    assert accepted_request.status_code == 200
    try:
        cancellation = client.post(f"/api/v1/backtests/{first_run_id}/cancel")
        assert cancellation.status_code == 200
        assert cancellation.json()["status"] == "cancel_requested"
    finally:
        barrier.release.set()

    state = _wait(client, first_run_id)
    assert state["status"] == "cancelled"
    assert state["artifact_uri"] is None
    assert state["artifact_sha256"] is None
    assert barrier.discarded == [first_run_id]
    assert not (artifact_root / first_run_id).exists()
    not_ready = client.get(f"/api/v1/backtests/{first_run_id}/result")
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["code"] == "backtest.result.not_ready"
    events = client.get(f"/api/v1/backtests/{first_run_id}/events").text
    assert '"status":"cancel_requested"' in events
    assert '"status":"cancelled"' in events
    assert '"status":"completed"' not in events

    # Reuse the server-serialized accepted request bytes, not the original client draft.
    replay = client.post(
        "/api/v1/backtests",
        content=accepted_request.content,
        headers={"content-type": "application/json"},
    )
    assert replay.status_code == 202
    assert replay.json()["run"]["run_id"] == replay_run_id
    replay_request = client.get(f"/api/v1/backtests/{replay_run_id}/request")
    assert replay_request.status_code == 200
    assert replay_request.content == accepted_request.content
    replay_state = _wait(client, replay_run_id)
    assert replay_state["status"] == "completed", replay_state
    replay_result = client.get(f"/api/v1/backtests/{replay_run_id}/result")
    assert replay_result.status_code == 200
    assert replay_result.json()["manifest"]["run_spec"] == accepted_request.json()


def test_python_reference_and_rust_core_have_golden_result_and_metric_parity() -> None:
    client = TestClient(build_http_app())

    rust = _execute(client, "rust")
    python = _execute(client, "python")

    assert rust["metrics"] == python["metrics"]
    assert rust["series"] == python["series"]
    assert rust["artifacts"] == python["artifacts"]
    assert rust["manifest"]["engine_core"] == "rust"
    assert python["manifest"]["engine_core"] == "python"


def test_backtest_unknown_run_and_invalid_metric_window_return_structured_errors() -> None:
    client = TestClient(build_http_app())
    assert client.get("/api/v1/backtests/missing").status_code == 404
    assert client.get("/api/v1/backtests/missing/request").status_code == 404
    assert client.post("/api/v1/backtests/missing/cancel").status_code == 404

    body = _run_body(client)
    body["metric_windows"][0]["start"] = "2025-01-01"
    response = client.post("/api/v1/backtests", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "backtest.run.invalid"
    TypeAdapter(Backtest422Response).validate_python(response.json())


def test_start_backtest_openapi_declares_every_actual_preflight_error() -> None:
    client = TestClient(build_http_app())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/backtests"]["post"]

    assert {"202", "404", "409", "422"} <= set(operation["responses"])
    assert operation["responses"]["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestStrategyNotFoundResponse"
    )
    assert operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestStrategyStaleResponse"
    )
    detail = schema["components"]["schemas"]["BacktestUnprocessableResponse"]["properties"][
        "detail"
    ]
    assert detail["discriminator"]["propertyName"] == "code"
    assert set(detail["discriminator"]["mapping"]) == {
        "backtest.run.invalid",
        "portfolio.data.unavailable",
        "portfolio.raw_observation.invalid",
        "portfolio.strategy.invalid",
    }

    semantic = _run_body(client, "python")
    semantic["strategy"]["portfolio"]["weighting"] = "risk"
    semantic["strategy"]["risk"]["risk_field_id"] = None
    semantic_response = client.post("/api/v1/backtests", json=semantic)
    malformed_response = client.post("/api/v1/backtests", json={"core": "not-a-core"})

    assert semantic_response.status_code == malformed_response.status_code == 422
    assert semantic_response.json()["detail"]["code"] == "portfolio.strategy.invalid"
    assert isinstance(malformed_response.json()["detail"], list)
    adapter = TypeAdapter(Backtest422Response)
    adapter.validate_python(semantic_response.json())
    adapter.validate_python(malformed_response.json())


def test_run_resource_openapi_declares_typed_not_found_and_not_ready_errors() -> None:
    client = TestClient(build_http_app())
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    run_not_found_paths = (
        ("/api/v1/backtests/{run_id}", "get"),
        ("/api/v1/backtests/{run_id}/request", "get"),
        ("/api/v1/backtests/{run_id}/cancel", "post"),
        ("/api/v1/backtests/{run_id}/events", "get"),
    )
    for path, method in run_not_found_paths:
        response = paths[path][method]["responses"]["404"]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith(
            "BacktestRunNotFoundResponse"
        )

    result_responses = paths["/api/v1/backtests/{run_id}/result"]["get"]["responses"]
    assert result_responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestRunNotFoundResponse"
    )
    assert result_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "BacktestResultNotReadyResponse"
    )
