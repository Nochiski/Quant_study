"""종목·벤치마크 bar 유무와 독립적인 거래 달력으로 DAY 주문을 처리한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataset,
    BacktestExecutionRequest,
    UniverseMembershipRecord,
)
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
    compile_target_tape,
)
from tests.integration.test_s02_accounting import CORES, SESSIONS, SNAPSHOT, A, B, _bar, _spec


def _request(
    core: ExecutionCore, *, benchmark: bool, empty_sessions: tuple[date, ...], fee_bps: float
) -> BacktestExecutionRequest:
    spec = _spec()
    environment = RunEnvironment(
        start=SESSIONS[0],
        end=SESSIONS[-1],
        universe_id="synthetic-only",
        fee_bps=fee_bps,
        slippage_bps=0,
        sell_tax=SellTax.CUSTOM,
        sell_tax_bps=0,
        participation_basis=ParticipationBasis.SESSION_VOLUME,
    )
    tape = compile_target_tape(
        spec,
        environment=environment,
        data_snapshot_id=SNAPSHOT,
        sessions=SESSIONS,
        observations=tuple(
            PortfolioObservation(
                as_of=session,
                security_id=A,
                universe_member=True,
                factor_values=(PortfolioFactorValue("price.close", 1, session),),
                sector_id="synthetic",
            )
            for session in SESSIONS
        ),
    )
    bars = tuple(
        _bar(session, A, 110 if index == 4 else 100, 110 if index >= 3 else 100)
        for index, session in enumerate(SESSIONS)
        if session not in empty_sessions
    )
    if benchmark:
        bars += tuple(_bar(session, B, 100, 100) for session in SESSIONS)
    dataset = BacktestDataset(
        SNAPSHOT,
        bars,
        tuple(UniverseMembershipRecord(name, SESSIONS[0], SESSIONS[-1]) for name in (A, B)),
        (),
        B if benchmark else None,
        sessions=SESSIONS,
    )
    return BacktestExecutionRequest(
        "calendar-synthetic",
        ResolvedBacktestRunSpec(
            strategy=spec,
            environment=environment,
            core=core,
            initial_cash=1000,
            benchmark_security_id=B if benchmark else None,
        ),
        tape,
        dataset,
        StrategyProvenance(StrategySourceKind.INLINE_DRAFT, tape.strategy_hash, "1.2"),
        None,
        None,
    )


def _execute(request: BacktestExecutionRequest) -> BacktestRunResult:
    return BacktestEngineExecutorAdapter().execute(
        request, progress=lambda _fraction, _message: None, cancelled=lambda: False
    )


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("fee_bps", [0, 100])
def test_day_order_expires_on_empty_session_independently_of_benchmark(
    core: ExecutionCore, fee_bps: float
) -> None:
    results = [
        _execute(
            _request(core, benchmark=benchmark, empty_sessions=(SESSIONS[2],), fee_bps=fee_bps)
        )
        for benchmark in (False, True)
    ]
    for result in results:
        assert tuple(snapshot.session for snapshot in result.artifacts.snapshots) == SESSIONS
        # 01-08 주문은 01-09에 만료한다. 01-10 재개에 체결하지 않고 새 판단의 01-11만 산다.
        assert [(fill.session, fill.quantity) for fill in result.artifacts.fills] == [
            (SESSIONS[4], "9")
        ]
        updates = result.artifacts.order_updates
        assert updates is not None
        assert any(
            update.session == SESSIONS[2] and update.status == "cancelled" for update in updates
        )
        assert result.artifacts.snapshots[-1].equity == pytest.approx(1000 - 990 * fee_bps / 10000)
    assert results[0].artifacts == results[1].artifacts
    # 같은 요청을 새 엔진으로 재실행해도 만료 주문이나 비용이 되살아나지 않는다.
    assert _execute(
        _request(core, benchmark=False, empty_sessions=(SESSIONS[2],), fee_bps=fee_bps)
    ).artifacts == results[0].artifacts


@pytest.mark.parametrize("core", CORES)
def test_calendar_preserves_empty_non_decision_sessions_and_entirely_empty_market(
    core: ExecutionCore,
) -> None:
    request = _request(core, benchmark=False, empty_sessions=SESSIONS, fee_bps=100)
    assert SESSIONS[0] not in {
        day
        for frame in request.target_tape.frames
        for day in (frame.signal_as_of, frame.execution_on)
    }
    result = _execute(request)
    assert tuple(snapshot.session for snapshot in result.artifacts.snapshots) == SESSIONS
    assert all(snapshot.equity == 1000 for snapshot in result.artifacts.snapshots)
    assert result.artifacts.orders == result.artifacts.fills == result.artifacts.costs == ()


@pytest.mark.parametrize("sessions", [(SESSIONS[1], SESSIONS[0]), (SESSIONS[0], SESSIONS[0])])
def test_dataset_rejects_non_increasing_calendar(sessions: tuple[date, ...]) -> None:
    dataset = _request(ExecutionCore.PYTHON, benchmark=False, empty_sessions=(), fee_bps=0).dataset
    with pytest.raises(ValueError, match="strictly increasing.*snapshot="):
        replace(dataset, sessions=sessions)


def test_dataset_rejects_bars_outside_calendar() -> None:
    dataset = _request(ExecutionCore.PYTHON, benchmark=False, empty_sessions=(), fee_bps=0).dataset
    with pytest.raises(ValueError, match="outside_sessions=.*calendar_sessions="):
        replace(dataset, sessions=SESSIONS[1:])


@pytest.mark.parametrize("core", CORES)
def test_held_position_keeps_previous_mark_on_empty_session(core: ExecutionCore) -> None:
    result = _execute(
        _request(core, benchmark=False, empty_sessions=(SESSIONS[3],), fee_bps=0)
    )
    snapshots = result.artifacts.snapshots
    assert tuple(snapshot.session for snapshot in snapshots) == SESSIONS
    # 01-09에 산 10주는 01-10에 bar가 없어도 보유·직전 종가 평가를 유지한다.
    assert [snapshot.equity for snapshot in snapshots] == [1000, 1000, 1000, 1000, 1100]
    assert [(fill.session, fill.quantity) for fill in result.artifacts.fills] == [
        (SESSIONS[2], "10")
    ]
    suspended = [
        position for position in result.artifacts.positions if position.session == SESSIONS[3]
    ]
    assert [(position.quantity, position.market_price) for position in suspended] == [("10", 100)]
