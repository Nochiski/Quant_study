"""탐색 그리드(`SearchSpec`)와 그리드 이웃 — v1 파라미터 탐색은 그리드만 쓴다(spec D5).

조합 제약(`ParameterConstraint`, 로드맵 M6)은 탐색 축 사이의 관계(`fast < slow` 꼴)다. 격자 모양
(좌표계)은 곱집합 그대로 두고 제약을 어기는 칸만 실행하지 않는다 — 이웃·고원·민감도는 실제로 있는
칸만 읽으므로 좌표계가 바뀌지 않는다. 걸러진 칸은 실행되지 않아 시도 N에도 들지 않는다.

탐색 값의 허용 판정과 선언 타입 정규화는 전략 도메인의 `normalized_parameter_value`(타입·범위·
선택지) 하나다. 간격(step)은 격자를 펼치는 폭으로만 쓰고, `Decimal` 로 셈해 `0.1 + 0.2` 같은 이진
부동소수 오차가 격자 값을 흔들지 않게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import product
from math import prod
from typing import TypeAlias, TypeGuard

from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    IntegerParameter,
    ParameterDefinition,
    ParameterValue,
    describe_allowed_parameter_values,
    normalized_parameter_value,
)

from ._errors import InvalidExperimentSpecError

# 한 실험의 그리드 칸 수 상한. 칸마다 워크포워드 창 수만큼 학습 실행이 붙으므로 대기열(V3-04)이
# 감당할 v1 값이다. 상한을 바꿀 때는 이 상수 하나만 고친다.
MAX_GRID_POINTS = 400

# 그리드 칸 좌표. 축마다 값 목록 안 위치이며 축 순서는 `SearchSpec.axes` 와 같다.
GridIndex: TypeAlias = tuple[int, ...]


@dataclass(frozen=True)
class SearchAxis:
    """탐색할 파라미터 하나와 그 값 목록. 숫자는 오름차순, 선택지는 문서 선언 순서다."""

    parameter_id: str
    values: tuple[ParameterValue, ...]


class ConstraintOperator(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    EQ = "eq"
    NE = "ne"


_ORDERING = frozenset(
    {ConstraintOperator.LT, ConstraintOperator.LE, ConstraintOperator.GT, ConstraintOperator.GE}
)


@dataclass(frozen=True)
class ParameterConstraint:
    """탐색 축 값과 다른 탐색 축 값(`right_parameter`) 또는 상수(`right_value`)의 관계.

    둘 중 정확히 하나를 쓴다. 순서 비교(`lt`·`le`·`gt`·`ge`)는 숫자끼리만, 선택지는 `eq`·`ne`만
    받는다.
    """

    left: str
    operator: ConstraintOperator
    right_parameter: str | None = None
    right_value: ParameterValue | None = None

    def holds(self, values: Mapping[str, ParameterValue]) -> bool:
        left = values[self.left]
        right = (
            values[self.right_parameter] if self.right_parameter is not None else self.right_value
        )
        if self.operator is ConstraintOperator.EQ:
            return left == right
        if self.operator is ConstraintOperator.NE:
            return left != right
        if not (_is_number(left) and _is_number(right)):
            # build_search_spec 가 막는다. 저장된 설계가 어긋나면 조용히 거르지 않고 알린다.
            raise TypeError(
                f"ordering constraint needs numbers — left={left!r} right={right!r} "
                f"operator={self.operator.value}"
            )
        if self.operator is ConstraintOperator.LT:
            return left < right
        if self.operator is ConstraintOperator.LE:
            return left <= right
        if self.operator is ConstraintOperator.GT:
            return left > right
        return left >= right


@dataclass(frozen=True)
class SearchSpec:
    """탐색 축들의 곱집합이 그리드다. 축 순서는 전략 문서의 파라미터 선언 순서다.

    축이 없으면 문서 기본값 한 칸(좌표 `()`)이다. 제약(`constraints`)을 모두 만족하는 칸만 실행한다.
    """

    axes: tuple[SearchAxis, ...]
    constraints: tuple[ParameterConstraint, ...] = ()

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(len(axis.values) for axis in self.axes)

    def indices(self) -> tuple[GridIndex, ...]:
        """실행할 칸 좌표를 행 우선 순서(마지막 축이 가장 빨리 바뀜)로 낸다(제약을 어긴 칸 제외)."""
        cells = product(*(range(size) for size in self.shape))
        return tuple(
            cell
            for cell in cells
            if all(constraint.holds(self.resolve(cell)) for constraint in self.constraints)
        )

    def resolve(self, index: GridIndex) -> dict[str, ParameterValue]:
        """칸 좌표를 파라미터 값으로 푼다. 탐색하지 않는 파라미터는 싣지 않는다(문서 기본값)."""
        return {
            axis.parameter_id: axis.values[position]
            for axis, position in zip(self.axes, index, strict=True)
        }


def parameter_grid_values(parameter: ParameterDefinition) -> tuple[ParameterValue, ...]:
    """파라미터 정의가 허용하는 격자 값 전체 — 최솟값부터 간격마다, 최댓값을 넘지 않는다.

    Raises:
        InvalidExperimentSpecError: 간격이 없는 실수 파라미터(격자가 없음)이거나 값 수가
            `MAX_GRID_POINTS` 를 넘는다.
    """
    if isinstance(parameter, ChoiceParameter):
        return parameter.choices
    if parameter.step is None:
        raise InvalidExperimentSpecError(
            "experiment.search.invalid_values",
            "간격(step)이 없는 실수 파라미터는 탐색할 값을 직접 적어야 합니다: "
            f"parameter_id={parameter.parameter_id}",
        )
    minimum, step = Decimal(str(parameter.minimum)), Decimal(str(parameter.step))
    count = int((Decimal(str(parameter.maximum)) - minimum) // step) + 1
    if count > MAX_GRID_POINTS:
        raise _too_many_points(count)
    as_number = int if isinstance(parameter, IntegerParameter) else float
    return tuple(as_number(minimum + position * step) for position in range(count))


def build_search_spec(
    parameters: Sequence[ParameterDefinition],
    axes: Mapping[str, Sequence[ParameterValue] | None],
    constraints: Sequence[ParameterConstraint] = (),
) -> SearchSpec:
    """요청한 탐색 축을 전략 문서의 파라미터 정의로 검증해 그리드를 만든다.

    Args:
        parameters: 기반 리비전의 `StrategySpec.parameters`.
        axes: parameter_id → 탐색 값 목록. `None` 이면 정의가 허용하는 격자 값 전체다. 요청의 키
            순서와 값 순서는 결과에 영향이 없다.

    Raises:
        InvalidExperimentSpecError: 문서에 없는 parameter_id, 비었거나 중복되거나 범위·간격·선택지
            밖인 값, 칸 수가 `MAX_GRID_POINTS` 초과.
    """
    unknown = sorted(set(axes) - {parameter.parameter_id for parameter in parameters})
    if unknown:
        raise InvalidExperimentSpecError(
            "experiment.search.unknown_parameter",
            f"전략 문서에 없는 파라미터는 탐색할 수 없습니다: parameter_ids={unknown}",
        )
    built: list[SearchAxis] = []
    for parameter in parameters:
        if parameter.parameter_id not in axes:
            continue
        requested = axes[parameter.parameter_id]
        values = parameter_grid_values(parameter) if requested is None else tuple(requested)
        built.append(SearchAxis(parameter.parameter_id, _checked_axis_values(parameter, values)))
    points = prod(len(axis.values) for axis in built)
    if points > MAX_GRID_POINTS:
        raise _too_many_points(points)
    for constraint in constraints:
        _check_constraint(constraint, {axis.parameter_id: axis.values for axis in built})
    spec = SearchSpec(tuple(built), tuple(constraints))
    if not spec.indices():
        raise InvalidExperimentSpecError(
            "experiment.search.no_feasible_point",
            f"제약을 모두 만족하는 탐색 조합이 없습니다: constraints={len(constraints)} "
            f"grid_points={points}",
        )
    return spec


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_constraint(
    constraint: ParameterConstraint, axes: Mapping[str, tuple[ParameterValue, ...]]
) -> None:
    """제약이 탐색 축만 가리키고 비교할 수 있는 값끼리인지 본다.

    탐색하지 않는 파라미터는 실험 안에서 상수라 제약이 늘 참이거나 늘 거짓이 된다 — 그런 제약은
    받지 않고 그 파라미터를 탐색 축에 넣거나 제약을 빼라고 알린다.
    """

    def invalid(reason: str) -> InvalidExperimentSpecError:
        return InvalidExperimentSpecError(
            "experiment.search.invalid_constraint",
            f"{reason}: left={constraint.left} operator={constraint.operator.value} "
            f"right_parameter={constraint.right_parameter} right_value={constraint.right_value!r}",
        )

    if (constraint.right_parameter is None) == (constraint.right_value is None):
        raise invalid("제약의 오른쪽은 파라미터나 값 중 정확히 하나여야 합니다")
    referenced = [constraint.left]
    if constraint.right_parameter is not None:
        referenced.append(constraint.right_parameter)
    outside = [parameter_id for parameter_id in referenced if parameter_id not in axes]
    if outside:
        raise invalid(f"제약은 탐색하는 파라미터끼리만 걸 수 있습니다(탐색 축이 아님: {outside})")
    if constraint.operator in _ORDERING:
        operands = [*axes[constraint.left]]
        operands += (
            axes[constraint.right_parameter]
            if constraint.right_parameter is not None
            else [constraint.right_value]
        )
        if not all(_is_number(value) for value in operands):
            raise invalid("크기 비교는 숫자 파라미터끼리만 할 수 있습니다")


def grid_neighbors(shape: tuple[int, ...], index: GridIndex) -> tuple[GridIndex, ...]:
    """체비셰프 거리 1인 칸 — d차원 안쪽 칸이면 3^d − 1칸(자기 칸 제외).

    그리드 밖 칸은 빼므로 경계 칸은 존재하는 칸만 이웃이다. 순서는 행 우선이다.
    """
    if not _inside(shape, index):
        raise ValueError(f"그리드 밖 좌표다 — shape={shape} index={index}")
    cells = (
        tuple(i + offset for i, offset in zip(index, offsets, strict=True))
        for offsets in product((-1, 0, 1), repeat=len(shape))
        if any(offsets)
    )
    return tuple(cell for cell in cells if _inside(shape, cell))


def neighbor_mean(
    scores: Mapping[GridIndex, float], shape: tuple[int, ...], index: GridIndex
) -> float | None:
    """`grid_neighbors` 칸 점수의 평균(자기 칸 제외, 수학 노트 5절 고원 점수).

    Args:
        scores: 평균에 넣을 칸만 담는다. 무엇을 넣을지는 호출자가 정한다 — 창 선택은 실패 칸을
            빼고, 파라미터 지도는 파산 칸을 최하 점수로 넣는다.

    Returns:
        값이 있는 이웃이 하나도 없으면 `None`.
    """
    values = [scores[cell] for cell in grid_neighbors(shape, index) if cell in scores]
    return sum(values) / len(values) if values else None


def neighbor_rank(
    scores: Mapping[GridIndex, float], shape: tuple[int, ...], index: GridIndex
) -> float:
    """이웃 평균 기준으로 칸을 줄 세우는 값 — 점수 있는 이웃이 없으면 자기 점수다."""
    mean = neighbor_mean(scores, shape, index)
    return scores[index] if mean is None else mean


def _checked_axis_values(
    parameter: ParameterDefinition, values: tuple[ParameterValue, ...]
) -> tuple[ParameterValue, ...]:
    normalized = [normalized_parameter_value(parameter, value) for value in values]
    rejected = [value for value, kept in zip(values, normalized, strict=True) if kept is None]
    # `True == 1` 이라 값 자체로 겹침을 보면 선택지 `1` 과 `True` 가 한 값이 된다. 타입과 함께 본다.
    keys = {(type(value), value) for value in normalized if value is not None}
    problems = (
        [f"정의 밖 값 values={rejected} {describe_allowed_parameter_values(parameter)}"]
        if rejected
        else []
    )
    if not values:
        problems.append("탐색 값이 비었습니다")
    elif len(keys) != len(values) - len(rejected):
        problems.append("같은 값이 두 번 있습니다")
    if problems:
        raise InvalidExperimentSpecError(
            "experiment.search.invalid_values",
            f"탐색 값을 쓸 수 없습니다: parameter_id={parameter.parameter_id} "
            + "; ".join(problems),
        )
    if isinstance(parameter, ChoiceParameter):
        return tuple(choice for choice in parameter.choices if (type(choice), choice) in keys)
    return tuple(sorted(value for _, value in keys))


def _too_many_points(points: int) -> InvalidExperimentSpecError:
    return InvalidExperimentSpecError(
        "experiment.search.too_many_points",
        f"탐색 조합이 너무 많습니다: points={points} limit={MAX_GRID_POINTS}",
    )


def _inside(shape: tuple[int, ...], cell: GridIndex) -> bool:
    return len(cell) == len(shape) and all(
        0 <= i < size for i, size in zip(cell, shape, strict=True)
    )
