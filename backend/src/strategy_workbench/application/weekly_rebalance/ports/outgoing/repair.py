"""Outgoing port: 실행 원장·브로커 참조 원장을 사람이 고치는 일(2026-10-22 리뷰 #593).

응답을 잃어 주문번호에 묶이지 않은 주문(S05 UNKNOWN)과 정지 래치는 사람이 확인해 풀어야 한다.
구현은 키움 참조 원장과 S05 실행 원장을 함께 다룬다. 원장 파일을 손으로 고치지 않게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "LedgerRepairState",
    "OrderLedgerRepair",
    "RepairError",
    "ResumeOutcome",
    "UnresolvedOrder",
]


class RepairError(Exception):
    """고치지 않았다(조건이 맞지 않거나 브로커·원장을 읽지 못했다)."""


@dataclass(frozen=True)
class UnresolvedOrder:
    """응답을 잃어 묶이지 않은 의도. `candidates`는 그 주문일 키움 주문 중 내용이 같은 번호다."""

    intent_id: str
    symbol: str
    side: str
    quantity: int
    limit_price_krw: int
    order_date: str  # KST YYYYMMDD
    sent_at: str  # KST HH:MM:SS
    status: str
    note: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class LedgerRepairState:
    latch: str | None
    halted: bool | None  # 실행 원장이 아직 없으면 None
    unresolved: tuple[UnresolvedOrder, ...]


@dataclass(frozen=True)
class ResumeOutcome:
    """다시 대사한 결과. 일치하면 실행 원장의 정지도 풀린다."""

    matched: bool
    detail: str


class OrderLedgerRepair(Protocol):
    def state(self) -> LedgerRepairState: ...

    def bind(self, intent_id: str, order_no: str) -> None:
        """사람이 고른 키움 주문번호에 묶는다. 후보가 아니면 `RepairError`."""
        ...

    def reject(self, intent_id: str) -> None:
        """키움에 같은 내용의 주문이 없을 때만 보내지 않은 것(거부)으로 확정한다."""
        ...

    def clear_latch(self) -> None: ...

    def resume(self) -> ResumeOutcome:
        """다시 대사하고, 일치하면 실행 원장의 신규 주문 정지를 푼다."""
        ...
