from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import TypeAlias

from strategy_workbench.domain.canonical.facade.encoding import (
    canonical_json_hash,
    canonical_json_spec_hash,
    canonical_payload_json,
)

from ._nodes import (
    FILLING_MISSING_POLICIES,
    ExpressionNode,
    FactorGraph,
    FieldMetadata,
    MissingPolicy,
)
from ._validation import FactorGraphValidation, node_dependencies, validate_factor_graph

# 평가 수치 규칙의 정본. 결과 digest 회귀와 함께 올리고 계획·실행 신원에 전달한다.
FACTOR_EVALUATION_VERSION = "factor-evaluation-v1"

FactorParameterValue: TypeAlias = float | int | str | bool


@dataclass(frozen=True)
class ResolvedFactorParameter:
    parameter_id: str
    value: FactorParameterValue


@dataclass(frozen=True)
class FactorExecutionStep:
    sequence: int
    node_id: str
    operation: str
    input_node_ids: tuple[str, ...]
    output_type: str
    output_unit: str
    minimum_history_sessions: int


@dataclass(frozen=True)
class FactorExecutionPlan:
    graph_hash: str
    plan_hash: str
    registry_version: str
    output_node_id: str
    steps: tuple[FactorExecutionStep, ...]
    required_field_ids: tuple[str, ...]
    minimum_history_sessions: int
    missing_policy: str
    evaluation_version: str
    as_of_policy: str = "available_date_lte_as_of"


class InvalidFactorGraphError(ValueError):
    def __init__(self, validation: FactorGraphValidation) -> None:
        codes = tuple(issue.code for issue in validation.issues)
        super().__init__(f"factor graph validation failed — issue_codes={codes}")
        self.validation = validation


def canonical_factor_graph_json(graph: FactorGraph) -> str:
    return canonical_payload_json(asdict(graph), default=_json_default)


def factor_graph_hash(graph: FactorGraph) -> str:
    return canonical_json_spec_hash(canonical_factor_graph_json(graph))


def compile_factor_plan(
    graph: FactorGraph,
    *,
    registry_version: str,
    missing: MissingPolicy,
    fields: tuple[FieldMetadata, ...] = (),
    parameter_ids: tuple[str, ...] = (),
    require_field_metadata: bool = False,
) -> FactorExecutionPlan:
    validation = validate_factor_graph(
        graph,
        fields=fields,
        parameter_ids=parameter_ids,
        require_field_metadata=require_field_metadata,
    )
    if not validation.valid:
        raise InvalidFactorGraphError(validation)
    nodes = {node.node_id: node for node in graph.nodes}
    contracts = {contract.node_id: contract for contract in validation.node_contracts}
    ordered_ids = _topological_order(graph.output_node_id, nodes)
    steps = tuple(
        FactorExecutionStep(
            sequence=index,
            node_id=node_id,
            operation=_operation(nodes[node_id]),
            input_node_ids=node_dependencies(nodes[node_id]),
            output_type=contracts[node_id].value_type.value,
            output_unit=contracts[node_id].unit,
            minimum_history_sessions=contracts[node_id].minimum_history_sessions,
        )
        for index, node_id in enumerate(ordered_ids, start=1)
    )
    graph_fingerprint = factor_graph_hash(graph)
    payload = {
        "graph_hash": graph_fingerprint,
        "registry_version": registry_version,
        "output_node_id": graph.output_node_id,
        "steps": [asdict(step) for step in steps],
        # 결측 정책은 실행 설정으로 옮겼지만 plan 의 일부로 남는다: 이 값이 `plan_hash` 에서
        # 빠지면 결측 처리만 다른 두 평가가 trace provenance에서 같은 계획으로 표시된다(spec D6).
        "missing_policy": missing.value,
        "evaluation_version": FACTOR_EVALUATION_VERSION,
        # 평가 규칙 판본의 첫 사례(#312 · #375 DR-A-10): 채우는 정책은 채움 자리를 잎에서 횡단면
        # 진입으로 옮겼던 역사 표지다. 새 evaluation_version과 별개로 보존한다.
        **({"missing_fill": "cross_section"} if missing in FILLING_MISSING_POLICIES else {}),
        "as_of_policy": "available_date_lte_as_of",
    }
    plan_hash = canonical_json_hash(payload, ensure_ascii=True)
    return FactorExecutionPlan(
        graph_hash=graph_fingerprint,
        plan_hash=plan_hash,
        registry_version=registry_version,
        output_node_id=graph.output_node_id,
        steps=steps,
        required_field_ids=validation.required_field_ids,
        minimum_history_sessions=validation.minimum_history_sessions,
        missing_policy=missing.value,
        evaluation_version=FACTOR_EVALUATION_VERSION,
    )


def _topological_order(output_node_id: str, nodes: dict[str, ExpressionNode]) -> tuple[str, ...]:
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        for dependency in node_dependencies(nodes[node_id]):
            visit(dependency)
        visited.add(node_id)
        ordered.append(node_id)

    visit(output_node_id)
    return tuple(ordered)


def _operation(node: ExpressionNode) -> str:
    operator = getattr(node, "operator", None)
    return f"{node.kind}.{operator.value}" if isinstance(operator, Enum) else node.kind


def _json_default(value: object) -> str:
    if isinstance(value, (date, Enum)):
        return value.isoformat() if isinstance(value, date) else str(value.value)
    raise TypeError(f"unsupported factor canonical value — type={type(value).__name__}")
