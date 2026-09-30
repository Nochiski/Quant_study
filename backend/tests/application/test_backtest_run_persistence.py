"""run 상태 전이의 저장 규칙(검증 랩 spec D3, V1-03).

- 접수·취소 요청·취소·실패 같은 전이가 파일에 남아, 저장소를 다시 열면(재시작) 같은 상태·실패
  코드다.
- 접수 저장이 실패하면 run 을 접수하지 않는다. 전이 저장이 실패하면 로그만 남기고 run 은 끝까지
  돈다.
- 목록은 저장소가 정하되, 이 프로세스가 도는 run 은 메모리 상태(진행률·단계)로 덮는다.

타이밍에 기대지 않도록 원시 관측 로딩 입구에서 멈추는 포트(`RawLoadBarrier`)로 run 을 붙잡는다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    ResearchStorageError,
    SQLiteBacktestRunRepository,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestRunRepositoryPort,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunNotFoundError,
    BacktestRunService,
    BacktestRunSpec,
    BacktestRunState,
    RunStatus,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    RawObservationUnavailableError,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore, StrategyProvenance
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.strategy.facade.specification import RebalanceFrequency
from tests.backtest_run_wait import RawLoadBarrier, wait_for_terminal_run

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


def _request(end: date = _ENVIRONMENT.end) -> BacktestRunSpec:
    # 끝 날짜로 입력을 갈라 같은 입력 잇기(#161)에 묶이지 않게 한다. 세션마다 리밸런싱해야 짧은
    # 구간에서도 포지션이 생긴다.
    template = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    return BacktestRunSpec(
        strategy=replace(
            template,
            portfolio=replace(
                template.portfolio,
                rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
                rebalance_every_n_sessions=1,
            ),
        ),
        core=ExecutionCore.PYTHON,
        environment=replace(_ENVIRONMENT, end=end),
    )


@pytest.fixture
def barriers() -> Iterator[list[RawLoadBarrier]]:
    """테스트가 붙잡은 입구를 끝에 모두 연다(단언이 먼저 실패해도 run 스레드가 남지 않게)."""
    opened: list[RawLoadBarrier] = []
    yield opened
    for barrier in opened:
        barrier.release.set()


def _service(
    tmp_path: Path,
    repository: BacktestRunRepositoryPort,
    barrier: RawLoadBarrier,
    *run_ids: str,
) -> BacktestRunService:
    return BacktestRunService(
        PortfolioDesignService(
            barrier,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=MockEquityDataAdapter.demo(),
            factor_registry_version="test-registry",
        ),
        InMemoryStrategyRepository(),
        MockEquityDataAdapter.demo(),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path / "artifacts"),
        run_repository=repository,
        new_id=iter(run_ids).__next__,
        run_slots=1,
    )


def _open_barrier(
    barriers: list[RawLoadBarrier], *, failure: Exception | None = None
) -> RawLoadBarrier:
    barrier = RawLoadBarrier(MockEquityDataAdapter.demo(), failure=failure)
    barriers.append(barrier)
    return barrier


def _stored(path: Path, run_id: str) -> BacktestRunState:
    """다시 연 저장소(재시작)가 읽는 상태."""
    return SQLiteBacktestRunRepository(path).get(run_id).run


def test_cancel_and_failure_transitions_survive_reopening_the_file(
    tmp_path: Path, barriers: list[RawLoadBarrier]
) -> None:
    path = tmp_path / "research.sqlite3"
    held = _open_barrier(barriers)
    runs = _service(tmp_path, SQLiteBacktestRunRepository(path), held, "running", "waiting")
    runs.start(_request())
    assert held.entered.wait(timeout=10)
    runs.start(_request(end=date(2024, 1, 11)))
    assert _stored(path, "running").status is RunStatus.RUNNING
    assert _stored(path, "waiting").status is RunStatus.QUEUED

    runs.cancel("waiting")
    runs.cancel("running")
    assert _stored(path, "waiting").status is RunStatus.CANCELLED
    assert _stored(path, "running").status is RunStatus.CANCEL_REQUESTED
    held.release.set()
    assert wait_for_terminal_run(runs, "running").status is RunStatus.CANCELLED
    assert _stored(path, "running").status is RunStatus.CANCELLED

    failure = RawObservationUnavailableError(DataLoadStatus.NO_DATA, "no members in universe")
    failing = _open_barrier(barriers, failure=failure)
    failing.release.set()
    runs = _service(tmp_path, SQLiteBacktestRunRepository(path), failing, "failed")
    runs.start(_request())
    assert wait_for_terminal_run(runs, "failed").status is RunStatus.FAILED

    stored = _stored(path, "failed")
    assert (stored.status, stored.error_code) == (RunStatus.FAILED, "portfolio.data.unavailable")
    assert stored.error == runs.state("failed").error


class _RefusingAdd(SQLiteBacktestRunRepository):
    def add(
        self,
        run: BacktestRunState,
        provenance: StrategyProvenance,
        request: BacktestRunSpec,
        *,
        lineage_id: str | None,
        trial_key: str,
    ) -> None:
        raise ResearchStorageError(f"disk full — run_id={run.run_id}")


def test_a_failed_accept_write_rejects_the_run(
    tmp_path: Path, barriers: list[RawLoadBarrier]
) -> None:
    runs = _service(tmp_path, _RefusingAdd(), _open_barrier(barriers), "lost")

    with pytest.raises(ResearchStorageError, match="run_id=lost"):
        runs.start(_request())

    # 기록 없는 run 은 돌지 않는다 — 재시작 뒤 흔적이 없는 계산이 된다.
    with pytest.raises(BacktestRunNotFoundError):
        runs.state("lost")
    assert "backtest-lost" not in {thread.name for thread in threading.enumerate()}


class _RefusingUpdate(SQLiteBacktestRunRepository):
    def update(self, state: BacktestRunState) -> None:
        raise ResearchStorageError(f"disk full — run_id={state.run_id}")


def test_a_failed_transition_write_is_logged_and_the_run_keeps_going(
    tmp_path: Path, barriers: list[RawLoadBarrier], caplog: pytest.LogCaptureFixture
) -> None:
    repository = _RefusingUpdate()
    free = _open_barrier(barriers)
    free.release.set()
    runs = _service(tmp_path, repository, free, "run-1")

    runs.start(_request())

    assert wait_for_terminal_run(runs, "run-1").status is RunStatus.COMPLETED
    assert runs.result("run-1").manifest.run_id == "run-1"
    # 파일에는 접수 상태만 남았다. 재시작하면 `interrupted` 로 닫혀 거짓 완료가 남지 않는다.
    assert repository.get("run-1").run.status is RunStatus.QUEUED
    assert "backtest run state could not be persisted — run_id=run-1" in caplog.text


class _FrozenAfterAccept(SQLiteBacktestRunRepository):
    """접수 뒤 전이를 적지 않는 저장소. 목록이 저장소 행이 아니라 메모리 상태를 보이는지 가른다."""

    def update(self, state: BacktestRunState) -> None:
        return None


def test_the_list_shows_the_in_process_state_of_a_running_run(
    tmp_path: Path, barriers: list[RawLoadBarrier]
) -> None:
    repository = _FrozenAfterAccept()
    held = _open_barrier(barriers)
    runs = _service(tmp_path, repository, held, "run-1")
    runs.start(_request())
    assert held.entered.wait(timeout=10)

    (item,) = runs.list_runs(PageRequest()).items

    assert repository.get("run-1").run.stage == "queued"
    assert item.run == runs.state("run-1")
    assert (item.run.status, item.run.stage) == (RunStatus.RUNNING, "tape")
    assert item.run.progress > 0
