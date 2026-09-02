"""엔진 이벤트 큐.

모든 것 — 시세 도착, 체결, 세션 마감 처리, 주문 접수 — 이 typed 이벤트로
이 큐를 통과한다. 정렬 키는 (ts, priority, seq)이며 seq는 단조 증가
일련번호로 동순위 FIFO를 보장한다. 이 결정론이 재현성의 전제다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any

from backtest_engine.types.events import (
    CorporateActionEvent,
    FillEvent,
    OrderEvent,
    OrderUpdateEvent,
)
from backtest_engine.types.market import MarketSnapshot


class EventPriority(IntEnum):
    """같은 ts 안에서의 처리 순서. 숫자가 작을수록 먼저."""

    MARKET = 10  # 새 세션 시세 도착 → 대기 주문 체결 시도
    FILL = 20  # 체결 결과를 포트폴리오에 반영
    NOTIFY = 25  # 전략이 선언한 Fill/OrderUpdate 알림 (모든 체결 반영 후)
    SESSION_CLOSE = 30  # 마감 평가, 스냅샷, 전략 호출
    ORDER = 40  # 전략 판단에서 나온 주문을 대기열에 등록


@dataclass(frozen=True)
class MarketArrived:
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class FillOccurred:
    fill: FillEvent
    snapshot: MarketSnapshot  # 체결이 일어난 세션 (알림 시 참조 가격용)


@dataclass(frozen=True)
class StrategyNotify:
    """전략이 requirements().events에 선언한 이벤트를 전달한다."""

    event: FillEvent | OrderUpdateEvent | CorporateActionEvent
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class SessionClose:
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class OrderPlaced:
    order: OrderEvent


EngineQueueEvent = MarketArrived | FillOccurred | StrategyNotify | SessionClose | OrderPlaced


@dataclass(order=True)
class _Entry:
    ts: datetime
    priority: int
    seq: int
    payload: EngineQueueEvent = field(compare=False)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[_Entry] = []
        self._seq = 0

    def push(self, ts: datetime, priority: EventPriority, payload: EngineQueueEvent) -> None:
        self._seq += 1
        heapq.heappush(
            self._heap, _Entry(ts=ts, priority=int(priority), seq=self._seq, payload=payload)
        )

    def pop(self) -> EngineQueueEvent:
        if not self._heap:
            raise IndexError("pop from empty event queue")
        return heapq.heappop(self._heap).payload

    def __bool__(self) -> bool:
        return bool(self._heap)

    def __len__(self) -> int:
        return len(self._heap)


class PersistentEventQueue:
    """정렬 heap/sequence는 Rust가, 전환 중인 typed payload는 Python이 소유한다."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._payloads: dict[int, EngineQueueEvent] = {}
        self._token = 0

    @staticmethod
    def _timestamp_micros(ts: datetime) -> int:
        offset = ts.utcoffset()
        normalized = ts if offset is None else ts - offset
        return (
            (
                (
                    (normalized.toordinal() * 24 + normalized.hour) * 60
                    + normalized.minute
                )
                * 60
                + normalized.second
            )
            * 1_000_000
            + normalized.microsecond
        )

    def push(self, ts: datetime, priority: EventPriority, payload: EngineQueueEvent) -> None:
        self._token += 1
        token = self._token
        self._runtime.queue_push(self._timestamp_micros(ts), int(priority), token)
        self._payloads[token] = payload

    def pop(self) -> EngineQueueEvent:
        token = self._runtime.queue_pop()
        return self._payloads.pop(token)

    def __bool__(self) -> bool:
        return bool(self._payloads)

    def __len__(self) -> int:
        return len(self._payloads)
