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
# 노드 값이 대표하는 시점 구간: 필드마다 오늘에서 (가까운 쪽, 먼 쪽) 몇 칸 앞인가(`_value_spans`)
_Spans: TypeAlias = dict[str, tuple[int, int]]

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
    # 원장이 무효라고 가린 셀(값 None, 원천 셀 종류 MASKED). 실행 결측 정책이 채우지 않고, 값 구간
    # 규칙(`_value_spans`)이 사건 경계로 다룬다 — 무엇을 가리나는 원장, 무엇을 채우나는 결측
    # 정책이다(#298·#337).
    masked: bool = False

    def __post_init__(self) -> None:
        # 가린 셀은 값이 없다. 값이 실려 오면 결측 정책과 중앙값 모집단이 그 값을 쓰므로, raw
        # 포트·연구 패널 셀과 같은 계약으로 만들 때 막는다(#311 리뷰 P3-2)
        if self.masked and self.value is not None:
            raise ValueError(
                "masked factor input must not carry a value — "
                f"field_id={self.field_id!r} value={self.value!r}"
            )


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
    # 원장이 가린 칸 때문에 값이 없다. 출력 노드 추적 상태 `masked` 와 같은 판정이고, 결측 탈락 중
    # 원장이 가린 몫을 기준일 요약이 센다(lang2 P4-03, #350)
    masked: bool = False


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

    evaluator = _compute_nodes(
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
        evaluator,
        checkpoint=checkpoint,
        progress=lambda fraction: progress(
            _NODES_PROGRESS_SHARE + (1.0 - _NODES_PROGRESS_SHARE) * fraction
        ),
    )


def _evaluation_from_computed(
    graph: FactorGraph,
    observations: tuple[FactorObservation, ...],
    evaluator: _NodeEvaluator,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> FactorEvaluation:
    """Build the public output from an already evaluated node cache.

    The trace use case calls this helper so its output values and per-node rows are projections
    of one `_compute_nodes` invocation, rather than two calculations that merely ought to agree.
    """
    raw_output = evaluator.computed[graph.output_node_id]
    masked = evaluator.masked_output(graph.output_node_id)
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
                masked=index in masked,
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
) -> _NodeEvaluator:
    """출력에서 닿는 노드를 한 번씩 평가한 평가기. 그 노드 캐시가 값의 유일한 출처다.

    `evaluate_factor_graph` 는 출력 노드만, `_trace` 는 캐시 전체(`computed`)와 원장이 가린
    칸 때문에 결측으로 둔 칸(`masked`)을 읽는다. 같은 목록을 읽으므로 추적 값과 평가 값은 만들
    때부터 같다.

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
    return evaluator


class _NodeEvaluator:
    """노드 캐시를 채우는 재귀 평가기. `_compute_nodes` 가 만들어 돌려주고, 그 결과를 읽는
    호출자(평가·추적 투영)가 쥐는 동안만 산다(#350).

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
        # 노드마다 값이 대표하는 시점 구간과, 필드마다 원장이 가린 셀(MASKED)의 자리(그 종목의 날짜
        # 순 관측 index, 그 안의 순번). 가린 셀은 사건 경계라 값의 구간에 들면 결측이다(#315·#337).
        self._spans: dict[str, _Spans] = {}
        self._boundaries: dict[str, list[tuple[list[int], int]]] = {}
        # 노드마다 그 규칙으로 결측이 된 출력 칸(필드 노드는 가린 셀 자체). 추적이 사유로
        # 싣는다(#350)
        self._masked: dict[str, frozenset[int]] = {}
        self._by_security: dict[str, list[int]] | None = None
        self._total_weight = _reachable_progress_weight(graph.output_node_id, self._nodes)
        self._completed_weight = 0

    @property
    def computed(self) -> dict[str, list[FactorComputedValue]]:
        return self._computed

    @property
    def masked(self) -> dict[str, frozenset[int]]:
        """노드마다 원장이 가린 칸 때문에 결측이 된 출력 칸. 판정은 값 구간 규칙(`_value_spans`)
        이다."""
        return self._masked

    def masked_output(self, node_id: str) -> frozenset[int]:
        """추적 상태가 `masked` 인 칸 — 가린 칸이 구간에 들어도 이력이 모자란 칸은 뺀다.

        그런 칸은 가림이 없어도 값이 없어서, 추적도 이력 부족을 먼저 말한다(#389 리뷰 P3-2).
        """
        cells = self._masked[node_id]
        if not cells:
            return cells
        node, by_security = self._nodes[node_id], self._securities()
        return frozenset(
            index
            for index in _checkpointed(cells, self._checkpoint)
            if not _warming_up(node, index, self._observations, by_security)
        )

    def _securities(self) -> dict[str, list[int]]:
        """종목별 관측 index(날짜 순). 관측에만 달려 있어 시간 연산 노드들이 나눠 쓴다."""
        if self._by_security is None:
            self._by_security = _indices_by_security(
                self._observations, checkpoint=self._checkpoint
            )
        return self._by_security

    def _located(self, cells: frozenset[int]) -> list[tuple[list[int], int]]:
        """가린 셀마다 (그 종목의 날짜 순 관측 index, 그 안의 순번). 가린 셀이 없으면 종목 index 를
        만들지 않는다."""
        if not cells:
            return []
        by_security = self._securities()
        located: list[tuple[list[int], int]] = []
        for index in _checkpointed(cells, self._checkpoint):
            indices = by_security[self._observations[index].security_id]
            located.append((indices, indices.index(index)))
        return located

    def _masked_in(self, spans: _Spans) -> frozenset[int]:
        """값의 시점 구간에 원장이 가린 칸이 드는 출력 칸(#315·#337).

        자리 p 의 값이 필드 F 를 [p - far, p - near] 에서 읽으면, F 의 가린 칸 q 는 출력 [q + near,
        q + far] 에 닿는다. 가리지 않은 결측은 경계가 아니라 건너도 된다.
        """
        reached: set[int] = set()
        for field_id, (near, far) in spans.items():
            for indices, position in _checkpointed(self._boundaries[field_id], self._checkpoint):
                reached.update(indices[position + near : position + far + 1])
        return frozenset(reached)

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
        spans = _value_spans(node, [self._spans[dependency] for dependency in dependencies])
        # 필드 노드의 가린 칸은 원장이 가린 셀 자체라 이미 값이 없다. 나머지 노드는 구간에 가린 칸이
        # 드는 칸을 먼저 구해 창 연산이 그 칸의 창을 읽지 않게 한다
        masked = frozenset[int]() if isinstance(node, FieldNode) else self._masked_in(spans)
        values: list[FactorComputedValue]
        if isinstance(node, FieldNode):
            values, masked = _field_values(
                self._observations, node.field_id, self._missing, checkpoint=self._checkpoint
            )
            self._boundaries[node.field_id] = self._located(masked)
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
            values = _lag(
                inputs[0], self._securities(), node.periods or 0, checkpoint=self._checkpoint
            )
        elif isinstance(node, UnaryNode):
            values = _unary(node, inputs[0], checkpoint=self._checkpoint)
        elif isinstance(node, TimeSeriesNode):
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
        # 구간에 가린 칸이 드는 칸은 결측이다 — 두 값을 섞는 연산은 입력이 모두 값이어도 여기서
        # 가려진다(#337)
        for index in _checkpointed(masked, self._checkpoint):
            values[index] = None
        _require_finite_values(
            node.node_id, values, self._observations, checkpoint=self._checkpoint
        )
        self._computed[node_id] = values
        self._spans[node_id] = spans
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


def _field_values(
    observations: tuple[FactorObservation, ...],
    field_id: str,
    missing_policy: MissingPolicy,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> tuple[list[FactorComputedValue], frozenset[int]]:
    """필드 값과, 그 가운데 원장이 가린 셀(MASKED)의 관측 index 를 관측을 한 번 훑어 낸다(#298)."""
    raw: list[float | None] = []
    masked: set[int] = set()
    for index, observation in _checkpointed(enumerate(observations), checkpoint):
        cell = {field.field_id: field for field in observation.fields}.get(field_id)
        if cell is not None and cell.masked:
            masked.add(index)
        value = None if cell is None else cell.value
        raw.append(
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )
    # 결측 정책은 모르는 값만 채운다. 원장이 가린 셀은 비워 둔다(#298).
    values: list[FactorComputedValue] = list(raw)
    if missing_policy is MissingPolicy.ZERO:
        values = [
            0.0 if value is None and index not in masked else value
            for index, value in enumerate(raw)
        ]
    elif missing_policy is MissingPolicy.CROSS_SECTIONAL_MEDIAN:
        by_date = _cross_section_indices(observations, checkpoint=checkpoint)
        for indices in _checkpointed(by_date.values(), checkpoint):
            available = [
                value
                for index in _checkpointed(indices, checkpoint)
                if (value := raw[index]) is not None
            ]
            fill = median(available) if available else None
            for index in _checkpointed(indices, checkpoint):
                if values[index] is None and index not in masked:
                    values[index] = fill
    return values, frozenset(masked)


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
            # 구간에 가린 칸이 드는 칸은 어차피 결측이라 창을 읽지 않는다
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
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[FactorComputedValue]:
    result: list[FactorComputedValue] = [None] * len(values)
    for indices in _checkpointed(by_security.values(), checkpoint):
        for position, index in _checkpointed(enumerate(indices), checkpoint):
            if position >= periods:
                result[index] = values[indices[position - periods]]
    return result


def _value_spans(node: ExpressionNode, inputs: list[_Spans]) -> _Spans:
    """노드 값이 대표하는 시점 구간 — 필드마다 오늘에서 (가까운 쪽, 먼 쪽) 몇 칸 앞인가(#315·#337).

    원장이 가린 셀은 사건 경계라(수정주가 층 이동 등) 이 구간에 가린 칸이 들면 값은 결측이다
    (`_NodeEvaluator._masked_in`). `lag` 는 구간을 k칸 옮기기만 한다 — 단독 출력은 k세션 전 값 그
    자체다. 창 연산은 창만큼 넓힌다 — 건너뛰는 세션은 창 밖이라, 창 안 값끼리 견주는 12-1 모멘텀은
    그 세션의 가린 칸과 무관하다. 두 값 이상을 섞는 연산(이항·비교·조건)은 필드마다 두 구간을 덮는
    구간이라, 오늘 값을 k세션 전 값이나 건너뛴 창과 견주면 그 사이의 가린 칸에서 결측이다. 같은 자리
    연산(부정·횡단면·그룹)은 입력 구간 그대로이고, 상수·파라미터는 구간이 없다.
    """
    if isinstance(node, FieldNode):
        return {node.field_id: (0, 0)}
    if isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
        shift = node.periods or 0
        return {
            field_id: (near + shift, far + shift) for field_id, (near, far) in inputs[0].items()
        }
    if isinstance(node, TimeSeriesNode):
        return {
            field_id: (near + node.lag, far + node.lag + node.window - 1)
            for field_id, (near, far) in inputs[0].items()
        }
    hull: _Spans = {}
    for spans in inputs:
        for field_id, (near, far) in spans.items():
            low, high = hull.get(field_id, (near, far))
            hull[field_id] = (min(low, near), max(high, far))
    return hull


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
