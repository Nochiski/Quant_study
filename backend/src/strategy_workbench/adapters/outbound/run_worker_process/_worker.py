"""worker 쪽 전송: job 한 줄을 읽어 계산하고 이벤트 줄을 쓴다(run-worker spec D3·D6).

조립(어댑터·계산 만들기)은 composition root(`bootstrap/run_worker`)가 `boot` 로 넘긴다. 이 모듈은
줄 주고받기, 진행 이벤트 상한, 코드 지문, 최대 RSS 만 소유한다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import TextIO

from strategy_workbench.application.backtest_run.facade.computation import (
    RunComputation,
    full_collections_suspended,
)
from strategy_workbench.application.backtest_run.facade.ports import RunCommitted, RunFailed
from strategy_workbench.domain.backtest.facade.runs import RunStage

from ._protocol import (
    CommittedMessage,
    FailedMessage,
    ProgressMessage,
    ProtocolError,
    ReadyMessage,
    VersionBundle,
    WorkerEvent,
    WorkerSettings,
    decode_job,
    encode_event,
)

logger = logging.getLogger(__name__)

# 진행 이벤트 상한(spec D3). 단계·설명이 바뀌거나 직전 전송 뒤 이만큼 지났을 때만 보낸다.
_PROGRESS_INTERVAL_S = 0.1


@dataclass(frozen=True)
class WorkerBoot:
    """부팅 결과: 이 worker 가 돌릴 계산과 그 계산이 읽는 판본."""

    computation: RunComputation
    versions: VersionBundle


def serve_stdio(boot: Callable[[WorkerSettings], WorkerBoot]) -> int:
    """표준 입출력으로 job 하나를 처리한다. 프로토콜 줄은 원래 stdout 에만 쓴다.

    라이브러리의 `print` 가 프로토콜 줄과 섞이지 않게 `sys.stdout` 을 stderr(로그)로 돌린다.
    """
    stdin, channel = sys.stdin, sys.stdout
    if not isinstance(stdin, io.TextIOWrapper) or not isinstance(channel, io.TextIOWrapper):
        raise RuntimeError(
            "run worker needs text stdio streams to speak the protocol — "
            f"stdin={type(stdin).__name__} stdout={type(channel).__name__}"
        )
    stdin.reconfigure(encoding="utf-8")
    channel.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout = sys.stderr
    return serve(boot, stdin=stdin, stdout=channel)


def serve(boot: Callable[[WorkerSettings], WorkerBoot], *, stdin: TextIO, stdout: TextIO) -> int:
    """job 하나를 읽어 부팅·계산하고 종결 이벤트를 보낸다.

    stdin 은 닫지 않는다(부모 사망 감지, D5).
    """
    line = stdin.readline()
    if not line:
        raise ProtocolError("run worker stdin closed before a job line arrived")
    message = decode_job(line)
    job = message.job

    def send(event: WorkerEvent) -> None:
        stdout.write(encode_event(event))
        stdout.flush()

    booted = boot(message.settings)
    send(ReadyMessage(booted.versions))
    progress = _ProgressThrottle(send)
    # 전체 수집 미루기는 스레드 전송과 같은 범위로 계산을 감싼다(`_gc_policy`).
    with full_collections_suspended():
        outcome = booted.computation.compute(job, progress=progress, cancelled=lambda: False)
    progress.flush()
    max_rss = peak_rss_bytes()
    if isinstance(outcome, RunCommitted):
        send(CommittedMessage(outcome, max_rss))
        return 0
    if isinstance(outcome, RunFailed):
        # 원래 예외의 stack trace 는 이 프로세스에만 있다. 로그(stderr)로 남기고 부모가 run_id 를
        # 붙여 중계한다. 부모에는 코드와 문장만 간다.
        logger.error(
            "backtest run failed in worker — run_id=%s error_code=%s",
            job.run_id,
            outcome.error_code,
            exc_info=outcome.exception,
        )
        send(FailedMessage(outcome.error_code, outcome.error, max_rss))
        return 0
    # worker 에는 협조적 취소 신호가 없다(취소는 프로세스 종료, D5). 계산이 취소로 멈췄다면
    # 계약 위반이라 실패로 알린다.
    send(
        FailedMessage(
            "backtest.run.internal",
            "run worker computation stopped as cancelled without a cancel request — "
            f"run_id={job.run_id}",
            max_rss,
        )
    )
    return 0


class _ProgressThrottle:
    """진행을 상한(0.1초)에 맞춰 보낸다. 단계·설명이 바뀌면 바로, 마지막 값은 `flush` 로 보낸다."""

    def __init__(
        self,
        send: Callable[[WorkerEvent], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._clock = clock
        self._sent_key: tuple[RunStage, str] | None = None
        self._sent_at = 0.0
        self._pending: ProgressMessage | None = None

    def __call__(self, stage: RunStage, fraction: float, message: str) -> None:
        event = ProgressMessage(stage, fraction, message)
        now = self._clock()
        if (stage, message) != self._sent_key or now - self._sent_at >= _PROGRESS_INTERVAL_S:
            self._send(event)
            self._sent_key = (stage, message)
            self._sent_at = now
            self._pending = None
        else:
            self._pending = event

    def flush(self) -> None:
        if self._pending is not None:
            self._send(self._pending)
            self._pending = None


def code_fingerprint() -> str:
    """worker 가 읽은 코드의 지문(spec D6).

    `strategy_workbench`·`backtest_engine` 의 `.py` 소스와 `backtest_core` 확장 파일을 경로와 함께
    해시한다. 커널 패키지는 import 하지 않고 위치만 찾는다.
    """
    entries: list[tuple[str, Path]] = []
    for package in ("strategy_workbench", "backtest_engine"):
        for root in _package_locations(package):
            entries.extend(
                (f"{package}/{path.relative_to(root).as_posix()}", path)
                for path in sorted(root.rglob("*.py"))
            )
    extensions = sorted(
        path
        for root in _package_locations("backtest_core")
        for path in root.iterdir()
        if any(path.name.endswith(suffix) for suffix in EXTENSION_SUFFIXES)
    )
    if not extensions:
        raise RuntimeError(
            "backtest_core extension file not found — run `maturin develop` "
            f"suffixes={EXTENSION_SUFFIXES}"
        )
    entries.extend((f"backtest_core/{path.name}", path) for path in extensions)
    digest = hashlib.sha256()
    for name, path in entries:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _package_locations(package: str) -> tuple[Path, ...]:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(
            f"package location not found for the code fingerprint — package={package}"
        )
    return tuple(Path(location) for location in spec.submodule_search_locations)


def peak_rss_bytes() -> int | None:
    """이 프로세스의 최대 RSS(바이트). 읽지 못하면 None 이다(spec D8 계수 재조정의 입력)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD)
        if not read(current(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux 는 KiB, macOS 는 바이트다.
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024
