"""실행 원장 고치기(리뷰 #593)의 구현. 키움 참조 원장과 S05 실행 원장을 함께 다룬다.

주문을 보내지 않는 브로커(`transmit=False`)로 연다. 다시 대사는 S05 `resume`이 한다.
"""

from __future__ import annotations

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomMockBroker
from strategy_workbench.application.offline_execution.facade.execution import (
    BrokerError,
    ExecutionStatus,
    OfflineExecutionService,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    LedgerRepairState,
    RepairError,
    ResumeOutcome,
    UnresolvedOrder,
)


class KiwoomLedgerRepair:
    def __init__(self, broker: KiwoomMockBroker, ledger: ExecutionSqliteLedger | None) -> None:
        self._broker = broker
        self._ledger = ledger

    def state(self) -> LedgerRepairState:
        try:
            unresolved = self._broker.unresolved()
        except BrokerError as error:
            raise RepairError(f"cannot read kiwoom orders — {error}") from error
        return LedgerRepairState(
            self._broker.latch(),
            None if self._ledger is None else self._ledger.halted(),
            tuple(
                UnresolvedOrder(
                    item.intent_id,
                    item.record.intent.symbol,
                    item.record.intent.side,
                    item.record.intent.quantity,
                    item.record.intent.limit_price_krw,
                    item.record.order_date,
                    item.record.sent_at,
                    item.record.status.value,
                    item.record.note,
                    item.candidates,
                )
                for item in unresolved
            ),
        )

    def bind(self, intent_id: str, order_no: str) -> None:
        try:
            self._broker.bind_unresolved(intent_id, order_no)
        except BrokerError as error:
            raise RepairError(str(error)) from error

    def reject(self, intent_id: str) -> None:
        try:
            self._broker.reject_unresolved(intent_id)
        except BrokerError as error:
            raise RepairError(str(error)) from error

    def clear_latch(self) -> None:
        self._broker.clear_latch()

    def resume(self) -> ResumeOutcome:
        if self._ledger is None:
            raise RepairError("execution ledger not created yet — nothing to resume")
        result = OfflineExecutionService(self._ledger, self._broker).resume()
        return ResumeOutcome(
            result.status is ExecutionStatus.OK, f"{result.detail} {result.reconciliation}".strip()
        )
