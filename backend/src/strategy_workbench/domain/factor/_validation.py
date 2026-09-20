from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    ExpressionNode,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    GroupNode,
    NodeValueType,
    ParameterNode,
    SavedFactorNode,
    SavedSubgraphNode,
    TimeSeriesNode,
    UnaryNode,
    UnaryOperator,
    field_minimum,
)

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
        "factor.graph.saved_factor_missing",
        "factor.graph.saved_subgraph_missing",
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
    factor_ids: tuple[str, ...] = (),
    subgraph_ids: tuple[str, ...] = (),
    require_field_metadata: bool = False,
) -> FactorGraphValidation:
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
    known_factors = set(factor_ids)
    known_subgraphs = set(subgraph_ids)
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
            issues.append(
                _issue(
                    "factor.graph.field_missing",
                    node.node_id,
                    path,
                    f"필드 계약을 찾을 수 없습니다: field_id={node.field_id!r}",
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
        elif isinstance(node, SavedFactorNode) and node.factor_id not in known_factors:
            issues.append(
                _issue(
                    "factor.graph.saved_factor_missing",
                    node.node_id,
                    path,
                    f"저장 팩터를 찾을 수 없습니다: factor_id={node.factor_id!r}",
                )
            )
        elif isinstance(node, SavedSubgraphNode) and node.subgraph_id not in known_subgraphs:
            issues.append(
                _issue(
                    "factor.graph.saved_subgraph_missing",
                    node.node_id,
                    path,
                    f"저장 서브그래프를 찾을 수 없습니다: subgraph_id={node.subgraph_id!r}",
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
        chain = " → ".join((*cycle, cycle[0]))
        for node_id in cycle:
            issues.append(
                _issue(
                    "factor.graph.cycle",
                    node_id,
                    f"nodes.{index_by_node_id[node_id]}",
                    "이 노드가 순환 참조에 묶여 있어 값을 계산할 수 없습니다. 고리 중 한 곳의 "
                    f"입력을 끊어 주세요 — cycle={chain}",
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
    """순환마다 그 순환에 묶인 node_id를 참조 순서대로.

    존재 여부(bool)만으로는 진단이 어느 노드를 가리킬지 정할 수 없어서 경로를 돌려준다. 방문 순서는
    문서 순서(`nodes` 삽입 순서)라 같은 그래프면 항상 같은 경로가 나온다. 노드 집합이 같은 순환은
    한 번만 보고한다 — 같은 고리를 진입점만 달리해 두 번 세지 않는다.
    """
    state: dict[str, int] = {}
    path: list[str] = []
    found: list[tuple[str, ...]] = []
    seen: set[frozenset[str]] = set()

    def visit(node_id: str) -> None:
        if state.get(node_id) == 2:
            return
        if state.get(node_id) == 1:
            cycle = tuple(path[path.index(node_id) :])
            if frozenset(cycle) not in seen:
                seen.add(frozenset(cycle))
                found.append(cycle)
            return
        state[node_id] = 1
        path.append(node_id)
        for dependency in node_dependencies(nodes[node_id]):
            if dependency in nodes:
                visit(dependency)
        path.pop()
        state[node_id] = 2

    for node_id in nodes:
        if state.get(node_id) is None:
            visit(node_id)
    return tuple(found)


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
    elif isinstance(node, (SavedFactorNode, SavedSubgraphNode)):
        value_type, unit, history = NodeValueType.NUMERIC_SERIES, "unknown", 1
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
