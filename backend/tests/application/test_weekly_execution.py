"""주간 리밸런싱 실행기(S07). 게이트웨이·호가·기록은 메모리 대역이고 기다림은 0초다.

주문안(신호일 10-23, 자리 3·완충 5): D 100주 유지, E 100주 매도, A·B 각 130주 매수.
기준가는 모두 10,000원이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    Approval,
    CloseStatus,
    DayRecord,
    ExecuteStatus,
    ExecutionEvent,
    GatewayState,
    MarketError,
    OrderRequest,
    OrderView,
    Phase,
    PlanRecord,
    PriceInfo,
    SignalRecord,
    SubmitOutcome,
    SubmitStatus,
    Waits,
    WeeklyExecutionService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    ExecutionPolicy,
    Quote,
    RebalancePolicy,
    plan_rebalance,
)
from tests.application.test_weekly_rebalance import Plans

KST = timezone(timedelta(hours=9))
MONDAY = datetime(2026, 10, 26, 9, 10, tzinfo=KST)
PLAN = "weekly-2026-10-23"
BOOK = Quote("", asks=(10_010, 10_020, 10_030), bids=(10_000, 9_990, 9_980))


def plan_record() -> PlanRecord:
    plan = plan_rebalance(
        RebalancePolicy(slots=3, keep_rank=5),
        ranks={"00000A": 1, "00000B": 2, "00000C": 3, "00000D": 4, "00000E": 7},
        holdings={"00000D": 100, "00000E": 100},
        prices=dict.fromkeys(("00000A", "00000B", "00000C", "00000D", "00000E"), 10_000),
        cash_krw=2_000_000,
        blocked={},
    )
    return PlanRecord(
        PLAN,
        datetime(2026, 10, 23, 16, 10, tzinfo=KST),
        SignalRecord("scope@1.0", "b", "2026-10-23", "ab" * 32, "p", ()),
        AccountState(2_000_000, {"00000D": 100, "00000E": 100}, True),
        plan,
    )


@dataclass
class Gateway:
    cash: int = 2_000_000
    holdings: dict[str, int] = field(default_factory=lambda: {"00000D": 100, "00000E": 100})
    orders: dict[str, OrderView] = field(default_factory=dict)
    ready: bool = False
    matched: bool = True
    retry_first: set[str] = field(default_factory=set)
    submitted: list[OrderRequest] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def initialized(self) -> bool:
        return self.ready

    def initialize(self) -> None:
        self.ready = True

    def state(self) -> GatewayState:
        reserved = sum(
            o.open_quantity * o.limit_price_krw for o in self.orders.values() if o.side == "buy"
        )
        return GatewayState(
            self.matched, "", self.cash, reserved, dict(self.holdings), tuple(self.orders.values())
        )

    def submit(self, request: OrderRequest) -> SubmitOutcome:
        if request.intent_id in self.retry_first:
            self.retry_first.discard(request.intent_id)
            return SubmitOutcome(SubmitStatus.RETRY, "orders kept changing")
        if request.intent_id in self.orders:
            return SubmitOutcome(SubmitStatus.EXISTS)
        self.submitted.append(request)
        view = OrderView(
            request.intent_id,
            request.symbol,
            request.side,
            request.quantity,
            request.limit_price_krw,
            "open",
            False,
            0,
            0,
            0,
        )
        self.orders[request.intent_id] = view
        if request.side == "sell":  # 매도는 지정가에 바로 전량 체결된다고 둔다
            self.fill(request.intent_id, request.quantity, request.limit_price_krw)
        return SubmitOutcome(SubmitStatus.SENT)

    def fill(self, intent_id: str, quantity: int, price: int) -> None:
        order = self.orders[intent_id]
        filled = order.filled_quantity + quantity
        sign = 1 if order.side == "buy" else -1
        self.holdings[order.symbol] = self.holdings.get(order.symbol, 0) + sign * quantity
        self.cash -= sign * quantity * price
        done = filled == order.quantity
        self.orders[intent_id] = replace(
            order,
            state="filled" if done else "partial",
            terminal=done,
            filled_quantity=filled,
            filled_value_krw=order.filled_value_krw + quantity * price,
        )

    def cancel(self, intent_id: str) -> SubmitOutcome:
        self.cancelled.append(intent_id)
        self.orders[intent_id] = replace(self.orders[intent_id], state="cancelled", terminal=True)
        return SubmitOutcome(SubmitStatus.SENT)

    def evidence(self) -> Mapping[str, object]:
        return {"kt00007": [order.intent_id for order in self.orders.values()]}


@dataclass
class Market:
    quotes: dict[str, Quote] = field(default_factory=dict)
    closes: dict[str, int] = field(default_factory=dict)

    def quote(self, symbol: str) -> Quote:
        if symbol not in self.quotes:
            return replace(BOOK, symbol=symbol)
        return self.quotes[symbol]

    def price(self, symbol: str) -> PriceInfo:
        if symbol not in self.closes:
            raise MarketError(f"no price — {symbol}")
        return PriceInfo(symbol, f"이름{symbol[-1]}", self.closes[symbol])


@dataclass
class Log:
    events_: list[ExecutionEvent] = field(default_factory=list)
    days_: dict[date, DayRecord] = field(default_factory=dict)
    raws: dict[date, Mapping[str, object]] = field(default_factory=dict)

    def append(self, event: ExecutionEvent) -> None:
        self.events_.append(event)

    def events(self) -> tuple[ExecutionEvent, ...]:
        return tuple(self.events_)

    def save_raw(self, day: date, payload: Mapping[str, object]) -> None:
        self.raws[day] = payload

    def save_day(self, record: DayRecord) -> None:
        self.days_[record.day] = record

    def day(self, day: date) -> DayRecord | None:
        return self.days_.get(day)

    def days(self) -> tuple[DayRecord, ...]:
        return tuple(self.days_[day] for day in sorted(self.days_))


@dataclass
class Rig:
    plans: Plans
    gateway: Gateway
    market: Market
    log: Log
    service: WeeklyExecutionService


def rig(
    *, approved: bool = True, now: datetime = MONDAY, policy: ExecutionPolicy | None = None
) -> Rig:
    plans = Plans()
    plans.save(plan_record())
    if approved:
        plans.save_approval(PLAN, Approval(now, "동민"))
    gateway, market, log = Gateway(), Market(), Log()
    service = WeeklyExecutionService(
        plans,
        gateway,
        market,
        log,
        now=lambda: now,
        sleep=lambda _: None,
        policy=policy,
        sizing=RebalancePolicy(slots=3, keep_rank=5),
        waits=Waits(0, 8, 0, 0, 3),
    )
    return Rig(plans, gateway, market, log, service)


def _sent(r: Rig) -> list[tuple[str, str, int, int]]:
    return [(o.intent_id, o.side, o.quantity, o.limit_price_krw) for o in r.gateway.submitted]


def test_preview_sends_nothing_and_orders_sells_before_buys() -> None:
    r = rig()
    result = r.service.execute(PLAN, Phase.OPEN, send=False)
    assert result.status is ExecuteStatus.PREVIEW
    assert [
        (line.symbol, line.side, line.quantity, line.limit_price_krw) for line in result.lines
    ] == [
        ("00000E", "sell", 100, 9_990),
        ("00000A", "buy", 130, 10_020),
        ("00000B", "buy", 130, 10_020),
    ]
    assert r.gateway.submitted == [] and not r.gateway.ready


def test_open_initializes_the_ledger_sells_first_and_buys_with_freed_cash() -> None:
    r = rig()
    r.gateway.cash = 1_000_000
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert result.status is ExecuteStatus.OK and r.gateway.ready
    # 매도 999,000원이 들어온 뒤 1,999,000원으로 산다. A 130주(1,302,600 + 비용 상한 5,211원) 뒤
    # 남은 691,189원 ÷ (10,020 × 1.0035) = 68.7 → B 68주
    assert _sent(r) == [
        (f"{PLAN}-00000E-sell", "sell", 100, 9_990),
        (f"{PLAN}-00000A-buy", "buy", 130, 10_020),
        (f"{PLAN}-00000B-buy", "buy", 68, 10_020),
    ]
    assert r.gateway.submitted[1].fee_budget_krw == 5_211
    assert r.gateway.submitted[0].fee_budget_krw == 6_993  # 매도는 거래세까지 0.7%
    assert [e.kind for e in r.log.events_][0] == "initialized"


def test_rerunning_open_does_not_send_twice() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    count = len(r.gateway.submitted)
    again = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert again.status is ExecuteStatus.OK and len(r.gateway.submitted) == count
    assert {line.outcome for line in again.lines} <= {"exists"}


def test_unsettled_presend_read_is_retried_with_the_same_intent() -> None:
    r = rig()
    r.gateway.retry_first = {f"{PLAN}-00000A-buy"}
    r.service.execute(PLAN, Phase.OPEN, send=True)
    assert [o.intent_id for o in r.gateway.submitted].count(f"{PLAN}-00000A-buy") == 1


def test_wide_spread_or_missing_book_defers_the_symbol() -> None:
    r = rig()
    r.market.quotes["00000A"] = Quote("00000A", (10_070, 10_080), (10_000, 9_990))  # 7틱
    r.market.quotes["00000B"] = Quote("00000B", (), ())
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    outcomes = {line.symbol: line.outcome for line in result.lines}
    assert outcomes == {"00000E": "sent", "00000A": "deferred", "00000B": "deferred"}


def test_refusals_before_any_order() -> None:
    assert rig(approved=False).service.execute(PLAN, Phase.OPEN, send=True).status is (
        ExecuteStatus.NOT_APPROVED
    )
    friday = datetime(2026, 10, 23, 16, 30, tzinfo=KST)
    assert (
        rig(now=friday).service.execute(PLAN, Phase.OPEN, send=True).status is ExecuteStatus.STALE
    )
    late = datetime(2026, 11, 2, 9, 10, tzinfo=KST)
    assert rig(now=late).service.execute(PLAN, Phase.OPEN, send=True).status is ExecuteStatus.STALE
    halted = rig()
    halted.gateway.matched = False
    assert halted.service.execute(PLAN, Phase.OPEN, send=True).status is ExecuteStatus.HALTED
    assert halted.gateway.submitted == []
    assert rig().service.execute("weekly-2026-10-30", Phase.OPEN, send=True).status is (
        ExecuteStatus.NOT_FOUND
    )


def test_order_budget_caps_the_run() -> None:
    r = rig(policy=ExecutionPolicy(max_orders_per_day=2))
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert len(r.gateway.submitted) == 2
    assert [line.outcome for line in result.lines][-1] == "limit"


def test_requote_cancels_the_remainder_then_sends_it_once_more() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.fill(f"{PLAN}-00000A-buy", 50, 10_020)  # A 50주만 체결, B는 체결 없음
    r.market.quotes["00000A"] = Quote("00000A", (10_100, 10_110), (10_090, 10_080))
    result = r.service.execute(PLAN, Phase.REQUOTE, send=True)
    assert r.gateway.cancelled == [f"{PLAN}-00000A-buy", f"{PLAN}-00000B-buy"]
    assert _sent(r)[-2:] == [
        (f"{PLAN}-00000A-buy-r", "buy", 80, 10_110),
        (f"{PLAN}-00000B-buy-r", "buy", 130, 10_020),
    ]
    assert [line.outcome for line in result.lines][:2] == ["cancelled", "cancelled"]
    again = r.service.execute(PLAN, Phase.REQUOTE, send=True)
    assert len(r.gateway.submitted) == 5 and {line.outcome for line in again.lines} <= {
        "exists",
        "waiting",
    }


def test_close_records_shortfall_missed_cost_and_raw_evidence() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.fill(f"{PLAN}-00000A-buy", 50, 10_020)
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    closed = r.service.close()  # 가장 최근 주문안
    assert closed.status is CloseStatus.OK and closed.record is not None
    record = closed.record
    orders = {(o.symbol, o.side): o for o in record.orders}
    a = orders[("00000A", "buy")]
    # 평균 10,020원 / 기준가 10,000원 → +20bp. 못 산 80주가 10,100원으로 올라 기회비용 8,000원
    assert (a.quantity, a.filled_quantity, a.shortfall_bp, a.missed_krw) == (130, 50, 20, 8_000)
    e = orders[("00000E", "sell")]
    assert (e.filled_quantity, e.shortfall_bp, e.missed_krw) == (100, 10, 0)
    assert orders[("00000B", "buy")].shortfall_bp is None
    assert record.equity_krw == r.gateway.cash + 50 * 10_100 + 100 * 10_050
    assert r.log.raws[MONDAY.date()]["kt00007"]
    assert r.service.days() == (record,)
