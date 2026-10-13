"""탐색 조합 제약(로드맵 M6) — 격자 좌표계는 그대로, 제약을 어긴 칸만 실행하지 않는다."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.experiment.facade.design import (
    ConstraintOperator,
    InvalidExperimentSpecError,
    ParameterConstraint,
    build_search_spec,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    IntegerParameter,
    ParameterDefinition,
)

FAST = IntegerParameter(
    parameter_id="fast", default=10, minimum=10, maximum=30, kind="integer", step=10
)
SLOW = IntegerParameter(
    parameter_id="slow", default=20, minimum=10, maximum=30, kind="integer", step=10
)
MODE = ChoiceParameter(parameter_id="mode", default="a", choices=("a", "b"), kind="choice")
PARAMETERS: tuple[ParameterDefinition, ...] = (FAST, SLOW, MODE)


def _fast_below_slow() -> ParameterConstraint:
    return ParameterConstraint("fast", ConstraintOperator.LT, right_parameter="slow")


def test_constraint_drops_cells_but_keeps_the_grid_shape() -> None:
    spec = build_search_spec(PARAMETERS, {"fast": None, "slow": None}, [_fast_below_slow()])

    # 좌표계(3×3)는 그대로라 이웃·고원 계산이 바뀌지 않는다. fast < slow 인 칸만 실행한다.
    assert spec.shape == (3, 3)
    assert spec.indices() == ((0, 1), (0, 2), (1, 2))
    assert [spec.resolve(cell) for cell in spec.indices()] == [
        {"fast": 10, "slow": 20},
        {"fast": 10, "slow": 30},
        {"fast": 20, "slow": 30},
    ]


def test_constraint_against_a_constant_and_a_choice() -> None:
    spec = build_search_spec(
        PARAMETERS,
        {"fast": None, "mode": None},
        [
            ParameterConstraint("fast", ConstraintOperator.GE, right_value=20),
            ParameterConstraint("mode", ConstraintOperator.NE, right_value="b"),
        ],
    )
    assert [spec.resolve(cell) for cell in spec.indices()] == [
        {"fast": 20, "mode": "a"},
        {"fast": 30, "mode": "a"},
    ]


@pytest.mark.parametrize(
    "constraint",
    [
        # 탐색하지 않는 파라미터는 실험 안에서 상수라 제약이 늘 참이거나 늘 거짓이다.
        ParameterConstraint("fast", ConstraintOperator.LT, right_parameter="slow"),
        # 오른쪽은 파라미터나 값 중 정확히 하나다.
        ParameterConstraint("fast", ConstraintOperator.LT),
        ParameterConstraint("fast", ConstraintOperator.LT, right_parameter="fast", right_value=1),
        # 크기 비교는 숫자끼리만 한다.
        ParameterConstraint("mode", ConstraintOperator.LT, right_value="b"),
    ],
)
def test_invalid_constraints_are_rejected(constraint: ParameterConstraint) -> None:
    with pytest.raises(InvalidExperimentSpecError) as error:
        build_search_spec(PARAMETERS, {"fast": None, "mode": None}, [constraint])
    assert error.value.code == "experiment.search.invalid_constraint"


def test_constraints_that_leave_no_cell_are_rejected() -> None:
    with pytest.raises(InvalidExperimentSpecError) as error:
        build_search_spec(
            PARAMETERS,
            {"fast": None},
            [ParameterConstraint("fast", ConstraintOperator.GT, right_value=30)],
        )
    assert error.value.code == "experiment.search.no_feasible_point"


def test_spec_without_constraints_is_the_full_product() -> None:
    spec = build_search_spec(PARAMETERS, {"fast": None, "slow": None})
    assert spec.constraints == ()
    assert len(spec.indices()) == 9
