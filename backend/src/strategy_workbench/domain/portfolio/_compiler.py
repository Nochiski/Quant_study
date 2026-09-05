from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from enum import Enum
from typing import TypeGuard, TypeVar

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

from ._construction_trace import (
    FactorContributionStatus,
    FactorContributionTrace,
    PortfolioCandidateTrace,
    PortfolioConstraintEffect,
    PortfolioConstructionTrace,
    PortfolioTraceSelection,
    TargetTapeTraceResult,
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

_T = TypeVar("_T")
_CHECKPOINT_BATCH = 256


def _noop_checkpoint() -> None:
    return None


def _checkpointed(items: Iterable[_T], checkpoint: Callable[[], None]) -> Iterator[_T]:
    for index, item in enumerate(items):
        if index % _CHECKPOINT_BATCH == 0:
            checkpoint()
        yield item


class NonFinitePortfolioCalculationError(ArithmeticError):
    """Portfolio arithmetic produced a value that cannot enter an auditable TargetTape."""

    def __init__(self, *, stage: str, value: float, context: str) -> None:
        super().__init__(
            "portfolio calculation produced a non-finite value — "
            f"stage={stage!r} value={value!r} context={context}"
        )
        self.stage = stage
        self.value = value
        self.context = context


@dataclass(frozen=True)
class PortfolioRebalanceSchedule:
    """Compiler-owned rebalance pairs shared by preflight and TargetTape construction."""

    sessions: tuple[date, ...]
    pairs: tuple[tuple[date, date], ...]
    rebalance: RebalanceFrequency
    rebalance_every_n_sessions: int

    @property
    def first_signal_as_of(self) -> date | None:
        return self.pairs[0][0] if self.pairs else None

    def resolve_signal_as_of(self, requested: date | None) -> date | None:
        """Resolve an explicit date or the latest executable signal without calendar guessing."""
        if requested is not None:
            return requested
        return self.pairs[-1][0] if self.pairs else None


def compile_rebalance_schedule(
    spec: StrategySpec,
    sessions: tuple[date, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> PortfolioRebalanceSchedule:
    ordered_sessions = tuple(sorted(set(sessions)))
    if len(ordered_sessions) != len(sessions):
        raise ValueError("portfolio sessions must be unique")
    return PortfolioRebalanceSchedule(
        sessions=ordered_sessions,
        pairs=_rebalance_pairs(spec, ordered_sessions, checkpoint=checkpoint),
        rebalance=spec.portfolio.rebalance,
        rebalance_every_n_sessions=spec.portfolio.rebalance_every_n_sessions,
    )


def compile_target_tape(
    spec: StrategySpec,
    *,
    data_snapshot_id: str,
    sessions: tuple[date, ...],
    observations: tuple[PortfolioObservation, ...],
    schedule: PortfolioRebalanceSchedule | None = None,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> TargetTape:
    """Compile the canonical executable tape without retaining an audit projection."""
    return _compile_target_tape(
        spec,
        data_snapshot_id=data_snapshot_id,
        sessions=sessions,
        observations=observations,
        schedule=schedule,
        trace_selection=None,
        checkpoint=checkpoint,
    ).tape


def compile_target_tape_with_trace(
    spec: StrategySpec,
    *,
    data_snapshot_id: str,
    sessions: tuple[date, ...],
    observations: tuple[PortfolioObservation, ...],
    trace_selection: PortfolioTraceSelection,
    schedule: PortfolioRebalanceSchedule | None = None,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> TargetTapeTraceResult:
    """Compile once and return an out-of-band audit from that same calculation."""
    return _compile_target_tape(
        spec,
        data_snapshot_id=data_snapshot_id,
        sessions=sessions,
        observations=observations,
        schedule=schedule,
        trace_selection=trace_selection,
        checkpoint=checkpoint,
    )


def _compile_target_tape(
    spec: StrategySpec,
    *,
    data_snapshot_id: str,
    sessions: tuple[date, ...],
    observations: tuple[PortfolioObservation, ...],
    schedule: PortfolioRebalanceSchedule | None,
    trace_selection: PortfolioTraceSelection | None,
    checkpoint: Callable[[], None],
) -> TargetTapeTraceResult:
    prepared_schedule = schedule or compile_rebalance_schedule(
        spec, sessions, checkpoint=checkpoint
    )
    ordered_sessions = tuple(sorted(set(sessions)))
    if len(ordered_sessions) != len(sessions):
        raise ValueError("portfolio sessions must be unique")
    if (
        prepared_schedule.sessions != ordered_sessions
        or prepared_schedule.rebalance is not spec.portfolio.rebalance
        or prepared_schedule.rebalance_every_n_sessions != spec.portfolio.rebalance_every_n_sessions
    ):
        raise ValueError("portfolio rebalance schedule does not match the strategy and sessions")
    by_date: dict[date, list[PortfolioObservation]] = {}
    seen: set[tuple[date, str]] = set()
    for observation in _checkpointed(observations, checkpoint):
        key = (observation.as_of, observation.security_id)
        if key in seen:
            raise ValueError(f"duplicate portfolio observation: key={key}")
        seen.add(key)
        by_date.setdefault(observation.as_of, []).append(observation)

    compiled: list[TargetFrame] = []
    construction_trace: PortfolioConstructionTrace | None = None
    trace_as_of = (
        prepared_schedule.resolve_signal_as_of(trace_selection.as_of)
        if trace_selection is not None
        else None
    )
    # The book is folded frame by frame: the compiler owns `previous_weight` from the second
    # rebalance on, and `PortfolioObservation.previous_weight` seeds only the first (D-002).
    carried: dict[str, float] | None = None
    for signal_as_of, execution_on in _checkpointed(prepared_schedule.pairs, checkpoint):
        frame_observations = tuple(by_date.get(signal_as_of, ()))
        previous_weights = (
            {
                item.security_id: item.previous_weight
                for item in _checkpointed(frame_observations, checkpoint)
            }
            if carried is None
            else carried
        )
        selected_ids = (
            set(trace_selection.security_ids)
            if trace_selection is not None and trace_as_of == signal_as_of
            else None
        )
        frame_result = _compile_frame(
            spec,
            signal_as_of,
            execution_on,
            frame_observations,
            previous_weights,
            trace_security_ids=selected_ids,
            include_order_delta=(
                trace_selection is not None
                and selected_ids is not None
                and trace_selection.include_order_delta
            ),
            checkpoint=checkpoint,
        )
        frame = frame_result.frame
        _require_finite_tree(
            frame,
            stage="frame",
            context=f"signal_as_of={signal_as_of} execution_on={execution_on}",
            checkpoint=checkpoint,
        )
        compiled.append(frame)
        if selected_ids is not None:
            construction_trace = PortfolioConstructionTrace(
                signal_as_of=signal_as_of,
                execution_on=execution_on,
                candidates=frame_result.trace_candidates,
            )
            _require_finite_tree(
                construction_trace,
                stage="construction_trace",
                context=f"signal_as_of={signal_as_of} execution_on={execution_on}",
                checkpoint=checkpoint,
            )
        carried = {
            target.security_id: target.weight for target in _checkpointed(frame.targets, checkpoint)
        }
    frames = tuple(compiled)
    checkpoint()
    strategy_hash = strategy_spec_hash(spec)
    payload = {
        "data_snapshot_id": data_snapshot_id,
        "strategy_hash": strategy_hash,
        "execution_timing": spec.execution.timing.value,
        "frames": _canonical_payload(frames, checkpoint=checkpoint),
    }
    tape_hash = _hash_payload(payload, checkpoint=checkpoint)
    return TargetTapeTraceResult(
        tape=TargetTape(
            data_snapshot_id=data_snapshot_id,
            strategy_hash=strategy_hash,
            tape_hash=tape_hash,
            frames=frames,
            execution_timing=spec.execution.timing.value,
        ),
        trace=construction_trace,
    )


def _rebalance_pairs(
    spec: StrategySpec,
    sessions: tuple[date, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> tuple[tuple[date, date], ...]:
    selected: set[int] = set()
    if spec.portfolio.rebalance is RebalanceFrequency.EVERY_N_SESSIONS:
        interval = spec.portfolio.rebalance_every_n_sessions
        selected.update(
            index
            for index in _checkpointed(range(len(sessions)), checkpoint)
            if (index + 1) % interval == 0
        )
    else:
        grouped: dict[tuple[int, ...], int] = {}
        for index, session in _checkpointed(enumerate(sessions), checkpoint):
            if spec.portfolio.rebalance is RebalanceFrequency.WEEKLY:
                iso = session.isocalendar()
                key = (iso.year, iso.week)
            elif spec.portfolio.rebalance is RebalanceFrequency.MONTHLY:
                key = (session.year, session.month)
            else:
                key = (session.year, (session.month - 1) // 3 + 1)
            grouped[key] = index
        selected.update(grouped.values())
    pairs: list[tuple[date, date]] = []
    for index in _checkpointed(sorted(selected), checkpoint):
        if index + 1 < len(sessions):
            pairs.append((sessions[index], sessions[index + 1]))
    return tuple(pairs)


@dataclass(frozen=True)
class _ScoredCandidate:
    decision: CandidateDecision
    contributions: tuple[FactorContributionTrace, ...]


@dataclass(frozen=True)
class _TargetWeightResult:
    constrained: dict[str, float]
    unconstrained: dict[str, float]
    reasons: dict[str, ExclusionReason]


@dataclass(frozen=True)
class _FrameCompilation:
    frame: TargetFrame
    trace_candidates: tuple[PortfolioCandidateTrace, ...] = ()


def _compile_frame(
    spec: StrategySpec,
    signal_as_of: date,
    execution_on: date,
    observations: tuple[PortfolioObservation, ...],
    previous_weights: Mapping[str, float],
    *,
    trace_security_ids: set[str] | None = None,
    include_order_delta: bool = False,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> _FrameCompilation:
    """One rebalance. `previous_weights` is the book carried in, never read off observations."""
    scored = [
        _score_candidate(
            spec,
            observation,
            include_trace=(
                trace_security_ids is not None and observation.security_id in trace_security_ids
            ),
        )
        for observation in _checkpointed(observations, checkpoint)
    ]
    contributions_by_id = {
        item.decision.security_id: item.contributions for item in _checkpointed(scored, checkpoint)
    }
    decisions = [item.decision for item in _checkpointed(scored, checkpoint)]
    ranked = sorted(
        (decision for decision in _checkpointed(decisions, checkpoint) if decision.eligible),
        key=lambda item: (-(item.composite_score or 0.0), item.security_id),
    )
    rank_by_id = {
        item.security_id: index
        for index, item in _checkpointed(enumerate(ranked, start=1), checkpoint)
    }
    decisions = [
        replace(decision, rank=rank_by_id.get(decision.security_id))
        for decision in _checkpointed(decisions, checkpoint)
    ]
    long_count, short_count = _selection_counts(spec, len(ranked))
    long_ids = {item.security_id for item in ranked[:long_count]}
    short_ids = (
        {item.security_id for item in ranked[-short_count:] if item.security_id not in long_ids}
        if spec.portfolio.side is PortfolioSide.LONG_SHORT
        else set()
    )
    observations_by_id = {
        item.security_id: item for item in _checkpointed(observations, checkpoint)
    }
    buffer_retained = _apply_turnover_buffer(
        spec,
        ranked,
        previous_weights,
        long_ids=long_ids,
        short_ids=short_ids,
        long_count=long_count,
        short_count=short_count,
        checkpoint=checkpoint,
    )
    weight_result = _target_weights(
        spec,
        ranked,
        observations_by_id,
        previous_weights,
        long_ids=long_ids,
        short_ids=short_ids,
        checkpoint=checkpoint,
    )
    decisions = [
        _finalize_decision(
            decision,
            previous_weights.get(decision.security_id, 0.0),
            long_ids,
            short_ids,
            weight_result.constrained,
            weight_result.reasons,
            buffer_retained,
        )
        for decision in _checkpointed(decisions, checkpoint)
    ]
    targets = tuple(
        TargetPosition(
            security_id=decision.security_id,
            weight=decision.target_weight,
            composite_score=decision.composite_score or 0.0,
            rank=decision.rank or 0,
            side=decision.side or CandidateSide.LONG,
        )
        for decision in _checkpointed(
            sorted(decisions, key=lambda item: item.security_id), checkpoint
        )
        if decision.selected and decision.target_weight != 0
    )
    ordered_decisions = tuple(sorted(decisions, key=lambda item: item.security_id))
    trace_candidates = (
        tuple(
            _candidate_trace(
                decision,
                contributions_by_id[decision.security_id],
                previous_weights.get(decision.security_id, 0.0),
                weight_result.unconstrained.get(decision.security_id),
                include_order_delta=include_order_delta,
            )
            for decision in _checkpointed(ordered_decisions, checkpoint)
            if decision.security_id in trace_security_ids
        )
        if trace_security_ids is not None
        else ()
    )
    return _FrameCompilation(
        frame=TargetFrame(
            signal_as_of=signal_as_of,
            execution_on=execution_on,
            targets=targets,
            candidates=ordered_decisions,
        ),
        trace_candidates=trace_candidates,
    )


def _score_candidate(
    spec: StrategySpec,
    observation: PortfolioObservation,
    *,
    include_trace: bool,
) -> _ScoredCandidate:
    """Score one candidate. FUTURE_DATA covers dated values only.

    Fields and factor values carry an `available_date`, so a value published after `as_of` is
    excluded below. `universe_member` and `sector_id` carry none: a retroactive reconstitution or
    sector reclassification passes this function unchallenged and reaches selection and the sector
    exposure constraint (D-006). Answering both with the as_of vintage is the observation
    adapter's contract, not something this compiler can verify.
    """
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
    contributions: list[FactorContributionTrace] = []
    for factor in spec.factors.factors:
        value = factors.get(factor.factor_id)
        if value is None or value.value is None:
            reasons.append(ExclusionReason.MISSING_FACTOR)
            if include_trace:
                contributions.append(
                    FactorContributionTrace(
                        factor_id=factor.factor_id,
                        value=None,
                        configured_weight=factor.weight,
                        direction=factor.direction,
                        weighted_value=None,
                        normalized_contribution=None,
                        status=FactorContributionStatus.MISSING,
                    )
                )
            continue
        if not _number(value.value):
            raise NonFinitePortfolioCalculationError(
                stage="factor_input",
                value=value.value,
                context=(
                    f"as_of={observation.as_of} security_id={observation.security_id!r} "
                    f"factor_id={factor.factor_id!r}"
                ),
            )
        if value.available_date > observation.as_of:
            reasons.append(ExclusionReason.FUTURE_DATA)
            if include_trace:
                contributions.append(
                    FactorContributionTrace(
                        factor_id=factor.factor_id,
                        value=value.value,
                        configured_weight=factor.weight,
                        direction=factor.direction,
                        weighted_value=None,
                        normalized_contribution=None,
                        status=FactorContributionStatus.FUTURE_DATA,
                    )
                )
            continue
        direction = 1.0 if factor.direction is FactorDirection.HIGH else -1.0
        weighted_value = _finite(
            direction * factor.weight * value.value,
            # Preserve the executable compiler's historical failure code/stage: the term is part
            # of the same composite-score operation, merely retained for the audit projection.
            stage="composite_score",
            context=(
                f"as_of={observation.as_of} security_id={observation.security_id!r} "
                f"factor_id={factor.factor_id!r}"
            ),
        )
        score = _finite(
            score + weighted_value,
            stage="composite_score",
            context=(
                f"as_of={observation.as_of} security_id={observation.security_id!r} "
                f"factor_id={factor.factor_id!r}"
            ),
        )
        denominator = _finite(
            denominator + abs(factor.weight),
            stage="composite_denominator",
            context=f"security_id={observation.security_id!r}",
        )
        if include_trace:
            contributions.append(
                FactorContributionTrace(
                    factor_id=factor.factor_id,
                    value=value.value,
                    configured_weight=factor.weight,
                    direction=factor.direction,
                    weighted_value=weighted_value,
                    normalized_contribution=None,
                    status=FactorContributionStatus.OK,
                )
            )
    composite_score = (
        _finite(
            score / denominator,
            stage="composite_score",
            context=f"as_of={observation.as_of} security_id={observation.security_id!r}",
        )
        if denominator
        else None
    )
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
    normalized = tuple(
        replace(
            item,
            normalized_contribution=(
                _finite(
                    item.weighted_value / denominator,
                    stage="factor_contribution",
                    context=(
                        f"as_of={observation.as_of} security_id={observation.security_id!r} "
                        f"factor_id={item.factor_id!r}"
                    ),
                )
                if denominator and item.weighted_value is not None
                else None
            ),
        )
        for item in contributions
    )
    return _ScoredCandidate(
        decision=CandidateDecision(
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
        ),
        contributions=normalized,
    )


def _candidate_trace(
    decision: CandidateDecision,
    contributions: tuple[FactorContributionTrace, ...],
    previous_weight: float,
    unconstrained_weight: float | None,
    *,
    include_order_delta: bool,
) -> PortfolioCandidateTrace:
    if decision.side is None:
        constraint_effect = PortfolioConstraintEffect.NOT_SELECTED
    elif (
        not decision.selected
        or unconstrained_weight is None
        or (unconstrained_weight != 0 and decision.target_weight == 0)
    ):
        constraint_effect = PortfolioConstraintEffect.REMOVED
    elif math.isclose(
        unconstrained_weight,
        decision.target_weight,
        rel_tol=1e-12,
        abs_tol=1e-15,
    ):
        constraint_effect = PortfolioConstraintEffect.UNCHANGED
    else:
        constraint_effect = PortfolioConstraintEffect.ADJUSTED
    estimated_delta = (
        _finite(
            decision.target_weight - previous_weight,
            stage="estimated_order_delta",
            context=(f"as_of={decision.as_of} security_id={decision.security_id!r}"),
        )
        if include_order_delta
        else None
    )
    return PortfolioCandidateTrace(
        as_of=decision.as_of,
        security_id=decision.security_id,
        factor_contributions=contributions,
        composite_score=decision.composite_score,
        rank=decision.rank,
        eligible=decision.eligible,
        selected=decision.selected,
        side=decision.side,
        unconstrained_target_weight=unconstrained_weight,
        constrained_target_weight=decision.target_weight,
        previous_weight=previous_weight if include_order_delta else None,
        estimated_order_delta=estimated_delta,
        constraint_effect=constraint_effect,
        exclusion_reasons=decision.exclusion_reasons,
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
    previous_weights: Mapping[str, float],
    *,
    long_ids: set[str],
    short_ids: set[str],
    long_count: int,
    short_count: int,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> set[str]:
    retained: set[str] = set()
    buffer_count = spec.portfolio.turnover_buffer_count
    if buffer_count == 0:
        return retained
    for index, candidate in _checkpointed(enumerate(ranked), checkpoint):
        previous = previous_weights.get(candidate.security_id, 0.0)
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
    previous_weights: Mapping[str, float],
    *,
    long_ids: set[str],
    short_ids: set[str],
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> _TargetWeightResult:
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
    long_scores = _weight_scores(
        spec, ranked, observations, long_ids, reasons, checkpoint=checkpoint
    )
    short_scores = _weight_scores(
        spec,
        list(reversed(ranked)),
        observations,
        short_ids,
        reasons,
        checkpoint=checkpoint,
    )
    unconstrained = _proportional_allocate(long_scores, long_budget, checkpoint=checkpoint)
    unconstrained.update(
        {
            security_id: -weight
            for security_id, weight in _proportional_allocate(
                short_scores, short_budget, checkpoint=checkpoint
            ).items()
        }
    )
    weights = _capped_allocate(
        long_scores, long_budget, spec.risk.max_name_weight, checkpoint=checkpoint
    )
    weights.update(
        {
            security_id: -weight
            for security_id, weight in _capped_allocate(
                short_scores,
                short_budget,
                spec.risk.max_name_weight,
                checkpoint=checkpoint,
            ).items()
        }
    )
    for security_id in _checkpointed(observations, checkpoint):
        proposed = weights.get(security_id, 0.0)
        previous = previous_weights.get(security_id, 0.0)
        sign_compatible = (security_id in long_ids and previous >= 0) or (
            security_id in short_ids and previous <= 0
        )
        within_name_cap = abs(previous) <= spec.risk.max_name_weight
        if (
            sign_compatible
            and within_name_cap
            and abs(proposed - previous) < spec.portfolio.minimum_trade_weight
        ):
            if proposed != previous:
                reasons[security_id] = ExclusionReason.MINIMUM_TRADE
            if previous != 0:
                weights[security_id] = previous
    weights = _apply_sector_constraints(spec, weights, observations, checkpoint=checkpoint)
    return _TargetWeightResult(
        constrained=_apply_side_budgets(weights, long_budget, short_budget, checkpoint=checkpoint),
        unconstrained=unconstrained,
        reasons=reasons,
    )


def _apply_side_budgets(
    weights: dict[str, float],
    long_budget: float,
    short_budget: float,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    result = dict(weights)
    current_long = sum(max(weight, 0.0) for weight in _checkpointed(result.values(), checkpoint))
    current_short = sum(
        abs(min(weight, 0.0)) for weight in _checkpointed(result.values(), checkpoint)
    )
    long_scale = min(long_budget / current_long, 1.0) if current_long > 0 else 0.0
    short_scale = min(short_budget / current_short, 1.0) if current_short > 0 else 0.0
    for security_id, weight in _checkpointed(tuple(result.items()), checkpoint):
        result[security_id] = weight * (long_scale if weight > 0 else short_scale)
    return result


def _weight_scores(
    spec: StrategySpec,
    ranked: list[CandidateDecision],
    observations: dict[str, PortfolioObservation],
    selected: set[str],
    reasons: dict[str, ExclusionReason],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for order, candidate in _checkpointed(enumerate(ranked, start=1), checkpoint):
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
        scores[candidate.security_id] = _finite(
            score,
            stage="weight_score",
            context=f"security_id={candidate.security_id!r} weighting={spec.portfolio.weighting}",
        )
    return scores


def _proportional_allocate(
    scores: dict[str, float],
    budget: float,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    """Allocate before name/sector/min-trade constraints using the exact weight scores."""
    if not scores or budget <= 0:
        return {security_id: 0.0 for security_id in scores}
    denominator = _finite(
        sum(score for score in _checkpointed(scores.values(), checkpoint)),
        stage="unconstrained_allocation_denominator",
        context=f"count={len(scores)} budget={budget!r}",
    )
    if denominator <= 0:
        return {security_id: 0.0 for security_id in scores}
    return {
        security_id: _finite(
            budget * score / denominator,
            stage="unconstrained_allocation",
            context=f"security_id={security_id!r} budget={budget!r}",
        )
        for security_id, score in _checkpointed(scores.items(), checkpoint)
    }


def _capped_allocate(
    scores: dict[str, float],
    budget: float,
    cap: float,
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    allocation = {security_id: 0.0 for security_id in scores}
    remaining = set(scores)
    remaining_budget = max(budget, 0.0)
    while remaining and remaining_budget > 1e-15:
        checkpoint()
        denominator = _finite(
            sum(scores[security_id] for security_id in _checkpointed(remaining, checkpoint)),
            stage="allocation_denominator",
            context=f"remaining={len(remaining)} budget={remaining_budget!r}",
        )
        if denominator <= 0:
            break
        proposed = {
            security_id: _finite(
                remaining_budget * scores[security_id] / denominator,
                stage="allocation",
                context=f"security_id={security_id!r} budget={remaining_budget!r}",
            )
            for security_id in _checkpointed(remaining, checkpoint)
        }
        capped = {security_id for security_id, weight in proposed.items() if weight > cap}
        if not capped:
            for security_id, weight in _checkpointed(proposed.items(), checkpoint):
                allocation[security_id] = _finite(
                    allocation[security_id] + weight,
                    stage="allocation",
                    context=f"security_id={security_id!r}",
                )
            break
        for security_id in _checkpointed(capped, checkpoint):
            room = max(cap - allocation[security_id], 0.0)
            allocation[security_id] = _finite(
                allocation[security_id] + room,
                stage="allocation_cap",
                context=f"security_id={security_id!r} cap={cap!r}",
            )
            remaining_budget = _finite(
                remaining_budget - room,
                stage="remaining_budget",
                context=f"security_id={security_id!r} cap={cap!r}",
            )
            remaining.remove(security_id)
    return allocation


def _apply_sector_constraints(
    spec: StrategySpec,
    weights: dict[str, float],
    observations: dict[str, PortfolioObservation],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    result = dict(weights)
    sectors: dict[str, list[str]] = {}
    for security_id in _checkpointed(weights, checkpoint):
        sector = observations[security_id].sector_id or "__unknown__"
        sectors.setdefault(sector, []).append(security_id)
    for security_ids in _checkpointed(sectors.values(), checkpoint):
        exposure = sum(abs(result[item]) for item in security_ids)
        if exposure > spec.risk.max_sector_weight:
            scale = spec.risk.max_sector_weight / exposure
            for security_id in _checkpointed(security_ids, checkpoint):
                result[security_id] *= scale
        if spec.risk.sector_neutral and spec.portfolio.side is PortfolioSide.LONG_SHORT:
            longs = sum(max(result[item], 0.0) for item in security_ids)
            shorts = sum(abs(min(result[item], 0.0)) for item in security_ids)
            matched = min(longs, shorts)
            for security_id in _checkpointed(security_ids, checkpoint):
                weight = result[security_id]
                if weight > 0:
                    result[security_id] = weight * matched / longs if longs > 0 else 0.0
                elif weight < 0:
                    result[security_id] = weight * matched / shorts if shorts > 0 else 0.0
    return result


def _finalize_decision(
    decision: CandidateDecision,
    previous_weight: float,
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
        selected = weight_reason is ExclusionReason.MINIMUM_TRADE and previous_weight != 0
        if selected:
            side = CandidateSide.LONG if previous_weight > 0 else CandidateSide.SHORT
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
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _finite(value: float, *, stage: str, context: str) -> float:
    if not math.isfinite(value):
        raise NonFinitePortfolioCalculationError(stage=stage, value=value, context=context)
    return value


def _require_finite_tree(
    value: object,
    *,
    stage: str,
    context: str,
    checkpoint: Callable[[], None],
) -> None:
    """Defense-in-depth at the immutable frame boundary, independent of calculation branches."""
    if isinstance(value, float):
        _finite(value, stage=stage, context=context)
        return
    if is_dataclass(value) and not isinstance(value, type):
        checkpoint()
        for model_field in fields(value):
            _require_finite_tree(
                getattr(value, model_field.name),
                stage=stage,
                context=f"{context} field={model_field.name!r}",
                checkpoint=checkpoint,
            )
        return
    if isinstance(value, (tuple, list)):
        for item in _checkpointed(value, checkpoint):
            _require_finite_tree(item, stage=stage, context=context, checkpoint=checkpoint)
        return
    if isinstance(value, Mapping):
        for key, item in _checkpointed(value.items(), checkpoint):
            _require_finite_tree(
                item,
                stage=stage,
                context=f"{context} key={key!r}",
                checkpoint=checkpoint,
            )


def _canonical_payload(
    value: object,
    *,
    checkpoint: Callable[[], None],
) -> object:
    """Checkpointed ``asdict`` equivalent used by the TargetTape hash contract."""
    if isinstance(value, float):
        return _finite(value, stage="canonicalization", context="TargetTape payload")
    if is_dataclass(value) and not isinstance(value, type):
        checkpoint()
        return {
            model_field.name: _canonical_payload(
                getattr(value, model_field.name), checkpoint=checkpoint
            )
            for model_field in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [
            _canonical_payload(item, checkpoint=checkpoint)
            for item in _checkpointed(value, checkpoint)
        ]
    if isinstance(value, Mapping):
        return {
            key: _canonical_payload(item, checkpoint=checkpoint)
            for key, item in _checkpointed(value.items(), checkpoint)
        }
    return value


def _hash_payload(payload: Mapping[str, object], *, checkpoint: Callable[[], None]) -> str:
    """Stream the legacy JSON byte contract so cancellation also reaches hashing."""
    encoder = json.JSONEncoder(
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        allow_nan=False,
    )
    digest = hashlib.sha256()
    for chunk in _checkpointed(encoder.iterencode(payload), checkpoint):
        digest.update(chunk.encode())
    checkpoint()
    return digest.hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported TargetTape value: {type(value).__name__}")
