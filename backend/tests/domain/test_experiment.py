"""domain.experiment(V3-01)의 탐색 그리드·워크포워드 창·trial 상태 전이를 손계산 정답으로 본다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import product

import pytest

from strategy_workbench.domain.analytics.facade.metrics import EquityCurvePoint
from strategy_workbench.domain.backtest.facade.environment import RESEARCH_START
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunState,
    RunFailureCode,
    RunStatus,
)
from strategy_workbench.domain.experiment.facade.design import (
    MAX_GRID_POINTS,
    ExperimentDesign,
    ExperimentKind,
    GridIndex,
    InvalidExperimentSpecError,
    SearchSpec,
    SplitMode,
    SplitSpec,
    WalkForwardGap,
    WalkForwardWindow,
    WindowSelectionRule,
    build_search_spec,
    grid_neighbors,
    neighbor_mean,
    parameter_grid_values,
    pick_window_cell,
    stitch_out_of_sample,
    walk_forward_gap,
    walk_forward_retention,
    window_gap,
)
from strategy_workbench.domain.experiment.facade.trial import (
    MAX_EXPERIMENT_PRIORITY,
    ExperimentControls,
    ExperimentStatus,
    TrialStatus,
    experiment_status,
    trial_status,
)
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

    # 정수 칸의 20.0 은 20 이다. 선택지 `1` 과 `True` 는 `==` 로 같지만 서로 다른 값이다.
    mixed = ChoiceParameter(parameter_id="mixed", default=1, choices=(1, True), kind="choice")
    typed = build_search_spec((LOOKBACK, mixed), {"lookback": [20.0], "mixed": [True]})
    assert typed.axes[0].values == (20,) and type(typed.axes[0].values[0]) is int
    assert typed.axes[1].values == (True,) and type(typed.axes[1].values[0]) is bool


def test_invalid_values_message_carries_the_allowed_range() -> None:
    with pytest.raises(InvalidExperimentSpecError) as caught:
        build_search_spec(PARAMETERS, {"lookback": [40], "mode": ["d"]})
    # 문서 선언 순서상 lookback 이 먼저 거절된다.
    assert "values=[40] kind=integer minimum=10 maximum=30" in str(caught.value)

    with pytest.raises(InvalidExperimentSpecError) as choice_caught:
        build_search_spec(PARAMETERS, {"mode": ["d"]})
    assert "values=['d'] choices=['a', 'b', 'c']" in str(choice_caught.value)


@pytest.mark.parametrize(
    ("axes", "code"),
    [
        ({"missing": [1]}, "experiment.search.unknown_parameter"),
        ({"lookback": [40]}, "experiment.search.invalid_values"),
        ({"lookback": [20.5]}, "experiment.search.invalid_values"),
        # 20.0 은 정수 칸에서 20 으로 맞춰지므로 20 과 겹친다.
        ({"lookback": [20, 20.0]}, "experiment.search.invalid_values"),
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
    def axis(maximum: int) -> IntegerParameter:
        return IntegerParameter(
            parameter_id="wide", default=1, minimum=1, maximum=maximum, kind="integer"
        )

    # 정확히 상한이면 통과하고 한 칸 넘으면 거절한다.
    assert build_search_spec((axis(MAX_GRID_POINTS),), {"wide": None}).shape == (MAX_GRID_POINTS,)
    for maximum in (MAX_GRID_POINTS + 1, 10**9):
        # 거대한 축은 값을 펼치기 전에 개수로 거절한다.
        with pytest.raises(InvalidExperimentSpecError) as axis_caught:
            build_search_spec((axis(maximum),), {"wide": None})
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


def test_split_normalizes_string_enums_so_anchored_is_not_rolling() -> None:
    split = SplitSpec(
        mode="anchored",  # pyright: ignore[reportArgumentType]  # reason: 요청 본문 문자열을 흉내 낸다
        train_years=3,
        test_years=1,
        embargo_sessions=0,
        selection_rule="train_sharpe_max",  # pyright: ignore[reportArgumentType]  # reason: 위와 같다
    )

    assert split.mode is SplitMode.ANCHORED
    assert split.selection_rule is WindowSelectionRule.TRAIN_SHARPE_MAX
    assert split.windows(RESEARCH_START, date(2026, 9, 29))[-1].train_start == RESEARCH_START


@pytest.mark.parametrize("field", ["mode", "selection_rule"])
def test_split_rejects_unknown_enum_values(field: str) -> None:
    arguments: dict[str, object] = {
        "mode": "rolling",
        "train_years": 3,
        "test_years": 1,
        "embargo_sessions": 0,
        field: "sideways",
    }
    with pytest.raises(InvalidExperimentSpecError) as caught:
        SplitSpec(**arguments)  # pyright: ignore[reportArgumentType]  # reason: 잘못된 값 주입
    assert caught.value.code == "experiment.split.invalid"
    assert f"{field}='sideways'" in str(caught.value)


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
    with pytest.raises(InvalidExperimentSpecError, match="train_sessions=6") as caught:
        split.train_measurement_end(_WINDOW, _SESSIONS)
    assert caught.value.code == "experiment.split.invalid"


def test_measured_windows_end_each_train_window_before_the_embargo() -> None:
    split = SplitSpec(mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=2)
    start, end = date(2020, 1, 2), date(2022, 6, 30)
    weekdays = [
        day
        for day in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        if day.weekday() < 5
    ]
    first, second = split.windows(start, end)

    # 검증 시작 2021-01-02(토) 앞 세션은 …12/30(수)·12/31(목)·1/1(금). 엠바고 2세션을 빼면 12/30.
    # 2022-01-02(일) 앞은 …12/29(수)·12/30(목)·12/31(금) → 12/29. 검증 창은 그대로다.
    assert split.measured_windows(start, end, weekdays) == (
        replace(first, train_end=date(2020, 12, 30)),
        replace(second, train_end=date(2021, 12, 29)),
    )


def test_window_cell_is_the_best_train_score_and_ties_go_to_the_first_cell() -> None:
    # 1차원 그리드 4칸 점수 1 3 2 3. 넣는 순서와 무관하게 좌표 순으로 동점을 가른다.
    scores: dict[GridIndex, float] = {(3,): 3.0, (2,): 2.0, (1,): 3.0, (0,): 1.0}

    assert pick_window_cell(scores, (4,), WindowSelectionRule.TRAIN_SHARPE_MAX) == (1,)
    # 이웃 평균: (0,)=3, (1,)=(1+2)/2=1.5, (2,)=(3+3)/2=3, (3,)=2 → (0,)·(2,) 동점, 앞 칸.
    assert pick_window_cell(scores, (4,), WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX) == (0,)


def test_window_cell_without_scored_neighbors_uses_its_own_score() -> None:
    # (2,) 는 실패해 점수가 없다: (0,)=2, (1,)=1, (3,) 는 이웃 점수가 없어 자기 점수 5.
    scores: dict[GridIndex, float] = {(0,): 1.0, (1,): 2.0, (3,): 5.0}

    assert pick_window_cell(scores, (4,), WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX) == (3,)
    assert pick_window_cell({(0,): -0.5}, (1,), WindowSelectionRule.NEIGHBOR_MEAN_SHARPE_MAX) == (
        0,
    )
    assert pick_window_cell({}, (4,), WindowSelectionRule.TRAIN_SHARPE_MAX) is None


def test_out_of_sample_curve_chains_returns_from_each_window_first_snapshot() -> None:
    """#364 리뷰 P3-1: 학습 점수(엔진 전체 구간)처럼 창마다 첫 스냅숏부터 수익률을 센다."""

    def segment(*points: tuple[date, float]) -> tuple[EquityCurvePoint, ...]:
        return tuple(EquityCurvePoint(session, equity, None) for session, equity in points)

    stitched = stitch_out_of_sample(
        [
            # 초기 자본 100 → 첫날 110(진입) 수익률은 들지 않는다. 첫날이 기준점 1.0 이다.
            segment((date(2023, 1, 2), 110.0), (date(2023, 1, 3), 99.0), (date(2023, 1, 4), 104.5)),
            # 둘째 창 첫날 점은 곡선에 없고, 앞 창 마지막 값에서 90/120 으로 이어진다.
            segment((date(2024, 1, 2), 120.0), (date(2024, 1, 3), 90.0), (date(2024, 1, 4), 99.0)),
        ]
    )

    assert [point.session for point in stitched] == [
        date(2023, 1, 2),
        date(2023, 1, 3),
        date(2023, 1, 4),
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]
    # 1, 99/110 = 0.9, 0.9 × 104.5/99 = 0.95, 0.95 × 90/120 = 0.7125, 0.7125 × 99/90 = 0.78375.
    assert [point.equity for point in stitched] == pytest.approx([1.0, 0.9, 0.95, 0.7125, 0.78375])
    assert stitch_out_of_sample([]) == ()
    assert stitch_out_of_sample([(), segment((date(2024, 1, 2), 120.0))]) == (
        EquityCurvePoint(date(2024, 1, 2), 1.0, None),
    )


def _test_run(status: RunStatus, error_code: RunFailureCode | None = None) -> BacktestRunState:
    at = datetime(2026, 9, 30, tzinfo=UTC)
    return BacktestRunState("run", status, 0.0, "", "", at, at, error_code=error_code)


@pytest.mark.parametrize(
    ("has_cell", "run", "expected"),
    [
        (False, None, WalkForwardGap.NO_CELL),
        # 접수가 거절돼 실행이 없다.
        (True, None, WalkForwardGap.TEST_FAILED),
        (True, _test_run(RunStatus.COMPLETED), None),
        (True, _test_run(RunStatus.RUNNING), WalkForwardGap.PENDING),
        (True, _test_run(RunStatus.CANCEL_REQUESTED), WalkForwardGap.PENDING),
        (
            True,
            _test_run(RunStatus.FAILED, "backtest.run.equity_wiped_out"),
            WalkForwardGap.TEST_FAILED,
        ),
        (True, _test_run(RunStatus.CANCELLED), WalkForwardGap.TEST_FAILED),
        # 재시작으로 중단된 실행은 복구가 같은 칸으로 다시 넘긴다.
        (True, _test_run(RunStatus.FAILED, "backtest.run.interrupted"), WalkForwardGap.PENDING),
    ],
)
def test_window_gap_says_why_a_window_stays_off_the_curve(
    has_cell: bool, run: BacktestRunState | None, expected: WalkForwardGap | None
) -> None:
    assert window_gap(has_cell, run) is expected


def test_a_finished_failure_outranks_windows_still_running() -> None:
    gaps = [None, WalkForwardGap.PENDING, WalkForwardGap.NO_CELL, WalkForwardGap.TEST_FAILED]

    assert walk_forward_gap(gaps, cancelled=False) is WalkForwardGap.TEST_FAILED
    assert walk_forward_gap(gaps[:3], cancelled=False) is WalkForwardGap.NO_CELL
    assert walk_forward_gap([None, None], cancelled=False) is None


def test_a_cancelled_experiment_reports_cancelled_unless_every_window_finished() -> None:
    """#377 V3-AUDIT-01: 취소한 실험은 남은 창을 더 고르지 않으므로 "진행 중" 으로 남기지 않는다."""
    assert walk_forward_gap([None, WalkForwardGap.PENDING], cancelled=True) is (
        WalkForwardGap.CANCELLED
    )
    assert walk_forward_gap([WalkForwardGap.TEST_FAILED], cancelled=True) is (
        WalkForwardGap.CANCELLED
    )
    # 취소 전에 모든 창의 검증이 끝났으면 요약을 그대로 낸다.
    assert walk_forward_gap([None, None], cancelled=True) is None


@pytest.mark.parametrize(
    ("out_of_sample", "train", "expected"),
    [
        (0.05, [0.1, 0.3], 0.25),
        (-0.02, [0.1], -0.2),
        (0.05, [-0.1, 0.1], None),
        (0.05, [-0.2], None),
        (0.05, [], None),
        (None, [0.1], None),
    ],
)
def test_retention_is_out_of_sample_over_mean_train_sharpe(
    out_of_sample: float | None, train: list[float], expected: float | None
) -> None:
    assert walk_forward_retention(out_of_sample, train) == pytest.approx(expected)


def test_terminal_statuses() -> None:
    assert {status for status in TrialStatus if status.is_terminal} == {
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.CANCELLED,
    }


def test_trials_are_grid_cells_times_windows_in_a_fixed_order() -> None:
    windows = SplitSpec(mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=0)
    design = ExperimentDesign(
        search=build_search_spec(PARAMETERS, {"mode": ["b", "a"], "lookback": [10, 30]}),
        parameter_values={"lookback": 20, "weight": 0.2, "threshold": 0.9, "mode": "a"},
        windows=windows.windows(date(2020, 1, 2), date(2023, 1, 1)),
    )

    trials = design.trials()

    # 칸(lookback 이 느린 축) × 창 순서이고, 탐색하지 않은 파라미터는 기반 해소 값 그대로다.
    assert [
        (trial.index, trial.grid_index, trial.window and trial.window.train_start)
        for trial in trials
    ] == [
        (0, (0, 0), date(2020, 1, 2)),
        (1, (0, 0), date(2021, 1, 2)),
        (2, (0, 1), date(2020, 1, 2)),
        (3, (0, 1), date(2021, 1, 2)),
        (4, (1, 0), date(2020, 1, 2)),
        (5, (1, 0), date(2021, 1, 2)),
        (6, (1, 1), date(2020, 1, 2)),
        (7, (1, 1), date(2021, 1, 2)),
    ]
    assert trials[5].parameter_values == {
        "lookback": 30,
        "weight": 0.2,
        "threshold": 0.9,
        "mode": "a",
    }
    assert design.trials() == trials


@pytest.mark.parametrize(
    ("run", "trial"),
    [
        (RunStatus.QUEUED, TrialStatus.QUEUED),
        (RunStatus.RUNNING, TrialStatus.RUNNING),
        # 취소를 요청한 실행은 아직 끝나지 않았다.
        (RunStatus.CANCEL_REQUESTED, TrialStatus.RUNNING),
        (RunStatus.COMPLETED, TrialStatus.COMPLETED),
        (RunStatus.FAILED, TrialStatus.FAILED),
        (RunStatus.CANCELLED, TrialStatus.CANCELLED),
    ],
)
def test_an_assigned_trial_takes_the_status_of_its_run(run: RunStatus, trial: TrialStatus) -> None:
    assert trial_status(run, attempted=True, experiment_cancelled=True) is trial


@pytest.mark.parametrize(
    ("attempted", "cancelled", "expected"),
    [
        (False, False, TrialStatus.QUEUED),
        (False, True, TrialStatus.CANCELLED),
        # 접수가 거절돼 실행이 없는 attempt 는 실패다.
        (True, False, TrialStatus.FAILED),
    ],
)
def test_a_trial_without_a_run_is_queued_cancelled_or_rejected(
    attempted: bool, cancelled: bool, expected: TrialStatus
) -> None:
    assert trial_status(None, attempted=attempted, experiment_cancelled=cancelled) is expected


@pytest.mark.parametrize(
    ("trials", "cancelled", "expected"),
    [
        ((TrialStatus.QUEUED, TrialStatus.QUEUED), False, ExperimentStatus.QUEUED),
        ((TrialStatus.COMPLETED, TrialStatus.QUEUED), False, ExperimentStatus.RUNNING),
        ((TrialStatus.FAILED, TrialStatus.COMPLETED), False, ExperimentStatus.COMPLETED),
        ((TrialStatus.COMPLETED, TrialStatus.COMPLETED), True, ExperimentStatus.CANCELLED),
    ],
)
def test_experiment_status_follows_its_trials_unless_cancelled(
    trials: tuple[TrialStatus, ...], cancelled: bool, expected: ExperimentStatus
) -> None:
    status = experiment_status(trials, cancelled=cancelled, paused=False, pending=False)
    assert status is expected


def test_a_paused_experiment_reads_paused_until_every_trial_ends() -> None:
    running = (TrialStatus.COMPLETED, TrialStatus.QUEUED)
    finished = (TrialStatus.COMPLETED, TrialStatus.FAILED)

    def status(trials: tuple[TrialStatus, ...], **flags: bool) -> ExperimentStatus:
        return experiment_status(trials, **{"cancelled": False, "pending": False, **flags})

    assert status(running, paused=True) is ExperimentStatus.PAUSED
    assert status(finished, paused=True) is ExperimentStatus.COMPLETED
    assert status(running, cancelled=True, paused=True) is ExperimentStatus.CANCELLED
    # 재시작으로 중단된 trial 이 복구를 기다리면 모두 끝난 것처럼 보여도 완료가 아니다.
    assert status(finished, paused=False, pending=True) is ExperimentStatus.RUNNING


@pytest.mark.parametrize("priority", [0, MAX_EXPERIMENT_PRIORITY + 1, True, 1.5])
def test_experiment_priority_stays_inside_its_range(priority: object) -> None:
    with pytest.raises(ValueError, match="priority"):
        ExperimentControls(paused=False, priority=priority)  # type: ignore[arg-type]  # 범위 밖 값을 일부러 넣는다


def test_only_a_measured_parameter_search_walks_forward() -> None:
    """V4-04: 워크포워드 여부는 종류로 가른다 — 용량 스윕은 창이 없어 측정 여부와 무관하게 돌지
    않는다."""
    search = SearchSpec(())
    base = ExperimentDesign(search=search, parameter_values={}, windows=())

    assert replace(base, measured=True).walks_forward is True
    assert base.walks_forward is False
    assert replace(base, measured=True, kind=ExperimentKind.CAPACITY_SWEEP).walks_forward is False
