"""Outgoing port: 주문안과 승인의 보관. 한 번 쓴 주문안·승인은 바꾸지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy_workbench.application.weekly_rebalance.ports.outgoing.account import AccountState
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePlan

__all__ = ["Approval", "PlanExistsError", "PlanRecord", "PlanStore", "SignalRecord"]


class PlanExistsError(Exception):
    """같은 주문안이나 승인이 이미 있다. 덮어쓰지 않는다."""


@dataclass(frozen=True)
class SignalRecord:
    """결과보다 먼저 남기는 신호. 순위 전체와 점수 파일 지문을 담는다."""

    model_id: str
    build_id: str
    signal_date: str
    scores_sha256: str
    prices_build_id: str
    ranks: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PlanRecord:
    plan_id: str
    created_at: datetime
    signal: SignalRecord
    account: AccountState
    plan: RebalancePlan


@dataclass(frozen=True)
class Approval:
    approved_at: datetime
    approver: str
    note: str = ""


class PlanStore(Protocol):
    def save(self, record: PlanRecord) -> None:
        """이미 있으면 `PlanExistsError`."""
        ...

    def load(self, plan_id: str) -> PlanRecord | None: ...

    def save_approval(self, plan_id: str, approval: Approval) -> None:
        """이미 승인했으면 `PlanExistsError`."""
        ...

    def approval(self, plan_id: str) -> Approval | None: ...

    def plan_ids(self) -> tuple[str, ...]:
        """남긴 주문안 ID를 오래된 것부터."""
        ...
