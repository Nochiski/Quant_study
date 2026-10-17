"""튼튼한지 확인하기의 기본 설계(검증 랩 V5-02, US-DM-11) — 기간에 맞는 분할과 탐색 축."""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.experiment.facade.design import (
    InvalidExperimentSpecError,
    SplitMode,
    SplitSpec,
    robustness_search,
    robustness_split,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FloatParameter,
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


def test_every_declared_parameter_is_searched_over_its_whole_grid() -> None:
    parameters = (
        FloatParameter("scale", 1.0, -1.0, 1.0, kind="float", step=1.0),
        ChoiceParameter("mode", "a", ("a", "b"), kind="choice"),
    )
    assert robustness_search(parameters) == {"scale": None, "mode": None}
    assert robustness_search(()) == {}
