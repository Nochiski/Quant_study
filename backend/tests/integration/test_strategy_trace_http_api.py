"""P5-01 scoped trace HTTP contract over the same FactorGraph/TargetTape calculation path."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._backtest_contract import Backtest422Response
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
    assert {row["kind"] for row in payload["raw"]} == {"observed"}

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
    construction = {item["security_id"]: item for item in payload["target"]["construction"]}
    assert set(construction) == set(request["security_ids"])
    for row in payload["trace"]["rows"]:
        assert row["value"] == by_security[row["security_id"]]["composite_score"]
    for security_id, row in construction.items():
        candidate = by_security[security_id]
        assert row["composite_score"] == candidate["composite_score"]
        assert row["constrained_target_weight"] == candidate["target_weight"]
        assert sum(
            item["normalized_contribution"] or 0.0 for item in row["factor_contributions"]
        ) == pytest.approx(row["composite_score"])
        assert row["previous_weight"] is None
        assert row["estimated_order_delta"] is None


@pytest.mark.parametrize("end", ["2026-09-04", "2026-09-05"])
def test_omitted_as_of_resolves_the_latest_executable_frame_for_inline_and_saved(
    end: str,
) -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"]["end"] = end
    preview = client.post("/api/v1/portfolio/preview", json={"spec": spec})
    assert preview.status_code == 200, preview.text
    expected = preview.json()["tape"]["frames"][-1]
    factor = spec["factors"]["factors"][0]
    security_ids = [item["security_id"] for item in expected["candidates"][:2]]
    saved = _save(client, spec)
    sources = (
        {"kind": "inline_draft", "spec": spec},
        {
            "kind": "saved_revision",
            "strategy_id": saved["strategy_id"],
            "revision": saved["revision"],
            "expected_spec_hash": saved["spec_hash"],
        },
    )

    for source in sources:
        response = client.post(
            "/api/v1/strategies/debug/trace",
            json={
                "strategy_source": source,
                "security_ids": security_ids,
                "factor_id": factor["factor_id"],
                "node_ids": [factor["graph"]["output_node_id"]],
                "include_raw": True,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["as_of"] == expected["signal_as_of"]
        assert payload["target"]["signal_as_of"] == expected["signal_as_of"]
        assert payload["target"]["execution_on"] == expected["execution_on"]
        assert payload["target"]["execution_on"] <= end
        assert payload["raw"] and payload["trace"]["rows"]


def test_explicit_non_rebalance_date_keeps_raw_and_node_partial_trace() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"]["end"] = "2026-09-04"
    _, security_ids, factor_id = _scope(client, spec)
    factor = spec["factors"]["factors"][0]

    response = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            "strategy_source": {"kind": "inline_draft", "spec": spec},
            "as_of": spec["data"]["end"],
            "security_ids": security_ids,
            "factor_id": factor_id,
            "node_ids": [factor["graph"]["output_node_id"]],
            "include_raw": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["as_of"] == spec["data"]["end"]
    assert payload["target"] is None
    assert payload["raw"]
    assert payload["trace"]["rows"]


def test_trace_page_is_stable_and_bounded() -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)
    request["node_ids"] = []
    request["include_raw"] = False

    whole = client.post("/api/v1/strategies/debug/trace", json={**request, "offset": 0, "limit": 2})
    first = client.post("/api/v1/strategies/debug/trace", json={**request, "offset": 0, "limit": 1})
    second = client.post(
        "/api/v1/strategies/debug/trace", json={**request, "offset": 1, "limit": 1}
    )

    assert whole.status_code == first.status_code == second.status_code == 200
    assert first.json()["trace"]["has_more"] is True
    assert (
        first.json()["trace"]["rows"] + second.json()["trace"]["rows"]
        == whole.json()["trace"]["rows"]
    )
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
                {"security_id": security_id, "weight": 0.1} for security_id in security_ids
            ],
        },
    )

    assert empty_book.status_code == seeded_book.status_code == 200
    assert empty_book.json()["target"]["targets"] == []
    assert {item["security_id"] for item in seeded_book.json()["target"]["targets"]} == set(
        security_ids
    )
    empty_construction = empty_book.json()["target"]["construction"]
    seeded_construction = seeded_book.json()["target"]["construction"]
    assert all(item["previous_weight"] == 0.0 for item in empty_construction)
    assert all(
        item["estimated_order_delta"]
        == pytest.approx(item["constrained_target_weight"] - item["previous_weight"])
        for item in empty_construction
    )
    assert all(item["previous_weight"] == pytest.approx(0.1) for item in seeded_construction)
    assert all(
        item["estimated_order_delta"]
        == pytest.approx(item["constrained_target_weight"] - item["previous_weight"])
        for item in seeded_construction
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


def test_trace_preserves_raw_zero_missing_collection_and_coverage_semantics() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"].update({"start": "2024-01-03", "end": "2024-01-09"})
    spec["portfolio"].update({"rebalance": "every_n_sessions", "rebalance_every_n_sessions": 1})
    factor = spec["factors"]["factors"][0]
    factor["graph"] = {
        "nodes": [
            {
                "node_id": "foreign-flow",
                "field_id": "flow.foreign_net_buy",
                "kind": "field",
            }
        ],
        "output_node_id": "foreign-flow",
        "missing_policy": "drop",
    }
    base = {
        "strategy_source": {"kind": "inline_draft", "spec": spec},
        "factor_id": factor["factor_id"],
        "node_ids": ["foreign-flow"],
        "include_raw": True,
    }
    scopes = (
        ("2024-01-03", ["sec-005930-1", "sec-000660-1"]),
        ("2024-01-04", ["sec-005930-1", "sec-000660-1"]),
        ("2024-01-08", ["sec-035420-1"]),
    )
    raw_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for as_of, security_ids in scopes:
        response = client.post(
            "/api/v1/strategies/debug/trace",
            json={**base, "as_of": as_of, "security_ids": security_ids},
        )
        assert response.status_code == 200, response.text
        for row in response.json()["raw"]:
            raw_by_key[(row["as_of"], row["security_id"])] = row

    assert (
        raw_by_key[("2024-01-03", "sec-005930-1")]["value"],
        raw_by_key[("2024-01-03", "sec-005930-1")]["kind"],
    ) == (0.0, "observed")
    assert (
        raw_by_key[("2024-01-03", "sec-000660-1")]["value"],
        raw_by_key[("2024-01-03", "sec-000660-1")]["kind"],
    ) == (None, "missing")
    assert (
        raw_by_key[("2024-01-04", "sec-005930-1")]["value"],
        raw_by_key[("2024-01-04", "sec-005930-1")]["kind"],
    ) == (0.0, "source_omitted_zero")
    assert (
        raw_by_key[("2024-01-04", "sec-000660-1")]["value"],
        raw_by_key[("2024-01-04", "sec-000660-1")]["kind"],
    ) == (None, "not_collected")
    assert (
        raw_by_key[("2024-01-08", "sec-035420-1")]["value"],
        raw_by_key[("2024-01-08", "sec-035420-1")]["kind"],
    ) == (None, "coverage_gap")


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


@pytest.mark.parametrize("literal", ["NaN", "1e309", "-1e309"])
@pytest.mark.parametrize(
    ("location", "expected_path"),
    (
        ("factor_weight", "factors.factors.0.weight"),
        ("constant", ""),
        ("eligibility", "eligibility.rules.0.value"),
        ("signal", "signal.score_threshold"),
        ("float_parameter", "parameters.0.default"),
        ("choice", "parameters.0.choices.1"),
    ),
)
def test_every_unbounded_non_finite_number_is_a_coded_422(
    literal: str, location: str, expected_path: str
) -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)
    spec = request["strategy_source"]["spec"]
    marker = "__NON_FINITE_NUMBER__"
    factor = spec["factors"]["factors"][0]
    if location == "factor_weight":
        factor["weight"] = marker
    elif location == "constant":
        factor["graph"]["nodes"].append(
            {"node_id": "non-finite", "value": marker, "kind": "constant"}
        )
        expected_path = f"factors.factors.0.graph.nodes.{len(factor['graph']['nodes']) - 1}.value"
    elif location == "eligibility":
        spec["eligibility"]["rules"] = [
            {"field_id": "price.close", "operator": "gt", "value": marker}
        ]
    elif location == "signal":
        spec["signal"]["score_threshold"] = marker
    elif location == "float_parameter":
        spec["parameters"] = [
            {
                "parameter_id": "scale",
                "default": marker,
                "minimum": 0.0,
                "maximum": 1.0,
                "kind": "float",
                "step": None,
            }
        ]
    else:
        spec["parameters"] = [
            {
                "parameter_id": "scale",
                "default": 1.0,
                "choices": [1.0, marker],
                "kind": "choice",
            }
        ]
    encoded = json.dumps(request).replace(f'"{marker}"', literal)

    response = client.post(
        "/api/v1/strategies/debug/trace",
        content=encoded,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "portfolio.strategy.invalid"
    issues = detail["validation"]["issues"]
    assert any(
        issue["code"] == "strategy.number.non_finite" and issue["path"] == expected_path
        for issue in issues
    )
    TypeAdapter(Trace422Response).validate_python(response.json())


def test_factor_weight_overflow_is_invalid_before_preview_or_backtest_hashing() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    marker = "__NON_FINITE_NUMBER__"
    spec["factors"]["factors"][0]["weight"] = marker

    def raw_json(payload: object) -> str:
        return json.dumps(payload).replace(f'"{marker}"', "1e309")

    validation = client.post(
        "/api/v1/strategies/validate",
        content=raw_json(spec),
        headers={"content-type": "application/json"},
    )
    preview = client.post(
        "/api/v1/portfolio/preview",
        content=raw_json({"spec": spec}),
        headers={"content-type": "application/json"},
    )
    backtest = client.post(
        "/api/v1/backtests",
        content=raw_json({"strategy": spec, "core": "python"}),
        headers={"content-type": "application/json"},
    )

    assert validation.status_code == 200
    assert validation.json()["valid"] is False
    assert any(
        issue["code"] == "strategy.number.non_finite"
        and issue["path"] == "factors.factors.0.weight"
        for issue in validation.json()["issues"]
    )
    for response in (preview, backtest):
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "portfolio.strategy.invalid"


def test_trace_rejects_non_session_and_unknown_security_but_accepts_non_member() -> None:
    client = TestClient(build_http_app())
    _, request = _inline_request(client)

    weekend = client.post("/api/v1/strategies/debug/trace", json={**request, "as_of": "2026-08-30"})
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
    assert {row["security_id"] for row in non_member.json()["trace"]["rows"]} == {"sec-035420-1"}


def test_finite_factor_overflow_is_the_same_coded_failure_for_all_execution_routes() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    factor = spec["factors"]["factors"][0]
    factor["graph"] = {
        "nodes": [
            {"node_id": "close", "field_id": "price.close", "kind": "field"},
            {"node_id": "scale", "value": 1e308, "kind": "constant"},
            {
                "node_id": "overflow",
                "operator": "multiply",
                "left_node_id": "close",
                "right_node_id": "scale",
                "kind": "binary",
            },
        ],
        "output_node_id": "overflow",
        "missing_policy": "drop",
    }
    spec["portfolio"]["weighting"] = "factor_score"
    spec["data"]["end"] = "2026-09-04"
    trace_request = {
        "strategy_source": {"kind": "inline_draft", "spec": spec},
        "as_of": spec["data"]["end"],
        "security_ids": ["sec-005930-1"],
        "factor_id": factor["factor_id"],
        "node_ids": ["overflow"],
    }

    responses = (
        client.post("/api/v1/portfolio/preview", json={"spec": spec}),
        client.post("/api/v1/strategies/debug/trace", json=trace_request),
        client.post("/api/v1/backtests", json={"strategy": spec, "core": "python"}),
    )

    for response in responses:
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "portfolio.strategy.invalid"
        assert {
            (issue["code"], issue["path"], issue["node_id"])
            for issue in detail["validation"]["issues"]
        } == {
            (
                "strategy.expression.calculation_non_finite",
                "factors.factors.0.graph.nodes.2",
                "overflow",
            )
        }
    TypeAdapter(Backtest422Response).validate_python(responses[-1].json())


def test_starting_holdings_without_a_target_frame_return_a_typed_preflight_error() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"].update({"start": "2026-09-04", "end": "2026-09-04"})
    spec["portfolio"].update({"rebalance": "every_n_sessions", "rebalance_every_n_sessions": 1})
    factor = spec["factors"]["factors"][0]

    response = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            "strategy_source": {"kind": "inline_draft", "spec": spec},
            "as_of": spec["data"]["end"],
            "security_ids": ["sec-005930-1"],
            "factor_id": factor["factor_id"],
            "starting_holdings": [{"security_id": "sec-005930-1", "weight": 0.5}],
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "trace.request.invalid"
    assert "require a TargetTape signal frame" in response.json()["detail"]["message"]
    TypeAdapter(Trace422Response).validate_python(response.json())


def test_omitted_as_of_without_an_executable_frame_returns_a_typed_error() -> None:
    client = TestClient(build_http_app())
    spec = client.get("/api/v1/strategies/template").json()
    spec["data"].update({"start": "2026-09-04", "end": "2026-09-04"})
    spec["portfolio"].update({"rebalance": "every_n_sessions", "rebalance_every_n_sessions": 1})
    factor = spec["factors"]["factors"][0]

    response = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            "strategy_source": {"kind": "inline_draft", "spec": spec},
            "security_ids": ["sec-005930-1"],
            "factor_id": factor["factor_id"],
            "node_ids": [factor["graph"]["output_node_id"]],
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "code": "trace.request.invalid",
        "message": "trace default date requires an executable TargetTape signal frame",
    }
    TypeAdapter(Trace422Response).validate_python(response.json())


def test_trace_openapi_contract_exposes_bounded_source_union() -> None:
    schema = TestClient(build_http_app()).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/strategies/debug/trace"]["post"]

    assert operation["operationId"] == "traceStrategy"
    assert {"200", "404", "409", "422", "499"} <= set(operation["responses"])
    request_schema = schema["components"]["schemas"]["StrategyTraceRequest"]
    properties = request_schema["properties"]
    assert "as_of" not in request_schema["required"]
    assert {item.get("type") for item in properties["as_of"]["anyOf"]} == {
        "string",
        "null",
    }
    assert {key: properties["limit"][key] for key in ("default", "minimum", "maximum")} == {
        "default": 200,
        "minimum": 1,
        "maximum": 500,
    }
    assert properties["offset"]["minimum"] == 0
    assert {
        key: properties["security_ids"][key] for key in ("minItems", "maxItems", "uniqueItems")
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
    detail = schema["components"]["schemas"]["TraceUnprocessableResponse"]["properties"]["detail"]
    assert detail["discriminator"]["propertyName"] == "code"
    assert "trace.capability.unsupported" in detail["discriminator"]["mapping"]
    TypeAdapter(Trace422Response).validate_python(
        {
            "detail": {
                "code": "trace.capability.unsupported",
                "capability": "raw_observation.cancellation",
                "message": "adapter does not support cancellable trace",
            }
        }
    )
