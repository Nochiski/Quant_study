from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import mean, median, pstdev
from typing import TypeAlias, TypeGuard

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    ParameterNode,
    SavedFactorNode,
    SavedSubgraphNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from ._planning import ResolvedFactorParameter
from ._statistics import quantile, rank_items
from ._validation import node_dependencies

FactorInputValue: TypeAlias = float | str | bool | None
FactorComputedValue: TypeAlias = float | bool | None


@dataclass(frozen=True)
class FactorFieldValue:
    field_id: str
    value: FactorInputValue


@dataclass(frozen=True)
class FactorReferenceValue:
    reference_id: str
    value: float | None


@dataclass(frozen=True)
class FactorObservation:
    as_of: date
    security_id: str
    fields: tuple[FactorFieldValue, ...]
    references: tuple[FactorReferenceValue, ...] = ()
    forward_return: float | None = None


@dataclass(frozen=True)
class FactorValue:
    as_of: date
    security_id: str
    value: float | None


@dataclass(frozen=True)
class FactorEvaluation:
    output_node_id: str
    values: tuple[FactorValue, ...]


def evaluate_factor_graph(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    parameters: tuple[ResolvedFactorParameter, ...] = (),
) -> FactorEvaluation:
    computed = _compute_nodes(graph, observations=observations, parameters=parameters)
    raw_output = computed[graph.output_node_id]
    output = tuple(
        FactorValue(
            as_of=observation.as_of,
            security_id=observation.security_id,
            value=float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None,
        )
        for observation, value in zip(observations, raw_output, strict=True)
    )
    return FactorEvaluation(output_node_id=graph.output_node_id, values=output)


def _compute_nodes(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    parameters: tuple[ResolvedFactorParameter, ...] = (),
) -> dict[str, list[FactorComputedValue]]:
    """Evaluate every node reachable from the output once; the cache is the single value source.

    `evaluate_factor_graph` returns the output node; `_trace.trace_factor_graph` projects the whole
    cache. Both read the same lists so trace values equal evaluation values by construction.
    """
    nodes = {node.node_id: node for node in graph.nodes}
    parameter_values = {parameter.parameter_id: parameter.value for parameter in parameters}
    computed: dict[str, list[FactorComputedValue]] = {}

    def evaluate(node_id: str) -> list[FactorComputedValue]:
        if node_id in computed:
            return computed[node_id]
        try:
            node = nodes[node_id]
        except KeyError as error:
            raise ValueError(
                f"factor evaluation references unknown node — node_id={node_id!r}"
            ) from error
        inputs = [evaluate(dependency) for dependency in node_dependencies(node)]
        values: list[FactorComputedValue]
        if isinstance(node, FieldNode):
            values = _field_values(observations, node.field_id, graph.missing_policy)
        elif isinstance(node, ConstantNode):
            values = [node.value for _ in observations]
        elif isinstance(node, ParameterNode):
            if node.parameter_id not in parameter_values:
                raise ValueError(
                    "factor evaluation parameter is unresolved — "
                    f"node_id={node.node_id!r} parameter_id={node.parameter_id!r}"
                )
            value = parameter_values[node.parameter_id]
            if isinstance(value, (str, bool)):
                raise ValueError(
                    "numeric factor parameter has incompatible value — "
                    f"parameter_id={node.parameter_id!r} value={value!r}"
                )
            values = [float(value)] * len(observations)
        elif isinstance(node, BinaryNode):
            values = [
                _binary(node.operator, left, right) for left, right in zip(*inputs, strict=True)
            ]
        elif isinstance(node, ComparisonNode):
            values = [
                _compare(node.operator, left, right) for left, right in zip(*inputs, strict=True)
            ]
        elif isinstance(node, ConditionalNode):
            values = [
                true_value if predicate is True else false_value if predicate is False else None
                for predicate, true_value, false_value in zip(*inputs, strict=True)
            ]
        elif isinstance(node, UnaryNode):
            values = _unary(node, inputs[0], observations)
        elif isinstance(node, TimeSeriesNode):
            values = _time_series(node, inputs[0], observations)
        elif isinstance(node, CrossSectionalNode):
            values = _cross_sectional(node, inputs[0], observations)
        elif isinstance(node, GroupNode):
            values = _group_transform(node, inputs[0], observations)
        elif isinstance(node, SavedFactorNode):
            values = _reference_values(observations, f"factor:{node.factor_id}")
        elif isinstance(node, SavedSubgraphNode):
            values = _reference_values(observations, f"subgraph:{node.subgraph_id}")
        computed[node_id] = values
        return values

    evaluate(graph.output_node_id)
    return computed


def _field_values(
    observations: tuple[FactorObservation, ...],
    field_id: str,
    missing_policy: MissingPolicy,
) -> list[FactorComputedValue]:
    raw: list[float | None] = []
    for observation in observations:
        by_id = {field.field_id: field.value for field in observation.fields}
        value = by_id.get(field_id)
        raw.append(
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )
    if missing_policy is MissingPolicy.ZERO:
        return [0.0 if value is None else value for value in raw]
    if missing_policy is MissingPolicy.CROSS_SECTIONAL_MEDIAN:
        by_date = _indices_by_date(observations)
        result = list(raw)
        for indices in by_date.values():
            available = [value for index in indices if (value := raw[index]) is not None]
            fill = median(available) if available else None
            for index in indices:
                if result[index] is None:
                    result[index] = fill
        return result
    return list(raw)


def _reference_values(
    observations: tuple[FactorObservation, ...], reference_id: str
) -> list[FactorComputedValue]:
    return [
        next(
            (
                reference.value
                for reference in observation.references
                if reference.reference_id == reference_id
            ),
            None,
        )
        for observation in observations
    ]


def _binary(
    operator: BinaryOperator,
    left: FactorComputedValue,
    right: FactorComputedValue,
) -> float | None:
    if not isinstance(left, (int, float)) or isinstance(left, bool):
        return None
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return None
    if operator is BinaryOperator.ADD:
        return left + right
    if operator is BinaryOperator.SUBTRACT:
        return left - right
    if operator is BinaryOperator.MULTIPLY:
        return left * right
    return None if right == 0 else left / right


def _compare(
    operator: FactorComparisonOperator,
    left: FactorComputedValue,
    right: FactorComputedValue,
) -> bool | None:
    if not isinstance(left, (int, float)) or isinstance(left, bool):
        return None
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return None
    if operator is FactorComparisonOperator.GREATER_THAN:
        return left > right
    if operator is FactorComparisonOperator.GREATER_THAN_OR_EQUAL:
        return left >= right
    if operator is FactorComparisonOperator.LESS_THAN:
        return left < right
    if operator is FactorComparisonOperator.LESS_THAN_OR_EQUAL:
        return left <= right
    return left == right


def _unary(
    node: UnaryNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
) -> list[FactorComputedValue]:
    if node.operator is UnaryOperator.NEGATE:
        return [
            -value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            for value in values
        ]
    if node.operator is UnaryOperator.LAG:
        return _lag(values, observations, node.periods or 0)
    if node.operator is UnaryOperator.NEUTRALIZE:
        return _cross_sectional_demean(values, observations)
    synthetic = CrossSectionalNode(
        node_id=node.node_id,
        operator={
            UnaryOperator.RANK: CrossSectionalOperator.RANK,
            UnaryOperator.ZSCORE: CrossSectionalOperator.ZSCORE,
            UnaryOperator.WINSORIZE: CrossSectionalOperator.WINSORIZE,
        }[node.operator],
        input_node_id=node.input_node_id,
        kind="cross_sectional",
    )
    return _cross_sectional(synthetic, values, observations)


def _cross_sectional_demean(
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _indices_by_date(observations).values():
        numeric = [
            (index, number)
            for index in indices
            if (number := _as_number(values[index])) is not None
        ]
        center = mean(value for _, value in numeric) if numeric else 0.0
        for index, value in numeric:
            result[index] = value - center
    return result


def _time_series(
    node: TimeSeriesNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _indices_by_security(observations).values():
        for position, result_index in enumerate(indices):
            end = position - node.lag + 1
            start = end - node.window
            if start < 0 or end <= 0:
                continue
            window = values_from_indices(values, indices[start:end])
            if len(window) != node.window:
                continue
            if node.operator is TimeSeriesOperator.MEAN:
                result[result_index] = mean(window)
            elif node.operator is TimeSeriesOperator.STANDARD_DEVIATION:
                result[result_index] = pstdev(window)
            elif node.operator is TimeSeriesOperator.MOMENTUM:
                result[result_index] = None if window[0] == 0 else window[-1] / window[0] - 1
            elif node.operator is TimeSeriesOperator.DELTA:
                result[result_index] = window[-1] - window[0]
            elif node.operator is TimeSeriesOperator.MINIMUM:
                result[result_index] = min(window)
            else:
                result[result_index] = max(window)
    return result


def _cross_sectional(
    node: CrossSectionalNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _indices_by_date(observations).values():
        numeric = [
            (index, number)
            for index in indices
            if (number := _as_number(values[index])) is not None
        ]
        if not numeric:
            continue
        if node.operator is CrossSectionalOperator.RANK:
            ranked = rank_items(numeric)
            denominator = max(len(ranked) - 1, 1)
            for index, rank in ranked.items():
                result[index] = (rank - 1) / denominator
        elif node.operator is CrossSectionalOperator.ZSCORE:
            samples = [value for _, value in numeric]
            center = mean(samples)
            deviation = pstdev(samples)
            for index, value in numeric:
                result[index] = 0.0 if deviation == 0 else (value - center) / deviation
        else:
            ordered = sorted(value for _, value in numeric)
            lower = quantile(ordered, node.lower_quantile)
            upper = quantile(ordered, node.upper_quantile)
            for index, value in numeric:
                result[index] = min(max(value, lower), upper)
    return result


def _group_transform(
    node: GroupNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
) -> list[FactorComputedValue]:
    grouped: dict[tuple[date, str], list[int]] = {}
    for index, observation in enumerate(observations):
        group = next(
            (field.value for field in observation.fields if field.field_id == node.group_field_id),
            None,
        )
        if isinstance(group, str):
            grouped.setdefault((observation.as_of, group), []).append(index)
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in grouped.values():
        numeric = [
            (index, number)
            for index in indices
            if (number := _as_number(values[index])) is not None
        ]
        if node.operator is GroupOperator.NEUTRALIZE:
            center = mean(value for _, value in numeric) if numeric else 0.0
            for index, value in numeric:
                result[index] = value - center
        else:
            ranked = rank_items(numeric)
            denominator = max(len(ranked) - 1, 1)
            for index, rank in ranked.items():
                result[index] = (rank - 1) / denominator
    return result


def _lag(
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
    periods: int,
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _indices_by_security(observations).values():
        for position, index in enumerate(indices):
            if position >= periods:
                result[index] = values[indices[position - periods]]
    return result


def _indices_by_date(observations: tuple[FactorObservation, ...]) -> dict[date, list[int]]:
    grouped: dict[date, list[int]] = {}
    for index, observation in enumerate(observations):
        grouped.setdefault(observation.as_of, []).append(index)
    return grouped


def _indices_by_security(
    observations: tuple[FactorObservation, ...],
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, observation in enumerate(observations):
        grouped.setdefault(observation.security_id, []).append(index)
    for indices in grouped.values():
        indices.sort(key=lambda index: observations[index].as_of)
    return grouped


def values_from_indices(values: list[FactorComputedValue], indices: list[int]) -> list[float]:
    return [number for index in indices if (number := _as_number(values[index])) is not None]


def _is_number(value: FactorComputedValue) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_number(value: FactorComputedValue) -> float | None:
    return float(value) if _is_number(value) else None
