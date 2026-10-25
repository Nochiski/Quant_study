"""작은 합성 횡단면으로 후보 교체·cadence·단일 목표 비중 fold를 검산한다."""

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    WindowSelection,
)
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode
from strategy_workbench.domain.factor.facade.planning import compile_factor_plan
from strategy_workbench.domain.portfolio.facade.construction import (
    ExclusionReason,
    NonFinitePortfolioCalculationError,
    PortfolioFactorValue,
    PortfolioObservation,
    compile_target_tape,
)
from strategy_workbench.domain.portfolio.facade.scheduled_construction import (
    ScheduledCandidate,
    ScheduledTargetTape,
    compile_scheduled_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    IntegerParameter,
    PortfolioStep,
    RebalanceFrequency,
    RiskStep,
    StrategyIdentity,
    StrategySpec,
)


def day(number: int) -> date:
    return date(2026, 10, number)


def environment() -> RunEnvironment:
    return RunEnvironment(start=day(5), end=day(16), universe_id="krx.common-stock")


def spec(*, interval: int = 1, buffer: int = 0, minimum: float = 0) -> StrategySpec:
    graph = FactorGraph(nodes=(FieldNode("f", "price.close", "field"),), output_node_id="f")
    return StrategySpec(
        identity=StrategyIdentity("fixture", 1),
        title="합성 후보",
        factors=(
            FactorSignal(
                factor_id="alpha", label="알파", direction=FactorDirection.HIGH, graph=graph
            ),
        ),
        portfolio=PortfolioStep(
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=interval,
            turnover_buffer_count=buffer,
            minimum_trade_weight=minimum,
        ),
        risk=RiskStep(max_name_weight=1, max_sector_weight=1),
    )


def observations(
    sessions: tuple[date, ...], *, winner: str = "A", previous: float = 0
) -> tuple[PortfolioObservation, ...]:
    return tuple(
        PortfolioObservation(
            as_of=session,
            security_id=security,
            universe_member=True,
            factor_values=(
                PortfolioFactorValue("alpha", 2.0 if security == winner else 1.0, session),
            ),
            sector_id="sector",
            previous_weight=previous,
        )
        for session in sessions
        for security in ("A", "B")
    )


def candidate(
    strategy: StrategySpec,
    sessions: tuple[date, ...],
    *,
    winner: str = "A",
    source: str = "source-a",
) -> ScheduledCandidate:
    plan = compile_factor_plan(
        strategy.factors[0].graph,
        registry_version="fixture",
        missing=environment().missing,
        parameter_ids=tuple(p.parameter_id for p in strategy.parameters),
    )
    return ScheduledCandidate(
        spec=strategy,
        parameter_values=(),
        source=source,
        factor_plans=(("alpha", plan),),
        data_snapshot_id="snapshot",
        observations=observations(sessions, winner=winner),
    )


def schedule(
    a: ScheduledCandidate, b: ScheduledCandidate, sessions: tuple[date, ...]
) -> SelectionSchedule:
    return SelectionSchedule(
        selections=(
            WindowSelection(
                window_index=0,
                window=WalkForwardWindow(day(1), day(2), day(5), day(9)),
                candidate_id=a.candidate_id,
                known_as_of=day(2),
            ),
            WindowSelection(
                window_index=1,
                window=WalkForwardWindow(day(1), day(9), day(10), day(16)),
                candidate_id=b.candidate_id,
                known_as_of=day(9),
            ),
        ),
        sessions=sessions,
    )


def compile_pair(
    a: ScheduledCandidate, b: ScheduledCandidate, sessions: tuple[date, ...]
) -> ScheduledTargetTape:
    return compile_scheduled_target_tape(
        schedule=schedule(a, b, sessions),
        candidates=(a,) if a.candidate_id == b.candidate_id else (a, b),
        environment=environment(),
    )


def test_same_candidate_keeps_global_every_n_anchor_without_boundary_force() -> None:
    sessions = tuple(day(n) for n in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16))
    a = candidate(spec(interval=4), sessions)
    tape = compile_pair(a, a, sessions)
    assert tuple(frame.signal_as_of for frame in tape.frames) == (day(5), day(8), day(14))
    assert tuple(frame.execution_on for frame in tape.frames) == (day(6), day(9), day(15))
    assert day(12) not in {frame.signal_as_of for frame in tape.frames}
    assert tape.frame_sources[-1].selection_window_index == 1


def test_changed_candidate_forces_once_and_removes_old_candidate_cadence() -> None:
    sessions = tuple(day(n) for n in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16))
    a = candidate(spec(interval=1), sessions)
    b = candidate(spec(interval=3), sessions, winner="B", source="source-b")
    tape = compile_pair(a, b, sessions)
    assert tuple(frame.signal_as_of for frame in tape.frames) == tuple(
        day(n) for n in (5, 6, 7, 8, 9, 12, 15)
    )
    assert len([frame for frame in tape.frames if frame.signal_as_of == day(12)]) == 1
    after = [source for source in tape.frame_sources if source.signal_as_of >= day(12)]
    assert all(source.candidate_id == b.candidate_id for source in after)
    cross = next(source for source in tape.frame_sources if source.signal_as_of == day(9))
    assert (cross.selection_window_index, cross.execution_window_index) == (0, 1)
    assert cross.candidate_id == a.candidate_id
    assert tape.frames[-1].targets[0].security_id == "B"


def test_buffer_keeps_previous_target_across_candidates() -> None:
    sessions = tuple(day(n) for n in (5, 6, 9, 12, 13, 16))
    a = candidate(spec(), sessions)
    b = candidate(spec(buffer=1), sessions, winner="B", source="b")
    tape = compile_pair(a, b, sessions)
    transition = next(frame for frame in tape.frames if frame.signal_as_of == day(12))
    assert {target.security_id: target.weight for target in transition.targets} == {
        "A": 0.5,
        "B": 0.5,
    }
    assert (
        ExclusionReason.TURNOVER_BUFFER
        in next(d for d in transition.candidates if d.security_id == "A").exclusion_reasons
    )


def test_minimum_trade_uses_carried_target_instead_of_later_observation_seed() -> None:
    sessions = tuple(day(n) for n in (5, 6, 9, 12, 13, 16))
    a = candidate(spec(), sessions)
    bspec = spec(minimum=0.2)
    bspec = replace(bspec, risk=replace(bspec.risk, gross_exposure=0.9))
    b = candidate(bspec, sessions, source="b")
    b = replace(b, observations=observations(sessions, previous=0.33))
    tape = compile_pair(a, b, sessions)
    transition = next(frame for frame in tape.frames if frame.signal_as_of == day(12))
    # 최소 거래 판단은 직전 목표 1.0에서 시작하지만 0.9 gross 예산으로 최종 제한된다.
    decision = next(d for d in transition.candidates if d.security_id == "A")
    assert ExclusionReason.MINIMUM_TRADE in decision.exclusion_reasons
    assert transition.targets[0].weight == pytest.approx(0.9)


def test_monthly_cadence_uses_the_whole_calendar() -> None:
    sessions = (
        date(2026, 10, 29),
        date(2026, 10, 30),
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
    )
    strategy = spec()
    strategy = replace(
        strategy, portfolio=replace(strategy.portfolio, rebalance=RebalanceFrequency.MONTHLY)
    )
    a = candidate(strategy, sessions)
    selection = WindowSelection(
        window_index=0,
        window=WalkForwardWindow(day(1), day(2), sessions[0], sessions[-1]),
        candidate_id=a.candidate_id,
        known_as_of=day(2),
    )
    planned = SelectionSchedule(selections=(selection,), sessions=sessions)
    tape = compile_scheduled_target_tape(
        schedule=planned,
        candidates=(a,),
        environment=replace(environment(), start=sessions[0], end=sessions[-1]),
    )
    assert tuple(frame.signal_as_of for frame in tape.frames) == sessions[:2]
    assert tuple(frame.execution_on for frame in tape.frames) == sessions[1:3]


def test_daily_single_candidate_frame_values_equal_the_ordinary_tape() -> None:
    sessions = tuple(day(n) for n in (5, 6, 9, 12, 13, 16))
    a = candidate(spec(), sessions)
    tape = compile_pair(a, a, sessions)
    ordinary = compile_target_tape(
        a.spec,
        environment=environment(),
        data_snapshot_id="snapshot",
        sessions=sessions,
        observations=a.observations,
    )
    assert tape.frames == ordinary.frames
    assert tape.tape_hash != ordinary.tape_hash
    assert tape == compile_pair(a, a, sessions)


def test_parameter_and_provenance_identity_is_canonical_and_data_independent() -> None:
    sessions = (day(5), day(6), day(12), day(13))
    strategy = replace(spec(), parameters=(IntegerParameter("n", 2, 1, 5, "integer"),))
    a = candidate(strategy, sessions)
    assert a.parameter_values == (("n", 2),)
    assert replace(a, parameter_values=(("n", 2.0),)).candidate_id == a.candidate_id
    assert replace(a, data_snapshot_id="other").candidate_id == a.candidate_id
    assert (
        replace(a, observations=observations(sessions, winner="B")).candidate_id == a.candidate_id
    )
    assert replace(a, source="changed").candidate_id != a.candidate_id
    assert replace(a, parameter_values=(("n", 3),)).candidate_id != a.candidate_id
    with pytest.raises(ValueError, match="duplicate scheduled parameter"):
        replace(a, parameter_values=(("n", 2), ("n", 3)))
    with pytest.raises(ValueError, match="graph mismatch"):
        replace(a, factor_plans=(("alpha", replace(a.factor_plans[0][1], graph_hash="wrong")),))
    with pytest.raises(ValueError, match="differ from spec"):
        replace(a, factor_plans=())


def test_duplicate_and_nonfinite_observations_are_rejected() -> None:
    sessions = (day(5), day(6), day(12), day(13))
    a = candidate(spec(), sessions)
    with pytest.raises(ValueError, match="duplicate scheduled observation"):
        replace(a, observations=(a.observations[0], a.observations[0]))
    first = a.observations[0]
    bad = replace(first, factor_values=(replace(first.factor_values[0], value=float("nan")),))
    with pytest.raises(NonFinitePortfolioCalculationError):
        replace(a, observations=(bad,))


def test_pit_future_value_stays_excluded_in_the_mixed_compiler() -> None:
    sessions = (day(5), day(6), day(12), day(13))
    a = candidate(spec(), sessions)
    a = replace(
        a,
        observations=tuple(
            replace(o, factor_values=(replace(o.factor_values[0], available_date=day(16)),))
            if o.security_id == "A"
            else o
            for o in a.observations
        ),
    )
    tape = compile_pair(a, a, sessions)
    assert tape.frames[0].targets[0].security_id == "B"
    assert ExclusionReason.FUTURE_DATA in tape.frames[0].candidates[0].exclusion_reasons


def test_candidate_set_snapshot_and_missing_policy_must_match() -> None:
    sessions = (day(5), day(6), day(12), day(13))
    a = candidate(spec(), sessions)
    b = candidate(spec(), sessions, source="b")
    planned = schedule(a, b, sessions)
    for candidates in ((a,), (a, a, b), (a, replace(b, data_snapshot_id="other"))):
        with pytest.raises(ValueError):
            compile_scheduled_target_tape(
                schedule=planned, candidates=candidates, environment=environment()
            )
    mismatch = replace(
        a, factor_plans=(("alpha", replace(a.factor_plans[0][1], missing_policy="different")),)
    )
    with pytest.raises(ValueError, match="missing policy differs"):
        compile_pair(mismatch, mismatch, sessions)


def test_snapshot_and_evaluated_values_change_only_the_mixed_tape_hash() -> None:
    sessions = (day(5), day(6), day(12), day(13))
    a = candidate(spec(), sessions)
    base = compile_pair(a, a, sessions)
    other_snapshot = replace(a, data_snapshot_id="other")
    assert compile_pair(other_snapshot, other_snapshot, sessions).tape_hash != base.tape_hash
    other_values = replace(a, observations=observations(sessions, winner="B"))
    assert compile_pair(other_values, other_values, sessions).tape_hash != base.tape_hash
