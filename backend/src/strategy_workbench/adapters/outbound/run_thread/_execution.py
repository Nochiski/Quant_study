from __future__ import annotations

from threading import Event, Thread

from strategy_workbench.application.backtest_run.facade.computation import (
    RunComputation,
    full_collections_suspended,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    RunHandle,
    RunJob,
    RunListener,
    RunMemory,
)


class _ThreadRun:
    """도는 run 하나의 손잡이. 취소는 계산이 checkpoint 에서 보는 신호를 세우기만 한다."""

    def __init__(self) -> None:
        self.cancellation = Event()

    def cancel(self) -> None:
        self.cancellation.set()


class ThreadRunExecution:
    """run 하나를 이 프로세스의 데몬 스레드(`backtest-<run_id>`)에서 돌리는 전송(spec D9·D13).

    run 스레드가 곧 감독 스레드다. 계산과 진행·종결 알림을 그 스레드에서 차례로 부른다. 취소는
    협조적이다 — 계산이 이 run 의 신호를 checkpoint 에서 보거나, 진행 알림이 취소를 보고
    `RunCancelledError` 를 내면 그 자리에서 멈춘다.
    """

    def __init__(self, computation: RunComputation) -> None:
        self._computation = computation

    def start(self, job: RunJob, listener: RunListener) -> RunHandle:
        handle = _ThreadRun()
        Thread(
            target=self._run,
            args=(job, listener, handle.cancellation),
            name=f"backtest-{job.run_id}",
            daemon=True,
        ).start()
        return handle

    def memory(self) -> RunMemory | None:
        """run 이 모두 이 프로세스에 있어 run 마다 메모리를 따로 잴 수 없다 — 메모리 가드를 쓰지
        않는다(spec D8, 가드는 worker 프로세스 전송의 것이다)."""
        return None

    def set_memory_hold(self, held: bool) -> None:
        """예비가 없어 미룰 것이 없다."""

    def shutdown(self) -> None:
        """도는 스레드는 데몬이라 프로세스와 함께 끝난다 — 따로 정리할 것이 없다."""

    def _run(self, job: RunJob, listener: RunListener, cancellation: Event) -> None:
        # 전체 수집(2세대)을 계산이 끝날 때까지 미룬다(이슈 #196). GC 설정은 프로세스 전역이라
        # 같은 프로세스에서 계산하는 이 전송이 계산 구간을 감싼다. 범위와 복원 규칙은 `_gc_policy`
        # 가 소유한다.
        with full_collections_suspended():
            outcome = self._computation.compute(
                job, progress=listener.on_progress, cancelled=cancellation.is_set
            )
        listener.on_finished(outcome)
