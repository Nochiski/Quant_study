"""실행 종결 판정 — 저장소·취소·진행 스트림이 공유하는 도메인 계약."""

import pytest

from strategy_workbench.domain.backtest.facade.runs import RunStatus


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        (RunStatus.QUEUED, False),
        (RunStatus.RUNNING, False),
        (RunStatus.CANCEL_REQUESTED, False),
        (RunStatus.CANCELLED, True),
        (RunStatus.COMPLETED, True),
        (RunStatus.FAILED, True),
    ],
)
def test_run_status_terminal_contract(status: RunStatus, terminal: bool) -> None:
    assert status.is_terminal is terminal
