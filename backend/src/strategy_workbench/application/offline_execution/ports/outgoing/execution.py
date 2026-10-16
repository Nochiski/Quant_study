"""실행 유스케이스가 소유하는 오프라인 주문·원장 계약."""

from contextlib import AbstractContextManager
from typing import Protocol

from strategy_workbench.domain.order_execution.facade.orders import (
    Account,
    Intent,
    OrderFact,
    Snapshot,
)


class LedgerError(RuntimeError):
    """영속 저장 또는 단일 실행자 획득 실패."""


class BrokerError(RuntimeError):
    """전송 결과를 확정할 수 없다. 재전송 근거로 사용하면 안 된다."""


class LedgerPort(Protocol):
    def executor(self) -> AbstractContextManager[None]: ...
    def initialize(self, baseline: Account) -> None: ...
    def baseline(self) -> Account: ...
    def orders(self) -> tuple[OrderFact, ...]: ...
    def save_intent(self, intent: Intent) -> OrderFact: ...
    def claim(self, intent_id: str) -> bool: ...
    def apply(self, fact: OrderFact) -> None: ...
    def set_halted(self, halted: bool) -> None: ...
    def halted(self) -> bool: ...


class BrokerPort(Protocol):
    """snapshot은 누적 체결과 잔고가 같은 시점인 완전한 오프라인 조회다.

    submit의 멱등성은 보장하지 않는다. intent_id 조회는 가짜 브로커에서만 보장한다.
    """

    def snapshot(self) -> Snapshot: ...
    def submit(self, intent: Intent) -> OrderFact: ...
    def cancel(self, intent_id: str) -> OrderFact: ...
