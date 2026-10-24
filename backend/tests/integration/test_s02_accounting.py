"""합성 신호의 선정부터 비용 후 계좌까지 독립 손계산하고 미지원 회계 경계를 드러낸다."""

from __future__ import annotations

from dataclasses import replace
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
from strategy_workbench.domain.strategy.facade.specification import RebalanceFrequency, StrategySpec
from tests.rust_core_guard import RUST_CORE

SESSIONS = tuple(date(2024, 1, day) for day in (5, 8, 9, 10, 11))
A, B = "synthetic-a", "synthetic-b"
SNAPSHOT = "s02-synthetic-only"
CORES = [
    ExecutionCore.PYTHON,
    pytest.param(
        ExecutionCore.RUST,
        marks=RUST_CORE,
    ),
]


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    return replace(
        template,
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=2,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )


def _bar(session: date, security_id: str, open_price: float, close: float) -> MarketBarRecord:
    return MarketBarRecord(
        session,
        security_id,
        open_price,
        max(open_price, close),
        min(open_price, close),
        close,
        10_000,
    )


def _run(
    core: ExecutionCore, *, listing_ended: bool = False
) -> tuple[TargetTape, BacktestRunResult]:
    spec = _spec()
    environment = RunEnvironment(
        start=SESSIONS[0],
        end=SESSIONS[-1],
        universe_id="synthetic-only",
        fee_bps=100.0,
        slippage_bps=0.0,
        sell_tax=SellTax.CUSTOM,
        sell_tax_bps=200.0,
        participation_basis=ParticipationBasis.SESSION_VOLUME,
    )
    # 입력은 이미 평가된 합성 팩터 신호다. 순위·선정·목표는 실제 compiler가 계산한다.
    observations = tuple(
        PortfolioObservation(
            as_of=session,
            security_id=security_id,
            universe_member=not (listing_ended and security_id == A and index >= 3),
            factor_values=(PortfolioFactorValue("price.close", score, session),),
            sector_id="synthetic-sector",
        )
        for index, session in enumerate(SESSIONS)
        for security_id, score in ((A, 2.0 if index < 3 else 1.0), (B, 1.0 if index < 3 else 2.0))
    )
    tape = compile_target_tape(
        spec,
        environment=environment,
        data_snapshot_id=SNAPSHOT,
        sessions=SESSIONS,
        observations=observations,
    )
    bars = tuple(
        bar
        for index, session in enumerate(SESSIONS)
        for bar in (
            _bar(
                session,
                A,
                (100.0, 100.0, 110.0, 55.0, 55.0)[index],
                (100.0, 100.0, 110.0, 55.0, 55.0)[index],
            ),
            _bar(session, B, 100.0, 110.0 if index == 4 else 100.0),
        )
        if not (listing_ended and bar.security_id == A and index >= 3)
    )
    dataset = BacktestDataset(
        data_snapshot_id=SNAPSHOT,
        sessions=SESSIONS,
        bars=bars,
        memberships=(
            UniverseMembershipRecord(
                A, SESSIONS[0], SESSIONS[2] if listing_ended else SESSIONS[-1]
            ),
            UniverseMembershipRecord(B, SESSIONS[0], SESSIONS[-1]),
        ),
        corporate_actions=()
        if listing_ended
        else (CorporateActionRecord(SESSIONS[3], A, "split", "2", "합성 2:1 분할"),),
        benchmark_security_id=A,
    )
    result = BacktestEngineExecutorAdapter().execute(
        BacktestExecutionRequest(
            run_id=f"s02-{core.value}-{listing_ended}",
            spec=ResolvedBacktestRunSpec(
                strategy=spec,
                environment=environment,
                core=core,
                initial_cash=1_000.0,
                benchmark_security_id=A,
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
    return tape, result


@pytest.mark.parametrize("core", CORES)
def test_signal_selection_split_and_rotation_match_independent_cash_book(
    core: ExecutionCore,
) -> None:
    tape, result = _run(core)
    assert [(frame.signal_as_of, frame.execution_on) for frame in tape.frames] == [
        (SESSIONS[1], SESSIONS[2]),
        (SESSIONS[3], SESSIONS[4]),
    ]
    assert [
        [(target.security_id, target.weight) for target in frame.targets] for frame in tape.frames
    ] == [
        [(A, 1.0)],
        [(B, 1.0)],
    ]
    assert [
        [candidate.security_id for candidate in frame.candidates if candidate.selected]
        for frame in tape.frames
    ] == [[A], [B]]
    artifacts = result.artifacts
    # 10주 목표는 갭과 비용 때문에 9주만 체결된다. DAY 잔량은 다음 세션에 재체결되지 않는다.
    assert [(order.security_id, order.side, order.quantity) for order in artifacts.orders] == [
        (A, "buy", "10"),
        (B, "buy", "9"),
        (A, "sell", "18"),
    ]
    assert [
        (fill.session, fill.security_id, fill.side, fill.quantity, fill.price)
        for fill in artifacts.fills
    ] == [
        (SESSIONS[2], A, "buy", "9", 110.0),
        (SESSIONS[4], A, "sell", "18", 55.0),
        (SESSIONS[4], B, "buy", "9", 100.0),
    ]
    assert [fill.fee for fill in artifacts.fills] == pytest.approx([9.9, 9.9, 9.0])
    assert [fill.sell_tax for fill in artifacts.fills] == pytest.approx([0.0, 19.8, 0.0])
    # 1000−990−9.9=.1; 분할은 현금 불변; .1+990−9.9−19.8−900−9=51.4.
    assert [snapshot.cash for snapshot in artifacts.snapshots] == pytest.approx(
        [1000, 1000, 0.1, 0.1, 51.4]
    )
    assert [snapshot.equity for snapshot in artifacts.snapshots] == pytest.approx(
        [1000, 1000, 990.1, 990.1, 1041.4]
    )
    assert [(position.security_id, position.quantity) for position in artifacts.positions] == [
        (A, "9"),
        (A, "18"),
        (B, "9"),
    ]
    assert [position.market_value for position in artifacts.positions] == pytest.approx(
        [990, 990, 990]
    )
    assert [position.average_price for position in artifacts.positions] == pytest.approx(
        [110, 55, 100]
    )
    assert artifacts.order_updates is not None
    assert "partially_filled" in {update.status for update in artifacts.order_updates}
    assert [(cost.kind, cost.amount) for cost in artifacts.costs] == [
        ("sell_tax", pytest.approx(19.8))
    ]
    assert [point.benchmark_equity for point in result.series.equity] == pytest.approx(
        [1000, 1000, 1100, 1100, 1100]
    )
    metrics = {metric.metric_id: metric.value for metric in result.metrics}
    assert metrics["total_return"] == pytest.approx(0.0414)
    assert metrics["benchmark_return"] == pytest.approx(0.1)
    assert metrics["excess_return"] == pytest.approx(-0.0586)


@pytest.mark.parametrize("core", CORES)
def test_ended_listing_exposes_stale_valuation_without_invented_recovery(
    core: ExecutionCore,
) -> None:
    """현재 한계: 상장 구간 종료는 회수금 정산이 아니며 마지막 가격이 계속 남는다."""
    tape, result = _run(core, listing_ended=True)
    assert [target.security_id for target in tape.frames[-1].targets] == [B]
    artifacts = result.artifacts
    assert [(fill.security_id, fill.side, fill.quantity) for fill in artifacts.fills] == [
        (A, "buy", "9")
    ]
    assert [snapshot.cash for snapshot in artifacts.snapshots] == pytest.approx(
        [1000, 1000, 0.1, 0.1, 0.1]
    )
    assert [snapshot.equity for snapshot in artifacts.snapshots] == pytest.approx(
        [1000, 1000, 990.1, 990.1, 990.1]
    )
    assert [
        (position.session, position.quantity, position.market_price)
        for position in artifacts.positions
    ] == [(session, "9", 110.0) for session in SESSIONS[2:]]
    assert artifacts.costs == ()
    assert [point.benchmark_equity for point in result.series.equity] == pytest.approx(
        [1000, 1000, 1100, 1100, 1100]
    )
    assert "benchmark.delisted_sessions_frozen" in {
        warning.code for warning in result.manifest.warnings
    }
