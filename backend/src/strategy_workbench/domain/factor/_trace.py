"""Bounded factor node trace: the evaluator's per-node values as an immutable projection.

WORKFLOW P1.5-02. 추적은 평가기가 채운 노드 캐시를 뷰(`TraceNodeCache`)로 읽는다 — 값은 같고 두
번째 평가 경로가 없다. 객체 평가기(`_NodeEvaluator.cache`)와 열 평가기(`FactorNodeCache`)의 캐시가
같은 뷰를 내므로 어느 캐시로 만들어도 trace 가 같다(columnar-tape T3-06). The trace explains every
`None` with a status so correctness parity (P1.5-04) and the later debug API (P5-01) can show *why*
a value is missing: warm-up, missing input, divide by zero, missing group, missing reference.
원장이 가린 칸 때문에 결측이 된 칸(`masked`)은 평가기가 값 구간 규칙으로 정한 칸을 그대로 읽는다 —
추적이 규칙을 다시 적지 않는다(#350). Selection is bounded by node ids, security ids, dates and a
row cap; ordering is deterministic (topological node order, then as_of, then security_id).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from ._evaluation import (
    FactorComputedValue,
    FactorEvaluation,
    FactorObservation,
    _as_number,
    _checkpointed,
    _compute_nodes,
    _evaluation_from_computed,
    _indices_by_security,
    _noop_checkpoint,
    _noop_progress,
)
from ._history import node_read_window
from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ConditionalNode,
    ConstantNode,
    ExpressionNode,
    FactorGraph,
    GroupNode,
    MissingPolicy,
    ParameterNode,
    TimeSeriesNode,
    TimeSeriesOperator,
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


class TraceNodeCache(Protocol):
    """trace 가 읽는 노드 캐시 뷰. 노드 값과 MASKED 판정은 평가기가 소유하고 trace 는 읽기만 한다.

    객체 평가기(`_NodeEvaluator.cache`)와 열 평가기(`FactorNodeCache`)가 같은 읽기를 낸다. 값은
    객체 평가기 칸과 같은 Python 값(float·bool·None)이다.
    """

    def value(self, node_id: str, row: int) -> FactorComputedValue: ...

    def is_masked(self, node_id: str, row: int) -> bool: ...


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

    포트폴리오 점수와 trace는 같은 메모리 캐시를 읽는다. 테스트도 운영과 같은 진입점을 쓴다.
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
        _project_trace(
            graph, observations, bounds, nodes, order, evaluator.cache, checkpoint=checkpoint
        ),
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


@dataclass(eq=False)
class _TraceStatuses:
    """선택 바깥 입력도 실제 읽은 칸의 원인을 메모한다. 중첩 클로저 대신 메서드 재귀(깊이는 그래프
    깊이)로 원인을 따라가며 패널 수명을 보존한다. 칸 값과 가림은 노드 캐시 뷰로만 읽는다."""

    nodes: dict[str, ExpressionNode]
    cache: TraceNodeCache
    observations: tuple[FactorObservation, ...]
    by_security: dict[str, list[int]]
    checkpoint: Callable[[], None]
    positions: dict[int, int]
    statuses: dict[tuple[str, int], TraceValueStatus] = field(default_factory=dict)

    def status_for(self, node_id: str, index: int) -> TraceValueStatus:
        key = (node_id, index)
        if key in self.statuses:
            return self.statuses[key]
        cache = self.cache
        value = cache.value(node_id, index)
        if value is not None:
            return TraceValueStatus.OK
        node = self.nodes[node_id]
        input_ids = tuple(node_dependencies(node))
        inputs = tuple(cache.value(dependency, index) for dependency in input_ids)
        indices = self.by_security[self.observations[index].security_id]
        window = node_read_window(node).bounds(self.positions[index])
        dependencies = input_ids
        if isinstance(node, ConditionalNode):
            # 조건식은 실제 선택한 가지의 원인만 잇는다. 선택하지 않은 가지의 워밍업은 무관하다.
            dependencies = (
                (input_ids[1],)
                if inputs[0] is True
                else (input_ids[2],)
                if inputs[0] is False
                else (input_ids[0],)
            )
        input_warming = False
        # 값이 있는 칸은 위에서 OK로 끝났다. 여기서는 창이 이력 안에 있는지만 본다.
        if window.start >= 0:
            input_warming = any(
                cache.value(dependency, source_index) is None
                and self.status_for(dependency, source_index) is TraceValueStatus.WARM_UP
                for dependency in dependencies
                for source_index in _checkpointed(indices[window], self.checkpoint)
            )
        status = _status(
            node,
            value,
            inputs,
            input_ids,
            index,
            self.observations,
            self.by_security,
            cache,
            input_warming=input_warming,
            own_warming=window.start < 0,
            checkpoint=self.checkpoint,
        )
        self.statuses[key] = status
        return status


def _project_trace(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    bounds: TraceSelection,
    nodes: dict[str, ExpressionNode],
    order: tuple[str, ...],
    cache: TraceNodeCache,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> FactorTrace:
    """이미 채운 노드 캐시를 선택 범위만큼 trace 로 투영한다. 캐시는 뷰로만 읽는다."""
    wanted_nodes = order if bounds.node_ids is None else [n for n in order if n in bounds.node_ids]

    positions = _positions(observations, bounds, checkpoint=checkpoint)
    by_security = _indices_by_security(observations, checkpoint=checkpoint)
    ordinal = {
        index: position
        for indices in by_security.values()
        for position, index in _checkpointed(enumerate(indices), checkpoint)
    }
    status_for = _TraceStatuses(
        nodes, cache, observations, by_security, checkpoint, ordinal
    ).status_for
    row_count = 0
    truncated = False
    traced_nodes: list[NodeTrace] = []
    for node_id in _checkpointed(wanted_nodes, checkpoint):
        if positions and row_count >= bounds.max_rows:
            truncated = True  # no empty trailing NodeTrace: a capped node is simply absent
            break
        node = nodes[node_id]
        input_ids = tuple(node_dependencies(node))
        rows: list[TracedValue] = []
        for index in _checkpointed(positions, checkpoint):
            if row_count >= bounds.max_rows:
                truncated = True
                break
            inputs = tuple(cache.value(dependency, index) for dependency in input_ids)
            rows.append(
                TracedValue(
                    as_of=observations[index].as_of,
                    security_id=observations[index].security_id,
                    value=cache.value(node_id, index),
                    status=status_for(node_id, index),
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
    input_ids: tuple[str, ...],
    index: int,
    observations: tuple[FactorObservation, ...],
    by_security: dict[str, list[int]],
    cache: TraceNodeCache,
    *,
    input_warming: bool,
    own_warming: bool,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> TraceValueStatus:
    if value is not None:
        return TraceValueStatus.OK
    if isinstance(node, (ConstantNode, ParameterNode)):  # pragma: no cover - never None
        return TraceValueStatus.OK
    if own_warming:
        return TraceValueStatus.WARM_UP
    # 평가기가 원장이 가린 칸 때문에 결측으로 둔 칸이다 — 두 입력이 모두 값이어도 그 사이에 가린
    # 칸이 들면 여기다(#337). 판정은 평가기의 값 구간 규칙이 하고, 추적은 그 결과를 읽기만
    # 한다(#350)
    if cache.is_masked(node.node_id, index):
        return TraceValueStatus.MASKED
    if isinstance(node, BinaryNode):
        if (
            node.operator is BinaryOperator.DIVIDE
            and _as_number(inputs[0]) is not None
            and _as_number(inputs[1]) == 0
        ):
            return TraceValueStatus.DIVIDE_BY_ZERO
        return TraceValueStatus.WARM_UP if input_warming else TraceValueStatus.MISSING_INPUT
    if isinstance(node, GroupNode):
        observation = observations[index]
        group = next(
            (f.value for f in observation.fields if f.field_id == node.group_field_id), None
        )
        return (
            (TraceValueStatus.WARM_UP if input_warming else TraceValueStatus.MISSING_INPUT)
            if isinstance(group, str)
            else TraceValueStatus.GROUP_MISSING
        )
    if input_warming:
        return TraceValueStatus.WARM_UP
    if isinstance(node, TimeSeriesNode) and node.operator is TimeSeriesOperator.MOMENTUM:
        # 이력은 충분하다: 창에 None 이 있었거나 모멘텀이 0 에서 시작했다
        indices = by_security[observations[index].security_id]
        bounds = node_read_window(node).bounds(indices.index(index))
        window = [
            number
            for source in _checkpointed(indices[bounds], checkpoint)
            if (number := _as_number(cache.value(input_ids[0], source))) is not None
        ]
        if len(window) == node.window and window[0] == 0:
            return TraceValueStatus.DIVIDE_BY_ZERO
    return TraceValueStatus.MISSING_INPUT
