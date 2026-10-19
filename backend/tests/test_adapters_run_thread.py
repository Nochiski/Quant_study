"""스레드 전송(`run_thread`)이 run 하나를 같은 프로세스에서 돌리고 진행·종결을 알리는지(spec D13).

계산은 가짜로 바꿔 전송만 본다: 스레드 이름·데몬 여부, 진행·종결 알림의 순서, 손잡이 취소,
GC 구간, 띄우지 못할 때의 예외. 계산 자체는 `application/test_run_computation.py` 가 본다.
"""

from __future__ import annotations

import gc
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import cast

import pytest

from strategy_workbench.adapters.outbound.run_thread.facade.execution import ThreadRunExecution
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.computation import RunComputation
from strategy_workbench.application.backtest_run.facade.ports import (
    RunCancelled,
    RunCommitted,
    RunFailed,
    RunHeadline,
    RunJob,
    RunOutcome,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    ResolvedBacktestRunSpec,
    RunStage,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.strategy.facade.specification import strategy_spec_hash

_TIMEOUT_S = 10.0
_Progress = Callable[[RunStage, float, str], None]
_Cancelled = Callable[[], bool]


def _job(run_id: str) -> RunJob:
    strategy = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    environment = RunEnvironment(
        start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
    )
    spec = BacktestRunSpec(strategy=strategy, environment=environment)
    return RunJob(
        run_id=run_id,
        spec=ResolvedBacktestRunSpec.from_request(
            spec, strategy=strategy, environment=environment, parameter_values={}
        ),
        provenance=StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=strategy_spec_hash(strategy),
            schema_version=strategy.identity.schema_version,
        ),
        strategy_source=None,
        lineage_strategy_id=None,
    )


@dataclass
class _Listener:
    """받은 알림과 그때의 스레드·GC 임계값을 남긴다."""

    progress: list[tuple[RunStage, float, str, str]] = field(default_factory=list)
    outcomes: list[RunOutcome] = field(default_factory=list)
    finished_on: list[str] = field(default_factory=list)
    finished: threading.Event = field(default_factory=threading.Event)

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        self.progress.append((stage, fraction, message, threading.current_thread().name))

    def on_finished(self, outcome: RunOutcome) -> None:
        self.outcomes.append(outcome)
        self.finished_on.append(threading.current_thread().name)
        self.finished.set()


class _Computation:
    """`RunComputation.compute` 자리에 들어가는 가짜 계산."""

    def __init__(
        self,
        body: Callable[[_Progress, _Cancelled], RunOutcome],
    ) -> None:
        self._body = body
        self.daemon: bool | None = None
        self.threshold: tuple[int, int, int] | None = None

    def compute(
        self,
        job: RunJob,
        *,
        progress: _Progress,
        cancelled: _Cancelled,
    ) -> RunOutcome:
        self.daemon = threading.current_thread().daemon
        self.threshold = gc.get_threshold()
        return self._body(progress, cancelled)


def _execution(computation: _Computation) -> ThreadRunExecution:
    return ThreadRunExecution(cast(RunComputation, computation))


_COMMITTED = RunCommitted(
    sha256="0" * 64,
    session_sharpe=0.1,
    metric_registry_version="registry-v1",
    headline=RunHeadline(total_return=0.2, sharpe=1.0, max_drawdown=-0.1),
)


def test_progress_then_the_outcome_arrive_on_the_run_thread() -> None:
    def body(progress: _Progress, _: _Cancelled) -> RunOutcome:
        progress("tape", 0.02, "Compiling target tape")
        progress("engine", 0.9, "Running backtest engine")
        return _COMMITTED

    computation = _Computation(body)
    listener = _Listener()
    before = gc.get_threshold()

    _execution(computation).start(_job("run-progress"), listener)

    assert listener.finished.wait(_TIMEOUT_S)
    assert [item[:3] for item in listener.progress] == [
        ("tape", 0.02, "Compiling target tape"),
        ("engine", 0.9, "Running backtest engine"),
    ]
    assert {item[3] for item in listener.progress} == {"backtest-run-progress"}
    assert listener.finished_on == ["backtest-run-progress"]
    assert listener.outcomes == [_COMMITTED]
    assert computation.daemon is True
    # 계산 구간에서만 전체 수집을 미룬다(#196). 종결 알림 때는 이미 되돌아와 있다.
    assert computation.threshold is not None and computation.threshold[2] != before[2]
    assert gc.get_threshold() == before


def test_a_failure_value_passes_through_unchanged() -> None:
    failure = RunFailed("backtest.run.no_positions", "NoPositionsError: x")
    listener = _Listener()

    _execution(_Computation(lambda _progress, _cancelled: failure)).start(
        _job("run-failed"), listener
    )

    assert listener.finished.wait(_TIMEOUT_S)
    assert listener.outcomes == [failure]


def test_cancelling_the_handle_raises_the_cooperative_signal() -> None:
    entered = threading.Event()

    def body(_: _Progress, cancelled: _Cancelled) -> RunOutcome:
        entered.set()
        deadline = time.monotonic() + _TIMEOUT_S
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        return RunCancelled() if cancelled() else _COMMITTED

    listener = _Listener()
    handle = _execution(_Computation(body)).start(_job("run-cancel"), listener)
    assert entered.wait(_TIMEOUT_S)

    handle.cancel()

    assert listener.finished.wait(_TIMEOUT_S)
    assert listener.outcomes == [RunCancelled()]


def test_a_thread_that_cannot_start_raises_and_never_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Unstartable(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(
        "strategy_workbench.adapters.outbound.run_thread._execution.Thread", _Unstartable
    )
    listener = _Listener()

    with pytest.raises(RuntimeError, match="can't start new thread"):
        _execution(_Computation(lambda _p, _c: _COMMITTED)).start(_job("run-x"), listener)

    assert listener.outcomes == []
    assert listener.progress == []


def test_shutdown_leaves_running_daemon_threads_alone() -> None:
    release = threading.Event()

    def body(_: _Progress, __: _Cancelled) -> RunOutcome:
        release.wait(_TIMEOUT_S)
        return _COMMITTED

    listener = _Listener()
    execution = _execution(_Computation(body))
    execution.start(_job("run-shutdown"), listener)

    execution.shutdown()
    release.set()

    assert listener.finished.wait(_TIMEOUT_S)
    assert listener.outcomes == [_COMMITTED]
