from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api.facade.api import (
    StrategyDraftConflictResponse,
)
from strategy_workbench.bootstrap.facade.http import build_http_app


def _body(source: str, expected_version: int = 0) -> dict[str, object]:
    return {
        "expected_version": expected_version,
        "source": source,
        "format": "yaml",
        "schema_version": "1.0",
        "strategy_id": None,
        "base_revision": None,
        "base_spec_hash": None,
    }


def test_invalid_source_draft_round_trips_and_conflicts_never_overwrite() -> None:
    client = TestClient(build_http_app())
    source = "factors: [invalid\r\n"
    created = client.put("/api/v1/strategy-drafts/new-one", json=_body(source))

    assert created.status_code == 200, created.text
    first = created.json()
    assert first["version"] == 1
    assert first["source"] == source
    assert first["source_hash"] == hashlib.sha256(source.encode()).hexdigest()
    assert client.get("/api/v1/strategy-drafts/new-one").json() == first

    second_source = "title: device two"
    second = client.put(
        "/api/v1/strategy-drafts/new-one",
        json=_body(second_source, expected_version=1),
    ).json()
    stale = client.put(
        "/api/v1/strategy-drafts/new-one",
        json=_body("title: stale device", expected_version=1),
    )

    assert second["version"] == 2
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "strategy.draft.conflict"
    assert detail["current"] == second
    TypeAdapter(StrategyDraftConflictResponse).validate_python(stale.json())
    assert client.get("/api/v1/strategy-drafts/new-one").json()["source"] == second_source

    stale_delete = client.delete("/api/v1/strategy-drafts/new-one", params={"expected_version": 1})
    assert stale_delete.status_code == 409
    assert stale_delete.json()["detail"]["current"] == second
    assert (
        client.delete("/api/v1/strategy-drafts/new-one", params={"expected_version": 2}).status_code
        == 204
    )
    assert client.get("/api/v1/strategy-drafts/new-one").status_code == 404


def test_saved_draft_requires_the_exact_immutable_base() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    saved = client.post("/api/v1/strategies", json=template).json()
    identity = saved["spec"]["identity"]
    body = _body("still: invalid")
    body.update(
        {
            "strategy_id": identity["strategy_id"],
            "base_revision": identity["revision"],
            "base_spec_hash": saved["spec_hash"],
        }
    )

    accepted = client.put("/api/v1/strategy-drafts/saved-base", json=body)
    assert accepted.status_code == 200, accepted.text

    body["base_spec_hash"] = "0" * 64
    rejected = client.put("/api/v1/strategy-drafts/wrong-base", json=body)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "strategy.draft.invalid"


def test_non_utf8_scalar_source_is_a_typed_rejection_and_is_not_persisted() -> None:
    client = TestClient(build_http_app(), raise_server_exceptions=False)
    payload = (
        b'{"expected_version":0,"source":"\\ud800","format":"yaml",'
        b'"schema_version":"1.0","strategy_id":null,"base_revision":null,'
        b'"base_spec_hash":null}'
    )

    rejected = client.put(
        "/api/v1/strategy-drafts/non-utf8",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "strategy.draft.invalid"
    assert client.get("/api/v1/strategy-drafts/non-utf8").status_code == 404


def test_non_utf8_persisted_metadata_is_a_typed_rejection_and_is_not_persisted() -> None:
    client = TestClient(build_http_app(), raise_server_exceptions=False)
    cases = (
        {**_body("title: valid"), "schema_version": "\ud800"},
        {
            **_body("title: valid"),
            "strategy_id": "\ud800",
            "base_revision": 1,
            "base_spec_hash": "0" * 64,
        },
        {
            **_body("title: valid"),
            "strategy_id": "strategy",
            "base_revision": 1,
            "base_spec_hash": "\ud800" * 64,
        },
    )

    for index, body in enumerate(cases):
        draft_id = f"non-utf8-metadata-{index}"
        rejected = client.put(
            f"/api/v1/strategy-drafts/{draft_id}",
            content=json.dumps(body).encode("ascii"),
            headers={"content-type": "application/json"},
        )

        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "strategy.draft.invalid"
        assert client.get(f"/api/v1/strategy-drafts/{draft_id}").status_code == 404


def test_server_draft_survives_runtime_container_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    first = TestClient(build_http_app(strategy_repository_path=path))
    response = first.put(
        "/api/v1/strategy-drafts/restart",
        json=_body("title: persisted invalid: yaml"),
    )
    assert response.status_code == 200

    second = TestClient(build_http_app(strategy_repository_path=path))
    assert second.get("/api/v1/strategy-drafts/restart").json() == response.json()


def test_draft_openapi_declares_typed_conflicts() -> None:
    schema = TestClient(build_http_app()).get("/openapi.json").json()
    path = schema["paths"]["/api/v1/strategy-drafts/{draft_id}"]

    assert path["put"]["operationId"] == "saveStrategyDraft"
    assert path["delete"]["operationId"] == "deleteStrategyDraft"
    assert path["put"]["responses"]["409"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("StrategyDraftConflictResponse")
    for method in ("get", "delete"):
        assert path[method]["responses"]["404"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("StrategyDraftErrorResponse")
    for method in ("get", "put", "delete"):
        response_422 = path[method]["responses"]["422"]["content"]["application/json"]["schema"]
        assert {reference["$ref"].rsplit("/", 1)[-1] for reference in response_422["anyOf"]} == {
            "RequestValidationResponse",
            "StrategyDraftInvalidResponse",
        }
