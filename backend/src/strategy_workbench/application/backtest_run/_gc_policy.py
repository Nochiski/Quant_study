"""백테스트 run 스레드 구간의 CPython GC 정책(이슈 #196).

tape 단계는 수백만 개의 오래 사는 작은 객체(원시·팩터·포트폴리오 관측)를 만든다. CPython 의
전체 수집(2세대)은 오래 사는 객체가 직전 전체 수집 때보다 25% 늘 때마다 돌고, 돌 때마다 살아
있는 객체 전부를 훑는다. 4년 실데이터에서 전체 수집 29번이 32초(최대 4.9초 정지)를 썼지만 회수한
객체는 한 번에 수백 개뿐이었다. 수집이 GIL 을 쥐는 동안 같은 프로세스의 HTTP 요청도 멈춘다.

그래서 run 이 도는 동안에는 2세대 임계값만 사실상 무한으로 올린다. 0·1세대 수집은 그대로 돌아
짧게 사는 순환 쓰레기(다른 요청이 만든 것 포함)는 계속 치운다. GC 설정은 프로세스 전역이므로 run
여러 개가 겹칠 때는 참조 카운트로 처음 들어온 run 이 값을 저장하고 마지막으로 나가는 run 이
복원한다. 구간 안에서 다른 코드가 임계값을 바꾸면 그 값은 복원 때 덮어쓴다 — 이 프로세스에서
임계값을 바꾸는 곳은 여기 하나다.

실험 대기열이 늘 차 있으면 run 이 끊이지 않고 겹쳐 참조 카운트가 0 으로 돌아오지 않는다. 그러면
전체 수집이 영영 밀려 오래 사는 순환 쓰레기가 쌓인다. 그래서 겹친 채로 run 이 끝날 때, 마지막 전체
수집에서 `FULL_COLLECTION_INTERVAL_SECONDS` 가 지났으면 한 번 직접 돈다(검증 랩 V3-04, spec D6).

효과는 CPython 3.11 세대 GC 의 임계값 의미(3번째 값 = 전체 수집 전 1세대 수집 횟수)에 기댄다.
테스트는 임계값 값만 단언하므로, 인터프리터를 올리면(특히 3.14 증분 GC) 2세대 수집 횟수를 다시
잰다(이슈 #196 PR 실측 방법).
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock

__all__ = [
    "FULL_COLLECTION_INTERVAL_SECONDS",
    "SUSPENDED_FULL_COLLECTION_THRESHOLD",
    "full_collections_suspended",
]

# 2세대 수집은 1세대 수집 횟수가 이 값을 넘어야 돈다. 1세대 수집은 약 7천 번 할당마다 한 번이라
# 10억이면 run 한 번 안에서는 도달하지 않는다(`gc.set_threshold` 는 C int 를 받는다).
SUSPENDED_FULL_COLLECTION_THRESHOLD = 1_000_000_000
# run 이 겹쳐 도는 동안 전체 수집 사이의 최소 간격. 4년 실데이터 run 한 건의 전체 수집 한 번이 최대
# 5초쯤이라, 5분에 한 번이면 멈춤은 2% 아래로 남는다.
FULL_COLLECTION_INTERVAL_SECONDS = 300.0


class _FullCollectionSuspension:
    """겹치는 run 들이 공유하는 전역 GC 임계값의 저장·복원을 한 곳에서 센다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        collect: Callable[[], object] = gc.collect,
    ) -> None:
        self._lock = Lock()
        self._depth = 0
        self._saved: tuple[int, int, int] | None = None
        self._clock = clock
        self._collect = collect
        self._last_full_collection = clock()

    def enter(self) -> None:
        with self._lock:
            if self._depth == 0:
                young, middle, old = gc.get_threshold()
                self._saved = (young, middle, old)
                gc.set_threshold(young, middle, SUSPENDED_FULL_COLLECTION_THRESHOLD)
            self._depth += 1

    def exit(self) -> None:
        with self._lock:
            if self._depth == 0 or self._saved is None:
                raise RuntimeError(
                    "full collection suspension exited more times than entered — "
                    f"depth={self._depth} saved={self._saved}"
                )
            self._depth -= 1
            if self._depth == 0:
                gc.set_threshold(*self._saved)
                self._saved = None
                # 복원한 임계값이 곧 전체 수집을 돌린다.
                self._last_full_collection = self._clock()
            elif self._clock() - self._last_full_collection >= FULL_COLLECTION_INTERVAL_SECONDS:
                self._collect()
                self._last_full_collection = self._clock()


_SUSPENSION = _FullCollectionSuspension()


@contextmanager
def full_collections_suspended() -> Iterator[None]:
    """이 구간 동안 전체 수집(2세대)을 미루고, 나갈 때(예외 포함) 들어가기 전 임계값으로 돌린다.

    복원 뒤 첫 전체 수집은 미뤄 둔 만큼 곧 돈다. 그때 훑는 양은 run 이 남긴 결과뿐이다 — tape
    중간 데이터는 run 스레드가 끝나며 참조 카운트로 이미 풀린다.
    """

    _SUSPENSION.enter()
    try:
        yield
    finally:
        _SUSPENSION.exit()
