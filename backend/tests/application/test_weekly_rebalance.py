"""주간 리밸런싱 주문안 서비스(S07). 신호·계좌·저장소는 메모리 대역이다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    UNTRADED_REASON,
    AccountState,
    AccountUnavailableError,
    Approval,
    ApproveStatus,
    EarlierClose,
    PlanExistsError,
    PlanRecord,
    PrepareStatus,
    SignalRead,
    SignalReadStatus,
    SignalSnapshot,
    WeeklyRebalanceService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalanceAction, RebalancePolicy

KST = timezone(timedelta(hours=9))
DAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 16, 10, tzinfo=KST)


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
    }
    values.update(overrides)
    return SignalSnapshot(**values)  # pyright: ignore[reportArgumentType]  # reason: 테스트 대역 값


@dataclass
class Signals:
    read_result: SignalRead = field(
        default_factory=lambda: SignalRead(SignalReadStatus.OK, snapshot())
    )
    calls: int = 0

    def read(self, signal_date: date) -> SignalRead:
        self.calls += 1
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


def service(
    signals: Signals | None = None, account: Account | None = None, plans: Plans | None = None
) -> WeeklyRebalanceService:
    return WeeklyRebalanceService(
        signals or Signals(),
        account or Account(),
        plans or Plans(),
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
    previous = {"000001": 1, "000002": 2, "000003": 3, "000004": 4, "000007": 5, "000008": 6}
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
