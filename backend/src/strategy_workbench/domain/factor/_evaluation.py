from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from statistics import mean, median, pstdev
from typing import TypeAlias, TypeGuard, TypeVar

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    ExpressionNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    ParameterNode,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from ._planning import ResolvedFactorParameter
from ._statistics import (
    cross_sectional_rank,
    cross_sectional_zscore,
    quantile,
)
from ._validation import node_dependencies

FactorInputValue: TypeAlias = float | str | bool | None
FactorComputedValue: TypeAlias = float | bool | None

_T = TypeVar("_T")
_CHECKPOINT_BATCH = 256


def _noop_checkpoint() -> None:
    return None


def _noop_progress(fraction: float) -> None:
    return None


def _checkpointed(items: Iterable[_T], checkpoint: Callable[[], None]) -> Iterator[_T]:
    """Yield work in bounded batches without assigning cancellation policy to the domain."""
    for index, item in enumerate(items):
        if index % _CHECKPOINT_BATCH == 0:
            checkpoint()
        yield item


@dataclass(frozen=True)
class FactorFieldValue:
    field_id: str
    value: FactorInputValue
    # 원장이 무효라고 가린 셀(값 None, 원천 셀 종류 MASKED). 실행 결측 정책이 채우지 않아 창 결측
    # 전파로만 처리된다 — 무엇을 가리나는 원장, 무엇을 채우나는 결측 정책이다(#298).
    masked: bool = False


@dataclass(frozen=True)
class FactorObservation:
    """One (as_of, security) row of raw inputs.

    `universe_member` narrows every cross-sectional peer group: rank / zscore / winsorize /
    neutralize / cross-sectional-median fill compare a row only against rows sharing its
    `(as_of, universe_member)`. A row that left the universe on `as_of` must therefore not move a
    member's rank or z-score. Time-series operators still see the security's whole row history, so
    membership churn never truncates a lookback window. Callers that have no universe concept
    (pure factor research over a fixed panel) leave the default and get one cross-section per date.
    """

    as_of: date
    security_id: str
    fields: tuple[FactorFieldValue, ...]
    forward_return: float | None = None
    universe_member: bool = True


@dataclass(frozen=True)
class FactorValue:
    as_of: date
    security_id: str
    value: float | None


@dataclass(frozen=True)
class FactorEvaluation:
    output_node_id: str
    values: tuple[FactorValue, ...]


class NonFiniteFactorCalculationError(ArithmeticError):
    """A factor node produced a value that cannot be represented in a truthful result."""

    def __init__(
        self,
        *,
        node_id: str,
        value: float,
        observation: FactorObservation,
    ) -> None:
        super().__init__(
            "factor calculation produced a non-finite value — "
            f"node_id={node_id!r} as_of={observation.as_of} "
            f"security_id={observation.security_id!r} value={value!r}"
        )
        self.node_id = node_id
        self.value = value
        self.as_of = observation.as_of
        self.security_id = observation.security_id


def evaluate_factor_graph(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    missing: MissingPolicy,
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> FactorEvaluation:
    """그래프를 평가한다. `progress` 는 그래프 평가 안의 완료 비율(0~1, 단조 증가)을 받는다.

    노드 계산이 `_NODES_PROGRESS_SHARE` 까지, 출력 값 조립이 나머지를 채운다. `missing` 은 실행
    설정(`RunEnvironment.missing`)이 소유한다 — schema 1.2 의 팩터 그래프에는 결측 정책이 없다
    (P2-02 에서 인자로, P2-03 에서 필드 삭제).
    """

    computed = _compute_nodes(
        graph,
        observations=observations,
        missing=missing,
        parameters=parameters,
        checkpoint=checkpoint,
        progress=lambda fraction: progress(fraction * _NODES_PROGRESS_SHARE),
    )
    return _evaluation_from_computed(
        graph,
        observations,
        computed,
        checkpoint=checkpoint,
        progress=lambda fraction: progress(
            _NODES_PROGRESS_SHARE + (1.0 - _NODES_PROGRESS_SHARE) * fraction
        ),
    )


def _evaluation_from_computed(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    computed: dict[str, list[FactorComputedValue]],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> FactorEvaluation:
    """Build the public output from an already evaluated node cache.

    The trace use case calls this helper so its output values and per-node rows are projections
    of one `_compute_nodes` invocation, rather than two calculations that merely ought to agree.
    """
    raw_output = computed[graph.output_node_id]
    total = max(len(observations), 1)
    output: list[FactorValue] = []
    for index, (observation, value) in enumerate(
        _checkpointed(zip(observations, raw_output, strict=True), checkpoint)
    ):
        if index % _CHECKPOINT_BATCH == 0:
            progress(index / total)
        output.append(
            FactorValue(
                as_of=observation.as_of,
                security_id=observation.security_id,
                value=float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None,
            )
        )
    progress(1.0)
    return FactorEvaluation(output_node_id=graph.output_node_id, values=tuple(output))


def _compute_nodes(
    graph: FactorGraph,
    *,
    observations: tuple[FactorObservation, ...],
    missing: MissingPolicy,
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> dict[str, list[FactorComputedValue]]:
    """Evaluate every node reachable from the output once; the cache is the single value source.

    `evaluate_factor_graph` returns the output node; `_trace.trace_factor_graph` projects the whole
    cache. Both read the same lists so trace values equal evaluation values by construction.

    진행은 도달 가능한 노드마다 종류별 가중치(`_node_progress_weight`)로 몫을 나눠 노드 완료 때
    올리고, 창 연산이라 가장 오래 걸리는 시계열 노드는 종목 하나를 끝낼 때마다 그 몫 안에서 올린다
    (이슈 #162). 누적은 정수 가중치 합이라 끝값이 정확히 1.0 이다. 보고 빈도 조절은 호출자 몫이다.
    """
    evaluator = _NodeEvaluator(
        graph,
        observations=observations,
        missing=missing,
        parameters=parameters,
        checkpoint=checkpoint,
        progress=progress,
    )
    evaluator.evaluate(graph.output_node_id)
    return evaluator.computed


class _NodeEvaluator:
    """노드 캐시를 채우는 재귀 평가기. `_compute_nodes` 한 번의 호출 동안만 산다.

    예전에는 재귀를 클로저(`evaluate` 가 자기 이름을 부르는 내부 함수)로 했는데, 그 함수는 자기
    cell 로 자신을 참조하는 순환(함수 → cell → 함수)을 남긴다. cell 들이 `observations` 를 쥐어
    평가가 끝나도 관측 튜플 전체가 참조 카운트로 풀리지 않고 다음 전체 수집(2세대)을 기다렸다 —
    4년 실데이터에서 약 830만 객체, run 종료 뒤 수 GB(이슈 #196). 메서드 재귀는 호출마다 바운드
    메서드를 잠깐 만들 뿐 인스턴스가 자신을 붙잡지 않으므로, 호출이 끝나면 참조 카운트로 풀린다.
    """

    def __init__(
        self,
        graph: FactorGraph,
        *,
        observations: tuple[FactorObservation, ...],
        missing: MissingPolicy,
        parameters: tuple[ResolvedFactorParameter, ...],
        checkpoint: Callable[[], None],
        progress: Callable[[float], None],
    ) -> None:
        self._nodes = {node.node_id: node for node in graph.nodes}
        self._parameter_values = {
            parameter.parameter_id: parameter.value for parameter in parameters
        }
        self._observations = observations
        self._missing = missing
        self._checkpoint = checkpoint
        self._progress = progress
        self._computed: dict[str, list[FactorComputedValue]] = {}
        # 노드 출력 가운데 원장이 가린 셀(MASKED)에서 온 칸의 관측 index. 가린 셀은 사건 경계라
        # 시간을 가로지르는 연산이 건너면 결측이다(`_masked_reach`, #315). 드물어서 집합으로 든다.
        self._masked: dict[str, frozenset[int]] = {}
        self._by_security: dict[str, list[int]] | None = None
        self._total_weight = _reachable_progress_weight(graph.output_node_id, self._nodes)
        self._completed_weight = 0

    @property
    def computed(self) -> dict[str, list[FactorComputedValue]]:
        return self._computed

    def _securities(self) -> dict[str, list[int]]:
        """종목별 관측 index(날짜 순). 관측에만 달려 있어 시간 연산 노드들이 나눠 쓴다."""
        if self._by_security is None:
            self._by_security = _indices_by_security(
                self._observations, checkpoint=self._checkpoint
            )
        return self._by_security

    def _advance_within_node(self, node: ExpressionNode, fraction: float) -> None:
        self._progress(
            (self._completed_weight + fraction * _node_progress_weight(node)) / self._total_weight
        )

    def evaluate(self, node_id: str) -> list[FactorComputedValue]:
        self._checkpoint()
        if node_id in self._computed:
            return self._computed[node_id]
        try:
            node = self._nodes[node_id]
        except KeyError as error:
            raise ValueError(
                f"factor evaluation references unknown node — node_id={node_id!r}"
            ) from error
        dependencies = node_dependencies(node)
        inputs = [self.evaluate(dependency) for dependency in dependencies]
        # 같은 자리를 읽는 연산은 입력의 가린 칸을 그대로 잇는다
        masked = frozenset[int]().union(*(self._masked[dependency] for dependency in dependencies))
        values: list[FactorComputedValue]
        if isinstance(node, FieldNode):
            masked = _masked_cells(self._observations, node.field_id, checkpoint=self._checkpoint)
            values = _field_values(
                self._observations,
                node.field_id,
                self._missing,
                masked,
                checkpoint=self._checkpoint,
            )
        elif isinstance(node, ConstantNode):
            values = [node.value for _ in _checkpointed(self._observations, self._checkpoint)]
        elif isinstance(node, ParameterNode):
            if node.parameter_id not in self._parameter_values:
                raise ValueError(
                    "factor evaluation parameter is unresolved — "
                    f"node_id={node.node_id!r} parameter_id={node.parameter_id!r}"
                )
            value = self._parameter_values[node.parameter_id]
            if isinstance(value, (str, bool)):
                raise ValueError(
                    "numeric factor parameter has incompatible value — "
                    f"parameter_id={node.parameter_id!r} value={value!r}"
                )
            values = [float(value) for _ in _checkpointed(self._observations, self._checkpoint)]
        elif isinstance(node, BinaryNode):
            values = [
                _binary(node.operator, left, right)
                for left, right in _checkpointed(zip(*inputs, strict=True), self._checkpoint)
            ]
        elif isinstance(node, ComparisonNode):
            values = [
                _compare(node.operator, left, right)
                for left, right in _checkpointed(zip(*inputs, strict=True), self._checkpoint)
            ]
        elif isinstance(node, ConditionalNode):
            values = [
                true_value if predicate is True else false_value if predicate is False else None
                for predicate, true_value, false_value in _checkpointed(
                    zip(*inputs, strict=True), self._checkpoint
                )
            ]
        elif isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
            masked = _masked_reach(node, masked, self._securities(), checkpoint=self._checkpoint)
            values = _lag(
                inputs[0],
                self._securities(),
                node.periods or 0,
                masked,
                checkpoint=self._checkpoint,
            )
        elif isinstance(node, UnaryNode):
            values = _unary(node, inputs[0], checkpoint=self._checkpoint)
        elif isinstance(node, TimeSeriesNode):
            masked = _masked_reach(node, masked, self._securities(), checkpoint=self._checkpoint)
            values = _time_series(
                node,
                inputs[0],
                self._securities(),
                masked,
                checkpoint=self._checkpoint,
                advance=lambda fraction: self._advance_within_node(node, fraction),
            )
        elif isinstance(node, CrossSectionalNode):
            values = _cross_sectional(
                node, inputs[0], self._observations, checkpoint=self._checkpoint
            )
        elif isinstance(node, GroupNode):
            values = _group_transform(
                node, inputs[0], self._observations, checkpoint=self._checkpoint
            )
        _require_finite_values(
            node.node_id, values, self._observations, checkpoint=self._checkpoint
        )
        self._computed[node_id] = values
        self._masked[node_id] = masked
        self._completed_weight += _node_progress_weight(node)
        self._progress(self._completed_weight / self._total_weight)
        return values


# 노드 계산이 평가 진행에서 차지하는 몫. 나머지는 출력 값 조립이다(4년 구간 실측 약 4%).
_NODES_PROGRESS_SHARE = 0.96

# 노드 종류별 진행 가중치. 실데이터 실측(이슈 #162, 모멘텀 252)에서 시계열 노드가 필드 노드의
# 약 30배 시간을 썼다(창 길이만큼 값을 복사한다). 횡단면·그룹 노드는 날짜별 정렬이라 그 사이다.
_TIME_SERIES_PROGRESS_WEIGHT = 20
_SECTION_PROGRESS_WEIGHT = 3


def _node_progress_weight(node: ExpressionNode) -> int:
    if isinstance(node, TimeSeriesNode):
        return _TIME_SERIES_PROGRESS_WEIGHT
    if isinstance(node, (CrossSectionalNode, GroupNode)):
        return _SECTION_PROGRESS_WEIGHT
    return 1


def _reachable_progress_weight(output_node_id: str, nodes: dict[str, ExpressionNode]) -> int:
    """출력에서 도달 가능한 노드의 진행 가중치 합(진행 분모).

    모르는 참조는 평가가 예외로 거부하므로 세지 않는다.
    """

    seen: set[str] = set()
    pending = [output_node_id]
    while pending:
        node_id = pending.pop()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        pending.extend(node_dependencies(nodes[node_id]))
    return max(sum(_node_progress_weight(nodes[node_id]) for node_id in seen), 1)


def _require_finite_values(
    node_id: str,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> None:
    """Reject arithmetic overflow and non-finite adapter/reference values at one node boundary."""
    for index, value in _checkpointed(enumerate(values), checkpoint):
        if isinstance(value, float) and not math.isfinite(value):
            raise NonFiniteFactorCalculationError(
                node_id=node_id,
                value=value,
                observation=observations[index],
            )


def _masked_cells(
    observations: tuple[FactorObservation, ...],
    field_id: str,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> frozenset[int]:
    """필드가 원장이 가린 셀(MASKED)인 관측 index(#298)."""
    return frozenset(
        index
        for index, observation in _checkpointed(enumerate(observations), checkpoint)
        if any(field.masked for field in observation.fields if field.field_id == field_id)
    )


def _field_values(
    observations: tuple[FactorObservation, ...],
    field_id: str,
    missing_policy: MissingPolicy,
    masked: frozenset[int],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    raw: list[float | None] = []
    for observation in _checkpointed(observations, checkpoint):
        by_id = {field.field_id: field.value for field in observation.fields}
        value = by_id.get(field_id)
        raw.append(
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )
    # 결측 정책은 모르는 값만 채운다. 원장이 가린 셀은 비워 둔다(#298).
    if missing_policy is MissingPolicy.ZERO:
        return [
            0.0 if value is None and index not in masked else value
            for index, value in enumerate(raw)
        ]
    if missing_policy is MissingPolicy.CROSS_SECTIONAL_MEDIAN:
        by_date = _cross_section_indices(observations, checkpoint=checkpoint)
        result = list(raw)
        for indices in _checkpointed(by_date.values(), checkpoint):
            available = [
                value
                for index in _checkpointed(indices, checkpoint)
                if (value := raw[index]) is not None
            ]
            fill = median(available) if available else None
            for index in _checkpointed(indices, checkpoint):
                if result[index] is None and index not in masked:
                    result[index] = fill
        return result
    return list(raw)


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
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    if node.operator is UnaryOperator.NEGATE:
        return [
            -value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            for value in _checkpointed(values, checkpoint)
        ]
    # LAG 는 평가기가 시간 연산으로 `_lag` 를 부른다. 새 멤버는 여기서 즉시 드러난다
    raise ValueError(  # pragma: no cover - enum은 두 멤버뿐
        f"unary operator has no evaluation — operator={node.operator!r} node_id={node.node_id!r}"
    )


def _time_series(
    node: TimeSeriesNode,
    values: list[FactorComputedValue],
    by_security: dict[str, list[int]],
    masked: frozenset[int],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    advance: Callable[[float], None] = _noop_progress,
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    # 종목마다 관측 수가 달라 종목 수로 세면 이력이 긴 종목 구간에서 느려진다.
    # 처리한 관측 수로 센다.
    observation_count = max(len(values), 1)
    finished = 0
    for indices in _checkpointed(by_security.values(), checkpoint):
        for position, result_index in _checkpointed(enumerate(indices), checkpoint):
            end = position - node.lag + 1
            start = end - node.window
            if start < 0 or end <= 0 or result_index in masked:
                continue
            window = values_from_indices(values, indices[start:end], checkpoint=checkpoint)
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
        finished += len(indices)
        advance(finished / observation_count)
    return result


def _cross_sectional(
    node: CrossSectionalNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    groups = _cross_section_indices(observations, checkpoint=checkpoint)
    for indices in _checkpointed(groups.values(), checkpoint):
        numeric = [
            (index, number)
            for index in _checkpointed(indices, checkpoint)
            if (number := _as_number(values[index])) is not None
        ]
        if not numeric:
            continue
        if node.operator is CrossSectionalOperator.DEMEAN:
            center = mean(value for _, value in numeric)
            for index, value in _checkpointed(numeric, checkpoint):
                result[index] = value - center
        elif node.operator is CrossSectionalOperator.RANK:
            ranks = cross_sectional_rank([value for _, value in numeric])
            for (index, _), rank in _checkpointed(zip(numeric, ranks, strict=True), checkpoint):
                result[index] = rank
        elif node.operator is CrossSectionalOperator.ZSCORE:
            scores = cross_sectional_zscore([value for _, value in numeric])
            for (index, _), score in _checkpointed(zip(numeric, scores, strict=True), checkpoint):
                result[index] = score
        else:
            ordered = sorted(value for _, value in numeric)
            lower = quantile(ordered, node.lower_quantile)
            upper = quantile(ordered, node.upper_quantile)
            for index, value in _checkpointed(numeric, checkpoint):
                result[index] = min(max(value, lower), upper)
    return result


def _group_transform(
    node: GroupNode,
    values: list[FactorComputedValue],
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    grouped: dict[tuple[date, bool, str], list[int]] = {}
    for index, observation in _checkpointed(enumerate(observations), checkpoint):
        group = next(
            (field.value for field in observation.fields if field.field_id == node.group_field_id),
            None,
        )
        if isinstance(group, str):
            key = (observation.as_of, observation.universe_member, group)
            grouped.setdefault(key, []).append(index)
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _checkpointed(grouped.values(), checkpoint):
        numeric = [
            (index, number)
            for index in _checkpointed(indices, checkpoint)
            if (number := _as_number(values[index])) is not None
        ]
        if node.operator is GroupOperator.NEUTRALIZE:
            center = mean(value for _, value in numeric) if numeric else 0.0
            for index, value in _checkpointed(numeric, checkpoint):
                result[index] = value - center
        else:
            ranks = cross_sectional_rank([value for _, value in numeric])
            for (index, _), rank in _checkpointed(zip(numeric, ranks, strict=True), checkpoint):
                result[index] = rank
    return result


def _lag(
    values: list[FactorComputedValue],
    by_security: dict[str, list[int]],
    periods: int,
    masked: frozenset[int],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _checkpointed(by_security.values(), checkpoint):
        for position, index in _checkpointed(enumerate(indices), checkpoint):
            if position >= periods and index not in masked:
                result[index] = values[indices[position - periods]]
    return result


def _masked_reach(
    node: UnaryNode | TimeSeriesNode,
    masked: frozenset[int],
    by_security: dict[str, list[int]],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> frozenset[int]:
    """시간을 가로지르는 연산(`lag`·창 연산)의 출력 가운데 가린 입력 칸이 닿는 칸(#315).

    원장이 가린 셀은 사건 경계다(수정주가 층 이동 등). 같은 종목 자리 p 의 출력이 쓰는 입력 구간에
    가린 칸이 하나라도 있으면 그 출력도 가린 칸이고 값은 결측이다 — 경계를 가로지른 비교는 값이
    없다. 구간은 `lag` 가 k칸 앞부터 오늘까지 [p - k, p](두 시점을 견주려고 쓰는 연산이다), 창
    연산이 창 [p - lag - window + 1, p - lag] 이다. 창 연산은 창 안 값끼리만 견주므로 건너뛰는
    세션은 보지 않는다. 가리지 않은 결측은 경계가 아니라 건너도 된다.
    """
    if not masked:
        return masked
    if isinstance(node, TimeSeriesNode):
        first, last = node.lag, node.lag + node.window - 1
    else:
        first, last = 0, node.periods or 0
    reached: set[int] = set()
    for indices in _checkpointed(by_security.values(), checkpoint):
        for position, index in _checkpointed(enumerate(indices), checkpoint):
            if index in masked:
                reached.update(indices[position + first : position + last + 1])
    return frozenset(reached)


def _cross_section_indices(
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[tuple[date, bool], list[int]]:
    """Peer groups for cross-sectional operators: one group per (as_of, universe_member).

    Membership is part of the key so a non-member row cannot enter a member's cross-section
    (D-001). Non-members are still grouped among themselves, which keeps the result list aligned
    with `observations` positionally; the portfolio compiler drops those rows afterwards.
    """
    grouped: dict[tuple[date, bool], list[int]] = {}
    for index, observation in _checkpointed(enumerate(observations), checkpoint):
        grouped.setdefault((observation.as_of, observation.universe_member), []).append(index)
    return grouped


def _indices_by_security(
    observations: tuple[FactorObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, observation in _checkpointed(enumerate(observations), checkpoint):
        grouped.setdefault(observation.security_id, []).append(index)
    for indices in _checkpointed(grouped.values(), checkpoint):
        indices.sort(key=lambda index: observations[index].as_of)
    return grouped


def values_from_indices(
    values: list[FactorComputedValue],
    indices: list[int],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[float]:
    return [
        number
        for index in _checkpointed(indices, checkpoint)
        if (number := _as_number(values[index])) is not None
    ]


def _is_number(value: FactorComputedValue) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_number(value: FactorComputedValue) -> float | None:
    return float(value) if _is_number(value) else None
