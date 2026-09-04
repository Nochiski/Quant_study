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


def test_deep_nesting_is_a_diagnostic_not_a_server_error() -> None:
    client = TestClient(build_http_app())
    source = "a: " + "[" * 2000 + "]" * 2000 + "\n"

    result = _compile(client, source)

    assert result["spec"] is None
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "document.too_deep"
    assert diagnostic["kind"] == "syntax"


def test_every_semantic_issue_is_reported_together_with_its_own_pointer() -> None:
    client = TestClient(build_http_app())
    source = (
        _source("quality_momentum.yaml")
        .replace("max_name_weight: 0.05", "max_name_weight: 1.5")
        .replace("selection_count: 20", "selection_count: 0")
        .replace(
            "parameters: []",
            "parameters:\n"
            "  - parameter_id: lookback\n"
            "    default: 5.0\n"
            "    minimum: 10.0\n"
            "    maximum: 1.0\n"
            "    kind: float\n",
        )
    )

    result = _compile(client, source)

    assert result["spec"] is None
    by_code = {d["code"]: d for d in result["diagnostics"]}
    assert set(by_code) >= {
        "strategy.risk.max_name_weight",
        "strategy.portfolio.selection_count",
        "strategy.parameter.bounds",
        "strategy.parameter.default",
    }
    assert by_code["strategy.parameter.bounds"]["pointer"] == "/parameters/0"
    bounds = by_code["strategy.parameter.bounds"]["range"]
    assert source.splitlines()[bounds["start"]["line"]].lstrip().startswith("- parameter_id")
    assert all(d["severity"] == "error" for d in result["diagnostics"])
    assert all(d["node_id"] is None for d in result["diagnostics"])


def test_factor_graph_issue_names_the_node_and_points_into_the_graph() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.yaml").replace("input_node_id: close", "input_node_id: nope")

    result = _compile(client, source)

    assert result["spec"] is None
    graph_issues = [
        d for d in result["diagnostics"] if d["pointer"].startswith("/factors/factors/0/graph")
    ]
    assert graph_issues, result["diagnostics"]
    issue = next(d for d in graph_issues if d["code"] == "strategy.expression.input_missing")
    assert issue["node_id"] == "mom_252"
    assert issue["kind"] == "semantic"
    assert issue["range"] is not None


def test_semantic_range_for_a_parent_path_falls_back_to_the_parent_node() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.yaml")
    # Duplicate the factor block: the strategy-level duplicate check fires on `factors`.
    lines = source.splitlines(keepends=True)
    start = lines.index("factors:\n") + 2
    end = lines.index("signal:\n")
    source = "".join(lines[:end] + lines[start:end] + lines[end:])

    result = _compile(client, source)

    issue = next(d for d in result["diagnostics"] if d["code"] == "strategy.factor.duplicate")
    assert issue["pointer"] == "/factors"
    assert issue["range"]["start"]["line"] == source.splitlines().index("factors:") + 1


def test_json_format_diagnostics_carry_json_ranges() -> None:
    client = TestClient(build_http_app())
    source = _source("quality_momentum.json").replace(
        '"max_name_weight": 0.05', '"max_name_weight": 1.5'
    )
    assert source != _source("quality_momentum.json")

    result = _compile(client, source, format="json")

    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "strategy.risk.max_name_weight"
    start, end = diagnostic["range"]["start"], diagnostic["range"]["end"]
    assert source[start["offset"] : end["offset"]] == "1.5"


def test_malformed_envelope_is_the_only_422() -> None:
    client = TestClient(build_http_app())

    assert (
        client.post("/api/v1/strategy-documents/compile", json={"source": "a: 1"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/strategy-documents/compile", json={"source": "a: 1", "format": "toml"}
        ).status_code
        == 422
    )
