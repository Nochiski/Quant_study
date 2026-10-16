"""동결 일정의 제한된 합성 검산 조립점. 제품의 혼합 전략 실행기가 아니다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.engine.store import EventStore
from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.orders import TimeInForce
from backtest_engine.types.requirements import StrategyRequirements
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import DeclarativeTapeStrategy, TapeFrame
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _columnar_feed
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import (
    ExecutionTiming,
    ParticipationBasis,
    RunEnvironment,
    SellTax,
    sell_tax_schedule,
)
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionScheduleStatus,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    TargetFrame,
    compile_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    PortfolioSide,
    RebalanceFrequency,
    StrategySpec,
    strategy_spec_hash,
)
from tests.s04_account_fixture import SyntheticAccountScenario


@dataclass(frozen=True)
class CompiledSelectionEvidence:
    candidate_id: str
    strategy_hash: str
    single_candidate_tape_hash: str
    frame: TargetFrame


@dataclass(frozen=True)
class ScheduledAccountRun:
    schedule: SelectionSchedule
    evidence: tuple[CompiledSelectionEvidence, ...]
    store: EventStore


class _SyntheticSelectionStrategy(DeclarativeTapeStrategy):
    """기존 선언형 판단 규칙에 합성 프레임만 제공한다."""

    def __init__(
        self, frames: Mapping[date, TapeFrame], requirements: StrategyRequirements
    ) -> None:
        self._frames = frames
        self._requirements = requirements

    @property
    def idle_reason(self) -> str:
        return "s04_synthetic_selection_idle"

    def requirements(self) -> StrategyRequirements:
        return self._requirements

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


def synthetic_candidates() -> dict[str, StrategySpec]:
    """동일 관측에서 A/B를 각각 고르는 서로 다른 전략을 만든다."""
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    base = replace(
        template,
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
            turnover_buffer_count=0,
            minimum_trade_weight=0.0,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )
    return {
        "high": replace(base, factors=(replace(base.factors[0], direction=FactorDirection.HIGH),)),
        "low": replace(base, factors=(replace(base.factors[0], direction=FactorDirection.LOW),)),
    }


def run_selection_schedule(
    scenario: SyntheticAccountScenario,
    schedule: SelectionSchedule,
    candidates: Mapping[str, StrategySpec],
    core: ExecutionCore,
) -> ScheduledAccountRun:
    """창별 단일 판단만 실행한다. 현금·수량·비용은 기존 커널만 계산한다.

    이 검산은 scenario의 유니버스 편입·벤치마크·보고 기간을 소비하지 않는다.
    기업행동·충격·GTC·후보 내부 cadence는 별도 기존 검증의 책임으로 남긴다.
    """
    if schedule.status is not SelectionScheduleStatus.READY:
        raise ValueError("synthetic schedule must be ready")
    if scenario.sessions != schedule.sessions or scenario.corporate_actions:
        raise ValueError("synthetic schedule requires matching sessions and no corporate actions")
    environment = RunEnvironment(
        start=scenario.sessions[0],
        end=scenario.sessions[-1],
        universe_id="s04-synthetic-selection-only",
        fee_bps=scenario.fee_bps,
        sell_tax=SellTax.CUSTOM,
        sell_tax_bps=scenario.sell_tax_bps,
        slippage_bps=0.0,
        participation_rate=scenario.participation_rate,
        participation_basis=ParticipationBasis.SESSION_VOLUME,
    )
    bridge = BacktestEnginePortfolioAdapter()
    frames: dict[date, TapeFrame] = {}
    evidence: list[CompiledSelectionEvidence] = []
    common_requirements: StrategyRequirements | None = None
    for transition in schedule.transitions:
        spec = candidates[transition.candidate_id]
        if (
            spec.portfolio.side is not PortfolioSide.LONG_ONLY
            or spec.risk.gross_exposure != 1.0
            or spec.portfolio.turnover_buffer_count != 0
            or spec.portfolio.minimum_trade_weight != 0.0
            or spec.portfolio.rebalance is not RebalanceFrequency.EVERY_N_SESSIONS
            or spec.portfolio.rebalance_every_n_sessions != 1
            or environment.timing is not ExecutionTiming.NEXT_OPEN
        ):
            raise ValueError("unsupported synthetic candidate policy")
        signal = transition.signal_as_of
        execution = transition.execution_on
        if scenario.sessions[scenario.sessions.index(signal) + 1] != execution:
            raise ValueError("synthetic execution must be the next common session")
        if signal in frames or schedule.candidate_at(execution) != transition.candidate_id:
            raise ValueError("ambiguous synthetic candidate transition")
        requirements = bridge.requirements(spec, environment)
        if common_requirements is not None and requirements != common_requirements:
            raise ValueError("synthetic candidates must share execution requirements")
        common_requirements = requirements
        tape = compile_target_tape(
            spec,
            environment=environment,
            data_snapshot_id="s04-synthetic-selection-only",
            sessions=(signal, execution),
            observations=tuple(item for item in scenario.observations if item.as_of == signal),
        )
        if len(tape.frames) != 1 or tape.frames[0].execution_on != execution:
            raise ValueError("synthetic candidate must compile exactly one scheduled frame")
        frame = tape.frames[0]
        action = bridge.to_target_action(frame, max_participation=environment.participation_rate)
        if action.execution.time_in_force is not TimeInForce.DAY:
            raise ValueError("synthetic candidate execution must use DAY orders")
        frames[signal] = TapeFrame(action, f"s04_synthetic_selection:{transition.candidate_id}")
        evidence.append(
            CompiledSelectionEvidence(
                transition.candidate_id, strategy_spec_hash(spec), tape.tape_hash, frame
            )
        )
    if common_requirements is None:
        raise ValueError("synthetic schedule must contain a transition")
    engine = BacktestEngine(
        RunConfig(
            run_id=f"s04-synthetic-selection-{core.value}",
            initial_cash=scenario.initial_cash,
            fee_bps=environment.fee_bps,
            sell_tax_schedule=sell_tax_schedule(environment),
        ),
        core=core.value,
    )
    engine.run(
        _SyntheticSelectionStrategy(frames, common_requirements),
        _columnar_feed(scenario.bars, session_calendar=scenario.sessions),
        corporate_actions=(),
    )
    return ScheduledAccountRun(schedule, tuple(evidence), engine.event_store)
