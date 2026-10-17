"""시계열 창 연산이 창마다 원소를 거르던 규칙과 같은 값을 낸다(실데이터 확인 2026-10-17 후속 1).

평가기는 종목마다 숫자 변환·누적 개수를 한 번 만들어 창을 자른다. 결측이 섞인 무작위 시계열에서
손으로 쓴 기준(창 칸을 숫자만 걸러 그 길이가 창 길이와 같을 때만 계산)과 모든 연산자·창·랙 조합을
견준다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from statistics import mean, pstdev

import pytest

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)

_START = date(2024, 1, 1)


def _reference(operator: TimeSeriesOperator, window: list[float]) -> float | None:
    if operator is TimeSeriesOperator.MEAN:
        return mean(window)
    if operator is TimeSeriesOperator.STANDARD_DEVIATION:
        return pstdev(window)
    if operator is TimeSeriesOperator.MOMENTUM:
        return None if window[0] == 0 else window[-1] / window[0] - 1
    if operator is TimeSeriesOperator.DELTA:
        return window[-1] - window[0]
    if operator is TimeSeriesOperator.MINIMUM:
        return min(window)
    return max(window)


def _series(seed: int) -> dict[str, list[float | None]]:
    rng = random.Random(seed)
    return {
        security: [
            None if rng.random() < 0.15 else rng.choice([0.0, rng.uniform(1, 200)])
            for _ in range(rng.randint(0, 40))
        ]
        for security in ("A", "B", "C")
    }


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("operator", list(TimeSeriesOperator))
@pytest.mark.parametrize(("window", "lag"), [(1, 0), (3, 0), (5, 2), (12, 1)])
def test_windows_match_filtering_each_window(
    seed: int, operator: TimeSeriesOperator, window: int, lag: int
) -> None:
    series = _series(seed)
    observations = tuple(
        FactorObservation(
            as_of=_START + timedelta(days=day),
            security_id=security,
            fields=(FactorFieldValue("x", value),),
        )
        for security, values in series.items()
        for day, value in enumerate(values)
    )
    graph = FactorGraph(
        nodes=(
            FieldNode("x", "x", "field"),
            TimeSeriesNode("out", operator, "x", window, "time_series", lag=lag),
        ),
        output_node_id="out",
    )

    evaluation = evaluate_factor_graph(graph, observations=observations, missing=MissingPolicy.DROP)

    expected: dict[tuple[str, date], float | None] = {}
    for security, values in series.items():
        for position in range(len(values)):
            start, end = position - lag - window + 1, position - lag + 1
            numbers = [value for value in values[max(start, 0) : end] if value is not None]
            expected[(security, _START + timedelta(days=position))] = (
                _reference(operator, numbers)
                if start >= 0 and end > 0 and len(numbers) == window
                else None
            )
    actual = {(value.security_id, value.as_of): value.value for value in evaluation.values}
    assert actual == expected
