"""메모리 가드(run-worker spec D8): run 마다 예약치를 어림하고 새 run 을 띄울지 판정한다.

예약치 계수의 정본이 이 모듈이다. worker 가 보고한 최대 RSS(로그)로 계수를 다시 맞추고, tape 열
지향화(columnar-tape T4-03)로 run 메모리가 줄면 그 측정 뒤 여기 한 곳만 고친다(WORKFLOW W2-02).
메모리를 재는 것은 전송(`RunExecutionPort.memory`)이고, 여기서는 판정만 한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .ports.outgoing.run_execution import RunMemory

# spec 의 GB 는 2³⁰ 바이트로 센다. 10⁹ 보다 크게 잡는 보수 쪽이다.
_BYTES_PER_GB = 1024**3
# 예약치(GB) = RESERVE_MARGIN × (RESERVE_BASE_GB + RESERVE_PER_SESSION_GB × 세션 수)(spec D8).
# 실측 두 점에서 나왔다: 1년 구간 약 520세션의 최대 RSS 2.0GB, 5년 구간 약 1,690세션의 5.5GB
# (BENCHMARK 1·2절). 1.25 는 여유다.
RESERVE_MARGIN = 1.25
RESERVE_BASE_GB = 0.44
RESERVE_PER_SESSION_GB = 0.0030


def estimated_sessions(start: date, end: date, history_sessions: int) -> int:
    """run 이 읽을 세션 수의 어림: 측정 구간의 평일 수 + 팩터 워밍업 세션 수.

    데이터를 읽지 않고 접수 때 정한다(spec D8). 평일 수는 거래일 달력 대신이라 공휴일만큼 크게
    센다(보수 쪽). `history_sessions` 는 원시 관측 질의가 시작일 앞에서 읽는 세션 수다.
    """
    if end < start:
        return history_sessions
    days = (end - start).days + 1
    weeks, rest = divmod(days, 7)
    first = start.weekday()
    weekdays = weeks * 5 + sum(1 for offset in range(rest) if (first + offset) % 7 < 5)
    return weekdays + history_sessions


def run_reserve_bytes(sessions: int) -> int:
    """세션 수 `sessions` 를 읽는 run 하나에 잡아 두는 메모리(바이트)."""
    return gb_bytes(RESERVE_MARGIN * (RESERVE_BASE_GB + RESERVE_PER_SESSION_GB * sessions))


def gb_bytes(gb: float) -> int:
    """GB(2³⁰ 바이트) 값을 바이트로. 고정 예약치 설정도 이 환산을 쓴다."""
    return round(gb * _BYTES_PER_GB)


@dataclass(frozen=True)
class MemoryVerdict:
    """새 run 하나를 띄울지의 판정과 그 근거(바이트). 거짓이면 run 은 `queued` 로 기다린다."""

    available_bytes: int
    overhead_bytes: int
    # 도는 run 마다 max(0, 예약치 − 그 worker 의 지금 RSS) 의 합.
    running_remaining_bytes: int
    reserve_bytes: int

    @property
    def admitted(self) -> bool:
        free = self.available_bytes - self.overhead_bytes - self.running_remaining_bytes
        return free >= self.reserve_bytes


def memory_verdict(
    memory: RunMemory, running: Iterable[tuple[str, int]], reserve_bytes: int
) -> MemoryVerdict:
    """`가용 − 고정 오버헤드 − Σ 도는 run 의 남은 예약분 ≥ 새 run 의 예약치` 를 판정한다(spec D8).

    `running` 은 도는 run 마다 (run_id, 예약치)다. 남은 몫은 예약치에서 그 run 의 worker 가 지금
    쓰는 RSS 를 뺀 것이라, 막 시작해 아직 작은 run 이 곧 쓸 메모리도 세고, 가져간 예비 worker 의
    기준 사용량(이미 가용에서 빠졌다)은 두 번 세지 않는다. RSS 를 모르는 run 은 예약치를 다 센다.
    도는 run 이 없을 때 가드를 쓰지 않는 규칙은 호출자(배정)가 지킨다.
    """
    remaining = sum(
        max(0, reserve - memory.rss_bytes.get(run_id, 0)) for run_id, reserve in running
    )
    return MemoryVerdict(
        available_bytes=memory.available_bytes,
        overhead_bytes=memory.overhead_bytes,
        running_remaining_bytes=remaining,
        reserve_bytes=reserve_bytes,
    )
