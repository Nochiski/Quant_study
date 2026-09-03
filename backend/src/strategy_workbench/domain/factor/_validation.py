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
)


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


def _issue(code: str, node_id: str | None, path: str, message: str) -> FactorValidationIssue:
    return FactorValidationIssue(code=code, node_id=node_id, path=path, message=message)


def node_dependencies(node: ExpressionNode) -> tuple[str, ...]:
    if isinstance(node, (UnaryNode, TimeSeriesNode, CrossSectionalNode, GroupNode)):
        return (node.input_node_id,)
    if isinstance(node, (BinaryNode, ComparisonNode)):
        return (node.left_node_id, node.right_node_id)
    if isinstance(node, ConditionalNode):
        return (node.predicate_node_id, node.true_node_id, node.false_node_id)
    return ()


def validate_factor_graph(
    graph: FactorGraph,
    *,
    fields: tuple[FieldMetadata, ...] = (),
    parameter_ids: tuple[str, ...] = (),
    factor_ids: tuple[str, ...] = (),
    subgraph_ids: tuple[str, ...] = (),
) -> FactorGraphValidation:
    issues: list[FactorValidationIssue] = []
    nodes = {node.node_id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        issues.append(
            _issue(
                "factor.graph.duplicate_node",
                None,
                "nodes",
                "노드 ID는 중복될 수 없습니다.",
            )
        )
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
        if isinstance(node, FieldNode) and fields and node.field_id not in field_by_id:
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
            if node.operator is UnaryOperator.LAG and (node.periods is None or node.periods < 1):
                issues.append(
                    _issue(
                        "factor.graph.lag_periods",
                        node.node_id,
                        path,
                        f"lag 기간은 1 이상이어야 합니다: periods={node.periods}",
                    )
                )
        elif isinstance(node, TimeSeriesNode):
            if node.window < 1 or node.lag < 0:
                issues.append(
                    _issue(
                        "factor.graph.time_series_window",
                        node.node_id,
                        path,
                        "window는 1 이상이고 lag는 0 이상이어야 합니다: "
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

    if _has_cycle(nodes):
        issues.append(
            _issue("factor.graph.cycle", None, "nodes", "팩터 그래프에 순환 참조가 있습니다.")
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
            contract, contract_issues = _infer_contract(node, contracts, field_by_id)
            contracts[node.node_id] = contract
            issues.extend(contract_issues)

        for node in graph.nodes:
            infer(node.node_id)

    output_contract = contracts.get(graph.output_node_id)
    minimum_history = output_contract.minimum_history_sessions if output_contract else 0
    required_fields = tuple(
        sorted({node.field_id for node in graph.nodes if isinstance(node, FieldNode)})
    )
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


def _has_cycle(nodes: dict[str, ExpressionNode]) -> bool:
    state: dict[str, int] = {}

    def visit(node_id: str) -> bool:
        if state.get(node_id) == 1:
            return True
        if state.get(node_id) == 2:
            return False
        state[node_id] = 1
        node = nodes[node_id]
        if any(dependency in nodes and visit(dependency) for dependency in node_dependencies(node)):
            return True
        state[node_id] = 2
        return False

    return any(visit(node_id) for node_id in nodes if state.get(node_id) is None)


def _infer_contract(
    node: ExpressionNode,
    contracts: dict[str, NodeContract],
    fields: dict[str, FieldMetadata],
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
            if fields and group_metadata is None:
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
