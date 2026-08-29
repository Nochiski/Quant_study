"""BrokerSim: 일봉 OHLC로 주문 종류별 트리거·체결을 결정하는 체결 모델.

체결 규칙 (경로 정보가 없는 일봉에서 결정론적으로 정한다):
- MARKET: 시가.
- LIMIT L: 시가가 이미 L 이내면 시가, 장중 L에 닿으면 L, 아니면 미체결.
- STOP S: 시가가 이미 S를 지났으면 시가, 장중 S에 닿으면 S, 아니면 미발동.
- STOP_LIMIT: STOP 규칙으로 발동한 가격 p가 L 이내면 p에 체결, 아니면 발동만
  기록하고 이후 세션부터 LIMIT으로 평가한다.
낙관적 가정(장중 최유리가 체결)은 쓰지 않는다.

회계 제약 (Capability에 반영):
- 매수는 현금 한도에 걸리면 체결 수량이 줄 수 있다 — 유동성 부분체결이
  아니라 MARGIN 미구현 상태에서 현금 초과 매수를 막는 규칙이다.
- 슬리피지 모델 없음: slippage_per_share=0.0으로 기록만 한다 (4d에서 포트로 분리).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

from backtest_engine.engine.orders import OpenOrder
from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.market import Bar
from backtest_engine.types.orders import OrderType, Side


class ExecutionStatus(Enum):
    FILLED = "filled"
    CASH_LIMITED = "cash_limited"  # 현금 한도로 주문 수량 일부만 체결
    REJECTED_NO_CASH = "rejected_no_cash"  # 1주도 살 수 없어 주문 전체 거절
    NOT_FILLED = "not_filled"  # 지정가·스톱 조건 미충족, 주문은 대기 유지
    TRIGGERED_UNFILLED = "triggered_unfilled"  # STOP_LIMIT 발동했지만 지정가 미충족


@dataclass(frozen=True)
class ExecutionOutcome:
    fill: FillEvent | None
    status: ExecutionStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.fill is not None


@dataclass(frozen=True)
class PriceDecision:
    price: float | None  # None이면 이 세션에 체결 없음
    triggered: bool  # STOP/STOP_LIMIT이 이 세션에 발동했는가


def execution_price(order: OrderEvent, bar: Bar, already_triggered: bool) -> PriceDecision:
    """주문 종류·방향과 bar OHLC로 체결 가격을 정한다. 순수 함수."""
    buy = order.side is Side.BUY
    match order.order_type:
        case OrderType.MARKET:
            return PriceDecision(bar.open, False)
        case OrderType.LIMIT:
            return PriceDecision(_limit_price(bar, float(_require(order.limit_price)), buy), False)
        case OrderType.STOP:
            return PriceDecision(_stop_price(bar, float(_require(order.stop_price)), buy), False)
        case OrderType.STOP_LIMIT:
            limit = float(_require(order.limit_price))
            if already_triggered:
                return PriceDecision(_limit_price(bar, limit, buy), False)
            trigger = _stop_price(bar, float(_require(order.stop_price)), buy)
            if trigger is None:
                return PriceDecision(None, False)
            within_limit = trigger <= limit if buy else trigger >= limit
            return PriceDecision(trigger if within_limit else None, True)


def _limit_price(bar: Bar, limit: float, buy: bool) -> float | None:
    if buy:
        if bar.open <= limit:
            return bar.open
        return limit if bar.low <= limit else None
    if bar.open >= limit:
        return bar.open
    return limit if bar.high >= limit else None


def _stop_price(bar: Bar, stop: float, buy: bool) -> float | None:
    if buy:
        if bar.open >= stop:
            return bar.open
        return stop if bar.high >= stop else None
    if bar.open <= stop:
        return bar.open
    return stop if bar.low <= stop else None


def _require(price: Decimal | None) -> Decimal:
    if price is None:  # OrderEvent.__post_init__가 막으므로 방어용
        raise ValueError("order type requires a price that is missing")
    return price


class BrokerSim:
    def __init__(self, fee_bps: float) -> None:
        self._fee_rate = fee_bps / 10_000.0

    def fee_for(self, notional: float) -> float:
        return notional * self._fee_rate

    def execute(
        self,
        open_order: OpenOrder,
        bar: Bar,
        cash_available: float,
        fill_id: str,
    ) -> ExecutionOutcome:
        """대기 주문 하나를 해당 세션 bar로 체결 시도한다.

        cash_available은 같은 세션에서 앞서 처리된 주문까지 반영한
        사용 가능 현금이다 (매도 먼저, 매수 나중 규칙은 호출 측 책임).
        """
        order = open_order.order
        decision = execution_price(order, bar, open_order.triggered)
        if decision.price is None:
            status = (
                ExecutionStatus.TRIGGERED_UNFILLED
                if decision.triggered
                else ExecutionStatus.NOT_FILLED
            )
            return ExecutionOutcome(fill=None, status=status)
        price = decision.price

        if order.side is Side.SELL:
            quantity = open_order.remaining
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
            quantity = min(open_order.remaining, affordable)

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
        if quantity < open_order.remaining:
            return ExecutionOutcome(
                fill=fill,
                status=ExecutionStatus.CASH_LIMITED,
                detail=(
                    f"buy capped by available cash — order_id={order.order_id} "
                    f"instrument={order.instrument.symbol} remaining={open_order.remaining} "
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
