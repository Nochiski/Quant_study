"""boolean 팩터 출력을 0/1 숫자 시계열로 올린다 (P2-07, spec D5).

팩터는 종목별 점수라 실행 경계(`_reject_non_numeric_factor_outputs`)가 `numeric_series` 만 받는다.
"20일 이평 돌파" 같은 비교 출력(아이디어 3)은 참·거짓이라 그대로는 점수가 아니다. 사용자에게
`조건 ? 1 : 0` 노드를 직접 쓰게 하는 대신, hydrate 가 canonical 그래프 **끝에** 조건 노드 하나와
상수 둘을 붙여 참 1.0 / 거짓 0.0 / 결측 None 으로 바꾼다.

- 사용자 문서(원문·tree)는 바꾸지 않는다. 붙인 노드는 compile 결과(`StrategySpec`)에만 있고
  `spec_hash` 에 들어간다 — 실행되는 그래프가 곧 해시되는 그래프다.
- 새 노드 종류를 만들지 않는다. 문법(`ConditionalNode`·`ConstantNode`)이 이미 표현하는 계산이라
  평가·계획·추적이 그대로 읽는다. 조건 노드의 출력이 시계열이라는 추론은 `domain/factor`
  `_validation.py` 가 소유한다.
- 붙인 뒤 출력은 숫자 시계열이므로 canonical payload 를 다시 hydrate 해도 또 붙지 않는다(저장소
  무결성 검사가 canonical JSON 을 다시 만들어 비교한다).
- 예약 node_id 를 사용자가 이미 쓴 그래프는 승격하지 않는다. 중복 노드를 만들면 원인과 무관한
  `duplicate_node` 진단이 문서에 없는 자리를 가리키게 된다. 그 경우 출력은 boolean 으로 남고
  validator 가 `strategy.factor.output_type` 으로 이유(예약 id)를 말한다.
"""

from __future__ import annotations

from dataclasses import replace

from strategy_workbench.domain.factor.facade.expression import (
    ConditionalNode,
    ConstantNode,
    FactorGraph,
    NodeValueType,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph

from ._models import FactorSignal, StrategySpec

PROMOTION_NODE_PREFIX = "__promote_"


def promotion_node_ids(factor_id: str) -> tuple[str, str, str]:
    """승격 노드 id 셋: (출력 조건 노드, 참 상수, 거짓 상수). 출력 id 는 `__promote_<factor>`."""
    output = f"{PROMOTION_NODE_PREFIX}{factor_id}"
    return output, f"{output}_one", f"{output}_zero"


def _output_value_type(graph: FactorGraph) -> NodeValueType | None:
    """필드 계약 없이 추론한 출력 타입. boolean 여부는 필드 계약과 무관하다(필드는 숫자·그룹뿐).

    그래프가 구조 오류(순환·없는 입력·중복 id)로 추론되지 않으면 None — validator 가 그 오류를
    먼저 보고하고, 고친 뒤 다시 compile 하면 그때 승격된다.
    """
    validation = validate_factor_graph(graph)
    for contract in validation.node_contracts:
        if contract.node_id == graph.output_node_id:
            return contract.value_type
    return None


def _promote(factor: FactorSignal) -> FactorSignal:
    graph = factor.graph
    if _output_value_type(graph) is not NodeValueType.BOOLEAN_SERIES:
        return factor
    output_id, true_id, false_id = promotion_node_ids(factor.factor_id)
    taken = {node.node_id for node in graph.nodes}
    if taken & {output_id, true_id, false_id}:
        return factor
    promoted = FactorGraph(
        nodes=(
            *graph.nodes,
            ConstantNode(node_id=true_id, value=1.0, kind="constant"),
            ConstantNode(node_id=false_id, value=0.0, kind="constant"),
            ConditionalNode(
                node_id=output_id,
                predicate_node_id=graph.output_node_id,
                true_node_id=true_id,
                false_node_id=false_id,
                kind="conditional",
            ),
        ),
        output_node_id=output_id,
    )
    return replace(factor, graph=promoted)


def promote_boolean_factor_outputs(spec: StrategySpec) -> StrategySpec:
    """boolean 출력 팩터마다 그래프 끝에 0/1 승격 노드를 붙인 spec. 나머지 팩터는 그대로다."""
    factors = tuple(_promote(factor) for factor in spec.factors)
    if factors == spec.factors:
        return spec
    return replace(spec, factors=factors)
