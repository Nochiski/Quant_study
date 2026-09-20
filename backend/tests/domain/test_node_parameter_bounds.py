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
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
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
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph
from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

_PRICE = FieldMetadata(field_id="price.close", unit="KRW", value_type=NodeValueType.NUMERIC_SERIES)


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
