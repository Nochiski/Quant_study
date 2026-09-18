"""append-only 이벤트 저장소.

결과가 이상할 때 Run → Decision → Action → Order → Fill → Snapshot
순서로 원인을 역추적할 수 있도록 실행 중 발생한 모든 것을 순서대로 남긴다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from backtest_engine.engine.tape import no_bar_reason
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
    """`value`는 trace 직렬화 이름, `code`는 Rust `records.rs`의 `KIND_*`와 같은 wire 코드다.

    코드는 선언 순서가 아니라 리터럴이다 — 멤버를 추가해도 기존 코드가 밀리지 않는다.
    순서를 코드로 쓰면 enum 중간에 멤버를 끼워 넣는 순간 Rust가 보낸 코드가 다른 kind로
    해석돼 trace가 조용히 망가진다. Rust 상수와의 일치는 `tests/test_core_parity.py`가
    `backtest_core.RECORD_KIND_CODES`로 고정한다.

    `__new__`에서 `_value_`를 이름 문자열로 고정하므로 `.value`는 여전히 `"market"`이다.
    `(label, code)` 쌍을 그대로 `value`로 두면 `_normalized` 같은 generic enum 처리가 튜플을
    trace에 흘려 wire 포맷이 조용히 바뀐다.
    """

    code: int

    def __new__(cls, label: str, code: int) -> RecordKind:
        member = object.__new__(cls)
        member._value_ = label
        member.code = code
        return member

    MARKET = ("market", 0)
    DECISION = ("decision", 1)
    ORDER = ("order", 2)
    ORDER_UPDATE = ("order_update", 3)
    FILL = ("fill", 4)
    SNAPSHOT = ("snapshot", 5)
    CORPORATE_ACTION = ("corporate_action", 6)  # 사건 도착 (적용 여부와 무관)
    CORPORATE_ACTION_APPLIED = ("corporate_action_applied", 7)  # 포지션에 실제 적용된 기록
    COST = ("cost", 8)  # 차입·이자 등 Fill 없는 현금 차감


_RECORD_KIND_BY_CODE: dict[int, RecordKind] = {kind.code: kind for kind in RecordKind}

# Rust wire 문자열 → enum 멤버. `Side(value)` 호출은 값 하나마다 Enum의 __call__ → __new__
# 경로를 타는데 결과 조회는 주문·체결 수만큼 이 변환을 반복한다. 조회표로 고정해 dict 조회
# 한 번으로 끝낸다. 모르는 wire 값은 KeyError를 잡아 어느 레코드의 어떤 필드인지 알린다.
# `drain_payloads` 한 번에 넘겨받을 레코드 수. 이 청크만큼의 wire tuple이 Rust payload·공개
# 객체와 동시에 살아 있으므로, 조회당 FFI 왕복 수(레코드 수 / 청크)와 그 순간 메모리의
# 균형점이다. SNAPSHOT wire는 한 행이 보유 종목 수만큼 커서 청크를 크게 잡으면 그 자체가
# peak가 된다.
_DRAIN_CHUNK_RECORDS = 512

_SIDE_BY_WIRE: dict[str, Side] = {member.value: member for member in Side}
_ORDER_TYPE_BY_WIRE: dict[str, OrderType] = {member.value: member for member in OrderType}
_TIF_BY_WIRE: dict[str, TimeInForce] = {member.value: member for member in TimeInForce}
_ORDER_STATUS_BY_WIRE: dict[str, OrderStatus] = {member.value: member for member in OrderStatus}
_COST_KIND_BY_WIRE: dict[str, CostKind] = {member.value: member for member in CostKind}


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

    조회는 kind 단위다. 종료된 실행이면 `drain_payloads(kind, limit)`로 그 kind의 payload를 seq
    순서로 청크씩 넘겨받고 Rust는 넘긴 자리를 바로 해제한다. 종료 전 partial trace는 해제하지
    않는 `record_payloads(kind)`로 읽는다. 넘긴 payload는 다시 읽을 수 없으므로
    `equity_values()`/`traded_notional()`처럼 Rust 레코드를 직접 누산하는 조회는 결과 조회보다
    **먼저** 불러야 한다 (`loop.py`가 `finish()` 직후 metrics를 계산한다).
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
        self._finished_batch: list[tuple[int, int, int]] | None = None
        # 이미 배치로 받아 공개 객체로 바꾸고 Rust payload까지 해제한 kind의 seq 순서 payload.
        self._kind_payloads: dict[RecordKind, tuple[RecordPayload, ...]] = {}
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

    def _index_decisions(self) -> None:
        """decision_id → `(session_index, native)` 인덱스를 DECISION 배치 조회 한 번으로 만든다.

        결정마다 배치를 선형 스캔하면 주문 수 × 레코드 수로 커진다 — 배치 길이가 바뀔 때만
        다시 만든다. DECISION을 이미 공개 객체로 바꿨다면 `_decisions`가 실체를 갖고 있고
        Rust payload는 해제됐으므로 인덱스를 다시 읽지 않는다.
        """
        if RecordKind.DECISION in self._kind_payloads:
            return
        batch = self._batch()
        if self._decision_index_len == len(batch):
            return
        self._decision_index = {
            decision_id: (session_index, native)
            for _seq, session_index, (decision_id, native) in self._runtime.record_payloads(
                RecordKind.DECISION.code
            )
        }
        self._decision_index_len = len(batch)

    def _decision_by_id(self, decision_id: str) -> StrategyDecision:
        """ORDER/open order 복원용. tape 결정은 DECISION 레코드를 찾아 먼저 재구성한다."""
        registered = self._decisions.get(decision_id)
        if registered is not None:
            return registered
        self._index_decisions()
        found = self._decision_index.get(decision_id)
        if found is None:
            raise KeyError(
                f"order references a decision that is not in the record batch — "
                f"decision_id={decision_id} records={len(self._batch())} "
                f"indexed={len(self._decision_index)} registered={len(self._decisions)}"
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
        frame_session, kept_wires, no_bar, reason = native
        if no_bar:
            reason = no_bar_reason(reason, no_bar)
        if frame_session is None:
            built = StrategyDecision.no_action(ts, reason)
        else:
            frame = self._tape_frames[frame_session]
            kept: list[PositionTarget] = []
            for instrument_id, is_weight, weight, quantity in kept_wires:
                instrument = self._instruments[instrument_id]
                if is_weight:
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
        try:
            side_value = _SIDE_BY_WIRE[side]
            order_type_value = _ORDER_TYPE_BY_WIRE[order_type]
            tif_value = _TIF_BY_WIRE[time_in_force]
        except KeyError as unknown:
            raise ValueError(
                f"unknown order wire enum value — order_id={order_id} value={unknown.args[0]!r} "
                f"side={side!r} order_type={order_type!r} time_in_force={time_in_force!r}"
            ) from unknown
        return OrderEvent(
            order_id=order_id,
            decision_id=decision_id,
            ts=ts,
            instrument=self._instruments[instrument_id],
            quantity=Decimal(quantity),
            side=side_value,
            source_action=source_action,
            order_type=order_type_value,
            limit_price=None if limit_text is None else Decimal(limit_text),
            stop_price=None if stop_text is None else Decimal(stop_text),
            time_in_force=tif_value,
            group_id=group_id,
        )

    def fill_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> FillEvent:
        fill_id, order_id, instrument_id, quantity, side, price, fee, slippage_per_share = wire
        try:
            side_value = _SIDE_BY_WIRE[side]
        except KeyError as unknown:
            raise ValueError(
                f"unknown fill side wire value — fill_id={fill_id} order_id={order_id} "
                f"side={side!r} expected={sorted(_SIDE_BY_WIRE)}"
            ) from unknown
        return FillEvent(
            fill_id=fill_id,
            order_id=order_id,
            ts=ts,
            instrument=self._instruments[instrument_id],
            quantity=Decimal(quantity),
            side=side_value,
            price=price,
            fee=fee,
            slippage_per_share=slippage_per_share,
        )

    def order_update_from_wire(self, ts: datetime, wire: tuple[Any, ...]) -> OrderUpdateEvent:
        order_id, status, detail = wire
        try:
            status_value = _ORDER_STATUS_BY_WIRE[status]
        except KeyError as unknown:
            raise ValueError(
                f"unknown order status wire value — order_id={order_id} status={status!r} "
                f"expected={sorted(_ORDER_STATUS_BY_WIRE)}"
            ) from unknown
        return OrderUpdateEvent(ts=ts, order_id=order_id, status=status_value, detail=detail)

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

    def _batch(self) -> list[tuple[int, int, int]]:
        """레코드 인덱스 `(seq, session_index, kind_code)`. payload는 kind 배치로 따로 읽는다."""
        if self._finished_batch is not None:
            return self._finished_batch
        # 종료 전(전략 예외 등)에는 Rust가 지금까지 쌓은 partial trace를 그대로 읽는다.
        return self._runtime.record_batch()

    def _payloads(self, kind: RecordKind) -> tuple[RecordPayload, ...]:
        """kind 하나를 배치 FFI로 받아 공개 객체로 바꾸고 Rust payload를 그때그때 해제한다.

        kind마다 레코드 인덱스를 다시 훑고 payload를 한 건씩 읽던 경로를 대체한다. 종료된
        실행이면 `drain_payloads`가 청크 단위로 wire를 넘기면서 그 자리를 바로 해제하므로,
        Rust payload·wire tuple·공개 객체가 한꺼번에 사는 구간이 청크 크기로 묶인다.
        kind를 통째로 받으면 그 구간이 kind 전체가 돼 결과 조회 peak RSS가 셋의 합이 된다.

        종료 전(partial trace)에는 레코드가 더 쌓일 수 있어 해제하지 않고 캐시도 남기지
        않는다. 다음 조회가 그 시점까지의 레코드를 다시 읽는다.
        """
        cached = self._kind_payloads.get(kind)
        if cached is not None:
            return cached
        if kind is RecordKind.ORDER:
            # ORDER는 decision_id로 결정을 되살린다 — DECISION을 먼저 공개 객체로 만들어
            # `_decisions`를 채운다. 그래야 DECISION payload를 넘겨 해제해도 주문이 복원된다.
            self._payloads(RecordKind.DECISION)
        build = self._wire_builder(kind)
        if self._finished_batch is None:
            return tuple(
                build(session_index, payload)
                for _seq, session_index, payload in self._runtime.record_payloads(kind.code)
            )
        built: list[RecordPayload] = []
        while True:
            chunk = self._runtime.drain_payloads(kind.code, _DRAIN_CHUNK_RECORDS)
            built.extend(build(session_index, payload) for _seq, session_index, payload in chunk)
            # 청크가 덜 찼으면 그 kind는 끝이다 — 빈 청크를 받으러 한 번 더 왕복하지 않는다.
            if len(chunk) < _DRAIN_CHUNK_RECORDS:
                break
        frozen = tuple(built)
        self._kind_payloads[kind] = frozen
        return frozen

    def _wire_builder(self, kind: RecordKind) -> Callable[[int, Any], RecordPayload]:
        """kind마다 한 번만 고르는 wire → 공개 객체 변환기.

        레코드마다 kind를 다시 분기하면 조회 한 번에 레코드 수만큼 같은 판단을 반복한다.
        """
        sessions = self._sessions
        if kind is RecordKind.MARKET:
            # MARKET payload는 Rust에 값이 없다 — 세션 index가 그대로 feed snapshot을 가리킨다.
            snapshots = self._market_snapshots
            return lambda session_index, _payload: snapshots[session_index]
        if kind is RecordKind.DECISION:
            return lambda session_index, payload: DecisionRecord(
                payload[0], self._decision(sessions[session_index], payload[0], payload[1])
            )
        if kind is RecordKind.ORDER:
            return lambda session_index, payload: self.order_from_wire(
                sessions[session_index], payload
            )
        if kind is RecordKind.ORDER_UPDATE:
            return lambda session_index, payload: self.order_update_from_wire(
                sessions[session_index], payload
            )
        if kind is RecordKind.FILL:
            return lambda session_index, payload: self.fill_from_wire(
                sessions[session_index], payload
            )
        if kind is RecordKind.SNAPSHOT:
            return lambda session_index, payload: self.snapshot_from_wire(
                sessions[session_index], payload
            )
        if kind is RecordKind.CORPORATE_ACTION:
            actions = self._corporate_actions
            return lambda _session_index, payload: actions[payload]
        if kind is RecordKind.CORPORATE_ACTION_APPLIED:
            return self._corporate_action_applied_from_wire
        if kind is RecordKind.COST:
            return self._cost_from_wire
        raise TypeError(f"unsupported persistent record kind — kind={kind!r}")

    def _corporate_action_applied_from_wire(
        self, session_index: int, payload: Any
    ) -> CorporateActionApplied:
        index, old_quantity, new_quantity, old_average, new_average, cash_paid = payload
        action = self._corporate_actions[index]
        return CorporateActionApplied(
            ts=self._sessions[session_index],
            instrument=action.instrument,
            action=action,
            old_quantity=Decimal(old_quantity),
            new_quantity=Decimal(new_quantity),
            old_average_price=old_average,
            new_average_price=new_average,
            cash_paid=cash_paid,
        )

    def _cost_from_wire(self, session_index: int, payload: Any) -> CostAccrued:
        ts = self._sessions[session_index]
        cost_kind, instrument_id, amount = payload
        try:
            cost_kind_value = _COST_KIND_BY_WIRE[cost_kind]
        except KeyError as unknown:
            raise ValueError(
                f"unknown cost kind wire value — ts={ts} kind={cost_kind!r} "
                f"expected={sorted(_COST_KIND_BY_WIRE)}"
            ) from unknown
        return CostAccrued(
            ts=ts,
            kind=cost_kind_value,
            instrument=None if instrument_id is None else self._instruments[instrument_id],
            amount=amount,
        )

    @property
    def records(self) -> tuple[Record, ...]:
        if self._records_cache is None:
            batch = self._batch()
            # kind별 payload는 seq 순서라 인덱스를 훑으며 kind마다 커서를 하나씩 밀면
            # seq → payload 사전(레코드 수만큼 커진다)을 따로 두지 않아도 된다.
            by_kind = {kind: self._payloads(kind) for kind in RecordKind}
            cursors = dict.fromkeys(RecordKind, 0)
            sessions = self._sessions
            built: list[Record] = []
            for seq, session_index, kind_code in batch:
                kind = _RECORD_KIND_BY_CODE[kind_code]
                cursor = cursors[kind]
                cursors[kind] = cursor + 1
                built.append(
                    Record(
                        seq=seq,
                        ts=sessions[session_index],
                        kind=kind,
                        payload=by_kind[kind][cursor],
                    )
                )
            self._records_cache = tuple(built)
        return self._records_cache

    def compact_trace(self) -> tuple[tuple[int, int, str], ...]:
        """디버그용 원시 Rust 레코드 인덱스 `(seq, session_index, kind)`."""
        return tuple(
            (seq, session_index, _RECORD_KIND_BY_CODE[kind_code].value)
            for seq, session_index, kind_code in self._batch()
        )

    def equity_values(self) -> tuple[float, ...]:
        """SNAPSHOT 레코드 순서의 equity — Event 객체 없이 metrics를 계산한다."""
        return tuple(self._runtime.equity_series())

    def traded_notional(self) -> float:
        """FILL 레코드 순서로 누산한 체결 금액 (Rust가 같은 결합 순서로 계산)."""
        return float(self._runtime.traded_notional())
