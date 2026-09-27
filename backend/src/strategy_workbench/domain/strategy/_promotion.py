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
- `PROMOTION_NODE_PREFIX` 는 예약 네임스페이스다. 문서가 이 접두사로 시작하는 node_id 를 쓰면
  compile 이 `strategy.factor.reserved_node_id` error 로 거절한다(`_validation.py`, 리뷰 #232
  DEFECT-232-01). 그래서 compile 을 통과한 spec 에서 이 접두사 노드는 승격이 붙인 것뿐이다. 문서를
  거치지 않은 spec(JSON spec API)에서도 중복 노드를 만들지 않도록, 예약 id 가 이미 있는 그래프는
  승격하지 않는다(출력은 boolean 으로 남고 validator 가 `strategy.factor.output_type` 으로 말한다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from strategy_workbench.domain.factor.facade.expression import (
    ConditionalNode,
    ConstantNode,
    FactorGraph,
    NodeValueType,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph

from ._models import FactorSignal, StrategySpec

PROMOTION_NODE_PREFIX = "__promote_"


def is_reserved_node_id(node_id: str) -> bool:
    """승격이 쓰는 예약 네임스페이스(`PROMOTION_NODE_PREFIX`)의 node_id 인가."""
    return node_id.startswith(PROMOTION_NODE_PREFIX)


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


def _promote_graph(graph: FactorGraph, factor_id: str) -> FactorGraph:
    if _output_value_type(graph) is not NodeValueType.BOOLEAN_SERIES:
        return graph
    output_id, true_id, false_id = promotion_node_ids(factor_id)
    taken = {node.node_id for node in graph.nodes}
    if taken & {output_id, true_id, false_id}:
        return graph
    return FactorGraph(
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


def _promote(factor: FactorSignal) -> FactorSignal:
    graph = _promote_graph(factor.graph, factor.factor_id)
    return factor if graph is factor.graph else replace(factor, graph=graph)


def promote_boolean_factor_outputs(spec: StrategySpec) -> StrategySpec:
    """boolean 출력 팩터마다 그래프 끝에 0/1 승격 노드를 붙인 spec. 나머지 팩터는 그대로다."""
    factors = tuple(_promote(factor) for factor in spec.factors)
    if factors == spec.factors:
        return spec
    return replace(spec, factors=factors)


def _demote_graph(graph: FactorGraph, factor_id: str) -> FactorGraph | None:
    """승격을 걷어 낸 사용자 그래프. `graph` 가 `factor_id` 로 승격된 그래프가 아니면 None."""
    output_id, true_id, false_id = promotion_node_ids(factor_id)
    if graph.output_node_id != output_id or len(graph.nodes) < 3:
        return None
    *authored, true_node, false_node, condition = graph.nodes
    if not (
        isinstance(true_node, ConstantNode)
        and true_node.node_id == true_id
        and isinstance(false_node, ConstantNode)
        and false_node.node_id == false_id
        and isinstance(condition, ConditionalNode)
        and condition.node_id == output_id
    ):
        return None
    candidate = FactorGraph(nodes=tuple(authored), output_node_id=condition.predicate_node_id)
    # 정확한 역함수만 인정한다: 걷어 낸 그래프를 다시 승격하면 원래 그래프가 나와야 한다.
    return candidate if _promote_graph(candidate, factor_id) == graph else None


def _demote(factor: FactorSignal) -> FactorSignal:
    graph = _demote_graph(factor.graph, factor.factor_id)
    return factor if graph is None else replace(factor, graph=graph)


def demote_boolean_factor_outputs(spec: StrategySpec) -> StrategySpec:
    """`promote_boolean_factor_outputs` 가 붙인 노드를 걷어 낸 spec(사용자가 쓴 그래프).

    표시 전용이다. 의미 diff 가 사용자가 쓴 변경만 말하게 할 때 쓴다(BACKLOG-014): 위치 비교는
    사용자 노드 하나를 더할 때 끝에 붙은 승격 노드 셋을 "바뀐 것"으로 보인다. 승격은 사용자 그래프의
    함수라 걷어 낸 두 spec 이 같으면 원래 spec(과 `spec_hash`)도 같다. 실행·해시에는 쓰지 않는다.
    """
    factors = tuple(_demote(factor) for factor in spec.factors)
    if factors == spec.factors:
        return spec
    return replace(spec, factors=factors)


class SynthesizedNodeRole(StrEnum):
    """compile 이 붙인 노드의 역할.

    `promoted_output` 은 참/거짓 출력을 1/0 점수로 바꾼 조건 노드(그래프 출력)이고, 그 predicate 가
    사용자가 쓴 원래 출력이다. `promotion_constant` 는 거기 딸린 참 1 / 거짓 0 상수다.
    """

    PROMOTED_OUTPUT = "promoted_output"
    PROMOTION_CONSTANT = "promotion_constant"


@dataclass(frozen=True)
class SynthesizedNode:
    """문서에 줄이 없는, compile 이 붙인 노드 표식(P3-01, Phase 2 감사 #13).

    화면은 이 표식으로 붙인 노드를 가른다. 승격 노드 이름 규칙(`PROMOTION_NODE_PREFIX`)을 화면이
    복제하지 않게 하는 wire 계약이다. `origin` 은 붙인 단계이고 지금은 boolean 출력 승격뿐이다.
    """

    node_id: str
    origin: Literal["promotion"]
    role: SynthesizedNodeRole


def synthesized_factor_nodes(graph: FactorGraph) -> tuple[SynthesizedNode, ...]:
    """컴파일된 팩터 그래프에서 compile 이 붙인 노드(그래프 순서). 사용자가 쓴 그래프면 빈 tuple.

    그래프만 보고 판정한다(실행 계획 설명 요청은 팩터 id 를 들고 오지 않는다): 출력 id 에서 팩터
    id 를 읽고, 걷어 낸 그래프를 다시 승격하면 원래 그래프가 나올 때만 승격으로 인정한다.

    그래프만으로는 사용자가 승격 모양을 예약 id 로 그대로 쓴 그래프와 구분할 수 없다. 그런 문서는
    compile 이 예약 접두사로 거절하므로(`strategy.factor.reserved_node_id`) compile 된 spec 에는
    나오지 않는다.
    """
    if not is_reserved_node_id(graph.output_node_id):
        return ()
    factor_id = graph.output_node_id.removeprefix(PROMOTION_NODE_PREFIX)
    if _demote_graph(graph, factor_id) is None:
        return ()
    output_id, true_id, false_id = promotion_node_ids(factor_id)
    return (
        SynthesizedNode(true_id, "promotion", SynthesizedNodeRole.PROMOTION_CONSTANT),
        SynthesizedNode(false_id, "promotion", SynthesizedNodeRole.PROMOTION_CONSTANT),
        SynthesizedNode(output_id, "promotion", SynthesizedNodeRole.PROMOTED_OUTPUT),
    )
