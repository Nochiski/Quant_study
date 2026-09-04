"""P1-03 compile API: one diagnostic list (syntax/structural/semantic) with source ranges.

Wire contract: POST /api/v1/strategy-documents/compile. Invalid source never returns spec/hash.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
GOLDEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"


def _source(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _compile(client: TestClient, source: str, format: str = "yaml") -> dict:
    response = client.post(
        "/api/v1/strategy-documents/compile", json={"source": source, "format": format}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_yaml_and_json_sources_compile_to_the_same_backend_hash() -> None:
    client = TestClient(build_http_app())

    yaml_result = _compile(client, _source("quality_momentum.yaml"))
    json_result = _compile(client, _source("quality_momentum.json"), format="json")

    assert yaml_result["diagnostics"] == []
    assert yaml_result["spec_hash"] == GOLDEN_SPEC_HASH == json_result["spec_hash"]
    assert yaml_result["source_hash"] != json_result["source_hash"]
    assert yaml_result["schema_version"] == "1.0"
    assert yaml_result["spec"]["identity"] == {
        "strategy_id": "draft",
        "revision": 0,
        "schema_version": "1.0",
    }
    assert yaml_result["canonical_json"] == json_result["canonical_json"]
    assert '"max_name_weight":0.05' in yaml_result["canonical_json"]


def test_semantic_error_points_at_the_exact_yaml_scalar_and_withholds_spec() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.yaml").replace(
        "max_name_weight: 0.05", "max_name_weight: 1.5"
    )

    result = _compile(client, source)

    assert result["spec"] is None and result["spec_hash"] is None
    assert result["canonical_json"] is None
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "strategy.risk.max_name_weight"
    assert diagnostic["kind"] == "semantic"
    assert diagnostic["severity"] == "error"
    assert diagnostic["pointer"] == "/risk/max_name_weight"
    start, end = diagnostic["range"]["start"], diagnostic["range"]["end"]
    line = source.splitlines()[start["line"]]
    assert line[start["column"] : end["column"]] == "1.5"


def test_missing_field_points_at_the_nearest_parent_range() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.yaml").replace('  end: "2026-08-31"\n', "")

    result = _compile(client, source)

    assert result["spec"] is None
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "structure.missing_field"
    assert diagnostic["kind"] == "structural"
    assert diagnostic["pointer"] == "/data/end"
    assert diagnostic["range"]["start"]["line"] == source.splitlines().index("data:") + 1


def test_unknown_key_points_at_the_key_itself() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.unknown_key.yaml")

    result = _compile(client, source)

    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "structure.unknown_key"
    assert diagnostic["pointer"] == "/risk/max_name_wieght"
    start, end = diagnostic["range"]["start"], diagnostic["range"]["end"]
    assert source[start["offset"] : end["offset"]] == "max_name_wieght"


def test_syntax_error_returns_a_positioned_syntax_diagnostic() -> None:
    client = TestClient(build_http_app())

    result = _compile(client, 'title: "unterminated\ndata:\n')

    assert result["spec"] is None and result["schema_version"] is None
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "yaml.syntax"
    assert diagnostic["kind"] == "syntax"
    assert diagnostic["range"]["start"] == {"line": 0, "column": 7, "offset": 7}


def test_existing_json_validation_api_is_unchanged() -> None:
    client = TestClient(build_http_app())
    template = client.get("/api/v1/strategies/template").json()

    assert client.post("/api/v1/strategies/validate", json=template).json() == {
        "valid": True,
        "issues": [],
    }
