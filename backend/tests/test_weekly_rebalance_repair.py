"""원장 고치기(S07, 2026-10-22 리뷰 #593): 키움 가짜 서버와 S05 실행 원장을 그대로 쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategy_workbench.application.offline_execution.facade.execution import ExecutionStatus
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    LedgerRepairService,
    RepairStatus,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import KiwoomLedgerRepair
from strategy_workbench.domain.order_execution.facade.orders import OrderState
from tests.application.test_weekly_execution import Log
from tests.test_adapters_kiwoom_mock import SYMBOL, Rig, buy, fact_of, started

KST = timezone(timedelta(hours=9))


def repair_of(r: Rig) -> tuple[LedgerRepairService, Log]:
    log = Log()
    service = LedgerRepairService(
        KiwoomLedgerRepair(r.broker, r.ledger), log, now=lambda: datetime(2026, 10, 26, tzinfo=KST)
    )
    return service, log


def test_a_lost_request_with_no_kiwoom_order_is_rejected_by_hand_and_resumes(
    tmp_path: Path,
) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "drop"  # 요청이 키움에 닿지 않았다
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert r.service.recover().status == ExecutionStatus.BLOCKED and r.ledger.halted()
    repairs, log = repair_of(r)
    [order] = repairs.state().unresolved
    assert (order.intent_id, order.candidates) == ("signal-1", ())
    result = repairs.reject("signal-1", "동민", "kt00007 에 없음")
    assert result.status is RepairStatus.OK, result
    assert fact_of(r.ledger).state == OrderState.REJECTED and not r.ledger.halted()
    assert [event.kind for event in log.events_] == ["ledger_reject"]


def test_binding_needs_one_of_the_listed_order_numbers(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "lose_twice"  # 같은 내용 주문이 둘이라 자동으로 묶지 않는다
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert r.service.recover().status == ExecutionStatus.BLOCKED
    repairs, _ = repair_of(r)
    [order] = repairs.state().unresolved
    assert order.candidates == ("0000001", "0000002")
    assert repairs.reject("signal-1", "동민").status is RepairStatus.REFUSED
    assert repairs.bind("signal-1", "0000009", "동민").status is RepairStatus.REFUSED
    bound = repairs.bind("signal-1", "0000001", "동민")
    # 묶었지만 다른 하나는 우리 주문이 아니라 대사가 아직 멈춰 있다(사람이 그 주문을 정리해야 한다)
    assert bound.status is RepairStatus.STILL_BLOCKED and "untracked" in bound.detail
    assert fact_of(r.ledger).state == OrderState.OPEN


def test_a_latch_is_cleared_by_hand_and_comes_back_if_the_cause_remains(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    amendment = r.fake.row(r.fake.add_row(SYMBOL, "현금매수정정", 5, 69_000, channel="영웅문4"))
    amendment["ori_ord"] = "0000001"
    amendment["mdfy_cncl"] = "정정확인"
    assert r.service.recover().status == ExecutionStatus.ERROR
    repairs, log = repair_of(r)
    assert "amended" in (repairs.state().latch or "")
    again = repairs.clear_latch("동민", "정정 행 확인 전")
    assert again.status is RepairStatus.STILL_BLOCKED and r.broker.latch() is not None
    r.fake.rows.remove(amendment)
    assert repairs.clear_latch("동민", "정정 행 사라짐").status is RepairStatus.OK
    assert r.broker.latch() is None
    assert [event.kind for event in log.events_] == ["ledger_clear_latch", "ledger_clear_latch"]
