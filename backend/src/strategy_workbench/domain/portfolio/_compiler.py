from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import date
from enum import Enum
from typing import TypeGuard

from strategy_workbench.domain.strategy.facade.specification import (
    ComparisonOperator,
    FactorDirection,
    PortfolioSide,
    RebalanceFrequency,
    SelectionMethod,
    StrategySpec,
    WeightingMethod,
    strategy_spec_hash,
)

from ._models import (
    CandidateDecision,
    CandidateSide,
    ExclusionReason,
    PortfolioObservation,
    TargetFrame,
    TargetPosition,
    TargetTape,
)


def compile_target_tape(
    spec: StrategySpec,
    *,
    data_snapshot_id: str,
    sessions: tuple[date, ...],
    observations: tuple[PortfolioObservation, ...],
) -> TargetTape:
    ordered_sessions = tuple(sorted(set(sessions)))
    if len(ordered_sessions) != len(sessions):
        raise ValueError("portfolio sessions must be unique")
    by_date: dict[date, list[PortfolioObservation]] = {}
    seen: set[tuple[date, str]] = set()
    for observation in observations:
        key = (observation.as_of, observation.security_id)
        if key in seen:
            raise ValueError(f"duplicate portfolio observation: key={key}")
        seen.add(key)
        by_date.setdefault(observation.as_of, []).append(observation)

    frames = tuple(
        _compile_frame(spec, signal_as_of, execution_on, tuple(by_date.get(signal_as_of, ())))
        for signal_as_of, execution_on in _rebalance_pairs(spec, ordered_sessions)
    )
    strategy_hash = strategy_spec_hash(spec)
    payload = {
        "data_snapshot_id": data_snapshot_id,
        "strategy_hash": strategy_hash,
        "execution_timing": spec.execution.timing.value,
        "frames": [asdict(frame) for frame in frames],
    }
    tape_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    ).hexdigest()
    return TargetTape(
        data_snapshot_id=data_snapshot_id,
        strategy_hash=strategy_hash,
        tape_hash=tape_hash,
        frames=frames,
        execution_timing=spec.execution.timing.value,
    )


def _rebalance_pairs(
    spec: StrategySpec, sessions: tuple[date, ...]
) -> tuple[tuple[date, date], ...]:
    selected: set[int] = set()
    if spec.portfolio.rebalance is RebalanceFrequency.EVERY_N_SESSIONS:
        interval = spec.portfolio.rebalance_every_n_sessions
        selected.update(index for index in range(len(sessions)) if (index + 1) % interval == 0)
    else:
        grouped: dict[tuple[int, ...], int] = {}
        for index, session in enumerate(sessions):
            if spec.portfolio.rebalance is RebalanceFrequency.WEEKLY:
                iso = session.isocalendar()
                key = (iso.year, iso.week)
            elif spec.portfolio.rebalance is RebalanceFrequency.MONTHLY:
                key = (session.year, session.month)
            else:
                key = (session.year, (session.month - 1) // 3 + 1)
            grouped[key] = index
        selected.update(grouped.values())
    return tuple(
        (sessions[index], sessions[index + 1])
        for index in sorted(selected)
        if index + 1 < len(sessions)
    )


def _compile_frame(
    spec: StrategySpec,
    signal_as_of: date,
    execution_on: date,
    observations: tuple[PortfolioObservation, ...],
) -> TargetFrame:
    decisions = [_score_candidate(spec, observation) for observation in observations]
    ranked = sorted(
        (decision for decision in decisions if decision.eligible),
        key=lambda item: (-(item.composite_score or 0.0), item.security_id),
    )
    rank_by_id = {item.security_id: index for index, item in enumerate(ranked, start=1)}
    decisions = [
        replace(decision, rank=rank_by_id.get(decision.security_id)) for decision in decisions
    ]
    long_count, short_count = _selection_counts(spec, len(ranked))
    long_ids = {item.security_id for item in ranked[:long_count]}
    short_ids = (
        {item.security_id for item in ranked[-short_count:] if item.security_id not in long_ids}
        if spec.portfolio.side is PortfolioSide.LONG_SHORT
        else set()
    )
    observations_by_id = {item.security_id: item for item in observations}
    buffer_retained = _apply_turnover_buffer(
        spec,
        ranked,
        observations_by_id,
        long_ids=long_ids,
        short_ids=short_ids,
        long_count=long_count,
        short_count=short_count,
    )
    weights, weight_reasons = _target_weights(
        spec,
        ranked,
        observations_by_id,
        long_ids=long_ids,
        short_ids=short_ids,
    )
    decisions = [
        _finalize_decision(
            decision,
            observations_by_id[decision.security_id],
            long_ids,
            short_ids,
            weights,
            weight_reasons,
            buffer_retained,
        )
        for decision in decisions
    ]
    targets = tuple(
        TargetPosition(
            security_id=decision.security_id,
            weight=decision.target_weight,
            composite_score=decision.composite_score or 0.0,
            rank=decision.rank or 0,
            side=decision.side or CandidateSide.LONG,
        )
        for decision in sorted(decisions, key=lambda item: item.security_id)
        if decision.selected and decision.target_weight != 0
    )
    return TargetFrame(
        signal_as_of=signal_as_of,
        execution_on=execution_on,
        targets=targets,
        candidates=tuple(sorted(decisions, key=lambda item: item.security_id)),
    )


def _score_candidate(spec: StrategySpec, observation: PortfolioObservation) -> CandidateDecision:
    reasons: list[ExclusionReason] = []
    if not observation.universe_member:
        reasons.append(ExclusionReason.NOT_IN_UNIVERSE)
    fields = {item.field_id: item for item in observation.fields}
    for rule in spec.eligibility.rules:
        field = fields.get(rule.field_id)
        if field is None or not _number(field.value):
            reasons.append(ExclusionReason.MISSING_ELIGIBILITY)
        elif field.available_date > observation.as_of:
            reasons.append(ExclusionReason.FUTURE_DATA)
        elif not _compare(float(field.value), rule.operator, rule.value):
            reasons.append(ExclusionReason.ELIGIBILITY_FAILED)
    if spec.portfolio.liquidity_field_id and spec.portfolio.minimum_liquidity is not None:
        liquidity = fields.get(spec.portfolio.liquidity_field_id)
        if (
            liquidity is None
            or not _number(liquidity.value)
            or liquidity.available_date > observation.as_of
            or float(liquidity.value) < spec.portfolio.minimum_liquidity
        ):
            reasons.append(ExclusionReason.LIQUIDITY_FAILED)
    if spec.signal.regime_field_id and spec.signal.regime_minimum is not None:
        regime = fields.get(spec.signal.regime_field_id)
        if (
            regime is None
            or not _number(regime.value)
            or regime.available_date > observation.as_of
            or float(regime.value) < spec.signal.regime_minimum
        ):
            reasons.append(ExclusionReason.REGIME_BLOCKED)

    factors = {item.factor_id: item for item in observation.factor_values}
    score = 0.0
    denominator = 0.0
    for factor in spec.factors.factors:
        value = factors.get(factor.factor_id)
        if value is None or value.value is None:
            reasons.append(ExclusionReason.MISSING_FACTOR)
            continue
        if value.available_date > observation.as_of:
            reasons.append(ExclusionReason.FUTURE_DATA)
            continue
        direction = 1.0 if factor.direction is FactorDirection.HIGH else -1.0
        score += direction * factor.weight * value.value
        denominator += abs(factor.weight)
    composite_score = score / denominator if denominator else None
    if composite_score is not None and spec.signal.score_threshold is not None:
        passes = (
            abs(composite_score) >= abs(spec.signal.score_threshold)
            if spec.portfolio.side is PortfolioSide.LONG_SHORT
            else composite_score >= spec.signal.score_threshold
        )
        if not passes:
            reasons.append(ExclusionReason.SCORE_THRESHOLD)
    blocking = {
        ExclusionReason.NOT_IN_UNIVERSE,
        ExclusionReason.FUTURE_DATA,
        ExclusionReason.MISSING_ELIGIBILITY,
        ExclusionReason.ELIGIBILITY_FAILED,
        ExclusionReason.MISSING_FACTOR,
        ExclusionReason.SCORE_THRESHOLD,
        ExclusionReason.REGIME_BLOCKED,
        ExclusionReason.LIQUIDITY_FAILED,
    }
    return CandidateDecision(
        as_of=observation.as_of,
        security_id=observation.security_id,
        eligible=not any(reason in blocking for reason in reasons),
        selected=False,
        composite_score=composite_score,
        rank=None,
        side=None,
        target_weight=0.0,
        sector_id=observation.sector_id,
        exclusion_reasons=tuple(dict.fromkeys(reasons)),
    )


def _selection_counts(spec: StrategySpec, eligible_count: int) -> tuple[int, int]:
    if spec.portfolio.selection_method is SelectionMethod.PERCENTILE:
        count = max(1, math.ceil(eligible_count * spec.portfolio.selection_percentile))
        return min(count, eligible_count), min(count, eligible_count)
    return (
        min(spec.portfolio.selection_count, eligible_count),
        min(spec.portfolio.short_selection_count, eligible_count),
    )


def _apply_turnover_buffer(
    spec: StrategySpec,
    ranked: list[CandidateDecision],
    observations: dict[str, PortfolioObservation],
    *,
    long_ids: set[str],
    short_ids: set[str],
    long_count: int,
    short_count: int,
) -> set[str]:
    retained: set[str] = set()
    buffer_count = spec.portfolio.turnover_buffer_count
    if buffer_count == 0:
        return retained
    for index, candidate in enumerate(ranked):
        previous = observations[candidate.security_id].previous_weight
        if previous > 0 and index < long_count + buffer_count:
            if candidate.security_id not in long_ids:
                retained.add(candidate.security_id)
            long_ids.add(candidate.security_id)
            short_ids.discard(candidate.security_id)
        from_bottom = len(ranked) - index
        if (
            previous < 0
            and spec.portfolio.side is PortfolioSide.LONG_SHORT
            and from_bottom <= short_count + buffer_count
        ):
            if candidate.security_id not in short_ids:
                retained.add(candidate.security_id)
            short_ids.add(candidate.security_id)
            long_ids.discard(candidate.security_id)
    return retained


def _target_weights(
    spec: StrategySpec,
    ranked: list[CandidateDecision],
    observations: dict[str, PortfolioObservation],
    *,
    long_ids: set[str],
    short_ids: set[str],
) -> tuple[dict[str, float], dict[str, ExclusionReason]]:
    long_budget = (
        (spec.risk.gross_exposure + spec.risk.net_exposure) / 2
        if spec.portfolio.side is PortfolioSide.LONG_SHORT
        else spec.risk.gross_exposure
    )
    short_budget = (
        max((spec.risk.gross_exposure - spec.risk.net_exposure) / 2, 0.0)
        if spec.portfolio.side is PortfolioSide.LONG_SHORT
        else 0.0
    )
    reasons: dict[str, ExclusionReason] = {}
    long_scores = _weight_scores(spec, ranked, observations, long_ids, reasons)
    short_scores = _weight_scores(spec, list(reversed(ranked)), observations, short_ids, reasons)
    weights = _capped_allocate(long_scores, long_budget, spec.risk.max_name_weight)
    weights.update(
        {
            security_id: -weight
            for security_id, weight in _capped_allocate(
                short_scores, short_budget, spec.risk.max_name_weight
            ).items()
        }
    )
    for security_id, observation in observations.items():
        proposed = weights.get(security_id, 0.0)
        sign_compatible = (security_id in long_ids and observation.previous_weight >= 0) or (
            security_id in short_ids and observation.previous_weight <= 0
        )
        within_name_cap = abs(observation.previous_weight) <= spec.risk.max_name_weight
        if (
            sign_compatible
            and within_name_cap
            and abs(proposed - observation.previous_weight) < spec.portfolio.minimum_trade_weight
        ):
            if proposed != observation.previous_weight:
                reasons[security_id] = ExclusionReason.MINIMUM_TRADE
            if observation.previous_weight != 0:
                weights[security_id] = observation.previous_weight
    weights = _apply_sector_constraints(spec, weights, observations)
    return _apply_side_budgets(weights, long_budget, short_budget), reasons


def _apply_side_budgets(
    weights: dict[str, float],
    long_budget: float,
    short_budget: float,
) -> dict[str, float]:
    result = dict(weights)
    current_long = sum(max(weight, 0.0) for weight in result.values())
    current_short = sum(abs(min(weight, 0.0)) for weight in result.values())
    long_scale = min(long_budget / current_long, 1.0) if current_long > 0 else 0.0
    short_scale = min(short_budget / current_short, 1.0) if current_short > 0 else 0.0
    for security_id, weight in result.items():
        result[security_id] = weight * (long_scale if weight > 0 else short_scale)
    return result


def _weight_scores(
    spec: StrategySpec,
    ranked: list[CandidateDecision],
    observations: dict[str, PortfolioObservation],
    selected: set[str],
    reasons: dict[str, ExclusionReason],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for order, candidate in enumerate(ranked, start=1):
        if candidate.security_id not in selected:
            continue
        if spec.portfolio.weighting is WeightingMethod.EQUAL:
            score = 1.0
        elif spec.portfolio.weighting is WeightingMethod.FACTOR_SCORE:
            score = max(abs(candidate.composite_score or 0.0), 1e-12)
        elif spec.portfolio.weighting is WeightingMethod.RANK:
            score = float(len(selected) - min(order, len(selected)) + 1)
        else:
            field_id = spec.risk.risk_field_id
            risk = _field(observations[candidate.security_id], field_id)
            if risk is None or risk <= 0:
                reasons[candidate.security_id] = ExclusionReason.MISSING_RISK
                continue
            score = 1 / risk
        scores[candidate.security_id] = score
    return scores


def _capped_allocate(scores: dict[str, float], budget: float, cap: float) -> dict[str, float]:
    allocation = {security_id: 0.0 for security_id in scores}
    remaining = set(scores)
    remaining_budget = max(budget, 0.0)
    while remaining and remaining_budget > 1e-15:
        denominator = sum(scores[security_id] for security_id in remaining)
        if denominator <= 0:
            break
        proposed = {
            security_id: remaining_budget * scores[security_id] / denominator
            for security_id in remaining
        }
        capped = {security_id for security_id, weight in proposed.items() if weight > cap}
        if not capped:
            for security_id, weight in proposed.items():
                allocation[security_id] += weight
            break
        for security_id in capped:
            room = max(cap - allocation[security_id], 0.0)
            allocation[security_id] += room
            remaining_budget -= room
            remaining.remove(security_id)
    return allocation


def _apply_sector_constraints(
    spec: StrategySpec,
    weights: dict[str, float],
    observations: dict[str, PortfolioObservation],
) -> dict[str, float]:
    result = dict(weights)
    sectors: dict[str, list[str]] = {}
    for security_id in weights:
        sector = observations[security_id].sector_id or "__unknown__"
        sectors.setdefault(sector, []).append(security_id)
    for security_ids in sectors.values():
        exposure = sum(abs(result[item]) for item in security_ids)
        if exposure > spec.risk.max_sector_weight:
            scale = spec.risk.max_sector_weight / exposure
            for security_id in security_ids:
                result[security_id] *= scale
        if spec.risk.sector_neutral and spec.portfolio.side is PortfolioSide.LONG_SHORT:
            longs = sum(max(result[item], 0.0) for item in security_ids)
            shorts = sum(abs(min(result[item], 0.0)) for item in security_ids)
            matched = min(longs, shorts)
            for security_id in security_ids:
                weight = result[security_id]
                if weight > 0:
                    result[security_id] = weight * matched / longs if longs > 0 else 0.0
                elif weight < 0:
                    result[security_id] = weight * matched / shorts if shorts > 0 else 0.0
    return result


def _finalize_decision(
    decision: CandidateDecision,
    observation: PortfolioObservation,
    long_ids: set[str],
    short_ids: set[str],
    weights: dict[str, float],
    weight_reasons: dict[str, ExclusionReason],
    buffer_retained: set[str],
) -> CandidateDecision:
    reasons = list(decision.exclusion_reasons)
    if decision.security_id in buffer_retained:
        reasons.append(ExclusionReason.TURNOVER_BUFFER)
    side = (
        CandidateSide.LONG
        if decision.security_id in long_ids
        else CandidateSide.SHORT
        if decision.security_id in short_ids
        else None
    )
    selected = side is not None and decision.security_id not in weight_reasons
    if decision.eligible and side is None:
        reasons.append(ExclusionReason.OUTSIDE_SELECTION)
    weight_reason = weight_reasons.get(decision.security_id)
    if weight_reason is not None:
        reasons.append(weight_reason)
        selected = (
            weight_reason is ExclusionReason.MINIMUM_TRADE and observation.previous_weight != 0
        )
        if selected:
            side = CandidateSide.LONG if observation.previous_weight > 0 else CandidateSide.SHORT
    return replace(
        decision,
        selected=selected,
        side=side,
        target_weight=weights.get(decision.security_id, 0.0) if selected else 0.0,
        exclusion_reasons=tuple(dict.fromkeys(reasons)),
    )


def _field(observation: PortfolioObservation, field_id: str | None) -> float | None:
    if field_id is None:
        return None
    value = next((item for item in observation.fields if item.field_id == field_id), None)
    if value is None or value.available_date > observation.as_of or not _number(value.value):
        return None
    return float(value.value)


def _compare(value: float, operator: ComparisonOperator, threshold: float) -> bool:
    if operator is ComparisonOperator.GREATER_THAN:
        return value > threshold
    if operator is ComparisonOperator.GREATER_THAN_OR_EQUAL:
        return value >= threshold
    if operator is ComparisonOperator.LESS_THAN:
        return value < threshold
    if operator is ComparisonOperator.LESS_THAN_OR_EQUAL:
        return value <= threshold
    return value == threshold


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported TargetTape value: {type(value).__name__}")
