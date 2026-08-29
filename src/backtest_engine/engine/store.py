"""append-only 이벤트 저장소.

결과가 이상할 때 Run → Decision → Action → Order → Fill → Snapshot
순서로 원인을 역추적할 수 있도록 실행 중 발생한 모든 것을 순서대로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    FillEvent,
    OrderEvent,
    OrderUpdateEvent,
)
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.portfolio import PortfolioSnapshot


class RecordKind(Enum):
    MARKET = "market"
    DECISION = "decision"
    ORDER = "order"
    ORDER_UPDATE = "order_update"
    FILL = "fill"
    SNAPSHOT = "snapshot"
    CORPORATE_ACTION = "corporate_action"  # 사건 도착 (적용 여부와 무관)
    CORPORATE_ACTION_APPLIED = "corporate_action_applied"  # 포지션에 실제 적용된 기록
    COST = "cost"  # 차입·이자 등 Fill 없는 현금 차감


@dataclass(frozen=True)
class DecisionRecord:
    """어떤 판단이었는지 추적하기 위해 decision_id를 함께 남긴다."""

    decision_id: str
    decision: StrategyDecision


RecordPayload = (
    MarketSnapshot
    | DecisionRecord
    | OrderEvent
    | OrderUpdateEvent
    | FillEvent
    | PortfolioSnapshot
    | CorporateActionEvent
    | CorporateActionApplied
    | CostAccrued
)


@dataclass(frozen=True)
class Record:
    seq: int
    ts: datetime
    kind: RecordKind
    payload: RecordPayload


class EventStore:
    def __init__(self) -> None:
        self._records: list[Record] = []

    def append(self, ts: datetime, kind: RecordKind, payload: RecordPayload) -> None:
        self._records.append(Record(seq=len(self._records), ts=ts, kind=kind, payload=payload))

    @property
    def records(self) -> tuple[Record, ...]:
        return tuple(self._records)

    def _payloads(self, kind: RecordKind) -> tuple[RecordPayload, ...]:
        return tuple(record.payload for record in self._records if record.kind is kind)

    def decisions(self) -> tuple[DecisionRecord, ...]:
        return tuple(
            p for p in self._payloads(RecordKind.DECISION) if isinstance(p, DecisionRecord)
        )

    def orders(self) -> tuple[OrderEvent, ...]:
        return tuple(p for p in self._payloads(RecordKind.ORDER) if isinstance(p, OrderEvent))

    def fills(self) -> tuple[FillEvent, ...]:
        return tuple(p for p in self._payloads(RecordKind.FILL) if isinstance(p, FillEvent))

    def order_updates(self) -> tuple[OrderUpdateEvent, ...]:
        return tuple(
            p for p in self._payloads(RecordKind.ORDER_UPDATE) if isinstance(p, OrderUpdateEvent)
        )

    def snapshots(self) -> tuple[PortfolioSnapshot, ...]:
        return tuple(
            p for p in self._payloads(RecordKind.SNAPSHOT) if isinstance(p, PortfolioSnapshot)
        )

    def corporate_actions(self) -> tuple[CorporateActionEvent, ...]:
        return tuple(
            p
            for p in self._payloads(RecordKind.CORPORATE_ACTION)
            if isinstance(p, CorporateActionEvent)
        )

    def corporate_actions_applied(self) -> tuple[CorporateActionApplied, ...]:
        return tuple(
            p
            for p in self._payloads(RecordKind.CORPORATE_ACTION_APPLIED)
            if isinstance(p, CorporateActionApplied)
        )

    def costs(self) -> tuple[CostAccrued, ...]:
        return tuple(p for p in self._payloads(RecordKind.COST) if isinstance(p, CostAccrued))
