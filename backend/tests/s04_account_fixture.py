"""S04 합성 계좌 입력과 실제 compiler·실행 어댑터의 공용 조립점."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataset,
    BacktestExecutionRequest,
    CorporateActionRecord,
    MarketBarRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import (
    ParticipationBasis,
    RunEnvironment,
    SellTax,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    ExecutionCore,
    MetricWindow,
    ResolvedBacktestRunSpec,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioObservation,
    TargetTape,
    compile_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import RebalanceFrequency
from tests.rust_core_guard import RUST_CORE

CORES = [
    ExecutionCore.PYTHON,
    pytest.param(
        ExecutionCore.RUST,
        marks=RUST_CORE,
    ),
]


@dataclass(frozen=True)
class SyntheticAccountScenario:
    sessions: tuple[date, ...]
    bars: tuple[MarketBarRecord, ...]
    observations: tuple[PortfolioObservation, ...]
    memberships: tuple[UniverseMembershipRecord, ...]
    corporate_actions: tuple[CorporateActionRecord, ...] = ()
    rebalance_every_n_sessions: int = 2
    initial_cash: float = 1_000.0
    fee_bps: float = 0.0
    sell_tax_bps: float = 0.0
    participation_rate: float = 0.1
    benchmark_security_id: str | None = None
    metric_windows: tuple[MetricWindow, ...] = ()


@dataclass(frozen=True)
class AccountRun:
    tape: TargetTape
    result: BacktestRunResult


def account_bar(
    session: date,
    security_id: str,
    open_price: float,
    close_price: float,
    volume: int = 10_000,
) -> MarketBarRecord:
    return MarketBarRecord(
        session,
        security_id,
        open_price,
        max(open_price, close_price),
        min(open_price, close_price),
        close_price,
        volume,
    )


def account_observations(
    sessions: tuple[date, ...],
    scores_by_session: tuple[tuple[tuple[str, float], ...], ...],
) -> tuple[PortfolioObservation, ...]:
    return tuple(
        PortfolioObservation(
            as_of=session,
            security_id=security_id,
            universe_member=True,
            factor_values=(PortfolioFactorValue("price.close", score, session),),
            sector_id="synthetic-sector",
        )
        for session, scores in zip(sessions, scores_by_session, strict=True)
        for security_id, score in scores
    )


def run_account_scenario(scenario: SyntheticAccountScenario, core: ExecutionCore) -> AccountRun:
    """하나의 tape와 하나의 엔진 실행을 조립하며 계좌 계산은 기존 owner에 맡긴다."""
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    spec = replace(
        template,
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=scenario.rebalance_every_n_sessions,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )
    environment = RunEnvironment(
        start=scenario.sessions[0],
        end=scenario.sessions[-1],
        universe_id="synthetic-only",
        fee_bps=scenario.fee_bps,
        slippage_bps=0.0,
        sell_tax=SellTax.CUSTOM,
        sell_tax_bps=scenario.sell_tax_bps,
        participation_rate=scenario.participation_rate,
        participation_basis=ParticipationBasis.SESSION_VOLUME,
    )
    snapshot_id = "s04-synthetic-only"
    tape = compile_target_tape(
        spec,
        environment=environment,
        data_snapshot_id=snapshot_id,
        sessions=scenario.sessions,
        observations=scenario.observations,
    )
    dataset = BacktestDataset(
        data_snapshot_id=snapshot_id,
        sessions=scenario.sessions,
        bars=scenario.bars,
        memberships=scenario.memberships,
        corporate_actions=scenario.corporate_actions,
        benchmark_security_id=scenario.benchmark_security_id,
    )
    result = BacktestEngineExecutorAdapter().execute(
        BacktestExecutionRequest(
            run_id=f"s04-{core.value}",
            spec=ResolvedBacktestRunSpec(
                strategy=spec,
                environment=environment,
                core=core,
                initial_cash=scenario.initial_cash,
                benchmark_security_id=scenario.benchmark_security_id,
                metric_windows=scenario.metric_windows,
            ),
            target_tape=tape,
            dataset=dataset,
            strategy_provenance=StrategyProvenance(
                StrategySourceKind.INLINE_DRAFT, tape.strategy_hash, "1.2"
            ),
            strategy_source=None,
            lineage_strategy_id=None,
        ),
        progress=lambda _fraction, _message: None,
        cancelled=lambda: False,
    )
    return AccountRun(tape, result)
