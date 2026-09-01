"""append-only 이벤트 저장소.

결과가 이상할 때 Run → Decision → Action → Order → Fill → Snapshot
순서로 원인을 역추적할 수 있도록 실행 중 발생한 모든 것을 순서대로 남긴다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    FillEvent,
    OrderEvent,
    OrderUpdateEvent,
    StrategyEvent,
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


@dataclass(frozen=True)
class DecisionTapeEntry:
    """전략 callback 입력과 반환을 순서대로 고정한 differential-test 기록."""

    seq: int
    event: StrategyEvent
    decision_id: str
    decision: StrategyDecision


def _normalized(value: Any) -> Any:
    """Convert engine values to an exact, deterministic JSON-compatible shape."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return {"$float": str(value)}
        return {"$float": value.hex()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat(timespec="microseconds")}
    if isinstance(value, Enum):
        return {
            "$enum": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _normalized(value.value),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{field.name: _normalized(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, dict):
        items = [(_normalized(key), _normalized(item)) for key, item in value.items()]
        items.sort(key=lambda pair: _json_bytes(pair[0]))
        return {"$dict": items}
    if isinstance(value, (set, frozenset)):
        items = [_normalized(item) for item in value]
        items.sort(key=_json_bytes)
        return {"$set": items}
    if isinstance(value, (tuple, list)):
        return [_normalized(item) for item in value]
    raise TypeError(f"unsupported trace value: {type(value).__module__}.{type(value).__qualname__}")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class EventStore:
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._decision_tape: list[DecisionTapeEntry] = []

    def append(self, ts: datetime, kind: RecordKind, payload: RecordPayload) -> None:
        self._records.append(Record(seq=len(self._records), ts=ts, kind=kind, payload=payload))

    @property
    def records(self) -> tuple[Record, ...]:
        return tuple(self._records)

    @property
    def decision_tape(self) -> tuple[DecisionTapeEntry, ...]:
        return tuple(self._decision_tape)

    def record_callback(
        self,
        event: StrategyEvent,
        decision_id: str,
        decision: StrategyDecision,
    ) -> None:
        self._decision_tape.append(
            DecisionTapeEntry(
                seq=len(self._decision_tape),
                event=event,
                decision_id=decision_id,
                decision=decision,
            )
        )

    def normalized_trace(self) -> tuple[dict[str, Any], ...]:
        """Return an exact, stable shape suitable for snapshots and differential tests."""
        return tuple(_normalized(record) for record in self._records)

    def trace_bytes(self) -> bytes:
        """Serialize the full EventStore trace deterministically."""
        return _json_bytes(self.normalized_trace())

    def decision_tape_bytes(self) -> bytes:
        """Serialize callback input/output ordering deterministically."""
        return _json_bytes(tuple(_normalized(entry) for entry in self._decision_tape))

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
