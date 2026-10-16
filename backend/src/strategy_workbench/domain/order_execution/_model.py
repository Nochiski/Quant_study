"""오프라인 정수 주식·KRW 주문과 누적 브로커 사실의 계약."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAX_VALUE = 10**15


def integer(value: int, *, field: str, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= MAX_VALUE:
        raise ValueError(f"invalid offline integer — field={field} value={value!r} min={minimum}")


class OrderState(StrEnum):
    READY = "ready"
    UNKNOWN = "unknown"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        return self in (self.FILLED, self.CANCELLED, self.REJECTED)


@dataclass(frozen=True)
class Intent:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    limit_price_krw: int
    fee_budget_krw: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.intent_id, str)
            or not isinstance(self.symbol, str)
            or not self.intent_id.strip()
            or not self.symbol.strip()
            or self.side not in ("buy", "sell")
        ):
            raise ValueError(
                f"invalid intent identity — intent_id={self.intent_id!r} "
                f"symbol={self.symbol!r} side={self.side!r}"
            )
        integer(self.quantity, field="quantity", minimum=1)
        integer(self.limit_price_krw, field="limit_price_krw", minimum=1)
        integer(self.fee_budget_krw, field="fee_budget_krw")
        integer(self.quantity * self.limit_price_krw + self.fee_budget_krw, field="order_value")


@dataclass(frozen=True)
class OrderFact:
    intent: Intent
    state: OrderState
    revision: int = 0
    filled_quantity: int = 0
    filled_value_krw: int = 0
    fees_krw: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.state, OrderState):
            raise ValueError(f"invalid order state — state={self.state!r}")
        if (self.state in (OrderState.READY, OrderState.UNKNOWN)) != (self.revision == 0):
            raise ValueError(
                f"invalid order revision — state={self.state} revision={self.revision}"
            )
        for field in ("revision", "filled_quantity", "filled_value_krw", "fees_krw"):
            integer(getattr(self, field), field=field)
        if self.filled_quantity > self.intent.quantity:
            raise ValueError(
                f"overfill — intent_id={self.intent.intent_id} filled={self.filled_quantity}"
            )
        if self.fees_krw > self.filled_value_krw:
            raise ValueError(f"offline fee exceeds gross fill — intent_id={self.intent.intent_id}")
        if self.fees_krw > self.intent.fee_budget_krw:
            raise ValueError(
                f"fee budget exceeded — intent_id={self.intent.intent_id} fees={self.fees_krw}"
            )
        if (
            self.filled_value_krw > self.filled_quantity * self.intent.limit_price_krw
            and self.intent.side == "buy"
        ):
            raise ValueError(f"buy limit exceeded — intent_id={self.intent.intent_id}")
        if (
            self.filled_value_krw < self.filled_quantity * self.intent.limit_price_krw
            and self.intent.side == "sell"
        ):
            raise ValueError(f"sell limit exceeded — intent_id={self.intent.intent_id}")
        if bool(self.filled_quantity) != bool(self.filled_value_krw):
            raise ValueError(f"fill value/quantity mismatch — intent_id={self.intent.intent_id}")
        if self.state == OrderState.FILLED and self.filled_quantity != self.intent.quantity:
            raise ValueError(f"incomplete FILLED fact — intent_id={self.intent.intent_id}")
        if self.state == OrderState.PARTIAL and not 0 < self.filled_quantity < self.intent.quantity:
            raise ValueError(f"invalid PARTIAL fact — intent_id={self.intent.intent_id}")
        if (
            self.state
            in (OrderState.READY, OrderState.UNKNOWN, OrderState.OPEN, OrderState.REJECTED)
            and self.filled_quantity
        ):
            raise ValueError(
                f"unexpected fill for state — intent_id={self.intent.intent_id} state={self.state}"
            )

    @property
    def remaining_quantity(self) -> int:
        return 0 if self.state.terminal else self.intent.quantity - self.filled_quantity

    @property
    def reserved_cash_krw(self) -> int:
        if self.intent.side != "buy" or self.state.terminal:
            return 0
        return (
            self.remaining_quantity * self.intent.limit_price_krw
            + self.intent.fee_budget_krw
            - self.fees_krw
        )

    @property
    def cash_change_krw(self) -> int:
        value = self.filled_value_krw
        return (-value if self.intent.side == "buy" else value) - self.fees_krw


@dataclass(frozen=True)
class Account:
    cash_krw: int
    holdings: tuple[tuple[str, int], ...] = ()
    reserved_cash_krw: int = 0

    def __post_init__(self) -> None:
        integer(self.cash_krw, field="cash_krw")
        integer(self.reserved_cash_krw, field="reserved_cash_krw")
        seen: set[str] = set()
        for symbol, quantity in self.holdings:
            integer(quantity, field=f"holdings[{symbol}]")
            if not isinstance(symbol, str) or not symbol.strip() or symbol in seen:
                raise ValueError(f"invalid holding symbol — symbol={symbol!r}")
            seen.add(symbol)
        if self.reserved_cash_krw > self.cash_krw:
            raise ValueError(
                f"reserve exceeds cash — cash={self.cash_krw} reserve={self.reserved_cash_krw}"
            )


@dataclass(frozen=True)
class Snapshot:
    account: Account
    orders: tuple[OrderFact, ...]

    def __post_init__(self) -> None:
        for fact in self.orders:
            if fact.state in (OrderState.READY, OrderState.UNKNOWN):
                raise ValueError(
                    f"broker snapshot contains local state — intent_id={fact.intent.intent_id} "
                    f"state={fact.state}"
                )


@dataclass(frozen=True)
class Reconciliation:
    matched: bool
    differences: tuple[str, ...]


def reconcile(
    baseline: Account, local: tuple[OrderFact, ...], snapshot: Snapshot
) -> Reconciliation:
    """기준 잔고에는 이후 누적 체결을 한 번만 더해 브로커 현재 잔고와 대조한다."""
    cash = baseline.cash_krw
    holdings = dict(baseline.holdings)
    reserve = 0
    differences: list[str] = []
    local_by_id = {fact.intent.intent_id: fact for fact in local}
    broker_by_id = {fact.intent.intent_id: fact for fact in snapshot.orders}
    if len(broker_by_id) != len(snapshot.orders):
        differences.append("duplicate broker intent identity")
    for fact in local:
        if fact.state == OrderState.READY:
            continue
        broker_fact = broker_by_id.get(fact.intent.intent_id)
        if broker_fact != fact:
            differences.append(f"order mismatch intent_id={fact.intent.intent_id}")
        cash += fact.cash_change_krw
        signed = fact.filled_quantity * (1 if fact.intent.side == "buy" else -1)
        holdings[fact.intent.symbol] = holdings.get(fact.intent.symbol, 0) + signed
        reserve += fact.reserved_cash_krw
    for identity in broker_by_id.keys() - local_by_id.keys():
        differences.append(f"untracked broker order intent_id={identity}")
    actual_holdings = {k: v for k, v in snapshot.account.holdings if v}
    if {k: v for k, v in holdings.items() if v} != actual_holdings:
        differences.append(f"holdings mismatch expected={holdings} actual={actual_holdings}")
    if cash != snapshot.account.cash_krw:
        differences.append(f"cash mismatch expected={cash} actual={snapshot.account.cash_krw}")
    if reserve != snapshot.account.reserved_cash_krw:
        differences.append(
            f"reserve mismatch expected={reserve} actual={snapshot.account.reserved_cash_krw}"
        )
    return Reconciliation(not differences, tuple(differences))


def target_deltas(
    target: dict[str, int], account: Account, orders: tuple[OrderFact, ...]
) -> dict[str, int]:
    """이미 채운 목표 주식 수와 미체결을 함께 차감한다. 목표 비중 buffer는 소비하지 않는다."""
    projected = dict(account.holdings)
    for fact in orders:
        if fact.state != OrderState.READY:
            projected[fact.intent.symbol] = projected.get(fact.intent.symbol, 0) + (
                fact.remaining_quantity * (1 if fact.intent.side == "buy" else -1)
            )
    for symbol, quantity in target.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"invalid target symbol — symbol={symbol!r}")
        integer(quantity, field=f"target[{symbol}]")
    return {
        symbol: target.get(symbol, 0) - projected.get(symbol, 0)
        for symbol in sorted(target.keys() | projected.keys())
        if target.get(symbol, 0) != projected.get(symbol, 0)
    }


class EventDisposition(StrEnum):
    APPLY = "apply"
    IGNORE = "ignore"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class EventDecision:
    disposition: EventDisposition
    detail: str


def classify_event(previous: OrderFact, incoming: OrderFact) -> EventDecision:
    """누적 revision 사실은 전진만 한다. 정정·재개 이벤트는 이 범위에서 허용하지 않는다."""
    identity = incoming.intent.intent_id
    if previous.intent != incoming.intent:
        return EventDecision(
            EventDisposition.CONFLICT, f"event payload conflict intent_id={identity}"
        )
    if incoming == previous or incoming.revision < previous.revision:
        return EventDecision(EventDisposition.IGNORE, f"duplicate/stale event intent_id={identity}")
    if incoming.revision == previous.revision:
        return EventDecision(
            EventDisposition.CONFLICT,
            f"conflicting same-revision event intent_id={identity} revision={incoming.revision}",
        )
    if previous.state == OrderState.READY or incoming.state in (
        OrderState.READY,
        OrderState.UNKNOWN,
    ):
        return EventDecision(
            EventDisposition.CONFLICT,
            f"event without valid claim/state intent_id={identity} state={incoming.state}",
        )
    quantity_delta = incoming.filled_quantity - previous.filled_quantity
    value_delta = incoming.filled_value_krw - previous.filled_value_krw
    fee_delta = incoming.fees_krw - previous.fees_krw
    if (
        previous.state.terminal
        or min(quantity_delta, value_delta, fee_delta) < 0
        or (quantity_delta == 0 and (value_delta or fee_delta))
        or (quantity_delta > 0 and value_delta == 0)
    ):
        return EventDecision(
            EventDisposition.CONFLICT,
            f"non-monotonic order event intent_id={identity} revision={incoming.revision}",
        )
    limit_value = quantity_delta * incoming.intent.limit_price_krw
    if (
        incoming.intent.side == "buy"
        and value_delta > limit_value
        or incoming.intent.side == "sell"
        and value_delta < limit_value
    ):
        return EventDecision(
            EventDisposition.CONFLICT, f"incremental fill violates limit intent_id={identity}"
        )
    return EventDecision(EventDisposition.APPLY, f"event applied intent_id={identity}")
