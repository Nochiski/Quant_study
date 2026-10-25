"""복구·대사를 통과한 단일 실행자만 주문을 한 번 전송한다."""

from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.application.offline_execution.ports.outgoing.execution import (
    BrokerError,
    BrokerPort,
    LedgerError,
    LedgerPort,
)
from strategy_workbench.domain.order_execution.facade.orders import (
    Intent,
    OrderFact,
    OrderState,
    Reconciliation,
    Snapshot,
    reconcile,
    target_deltas,
)


class ExecutionStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    detail: str
    order: OrderFact | None = None
    reconciliation: Reconciliation | None = None


@dataclass(frozen=True)
class _Recovery:
    report: Reconciliation
    snapshot: Snapshot


class OfflineExecutionService:
    def __init__(self, ledger: LedgerPort, broker: BrokerPort) -> None:
        self._ledger = ledger
        self._broker = broker

    def initialize(self) -> ExecutionResult:
        """기준 잔고는 주문이 없는 최초 시점에만 명시적으로 저장한다."""
        try:
            with self._ledger.executor():
                snapshot = self._broker.snapshot()
                if snapshot.orders or snapshot.account.reserved_cash_krw:
                    return ExecutionResult(
                        ExecutionStatus.BLOCKED, "initial snapshot has orders/reserve"
                    )
                self._ledger.initialize(snapshot.account)
                return ExecutionResult(ExecutionStatus.OK, "baseline initialized")
        except (LedgerError, BrokerError) as error:
            return ExecutionResult(ExecutionStatus.ERROR, f"initialize failed: {error}")

    def _recover(self) -> _Recovery:
        snapshot = self._broker.snapshot()
        local_ids = {fact.intent.intent_id for fact in self._ledger.orders()}
        for fact in snapshot.orders:
            if fact.intent.intent_id in local_ids:
                self._ledger.apply(fact)
        return _Recovery(
            reconcile(self._ledger.baseline(), self._ledger.orders(), snapshot), snapshot
        )

    def recover(self) -> ExecutionResult:
        try:
            with self._ledger.executor():
                recovery = self._recover()
                report = recovery.report
                if not report.matched:
                    self._ledger.set_halted(True)
                return ExecutionResult(
                    ExecutionStatus.OK if report.matched else ExecutionStatus.BLOCKED,
                    "reconciliation complete",
                    reconciliation=report,
                )
        except (LedgerError, BrokerError) as error:
            return ExecutionResult(ExecutionStatus.ERROR, f"recover failed: {error}")

    def submit(self, intent: Intent) -> ExecutionResult:
        transmission_started = False
        try:
            with self._ledger.executor():
                existing = next(
                    (f for f in self._ledger.orders() if f.intent.intent_id == intent.intent_id),
                    None,
                )
                if existing is not None and existing.intent != intent:
                    return ExecutionResult(
                        ExecutionStatus.BLOCKED,
                        f"intent identity conflict intent_id={intent.intent_id}",
                    )
                if existing is not None and existing.state != OrderState.READY:
                    return ExecutionResult(
                        ExecutionStatus.UNKNOWN
                        if existing.state == OrderState.UNKNOWN
                        else ExecutionStatus.OK,
                        "intent already claimed",
                        existing,
                    )
                recovery = self._recover()
                report = recovery.report
                if not report.matched:
                    self._ledger.set_halted(True)
                if self._ledger.halted() or not report.matched:
                    return ExecutionResult(
                        ExecutionStatus.BLOCKED, "execution halted", reconciliation=report
                    )
                snapshot = recovery.snapshot
                fact = OrderFact(intent, OrderState.READY)
                if intent.side == "buy":
                    if (
                        fact.reserved_cash_krw
                        > snapshot.account.cash_krw - snapshot.account.reserved_cash_krw
                    ):
                        return ExecutionResult(
                            ExecutionStatus.BLOCKED,
                            f"insufficient available cash intent_id={intent.intent_id}",
                        )
                else:
                    reserved_shares = sum(
                        f.remaining_quantity
                        for f in snapshot.orders
                        if f.intent.side == "sell" and f.intent.symbol == intent.symbol
                    )
                    if (
                        intent.quantity
                        > dict(snapshot.account.holdings).get(intent.symbol, 0) - reserved_shares
                    ):
                        return ExecutionResult(
                            ExecutionStatus.BLOCKED,
                            f"insufficient available shares intent_id={intent.intent_id}",
                        )
                self._ledger.save_intent(intent)
                if not self._ledger.claim(intent.intent_id):
                    return ExecutionResult(ExecutionStatus.BLOCKED, "intent claim lost")
                # UNKNOWN 커밋 뒤에만 전송한다. 종료되면 조회로 확인한다. 자동 재발주하지 않는다.
                transmission_started = True
                broker_fact = self._broker.submit(intent)
                self._ledger.apply(broker_fact)
                return ExecutionResult(ExecutionStatus.OK, "order submitted", broker_fact)
        except BrokerError as error:
            return ExecutionResult(
                ExecutionStatus.UNKNOWN,
                f"broker outcome unknown intent_id={intent.intent_id}: {error}",
            )
        except LedgerError as error:
            return ExecutionResult(
                ExecutionStatus.UNKNOWN if transmission_started else ExecutionStatus.ERROR,
                f"ledger failure intent_id={intent.intent_id}: {error}",
            )

    def stop(self) -> None:
        """신규 전송을 막는다. 이미 보낸 주문은 취소하지 않는다."""
        with self._ledger.executor():
            self._ledger.set_halted(True)

    def resume(self) -> ExecutionResult:
        try:
            with self._ledger.executor():
                recovery = self._recover()
                report = recovery.report
                if report.matched:
                    self._ledger.set_halted(False)
                return ExecutionResult(
                    ExecutionStatus.OK if report.matched else ExecutionStatus.BLOCKED,
                    "resume reconciliation",
                    reconciliation=report,
                )
        except (LedgerError, BrokerError) as error:
            return ExecutionResult(ExecutionStatus.ERROR, f"resume failed: {error}")

    def cancel(self, intent_id: str) -> ExecutionResult:
        try:
            with self._ledger.executor():
                local = next(
                    (f for f in self._ledger.orders() if f.intent.intent_id == intent_id), None
                )
                if local is None or local.state in (OrderState.READY, OrderState.UNKNOWN):
                    return ExecutionResult(
                        ExecutionStatus.BLOCKED,
                        f"cancel requires known order intent_id={intent_id}",
                    )
                fact = self._broker.cancel(intent_id)
                self._ledger.apply(fact)
                return ExecutionResult(ExecutionStatus.OK, "cancel confirmed", fact)
        except BrokerError as error:
            return ExecutionResult(
                ExecutionStatus.UNKNOWN, f"cancel outcome unknown intent_id={intent_id}: {error}"
            )
        except LedgerError as error:
            return ExecutionResult(
                ExecutionStatus.ERROR, f"cancel ledger failure intent_id={intent_id}: {error}"
            )

    def plan(self, target_shares: dict[str, int]) -> dict[str, int]:
        """명시적 목표 주식 수를 현재 보유·미체결과 대조한다. 전송하지 않는다."""
        with self._ledger.executor():
            snapshot = self._broker.snapshot()
            report = reconcile(self._ledger.baseline(), self._ledger.orders(), snapshot)
            if not report.matched or self._ledger.halted():
                raise LedgerError(
                    f"cannot plan before reconciliation — differences={report.differences}"
                )
            return target_deltas(target_shares, snapshot.account, snapshot.orders)
