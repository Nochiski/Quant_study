"""주간 리밸런싱 실행기(S07). 남긴 주문안을 키움 모의계좌에 낸다.

- 주문안이 있어야 하고, 첫 4주는 승인이 있어야 한다. 신호일 뒤 며칠 안에만 낸다.
- 매도를 먼저 내고 체결을 기다린 뒤, 그때 현금으로 살 수 있는 만큼 매수한다.
- 한 종목에는 주문을 하나만 둔다. 의도 ID는 `<주문안>-<종목>-<방향>`(다시 낸 주문은 `-r`)이라 다시
  돌려도 같은 주문을 두 번 보내지 않는다.
- 호가가 없거나 1호가 사이가 넓으면 그 종목은 미루고, 14:00 재주문 단계에서 다시 본다.
- 재주문 단계는 남은 잔량을 취소하고, 취소가 확정된 것을 본 뒤 지금 호가로 한 번 더 낸다.
- 장 마감 기록은 신호 기준가 대비 체결 차이와 미체결 기회비용, 그날 총자산을 남긴다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance.ports.outgoing.market import (
    MarketError,
    MarketSource,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.orders import (
    GatewayError,
    GatewayState,
    OrderGateway,
    OrderRequest,
    SubmitStatus,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.plans import (
    PlanRecord,
    PlanStore,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.records import (
    DayOrder,
    DayRecord,
    ExecutionEvent,
    ExecutionLog,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    ExecutionPolicy,
    RebalancePolicy,
    affordable_shares,
    limit_price,
    missed_krw,
    shortfall_bp,
    spread_ticks,
)


class Phase(StrEnum):
    OPEN = "open"
    REQUOTE = "requote"


class ExecuteStatus(StrEnum):
    OK = "ok"
    PREVIEW = "preview"
    NOT_FOUND = "not_found"
    NOT_APPROVED = "not_approved"
    STALE = "stale"
    HALTED = "halted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class OrderLine:
    symbol: str
    side: str
    quantity: int
    limit_price_krw: int
    intent_id: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class ExecuteResult:
    status: ExecuteStatus
    lines: tuple[OrderLine, ...] = ()
    detail: str = ""


class CloseStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CloseResult:
    status: CloseStatus
    record: DayRecord | None = None
    detail: str = ""


@dataclass(frozen=True)
class Waits:
    """실제 시간 간격. 테스트는 모두 0으로 둔다."""

    retry_seconds: float = 15.0
    retry_attempts: int = 8
    poll_seconds: float = 20.0
    sell_wait_seconds: float = 600.0
    cancel_polls: int = 12


class WeeklyExecutionService:
    def __init__(
        self,
        plans: PlanStore,
        gateway: OrderGateway,
        market: MarketSource,
        log: ExecutionLog,
        *,
        now: Callable[[], datetime],
        sleep: Callable[[float], None],
        policy: ExecutionPolicy | None = None,
        sizing: RebalancePolicy | None = None,
        require_approval: bool = True,
        max_stale_days: int = 5,
        waits: Waits | None = None,
    ) -> None:
        self._plans = plans
        self._gateway = gateway
        self._market = market
        self._log = log
        self._now = now
        self._sleep = sleep
        self._policy = policy or ExecutionPolicy()
        self._sizing = sizing or RebalancePolicy()
        self._require_approval = require_approval
        self._max_stale_days = max_stale_days
        self._waits = waits or Waits()

    # --- 주문 ---------------------------------------------------------------------------------

    def execute(self, plan_id: str, phase: Phase, *, send: bool) -> ExecuteResult:
        record = self._plans.load(plan_id)
        if record is None:
            return ExecuteResult(ExecuteStatus.NOT_FOUND, detail=f"no plan — plan_id={plan_id}")
        if self._require_approval and self._plans.approval(plan_id) is None:
            return ExecuteResult(ExecuteStatus.NOT_APPROVED, detail=f"plan_id={plan_id}")
        age = (self._now().date() - date.fromisoformat(record.signal.signal_date)).days
        if not 0 < age <= self._max_stale_days:
            return ExecuteResult(
                ExecuteStatus.STALE,
                detail=f"plan is not for this week — plan_id={plan_id} age_days={age} "
                f"max={self._max_stale_days}",
            )
        try:
            if send and not self._gateway.initialized():
                self._gateway.initialize()
                self._event(plan_id, phase, "initialized", detail="기준 잔고를 잡았다")
            state = self._gateway.state()
            if not state.matched:
                self._event(plan_id, phase, "halted", detail=state.detail)
                return ExecuteResult(ExecuteStatus.HALTED, detail=state.detail)
            lines: list[OrderLine] = []
            if phase is Phase.REQUOTE:
                lines += self._cancel_open(record, state, send=send)
                state = self._gateway.state()
            lines += self._place(record, phase, state, send=send)
        except GatewayError as error:
            self._event(plan_id, phase, "error", detail=str(error))
            return ExecuteResult(ExecuteStatus.UNAVAILABLE, tuple(), str(error))
        return ExecuteResult(ExecuteStatus.OK if send else ExecuteStatus.PREVIEW, tuple(lines))

    def _cancel_open(
        self, record: PlanRecord, state: GatewayState, *, send: bool
    ) -> list[OrderLine]:
        lines: list[OrderLine] = []
        prefix = f"{record.plan_id}-"
        for order in state.orders:
            # 재주문 단계가 낸 주문(`-r`)은 다시 취소하지 않는다. 다시 돌려도 한 번만 다시 낸다.
            if (
                not order.intent_id.startswith(prefix)
                or order.intent_id.endswith("-r")
                or order.open_quantity <= 0
            ):
                continue
            line = OrderLine(
                order.symbol,
                order.side,
                order.open_quantity,
                order.limit_price_krw,
                order.intent_id,
                "cancel",
            )
            if not send:
                lines.append(line)
                continue
            outcome = self._gateway.cancel(order.intent_id)
            confirmed = self._await_terminal(order.intent_id)
            self._event(
                record.plan_id,
                Phase.REQUOTE,
                "cancel",
                symbol=order.symbol,
                intent_id=order.intent_id,
                side=order.side,
                quantity=order.open_quantity,
                status=outcome.status.value,
                detail=f"{outcome.detail} confirmed={confirmed}",
            )
            lines.append(
                OrderLine(
                    line.symbol,
                    line.side,
                    line.quantity,
                    line.limit_price_krw,
                    line.intent_id,
                    "cancelled" if confirmed else "cancel_unconfirmed",
                    outcome.detail,
                )
            )
        return lines

    def _await_terminal(self, intent_id: str) -> bool:
        for _ in range(self._waits.cancel_polls):
            state = self._gateway.state()
            order = next((o for o in state.orders if o.intent_id == intent_id), None)
            if order is not None and order.terminal:
                return True
            self._sleep(self._waits.poll_seconds)
        return False

    def _place(
        self, record: PlanRecord, phase: Phase, state: GatewayState, *, send: bool
    ) -> list[OrderLine]:
        suffix = "" if phase is Phase.OPEN else "-r"
        existing = {order.intent_id for order in state.orders}
        # 다시 낼 주문이 남은 종목만 낸다. 앞 단계 주문이 아직 살아 있으면 건드리지 않는다.
        blocked = {
            (order.symbol, order.side)
            for order in state.orders
            if order.intent_id.startswith(f"{record.plan_id}-") and not order.terminal
        }
        deltas = _deltas(record, state)
        ranks = {line.symbol: line.rank or 0 for line in record.plan.lines}
        budget = [
            self._policy.max_orders_per_day
            - sum(order.intent_id.startswith(f"{record.plan_id}-") for order in state.orders)
        ]
        lines: list[OrderLine] = []
        sells = [(symbol, -delta) for symbol, delta in deltas.items() if delta < 0]
        for symbol, quantity in sorted(sells):
            quantity = min(quantity, state.holdings.get(symbol, 0))
            lines.append(
                self._order(
                    record, phase, symbol, "sell", quantity, suffix, existing, blocked, budget, send
                )
            )
        if send and any(line.outcome == SubmitStatus.SENT.value for line in lines):
            self._await_sells(record.plan_id)
        state = self._gateway.state() if send else state
        available = state.cash_krw - state.reserved_cash_krw
        if not send:
            # 미리보기에서는 매도 대금이 아직 들어오지 않았다. 지정가 대금에서 비용 상한을
            # 뺀 값을 더해 본다.
            available += sum(
                line.quantity * line.limit_price_krw
                - self._policy.fee_budget("sell", line.quantity, line.limit_price_krw)
                for line in lines
                if line.outcome == "preview"
            )
        buys = [(symbol, delta) for symbol, delta in deltas.items() if delta > 0]
        for symbol, quantity in sorted(buys, key=lambda item: (ranks.get(item[0], 0), item[0])):
            line = self._order(
                record,
                phase,
                symbol,
                "buy",
                quantity,
                suffix,
                existing,
                blocked,
                budget,
                send,
                available=available,
            )
            if line.outcome in (SubmitStatus.SENT.value, "preview"):
                fee = self._policy.fee_budget("buy", line.quantity, line.limit_price_krw)
                available -= line.quantity * line.limit_price_krw + fee
            lines.append(line)
        return lines

    def _order(
        self,
        record: PlanRecord,
        phase: Phase,
        symbol: str,
        side: str,
        quantity: int,
        suffix: str,
        existing: set[str],
        blocked: set[tuple[str, str]],
        budget: list[int],
        send: bool,
        *,
        available: int | None = None,
    ) -> OrderLine:
        intent_id = f"{record.plan_id}-{symbol}-{side}{suffix}"
        reference = next(
            (line.reference_price_krw or 0 for line in record.plan.lines if line.symbol == symbol),
            0,
        )

        def skipped(outcome: str, detail: str, limit: int = 0) -> OrderLine:
            self._event(
                record.plan_id,
                phase,
                outcome,
                symbol=symbol,
                intent_id=intent_id,
                side=side,
                quantity=quantity,
                limit_price_krw=limit,
                reference_price_krw=reference,
                detail=detail,
            )
            return OrderLine(symbol, side, quantity, limit, intent_id, outcome, detail)

        if intent_id in existing:
            return OrderLine(symbol, side, quantity, 0, intent_id, SubmitStatus.EXISTS.value)
        if (symbol, side) in blocked:
            return skipped("waiting", "앞 단계 주문이 아직 살아 있다")
        if quantity <= 0:
            return skipped("skipped", "팔 보유 수량이 없다")
        if budget[0] <= 0:
            return skipped("limit", f"주문 수 상한 {self._policy.max_orders_per_day}건")
        try:
            quote = self._market.quote(symbol)
        except MarketError as error:
            return skipped("deferred", f"호가를 읽지 못했다 — {error}")
        spread = spread_ticks(quote)
        limit = limit_price(side, quote, self._policy.limit_level)
        if spread is None or limit is None:
            return skipped("deferred", "호가가 없다(거래정지·VI 가능)")
        if spread > self._policy.max_spread_ticks:
            return skipped("deferred", f"1호가 사이 {spread}틱 > {self._policy.max_spread_ticks}틱")
        if available is not None:
            quantity = min(quantity, affordable_shares(available, limit, self._sizing.fee_rate_bp))
            if quantity <= 0:
                return skipped("skipped", f"살 현금이 없다 — available={available}", limit)
        request = OrderRequest(
            intent_id,
            symbol,
            side,
            quantity,
            limit,
            self._policy.fee_budget(side, quantity, limit),
        )
        budget[0] -= 1
        if not send:
            return OrderLine(symbol, side, quantity, limit, intent_id, "preview")
        outcome = self._gateway.submit(request)
        for _ in range(self._waits.retry_attempts):
            if outcome.status is not SubmitStatus.RETRY:
                break
            self._sleep(self._waits.retry_seconds)
            outcome = self._gateway.submit(request)
        self._event(
            record.plan_id,
            phase,
            "submit",
            symbol=symbol,
            intent_id=intent_id,
            side=side,
            quantity=quantity,
            limit_price_krw=limit,
            reference_price_krw=reference,
            asks=quote.asks[:3],
            bids=quote.bids[:3],
            status=outcome.status.value,
            detail=outcome.detail,
        )
        return OrderLine(
            symbol, side, quantity, limit, intent_id, outcome.status.value, outcome.detail
        )

    def _await_sells(self, plan_id: str) -> None:
        waited = 0.0
        while waited < self._waits.sell_wait_seconds:
            state = self._gateway.state()
            if all(
                order.terminal
                for order in state.orders
                if order.intent_id.startswith(f"{plan_id}-") and order.side == "sell"
            ):
                return
            self._sleep(self._waits.poll_seconds)
            waited += max(self._waits.poll_seconds, 1.0)

    # --- 장 마감 기록 ---------------------------------------------------------------------------

    def close(self, plan_id: str | None = None) -> CloseResult:
        """장 마감 뒤 대사하고 하루 기록을 남긴다. 주문안을 주지 않으면 가장 최근 것을 쓴다."""
        plan_id = plan_id or next(reversed(self._plans.plan_ids()), None)
        record = None if plan_id is None else self._plans.load(plan_id)
        if plan_id is None or record is None:
            return CloseResult(CloseStatus.NOT_FOUND, detail=f"no plan — plan_id={plan_id}")
        try:
            state = self._gateway.state()
            plan_orders = [o for o in state.orders if o.intent_id.startswith(f"{plan_id}-")]
            planned_symbols = {line.symbol for line in record.plan.lines if line.delta_shares}
            symbols = sorted(
                set(state.holdings) | {o.symbol for o in plan_orders} | planned_symbols
            )
            prices = {symbol: self._market.price(symbol) for symbol in symbols}
            evidence = self._gateway.evidence()
        except (GatewayError, MarketError) as error:
            return CloseResult(CloseStatus.UNAVAILABLE, detail=str(error))
        holdings = tuple(
            (symbol, shares, prices[symbol].last_price_krw)
            for symbol, shares in sorted(state.holdings.items())
        )
        planned = {
            (line.symbol, "buy" if line.delta_shares > 0 else "sell"): abs(line.delta_shares)
            for line in record.plan.lines
            if line.delta_shares
        }
        references = {line.symbol: line.reference_price_krw or 0 for line in record.plan.lines}
        orders: list[DayOrder] = []
        for (symbol, side), quantity in sorted(planned.items()):
            group = [o for o in plan_orders if o.symbol == symbol and o.side == side]
            filled = sum(o.filled_quantity for o in group)
            value = sum(o.filled_value_krw for o in group)
            reference, close = references[symbol], prices[symbol].last_price_krw
            orders.append(
                DayOrder(
                    ",".join(o.intent_id for o in group),
                    symbol,
                    prices[symbol].name,
                    side,
                    quantity,
                    filled,
                    value,
                    sum(o.fees_krw for o in group),
                    group[-1].state if group else "not_sent",
                    reference,
                    close,
                    shortfall_bp(side, filled, value, reference) if filled and reference else None,
                    missed_krw(side, max(quantity - filled, 0), reference, close)
                    if reference and close
                    else 0,
                )
            )
        day_record = DayRecord(
            self._now().date(),
            self._now(),
            plan_id,
            state.matched,
            state.detail,
            state.cash_krw,
            holdings,
            state.cash_krw + sum(shares * price for _, shares, price in holdings),
            sum(o.filled_value_krw for o in plan_orders),
            tuple(orders),
        )
        self._log.save_raw(day_record.day, evidence)
        self._log.save_day(day_record)
        return CloseResult(CloseStatus.OK, day_record)

    def days(self) -> tuple[DayRecord, ...]:
        return self._log.days()

    def events(self) -> tuple[ExecutionEvent, ...]:
        return self._log.events()

    def _event(
        self,
        plan_id: str,
        phase: Phase,
        kind: str,
        *,
        symbol: str = "",
        intent_id: str = "",
        side: str = "",
        quantity: int = 0,
        limit_price_krw: int = 0,
        reference_price_krw: int = 0,
        asks: tuple[int, ...] = (),
        bids: tuple[int, ...] = (),
        status: str = "",
        detail: str = "",
    ) -> None:
        self._log.append(
            ExecutionEvent(
                self._now(),
                plan_id,
                phase.value,
                kind,
                symbol,
                intent_id,
                side,
                quantity,
                limit_price_krw,
                reference_price_krw,
                asks,
                bids,
                status,
                detail,
            )
        )


def _deltas(record: PlanRecord, state: GatewayState) -> dict[str, int]:
    """주문안 종목만 본다. 지금 보유와 살아 있는 주문을 목표에서 뺀다."""
    targets = record.plan.target_shares()
    projected: dict[str, int] = dict(state.holdings)
    for order in state.orders:
        sign = 1 if order.side == "buy" else -1
        projected[order.symbol] = projected.get(order.symbol, 0) + sign * order.open_quantity
    return {
        symbol: target - projected.get(symbol, 0)
        for symbol, target in targets.items()
        if target != projected.get(symbol, 0)
    }
