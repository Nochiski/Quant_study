"""append-only 이벤트 저장소.

결과가 이상할 때 Run → Decision → Action → Order → Fill → Snapshot
순서로 원인을 역추적할 수 있도록 실행 중 발생한 모든 것을 순서대로 남긴다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from backtest_engine.types.actions import (
    BasketAction,
    PositionTarget,
    QuantityTarget,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    CostKind,
    FillEvent,
    OpenOrderSnapshot,
    OrderEvent,
    OrderStatus,
    OrderUpdateEvent,
    StrategyEvent,
)
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import OrderType, Side, TimeInForce
from backtest_engine.types.portfolio import PortfolioSnapshot, Position
from backtest_engine.types.tape import TapeFrame


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
    """Rust runtime이 소유한 레코드 배치를 공개 Event 객체로 lazy materialize하는 어댑터.

    실행 중에는 Python 객체를 만들지 않는다. Rust 레코드 payload는 원시 wire이고, Python 객체가
    필요한 payload(DECISION의 결정 객체, CORPORATE_ACTION의 입력 사건, MARKET의 feed snapshot)만
    side table로 보관한다. `records`/`orders()`/`fills()`는 최초 조회 시 seq 순서로 만든다.
    """

    # `Any`: backtest_core는 pyo3 확장 모듈이라 stub이 없다 (typings/backtest_core는 레거시
    # 함수만 담는다). runtime과 그 wire tuple은 이 클래스 안에서만 풀어 공개 타입으로 바꾼다.
    def __init__(self, runtime: Any) -> None:  # reason: pyo3 확장 모듈 stub 부재
        super().__init__()
        self._runtime = runtime
        self._sessions: tuple[datetime, ...] = ()
        self._instruments: tuple[InstrumentId, ...] = ()
        self._market_snapshots: tuple[MarketSnapshot, ...] = ()
        self._corporate_actions: tuple[CorporateActionEvent, ...] = ()
        self._decisions: dict[str, StrategyDecision] = {}
        # submit_decision이 Rust 안에서 실패하면 decision_id를 못 받는다 — 제출 직전에 잡아둔
        # 결정을 partial trace의 마지막 DECISION 레코드에 붙인다.
        self._staged_decision: StrategyDecision | None = None
        self._tape_frames: dict[int, TapeFrame] = {}
        self._decision_index: dict[str, tuple[int, Any]] = {}
        self._decision_index_len = -1
        self._finished_batch: list[tuple[int, int, int, Any]] | None = None
        self._materialized: dict[int, RecordPayload] = {}
        self._records_cache: tuple[Record, ...] | None = None

    # --- 실행 중 등록 ---------------------------------------------------------

    def bind_feed(
        self,
        sessions: tuple[datetime, ...],
        instruments: tuple[InstrumentId, ...],
        market_snapshots: tuple[MarketSnapshot, ...],
    ) -> None:
        self._sessions = sessions
        self._instruments = instruments
        self._market_snapshots = market_snapshots

    def bind_corporate_actions(self, actions: tuple[CorporateActionEvent, ...]) -> None:
        self._corporate_actions = actions

    def append(self, ts: datetime, kind: RecordKind, payload: RecordPayload) -> None:
        raise RuntimeError(
            "persistent event store is append-only from the Rust runtime — "
            f"refusing Python-side append kind={kind.value} ts={ts}"
        )

    def stage_decision(self, decision: StrategyDecision) -> None:
        self._staged_decision = decision

    def register_decision(self, decision_id: str, decision: StrategyDecision) -> None:
        self._decisions[decision_id] = decision
        self._staged_decision = None

    def bind_tape(self, frames_by_session: dict[int, TapeFrame]) -> None:
        # idle 사유는 Rust가 DECISION payload의 reason으로 돌려주므로 여기서는 프레임만 보관한다.
        self._tape_frames = frames_by_session

    def _decision_by_id(self, decision_id: str) -> StrategyDecision:
        """ORDER/open order 복원용. tape 결정은 DECISION 레코드를 찾아 먼저 재구성한다."""
        registered = self._decisions.get(decision_id)
        if registered is not None:
            return registered
        batch = self._batch()
        # 결정마다 배치를 선형 스캔하면 주문 수 × 레코드 수로 커진다 — 배치 길이가 바뀔 때만
        # decision_id → (session_index, native) 인덱스를 다시 만든다.
        if self._decision_index_len != len(batch):
            decision_code = _RECORD_KIND_CODES[RecordKind.DECISION]
            self._decision_index = {
                payload[0]: (session_index, payload[1])
                for _seq, session_index, code, payload in batch
                if code == decision_code
            }
            self._decision_index_len = len(batch)
        found = self._decision_index.get(decision_id)
        if found is None:
            raise KeyError(
                f"order references a decision that is not in the record batch — "
                f"decision_id={decision_id} records={len(batch)}"
            )
        session_index, native = found
        return self._decision(self._sessions[session_index], decision_id, native)

    def _decision(self, ts: datetime, decision_id: str, native: Any) -> StrategyDecision:
        """Python이 제출한 결정은 side table에서, Rust tape가 만든 결정은 재구성 정보에서 만든다."""
        registered = self._decisions.get(decision_id)
        if registered is not None:
            return registered
        if native is None:
            staged = self._staged_decision
            if staged is None:
                raise KeyError(
                    f"decision was neither submitted by Python nor generated by the Rust tape — "
                    f"decision_id={decision_id} ts={ts} registered={len(self._decisions)}"
                )
            self._decisions[decision_id] = staged
            self._staged_decision = None
            return staged
        frame_session, kept_wires, _no_bar, reason = native
        if frame_session is None:
            built = StrategyDecision.no_action(ts, reason)
        else:
            frame = self._tape_frames[frame_session]
            kept: list[PositionTarget] = []
            for instrument_id, kind, weight, quantity in kept_wires:
                instrument = self._instruments[instrument_id]
                if kind == "weight":
                    kept.append(WeightTarget(instrument=instrument, weight=weight))
                else:
                    kept.append(QuantityTarget(instrument=instrument, quantity=Decimal(quantity)))
            built = StrategyDecision.of(ts, replace(frame.action, targets=tuple(kept)), reason)
        self._decisions[decision_id] = built
        return built

    def finish(self) -> None:
        self._finished_batch = self._runtime.finish()
        self._records_cache = None

    # --- wire → 공개 타입 -----------------------------------------------------

    def session_ts(self, session_index: int) -> datetime:
        return self._sessions[session_index]

    def snapshot_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> PortfolioSnapshot:
        cash, rows, equity, gross_exposure = wire
        instruments = self._instruments
        # Rust 원장이 삽입 순서를 보존하므로 행 순서가 곧 Python dict 순서다.
        positions = tuple(
            Position(
                instrument=instruments[instrument_id],
                quantity=Decimal(quantity),
                average_price=average_price,
                market_price=market_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
            )
            for (
                instrument_id,
                quantity,
                average_price,
                market_price,
                market_value,
                unrealized_pnl,
            ) in rows
        )
        return PortfolioSnapshot(
            ts=ts, cash=cash, positions=positions, equity=equity, gross_exposure=gross_exposure
        )

    def order_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> OrderEvent:
        (
            order_id,
            decision_id,
            instrument_id,
            quantity,
            side,
            order_type,
            limit_text,
            stop_text,
            time_in_force,
            group_id,
            action_index,
            leg_index,
        ) = wire
        action = self._decision_by_id(decision_id).actions[action_index]
        source_action = (
            action.legs[leg_index]
            if isinstance(action, BasketAction) and leg_index is not None
            else action
        )
        return OrderEvent(
            order_id=order_id,
            decision_id=decision_id,
            ts=ts,
            instrument=self._instruments[instrument_id],
            quantity=Decimal(quantity),
            side=Side(side),
            source_action=source_action,
            order_type=OrderType(order_type),
            limit_price=None if limit_text is None else Decimal(limit_text),
            stop_price=None if stop_text is None else Decimal(stop_text),
            time_in_force=TimeInForce(time_in_force),
            group_id=group_id,
        )

    def fill_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> FillEvent:
        fill_id, order_id, instrument_id, quantity, side, price, fee, slippage_per_share = wire
        return FillEvent(
            fill_id=fill_id,
            order_id=order_id,
            ts=ts,
            instrument=self._instruments[instrument_id],
            quantity=Decimal(quantity),
            side=Side(side),
            price=price,
            fee=fee,
            slippage_per_share=slippage_per_share,
        )

    def order_update_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> OrderUpdateEvent:
        order_id, status, detail = wire
        return OrderUpdateEvent(ts=ts, order_id=order_id, status=OrderStatus(status), detail=detail)

    def open_orders_from_wire(
        self, ts: datetime, rows: list[tuple[tuple[Any, ...], int]]
    ) -> tuple[OpenOrderSnapshot, ...]:
        return tuple(
            OpenOrderSnapshot(order=self.order_from_wire(ts, wire), remaining=Decimal(remaining))
            for wire, remaining in rows
        )

    def frame_event(self, frame: Any) -> StrategyEvent:
        """콜백 프레임의 이벤트 payload를 전략에 건넬 공개 이벤트로 바꾼다."""
        ts = self._sessions[frame.session_index]
        kind = frame.event_kind
        if kind == "market":
            return self._market_snapshots[frame.session_index]
        if kind == "fill":
            return self.fill_from_wire(ts, frame.event)
        if kind == "order_update":
            return self.order_update_from_wire(ts, frame.event)
        if kind == "corporate_action":
            return self._corporate_actions[frame.event]
        raise TypeError(f"unsupported strategy callback event — kind={kind!r}")

    # --- 레코드 조회 -------------------------------------------------------------

    def _batch(self) -> list[tuple[int, int, int, Any]]:
        if self._finished_batch is not None:
            return self._finished_batch
        # 종료 전(전략 예외 등)에는 Rust가 지금까지 쌓은 partial trace를 그대로 읽는다.
        return self._runtime.record_batch()

    def _materialize(
        self, seq: int, session_index: int, kind_code: int, payload: Any
    ) -> RecordPayload:
        cached = self._materialized.get(seq)
        if cached is not None:
            return cached
        ts = self._sessions[session_index]
        kind = _RECORD_KINDS[kind_code]
        built: RecordPayload
        if kind is RecordKind.MARKET:
            built = self._market_snapshots[session_index]
        elif kind is RecordKind.DECISION:
            decision_id, native = payload
            built = DecisionRecord(decision_id, self._decision(ts, decision_id, native))
        elif kind is RecordKind.ORDER:
            built = self.order_from_wire(ts, payload)
        elif kind is RecordKind.ORDER_UPDATE:
            built = self.order_update_from_wire(ts, payload)
        elif kind is RecordKind.FILL:
            built = self.fill_from_wire(ts, payload)
        elif kind is RecordKind.SNAPSHOT:
            built = self.snapshot_from_wire(ts, payload)
        elif kind is RecordKind.CORPORATE_ACTION:
            built = self._corporate_actions[payload]
        elif kind is RecordKind.CORPORATE_ACTION_APPLIED:
            index, old_quantity, new_quantity, old_average, new_average, cash_paid = payload
            action = self._corporate_actions[index]
            built = CorporateActionApplied(
                ts=ts,
                instrument=action.instrument,
                action=action,
                old_quantity=Decimal(old_quantity),
                new_quantity=Decimal(new_quantity),
                old_average_price=old_average,
                new_average_price=new_average,
                cash_paid=cash_paid,
            )
        elif kind is RecordKind.COST:
            cost_kind, instrument_id, amount = payload
            built = CostAccrued(
                ts=ts,
                kind=CostKind(cost_kind),
                instrument=None if instrument_id is None else self._instruments[instrument_id],
                amount=amount,
            )
        else:
            raise TypeError(f"unsupported persistent record kind — kind={kind!r}")
        self._materialized[seq] = built
        return built

    @property
    def records(self) -> tuple[Record, ...]:
        if self._records_cache is None:
            self._records_cache = tuple(
                Record(
                    seq=seq,
                    ts=self._sessions[session_index],
                    kind=_RECORD_KINDS[kind_code],
                    payload=self._materialize(seq, session_index, kind_code, payload),
                )
                for seq, session_index, kind_code, payload in self._batch()
            )
        return self._records_cache

    def _payloads(self, kind: RecordKind) -> tuple[RecordPayload, ...]:
        kind_code = _RECORD_KIND_CODES[kind]
        return tuple(
            self._materialize(seq, session_index, code, payload)
            for seq, session_index, code, payload in self._batch()
            if code == kind_code
        )

    def compact_trace(self) -> tuple[tuple[int, int, str, int], ...]:
        """디버그용 원시 Rust 레코드 인덱스 `(seq, session_index, kind, seq)`."""
        return tuple(
            (seq, session_index, _RECORD_KINDS[kind_code].value, seq)
            for seq, session_index, kind_code, _payload in self._batch()
        )

    def equity_values(self) -> tuple[float, ...]:
        """SNAPSHOT 레코드 순서의 equity — Event 객체 없이 metrics를 계산한다."""
        return tuple(self._runtime.equity_series())

    def traded_notional(self) -> float:
        """FILL 레코드 순서로 누산한 체결 금액 (Rust가 같은 결합 순서로 계산)."""
        return float(self._runtime.traded_notional())
