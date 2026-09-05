"""P1-05 runtime schema: derived from the model, navigable for every authoring path.

The checker below is a deliberately small JSON-Schema-subset walker used only to prove the
template and the golden fixture satisfy the generated schema; the product never evaluates JSON
Schema itself (editor ADR D2).
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from datetime import date
from pathlib import Path
from typing import Any

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.factor.facade.expression import EXPRESSION_NODE_KINDS
from strategy_workbench.domain.strategy.facade.constraints import STRATEGY_SCALAR_CONSTRAINTS
from strategy_workbench.domain.strategy.facade.schema import (
    FieldContract,
    strategy_document_schema,
    strategy_document_schema_hash,
    strategy_field_contracts,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategySpec,
    canonical_strategy_payload,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def _template_document() -> dict[str, Any]:
    spec = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()
    return json.loads(json.dumps(canonical_strategy_payload(spec), default=str))


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        node = schema["$defs"][name]
    return node


def _check(schema: dict[str, Any], node: dict[str, Any], value: Any, pointer: str) -> None:
    node = _resolve(schema, node)
    if "anyOf" in node:
        errors = []
        for option in node["anyOf"]:
            try:
                _check(schema, option, value, pointer)
                return
            except AssertionError as error:
                errors.append(str(error))
        raise AssertionError(f"{pointer}: no anyOf branch matched — {errors}")
    if "oneOf" in node:
        kind = value.get("kind") if isinstance(value, dict) else None
        options = [
            option
            for option in node["oneOf"]
            if _resolve(schema, option)["properties"]["kind"].get("const") == kind
        ]
        assert len(options) == 1, f"{pointer}: kind={kind!r} must select exactly one branch"
        _check(schema, options[0], value, pointer)
        return
    if "const" in node:
        assert value == node["const"], f"{pointer}: expected const {node['const']!r}"
        return
    types = node["type"] if isinstance(node["type"], list) else [node["type"]]
    actual = _json_type(value)
    if actual == "integer" and "number" in types:
        actual = "number"  # JSON Schema: every integer is a number
    assert actual in types, f"{pointer}: {value!r} is not {types}"
    if "enum" in node:
        assert value in node["enum"], f"{pointer}: {value!r} not in enum"
    for key, bound in (("minimum", lambda v, b: v >= b), ("maximum", lambda v, b: v <= b)):
        if key in node:
            assert bound(value, node[key]), f"{pointer}: {key} {node[key]} violated by {value}"
    if node["type"] == "object":
        assert node["additionalProperties"] is False, f"{pointer}: unknown keys must fail closed"
        unknown = set(value) - set(node["properties"])
        assert not unknown, f"{pointer}: unknown keys {sorted(unknown)}"
        missing = set(node["required"]) - set(value)
        assert not missing, f"{pointer}: missing {sorted(missing)}"
        for key, item in value.items():
            _check(schema, node["properties"][key], item, f"{pointer}/{key}")
    elif node["type"] == "array":
        for index, item in enumerate(value):
            _check(schema, node["items"], item, f"{pointer}/{index}")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _pointers(value: Any, prefix: str = "") -> set[str]:
    found = {prefix}
    if isinstance(value, dict):
        for key, item in value.items():
            found |= _pointers(item, f"{prefix}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found |= _pointers(item, f"{prefix}/{index}")
    return found


def _navigate(schema: dict[str, Any], pointer: str, document: Any) -> dict[str, Any]:
    """Follow a concrete pointer through $ref / oneOf (by `kind`) / items, as an editor would."""
    node = _resolve(schema, schema)
    value = document
    for token in [t for t in pointer.split("/")[1:]]:
        node = _resolve(schema, node)
        if "oneOf" in node:
            kind = value["kind"]
            node = next(
                _resolve(schema, o)
                for o in node["oneOf"]
                if _resolve(schema, o)["properties"]["kind"]["const"] == kind
            )
        if node.get("type") == "array":
            node, value = node["items"], value[int(token)]
        else:
            node, value = node["properties"][token], value[token]
    return _resolve(schema, node)


def test_schema_is_navigable_for_every_pointer_in_the_golden_fixture() -> None:
    schema = strategy_document_schema()
    document = json.loads((FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"))
    for pointer in sorted(_pointers(document)):
        assert _navigate(schema, pointer, document) is not None, pointer


def test_template_and_golden_fixture_pass_the_runtime_schema() -> None:
    schema = strategy_document_schema()
    _check(schema, schema, _template_document(), "")
    fixture = json.loads((FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"))
    _check(schema, schema, fixture, "")


def test_every_factor_node_kind_is_a_discriminated_branch() -> None:
    schema = strategy_document_schema()
    nodes = schema["$defs"]["FactorGraph"]["properties"]["nodes"]["items"]
    assert nodes["discriminator"] == {"propertyName": "kind"}
    kinds = {_resolve(schema, option)["properties"]["kind"]["const"] for option in nodes["oneOf"]}
    assert kinds == set(EXPRESSION_NODE_KINDS)
    for option in nodes["oneOf"]:
        branch = _resolve(schema, option)
        assert branch["additionalProperties"] is False
        assert "kind" in branch["required"]


def test_parameter_definitions_are_a_discriminated_union_with_defaults() -> None:
    schema = strategy_document_schema()
    parameters = schema["properties"]["parameters"]
    assert parameters["type"] == "array" and parameters["default"] == []
    assert parameters["items"]["discriminator"] == {"propertyName": "kind"}
    kinds = {
        _resolve(schema, o)["properties"]["kind"]["const"] for o in parameters["items"]["oneOf"]
    }
    assert kinds == {"float", "integer", "choice"}


def test_catalog_bounds_and_metadata_are_merged_into_the_schema() -> None:
    schema = strategy_document_schema()
    risk = schema["$defs"]["RiskStep"]["properties"]["max_name_weight"]
    constraint = next(
        c for c in STRATEGY_SCALAR_CONSTRAINTS if c.pointer == "/risk/max_name_weight"
    )
    assert risk["type"] == "number"
    assert risk["default"] == 0.1
    assert risk.get("minimum", risk.get("exclusiveMinimum")) == constraint.minimum
    assert risk.get("maximum", risk.get("exclusiveMaximum")) == constraint.maximum
    assert risk["x-unit"] == constraint.unit.value
    assert risk["x-applied-stage"] == constraint.stage.value
    assert risk["examples"] == [constraint.example]
    nullable = schema["$defs"]["PortfolioStep"]["properties"]["minimum_liquidity"]
    assert nullable["anyOf"][1] == {"type": "null"} and nullable["default"] is None
    assert nullable["x-applied-stage"] == "eligibility"


def test_schema_properties_are_exactly_the_model_fields_and_nothing_is_hand_written() -> None:
    schema = strategy_document_schema()
    model_fields = [f.name for f in dataclass_fields(StrategySpec) if f.name != "identity"]
    assert list(schema["properties"]) == ["schema_version", *model_fields]
    assert schema["properties"]["schema_version"] == {"type": "string", "const": "1.0"}
    assert schema["required"][0] == "schema_version"
    assert schema["additionalProperties"] is False
    data = schema["$defs"]["DataStep"]
    assert data["properties"]["start"] == {
        "type": "string",
        "format": "date",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
    }
    assert data["properties"]["market"] == {"type": "string", "enum": ["KRX"]}
    assert data["required"] == ["market", "start", "end", "universe_id"]


def test_schema_hash_is_stable_and_order_independent() -> None:
    first, second = strategy_document_schema(), strategy_document_schema()
    assert first == second
    assert strategy_document_schema_hash(first) == strategy_document_schema_hash(second)
    reordered = dict(reversed(list(first.items())))
    assert strategy_document_schema_hash(reordered) == strategy_document_schema_hash(first)


def test_field_contracts_cover_every_scalar_path_with_catalog_metadata() -> None:
    contracts = {c.pointer: c for c in strategy_field_contracts()}
    assert contracts["/schema_version"].const == "1.0"
    weight = contracts["/factors/factors/*/weight"]
    assert weight.type == "number" and weight.required and not weight.has_default
    name_weight = contracts["/risk/max_name_weight"]
    assert (name_weight.minimum, name_weight.maximum) == (
        next(
            c.minimum for c in STRATEGY_SCALAR_CONSTRAINTS if c.pointer == "/risk/max_name_weight"
        ),
        next(
            c.maximum for c in STRATEGY_SCALAR_CONSTRAINTS if c.pointer == "/risk/max_name_weight"
        ),
    )
    assert name_weight.unit == "ratio" and name_weight.applied_stage == "risk"
    assert name_weight.default == 0.1 and name_weight.has_default
    liquidity = contracts["/portfolio/minimum_liquidity"]
    assert liquidity.nullable and liquidity.default is None and liquidity.has_default
    node_kind = contracts["/factors/factors/*/graph/nodes/*/kind"]
    assert node_kind.type == "string"  # one row per union branch shares the pointer template
    for constraint in STRATEGY_SCALAR_CONSTRAINTS:
        assert constraint.pointer in contracts, constraint.pointer
    assert all(isinstance(c, FieldContract) for c in contracts.values())


def test_scalar_unions_keep_integer_unless_number_is_present() -> None:
    from dataclasses import dataclass

    from strategy_workbench.domain.strategy import _schema

    @dataclass(frozen=True)
    class Probe:
        int_or_str: int | str
        bool_or_int: bool | int
        number_union: float | int | str

    builder = _schema._SchemaBuilder({})  # pyright: ignore[reportPrivateUsage]  # reason: unit
    schema = builder.dataclass_schema(Probe, "")
    assert schema["properties"]["int_or_str"] == {"type": ["integer", "string"]}
    assert schema["properties"]["bool_or_int"] == {"type": ["boolean", "integer"]}
    assert schema["properties"]["number_union"] == {"type": ["number", "string"]}


def test_contract_rows_are_unique_per_pointer_and_branch() -> None:
    rows = strategy_field_contracts()
    keys = [(row.pointer, row.branch) for row in rows]
    assert len(keys) == len(set(keys))
    kinds = {row.branch for row in rows if row.pointer == "/factors/factors/*/graph/nodes/*/kind"}
    assert kinds == set(EXPRESSION_NODE_KINDS)
    assert {row.branch for row in rows if row.pointer == "/risk/max_name_weight"} == {None}


def _identifier_markers(schema: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(x-catalog by path, x-reference by path, unmarked `*_id` string properties)."""
    catalogs: dict[str, str] = {}
    references: dict[str, str] = {}
    unmarked: list[str] = []

    def walk(node: dict[str, Any], path: str) -> None:
        for name, prop in node.get("properties", {}).items():
            here = f"{path}/{name}"
            if "x-catalog" in prop:
                catalogs[here] = prop["x-catalog"]
            elif "x-reference" in prop:
                references[here] = prop["x-reference"]
            elif name.endswith("_id"):
                unmarked.append(here)

    walk(schema, "")
    for name, definition in schema["$defs"].items():
        walk(definition, f"#/$defs/{name}")
    return catalogs, references, unmarked


def test_identifier_fields_declare_their_catalog_or_reference_namespace() -> None:
    """P3-03: an editor completes ids from the marker, never from a hand-written list."""
    schema = strategy_document_schema()
    catalogs, references, unmarked = _identifier_markers(schema)
    assert catalogs == {
        "#/$defs/DataStep/universe_id": "universe",
        "#/$defs/EligibilityRule/field_id": "equity-field",
        "#/$defs/FieldNode/field_id": "equity-field",
        "#/$defs/GroupNode/group_field_id": "equity-field",
        "#/$defs/SavedFactorNode/factor_id": "factor",
        "#/$defs/SavedSubgraphNode/subgraph_id": "subgraph",
        "#/$defs/SignalStep/regime_field_id": "equity-field",
        "#/$defs/PortfolioStep/liquidity_field_id": "equity-field",
        "#/$defs/RiskStep/risk_field_id": "equity-field",
    }
    assert set(references.values()) == {"node", "parameter"}
    defines = {
        f"{path}/{name}": prop["x-defines"]
        for path, node in [
            ("", schema),
            *[(f"#/$defs/{name}", definition) for name, definition in schema["$defs"].items()],
        ]
        for name, prop in node.get("properties", {}).items()
        if "x-defines" in prop
    }
    assert defines == {"/parameters": "parameter", "#/$defs/FactorGraph/nodes": "node"}
    assert set(defines.values()) == set(references.values())
    for path, namespace in defines.items():
        container, name = path.rsplit("/", 1)
        node = schema if container == "" else schema["$defs"][container.split("/")[-1]]
        items = node["properties"][name]["items"]
        members = items["oneOf"] if "oneOf" in items else [items]
        for member in members:
            definition = schema["$defs"][member["$ref"].split("/")[-1]]
            assert f"{namespace}_id" in definition["properties"], (path, member)
    assert all(p.endswith("_node_id") for p, r in references.items() if r == "node")
    assert references["#/$defs/ParameterNode/parameter_id"] == "parameter"
    # The only unmarked ids are definitions (a node's own id, a user-named factor, a parameter
    # declaration), never lookups into a catalog or into the document.
    definitions = {"#/$defs/FactorSignal/factor_id"} | {
        f"#/$defs/{name}Parameter/parameter_id" for name in ("Float", "Integer", "Choice")
    }
    assert all(p.endswith("/node_id") or p in definitions for p in unmarked), unmarked


def test_field_contracts_carry_the_identifier_markers() -> None:
    contracts = {c.pointer: c for c in strategy_field_contracts()}
    assert contracts["/eligibility/rules/*/field_id"].catalog == "equity-field"
    assert contracts["/signal/regime_field_id"].catalog == "equity-field"  # nullable keeps it
    assert contracts["/factors/factors/*/graph/nodes/*/factor_id"].catalog == "factor"
    assert contracts["/factors/factors/*/graph/nodes/*/input_node_id"].reference == "node"
    assert contracts["/factors/factors/*/graph/nodes/*/node_id"].catalog is None
    assert contracts["/factors/factors/*/graph/nodes/*/node_id"].reference is None
    assert contracts["/risk/max_name_weight"].catalog is None


def test_factor_authoring_mapping_is_owned_by_the_runtime_schema() -> None:
    properties = strategy_document_schema()["$defs"]["FactorSignal"]["properties"]
    assert {
        name: {
            key: value
            for key, value in schema.items()
            if key.startswith("x-authoring-")
        }
        for name, schema in properties.items()
    } == {
        "factor_id": {"x-authoring-source": "factor_id", "x-authoring-identity": True},
        "label": {"x-authoring-source": "label"},
        "direction": {"x-authoring-source": "preference"},
        "weight": {"x-authoring-default": 1.0},
        "graph": {"x-authoring-source": "default_graph"},
    }


def test_runtime_schema_fixture_is_current() -> None:
    """The frontend navigates the fixture copy in its own tests; it must equal the live schema."""
    fixture = json.loads((FIXTURES / "runtime-schema.json").read_text(encoding="utf-8"))
    assert fixture == strategy_document_schema(), (
        "runtime-schema.json is stale; regenerate with: "
        "uv run python tools/export_runtime_schema.py"
    )
