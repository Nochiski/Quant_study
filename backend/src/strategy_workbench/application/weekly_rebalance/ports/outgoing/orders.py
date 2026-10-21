"""Outgoing port: 실행 원장을 거친 주문 전송·취소와 대사된 계좌 상태(S07 실행기).

구현은 S05 실행 서비스를 감싼다. 같은 의도 ID를 다시 보내도 두 번 전송하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "GatewayError",
    "GatewayState",
    "OrderGateway",
    "OrderRequest",
    "OrderView",
    "SubmitOutcome",
    "SubmitStatus",
]


class GatewayError(Exception):
    """원장이나 브로커를 읽지 못했다. 주문은 나가지 않았다."""


class SubmitStatus(StrEnum):
    SENT = "sent"  # 접수됐다(체결은 조회로 확인한다)
    EXISTS = "exists"  # 같은 의도가 이미 원장에 있어 다시 보내지 않았다
    RETRY = "retry"  # 보내기 전 조회가 안정되지 않아 보내지 않았다. 같은 의도로 다시 시도해도 된다
    REJECTED = "rejected"  # 키움이 처리하지 않았거나 보내지 않기로 확정했다
    BLOCKED = "blocked"  # 대사 불일치·정지·현금 부족으로 보내지 않았다
    UNKNOWN = "unknown"  # 보냈을 수 있으나 결과를 모른다. 다시 보내지 않는다
    ERROR = "error"


@dataclass(frozen=True)
class OrderRequest:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    limit_price_krw: int
    fee_budget_krw: int


@dataclass(frozen=True)
class SubmitOutcome:
    status: SubmitStatus
    detail: str = ""


@dataclass(frozen=True)
class OrderView:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    limit_price_krw: int
    state: str
    terminal: bool
    filled_quantity: int
    filled_value_krw: int
    fees_krw: int

    @property
    def open_quantity(self) -> int:
        return 0 if self.terminal else self.quantity - self.filled_quantity


@dataclass(frozen=True)
class GatewayState:
    """`matched`가 거짓이면 실행 원장이 정지했고 새 주문은 나가지 않는다."""

    matched: bool
    detail: str
    cash_krw: int
    reserved_cash_krw: int
    holdings: Mapping[str, int]
    orders: tuple[OrderView, ...]


class OrderGateway(Protocol):
    def initialized(self) -> bool: ...

    def initialize(self) -> None:
        """주문이 없는 지금 계좌를 기준 잔고로 잡는다. 할 수 없으면 `GatewayError`."""
        ...

    def state(self) -> GatewayState:
        """대사한 뒤의 계좌·주문. 읽지 못하면 `GatewayError`."""
        ...

    def submit(self, request: OrderRequest) -> SubmitOutcome: ...

    def cancel(self, intent_id: str) -> SubmitOutcome: ...

    def evidence(self) -> Mapping[str, object]:
        """감사·복기용 브로커 조회 원본. 해석하지 않은 그대로다. 읽지 못하면 `GatewayError`."""
        ...
