"""실행 원장을 사람이 고치는 일(2026-10-22 리뷰 #593). 고친 일은 실행 사건 기록에 남긴다.

응답을 잃은 주문을 키움 주문번호에 묶거나 거부로 확정하고, 정지 래치를 푼 뒤 다시 대사한다.
일치하면 신규 주문 정지도 풀린다. 원인이 남아 있으면 다음 조회에서 다시 멈춘다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance.ports.outgoing.records import (
    ExecutionEvent,
    ExecutionLog,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.repair import (
    LedgerRepairState,
    OrderLedgerRepair,
    RepairError,
    ResumeOutcome,
)

_PLAN_PREFIX = re.compile(r"^(weekly-\d{4}-\d{2}-\d{2})-")


class RepairStatus(StrEnum):
    OK = "ok"  # 고쳤고 다시 대사해 일치했다
    STILL_BLOCKED = "still_blocked"  # 고쳤지만 다시 대사가 아직 맞지 않는다
    REFUSED = "refused"  # 조건이 맞지 않아 고치지 않았다


@dataclass(frozen=True)
class RepairResult:
    status: RepairStatus
    detail: str


class LedgerRepairService:
    def __init__(
        self, repair: OrderLedgerRepair, log: ExecutionLog, *, now: Callable[[], datetime]
    ) -> None:
        self._repair = repair
        self._log = log
        self._now = now

    def state(self) -> LedgerRepairState:
        return self._repair.state()

    def bind(self, intent_id: str, order_no: str, operator: str, note: str = "") -> RepairResult:
        return self._apply(
            "ledger_bind",
            intent_id,
            f"주문번호 {order_no}에 묶음",
            lambda: self._repair.bind(intent_id, order_no),
            operator,
            note,
        )

    def reject(self, intent_id: str, operator: str, note: str = "") -> RepairResult:
        return self._apply(
            "ledger_reject",
            intent_id,
            "보내지 않은 것(거부)으로 확정",
            lambda: self._repair.reject(intent_id),
            operator,
            note,
        )

    def clear_latch(self, operator: str, note: str = "") -> RepairResult:
        latch = self._repair.state().latch
        if latch is None:
            return RepairResult(RepairStatus.REFUSED, "정지 래치가 걸려 있지 않다")
        return self._apply(
            "ledger_clear_latch",
            "",
            f"래치 해제 — {latch}",
            self._repair.clear_latch,
            operator,
            note,
        )

    def resume(self, operator: str, note: str = "") -> RepairResult:
        return self._apply("ledger_resume", "", "다시 대사", lambda: None, operator, note)

    def _apply(
        self,
        kind: str,
        intent_id: str,
        action: str,
        change: Callable[[], None],
        operator: str,
        note: str,
    ) -> RepairResult:
        try:
            change()
        except RepairError as error:
            return RepairResult(RepairStatus.REFUSED, str(error))
        match = _PLAN_PREFIX.match(intent_id)
        self._log.append(
            ExecutionEvent(
                self._now(),
                match.group(1) if match else "",
                "repair",
                kind,
                intent_id=intent_id,
                detail=f"{action} · 한 사람 {operator}" + (f" · {note}" if note else ""),
            )
        )
        try:
            outcome = self._repair.resume()
        except RepairError as error:
            return RepairResult(RepairStatus.STILL_BLOCKED, f"{action} 뒤 다시 대사 실패 — {error}")
        return _result(action, outcome)


def _result(action: str, outcome: ResumeOutcome) -> RepairResult:
    if outcome.matched:
        return RepairResult(RepairStatus.OK, f"{action} · 다시 대사 일치(정지 풀림)")
    return RepairResult(
        RepairStatus.STILL_BLOCKED, f"{action} · 다시 대사 불일치 — {outcome.detail}"
    )
