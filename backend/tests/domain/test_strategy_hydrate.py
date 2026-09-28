"""P1-01 typed hydrate: authoring payload → StrategySpec, fail-closed with JSON Pointer issues."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
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
    assert spec.identity.schema_version == "1.2"
    saved = hydrate_strategy_document(_document(), identity=StrategyIdentity("s-1", 4))
    assert saved.spec is not None
    assert saved.spec.identity == StrategyIdentity("s-1", 4)


def test_document_carrying_identity_is_rejected() -> None:
    document = _document()
    document["identity"] = {"strategy_id": "x", "revision": 1, "schema_version": "1.2"}

    assert _issue_codes(document) == [("structure.unknown_key", "/identity")]


@pytest.mark.parametrize("version", ["0.9", "1.1", "2", 1.0, None])
def test_unsupported_schema_version_fails_closed(version: object) -> None:
    document = _document()
    document["schema_version"] = version

    assert _issue_codes(document) == [("structure.unsupported_schema_version", "/schema_version")]
    # 1.1 은 은퇴 버전이다 — 업그레이더(P2-09)를 거쳐서만 들어온다.
    assert "1.2" in SUPPORTED_SCHEMA_VERSIONS and "1.1" not in SUPPORTED_SCHEMA_VERSIONS


def test_missing_schema_version_fails_closed() -> None:
    document = _document()
    del document["schema_version"]

    assert _issue_codes(document) == [("structure.missing_field", "/schema_version")]


def test_only_schema_version_and_title_are_required() -> None:
    """spec D3: 1.2 최상위 필수 키는 둘뿐이다. 나머지는 모델 기본값으로 채워진다.

    `factors` 를 생략해도 빈 배열이어도 구조 오류가 아니다 — 새 전략이 "구조 오류"가 아니라
    "팩터를 추가하세요"(semantic `strategy.factor.required`)로 시작하는 근거다(P4-04 시작 문서).
    """
    from strategy_workbench.domain.strategy.facade.validation import validate_strategy

    for document in (
        {"schema_version": "1.2", "title": ""},
        {"schema_version": "1.2", "title": "", "factors": []},
    ):
        result = hydrate_strategy_document(document, identity=DRAFT)
        assert result.ok, result.issues
        assert result.spec is not None
        assert result.spec.factors == ()
        assert [issue.code for issue in validate_strategy(result.spec).issues] == [
            "strategy.title.empty",
            "strategy.factor.required",
        ]


@pytest.mark.parametrize(
    ("pointer", "patch"),
    [
        (
            "/data",
            {
                "data": {
                    "market": "KRX",
                    "start": "2021-01-01",
                    "end": "2026-08-31",
                    "universe_id": "krx.common-stock",
                }
            },
        ),
        ("/execution", {"execution": {"timing": "next_open", "fee_bps": 15.0}}),
    ],
)
def test_execution_settings_are_unknown_keys_in_1_2(pointer: str, patch: dict[str, Any]) -> None:
    """spec D3 S1~S2: 실행 설정은 문서를 떠났다. 남아 있으면 조용히 무시되지 않고 fail-closed."""
    document = {**_document(), **patch}

    assert (("structure.unknown_key", pointer)) in _issue_codes(document)


def test_graph_missing_policy_is_an_unknown_key_in_1_2() -> None:
    """spec D3 S3: 결측 정책은 실행 설정이 소유한다(P2-02). 1.2 문서에서는 키 자체가 없다."""
    document = _document()
    document["factors"][0]["graph"]["missing_policy"] = "zero"

    assert ("structure.unknown_key", "/factors/0/graph/missing_policy") in _issue_codes(document)


def test_unknown_keys_at_every_depth_carry_pointers() -> None:
    document = _document()
    document["risk"]["max_name_wieght"] = 0.05
    document["factors"][0]["graph"]["nodes"][1]["bogus"] = 1
    document["extra"] = True

    codes = _issue_codes(document)
    assert ("structure.unknown_key", "/risk/max_name_wieght") in codes
    assert ("structure.unknown_key", "/factors/0/graph/nodes/1/bogus") in codes
    assert ("structure.unknown_key", "/extra") in codes


def test_missing_required_fields_are_reported_not_defaulted() -> None:
    document = _document()
    del document["title"]
    del document["factors"][0]["graph"]["output_node_id"]

    codes = _issue_codes(document)
    assert ("structure.missing_field", "/title") in codes
    assert ("structure.missing_field", "/factors/0/graph/output_node_id") in codes


def test_kind_discriminator_is_required_and_validated() -> None:
    document = _document()
    nodes = document["factors"][0]["graph"]["nodes"]
    nodes[0]["kind"] = "fieldd"
    nodes.append({"node_id": "c", "value": 1.0})

    codes = _issue_codes(document)
    assert ("structure.unknown_kind", "/factors/0/graph/nodes/0/kind") in codes
    assert ("structure.missing_field", "/factors/0/graph/nodes/2/kind") in codes


def test_scalar_literals_are_typed_and_bad_literals_fail() -> None:
    document = _document()
    document["portfolio"]["rebalance"] = "month_end"
    document["risk"]["max_name_weight"] = "5%"
    document["portfolio"]["selection_count"] = 20.5
    document["risk"]["sector_neutral"] = "yes"

    codes = _issue_codes(document)
    assert ("structure.invalid_enum", "/portfolio/rebalance") in codes
    assert ("structure.type_mismatch", "/risk/max_name_weight") in codes
    assert ("structure.type_mismatch", "/portfolio/selection_count") in codes
    assert ("structure.type_mismatch", "/risk/sector_neutral") in codes


def test_typed_fields_normalise_int_float_and_iso_dates() -> None:
    document = _document()
    document["risk"]["max_name_weight"] = 1
    document["factors"][0]["weight"] = 1
    document["portfolio"]["selection_count"] = 20.0

    spec = _hydrate_ok(document)

    assert spec.risk.max_name_weight == 1.0 and isinstance(spec.risk.max_name_weight, float)
    assert isinstance(spec.factors[0].weight, float)
    assert spec.portfolio.selection_count == 20 and isinstance(spec.portfolio.selection_count, int)


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
    document["factors"][0]["weight"] = -0.0
    document["eligibility"]["rules"] = [{"field_id": "x", "operator": "gt", "value": -0.0}]
    document["factors"][0]["graph"]["nodes"].append(
        {"kind": "constant", "node_id": "zero", "value": -0.0}
    )
    positive = copy.deepcopy(document)
    positive["factors"][0]["weight"] = 0.0
    positive["eligibility"]["rules"][0]["value"] = 0.0
    positive["factors"][0]["graph"]["nodes"][2]["value"] = 0.0

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
    document["factors"][0]["graph"]["nodes"][1]["lag"] = datetime(2021, 1, 1)
    document["risk"]["max_name_weight"] = 10**400

    codes = _issue_codes(document)
    assert ("structure.type_mismatch", "/factors/0/graph/nodes/1/lag") in codes
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
    )
    plus_zero = replace(
        spec,
        risk=replace(spec.risk, net_exposure=0.0),
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
    document["factors"][0]["graph"]["nodes"][1]["window"] = "252"

    codes = _issue_codes(document)
    assert ("structure.type_mismatch", "/eligibility/rules") in codes
    assert ("structure.type_mismatch", "/factors/0/graph/nodes/1/window") in codes


def test_default_from_must_name_a_required_sibling_field() -> None:
    """`default-from`은 모델 선언 계약이다. 원천이 없거나 선택 필드면 사용자 문서 오류가 아니라
    TypeError로 즉시 드러나야 한다(원천이 문서에 없을 때 KeyError로 새는 것을 막는다)."""
    from dataclasses import dataclass, field

    from strategy_workbench.domain.strategy._hydrate import _hydrate

    @dataclass(frozen=True, kw_only=True)
    class OptionalSource:
        name: str = "n"
        label: str = field(metadata={"default-from": "name"})

    @dataclass(frozen=True, kw_only=True)
    class MissingSource:
        label: str = field(metadata={"default-from": "nope"})

    for bad in (OptionalSource, MissingSource):
        with pytest.raises(TypeError, match="default-from must name a required field"):
            _hydrate(bad, {}, "", [])

    @dataclass(frozen=True, kw_only=True)
    class Good:
        name: str
        label: str = field(metadata={"default-from": "name"})

    issues: list[Any] = []
    assert _hydrate(Good, {"name": "x"}, "", issues) == Good(name="x", label="x")
    assert _hydrate(Good, {"name": "x", "label": "y"}, "", issues) == Good(name="x", label="y")
    assert issues == []
    missing: list[Any] = []
    _hydrate(Good, {}, "", missing)
    assert [(issue.code, issue.pointer) for issue in missing] == [
        ("structure.missing_field", "/name")
    ]


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("signal", "method", "weighted_sum"),
        ("signal", "entry_percentile", 0.1),
    ],
)
def test_removed_1_0_fields_are_flagged_as_legacy_shape(
    section: str, key: str, value: object
) -> None:
    """schema 1.1 S2: 읽지 않던 필드는 fail-closed이고, 1.0 문법임을 문장이 말한다(P1-05).

    1.0 의 `execution.order_style` 은 1.2 에서 섹션째 사라져(`/execution`) 다른 행이 소유한다.
    """
    document = copy.deepcopy(_document())
    document.setdefault(section, {})[key] = value

    result = hydrate_strategy_document(document, identity=DRAFT)

    assert not result.ok
    assert [(issue.code, issue.pointer) for issue in result.issues] == [
        ("structure.legacy_shape", f"/{section}/{key}")
    ]
    assert "1.0" in result.issues[0].message and f"got={key!r}" in result.issues[0].message


def test_cross_sectional_demean_hydrates_from_a_document() -> None:
    """schema 1.1 S4: GUI가 노출하는 `cross_sectional: demean`을 문서 입구가 받아 준다."""
    document = copy.deepcopy(_document())
    document["factors"][0]["graph"]["nodes"].append(
        {
            "kind": "cross_sectional",
            "node_id": "dm",
            "operator": "demean",
            "input_node_id": "mom_252",
        }
    )
    document["factors"][0]["graph"]["output_node_id"] = "dm"

    spec = _hydrate_ok(document)

    node = spec.factors[0].graph.nodes[-1]
    assert node.kind == "cross_sectional" and str(node.operator) == "demean"


@pytest.mark.parametrize("operator", ["rank", "zscore", "winsorize", "neutralize"])
def test_unary_aliases_of_cross_sectional_operators_are_gone(operator: str) -> None:
    """schema 1.1 S4: 횡단면 변환은 `cross_sectional`로만 쓴다. `unary`에는 negate·lag만 남는다."""
    document = copy.deepcopy(_document())
    document["factors"][0]["graph"]["nodes"].append(
        {"kind": "unary", "node_id": "x", "operator": operator, "input_node_id": "close"}
    )

    result = hydrate_strategy_document(document, identity=DRAFT)

    assert not result.ok
    # 1.0에서만 쓰던 alias라 "고를 수 없는 값"이 아니라 "예전 문법"으로 안내한다(P1-05).
    assert [(issue.code, issue.pointer) for issue in result.issues] == [
        ("structure.legacy_shape", "/factors/0/graph/nodes/2/operator")
    ]
    assert f"got=unary/{operator}" in result.issues[0].message


def test_normalization_is_part_of_the_strategy_hash() -> None:
    """`signal.normalization` 은 전략 의미라 해시가 값마다 달라야 한다(P2-04).

    다르지 않으면 같은 `spec_hash` 아래에 서로 다른 합성 규칙 두 벌이 저장되고, 캐시된
    백테스트 결과가 다른 전략의 것으로 재사용된다.
    """
    document = _document()
    hashes = {}
    for method in ("none", "rank", "zscore"):
        candidate = copy.deepcopy(document)
        candidate["signal"] = {"normalization": method}
        result = hydrate_strategy_document(candidate, identity=DRAFT)
        assert result.ok and result.spec is not None, [i.code for i in result.issues]
        hashes[method] = strategy_spec_hash(result.spec)

    assert len(set(hashes.values())) == 3


def test_omitted_normalization_hashes_like_an_explicit_rank() -> None:
    """생략과 기본값 명시가 같은 해시다 — 업그레이드된 문서만 `none` 을 적는다."""
    omitted = _document()
    omitted.pop("signal", None)
    explicit = copy.deepcopy(omitted)
    explicit["signal"] = {"normalization": "rank"}

    both = [hydrate_strategy_document(item, identity=DRAFT) for item in (omitted, explicit)]
    assert all(result.ok and result.spec is not None for result in both)

    left, right = both
    assert left.spec is not None and right.spec is not None
    assert strategy_spec_hash(left.spec) == strategy_spec_hash(right.spec)


def test_unknown_normalization_value_is_a_structural_enum_issue() -> None:
    """모르는 값은 구조 오류로 fail-closed 한다 — 기본값으로 조용히 떨어지지 않는다."""
    document = _document()
    document["signal"] = {"normalization": "percentile"}

    result = hydrate_strategy_document(document, identity=DRAFT)

    assert not result.ok and result.spec is None
    (issue,) = [item for item in result.issues if item.pointer == "/signal/normalization"]
    assert issue.code == "structure.invalid_enum"
    assert "percentile" in issue.message
