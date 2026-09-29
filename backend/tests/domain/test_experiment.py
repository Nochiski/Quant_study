"""domain.experiment(V3-01)의 탐색 그리드·워크포워드 창·trial 상태 전이를 손계산 정답으로 본다."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from itertools import product

import pytest

from strategy_workbench.domain.backtest.facade.environment import RESEARCH_START
from strategy_workbench.domain.experiment.facade.design import (
    MAX_GRID_POINTS,
    GridIndex,
    InvalidExperimentSpecError,
    SplitMode,
    SplitSpec,
    WalkForwardWindow,
    WindowSelectionRule,
    build_search_spec,
    grid_neighbors,
    neighbor_mean,
    parameter_grid_values,
)
from strategy_workbench.domain.experiment.facade.trial import TrialStatus, advance_trial_status
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
    ParameterDefinition,
    ParameterValue,
)

LOOKBACK = IntegerParameter(
    parameter_id="lookback", default=20, minimum=10, maximum=30, kind="integer", step=10
)
WEIGHT = FloatParameter(
    parameter_id="weight", default=0.2, minimum=0.1, maximum=0.3, kind="float", step=0.1
)
THRESHOLD = FloatParameter(
    parameter_id="threshold", default=0.5, minimum=0.0, maximum=1.0, kind="float"
)
MODE = ChoiceParameter(parameter_id="mode", default="a", choices=("a", "b", "c"), kind="choice")
PARAMETERS: tuple[ParameterDefinition, ...] = (LOOKBACK, WEIGHT, THRESHOLD, MODE)


def test_grid_values_follow_step_without_binary_float_drift() -> None:
    assert parameter_grid_values(LOOKBACK) == (10, 20, 30)
    # 0.1 + 2 × 0.1 을 이진 부동소수로 더하면 0.30000000000000004 다.
    assert parameter_grid_values(WEIGHT) == (0.1, 0.2, 0.3)
    assert parameter_grid_values(MODE) == ("a", "b", "c")


def test_grid_is_the_same_regardless_of_request_order() -> None:
    first = build_search_spec(PARAMETERS, {"weight": [0.3, 0.1], "lookback": None})
    second = build_search_spec(PARAMETERS, {"lookback": [30, 10, 20], "weight": [0.1, 0.3]})

    assert first == second
    # 축은 문서 선언 순서, 칸은 행 우선(마지막 축이 가장 빨리 바뀜)이다.
    assert [axis.parameter_id for axis in first.axes] == ["lookback", "weight"]
    assert first.shape == (3, 2)
    assert first.indices() == ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1))
    assert first.resolve((2, 1)) == {"lookback": 30, "weight": 0.3}


def test_axis_values_are_normalized() -> None:
    spec = build_search_spec(PARAMETERS, {"threshold": [1, 0.25], "mode": ["c", "a"]})

    assert spec.axes[0].values == (0.25, 1.0)
    assert type(spec.axes[0].values[1]) is float
    # 선택지는 문서 선언 순서로 놓여 이웃 관계가 요청 순서에 흔들리지 않는다.
    assert spec.axes[1].values == ("a", "c")


@pytest.mark.parametrize(
    ("axes", "code"),
    [
        ({"missing": [1]}, "experiment.search.unknown_parameter"),
        ({"lookback": [40]}, "experiment.search.invalid_values"),
        ({"lookback": [20.0]}, "experiment.search.invalid_values"),
        ({"lookback": [True]}, "experiment.search.invalid_values"),
        ({"threshold": [1.5]}, "experiment.search.invalid_values"),
        ({"mode": ["d"]}, "experiment.search.invalid_values"),
        ({"lookback": []}, "experiment.search.invalid_values"),
        ({"lookback": [10, 10]}, "experiment.search.invalid_values"),
        ({"threshold": None}, "experiment.search.invalid_values"),
    ],
)
def test_search_rejects_values_outside_the_definition(
    axes: dict[str, Sequence[ParameterValue] | None], code: str
) -> None:
    with pytest.raises(InvalidExperimentSpecError) as caught:
        build_search_spec(PARAMETERS, axes)
    assert caught.value.code == code


def test_explicit_values_may_leave_the_step_grid() -> None:
    # 간격은 격자를 펼치는 폭일 뿐 허용 판정이 아니다(`parameter_value_allowed`).
    spec = build_search_spec(PARAMETERS, {"lookback": [15], "weight": [0.15]})

    assert spec.resolve((0, 0)) == {"lookback": 15, "weight": 0.15}


def test_search_rejects_more_points_than_the_limit() -> None:
    wide = IntegerParameter(
        parameter_id="wide", default=1, minimum=1, maximum=MAX_GRID_POINTS + 1, kind="integer"
    )
    with pytest.raises(InvalidExperimentSpecError) as axis_caught:
        build_search_spec((wide,), {"wide": None})
    assert axis_caught.value.code == "experiment.search.too_many_points"

    # 축 하나는 상한 안이어도 곱이 상한을 넘으면 거절한다: 21 × 21 = 441.
    square = tuple(
        IntegerParameter(parameter_id=name, default=1, minimum=1, maximum=21, kind="integer")
        for name in ("x", "y")
    )
    with pytest.raises(InvalidExperimentSpecError) as grid_caught:
        build_search_spec(square, {"x": None, "y": None})
    assert grid_caught.value.code == "experiment.search.too_many_points"


def test_neighbors_are_chebyshev_distance_one_clipped_at_the_edge() -> None:
    # d차원 안쪽 칸의 이웃은 3^d − 1칸이다.
    assert len(grid_neighbors((3, 3), (1, 1))) == 8
    assert grid_neighbors((3, 3), (0, 0)) == ((0, 1), (1, 0), (1, 1))
    assert grid_neighbors((3, 3), (0, 1)) == ((0, 0), (0, 2), (1, 0), (1, 1), (1, 2))
    assert grid_neighbors((4,), (0,)) == ((1,),)
    assert grid_neighbors((4,), (2,)) == ((1,), (3,))
    assert len(grid_neighbors((3, 3, 3), (1, 1, 1))) == 26
    with pytest.raises(ValueError):
        grid_neighbors((3, 3), (3, 0))


def test_neighbor_mean_skips_missing_cells() -> None:
    # 3×3 그리드 점수:
    #   1 2 3
    #   4 5 6
    #   7 8 9
    scores: dict[GridIndex, float] = {
        (row, column): float(row * 3 + column + 1) for row, column in product(range(3), repeat=2)
    }
    shape = (3, 3)

    assert neighbor_mean(scores, shape, (1, 1)) == pytest.approx((45 - 5) / 8)
    assert neighbor_mean(scores, shape, (0, 0)) == pytest.approx((2 + 4 + 5) / 3)
    assert neighbor_mean(scores, shape, (0, 1)) == pytest.approx((1 + 3 + 4 + 5 + 6) / 5)

    failed = {cell: score for cell, score in scores.items() if cell != (0, 1)}
    assert neighbor_mean(failed, shape, (0, 0)) == pytest.approx((4 + 5) / 2)
    assert neighbor_mean({(2, 2): 9.0}, shape, (0, 0)) is None


def _rolling(train_years: int = 3, test_years: int = 1) -> SplitSpec:
    return SplitSpec(
        mode=SplitMode.ROLLING, train_years=train_years, test_years=test_years, embargo_sessions=5
    )


def test_rolling_windows_include_the_partial_last_test_window() -> None:
    windows = _rolling().windows(RESEARCH_START, date(2026, 9, 29))

    assert windows == (
        WalkForwardWindow(date(2020, 1, 2), date(2023, 1, 1), date(2023, 1, 2), date(2024, 1, 1)),
        WalkForwardWindow(date(2021, 1, 2), date(2024, 1, 1), date(2024, 1, 2), date(2025, 1, 1)),
        WalkForwardWindow(date(2022, 1, 2), date(2025, 1, 1), date(2025, 1, 2), date(2026, 1, 1)),
        WalkForwardWindow(date(2023, 1, 2), date(2026, 1, 1), date(2026, 1, 2), date(2026, 9, 29)),
    )


def test_anchored_windows_keep_the_train_start() -> None:
    split = SplitSpec(mode=SplitMode.ANCHORED, train_years=3, test_years=1, embargo_sessions=0)
    windows = split.windows(RESEARCH_START, date(2026, 9, 29))

    assert [window.train_start for window in windows] == [RESEARCH_START] * 4
    assert [window.test_start for window in windows] == [
        date(2023, 1, 2),
        date(2024, 1, 2),
        date(2025, 1, 2),
        date(2026, 1, 2),
    ]


@pytest.mark.parametrize(
    ("research_end", "expected_count", "last_test_end"),
    [
        # 검증 시작일(2026-01-02) 하루 전에 끝나면 네 번째 창은 없다.
        (date(2026, 1, 1), 3, date(2026, 1, 1)),
        # 검증 시작일에 끝나면 하루짜리 부분 창이 들어간다.
        (date(2026, 1, 2), 4, date(2026, 1, 2)),
    ],
)
def test_partial_window_boundary(
    research_end: date, expected_count: int, last_test_end: date
) -> None:
    windows = _rolling().windows(RESEARCH_START, research_end)

    assert len(windows) == expected_count
    assert windows[-1].test_end == last_test_end


def test_multi_year_test_windows_stay_inside_the_research_span() -> None:
    research_end = date(2026, 9, 29)
    windows = _rolling(test_years=2).windows(RESEARCH_START, research_end)

    assert [(window.test_start, window.test_end) for window in windows] == [
        (date(2023, 1, 2), date(2025, 1, 1)),
        (date(2025, 1, 2), research_end),
    ]
    assert all(
        RESEARCH_START <= window.train_start and window.test_end <= research_end
        for window in windows
    )


def test_test_windows_are_contiguous_from_a_leap_day() -> None:
    # 평년 2023-02-28 에서 1년을 더하면 2024-02-28 이지만 다음 검증 창은 윤년 2024-02-29 에
    # 시작한다.
    windows = _rolling().windows(date(2020, 2, 29), date(2024, 12, 31))

    assert [(window.test_start, window.test_end) for window in windows] == [
        (date(2023, 2, 28), date(2024, 2, 28)),
        (date(2024, 2, 29), date(2024, 12, 31)),
    ]


def test_split_without_a_test_day_is_rejected() -> None:
    with pytest.raises(InvalidExperimentSpecError) as caught:
        _rolling().windows(RESEARCH_START, date(2023, 1, 1))
    assert caught.value.code == "experiment.split.no_window"


@pytest.mark.parametrize(
    ("train_years", "test_years", "embargo_sessions"),
    [(0, 1, 0), (3, 0, 0), (3, 1, -1), (3, True, 0)],
)
def test_split_rejects_invalid_lengths(
    train_years: int, test_years: int, embargo_sessions: int
) -> None:
    with pytest.raises(InvalidExperimentSpecError) as caught:
        SplitSpec(
            mode=SplitMode.ROLLING,
            train_years=train_years,
            test_years=test_years,
            embargo_sessions=embargo_sessions,
        )
    assert caught.value.code == "experiment.split.invalid"


def test_neighbor_mean_is_the_default_selection_rule() -> None:
    assert _rolling().selection_rule is WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX


# 2020-01-02(목)부터 평일 세션. 학습 창 앞 워밍업 세션 2019-12-31 을 섞어 둔다.
_SESSIONS = [date(2019, 12, 31)] + [
    day
    for day in (date(2020, 1, 2) + timedelta(days=offset) for offset in range(16))
    if day.weekday() < 5
]
_WINDOW = WalkForwardWindow(
    date(2020, 1, 2), date(2020, 1, 9), date(2020, 1, 10), date(2020, 1, 17)
)


@pytest.mark.parametrize(
    ("embargo_sessions", "expected"),
    [
        # 학습 세션: 1/2, 1/3, 1/6, 1/7, 1/8, 1/9.
        (0, date(2020, 1, 9)),
        (2, date(2020, 1, 7)),
        (5, date(2020, 1, 2)),
    ],
)
def test_embargo_pulls_the_train_measurement_end(embargo_sessions: int, expected: date) -> None:
    split = SplitSpec(
        mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=embargo_sessions
    )
    assert split.train_measurement_end(_WINDOW, _SESSIONS) == expected


def test_embargo_that_swallows_the_train_window_is_rejected() -> None:
    split = SplitSpec(mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=6)
    with pytest.raises(ValueError, match="train_sessions=6"):
        split.train_measurement_end(_WINDOW, _SESSIONS)


_FORWARD = {
    (TrialStatus.QUEUED, TrialStatus.RUNNING),
    (TrialStatus.QUEUED, TrialStatus.CANCELLED),
    (TrialStatus.RUNNING, TrialStatus.COMPLETED),
    (TrialStatus.RUNNING, TrialStatus.FAILED),
    (TrialStatus.RUNNING, TrialStatus.CANCELLED),
}


@pytest.mark.parametrize(("current", "target"), sorted(product(TrialStatus, repeat=2)))
def test_trial_status_only_moves_forward(current: TrialStatus, target: TrialStatus) -> None:
    if (current, target) in _FORWARD:
        assert advance_trial_status(current, target) is target
    else:
        with pytest.raises(ValueError):
            advance_trial_status(current, target)


def test_terminal_statuses() -> None:
    assert {status for status in TrialStatus if status.is_terminal} == {
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.CANCELLED,
    }
