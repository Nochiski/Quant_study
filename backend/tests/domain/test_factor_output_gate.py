"""팩터 출력 타입을 compile 에서 판정한다 (P2-07, spec D5).

boolean 은 0/1 로 승격하고 나머지 비수치 출력은 error 다. 실행 경계
(`_reject_non_numeric_factor_outputs`)는 종목별 점수인 `numeric_series` 만 받는다. 그 판정을 실행
직전이 아니라 compile 에서 내야 "검증 통과 = 실행 가능"이 된다.

- boolean 출력(비교 노드 등)은 hydrate 가 canonical 그래프 **끝에** 조건 노드 하나와 상수 둘을 붙여
  참 1.0 / 거짓 0.0 / 결측 None 으로 바꾼다. 사용자 문서(원문·tree)는 바꾸지 않는다.
- scalar·group 출력은 `strategy.factor.output_type` error 다. group 은 필드 계약을 알아야(어댑터가
  연결돼야) 판정할 수 있다.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any

import pytest

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    PROMOTION_NODE_PREFIX,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ConditionalNode,
    ConstantNode,
    FieldMetadata,
    NodeValueType,
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
    canonical_strategy_payload,
)
from strategy_workbench.domain.strategy.facade.validation import (
    ValidationSeverity,
    validate_strategy,
)

DRAFT = StrategyIdentity("draft", 0)
DAY = date(2026, 1, 2)

_CLOSE = FieldMetadata(field_id="price.close", unit="KRW")
_SECTOR = FieldMetadata(
    field_id="classification.sector", unit="category", value_type=NodeValueType.GROUP_SERIES
)

# 아이디어 3(20일 이평 돌파)의 레시피 산출 형태: 잎 두 개와 비교 출력 (WORKFLOW P2-08).
_BREAKOUT_NODES: list[dict[str, Any]] = [
    {"kind": "field", "node_id": "close", "field_id": "price.close"},
    {
        "kind": "time_series",
        "node_id": "ma20",
        "operator": "mean",
        "input_node_id": "close",
        "window": 20,
    },
    {"kind": "field", "node_id": "close_2", "field_id": "price.close"},
    {
        "kind": "comparison",
        "node_id": "breakout",
        "operator": "gt",
        "left_node_id": "close_2",
        "right_node_id": "ma20",
    },
]


def _document(nodes: list[dict[str, Any]], output: str, *, factor_id: str = "breakout") -> dict:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "title": "출력 타입",
        "factors": [
            {
                "factor_id": factor_id,
                "label": factor_id,
                "direction": "high",
                "weight": 1.0,
                "graph": {"nodes": copy.deepcopy(nodes), "output_node_id": output},
            }
        ],
        "portfolio": {"selection_count": 5, "rebalance": "monthly"},
        "risk": {"max_name_weight": 0.2},
    }


def _hydrate(document: dict) -> StrategySpec:
    hydration = hydrate_strategy_document(document, identity=DRAFT)
    assert hydration.ok and hydration.spec is not None, hydration.issues
    return hydration.spec


def _output_type_issues(spec: StrategySpec, **kwargs: Any) -> list[str]:
    return [
        issue.message
        for issue in validate_strategy(spec, **kwargs).issues
        if issue.code == "strategy.factor.output_type"
    ]


# -- boolean 승격 --------------------------------------------------------------------------------


def test_boolean_output_is_promoted_at_the_end_of_the_canonical_graph() -> None:
    document = _document(_BREAKOUT_NODES, "breakout")
    before = copy.deepcopy(document)

    graph = _hydrate(document).factors[0].graph

    assert document == before, "사용자 문서 tree 는 바뀌지 않는다"
    user_nodes = tuple(node.node_id for node in graph.nodes[: len(_BREAKOUT_NODES)])
    assert user_nodes == ("close", "ma20", "close_2", "breakout"), "사용자 노드 순서·위치 불변"
    promoted = graph.nodes[len(_BREAKOUT_NODES) :]
    assert graph.output_node_id == f"{PROMOTION_NODE_PREFIX}breakout" == "__promote_breakout"
    conditional = next(node for node in promoted if node.node_id == graph.output_node_id)
    assert isinstance(conditional, ConditionalNode)
    assert conditional.predicate_node_id == "breakout"
    constants = {node.node_id: node for node in promoted if isinstance(node, ConstantNode)}
    assert constants[conditional.true_node_id].value == 1.0
    assert constants[conditional.false_node_id].value == 0.0
    assert len(promoted) == 3


def test_promoted_output_is_a_numeric_series_and_the_document_is_valid() -> None:
    spec = _hydrate(_document(_BREAKOUT_NODES, "breakout"))

    validation = validate_strategy(spec, fields=(_CLOSE,))
    contracts = validate_factor_graph(spec.factors[0].graph, fields=(_CLOSE,)).node_contracts

    assert validation.valid, [issue.message for issue in validation.issues]
    assert contracts[-1].node_id == "__promote_breakout"
    assert contracts[-1].value_type is NodeValueType.NUMERIC_SERIES


def test_promoted_output_evaluates_to_one_zero_or_missing() -> None:
    document = _document(
        [
            {"kind": "field", "node_id": "close", "field_id": "price.close"},
            {"kind": "constant", "node_id": "threshold", "value": 100.0},
            {
                "kind": "comparison",
                "node_id": "above",
                "operator": "gt",
                "left_node_id": "close",
                "right_node_id": "threshold",
            },
        ],
        "above",
        factor_id="above",
    )
    promoted = _hydrate(document).factors[0].graph
    observations = tuple(
        FactorObservation(
            as_of=DAY,
            security_id=security_id,
            fields=(FactorFieldValue("price.close", value),),
        )
        for security_id, value in (("a", 150.0), ("b", 50.0), ("c", None))
    )

    evaluation = evaluate_factor_graph(
        promoted, observations=observations, missing=MissingPolicy.KEEP
    )

    assert [value.value for value in evaluation.values] == [1.0, 0.0, None]


def test_promotion_is_idempotent_through_the_canonical_payload() -> None:
    """저장된 canonical payload 를 다시 hydrate 해도 노드가 또 붙지 않는다(저장소 무결성 검사)."""
    spec = _hydrate(_document(_BREAKOUT_NODES, "breakout"))
    payload = canonical_strategy_payload(spec)

    again = _hydrate(json.loads(json.dumps(payload)))

    assert canonical_strategy_json(again) == canonical_strategy_json(spec)


def test_numeric_output_is_not_promoted() -> None:
    nodes = _BREAKOUT_NODES[:2]
    graph = _hydrate(_document(nodes, "ma20", factor_id="ma")).factors[0].graph

    assert graph.output_node_id == "ma20"
    assert not any(node.node_id.startswith(PROMOTION_NODE_PREFIX) for node in graph.nodes)


def test_a_taken_promotion_id_skips_promotion_and_the_gate_explains_why() -> None:
    """예약 id 를 사용자가 이미 썼으면 승격하지 않는다 — 중복 노드를 만들지 않고 이유를 말한다."""
    nodes = [
        *_BREAKOUT_NODES,
        {"kind": "constant", "node_id": "__promote_breakout", "value": 1.0},
    ]
    spec = _hydrate(_document(nodes, "breakout"))

    assert spec.factors[0].graph.output_node_id == "breakout"
    messages = _output_type_issues(spec)
    assert len(messages) == 1
    assert "__promote_breakout" in messages[0]
    assert "boolean_series" in messages[0]


# -- scalar·group 출력 -----------------------------------------------------------------------------


def test_scalar_output_is_a_compile_error_even_without_field_contracts() -> None:
    spec = _hydrate(
        _document([{"kind": "constant", "node_id": "one", "value": 1.0}], "one", factor_id="c")
    )

    issues = [
        issue
        for issue in validate_strategy(spec).issues
        if issue.code == "strategy.factor.output_type"
    ]

    assert len(issues) == 1
    assert issues[0].severity is ValidationSeverity.ERROR
    assert issues[0].path == "factors.0.graph.output_node_id"
    assert issues[0].node_id == "one"
    assert "actual='scalar'" in issues[0].message


def test_group_output_is_an_error_once_field_contracts_are_known() -> None:
    spec = _hydrate(
        _document(
            [{"kind": "field", "node_id": "sector", "field_id": "classification.sector"}],
            "sector",
            factor_id="sector",
        )
    )

    # 계약이 없으면 필드 노드는 숫자로 추론된다 — 어댑터가 없는 컨텍스트는 지금과 같다.
    assert _output_type_issues(spec) == []
    messages = _output_type_issues(spec, fields=(_CLOSE, _SECTOR))
    assert len(messages) == 1
    assert "actual='group_series'" in messages[0]


def test_conditional_with_a_series_predicate_and_constant_branches_is_a_numeric_series() -> None:
    """조건 노드의 값은 종목마다 조건을 따라 갈리므로 가지가 상수여도 시계열이다.

    승격 노드가 이 규칙에 기댄다. 이전 추론은 가지 타입(scalar)을 그대로 물려줘, 사용자가 직접
    쓴 `조건 ? 1 : 0` 팩터가 실행 경계에서 "scalar 출력"으로 거부됐다.
    """
    graph = (
        _hydrate(
            _document(
                [
                    *_BREAKOUT_NODES,
                    {"kind": "constant", "node_id": "yes", "value": 2.0},
                    {"kind": "constant", "node_id": "no", "value": -1.0},
                    {
                        "kind": "conditional",
                        "node_id": "pick",
                        "predicate_node_id": "breakout",
                        "true_node_id": "yes",
                        "false_node_id": "no",
                    },
                ],
                "pick",
            )
        )
        .factors[0]
        .graph
    )

    contracts = {
        contract.node_id: contract
        for contract in validate_factor_graph(graph, fields=(_CLOSE,)).node_contracts
    }

    assert contracts["pick"].value_type is NodeValueType.NUMERIC_SERIES
    assert graph.output_node_id == "pick", "숫자 출력이라 승격하지 않는다"


@pytest.mark.parametrize("branch", ["yes", "no"])
def test_a_scalar_conditional_without_a_series_predicate_stays_scalar(branch: str) -> None:
    """조건이 boolean 시계열이 아니면(오류 문서) 시계열로 올리지 않는다 — 오진을 덧씌우지 않는다."""
    graph = (
        _hydrate(
            _document(
                [
                    {"kind": "constant", "node_id": "yes", "value": 2.0},
                    {"kind": "constant", "node_id": "no", "value": -1.0},
                    {
                        "kind": "conditional",
                        "node_id": "pick",
                        "predicate_node_id": branch,
                        "true_node_id": "yes",
                        "false_node_id": "no",
                    },
                ],
                "pick",
            )
        )
        .factors[0]
        .graph
    )

    validation = validate_factor_graph(graph)
    contracts = {contract.node_id: contract for contract in validation.node_contracts}

    assert contracts["pick"].value_type is NodeValueType.SCALAR
    assert "factor.graph.predicate_type" in {issue.code for issue in validation.issues}
