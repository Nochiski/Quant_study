from __future__ import annotations

from math import sqrt
from statistics import mean


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
