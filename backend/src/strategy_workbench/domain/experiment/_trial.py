"""trial 상태 전이 — 앞으로만 가고 종결 상태는 바뀌지 않는다(spec D5, 로드맵 10절).

전진 전용 전이의 단위는 attempt(trial 의 실행 시도 하나)이고, trial 이 보이는 상태는 최신 attempt 의
상태에서 파생한다. 실행에 배정된 attempt 의 상태는 따로 저장하지 않고 그 실행의 `RunStatus` 에서
읽는다(`trial_status_of_run`). 그래서 재시도와 재시작 복구는 상태를 되돌리지 않고 새 attempt 를
만든다(V3-03·V3-04).
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
        return not _NEXT_STATUSES[self]


# 상태마다 갈 수 있는 다음 상태. 대기 중 취소는 실행 없이 끝난다. 종결 상태는 빈 집합이다.
_NEXT_STATUSES: dict[TrialStatus, frozenset[TrialStatus]] = {
    TrialStatus.QUEUED: frozenset({TrialStatus.RUNNING, TrialStatus.CANCELLED}),
    TrialStatus.RUNNING: frozenset(
        {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.CANCELLED}
    ),
    TrialStatus.COMPLETED: frozenset(),
    TrialStatus.FAILED: frozenset(),
    TrialStatus.CANCELLED: frozenset(),
}

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


def advance_trial_status(current: TrialStatus, target: TrialStatus) -> TrialStatus:
    """허용된 전이면 `target` 을 돌려준다.

    Raises:
        ValueError: 역행, 같은 상태로의 전이, 종결 상태에서의 전이.
    """
    allowed = _NEXT_STATUSES[current]
    if target not in allowed:
        raise ValueError(
            "trial 상태는 앞으로만 가고 종결 뒤에는 바뀌지 않는다 — "
            f"current={current} target={target} allowed={sorted(allowed)}"
        )
    return target


def trial_status_of_run(status: RunStatus) -> TrialStatus:
    """실행에 배정된 attempt 의 상태 — 그 실행의 `RunStatus` 에서 파생한다."""
    return _RUN_TRIAL_STATUSES[status]


def experiment_status(trials: Sequence[TrialStatus], *, cancelled: bool) -> ExperimentStatus:
    """취소한 실험은 취소, trial 이 모두 끝났으면 완료, 하나도 시작하지 않았으면 대기다."""
    if cancelled:
        return ExperimentStatus.CANCELLED
    if all(status.is_terminal for status in trials):
        return ExperimentStatus.COMPLETED
    if all(status is TrialStatus.QUEUED for status in trials):
        return ExperimentStatus.QUEUED
    return ExperimentStatus.RUNNING
