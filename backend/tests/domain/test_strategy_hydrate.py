"""P1-01 typed hydrate: authoring payload → StrategySpec, fail-closed with JSON Pointer issues."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.domain.strategy.facade.document import (
    SUPPORTED_SCHEMA_VERSIONS,
    HydrationStatus,
    hydrate_saved_strategy,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
    canonical_strategy_payload,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)


def _document() -> dict[str, Any]:
    return json.loads((FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"))


def _hydrate_ok(document: dict[str, Any]) -> StrategySpec:
    result = hydrate_strategy_document(document, identity=DRAFT)
    assert result.ok, result.issues
    assert result.spec is not None
    return result.spec


def _issue_codes(document: dict[str, Any]) -> list[tuple[str, str]]:
    result = hydrate_strategy_document(document, identity=DRAFT)
    assert result.status is HydrationStatus.STRUCTURAL_ERROR
    assert result.spec is None
    return [(issue.code, issue.pointer) for issue in result.issues]


def test_identity_is_injected_from_the_envelope_not_the_document() -> None:
    spec = _hydrate_ok(_document())

    assert spec.identity == DRAFT
    assert spec.identity.schema_version == "1.0"
    saved = hydrate_strategy_document(_document(), identity=StrategyIdentity("s-1", 4))
    assert saved.spec is not None
    assert saved.spec.identity == StrategyIdentity("s-1", 4)


def test_document_carrying_identity_is_rejected() -> None:
    document = _document()
    document["identity"] = {"strategy_id": "x", "revision": 1, "schema_version": "1.0"}

    assert _issue_codes(document) == [("structure.unknown_key", "/identity")]


@pytest.mark.parametrize("version", ["0.9", "2", 1.0, None])
def test_unsupported_schema_version_fails_closed(version: object) -> None:
    document = _document()
    document["schema_version"] = version

    assert _issue_codes(document) == [("structure.unsupported_schema_version", "/schema_version")]
    assert "1.0" in SUPPORTED_SCHEMA_VERSIONS


def test_missing_schema_version_fails_closed() -> None:
    document = _document()
    del document["schema_version"]

    assert _issue_codes(document) == [("structure.missing_field", "/schema_version")]


def test_unknown_keys_at_every_depth_carry_pointers() -> None:
    document = _document()
    document["risk"]["max_name_wieght"] = 0.05
    document["factors"]["factors"][0]["graph"]["nodes"][1]["bogus"] = 1
    document["extra"] = True

    codes = _issue_codes(document)
    assert ("structure.unknown_key", "/risk/max_name_wieght") in codes
    assert ("structure.unknown_key", "/factors/factors/0/graph/nodes/1/bogus") in codes
    assert ("structure.unknown_key", "/extra") in codes


def test_missing_required_fields_are_reported_not_defaulted() -> None:
    document = _document()
    del document["data"]["start"]
    del document["factors"]["factors"][0]["graph"]["output_node_id"]

    codes = _issue_codes(document)
    assert ("structure.missing_field", "/data/start") in codes
    assert ("structure.missing_field", "/factors/factors/0/graph/output_node_id") in codes


def test_kind_discriminator_is_required_and_validated() -> None:
    document = _document()
    nodes = document["factors"]["factors"][0]["graph"]["nodes"]
    nodes[0]["kind"] = "fieldd"
    nodes.append({"node_id": "c", "value": 1.0})

    codes = _issue_codes(document)
    assert ("structure.unknown_kind", "/factors/factors/0/graph/nodes/0/kind") in codes
    assert ("structure.missing_field", "/factors/factors/0/graph/nodes/2/kind") in codes


def test_scalar_literals_are_typed_and_bad_literals_fail() -> None:
    document = _document()
    document["data"]["start"] = "2021-13-01"
    document["portfolio"]["rebalance"] = "month_end"
    document["risk"]["max_name_weight"] = "5%"
    document["portfolio"]["selection_count"] = 20.5
    document["risk"]["sector_neutral"] = "yes"

    codes = _issue_codes(document)
    assert ("structure.invalid_date", "/data/start") in codes
    assert ("structure.invalid_enum", "/portfolio/rebalance") in codes
    assert ("structure.type_mismatch", "/risk/max_name_weight") in codes
    assert ("structure.type_mismatch", "/portfolio/selection_count") in codes
    assert ("structure.type_mismatch", "/risk/sector_neutral") in codes


def test_typed_fields_normalise_int_float_and_iso_dates() -> None:
    document = _document()
    document["execution"]["fee_bps"] = 15
    document["factors"]["factors"][0]["weight"] = 1
    document["portfolio"]["selection_count"] = 20.0

    spec = _hydrate_ok(document)

    assert spec.execution.fee_bps == 15.0 and isinstance(spec.execution.fee_bps, float)
    assert isinstance(spec.factors.factors[0].weight, float)
    assert spec.portfolio.selection_count == 20 and isinstance(spec.portfolio.selection_count, int)
    assert spec.data.start == date(2021, 1, 1)


def test_parameter_value_union_folds_integral_floats_but_keeps_bool_and_str() -> None:
    document = _document()
    document["parameters"] = [
        {"kind": "choice", "parameter_id": "c", "default": 1.0, "choices": [1.0, 2.5, "a", True]},
        {"kind": "float", "parameter_id": "w", "default": 1, "minimum": 0, "maximum": 2},
        {"kind": "integer", "parameter_id": "n", "default": 3.0, "minimum": 1, "maximum": 5},
    ]
    variant = copy.deepcopy(document)
    variant["parameters"][0]["default"] = 1
    variant["parameters"][0]["choices"] = [1, 2.5, "a", True]
    variant["parameters"][1]["default"] = 1.0
    variant["parameters"][2]["default"] = 3

    spec = _hydrate_ok(document)
    choice, weight, count = spec.parameters
    assert (
        isinstance(choice, ChoiceParameter)
        and choice.default == 1
        and isinstance(choice.default, int)
    )
    assert choice.choices == (1, 2.5, "a", True)
    assert isinstance(weight, FloatParameter) and isinstance(weight.default, float)
    assert isinstance(count, IntegerParameter) and isinstance(count.default, int)
    assert strategy_spec_hash(spec) == strategy_spec_hash(_hydrate_ok(variant))


def test_negative_zero_inside_tuple_fields_hashes_like_zero() -> None:
    document = _document()
    document["factors"]["factors"][0]["weight"] = -0.0
    document["eligibility"]["rules"] = [{"field_id": "x", "operator": "gt", "value": -0.0}]
    document["factors"]["factors"][0]["graph"]["nodes"].append(
        {"kind": "constant", "node_id": "zero", "value": -0.0}
    )
    positive = copy.deepcopy(document)
    positive["factors"]["factors"][0]["weight"] = 0.0
    positive["eligibility"]["rules"][0]["value"] = 0.0
    positive["factors"]["factors"][0]["graph"]["nodes"][2]["value"] = 0.0

    minus = _hydrate_ok(document)

    assert strategy_spec_hash(minus) == strategy_spec_hash(_hydrate_ok(positive))
    assert "-0.0" not in canonical_strategy_json(minus)


def test_scientific_notation_hydrates_like_the_decimal_literal() -> None:
    # JSON path; YAML `1e-2` depends on the YAML 1.2 codec (P0-03/P1-02) and is tested there.
    document = _document()
    document["risk"]["max_name_weight"] = 1e-2
    decimal = copy.deepcopy(document)
    decimal["risk"]["max_name_weight"] = 0.01

    assert strategy_spec_hash(_hydrate_ok(document)) == strategy_spec_hash(_hydrate_ok(decimal))


def test_datetime_on_a_date_field_and_huge_ints_fail_closed() -> None:
    from datetime import datetime

    document = _document()
    document["data"]["start"] = datetime(2021, 1, 1)
    document["risk"]["max_name_weight"] = 10**400

    codes = _issue_codes(document)
    assert ("structure.invalid_date", "/data/start") in codes
    assert ("structure.type_mismatch", "/risk/max_name_weight") in codes


def test_pointers_escape_rfc6901_tokens() -> None:
    document = _document()
    document["a/b"] = 1
    document["~x"] = 1

    codes = _issue_codes(document)
    assert ("structure.unknown_key", "/a~1b") in codes
    assert ("structure.unknown_key", "/~0x") in codes


def test_canonical_payload_normalises_negative_zero_and_choice_values() -> None:
    spec = _hydrate_ok(_document())
    minus_zero = replace(
        spec,
        risk=replace(spec.risk, net_exposure=-0.0),
        execution=replace(spec.execution, slippage_bps=0.0),
    )
    plus_zero = replace(
        spec,
        risk=replace(spec.risk, net_exposure=0.0),
        execution=replace(spec.execution, slippage_bps=0.0),
    )
    assert strategy_spec_hash(minus_zero) == strategy_spec_hash(plus_zero)
    assert "-0.0" not in canonical_strategy_json(minus_zero)

    with_choice = replace(
        spec,
        parameters=(
            ChoiceParameter(parameter_id="c", default=2.0, choices=(2.0, 3.5), kind="choice"),
        ),
    )
    payload = canonical_strategy_payload(with_choice)
    assert payload["parameters"][0]["default"] == 2 and isinstance(
        payload["parameters"][0]["default"], int
    )
    assert payload["parameters"][0]["choices"] == [2, 3.5]


def test_canonical_round_trip_through_hydrate_preserves_everything_but_identity() -> None:
    original = replace(_hydrate_ok(_document()), identity=StrategyIdentity("s-7", 3))
    payload = canonical_strategy_payload(original)

    rehydrated = hydrate_strategy_document(payload, identity=StrategyIdentity("s-9", 1))

    assert rehydrated.ok and rehydrated.spec is not None
    assert replace(rehydrated.spec, identity=original.identity) == original
    assert strategy_spec_hash(rehydrated.spec) == strategy_spec_hash(original)


def test_saved_strategy_hydrate_reads_identity_from_the_document() -> None:
    legacy = json.loads((FIXTURES / "quality_momentum.legacy.json").read_text(encoding="utf-8"))

    result = hydrate_saved_strategy(legacy)

    assert result.ok and result.spec is not None
    assert result.spec.identity == StrategyIdentity("strategy-legacy", 3)
    assert strategy_spec_hash(result.spec) == strategy_spec_hash(_hydrate_ok(_document()))

    legacy["identity"]["schema_version"] = "9.9"
    stale = hydrate_saved_strategy(legacy)
    assert [(i.code, i.pointer) for i in stale.issues] == [
        ("structure.unsupported_schema_version", "/identity/schema_version")
    ]


def test_sequence_index_pointers_and_non_sequence_values() -> None:
    document = _document()
    document["eligibility"]["rules"] = {"field_id": "x"}
    document["factors"]["factors"][0]["graph"]["nodes"][1]["window"] = "252"

    codes = _issue_codes(document)
    assert ("structure.type_mismatch", "/eligibility/rules") in codes
    assert ("structure.type_mismatch", "/factors/factors/0/graph/nodes/1/window") in codes
