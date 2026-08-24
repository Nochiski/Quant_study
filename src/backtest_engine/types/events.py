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
from backtest_engine.types.orders import Side


@dataclass(frozen=True)
class TimerEvent:
    """schedule로 예약된 시각에 도착하는 호출. (스키마만 정의, v1 미전달)"""

    ts: datetime
    name: str


class OrderStatus(Enum):
    NEW = "new"
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
    """

    order_id: str
    decision_id: str
    ts: datetime
    instrument: InstrumentId
    quantity: Decimal
    side: Side
    source_action: StrategyAction

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"order quantity must be > 0 — order_id={self.order_id} "
                f"instrument={self.instrument.symbol} quantity={self.quantity}"
            )


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


StrategyEvent = (
    MarketSnapshot | TimerEvent | FillEvent | OrderUpdateEvent | CorporateActionEvent
)
