"""OrderManager: 실행 식별자 발급과 미체결 주문 대기열.

식별자는 uuid가 아니라 run 범위 일련번호다 — 같은 입력이면
같은 id가 나와야 실행 재현과 결과 diff가 가능하다.

OrderEvent는 frozen이라 잔량·발동 여부 같은 가변 상태는 OpenOrder가 갖는다.
한 Order에 여러 Fill이 붙어도 order_id는 바뀌지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backtest_engine.types.events import OpenOrderSnapshot, OrderEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot


@dataclass
class OpenOrder:
    """대기 중인 주문의 가변 상태. remaining은 아직 체결되지 않은 수량."""

    order: OrderEvent
    remaining: Decimal
    triggered: bool = False

    @property
    def order_id(self) -> str:
        return self.order.order_id

    @property
    def is_partially_filled(self) -> bool:
        return self.remaining < self.order.quantity


class OrderManager:
    def __init__(self) -> None:
        self._decision_seq = 0
        self._order_seq = 0
        self._fill_seq = 0
        self._open: dict[str, OpenOrder] = {}

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
        if order.order_id in self._open:
            raise ValueError(
                f"duplicate order id — order_id={order.order_id} "
                f"instrument={order.instrument.symbol}"
            )
        self._open[order.order_id] = OpenOrder(order=order, remaining=order.quantity)

    def open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        """대기 주문과 잔량 (전략이 Cancel/Replace 수량을 정할 때 잔량을 봐야 한다)."""
        return tuple(
            OpenOrderSnapshot(order=entry.order, remaining=entry.remaining)
            for entry in self._open.values()
        )

    def open_entries(self) -> tuple[OpenOrder, ...]:
        return tuple(self._open.values())

    def get(self, order_id: str) -> OpenOrder | None:
        return self._open.get(order_id)

    def due(self, snapshot: MarketSnapshot) -> tuple[OpenOrder, ...]:
        """이 세션에 거래 가능한(bar가 있는) 대기 주문."""
        return tuple(entry for entry in self._open.values() if snapshot.has(entry.order.instrument))

    def settle(self, order_id: str, filled: Decimal) -> Decimal:
        """체결 수량을 반영하고 잔량을 돌려준다. 잔량 0이면 대기열에서 제거."""
        entry = self._require(order_id)
        if filled <= 0 or filled > entry.remaining:
            raise ValueError(
                f"fill quantity out of range — order_id={order_id} "
                f"filled={filled} remaining={entry.remaining}"
            )
        entry.remaining -= filled
        if entry.remaining == 0:
            del self._open[order_id]
        return entry.remaining

    def mark_triggered(self, order_id: str) -> None:
        self._require(order_id).triggered = True

    def remove(self, order_id: str) -> OpenOrder:
        """취소·만료로 대기열에서 뺀다. 남은 상태는 호출 측이 기록한다."""
        entry = self._require(order_id)
        del self._open[order_id]
        return entry

    def cancel_for_instrument(self, instrument: InstrumentId) -> tuple[OrderEvent, ...]:
        """해당 종목의 미체결 주문을 취소하고 취소된 주문을 반환한다."""
        cancelled = tuple(
            entry.order for entry in self._open.values() if entry.order.instrument == instrument
        )
        for order in cancelled:
            del self._open[order.order_id]
        return cancelled

    def drain(self) -> tuple[OpenOrder, ...]:
        """대기열 전부를 비운다 (run 종료)."""
        entries = tuple(self._open.values())
        self._open.clear()
        return entries

    def _require(self, order_id: str) -> OpenOrder:
        entry = self._open.get(order_id)
        if entry is None:
            raise KeyError(f"order not open — order_id={order_id}")
        return entry
