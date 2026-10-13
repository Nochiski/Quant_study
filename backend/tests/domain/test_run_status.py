"""실행 종결 판정 — 저장소·취소·진행 스트림이 공유하는 도메인 계약."""

import pytest

from strategy_workbench.domain.backtest.facade.runs import RunStatus

_TERMINAL_CASES = {
    RunStatus.QUEUED: False,
    RunStatus.RUNNING: False,
    RunStatus.CANCEL_REQUESTED: False,
    RunStatus.CANCELLED: True,
    RunStatus.COMPLETED: True,
    RunStatus.FAILED: True,
}


def test_terminal_examples_cover_every_run_status() -> None:
    assert set(_TERMINAL_CASES) == set(RunStatus)


@pytest.mark.parametrize(("status", "terminal"), _TERMINAL_CASES.items())
def test_run_status_terminal_contract(status: RunStatus, terminal: bool) -> None:
    assert status.is_terminal is terminal
