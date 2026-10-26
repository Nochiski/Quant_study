"""주간 리밸런싱 실행기(S07). 게이트웨이·호가·기록은 메모리 대역이고 기다림은 0초다.

주문안(신호일 10-23, 자리 3·완충 5): D 100주 유지, E 100주 매도, A·B 각 130주 매수.
기준가는 모두 10,000원이다. 리스크 판정은 따로 정하지 않으면 주문안 목표 그대로다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    Approval,
    CloseStatus,
    ExecuteStatus,
    GatewayState,
    MarketError,
    OrderRequest,
    OrderView,
    Phase,
    PlanRecord,
    PriceInfo,
    RiskVerdict,
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
from strategy_workbench.domain.risk.facade.risk import (
    DrawdownState,
    RiskLevel,
    SecurityFlag,
    SignalFacts,
    TradeStatus,
    ViWindow,
    guard_targets,
)
from tests.application.test_weekly_rebalance import LIMITS, Log, Plans, Risk, close_record

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
        state = self.state()
        cost = request.quantity * request.limit_price_krw + request.fee_budget_krw
        if request.side == "buy" and cost > state.cash_krw - state.reserved_cash_krw:
            # S05 처럼 대금 + 비용 상한이 쓸 수 있는 현금을 넘으면 보내지 않는다
            return SubmitOutcome(SubmitStatus.BLOCKED, "insufficient cash")
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
    statuses: dict[str, TradeStatus] = field(default_factory=dict)
    unreadable: set[str] = field(default_factory=set)

    def trade_status(self, symbol: str) -> TradeStatus:
        if symbol in self.unreadable:
            raise MarketError(f"kiwoom ka10100 failed — symbol={symbol}")
        return self.statuses.get(symbol, TradeStatus(symbol, frozenset(), 13_000, 7_000))

    def quote(self, symbol: str) -> Quote:
        if symbol not in self.quotes:
            return replace(BOOK, symbol=symbol)
        return self.quotes[symbol]

    def price(self, symbol: str) -> PriceInfo:
        if symbol not in self.closes:
            raise MarketError(f"no price — {symbol}")
        return PriceInfo(symbol, f"이름{symbol[-1]}", self.closes[symbol])


def verdict_for(record: PlanRecord, level: RiskLevel = RiskLevel.NORMAL) -> RiskVerdict:
    """주문안 목표에 그 단계의 리스크 한도를 씌운 판정. 종목 자료는 넉넉하다."""
    guarded = guard_targets(
        targets=record.plan.target_shares(),
        holdings=record.account.holdings,
        frozen=(),
        prices={line.symbol: line.reference_price_krw or 0 for line in record.plan.lines},
        facts={
            line.symbol: SignalFacts(10_000_000_000, admin=False, halted=False)
            for line in record.plan.lines
        },
        equity_krw=record.plan.equity_krw,
        level=level,
        limits=LIMITS,
    )
    state = DrawdownState(level, 100_000_000, None, 0, None, None)
    return RiskVerdict(PLAN, record.created_at, 1, "ab" * 32, state, guarded)


@dataclass
class Rig:
    plans: Plans
    gateway: Gateway
    market: Market
    log: Log
    risk: Risk
    service: WeeklyExecutionService


def rig(
    *,
    approved: bool = True,
    now: datetime = MONDAY,
    policy: ExecutionPolicy | None = None,
    level: RiskLevel | None = RiskLevel.NORMAL,
) -> Rig:
    """`level`이 None이면 리스크 판정이 없는 주문안이다."""
    plans = Plans()
    record = plan_record()
    plans.save(record)
    if approved:
        plans.save_approval(PLAN, Approval(now, "동민"))
    gateway, market, log, risk = Gateway(), Market(), Log(), Risk()
    if level is not None:
        risk.save_verdict(verdict_for(record, level))
    service = WeeklyExecutionService(
        plans,
        gateway,
        market,
        log,
        risk=risk,
        now=lambda: now,
        sleep=lambda _: None,
        policy=policy,
        waits=Waits(0, 8, 0, 0, 3),
    )
    return Rig(plans, gateway, market, log, risk, service)


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


def test_events_from_a_preview_are_marked_as_preview() -> None:
    # 리뷰 #604 P3: 미리보기도 사건을 남기는데 표시가 없어 복기 기록에 실행하지 않은 일이 섞였다
    r = rig()
    r.market.quotes["00000B"] = Quote("00000B", (), ())  # 호가가 없어 건너뜀 사건이 남는다
    r.service.execute(PLAN, Phase.OPEN, send=False)
    assert r.log.events_ and all(event.preview for event in r.log.events_)
    count = len(r.log.events_)
    r.service.execute(PLAN, Phase.OPEN, send=True)
    assert not any(event.preview for event in r.log.events_[count:])


def test_rerunning_open_does_not_send_twice() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    count = len(r.gateway.submitted)
    again = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert again.status is ExecuteStatus.OK and len(r.gateway.submitted) == count
    assert again.lines == ()  # 살아 있는 주문을 목표에서 빼므로 더 낼 것이 없다


def test_unsettled_presend_read_is_retried_with_the_same_intent() -> None:
    r = rig()
    r.gateway.retry_first = {f"{PLAN}-00000A-buy"}
    r.service.execute(PLAN, Phase.OPEN, send=True)
    assert [o.intent_id for o in r.gateway.submitted].count(f"{PLAN}-00000A-buy") == 1


def test_wide_spread_is_not_deferred_and_only_a_missing_book_is_skipped() -> None:
    r = rig()
    r.market.quotes["00000A"] = Quote("00000A", (10_070, 10_080), (10_000, 9_990))  # 7틱
    r.market.quotes["00000B"] = Quote("00000B", (), ())
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    outcomes = {line.symbol: (line.outcome, line.limit_price_krw) for line in result.lines}
    assert outcomes == {
        "00000E": ("sent", 9_990),
        "00000A": ("sent", 10_080),  # 넓어도 그때 2호가로 낸다
        "00000B": ("no_quote", 0),
    }


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


def test_a_plan_can_run_after_a_long_holiday() -> None:
    # 리뷰 #605 P3: 연휴로 다음 거래일이 신호일 8일 뒤면 주문지의 실행일에 open 이 stale 로 끝났다
    after_holiday = datetime(2026, 10, 31, 9, 10, tzinfo=KST)  # 신호일(10-23) 8일 뒤
    assert rig(now=after_holiday).service.execute(PLAN, Phase.OPEN, send=True).status is (
        ExecuteStatus.OK
    )


def test_order_budget_caps_the_run() -> None:
    r = rig(policy=ExecutionPolicy(max_orders_per_day=2))
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert len(r.gateway.submitted) == 2
    assert [line.outcome for line in result.lines][-1] == "limit"


def test_chase_rounds_cancel_the_remainder_and_resend_at_the_current_book() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.fill(f"{PLAN}-00000A-buy", 50, 10_020)  # A 50주만 체결, B는 체결 없음
    r.market.quotes["00000A"] = Quote("00000A", (10_100, 10_110), (10_090, 10_080))
    first = r.service.execute(PLAN, Phase.CHASE, send=True)
    assert r.gateway.cancelled == [f"{PLAN}-00000A-buy", f"{PLAN}-00000B-buy"]
    assert _sent(r)[-2:] == [
        (f"{PLAN}-00000A-buy-c1", "buy", 80, 10_110),
        (f"{PLAN}-00000B-buy-c1", "buy", 130, 10_020),
    ]
    assert [line.outcome for line in first.lines][:2] == ["cancelled", "cancelled"]
    # 다음 회차: B는 다 찼고 A는 그대로다. A만 다시 쫓는다
    r.gateway.fill(f"{PLAN}-00000B-buy-c1", 130, 10_020)
    r.market.quotes["00000A"] = Quote("00000A", (10_150, 10_160), (10_140, 10_130))
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert r.gateway.cancelled[-1] == f"{PLAN}-00000A-buy-c1"
    assert _sent(r)[-1] == (f"{PLAN}-00000A-buy-c2", "buy", 80, 10_160)
    # 다 차면 더 내지 않는다
    r.gateway.fill(f"{PLAN}-00000A-buy-c2", 80, 10_160)
    count = len(r.gateway.submitted)
    assert r.service.execute(PLAN, Phase.CHASE, send=True).lines == ()
    assert len(r.gateway.submitted) == count


def test_chase_leaves_unknown_orders_alone() -> None:
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.orders[f"{PLAN}-00000A-buy"] = replace(
        r.gateway.orders[f"{PLAN}-00000A-buy"], state="unknown"
    )
    count = len(r.gateway.submitted)
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert f"{PLAN}-00000A-buy" not in r.gateway.cancelled
    assert all(o.symbol != "00000A" for o in r.gateway.submitted[count:])


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


def test_close_on_a_later_day_does_not_record_the_weeks_fills_again() -> None:
    # 리뷰 #604: 화~금 장 마감 기록이 월요일 누적 체결과 주문 줄을 날마다 다시 적어 합계가 부풀었다
    r = rig()
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.fill(f"{PLAN}-00000A-buy", 50, 10_020)
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    monday = r.service.close().record
    assert monday is not None and monday.orders and monday.traded_krw == 999_000 + 501_000
    tuesday = WeeklyExecutionService(
        r.plans,
        r.gateway,
        r.market,
        r.log,
        risk=r.risk,
        now=lambda: MONDAY + timedelta(days=1, hours=6),
        sleep=lambda _: None,
        waits=Waits(0, 8, 0, 0, 3),
    )
    record = tuesday.close().record
    assert record is not None and (record.traded_krw, record.orders) == (0, ())
    assert record.equity_krw == monday.equity_krw  # 보유·현금은 그날 값 그대로 남긴다
    assert [day.traded_krw for day in r.log.days()] == [1_500_000, 0]


def test_no_trade_symbols_from_the_approval_are_neither_bought_nor_sold() -> None:
    r = rig(approved=False)
    r.plans.save_approval(PLAN, Approval(MONDAY, "동민", no_trade=("00000A",)))
    r.service.execute(PLAN, Phase.OPEN, send=True)
    assert {o.symbol for o in r.gateway.submitted} == {"00000E", "00000B"}
    assert any(e.kind == "no_trade" for e in r.log.events_)
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert {o.symbol for o in r.gateway.submitted} == {"00000E", "00000B"}
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    record = r.service.close().record
    assert record is not None and {o.symbol for o in record.orders} == {"00000E", "00000B"}


def test_keeping_a_name_planned_for_sale_also_drops_the_lowest_new_buy() -> None:
    # 리뷰 #606: 파는 E 를 빼면 E 가 자리를 그대로 차지한다. 대체 매수 중 순위가 낮은 B 를 함께 뺀다
    r = rig(approved=False)
    r.plans.save_approval(PLAN, Approval(MONDAY, "동민", no_trade=("00000E",)))
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert {o.symbol for o in r.gateway.submitted} == {"00000A"}
    assert r.gateway.holdings["00000E"] == 100
    no_trade = next(e.detail for e in r.log.events_ if e.kind == "no_trade")
    assert "함께 뺀 새 매수 ['00000B']" in no_trade
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    record = r.service.close().record
    assert record is not None and {o.symbol for o in record.orders} == {"00000A"}


def test_cash_limited_buy_is_sized_with_the_fee_budget_the_ledger_checks() -> None:
    # 리뷰 #603: 수량은 35bp 로 정하고 S05 는 40bp 로 현금을 검사해 주문 전체가 막혔다
    r = rig()
    r.gateway.cash = 992_811  # 매도 999,000원 · A 130주(1,307,811원) 뒤 B 에 684,000원이 남는다
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    b = next(line for line in result.lines if line.symbol == "00000B")
    # 684,000 ÷ (10,020 × 1.0040) = 67.99 → 67주. 35bp 로는 68주(684,086원)라 현금을 넘었다
    assert (b.outcome, b.quantity) == ("sent", 67)


# --- 리스크 가드(②·③) -------------------------------------------------------------------------


def test_a_plan_without_a_risk_verdict_sends_nothing() -> None:
    r = rig(level=None)
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert result.status is ExecuteStatus.RISK_UNAVAILABLE and r.gateway.submitted == []
    unreadable = rig()
    unreadable.risk.limits_ = None
    assert unreadable.service.execute(PLAN, Phase.OPEN, send=True).status is (
        ExecuteStatus.RISK_UNAVAILABLE
    )
    assert unreadable.gateway.submitted == []


def test_orders_follow_the_risk_targets_not_the_plan_targets() -> None:
    r = rig(level=RiskLevel.REDUCE)
    r.service.execute(PLAN, Phase.OPEN, send=True)
    # 노출 50%: D 100주 → 50주(매도 50), E 0주, A·B 130주 → 65주
    assert _sent(r) == [
        (f"{PLAN}-00000D-sell", "sell", 50, 9_990),
        (f"{PLAN}-00000E-sell", "sell", 100, 9_990),
        (f"{PLAN}-00000A-buy", "buy", 65, 10_020),
        (f"{PLAN}-00000B-buy", "buy", 65, 10_020),
    ]


def test_a_buy_of_a_flagged_name_is_blocked_every_round_and_a_sell_goes_out() -> None:
    r = rig()
    warned = TradeStatus("00000A", frozenset({SecurityFlag.INVESTMENT_WARNING}), 13_000, 7_000)
    r.market.statuses = {"00000A": warned, "00000E": replace(warned, symbol="00000E")}
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    outcomes = {line.symbol: line.outcome for line in result.lines}
    assert outcomes == {"00000E": "sent", "00000A": "risk_blocked", "00000B": "sent"}
    blocked = [e for e in r.log.events_ if e.kind == "risk_blocked"]
    assert [(e.symbol, e.side) for e in blocked] == [("00000A", "buy")]
    assert "investment_warning" in blocked[0].detail
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert all(o.symbol != "00000A" for o in r.gateway.submitted)


def test_an_active_vi_holds_the_order_for_one_round_only() -> None:
    r = rig()
    vi = ViWindow(time(9, 8), None)  # 09:10 에 해제 시각 없이 발동 2분째
    r.market.statuses = {"00000A": TradeStatus("00000A", frozenset(), 13_000, 7_000, (vi,))}
    first = r.service.execute(PLAN, Phase.OPEN, send=True)
    assert {line.symbol: line.outcome for line in first.lines}["00000A"] == "risk_hold"
    r.market.statuses = {}
    r.service.execute(PLAN, Phase.CHASE, send=True)
    assert f"{PLAN}-00000A-buy" in {o.intent_id for o in r.gateway.submitted}


def test_an_unreadable_status_skips_only_that_order_this_round() -> None:
    r = rig()
    r.market.unreadable = {"00000A"}
    result = r.service.execute(PLAN, Phase.OPEN, send=True)
    outcomes = {line.symbol: line.outcome for line in result.lines}
    assert outcomes == {"00000E": "sent", "00000A": "no_status", "00000B": "sent"}


def test_close_counts_unfilled_against_the_risk_targets_and_reviews_the_day() -> None:
    r = rig(level=RiskLevel.REDUCE)
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.gateway.fill(f"{PLAN}-00000A-buy", 65, 10_020)
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    closed = r.service.close()
    assert closed.status is CloseStatus.OK and closed.record is not None
    orders = {(o.symbol, o.side): o for o in closed.record.orders}
    # 계획 수량은 리스크 뒤 65주이고, 리스크가 만든 D 매도 50주도 줄에 든다
    assert (orders[("00000A", "buy")].quantity, orders[("00000A", "buy")].missed_krw) == (65, 0)
    assert orders[("00000D", "sell")].quantity == 50
    assert closed.risk is not None and closed.risk.review is not None
    reviewed = {(o.symbol, o.side): o for o in closed.risk.review.orders}
    assert (
        reviewed[("00000A", "buy")].plan_quantity,
        reviewed[("00000A", "buy")].reduced_by_risk,
    ) == (
        130,
        65,
    )
    assert reviewed[("00000A", "buy")].unfilled_bp == 0
    assert reviewed[("00000D", "sell")].plan_quantity == 0
    assert closed.risk.review.violations == ()
    assert r.risk.days_[MONDAY.date()] == closed.risk


def test_close_records_the_drawdown_level_move_of_the_day() -> None:
    r = rig()
    r.log.save_day(close_record(date(2026, 10, 23), 100_000_000))
    r.service.execute(PLAN, Phase.OPEN, send=True)
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    closed = r.service.close()
    # 고점 1억 → 오늘 총자산 약 300만: 정지 선을 넘었다
    assert closed.risk is not None
    assert (closed.risk.previous_level, closed.risk.state.level) == (
        RiskLevel.NORMAL,
        RiskLevel.HALT,
    )


def test_a_close_still_records_the_day_when_risk_records_are_unreadable() -> None:
    r = rig()
    r.market.closes = {"00000D": 10_050, "00000E": 9_900}
    r.risk.limits_ = None
    closed = r.service.close()
    assert closed.status is CloseStatus.OK and closed.record is not None
    assert closed.risk is None and "limits" in closed.risk_detail
    assert r.service.days() == (closed.record,)
