"""P1-07 document save/get/history API.

Only a cleanly compiled exact source is stored; identity is assigned outside the source; history
is deterministic and paginated; legacy JSON revisions come back as a generated document with
provenance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
GOLDEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"


def _source(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_save_get_revise_history_round_trip_preserves_source_and_hashes() -> None:
    client = TestClient(build_http_app())
    yaml_text = _source("quality_momentum.yaml")

    created = client.post(
        "/api/v1/strategy-documents", json={"source": yaml_text, "format": "yaml"}
    )
    assert created.status_code == 201, created.text
    document = created.json()
    strategy_id = document["strategy_id"]
    assert document["revision"] == 1 and document["generated"] is False
    assert document["spec_hash"] == GOLDEN_SPEC_HASH
    assert document["source"] == yaml_text and document["source_hash"] == _sha(yaml_text)
    assert document["spec"]["identity"] == {
        "strategy_id": strategy_id,
        "revision": 1,
        "schema_version": "1.0",
    }
    assert document["origin"] == "document" and document["schema_version"] == "1.0"

    fetched = client.get(f"/api/v1/strategies/{strategy_id}/revisions/1/document").json()
    assert fetched == document

    recompiled = client.post(
        "/api/v1/strategy-documents/compile", json={"source": fetched["source"], "format": "yaml"}
    ).json()
    assert recompiled["spec_hash"] == document["spec_hash"]
    assert recompiled["source_hash"] == document["source_hash"]

    json_text = _source("quality_momentum.json")
    revised = client.post(
        f"/api/v1/strategy-documents/{strategy_id}/revisions",
        json={"source": json_text, "format": "json", "expected_revision": 1},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2 and revised.json()["format"] == "json"
    assert revised.json()["spec_hash"] == GOLDEN_SPEC_HASH  # same meaning, same hash

    first_again = client.get(f"/api/v1/strategies/{strategy_id}/revisions/1/document").json()
    assert first_again["source"] == yaml_text  # revision 1 is immutable

    history = client.get(f"/api/v1/strategies/{strategy_id}/revisions").json()
    assert [item["revision"] for item in history["items"]] == [1, 2]
    assert history["total"] == 2 and history["offset"] == 0
    assert history["items"][0]["source_format"] == "yaml"
    assert history["items"][1]["source_hash"] == _sha(json_text)
    page = client.get(
        f"/api/v1/strategies/{strategy_id}/revisions", params={"offset": 1, "limit": 1}
    ).json()
    assert [item["revision"] for item in page["items"]] == [2] and page["total"] == 2


def test_stale_expected_revision_is_409_and_invalid_source_is_422_with_diagnostics() -> None:
    client = TestClient(build_http_app())
    yaml_text = _source("quality_momentum.yaml")
    strategy_id = client.post(
        "/api/v1/strategy-documents", json={"source": yaml_text, "format": "yaml"}
    ).json()["strategy_id"]

    stale = client.post(
        f"/api/v1/strategy-documents/{strategy_id}/revisions",
        json={"source": yaml_text, "format": "yaml", "expected_revision": 0},
    )
    assert (
        stale.status_code == 409 and stale.json()["detail"]["code"] == "strategy.revision_conflict"
    )
    assert stale.json()["detail"]["latest_revision"] == 1

    invalid = client.post(
        "/api/v1/strategy-documents",
        json={"source": _source("quality_momentum.unknown_key.yaml"), "format": "yaml"},
    )
    assert invalid.status_code == 422, invalid.text
    detail = invalid.json()["detail"]
    assert detail["code"] == "strategy_document.invalid"
    (diagnostic,) = detail["diagnostics"]
    assert diagnostic["code"] == "structure.unknown_key"
    assert diagnostic["pointer"] == "/risk/max_name_wieght"
    assert detail["source_hash"] == _sha(_source("quality_momentum.unknown_key.yaml"))

    missing = client.get("/api/v1/strategies/nope/revisions/1/document")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "strategy.not_found"
    assert client.get("/api/v1/strategies/nope/revisions").status_code == 404


def test_legacy_json_revision_returns_a_generated_document_with_provenance() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()
    saved = client.post("/api/v1/strategies", json=template).json()
    strategy_id = saved["spec"]["identity"]["strategy_id"]

    document = client.get(f"/api/v1/strategies/{strategy_id}/revisions/1/document").json()

    assert document["generated"] is True and document["origin"] == "legacy_json"
    assert document["format"] == "json"
    assert document["source_hash"] == _sha(document["source"])
    assert document["spec_hash"] == saved["spec_hash"]
    recompiled = client.post(
        "/api/v1/strategy-documents/compile",
        json={"source": document["source"], "format": "json"},
    ).json()
    assert recompiled["diagnostics"] == []
    assert recompiled["spec_hash"] == saved["spec_hash"]
    history = client.get(f"/api/v1/strategies/{strategy_id}/revisions").json()
    assert history["items"][0]["origin"] == "legacy_json"
    assert history["items"][0]["source_hash"] is None


def test_legacy_revise_of_a_document_strategy_is_refused() -> None:
    client = TestClient(build_http_app())
    created = client.post(
        "/api/v1/strategy-documents",
        json={"source": _source("quality_momentum.yaml"), "format": "yaml"},
    ).json()
    strategy_id = created["strategy_id"]

    response = client.post(
        f"/api/v1/strategies/{strategy_id}/revisions",
        json={"expected_revision": 1, "spec": created["spec"]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "strategy.revision_conflict"
    assert response.json()["detail"]["latest_revision"] == 1
    history = client.get(f"/api/v1/strategies/{strategy_id}/revisions").json()
    assert [item["origin"] for item in history["items"]] == ["document"]


def test_source_carrying_identity_is_rejected_and_missing_strategy_is_404() -> None:
    client = TestClient(build_http_app())
    with_identity = (
        _source("quality_momentum.yaml") + "identity:\n  strategy_id: x\n  revision: 9\n"
    )

    invalid = client.post(
        "/api/v1/strategy-documents", json={"source": with_identity, "format": "yaml"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["diagnostics"][0]["pointer"] == "/identity"

    missing = client.post(
        "/api/v1/strategy-documents/nope/revisions",
        json={"source": _source("quality_momentum.yaml"), "format": "yaml", "expected_revision": 1},
    )
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "strategy.not_found"


def test_exact_bytes_round_trip_including_crlf_and_no_trailing_newline() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.yaml").replace("\n", "\r\n").rstrip("\r\n")

    created = client.post("/api/v1/strategy-documents", json={"source": source, "format": "yaml"})
    assert created.status_code == 201, created.text
    strategy_id = created.json()["strategy_id"]
    fetched = client.get(f"/api/v1/strategies/{strategy_id}/revisions/1/document").json()

    assert fetched["source"] == source
    assert fetched["source_hash"] == _sha(source)
    assert fetched["spec_hash"] == GOLDEN_SPEC_HASH
    assert (
        client.get(f"/api/v1/strategies/{strategy_id}/revisions", params={"limit": 501}).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/v1/strategies/{strategy_id}/revisions",
            params={"offset": 9_007_199_254_740_992},
        ).status_code
        == 422
    )
