"""노드 정수 파라미터의 하한을 검증기와 runtime schema가 같은 상수에서 읽는지 검사한다 (P1-04).

이 테스트가 막는 회귀는 하나다. 하한을 `_validation.py`에만 두면 runtime schema는 하한 없는
`integer`를 발행하고, 스키마로 기본값을 만드는 화면(Form 컨트롤·Graph 팔레트의
`materializeSchemaValue`)은 `window: 0`인 노드를 만들어 backend가 곧바로 거부한다 — 사용자는
방금 고른 연산자가 왜 오류인지 알 수 없다.

두 방향으로 본다.

1. **선언 완전성**: 노드 dataclass의 정수 property는 전부 `minimum` 선언을 갖는다. 하한 없는 정수
   property를 새로 만들면 여기서 깨진다.
2. **소비자 일치**: 선언한 값 그대로 runtime schema가 `minimum`으로 발행하고, 검증기는 그 값은
   받고 하나 작은 값은 거부한다.
3. **씨앗 수용**: 카탈로그의 모든 연산자에 대해, runtime schema가 발행한 파라미터 씨앗
   (non-null `default` → 그 값, 아니면 정수 `minimum`)으로 만든 그래프를 검증기가 받아들인다.
   화면은 이 씨앗으로 노드를 만들므로(`materializeSchemaValue`), 여기가 통과해야 "팔레트에서
   고르자마자 검증 오류"가 나지 않는다(리뷰 차단 1: `unary.lag`의 `periods`가 null이었다).
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest

from strategy_workbench.domain.factor.facade.expression import (
    EXPRESSION_NODE_KINDS,
    ConstantNode,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    NodeValueType,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
    field_minimum,
)
from strategy_workbench.domain.factor.facade.operators import (
    OperatorDefinition,
    operator_definitions,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph
from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_documents"

_PRICE = FieldMetadata(field_id="price.close", unit="KRW", value_type=NodeValueType.NUMERIC_SERIES)
_SECTOR = FieldMetadata(field_id="sector", unit="1", value_type=NodeValueType.GROUP_SERIES)
_FIELDS = (_PRICE, _SECTOR)

# 카탈로그 id는 사용자가 고르는 자리라 스키마가 씨앗을 발행하지 않는다(화면도 빈 문자열로 둔다).
# 검증 가능한 그래프를 만들려면 테스트가 실제 카탈로그 값을 하나 준다. 새 카탈로그 파라미터가
# 생기면 아래 `test_every_catalog_parameter_has_a_sample`이 먼저 깨진다.
_CATALOG_SAMPLES: dict[str, object] = {"group_field_id": _SECTOR.field_id}


def _is_integer_property(annotation: Any) -> bool:
    """`int` 또는 `int | None`인가. 다른 숫자 타입(분위수 `float`)은 관계 제약이라 대상이 아니다."""
    if annotation is int:
        return True
    members = get_args(annotation)
    return bool(members) and set(members) <= {int, type(None)} and int in members


_INTEGER_PROPERTIES: list[tuple[str, type, str]] = [
    (kind, node_type, item.name)
    for kind, node_type in EXPRESSION_NODE_KINDS.items()
    for item in dataclass_fields(node_type)
    if _is_integer_property(get_type_hints(node_type)[item.name])
]


def test_integer_node_properties_exist() -> None:
    """대상이 사라지면(리팩터링으로 전부 빠지면) 아래 테스트가 조용히 통과하지 않게 한다."""
    assert _INTEGER_PROPERTIES


@pytest.mark.parametrize(
    ("kind", "node_type", "name"),
    _INTEGER_PROPERTIES,
    ids=[f"{kind}.{name}" for kind, _, name in _INTEGER_PROPERTIES],
)
def test_integer_property_declares_a_bound_the_schema_publishes(
    kind: str, node_type: type, name: str
) -> None:
    declared = field_minimum(node_type, name)  # 선언이 없으면 KeyError로 실패한다
    published = strategy_document_schema()["$defs"][node_type.__name__]["properties"][name]

    assert published["minimum"] == declared, kind


def _time_series(window: int, lag: int) -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode(node_id="source", field_id=_PRICE.field_id, kind="field"),
            TimeSeriesNode(
                node_id="subject",
                operator=TimeSeriesOperator.MEAN,
                input_node_id="source",
                window=window,
                kind="time_series",
                lag=lag,
            ),
        ),
        output_node_id="subject",
    )


def _lag(periods: int) -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode(node_id="source", field_id=_PRICE.field_id, kind="field"),
            UnaryNode(
                node_id="subject",
                operator=UnaryOperator.LAG,
                input_node_id="source",
                kind="unary",
                periods=periods,
            ),
        ),
        output_node_id="subject",
    )


def _codes(graph: FactorGraph) -> set[str]:
    return {issue.code for issue in validate_factor_graph(graph, fields=(_PRICE,)).issues}


def test_validator_accepts_the_declared_minimum_and_rejects_one_below() -> None:
    window = field_minimum(TimeSeriesNode, "window")
    lag = field_minimum(TimeSeriesNode, "lag")
    periods = field_minimum(UnaryNode, "periods")

    # 스키마가 발행한 하한 그대로 만든 노드는 유효하다 — 화면이 만든 기본 노드가 이 모양이다.
    assert validate_factor_graph(_time_series(window, lag), fields=(_PRICE,)).valid
    assert validate_factor_graph(_lag(periods), fields=(_PRICE,)).valid

    assert "factor.graph.time_series_window" in _codes(_time_series(window - 1, lag))
    assert "factor.graph.time_series_window" in _codes(_time_series(window, lag - 1))
    assert "factor.graph.lag_periods" in _codes(_lag(periods - 1))


def test_bound_messages_quote_the_declared_constant() -> None:
    """메시지가 상수를 인용하므로 하한을 바꿔도 문장이 낡지 않는다(`error-messages` 규칙)."""
    window = field_minimum(TimeSeriesNode, "window")
    issues = validate_factor_graph(_time_series(window - 1, 0), fields=(_PRICE,)).issues
    message = next(
        issue.message for issue in issues if issue.code == "factor.graph.time_series_window"
    )

    assert f"window는 {window} 이상" in message


def test_constant_node_value_is_not_treated_as_an_integer_property() -> None:
    """`float` property는 관계 제약(분위수 `lower < upper`)이라 이 하한 계약의 대상이 아니다."""
    assert ConstantNode not in {node_type for _, node_type, _ in _INTEGER_PROPERTIES}


def _property_schema(node_type: type, name: str) -> dict[str, Any]:
    return strategy_document_schema()["$defs"][node_type.__name__]["properties"][name]


_SEEDS: dict[str, object] = json.loads(
    (FIXTURES / "parameter-seeds.json").read_text(encoding="utf-8")
)["seeds"]


def _published_seed(node_type: type, name: str) -> object | None:
    """화면이 이 property에 넣을 값. 없으면 None.

    규칙을 여기서 다시 적지 않는다(2차 리뷰 P3). non-null `default`는 스키마가 그대로 들고 있고,
    `default: null`인 자리의 씨앗은 `parameter-seeds.json` golden이 owner다 — 그 표를
    `tools/export_runtime_schema.py`가 만들고 frontend 테스트가 자기 구현으로 같은 표를 재현한다.
    """
    property_schema = _property_schema(node_type, name)
    default = property_schema.get("default", _UNSET)
    if default is not _UNSET:
        # `default: null` 자리의 씨앗 판정(정수만·nullable 포장 벗기기)은 golden이 owner다.
        return _SEEDS.get(f"{node_type.__name__}.{name}") if default is None else default
    # `default`가 아예 없는 필수 숫자 property(`window`)는 스키마가 발행한 하한이 그대로 씨앗이다.
    bound = property_schema.get("minimum")
    return bound if isinstance(bound, (int, float)) and not isinstance(bound, bool) else None


_UNSET = object()


def _seeded_parameters(definition: OperatorDefinition) -> dict[str, object]:
    node_type = EXPRESSION_NODE_KINDS[definition.kind]
    values: dict[str, object] = {}
    for parameter in definition.params:
        name = parameter.property_name
        seed = _published_seed(node_type, name)
        values[name] = _CATALOG_SAMPLES[name] if seed is None else seed
    return values


def test_every_catalog_parameter_has_a_seed_or_a_sample() -> None:
    """씨앗도 카탈로그 샘플도 없는 파라미터가 생기면 아래 순회 테스트가 조용히 비지 않게 한다."""
    missing = [
        f"{definition.kind}.{definition.operator}.{parameter.property_name}"
        for definition in operator_definitions()
        for parameter in definition.params
        if _published_seed(EXPRESSION_NODE_KINDS[definition.kind], parameter.property_name) is None
        and parameter.property_name not in _CATALOG_SAMPLES
    ]

    assert missing == []


@pytest.mark.parametrize(
    "definition",
    operator_definitions(),
    ids=lambda item: f"{item.kind}.{item.operator}",
)
def test_published_seed_makes_a_valid_graph(definition: OperatorDefinition) -> None:
    """화면이 만드는 모양(입력은 이어 주고 파라미터는 발행된 씨앗) 그대로 검증을 통과한다."""
    node_type = EXPRESSION_NODE_KINDS[definition.kind]
    inputs = [
        item.name
        for item in dataclass_fields(node_type)
        if item.metadata.get("reference") == "node"
    ]
    operator_enum = get_type_hints(node_type)["operator"]
    values: dict[str, object] = {
        "node_id": "subject",
        "kind": definition.kind,
        "operator": operator_enum(definition.operator),
        **_seeded_parameters(definition),
    }
    for index, name in enumerate(inputs):
        values[name] = f"source_{index}"
    sources = tuple(
        FieldNode(node_id=f"source_{index}", field_id=_PRICE.field_id, kind="field")
        for index in range(len(inputs))
    )
    graph = FactorGraph(
        nodes=(*sources, node_type(**values)),  # pyright: ignore[reportCallIssue]  # reason: kind별 생성자가 달라 정적으로 좁힐 수 없다
        output_node_id="subject",
    )

    validation = validate_factor_graph(graph, fields=_FIELDS)

    assert validation.valid, [issue.message for issue in validation.issues]
