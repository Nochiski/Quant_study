from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from statistics import mean, pstdev


def cross_sectional_rank(values: Sequence[float]) -> list[float]:
    """횡단면 백분위 순위를 0~1 로 돌려준다(입력 순서 그대로).

    동점은 평균 순위를 공유하므로 입력 순서가 결과를 바꾸지 않는다. 표본이 1개면 분모가 0 이 되지
    않도록 1 로 막아 유일한 값이 0.0 이 된다 — 횡단면에 비교 대상이 없으면 순위 정보도 없다.

    `CrossSectionalOperator.RANK`·`GroupOperator.RANK` 와 `signal.normalization: rank` 가 같은
    정의를 쓰도록 이 함수 하나가 순위 공식을 소유한다(P2-04).
    """
    ranked = rank_items(list(enumerate(values)))
    denominator = max(len(ranked) - 1, 1)
    return [(ranked[index] - 1) / denominator for index in range(len(values))]


def cross_sectional_zscore(values: Sequence[float]) -> list[float]:
    """횡단면 표준화 값을 돌려준다(입력 순서 그대로).

    모집단 표준편차(`pstdev`)를 쓴다. 분산이 0 이면(표본 1개 포함) 편차 정보가 없으므로 전부
    0.0 이다 — 0 으로 나누는 대신 "정보 없음"을 중립값으로 표현한다.

    `CrossSectionalOperator.ZSCORE` 와 `signal.normalization: zscore` 가 같은 정의를 쓰도록 이
    함수 하나가 표준화 공식을 소유한다(P2-04).
    """
    if not values:
        return []
    center = mean(values)
    deviation = pstdev(values)
    if deviation == 0:
        return [0.0] * len(values)
    return [(value - center) / deviation for value in values]


def rank_items(values: list[tuple[int, float]]) -> dict[int, float]:
    ordered = sorted(values, key=lambda item: (item[1], item[0]))
    ranks: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = (position + 1 + end) / 2
        for index, _ in ordered[position:end]:
            ranks[index] = average_rank
        position = end
    return ranks


def quantile(ordered: list[float], probability: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_center = mean(left)
    right_center = mean(right)
    numerator = sum(
        (left_value - left_center) * (right_value - right_center)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = sqrt(sum((value - left_center) ** 2 for value in left))
    right_scale = sqrt(sum((value - right_center) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)
