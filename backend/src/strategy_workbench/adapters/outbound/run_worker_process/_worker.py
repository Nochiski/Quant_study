"""worker 쪽 전송: boot 줄로 부팅하고 job 한 줄을 읽어 계산해 이벤트 줄을 쓴다(spec D3·D5~D7·D11).

조립(어댑터·계산 만들기)은 composition root(`bootstrap/run_worker`)가 `boot` 로 넘긴다. 이 모듈은
줄 주고받기, 진행 이벤트 상한, 부모 사망 감시, 엔진 관문, 코드 지문과 그 뒤 import 가드만 소유한다.
최대 RSS 읽기는 `_memory.py` 가 소유한다.
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

from ._memory import peak_rss_bytes
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
    decode_boot,
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
# Windows 에서 stdin 파이프에 쌓인 바이트를 다시 들여다보는 간격(`_StdinPipe`). 감시 스레드와
# job 을 기다리는 예비 worker 의 주 스레드가 쓴다.
_PIPE_POLL_S = 0.02
# 코드 지문(spec D6)이 덮는 패키지: 앞 둘은 `.py` 소스를, 마지막은 확장 파일을 해시한다.
# 부팅 뒤 run 은 이 패키지의 모듈을 처음 불러오지 않는다 — 불러오면 대조하지 않은 코드라
# commit 앞에서 거절한다(spec D7, `_GuardedStore`).
_SOURCE_PACKAGES = ("strategy_workbench", "backtest_engine")
_EXTENSION_PACKAGE = "backtest_core"
_PROJECT_PACKAGES = frozenset((*_SOURCE_PACKAGES, _EXTENSION_PACKAGE))


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
    """`boot` 줄로 부팅해 `ready` 를 보내고, job 하나를 읽어 계산한 뒤 종결 이벤트를 보낸다.

    `ready` 뒤에는 job 을 기다린다 — 예비 worker 는 여기서 run 이 정해질 때까지 머문다(spec D7).
    그동안의 EOF 는 부모가 사라진 것이라 결과 없이 끝낸다. job 뒤의 stdin 은 감시 스레드가 끝까지
    읽고, EOF 면 결과를 쓰지 않고 끝낸다(spec D5 보조 경로).
    """
    # boot·job 읽기와 부팅 실패는 여기서 로그로 남기지 않고 올린다. 잡히지 않은 예외의 traceback
    # 이 stderr 에 한 번 남고, 부모가 run_id 를 붙여 ERROR 로 옮긴다(#581 리뷰 nit).
    line = stdin.readline()
    if not line:
        raise ProtocolError("not_json", "run worker stdin closed before a boot line arrived")
    settings = decode_boot(line)

    def sender(event: WorkerEvent) -> None:
        # 이벤트는 계산 스레드 하나만 쓴다(진행·엔진 관문·종결).
        stdout.write(encode_event(event))
        stdout.flush()

    booted = boot(settings)
    # 코드 지문은 boot 의 마지막 단계다. 지금 불러온 프로젝트 모듈이 판본 대조에 든 코드이고, run 이
    # 이 뒤에 처음 불러오는 모듈은 commit 앞에서 거절한다(spec D7, #591 2차 리뷰 P3-1).
    booted_modules = project_modules()
    sender(ReadyMessage(booted.versions, os.getpid()))
    line = stdin.readline()
    if not line:
        # 예비로 기다리던 중 부모가 사라졌다(POSIX 의 부모 사망 경로, Windows 는 Job Object).
        logger.error("run worker parent is gone (stdin closed before a job) — exiting")
        return EXIT_PARENT_GONE
    job = decode_job(line)
    watch = _ParentWatch(stdin)
    watch.start()
    computation = replace(
        booted.computation,
        artifact_store=_GuardedStore(booted.computation.artifact_store, watch, booted_modules),
    )
    if settings.engine_gate:
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
        code, error = outcome.error_code, outcome.error
        if isinstance(outcome.exception, _LateProjectImportError):
            # 대조하지 않은 코드로 낸 결과라 판본 불일치로 닫는다. 계산은 이 예외를 모르므로 분류는
            # 이 노드가 한다(spec D6·D7).
            code = "backtest.run.worker_mismatch"
            error = f"{outcome.exception} — run_id={job.run_id}"
        # 원래 예외의 stack trace 는 이 프로세스에만 있다. 로그(stderr)로 남기고 부모가 run_id 를
        # 붙여 중계한다. 부모에는 코드와 문장만 간다.
        logger.error(
            "backtest run failed in worker — run_id=%s error_code=%s",
            job.run_id,
            code,
            exc_info=outcome.exception,
        )
        sender(FailedMessage(code, error, max_rss))
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
        # 줄 읽기도 감싼다. UTF-8 이 아닌 줄(`not_utf8`)에 감시 스레드만 조용히 죽으면 부모 사망을
        # 더는 보지 못한다(#581 2차 리뷰 nit-2).
        try:
            while line := self._stdin.readline():
                decode_proceed(line)
                self.proceed.set()
        except ProtocolError:
            logger.exception("run worker got a broken command line from its parent")
            os._exit(EXIT_PROTOCOL)
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
        """쓰는 쪽(부모)이 닫았으면 참. 막히지 않는다.

        플랫폼마다 남은 바이트를 다르게 본다: POSIX 는 안 읽은 바이트가 있어도 닫힘을 바로 보고,
        Windows 는 안 읽은 바이트가 남아 있으면 그 바이트를 읽은 뒤에야 참이 된다(`PeekNamedPipe` 는
        쌓인 바이트가 있는 동안 성공한다).
        """
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
    """POSIX: 쓰는 쪽이 모두 닫혔으면 참. 안 읽은 바이트가 있어도 POLLHUP 로 닫힘을 본다
    (Windows 의 `_named_pipe_peek` 는 그 바이트를 다 읽어야 닫힘을 안다)."""
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


class _LateProjectImportError(RuntimeError):
    """run 이 판본 대조 뒤 프로젝트 모듈을 처음 불러왔다 — 그 코드는 서버와 대조하지 않았다."""


class _GuardedStore:
    """commit 직전에 두 가지를 본다. 나머지는 그대로 넘긴다.

    부모가 살아 있는가(spec D5), 그리고 부팅 뒤 run 이 프로젝트 모듈을 처음 불러오지 않았는가(spec
    D7). 지문은 디스크의 소스를 해시하므로, 예비가 기다리는 동안 바뀐 파일을 run 이 처음 불러오면
    서버와 다른 코드로 계산한 결과가 같은 판본으로 남는다. 그런 결과는 commit 하지 않는다.
    """

    def __init__(
        self,
        store: BacktestArtifactStorePort,
        watch: _ParentWatch,
        booted_modules: frozenset[str],
    ) -> None:
        self._store = store
        self._watch = watch
        self._booted_modules = booted_modules

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        self._watch.check_parent_alive()
        if late := project_modules() - self._booted_modules:
            raise _LateProjectImportError(
                "run worker imported project modules after its version check, so their code was "
                f"not compared with the server — modules={','.join(sorted(late))}; the result was "
                "not committed (import them at worker boot, run-worker spec D7)"
            )
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
    for package in _SOURCE_PACKAGES:
        for root in _package_locations(package):
            entries.extend(
                (f"{package}/{path.relative_to(root).as_posix()}", path)
                for path in sorted(root.rglob("*.py"))
            )
    extensions = sorted(
        path
        for root in _package_locations(_EXTENSION_PACKAGE)
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


def project_modules() -> frozenset[str]:
    """지금 불러온 프로젝트 모듈 이름(지문이 덮는 패키지와 그 하위)."""
    # 다른 스레드가 import 하는 중일 수 있어 사본을 훑는다(dict.copy 는 GIL 아래 한 번에 끝난다).
    return frozenset(
        name for name in sys.modules.copy() if name.partition(".")[0] in _PROJECT_PACKAGES
    )


def _package_locations(package: str) -> tuple[Path, ...]:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(
            f"package location not found for the code fingerprint — package={package}"
        )
    return tuple(Path(location) for location in spec.submodule_search_locations)
