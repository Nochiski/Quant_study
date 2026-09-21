from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import TypeGuard, TypeVar

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.factor.facade.cross_section import (
    cross_sectional_rank,
    cross_sectional_zscore,
)
from strategy_workbench.domain.strategy.facade.specification import (
    CROSS_SECTIONAL_ELIGIBILITY_OPERATORS,
    EligibilityOperator,
    EligibilityRule,
    FactorDirection,
    PortfolioSide,
    RebalanceFrequency,
    SelectionMethod,
    SignalNormalization,
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

# 절대 규칙 = 횡단면이 아닌 나머지. 목록을 여기 다시 적지 않고 owner(`domain/strategy`)의
# 횡단면 집합에서 뺀다 — 연산자가 늘면 두 집합이 같이 움직인다.
_ABSOLUTE_ELIGIBILITY_OPERATORS: frozenset[EligibilityOperator] = (
    frozenset(EligibilityOperator) - CROSS_SECTIONAL_ELIGIBILITY_OPERATORS
)

# 후보를 선정 순위에서 빼는 사유. `_score_candidate`(1-pass)와 횡단면 2-pass 가 같은 집합으로
# `eligible` 을 다시 계산한다 — 2-pass 가 덧붙인 사유를 이 집합이 모르면 잘린 종목이 다시
# 순위에 들어간다.
_BLOCKING_EXCLUSIONS: frozenset[ExclusionReason] = frozenset(
    {
        ExclusionReason.NOT_IN_UNIVERSE,
        ExclusionReason.FUTURE_DATA,
        ExclusionReason.MISSING_ELIGIBILITY,
        ExclusionReason.ELIGIBILITY_FAILED,
        ExclusionReason.ELIGIBILITY_RANK_CUT,
        ExclusionReason.MISSING_FACTOR,
        ExclusionReason.SCORE_THRESHOLD,
        ExclusionReason.REGIME_BLOCKED,
        ExclusionReason.LIQUIDITY_FAILED,
    }
)


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
    environment: RunEnvironment,
    data_snapshot_id: str,
    sessions: tuple[date, ...],
    observations: tuple[PortfolioObservation, ...],
    schedule: PortfolioRebalanceSchedule | None = None,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> TargetTape:
    """Compile the canonical executable tape without retaining an audit projection."""
    return _compile_target_tape(
        spec,
        environment=environment,
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
    environment: RunEnvironment,
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
        environment=environment,
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
    # 체결 시점은 전략 문서가 아니라 실행 설정이 소유한다(1.2, spec D3 S2). tape hash payload 와
    # `TargetTape.execution_timing` 이 같은 값을 읽어야 같은 전략·다른 체결 시점이 같은 tape 로
    # 취급되지 않는다.
    environment: RunEnvironment,
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
        "execution_timing": environment.timing.value,
        "frames": _canonical_payload(frames, checkpoint=checkpoint),
    }
    tape_hash = _hash_payload(payload, checkpoint=checkpoint)
    return TargetTapeTraceResult(
        tape=TargetTape(
            data_snapshot_id=data_snapshot_id,
            strategy_hash=strategy_hash,
            tape_hash=tape_hash,
            frames=frames,
            execution_timing=environment.timing.value,
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
    # 1-pass 절대 eligibility 규칙(`gt`~`eq`)을 전부 통과했는가. 2-pass 횡단면 모집단의 자격이며
    # `decision.eligible`(유동성·레짐·팩터 결측·점수 문턱까지 반영)과 다르다 — 유동성 필터에
    # 걸린 종목까지 분모에서 빼면 "상위 20%"의 모집단이 단계마다 달라진다(spec D3 S5).
    passes_absolute_eligibility: bool


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
    # 정규화는 후보 하나로 판단할 수 없는 횡단면 사실이라 점수 계산보다 먼저 프레임 전체에서 센다.
    normalized_signals = _cross_sectional_signals(spec, observations, checkpoint=checkpoint)
    scored = [
        _score_candidate(
            spec,
            observation,
            normalized_signals=normalized_signals,
            include_trace=(
                trace_security_ids is not None and observation.security_id in trace_security_ids
            ),
        )
        for observation in _checkpointed(observations, checkpoint)
    ]
    contributions_by_id = {
        item.decision.security_id: item.contributions for item in _checkpointed(scored, checkpoint)
    }
    decisions = _apply_cross_sectional_eligibility(
        spec, observations, scored, checkpoint=checkpoint
    )
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


def _signal_value(
    spec: StrategySpec,
    observation: PortfolioObservation,
    factor_id: str,
    raw: float,
    normalized_signals: Mapping[tuple[str, str], float],
) -> float:
    """가중 합에 들어갈 값 하나. `none` 이면 원시값, 그 밖에는 정규화 값이다.

    `none` 이 아닌데 조회가 빗나가면 **기본값으로 떨어지지 않고 올린다.** default 를 원시값으로
    두면 정규화된 값과 원시값이 같은 가중 합에 섞여, 이 PR 이 없애려던 단위 지배가 진단도 예외도
    없이 되살아난다(P2-04 리뷰 P2-2). 지금은 `_cross_sectional_signals` 의 모집단 술어와
    `_score_candidate` 의 값 단위 탈락 술어가 글자 그대로 같아서 도달 불가이지만, 그 전제를
    건드리는 변경(예: 모집단을 eligible 종목으로 좁히기)이 오면 조용히 틀리는 대신 멈춰야 한다.
    """
    if spec.signal.normalization is SignalNormalization.NONE:
        return raw
    try:
        return normalized_signals[(factor_id, observation.security_id)]
    except KeyError as error:
        raise ValueError(
            "normalized signal missing for a scored factor value — "
            f"as_of={observation.as_of} security_id={observation.security_id!r} "
            f"factor_id={factor_id!r} normalization={spec.signal.normalization.value!r} "
            f"population_size={len(normalized_signals)}"
        ) from error


def _apply_cross_sectional_eligibility(
    spec: StrategySpec,
    observations: tuple[PortfolioObservation, ...],
    scored: list[_ScoredCandidate],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> list[CandidateDecision]:
    """2-pass: 프레임 모집단의 순위로 `top_*` 규칙을 적용해 탈락 사유를 덧붙인다 (spec D3 S5).

    **모집단**은 규칙마다 따로 센다 — 유니버스 멤버 중 절대 규칙(`gt`~`eq`)을 전부 통과했고 그
    규칙의 `field_id` 값이 기준일까지 공개된 종목이다. 결측·공개일 초과 종목은 탈락시키면서
    **분모에서도 뺀다**: 값을 모르는 종목을 분모에 세면 "거래대금 상위 20%"가 데이터 커버리지에
    따라 실제 20%보다 적은 종목을 남긴다.

    **동점**은 값 내림차순 → `security_id` 오름차순으로 자른다. 두 키가 전순서를 이뤄 같은 입력이
    언제나 같은 컷을 낸다(비결정 선정 방지).

    절대 규칙과 횡단면 규칙은 AND 다. 규칙이 여러 개면 각자의 모집단에서 잘리고, 한 번이라도
    잘린 종목은 최종적으로 탈락한다.
    """
    rules = tuple(
        rule
        for rule in spec.eligibility.rules
        if rule.operator in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS
    )
    decisions = [item.decision for item in _checkpointed(scored, checkpoint)]
    if not rules:
        return decisions
    eligible_for_population = {
        item.decision.security_id: item.passes_absolute_eligibility
        for item in _checkpointed(scored, checkpoint)
    }
    # 1-pass 의 `_score_candidate` 와 **같은 방식**으로 필드를 찾는다(관측당 dict 한 벌, 중복
    # `field_id` 면 마지막 항목이 이긴다). 한쪽이 선형 탐색이면 중복이 들어왔을 때 절대 규칙과
    # 횡단면 모집단이 서로 다른 값을 읽는다 — 포트 계약이 중복을 거절하므로 실 파이프라인에서는
    # 안 나지만, 이 함수는 공개 도메인 facade 를 통해 임의 관측으로도 불린다(리뷰 DEFECT-P3-2).
    fields_by_security = {
        observation.security_id: {item.field_id: item for item in observation.fields}
        for observation in _checkpointed(observations, checkpoint)
    }
    added: dict[str, list[ExclusionReason]] = {}
    for rule in rules:
        population: list[tuple[str, float]] = []
        for observation in _checkpointed(observations, checkpoint):
            if not observation.universe_member:
                continue  # 1-pass 가 이미 NOT_IN_UNIVERSE 로 탈락시켰다
            if not eligible_for_population[observation.security_id]:
                # 절대 규칙에서 이미 떨어진 종목에는 횡단면 사유를 덧붙이지 않는다. 모집단 밖이라
                # 순위가 없고, 탈락 사유 목록에 도달하지도 않은 규칙 이야기가 섞이면 trace 화면이
                # 실제로 걸린 규칙을 가린다.
                continue
            field = fields_by_security[observation.security_id].get(rule.field_id)
            if field is None or not _number(field.value):
                added.setdefault(observation.security_id, []).append(
                    ExclusionReason.MISSING_ELIGIBILITY
                )
                continue
            if field.available_date > observation.as_of:
                added.setdefault(observation.security_id, []).append(ExclusionReason.FUTURE_DATA)
                continue
            population.append((observation.security_id, float(field.value)))
        population.sort(key=lambda item: (-item[1], item[0]))
        kept = _cross_sectional_cut(rule, len(population))
        for security_id, _value in _checkpointed(population[kept:], checkpoint):
            added.setdefault(security_id, []).append(ExclusionReason.ELIGIBILITY_RANK_CUT)
    if not added:
        return decisions
    return [
        _with_exclusions(decision, added.get(decision.security_id, ()))
        for decision in _checkpointed(decisions, checkpoint)
    ]


def _with_exclusions(
    decision: CandidateDecision, extra: Iterable[ExclusionReason]
) -> CandidateDecision:
    """탈락 사유를 덧붙이고 `eligible` 을 다시 판정한다. 순서는 1-pass 사유가 먼저다."""
    reasons = tuple(dict.fromkeys((*decision.exclusion_reasons, *extra)))
    if reasons == decision.exclusion_reasons:
        return decision
    return replace(
        decision,
        exclusion_reasons=reasons,
        eligible=not any(reason in _BLOCKING_EXCLUSIONS for reason in reasons),
    )


def _cross_sectional_signals(
    spec: StrategySpec,
    observations: tuple[PortfolioObservation, ...],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[tuple[str, str], float]:
    """`signal.normalization` 을 프레임 횡단면에 적용한 팩터 값 — 키는 (factor_id, security_id).

    `none` 이면 빈 맵을 돌려주고 `_score_candidate` 가 원시값을 그대로 쓴다(1.1 의미, spec D4).

    모집단은 `domain.factor` 의 횡단면 연산자와 같은 동료 집단 규칙을 따른다: 한 프레임은 기준일
    하나이므로 남는 구분자는 `universe_member` 이고, 유니버스 밖 행은 유니버스 안 종목의 순위를
    움직이지 못한다(D-001). 세 부류가 모집단에서 빠지며, 빠지는 사유는 `_score_candidate` 가
    같은 값을 점수에서 버리는 사유와 같다.

    1. 값이 `None` — 결측. `missing` 정책은 팩터 그래프 평가에서 이미 적용됐으므로
       (`application/portfolio_design/_service.py`), 여기까지 남은 `None` 은 정책으로도 채우지
       못한 결측이고 `MISSING_FACTOR` 로 탈락한다. 즉 정규화는 항상 결측 처리 **뒤**에 온다.
    2. 공개일이 기준일보다 늦은 값 — `FUTURE_DATA`. 모집단에 넣으면 아직 알 수 없는 값이 다른
       종목의 순위를 바꾸는 look-ahead 가 된다.
    3. 유한하지 않은 값 — `_score_candidate` 가 `NonFinitePortfolioCalculationError` 로 올린다.
       여기서는 건너뛰기만 해서 그 예외의 stage/context 가 그대로 유지되게 한다.
    """
    method = spec.signal.normalization
    if method is SignalNormalization.NONE:
        return {}
    # 분기는 exhaustive 다. 값을 하나 더 늘렸을 때 catch-all 이 그것을 조용히 다른 정규화로
    # 돌리면 진단도 예외도 없이 다른 종목이 선정된다(spec S5 가 `_compare` 에서 짚은 실패 모양).
    if method is SignalNormalization.RANK:
        normalize = cross_sectional_rank
    elif method is SignalNormalization.ZSCORE:
        normalize = cross_sectional_zscore
    else:
        raise ValueError(
            f"unknown signal normalization — method={method!r} "
            f"supported={[item.value for item in SignalNormalization]}"
        )
    # 문서에 없는 팩터 값이 관측에 섞여 와도 모집단에 넣지 않는다. 합성에 안 들어가는 값이다.
    scored_factor_ids = {factor.factor_id for factor in spec.factors}
    populations: dict[tuple[str, bool], list[tuple[str, float]]] = {}
    for observation in _checkpointed(observations, checkpoint):
        for value in observation.factor_values:
            if value.factor_id not in scored_factor_ids:
                continue
            if value.value is None or not _number(value.value):
                continue
            if value.available_date > observation.as_of:
                continue
            key = (value.factor_id, observation.universe_member)
            populations.setdefault(key, []).append((observation.security_id, float(value.value)))
    normalized: dict[tuple[str, str], float] = {}
    for (factor_id, _member), samples in _checkpointed(populations.items(), checkpoint):
        scores = normalize([value for _, value in samples])
        paired = zip(samples, scores, strict=True)
        for (security_id, _raw), score in _checkpointed(paired, checkpoint):
            normalized[(factor_id, security_id)] = score
    return normalized


def _score_candidate(
    spec: StrategySpec,
    observation: PortfolioObservation,
    *,
    normalized_signals: Mapping[tuple[str, str], float],
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
    # 1-pass: 절대 규칙만 본다. `top_*` 는 프레임 전체 모집단이 있어야 판정되므로
    # `_apply_cross_sectional_eligibility` 가 2-pass 로 붙인다(spec D3 S5).
    passes_absolute_eligibility = True
    for rule in spec.eligibility.rules:
        if rule.operator in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS:
            continue
        field = fields.get(rule.field_id)
        if field is None or not _number(field.value):
            reasons.append(ExclusionReason.MISSING_ELIGIBILITY)
            passes_absolute_eligibility = False
        elif field.available_date > observation.as_of:
            reasons.append(ExclusionReason.FUTURE_DATA)
            passes_absolute_eligibility = False
        elif not _compare(float(field.value), rule.operator, rule.value):
            reasons.append(ExclusionReason.ELIGIBILITY_FAILED)
            passes_absolute_eligibility = False
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
    for factor in spec.factors:
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
        signal_value = _signal_value(
            spec, observation, factor.factor_id, value.value, normalized_signals
        )
        weighted_value = _finite(
            direction * factor.weight * signal_value,
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
        passes_absolute_eligibility=passes_absolute_eligibility,
        decision=CandidateDecision(
            as_of=observation.as_of,
            security_id=observation.security_id,
            eligible=not any(reason in _BLOCKING_EXCLUSIONS for reason in reasons),
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
        spec, ranked, observations, long_ids, reasons, short=False, checkpoint=checkpoint
    )
    short_scores = _weight_scores(
        spec,
        list(reversed(ranked)),
        observations,
        short_ids,
        reasons,
        short=True,
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
    short: bool,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    strengths = (
        _margin_strengths(ranked, selected, short=short)
        if spec.portfolio.weighting is WeightingMethod.FACTOR_SCORE
        else {}
    )
    for order, candidate in _checkpointed(enumerate(ranked, start=1), checkpoint):
        if candidate.security_id not in selected:
            continue
        if spec.portfolio.weighting is WeightingMethod.EQUAL:
            score = 1.0
        elif spec.portfolio.weighting is WeightingMethod.FACTOR_SCORE:
            score = strengths[candidate.security_id]
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


def _margin_strengths(
    ranked: list[CandidateDecision], selected: set[str], *, short: bool
) -> dict[str, float]:
    """점수 비례 가중(`weighting: factor_score`)의 선정 종목별 강도다. 비중은 이 값에 비례한다.

    규칙(PLAN 결정 5): 선호 점수 p 는 롱이면 합성 점수, 숏이면 그 부호를 뒤집은 값이다.
    선정이 2종목 이상이면 선정 종목 강도 = p − 기준점, 기준점 = `min(선정 최저 p 이하인 eligible
    비선정 종목 중 최고 p, 선정 최저 p − 평균 간격)`, 평균 간격 = `(선정 최고 p − 선정 최저 p) /
    (선정 수 − 1)` 이다. 컷 아래 종목이 없으면 뒤 항만 쓴다. 선정 1종목이거나 강도가
    모두 0 이면(선정 전원 동점이고 아래 종목 없음) 균등 배분한다.

    - 합성 점수는 방향을 이미 반영해서 **클수록 매수 선호**다(spec D4). 절댓값을 쓰면
      `direction: low` 와 공매도 쪽에서 순서가 뒤집혔다(2차 리뷰 R2-P204-001).
    - 기준점이 "선정 최저 − 평균 간격" 이하라서 선정 종목은 최소 평균 간격만큼의 강도를 갖는다.
      eligible 최저를 바닥으로 두면 그 종목이 0 이 되어 1종목·동점 프레임이 비었고(3차 리뷰
      R3-P204-001), 컷 아래 최고만 쓰면 근접 동점 종목이 dust 비중을 받았다(4차 리뷰
      R4-P204-001). 그래서 최고/최저 강도 비는 선정 수를 넘지 않는다.
    - 선정 최저와 동점인 비선정 종목도 기준점 후보에 넣는다(`<=`). 빼면 동점이 풀리고 묶일 때
      기준점이 튀어 자기 점수가 올라도 자기 비중이 주는 경우가 생겼다(5차 리뷰 R5-P204-001).
      평균 간격 하한이 있어 동점 후보가 들어와도 선정 종목의 강도는 0 이 되지 않는다(간격이
      0 이면 강도가 모두 0 이라 균등 배분).
    - 컷 아래 종목이 선정 최저에서 멀면 그 점수가 기준점이 되어 비중이 균등 쪽으로 평평해진다.
      하한은 기준점이 선정 최저에 너무 가까운 쪽만 막는다(PLAN 결정 5 의 알려진 성질).
    - 두 항 모두 점수의 평행 이동·양의 배율에 공변이라 비중은 불변이다. 그래서 x 에 `low` 를 준
      문서와 −x 에 `high` 를 준 문서가 `rank`(두 합성 점수가 상수 1 차이)에서도 같은 비중을 낸다.
    - 기준점은 같은 프레임의 eligible 후보에서만 구하므로 날짜를 가로지르지 않는다.
    """
    sign = -1.0 if short else 1.0
    preference = {
        candidate.security_id: sign * (candidate.composite_score or 0.0) for candidate in ranked
    }
    chosen = [value for security_id, value in preference.items() if security_id in selected]
    if not chosen:
        return {}
    if len(chosen) == 1:
        return {security_id: 1.0 for security_id in preference if security_id in selected}
    lowest = min(chosen)
    reference = lowest - (max(chosen) - lowest) / (len(chosen) - 1)
    below = [
        value
        for security_id, value in preference.items()
        if security_id not in selected and value <= lowest
    ]
    if below:
        reference = min(reference, max(below))
    strengths = {
        security_id: value - reference
        for security_id, value in preference.items()
        if security_id in selected
    }
    if not any(strength > 0.0 for strength in strengths.values()):
        return {security_id: 1.0 for security_id in strengths}
    return strengths


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


def _compare(value: float, operator: EligibilityOperator, threshold: float) -> bool:
    """후보 하나의 값으로 판정하는 절대 규칙 비교 (spec D3 S5).

    분기는 **exhaustive** 다. 예전 구현의 마지막 줄은 catch-all `return value == threshold` 라
    모집단이 필요한 `top_*` 가 들어와도 예외 없이 "값이 같은가"로 답했다 — 진단도 로그도 없이
    다른 종목이 선정되는 조용한 오필터다. 모르는 연산자는 여기서 멈춘다.
    """
    if operator is EligibilityOperator.GREATER_THAN:
        return value > threshold
    if operator is EligibilityOperator.GREATER_THAN_OR_EQUAL:
        return value >= threshold
    if operator is EligibilityOperator.LESS_THAN:
        return value < threshold
    if operator is EligibilityOperator.LESS_THAN_OR_EQUAL:
        return value <= threshold
    if operator is EligibilityOperator.EQUAL:
        return value == threshold
    raise ValueError(
        "absolute eligibility comparison received an operator it cannot decide alone — "
        f"operator={operator!r} value={value!r} threshold={threshold!r} "
        f"expected={[member.value for member in _ABSOLUTE_ELIGIBILITY_OPERATORS]} "
        f"cross_sectional={[member.value for member in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS]}"
    )


def _cross_sectional_cut(rule: EligibilityRule, population_size: int) -> int:
    """`top_*` 규칙이 남길 종목 수. 둘 다 소수점을 버리고 모집단 크기로 잘린다.

    비율은 **문서가 쓴 10진 표기 그대로** 곱한다(`Decimal(str(value))`). 이진 부동소수로
    곱하면 `100 × 0.29` 가 `28.999…` 라 `floor` 가 28 을 내고, "상위 29%" 문서가 진단도 예외도
    없이 한 종목을 더 떨군다. `repr(float)` 는 그 float 로 되돌아가는 최단 10진 표기라
    문서에 적힌 리터럴을 그대로 복원한다.
    """
    if not _number(rule.value):
        # validator(`strategy.eligibility.rule_value`·`strategy.number.non_finite`)가 먼저
        # 막지만, 검증을 건너뛴 경로가 생기면 `math.floor(Decimal("NaN"))` 의 맨몸 ValueError
        # 대신 어떤 규칙이었는지 말하고 멈춘다.
        raise ValueError(
            "cross-sectional eligibility cut received a non-finite size — "
            f"operator={rule.operator!r} field_id={rule.field_id!r} value={rule.value!r} "
            f"population_size={population_size}"
        )
    if rule.operator is EligibilityOperator.TOP_PERCENT:
        kept = math.floor(Decimal(population_size) * Decimal(str(rule.value)))
    elif rule.operator is EligibilityOperator.TOP_COUNT:
        kept = math.floor(Decimal(str(rule.value)))
    else:
        raise ValueError(
            "cross-sectional eligibility cut received an operator it cannot size — "
            f"operator={rule.operator!r} field_id={rule.field_id!r} value={rule.value!r} "
            f"expected={[member.value for member in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS]}"
        )
    return max(0, min(kept, population_size))


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
