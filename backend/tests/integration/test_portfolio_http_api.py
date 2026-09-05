from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._execution_error_contract import (
    Portfolio422Response,
)
from strategy_workbench.bootstrap.facade.http import build_http_app


def _preview_body(client: TestClient) -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"].update({"start": "2026-01-02", "end": "2026-01-16"})
    spec["portfolio"].update(
        {
            "selection_count": 2,
            "rebalance": "every_n_sessions",
            "rebalance_every_n_sessions": 3,
        }
    )
    spec["risk"].update({"max_name_weight": 0.6, "max_sector_weight": 1.0})
    return {"spec": spec}


def test_portfolio_preview_returns_candidates_target_tape_and_engine_contract() -> None:
    client = TestClient(build_http_app())
    body = _preview_body(client)

    first = client.post("/api/v1/portfolio/preview", json=body)
    second = client.post("/api/v1/portfolio/preview", json=body)

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["engine"]["compatible"] is True
    assert payload["engine"]["requirements"]["actions"] == [
        "no_action",
        "set_portfolio_target",
    ]
    assert len(payload["tape"]["tape_hash"]) == 64
    assert payload["tape"]["frames"]
    assert payload["tape"]["frames"][0]["candidates"]


def test_close_signal_is_never_executed_before_the_next_session() -> None:
    client = TestClient(build_http_app())

    response = client.post("/api/v1/portfolio/preview", json=_preview_body(client))

    assert response.status_code == 200
    for frame in response.json()["tape"]["frames"]:
        assert frame["execution_on"] > frame["signal_as_of"]


def test_invalid_portfolio_configuration_returns_structured_validation() -> None:
    client = TestClient(build_http_app())
    body = _preview_body(client)
    body["spec"]["portfolio"]["weighting"] = "risk"
    body["spec"]["risk"]["risk_field_id"] = None

    response = client.post("/api/v1/portfolio/preview", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "portfolio.strategy.invalid"
    codes = {item["code"] for item in response.json()["detail"]["validation"]["issues"]}
    assert "strategy.risk.risk_field" in codes
    TypeAdapter(Portfolio422Response).validate_python(response.json())


def test_portfolio_preview_openapi_declares_coded_and_malformed_422() -> None:
    client = TestClient(build_http_app())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/portfolio/preview"]["post"]
    detail = schema["components"]["schemas"]["PortfolioUnprocessableResponse"]["properties"][
        "detail"
    ]

    assert "422" in operation["responses"]
    assert detail["discriminator"]["propertyName"] == "code"
    assert set(detail["discriminator"]["mapping"]) == {
        "portfolio.data.unavailable",
        "portfolio.strategy.invalid",
    }
    malformed = client.post("/api/v1/portfolio/preview", json={"spec": {}})
    assert malformed.status_code == 422
    assert isinstance(malformed.json()["detail"], list)
    TypeAdapter(Portfolio422Response).validate_python(malformed.json())
