from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._backtest_contract import Backtest422Response
from strategy_workbench.bootstrap.facade.http import build_http_app


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
