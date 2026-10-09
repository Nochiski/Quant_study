"""백테스트 run 이 종결 상태에 이를 때까지 기다리는 테스트 헬퍼.

시작 요청이 즉시 202 를 돌려주고 TargetTape 까지 run 스레드에서 만들어지므로(#158) 모든 실행
테스트가 종결 대기를 필요로 한다. 예산·폴링 간격·타임아웃 메시지를 한 곳에 둔다.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunState,
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_POLL_INTERVAL_S = 0.01


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
