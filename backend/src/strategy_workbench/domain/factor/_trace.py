"""Bounded factor node trace: the evaluator's per-node values as an immutable projection.

WORKFLOW P1.5-02. The trace reuses the evaluator's computed-node cache (same values, no second
evaluation path) and explains every `None` with a status so correctness parity (P1.5-04) and the
later debug API (P5-01) can show *why* a value is missing: warm-up, missing input, divide by zero,
missing group, missing reference. 원장이 가린 칸 때문에 결측이 된 칸(`masked`)은 평가기가 값 구간
규칙으로 정한 칸을 그대로 읽는다 — 추적이 규칙을 다시 적지 않는다(#350). Selection is bounded by
node ids, security ids, dates and a row cap; ordering is deterministic (topological node order,
then as_of, then security_id).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from ._evaluation import (
    FactorComputedValue,
    FactorEvaluation,
    FactorObservation,
    _as_number,
    _checkpointed,
    _compute_nodes,
    _evaluation_from_computed,
    _indices_by_security,
    _NodeEvaluator,
    _noop_checkpoint,
    _noop_progress,
    values_from_indices,
)
from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ConstantNode,
    ExpressionNode,
    FactorGraph,
    GroupNode,
    MissingPolicy,
    ParameterNode,
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
    # 원장이 가린 셀이거나, 값이 대표하는 시점 구간에 가린 셀이 들어 결측이 된 칸(#337·#350)
    MASKED = "masked"


@dataclass(frozen=True)
class TraceSelection:
    """Bounds for a trace request. `None` means "all"; `max_rows` always applies."""

    node_ids: tuple[str, ...] | None = None
    security_ids: tuple[str, ...] | None = None
    as_of: tuple[date, ...] | None = None
    max_rows: int = 2_000


@dataclass(frozen=True)
class TracedValue:
    """One (as_of, security) row of a node.

    `inputs` are the dependency values on the same row; time-series windows are not expanded
    (the status explains warm-up).
    """

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
    missing: MissingPolicy,
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    selection: TraceSelection | None = None,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> FactorTrace:
    """Project the evaluator node cache into a bounded, explained, deterministically ordered trace.

    Values come from the same cache `evaluate_factor_graph` reads, so they never diverge.
    """
    bounds, nodes, order = _trace_context(graph, observations, selection, checkpoint=checkpoint)
    evaluator = _compute_nodes(
        graph,
        observations=observations,
        missing=missing,
        parameters=parameters,
        checkpoint=checkpoint,
    )
    return _project_trace(
        graph, observations, bounds, nodes, order, evaluator, checkpoint=checkpoint
    )


def evaluate_factor_graph_with_trace(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    missing: MissingPolicy,
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    selection: TraceSelection | None = None,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> tuple[FactorEvaluation, FactorTrace]:
    """Evaluate once and derive both the executable output and bounded debug projection.

    This is the truthful pipeline entry point for P5: the portfolio score and trace rows share
    the exact in-memory node cache. Callers do not run `evaluate_factor_graph` and
    `trace_factor_graph` independently.
    """
    bounds, nodes, order = _trace_context(graph, observations, selection, checkpoint=checkpoint)
    evaluator = _compute_nodes(
        graph,
        observations=observations,
        missing=missing,
        parameters=parameters,
        checkpoint=checkpoint,
        progress=progress,
    )
    return (
        _evaluation_from_computed(graph, observations, evaluator.computed, checkpoint=checkpoint),
        _project_trace(graph, observations, bounds, nodes, order, evaluator, checkpoint=checkpoint),
    )


def _trace_context(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    selection: TraceSelection | None,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> tuple[TraceSelection, dict[str, ExpressionNode], tuple[str, ...]]:
    bounds = selection or TraceSelection()
    if bounds.max_rows <= 0:
        raise ValueError(f"trace max_rows must be positive — max_rows={bounds.max_rows}")
    nodes = {node.node_id: node for node in graph.nodes}
    order = _topological_order(graph.output_node_id, nodes)
    # Validate the selection before any evaluation: unknown or unreachable nodes are caller errors.
    unknown = set(bounds.node_ids or ()) - set(order)
    if unknown:
        raise ValueError(
            "trace selection references nodes that are unknown or not reachable from the output — "
            f"node_ids={sorted(unknown)!r} reachable={list(order)!r}"
        )
    _require_unique_rows(observations, checkpoint=checkpoint)
    return bounds, nodes, order


def _project_trace(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    bounds: TraceSelection,
    nodes: dict[str, ExpressionNode],
    order: tuple[str, ...],
    evaluator: _NodeEvaluator,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> FactorTrace:
    computed = evaluator.computed
    wanted_nodes = order if bounds.node_ids is None else [n for n in order if n in bounds.node_ids]

    positions = _positions(observations, bounds, checkpoint=checkpoint)
    by_security = _indices_by_security(observations, checkpoint=checkpoint)
    row_count = 0
    truncated = False
    traced_nodes: list[NodeTrace] = []
    for node_id in _checkpointed(wanted_nodes, checkpoint):
        if positions and row_count >= bounds.max_rows:
            truncated = True  # no empty trailing NodeTrace: a capped node is simply absent
            break
        node = nodes[node_id]
        values = computed[node_id]
        input_ids = tuple(node_dependencies(node))
        rows: list[TracedValue] = []
        for index in _checkpointed(positions, checkpoint):
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
                        node,
                        values[index],
                        inputs,
                        first_series,
                        index,
                        observations,
                        by_security,
                        evaluator.masked[node_id],
                        checkpoint=checkpoint,
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


def _require_unique_rows(
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> None:
    """(as_of, security_id) must be unique: the evaluator's time-series order depends on it."""
    seen: set[tuple[date, str]] = set()
    duplicates: set[tuple[date, str]] = set()
    for observation in _checkpointed(observations, checkpoint):
        key = (observation.as_of, observation.security_id)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        raise ValueError(
            "trace requires unique (as_of, security_id) observations — "
            f"duplicates={sorted(duplicates)!r} count={len(duplicates)}"
        )


def _positions(
    observations: tuple[FactorObservation, ...],
    bounds: TraceSelection,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[int]:
    selected = [
        index
        for index, observation in _checkpointed(enumerate(observations), checkpoint)
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
    masked: frozenset[int],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> TraceValueStatus:
    if value is not None:
        return TraceValueStatus.OK
    if isinstance(node, (ConstantNode, ParameterNode)):  # pragma: no cover - never None
        return TraceValueStatus.OK
    if _warming_up(node, index, observations, by_security):
        return TraceValueStatus.WARM_UP
    # 평가기가 원장이 가린 칸 때문에 결측으로 둔 칸이다 — 두 입력이 모두 값이어도 그 사이에 가린
    # 칸이 들면 여기다(#337). 판정은 평가기의 값 구간 규칙이 하고, 추적은 그 결과를 읽기만
    # 한다(#350)
    if index in masked:
        return TraceValueStatus.MASKED
    if isinstance(node, BinaryNode):
        if (
            node.operator is BinaryOperator.DIVIDE
            and _as_number(inputs[0]) is not None
            and _as_number(inputs[1]) == 0
        ):
            return TraceValueStatus.DIVIDE_BY_ZERO
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
    if isinstance(node, TimeSeriesNode) and node.operator is TimeSeriesOperator.MOMENTUM:
        # 이력은 충분하다: 창에 None 이 있었거나 모멘텀이 0 에서 시작했다
        indices = by_security[observations[index].security_id]
        end = indices.index(index) - node.lag + 1
        window = values_from_indices(
            first_series, indices[end - node.window : end], checkpoint=checkpoint
        )
        if len(window) == node.window and window[0] == 0:
            return TraceValueStatus.DIVIDE_BY_ZERO
    return TraceValueStatus.MISSING_INPUT


def _warming_up(
    node: ExpressionNode,
    index: int,
    observations: tuple[FactorObservation, ...],
    by_security: dict[str, list[int]],
) -> bool:
    """시간 연산(`lag`·창 연산)이 읽을 칸이 그 종목의 첫 관측보다 앞이다."""
    if isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
        lag, window = node.periods or 0, 1
    elif isinstance(node, TimeSeriesNode):
        lag, window = node.lag, node.window
    else:
        return False
    indices = by_security[observations[index].security_id]
    end = indices.index(index) - lag + 1
    return end - window < 0 or end <= 0
