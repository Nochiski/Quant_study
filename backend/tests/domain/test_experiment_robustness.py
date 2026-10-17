"""튼튼한지 확인하기의 기본 설계(검증 랩 V5-02, US-DM-11) — 기간에 맞는 분할과 탐색 축."""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.experiment.facade.design import (
    InvalidExperimentSpecError,
    SplitMode,
    SplitSpec,
    robustness_end,
    robustness_search,
    robustness_split,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
)


def _rolling(train_years: int) -> SplitSpec:
    return SplitSpec(
        mode=SplitMode.ROLLING, train_years=train_years, test_years=1, embargo_sessions=5
    )


@pytest.mark.parametrize(
    ("start", "end", "train_years"),
    [
        # 거래일 기준 꽉 찬 4년(연초 첫 거래일 ~ 연말 마지막 거래일)은 학습 3년 + 검증 1년이다.
        (date(2020, 1, 2), date(2023, 12, 28), 3),
        (date(2020, 1, 2), date(2026, 9, 30), 3),
        # 4년을 담지 못하면 학습을 1년으로 줄인다.
        (date(2021, 1, 4), date(2023, 6, 30), 1),
        # 거래일 기준 꽉 찬 2년은 연말연시 휴장으로 달력 2년보다 며칠 짧아도 담는다.
        (date(2021, 1, 4), date(2022, 12, 30), 1),
    ],
)
def test_the_longest_training_span_the_period_holds_is_chosen(
    start: date, end: date, train_years: int
) -> None:
    assert robustness_split(start, end) == _rolling(train_years)


def test_a_period_shorter_than_two_years_is_refused_with_its_reason() -> None:
    with pytest.raises(InvalidExperimentSpecError) as refused:
        robustness_split(date(2021, 1, 4), date(2022, 6, 30))
    assert refused.value.code == "experiment.robustness.period_too_short"
    assert "start=2021-01-04 end=2022-06-30" in str(refused.value)


def test_numeric_parameters_are_searched_around_the_current_value_within_range() -> None:
    parameters = (
        # 간격 있는 실수: 지금 값 ±간격, 범위 밖은 뺀다(1.0 + 1.0 = 2.0 은 최댓값 밖).
        FloatParameter("scale", 0.0, -1.0, 1.0, kind="float", step=1.0),
        # 정수: ±간격.
        IntegerParameter("window", 20, 5, 60, kind="integer", step=5),
        # 간격 없는 실수: ±20%.
        FloatParameter("threshold", 0.5, 0.0, 1.0, kind="float"),
        # 선택지는 구조를 바꾸는 선택이라 지금 값에 둔다(탐색하지 않는다).
        ChoiceParameter("mode", "a", ("a", "b"), kind="choice"),
    )
    current = {"scale": 1.0, "window": 20, "threshold": 0.5, "mode": "b"}

    assert robustness_search(parameters, current) == {
        "scale": [0.0, 1.0],
        "window": [15, 20, 25],
        "threshold": [0.4, 0.5, 0.6],
    }
    assert robustness_search((), {}) == {}


def test_neighbours_are_exact_decimal_steps_not_float_noise() -> None:
    """재검토 P2: 0.3 - 0.1 이 0.19999… 가 아니라 격자 값 0.2 여야 같은 설정이 같은 시도 키다."""
    parameter = FloatParameter("th", 0.3, 0.0, 1.0, kind="float", step=0.1)
    assert robustness_search((parameter,), {"th": 0.3}) == {"th": [0.2, 0.3, 0.4]}
    no_step = FloatParameter("w", 0.7, 0.0, 1.0, kind="float")
    # 0.7 의 ±20% = ±0.14.
    assert robustness_search((no_step,), {"w": 0.7}) == {"w": [0.56, 0.7, 0.84]}


def test_a_zero_value_without_step_moves_by_a_tenth_of_the_range() -> None:
    parameter = FloatParameter("tilt", 0.0, -1.0, 1.0, kind="float")
    # 0 의 ±20% 는 0 이라 범위 폭의 10%(= 20% 의 절반)로 옮긴다.
    assert robustness_search((parameter,), {"tilt": 0.0}) == {"tilt": [-0.2, 0.0, 0.2]}


def test_too_many_numeric_parameters_are_refused_with_the_cell_count() -> None:
    parameters = tuple(
        IntegerParameter(f"p{index}", 10, 0, 100, kind="integer", step=1) for index in range(5)
    )
    with pytest.raises(InvalidExperimentSpecError) as refused:
        robustness_search(parameters, {})
    # 3⁵ = 243 칸 > 81.
    assert refused.value.code == "experiment.robustness.too_many_parameters"
    assert "243칸" in str(refused.value)


def test_a_short_trailing_test_window_is_dropped() -> None:
    split = robustness_split(date(2020, 1, 2), date(2024, 1, 10))
    # 둘째 창의 검증은 2024-01-02 ~ 2024-01-10 로 며칠뿐이라 첫 창 검증 끝까지만 쓴다.
    end = robustness_end(split, date(2020, 1, 2), date(2024, 1, 10))
    assert end == split.windows(date(2020, 1, 2), date(2024, 1, 10))[0].test_end
    # 꼬리가 반년 이상이면 그대로다.
    assert robustness_end(split, date(2020, 1, 2), date(2024, 9, 30)) == date(2024, 9, 30)
