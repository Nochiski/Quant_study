"""worker 프로세스 run 의 끝: 취소·고장·부모 사망·판본·서버 종료·e2e hold(W1-03, spec D5·D6·D11).

실제 하위 프로세스를 쓴다. 단계마다 멈추는 worker(`tests/run_worker_support.hook_command`)는 협조적
취소가 닿지 않는 자리에서 잠들어, 프로세스 종료만이 그 run 을 끝낼 수 있다. 산출물 루트는 tmp 를
명시한다(하위 프로세스가 여는 경로는 감사 훅이 보지 못한다, testing.md).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteBacktestRunRepository,
)
from strategy_workbench.adapters.outbound.run_worker_process.facade.execution import (
    WORKER_COMMAND,
    ProcessRunExecution,
    WorkerSettings,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    RunCancelled,
    RunCommitted,
    RunExecutionPort,
    RunFailed,
    RunHandle,
    RunJob,
    RunListener,
    RunOutcome,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import RunStage, RunStatus
from tests.backtest_run_wait import wait_for_terminal_run
from tests.run_worker_support import (
    TIMEOUT_S,
    hook_command,
    parent_versions,
    wait_for_exit,
    wait_for_marker,
)
from tests.test_run_worker_protocol import resolved_job

# V-4: 취소 요청부터 `cancelled` 까지 어느 단계에서든 2초 이하.
_CANCEL_LIMIT_S = 2.0
# 계산이 실행기를 부르기 직전에 보내는 진행 설명(`_computation.py`).
_ENGINE_START = "Running backtest engine"
_BACKEND = Path(__file__).resolve().parents[2]


def _service(
    tmp_path: Path,
    command: list[str] | tuple[str, ...] = WORKER_COMMAND,
    *,
    wrap: type[_CommitBarrier] | None = None,
) -> tuple[BacktestRunService, Path]:
    container = build_container(artifact_root=tmp_path / "unused")
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)

    def transport(_computation: object) -> RunExecutionPort:
        execution = ProcessRunExecution(
            WorkerSettings("mock", None, root),
            versions=parent_versions(),
            discard_unfinished=store.discard_unfinished,
            command=command,
        )
        return execution if wrap is None else wrap(execution)

    service = BacktestRunService(
        container.portfolio_design,
        container.strategy_repository,
        cast(BacktestDataPort, container.equity_data),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        store,
        run_repository=SQLiteBacktestRunRepository(),
        new_id=lambda: str(uuid4()),
        run_execution=transport,
    )
    return service, root


def _request() -> BacktestRunSpec:
    job = resolved_job("unused")
    return BacktestRunSpec(
        strategy=job.spec.strategy,
        environment=RunEnvironment(
            start=date(2026, 1, 2), end=date(2026, 2, 20), universe_id="krx.common-stock"
        ),
        benchmark_security_id="sec-005930-1",
    )


def _leftovers(root: Path, run_id: str) -> list[str]:
    """그 run 의 staging·결과 디렉터리 이름(정리 동작을 보려고 테스트가 만든 tmp 를 읽는다)."""
    return sorted(path.name for path in root.iterdir() if run_id in path.name)


@pytest.mark.parametrize("stage", ["tape", "data", "engine", "artifact", "artifact_renamed"])
def test_cancel_ends_the_run_within_two_seconds_at_every_stage(tmp_path: Path, stage: str) -> None:
    """V-4: 끊을 수 없는 자리에서도 취소는 프로세스 종료로 2초 안에 `cancelled` 다(D5).

    commit 중(staging)이든 rename 뒤든 그 run 의 산출물 흔적이 남지 않는다.
    """
    marker = tmp_path / "marker"
    service, root = _service(tmp_path, hook_command(stage, marker))
    run_id = service.start(_request()).run.run_id
    wait_for_marker(marker)

    requested = time.monotonic()
    service.cancel(run_id)
    state = wait_for_terminal_run(service, run_id, timeout_s=_CANCEL_LIMIT_S)
    elapsed = time.monotonic() - requested

    print(f"cancel latency stage={stage} seconds={elapsed:.3f}")
    assert state.status is RunStatus.CANCELLED, state
    assert elapsed <= _CANCEL_LIMIT_S
    assert _leftovers(root, run_id) == []


def test_a_killed_worker_fails_the_run_and_its_renamed_result_is_removed(tmp_path: Path) -> None:
    """worker 강제 종료는 `worker_exited` 이고, 이미 rename 된 결과 디렉터리도 지운다(D5 2·3)."""
    marker = tmp_path / "marker"
    service, root = _service(tmp_path, hook_command("artifact_renamed", marker))
    run_id = service.start(_request()).run.run_id
    pid = wait_for_marker(marker)
    assert (root / run_id).exists()

    os.kill(pid, signal.SIGTERM)
    state = wait_for_terminal_run(service, run_id, timeout_s=TIMEOUT_S)

    assert state.status is RunStatus.FAILED, state
    assert state.error_code == "backtest.run.worker_exited"
    assert run_id in (state.error or "") and "exit_code=" in (state.error or "")
    assert "last_stage=artifact" in (state.error or "")
    assert _leftovers(root, run_id) == []


class _CommitBarrier:
    """부모 쪽 경합 장치: 감독 스레드가 `committed` 를 받은 뒤 서비스 잠금을 잡기 전에 멈춘다.

    worker 안의 commit 에는 같은 프로세스 barrier 를 걸 수 없어 부모 쪽에서 재현한다(WORKFLOW
    W1-03).
    """

    instances: list[_CommitBarrier] = []

    def __init__(self, inner: ProcessRunExecution) -> None:
        self._inner = inner
        self.entered = threading.Event()
        self.release = threading.Event()
        _CommitBarrier.instances.append(self)

    def start(self, job: RunJob, listener: RunListener) -> RunHandle:
        return self._inner.start(job, _BarrierListener(listener, self))

    def shutdown(self) -> None:
        self._inner.shutdown()


class _BarrierListener:
    def __init__(self, inner: RunListener, barrier: _CommitBarrier) -> None:
        self._inner = inner
        self._barrier = barrier

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        self._inner.on_progress(stage, fraction, message)

    def on_finished(self, outcome: RunOutcome) -> None:
        if isinstance(outcome, RunCommitted):
            self._barrier.entered.set()
            if not self._barrier.release.wait(TIMEOUT_S):
                raise TimeoutError("commit barrier was not released")
        self._inner.on_finished(outcome)


def test_cancel_accepted_after_committed_wins_and_removes_the_result(tmp_path: Path) -> None:
    """D5 4: `committed` 뒤 잠금 전에 취소가 들어오면 `cancelled` 이고 결과 디렉터리는 없다."""
    _CommitBarrier.instances.clear()
    service, root = _service(tmp_path, wrap=_CommitBarrier)
    run_id = service.start(_request()).run.run_id
    barrier = _CommitBarrier.instances[0]
    assert barrier.entered.wait(TIMEOUT_S)
    assert (root / run_id).exists()

    service.cancel(run_id)
    barrier.release.set()
    state = wait_for_terminal_run(service, run_id, timeout_s=TIMEOUT_S)

    assert state.status is RunStatus.CANCELLED, state
    assert state.artifact_sha256 is None
    assert _leftovers(root, run_id) == []


def test_a_worker_with_other_versions_fails_the_run_as_a_mismatch(tmp_path: Path) -> None:
    """D6: 판본 묶음이 다르면 `worker_mismatch` 로 닫고 어긋난 항목과 재시작 안내를 싣는다."""
    listener = _Recorder()
    execution = ProcessRunExecution(
        WorkerSettings("mock", None, tmp_path / "artifacts"),
        versions=replace(parent_versions(), snapshot_id="other", code_fingerprint="other"),
        discard_unfinished=LocalArtifactStore(tmp_path / "artifacts").discard_unfinished,
    )
    execution.start(resolved_job("run-mismatch"), listener)

    outcome = listener.wait()
    assert isinstance(outcome, RunFailed)
    assert outcome.error_code == "backtest.run.worker_mismatch"
    assert "mismatched=snapshot_id,code_fingerprint" in outcome.error
    assert "restart the server" in outcome.error
    assert _leftovers(tmp_path / "artifacts", "run-mismatch") == []


class _Recorder:
    def __init__(self) -> None:
        self.stages: list[RunStage] = []
        self.messages: list[str] = []
        self.outcomes: list[RunOutcome] = []
        self.finished = threading.Event()

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        self.stages.append(stage)
        self.messages.append(message)

    def on_finished(self, outcome: RunOutcome) -> None:
        self.outcomes.append(outcome)
        self.finished.set()

    def wait(self) -> RunOutcome:
        assert self.finished.wait(TIMEOUT_S)
        return self.outcomes[0]


class _Gate:
    """부모 쪽 e2e hold 판정(`_TrialHold.release` 자리). 열 때까지 붙잡고, 취소면 거짓이다."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.open = threading.Event()

    def __call__(self, run_id: str, cancelled: object) -> bool:
        self.entered.set()
        while not self.open.wait(0.05):
            if callable(cancelled) and cancelled():
                return False
        return True


def _gated(tmp_path: Path, gate: _Gate) -> ProcessRunExecution:
    root = tmp_path / "artifacts"
    return ProcessRunExecution(
        WorkerSettings("mock", None, root, engine_gate=True),
        versions=parent_versions(),
        discard_unfinished=LocalArtifactStore(root).discard_unfinished,
        engine_gate=gate,
    )


def test_engine_gate_holds_the_worker_before_the_engine_until_the_parent_proceeds(
    tmp_path: Path,
) -> None:
    """D11: worker 는 엔진 단계 앞에서 `engine_ready` 를 보내고 부모의 `proceed` 를 기다린다."""
    gate = _Gate()
    listener = _Recorder()
    _gated(tmp_path, gate).start(resolved_job("run-held"), listener)
    assert gate.entered.wait(TIMEOUT_S)
    time.sleep(0.3)
    # 엔진 단계 시작 표시까지만 왔고(스레드 전송의 hold 와 같은 자리) 엔진은 아직 돌지 않았다.
    assert listener.messages[-1] == _ENGINE_START and not listener.finished.is_set()
    held = len(listener.messages)

    gate.open.set()

    assert isinstance(listener.wait(), RunCommitted)
    assert "engine" in listener.stages[held:]


def test_a_held_run_is_cancelled_without_reaching_the_engine(tmp_path: Path) -> None:
    gate = _Gate()
    listener = _Recorder()
    handle = _gated(tmp_path, gate).start(resolved_job("run-held-cancel"), listener)
    assert gate.entered.wait(TIMEOUT_S)

    requested = time.monotonic()
    handle.cancel()

    assert isinstance(listener.wait(), RunCancelled)
    assert time.monotonic() - requested <= _CANCEL_LIMIT_S
    assert listener.messages[-1] == _ENGINE_START


def test_the_engine_gate_is_set_on_both_sides_or_not_at_all(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="set together"):
        ProcessRunExecution(
            WorkerSettings("mock", None, tmp_path, engine_gate=True),
            versions=parent_versions(),
            discard_unfinished=lambda _run_id: None,
        )


def test_server_shutdown_ends_running_workers_without_closing_their_runs(tmp_path: Path) -> None:
    """D5 서버 정상 종료: 도는 worker 를 끝내고 감독 스레드를 기다린다. 기록은 다음 부팅이
    닫는다."""
    marker = tmp_path / "marker"
    service, root = _service(tmp_path, hook_command("engine", marker))
    run_id = service.start(_request()).run.run_id
    pid = wait_for_marker(marker)

    service.shutdown()

    assert wait_for_exit(pid, timeout_s=5.0)
    assert not any(thread.name == f"backtest-worker-{run_id}" for thread in threading.enumerate())
    assert not service.state(run_id).status.is_terminal
    assert _leftovers(root, run_id) == []


def test_http_lifespan_shutdown_reaches_the_run_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(BacktestRunService, "shutdown", lambda self: calls.append("shutdown"))

    with TestClient(build_http_app(artifact_root=tmp_path / "artifacts", run_isolation="process")):
        assert calls == []

    assert calls == ["shutdown"]


_ORPHAN_PARENT = r"""
import sys, time
from pathlib import Path
from strategy_workbench.adapters.outbound.run_worker_process.facade.execution import (
    ProcessRunExecution, WorkerSettings,
)
from tests.run_worker_support import hook_command, parent_versions
from tests.test_run_worker_protocol import resolved_job

class Listener:
    def on_progress(self, *args):
        pass
    def on_finished(self, outcome):
        pass

root, stage, marker = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
ProcessRunExecution(
    WorkerSettings("mock", None, root),
    versions=parent_versions(),
    discard_unfinished=lambda run_id: None,
    command=hook_command(stage, marker),
).start(resolved_job("run-orphan"), Listener())
time.sleep(600)
"""


@pytest.mark.parametrize(
    "stage",
    [
        # stdin 감시가 도는 자리: Windows 는 Job Object, POSIX 는 stdin EOF 가 끝낸다.
        "engine",
        # 감시 스레드가 뜨기 전: OS 의 Job Object 만 끝낼 수 있다.
        pytest.param(
            "before_main",
            marks=pytest.mark.skipif(
                sys.platform != "win32", reason="Job Object 는 Windows 전용이다(POSIX 는 EOF 경로)"
            ),
        ),
    ],
)
def test_killing_the_parent_process_ends_its_worker(tmp_path: Path, stage: str) -> None:
    """D5 부모 사망: worker 를 띄운 중간 부모 프로세스를 실제로 죽이면 worker 도 끝난다.

    stdin 을 닫는 것으로 대신하지 않는다(WORKFLOW W1-03).
    """
    marker = tmp_path / "marker"
    parent = subprocess.Popen(
        [sys.executable, "-c", _ORPHAN_PARENT, str(tmp_path / "artifacts"), stage, str(marker)],
        cwd=_BACKEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid = wait_for_marker(marker)
    finally:
        parent.kill()
        parent.wait(TIMEOUT_S)

    killed = time.monotonic()
    ended = wait_for_exit(pid, timeout_s=15.0)
    print(f"orphan worker stage={stage} ended={ended} seconds={time.monotonic() - killed:.3f}")
    if not ended:
        os.kill(pid, signal.SIGTERM)
    assert ended
