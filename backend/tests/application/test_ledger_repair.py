"""실행 원장을 사람이 고치는 일(S07, 2026-10-22 리뷰 #593). 원장·기록은 메모리 대역이다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    LedgerRepairService,
    LedgerRepairState,
    RepairError,
    RepairStatus,
    ResumeOutcome,
    UnresolvedOrder,
)
from tests.application.test_weekly_execution import Log

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 26, 9, 40, tzinfo=KST)
INTENT = "weekly-2026-10-23-005930-buy"


@dataclass
class Repair:
    latch: str | None = None
    candidates: tuple[str, ...] = ("0000007",)
    matched_after: bool = True
    done: list[str] = field(default_factory=list)

    def state(self) -> LedgerRepairState:
        order = UnresolvedOrder(
            INTENT,
            "005930",
            "buy",
            5,
            70_000,
            "20261026",
            "09:05:01",
            "pending",
            "",
            self.candidates,
        )
        return LedgerRepairState(self.latch, True, (order,))

    def bind(self, intent_id: str, order_no: str) -> None:
        if order_no not in self.candidates:
            raise RepairError(f"order number is not a candidate — ord_no={order_no}")
        self.done.append(f"bind {intent_id} {order_no}")

    def reject(self, intent_id: str) -> None:
        if self.candidates:
            raise RepairError("kiwoom has orders with the same content")
        self.done.append(f"reject {intent_id}")

    def clear_latch(self) -> None:
        self.latch = None
        self.done.append("clear")

    def resume(self) -> ResumeOutcome:
        self.done.append("resume")
        return ResumeOutcome(self.matched_after, "" if self.matched_after else "untracked order")


def service(repair: Repair) -> tuple[LedgerRepairService, Log]:
    log = Log()
    return LedgerRepairService(repair, log, now=lambda: NOW), log


def test_binding_a_lost_order_records_who_did_it_and_resumes() -> None:
    repair = Repair()
    repairs, log = service(repair)
    result = repairs.bind(INTENT, "0000007", "동민", "kt00007 로 확인")
    assert result.status is RepairStatus.OK
    assert repair.done == [f"bind {INTENT} 0000007", "resume"]
    [event] = log.events_
    assert (event.plan_id, event.phase, event.kind, event.intent_id) == (
        "weekly-2026-10-23",
        "repair",
        "ledger_bind",
        INTENT,
    )
    assert "주문번호 0000007" in event.detail and "동민" in event.detail


def test_a_refused_repair_changes_and_records_nothing() -> None:
    repair = Repair()
    repairs, log = service(repair)
    assert repairs.bind(INTENT, "0000099", "동민").status is RepairStatus.REFUSED
    assert repairs.reject(INTENT, "동민").status is RepairStatus.REFUSED  # 같은 내용 주문이 있다
    assert repair.done == [] and log.events_ == []


def test_reject_and_clear_latch_report_when_the_ledger_still_does_not_match() -> None:
    repair = Repair(latch="snapshot — amended order", candidates=(), matched_after=False)
    repairs, log = service(repair)
    assert repairs.reject(INTENT, "동민").status is RepairStatus.STILL_BLOCKED
    cleared = repairs.clear_latch("동민", "정정 행 확인")
    assert cleared.status is RepairStatus.STILL_BLOCKED and "untracked order" in cleared.detail
    assert "래치 해제 — snapshot — amended order" in log.events_[-1].detail
    assert repairs.clear_latch("동민").status is RepairStatus.REFUSED  # 이미 풀렸다
