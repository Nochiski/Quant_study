"""BrokerSim: 시장가 주문을 세션 시가로 체결하는 v1 체결 모델.

v1 가정 (Capability에 반영):
- 모든 주문은 다음 세션 시가에 시장가로 전량 체결된다 (유동성 무제한).
- PARTIAL_FILL(유동성 기반 부분체결)은 미구현. 단, 매수는 현금 한도에
  걸리면 체결 수량이 줄 수 있다 — 이는 유동성이 아니라 MARGIN 미구현
  상태에서 현금 초과 매수를 막는 회계 제약이다.
- 슬리피지 모델 없음: slippage_per_share=0.0으로 기록만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.market import Bar
from backtest_engine.types.orders import Side


class ExecutionStatus(Enum):
    FILLED = "filled"
    CASH_LIMITED = "cash_limited"  # 현금 한도로 주문 수량 일부만 체결
    REJECTED_NO_CASH = "rejected_no_cash"  # 1주도 살 수 없어 주문 전체 거절


@dataclass(frozen=True)
class ExecutionOutcome:
    fill: FillEvent | None
    status: ExecutionStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.fill is not None


class BrokerSim:
    def __init__(self, fee_bps: float) -> None:
        self._fee_rate = fee_bps / 10_000.0

    def fee_for(self, notional: float) -> float:
        return notional * self._fee_rate

    def execute(
        self,
        order: OrderEvent,
        bar: Bar,
        cash_available: float,
        fill_id: str,
    ) -> ExecutionOutcome:
        """주문 하나를 해당 세션 시가로 체결한다.

        cash_available은 같은 세션에서 앞서 처리된 주문까지 반영한
        사용 가능 현금이다 (매도 먼저, 매수 나중 규칙은 호출 측 책임).
        """
        price = bar.open

        if order.side is Side.SELL:
            quantity = order.quantity
        else:
            affordable = self._affordable_quantity(cash_available, price)
            if affordable <= 0:
                return ExecutionOutcome(
                    fill=None,
                    status=ExecutionStatus.REJECTED_NO_CASH,
                    detail=(
                        f"cannot afford a single share — order_id={order.order_id} "
                        f"instrument={order.instrument.symbol} price={price} "
                        f"cash_available={cash_available}"
                    ),
                )
            quantity = min(order.quantity, affordable)

        notional = float(quantity) * price
        fill = FillEvent(
            fill_id=fill_id,
            order_id=order.order_id,
            ts=bar.ts,
            instrument=order.instrument,
            quantity=quantity,
            side=order.side,
            price=price,
            fee=self.fee_for(notional),
            slippage_per_share=0.0,
        )
        if quantity < order.quantity:
            return ExecutionOutcome(
                fill=fill,
                status=ExecutionStatus.CASH_LIMITED,
                detail=(
                    f"buy capped by available cash — order_id={order.order_id} "
                    f"instrument={order.instrument.symbol} ordered={order.quantity} "
                    f"filled={quantity} price={price} cash_available={cash_available}"
                ),
            )
        return ExecutionOutcome(fill=fill, status=ExecutionStatus.FILLED)

    def _affordable_quantity(self, cash_available: float, price: float) -> Decimal:
        """수수료까지 포함해 현금으로 살 수 있는 최대 정수 수량."""
        if price <= 0 or cash_available <= 0:
            return Decimal(0)
        raw = cash_available / (price * (1.0 + self._fee_rate))
        return Decimal(raw).quantize(Decimal(1), rounding=ROUND_FLOOR)
