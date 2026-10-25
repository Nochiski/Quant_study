"""독립 요청 ID·단일 claim·실패 복구·현금 계좌를 실제 양 코어로 확인한다."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import date
from threading import Event
from uuid import uuid4

import pytest

from strategy_workbench.adapters.outbound.backtest_engine.facade.continuous import (
    ContinuousBacktestExecutorAdapter,
)
from strategy_workbench.adapters.outbound.continuous_account_sqlite.facade.store import (
    ContinuousAccountSqliteStore,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
)
from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountRejectedError,
    ContinuousAccountService,
    ContinuousAccountStatus,
    FrozenExperimentInput,
    FrozenWindowChoice,
)
from strategy_workbench.application.continuous_account.facade.ports import PreparedContinuousSignals
from strategy_workbench.domain.backtest.facade.environment import (
    ParticipationBasis,
    RunEnvironment,
    SellTax,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunSpec, ExecutionCore
from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.portfolio.facade.scheduled_construction import ScheduledCandidate
from tests.domain.test_scheduled_portfolio import candidate, spec
from tests.s04_account_fixture import CORES, account_bar

SESSIONS = tuple(date(2026, 10, day) for day in (5, 6, 7, 8, 9))


class _Source:
    def __init__(self, prepared: ScheduledCandidate, core: ExecutionCore) -> None:
        self.freeze_count = 0
        self.inspect_count = 0
        self.input = FrozenExperimentInput(
            "experiment",
            BacktestRunSpec(
                strategy=prepared.spec,
                core=core,
                initial_cash=1000,
                environment=RunEnvironment(
                    start=SESSIONS[0], end=SESSIONS[-1], universe_id="synthetic-only"
                ),
            ),
            prepared.spec,
            prepared.source,
            "fixture",
            1,
            (
                FrozenWindowChoice(
                    0,
                    WalkForwardWindow(
                        date(2026, 1, 1), date(2026, 10, 2), SESSIONS[0], SESSIONS[-1]
                    ),
                    0,
                    "training-run",
                    "validation-run",
                    (),
                    prepared.data_snapshot_id,
                    prepared.factor_evaluation_version,
                ),
            ),
        )

    def inspect(self, experiment_id: str) -> FrozenExperimentInput:
        self.inspect_count += 1
        return self.input

    def freeze(self, experiment_id: str) -> FrozenExperimentInput:
        self.freeze_count += 1
        return self.input


class _Pipeline:
    def __init__(self, prepared: ScheduledCandidate) -> None:
        self.prepared = prepared
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.fail = False

    def preflight(self, source: FrozenExperimentInput, environment: RunEnvironment) -> None:
        pass

    def prepare(
        self,
        source: FrozenExperimentInput,
        choice: FrozenWindowChoice,
        environment: RunEnvironment,
        *,
        progress: Callable[[float, str], None],
    ) -> PreparedContinuousSignals:
        progress(0.2, "synthetic preparation")
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("synthetic preparation timed out")
        if self.fail:
            raise RuntimeError("sensitive /private/internal/path must not reach the response")
        return PreparedContinuousSignals(self.prepared)


class _Data:
    def __init__(self, *, trading: bool = False) -> None:
        self.trading = trading

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        bars = (
            tuple(
                account_bar(
                    session,
                    security_id,
                    110 if security_id == "A" and index >= 2 else 100,
                    110
                    if (security_id == "A" and index >= 2) or (security_id == "B" and index == 4)
                    else 100,
                )
                for index, session in enumerate(SESSIONS)
                for security_id in ("A", "B")
            )
            if self.trading and query.security_ids
            else ()
        )
        return BacktestDataset("snapshot", bars, (), (), None, sessions=SESSIONS)


@pytest.mark.parametrize("core", CORES)
def test_shared_kernel_preserves_integer_rotation_fee_and_tax(core: ExecutionCore) -> None:
    prepared = candidate(spec(interval=2), SESSIONS)
    prepared = replace(
        prepared,
        observations=tuple(
            replace(
                row,
                factor_values=(
                    replace(row.factor_values[0], value=2.0 if row.security_id == "B" else 1.0),
                ),
            )
            if row.as_of >= SESSIONS[3]
            else row
            for row in prepared.observations
        ),
    )
    source = _Source(prepared, core)
    source.input = replace(
        source.input,
        base_run=replace(
            source.input.base_run,
            environment=RunEnvironment(
                start=SESSIONS[0],
                end=SESSIONS[-1],
                universe_id="synthetic-only",
                fee_bps=100,
                sell_tax=SellTax.CUSTOM,
                sell_tax_bps=200,
                slippage_bps=0,
                participation_basis=ParticipationBasis.SESSION_VOLUME,
            ),
        ),
    )
    service = ContinuousAccountService(
        source,
        _Pipeline(prepared),
        _Data(trading=True),
        ContinuousBacktestExecutorAdapter(),
        ContinuousAccountSqliteStore(None),
        data_mode="mock",
    )
    state = service.run("experiment", str(uuid4()))
    assert state.status is ContinuousAccountStatus.COMPLETED
    assert state.result is not None
    assert [
        (fill.security_id, fill.side, fill.quantity) for fill in state.result.artifacts.fills
    ] == [
        ("A", "buy", "9"),
        ("A", "sell", "9"),
        ("B", "buy", "10"),
    ]
    assert state.result.total_fees == pytest.approx(28.9)
    assert state.result.total_sell_tax == pytest.approx(19.8)
    assert state.result.summary.final_cash == pytest.approx(41.3)
    assert state.result.summary.final_equity == pytest.approx(1141.3)
    assert state.result.summary.total_return == pytest.approx(0.1413)


def _service(
    core: ExecutionCore = ExecutionCore.PYTHON,
) -> tuple[ContinuousAccountService, _Source, _Pipeline]:
    prepared = candidate(spec(interval=20), SESSIONS)
    prepared = replace(
        prepared,
        observations=tuple(replace(row, universe_member=False) for row in prepared.observations),
    )
    source = _Source(prepared, core)
    pipeline = _Pipeline(prepared)
    service = ContinuousAccountService(
        source,
        pipeline,
        _Data(),
        ContinuousBacktestExecutorAdapter(),
        ContinuousAccountSqliteStore(None),
        data_mode="mock",
    )
    return service, source, pipeline


@pytest.mark.parametrize("core", CORES)
def test_all_cash_is_completed_with_empty_market_sessions_and_no_trades(
    core: ExecutionCore,
) -> None:
    service, source, _pipeline = _service(core)
    assert service.latest("experiment").status == "not_started"
    assert source.inspect_count == 1 and source.freeze_count == 0
    request_id = str(uuid4())
    completed = service.run("experiment", request_id)
    assert completed.status is ContinuousAccountStatus.COMPLETED
    assert completed.result is not None
    assert completed.result.summary.final_cash == completed.result.summary.final_equity == 1000
    assert completed.result.summary.total_return == completed.result.summary.pnl == 0
    assert [point.equity for point in completed.result.curve] == [1000] * len(SESSIONS)
    assert completed.result.artifacts.fills == completed.result.artifacts.orders == ()
    assert completed.result.total_fees == completed.result.total_sell_tax == 0
    assert asdict(service.get(request_id)) == asdict(completed)
    assert asdict(service.run("experiment", request_id)) == asdict(completed)
    assert source.freeze_count == 1


def test_in_flight_duplicate_returns_original_and_other_request_is_busy() -> None:
    service, source, pipeline = _service()
    request_id = str(uuid4())
    pipeline.release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.run, "experiment", request_id)
        assert pipeline.entered.wait(timeout=5)
        original = service.run("experiment", request_id)
        assert original.status is ContinuousAccountStatus.RUNNING
        assert original.progress > 0
        assert source.freeze_count == 1
        with pytest.raises(ContinuousAccountRejectedError) as busy:
            service.run("experiment", str(uuid4()))
        assert busy.value.code == "continuous.busy"
        pipeline.release.set()
        completed = pending.result(timeout=10)
    # POST 응답을 받지 못해도 같은 ID 조회·재전송은 같은 완료 이력만 돌려준다.
    assert asdict(service.get(request_id)) == asdict(completed)
    assert asdict(service.run("experiment", request_id)) == asdict(completed)
    with pytest.raises(ContinuousAccountRejectedError) as conflict:
        service.run("other-experiment", request_id)
    assert conflict.value.code == "continuous.request_conflict"


def test_failure_is_durable_redacted_and_duplicate_never_reexecutes() -> None:
    service, source, pipeline = _service()
    pipeline.fail = True
    request_id = str(uuid4())
    failed = service.run("experiment", request_id)
    assert failed.status is ContinuousAccountStatus.FAILED
    assert failed.result is None
    assert failed.error_code == "continuous.execution_failed"
    assert failed.error_detail is not None and "/private" not in failed.error_detail
    pipeline.fail = False
    assert service.run("experiment", request_id) == failed
    assert source.freeze_count == 1
    assert service.run("experiment", str(uuid4())).status is ContinuousAccountStatus.COMPLETED
