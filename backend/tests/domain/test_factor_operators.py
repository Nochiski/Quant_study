"""연산자 카탈로그가 스키마 enum과 같은 집합을 덮는지, 정의한 규칙이 실제 추론과 같은지 검사한다.

P1-03 (spec D8). 이 테스트가 막는 회귀는 둘이다.

1. 레지스트리와 `_nodes.py` enum이 갈라지는 것 — 새 연산자를 enum에만 추가하면 화면에 이름 없는
   항목이 뜨고, 레지스트리에만 추가하면 팔레트가 문서에 못 쓰는 항목을 권한다. 양방향으로 본다.
2. `output_type_rule`·`unit_rule`이 `_validation.py`의 실제 추론과 갈라지는 것 — 카탈로그는
   추론 결과를 사람 말로 옮기기 위한 메타데이터이므로, 같은 그래프를 실제로 검증해 대조한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypeAlias, cast, get_type_hints

import pytest

from strategy_workbench.domain.factor.facade.expression import (
    EXPRESSION_NODE_KINDS,
    ConstantNode,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    NodeValueType,
)
from strategy_workbench.domain.factor.facade.operators import (
    OPERATOR_DEFINITIONS,
    OperatorAvailability,
    OperatorDefinition,
    OutputTypeRule,
    UnitRule,
    operator_definitions,
    operator_description_keys,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph


def _operator_enum(kind: str) -> type[StrEnum] | None:
    """스키마 쪽 진실: 노드 dataclass의 `operator` 필드 타입(없으면 None)."""
    node_type = EXPRESSION_NODE_KINDS[kind]
    if "operator" not in node_type.__dataclass_fields__:
        return None
    return cast(type[StrEnum], get_type_hints(node_type)["operator"])


def _schema_keys() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for kind in EXPRESSION_NODE_KINDS:
        enum_type = _operator_enum(kind)
        if enum_type is None:
            continue
        for member in enum_type:
            keys.add((kind, str(member.value)))
    return keys


def test_registry_covers_every_schema_operator() -> None:
    missing = _schema_keys() - set(OPERATOR_DEFINITIONS)
    assert not missing, f"스키마 enum에 있는데 레지스트리에 없다 — keys={sorted(missing)}"


def test_registry_has_no_operator_the_schema_rejects() -> None:
    extra = set(OPERATOR_DEFINITIONS) - _schema_keys()
    assert not extra, f"레지스트리에 있는데 스키마 enum에 없다 — keys={sorted(extra)}"


def test_definition_order_is_kind_then_schema_enum_order() -> None:
    """팔레트·드롭다운이 카탈로그 순서를 그대로 쓴다. 노드 선언 순서 × enum 선언 순서가 화면 순서다.

    기대 순서는 `_nodes.py`에서 독립적으로 다시 만든다 — 레지스트리 자신에서 파생하면
    (`OPERATOR_DEFINITIONS`는 `_DEFINITIONS`를 그대로 순회한 dict라) 무엇을 바꿔도 통과한다.
    """
    expected = [
        (kind, str(member.value))
        for kind in EXPRESSION_NODE_KINDS
        if (enum_type := _operator_enum(kind)) is not None
        for member in enum_type
    ]

    assert [
        (definition.kind, definition.operator) for definition in operator_definitions()
    ] == expected


def test_description_and_formula_keys_are_unique_and_namespaced() -> None:
    definitions = operator_definitions()
    keys = [definition.description_key for definition in definitions]
    assert len(set(keys)) == len(keys), "설명 키가 중복되면 두 연산자가 같은 문장을 쓴다"
    for definition in definitions:
        assert definition.description_key == (
            f"strategy.operator.{definition.kind}.{definition.operator}"
        )
        assert definition.formula_key == f"{definition.description_key}.formula"


def test_params_name_real_node_properties() -> None:
    for (kind, operator), definition in OPERATOR_DEFINITIONS.items():
        properties = set(EXPRESSION_NODE_KINDS[kind].__dataclass_fields__)
        unknown = {parameter.property_name for parameter in definition.params} - properties
        assert not unknown, f"노드에 없는 property를 params에 적었다 — {kind}.{operator} {unknown}"


def test_group_operators_are_unsupported_until_a_group_field_exists() -> None:
    # spec D5: 실데이터 어댑터가 GROUP_SERIES 필드를 주기 전까지 그룹 노드는 팔레트에 없다.
    for (kind, _operator), definition in OPERATOR_DEFINITIONS.items():
        expected = (
            OperatorAvailability.UNSUPPORTED if kind == "group" else OperatorAvailability.AVAILABLE
        )
        assert definition.availability is expected, definition


def test_operator_description_keys_index_matches_the_registry() -> None:
    for (kind, operator), definition in OPERATOR_DEFINITIONS.items():
        keys = operator_description_keys(_operator_enum(kind))
        assert keys is not None, kind
        assert keys[operator] == definition.description_key


def test_operator_description_keys_is_none_for_a_non_operator_enum() -> None:
    assert operator_description_keys(NodeValueType) is None


# -- 규칙 ↔ 실제 추론 대조 ----------------------------------------------------------------------

# 입력 모양을 바꿔 가며 대조해야 `output_type_rule` 세 값이 서로 갈린다(P1-03 리뷰 P2-3):
# 숫자 시계열 입력만 쓰면 SAME_AS_INPUT·NUMERIC_SERIES·NUMERIC_IF_ANY_SERIES가 전부
# NUMERIC_SERIES 하나로 접혀서 규칙을 뒤바꿔도 통과한다.
_PRICE = FieldMetadata(field_id="close", unit="KRW", value_type=NodeValueType.NUMERIC_SERIES)
# scalar와 섞을 때 쓰는 무차원 시계열. 더하기·빼기는 좌우 단위가 다르면 error라 상수(단위 "1")와
# 섞으려면 시계열 쪽도 무차원이어야 한다.
_RATIO = FieldMetadata(field_id="ratio", unit="1", value_type=NodeValueType.NUMERIC_SERIES)
_SECTOR = FieldMetadata(field_id="sector", unit="1", value_type=NodeValueType.GROUP_SERIES)

Shape: TypeAlias = Literal["series", "scalar"]

# 검증을 통과하는 최소 파라미터 값. 기본값으로 충분한 property는 여기 없다.
_SAMPLE_PARAMETER_VALUES: dict[str, object] = {
    "window": 2,
    "periods": 1,
    "group_field_id": "sector",
}


def _node_inputs(kind: str) -> tuple[str, ...]:
    return tuple(
        item.name
        for item in EXPRESSION_NODE_KINDS[kind].__dataclass_fields__.values()
        if item.metadata.get("reference") == "node"
    )


def _input_shapes(definition: OperatorDefinition) -> list[tuple[Shape, ...]]:
    """대조할 입력 모양 조합. arity 2는 양쪽 순서의 혼합까지 봐야 규칙이 전부 갈린다."""
    series: Shape = "series"
    scalar: Shape = "scalar"
    combinations: list[tuple[Shape, ...]] = [
        tuple(series for _ in range(definition.arity)),
        tuple(scalar for _ in range(definition.arity)),
    ]
    if definition.arity == 2:
        combinations.append((series, scalar))
        combinations.append((scalar, series))
    return combinations


def _sample_graph(definition: OperatorDefinition, shapes: tuple[Shape, ...]) -> FactorGraph:
    """정의 하나를 주어진 모양의 입력 위에 올린 최소 그래프."""
    node_type = EXPRESSION_NODE_KINDS[definition.kind]
    inputs = _node_inputs(definition.kind)
    # 문서 경로(hydrate)는 operator를 enum으로 강제한다. `_validation.py`가 `is`로 비교하므로
    # 직접 만드는 노드도 같은 enum 멤버를 넣어야 실제 추론과 같은 값을 본다.
    enum_type = _operator_enum(definition.kind)
    assert enum_type is not None
    values: dict[str, object] = {
        "node_id": "subject",
        "kind": definition.kind,
        "operator": enum_type(definition.operator),
    }
    for index, name in enumerate(inputs):
        values[name] = f"source_{index}"
    for parameter in definition.params:
        if parameter.property_name in _SAMPLE_PARAMETER_VALUES:
            values[parameter.property_name] = _SAMPLE_PARAMETER_VALUES[parameter.property_name]
    series_field = _RATIO if "scalar" in shapes else _PRICE
    sources = tuple(
        FieldNode(node_id=f"source_{index}", field_id=series_field.field_id, kind="field")
        if shape == "series"
        else ConstantNode(node_id=f"source_{index}", value=1.0, kind="constant")
        for index, shape in enumerate(shapes)
    )
    return FactorGraph(
        nodes=(*sources, node_type(**values)),  # pyright: ignore[reportCallIssue]  # reason: kind별 생성자가 달라 정적으로 좁힐 수 없다
        output_node_id="subject",
    )


def _expected_value_type(rule: OutputTypeRule, shapes: tuple[Shape, ...]) -> NodeValueType:
    if rule is OutputTypeRule.BOOLEAN_SERIES:
        return NodeValueType.BOOLEAN_SERIES
    if rule is OutputTypeRule.NUMERIC_SERIES:
        return NodeValueType.NUMERIC_SERIES  # scalar를 넣어도 시계열로 승격된다
    if rule is OutputTypeRule.SAME_AS_INPUT:
        source = shapes[0]
    else:  # NUMERIC_IF_ANY_SERIES — 한쪽이라도 시계열이면 시계열
        source = "series" if "series" in shapes else "scalar"
    return NodeValueType.NUMERIC_SERIES if source == "series" else NodeValueType.SCALAR


def _expected_unit(definition: OperatorDefinition, source_units: list[str]) -> str:
    if definition.unit_rule is UnitRule.BOOLEAN:
        return "bool"
    if definition.unit_rule is UnitRule.COMBINED:
        symbol = "*" if definition.operator == "multiply" else "/"
        return f"({source_units[0]}{symbol}{source_units[1]})"
    return source_units[0]


@pytest.mark.parametrize(
    ("definition", "shapes"),
    [
        (definition, shapes)
        for definition in operator_definitions()
        for shapes in _input_shapes(definition)
    ],
    ids=lambda item: (
        f"{item.kind}.{item.operator}" if isinstance(item, OperatorDefinition) else "+".join(item)
    ),
)
def test_declared_rules_match_the_inferred_contract(
    definition: OperatorDefinition, shapes: tuple[Shape, ...]
) -> None:
    validation = validate_factor_graph(
        _sample_graph(definition, shapes), fields=(_PRICE, _RATIO, _SECTOR)
    )
    assert validation.valid, [issue.message for issue in validation.issues]
    contracts = {contract.node_id: contract for contract in validation.node_contracts}
    subject = contracts["subject"]

    assert subject.value_type is _expected_value_type(definition.output_type_rule, shapes)
    assert subject.unit == _expected_unit(
        definition, [contracts[f"source_{index}"].unit for index in range(definition.arity)]
    )


def test_arity_matches_the_node_input_count() -> None:
    for (kind, operator), definition in OPERATOR_DEFINITIONS.items():
        assert definition.arity == len(_node_inputs(kind)), f"{kind}.{operator}"
