from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import mean

from ._evaluation import FactorObservation, FactorValue
from ._statistics import pearson, rank_items


@dataclass(frozen=True)
class FactorAnalytics:
    information_coefficient: float | None
    rank_information_coefficient: float | None
    quantile_spread: float | None
    coverage: float
    turnover: float | None
    decay: float | None
    observation_count: int
    valid_count: int


def analyze_factor_values(
    values: tuple[FactorValue, ...],
    observations: tuple[FactorObservation, ...],
) -> FactorAnalytics:
    returns_by_key = {
        (observation.as_of, observation.security_id): observation.forward_return
        for observation in observations
    }
    valid_pairs: list[tuple[FactorValue, float]] = []
    for value in values:
        forward_return = returns_by_key.get((value.as_of, value.security_id))
        if value.value is not None and forward_return is not None:
            valid_pairs.append((value, forward_return))
    factor_samples = [
        resolved_value for value, _ in valid_pairs if (resolved_value := value.value) is not None
    ]
    return_samples = [forward_return for _, forward_return in valid_pairs]
    information_coefficient = pearson(factor_samples, return_samples)
    rank_information_coefficient = _rank_correlation(factor_samples, return_samples)
    valid_values = tuple(value for value in values if value.value is not None)
    return FactorAnalytics(
        information_coefficient=information_coefficient,
        rank_information_coefficient=rank_information_coefficient,
        quantile_spread=_quantile_spread(valid_values, returns_by_key),
        coverage=len(valid_values) / len(values) if values else 0.0,
        turnover=_turnover(valid_values),
        decay=_decay(valid_values),
        observation_count=len(values),
        valid_count=len(valid_values),
    )


def _rank_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_rank_map = rank_items(list(enumerate(left)))
    right_rank_map = rank_items(list(enumerate(right)))
    return pearson(
        [left_rank_map[index] for index in range(len(left))],
        [right_rank_map[index] for index in range(len(right))],
    )


def _quantile_spread(
    values: tuple[FactorValue, ...],
    returns: dict[tuple[date, str], float | None],
) -> float | None:
    by_date: dict[date, list[FactorValue]] = {}
    for value in values:
        by_date.setdefault(value.as_of, []).append(value)
    spreads: list[float] = []
    for samples in by_date.values():
        ranked = sorted(
            (
                sample
                for sample in samples
                if returns.get((sample.as_of, sample.security_id)) is not None
            ),
            key=lambda sample: (float(sample.value or 0.0), sample.security_id),
        )
        bucket_size = max(len(ranked) // 3, 1)
        if len(ranked) < 2:
            continue
        low = _resolved_returns(ranked[:bucket_size], returns)
        high = _resolved_returns(ranked[-bucket_size:], returns)
        spreads.append(mean(high) - mean(low))
    return mean(spreads) if spreads else None


def _resolved_returns(
    samples: list[FactorValue],
    returns: dict[tuple[date, str], float | None],
) -> list[float]:
    resolved: list[float] = []
    for sample in samples:
        value = returns[(sample.as_of, sample.security_id)]
        if value is not None:
            resolved.append(value)
    return resolved


def _turnover(values: tuple[FactorValue, ...]) -> float | None:
    by_date: dict[date, list[FactorValue]] = {}
    for value in values:
        by_date.setdefault(value.as_of, []).append(value)
    selections: list[set[str]] = []
    for as_of in sorted(by_date):
        ranked = sorted(
            by_date[as_of],
            key=lambda sample: (float(sample.value or 0.0), sample.security_id),
            reverse=True,
        )
        count = max(len(ranked) // 3, 1)
        selections.append({sample.security_id for sample in ranked[:count]})
    changes = [
        1 - len(previous & current) / max(len(previous), len(current), 1)
        for previous, current in zip(selections, selections[1:], strict=False)
    ]
    return mean(changes) if changes else None


def _decay(values: tuple[FactorValue, ...]) -> float | None:
    by_date: dict[date, dict[str, float]] = {}
    for value in values:
        if value.value is not None:
            by_date.setdefault(value.as_of, {})[value.security_id] = value.value
    correlations: list[float] = []
    ordered_dates = sorted(by_date)
    for previous_date, current_date in zip(ordered_dates, ordered_dates[1:], strict=False):
        common = sorted(set(by_date[previous_date]) & set(by_date[current_date]))
        correlation = pearson(
            [by_date[previous_date][security_id] for security_id in common],
            [by_date[current_date][security_id] for security_id in common],
        )
        if correlation is not None:
            correlations.append(correlation)
    return mean(correlations) if correlations else None
