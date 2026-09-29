"""탐색 그리드(`SearchSpec`)와 그리드 이웃 — v1 파라미터 탐색은 그리드만 쓴다(spec D5).

탐색 값의 허용 판정은 전략 도메인의 `parameter_value_allowed`(타입·범위·선택지) 하나다. 간격(step)은
격자를 펼치는 폭으로만 쓰고, `Decimal` 로 셈해 `0.1 + 0.2` 같은 이진 부동소수 오차가 격자 값을
흔들지 않게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from math import prod
from typing import TypeAlias

from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
    ParameterDefinition,
    ParameterValue,
    parameter_value_allowed,
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


@dataclass(frozen=True)
class SearchSpec:
    """탐색 축들의 곱집합이 그리드다. 축 순서는 전략 문서의 파라미터 선언 순서다.

    축이 없으면 문서 기본값 한 칸(좌표 `()`)이다.
    """

    axes: tuple[SearchAxis, ...]

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(len(axis.values) for axis in self.axes)

    def indices(self) -> tuple[GridIndex, ...]:
        """모든 칸 좌표를 행 우선 순서(마지막 축이 가장 빨리 바뀜)로 낸다."""
        return tuple(product(*(range(size) for size in self.shape)))

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
    return SearchSpec(tuple(built))


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
        scores: 값이 있는 칸만 담는다. 실패한 칸은 넣지 않으며 평균에서 빠진다.

    Returns:
        값이 있는 이웃이 하나도 없으면 `None`.
    """
    values = [scores[cell] for cell in grid_neighbors(shape, index) if cell in scores]
    return sum(values) / len(values) if values else None


def _checked_axis_values(
    parameter: ParameterDefinition, values: tuple[ParameterValue, ...]
) -> tuple[ParameterValue, ...]:
    rejected = [value for value in values if not parameter_value_allowed(parameter, value)]
    problems = [f"정의 밖 값 values={rejected} definition={parameter!r}"] if rejected else []
    if not values:
        problems.append("탐색 값이 비었습니다")
    elif len(set(values)) != len(values):
        problems.append("같은 값이 두 번 있습니다")
    if problems:
        raise InvalidExperimentSpecError(
            "experiment.search.invalid_values",
            f"탐색 값이 파라미터 정의를 벗어납니다: parameter_id={parameter.parameter_id} "
            + "; ".join(problems),
        )
    if isinstance(parameter, ChoiceParameter):
        return tuple(choice for choice in parameter.choices if choice in values)
    if isinstance(parameter, FloatParameter):
        return tuple(sorted(float(value) for value in values))
    return tuple(sorted(values))


def _too_many_points(points: int) -> InvalidExperimentSpecError:
    return InvalidExperimentSpecError(
        "experiment.search.too_many_points",
        f"탐색 조합이 너무 많습니다: points={points} limit={MAX_GRID_POINTS}",
    )


def _inside(shape: tuple[int, ...], cell: GridIndex) -> bool:
    return len(cell) == len(shape) and all(
        0 <= i < size for i, size in zip(cell, shape, strict=True)
    )
