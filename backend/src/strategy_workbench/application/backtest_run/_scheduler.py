"""대기 run 의 배정 순서 — 단일 실행 먼저, 실험끼리는 우선순위 가중 라운드로빈(검증 랩 spec D6).

레인은 단일 실행 하나(`None`)와 실험마다 하나다. 슬롯이 비면 단일 실행 레인이 먼저 나가고, 실험
레인은 차례대로 가중치(우선순위)만큼 꺼낸 뒤 줄 끝으로 간다. 그래서 뒤에 온 실험도 굶지 않는다.
일시정지한 실험 레인은 자리를 지킨 채 건너뛴다. 몇 개를 동시에 돌릴지는 서비스가 정한다
(`experiment_slots`).
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
        # 레인 설정은 대기 run 이 없는 동안에도 남는다. 기본값(진행·가중치 1)은 적지 않는다.
        self._paused: set[str] = set()
        self._weights: dict[str, int] = {}
        # 이번 차례를 받는 레인과 꺼낸 수. 한 레인만 차례 중이다.
        self._served: dict[str | None, int] = {}

    def __bool__(self) -> bool:
        return bool(self._lanes)

    def __contains__(self, item: _T) -> bool:
        return any(item in lane for lane in self._lanes.values())

    def configure(self, lane: str, *, paused: bool, weight: int) -> None:
        """실험 레인의 일시정지와 한 차례에 꺼내는 수(우선순위)."""
        if paused:
            self._paused.add(lane)
        else:
            self._paused.discard(lane)
        if weight == 1:
            self._weights.pop(lane, None)
        else:
            self._weights[lane] = weight

    def is_paused(self, lane: str) -> bool:
        return lane in self._paused

    def push(self, item: _T, lane: str | None) -> None:
        """`lane` 은 실험 id, 단일 실행이면 None."""
        self._lanes.setdefault(lane, deque()).append(item)

    def remove(self, item: _T) -> None:
        for lane, queue in self._lanes.items():
            if item in queue:
                queue.remove(item)
                if not queue:
                    del self._lanes[lane]
                    self._served.pop(lane, None)
                return

    def pop(self, *, experiments: bool) -> _T | None:
        """다음 run. `experiments` 가 거짓이면(실험 몫 슬롯이 찼다) 단일 실행만 꺼낸다."""
        lane: str | None
        if None in self._lanes:
            lane = None
        elif experiments:
            lane = next((lane for lane in self._lanes if lane not in self._paused), None)
            if lane is None:
                return None
        else:
            return None
        queue = self._lanes[lane]
        item = queue.popleft()
        # 차례 중이던 실험 레인이 멈춰 다른 실험 레인이 꺼내면 그 차례는 끝난다(단일 실행은 끼어들
        # 뿐 차례를 끊지 않는다).
        served = self._served.get(lane, 0) + 1
        if lane is not None:
            self._served.clear()
        if not queue:
            del self._lanes[lane]
        elif lane is not None and served < self._weights.get(lane, 1):
            self._served[lane] = served
        else:
            del self._lanes[lane]
            self._lanes[lane] = queue
        return item
