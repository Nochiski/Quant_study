"""Persistent Rust 경로의 Python-side compact payload.

공개 dataclass 이벤트는 결과 조회 또는 전략 callback에 필요할 때만 만든다. 이 타입들은
Rust append-only record token의 side table payload이며 mutable 실행 상태를 소유하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from backtest_engine.engine.orders import BasketGroup
from backtest_engine.types.actions import StrategyAction
from backtest_engine.types.events import FillEvent, OrderEvent, OrderStatus, OrderUpdateEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.orders import OrderType, Side, TimeInForce


@dataclass(frozen=True, slots=True)
class CompactOrder:
    order_id: str
    decision_id: str
    ts: datetime
    instrument: InstrumentId
    quantity: int
    side: Side
    source_action: StrategyAction
    order_type: OrderType
    limit_price: str | None
    stop_price: str | None
    time_in_force: TimeInForce
    group_id: str | None

    def materialize(self) -> OrderEvent:
        return OrderEvent(
            order_id=self.order_id,
            decision_id=self.decision_id,
            ts=self.ts,
            instrument=self.instrument,
            quantity=Decimal(self.quantity),
            side=self.side,
            source_action=self.source_action,
            order_type=self.order_type,
            limit_price=None if self.limit_price is None else Decimal(self.limit_price),
            stop_price=None if self.stop_price is None else Decimal(self.stop_price),
            time_in_force=self.time_in_force,
            group_id=self.group_id,
        )

    @classmethod
    def from_event(cls, order: OrderEvent) -> CompactOrder:
        return cls(
            order_id=order.order_id,
            decision_id=order.decision_id,
            ts=order.ts,
            instrument=order.instrument,
            quantity=int(order.quantity),
            side=order.side,
            source_action=order.source_action,
            order_type=order.order_type,
            limit_price=None if order.limit_price is None else str(order.limit_price),
            stop_price=None if order.stop_price is None else str(order.stop_price),
            time_in_force=order.time_in_force,
            group_id=order.group_id,
        )


@dataclass(frozen=True, slots=True)
class CompactFill:
    fill_id: str
    order_id: str
    ts: datetime
    instrument: InstrumentId
    quantity: int
    side: Side
    price: float
    fee: float
    slippage_per_share: float

    def materialize(self) -> FillEvent:
        return FillEvent(
            fill_id=self.fill_id,
            order_id=self.order_id,
            ts=self.ts,
            instrument=self.instrument,
            quantity=Decimal(self.quantity),
            side=self.side,
            price=self.price,
            fee=self.fee,
            slippage_per_share=self.slippage_per_share,
        )


@dataclass(frozen=True, slots=True)
class CompactOrderUpdate:
    ts: datetime
    order_id: str
    status: OrderStatus
    detail: str | None

    def materialize(self) -> OrderUpdateEvent:
        return OrderUpdateEvent(
            ts=self.ts,
            order_id=self.order_id,
            status=self.status,
            detail=self.detail,
        )


@dataclass(frozen=True, slots=True)
class CompactRoutingResult:
    orders: tuple[CompactOrder, ...]
    updates: tuple[CompactOrderUpdate, ...]
    groups: tuple[BasketGroup, ...] = ()


CompactRecordPayload = CompactOrder | CompactFill | CompactOrderUpdate


def materialize_payload(payload: object) -> object:
    if isinstance(payload, (CompactOrder, CompactFill, CompactOrderUpdate)):
        return payload.materialize()
    return payload
