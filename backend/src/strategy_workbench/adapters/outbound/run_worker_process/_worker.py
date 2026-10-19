"""worker 쪽 전송: job 한 줄을 읽어 계산하고 이벤트 줄을 쓴다(run-worker spec D3·D5·D6·D11).

조립(어댑터·계산 만들기)은 composition root(`bootstrap/run_worker`)가 `boot` 로 넘긴다. 이 모듈은
줄 주고받기, 진행 이벤트 상한, 부모 사망 감시, 엔진 관문, 코드 지문, 최대 RSS 만 소유한다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from threading import Event, Thread
from typing import Protocol, TextIO

from strategy_workbench.application.backtest_run.facade.computation import (
    RunComputation,
    full_collections_suspended,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    ArtifactCommit,
    BacktestArtifactStorePort,
    BacktestExecutionRequest,
    BacktestExecutorPort,
    CancellationCheck,
    ProgressCallback,
    RunCommitted,
    RunFailed,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, RunStage

from ._protocol import (
    CommittedMessage,
    EngineReadyMessage,
    FailedMessage,
    ProgressMessage,
    ProtocolError,
    ReadyMessage,
    VersionBundle,
    WorkerEvent,
    WorkerSettings,
    decode_job,
    decode_proceed,
    encode_event,
)

logger = logging.getLogger(__name__)

# 진행 이벤트 상한(spec D3). 단계·설명이 바뀌거나 직전 전송 뒤 이만큼 지났을 때만 보낸다.
_PROGRESS_INTERVAL_S = 0.1
# 부모가 사라졌을 때(stdin EOF) 결과를 쓰지 않고 끝내는 종료 코드와, 부모가 깨진 줄을 보냈을 때의
# 종료 코드.
EXIT_PARENT_GONE = 3
EXIT_PROTOCOL = 4
# Windows 감시 스레드가 stdin 파이프에 쌓인 바이트를 다시 들여다보는 간격(`_StdinPipe`).
_PIPE_POLL_S = 0.02


@dataclass(frozen=True)
class WorkerBoot:
    """부팅 결과: 이 worker 가 돌릴 계산과 그 계산이 읽는 판본."""

    computation: RunComputation
    versions: VersionBundle


def serve_stdio(boot: Callable[[WorkerSettings], WorkerBoot]) -> int:
    """표준 입출력으로 job 하나를 처리한다. 프로토콜 줄은 원래 stdout 에만 쓴다.

    라이브러리의 `print` 가 프로토콜 줄과 섞이지 않게 `sys.stdout` 을 stderr(로그)로 돌린다.
    """
    stdin, channel, log = sys.stdin, sys.stdout, sys.stderr
    if (
        not isinstance(stdin, io.TextIOWrapper)
        or not isinstance(channel, io.TextIOWrapper)
        or not isinstance(log, io.TextIOWrapper)
    ):
        raise RuntimeError(
            "run worker needs text stdio streams to speak the protocol — "
            f"stdin={type(stdin).__name__} stdout={type(channel).__name__} "
            f"stderr={type(log).__name__}"
        )
    # 부모가 넣는 환경(`PYTHONIOENCODING`)이 없어도 stdout·stderr 은 UTF-8 이다. 콘솔 코드 페이지
    # (cp949·cp1252)로 쓴 로그를 부모가 UTF-8 로 읽다 깨지지 않게 한다(spec D2). stdin 은 원시
    # 파이프로 직접 읽으므로(`_StdinPipe`) 텍스트 래퍼를 쓰지 않는다.
    channel.reconfigure(encoding="utf-8", newline="\n")
    log.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout = sys.stderr
    return serve(boot, stdin=_StdinPipe(stdin.fileno()), stdout=channel)


class ParentPipe(Protocol):
    """부모가 쓰는 stdin: 줄 읽기와, 막히지 않는 닫힘 확인."""

    def readline(self) -> str: ...

    def closed(self) -> bool: ...


def serve(
    boot: Callable[[WorkerSettings], WorkerBoot], *, stdin: ParentPipe, stdout: TextIO
) -> int:
    """job 하나를 읽어 부팅·계산하고 종결 이벤트를 보낸다.

    job 뒤의 stdin 은 감시 스레드가 끝까지 읽는다. EOF 면 부모가 사라진 것이라 결과를 쓰지 않고
    끝낸다(spec D5 보조 경로).
    """
    # job 읽기·부팅 실패는 여기서 로그로 남기지 않고 올린다. 잡히지 않은 예외의 traceback 이
    # stderr 에 한 번 남고, 부모가 run_id 를 붙여 ERROR 로 옮긴다(#581 리뷰 nit).
    line = stdin.readline()
    if not line:
        raise ProtocolError("not_json", "run worker stdin closed before a job line arrived")
    message = decode_job(line)
    job = message.job
    watch = _ParentWatch(stdin)
    watch.start()

    def sender(event: WorkerEvent) -> None:
        # 이벤트는 계산 스레드 하나만 쓴다(진행·엔진 관문·종결).
        stdout.write(encode_event(event))
        stdout.flush()

    booted = boot(message.settings)
    sender(ReadyMessage(booted.versions))
    computation = replace(
        booted.computation,
        artifact_store=_ParentAliveStore(booted.computation.artifact_store, watch),
    )
    if message.settings.engine_gate:
        computation = replace(
            computation, executor=_EngineGate(computation.executor, sender, watch)
        )
    progress = _ProgressThrottle(sender)
    # 전체 수집 미루기는 스레드 전송과 같은 범위로 계산을 감싼다(`_gc_policy`).
    with full_collections_suspended():
        outcome = computation.compute(job, progress=progress, cancelled=lambda: False)
    progress.flush()
    max_rss = peak_rss_bytes()
    if isinstance(outcome, RunCommitted):
        sender(CommittedMessage(outcome, max_rss))
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
        sender(FailedMessage(outcome.error_code, outcome.error, max_rss))
        return 0
    # worker 에는 협조적 취소 신호가 없다(취소는 프로세스 종료, D5). 계산이 취소로 멈췄다면
    # 계약 위반이라 실패로 알린다.
    sender(
        FailedMessage(
            "backtest.run.internal",
            "run worker computation stopped as cancelled without a cancel request — "
            f"run_id={job.run_id}",
            max_rss,
        )
    )
    return 0


class _ParentWatch:
    """job 뒤의 stdin 을 읽는다. `proceed` 는 관문을 열고, EOF 는 부모가 사라진 것이라 바로
    끝낸다."""

    def __init__(self, stdin: ParentPipe) -> None:
        self._stdin = stdin
        self.proceed = Event()
        self.parent_gone = Event()

    def start(self) -> None:
        Thread(target=self._watch, name="run-worker-parent-watch", daemon=True).start()

    def _watch(self) -> None:
        while line := self._stdin.readline():
            try:
                decode_proceed(line)
            except ProtocolError:
                logger.exception("run worker got a broken command line from its parent")
                os._exit(EXIT_PROTOCOL)
            self.proceed.set()
        self.parent_gone.set()
        _exit_parent_gone("stdin closed")

    def check_parent_alive(self) -> None:
        """부모가 이미 사라졌으면 결과를 쓰지 않고 끝낸다. 막히지 않는다(spec D5).

        감시 스레드의 표시만 믿지 않는다 — Rust 엔진이 GIL 을 쥐던 동안 감시 스레드가 EOF 를 보고도
        움직이지 못했을 수 있어 파이프를 직접 한 번 더 본다.
        """
        if self.parent_gone.is_set() or self._stdin.closed():
            _exit_parent_gone("stdin closed before commit")


def _exit_parent_gone(why: str) -> None:
    logger.error("run worker parent is gone (%s) — exiting without a result", why)
    os._exit(EXIT_PARENT_GONE)


class _StdinPipe:
    """부모가 쓰는 stdin 파이프를 원시 fd 로 줄 단위로 읽고, 막히지 않고 닫힘을 본다(spec D5).

    Windows 의 동기 익명 파이프는 다른 스레드가 `ReadFile` 로 기다리는 동안 `PeekNamedPipe` 가 그
    읽기가 끝날 때까지 막힌다(#581 리뷰 P3-3 에서 실측). 그래서 Windows 는 쌓인 바이트를 확인한
    만큼만 읽고, 없으면 잠깐 쉬었다 다시 본다. POSIX 는 막히는 읽기와 `poll` 이 겹쳐도 된다.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._pending = b""
        self._peek: Callable[[], int | None] | None = None
        if sys.platform == "win32":
            import msvcrt

            # 줄바꿈·Ctrl-Z 를 C 런타임이 바꾸지 않게 바이트 그대로 읽는다.
            msvcrt.setmode(fd, os.O_BINARY)
            self._peek = _named_pipe_peek(msvcrt.get_osfhandle(fd))

    def readline(self) -> str:
        """다음 줄(줄바꿈 포함). EOF 면 빈 문자열이다."""
        while b"\n" not in self._pending:
            chunk = self._read()
            if not chunk:
                rest, self._pending = self._pending, b""
                return _decode(rest)
            self._pending += chunk
        line, _, self._pending = self._pending.partition(b"\n")
        return _decode(line + b"\n")

    def closed(self) -> bool:
        if self._peek is None:
            return _hung_up(self._fd)
        return self._peek() is None

    def _read(self) -> bytes:
        if self._peek is None:
            return os.read(self._fd, 65536)
        while True:
            available = self._peek()
            if available is None:
                return b""
            if available:
                return os.read(self._fd, available)
            time.sleep(_PIPE_POLL_S)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("not_utf8", f"run worker stdin line is not UTF-8 — {error}") from error


def _hung_up(fd: int) -> bool:
    """POSIX: 쓰는 쪽이 모두 닫혔으면 참. 남은 바이트가 있어도 닫힘을 본다."""
    if sys.platform == "win32":
        raise RuntimeError("poll() is POSIX only — use PeekNamedPipe on Windows")
    import select

    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return any(events & (select.POLLHUP | select.POLLERR) for _, events in poller.poll(0))


_ERROR_BROKEN_PIPE = 109


def _named_pipe_peek(handle: int) -> Callable[[], int | None]:
    """Windows: 파이프에 쌓인 바이트 수를 막히지 않고 읽는 함수.

    쓰는 쪽이 닫혔거나 파이프를 읽을 수 없으면 None 을 돌려준다.
    """
    if sys.platform != "win32":
        raise RuntimeError("PeekNamedPipe is Windows only — use poll() on POSIX")
    import ctypes
    from ctypes import wintypes

    peek_named_pipe = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek_named_pipe.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    peek_named_pipe.restype = wintypes.BOOL

    def peek() -> int | None:
        available = wintypes.DWORD()
        if peek_named_pipe(handle, None, 0, None, ctypes.byref(available), None):
            return int(available.value)
        code = ctypes.get_last_error()
        if code != _ERROR_BROKEN_PIPE:
            logger.warning("run worker stdin pipe could not be peeked — winerror=%s", code)
        return None

    return peek


class _ParentAliveStore:
    """commit 직전에 부모가 살아 있는지 한 번 더 본다(spec D5). 나머지는 그대로 넘긴다."""

    def __init__(self, store: BacktestArtifactStorePort, watch: _ParentWatch) -> None:
        self._store = store
        self._watch = watch

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        self._watch.check_parent_alive()
        return self._store.commit(result)

    def load(self, run_id: str, *, sha256: str) -> BacktestRunResult:
        return self._store.load(run_id, sha256=sha256)

    def discard(self, run_id: str) -> None:
        self._store.discard(run_id)


class _EngineGate:
    """엔진 단계 앞에서 `engine_ready` 를 보내고 부모의 `proceed` 를 기다린다(spec D11)."""

    def __init__(
        self,
        executor: BacktestExecutorPort,
        send: Callable[[WorkerEvent], None],
        watch: _ParentWatch,
    ) -> None:
        self._executor = executor
        self._send = send
        self._watch = watch

    def execute(
        self,
        request: BacktestExecutionRequest,
        *,
        progress: ProgressCallback,
        cancelled: CancellationCheck,
    ) -> BacktestRunResult:
        self._send(EngineReadyMessage())
        # 판정·기한·취소는 부모가 한다. 취소면 부모가 이 프로세스를 끝낸다.
        self._watch.proceed.wait()
        return self._executor.execute(request, progress=progress, cancelled=cancelled)


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
