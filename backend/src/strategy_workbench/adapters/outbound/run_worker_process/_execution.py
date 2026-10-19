"""부모 쪽 전송: run 하나를 worker 프로세스 하나에서 돌린다(run-worker spec D1·D2·D12·D13).

run 마다 감독 스레드 하나가 worker 를 띄우고 job 을 쓰고 stdout 이벤트를 끝까지 읽어 listener 에
옮긴다. stderr 는 따로 읽는 스레드가 run_id 를 붙여 로그로 중계한다. 두 파이프를 모두 끝까지 읽어
worker 가 쓰기에서 막히지 않게 한다(교착 방지, D3).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from threading import Lock, Thread
from typing import IO

from strategy_workbench.application.backtest_run.facade.ports import (
    RunCancelled,
    RunCancelledError,
    RunFailed,
    RunHandle,
    RunJob,
    RunListener,
    RunOutcome,
)
from strategy_workbench.domain.backtest.facade.runs import RunStage

from ._protocol import (
    CommittedMessage,
    FailedMessage,
    ProgressMessage,
    ProtocolError,
    WorkerSettings,
    decode_event,
    encode_job,
)

logger = logging.getLogger(__name__)

WORKER_COMMAND: tuple[str, ...] = (sys.executable, "-m", "strategy_workbench.bootstrap.run_worker")

# 개발 터미널의 Ctrl+C 가 worker 에 직접 닿지 않게 프로세스 그룹(Windows)·세션(POSIX)을 나눈다
# (spec D2).
_CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
_LOG_LEVELS = logging.getLevelNamesMapping()


class ProcessRunExecution:
    """run 하나를 worker 프로세스 하나에서 돌리는 전송. 끝나면 worker 는 스스로 끝난다(D1).

    `start` 는 감독 스레드만 띄우고 바로 돌아온다 — 서비스 잠금 안에서 불리므로 `Popen`·파이프
    쓰기·`ready` 대기를 여기서 하지 않는다(D12). `command` 는 테스트가 worker 진입점을 감쌀 때만
    바꾼다.
    """

    def __init__(
        self, settings: WorkerSettings, *, command: Sequence[str] = WORKER_COMMAND
    ) -> None:
        self._settings = settings
        self._command = tuple(command)
        self._lock = Lock()
        self._runs: set[_WorkerRun] = set()

    def start(self, job: RunJob, listener: RunListener) -> RunHandle:
        run = _WorkerRun(job, self._settings, self._command, listener, self._forget)
        with self._lock:
            self._runs.add(run)
        try:
            Thread(target=run.supervise, name=f"backtest-worker-{job.run_id}", daemon=True).start()
        except BaseException:
            self._forget(run)
            raise
        return run

    def shutdown(self) -> None:
        """도는 worker 에 종료를 요청한다. 완전한 종결 순서는 W1-03 이 맡는다."""
        with self._lock:
            runs = tuple(self._runs)
        for run in runs:
            run.cancel()

    def _forget(self, run: _WorkerRun) -> None:
        with self._lock:
            self._runs.discard(run)


class _WorkerRun:
    """worker 하나의 손잡이와 감독. 종결(`on_finished`)은 감독 스레드만 한 번 알린다.

    잠금 순서는 서비스 → 전송이다(`RunListener`). `cancel` 은 서비스 잠금 안에서 이 객체의 잠금을
    잡으므로, 감독 스레드는 이 객체나 전송의 잠금을 쥔 채 listener 를 부르지 않는다.
    """

    def __init__(
        self,
        job: RunJob,
        settings: WorkerSettings,
        command: tuple[str, ...],
        listener: RunListener,
        forget: Callable[[_WorkerRun], None],
    ) -> None:
        self._job = job
        self._settings = settings
        self._command = command
        self._listener = listener
        self._forget = forget
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """worker 종료를 요청만 한다. 기다리지 않는다 — 결말은 감독 스레드가 EOF 를 보고 알린다."""
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            process.kill()

    def supervise(self) -> None:
        try:
            outcome = self._supervise()
        except Exception as error:
            logger.exception("run worker supervision failed — run_id=%s", self._job.run_id)
            outcome = RunFailed(
                "backtest.run.internal",
                f"run worker could not be supervised — run_id={self._job.run_id} "
                f"error={type(error).__name__}",
                exception=error,
            )
        try:
            self._listener.on_finished(outcome)
        finally:
            self._forget(self)

    def _supervise(self) -> RunOutcome:
        run_id = self._job.run_id
        process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_CREATION_FLAGS,
            start_new_session=sys.platform != "win32",
        )
        with self._lock:
            self._process = process
            cancelled = self._cancelled
        stdin, stdout, stderr = _pipes(process)
        relay = Thread(
            target=_relay_stderr, args=(stderr, run_id), name=f"backtest-worker-{run_id}-stderr"
        )
        relay.daemon = True
        relay.start()
        try:
            if cancelled:
                process.kill()
            else:
                _write_job(stdin, encode_job(self._job, self._settings))
            terminal, broken, last_stage = self._read_events(process, stdout)
            exit_code = process.wait()
        except BaseException:
            process.kill()
            raise
        finally:
            relay.join()
            _close(stdin)
        if broken is not None:
            return RunFailed(
                "backtest.run.internal",
                f"run worker broke the protocol — run_id={run_id} exit_code={exit_code} "
                f"last_stage={last_stage} error={broken}",
            )
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
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            return RunCancelled()
        # 코드는 W1-03 이 `backtest.run.worker_exited` 로 바꾼다(spec D5).
        return RunFailed(
            "backtest.run.internal",
            f"run worker exited without a result — run_id={run_id} exit_code={exit_code} "
            f"last_stage={last_stage}",
        )

    def _read_events(
        self, process: subprocess.Popen[bytes], stdout: IO[bytes]
    ) -> tuple[CommittedMessage | FailedMessage | None, ProtocolError | None, RunStage | None]:
        """stdout 을 EOF 까지 읽는다. 프로토콜이 깨지면 worker 를 끝내고 남은 줄은 버리며 읽는다."""
        terminal: CommittedMessage | FailedMessage | None = None
        broken: ProtocolError | None = None
        last_stage: RunStage | None = None
        for raw in stdout:
            if broken is not None:
                continue
            try:
                event = decode_event(raw.decode("utf-8"))
                if terminal is not None:
                    raise ProtocolError(
                        "run worker sent an event after its terminal event — "
                        f"type={type(event).__name__}"
                    )
            except (ProtocolError, UnicodeDecodeError) as error:
                broken = error if isinstance(error, ProtocolError) else ProtocolError(str(error))
                process.kill()
                continue
            if isinstance(event, ProgressMessage):
                last_stage = event.stage
                try:
                    self._listener.on_progress(event.stage, event.fraction, event.message)
                except RunCancelledError:
                    # 취소가 이겼다. 읽기를 멈추면 worker 가 쓰기에서 막히므로 끝까지 읽는다(D12).
                    pass
            elif isinstance(event, (CommittedMessage, FailedMessage)):
                terminal = event
            # `ready` 의 판본 묶음 대조는 W1-03 이 한다(spec D6).
        return terminal, broken, last_stage


def _pipes(process: subprocess.Popen[bytes]) -> tuple[IO[bytes], IO[bytes], IO[bytes]]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError(f"run worker pipes were not opened — pid={process.pid}")
    return process.stdin, process.stdout, process.stderr


def _write_job(stdin: IO[bytes], line: str) -> None:
    try:
        stdin.write(line.encode("utf-8"))
        stdin.flush()
    except OSError:
        # worker 가 job 을 읽기 전에 끝났다. 결말은 stdout EOF 와 종료 코드가 정한다.
        logger.warning("run worker closed stdin before the job was written", exc_info=True)


def _close(stream: IO[bytes]) -> None:
    try:
        stream.close()
    except OSError:
        logger.debug("run worker stdin close failed", exc_info=True)


def _relay_stderr(stream: IO[bytes], run_id: str) -> None:
    """worker 로그 줄을 run_id 를 붙여 이 프로세스 로거로 옮긴다. 레벨은 줄 머리를 따르고, 머리가
    없는 줄(traceback 이어짐)은 앞 줄의 레벨을 잇는다."""
    level = logging.WARNING
    for raw in stream:
        line = raw.decode("utf-8", errors="replace").rstrip()
        level = _LOG_LEVELS.get(line.split(" ", 1)[0], level)
        logger.log(level, "run worker — run_id=%s %s", run_id, line)
