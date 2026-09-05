"""P5-01 scoped trace HTTP contract over the same FactorGraph/TargetTape calculation path."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._trace_contract import (
    Trace422Response,
    TraceStrategyNotFoundResponse,
    TraceStrategyStaleResponse,
)
from strategy_workbench.bootstrap.facade.http import build_http_app


def _scope(client: TestClient, spec: dict[str, Any]) -> tuple[str, list[str], str]:
    preview = client.post("/api/v1/portfolio/preview", json={"spec": spec})
    assert preview.status_code == 200, preview.text
    frame = preview.json()["tape"]["frames"][-1]
    security_ids = sorted(item["security_id"] for item in frame["candidates"][:3])
    factor = spec["factors"]["factors"][0]
    return frame["signal_as_of"], security_ids, factor["factor_id"]


def _inline_request(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = client.get("/api/v1/strategies/template").json()
    as_of, security_ids, factor_id = _scope(client, spec)
    factor = spec["factors"]["factors"][0]
    return spec, {
        "strategy_source": {"kind": "inline_draft", "spec": spec},
        "as_of": as_of,
        "security_ids": security_ids,
        "factor_id": factor_id,
        "node_ids": [factor["graph"]["output_node_id"]],
        "include_raw": True,
        "limit": 500,
    }


def _save(client: TestClient, spec: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(spec)
    document.pop("identity")
    document["schema_version"] = "1.0"
    response = client.post(
        "/api/v1/strategy-documents",
        json={
            "source": json.dumps(document, ensure_ascii=False, indent=2),
            "format": "json",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_inline_trace_matches_preview_target_and_is_deterministic() -> None:
    client = TestClient(build_http_app())
    spec, request = _inline_request(client)

    first = client.post("/api/v1/strategies/debug/trace", json=request)
    second = client.post("/api/v1/strategies/debug/trace", json=request)

    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    payload = first.json()
    assert len(payload["spec_hash"]) == len(payload["plan_hash"]) == 64
    assert payload["registry_version"] == "factor-registry-v1"
    assert payload["provenance"] == {
        "kind": "inline_draft",
        "spec_hash": payload["spec_hash"],
        "schema_version": "1.0",
        "strategy_id": None,
        "revision": None,
        "source_hash": None,
    }
    assert payload["trace"]["returned"] == len(request["security_ids"])
    assert payload["trace"]["has_more"] is False
    assert [row["security_id"] for row in payload["trace"]["rows"]] == sorted(
        request["security_ids"]
    )
    assert payload["raw"] and payload["raw_truncated"] is False

    factor = spec["factors"]["factors"][0]
    explained = client.post(
        "/api/v1/factors/explain",
        json={
            "graph": factor["graph"],
            "parameter_ids": [item["parameter_id"] for item in spec["parameters"]],
            "factor_ids": [item["factor_id"] for item in spec["factors"]["factors"]],
        },
    )
    assert explained.status_code == 200, explained.text
    assert payload["plan_hash"] == explained.json()["plan"]["plan_hash"]

    preview = client.post("/api/v1/portfolio/preview", json={"spec": spec}).json()["tape"]
    assert payload["snapshot_id"] == preview["data_snapshot_id"]
    frame = next(item for item in preview["frames"] if item["signal_as_of"] == request["as_of"])
    expected_candidates = [
        item for item in frame["candidates"] if item["security_id"] in request["security_ids"]
    ]
    expected_targets = [
        item for item in frame["targets"] if item["security_id"] in request["security_ids"]
    ]
    assert payload["target"]["candidates"] == expected_candidates
    assert payload["target"]["targets"] == expected_targets
    by_security = {item["security_id"]: item for item in expected_candidates}
    for row in payload["trace"]["rows"]:
        assert row["value"] == by_security[row["security_id"]]["composite_score"]


def test_trace_page_is_stable_and_bounded() -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)
    request["node_ids"] = []
    request["include_raw"] = False

    whole = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "offset": 0, "limit": 2}
    )
    first = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "offset": 0, "limit": 1}
    )
    second = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "offset": 1, "limit": 1}
    )

    assert whole.status_code == first.status_code == second.status_code == 200
    assert first.json()["trace"]["has_more"] is True
    assert first.json()["trace"]["rows"] + second.json()["trace"]["rows"] == whole.json()[
        "trace"
    ]["rows"]
    assert whole.json()["raw"] == []

    over_cap = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "offset": 9_900, "limit": 200}
    )
    assert over_cap.status_code == 422


def test_starting_holdings_change_the_actual_target_and_unknown_holding_is_rejected() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["portfolio"]["minimum_trade_weight"] = 0.2
    preview = client.post("/api/v1/portfolio/preview", json={"spec": spec})
    assert preview.status_code == 200, preview.text
    frame = preview.json()["tape"]["frames"][0]
    security_ids = sorted(item["security_id"] for item in frame["candidates"])
    factor = spec["factors"]["factors"][0]
    request = {
        "strategy_source": {"kind": "inline_draft", "spec": spec},
        "as_of": frame["signal_as_of"],
        "security_ids": security_ids,
        "factor_id": factor["factor_id"],
        "node_ids": [factor["graph"]["output_node_id"]],
        "starting_holdings": [],
    }

    empty_book = client.post("/api/v1/strategies/debug/trace", json=request)
    seeded_book = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **request,
            "starting_holdings": [
                {"security_id": security_id, "weight": 0.1}
                for security_id in security_ids
            ],
        },
    )

    assert empty_book.status_code == seeded_book.status_code == 200
    assert empty_book.json()["target"]["targets"] == []
    assert {item["security_id"] for item in seeded_book.json()["target"]["targets"]} == set(
        security_ids
    )

    unknown = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **request,
            "starting_holdings": [{"security_id": "NOPE", "weight": 0.1}],
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "trace.request.invalid"


def test_saved_revision_trace_is_hash_guarded_and_errors_are_structured() -> None:
    client = TestClient(build_http_app())
    spec, inline = _inline_request(client)
    saved = _save(client, spec)
    source = {
        "kind": "saved_revision",
        "strategy_id": saved["strategy_id"],
        "revision": saved["revision"],
        "expected_spec_hash": saved["spec_hash"],
    }
    request = {**inline, "strategy_source": source, "include_raw": False}

    response = client.post("/api/v1/strategies/debug/trace", json=request)

    assert response.status_code == 200, response.text
    assert response.json()["spec_hash"] == saved["spec_hash"]
    assert response.json()["provenance"] == {
        "kind": "saved_revision",
        "spec_hash": saved["spec_hash"],
        "schema_version": "1.0",
        "strategy_id": saved["strategy_id"],
        "revision": 1,
        "source_hash": saved["source_hash"],
    }

    stale = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **request,
            "strategy_source": {**source, "expected_spec_hash": "0" * 64},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "trace.strategy.stale"
    TypeAdapter(TraceStrategyStaleResponse).validate_python(stale.json())

    missing = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **request,
            "strategy_source": {**source, "strategy_id": "missing"},
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "trace.strategy.not_found"
    TypeAdapter(TraceStrategyNotFoundResponse).validate_python(missing.json())

    unknown_node = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "node_ids": ["nope"]}
    )
    assert unknown_node.status_code == 422
    assert unknown_node.json()["detail"]["code"] == "trace.request.invalid"
    TypeAdapter(Trace422Response).validate_python(unknown_node.json())

    invalid_spec = copy.deepcopy(spec)
    invalid_spec["title"] = ""
    invalid = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **inline,
            "strategy_source": {"kind": "inline_draft", "spec": invalid_spec},
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "portfolio.strategy.invalid"
    TypeAdapter(Trace422Response).validate_python(invalid.json())

    malformed = client.post("/api/v1/strategies/debug/trace", json={})
    assert malformed.status_code == 422
    TypeAdapter(Trace422Response).validate_python(malformed.json())


@pytest.mark.parametrize("literal", ["NaN", "1e309", "-1e309"])
def test_non_finite_inline_number_is_a_coded_preflight_error(literal: str) -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)
    encoded = json.dumps(request)
    needle = '"fee_bps": 15.0'
    assert needle in encoded

    response = client.post(
        "/api/v1/strategies/debug/trace",
        content=encoded.replace(needle, f'"fee_bps": {literal}'),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "portfolio.strategy.invalid"
    assert any(
        issue["path"] == "execution.fee_bps"
        for issue in response.json()["detail"]["validation"]["issues"]
    )
    TypeAdapter(Trace422Response).validate_python(response.json())


def test_trace_rejects_non_session_and_unknown_security_but_accepts_non_member() -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)

    weekend = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "as_of": "2026-08-30"}
    )
    assert weekend.status_code == 422
    assert weekend.json()["detail"]["code"] == "trace.request.invalid"
    assert "not trading sessions" in weekend.json()["detail"]["message"]

    mixed = client.post(
        "/api/v1/strategies/debug/trace",
        json={**request, "security_ids": [request["security_ids"][0], "NOPE"]},
    )
    assert mixed.status_code == 422
    assert mixed.json()["detail"]["code"] == "trace.request.invalid"
    assert "NOPE" in mixed.json()["detail"]["message"]

    # The third mock name is outside the universe on the first two sessions of a synthetic month,
    # but still has a PIT row. That is a valid trace scope, not an unknown identifier.
    non_member = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            **request,
            "as_of": "2026-09-01",
            "security_ids": ["sec-035420-1"],
        },
    )
    assert non_member.status_code == 200, non_member.text
    assert {row["security_id"] for row in non_member.json()["trace"]["rows"]} == {
        "sec-035420-1"
    }


def test_trace_openapi_contract_exposes_bounded_source_union() -> None:
    schema = TestClient(build_http_app()).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/strategies/debug/trace"]["post"]

    assert operation["operationId"] == "traceStrategy"
    assert {"200", "404", "409", "422", "499"} <= set(operation["responses"])
    request_schema = schema["components"]["schemas"]["StrategyTraceRequest"]
    properties = request_schema["properties"]
    assert {
        key: properties["limit"][key] for key in ("default", "minimum", "maximum")
    } == {"default": 200, "minimum": 1, "maximum": 500}
    assert properties["offset"]["minimum"] == 0
    assert {
        key: properties["security_ids"][key]
        for key in ("minItems", "maxItems", "uniqueItems")
    } == {
        "minItems": 1,
        "maxItems": 100,
        "uniqueItems": True,
    }
    assert properties["node_ids"]["maxItems"] == 100
    holdings_array = next(
        item for item in properties["starting_holdings"]["anyOf"] if item.get("type") == "array"
    )
    assert holdings_array["maxItems"] == 100
    source = properties["strategy_source"]
    assert len(source["anyOf"]) == 2
    assert source["discriminator"]["propertyName"] == "kind"

    responses = operation["responses"]
    assert responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "TraceStrategyNotFoundResponse"
    )
    assert responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "TraceStrategyStaleResponse"
    )
    assert responses["499"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "TraceCancelledResponse"
    )
    detail = schema["components"]["schemas"]["TraceUnprocessableResponse"]["properties"][
        "detail"
    ]
    assert detail["discriminator"]["propertyName"] == "code"
