"""네트워크·계좌 설정 없는 결정적 브로커. submit은 의도적으로 멱등하지 않다."""

from dataclasses import replace

from strategy_workbench.application.offline_execution.facade.execution import BrokerError
from strategy_workbench.domain.order_execution.facade.orders import (
    Account,
    Intent,
    OrderFact,
    OrderState,
    Snapshot,
)


class FakeBroker:
    def __init__(self, baseline: Account) -> None:
        if baseline.reserved_cash_krw:
            raise ValueError(
                f"fake baseline must have no reserve — reserve={baseline.reserved_cash_krw}"
            )
        self._baseline = baseline
        self._orders: dict[str, OrderFact] = {}
        self.submit_calls = 0
        self.lose_next_ack = False
        self.reject_next = False
        self.lose_next_cancel_ack = False

    def snapshot(self) -> Snapshot:
        holdings = dict(self._baseline.holdings)
        cash = self._baseline.cash_krw
        reserve = 0
        for fact in self._orders.values():
            holdings[fact.intent.symbol] = holdings.get(fact.intent.symbol, 0) + (
                fact.filled_quantity * (1 if fact.intent.side == "buy" else -1)
            )
            cash += fact.cash_change_krw
            reserve += fact.reserved_cash_krw
        return Snapshot(
            Account(cash, tuple(sorted(holdings.items())), reserve), tuple(self._orders.values())
        )

    def submit(self, intent: Intent) -> OrderFact:
        self.submit_calls += 1
        # 같은 의도의 재전송도 별도 주문으로 접수해 호출자의 중복 방지를 검증한다.
        storage_key = intent.intent_id
        if storage_key in self._orders:
            storage_key = f"{intent.intent_id}#duplicate-{self.submit_calls}"
        snapshot = self.snapshot()
        fact = OrderFact(intent, OrderState.OPEN, revision=1)
        insufficient = (
            fact.reserved_cash_krw > snapshot.account.cash_krw - snapshot.account.reserved_cash_krw
            if intent.side == "buy"
            else intent.quantity
            > dict(snapshot.account.holdings).get(intent.symbol, 0)
            - sum(
                f.remaining_quantity
                for f in snapshot.orders
                if f.intent.side == "sell" and f.intent.symbol == intent.symbol
            )
        )
        if self.reject_next or insufficient:
            fact = replace(fact, state=OrderState.REJECTED)
            self.reject_next = False
        self._orders[storage_key] = fact
        if self.lose_next_ack:
            self.lose_next_ack = False
            raise BrokerError(f"ACK lost after acceptance — intent_id={intent.intent_id}")
        return fact

    def fill(self, intent_id: str, *, quantity: int, price_krw: int, fee_krw: int = 0) -> OrderFact:
        """증분 fixture 입력을 누적 revision 사실로 만든다."""
        previous = self._orders[intent_id]
        if previous.state.terminal or type(quantity) is not int or quantity <= 0:
            raise ValueError(
                f"invalid fake fill — intent_id={intent_id} state={previous.state} "
                f"quantity={quantity}"
            )
        # 가격·비용 검증은 동일 도메인 값 타입으로 먼저 수행한다.
        Intent(
            "fixture-validation",
            previous.intent.symbol,
            previous.intent.side,
            quantity,
            price_krw,
            fee_krw,
        )
        filled = previous.filled_quantity + quantity
        fact = replace(
            previous,
            state=OrderState.FILLED if filled == previous.intent.quantity else OrderState.PARTIAL,
            revision=previous.revision + 1,
            filled_quantity=filled,
            filled_value_krw=previous.filled_value_krw + quantity * price_krw,
            fees_krw=previous.fees_krw + fee_krw,
        )
        self._orders[intent_id] = fact
        return fact

    def cancel(self, intent_id: str) -> OrderFact:
        previous = self._orders[intent_id]
        if previous.state.terminal:
            return previous
        fact = replace(previous, state=OrderState.CANCELLED, revision=previous.revision + 1)
        self._orders[intent_id] = fact
        if self.lose_next_cancel_ack:
            self.lose_next_cancel_ack = False
            raise BrokerError(f"cancel ACK lost — intent_id={intent_id}")
        return fact

    def manual_account_change(
        self, *, cash_change_krw: int = 0, symbol: str = "", quantity_change: int = 0
    ) -> None:
        """추적되지 않은 수동 잔고 변동을 장애 fixture로 주입한다."""
        holdings = dict(self._baseline.holdings)
        if symbol:
            holdings[symbol] = holdings.get(symbol, 0) + quantity_change
        self._baseline = Account(
            self._baseline.cash_krw + cash_change_krw, tuple(sorted(holdings.items()))
        )
