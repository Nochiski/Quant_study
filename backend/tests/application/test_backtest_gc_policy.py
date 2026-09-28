"""이슈 #196: 백테스트 run 스레드 구간에서만 전체 수집(2세대)을 미루고, 끝나면 되돌린다.

GC 임계값은 프로세스 전역이라 HTTP 요청과 공유된다. 그래서 (1) 구간이 끝나면 들어가기 전
설정으로 돌아오고, (2) 예외가 나도 돌아오며, (3) run 두 개가 겹치면 마지막 run 이 끝날 때만
돌아와야 한다. 서비스 경로에서는 run 이 도는 동안 미뤄져 있다가 종결 뒤 복원되는지를 본다.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Event, Thread

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run._gc_policy import (
    SUSPENDED_FULL_COLLECTION_THRESHOLD,
    full_collections_suspended,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestExecutionRequest,
    CancellationCheck,
    ProgressCallback,
    RunCancelledError,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, ExecutionCore
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import wait_for_terminal_run

# 기본값(700, 10, 10)과 다른 값으로 시작해, 복원이 "기본값으로 되돌리기"가 아니라 "들어가기 전
# 값으로 되돌리기"임을 구분한다.
ORIGINAL_THRESHOLD = (650, 9, 11)
WINDOW = (date(2024, 1, 8), date(2024, 1, 12))


@pytest.fixture(autouse=True)
def _known_threshold() -> Iterator[None]:
    saved = gc.get_threshold()
    gc.set_threshold(*ORIGINAL_THRESHOLD)
    try:
        yield
    finally:
        gc.set_threshold(*saved)


def _suspended() -> tuple[int, int, int]:
    return (ORIGINAL_THRESHOLD[0], ORIGINAL_THRESHOLD[1], SUSPENDED_FULL_COLLECTION_THRESHOLD)


def test_scope_defers_only_full_collections_and_restores_the_previous_threshold() -> None:
    with full_collections_suspended():
        # 0·1세대(젊은 객체) 수집은 그대로 돌아 짧게 사는 순환 쓰레기는 계속 치운다.
        assert gc.get_threshold() == _suspended()
        assert gc.isenabled()

    assert gc.get_threshold() == ORIGINAL_THRESHOLD


def test_scope_restores_the_threshold_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError, match="boom"), full_collections_suspended():
        raise RuntimeError("boom")

    assert gc.get_threshold() == ORIGINAL_THRESHOLD


def test_overlapping_scopes_restore_only_after_the_last_one_exits() -> None:
    first_entered = Event()
    release_first = Event()
    observed: list[tuple[int, int, int]] = []

    def first_run() -> None:
        with full_collections_suspended():
            first_entered.set()
            if not release_first.wait(timeout=10):
                raise TimeoutError("first scope was not released")

    thread = Thread(target=first_run, name="gc-scope-first")
    thread.start()
    assert first_entered.wait(timeout=10)

    with full_collections_suspended():
        release_first.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        # 먼저 들어온 run 이 끝나도 아직 도는 run 이 있으므로 미룬 상태가 유지된다.
        observed.append(gc.get_threshold())

    assert observed == [_suspended()]
    assert gc.get_threshold() == ORIGINAL_THRESHOLD


class _RecordingExecutor:
    """실행기 호출 시점의 GC 임계값을 기록하는 테스트용 실행기.

    `failure` 가 있으면 그 예외로 실패한다. `hold` 이면 `entered` 를 올리고 `release` 까지
    기다렸다가, 그사이 취소가 들어왔으면 `RunCancelledError` 로 빠진다(엔진 단계 취소 경로).
    """

    def __init__(self, *, failure: Exception | None = None, hold: bool = False) -> None:
        self._delegate = BacktestEngineExecutorAdapter(build_default_metric_registry())
        self._failure = failure
        self._hold = hold
        self.entered = Event()
        self.release = Event()
        self.thresholds: list[tuple[int, int, int]] = []

    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult:
        self.thresholds.append(gc.get_threshold())
        if self._hold:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise TimeoutError("executor test barrier was not released")
            if cancelled():
                raise RunCancelledError("cancelled at the executor test barrier")
        if self._failure is not None:
            raise self._failure
        return self._delegate.execute(request, progress=progress, cancelled=cancelled)


# schema 1.2 문서는 기간·유니버스를 담지 않는다(lang2 P2-03). 실행 설정은 run 요청이 싣는다.
_ENVIRONMENT = RunEnvironment(start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock")


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    momentum = FactorSignal(
        factor_id="momentum_3",
        label="3세션 모멘텀",
        direction=FactorDirection.HIGH,
        weight=1.0,
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
            ),
            output_node_id="mom",
        ),
    )
    return replace(
        template,
        factors=(momentum,),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _runs(executor: _RecordingExecutor, tmp_path: Path, run_id: str) -> BacktestRunService:
    adapter = MockEquityDataAdapter.demo()
    portfolio = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )
    return BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        MockEquityDataAdapter.demo(),
        executor,
        LocalArtifactStore(tmp_path),
        new_id=lambda: run_id,
    )


def _finish(runs: BacktestRunService, run_id: str) -> str:
    """종결 상태를 기다린 뒤 run 스레드가 범위를 빠져나올 때까지 join 한다.

    종결 상태는 run 스레드가 GC 범위를 나오기 직전에 기록되므로, 상태만 보고 복원을 단언하면
    경합한다.
    """

    state = wait_for_terminal_run(runs, run_id)
    for thread in threading.enumerate():
        if thread.name == f"backtest-{run_id}":
            thread.join(timeout=10)
            assert not thread.is_alive(), f"run thread did not exit — run_id={run_id}"
    return state.status.value


def test_run_thread_defers_full_collections_until_the_run_completes(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    runs = _runs(executor, tmp_path, "gc-completed")

    runs.start(
        BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON, environment=_ENVIRONMENT)
    )
    status = _finish(runs, "gc-completed")

    assert status == "completed"
    assert executor.thresholds == [_suspended()]
    assert gc.get_threshold() == ORIGINAL_THRESHOLD


def test_failed_run_restores_the_threshold(tmp_path: Path) -> None:
    executor = _RecordingExecutor(failure=RuntimeError("engine exploded"))
    runs = _runs(executor, tmp_path, "gc-failed")

    runs.start(
        BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON, environment=_ENVIRONMENT)
    )
    status = _finish(runs, "gc-failed")

    assert status == "failed"
    assert executor.thresholds == [_suspended()]
    assert gc.get_threshold() == ORIGINAL_THRESHOLD


def test_cancelled_run_restores_the_threshold(tmp_path: Path) -> None:
    # 취소는 run 스레드 안에서 `RunCancelledError` 로 빠져 `_run` 의 범위를 지난다. 나중에 취소를
    # 스레드 밖으로 옮기는 변경이 복원을 우회하면 프로세스 전체의 전체 수집이 영구히 꺼진다.
    executor = _RecordingExecutor(hold=True)
    runs = _runs(executor, tmp_path, "gc-cancelled")

    runs.start(
        BacktestRunSpec(strategy=_spec(), core=ExecutionCore.PYTHON, environment=_ENVIRONMENT)
    )
    assert executor.entered.wait(timeout=10)
    runs.cancel("gc-cancelled")
    executor.release.set()
    status = _finish(runs, "gc-cancelled")

    assert status == "cancelled"
    assert executor.thresholds == [_suspended()]
    assert gc.get_threshold() == ORIGINAL_THRESHOLD
