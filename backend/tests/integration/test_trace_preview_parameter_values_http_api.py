"""선택한 파라미터가 저장·인라인 추적과 미리보기 값까지 도달하는 HTTP 계약."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._execution_error_contract import (
    Portfolio422Response,
)
from strategy_workbench.adapters.inbound.http_api._trace_contract import Trace422Response
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from tests.application.test_run_parameter_values import _ENVIRONMENT, _spec
from tests.integration.test_strategy_trace_http_api import _save


def _request(client: TestClient, saved: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = TypeAdapter(StrategySpec).dump_python(_spec(), mode="json")
    environment = TypeAdapter(RunEnvironment).dump_python(_ENVIRONMENT, mode="json")
    preview = {"spec": spec, "environment": environment}
    source = {"kind": "inline_draft", "spec": spec, "source_hash": "a" * 64}
    if saved:
        revision = _save(client, spec)
        source = {
            "kind": "saved_revision",
            "strategy_id": revision["strategy_id"],
            "revision": revision["revision"],
            "expected_spec_hash": revision["spec_hash"],
        }
    return preview, {
        "strategy_source": source,
        "environment": environment,
        "factor_id": "scaled_momentum",
        "security_ids": [],
        "node_ids": ["scaled"],
        "include_raw": True,
    }


@pytest.mark.parametrize("saved", [False, True])
def test_parameter_values_change_trace_and_tape_not_source_or_plan_identity(saved: bool) -> None:
    client = TestClient(build_http_app())
    preview_request, trace_request = _request(client, saved)
    base = client.post("/api/v1/portfolio/preview", json=preview_request)
    changed = client.post(
        "/api/v1/portfolio/preview", json={**preview_request, "parameter_values": {"scale": -1}}
    )
    assert base.status_code == changed.status_code == 200, changed.text
    first, second = base.json(), changed.json()
    assert first["parameter_values"] == {"scale": 1.0}
    assert second["parameter_values"] == {"scale": -1.0}
    assert first["tape"]["strategy_hash"] == second["tape"]["strategy_hash"]
    assert first["tape"]["tape_hash"] != second["tape"]["tape_hash"]
    frame = first["tape"]["frames"][-1]
    ids = [item["security_id"] for item in frame["candidates"]]
    trace_request = {**trace_request, "security_ids": ids, "as_of": frame["signal_as_of"]}
    normal = client.post("/api/v1/strategies/debug/trace", json=trace_request)
    flipped = client.post(
        "/api/v1/strategies/debug/trace", json={**trace_request, "parameter_values": {"scale": -1}}
    )
    assert normal.status_code == flipped.status_code == 200, flipped.text
    before, after = normal.json(), flipped.json()
    assert after["parameter_values"] == {"scale": -1.0}
    assert before["spec_hash"] == after["spec_hash"] == first["tape"]["strategy_hash"]
    assert before["provenance"] == after["provenance"]
    assert before["plan_hash"] == after["plan_hash"]
    values = {row["security_id"]: row["value"] for row in before["trace"]["rows"]}
    assert values and all(value is not None for value in values.values())
    assert {row["security_id"]: row["value"] for row in after["trace"]["rows"]} == {
        key: -value for key, value in values.items()
    }
    assert after["target"]["targets"] == second["tape"]["frames"][-1]["targets"]
    graph = preview_request["spec"]["factors"][0]["graph"]
    explained = client.post(
        "/api/v1/factors/explain", json={"graph": graph, "parameter_ids": ["scale"]}
    )
    assert explained.status_code == 200
    assert after["plan_hash"] == explained.json()["plan"]["plan_hash"]
    if saved:
        source = trace_request["strategy_source"]
        document = client.get(
            f"/api/v1/strategies/{source['strategy_id']}/revisions/{source['revision']}/document"
        )
        assert document.status_code == 200
        assert document.json()["spec"]["parameters"][0]["default"] == 1.0


@pytest.mark.parametrize(
    "parameters", [{"scale": True}, {"scale": 2}, {"scale": "bad"}, {"unknown": 1}]
)
@pytest.mark.parametrize("saved", [False, True])
def test_invalid_values_have_existing_coded_parameter_error_for_both_endpoints(
    parameters: dict[str, Any],
    saved: bool,
) -> None:
    client = TestClient(build_http_app())
    preview, trace = _request(client, saved)
    for path, body, envelope in (
        ("/api/v1/portfolio/preview", preview, Portfolio422Response),
        ("/api/v1/strategies/debug/trace", trace, Trace422Response),
    ):
        response = client.post(path, json={**body, "parameter_values": parameters})
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "backtest.run.parameter_invalid"
        assert detail["parameter_id"] == ("unknown" if "unknown" in parameters else "scale")
        TypeAdapter(envelope).validate_python(response.json())
