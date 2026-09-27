from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import date, timedelta
from enum import Enum

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.portfolio.facade.construction import (
    ExclusionReason,
    FactorContributionStatus,
    NonFinitePortfolioCalculationError,
    PortfolioConstraintEffect,
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    PortfolioTraceSelection,
    PortfolioWarningCode,
    compile_rebalance_schedule,
    compile_target_tape,
    compile_target_tape_with_trace,
)
from strategy_workbench.domain.strategy.facade.specification import (
    ComparisonOperator,
    EligibilityRule,
    EligibilityStep,
    PortfolioSide,
    RebalanceFrequency,
    SelectionMethod,
    WeightingMethod,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy


def _spec():
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
        today=lambda: date(2026, 9, 3),
    ).template()
    return replace(
        template,
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            short_selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )


def _observation(
    as_of: date,
    security_id: str,
    score: float | None,
    *,
    available_date: date | None = None,
    universe_member: bool = True,
    fields: tuple[PortfolioFieldValue, ...] = (),
    sector_id: str | None = "sector-a",
    previous_weight: float = 0.0,
) -> PortfolioObservation:
    return PortfolioObservation(
        as_of=as_of,
        security_id=security_id,
        universe_member=universe_member,
        factor_values=(
            PortfolioFactorValue(
                factor_id="price.close",
                value=score,
                available_date=available_date or as_of,
            ),
        ),
        fields=fields,
        sector_id=sector_id,
        previous_weight=previous_weight,
    )


def _compile(spec, observations, sessions=None):
    signal_day = observations[0].as_of if observations else date(2026, 1, 2)
    return compile_target_tape(
        spec,
        data_snapshot_id="snapshot-1",
        sessions=sessions or (signal_day, signal_day + timedelta(days=1)),
        observations=tuple(observations),
    )


def test_preflight_and_target_tape_share_the_compiler_owned_schedule(monkeypatch) -> None:
    from strategy_workbench.domain.portfolio import _compiler as compiler_module

    spec = _spec()
    sessions = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))
    schedule = compile_rebalance_schedule(spec, sessions)
    assert schedule.first_signal_as_of == sessions[0]

    def fail_if_recomputed(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(compiler_module, "_rebalance_pairs", fail_if_recomputed)
    tape = compile_target_tape(
        spec,
        data_snapshot_id="snapshot-1",
        sessions=sessions,
        observations=(_observation(sessions[0], "a", 1.0),),
        schedule=schedule,
    )

    assert tuple(frame.signal_as_of for frame in tape.frames) == sessions[:-1]


def test_eligibility_and_point_in_time_rules_explain_every_rejection() -> None:
    spec = _spec()
    spec = replace(
        spec,
        eligibility=EligibilityStep(
            (EligibilityRule("price.market_cap", ComparisonOperator.GREATER_THAN, 100.0),)
        ),
    )
    day = date(2026, 1, 2)
    valid_field = PortfolioFieldValue("price.market_cap", 200.0, day)
    observations = (
        _observation(day, "eligible", 3.0, fields=(valid_field,)),
        _observation(day, "not-member", 2.0, universe_member=False, fields=(valid_field,)),
        _observation(
            day,
            "future-factor",
            1.0,
            available_date=day + timedelta(days=1),
            fields=(valid_field,),
        ),
        _observation(day, "failed-rule", 0.0, fields=(replace(valid_field, value=50.0),)),
    )

    decisions = {
        item.security_id: item for item in _compile(spec, observations).frames[0].candidates
    }

    assert decisions["eligible"].selected
    assert decisions["not-member"].exclusion_reasons == (ExclusionReason.NOT_IN_UNIVERSE,)
    assert ExclusionReason.FUTURE_DATA in decisions["future-factor"].exclusion_reasons
    assert ExclusionReason.ELIGIBILITY_FAILED in decisions["failed-rule"].exclusion_reasons


def test_composite_rank_threshold_regime_and_long_short_selection() -> None:
    spec = _spec()
    spec = replace(
        spec,
        signal=replace(
            spec.signal,
            score_threshold=0.5,
            regime_field_id="market.regime",
            regime_minimum=0.0,
        ),
        portfolio=replace(spec.portfolio, side=PortfolioSide.LONG_SHORT),
        risk=replace(spec.risk, gross_exposure=1.0, net_exposure=0.0),
    )
    day = date(2026, 1, 2)
    regime_on = PortfolioFieldValue("market.regime", 1.0, day)
    observations = (
        _observation(day, "long", 3.0, fields=(regime_on,), sector_id="a"),
        _observation(day, "middle", 0.1, fields=(regime_on,), sector_id="b"),
        _observation(day, "short", -2.0, fields=(regime_on,), sector_id="c"),
        _observation(
            day,
            "regime-off",
            4.0,
            fields=(replace(regime_on, value=-1.0),),
            sector_id="d",
        ),
    )

    frame = _compile(spec, observations).frames[0]
    targets = {item.security_id: item.weight for item in frame.targets}
    decisions = {item.security_id: item for item in frame.candidates}

    assert targets == {"long": pytest.approx(0.5), "short": pytest.approx(-0.5)}
    assert decisions["long"].rank == 1
    assert ExclusionReason.SCORE_THRESHOLD in decisions["middle"].exclusion_reasons
    assert ExclusionReason.REGIME_BLOCKED in decisions["regime-off"].exclusion_reasons


@pytest.mark.parametrize("weighting", tuple(WeightingMethod))
def test_equal_factor_rank_and_risk_weighting(weighting: WeightingMethod) -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(spec.portfolio, selection_count=3, weighting=weighting),
        risk=replace(spec.risk, risk_field_id="risk.volatility"),
    )
    day = date(2026, 1, 2)
    observations = tuple(
        _observation(
            day,
            security_id,
            score,
            fields=(PortfolioFieldValue("risk.volatility", risk, day),),
            sector_id=security_id,
        )
        for security_id, score, risk in (("a", 4.0, 1.0), ("b", 2.0, 2.0), ("c", 1.0, 4.0))
    )

    weights = [item.weight for item in _compile(spec, observations).frames[0].targets]

    assert sum(weights) == pytest.approx(1.0)
    if weighting is WeightingMethod.EQUAL:
        assert len(set(round(item, 8) for item in weights)) == 1
    else:
        assert weights[0] > weights[1] > weights[2]


def test_percentile_liquidity_buffer_and_minimum_trade_rules() -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            selection_method=SelectionMethod.PERCENTILE,
            selection_percentile=0.25,
            turnover_buffer_count=1,
            minimum_trade_weight=0.02,
            liquidity_field_id="liquidity.adv",
            minimum_liquidity=100.0,
        ),
        risk=replace(spec.risk, max_name_weight=0.6, max_sector_weight=0.6),
    )
    day = date(2026, 1, 2)
    liquid = PortfolioFieldValue("liquidity.adv", 200.0, day)
    observations = (
        _observation(day, "a", 4.0, fields=(liquid,), sector_id="one"),
        _observation(
            day,
            "b",
            3.0,
            fields=(liquid,),
            sector_id="two",
            previous_weight=0.49,
        ),
        _observation(day, "c", 2.0, fields=(liquid,), sector_id="three"),
        _observation(
            day,
            "illiquid",
            5.0,
            fields=(replace(liquid, value=10.0),),
            sector_id="four",
        ),
    )

    decisions = {
        item.security_id: item for item in _compile(spec, observations).frames[0].candidates
    }

    assert ExclusionReason.LIQUIDITY_FAILED in decisions["illiquid"].exclusion_reasons
    assert decisions["b"].selected
    assert ExclusionReason.TURNOVER_BUFFER in decisions["b"].exclusion_reasons
    assert ExclusionReason.MINIMUM_TRADE in decisions["b"].exclusion_reasons
    assert decisions["b"].target_weight == pytest.approx(0.49)


def test_gross_net_name_sector_caps_and_sector_neutralization() -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=2,
            short_selection_count=2,
        ),
        risk=replace(
            spec.risk,
            gross_exposure=1.0,
            net_exposure=0.0,
            max_name_weight=0.3,
            max_sector_weight=0.6,
            sector_neutral=True,
        ),
    )
    day = date(2026, 1, 2)
    observations = (
        _observation(day, "long-x", 4.0, sector_id="x"),
        _observation(day, "long-y", 3.0, sector_id="y"),
        _observation(day, "short-x", -3.0, sector_id="x"),
        _observation(day, "short-y", -4.0, sector_id="y"),
    )

    weights = {
        item.security_id: item.weight for item in _compile(spec, observations).frames[0].targets
    }

    assert sum(abs(item) for item in weights.values()) == pytest.approx(1.0)
    assert sum(weights.values()) == pytest.approx(0.0)
    assert max(abs(item) for item in weights.values()) <= 0.3
    assert weights["long-x"] + weights["short-x"] == pytest.approx(0.0)
    assert weights["long-y"] + weights["short-y"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "frequency",
    (
        RebalanceFrequency.EVERY_N_SESSIONS,
        RebalanceFrequency.WEEKLY,
        RebalanceFrequency.MONTHLY,
        RebalanceFrequency.QUARTERLY,
    ),
)
def test_rebalance_calendars_always_execute_on_a_later_session(
    frequency: RebalanceFrequency,
) -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            rebalance=frequency,
            rebalance_every_n_sessions=5,
        ),
    )
    sessions = tuple(
        date(2026, 1, 2) + timedelta(days=offset)
        for offset in range(100)
        if (date(2026, 1, 2) + timedelta(days=offset)).weekday() < 5
    )

    tape = _compile(spec, (), sessions=sessions)

    assert tape.frames
    for frame in tape.frames:
        assert sessions.index(frame.execution_on) == sessions.index(frame.signal_as_of) + 1


def test_target_tape_hash_is_immutable_and_snapshot_sensitive() -> None:
    spec = _spec()
    day = date(2026, 1, 2)
    observations = (_observation(day, "a", 1.0),)

    first = _compile(spec, observations)
    second = _compile(spec, observations)
    different_snapshot = compile_target_tape(
        spec,
        data_snapshot_id="snapshot-2",
        sessions=(day, day + timedelta(days=1)),
        observations=observations,
    )

    assert first == second
    assert first.tape_hash == second.tape_hash
    assert first.tape_hash != different_snapshot.tape_hash


def test_construction_trace_is_out_of_band_and_contributions_sum_to_the_same_score() -> None:
    spec = _spec()
    original = spec.factors[0]
    second = replace(original, factor_id="second", weight=3.0)
    first = replace(original, weight=1.0)
    spec = replace(
        spec,
        factors=(first, second),
        portfolio=replace(
            spec.portfolio, selection_count=2, weighting=WeightingMethod.FACTOR_SCORE
        ),
        risk=replace(spec.risk, max_name_weight=0.6),
    )
    day = date(2026, 1, 2)
    observations = (
        replace(
            _observation(day, "a", 4.0),
            factor_values=(
                PortfolioFactorValue("price.close", 4.0, day),
                PortfolioFactorValue("second", 2.0, day),
            ),
        ),
        replace(
            _observation(day, "b", 1.0),
            factor_values=(
                PortfolioFactorValue("price.close", 1.0, day),
                PortfolioFactorValue("second", 1.0, day),
            ),
        ),
    )
    kwargs = {
        "data_snapshot_id": "snapshot-1",
        "sessions": (day, day + timedelta(days=1)),
        "observations": observations,
    }

    plain = compile_target_tape(spec, **kwargs)
    traced = compile_target_tape_with_trace(
        spec,
        **kwargs,
        trace_selection=PortfolioTraceSelection(day, ("a", "b")),
    )

    assert traced.tape == plain
    assert traced.tape.tape_hash == plain.tape_hash
    assert traced.trace is not None
    by_id = {item.security_id: item for item in traced.trace.candidates}
    for candidate in by_id.values():
        contributions = candidate.factor_contributions
        assert all(item.status is FactorContributionStatus.OK for item in contributions)
        assert sum(item.normalized_contribution or 0.0 for item in contributions) == pytest.approx(
            candidate.composite_score
        )
    assert by_id["a"].unconstrained_target_weight == pytest.approx(5 / 7)
    assert by_id["a"].constrained_target_weight == pytest.approx(0.6)
    assert by_id["a"].constraint_effect is PortfolioConstraintEffect.ADJUSTED
    assert by_id["a"].previous_weight is None
    assert by_id["a"].estimated_order_delta is None


def test_omitted_construction_trace_date_uses_the_schedule_latest_signal() -> None:
    spec = _spec()
    sessions = (
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    )
    observations = tuple(_observation(day, "a", float(index)) for index, day in enumerate(sessions))

    result = compile_target_tape_with_trace(
        spec,
        data_snapshot_id="snapshot-1",
        sessions=sessions,
        observations=observations,
        trace_selection=PortfolioTraceSelection(None, ("a",)),
    )

    assert result.trace is not None
    assert result.trace.signal_as_of == sessions[-2]
    assert result.trace.execution_on == sessions[-1]
    assert result.trace.signal_as_of == result.tape.frames[-1].signal_as_of


def test_construction_trace_explains_missing_future_removed_and_explicit_order_delta() -> None:
    spec = replace(
        _spec(),
        portfolio=replace(_spec().portfolio, side=PortfolioSide.LONG_SHORT),
        risk=replace(
            _spec().risk,
            gross_exposure=1.0,
            net_exposure=0.0,
            sector_neutral=True,
        ),
    )
    day = date(2026, 1, 2)
    observations = (
        _observation(day, "long", 2.0, sector_id="long-only", previous_weight=0.2),
        _observation(day, "short", -2.0, sector_id="short-only", previous_weight=-0.1),
        _observation(day, "missing", None, sector_id="missing"),
        _observation(
            day,
            "future",
            1.0,
            available_date=day + timedelta(days=1),
            sector_id="future",
        ),
    )

    result = compile_target_tape_with_trace(
        spec,
        data_snapshot_id="snapshot-1",
        sessions=(day, day + timedelta(days=1)),
        observations=observations,
        trace_selection=PortfolioTraceSelection(
            day,
            tuple(item.security_id for item in observations),
            include_order_delta=True,
        ),
    )

    assert result.trace is not None
    by_id = {item.security_id: item for item in result.trace.candidates}
    assert by_id["missing"].factor_contributions[0].status is FactorContributionStatus.MISSING
    assert by_id["future"].factor_contributions[0].status is FactorContributionStatus.FUTURE_DATA
    assert by_id["long"].constraint_effect is PortfolioConstraintEffect.REMOVED
    assert by_id["short"].constraint_effect is PortfolioConstraintEffect.REMOVED
    assert by_id["long"].previous_weight == pytest.approx(0.2)
    assert by_id["long"].estimated_order_delta == pytest.approx(-0.2)


# 섹터 없는 경우는 tape 에 경고가 붙어도 해시가 frames 만의 바이트 계약을 지키는지 본다(이슈 #203).
@pytest.mark.parametrize("sector_id", ["sector-a", None])
def test_target_tape_hash_preserves_the_pre_cancellation_byte_contract(
    sector_id: str | None,
) -> None:
    spec = _spec()
    if sector_id is None:
        # 섹터 상한이 걸릴 수 있어야 경고가 붙는다(비중 1.0 > 상한 0.5).
        spec = replace(spec, risk=replace(spec.risk, max_sector_weight=0.5))
    day = date(2026, 1, 2)
    tape = _compile(spec, (_observation(day, "a", 1.0, sector_id=sector_id),))
    assert bool(tape.warnings) is (sector_id is None)
    payload = {
        "data_snapshot_id": tape.data_snapshot_id,
        "strategy_hash": tape.strategy_hash,
        "execution_timing": tape.execution_timing,
        "frames": [asdict(frame) for frame in tape.frames],
    }

    def encode(value: object) -> str:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Enum):
            return str(value.value)
        raise TypeError(type(value).__name__)

    legacy_hash = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=encode,
        ).encode()
    ).hexdigest()

    assert tape.tape_hash == legacy_hash


def test_portfolio_arithmetic_overflow_never_materializes_a_target_tape() -> None:
    spec = _spec()
    factor = replace(spec.factors[0], weight=1e308)
    spec = replace(spec, factors=(factor,))
    day = date(2026, 1, 2)

    with pytest.raises(NonFinitePortfolioCalculationError, match="composite_score"):
        _compile(spec, (_observation(day, "overflow", 2.0),))


@pytest.mark.parametrize("stage", ["materialization", "hash"])
def test_target_tape_cancellation_reaches_canonical_materialization_and_hash(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from strategy_workbench.domain.portfolio import _compiler as compiler_module

    entered = False
    if stage == "materialization":
        original = compiler_module._canonical_payload

        def observed_materialization(*args, **kwargs):
            nonlocal entered
            entered = True
            return original(*args, **kwargs)

        monkeypatch.setattr(compiler_module, "_canonical_payload", observed_materialization)
    else:
        original = compiler_module._hash_payload

        def observed_hash(*args, **kwargs):
            nonlocal entered
            entered = True
            return original(*args, **kwargs)

        monkeypatch.setattr(compiler_module, "_hash_payload", observed_hash)

    def checkpoint() -> None:
        if entered:
            raise RuntimeError(f"cancelled during {stage}")

    spec = _spec()
    day = date(2026, 1, 2)
    with pytest.raises(RuntimeError, match=f"cancelled during {stage}"):
        compile_target_tape(
            spec,
            data_snapshot_id="snapshot-1",
            sessions=(day, day + timedelta(days=1)),
            observations=(_observation(day, "a", 1.0),),
            checkpoint=checkpoint,
        )
    assert entered


def test_every_materialized_target_tape_number_is_finite() -> None:
    spec = _spec()
    day = date(2026, 1, 2)
    payload = asdict(_compile(spec, (_observation(day, "a", 1.0),)))

    def numbers(value: object):
        if isinstance(value, float):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from numbers(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from numbers(item)

    assert all(math.isfinite(value) for value in numbers(payload))


def test_hard_risk_limits_override_a_minimum_trade_hold() -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(spec.portfolio, minimum_trade_weight=1.0),
        risk=replace(spec.risk, max_name_weight=0.2),
    )
    day = date(2026, 1, 2)

    target = (
        _compile(
            spec,
            (_observation(day, "oversized", 1.0, previous_weight=0.9),),
        )
        .frames[0]
        .targets[0]
    )

    assert target.weight == pytest.approx(0.2)


def test_long_only_and_sector_neutral_exposure_semantics_are_validated() -> None:
    spec = _spec()
    invalid = replace(
        spec,
        risk=replace(spec.risk, net_exposure=0.5, sector_neutral=True),
    )

    codes = {item.code for item in validate_strategy(invalid).issues}

    assert "strategy.risk.long_only_exposure" in codes
    assert "strategy.risk.sector_neutral_side" in codes


_FOLD_DAYS = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))


def _fold_spec():
    spec = _spec()
    return replace(
        spec,
        portfolio=replace(spec.portfolio, selection_count=1, turnover_buffer_count=1),
    )


def test_previous_weight_folds_forward_from_the_previous_frame() -> None:
    """D-002: the book a frame carries in is the previous frame's targets, not the port value."""
    observations = (
        _observation(_FOLD_DAYS[0], "a", 2.0),
        _observation(_FOLD_DAYS[0], "b", 1.0),
        _observation(_FOLD_DAYS[1], "a", 1.0),
        _observation(_FOLD_DAYS[1], "b", 2.0),
        _observation(_FOLD_DAYS[2], "a", 1.0),
        _observation(_FOLD_DAYS[2], "b", 2.0),
    )

    frames = _compile(_fold_spec(), observations, sessions=_FOLD_DAYS).frames

    assert len(frames) == 2
    assert {target.security_id for target in frames[0].targets} == {"a"}
    # "a" fell to rank 2 but is still held, so the turnover buffer must retain it.
    held = {item.security_id: item for item in frames[1].candidates}
    assert ExclusionReason.TURNOVER_BUFFER in held["a"].exclusion_reasons
    assert {target.security_id for target in frames[1].targets} == {"a", "b"}


def test_only_the_first_frame_reads_the_adapter_seed() -> None:
    """A `previous_weight` on a later frame's observations is stale and must be ignored."""
    observations = (
        _observation(_FOLD_DAYS[0], "a", 3.0),
        _observation(_FOLD_DAYS[0], "b", 2.0, previous_weight=0.5),
        _observation(_FOLD_DAYS[0], "c", 1.0),
        _observation(_FOLD_DAYS[1], "a", 3.0),
        _observation(_FOLD_DAYS[1], "b", 2.0),
        _observation(_FOLD_DAYS[1], "c", 1.0, previous_weight=0.5),
    )

    frames = _compile(_fold_spec(), observations, sessions=_FOLD_DAYS).frames

    # Frame 0 honours the seed: "b" is rank 2 but held, so the buffer keeps it.
    assert {target.security_id for target in frames[0].targets} == {"a", "b"}
    # Frame 1 reads the folded book ("a", "b"), never "c"'s stale seed.
    assert {target.security_id for target in frames[1].targets} == {"a", "b"}


# 이슈 #203: 섹터를 모르는 종목(`sector_id is None`)은 섹터 제약 계산에서 빠진다. 전에는 전부
# `"__unknown__"` 한 섹터로 묶여 기본 상한 0.3에 걸렸고, 섹터 원천이 없는 실데이터(duckdb)
# 백테스트가 경고 없이 현금 70%로 돌았다.
def _unknown_sector_spec(*, selection_count: int, max_name_weight: float):
    spec = _spec()
    return replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            selection_count=selection_count,
            weighting=WeightingMethod.EQUAL,
            selection_method=SelectionMethod.TOP_N,
        ),
        risk=replace(spec.risk, max_name_weight=max_name_weight, max_sector_weight=0.3),
    )


def test_unknown_sector_names_are_left_out_of_the_sector_cap() -> None:
    spec = _unknown_sector_spec(selection_count=10, max_name_weight=0.15)
    day = date(2026, 1, 2)
    observations = tuple(
        _observation(day, f"s{index:02d}", float(10 - index), sector_id=None) for index in range(10)
    )

    targets = _compile(spec, observations).frames[0].targets

    # 섹터 상한 0.3이 "모르는 섹터" 묶음에 걸리지 않으므로 예산 1.0을 다 쓴다.
    assert len(targets) == 10
    assert sum(item.weight for item in targets) == pytest.approx(1.0)
    assert all(item.weight == pytest.approx(0.1) for item in targets)


def test_unknown_sector_names_still_respect_the_name_cap() -> None:
    spec = _unknown_sector_spec(selection_count=4, max_name_weight=0.2)
    day = date(2026, 1, 2)
    observations = tuple(
        _observation(day, f"s{index}", float(4 - index), sector_id=None) for index in range(4)
    )

    targets = _compile(spec, observations).frames[0].targets

    # 종목 상한은 섹터 정보와 무관하게 그대로다: 4 x 0.2 = 0.8.
    assert all(item.weight == pytest.approx(0.2) for item in targets)
    assert sum(item.weight for item in targets) == pytest.approx(0.8)


def test_known_sectors_keep_their_cap_when_unknown_sector_names_are_mixed_in() -> None:
    spec = _unknown_sector_spec(selection_count=10, max_name_weight=0.15)
    day = date(2026, 1, 2)
    observations = (
        *(_observation(day, f"x{index}", float(20 - index), sector_id="x") for index in range(4)),
        *(_observation(day, f"u{index}", float(10 - index), sector_id=None) for index in range(6)),
    )

    weights = {
        item.security_id: item.weight for item in _compile(spec, observations).frames[0].targets
    }

    # 섹터 x는 0.4가 상한 0.3으로 줄고(0.075씩), 섹터 없는 종목은 상한 밖이라 0.1 그대로다.
    assert sum(weights[f"x{index}"] for index in range(4)) == pytest.approx(0.3)
    assert all(weights[f"u{index}"] == pytest.approx(0.1) for index in range(6))
    assert sum(weights.values()) == pytest.approx(0.9)


def test_unknown_sector_exclusion_is_reported_with_the_count_and_the_frames() -> None:
    spec = _unknown_sector_spec(selection_count=3, max_name_weight=0.5)
    days = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
    observations = (
        _observation(days[0], "a", 3.0, sector_id=None),
        _observation(days[0], "b", 2.0, sector_id=None),
        _observation(days[0], "c", 1.0, sector_id="known"),
        _observation(days[1], "a", 3.0, sector_id="known"),
        _observation(days[1], "b", 2.0, sector_id="known"),
        _observation(days[1], "c", 1.0, sector_id=None),
        # 세 번째 프레임은 섹터를 모두 안다. 분모에는 들고 영향 프레임에는 들지 않는다.
        _observation(days[2], "a", 3.0, sector_id="known"),
        _observation(days[2], "b", 2.0, sector_id="known"),
        _observation(days[2], "c", 1.0, sector_id="known"),
    )

    tape = _compile(spec, observations, sessions=days)

    assert len(tape.frames) == 3
    assert len(tape.warnings) == 1
    warning = tape.warnings[0]
    assert warning.code is PortfolioWarningCode.SECTOR_UNKNOWN_EXCLUDED
    # 진단 문장에는 영향 프레임 수/전체 프레임 수, 프레임당 제외 종목 수 범위, 프레임별
    # signal_as_of 와 제외 종목 수, 상한 값이 들어간다.
    assert "영향 프레임 2/3개" in warning.message
    assert "프레임당 제외 종목 1~2개" in warning.message
    assert "2026-01-02(2종목)" in warning.message
    assert "2026-01-05(1종목)" in warning.message
    assert "2026-01-06" not in warning.message
    assert "max_sector_weight=0.3" in warning.message


def test_unselected_unknown_sector_names_raise_no_warning_and_known_sectors_none() -> None:
    spec = _unknown_sector_spec(selection_count=2, max_name_weight=0.5)
    day = date(2026, 1, 2)
    observations = (
        _observation(day, "a", 3.0, sector_id="x"),
        _observation(day, "b", 2.0, sector_id="y"),
        # 선택되지 않은 종목은 섹터 제약을 받을 비중이 없으므로 제외한 것이 아니다.
        _observation(day, "c", 1.0, sector_id=None),
    )

    tape = _compile(spec, observations)

    assert tape.warnings == ()


def test_sector_neutral_skips_unknown_sector_names_and_keeps_known_pairs_neutral() -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=2,
            short_selection_count=2,
        ),
        risk=replace(
            spec.risk,
            gross_exposure=1.0,
            net_exposure=0.0,
            max_name_weight=0.3,
            max_sector_weight=1.0,
            sector_neutral=True,
        ),
    )
    day = date(2026, 1, 2)
    observations = (
        _observation(day, "long-x", 4.0, sector_id="x"),
        _observation(day, "long-u", 3.0, sector_id=None),
        _observation(day, "short-x", -3.0, sector_id="x"),
        _observation(day, "short-u", -4.0, sector_id=None),
    )

    tape = _compile(spec, observations)
    weights = {item.security_id: item.weight for item in tape.frames[0].targets}

    # 섹터 x는 중립화되고, 섹터를 모르는 종목은 "모르는 섹터" 한 묶음으로 짝지어지지 않는다.
    assert weights["long-x"] + weights["short-x"] == pytest.approx(0.0)
    assert weights["long-u"] > 0 > weights["short-u"]
    assert [item.code for item in tape.warnings] == [PortfolioWarningCode.SECTOR_UNKNOWN_EXCLUDED]
    assert "sector_neutral=True" in tape.warnings[0].message
    assert "1/1" in tape.warnings[0].message
    assert "2026-01-02(2종목)" in tape.warnings[0].message


def _long_short_unknown_sector_spec(
    *,
    gross_exposure: float,
    net_exposure: float,
    max_sector_weight: float,
    sector_neutral: bool,
):
    spec = _spec()
    return replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=2,
            short_selection_count=2,
        ),
        risk=replace(
            spec.risk,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            max_name_weight=0.5,
            max_sector_weight=max_sector_weight,
            sector_neutral=sector_neutral,
        ),
    )


def _long_short_unknown_sector_observations(day: date):
    return (
        _observation(day, "long-1", 4.0, sector_id=None),
        _observation(day, "long-2", 3.0, sector_id=None),
        _observation(day, "short-1", -3.0, sector_id=None),
        _observation(day, "short-2", -4.0, sector_id=None),
    )


def test_sector_neutral_does_not_pair_unknown_sector_longs_and_shorts() -> None:
    # 롱 예산 0.75, 숏 예산 0.25로 비대칭이다. 섹터를 모르는 종목을 "모르는 섹터" 한 묶음으로
    # 중립화하면 롱이 숏에 맞춰 0.25로 줄어든다. 묶지 않으면 예산이 그대로 남는다.
    spec = _long_short_unknown_sector_spec(
        gross_exposure=1.0, net_exposure=0.5, max_sector_weight=1.0, sector_neutral=True
    )
    day = date(2026, 1, 2)

    tape = _compile(spec, _long_short_unknown_sector_observations(day))
    weights = {item.security_id: item.weight for item in tape.frames[0].targets}

    assert weights["long-1"] == pytest.approx(0.375)
    assert weights["long-2"] == pytest.approx(0.375)
    assert weights["short-1"] == pytest.approx(-0.125)
    assert weights["short-2"] == pytest.approx(-0.125)
    assert [item.code for item in tape.warnings] == [PortfolioWarningCode.SECTOR_UNKNOWN_EXCLUDED]


def test_zero_weight_unknown_sector_names_are_not_counted_as_excluded() -> None:
    # net == gross 라 숏 예산이 0이다. 선택된 숏 종목은 비중 0으로 섹터 제약 단계에 들어오지만
    # 제약할 비중이 없으므로 제외 종목 수에 넣지 않는다.
    spec = _long_short_unknown_sector_spec(
        gross_exposure=1.0, net_exposure=1.0, max_sector_weight=0.3, sector_neutral=False
    )
    day = date(2026, 1, 2)

    tape = _compile(spec, _long_short_unknown_sector_observations(day))
    weights = {item.security_id: item.weight for item in tape.frames[0].targets}

    assert sum(weight for weight in weights.values() if weight > 0) == pytest.approx(1.0)
    assert [item.code for item in tape.warnings] == [PortfolioWarningCode.SECTOR_UNKNOWN_EXCLUDED]
    assert "프레임당 제외 종목 2~2개" in tape.warnings[0].message
    assert "2026-01-02(2종목)" in tape.warnings[0].message


def test_empty_sector_id_is_treated_as_unknown() -> None:
    spec = _unknown_sector_spec(selection_count=10, max_name_weight=0.15)
    day = date(2026, 1, 2)
    observations = tuple(
        _observation(day, f"s{index:02d}", float(10 - index), sector_id="") for index in range(10)
    )

    tape = _compile(spec, observations)

    # 빈 문자열을 한 섹터로 보면 #203처럼 비중 합이 0.3으로 줄어든다.
    assert sum(item.weight for item in tape.frames[0].targets) == pytest.approx(1.0)
    assert [item.code for item in tape.warnings] == [PortfolioWarningCode.SECTOR_UNKNOWN_EXCLUDED]


# 섹터 제약이 어떤 섹터 구성에서도 걸릴 수 없는 프레임에서는 경고하지 않는다. 한 섹터의 노출은
# 프레임 전체 노출을 넘지 못하므로, 섹터 중립이 꺼져 있고 전체 노출이 상한 이하면 제외해도 결과가
# 같다.
@pytest.mark.parametrize(
    ("side", "gross_exposure", "max_sector_weight", "sector_neutral", "warned"),
    [
        # 20 x 0.05 의 부동소수 합(1.0000000000000002)이 상한 1.0을 넘는다고 보지 않는다.
        (PortfolioSide.LONG_ONLY, 1.0, 1.0, False, False),
        (PortfolioSide.LONG_ONLY, 1.0, 0.99, False, True),
        (PortfolioSide.LONG_SHORT, 1.0, 1.0, False, False),
        (PortfolioSide.LONG_SHORT, 1.6, 1.0, False, True),
        (PortfolioSide.LONG_SHORT, 1.0, 1.0, True, True),
    ],
)
def test_unknown_sector_warning_appears_only_when_a_sector_constraint_could_bind(
    side: PortfolioSide,
    gross_exposure: float,
    max_sector_weight: float,
    sector_neutral: bool,
    warned: bool,
) -> None:
    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=side,
            selection_count=20,
            short_selection_count=20,
            weighting=WeightingMethod.EQUAL,
            selection_method=SelectionMethod.TOP_N,
        ),
        risk=replace(
            spec.risk,
            gross_exposure=gross_exposure,
            net_exposure=0.0 if side is PortfolioSide.LONG_SHORT else 1.0,
            max_name_weight=0.05,
            max_sector_weight=max_sector_weight,
            sector_neutral=sector_neutral,
        ),
    )
    day = date(2026, 1, 2)
    observations = (
        *(
            _observation(day, f"l{index:02d}", float(40 - index), sector_id=None)
            for index in range(20)
        ),
        *(
            _observation(day, f"s{index:02d}", float(-40 + index), sector_id=None)
            for index in range(20)
        ),
    )

    tape = _compile(spec, observations)

    assert tape.frames[0].targets
    assert bool(tape.warnings) is warned
