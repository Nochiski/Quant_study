"""주간 리밸런싱 주문안(S07). 신호를 읽고 목표를 계산해 주문 전에 남긴다.

주문안은 신호 날짜마다 하나이고, 한 번 쓰면 바꾸지 않는다. 그래서 결과보다 신호가 먼저 기록된다.
주문 전송은 이 서비스의 범위가 아니다(S07 실행기).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance.ports.outgoing.account import (
    AccountSource,
    AccountUnavailableError,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.plans import (
    Approval,
    PlanExistsError,
    PlanRecord,
    PlanStore,
    SignalRecord,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.signal import SignalSource
from strategy_workbench.domain.rebalance.facade.rebalance import (
    RebalancePolicy,
    plan_rebalance,
    signal_anomalies,
)

UNTRADED_REASON = "신호일 거래량 0(거래정지 가능)"


class PrepareStatus(StrEnum):
    OK = "ok"
    EXISTS = "exists"
    SIGNAL_UNAVAILABLE = "signal_unavailable"
    SIGNAL_SUSPECT = "signal_suspect"  # 직전 빌드와 견줘 데이터 누락이 의심된다. 사람이 확인한다
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    NOT_RECONCILED = "not_reconciled"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class PrepareResult:
    status: PrepareStatus
    record: PlanRecord | None = None
    detail: str = ""


class ApproveStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ALREADY_APPROVED = "already_approved"
    INVALID = "invalid"


@dataclass(frozen=True)
class ApproveResult:
    status: ApproveStatus
    approval: Approval | None = None
    detail: str = ""


def plan_id_for(signal_date: date) -> str:
    return f"weekly-{signal_date.isoformat()}"


class WeeklyRebalanceService:
    def __init__(
        self,
        signals: SignalSource,
        account: AccountSource,
        plans: PlanStore,
        *,
        now: Callable[[], datetime],
        policy: RebalancePolicy | None = None,
    ) -> None:
        self._signals = signals
        self._account = account
        self._plans = plans
        self._now = now
        self._policy = policy or RebalancePolicy()

    def prepare(self, signal_date: date, *, accept_anomalies: bool = False) -> PrepareResult:
        """그 날짜 신호로 주문안을 만들어 남긴다. 이미 있으면 다시 만들지 않고 그대로 돌려준다.

        직전 빌드와 견줘 데이터 누락이 의심되면 만들지 않는다. 사람이 확인하고
        `accept_anomalies`로 다시 부를 때만 사유를 함께 남기고 만든다.
        """
        plan_id = plan_id_for(signal_date)
        existing = self._plans.load(plan_id)
        if existing is not None:
            return PrepareResult(PrepareStatus.EXISTS, existing, "plan already recorded")
        read = self._signals.read(signal_date)
        if not read.ok or read.snapshot is None:
            return PrepareResult(PrepareStatus.SIGNAL_UNAVAILABLE, detail=read.detail)
        signal = read.snapshot
        anomalies = (
            ()
            if signal.previous_ranks is None
            else signal_anomalies(signal.ranks, signal.previous_ranks)
        )
        if anomalies and not accept_anomalies:
            return PrepareResult(
                PrepareStatus.SIGNAL_SUSPECT,
                detail=f"{'; '.join(anomalies)} — build_id={signal.build_id} "
                f"previous={signal.previous_build_id}",
            )
        try:
            account = self._account.current()
        except AccountUnavailableError as error:
            return PrepareResult(PrepareStatus.ACCOUNT_UNAVAILABLE, detail=str(error))
        if account.reconciled is False:
            return PrepareResult(PrepareStatus.NOT_RECONCILED, detail=account.detail)
        try:
            plan = plan_rebalance(
                self._policy,
                ranks=signal.ranks,
                holdings=account.holdings,
                prices=signal.closes_krw,
                cash_krw=account.cash_krw,
                blocked=dict.fromkeys(signal.untraded, UNTRADED_REASON),
                previous_ranks=signal.previous_ranks,
                missing_reasons=signal.exclusion_reasons,
            )
        except ValueError as error:
            return PrepareResult(
                PrepareStatus.INVALID_INPUT,
                detail=f"{error} — plan_id={plan_id} build_id={signal.build_id}",
            )
        record = PlanRecord(
            plan_id,
            self._now(),
            SignalRecord(
                signal.model_id,
                signal.build_id,
                signal.signal_date.isoformat(),
                signal.scores_sha256,
                signal.prices_build_id,
                tuple(sorted(signal.ranks.items(), key=lambda item: item[1])),
                signal.previous_build_id,
                anomalies,
                tuple(
                    (line.symbol, signal.names[line.symbol])
                    for line in plan.lines
                    if signal.names is not None and line.symbol in signal.names
                ),
            ),
            account,
            plan,
        )
        try:
            self._plans.save(record)
        except PlanExistsError:
            # 다른 실행이 먼저 남겼다. 먼저 남은 것이 정본이다.
            stored = self._plans.load(plan_id)
            return PrepareResult(PrepareStatus.EXISTS, stored, "plan recorded concurrently")
        return PrepareResult(PrepareStatus.OK, record)

    def approve(
        self, plan_id: str, approver: str, note: str = "", no_trade: tuple[str, ...] = ()
    ) -> ApproveResult:
        """승인을 한 번만 남긴다. `no_trade` 종목은 이번 주 매매에서 뺀다(주문안 종목만)."""
        record = self._plans.load(plan_id)
        if record is None:
            return ApproveResult(ApproveStatus.NOT_FOUND)
        existing = self._plans.approval(plan_id)
        if existing is not None:
            return ApproveResult(ApproveStatus.ALREADY_APPROVED, existing)
        planned = {line.symbol for line in record.plan.lines}
        unknown = sorted(set(no_trade) - planned)
        if unknown:
            return ApproveResult(
                ApproveStatus.INVALID,
                detail=f"not in the plan — plan_id={plan_id} symbols={unknown}",
            )
        approval = Approval(self._now(), approver, note, tuple(sorted(set(no_trade))))
        try:
            self._plans.save_approval(plan_id, approval)
        except PlanExistsError:
            return ApproveResult(ApproveStatus.ALREADY_APPROVED, self._plans.approval(plan_id))
        return ApproveResult(ApproveStatus.OK, approval)

    def plan(self, plan_id: str) -> tuple[PlanRecord | None, Approval | None]:
        return self._plans.load(plan_id), self._plans.approval(plan_id)
