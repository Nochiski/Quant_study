"""같은 단위의 나눗셈은 무차원이다 — 단위 비교는 약분한 차원으로 한다(#668 IT-01).

단위 표기(`(KRW/KRW)` 같은 이어 붙인 문자열)는 실행 계획 `output_unit` 과 `plan_hash`(연속 계좌
후보 신원)에 실리므로 바꾸지 않는다. 더하기·빼기와 조건 분기, 전략의 정규화 없는 합성 경고가
단위를 비교할 때만 약분한 차원을 쓴다. 그래서 수익률 `가격 ÷ n일 전 가격 − 1` 이 단위 오류로
막히지 않는다.
"""

from __future__ import annotations

import pytest

from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    NodeValueType,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.planning import compile_factor_plan
from strategy_workbench.domain.factor.facade.validation import (
    same_unit,
    unit_dimensions,
    validate_factor_graph,
)

_PRICE = FieldMetadata(field_id="close", unit="KRW", value_type=NodeValueType.NUMERIC_SERIES)
_VOLUME = FieldMetadata(field_id="volume", unit="shares", value_type=NodeValueType.NUMERIC_SERIES)


@pytest.mark.parametrize(
    ("unit", "dimensions"),
    [
        ("KRW", (("KRW", 1),)),
        ("1", ()),
        ("(KRW/KRW)", ()),
        ("(1*1)", ()),
        ("((KRW*shares)/shares)", (("KRW", 1),)),
        ("(KRW/shares)", (("KRW", 1), ("shares", -1))),
        ("KRW/shares*shares", (("KRW", 1),)),  # 괄호 없으면 왼쪽부터
        ("(KRW", (("(KRW", 1),)),  # 괄호가 맞지 않으면 문자열 전체가 기본 단위 하나
        ("unknown", None),
        ("(KRW/unknown)", None),
    ],
)
def test_unit_dimensions_cancel_equal_units(
    unit: str, dimensions: tuple[tuple[str, int], ...] | None
) -> None:
    assert unit_dimensions(unit) == dimensions


def test_same_unit_skips_unknown_and_compares_dimensions() -> None:
    assert same_unit("(KRW/KRW)", "1")
    assert same_unit("unknown", "KRW")
    assert not same_unit("KRW", "shares")
    assert not same_unit("(KRW/shares)", "KRW")


def _return_minus_one(*, right_field: str | None = None) -> FactorGraph:
    """`close ÷ lag(close, 5) − 1`(또는 오른쪽을 다른 필드로 바꾼 대조군)."""
    nodes = [
        FieldNode(node_id="close", field_id="close", kind="field"),
        UnaryNode(
            node_id="close_5",
            operator=UnaryOperator.LAG,
            input_node_id="close",
            periods=5,
            kind="unary",
        ),
        BinaryNode(
            node_id="ratio",
            operator=BinaryOperator.DIVIDE,
            left_node_id="close",
            right_node_id="close_5",
            kind="binary",
        ),
    ]
    if right_field is None:
        nodes.append(ConstantNode(node_id="one", value=1.0, kind="constant"))
        right = "one"
    else:
        nodes.append(FieldNode(node_id="other", field_id=right_field, kind="field"))
        right = "other"
    nodes.append(
        BinaryNode(
            node_id="ret",
            operator=BinaryOperator.SUBTRACT,
            left_node_id="ratio",
            right_node_id=right,
            kind="binary",
        )
    )
    return FactorGraph(nodes=tuple(nodes), output_node_id="ret")


def test_price_ratio_minus_one_is_valid() -> None:
    """P1 실험(MM-V0)에서 `strategy.expression.unit_mismatch` 로 거절되던 수익률 식이 통과한다."""
    validation = validate_factor_graph(_return_minus_one(), fields=(_PRICE,))

    assert validation.valid, [issue.message for issue in validation.issues]


def test_mixing_different_units_is_still_rejected() -> None:
    """대조군: 약분해도 단위가 남는 값(`원 ÷ 원 − 주`)은 여전히 단위 오류다."""
    validation = validate_factor_graph(
        _return_minus_one(right_field="volume"), fields=(_PRICE, _VOLUME)
    )

    assert [issue.code for issue in validation.issues] == ["factor.graph.unit_mismatch"]


def test_branch_units_compare_by_dimensions() -> None:
    """조건 분기 두 가지가 `(KRW/KRW)` 와 상수 `1` 이어도 단위가 같다."""
    graph = FactorGraph(
        nodes=(
            *_return_minus_one().nodes[:3],
            ConstantNode(node_id="one", value=1.0, kind="constant"),
            ComparisonNode(
                node_id="above",
                operator=FactorComparisonOperator.GREATER_THAN,
                left_node_id="ratio",
                right_node_id="one",
                kind="comparison",
            ),
            ConditionalNode(
                node_id="capped",
                predicate_node_id="above",
                true_node_id="one",
                false_node_id="ratio",
                kind="conditional",
            ),
        ),
        output_node_id="capped",
    )

    validation = validate_factor_graph(graph, fields=(_PRICE,))

    assert validation.valid, [issue.message for issue in validation.issues]


def test_plan_keeps_the_unit_notation() -> None:
    """표기는 그대로다 — 실행 계획의 `output_unit` 이 바뀌면 `plan_hash`(후보 신원)가 바뀐다."""
    from strategy_workbench.domain.factor.facade.expression import MissingPolicy

    plan = compile_factor_plan(
        _return_minus_one(), registry_version="test", missing=MissingPolicy.DROP, fields=(_PRICE,)
    )

    units = {step.node_id: step.output_unit for step in plan.steps}
    assert units["ratio"] == "(KRW/KRW)"
    assert units["ret"] == "(KRW/KRW)"


def test_boolean_and_numeric_branches_are_still_rejected() -> None:
    """대조군: 참거짓 가지와 숫자 가지는 열 하나에 담을 수 없어 여전히 막는다."""
    graph = FactorGraph(
        nodes=(
            FieldNode(node_id="close", field_id="close", kind="field"),
            ConstantNode(node_id="one", value=1.0, kind="constant"),
            ComparisonNode(
                node_id="above",
                operator=FactorComparisonOperator.GREATER_THAN,
                left_node_id="close",
                right_node_id="one",
                kind="comparison",
            ),
            ConditionalNode(
                node_id="out",
                predicate_node_id="above",
                true_node_id="above",
                false_node_id="close",
                kind="conditional",
            ),
        ),
        output_node_id="out",
    )

    validation = validate_factor_graph(graph, fields=(_PRICE,))

    assert "factor.graph.branch_type" in {issue.code for issue in validation.issues}
