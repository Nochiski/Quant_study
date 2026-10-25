"""S07 실행기의 `OrderGateway`를 S05 실행 서비스와 키움 모의 어댑터로 조립한다.

전송·대사·중복 방지는 S05가 그대로 맡는다. 여기서는 결과를 실행기의 어휘로 옮기기만 한다.
보내기 전 조회가 안정되지 않아 S05가 '결과 불명'을 돌려줘도 원장에 의도가 없으면 보내지 않은 것이
확실하므로, 같은 의도로 다시 시도해도 되는 `retry`로 옮긴다.
"""

from __future__ import annotations

from pathlib import Path

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomMockBroker
from strategy_workbench.application.offline_execution.facade.execution import (
    BrokerError,
    ExecutionStatus,
    LedgerError,
    OfflineExecutionService,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    GatewayError,
    GatewayState,
    OrderRequest,
    OrderView,
    SubmitOutcome,
    SubmitStatus,
)
from strategy_workbench.domain.order_execution.facade.orders import Intent, OrderFact, OrderState

_ALREADY_CLAIMED = "intent already claimed"


class OfflineOrderGateway:
    def __init__(self, ledger_dir: Path, broker: KiwoomMockBroker) -> None:
        """`broker`는 조립하는 쪽이 만들고 보내는지(`transmit`)도 거기서 정한다. 닫기는 이쪽이
        한다."""
        ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ledger_path = ledger_dir / "execution.sqlite"
        self._broker = broker
        self._ledger: ExecutionSqliteLedger | None = None
        self._service: OfflineExecutionService | None = None
        if self._ledger_path.exists():
            self._open_ledger()

    def close(self) -> None:
        self._broker.close()
        if self._ledger is not None:
            self._ledger.close()

    def initialized(self) -> bool:
        if self._ledger is None:
            return False
        try:
            self._ledger.baseline()
        except LedgerError:
            return False
        return True

    def initialize(self) -> None:
        if self._service is None:
            self._open_ledger()
        result = self._require_service().initialize()
        if result.status is not ExecutionStatus.OK:
            raise GatewayError(f"cannot set the baseline — {result.status.value} {result.detail}")

    def state(self) -> GatewayState:
        try:
            if not self.initialized():
                account = self._broker.snapshot().account
                return GatewayState(
                    True,
                    "실행 원장 없음(미리보기)",
                    account.cash_krw,
                    account.reserved_cash_krw,
                    dict(account.holdings),
                    (),
                )
            service, ledger = self._require_service(), self._require_ledger()
            result = service.recover()
            account = self._broker.snapshot().account
            orders = tuple(_view(fact) for fact in ledger.orders())
        except (BrokerError, LedgerError) as error:
            raise GatewayError(f"cannot read the account — {error}") from error
        detail = f"{result.status.value} {result.detail} {result.reconciliation or ''}".strip()
        return GatewayState(
            result.status is ExecutionStatus.OK,
            detail,
            account.cash_krw,
            account.reserved_cash_krw,
            dict(account.holdings),
            orders,
        )

    def submit(self, request: OrderRequest) -> SubmitOutcome:
        service, ledger = self._require_service(), self._require_ledger()
        intent = Intent(
            request.intent_id,
            request.symbol,
            request.side,
            request.quantity,
            request.limit_price_krw,
            request.fee_budget_krw,
        )
        result = service.submit(intent)
        if result.status is ExecutionStatus.OK:
            if result.order is not None and result.order.state is OrderState.REJECTED:
                return SubmitOutcome(SubmitStatus.REJECTED, result.detail)
            if result.detail == _ALREADY_CLAIMED:
                return SubmitOutcome(SubmitStatus.EXISTS, result.detail)
            return SubmitOutcome(SubmitStatus.SENT, result.detail)
        if result.status is ExecutionStatus.UNKNOWN:
            claimed = any(f.intent.intent_id == request.intent_id for f in ledger.orders())
            status = SubmitStatus.UNKNOWN if claimed else SubmitStatus.RETRY
            return SubmitOutcome(status, result.detail)
        if result.status is ExecutionStatus.BLOCKED:
            return SubmitOutcome(SubmitStatus.BLOCKED, result.detail)
        return SubmitOutcome(SubmitStatus.ERROR, result.detail)

    def cancel(self, intent_id: str) -> SubmitOutcome:
        result = self._require_service().cancel(intent_id)
        mapping = {
            ExecutionStatus.OK: SubmitStatus.SENT,
            ExecutionStatus.UNKNOWN: SubmitStatus.UNKNOWN,
            ExecutionStatus.BLOCKED: SubmitStatus.BLOCKED,
        }
        return SubmitOutcome(mapping.get(result.status, SubmitStatus.ERROR), result.detail)

    def evidence(self) -> dict[str, object]:
        try:
            return self._broker.raw_reads()
        except BrokerError as error:
            raise GatewayError(f"cannot read broker evidence — {error}") from error

    def _open_ledger(self) -> None:
        self._ledger = ExecutionSqliteLedger(self._ledger_path)
        self._service = OfflineExecutionService(self._ledger, self._broker)

    def _require_service(self) -> OfflineExecutionService:
        if self._service is None:
            raise GatewayError("execution ledger missing — initialize first")
        return self._service

    def _require_ledger(self) -> ExecutionSqliteLedger:
        if self._ledger is None:
            raise GatewayError("execution ledger missing — initialize first")
        return self._ledger


def _view(fact: OrderFact) -> OrderView:
    intent = fact.intent
    return OrderView(
        intent.intent_id,
        intent.symbol,
        intent.side,
        intent.quantity,
        intent.limit_price_krw,
        fact.state.value,
        fact.state.terminal,
        fact.filled_quantity,
        fact.filled_value_krw,
        fact.fees_krw,
    )
