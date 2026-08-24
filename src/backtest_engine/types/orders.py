"""주문 요청 타입. 주문 종류별 필수 가격 필드를 별도 타입으로 강제한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from backtest_engine.types.instruments import InstrumentId


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


@dataclass(frozen=True)
class OrderCore:
    """모든 주문 종류가 공유하는 필드. quantity는 항상 양수, 방향은 Side로만 표현."""

    instrument: InstrumentId
    side: Side
    quantity: Decimal
    time_in_force: TimeInForce

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"order quantity must be > 0 — instrument={self.instrument.symbol} "
                f"side={self.side.value} quantity={self.quantity}"
            )


@dataclass(frozen=True)
class MarketOrderRequest:
    core: OrderCore


@dataclass(frozen=True)
class LimitOrderRequest:
    core: OrderCore
    limit_price: Decimal


@dataclass(frozen=True)
class StopOrderRequest:
    core: OrderCore
    stop_price: Decimal


@dataclass(frozen=True)
class StopLimitOrderRequest:
    core: OrderCore
    stop_price: Decimal
    limit_price: Decimal


OrderRequest = (
    MarketOrderRequest | LimitOrderRequest | StopOrderRequest | StopLimitOrderRequest
)
