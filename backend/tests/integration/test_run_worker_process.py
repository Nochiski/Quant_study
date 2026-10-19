"""worker 프로세스 전송(`run_worker_process`)이 실제 하위 프로세스로 run 을 돌리는지(W1-02).

mock 어댑터와 실제 `python -m strategy_workbench.bootstrap.run_worker` 하위 프로세스를 쓴다. 하위
프로세스가 여는 경로는 감사 훅(`runtime_path_guard`)이 보지 못하므로 테스트가 직접 격리한다: 산출물
루트·연구 DB 는 tmp 를 명시하고, `STRATEGY_WORKBENCH_*` 는 autouse 격리 fixture 가 지운 환경을
하위 프로세스가 물려받는다(worker 는 그 값을 읽지도 않는다, spec D10). mock 어댑터는 파일을
열지 않는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.run_worker_process.facade.execution import (
    WORKER_COMMAND,
    ProcessRunExecution,
    WorkerSettings,
)
from strategy_workbench.adapters.outbound.run_worker_process.facade.protocol import (
    PROTOCOL_VERSION,
)
from strategy_workbench.application.backtest_run.facade.ports import RunFailed, RunOutcome
from strategy_workbench.bootstrap import _container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.runs import RunStage
from tests.backtest_run_wait import wait_for_terminal_state
from tests.run_worker_support import hook_command, parent_versions
from tests.test_run_worker_protocol import resolved_job

# 하위 프로세스 부팅(import·조립)을 CI 의 느린 기계에서도 기다린다.
_TIMEOUT_S = 120.0
# 감독 스레드가 잠금 밖에서 일하면 조회는 ms 단위다. 느린 CI 여유를 둔 상한이다.
_QUERY_LIMIT_S = 1.0


def _client(tmp_path: Path, isolation: str) -> TestClient:
    return TestClient(
        build_http_app(
            artifact_root=tmp_path / isolation / "artifacts",
            research_db_path=tmp_path / isolation / "research.sqlite3",
            run_isolation=isolation,
        )
    )


def _body(client: TestClient, **overrides: Any) -> dict[str, Any]:
    spec = client.get("/api/v1/strategies/template").json()
    spec["description"] = overrides.pop("description", "한글 설명 — worker 왕복")
    return {
        "strategy": spec,
        "environment": {
            "start": "2026-01-02",
            "end": "2026-02-20",
            "universe_id": "krx.common-stock",
        },
        "benchmark_security_id": "sec-005930-1",
        **overrides,
    }


def _run(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    accepted = client.post("/api/v1/backtests", json=body)
    assert accepted.status_code == 202, accepted.text
    return wait_for_terminal_state(client, accepted.json()["run"]["run_id"], timeout_s=_TIMEOUT_S)


def _result(client: TestClient, run_id: str) -> dict[str, Any]:
    """커밋한 산출물을 서비스가 해시 검증해 되읽은 결과. run_id·시각만 run 마다 다르다(V-3)."""
    response = client.get(f"/api/v1/backtests/{run_id}/result")
    assert response.status_code == 200, response.text
    payload = response.json()
    for key in ("run_id", "created_at", "completed_at"):
        payload["manifest"].pop(key)
    return payload


def _progress(client: TestClient, run_id: str) -> list[tuple[str, float]]:
    events = client.get(f"/api/v1/backtests/{run_id}/events").text
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in events.splitlines()
        if line.startswith("data: ")
    ]
    return [(frame["stage"], frame["progress"]) for frame in frames]


def test_process_run_completes_with_monotonic_progress(tmp_path: Path) -> None:
    client = _client(tmp_path, "process")
    state = _run(client, _body(client))

    assert state["status"] == "completed", state
    progress = _progress(client, state["run_id"])
    stages = [stage for stage, _ in progress]
    assert {"tape", "data", "engine", "artifact", "completed"} <= set(stages)
    fractions = [fraction for _, fraction in progress]
    assert fractions == sorted(fractions), progress
    assert progress[-1] == ("completed", 1.0)


def test_thread_and_process_runs_produce_the_same_result(tmp_path: Path) -> None:
    """V-3: 같은 요청을 두 전송으로 돌리면 지문·tape 해시·지표·산출물이 같다(run_id·시각 제외)."""
    results = {}
    for isolation in ("thread", "process"):
        client = _client(tmp_path, isolation)
        state = _run(client, _body(client))
        assert state["status"] == "completed", state
        results[isolation] = _result(client, state["run_id"])

    thread, process = results["thread"], results["process"]
    for key in ("run_fingerprint", "target_tape_hash", "data_snapshot_id", "engine_version"):
        assert process["manifest"][key] == thread["manifest"][key], key
    assert process["metrics"] == thread["metrics"]
    assert process["manifest"]["run_spec"]["strategy"]["description"] == "한글 설명 — worker 왕복"
    assert process == thread


def test_failure_code_and_korean_message_cross_the_process_boundary(tmp_path: Path) -> None:
    """실패 코드와 문장(한글 포함)이 스레드 전송과 같은 값으로 부모 상태에 닿는다."""
    states = {}
    for isolation in ("thread", "process"):
        client = _client(tmp_path, isolation)
        states[isolation] = _run(client, _body(client, benchmark_security_id="없는벤치마크"))

    thread, process = states["thread"], states["process"]
    assert process["status"] == "failed", process
    assert process["error_code"] == thread["error_code"] == "backtest.run.benchmark_unknown"
    assert "없는벤치마크" in process["error"]
    assert process["error"] == thread["error"]


def test_service_queries_stay_responsive_while_the_worker_boots_with_a_large_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D12: 감독 스레드가 큰 job 을 쓰고 `ready` 를 기다리는 동안에도 조회가 막히지 않는다.

    job 은 파이프 버퍼보다 훨씬 크다(설명 약 1.5MB). worker 는 job 을 읽기 전에 2초 잠들어(무거운
    import 를 과장한다) 그동안 감독 스레드의 쓰기가 막힌다. 접수와, 그 뒤 조립·`ready` 까지의 조회
    시간을 잰다. 쓰기가 서비스 잠금 안에 있으면 접수가 2초 넘게 걸려 실패한다.
    """
    monkeypatch.setattr(
        _container,
        "ProcessRunExecution",
        partial(ProcessRunExecution, command=hook_command("slow_boot", tmp_path / "unused")),
    )
    client = _client(tmp_path, "process")
    body = _body(client, description="큰 job 한글 설명 " * 100_000)
    posted = time.monotonic()
    accepted = client.post("/api/v1/backtests", json=body)
    # 접수는 서비스 잠금 안에서 전송의 `start` 를 부른다. job 쓰기가 거기 있으면 여기서 막힌다.
    assert time.monotonic() - posted < _QUERY_LIMIT_S
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["run_id"]

    slowest = 0.0
    queries_before_tape = 0
    deadline = time.monotonic() + _TIMEOUT_S
    while time.monotonic() < deadline:
        started = time.monotonic()
        assert client.get("/api/v1/health").status_code == 200
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        slowest = max(slowest, time.monotonic() - started)
        if state["stage"] == "queued":
            queries_before_tape += 1
        if state["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.02)

    assert state["status"] == "completed", state
    # 부팅(수백 ms 이상) 동안 여러 번 조회했고 어느 것도 오래 걸리지 않았다.
    assert queries_before_tape >= 5, queries_before_tape
    assert slowest < _QUERY_LIMIT_S, slowest


def test_worker_rejects_a_job_of_another_protocol_version(tmp_path: Path) -> None:
    """worker 도 다른 판본을 거절한다. 인코딩 환경 변수가 없어도 로그(stderr)는 UTF-8 이다.

    Windows 콘솔 코드 페이지(cp1252·cp949)로 쓰면 부모가 UTF-8 로 읽다 깨진다(CI windows job).
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONUTF8", "PYTHONIOENCODING"}
    }
    completed = subprocess.run(
        WORKER_COMMAND,
        input=json.dumps({"protocol": PROTOCOL_VERSION + 1, "type": "job"}) + "\n",
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=_TIMEOUT_S,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "protocol version mismatch" in completed.stderr


@pytest.mark.parametrize(
    ("program", "code", "expected"),
    [
        # 다른 판본의 worker: 부모가 거절하고 서버를 다시 시작하라고 안내한다.
        (
            "import sys; sys.stdin.readline(); "
            f'print(\'{{"protocol": {PROTOCOL_VERSION + 1}, "type": "ready"}}\', flush=True)',
            "backtest.run.worker_mismatch",
            "restart the server",
        ),
        # 깨진 줄: 상태에는 종류·type 만 싣고 worker 원문 발췌는 로그에만 남긴다(#575 리뷰 P3).
        (
            "import sys; sys.stdin.readline(); print('secret C:/private 원문', flush=True)",
            "backtest.run.internal",
            "reason=not_json",
        ),
        # 종결 이벤트 없이 끝난 worker: run_id·종료 코드를 싣고 실패로 닫는다.
        (
            "import sys; sys.stdin.readline(); sys.exit(3)",
            "backtest.run.worker_exited",
            "exit_code=3",
        ),
    ],
)
def test_parent_fails_the_run_when_the_worker_breaks_the_protocol(
    tmp_path: Path, program: str, code: str, expected: str
) -> None:
    outcome = _start(tmp_path, [sys.executable, "-c", program])

    assert isinstance(outcome, RunFailed)
    assert outcome.error_code == code
    assert "run-protocol" in outcome.error
    assert expected in outcome.error
    assert "secret" not in outcome.error


def _start(tmp_path: Path, command: list[str]) -> RunOutcome:
    """전송을 직접 띄워 종결을 기다린다. `start` 는 감독 스레드만 띄우고 바로 돌아와야 한다."""
    listener = _Listener()
    execution = ProcessRunExecution(
        WorkerSettings("mock", None, tmp_path / "artifacts"),
        versions=parent_versions(),
        discard_unfinished=lambda _run_id: None,
        command=command,
    )
    started = time.monotonic()
    execution.start(resolved_job("run-protocol"), listener)
    assert time.monotonic() - started < _QUERY_LIMIT_S
    assert listener.finished.wait(_TIMEOUT_S)
    assert listener.thread_names[0] != threading.current_thread().name
    return listener.outcomes[0]


class _Listener:
    def __init__(self) -> None:
        self.outcomes: list[RunOutcome] = []
        self.thread_names: list[str] = []
        self.finished = threading.Event()

    def on_progress(self, stage: RunStage, fraction: float, message: str) -> None:
        pass

    def on_finished(self, outcome: RunOutcome) -> None:
        self.outcomes.append(outcome)
        self.thread_names.append(threading.current_thread().name)
        self.finished.set()
