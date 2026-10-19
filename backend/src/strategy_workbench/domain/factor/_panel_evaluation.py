"""열 지향 팩터 평가기(columnar-tape spec D1~D7, T3-01).

`_evaluation.py` 의 객체 평가기와 같은 노드 의미를 numpy 열로 계산한다. 모든 노드·칸의 값이 객체
경로와 비트 단위로 같아야 한다(spec D1). 대조는 `tests/parity` L1 이 한다.

SoT: 팩터 값의 계산자는 `application/portfolio_design` 하나이고, 횡단면 공식의 owner 는
`_statistics.py` 하나다. 이 모듈은 같은 노드 의미의 두 번째 구현이며 대조 기간(T3-01 ~ T7-01)에만
허용된다. 운영 경로에는 아직 연결하지 않는다(spec D8). T7-01 에서 객체 평가기를 지우고 이 구현만
남긴다. 값 구간 규칙(`_value_spans`), 진행 가중치, 예외 타입은 `_evaluation.py` 의 것을 그대로 쓰고
다시 적지 않는다.

T3-01 범위는 field·constant·parameter·unary(negate·lag)·binary·comparison·conditional 이다.
time_series(T3-02~T3-04)·cross_sectional·group(T3-05)과 `cross_sectional_median` 채움(T3-05)은
`UnsupportedPanelEvaluationError` 로 거절한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from numpy.typing import NDArray

from ._evaluation import (
    FactorObservation,
    NonFiniteFactorCalculationError,
    _node_progress_weight,
    _noop_checkpoint,
    _noop_progress,
    _reachable_progress_weight,
    _Spans,
    _value_spans,
)
from ._history import node_read_window
from ._nodes import (
    FILLING_MISSING_POLICIES,
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    MissingPolicy,
    ParameterNode,
    UnaryNode,
    UnaryOperator,
)
from ._planning import ResolvedFactorParameter
from ._validation import node_dependencies

# 원소 단위 전체 연산은 이 행 수마다 checkpoint 를 부른다(spec D7).
_CHUNK_ROWS = 1 << 18


class UnsupportedPanelEvaluationError(NotImplementedError):
    """열 평가기가 아직 구현하지 않은 노드·채움. 운영 경로에 연결하기 전까지만 있다."""


def _chunks(rows: int, checkpoint: Callable[[], None]) -> Iterator[slice]:
    for low in range(0, rows, _CHUNK_ROWS):
        checkpoint()
        yield slice(low, min(low + _CHUNK_ROWS, rows))


@dataclass(frozen=True, eq=False)
class FactorFieldColumn:
    """숫자로 읽는 필드 하나. 모든 배열은 패널 행 수와 길이가 같다."""

    field_id: str
    value: NDArray[np.float64]  # 숫자가 아닌 칸은 0.0
    valid: NDArray[np.bool_]  # 숫자 값이 있다. 생략·None·bool·str 은 False(`_field_values`)
    masked: NDArray[np.bool_]  # 원장이 가린 셀(값 없음)


@dataclass(frozen=True, eq=False)
class FactorGroupColumn:
    """그룹으로 읽는 필드 하나. 문자열이 아닌 칸은 -1 이고 어느 그룹에도 들지 않는다."""

    field_id: str
    codes: NDArray[np.int32]
    categories: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class FactorPanel:
    """평가 입력. 행 i 가 관측 i 다(정본 순서는 (as_of, security_id)).

    종목 우선 안정 순열(`order`), 종목 안 행 위치(`position`), 정렬 위치마다 그 종목 구간의
    끝(`segment_end`)은 생성 때 한 번 계산한다. 창은 세션 수가 아니라 종목 안 행 위치를 센다(D-003).
    """

    dates: tuple[date, ...]  # 오름차순. `row_date` 가 위치를 가리킨다
    securities: tuple[str, ...]  # 오름차순. `row_security` 가 위치를 가리킨다
    row_date: NDArray[np.int32]
    row_security: NDArray[np.int32]
    universe_member: NDArray[np.bool_]
    fields: Mapping[str, FactorFieldColumn]
    groups: Mapping[str, FactorGroupColumn] = field(default_factory=dict)
    order: NDArray[np.intp] = field(init=False)  # 정렬 위치 → 행
    rank: NDArray[np.intp] = field(init=False)  # 행 → 정렬 위치
    position: NDArray[np.intp] = field(init=False)  # 행 → 종목 안 행 위치
    segment_end: NDArray[np.intp] = field(init=False)  # 정렬 위치 → 그 종목 구간 끝(반열린)

    def __post_init__(self) -> None:
        rows = self.row_date.shape[0]
        arrays = [self.row_security, self.universe_member]
        arrays += [array for column in self.fields.values() for array in _field_arrays(column)]
        arrays += [column.codes for column in self.groups.values()]
        if any(array.shape != (rows,) for array in arrays):
            raise ValueError(
                "factor panel arrays must share one row length — "
                f"rows={rows} shapes={sorted({array.shape for array in arrays})}"
            )
        # 같은 종목 안에서는 날짜 순, 날짜도 같으면 입력 순서(`_indices_by_security` 의 안정 정렬)
        order = np.lexsort((self.row_date, self.row_security)).astype(np.intp)
        rank = np.empty(rows, dtype=np.intp)
        rank[order] = np.arange(rows, dtype=np.intp)
        ordered = self.row_security[order]
        first = np.ones(rows, dtype=bool)
        first[1:] = ordered[1:] != ordered[:-1]
        starts = np.flatnonzero(first)
        segment = np.cumsum(first) - 1
        ends = np.append(starts[1:], rows)
        position = np.empty(rows, dtype=np.intp)
        position[order] = np.arange(rows, dtype=np.intp) - starts[segment]
        object.__setattr__(self, "order", order)
        object.__setattr__(self, "rank", rank)
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "segment_end", ends[segment].astype(np.intp))

    @property
    def rows(self) -> int:
        return int(self.row_date.shape[0])

    @classmethod
    def from_observations(
        cls, observations: tuple[FactorObservation, ...], graph: FactorGraph
    ) -> FactorPanel:
        """객체 관측을 `graph` 가 읽는 필드만 담은 패널로 바꾼다(대조·경계 함수용, spec D8 4단계).

        같은 관측에 같은 필드가 둘이면 뒤 셀을 쓴다(`_field_values` 와 같다).
        """
        numeric = dict.fromkeys(
            node.field_id for node in graph.nodes if isinstance(node, FieldNode)
        )
        grouped = dict.fromkeys(
            node.group_field_id for node in graph.nodes if isinstance(node, GroupNode)
        )
        dates = tuple(sorted({row.as_of for row in observations}))
        securities = tuple(sorted({row.security_id for row in observations}))
        date_codes = {value: code for code, value in enumerate(dates)}
        security_codes = {value: code for code, value in enumerate(securities)}
        values: dict[str, list[float]] = {field_id: [] for field_id in numeric}
        valid: dict[str, list[bool]] = {field_id: [] for field_id in numeric}
        masked: dict[str, list[bool]] = {field_id: [] for field_id in numeric}
        labels: dict[str, list[str | None]] = {field_id: [] for field_id in grouped}
        for row in observations:
            cells = {cell.field_id: cell for cell in row.fields}
            for field_id in numeric:
                cell = cells.get(field_id)
                value = None if cell is None else cell.value
                number = isinstance(value, (int, float)) and not isinstance(value, bool)
                values[field_id].append(float(value) if isinstance(value, (int, float)) else 0.0)
                valid[field_id].append(number)
                masked[field_id].append(cell is not None and cell.masked)
            for field_id in grouped:
                cell = cells.get(field_id)
                labels[field_id].append(
                    cell.value if cell is not None and isinstance(cell.value, str) else None
                )
        groups: dict[str, FactorGroupColumn] = {}
        for field_id, column in labels.items():
            categories = tuple(sorted({label for label in column if label is not None}))
            codes = {label: code for code, label in enumerate(categories)}
            groups[field_id] = FactorGroupColumn(
                field_id,
                np.array([-1 if label is None else codes[label] for label in column], np.int32),
                categories,
            )
        return cls(
            dates=dates,
            securities=securities,
            row_date=np.array([date_codes[row.as_of] for row in observations], np.int32),
            row_security=np.array(
                [security_codes[row.security_id] for row in observations], np.int32
            ),
            universe_member=np.array([row.universe_member for row in observations], bool),
            fields={
                field_id: FactorFieldColumn(
                    field_id,
                    np.array(values[field_id], np.float64),
                    np.array(valid[field_id], bool),
                    np.array(masked[field_id], bool),
                )
                for field_id in numeric
            },
            groups=groups,
        )


def _field_arrays(column: FactorFieldColumn) -> tuple[np.ndarray, ...]:
    return (column.value, column.valid, column.masked)


@dataclass(frozen=True, eq=False)
class FactorNodeColumn:
    """노드 하나의 값 열(숫자 float64 또는 bool), 유효 열, 가린 칸 열.

    `valid` 가 False 인 칸은 None 이고 그 칸의 `values` 는 의미가 없다. `masked` 는 원장이 가린 칸
    때문에 결측이 된 칸이다(`_NodeEvaluator.masked`).
    """

    values: NDArray[np.float64] | NDArray[np.bool_]
    valid: NDArray[np.bool_]
    masked: NDArray[np.bool_]

    @property
    def boolean(self) -> bool:
        return self.values.dtype == np.bool_


class FactorNodeCache:
    """출력에서 닿는 노드의 열. 키 순서는 객체 평가기 `computed` 와 같은 완료 순서다."""

    def __init__(self, columns: dict[str, FactorNodeColumn]) -> None:
        self._columns = columns

    @property
    def columns(self) -> Mapping[str, FactorNodeColumn]:
        return self._columns

    def value(self, node_id: str, row: int) -> float | bool | None:
        """trace 용 칸 하나. 객체 평가기 칸과 같은 Python 값(float·bool·None)이다."""
        column = self._columns[node_id]
        if not column.valid[row]:
            return None
        return bool(column.values[row]) if column.boolean else float(column.values[row])


def evaluate_panel_nodes(
    graph: FactorGraph,
    *,
    panel: FactorPanel,
    missing: MissingPolicy,
    parameters: tuple[ResolvedFactorParameter, ...] = (),
    checkpoint: Callable[[], None] = _noop_checkpoint,
    progress: Callable[[float], None] = _noop_progress,
) -> FactorNodeCache:
    """출력에서 닿는 노드를 한 번씩 평가한다(`_compute_nodes` 의 열판).

    진행은 객체 평가기와 같은 노드 가중치로 노드 완료 때 올리고 끝값은 1.0 이다. checkpoint 는
    노드마다, 원소 연산은 `_CHUNK_ROWS` 행마다 부른다.
    """
    evaluator = _PanelEvaluator(graph, panel, missing, parameters, checkpoint, progress)
    evaluator.evaluate(graph.output_node_id)
    return FactorNodeCache(evaluator.columns)


_UNSUPPORTED_KINDS = frozenset({"time_series", "cross_sectional", "group"})


class _PanelEvaluator:
    def __init__(
        self,
        graph: FactorGraph,
        panel: FactorPanel,
        missing: MissingPolicy,
        parameters: tuple[ResolvedFactorParameter, ...],
        checkpoint: Callable[[], None],
        progress: Callable[[float], None],
    ) -> None:
        self._nodes = {node.node_id: node for node in graph.nodes}
        self._output_node_id = graph.output_node_id
        self._parameter_values = {item.parameter_id: item.value for item in parameters}
        self._panel = panel
        self._missing = missing
        self._checkpoint = checkpoint
        self._progress = progress
        self.columns: dict[str, FactorNodeColumn] = {}
        self._spans: dict[str, _Spans] = {}
        # 필드마다 원장이 가린 셀의 정렬 위치(`_NodeEvaluator._boundaries` 의 열판)
        self._boundaries: dict[str, NDArray[np.intp]] = {}
        self._total_weight = _reachable_progress_weight(graph.output_node_id, self._nodes)
        self._completed_weight = 0

    def evaluate(self, node_id: str) -> FactorNodeColumn:
        self._checkpoint()
        if node_id in self.columns:
            return self.columns[node_id]
        try:
            node = self._nodes[node_id]
        except KeyError as error:
            raise ValueError(
                f"factor evaluation references unknown node — node_id={node_id!r}"
            ) from error
        if node.kind in _UNSUPPORTED_KINDS:
            raise UnsupportedPanelEvaluationError(
                "columnar evaluator does not implement this node yet (T3-02~T3-05) — "
                f"node_id={node_id!r} kind={node.kind!r}"
            )
        dependencies = node_dependencies(node)
        inputs = [self.evaluate(dependency) for dependency in dependencies]
        spans = _value_spans(node, [self._spans[dependency] for dependency in dependencies])
        rows = self._panel.rows
        if isinstance(node, FieldNode):
            column = self._field(node)
            masked = column.masked
            self._boundaries[node.field_id] = self._panel.rank[np.flatnonzero(masked)]
            values, valid = column.value, column.valid
        else:
            masked = self._masked_in(spans)
            values, valid = self._compute(node, inputs, rows)
        # 구간에 가린 칸이 드는 칸은 결측이다(`_NodeEvaluator.evaluate` 와 같은 자리)
        valid = valid & ~masked
        if node_id == self._output_node_id:
            values, valid = self._filled(values, valid, masked)
        self._require_finite(node_id, values, valid)
        result = FactorNodeColumn(values, valid, masked)
        self.columns[node_id] = result
        self._spans[node_id] = spans
        self._completed_weight += _node_progress_weight(node)
        self._progress(self._completed_weight / self._total_weight)
        return result

    def _field(self, node: FieldNode) -> FactorFieldColumn:
        try:
            return self._panel.fields[node.field_id]
        except KeyError as error:
            raise ValueError(
                "factor panel has no column for the field node — "
                f"node_id={node.node_id!r} field_id={node.field_id!r} "
                f"fields={sorted(self._panel.fields)}"
            ) from error

    def _compute(
        self, node: object, inputs: list[FactorNodeColumn], rows: int
    ) -> tuple[NDArray[np.float64] | NDArray[np.bool_], NDArray[np.bool_]]:
        if isinstance(node, ConstantNode):
            # hydrate 가 상수를 float 로 바꾼다(`_hydrate.py`). int·bool 상수는 객체 경로에서 Python
            # 타입이 남아(`-0` 이 0, bool 은 참거짓 열) 열 float 와 비트가 갈리므로 받지 않는다.
            if not isinstance(node.value, float):
                raise ValueError(
                    "columnar evaluator needs a float constant — "
                    f"node_id={node.node_id!r} value={node.value!r}"
                )
            return np.full(rows, node.value, np.float64), np.ones(rows, bool)
        if isinstance(node, ParameterNode):
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
            return np.full(rows, float(value), np.float64), np.ones(rows, bool)
        if isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
            return self._lag(node, inputs[0], rows)
        if isinstance(node, UnaryNode):
            source = inputs[0]
            # bool 은 숫자가 아니라 부정하지 않는다(`_unary`). `np.negative` 는 −0.0 을 지킨다
            if source.boolean:
                return np.zeros(rows, np.float64), np.zeros(rows, bool)
            return self._map(
                rows, np.float64, lambda s: np.negative(source.values[s])
            ), source.valid
        if isinstance(node, BinaryNode):
            return self._binary(node.operator, inputs[0], inputs[1], rows)
        if isinstance(node, ComparisonNode):
            return self._compare(node.operator, inputs[0], inputs[1], rows)
        if isinstance(node, ConditionalNode):
            return self._conditional(node, *inputs, rows=rows)
        raise UnsupportedPanelEvaluationError(  # pragma: no cover - 종류는 위에서 모두 갈랐다
            f"columnar evaluator has no rule for the node — node={node!r}"
        )

    def _map(
        self, rows: int, dtype: type, fill: Callable[[slice], np.ndarray]
    ) -> NDArray[np.float64] | NDArray[np.bool_]:
        """원소 연산을 `_CHUNK_ROWS` 행씩 나눠 채운다. 넘침·0 나누기 경고는 결과 값으로 다룬다."""
        out = np.empty(rows, dtype)
        with np.errstate(all="ignore"):
            for chunk in _chunks(rows, self._checkpoint):
                out[chunk] = fill(chunk)
        return out

    def _binary(
        self,
        operator: BinaryOperator,
        left: FactorNodeColumn,
        right: FactorNodeColumn,
        rows: int,
    ) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
        # 숫자가 아닌 입력(bool 열)은 None 이다(`_binary`). IEEE 연산 한 번이라 객체 경로와 같다
        if left.boolean or right.boolean:
            return np.zeros(rows, np.float64), np.zeros(rows, bool)
        a, b = left.values, right.values
        valid = left.valid & right.valid
        if operator is BinaryOperator.ADD:
            values = self._map(rows, np.float64, lambda s: a[s] + b[s])
        elif operator is BinaryOperator.SUBTRACT:
            values = self._map(rows, np.float64, lambda s: a[s] - b[s])
        elif operator is BinaryOperator.MULTIPLY:
            values = self._map(rows, np.float64, lambda s: a[s] * b[s])
        else:
            # 분모가 0(−0.0 포함)이면 None 이다
            valid = valid & (b != 0)
            values = self._map(rows, np.float64, lambda s: a[s] / b[s])
        return values, valid  # pyright: ignore[reportReturnType]  # reason: dtype=float64 로 채웠다

    def _compare(
        self,
        operator: FactorComparisonOperator,
        left: FactorNodeColumn,
        right: FactorNodeColumn,
        rows: int,
    ) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
        if left.boolean or right.boolean:
            return np.zeros(rows, bool), np.zeros(rows, bool)
        a, b = left.values, right.values
        compare = {
            FactorComparisonOperator.GREATER_THAN: np.greater,
            FactorComparisonOperator.GREATER_THAN_OR_EQUAL: np.greater_equal,
            FactorComparisonOperator.LESS_THAN: np.less,
            FactorComparisonOperator.LESS_THAN_OR_EQUAL: np.less_equal,
            FactorComparisonOperator.EQUAL: np.equal,
        }[operator]
        values = self._map(rows, np.bool_, lambda s: compare(a[s], b[s]))
        return values, left.valid & right.valid  # pyright: ignore[reportReturnType]  # reason: dtype=bool 로 채웠다

    def _conditional(
        self,
        node: ConditionalNode,
        predicate: FactorNodeColumn,
        true: FactorNodeColumn,
        false: FactorNodeColumn,
        *,
        rows: int,
    ) -> tuple[NDArray[np.float64] | NDArray[np.bool_], NDArray[np.bool_]]:
        # 가지의 값 타입이 칸마다 섞이면 열 하나에 담을 수 없다. 검증기가 두 가지 타입을 맞춘다
        if true.boolean != false.boolean:
            raise UnsupportedPanelEvaluationError(
                "conditional branches must share one value type — "
                f"node_id={node.node_id!r} true_boolean={true.boolean} "
                f"false_boolean={false.boolean}"
            )
        # `is True`·`is False` 만 가지를 고른다. 숫자 predicate 는 모두 None 이다
        if predicate.boolean:
            flags = predicate.values.astype(np.bool_, copy=False)
            take_true = predicate.valid & flags
            take_false = predicate.valid & ~flags
        else:
            take_true = take_false = np.zeros(rows, bool)
        values = self._map(
            rows,
            true.values.dtype.type,
            lambda s: np.where(take_true[s], true.values[s], false.values[s]),
        )
        return values, (take_true & true.valid) | (take_false & false.valid)

    def _lag(
        self, node: UnaryNode, source: FactorNodeColumn, rows: int
    ) -> tuple[NDArray[np.float64] | NDArray[np.bool_], NDArray[np.bool_]]:
        """세션 창 정본(`node_read_window`)의 한 칸을 종목 안 행 위치로 옮긴다. 이력 부족은 None."""
        periods = node_read_window(node).far
        panel = self._panel
        reached = panel.position >= periods
        origin = np.where(reached, panel.rank - periods, 0)
        source_row = panel.order[origin] if rows else origin
        values = self._map(rows, source.values.dtype.type, lambda s: source.values[source_row[s]])
        return values, reached & source.valid[source_row]

    def _masked_in(self, spans: _Spans) -> NDArray[np.bool_]:
        """값의 시점 구간에 가린 칸이 드는 칸(`_NodeEvaluator._masked_in` 의 열판).

        가린 칸 q 는 같은 종목의 출력 [q + near, q + far] 에 닿는다. 정렬 위치에서 구간 시작에 +1,
        끝(종목 구간 끝에서 자른다) 다음에 −1 을 두고 누적해 한 번에 표시한다.
        """
        panel = self._panel
        rows = panel.rows
        steps = np.zeros(rows + 1, np.int64)
        for field_id, (near, far) in spans.items():
            cells = self._boundaries[field_id]
            self._checkpoint()
            start = cells + near
            stop = np.minimum(cells + far + 1, panel.segment_end[cells])
            kept = start < stop
            np.add.at(steps, start[kept], 1)
            np.add.at(steps, stop[kept], -1)
        masked = np.empty(rows, bool)
        masked[panel.order] = np.cumsum(steps[:rows]) > 0
        return masked

    def _filled(
        self,
        values: NDArray[np.float64] | NDArray[np.bool_],
        valid: NDArray[np.bool_],
        masked: NDArray[np.bool_],
    ) -> tuple[NDArray[np.float64] | NDArray[np.bool_], NDArray[np.bool_]]:
        """결측 정책의 채움(`_NodeEvaluator._filled` 의 열판).

        채움 자리는 셋이다: 횡단면 노드 입력, 그룹 노드 입력, 그래프 출력. T3-01 은 그래프 출력의
        `zero` 만 한다. 두 노드 입력 자리는 그 노드와 함께 T3-05 에서 넣고, `cross_sectional_median`
        도 T3-05 다. `zero` 는 같은 (as_of, universe_member) 동료에 숫자 값이 하나라도 있을 때
        가리지 않은 빈칸을 0.0 으로 채운다. bool 은 숫자가 아니라 bool 열은 채우지 않는다.
        """
        if self._missing not in FILLING_MISSING_POLICIES:
            return values, valid
        if self._missing is not MissingPolicy.ZERO:
            raise UnsupportedPanelEvaluationError(
                "columnar evaluator fills cross_sectional_median from T3-05 — "
                f"missing={self._missing.value!r} output_node_id={self._output_node_id!r}"
            )
        if values.dtype == np.bool_:
            return values, valid
        panel = self._panel
        peers = panel.row_date.astype(np.int64) * 2 + panel.universe_member
        has_number = np.bincount(peers[valid], minlength=2 * len(panel.dates)) > 0
        fill = ~valid & ~masked & has_number[peers]
        return np.where(fill, 0.0, values), valid | fill

    def _require_finite(
        self,
        node_id: str,
        values: NDArray[np.float64] | NDArray[np.bool_],
        valid: NDArray[np.bool_],
    ) -> None:
        """채운 뒤 노드마다 검사한다. 이 노드 안에서 가장 작은 행의 비유한값으로 거절한다."""
        if values.dtype == np.bool_:
            return
        for chunk in _chunks(self._panel.rows, self._checkpoint):
            bad = np.flatnonzero(valid[chunk] & ~np.isfinite(values[chunk]))
            if bad.size:
                row = chunk.start + int(bad[0])
                panel = self._panel
                raise NonFiniteFactorCalculationError(
                    node_id=node_id,
                    value=float(values[row]),
                    observation=FactorObservation(
                        as_of=panel.dates[panel.row_date[row]],
                        security_id=panel.securities[panel.row_security[row]],
                        fields=(),
                    ),
                )
