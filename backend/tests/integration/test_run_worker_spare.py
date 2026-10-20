"""예비 worker 의 수명(run-worker spec D7, W2-01): 쓰기·채우기·판본·고장·종료·결과 동일성.

실제 하위 프로세스를 쓴다. `record` worker(`tests/run_worker_support.hook_command`)는 띄워짐·
job 대기·job 받음을 표시 파일로 남겨, 어느 worker 가 예비였고 어느 run 이 그것을 썼는지 본다.
산출물 루트는 tmp 를 명시한다(하위 프로세스가 여는 경로는 감사 훅이 보지 못한다, testing.md).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.run_worker_process.facade.execution import (
    ProcessRunExecution,
    WorkerSettings,
)
from strategy_workbench.adapters.outbound.run_worker_process.facade.protocol import (
    VersionBundle,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    RunCancelled,
    RunCommitted,
    RunFailed,
    RunOutcome,
)
from strategy_workbench.bootstrap import _container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.runs import RunStage
from tests.integration.test_run_worker_process import _body, _result, _run
from tests.run_worker_support import (
    TIMEOUT_S,
    hook_command,
    parent_versions,
    records,
    wait_for_exit,
    wait_for_marker,
    wait_for_records,
)
from tests.test_run_worker_protocol import resolved_job

# V-5: 예비가 있을 때 배정부터 첫 tape 진행까지 ≤ 0.5초. 느린 CI 여유를 둔 상한이다 — 예비를
# 쓰지 않고 띄우면 부팅(수 초)이 들어 이 상한을 넘는다.
_SPARE_START_LIMIT_S = 2.0
# 예비가 더 생기지 않는지 지켜보는 시간. mock worker 부팅(약 2~3초)보다 길다.
_QUIET_S = 6.0
# V-4: 취소(와 서버 종료)는 부팅을 기다리지 않는다.
_CANCEL_LIMIT_S = 2.0


def _execution(
    tmp_path: Path,
    folder: Path,
    *,
    versions: VersionBundle | None = None,
    command: list[str] | None = None,
) -> ProcessRunExecution:
    root = tmp_path / "artifacts"
    return ProcessRunExecution(
        WorkerSettings("mock", None, root),
        versions=versions or parent_versions(),
        discard_unfinished=LocalArtifactStore(root).discard_unfinished,
        command=command or hook_command("record", folder),
    )


def _wait_until(condition: Callable[[], object], *, timeout_s: float = TIMEOUT_S) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if value := condition():
            return value
        time.sleep(0.05)
    raise AssertionError(f"condition did not hold within {timeout_s}s")


def _ready_spare(
    execution: ProcessRunExecution, folder: Path, *, known: frozenset[int] = frozenset()
) -> tuple[int, int]:
    """부모가 `ready` 를 받아 판본을 대조한 예비: (부모가 띄운 프로세스 pid, worker 의 pid).

    worker 의 pid 는 `known` 밖에서 새로 job 을 기다리기 시작한 `record` worker 다. Windows venv 의
    python.exe 는 실제 인터프리터를 자식으로 띄우는 런처라 두 pid 가 다를 수 있다 — 런처를 끝내면
    인터프리터도 끝난다.
    """

    def ready() -> tuple[int, int] | None:
        handle = execution.spare_pid()
        fresh = records(folder, "idle") - known
        return (handle, next(iter(fresh))) if handle is not None and len(fresh) == 1 else None

    spare: tuple[int, int] = _wait_until(ready)
    return spare


def _spare_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "backtest-worker-spare"]


class _Recorder:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.first_tape: float | None = None
        self.outcomes: list[RunOutcome] = []
        self.finished = threading.Event()

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        if stage == "tape" and self.first_tape is None:
            self.first_tape = time.perf_counter() - self.started

    def on_finished(self, outcome: RunOutcome) -> None:
        self.outcomes.append(outcome)
        self.finished.set()

    def wait(self) -> RunOutcome:
        assert self.finished.wait(TIMEOUT_S)
        return self.outcomes[0]


def test_a_run_takes_the_ready_spare_and_the_next_spare_starts_at_once(tmp_path: Path) -> None:
    """D7: 준비된 예비에 job 을 주고 곧바로 새 예비를 띄운다. V-5: 첫 tape 진행이 부팅 없이 온다."""
    folder = tmp_path / "records"
    execution = _execution(tmp_path, folder)
    _, spare = _ready_spare(execution, folder)

    listener = _Recorder()
    execution.start(resolved_job("run-spare"), listener)

    assert isinstance(listener.wait(), RunCommitted)
    print(f"spare start latency seconds={listener.first_tape:.3f}")
    assert listener.first_tape is not None and listener.first_tape < _SPARE_START_LIMIT_S
    assert records(folder, "job") == {spare}
    # 다음 예비는 run 이 끝나기를 기다리지 않고 떴고, 그것이 지금의 예비다.
    (following,) = wait_for_records(folder, "idle", 2) - {spare}
    assert _ready_spare(execution, folder, known=frozenset({spare}))[1] == following


def test_two_runs_at_once_use_the_spare_and_one_own_worker(tmp_path: Path) -> None:
    """D7: 예비는 슬롯 수와 무관하게 하나다. 동시에 든 둘째 run 은 자기 worker 를 띄운다.

    worker 는 도는 run 수 + 1(예비)을 넘지 않는다.
    """
    folder = tmp_path / "records"
    execution = _execution(tmp_path, folder)
    _, spare = _ready_spare(execution, folder)

    listeners = [_Recorder(), _Recorder()]
    for index, listener in enumerate(listeners):
        execution.start(resolved_job(f"run-both-{index}"), listener)

    assert all(isinstance(listener.wait(), RunCommitted) for listener in listeners)
    jobs = records(folder, "job")
    assert spare in jobs and len(jobs) == 2
    _ready_spare(execution, folder, known=frozenset(jobs))
    time.sleep(_QUIET_S)
    # 예비 하나 + 첫 run 이 쓴 예비 + 둘째 run 의 worker. 예비를 더 띄우지 않았다.
    assert len(records(folder, "started")) == 3


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        # 판본이 다른 예비는 어떤 run 도 쓰지 않는다(D6).
        ("mismatch", "backtest.run.worker_mismatch"),
        # `ready` 전에 끝나는 예비(부팅 실패).
        ("exit", "backtest.run.worker_exited"),
    ],
)
def test_spares_that_fail_are_retried_then_stopped_and_runs_start_their_own(
    tmp_path: Path, failure: str, code: str
) -> None:
    """D7: 예비 부팅 실패·판본 불일치는 로그만 남기고 다시 띄우고, 3회 이어지면 멈춘다.

    그 뒤의 run 은 감독 스레드가 직접 띄우고, 그 부팅이 같은 이유로 실패하면 그 run 이 그 코드로
    닫힌다.
    """
    folder = tmp_path / "records"
    if failure == "mismatch":
        execution = _execution(
            tmp_path, folder, versions=replace(parent_versions(), code_fingerprint="other")
        )
    else:
        exit_early = (
            "import os, sys, pathlib\n"
            "folder = pathlib.Path(sys.argv[1])\n"
            "folder.mkdir(parents=True, exist_ok=True)\n"
            "(folder / f'started-{os.getpid()}').write_text('')\n"
            "sys.exit(3)\n"
        )
        execution = _execution(
            tmp_path, folder, command=[sys.executable, "-c", exit_early, str(folder)]
        )

    wait_for_records(folder, "started", 3)
    time.sleep(_QUIET_S)
    assert len(records(folder, "started")) == 3
    assert execution.spare_pid() is None

    listener = _Recorder()
    execution.start(resolved_job("run-after-spares"), listener)
    outcome = listener.wait()

    assert isinstance(outcome, RunFailed) and outcome.error_code == code, outcome
    assert len(records(folder, "started")) == 4
    assert records(folder, "job") == set()


def test_a_spare_that_dies_while_waiting_is_replaced(tmp_path: Path) -> None:
    """D7: 기다리던 예비가 죽으면 다시 띄운다. 죽은 예비는 run 에 주지 않는다."""
    folder = tmp_path / "records"
    execution = _execution(tmp_path, folder)
    handle, dead = _ready_spare(execution, folder)

    os.kill(handle, signal.SIGTERM)
    assert wait_for_exit(dead, timeout_s=TIMEOUT_S)
    _, replacement = _ready_spare(execution, folder, known=frozenset({dead}))

    listener = _Recorder()
    execution.start(resolved_job("run-after-death"), listener)

    assert isinstance(listener.wait(), RunCommitted)
    assert records(folder, "job") == {replacement}


def _held_take(execution: ProcessRunExecution, monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """감독 스레드가 worker 를 손에 넣기 전에 멈추게 한다. 돌려준 표시를 세우면 이어 간다."""
    release = threading.Event()
    take = execution._take_spare  # pyright: ignore[reportPrivateUsage]  # reason: 틈 고정

    def held() -> object:
        assert release.wait(TIMEOUT_S)
        return take()

    monkeypatch.setattr(execution, "_take_spare", held)
    return release


def test_a_run_cancelled_before_it_takes_the_spare_never_sends_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D5 1: 예비를 손에 넣은 뒤 job 을 쓰기 전에 취소를 본다. job 을 보내지 않고 그 예비를
    끝내며, 다음 예비가 뜬다."""
    folder = tmp_path / "records"
    execution = _execution(tmp_path, folder)
    _, spare = _ready_spare(execution, folder)
    release = _held_take(execution, monkeypatch)

    listener = _Recorder()
    execution.start(resolved_job("run-cancel-spare"), listener).cancel()
    release.set()

    assert isinstance(listener.wait(), RunCancelled)
    assert wait_for_exit(spare, timeout_s=5.0)
    assert records(folder, "job") == set()
    _ready_spare(execution, folder, known=frozenset({spare}))


def test_a_run_cancelled_before_its_own_worker_boots_does_not_wait_for_the_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D5 1·V-4: 예비 없이 직접 띄운 worker 도, 손에 넣은 직후 취소를 보면 부팅을 기다리지 않는다.

    worker 는 `boot` 줄을 읽기 전에 2초 잠든다(`slow_boot`). 부팅이 끝난 뒤에야 취소를 보면 2초를
    넘긴다.
    """
    root = tmp_path / "artifacts"
    execution = ProcessRunExecution(
        WorkerSettings("mock", None, root),
        versions=parent_versions(),
        discard_unfinished=LocalArtifactStore(root).discard_unfinished,
        command=hook_command("slow_boot", tmp_path / "unused"),
        spare=False,
    )
    release = _held_take(execution, monkeypatch)

    listener = _Recorder()
    execution.start(resolved_job("run-cancel-boot"), listener).cancel()
    requested = time.monotonic()
    release.set()

    assert isinstance(listener.wait(), RunCancelled)
    assert time.monotonic() - requested <= _CANCEL_LIMIT_S


def test_shutdown_ends_a_waiting_spare_and_starts_no_other(tmp_path: Path) -> None:
    """D5 서버 종료: job 을 기다리는 예비를 끝내고 그 스레드를 기다린다. 다시 띄우지 않는다."""
    folder = tmp_path / "records"
    execution = _execution(tmp_path, folder)
    _, spare = _ready_spare(execution, folder)

    execution.shutdown()

    assert _spare_threads() == []
    assert wait_for_exit(spare, timeout_s=5.0)
    time.sleep(1.0)
    assert records(folder, "started") == {spare}
    assert execution.spare_pid() is None


def test_shutdown_ends_a_spare_that_never_finishes_booting(tmp_path: Path) -> None:
    """D5 서버 종료: `ready` 를 보내지 않는 예비도 부팅을 기다리지 않고 끝낸다."""
    marker = tmp_path / "marker"
    execution = _execution(tmp_path, marker, command=hook_command("before_main", marker))
    pid = wait_for_marker(marker)

    started = time.monotonic()
    execution.shutdown()

    assert time.monotonic() - started <= _CANCEL_LIMIT_S
    assert _spare_threads() == []
    assert wait_for_exit(pid, timeout_s=5.0)


def test_a_run_on_the_spare_gives_the_thread_mode_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-3: 예비 worker 로 돈 run 의 결과가 스레드 방식과 같다(지문·tape 해시·지표·산출물 전체).

    서버 조립(`build_http_app`)의 전송을 잡아 예비가 준비된 뒤에 요청을 보내고, 그 예비가 job 을
    받았는지 확인한다.
    """
    folder = tmp_path / "records"
    executions: list[ProcessRunExecution] = []

    class Recording(ProcessRunExecution):
        # reason: 서버 조립이 넘기는 인자를 그대로 넘기는 패스스루라 `Any` 다.
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs, command=hook_command("record", folder))
            executions.append(self)

    monkeypatch.setattr(_container, "ProcessRunExecution", Recording)
    results = {}
    for isolation in ("thread", "process"):
        app = build_http_app(
            artifact_root=tmp_path / isolation / "artifacts",
            research_db_path=tmp_path / isolation / "research.sqlite3",
            run_isolation=isolation,
        )
        with TestClient(app) as client:
            spare = _ready_spare(executions[0], folder)[1] if isolation == "process" else None
            state = _run(client, _body(client))
            assert state["status"] == "completed", state
            results[isolation] = _result(client, state["run_id"])
        if spare is not None:
            assert records(folder, "job") == {spare}

    thread, process = results["thread"], results["process"]
    for key in ("run_fingerprint", "target_tape_hash", "data_snapshot_id", "engine_version"):
        assert process["manifest"][key] == thread["manifest"][key], key
    assert process["metrics"] == thread["metrics"]
    assert process == thread
