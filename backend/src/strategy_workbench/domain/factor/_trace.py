"""Bounded factor node trace: the evaluator's per-node values as an immutable projection.

WORKFLOW P1.5-02. The trace reuses the evaluator's computed-node cache (same values, no second
evaluation path) and explains every `None` with a status so correctness parity (P1.5-04) and the
later debug API (P5-01) can show *why* a value is missing: warm-up, missing input, divide by zero,
missing group, missing reference. Selection is bounded by node ids, security ids, dates and a row
cap; ordering is deterministic (topological node order, then as_of, then security_id).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from ._evaluation import (
    FactorComputedValue,
    FactorObservation,
    _as_number,
    _compute_nodes,
    _indices_by_security,
    values_from_indices,
)
from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    ExpressionNode,
    FactorGraph,
    FieldNode,
    GroupNode,
    ParameterNode,
    SavedFactorNode,
    SavedSubgraphNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from ._planning import ResolvedFactorParameter, _operation, _topological_order
from ._validation import node_dependencies


class TraceValueStatus(StrEnum):
    OK = "ok"
    MISSING_INPUT = "missing_input"
    WARM_UP = "warm_up"
    DIVIDE_BY_ZERO = "divide_by_zero"
    GROUP_MISSING = "group_missing"
    REFERENCE_MISSING = "reference_missing"


@dataclass(frozen=True)
class TraceSelection:
    """Bounds for a trace request. `None` means "all"; `max_rows` always applies."""

    node_ids: tuple[str, ...] | None = None
    security_ids: tuple[str, ...] | None = None
    as_of: tuple[date, ...] | None = None
    max_rows: int = 2_000


@dataclass(frozen=True)
class TracedValue:
    as_of: date
    security_id: str
    value: FactorComputedValue
    status: TraceValueStatus
    inputs: tuple[FactorComputedValue, ...]


@dataclass(frozen=True)
class NodeTrace:
    node_id: str
    operation: str
    input_node_ids: tuple[str, ...]
    values: tuple[TracedValue, ...]


@dataclass(frozen=True)
class FactorTrace:
    output_node_id: str
    nodes: tuple[NodeTrace, ...]
    row_count: int
    truncated: bool


def trace_factor_graph(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    selection: TraceSelection | None = None,
) -> FactorTrace:
    """Project the evaluator node cache into a bounded, explained, deterministically ordered trace.

    Values come from the same cache `evaluate_factor_graph` reads, so they never diverge.
    """
    bounds = selection or TraceSelection()
    if bounds.max_rows <= 0:
        raise ValueError(f"trace max_rows must be positive — max_rows={bounds.max_rows}")
    nodes = {node.node_id: node for node in graph.nodes}
    computed = _compute_nodes(graph, observations=observations, parameters=parameters)
    order = _topological_order(graph.output_node_id, nodes)
    wanted_nodes = order if bounds.node_ids is None else [n for n in order if n in bounds.node_ids]
    unknown = set(bounds.node_ids or ()) - set(nodes)
    if unknown:
        raise ValueError(
            f"trace selection references unknown nodes — node_ids={sorted(unknown)!r} "
            f"known={sorted(nodes)!r}"
        )

    positions = _positions(observations, bounds)
    by_security = _indices_by_security(observations)
    row_count = 0
    truncated = False
    traced_nodes: list[NodeTrace] = []
    for node_id in wanted_nodes:
        node = nodes[node_id]
        values = computed[node_id]
        input_ids = tuple(node_dependencies(node))
        rows: list[TracedValue] = []
        for index in positions:
            if row_count >= bounds.max_rows:
                truncated = True
                break
            inputs = tuple(computed[dependency][index] for dependency in input_ids)
            first_series = computed[input_ids[0]] if input_ids else []
            rows.append(
                TracedValue(
                    as_of=observations[index].as_of,
                    security_id=observations[index].security_id,
                    value=values[index],
                    status=_status(
                        node, values[index], inputs, first_series, index, observations, by_security
                    ),
                    inputs=inputs,
                )
            )
            row_count += 1
        traced_nodes.append(
            NodeTrace(
                node_id=node_id,
                operation=_operation(node),
                input_node_ids=input_ids,
                values=tuple(rows),
            )
        )
        if truncated:
            break
    return FactorTrace(
        output_node_id=graph.output_node_id,
        nodes=tuple(traced_nodes),
        row_count=row_count,
        truncated=truncated,
    )


def _positions(observations: tuple[FactorObservation, ...], bounds: TraceSelection) -> list[int]:
    selected = [
        index
        for index, observation in enumerate(observations)
        if (bounds.security_ids is None or observation.security_id in bounds.security_ids)
        and (bounds.as_of is None or observation.as_of in bounds.as_of)
    ]
    selected.sort(key=lambda index: (observations[index].as_of, observations[index].security_id))
    return selected


def _status(
    node: ExpressionNode,
    value: FactorComputedValue,
    inputs: tuple[FactorComputedValue, ...],
    first_series: list[FactorComputedValue],
    index: int,
    observations: tuple[FactorObservation, ...],
    by_security: dict[str, list[int]],
) -> TraceValueStatus:
    if value is not None:
        return TraceValueStatus.OK
    if isinstance(node, (ConstantNode, ParameterNode)):  # pragma: no cover - never None
        return TraceValueStatus.OK
    if isinstance(node, FieldNode):
        return TraceValueStatus.MISSING_INPUT
    if isinstance(node, (SavedFactorNode, SavedSubgraphNode)):
        return TraceValueStatus.REFERENCE_MISSING
    if isinstance(node, BinaryNode):
        if node.operator is BinaryOperator.DIVIDE and all(
            _as_number(item) is not None for item in inputs
        ):
            return TraceValueStatus.DIVIDE_BY_ZERO
        return TraceValueStatus.MISSING_INPUT
    if isinstance(node, (ComparisonNode, ConditionalNode, CrossSectionalNode)):
        return TraceValueStatus.MISSING_INPUT
    if isinstance(node, GroupNode):
        observation = observations[index]
        group = next(
            (f.value for f in observation.fields if f.field_id == node.group_field_id), None
        )
        return (
            TraceValueStatus.MISSING_INPUT
            if isinstance(group, str)
            else TraceValueStatus.GROUP_MISSING
        )
    if isinstance(node, UnaryNode):
        if node.operator is UnaryOperator.LAG:
            history = _history_status(index, observations, by_security, node.periods or 0, 1)
            # Enough history but still None: the lagged input itself was missing.
            return TraceValueStatus.MISSING_INPUT if history is TraceValueStatus.OK else history
        return TraceValueStatus.MISSING_INPUT
    if isinstance(node, TimeSeriesNode):
        status = _history_status(index, observations, by_security, node.lag, node.window)
        if status is not TraceValueStatus.OK:
            return status
        # Enough history: the window held a None input, or momentum divided by a zero start.
        indices = by_security[observations[index].security_id]
        position = indices.index(index)
        end = position - node.lag + 1
        window = values_from_indices(first_series, indices[end - node.window : end])
        if (
            node.operator is TimeSeriesOperator.MOMENTUM
            and len(window) == node.window
            and window[0] == 0
        ):
            return TraceValueStatus.DIVIDE_BY_ZERO
        return TraceValueStatus.MISSING_INPUT
    return TraceValueStatus.MISSING_INPUT  # pragma: no cover - node kinds are exhaustive


def _history_status(
    index: int,
    observations: tuple[FactorObservation, ...],
    by_security: dict[str, list[int]],
    lag: int,
    window: int,
) -> TraceValueStatus:
    indices = by_security[observations[index].security_id]
    position = indices.index(index)
    end = position - lag + 1
    start = end - window
    if start < 0 or end <= 0:
        return TraceValueStatus.WARM_UP
    return TraceValueStatus.OK
