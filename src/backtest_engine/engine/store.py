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
from typing import Any, cast

from backtest_engine.engine.compact import CompactFill, CompactRecordPayload, materialize_payload
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


_RECORD_KINDS = tuple(RecordKind)
_RECORD_KIND_CODES = {kind: code for code, kind in enumerate(_RECORD_KINDS)}


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
        return tuple(_normalized(record) for record in self.records)

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


class PersistentEventStore(EventStore):
    """Rust append-only index + Python lazy public-object materialization adapter."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._payloads_by_token: dict[
            int, tuple[datetime, RecordPayload | CompactRecordPayload]
        ] = {}
        self._payload_token = 0
        self._records_cache: tuple[Record, ...] | None = None
        self._payload_cache: dict[RecordKind, tuple[RecordPayload, ...]] = {}
        self._finished_batch: list[tuple[int, int, int, int]] | None = None
        self._pending_records: list[tuple[int, int, int]] = []
        self._timestamp_cache: dict[datetime, int] = {}
        self._decision_tape: list[DecisionTapeEntry] = []

    @staticmethod
    def _timestamp_micros(ts: datetime) -> int:
        offset = ts.utcoffset()
        normalized = ts if offset is None else ts - offset
        return (
            (
                (
                    (normalized.toordinal() * 24 + normalized.hour) * 60
                    + normalized.minute
                )
                * 60
                + normalized.second
            )
            * 1_000_000
            + normalized.microsecond
        )

    def append(
        self,
        ts: datetime,
        kind: RecordKind,
        payload: RecordPayload | CompactRecordPayload,
    ) -> None:
        self._payload_token += 1
        token = self._payload_token
        timestamp_micros = self._timestamp_cache.get(ts)
        if timestamp_micros is None:
            timestamp_micros = self._timestamp_micros(ts)
            self._timestamp_cache[ts] = timestamp_micros
        self._pending_records.append((timestamp_micros, _RECORD_KIND_CODES[kind], token))
        self._payloads_by_token[token] = (ts, payload)
        self._records_cache = None
        self._payload_cache.pop(kind, None)
        if len(self._pending_records) >= 1_024:
            self._flush()

    def _flush(self) -> None:
        if not self._pending_records:
            return
        self._runtime.record_extend(self._pending_records)
        self._pending_records = []

    @property
    def records(self) -> tuple[Record, ...]:
        if self._records_cache is None:
            batch = self._batch()
            self._records_cache = tuple(
                Record(
                    seq=seq,
                    ts=self._payloads_by_token[token][0],
                    kind=_RECORD_KINDS[kind_code],
                    payload=self._materialize_token(token),
                )
                for seq, _timestamp_micros, kind_code, token in batch
            )
        return self._records_cache

    def _batch(self) -> list[tuple[int, int, int, int]]:
        self._flush()
        return (
            self._finished_batch
            if self._finished_batch is not None
            else self._runtime.record_batch()
        )

    def _materialize_token(self, token: int) -> RecordPayload:
        ts, payload = self._payloads_by_token[token]
        materialized = cast(RecordPayload, materialize_payload(payload))
        if materialized is not payload:
            # 결과와 EventStore가 동일 객체를 공유하도록 compact payload를 즉시 놓는다.
            self._payloads_by_token[token] = (ts, materialized)
        return materialized

    def finish(self) -> None:
        self._flush()
        self._finished_batch = self._runtime.finish()
        self._records_cache = None
        self._payload_cache.clear()

    def _payloads(self, kind: RecordKind) -> tuple[RecordPayload, ...]:
        cached = self._payload_cache.get(kind)
        if cached is None:
            kind_code = _RECORD_KIND_CODES[kind]
            cached = tuple(
                self._materialize_token(token)
                for _seq, _timestamp_micros, code, token in self._batch()
                if code == kind_code
            )
            self._payload_cache[kind] = cached
        return cached

    def compact_trace(self) -> tuple[tuple[int, int, str, int], ...]:
        """Return the primitive Rust record index for low-overhead debugging."""
        return tuple(
            (seq, timestamp_micros, _RECORD_KINDS[kind_code].value, token)
            for seq, timestamp_micros, kind_code, token in self._batch()
        )

    def traded_notional(self) -> float:
        """Sum fill notional directly from compact payloads for batch metrics."""
        kind_code = _RECORD_KIND_CODES[RecordKind.FILL]
        total = 0.0
        for _seq, _timestamp_micros, code, token in self._batch():
            if code != kind_code:
                continue
            payload = self._payloads_by_token[token][1]
            if isinstance(payload, CompactFill):
                total += payload.quantity * payload.price
            elif isinstance(payload, FillEvent):
                total += float(payload.quantity) * payload.price
        return total
