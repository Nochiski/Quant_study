"""주간 리밸런싱 주문안 서비스(S07). 신호·계좌·저장소는 메모리 대역이다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    UNTRADED_REASON,
    AccountState,
    AccountUnavailableError,
    Approval,
    ApproveStatus,
    DayRecord,
    EarlierClose,
    ExecutionEvent,
    PlanExistsError,
    PlanRecord,
    PrepareStatus,
    RiskDay,
    RiskUnavailableError,
    RiskVerdict,
    SignalBasis,
    SignalRead,
    SignalReadStatus,
    SignalSnapshot,
    WeeklyRebalanceService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalanceAction, RebalancePolicy
from strategy_workbench.domain.risk.facade.risk import (
    GuardReason,
    PostTradeReview,
    RiskAction,
    RiskLevel,
    RiskLimits,
    SignalFacts,
    parse_limits,
)
from tests.domain.test_risk import PAYLOAD

KST = timezone(timedelta(hours=9))
DAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 16, 10, tzinfo=KST)
# 이 대역의 주문안은 자리 3개(종목당 약 33%)라 단일 종목 상한만 50%로 넓힌다
LIMITS = parse_limits({**PAYLOAD, "max_weight_bp": 5_000}, "ab" * 32)


def snapshot(**overrides: object) -> SignalSnapshot:
    values: dict[str, object] = {
        "model_id": "scope@1.0",
        "build_id": "e_20261023T065500Z",
        "signal_date": DAY,
        "ranks": {"000001": 1, "000002": 2, "000003": 3, "000004": 4},
        "closes_krw": dict.fromkeys(("000001", "000002", "000003", "000004", "000009"), 10_000),
        "untraded": frozenset(),
        "scores_sha256": "ab" * 32,
        "prices_build_id": "e_20261023T065450Z",
        # 거래대금 100억 원, 관리·정지 표시 없음 — 리스크 한도가 목표를 줄이지 않는다
        "facts": dict.fromkeys(
            ("000001", "000002", "000003", "000004", "000009"),
            SignalFacts(10_000_000_000, admin=False, halted=False),
        ),
    }
    values.update(overrides)
    return SignalSnapshot(**values)  # pyright: ignore[reportArgumentType]  # reason: 테스트 대역 값


@dataclass
class Signals:
    read_result: SignalRead = field(
        default_factory=lambda: SignalRead(SignalReadStatus.OK, snapshot())
    )
    calls: int = 0
    bases: list[SignalBasis] = field(default_factory=list)

    def read(self, signal_date: date, basis: SignalBasis) -> SignalRead:
        self.calls += 1
        self.bases.append(basis)
        return self.read_result


@dataclass
class Account:
    state: AccountState = field(default_factory=lambda: AccountState(3_000_000, {}, None))
    error: str | None = None

    def current(self) -> AccountState:
        if self.error is not None:
            raise AccountUnavailableError(self.error)
        return self.state


@dataclass
class Plans:
    records: dict[str, PlanRecord] = field(default_factory=dict)
    approvals: dict[str, Approval] = field(default_factory=dict)

    def save(self, record: PlanRecord) -> None:
        if record.plan_id in self.records:
            raise PlanExistsError(record.plan_id)
        self.records[record.plan_id] = record

    def load(self, plan_id: str) -> PlanRecord | None:
        return self.records.get(plan_id)

    def save_approval(self, plan_id: str, approval: Approval) -> None:
        if plan_id in self.approvals:
            raise PlanExistsError(plan_id)
        self.approvals[plan_id] = approval

    def approval(self, plan_id: str) -> Approval | None:
        return self.approvals.get(plan_id)

    def plan_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.records))


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
class Risk:
    """리스크 저장소 대역. `limits_`가 None이면 한도 파일을 읽지 못한 것처럼 군다."""

    limits_: RiskLimits | None = field(default_factory=lambda: LIMITS)
    actions_: list[RiskAction] = field(default_factory=list)
    verdicts: dict[str, RiskVerdict] = field(default_factory=dict)
    days_: dict[date, RiskDay] = field(default_factory=dict)

    def limits(self) -> RiskLimits:
        if self.limits_ is None:
            raise RiskUnavailableError("risk limits unreadable — file=limits.json")
        return self.limits_

    def actions(self) -> tuple[RiskAction, ...]:
        return tuple(self.actions_)

    def add_action(self, action: RiskAction) -> None:
        self.actions_.append(action)

    def save_verdict(self, verdict: RiskVerdict) -> None:
        if verdict.plan_id in self.verdicts:
            raise PlanExistsError(verdict.plan_id)
        self.verdicts[verdict.plan_id] = verdict

    def verdict(self, plan_id: str) -> RiskVerdict | None:
        return self.verdicts.get(plan_id)

    def save_day(self, record: RiskDay) -> None:
        self.days_[record.day] = record

    def days(self) -> tuple[RiskDay, ...]:
        return tuple(self.days_[day] for day in sorted(self.days_))


def close_record(day: date, equity_krw: int, *, matched: bool = True) -> DayRecord:
    """장 마감 기록. 낙폭은 총자산만 본다."""
    at = datetime.combine(day, datetime.min.time(), KST) + timedelta(hours=15, minutes=40)
    return DayRecord(day, at, "weekly-2026-10-16", matched, "", equity_krw, (), equity_krw, 0, ())


def service(
    signals: Signals | None = None,
    account: Account | None = None,
    plans: Plans | None = None,
    *,
    risk: Risk | None = None,
    log: Log | None = None,
) -> WeeklyRebalanceService:
    return WeeklyRebalanceService(
        signals or Signals(),
        account or Account(),
        plans or Plans(),
        risk=risk or Risk(),
        log=log or Log(),
        now=lambda: NOW,
        policy=RebalancePolicy(slots=3, keep_rank=5),
    )


def test_prepare_records_the_signal_and_plan_once() -> None:
    signals, plans = Signals(), Plans()
    first = service(signals, plans=plans).prepare(DAY)
    assert first.status is PrepareStatus.OK and first.record is not None
    record = first.record
    assert record.plan_id == "weekly-2026-10-23"
    assert record.signal.ranks[:2] == (("000001", 1), ("000002", 2))
    assert record.signal.scores_sha256 == "ab" * 32
    assert record.plan.target_shares() == dict.fromkeys(("000001", "000002", "000003"), 97)
    again = service(signals, plans=plans).prepare(DAY)
    assert again.status is PrepareStatus.EXISTS and again.record == record
    assert signals.calls == 1  # 남긴 주문안이 있으면 신호를 다시 읽지 않는다


def test_prepare_reads_the_requested_build_and_records_which() -> None:
    # 예약 실행은 휴장 첫날 주말 보강판으로 만든다(2026-10-23 사용자 결정).
    # 주문안에 어느 빌드였는지 남는다
    signals = Signals()
    record = service(signals).prepare(DAY, basis=SignalBasis.SUPPLEMENT).record
    assert signals.bases == [SignalBasis.SUPPLEMENT]
    assert record is not None and record.signal.basis is SignalBasis.SUPPLEMENT
    evening = service(Signals()).prepare(DAY).record
    assert evening is not None and evening.signal.basis is SignalBasis.EVENING


def test_plan_keeps_names_of_its_lines_and_why_holdings_left_the_signal() -> None:
    names = {"000001": "가", "000002": "나", "000003": "다", "000004": "라", "000009": "보유"}
    signal = snapshot(names=names, exclusion_reasons={"000009": "시총 하한 미만(시총 800억)"})
    account = Account(AccountState(2_000_000, {"000009": 100}, None))
    record = service(Signals(SignalRead(SignalReadStatus.OK, signal)), account).prepare(DAY).record
    assert record is not None
    assert dict(record.signal.names) == {
        code: names[code] for code in ("000001", "000002", "000003", "000009")
    }
    [held] = [line for line in record.plan.lines if line.symbol == "000009"]
    assert held.review and held.reason.startswith("신호에서 빠짐 — 시총 하한 미만(시총 800억)")


def test_untraded_names_are_blocked() -> None:
    signals = Signals(SignalRead(SignalReadStatus.OK, snapshot(untraded=frozenset({"000001"}))))
    record = service(signals).prepare(DAY).record
    assert record is not None
    skipped = [line for line in record.plan.lines if line.action is RebalanceAction.SKIP]
    assert [(line.symbol, line.reason) for line in skipped] == [
        ("000001", f"매매가 막혀 건너뜀 — {UNTRADED_REASON}")
    ]


def test_unavailable_inputs_leave_no_plan() -> None:
    not_ready = Signals(SignalRead(SignalReadStatus.NOT_READY, detail="other date"))
    cases = [
        (not_ready, Account(), PrepareStatus.SIGNAL_UNAVAILABLE),
        (Signals(), Account(error="no response"), PrepareStatus.ACCOUNT_UNAVAILABLE),
        (
            Signals(),
            Account(AccountState(0, {"000001": 1}, False, "cash mismatch")),
            PrepareStatus.NOT_RECONCILED,
        ),
        # 보유 종목 000008의 기준가격이 신호일에도, 그 전에도 가격 판에 없다
        (Signals(), Account(AccountState(0, {"000008": 1}, True)), PrepareStatus.INVALID_INPUT),
    ]
    for signals, account, status in cases:
        plans = Plans()
        result = service(signals, account, plans).prepare(DAY)
        assert result.status is status, result
        assert plans.records == {}


def test_approval_is_recorded_once() -> None:
    plans = Plans()
    rebalance = service(plans=plans)
    assert rebalance.approve("weekly-2026-10-23", "동민").status is ApproveStatus.NOT_FOUND
    rebalance.prepare(DAY)
    first = rebalance.approve("weekly-2026-10-23", "동민", "첫 주")
    assert first.status is ApproveStatus.OK and first.approval == Approval(NOW, "동민", "첫 주")
    second = rebalance.approve("weekly-2026-10-23", "다른 사람")
    assert second.status is ApproveStatus.ALREADY_APPROVED and second.approval == first.approval


def test_suspected_data_gap_holds_the_plan_until_a_person_accepts_it() -> None:
    previous = {"000001": 1, "000002": 2, "000007": 3, "000008": 4, "000003": 5, "000004": 6}
    suspect = snapshot(previous_build_id="e_prev", previous_ranks=previous)  # 000007·000008 사라짐
    plans = Plans()
    held = service(Signals(SignalRead(SignalReadStatus.OK, suspect)), plans=plans).prepare(DAY)
    assert held.status is PrepareStatus.SIGNAL_SUSPECT and plans.records == {}
    assert "000007" in held.detail and "e_prev" in held.detail
    accepted = service(Signals(SignalRead(SignalReadStatus.OK, suspect)), plans=plans).prepare(
        DAY, accept_anomalies=True
    )
    assert accepted.status is PrepareStatus.OK and accepted.record is not None
    assert accepted.record.signal.anomalies and accepted.record.signal.previous_build_id == "e_prev"


def test_the_gap_check_looks_at_the_buffer_of_the_policy() -> None:
    # 리뷰 #603 P3: 점검 범위가 완충 순위와 따로 30위로 고정돼 있었다. 이 주문안의 완충은 5위다
    others = {f"1{index:05d}": 10 + index for index in range(100)}  # 종목 수 감소는 2% 밑
    ranks = {"000001": 1, "000002": 2, "000003": 3, "000004": 4, **others}
    previous = {**ranks, "000007": 6, "000008": 7}  # 완충(5위) 밖 두 종목이 사라졌다
    outside = snapshot(ranks=ranks, previous_build_id="e_prev", previous_ranks=previous)
    result = service(Signals(SignalRead(SignalReadStatus.OK, outside))).prepare(DAY)
    assert result.status is PrepareStatus.OK


def test_a_skipped_gap_check_is_recorded_with_why() -> None:
    # 리뷰 #606 P3: '점검 통과'와 '점검 건너뜀'이 구분되지 않았다
    unreadable = snapshot(previous_build_id="e_unreadable", previous_ranks=None)
    record = service(Signals(SignalRead(SignalReadStatus.OK, unreadable))).prepare(DAY).record
    assert record is not None and record.signal.gap_check_skipped is not None
    assert "e_unreadable" in record.signal.gap_check_skipped
    first = service(Signals(SignalRead(SignalReadStatus.OK, snapshot()))).prepare(DAY).record
    assert first is not None and first.signal.gap_check_skipped == "직전 빌드 없음"
    checked = snapshot(previous_build_id="e_prev", previous_ranks={"000001": 1})
    plain = service(Signals(SignalRead(SignalReadStatus.OK, checked))).prepare(DAY).record
    assert plain is not None and plain.signal.gap_check_skipped is None


def test_held_name_without_a_signal_day_price_is_held_on_its_earlier_close() -> None:
    # 리뷰 #603: 거래정지로 신호일 가격 행이 없는 보유 종목 하나 때문에 주문안 전체가 실패했다
    signal = snapshot(earlier_closes={"000008": EarlierClose(date(2026, 10, 20), 12_000)})
    account = Account(AccountState(1_800_000, {"000008": 100}, True))
    result = service(Signals(SignalRead(SignalReadStatus.OK, signal)), account).prepare(DAY)
    assert result.status is PrepareStatus.OK and result.record is not None
    [held] = [line for line in result.record.plan.lines if line.symbol == "000008"]
    assert (held.action, held.target_shares, held.review) == (RebalanceAction.HOLD, 100, True)
    assert held.reference_price_krw == 12_000
    assert "신호일 종가 없음(거래정지 등) — 2026-10-20 종가로 평가" in held.reason
    assert result.record.plan.equity_krw == 3_000_000


def test_approval_reports_the_new_buys_dropped_with_a_kept_sale() -> None:
    plans = Plans()
    held = Account(AccountState(2_000_000, {"000009": 100}, None))
    rebalance = service(account=held, plans=plans)
    rebalance.prepare(DAY)  # 000009 는 신호에서 빠져 팔고, 1~3위를 산다
    approved = rebalance.approve("weekly-2026-10-23", "동민", no_trade=("000009",))
    assert approved.status is ApproveStatus.OK
    assert approved.dropped_buys == ("000003",)


def test_approval_can_take_symbols_out_of_this_week() -> None:
    plans = Plans()
    rebalance = service(plans=plans)
    rebalance.prepare(DAY)
    unknown = rebalance.approve("weekly-2026-10-23", "동민", no_trade=("999999",))
    assert unknown.status is ApproveStatus.INVALID and plans.approvals == {}
    approved = rebalance.approve("weekly-2026-10-23", "동민", no_trade=("000002", "000002"))
    assert approved.status is ApproveStatus.OK and approved.approval is not None
    assert approved.approval.no_trade == ("000002",)


# --- 리스크 판정(S07 리스크 가드 ①) -------------------------------------------------------------


def test_prepare_leaves_a_risk_verdict_with_the_limits_it_used() -> None:
    risk, plans = Risk(), Plans()
    result = service(plans=plans, risk=risk).prepare(DAY)
    assert result.status is PrepareStatus.OK and result.verdict is not None
    verdict = risk.verdicts["weekly-2026-10-23"]
    assert verdict == result.verdict
    assert (verdict.limits_version, verdict.limits_sha256) == (1, "ab" * 32)
    assert verdict.state.level is RiskLevel.NORMAL
    # 한도 안이라 리스크 목표 = 주문안 목표
    assert verdict.guarded.targets() == plans.records["weekly-2026-10-23"].plan.target_shares()


def test_prepare_halves_targets_after_a_15_percent_drawdown() -> None:
    log = Log()
    log.save_day(close_record(date(2026, 10, 23), 85_000_000))  # 시작 고점 1억 대비 −15%
    result = service(log=log).prepare(DAY)
    assert result.verdict is not None and result.verdict.state.level is RiskLevel.REDUCE
    # 주문안 97주 → 48주(내림)
    assert result.verdict.guarded.targets() == dict.fromkeys(("000001", "000002", "000003"), 48)
    assert {line.reasons for line in result.verdict.guarded.lines} == {(GuardReason.EXPOSURE,)}


def test_unreconciled_close_records_do_not_move_the_drawdown() -> None:
    log = Log()
    log.save_day(close_record(date(2026, 10, 23), 80_000_000, matched=False))
    result = service(log=log).prepare(DAY)
    assert result.verdict is not None and result.verdict.state.level is RiskLevel.NORMAL


def test_no_plan_is_left_when_risk_cannot_be_judged() -> None:
    plans = Plans()
    result = service(plans=plans, risk=Risk(limits_=None)).prepare(DAY)
    assert result.status is PrepareStatus.RISK_UNAVAILABLE and plans.records == {}
    assert "limits.json" in result.detail


def test_names_without_signal_day_facts_are_not_bought() -> None:
    signals = Signals(SignalRead(SignalReadStatus.OK, snapshot(facts=None)))
    result = service(signals).prepare(DAY)
    assert result.verdict is not None
    assert result.verdict.guarded.targets() == dict.fromkeys(("000001", "000002", "000003"), 0)


def test_the_next_verdict_carries_violations_found_since_the_last_plan() -> None:
    risk, plans = Risk(), Plans()
    service(plans=plans, risk=risk).prepare(DAY)
    first = risk.verdicts["weekly-2026-10-23"]
    risk.days_[date(2026, 10, 26)] = RiskDay(
        date(2026, 10, 26),
        NOW + timedelta(days=3),
        1,
        "ab" * 32,
        RiskLevel.NORMAL,
        first.state,
        PostTradeReview((), ("리스크가 허용한 수량보다 더 샀다 — symbol=000001",), ()),
    )
    later = WeeklyRebalanceService(
        Signals(SignalRead(SignalReadStatus.OK, snapshot(signal_date=date(2026, 10, 30)))),
        Account(),
        plans,
        risk=risk,
        log=Log(),
        now=lambda: NOW + timedelta(days=7),
        policy=RebalancePolicy(slots=3, keep_rank=5),
    ).prepare(date(2026, 10, 30))
    assert later.verdict is not None
    assert later.verdict.violations_since_last == (
        "리스크가 허용한 수량보다 더 샀다 — symbol=000001",
    )
