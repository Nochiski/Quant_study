"""Outgoing port: 실행 기록. 사건은 덧붙이기만 하고, 하루 기록은 그날 마지막 대사로 갈아 끼운다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

__all__ = ["DayOrder", "DayRecord", "ExecutionEvent", "ExecutionLog"]


@dataclass(frozen=True)
class ExecutionEvent:
    at: datetime
    plan_id: str
    phase: str
    kind: str
    symbol: str = ""
    intent_id: str = ""
    side: str = ""
    quantity: int = 0
    limit_price_krw: int = 0
    reference_price_krw: int = 0
    asks: tuple[int, ...] = ()
    bids: tuple[int, ...] = ()
    status: str = ""
    detail: str = ""
    preview: bool = False  # 보내지 않는 미리보기 실행에서 남긴 사건(2026-10-22 리뷰 #604)


@dataclass(frozen=True)
class DayOrder:
    """그날 끝난(또는 진행 중인) 주문 하나의 체결 품질. 체결이 없으면 차이 bp는 None이다."""

    intent_id: str
    symbol: str
    name: str
    side: str
    quantity: int
    filled_quantity: int
    filled_value_krw: int
    fees_krw: int
    state: str
    reference_price_krw: int
    close_price_krw: int
    shortfall_bp: int | None
    missed_krw: int


@dataclass(frozen=True)
class DayRecord:
    day: date
    recorded_at: datetime
    plan_id: str
    matched: bool
    detail: str
    cash_krw: int
    holdings: tuple[tuple[str, int, int], ...]  # (종목, 주식 수, 장 마감가)
    equity_krw: int
    traded_krw: int
    orders: tuple[DayOrder, ...]


class ExecutionLog(Protocol):
    def append(self, event: ExecutionEvent) -> None: ...

    def events(self) -> tuple[ExecutionEvent, ...]: ...

    def save_raw(self, day: date, payload: Mapping[str, object]) -> None:
        """그날 브로커 조회 원본. 다시 대사하면 갈아 끼운다."""
        ...

    def save_day(self, record: DayRecord) -> None: ...

    def day(self, day: date) -> DayRecord | None: ...

    def days(self) -> tuple[DayRecord, ...]: ...
