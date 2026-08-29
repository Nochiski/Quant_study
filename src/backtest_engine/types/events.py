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
    OPEN = "open"  # 이 세션에 체결되지 못했지만 대기 유지 — 사유(유동성·여력)를 detail에 남김
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    REPLACED = "replaced"


@dataclass(frozen=True)
class OrderUpdateEvent:
    """주문 상태 변화 통지. EventStore에 기록되고, ORDER_UPDATE를 선언한 전략에 전달된다 (4c)."""

    ts: datetime
    order_id: str
    status: OrderStatus
    detail: str | None = None


class CorporateActionType(Enum):
    SPLIT = "split"  # 주식 수 증가 + 가격 반비례 확인
    REVERSE_SPLIT = "reverse_split"  # 주식 수 감소 + 가격 반비례 확인
    SHARE_COUNT_CHANGE = "share_count_change"  # 주식 수 변화만 확인 (가격 미확인) — 알림 전용


@dataclass(frozen=True)
class CorporateActionEvent:
    """상장주식수 변동 사건 통지 (D1).

    ratio는 구주 1주당 신주 수(SPLIT이면 > 1). 엔진은 SPLIT/REVERSE_SPLIT에만 포지션을
    조정하고, SHARE_COUNT_CHANGE는 기록·알림만 한다. detail은 검출 근거.
    """

    ts: datetime
    instrument: InstrumentId
    action_type: CorporateActionType
    ratio: Decimal
    detail: str

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError(
                f"corporate action ratio must be > 0 — instrument={self.instrument.symbol} "
                f"ts={self.ts} ratio={self.ratio}"
            )


@dataclass(frozen=True)
class CorporateActionApplied:
    """엔진이 포지션에 실제로 적용한 자본변동 기록. Fill과 함께 회계 재계산의 입력이다."""

    ts: datetime
    instrument: InstrumentId
    action: CorporateActionEvent
    old_quantity: Decimal
    new_quantity: Decimal
    old_average_price: float
    new_average_price: float
    cash_paid: float  # 단주(소수 부분) 정산 현금, 세션 시가 기준


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
class OpenOrderSnapshot:
    """전략에 보여주는 대기 주문: 원 주문과 현재 잔량. ctx.open_orders()의 원소."""

    order: OrderEvent
    remaining: Decimal

    @property
    def order_id(self) -> str:
        return self.order.order_id

    @property
    def instrument(self) -> InstrumentId:
        return self.order.instrument

    @property
    def side(self) -> Side:
        return self.order.side


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


class CostKind(Enum):
    SHORT_BORROW = "short_borrow"  # 숏 포지션 차입 비용 (세션 종료 평가액 기준)
    MARGIN_INTEREST = "margin_interest"  # 음수 현금 이자 (세션 종료 잔액 기준)


@dataclass(frozen=True)
class CostAccrued:
    """Fill 없이 현금을 줄이는 비용 발생 기록. 회계 재계산의 입력이다 (5단계)."""

    ts: datetime
    kind: CostKind
    instrument: InstrumentId | None
    amount: float  # 항상 > 0, 현금에서 차감

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(
                f"cost amount must be > 0 — kind={self.kind.value} ts={self.ts} "
                f"instrument={self.instrument.symbol if self.instrument else None} "
                f"amount={self.amount}"
            )


StrategyEvent = MarketSnapshot | TimerEvent | FillEvent | OrderUpdateEvent | CorporateActionEvent
