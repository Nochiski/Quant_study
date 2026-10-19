"""부모 쪽 전송: run 하나를 worker 프로세스 하나에서 돌린다(run-worker spec D1·D2·D5·D6·D11·D13).

run 마다 감독 스레드 하나가 worker 를 띄우고 job 을 쓰고 stdout 이벤트를 끝까지 읽어 listener 에
옮긴다. 그 run 의 종결은 이 스레드 하나만 알린다. stderr 는 따로 읽는 스레드가 run_id 를 붙여 로그로
중계한다. 두 파이프를 모두 끝까지 읽어 worker 가 쓰기에서 막히지 않게 한다(교착 방지, D3).

종결 순서(D5):
1. 취소는 플래그를 세우고 worker 종료를 요청만 한다. 감독 스레드는 worker 를 손에 넣은 직후와 job 을
   쓰기 직전에 플래그를 다시 본다.
2. stdout EOF 뒤 `wait()` 로 프로세스가 끝난 것을 확인하고, 완료로 넘기지 않는 run 의 staging·결과
   디렉터리를 지운다.
3. 종결 이벤트가 없으면 취소 플래그가 섰을 때 취소, 아니면 `backtest.run.worker_exited` 다.
4. `committed` 가 먼저 왔으면 완료로 넘기고, 취소와의 선형화는 서비스가 잠금 안에서 한다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from threading import Lock, Thread
from typing import IO, Protocol

from strategy_workbench.application.backtest_run.facade.ports import (
    RunCancelled,
    RunCancelledError,
    RunCommitted,
    RunFailed,
    RunHandle,
    RunJob,
    RunListener,
    RunOutcome,
)
from strategy_workbench.domain.backtest.facade.runs import RunStage

from ._protocol import (
    CommittedMessage,
    EngineReadyMessage,
    FailedMessage,
    ProgressMessage,
    ProtocolError,
    ReadyMessage,
    VersionBundle,
    WorkerSettings,
    decode_event,
    encode_job,
    encode_proceed,
)

logger = logging.getLogger(__name__)

WORKER_COMMAND: tuple[str, ...] = (sys.executable, "-m", "strategy_workbench.bootstrap.run_worker")

# 개발 터미널의 Ctrl+C 가 worker 에 직접 닿지 않게 프로세스 그룹(Windows)·세션(POSIX)을 나눈다
# (spec D2).
_CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
_LOG_LEVELS = logging.getLevelNamesMapping()
# 서버 종료 때 감독 스레드를 기다리는 상한. worker 는 이미 끝내라고 했으니 EOF 처리만 남는다.
_SHUTDOWN_JOIN_S = 10.0

EngineGate = Callable[[str, Callable[[], bool]], bool]
"""(run_id, 취소 확인) → 엔진 단계로 보내도 되면 참. e2e hold 판정이다(spec D11)."""


class ProcessRunExecution:
    """run 하나를 worker 프로세스 하나에서 돌리는 전송. 끝나면 worker 는 스스로 끝난다(D1).

    `start` 는 감독 스레드만 띄우고 바로 돌아온다 — 서비스 잠금 안에서 불리므로 `Popen`·파이프
    쓰기·`ready` 대기를 여기서 하지 않는다(D12). `versions` 는 서버가 부팅 때 계산한 판본 묶음이고
    worker 의 `ready` 와 대조한다(D6). `discard_unfinished` 는 완료로 넘기지 않는 run 의 산출물
    흔적을 지운다(D5). `command` 는 테스트가 worker 진입점을 감쌀 때만 바꾼다.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        versions: VersionBundle,
        discard_unfinished: Callable[[str], None],
        engine_gate: EngineGate | None = None,
        command: Sequence[str] = WORKER_COMMAND,
    ) -> None:
        if settings.engine_gate != (engine_gate is not None):
            raise ValueError(
                "worker engine gate and its parent-side decision must be set together — "
                f"settings.engine_gate={settings.engine_gate} "
                f"engine_gate={'set' if engine_gate else 'unset'}"
            )
        self._settings = settings
        self._versions = versions
        self._discard_unfinished = discard_unfinished
        self._engine_gate = engine_gate
        self._command = tuple(command)
        self._lock = Lock()
        self._runs: dict[_WorkerRun, Thread] = {}
        self._closed = False
        self._job = _job_object()

    def start(self, job: RunJob, listener: RunListener) -> RunHandle:
        run = _WorkerRun(
            job,
            listener,
            settings=self._settings,
            versions=self._versions,
            discard_unfinished=self._discard_unfinished,
            engine_gate=self._engine_gate,
            command=self._command,
            assign=self._assign,
            forget=self._forget,
        )
        thread = Thread(target=run.supervise, name=f"backtest-worker-{job.run_id}", daemon=True)
        with self._lock:
            if self._closed:
                # 서버가 끝나는 중이다. 새 worker 를 띄우지 않는다 — 그 run 은 다음 부팅이
                # `interrupted` 로 닫는다(spec D5).
                return run
            # 시작과 등록을 같은 잠금 안에서 한다. 사이가 열려 있으면 `shutdown` 이 아직 시작하지
            # 않은 스레드를 join 해 RuntimeError 로 끝난다(#581 리뷰 P3-1). `start` 는 스레드가
            # 뜰 때까지만 기다린다. 감독 본문은 끝(`_forget`)에서야 이 잠금을 잡으므로 교착이 없고,
            # 시작이 실패하면 등록하지 않는다.
            thread.start()
            self._runs[run] = thread
        return run

    def shutdown(self) -> None:
        """도는 worker 를 끝내고 감독 스레드를 기다린다. 종결은 알리지 않는다(다음 부팅 몫)."""
        with self._lock:
            self._closed = True
            runs = dict(self._runs)
        for run in runs:
            run.abandon()
        for run, thread in runs.items():
            thread.join(_SHUTDOWN_JOIN_S)
            if thread.is_alive():
                logger.error(
                    "run worker supervisor did not stop on shutdown — run_id=%s waited_s=%s",
                    run.run_id,
                    _SHUTDOWN_JOIN_S,
                )

    def _assign(self, process: subprocess.Popen[bytes], run_id: str) -> None:
        if self._job is None:
            return
        try:
            self._job.assign(process.pid)
        except OSError:
            # stdin EOF 감시가 보조 경로로 남는다(spec D5).
            logger.warning(
                "run worker not placed in the kill-on-close job — run_id=%s pid=%s",
                run_id,
                process.pid,
                exc_info=True,
            )

    def _forget(self, run: _WorkerRun) -> None:
        with self._lock:
            self._runs.pop(run, None)


class _ProcessJob(Protocol):
    def assign(self, pid: int) -> None: ...


def _job_object() -> _ProcessJob | None:
    if sys.platform != "win32":
        return None
    from ._job_object import KillOnCloseJob

    try:
        return KillOnCloseJob()
    except OSError:
        logger.warning("kill-on-close job object unavailable — stdin EOF only", exc_info=True)
        return None


class _WorkerRun:
    """worker 하나의 손잡이와 감독. 종결(`on_finished`)은 감독 스레드만 한 번 알린다.

    잠금 순서는 서비스 → 전송이다(`RunListener`). `cancel` 은 서비스 잠금 안에서 이 객체의 잠금을
    잡으므로, 감독 스레드는 이 객체나 전송의 잠금을 쥔 채 listener 를 부르지 않는다.
    """

    def __init__(
        self,
        job: RunJob,
        listener: RunListener,
        *,
        settings: WorkerSettings,
        versions: VersionBundle,
        discard_unfinished: Callable[[str], None],
        engine_gate: EngineGate | None,
        command: tuple[str, ...],
        assign: Callable[[subprocess.Popen[bytes], str], None],
        forget: Callable[[_WorkerRun], None],
    ) -> None:
        self._job = job
        self._listener = listener
        self._settings = settings
        self._versions = versions
        self._discard_unfinished = discard_unfinished
        self._engine_gate = engine_gate
        self._command = command
        self._assign = assign
        self._forget = forget
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = False
        self._abandoned = False

    @property
    def run_id(self) -> str:
        return self._job.run_id

    def cancel(self) -> None:
        """worker 종료를 요청만 한다(D5 1). 기다리지 않는다 — 결말은 감독 스레드가 EOF 를 보고
        알린다."""
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            process.kill()

    def abandon(self) -> None:
        """서버 종료: worker 를 끝내되 종결을 알리지 않는다."""
        with self._lock:
            self._abandoned = True
        self.cancel()

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def supervise(self) -> None:
        try:
            outcome = self._supervise()
        except Exception as error:
            logger.exception("run worker supervision failed — run_id=%s", self.run_id)
            outcome = RunFailed(
                "backtest.run.internal",
                f"run worker could not be supervised — run_id={self.run_id} "
                f"error={type(error).__name__}",
                exception=error,
            )
        try:
            with self._lock:
                abandoned = self._abandoned
            if not abandoned:
                self._listener.on_finished(outcome)
        finally:
            self._forget(self)

    def _supervise(self) -> RunOutcome:
        run_id = self.run_id
        process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_CREATION_FLAGS,
            start_new_session=sys.platform != "win32",
        )
        relay: Thread | None = None
        stdin: IO[bytes] | None = None
        outcome: RunOutcome | None = None
        try:
            self._assign(process, run_id)
            with self._lock:
                self._process = process
                cancelled = self._cancelled
            if cancelled:
                process.kill()
            stdin, stdout, stderr = _pipes(process)
            relay = Thread(
                target=_relay_stderr,
                args=(stderr, run_id),
                name=f"backtest-worker-{run_id}-stderr",
                daemon=True,
            )
            relay.start()
            # job 을 쓰기 직전에 플래그를 다시 본다(D5 1). 섰으면 쓰지 않고 그 worker 를 버린다.
            if self._is_cancelled():
                process.kill()
            else:
                _write(stdin, encode_job(self._job, self._settings))
            reading = self._read_events(process, stdin, stdout)
            outcome = self._outcome(reading, process.wait())
            return outcome
        finally:
            # 감독이 예외로 멈춰도 같은 순서로 치운다: 끝내고 기다린 뒤에만 지운다(D5 2).
            self._clean_up(process, relay, stdin, outcome)

    def _clean_up(
        self,
        process: subprocess.Popen[bytes],
        relay: Thread | None,
        stdin: IO[bytes] | None,
        outcome: RunOutcome | None,
    ) -> None:
        run_id = self.run_id
        if outcome is None:
            process.kill()
        process.wait()
        if relay is not None:
            relay.join()
        if stdin is not None:
            _close(stdin)
        with self._lock:
            handed_over = isinstance(outcome, RunCommitted) and not self._abandoned
        if handed_over:
            # 완료로 넘기는 결과는 서비스가 취소와 선형화한 뒤 지우거나 남긴다.
            return
        # 프로세스가 끝난 뒤에만 지운다(rename·열린 파일 경합, D5 2).
        try:
            self._discard_unfinished(run_id)
        except OSError:
            logger.exception("run worker leftovers not removed — run_id=%s", run_id)

    def _outcome(self, reading: _Reading, exit_code: int) -> RunOutcome:
        run_id = self.run_id
        if reading.mismatched:
            return RunFailed(
                "backtest.run.worker_mismatch",
                "run worker read other data, rule or code versions than the server — "
                f"run_id={run_id} mismatched={','.join(reading.mismatched)}; restart the server",
            )
        if reading.broken is not None:
            if reading.broken.reason == "version_mismatch":
                return RunFailed(
                    "backtest.run.worker_mismatch",
                    "run worker speaks another protocol version than the server — "
                    f"run_id={run_id}; restart the server",
                )
            # 줄 발췌는 로그에만 남겼다. 상태에는 종류와 메시지 type 만 싣고, type 은 이 판본이 아는
            # 이름이 아니면 `<unknown>` 으로 접혀 있다(#575 리뷰 P3, #581 리뷰 nit).
            return RunFailed(
                "backtest.run.internal",
                f"run worker broke the protocol — run_id={run_id} "
                f"reason={reading.broken.reason} type={reading.broken.message_type} "
                f"exit_code={exit_code}",
            )
        terminal = reading.terminal
        if isinstance(terminal, CommittedMessage):
            logger.info(
                "run worker committed — run_id=%s max_rss_bytes=%s", run_id, terminal.max_rss_bytes
            )
            return terminal.committed
        if isinstance(terminal, FailedMessage):
            logger.info(
                "run worker failed — run_id=%s error_code=%s max_rss_bytes=%s",
                run_id,
                terminal.error_code,
                terminal.max_rss_bytes,
            )
            return RunFailed(terminal.error_code, terminal.error)
        if self._is_cancelled():
            return RunCancelled()
        # stderr 원문과 서버 경로는 로그에만 있다(error-messages.md).
        return RunFailed(
            "backtest.run.worker_exited",
            f"run worker exited without a result — run_id={run_id} exit_code={exit_code} "
            f"last_stage={reading.last_stage}",
        )

    def _read_events(
        self, process: subprocess.Popen[bytes], stdin: IO[bytes], stdout: IO[bytes]
    ) -> _Reading:
        """stdout 을 EOF 까지 읽는다. 버릴 worker 면 끝내라고 한 뒤에도 남은 줄을 버리며 읽는다."""
        reading = _Reading()
        for raw in stdout:
            if reading.discarding:
                continue
            try:
                event = decode_event(raw.decode("utf-8"))
                if not reading.ready and not isinstance(event, ReadyMessage):
                    # 판본을 대조하기 전의 진행·종결은 믿을 수 없다(D6, #581 리뷰 nit).
                    raise ProtocolError(
                        "before_ready",
                        "run worker sent an event before its ready event — "
                        f"type={type(event).__name__}",
                    )
                if reading.terminal is not None:
                    raise ProtocolError(
                        "after_terminal",
                        "run worker sent an event after its terminal event — "
                        f"type={type(event).__name__}",
                    )
            except (ProtocolError, UnicodeDecodeError) as error:
                broken = (
                    error
                    if isinstance(error, ProtocolError)
                    else ProtocolError("not_utf8", f"run worker line is not UTF-8 — {error}")
                )
                logger.error("run worker broke the protocol — run_id=%s %s", self.run_id, broken)
                reading.broken = broken
                process.kill()
                continue
            if isinstance(event, ReadyMessage):
                reading.ready = True
                mismatched = self._versions.mismatched(event.versions)
                if mismatched:
                    logger.error(
                        "run worker versions differ from the server — "
                        "run_id=%s server=%s worker=%s",
                        self.run_id,
                        self._versions,
                        event.versions,
                    )
                    reading.mismatched = mismatched
                    process.kill()
            elif isinstance(event, ProgressMessage):
                reading.last_stage = event.stage
                try:
                    self._listener.on_progress(event.stage, event.fraction, event.message)
                except RunCancelledError:
                    # 취소가 이겼다. 읽기를 멈추면 worker 가 쓰기에서 막히므로 끝까지 읽는다(D12).
                    pass
            elif isinstance(event, EngineReadyMessage):
                self._release_engine(process, stdin, reading)
            else:
                reading.terminal = event
        return reading

    def _release_engine(
        self, process: subprocess.Popen[bytes], stdin: IO[bytes], reading: _Reading
    ) -> None:
        """e2e hold 판정 뒤 `proceed` 를 보낸다(D11). 관문을 켜지 않은 worker 가 물으면 깨졌다."""
        if self._engine_gate is None:
            reading.broken = ProtocolError(
                "unexpected_type",
                "run worker asked for the engine gate it was not given",
                message_type="engine_ready",
            )
            logger.error(
                "run worker broke the protocol — run_id=%s %s", self.run_id, reading.broken
            )
            process.kill()
            return
        if self._engine_gate(self.run_id, self._is_cancelled) and not self._is_cancelled():
            _write(stdin, encode_proceed())


class _Reading:
    """stdout 을 읽으며 모은 것."""

    def __init__(self) -> None:
        self.ready = False
        self.terminal: CommittedMessage | FailedMessage | None = None
        self.broken: ProtocolError | None = None
        self.mismatched: tuple[str, ...] = ()
        self.last_stage: RunStage | None = None

    @property
    def discarding(self) -> bool:
        return self.broken is not None or bool(self.mismatched)


def _pipes(process: subprocess.Popen[bytes]) -> tuple[IO[bytes], IO[bytes], IO[bytes]]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError(f"run worker pipes were not opened — pid={process.pid}")
    return process.stdin, process.stdout, process.stderr


def _write(stdin: IO[bytes], line: str) -> None:
    try:
        stdin.write(line.encode("utf-8"))
        stdin.flush()
    except OSError:
        # worker 가 먼저 끝났다. 결말은 stdout EOF 와 종료 코드가 정한다.
        logger.warning("run worker closed stdin before a line was written", exc_info=True)


def _close(stream: IO[bytes]) -> None:
    try:
        stream.close()
    except OSError:
        logger.debug("run worker stdin close failed", exc_info=True)


def _relay_stderr(stream: IO[bytes], run_id: str) -> None:
    """worker 로그 줄을 run_id 를 붙여 이 프로세스 로거로 옮긴다.

    레벨은 줄 머리를 따르고, 머리가 없는 줄은 앞 줄의 레벨을 잇는다. 머리 없이 시작하는
    traceback(로깅 전에 죽은 worker)은 ERROR 다(#575 리뷰 P3).
    """
    level = logging.WARNING
    for raw in stream:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line.startswith("Traceback ("):
            level = logging.ERROR
        else:
            level = _LOG_LEVELS.get(line.split(" ", 1)[0], level)
        logger.log(level, "run worker — run_id=%s %s", run_id, line)
