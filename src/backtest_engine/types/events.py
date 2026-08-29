"""이벤트 타입.

- StrategyEvent: 엔진 → 전략으로 전달되는 호출 원인.
- OrderEvent / FillEvent: 엔진 내부의 주문·체결 기록.
  FillEvent는 포트폴리오 상태를 바꿀 수 있는 유일한 거래 이벤트이며,
  전략에도 전달될 수 있어 StrategyEvent 합 타입에 포함된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from backtest_engine.types.actions import StrategyAction
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import OrderType, Side, TimeInForce


@dataclass(frozen=True)
class TimerEvent:
    """schedule로 예약된 시각에 도착하는 호출. (스키마만 정의, v1 미전달)"""

    ts: datetime
    name: str


class OrderStatus(Enum):
    NEW = "new"
    TRIGGERED = "triggered"  # STOP_LIMIT이 발동했지만 지정가 미충족으로 대기 (4b 확장)
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    REPLACED = "replaced"


@dataclass(frozen=True)
class OrderUpdateEvent:
    """주문 상태 변화 통지. (스키마만 정의, v1 미전달)"""

    ts: datetime
    order_id: str
    status: OrderStatus
    detail: str | None = None


@dataclass(frozen=True)
class CorporateActionEvent:
    """배당·분할 등 기업 행위 통지. (스키마만 정의, v1 미전달)"""

    ts: datetime
    instrument: InstrumentId
    action_type: str


@dataclass(frozen=True)
class OrderEvent:
    """전략 판단을 실제로 제출 가능한 매매 지시로 변환한 결과.

    decision_id와 source_action을 남겨 어떤 전략 판단에서 나온 주문인지 추적한다.
    quantity는 항상 양수, 방향은 Side로만 표현한다.

    order_type/limit_price/stop_price/time_in_force는 4b에서 추가된 평면 필드다.
    기본값(MARKET/None/None/DAY)은 v1 주문과 같아 기존 생성 코드가 그대로 돈다.
    """

    order_id: str
    decision_id: str
    ts: datetime
    instrument: InstrumentId
    quantity: Decimal
    side: Side
    source_action: StrategyAction
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"order quantity must be > 0 — order_id={self.order_id} "
                f"instrument={self.instrument.symbol} quantity={self.quantity}"
            )
        needs_limit = self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
        if needs_limit != (self.limit_price is not None):
            raise ValueError(
                f"limit_price must be set iff order type is LIMIT/STOP_LIMIT — "
                f"order_id={self.order_id} order_type={self.order_type.value} "
                f"limit_price={self.limit_price}"
            )
        if needs_stop != (self.stop_price is not None):
            raise ValueError(
                f"stop_price must be set iff order type is STOP/STOP_LIMIT — "
                f"order_id={self.order_id} order_type={self.order_type.value} "
                f"stop_price={self.stop_price}"
            )
        for label, price in (("limit_price", self.limit_price), ("stop_price", self.stop_price)):
            if price is not None and price <= 0:
                raise ValueError(f"{label} must be > 0 — order_id={self.order_id} {label}={price}")


@dataclass(frozen=True)
class FillEvent:
    """주문 중 실제로 체결됐다고 시뮬레이션된 부분.

    포트폴리오 상태를 바꿀 수 있는 유일한 거래 이벤트다.
    fee는 총액, slippage는 주당 금액으로 고정한다.
    한 Order에서 여러 Fill이 나올 수 있으므로 order_id로 묶는다.

    side는 설계 노트 스키마에 없지만, "fills와 bars만으로 equity curve를
    재계산할 수 있어야 한다"는 불변조건을 위해 Fill 자체에 방향을 남긴다.
    """

    fill_id: str
    order_id: str
    ts: datetime
    instrument: InstrumentId
    quantity: Decimal
    side: Side
    price: float
    fee: float
    slippage_per_share: float

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"fill quantity must be > 0 — fill_id={self.fill_id} "
                f"order_id={self.order_id} quantity={self.quantity}"
            )
        if self.price <= 0:
            raise ValueError(
                f"fill price must be > 0 — fill_id={self.fill_id} "
                f"order_id={self.order_id} instrument={self.instrument.symbol} "
                f"price={self.price}"
            )


StrategyEvent = MarketSnapshot | TimerEvent | FillEvent | OrderUpdateEvent | CorporateActionEvent
