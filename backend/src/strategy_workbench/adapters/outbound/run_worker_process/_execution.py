"""부모 쪽 전송: run 하나를 worker 프로세스 하나에서 돌린다(run-worker spec D1·D2·D5·D6·D7·D11·D13).

run 마다 감독 스레드 하나가 worker 를 손에 넣고 job 을 쓰고 stdout 이벤트를 끝까지 읽어 listener 에
옮긴다. 그 run 의 종결은 이 스레드 하나만 알린다. stderr 는 따로 읽는 스레드가 run_id 를 붙여 로그로
중계한다. 두 파이프를 모두 끝까지 읽어 worker 가 쓰기에서 막히지 않게 한다(교착 방지, D3).

worker 는 `boot` 줄로 부팅해 `ready` 를 보내고, 부모가 판본을 대조한 뒤에야 job 을 받는다.
전송은 그렇게 `ready` 까지 부팅해 둔 예비 worker 를 슬롯 수와 무관하게 늘 하나 둔다(D7). run 은
준비된 예비가 있으면 그것을 쓰고 곧바로 다음 예비를 띄운다. 예비가 아직 부팅 중이거나 없으면
감독 스레드가 직접 띄운다. 그래서 worker 프로세스는 도는 run 수 + 1 개를 넘지 않는다.

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
from threading import Event, Lock, Thread
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
    encode_boot,
    encode_job,
    encode_proceed,
    event_type,
)

logger = logging.getLogger(__name__)

WORKER_COMMAND: tuple[str, ...] = (sys.executable, "-m", "strategy_workbench.bootstrap.run_worker")

# 개발 터미널의 Ctrl+C 가 worker 에 직접 닿지 않게 프로세스 그룹(Windows)·세션(POSIX)을 나눈다
# (spec D2).
_CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
_LOG_LEVELS = logging.getLevelNamesMapping()
# 서버 종료 때 감독·예비 스레드를 기다리는 상한. worker 는 이미 끝내라고 했으니 EOF 처리만 남는다.
_SHUTDOWN_JOIN_S = 10.0
# 예비가 부팅에 실패하거나 판본이 어긋나거나 기다리다 죽는 일이 이만큼 이어지면 예비 띄우기를 멈춘다
# (spec D7). 그 뒤로는 run 마다 감독 스레드가 worker 를 띄운다.
SPARE_FAILURE_LIMIT = 3
# 기다리는 예비가 살아 있는지 다시 보는 간격. 죽은 예비를 다시 띄우기까지의 지연이다.
_SPARE_POLL_S = 0.2

EngineGate = Callable[[str, Callable[[], bool]], bool]
"""(run_id, 취소 확인) → 엔진 단계로 보내도 되면 참. e2e hold 판정이다(spec D11)."""


class ProcessRunExecution:
    """run 하나를 worker 프로세스 하나에서 돌리는 전송. 끝나면 worker 는 스스로 끝난다(D1).

    `start` 는 감독 스레드만 띄우고 바로 돌아온다 — 서비스 잠금 안에서 불리므로 `Popen`·파이프
    쓰기·`ready` 대기를 여기서 하지 않는다(D12). `versions` 는 서버가 부팅 때 계산한 판본 묶음이고
    worker 의 `ready` 와 대조한다(D6). `discard_unfinished` 는 완료로 넘기지 않는 run 의 산출물
    흔적을 지운다(D5).

    만들 때 예비 worker 를 띄우기 시작한다(D7). `spare=False` 는 예비 없이 run 마다 감독 스레드가
    띄우는 경로만 볼 때(테스트·측정) 끈다. `command` 는 테스트가 worker 진입점을 감쌀 때만 바꾼다.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        versions: VersionBundle,
        discard_unfinished: Callable[[str], None],
        engine_gate: EngineGate | None = None,
        command: Sequence[str] = WORKER_COMMAND,
        spare: bool = True,
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
        self._keeps_spare = spare
        self._spare: _Spare | None = None
        self._spare_failures = 0
        self._fill_spare()

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
            take_spare=self._take_spare,
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
        """도는 worker 와 예비를 끝내고 그 스레드들을 기다린다. 종결은 알리지 않는다(다음 부팅
        몫)."""
        with self._lock:
            self._closed = True
            runs = dict(self._runs)
            spare, self._spare = self._spare, None
            spare_worker = None if spare is None else spare.worker
        if spare is not None:
            spare.left.set()
        if spare_worker is not None:
            spare_worker.process.kill()
        for run in runs:
            run.abandon()
        if spare is not None:
            spare.thread.join(_SHUTDOWN_JOIN_S)
            if spare.thread.is_alive():
                logger.error(
                    "run worker spare did not stop on shutdown — waited_s=%s", _SHUTDOWN_JOIN_S
                )
        for run, thread in runs.items():
            thread.join(_SHUTDOWN_JOIN_S)
            if thread.is_alive():
                logger.error(
                    "run worker supervisor did not stop on shutdown — run_id=%s waited_s=%s",
                    run.run_id,
                    _SHUTDOWN_JOIN_S,
                )

    def spare_pid(self) -> int | None:
        """준비된(`ready`·판본 일치) 예비 worker 의 pid. 없거나 부팅 중이면 None 이다.

        진단·측정용 조회다. 아무것도 바꾸지 않는다.
        """
        with self._lock:
            spare = self._spare
            worker = None if spare is None or not spare.ready else spare.worker
            return None if worker is None else worker.process.pid

    def _fill_spare(self) -> None:
        """예비 자리가 비었으면 새 예비를 띄운다(D7). 띄우기·부팅·대기는 예비 스레드가 한다(D12).

        연속 실패로 멈춘 뒤에는 `_spare_failed` 가 더 부르지 않는다. 예비를 run 에 넘길 때(실패
        횟수가 0 으로 돌아간 뒤)와 만들 때만 여기로 온다.
        """
        with self._lock:
            if not self._keeps_spare or self._closed or self._spare is not None:
                return
            spare = _Spare()
            spare.thread = Thread(
                target=self._keep_spare, args=(spare,), name="backtest-worker-spare", daemon=True
            )
            self._spare = spare
            try:
                spare.thread.start()
            except RuntimeError:
                # 스레드를 못 띄우면 예비 없이 간다. run 은 감독 스레드가 직접 띄운다.
                self._spare = None
                logger.exception("run worker spare thread could not be started")

    def _keep_spare(self, spare: _Spare) -> None:
        """예비 하나를 띄워 `ready` 까지 부팅하고, 쓰이거나 서버가 끝날 때까지 살아 있는지 본다."""
        try:
            worker = _Worker.spawn(self._command, self._settings, self._assign, label="spare")
        except Exception:
            logger.exception("run worker spare could not be started")
            self._spare_failed(spare)
            return
        with self._lock:
            spare.worker = worker
            kept = self._spare is spare
        if not kept:
            # 띄우는 동안 서버가 끝나기 시작했다.
            worker.process.kill()
            worker.close()
            return
        worker.label = f"spare pid={worker.process.pid}"
        worker.boot(self._versions)
        if worker.booted:
            with self._lock:
                if self._spare is spare:
                    spare.ready = True
                    self._spare_failures = 0
            # 쓰이거나(`left`) 죽을 때까지 기다린다. 기다리는 동안 worker 는 아무것도 쓰지 않는다.
            while not spare.left.wait(_SPARE_POLL_S):
                if worker.process.poll() is not None:
                    break
            with self._lock:
                if spare.taken:
                    # run 의 감독 스레드가 이 worker 를 맡았다.
                    return
        worker.process.kill()
        exit_code = worker.close()
        with self._lock:
            kept = self._spare is spare
        if kept:
            reading = worker.reading
            logger.warning(
                "run worker spare is gone — pid=%s exit_code=%s phase=%s reason=%s",
                worker.process.pid,
                exit_code,
                "waiting" if worker.booted else "boot",
                _boot_failure(reading),
            )
        self._spare_failed(spare)

    def _spare_failed(self, spare: _Spare) -> None:
        """예비 하나를 잃었다. 서버 종료로 버린 예비가 아니면 세고 다시 띄운다(D7)."""
        with self._lock:
            if self._spare is not spare:
                return
            self._spare = None
            self._spare_failures += 1
            failures = self._spare_failures
        if failures >= SPARE_FAILURE_LIMIT:
            logger.error(
                "run worker spares stopped after consecutive failures — failures=%s; "
                "each run now starts its own worker",
                failures,
            )
            return
        self._fill_spare()

    def _take_spare(self) -> _Worker | None:
        """준비된 예비를 run 에 넘기고 다음 예비를 띄운다. 준비된 예비가 없으면 None 이다(D7)."""
        with self._lock:
            spare = self._spare
            worker = None if spare is None else spare.worker
            if (
                spare is None
                or worker is None
                or not spare.ready
                or worker.process.poll() is not None
            ):
                return None
            spare.taken = True
            self._spare = None
        spare.left.set()
        self._fill_spare()
        return worker

    def _assign(self, process: subprocess.Popen[bytes], label: str) -> None:
        if self._job is None:
            return
        try:
            self._job.assign(process.pid)
        except OSError:
            # stdin EOF 감시가 보조 경로로 남는다(spec D5).
            logger.warning(
                "run worker not placed in the kill-on-close job — %s pid=%s",
                label,
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


class _Spare:
    """예비 자리 하나(D7). `worker`·`ready`·`taken` 은 전송 잠금 안에서만 바꾼다.

    `left` 는 run 이 가져갔거나 서버가 끝나 예비 스레드가 기다림을 멈출 때 선다.
    """

    def __init__(self) -> None:
        self.worker: _Worker | None = None
        self.ready = False
        self.taken = False
        self.left = Event()
        self.thread: Thread


class _Worker:
    """worker 프로세스 하나: 파이프, stderr 중계, 부팅 결과(`reading`).

    `label` 은 로그에 붙는 이름이다. 예비일 때는 pid 이고, run 이 맡으면 그 run_id 로 바뀐다.
    """

    def __init__(self, process: subprocess.Popen[bytes], label: str) -> None:
        self.process = process
        self.label = label
        self.stdin, self.stdout, stderr = _pipes(process)
        self.reading = _Reading()
        self._relay = Thread(
            target=_relay_stderr,
            args=(stderr, self),
            name=f"backtest-worker-{process.pid}-stderr",
            daemon=True,
        )

    @classmethod
    def spawn(
        cls,
        command: tuple[str, ...],
        settings: WorkerSettings,
        assign: Callable[[subprocess.Popen[bytes], str], None],
        *,
        label: str,
    ) -> _Worker:
        """worker 를 띄우고 `boot` 줄을 쓴다. 중간에 실패하면 끝내고 기다린 뒤 올린다."""
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_CREATION_FLAGS,
            start_new_session=sys.platform != "win32",
        )
        try:
            assign(process, label)
            worker = cls(process, label)
            worker._relay.start()
        except BaseException:
            process.kill()
            process.wait()
            raise
        _write(worker.stdin, encode_boot(settings))
        return worker

    @property
    def booted(self) -> bool:
        """`ready` 를 받았고 판본이 같다 — job 을 써도 된다."""
        return self.reading.ready and not self.reading.discarding

    def boot(self, versions: VersionBundle) -> None:
        """stdout 을 `ready` 까지 읽고 판본을 대조한다(D6). 결과는 `reading` 에 남는다.

        `ready` 이고 판본이 같으면 거기서 읽기를 멈춘다 — 다음 줄(job 의 사건)은 run 의 감독이 같은
        stdout 에서 읽는다. `ready` 전의 다른 사건, 깨진 줄, 다른 판본이면 worker 를 끝내고 EOF 까지
        남은 줄을 버리며 읽는다.
        """
        reading = self.reading
        for raw in self.stdout:
            if reading.discarding:
                continue
            try:
                event = decode_event(raw.decode("utf-8"))
            except (ProtocolError, UnicodeDecodeError) as error:
                self.broke(error)
                continue
            if not isinstance(event, ReadyMessage):
                # 판본을 대조하기 전의 진행·종결은 믿을 수 없다(D6, #581 리뷰 nit).
                self.broke(
                    ProtocolError(
                        "before_ready",
                        "run worker sent an event before its ready event — "
                        f"type={event_type(event)}",
                        message_type=event_type(event),
                    )
                )
                continue
            reading.ready = True
            mismatched = versions.mismatched(event.versions)
            if not mismatched:
                return
            logger.error(
                "run worker versions differ from the server — %s server=%s worker=%s",
                self.label,
                versions,
                event.versions,
            )
            reading.mismatched = mismatched
            self.process.kill()

    def broke(self, error: ProtocolError | UnicodeDecodeError) -> None:
        """worker 가 프로토콜을 어겼다. 남기고 끝낸다 — 그 뒤의 줄은 버린다."""
        broken = (
            error
            if isinstance(error, ProtocolError)
            else ProtocolError("not_utf8", f"run worker line is not UTF-8 — {error}")
        )
        logger.error("run worker broke the protocol — %s %s", self.label, broken)
        self.reading.broken = broken
        self.process.kill()

    def close(self) -> int:
        """끝난 것을 확인하고(`wait`) stderr 를 마저 옮긴 뒤 stdin 을 닫는다. 종료 코드를
        돌려준다."""
        exit_code = self.process.wait()
        self._relay.join()
        _close(self.stdin)
        return exit_code


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
        take_spare: Callable[[], _Worker | None],
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
        self._take_spare = take_spare
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
        label = f"run_id={run_id}"
        worker = self._take_spare()
        if worker is None:
            # 준비된 예비가 없다(부팅 중이거나 멈췄다). 이 run 의 worker 를 직접 띄운다(D7).
            logger.info("run worker starts without a ready spare — %s", label)
            worker = _Worker.spawn(self._command, self._settings, self._assign, label=label)
        else:
            logger.info("run worker spare taken — %s pid=%s", label, worker.process.pid)
            worker.label = label
        outcome: RunOutcome | None = None
        try:
            with self._lock:
                self._process = worker.process
                cancelled = self._cancelled
            if cancelled:
                worker.process.kill()
            if not worker.reading.ready:
                worker.boot(self._versions)
            # job 을 쓰기 직전에 플래그를 다시 본다(D5 1). 섰으면 쓰지 않고 그 worker 를 버린다.
            if worker.booted:
                if self._is_cancelled():
                    worker.process.kill()
                else:
                    _write(worker.stdin, encode_job(self._job))
                    self._read_events(worker)
            outcome = self._outcome(worker.reading, worker.process.wait())
            return outcome
        finally:
            # 감독이 예외로 멈춰도 같은 순서로 치운다: 끝내고 기다린 뒤에만 지운다(D5 2).
            self._clean_up(worker, outcome)

    def _clean_up(self, worker: _Worker, outcome: RunOutcome | None) -> None:
        run_id = self.run_id
        if outcome is None:
            worker.process.kill()
        worker.close()
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
        # stderr 원문과 서버 경로는 로그에만 있다(error-messages.md). 부팅 중에 끝났으면 마지막
        # 단계가 없다(None).
        return RunFailed(
            "backtest.run.worker_exited",
            f"run worker exited without a result — run_id={run_id} exit_code={exit_code} "
            f"last_stage={reading.last_stage}",
        )

    def _read_events(self, worker: _Worker) -> None:
        """job 을 쓴 뒤의 stdout 을 EOF 까지 읽는다. 버릴 worker 면 끝내라고 한 뒤에도 남은 줄을
        버리며 읽는다."""
        reading = worker.reading
        for raw in worker.stdout:
            if reading.discarding:
                continue
            try:
                event = decode_event(raw.decode("utf-8"))
            except (ProtocolError, UnicodeDecodeError) as error:
                worker.broke(error)
                continue
            if isinstance(event, ReadyMessage) or reading.terminal is not None:
                worker.broke(
                    ProtocolError(
                        "unexpected_type" if isinstance(event, ReadyMessage) else "after_terminal",
                        "run worker sent an event out of order — "
                        f"type={event_type(event)} terminal={reading.terminal is not None}",
                        message_type=event_type(event),
                    )
                )
                continue
            if isinstance(event, ProgressMessage):
                reading.last_stage = event.stage
                try:
                    self._listener.on_progress(event.stage, event.fraction, event.message)
                except RunCancelledError:
                    # 취소가 이겼다. 읽기를 멈추면 worker 가 쓰기에서 막히므로 끝까지 읽는다(D12).
                    pass
            elif isinstance(event, EngineReadyMessage):
                self._release_engine(worker)
            else:
                reading.terminal = event

    def _release_engine(self, worker: _Worker) -> None:
        """e2e hold 판정 뒤 `proceed` 를 보낸다(D11). 관문을 켜지 않은 worker 가 물으면 깨졌다."""
        if self._engine_gate is None:
            worker.broke(
                ProtocolError(
                    "unexpected_type",
                    "run worker asked for the engine gate it was not given",
                    message_type="engine_ready",
                )
            )
            return
        if self._engine_gate(self.run_id, self._is_cancelled) and not self._is_cancelled():
            _write(worker.stdin, encode_proceed())


class _Reading:
    """stdout 을 읽으며 모은 것. 부팅(`ready` 까지)과 job 뒤의 사건을 한 객체에 모은다."""

    def __init__(self) -> None:
        self.ready = False
        self.terminal: CommittedMessage | FailedMessage | None = None
        self.broken: ProtocolError | None = None
        self.mismatched: tuple[str, ...] = ()
        self.last_stage: RunStage | None = None

    @property
    def discarding(self) -> bool:
        return self.broken is not None or bool(self.mismatched)


def _boot_failure(reading: _Reading) -> str:
    """예비를 잃은 사유(로그용)."""
    if reading.mismatched:
        return f"mismatched={','.join(reading.mismatched)}"
    if reading.broken is not None:
        return f"protocol={reading.broken.reason}"
    return "exited" if not reading.ready else "exited_while_waiting"


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


def _relay_stderr(stream: IO[bytes], worker: _Worker) -> None:
    """worker 로그 줄을 그 worker 의 이름(run_id 또는 예비 pid)을 붙여 이 프로세스 로거로 옮긴다.

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
        logger.log(level, "run worker — %s %s", worker.label, line)
