from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    ExpressionNode,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    GroupNode,
    GroupOperator,
    NodeValueType,
    ParameterNode,
    TimeSeriesNode,
    UnaryNode,
    UnaryOperator,
    field_minimum,
)

# 출력이 무차원인 횡단면 연산(BACKLOG-003). `demean`·`winsorize` 는 값의 단위를 그대로 둔다.
_DIMENSIONLESS_SECTIONS = frozenset({CrossSectionalOperator.RANK, CrossSectionalOperator.ZSCORE})
# 출력이 무차원인 그룹 연산(BACKLOG-015). 그룹 안 순위는 횡단면 순위와 같은 백분위 공식
# (`cross_sectional_rank`)이다. `neutralize` 는 그룹 평균을 빼므로 입력 단위를 그대로 둔다.
_DIMENSIONLESS_GROUP_OPERATIONS = frozenset({GroupOperator.RANK})

# 정수 파라미터의 하한은 노드 dataclass 옆에 한 번만 선언한다(`_nodes.minimum`). runtime schema가
# 같은 값을 JSON Schema `minimum`으로 발행하므로 화면이 만든 기본값과 검증기가
# 어긋나지 않는다(P1-04).
LAG_PERIODS_MINIMUM = field_minimum(UnaryNode, "periods")
TIME_SERIES_WINDOW_MINIMUM = field_minimum(TimeSeriesNode, "window")
TIME_SERIES_LAG_MINIMUM = field_minimum(TimeSeriesNode, "lag")


class FactorValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class FactorValidationIssue:
    code: str
    node_id: str | None
    path: str
    message: str
    severity: FactorValidationSeverity = FactorValidationSeverity.ERROR


@dataclass(frozen=True)
class NodeContract:
    node_id: str
    value_type: NodeValueType
    unit: str
    minimum_history_sessions: int


@dataclass(frozen=True)
class FactorGraphValidation:
    valid: bool
    issues: tuple[FactorValidationIssue, ...]
    node_contracts: tuple[NodeContract, ...]
    minimum_history_sessions: int
    required_field_ids: tuple[str, ...]


# 이 모듈이 낼 수 있는 그래프 진단 코드 전부. 전략 문서 쪽 레지스트리(`EXPRESSION_CODES`)는 이
# 집합을 `strategy.expression.*`로 옮긴 것과 같아야 하고, 그 불변식을 테스트가 지킨다(P1-05).
# 코드를 여기 적지 않고 새로 만들면 전략 validator가 alias를 찾지 못해 진단이 조용히 새
# 네임스페이스로 샌다 — 그래서 목록이 아니라 게이트다.
FACTOR_GRAPH_CODES: frozenset[str] = frozenset(
    {
        "factor.graph.branch_type",
        "factor.graph.branch_unit",
        "factor.graph.cycle",
        "factor.graph.duplicate_node",
        "factor.graph.field_missing",
        "factor.graph.group_field_missing",
        "factor.graph.group_field_type",
        "factor.graph.input_missing",
        "factor.graph.input_type",
        "factor.graph.insufficient_history",
        "factor.graph.lag_periods",
        "factor.graph.operand_type",
        "factor.graph.output_missing",
        "factor.graph.parameter_missing",
        "factor.graph.predicate_type",
        "factor.graph.time_series_window",
        "factor.graph.unit_mismatch",
        "factor.graph.winsor_bounds",
    }
)


def _issue(code: str, node_id: str | None, path: str, message: str) -> FactorValidationIssue:
    if code not in FACTOR_GRAPH_CODES:
        raise ValueError(
            "factor graph diagnostic code has no owner — add it to FACTOR_GRAPH_CODES and to the "
            f"strategy alias table: code={code!r} path={path!r} node_id={node_id!r}"
        )
    return FactorValidationIssue(code=code, node_id=node_id, path=path, message=message)


def unavailable_field_message(field_id: str, reason: str) -> str:
    """연결된 어댑터가 선언했지만 주지 않는 필드의 진단 문장(#316).

    그래프 안(`factor.graph.field_missing`)과 밖(`strategy.field.missing`)이 같은 문장을 쓴다.
    사유(원천을 뺀 `catalog_*` 사유와 조치 `CATALOG_REBUILD`, 원장에 없는 필드의 사유)는 어댑터가
    완성한 문장을 그대로 싣고, 조치를 여기서 다시 적지 않는다.
    """
    return f"연결된 데이터가 이 필드를 주지 않습니다 — {reason}: field_id={field_id!r}"


def node_dependencies(node: ExpressionNode) -> tuple[str, ...]:
    if isinstance(node, (UnaryNode, TimeSeriesNode, CrossSectionalNode, GroupNode)):
        return (node.input_node_id,)
    if isinstance(node, (BinaryNode, ComparisonNode)):
        return (node.left_node_id, node.right_node_id)
    if isinstance(node, ConditionalNode):
        return (node.predicate_node_id, node.true_node_id, node.false_node_id)
    return ()


def required_field_ids(graph: FactorGraph) -> tuple[str, ...]:
    """Return every observation field the graph reads, including grouping keys."""
    field_ids: set[str] = set()
    for node in graph.nodes:
        if isinstance(node, FieldNode):
            field_ids.add(node.field_id)
        elif isinstance(node, GroupNode):
            field_ids.add(node.group_field_id)
    return tuple(sorted(field_ids))


def validate_factor_graph(
    graph: FactorGraph,
    *,
    fields: tuple[FieldMetadata, ...] = (),
    parameter_ids: tuple[str, ...] = (),
    require_field_metadata: bool = False,
    unavailable_fields: Mapping[str, str] | None = None,
) -> FactorGraphValidation:
    """`unavailable_fields` 는 어댑터가 선언했지만 주지 않는 필드 → 사유다. 없는 필드가 그 안에
    있으면 진단이 "계약을 찾을 수 없다" 대신 그 사유(조치 포함)를 싣는다(#316)."""
    issues: list[FactorValidationIssue] = []
    nodes = {node.node_id: node for node in graph.nodes}
    # 중복 id를 쓴 자리마다(첫 자리는 빼고) 진단을 단다: 그래프 카드가 어느 노드를 고쳐야 하는지
    # node_id로 집어 하이라이트한다(P1-05). 한 줄짜리 "중복될 수 없습니다"는 어느 노드인지 말하지
    # 못해 사용자가 목록을 눈으로 훑어야 했다.
    seen_node_ids: set[str] = set()
    for index, node in enumerate(graph.nodes):
        if node.node_id in seen_node_ids:
            issues.append(
                _issue(
                    "factor.graph.duplicate_node",
                    node.node_id,
                    f"nodes.{index}",
                    "앞에서 이미 쓴 node_id입니다. 다른 이름을 붙여 주세요 — "
                    f"node_id={node.node_id!r}",
                )
            )
        seen_node_ids.add(node.node_id)
    if graph.output_node_id not in nodes:
        issues.append(
            _issue(
                "factor.graph.output_missing",
                graph.output_node_id,
                "output_node_id",
                f"출력 노드를 찾을 수 없습니다: node_id={graph.output_node_id!r}",
            )
        )

    field_by_id = {field.field_id: field for field in fields}
    known_parameters = set(parameter_ids)
    for index, node in enumerate(graph.nodes):
        path = f"nodes.{index}"
        for dependency in node_dependencies(node):
            if dependency not in nodes:
                issues.append(
                    _issue(
                        "factor.graph.input_missing",
                        node.node_id,
                        path,
                        "입력 노드를 찾을 수 없습니다: "
                        f"node_id={node.node_id!r} input={dependency!r}",
                    )
                )
        if (
            isinstance(node, FieldNode)
            and (fields or require_field_metadata)
            and node.field_id not in field_by_id
        ):
            reason = (unavailable_fields or {}).get(node.field_id)
            issues.append(
                _issue(
                    "factor.graph.field_missing",
                    node.node_id,
                    path,
                    f"필드 계약을 찾을 수 없습니다: field_id={node.field_id!r}"
                    if reason is None
                    else unavailable_field_message(node.field_id, reason),
                )
            )
        elif isinstance(node, ParameterNode) and node.parameter_id not in known_parameters:
            issues.append(
                _issue(
                    "factor.graph.parameter_missing",
                    node.node_id,
                    path,
                    f"파라미터를 찾을 수 없습니다: parameter_id={node.parameter_id!r}",
                )
            )
        elif isinstance(node, UnaryNode):
            if node.operator is UnaryOperator.LAG and (
                node.periods is None or node.periods < LAG_PERIODS_MINIMUM
            ):
                issues.append(
                    _issue(
                        "factor.graph.lag_periods",
                        node.node_id,
                        path,
                        f"lag 기간은 {LAG_PERIODS_MINIMUM} 이상이어야 합니다: "
                        f"periods={node.periods}",
                    )
                )
        elif isinstance(node, TimeSeriesNode):
            if node.window < TIME_SERIES_WINDOW_MINIMUM or node.lag < TIME_SERIES_LAG_MINIMUM:
                issues.append(
                    _issue(
                        "factor.graph.time_series_window",
                        node.node_id,
                        path,
                        f"window는 {TIME_SERIES_WINDOW_MINIMUM} 이상이고 "
                        f"lag는 {TIME_SERIES_LAG_MINIMUM} 이상이어야 합니다: "
                        f"window={node.window} lag={node.lag}",
                    )
                )
        elif isinstance(node, CrossSectionalNode):
            if not 0 <= node.lower_quantile < node.upper_quantile <= 1:
                issues.append(
                    _issue(
                        "factor.graph.winsor_bounds",
                        node.node_id,
                        path,
                        "winsor 분위수는 0 <= lower < upper <= 1이어야 합니다: "
                        f"lower={node.lower_quantile} upper={node.upper_quantile}",
                    )
                )

    index_by_node_id = {node.node_id: index for index, node in enumerate(graph.nodes)}
    for cycle in _cycles(nodes):
        chain = _cycle_chain(cycle, nodes)
        for node_id in cycle:
            issues.append(
                _issue(
                    "factor.graph.cycle",
                    node_id,
                    f"nodes.{index_by_node_id[node_id]}",
                    "이 노드가 순환 참조에 묶여 있어 값을 계산할 수 없습니다. 고리 중 한 곳의 "
                    f"입력을 끊어 주세요 — {chain}",
                )
            )

    contracts: dict[str, NodeContract] = {}
    if not any(
        issue.code
        in {
            "factor.graph.duplicate_node",
            "factor.graph.input_missing",
            "factor.graph.cycle",
        }
        for issue in issues
    ):

        def infer(node_id: str) -> None:
            if node_id in contracts:
                return
            node = nodes[node_id]
            for dependency in node_dependencies(node):
                infer(dependency)
            contract, contract_issues = _infer_contract(
                node,
                contracts,
                field_by_id,
                require_field_metadata=require_field_metadata,
            )
            contracts[node.node_id] = contract
            issues.extend(contract_issues)

        for node in graph.nodes:
            infer(node.node_id)

    output_contract = contracts.get(graph.output_node_id)
    minimum_history = output_contract.minimum_history_sessions if output_contract else 0
    required_fields = required_field_ids(graph)
    for field_id in required_fields:
        metadata = field_by_id.get(field_id)
        if (
            metadata is not None
            and metadata.available_history_sessions is not None
            and metadata.available_history_sessions < minimum_history
        ):
            issues.append(
                _issue(
                    "factor.graph.insufficient_history",
                    None,
                    "fields",
                    "필드 이력이 팩터 최소 이력보다 짧습니다: "
                    f"field_id={field_id!r} available={metadata.available_history_sessions} "
                    f"required={minimum_history}",
                )
            )
    return FactorGraphValidation(
        valid=not any(issue.severity is FactorValidationSeverity.ERROR for issue in issues),
        issues=tuple(issues),
        node_contracts=tuple(
            contracts[node.node_id] for node in graph.nodes if node.node_id in contracts
        ),
        minimum_history_sessions=minimum_history,
        required_field_ids=required_fields,
    )


def _cycles(nodes: dict[str, ExpressionNode]) -> tuple[tuple[str, ...], ...]:
    """순환에 묶인 node_id 묶음들. 한 묶음은 서로를 물고 도는 노드 전부다.

    강결합 요소(SCC)를 쓴다. DFS back-edge 한 번으로 순환 하나를 적는 방식은 **이미 끝난 노드를
    통해서만 닿는 순환을 놓쳐서**, 그 노드 카드에 배지가 붙지 않았다(1차 리뷰 P3-6 실측:
    `1→2, 1→4, 2→3, 3→1, 4→2`에서 노드 `4`가 빠졌다). 진단이 "이 노드가 고리에 묶였다"고 말하려면
    묶인 노드를 하나도 빠뜨리면 안 된다.

    한 묶음 안의 순서는 문서 순서(`nodes` 삽입 순서)라 같은 그래프면 항상 같은 결과가 나온다.
    자기 자신을 가리키는 노드도 순환이다.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    order = {node_id: position for position, node_id in enumerate(nodes)}
    counter = 0
    groups: list[tuple[str, ...]] = []

    def strongconnect(node_id: str) -> None:
        nonlocal counter
        index[node_id] = low[node_id] = counter
        counter += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for dependency in node_dependencies(nodes[node_id]):
            if dependency not in nodes:
                continue
            if dependency not in index:
                strongconnect(dependency)
                low[node_id] = min(low[node_id], low[dependency])
            elif dependency in on_stack:
                low[node_id] = min(low[node_id], index[dependency])
        if low[node_id] != index[node_id]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.discard(member)
            component.append(member)
            if member == node_id:
                break
        self_loop = node_id in node_dependencies(nodes[node_id])
        if len(component) > 1 or self_loop:
            groups.append(tuple(sorted(component, key=lambda member: order[member])))

    for node_id in nodes:
        if node_id not in index:
            strongconnect(node_id)
    return tuple(sorted(groups, key=lambda group: order[group[0]]))


def _cycle_chain(group: tuple[str, ...], nodes: dict[str, ExpressionNode]) -> str:
    """고리를 사람이 읽을 한 줄로.

    묶음 안에서 각 노드의 다음이 하나뿐이면 진짜 경로라 화살표로 잇는다(흔한 단순 고리). 갈래가
    있으면 화살표가 없는 경로를 있는 것처럼 보이게 하므로 묶인 노드 목록만 보인다.
    """
    members = set(group)
    # 같은 노드를 두 입력으로 받는 경우(`a + a`)가 있으므로 중복은 걷어낸다 — 갈래가 아니다.
    successors = {
        member: list(
            dict.fromkeys(
                dependency
                for dependency in node_dependencies(nodes[member])
                if dependency in members
            )
        )
        for member in group
    }
    if all(len(nexts) == 1 for nexts in successors.values()):
        walk = [group[0]]
        while True:
            following = successors[walk[-1]][0]
            if following == walk[0]:
                break
            walk.append(following)
        return "cycle=" + " → ".join((*walk, walk[0]))
    # 갈래가 있으면 경로가 아니라 묶인 노드 목록이다. `키=값` 한 쌍이 되도록 키를 따로 쓴다
    # (`error-messages.md`) — `cycle=nodes=[...]` 는 값 안에 `=` 가 또 들어간다.
    return "cycle_nodes=[" + ", ".join(group) + "]"


def _infer_contract(
    node: ExpressionNode,
    contracts: dict[str, NodeContract],
    fields: dict[str, FieldMetadata],
    *,
    require_field_metadata: bool,
) -> tuple[NodeContract, tuple[FactorValidationIssue, ...]]:
    dependencies = [contracts[dependency] for dependency in node_dependencies(node)]
    issues: list[FactorValidationIssue] = []
    if isinstance(node, FieldNode):
        metadata = fields.get(node.field_id)
        value_type = metadata.value_type if metadata else NodeValueType.NUMERIC_SERIES
        unit = metadata.unit if metadata else "unknown"
        history = 1
    elif isinstance(node, (ConstantNode, ParameterNode)):
        value_type, unit, history = NodeValueType.SCALAR, "1", 0
    elif isinstance(node, BinaryNode):
        left, right = dependencies
        for side, operand in (("left", left), ("right", right)):
            if operand.value_type not in {
                NodeValueType.NUMERIC_SERIES,
                NodeValueType.SCALAR,
            }:
                issues.append(
                    _issue(
                        "factor.graph.operand_type",
                        node.node_id,
                        node.node_id,
                        "산술 입력은 숫자여야 합니다: "
                        f"side={side} actual={operand.value_type.value}",
                    )
                )
        value_type = (
            NodeValueType.NUMERIC_SERIES
            if NodeValueType.NUMERIC_SERIES in (left.value_type, right.value_type)
            else NodeValueType.SCALAR
        )
        history = max(left.minimum_history_sessions, right.minimum_history_sessions)
        if node.operator in (BinaryOperator.ADD, BinaryOperator.SUBTRACT):
            unit = left.unit
            if left.unit != "unknown" and right.unit != "unknown" and left.unit != right.unit:
                issues.append(
                    _issue(
                        "factor.graph.unit_mismatch",
                        node.node_id,
                        node.node_id,
                        f"더하기/빼기 단위가 다릅니다: left={left.unit!r} right={right.unit!r}",
                    )
                )
        else:
            symbol = "*" if node.operator is BinaryOperator.MULTIPLY else "/"
            unit = f"({left.unit}{symbol}{right.unit})"
    elif isinstance(node, ComparisonNode):
        left, right = dependencies
        for side, operand in (("left", left), ("right", right)):
            if operand.value_type not in {
                NodeValueType.NUMERIC_SERIES,
                NodeValueType.SCALAR,
            }:
                issues.append(
                    _issue(
                        "factor.graph.operand_type",
                        node.node_id,
                        node.node_id,
                        "비교 입력은 숫자여야 합니다: "
                        f"side={side} actual={operand.value_type.value}",
                    )
                )
        value_type = NodeValueType.BOOLEAN_SERIES
        unit = "bool"
        history = max(left.minimum_history_sessions, right.minimum_history_sessions)
    elif isinstance(node, ConditionalNode):
        predicate, true_value, false_value = dependencies
        if predicate.value_type is not NodeValueType.BOOLEAN_SERIES:
            issues.append(
                _issue(
                    "factor.graph.predicate_type",
                    node.node_id,
                    node.node_id,
                    f"조건 노드는 boolean이어야 합니다: actual={predicate.value_type.value}",
                )
            )
        if true_value.value_type is not false_value.value_type:
            issues.append(
                _issue(
                    "factor.graph.branch_type",
                    node.node_id,
                    node.node_id,
                    "조건 분기의 출력 타입이 다릅니다: "
                    f"true={true_value.value_type.value} false={false_value.value_type.value}",
                )
            )
        if (
            true_value.unit != "unknown"
            and false_value.unit != "unknown"
            and true_value.unit != false_value.unit
        ):
            issues.append(
                _issue(
                    "factor.graph.branch_unit",
                    node.node_id,
                    node.node_id,
                    "조건 분기의 출력 단위가 다릅니다: "
                    f"true={true_value.unit!r} false={false_value.unit!r}",
                )
            )
        value_type = true_value.value_type
        if (
            value_type is NodeValueType.SCALAR
            and false_value.value_type is NodeValueType.SCALAR
            and predicate.value_type is NodeValueType.BOOLEAN_SERIES
        ):
            # 가지가 상수여도 조건이 종목·날짜마다 갈리므로 값은 시계열이다(P2-07). boolean
            # 출력 승격(`domain/strategy/_promotion.py`)이 이 규칙으로 `조건 ? 1 : 0` 을 숫자
            # 점수로 만든다.
            value_type = NodeValueType.NUMERIC_SERIES
        unit = true_value.unit
        history = max(item.minimum_history_sessions for item in dependencies)
    else:
        source = dependencies[0]
        value_type, unit = source.value_type, source.unit
        history = source.minimum_history_sessions
        if source.value_type not in {
            NodeValueType.NUMERIC_SERIES,
            NodeValueType.SCALAR,
        }:
            issues.append(
                _issue(
                    "factor.graph.input_type",
                    node.node_id,
                    node.node_id,
                    f"변환 입력은 숫자여야 합니다: actual={source.value_type.value}",
                )
            )
        if isinstance(node, TimeSeriesNode):
            history += node.window - 1 + node.lag
        elif isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
            history += node.periods or 0
        if isinstance(node, (TimeSeriesNode, CrossSectionalNode, GroupNode)) or (
            isinstance(node, UnaryNode) and node.operator is not UnaryOperator.NEGATE
        ):
            value_type = NodeValueType.NUMERIC_SERIES
        if isinstance(node, CrossSectionalNode) and node.operator in _DIMENSIONLESS_SECTIONS:
            # 순위·z-score 는 입력 단위를 지운다(BACKLOG-003). 카탈로그 `UnitRule.DIMENSIONLESS`
            # 와 같은 규칙이며 `test_factor_operators.py` 가 둘을 대조한다.
            unit = "1"
        if isinstance(node, GroupNode) and node.operator in _DIMENSIONLESS_GROUP_OPERATIONS:
            # 카탈로그 `UnitRule.DIMENSIONLESS` 와 같은 규칙이다(`test_factor_operators.py` 대조).
            unit = "1"
        if isinstance(node, GroupNode):
            group_metadata = fields.get(node.group_field_id)
            if (fields or require_field_metadata) and group_metadata is None:
                issues.append(
                    _issue(
                        "factor.graph.group_field_missing",
                        node.node_id,
                        node.node_id,
                        f"그룹 필드 계약을 찾을 수 없습니다: field_id={node.group_field_id!r}",
                    )
                )
            elif group_metadata and group_metadata.value_type is not NodeValueType.GROUP_SERIES:
                issues.append(
                    _issue(
                        "factor.graph.group_field_type",
                        node.node_id,
                        node.node_id,
                        "그룹 필드는 group_series여야 합니다: "
                        f"actual={group_metadata.value_type.value}",
                    )
                )
    return NodeContract(node.node_id, value_type, unit, history), tuple(issues)
