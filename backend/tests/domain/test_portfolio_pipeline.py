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
    FactorDirection,
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
    # `rank` 에서 같은 필드가 0~1 백분위와 비교된다는 사실은
    # `test_score_threshold_compares_against_the_percentile_under_rank` 가 따로 덮는다(P2-04).
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
    # 가중 방식 자체의 산술을 보는 테스트라 원시 점수(`none`)로 고정한다. 기본값 `rank` 에서는
    # 횡단면 최하위가 0점이 되어 `factor_score` 가 그 종목을 비중 0으로 빼므로(P2-04 리뷰 P2-1)
    # 목표 3건이라는 전제가 깨진다 — 그 동작은 아래 전용 테스트가 덮는다.
    spec = _spec()
    spec = replace(
        spec,
        signal=replace(spec.signal, normalization=SignalNormalization.NONE),
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
    # 기여도 합 = 합성 점수라는 불변식은 정규화와 무관하지만, 기대 비중은 원시값 산술이라 `none`
    # 으로 고정한다. 합성 점수는 a 2.5, b 1.0 이다. 1.1 은 점수에 비례해 5/7 이었고, P2-04 결정 5 의
    # 강도 규칙은 선정 2종목·아래 종목 없음이라 기준점이 1.0 − 1.5 = −0.5, 강도 3 : 1.5 → 2/3 이다.
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
    assert by_id["a"].unconstrained_target_weight == pytest.approx(2 / 3)
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
    선정을 결정한다. `rank` 는 둘을 같은 0~1 구간으로 맞춰 그 지배를 없앤다 — 이 fixture 에서는
    두 종목이 0.5 동점이 되고 `security_id` 오름차순 tie-break 로 갈린다.
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


# --- P2-04 점수 비례 가중(`weighting: factor_score`) 규칙 -----------------------------------
#
# 규칙(PLAN 결정 5): 롱 선정 종목 강도 = 방향 반영 합성 점수 − 기준점. 기준점은 선정 최저 점수보다
# 엄격히 낮은 eligible 비선정 종목 중 최고 점수이고, 없으면 "선정 최저 − 선정 점수 평균 간격"이다.
# 강도가 모두 0 이면(1종목·전원 동점) 균등 배분한다. 숏은 점수 부호를 뒤집어 같은 규칙을 쓴다.
# 1~3차 리뷰(P2-1, R2-P204-001, R3-P204-001)의 경계를 값으로 고정한다.

_FIVE_RAW = (("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0), ("e", 5.0))
_ELIGIBILITY_FIELD = "price.market_cap"


def _factor_score_weights(
    raw: tuple[tuple[str, float], ...],
    *,
    method: SignalNormalization,
    direction: FactorDirection,
    side: PortfolioSide = PortfolioSide.LONG_ONLY,
    selection_count: int = 5,
    short_selection_count: int = 2,
    eligible_above: float | None = None,
    field_values: dict[str, float] | None = None,
) -> dict[str, float]:
    """`weighting: factor_score` 의 목표 비중(tape 에 없는 종목은 빠진다).

    `eligible_above` 를 주면 `price.market_cap > eligible_above` 절대 규칙으로 eligible 종목을
    줄인다. 필드 값은 `field_values`(없으면 원시 팩터 값)다.
    """
    day = _NORMALIZATION_DAY
    fields = field_values or dict(raw)
    observations = tuple(
        _observation(
            day,
            security_id,
            value,
            fields=(PortfolioFieldValue(_ELIGIBILITY_FIELD, fields[security_id], day),),
        )
        for security_id, value in raw
    )
    spec = _spec()
    long_short = side is PortfolioSide.LONG_SHORT
    rules = (
        ()
        if eligible_above is None
        else (EligibilityRule(_ELIGIBILITY_FIELD, ComparisonOperator.GREATER_THAN, eligible_above),)
    )
    spec = replace(
        spec,
        factors=(replace(spec.factors[0], direction=direction),),
        signal=replace(spec.signal, normalization=method),
        eligibility=EligibilityStep(rules),
        portfolio=replace(
            spec.portfolio,
            side=side,
            selection_count=selection_count,
            short_selection_count=short_selection_count,
            weighting=WeightingMethod.FACTOR_SCORE,
        ),
        risk=replace(spec.risk, max_name_weight=1.0, net_exposure=0.0 if long_short else 1.0),
    )
    frame = _compile(spec, observations).frames[0]
    return {item.security_id: item.weight for item in frame.targets}


def _approx_table(table: dict[str, float]) -> dict[str, object]:
    return {security_id: pytest.approx(weight) for security_id, weight in table.items()}


_ONE_TO_FIVE = {"a": 1 / 15, "b": 2 / 15, "c": 3 / 15, "d": 4 / 15, "e": 5 / 15}


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_holds_every_selected_name_in_margin_order(
    method: SignalNormalization,
) -> None:
    """원시값 1~5, `high`, 5종목 선정: 선정 종목이 모두 보유되고 가장 좋은 `e` 가 가장 크다.

    eligible 이 전부 선정돼 기준점은 "선정 최저 − 평균 간격" 이다. 등간격이라 세 정규화 모두
    강도 1:2:3:4:5 가 된다. `rank` 에서 선정 최하위(백분위 0)도 dust 가 아니라 제 몫을 받는다
    (1차 리뷰 P2-1 의 dust 문제는 강도가 0 이 되지 않으므로 생기지 않는다).
    """
    table = _factor_score_weights(_FIVE_RAW, method=method, direction=FactorDirection.HIGH)

    assert table == _approx_table(_ONE_TO_FIVE)


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_weights_the_preferred_name_most_when_lower_is_better(
    method: SignalNormalization,
) -> None:
    """`direction: low` 는 원시값이 가장 작은 `a` 가 가장 크고, 5종목을 골라 5종목을 보유한다.

    절댓값 가중은 최선 종목을 0 으로 만들었고(R2-P204-001), 롱 바닥을 eligible 최저 점수로 둔
    규칙은 최악 종목을 0 으로 만들어 4종목만 보유했다(R3-P204-001).
    """
    table = _factor_score_weights(_FIVE_RAW, method=method, direction=FactorDirection.LOW)

    assert table == _approx_table({"a": 5 / 15, "b": 4 / 15, "c": 3 / 15, "d": 2 / 15, "e": 1 / 15})


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_weights_the_strongest_short_most(method: SignalNormalization) -> None:
    """`long_short` 2/2: 롱은 `e` 가, 숏은 가장 강한 숏 `a` 가 가장 크다.

    롱 기준점은 선정 최저 `d` 바로 아래 비선정 `c`, 숏 기준점은 선정 숏 최약 `b` 바로 위 비선정
    `c` 다. 세 정규화 모두 강도 1:2 다.
    """
    table = _factor_score_weights(
        _FIVE_RAW,
        method=method,
        direction=FactorDirection.HIGH,
        side=PortfolioSide.LONG_SHORT,
        selection_count=2,
    )

    assert table == _approx_table({"d": 1 / 6, "e": 2 / 6, "a": -2 / 6, "b": -1 / 6})


@pytest.mark.parametrize("method", list(SignalNormalization))
@pytest.mark.parametrize("direction", list(FactorDirection))
def test_factor_score_holds_a_single_eligible_name_fully(
    method: SignalNormalization, direction: FactorDirection
) -> None:
    """eligible 이 1종목이면 그 종목을 100% 보유한다 — 프레임을 비우지 않는다(R3-P204-001).

    `top_count: 1` 과 같은 모양이다. 롱 바닥을 eligible 최저 점수로 둔 규칙은 `low` 에서 이
    프레임을 통째로 비웠다.
    """
    table = _factor_score_weights(_FIVE_RAW, method=method, direction=direction, eligible_above=4.5)

    assert table == _approx_table({"e": 1.0})


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_holds_a_single_non_positive_name_fully(method: SignalNormalization) -> None:
    """점수가 0 이하인 1종목(원시값 −3, `high`)도 100% 보유한다."""
    table = _factor_score_weights(
        (("a", -3.0),), method=method, direction=FactorDirection.HIGH, selection_count=1
    )

    assert table == _approx_table({"a": 1.0})


@pytest.mark.parametrize("method", list(SignalNormalization))
@pytest.mark.parametrize("direction", list(FactorDirection))
def test_factor_score_splits_evenly_when_every_selected_score_ties(
    method: SignalNormalization, direction: FactorDirection
) -> None:
    """전원 동점이면 강도가 모두 0 이라 균등 배분한다(퇴화 프레임, R3-P204-001)."""
    tied = tuple((security_id, 2.0) for security_id, _ in _FIVE_RAW)

    table = _factor_score_weights(tied, method=method, direction=direction)

    assert table == _approx_table({security_id: 0.2 for security_id, _ in _FIVE_RAW})


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_holds_every_name_when_all_scores_are_negative(
    method: SignalNormalization,
) -> None:
    """원시값이 전부 음수(−5~−1)여도 `high` 5종목 선정은 5종목 보유, 원시값이 큰 `e` 가 최대다."""
    negative = tuple((security_id, value - 6.0) for security_id, value in _FIVE_RAW)

    table = _factor_score_weights(negative, method=method, direction=FactorDirection.HIGH)

    assert table == _approx_table(_ONE_TO_FIVE)


_TEN_RAW = tuple((f"s{index:02d}", float(index)) for index in range(1, 11))


@pytest.mark.parametrize("method", list(SignalNormalization))
def test_factor_score_holds_all_five_selected_when_eligibility_shrinks_the_pool(
    method: SignalNormalization,
) -> None:
    """모집단 10·eligible 5·선정 5, `low`: 5종목을 골라 5종목을 보유한다(R3-P204-001).

    롱 바닥을 eligible 최저 점수로 둔 규칙은 여기서 `s10` 을 0 으로 만들어 4종목만 보유했다.
    """
    table = _factor_score_weights(
        _TEN_RAW, method=method, direction=FactorDirection.LOW, eligible_above=5.5
    )

    assert table == _approx_table(
        {"s06": 5 / 15, "s07": 4 / 15, "s08": 3 / 15, "s09": 2 / 15, "s10": 1 / 15}
    )


_MIRROR_CASES = {
    "long_only_all_selected": {"side": PortfolioSide.LONG_ONLY, "selection_count": 10},
    "long_only_top_two": {"side": PortfolioSide.LONG_ONLY, "selection_count": 2},
    "long_only_shrunken_pool": {
        "side": PortfolioSide.LONG_ONLY,
        "selection_count": 5,
        "eligible_above": 5.5,
    },
    # eligible 5종목이 `low` 선호 쪽(원시값이 작은 쪽)이다. 롱 바닥을 `min(0, eligible 최저)`
    # 로 둔 규칙은 여기서 `high` 는 5종목, `low` 는 4종목을 보유했다(R3-P204-001).
    "long_only_preferred_pool": {
        "side": PortfolioSide.LONG_ONLY,
        "selection_count": 5,
        "eligible_above": 5.5,
        "field_values": {security_id: 11.0 - value for security_id, value in _TEN_RAW},
    },
    "long_short": {"side": PortfolioSide.LONG_SHORT, "selection_count": 2},
}


@pytest.mark.parametrize("method", list(SignalNormalization))
@pytest.mark.parametrize("case", sorted(_MIRROR_CASES))
def test_factor_score_is_mirror_symmetric_between_low_and_high(
    method: SignalNormalization, case: str
) -> None:
    """데이터 x 에 `low` 를 준 결과와 −x 에 `high` 를 준 결과가 보유 종목·비중 모두 같다.

    `none`·`zscore` 는 두 문서의 합성 점수가 같다. `rank` 는 백분위 정의상 두 합성 점수가 상수
    1 만큼 어긋나지만(`-r` 대 `1 - r`), 규칙이 점수의 평행 이동에 불변이라 결과가 같다.
    eligibility 필드는 원시 팩터와 따로 두어 두 문서가 같은 eligible 집합을 본다.
    """
    options = dict(_MIRROR_CASES[case])
    field_values = options.pop("field_values", dict(_TEN_RAW))
    mirrored = tuple((security_id, -value) for security_id, value in _TEN_RAW)

    low = _factor_score_weights(
        _TEN_RAW,
        method=method,
        direction=FactorDirection.LOW,
        field_values=field_values,
        **options,
    )
    high = _factor_score_weights(
        mirrored,
        method=method,
        direction=FactorDirection.HIGH,
        field_values=field_values,
        **options,
    )

    assert set(low) == set(high)
    assert low == _approx_table(high)


_AFFINE_METHODS = (SignalNormalization.NONE, SignalNormalization.ZSCORE)


def _labelled(values: tuple[float, ...]) -> tuple[tuple[str, float], ...]:
    return tuple(zip("abcdefgh", values, strict=False))


@pytest.mark.parametrize("method", _AFFINE_METHODS)
def test_factor_score_gives_a_near_tie_at_the_cut_a_real_share(
    method: SignalNormalization,
) -> None:
    """컷 바로 아래 비선정 종목과 거의 동점인 선정 종목도 평균 간격만큼의 강도를 받는다.

    원시값 3, 2, 1+2⁻⁵², 1 에서 3종목 선정. 기준점을 "컷 아래 최고"만으로 잡으면 `c` 의 강도가
    2⁻⁵² 라 비중이 `7.4e-17` 인 dust target 이 됐다(4차 리뷰 R4-P204-001). 기준점은
    `min(컷 아래 최고 1, 선정 최저 − 평균 간격 0) = 0` 이라 강도 3 : 2 : 1 이다. `none`·`zscore`
    는 원시값의 양의 아핀 변환이라 같은 표다.
    """
    raw = _labelled((3.0, 2.0, 1.0 + 2.0**-52, 1.0))

    table = _factor_score_weights(
        raw, method=method, direction=FactorDirection.HIGH, selection_count=3
    )

    assert table == _approx_table({"a": 3 / 6, "b": 2 / 6, "c": 1 / 6})


@pytest.mark.parametrize("method", _AFFINE_METHODS)
def test_factor_score_uses_the_best_rejected_score_when_it_is_further_than_the_gap(
    method: SignalNormalization,
) -> None:
    """원시값 5, 4, 2, 1 에서 2종목 선정: 기준점은 컷 아래 최고 2 다(평균 간격 기준 3 보다 낮다).

    강도 3 : 2 → .6/.4. 컷 아래 종목을 무시하고 평균 간격만 쓰면 2 : 1 → 2/3·1/3 이 된다. 이
    테스트가 "컷 아래 종목 정보를 쓴다"는 규칙 부분을 고정한다(R4-P204-002).
    """
    raw = _labelled((5.0, 4.0, 2.0, 1.0))

    table = _factor_score_weights(
        raw, method=method, direction=FactorDirection.HIGH, selection_count=2
    )

    assert table == _approx_table({"a": 0.6, "b": 0.4})


@pytest.mark.parametrize("method", _AFFINE_METHODS)
def test_factor_score_skips_a_rejected_name_tied_with_the_weakest_selected(
    method: SignalNormalization,
) -> None:
    """원시값 5, 4, 4, 1 에서 2종목 선정: `c` 는 `b` 와 동점이지만 비선정이라 기준점이 되지 않는다.

    기준점은 선정 최저 4 보다 **엄격히** 낮은 비선정 중 최고 1 과 평균 간격 기준 3 중 작은 값
    1 이다. 강도 4 : 3 → 4/7·3/7 이고 `b` 를 보유한다. 동점 비선정을 기준점에 넣으면 기준점이
    3 이 되어 2/3·1/3 이 되고, 평균 간격 하한이 없던 규칙에서는 `b` 가 비중 0 으로 빠졌다
    (R4-P204-002).
    """
    raw = _labelled((5.0, 4.0, 4.0, 1.0))

    table = _factor_score_weights(
        raw, method=method, direction=FactorDirection.HIGH, selection_count=2
    )

    assert table == _approx_table({"a": 4 / 7, "b": 3 / 7})


@pytest.mark.parametrize("method", _AFFINE_METHODS)
def test_factor_score_lets_a_far_rejected_name_flatten_the_weights(
    method: SignalNormalization,
) -> None:
    """원시값 3, 2, 1, −100 에서 3종목 선정: 컷 아래 종목이 멀면 기준점도 그 점수다(알려진 성질).

    기준점은 `min(−100, 0) = −100` 이라 강도 103 : 102 : 101 로 거의 균등하다. 평균 간격 하한은
    기준점이 선정 최저에 **너무 가까운** 쪽만 막고, 먼 쪽은 막지 않는다. PLAN 결정 5 가 이 성질을
    기록한다.
    """
    raw = _labelled((3.0, 2.0, 1.0, -100.0))

    table = _factor_score_weights(
        raw, method=method, direction=FactorDirection.HIGH, selection_count=3
    )

    assert table == _approx_table({"a": 103 / 306, "b": 102 / 306, "c": 101 / 306})


def test_factor_score_under_rank_without_ties_matches_rank_weighting() -> None:
    """`rank` 정규화에 동점이 없으면 순위 간격이 균일해 강도가 N : … : 1 이 된다.

    `weighting: rank` 와 같은 비중이다. PLAN 결정 5 가 이 성질을 기록한다(4차 리뷰 R4-P204-003).
    """
    raw = tuple((f"s{index:02d}", float(index) ** 3) for index in range(1, 11))

    table = _factor_score_weights(
        raw, method=SignalNormalization.RANK, direction=FactorDirection.HIGH, selection_count=4
    )

    assert table == _approx_table({"s10": 0.4, "s09": 0.3, "s08": 0.2, "s07": 0.1})


def test_a_normalized_signal_missing_from_the_population_raises_instead_of_using_raw(
    monkeypatch,
) -> None:
    """정규화 맵에서 빠진 값은 원시값으로 떨어지지 않고 진단과 함께 멈춘다(P2-04 결정 7).

    지금은 정규화 모집단 술어와 `_score_candidate` 의 값 단위 탈락 술어가 같아서 도달 불가다.
    그 전제가 깨졌을 때 `.get(key, raw)` 처럼 원시값으로 떨어지면 정규화 값과 원시값이 같은
    가중 합에 섞인다. 조회를 되돌려도 CI 가 초록이던 것을 막는다(2차 리뷰 R2-P204-002).
    """
    from strategy_workbench.domain.portfolio import _compiler as compiler_module

    original = compiler_module._cross_sectional_signals

    def drop_one(*args, **kwargs):
        signals = dict(original(*args, **kwargs))
        signals.pop(("price.close", "c"))
        return signals

    monkeypatch.setattr(compiler_module, "_cross_sectional_signals", drop_one)
    day = _NORMALIZATION_DAY
    observations = tuple(_observation(day, security_id, value) for security_id, value in _FIVE_RAW)
    spec = _spec()
    spec = replace(spec, signal=replace(spec.signal, normalization=SignalNormalization.RANK))

    with pytest.raises(ValueError, match="normalized signal missing") as raised:
        _compile(spec, observations)

    message = str(raised.value)
    assert "security_id='c'" in message
    assert "factor_id='price.close'" in message
    assert "normalization='rank'" in message


@pytest.mark.parametrize("method", list(SignalNormalization))
@pytest.mark.parametrize("direction", list(FactorDirection))
@pytest.mark.parametrize("side", list(PortfolioSide))
def test_factor_score_weighting_is_valid_with_every_normalization(
    method: SignalNormalization, direction: FactorDirection, side: PortfolioSide
) -> None:
    """점수 비례 가중은 정규화·방향·side 조합 어디서도 compile 을 막지 않는다.

    1차 리뷰 반영에서 `zscore` × `factor_score` 를 error 로 막았지만 원인(`abs(composite_score)`)은
    `rank` 의 `direction: low` 와 공매도 쪽에 남아 있었다(R2-P204-001). 비중이 선호 방향 거리를
    쓰게 고친 뒤로는 막을 조합이 없다 — 위 두 방향 테스트가 세 정규화 모두에서 순서를 고정한다.
    """
    base = _spec()
    spec = replace(
        base,
        factors=(replace(base.factors[0], direction=direction),),
        signal=replace(base.signal, normalization=method),
        portfolio=replace(base.portfolio, side=side, weighting=WeightingMethod.FACTOR_SCORE),
        risk=replace(base.risk, net_exposure=0.0 if side is PortfolioSide.LONG_SHORT else 1.0),
    )

    validation = validate_strategy(spec)

    assert validation.valid, [issue.code for issue in validation.issues]


def test_score_threshold_compares_against_the_percentile_under_rank() -> None:
    """`rank` 에서 `score_threshold` 는 원시값이 아니라 0~1 백분위와 비교된다.

    같은 문턱 값이 `none` 과 `rank` 에서 전혀 다른 필터가 된다 — 1.1 문서를 그대로 옮기면
    문턱이 사실상 꺼지거나(원시값 스케일이 1 보다 클 때) 전부 걸러낸다(P2-04 리뷰 P3).
    """
    observations = tuple(
        _observation(_NORMALIZATION_DAY, security_id, value)
        for security_id, value in (("a", 10.0), ("b", 20.0), ("c", 30.0))
    )

    def blocked(method: SignalNormalization, threshold: float) -> set[str]:
        spec = _spec()
        spec = replace(
            spec,
            signal=replace(spec.signal, normalization=method, score_threshold=threshold),
            portfolio=replace(spec.portfolio, selection_count=3),
        )
        frame = _compile(spec, observations).frames[0]
        return {
            item.security_id
            for item in frame.candidates
            if ExclusionReason.SCORE_THRESHOLD in item.exclusion_reasons
        }

    # 원시값 10·20·30 은 문턱 0.5 를 전부 넘는다.
    assert blocked(SignalNormalization.NONE, 0.5) == set()
    # 같은 문턱이 `rank` 에서는 백분위 0.0·0.5·1.0 과 비교되어 최하위만 걸린다.
    assert blocked(SignalNormalization.RANK, 0.5) == {"a"}
    # 백분위는 1.0 을 넘지 않으므로 1 보다 큰 문턱은 전부 걸러낸다.
    assert blocked(SignalNormalization.RANK, 1.5) == {"a", "b", "c"}
