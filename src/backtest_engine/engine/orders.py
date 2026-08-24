"""OrderManager: 실행 식별자 발급과 미체결 주문 대기열.

식별자는 uuid가 아니라 run 범위 일련번호다 — 같은 입력이면
같은 id가 나와야 실행 재현과 결과 diff가 가능하다.
"""

from __future__ import annotations

from backtest_engine.types.events import OrderEvent
from backtest_engine.types.instruments import InstrumentId


class OrderManager:
    def __init__(self) -> None:
        self._decision_seq = 0
        self._order_seq = 0
        self._fill_seq = 0
        self._open_orders: dict[str, OrderEvent] = {}

    # --- 식별자 발급 ---------------------------------------------------------

    def next_decision_id(self) -> str:
        self._decision_seq += 1
        return f"D-{self._decision_seq:06d}"

    def next_order_id(self) -> str:
        self._order_seq += 1
        return f"O-{self._order_seq:06d}"

    def next_fill_id(self) -> str:
        self._fill_seq += 1
        return f"F-{self._fill_seq:06d}"

    # --- 대기열 --------------------------------------------------------------

    def place(self, order: OrderEvent) -> None:
        if order.order_id in self._open_orders:
            raise ValueError(
                f"duplicate order id — order_id={order.order_id} "
                f"instrument={order.instrument.symbol}"
            )
        self._open_orders[order.order_id] = order

    def open_orders(self) -> tuple[OrderEvent, ...]:
        return tuple(self._open_orders.values())

    def pop_all(self) -> tuple[OrderEvent, ...]:
        """대기 중인 주문 전부를 꺼낸다. v1 주문은 전부 다음 세션에 처리된다."""
        orders = tuple(self._open_orders.values())
        self._open_orders.clear()
        return orders

    def cancel_for_instrument(self, instrument: InstrumentId) -> tuple[OrderEvent, ...]:
        """해당 종목의 미체결 주문을 취소하고 취소된 주문을 반환한다."""
        cancelled = tuple(
            order for order in self._open_orders.values() if order.instrument == instrument
        )
        for order in cancelled:
            del self._open_orders[order.order_id]
        return cancelled
