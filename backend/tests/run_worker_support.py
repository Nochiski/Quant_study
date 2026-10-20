"""run worker 통합 테스트 도구: 부모 판본 묶음, 단계에서 멈추는 worker, 프로세스 생존 확인.

멈추는 worker(`hook_command`)는 실제 진입점을 그대로 돌리되, 지정한 단계에 들어서면 자기 pid 를 표시
파일에 쓰고 오래 잠든다. 협조적 취소가 닿지 않는 자리라 프로세스 종료만이 그 run 을 끝낼 수 있다.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from functools import cache
from pathlib import Path

from strategy_workbench.adapters.outbound.run_worker_process.facade.worker import VersionBundle
from strategy_workbench.bootstrap.facade.container import build_equity_data, build_run_versions
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

TIMEOUT_S = 120.0

_HOOK = r"""
import os, sys, time
stage, marker = sys.argv[1], sys.argv[2]

def hold():
    with open(marker, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    time.sleep(600)

if stage == "before_main":
    hold()
elif stage == "record":
    # marker 는 디렉터리다. 띄워지면 started-<pid>, `ready` 뒤 job 을 기다리기 시작하면 idle-<pid>,
    # job 을 받으면 job-<pid> 를 만든다. 예비 worker 를 누가 언제 썼는지 본다(spec D7).
    from pathlib import Path
    from strategy_workbench.adapters.outbound.run_worker_process import _worker
    folder = Path(marker)
    folder.mkdir(parents=True, exist_ok=True)

    def touch(kind):
        (folder / f"{kind}-{os.getpid()}").write_text("", encoding="utf-8")

    touch("started")
    readline, calls = _worker._StdinPipe.readline, []

    def tracked(self):
        calls.append(None)
        if len(calls) == 2:  # 첫 줄은 boot, 둘째 줄을 기다리는 것은 job 이다
            touch("idle")
        return readline(self)

    _worker._StdinPipe.readline = tracked
    decode_job = _worker.decode_job

    def decoded(line):
        job = decode_job(line)
        touch("job")
        return job

    _worker.decode_job = decoded
elif stage == "slow_boot":
    # job 을 읽기 전에 잠깐 잠든다. 부모의 큰 job 쓰기가 그동안 파이프에서 막힌다.
    time.sleep(2.0)
elif stage == "watch_off":
    # 부모 사망 감시 스레드를 띄우지 않는다. commit 직전 확인만이 닫힌 stdin 을 볼 수 있다.
    from strategy_workbench.adapters.outbound.run_worker_process import _worker
    _worker._ParentWatch.start = lambda self: None
elif stage == "tape":
    from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
    PortfolioDesignService.run_pipeline = lambda self, *args, **kwargs: hold()
elif stage == "data":
    from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
        MockEquityDataAdapter,
    )
    original = MockEquityDataAdapter.load_backtest_dataset
    def load(self, query):
        if query.security_ids:
            hold()
        return original(self, query)
    MockEquityDataAdapter.load_backtest_dataset = load
elif stage == "engine":
    from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
        BacktestEngineExecutorAdapter,
    )
    BacktestEngineExecutorAdapter.execute = lambda self, *args, **kwargs: hold()
elif stage in ("artifact", "artifact_renamed"):
    from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
    original = LocalArtifactStore.commit
    def commit(self, result):
        if stage == "artifact_renamed":
            original(self, result)
        else:
            staging = self._root / f".{result.manifest.run_id}.hook.tmp"
            staging.mkdir()
            (staging / "result.json").write_text("{}", encoding="utf-8")
        hold()
    LocalArtifactStore.commit = commit
else:
    raise SystemExit(f"unknown hook stage {stage!r}")

from strategy_workbench.bootstrap.run_worker.facade.worker import main
raise SystemExit(main())
"""


def hook_command(stage: str, marker: Path) -> list[str]:
    """`stage` 에 들어서면 pid 를 `marker` 에 쓰고 잠드는 worker 진입점.

    `record` 는 잠들지 않고 `marker` 디렉터리에 띄워짐·job 대기·job 받음 표시만 남긴다(`records`).
    """
    return [sys.executable, "-c", _HOOK, stage, str(marker)]


@cache
def parent_versions() -> VersionBundle:
    """mock 서버가 부팅 때 계산하는 판본 묶음(worker 의 `ready` 와 같아야 한다)."""
    return build_run_versions(
        build_equity_data("mock", None),
        build_default_factor_registry(),
        build_default_metric_registry(),
    )


def wait_for_marker(marker: Path, *, timeout_s: float = TIMEOUT_S) -> int:
    """멈춘 worker 의 pid. 그 단계에 들어서지 않으면 AssertionError."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if marker.exists() and (text := marker.read_text(encoding="utf-8")):
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"worker did not reach its hook stage within {timeout_s}s — {marker}")


def records(folder: Path, kind: str) -> set[int]:
    """`record` worker 들이 남긴 표시(`started`·`idle`·`job`)의 pid."""
    return {int(path.name.split("-", 1)[1]) for path in folder.glob(f"{kind}-*")}


def wait_for_records(
    folder: Path, kind: str, count: int, *, timeout_s: float = TIMEOUT_S
) -> set[int]:
    """`kind` 표시가 `count` 개 이상 생길 때까지 기다린 pid. 모자라면 AssertionError."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if len(found := records(folder, kind)) >= count:
            return found
        time.sleep(0.05)
    raise AssertionError(
        f"expected {count} {kind} records within {timeout_s}s — got {sorted(records(folder, kind))}"
    )


def wait_for_exit(pid: int, *, timeout_s: float) -> bool:
    """`pid` 가 `timeout_s` 안에 끝나면 참. 우리 자식이 아닌 프로세스도 본다."""
    if sys.platform == "win32":
        synchronize, query = 0x00100000, 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(synchronize | query, 0, pid)
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if _is_zombie(pid):
            return True
        time.sleep(0.05)
    return False


def _is_zombie(pid: int) -> bool:
    """POSIX: 끝났지만 부모가 아직 거두지 않은 프로세스면 참이다. 거두지 않는다.

    죽은 자식은 부모의 `Popen` 이 poll·wait 로 거둘 때까지 좀비로 남아 `os.kill(pid, 0)` 이
    계속 성공한다(#591 2차 리뷰 P2-1). 남의 자식(부모를 죽인 고아)도 볼 수 있게 `waitid` 대신
    `/proc/<pid>/stat` 의 상태 칸을 읽는다. `/proc` 이 없는 플랫폼은 거짓이다.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    # 둘째 칸(실행 파일 이름)은 괄호 안이고 공백을 담을 수 있어 마지막 ')' 뒤에서 상태를 읽는다.
    return stat.rpartition(")")[2].split()[:1] == ["Z"]
