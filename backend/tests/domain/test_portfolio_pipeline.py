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
    NonFinitePortfolioCalculationError,
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    compile_target_tape,
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
    sector_id: str = "sector-a",
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


def test_target_tape_hash_preserves_the_pre_cancellation_byte_contract() -> None:
    spec = _spec()
    day = date(2026, 1, 2)
    tape = _compile(spec, (_observation(day, "a", 1.0),))
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
    factor = replace(spec.factors.factors[0], weight=1e308)
    spec = replace(spec, factors=replace(spec.factors, factors=(factor,)))
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
