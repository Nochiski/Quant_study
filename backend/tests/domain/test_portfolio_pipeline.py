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
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import (
    ExclusionReason,
    FactorContributionStatus,
    NonFinitePortfolioCalculationError,
    PortfolioConstraintEffect,
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    PortfolioTraceSelection,
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
    SignalNormalization,
    WeightingMethod,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy


def _environment() -> RunEnvironment:
    """체결 시점은 1.2 부터 실행 설정의 사실이라 tape 컴파일이 인자로 받는다(P2-03)."""
    return RunEnvironment(
        start=date(2026, 1, 1), end=date(2026, 12, 31), universe_id="krx.common-stock"
    )


def _spec():
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
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
        environment=_environment(),
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
        environment=_environment(),
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
    # `score_threshold` 는 원시값 합성 점수를 기준으로 쓰던 1.1 규칙이라 `none` 으로 고정한다.
    # 기본값 `rank` 에서의 문턱 의미는 아래 정규화 전용 테스트가 따로 덮는다(P2-04).
    spec = _spec()
    spec = replace(
        spec,
        signal=replace(
            spec.signal,
            normalization=SignalNormalization.NONE,
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
        environment=_environment(),
        data_snapshot_id="snapshot-2",
        sessions=(day, day + timedelta(days=1)),
        observations=observations,
    )

    assert first == second
    assert first.tape_hash == second.tape_hash
    assert first.tape_hash != different_snapshot.tape_hash


def test_construction_trace_is_out_of_band_and_contributions_sum_to_the_same_score() -> None:
    # 기여도 합 = 합성 점수라는 불변식은 정규화와 무관하지만, 기대 비중 5/7 은 원시값 산술이라
    # `none` 으로 고정한다(1.1 수치 회귀, P2-04).
    spec = _spec()
    original = spec.factors[0]
    second = replace(original, factor_id="second", weight=3.0)
    first = replace(original, weight=1.0)
    spec = replace(
        spec,
        signal=replace(spec.signal, normalization=SignalNormalization.NONE),
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

    plain = compile_target_tape(spec, environment=_environment(), **kwargs)
    traced = compile_target_tape_with_trace(
        spec,
        environment=_environment(),
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
        environment=_environment(),
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
        environment=_environment(),
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
    # 정규화는 값을 유한한 구간으로 눌러서 오버플로를 못 만든다. 오버플로 가드가 살아 있는지는
    # 원시값을 그대로 곱하는 `none` 에서만 확인할 수 있다(P2-04).
    spec = _spec()
    factor = replace(spec.factors[0], weight=1e308)
    spec = replace(
        spec,
        signal=replace(spec.signal, normalization=SignalNormalization.NONE),
        factors=(factor,),
    )
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
            environment=_environment(),
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


# --- signal.normalization (P2-04, spec D3 S4·D4) ---------------------------------------------
#
# 합성식은 `composite = Σ(sign(direction) × weight × norm(x)) / Σ|weight|` 이고, `norm` 만
# `signal.normalization` 이 고른다. 아래 fixture 는 팩터 하나·weight 1.0·`direction: high` 라
# 분모가 1.0 이므로 합성 점수가 곧 `norm(x)` 다 — 정규화 값을 직접 읽을 수 있다.

_NORMALIZATION_DAY = date(2026, 1, 2)


def _normalization_spec(method: SignalNormalization, *, selection_count: int = 1):
    spec = _spec()
    return replace(
        spec,
        signal=replace(spec.signal, normalization=method),
        portfolio=replace(spec.portfolio, selection_count=selection_count),
    )


def _scores(method: SignalNormalization, observations) -> dict[str, float | None]:
    frame = _compile(_normalization_spec(method), observations).frames[0]
    return {item.security_id: item.composite_score for item in frame.candidates}


def _ladder() -> tuple[PortfolioObservation, ...]:
    return tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0))
    )


def test_normalization_none_keeps_the_raw_weighted_sum() -> None:
    """1.1 수치 회귀: `none` 은 원시값을 그대로 가중 합한다."""
    assert _scores(SignalNormalization.NONE, _ladder()) == {
        "a": pytest.approx(1.0),
        "b": pytest.approx(2.0),
        "c": pytest.approx(3.0),
        "d": pytest.approx(4.0),
    }


def test_normalization_rank_is_the_cross_sectional_percentile() -> None:
    """`rank` 는 같은 날 횡단면 백분위다 — 최저 0.0, 최고 1.0, 사이는 등간격."""
    assert _scores(SignalNormalization.RANK, _ladder()) == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(1 / 3),
        "c": pytest.approx(2 / 3),
        "d": pytest.approx(1.0),
    }


def test_normalization_rank_gives_tied_values_the_average_rank() -> None:
    """동점은 평균 순위를 나눠 가져서 입력 순서가 결과를 바꾸지 못한다(결정성)."""
    observations = tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 1.0), ("b", 2.0), ("c", 2.0), ("d", 4.0))
    )

    scores = _scores(SignalNormalization.RANK, observations)

    # 순위 1, 2.5, 2.5, 4 → (r - 1) / 3.
    assert scores == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(0.5),
        "c": pytest.approx(0.5),
        "d": pytest.approx(1.0),
    }
    assert _scores(SignalNormalization.RANK, tuple(reversed(observations))) == scores


def test_normalization_zscore_standardizes_the_cross_section() -> None:
    """`zscore` 는 모집단 표준편차 기준 표준화다."""
    # 평균 2.5, 모집단 표준편차 sqrt(1.25).
    deviation = math.sqrt(1.25)

    assert _scores(SignalNormalization.ZSCORE, _ladder()) == {
        "a": pytest.approx(-1.5 / deviation),
        "b": pytest.approx(-0.5 / deviation),
        "c": pytest.approx(0.5 / deviation),
        "d": pytest.approx(1.5 / deviation),
    }


def test_normalization_zscore_without_dispersion_is_neutral() -> None:
    """값이 전부 같으면 편차 정보가 없으므로 0 으로 나누지 않고 전부 0.0 이다."""
    observations = tuple(
        _observation(_NORMALIZATION_DAY, security_id, 7.0) for security_id in ("a", "b", "c")
    )

    assert _scores(SignalNormalization.ZSCORE, observations) == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(0.0),
        "c": pytest.approx(0.0),
    }


def test_normalization_population_excludes_future_dated_values() -> None:
    """공개일이 기준일보다 늦은 값은 모집단에 못 들어간다 — 들어가면 look-ahead 다."""
    visible = tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 1.0), ("b", 2.0), ("c", 3.0))
    )
    with_future = visible + (
        _observation(
            _NORMALIZATION_DAY,
            "future",
            100.0,
            available_date=_NORMALIZATION_DAY + timedelta(days=1),
        ),
    )

    scores = _scores(SignalNormalization.RANK, with_future)

    # 모집단이 3개 그대로라 순위가 0, 0.5, 1 이다. 아직 알 수 없는 값이 끼면 0, 1/3, 2/3 이 된다.
    assert {key: scores[key] for key in ("a", "b", "c")} == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(0.5),
        "c": pytest.approx(1.0),
    }
    assert scores["future"] is None


def test_normalization_population_excludes_non_universe_members() -> None:
    """유니버스 밖 행은 유니버스 안 종목의 순위를 움직이지 못한다(domain.factor 와 같은 규칙)."""
    members = tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 1.0), ("b", 2.0), ("c", 3.0))
    )
    with_outsider = members + (
        _observation(_NORMALIZATION_DAY, "outsider", 100.0, universe_member=False),
    )

    scores = _scores(SignalNormalization.RANK, with_outsider)

    assert {key: scores[key] for key in ("a", "b", "c")} == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(0.5),
        "c": pytest.approx(1.0),
    }
    # 유니버스 밖 행끼리는 따로 한 집단이라 혼자면 0.0 이고, 선정에는 못 들어간다.
    assert scores["outsider"] == pytest.approx(0.0)


def test_missing_factor_values_drop_out_before_normalization() -> None:
    """결측은 정규화 **전**에 빠진다 — `missing` 정책은 팩터 평가에서 이미 끝났다."""
    observations = tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 1.0), ("b", 2.0), ("c", 3.0), ("gap", None))
    )

    frame = _compile(_normalization_spec(SignalNormalization.RANK), observations).frames[0]
    decisions = {item.security_id: item for item in frame.candidates}

    assert ExclusionReason.MISSING_FACTOR in decisions["gap"].exclusion_reasons
    assert decisions["gap"].composite_score is None
    # 모집단이 3개라 분모가 2 다. 결측이 모집단에 남으면 0, 1/3, 2/3 이 된다.
    assert {key: decisions[key].composite_score for key in ("a", "b", "c")} == {
        "a": pytest.approx(0.0),
        "b": pytest.approx(0.5),
        "c": pytest.approx(1.0),
    }


def test_normalization_cross_section_is_per_rebalance_frame() -> None:
    """같은 종목도 그날 동료가 달라지면 순위가 달라진다 — 날짜를 가로지르지 않는다."""
    days = (_NORMALIZATION_DAY, _NORMALIZATION_DAY + timedelta(days=1))
    sessions = days + (_NORMALIZATION_DAY + timedelta(days=2),)
    observations = (
        _observation(days[0], "a", 5.0),
        _observation(days[0], "peer_low_1", 1.0),
        _observation(days[0], "peer_low_2", 2.0),
        _observation(days[1], "a", 5.0),
        _observation(days[1], "peer_high_1", 9.0),
        _observation(days[1], "peer_high_2", 10.0),
    )

    frames = _compile(
        _normalization_spec(SignalNormalization.RANK), observations, sessions=sessions
    ).frames

    scores = [
        {item.security_id: item.composite_score for item in frame.candidates}["a"]
        for frame in frames
    ]
    assert scores == [pytest.approx(1.0), pytest.approx(0.0)]


def test_rank_normalization_stops_a_large_unit_factor_from_dominating_selection() -> None:
    """아이디어 2: 단위가 다른 두 팩터를 결합할 때 `rank` 가 한쪽 지배를 막는다.

    `alpha` 는 0~1 구간, `scale` 은 만 단위다. 같은 weight 로 원시값을 더하면 `scale` 하나가
    선정을 결정하고, `rank` 는 둘을 같은 0~1 구간으로 맞춰 `alpha` 가 결과를 되돌린다.
    """
    values = {"a": (1.0, 10_000.0), "b": (0.0, 40_000.0)}
    observations = tuple(
        replace(
            _observation(_NORMALIZATION_DAY, security_id, alpha),
            factor_values=(
                PortfolioFactorValue("alpha", alpha, _NORMALIZATION_DAY),
                PortfolioFactorValue("scale", scale, _NORMALIZATION_DAY),
            ),
        )
        for security_id, (alpha, scale) in values.items()
    )
    template_factor = _spec().factors[0]
    factors = (
        replace(template_factor, factor_id="alpha", weight=1.0),
        replace(template_factor, factor_id="scale", weight=1.0),
    )

    def _selected(method: SignalNormalization) -> str:
        spec = replace(_normalization_spec(method), factors=factors)
        (target,) = _compile(spec, observations).frames[0].targets
        return target.security_id

    assert _selected(SignalNormalization.NONE) == "b"
    # `rank` 에서는 alpha 1.0 + scale 0.0 = 0.5 와 alpha 0.0 + scale 1.0 = 0.5 로 동점이 되고,
    # 동점은 `security_id` 오름차순이라 "a" 가 앞선다 — `scale` 단독 지배가 사라졌다.
    assert _selected(SignalNormalization.RANK) == "a"


def test_normalization_reaches_the_tape_hash_through_the_strategy_hash() -> None:
    """정규화가 다르면 tape 지문도 달라야 캐시된 실행 결과가 섞이지 않는다(P2-04)."""
    observations = _ladder()

    tapes = {
        method: _compile(_normalization_spec(method), observations)
        for method in SignalNormalization
    }

    assert len({tape.strategy_hash for tape in tapes.values()}) == len(SignalNormalization)
    assert len({tape.tape_hash for tape in tapes.values()}) == len(SignalNormalization)
