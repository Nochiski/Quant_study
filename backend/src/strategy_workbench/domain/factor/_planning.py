from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import TypeAlias

from ._nodes import (
    ExpressionNode,
    FactorGraph,
    FieldMetadata,
    SavedFactorNode,
    SavedSubgraphNode,
)
from ._validation import FactorGraphValidation, node_dependencies, validate_factor_graph

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
    referenced_factor_ids: tuple[str, ...]
    referenced_subgraph_ids: tuple[str, ...]
    minimum_history_sessions: int
    missing_policy: str
    as_of_policy: str = "available_date_lte_as_of"


@dataclass(frozen=True)
class FactorMatrixCacheKey:
    fingerprint: str
    data_snapshot_id: str
    plan_hash: str
    registry_version: str
    parameters: tuple[ResolvedFactorParameter, ...]
    as_of_start: date
    as_of_end: date


class InvalidFactorGraphError(ValueError):
    def __init__(self, validation: FactorGraphValidation) -> None:
        codes = tuple(issue.code for issue in validation.issues)
        super().__init__(f"factor graph validation failed — issue_codes={codes}")
        self.validation = validation


def canonical_factor_graph_json(graph: FactorGraph) -> str:
    return json.dumps(
        asdict(graph),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def factor_graph_hash(graph: FactorGraph) -> str:
    return hashlib.sha256(canonical_factor_graph_json(graph).encode("utf-8")).hexdigest()


def compile_factor_plan(
    graph: FactorGraph,
    *,
    registry_version: str,
    fields: tuple[FieldMetadata, ...] = (),
    parameter_ids: tuple[str, ...] = (),
    factor_ids: tuple[str, ...] = (),
    subgraph_ids: tuple[str, ...] = (),
    require_field_metadata: bool = False,
) -> FactorExecutionPlan:
    validation = validate_factor_graph(
        graph,
        fields=fields,
        parameter_ids=parameter_ids,
        factor_ids=factor_ids,
        subgraph_ids=subgraph_ids,
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
        "missing_policy": graph.missing_policy.value,
        "as_of_policy": "available_date_lte_as_of",
    }
    plan_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FactorExecutionPlan(
        graph_hash=graph_fingerprint,
        plan_hash=plan_hash,
        registry_version=registry_version,
        output_node_id=graph.output_node_id,
        steps=steps,
        required_field_ids=validation.required_field_ids,
        referenced_factor_ids=tuple(
            sorted({node.factor_id for node in graph.nodes if isinstance(node, SavedFactorNode)})
        ),
        referenced_subgraph_ids=tuple(
            sorted(
                {node.subgraph_id for node in graph.nodes if isinstance(node, SavedSubgraphNode)}
            )
        ),
        minimum_history_sessions=validation.minimum_history_sessions,
        missing_policy=graph.missing_policy.value,
    )


def build_factor_matrix_cache_key(
    *,
    data_snapshot_id: str,
    plan_hash: str,
    registry_version: str,
    parameters: tuple[ResolvedFactorParameter, ...],
    as_of_start: date,
    as_of_end: date,
) -> FactorMatrixCacheKey:
    ordered_parameters = tuple(sorted(parameters, key=lambda item: item.parameter_id))
    payload = {
        "data_snapshot_id": data_snapshot_id,
        "plan_hash": plan_hash,
        "registry_version": registry_version,
        "parameters": [asdict(parameter) for parameter in ordered_parameters],
        "as_of_start": as_of_start.isoformat(),
        "as_of_end": as_of_end.isoformat(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FactorMatrixCacheKey(
        fingerprint=fingerprint,
        data_snapshot_id=data_snapshot_id,
        plan_hash=plan_hash,
        registry_version=registry_version,
        parameters=ordered_parameters,
        as_of_start=as_of_start,
        as_of_end=as_of_end,
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
