"""백테스트 run 을 원시 관측 로딩 입구에 붙잡아 두거나, 종결 상태에 이를 때까지(필요하면 run
스레드가 끝날 때까지) 기다리는 테스트 헬퍼.

시작 요청이 즉시 202 를 돌려주고 TargetTape 까지 run 스레드에서 만들어지므로(#158) 모든 실행
테스트가 종결 대기를 필요로 한다. 예산·폴링 간격·타임아웃 메시지를 한 곳에 둔다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunState,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_POLL_INTERVAL_S = 0.01


class RawLoadBarrier:
    """tape 단계의 원시 관측 로딩 입구에서 `release` 까지 멈추는 테스트용 관측 포트.

    입구에서 `on_enter(query)` 를 부르고 `entered` 를 올린 뒤 기다린다. 풀리면 `failure` 가 있으면
    그 예외를 던지고(데이터 부재 경로), 없으면 mock 어댑터에 맡긴다. 어댑터의 checkpoint 가 취소
    플래그를 보므로 "로딩 도중 취소" 경로를 그대로 탄다.
    """

    def __init__(
        self,
        delegate: MockEquityDataAdapter,
        *,
        failure: Exception | None = None,
        on_enter: Callable[[RawObservationQuery], None] = lambda _query: None,
    ) -> None:
        self._delegate = delegate
        self._failure = failure
        self._on_enter = on_enter
        self.entered = threading.Event()
        self.release = threading.Event()
        self.loaded = False

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self, query: RawObservationQuery, *, checkpoint: Callable[[], None]
    ) -> RawObservationSet:
        self._on_enter(query)
        self.entered.set()
        if not self.release.wait(timeout=30):
            raise TimeoutError("raw observation test barrier was not released")
        if self._failure is not None:
            raise self._failure
        result = self._delegate.load_raw_observations_cancellable(query, checkpoint=checkpoint)
        self.loaded = True
        return result


def wait_for_terminal_state(
    client: TestClient, run_id: str, *, timeout_s: float = 10.0
) -> dict[str, Any]:
    """HTTP 상태 폴링. 종결되지 않으면 마지막 상태를 실은 AssertionError."""

    deadline = time.monotonic() + timeout_s
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        if state.get("status") in TERMINAL_STATUSES:
            return state
        time.sleep(_POLL_INTERVAL_S)
    raise AssertionError(f"run did not finish within {timeout_s}s — run_id={run_id} state={state}")


def wait_for_terminal_run(
    backtests: BacktestRunService, run_id: str, *, timeout_s: float = 10.0
) -> BacktestRunState:
    """서비스 직접 폴링 판. 종결되지 않으면 마지막 상태를 실은 AssertionError."""

    deadline = time.monotonic() + timeout_s
    state = backtests.state(run_id)
    while state.status.value not in TERMINAL_STATUSES:
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"run did not finish within {timeout_s}s — run_id={run_id} state={state}"
            )
        time.sleep(_POLL_INTERVAL_S)
        state = backtests.state(run_id)
    return state


def join_run_thread(run_id: str, *, timeout_s: float = 10.0) -> None:
    """run 스레드(`backtest-{run_id}`)가 끝날 때까지 기다린다.

    종결 상태는 run 스레드가 GC 범위를 나오고 자리를 내놓기 전에 기록된다. 그 뒤의 일(GC 임계값
    복원·대기열의 다음 run 띄우기)을 단언하려면 상태가 아니라 스레드를 기다려야 한다.
    """

    for thread in threading.enumerate():
        if thread.name == f"backtest-{run_id}":
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                raise AssertionError(
                    f"run thread did not exit within {timeout_s}s — run_id={run_id}"
                )
