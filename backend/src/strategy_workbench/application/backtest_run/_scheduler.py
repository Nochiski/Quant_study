"""대기 run 의 배정 순서 — 단일 실행 먼저, 실험끼리는 번갈아(검증 랩 spec D6).

레인은 단일 실행 하나(`None`)와 실험마다 하나다. 슬롯이 비면 단일 실행 레인이 먼저 나가고, 실험
레인은 라운드로빈으로 하나씩 나간다. 한 레인에서 하나를 꺼내면 그 레인은 줄 끝으로 가므로 뒤에 온
실험도 굶지 않는다. 몇 개를 동시에 돌릴지는 서비스가 정한다(`experiment_slots`).
"""

from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar

_T = TypeVar("_T")


def experiment_slots(run_slots: int) -> int:
    """실험 run 이 동시에 쓸 수 있는 슬롯 수. 슬롯이 2개 이상이면 1개를 단일 실행 전용으로 비운다.

    슬롯이 1개면 비우지 않는다 — 비우면 실험이 영영 돌지 못한다.
    """
    return run_slots - 1 if run_slots >= 2 else run_slots


class RunQueue(Generic[_T]):
    def __init__(self) -> None:
        # 삽입 순서가 라운드로빈 순서다. 빈 레인은 두지 않는다.
        self._lanes: dict[str | None, deque[_T]] = {}

    def __bool__(self) -> bool:
        return bool(self._lanes)

    def __contains__(self, item: _T) -> bool:
        return any(item in lane for lane in self._lanes.values())

    def push(self, item: _T, lane: str | None) -> None:
        """`lane` 은 실험 id, 단일 실행이면 None."""
        self._lanes.setdefault(lane, deque()).append(item)

    def remove(self, item: _T) -> None:
        for lane, queue in self._lanes.items():
            if item in queue:
                queue.remove(item)
                if not queue:
                    del self._lanes[lane]
                return

    def pop(self, *, experiments: bool) -> _T | None:
        """다음 run. `experiments` 가 거짓이면(실험 몫 슬롯이 찼다) 단일 실행만 꺼낸다."""
        if None in self._lanes:
            lane: str | None = None
        elif experiments and self._lanes:
            lane = next(iter(self._lanes))
        else:
            return None
        queue = self._lanes.pop(lane)
        item = queue.popleft()
        if queue:
            self._lanes[lane] = queue
        return item
