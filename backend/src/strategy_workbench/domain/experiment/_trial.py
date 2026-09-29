"""trial·실험 상태 — 저장하지 않고 attempt 와 그 실행의 상태에서 파생한다(spec D5·D6).

전진 전용의 단위는 attempt(trial 의 실행 시도 하나)이고, trial 이 보이는 상태는 최신 attempt 에서
파생한다. 실행에 배정된 attempt 의 상태는 그 실행의 `RunStatus` 다. 그래서 재시도와 재시작 복구는
상태를 되돌리지 않고 새 attempt 를 만든다(V3-03·V3-04).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from strategy_workbench.domain.backtest.facade.runs import RunStatus


class TrialStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.CANCELLED)


# 취소를 요청한 실행은 아직 끝나지 않았으므로 도는 중이다.
_RUN_TRIAL_STATUSES: dict[RunStatus, TrialStatus] = {
    RunStatus.QUEUED: TrialStatus.QUEUED,
    RunStatus.RUNNING: TrialStatus.RUNNING,
    RunStatus.CANCEL_REQUESTED: TrialStatus.RUNNING,
    RunStatus.COMPLETED: TrialStatus.COMPLETED,
    RunStatus.FAILED: TrialStatus.FAILED,
    RunStatus.CANCELLED: TrialStatus.CANCELLED,
}


class ExperimentStatus(StrEnum):
    """실험 단위 상태. trial 상태와 실험 취소에서 파생하고 따로 저장하지 않는다(spec D6)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def trial_status(
    latest_run: RunStatus | None, *, attempted: bool, experiment_cancelled: bool
) -> TrialStatus:
    """최신 attempt 에서 trial 상태를 파생한다.

    Args:
        latest_run: 최신 attempt 가 배정된 실행의 상태. 접수가 거절돼 실행이 없으면 None.
        attempted: attempt 가 하나라도 있는가. 없으면 대기(실험을 취소했으면 취소)다.
    """
    if not attempted:
        return TrialStatus.CANCELLED if experiment_cancelled else TrialStatus.QUEUED
    if latest_run is None:
        return TrialStatus.FAILED
    return _RUN_TRIAL_STATUSES[latest_run]


def experiment_status(trials: Sequence[TrialStatus], *, cancelled: bool) -> ExperimentStatus:
    """취소한 실험은 취소, trial 이 모두 끝났으면 완료, 하나도 시작하지 않았으면 대기다."""
    if cancelled:
        return ExperimentStatus.CANCELLED
    if all(status.is_terminal for status in trials):
        return ExperimentStatus.COMPLETED
    if all(status is TrialStatus.QUEUED for status in trials):
        return ExperimentStatus.QUEUED
    return ExperimentStatus.RUNNING
