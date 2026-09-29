"""trial 상태 전이 — 앞으로만 가고 종결 상태는 바뀌지 않는다(spec D5, 로드맵 10절).

전진 전용 전이의 단위는 attempt(trial 의 실행 시도 하나)이고, trial 이 보이는 상태는 최신 attempt 의
상태에서 파생한다. 그래서 재시도와 재시작 복구는 상태를 되돌리지 않고 새 attempt 를 `QUEUED` 로
만든다(V3-03·V3-04).
"""

from __future__ import annotations

from enum import StrEnum


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
