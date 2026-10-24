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
from strategy_workbench.application.weekly_rebalance.ports.outgoing.signal import (
    SignalBasis,
    SignalRead,
    SignalSource,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    RebalancePolicy,
    plan_rebalance,
    signal_anomalies,
    week_targets,
)

UNTRADED_REASON = "신호일 거래량 0(거래정지 가능)"
EARLIER_CLOSE_REASON = "신호일 종가 없음(거래정지 등) — {day} 종가로 평가"


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
    """`dropped_buys`는 뺀 보유 종목이 자리를 차지해 이번 주 함께 하지 않는 새 매수다."""

    status: ApproveStatus
    approval: Approval | None = None
    detail: str = ""
    dropped_buys: tuple[str, ...] = ()


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

    def prepare(
        self,
        signal_date: date,
        *,
        basis: SignalBasis = SignalBasis.EVENING,
        accept_anomalies: bool = False,
    ) -> PrepareResult:
        """그 날짜 `basis` 빌드 신호로 주문안을 만들어 남긴다. 이미 있으면 그대로 돌려준다.

        직전 빌드와 견줘 데이터 누락이 의심되면 만들지 않는다. 사람이 확인하고
        `accept_anomalies`로 다시 부를 때만 사유를 함께 남기고 만든다.
        """
        plan_id = plan_id_for(signal_date)
        existing = self._plans.load(plan_id)
        if existing is not None:
            return PrepareResult(PrepareStatus.EXISTS, existing, "plan already recorded")
        read = self._signals.read(signal_date, basis)
        if not read.ok or read.snapshot is None:
            return PrepareResult(PrepareStatus.SIGNAL_UNAVAILABLE, detail=read.detail)
        signal = read.snapshot
        anomalies = (
            ()
            if signal.previous_ranks is None
            else signal_anomalies(signal.ranks, signal.previous_ranks, top=self._policy.keep_rank)
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
        prices = dict(signal.closes_krw)
        blocked = dict.fromkeys(signal.untraded, UNTRADED_REASON)
        # 신호일 가격 행이 없는 보유 종목은 그 전 마지막 종가로 평가하고 매매하지 않는다(확인 표시).
        earlier = {
            symbol: close
            for symbol, close in (signal.earlier_closes or {}).items()
            if symbol in account.holdings and symbol not in prices
        }
        for symbol, close in earlier.items():
            prices[symbol] = close.close_krw
            blocked[symbol] = EARLIER_CLOSE_REASON.format(day=close.day.isoformat())
        try:
            plan = plan_rebalance(
                self._policy,
                ranks=signal.ranks,
                holdings=account.holdings,
                prices=prices,
                cash_krw=account.cash_krw,
                blocked=blocked,
                previous_ranks=signal.previous_ranks,
                missing_reasons=signal.exclusion_reasons,
                stale_prices=frozenset(earlier),
            )
        except ValueError as error:
            return PrepareResult(
                PrepareStatus.INVALID_INPUT,
                detail=f"{error} — plan_id={plan_id} build_id={signal.build_id}",
            )
        skipped = (
            None
            if signal.previous_ranks is not None
            else "직전 빌드 없음"
            if signal.previous_build_id is None
            else f"직전 빌드 {signal.previous_build_id} 순위를 쓸 수 없음(읽기 실패)"
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
                skipped,
                basis,
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
        dropped = week_targets(record.plan, approval.no_trade).dropped_buys
        return ApproveResult(ApproveStatus.OK, approval, dropped_buys=dropped)

    def signal_status(
        self, signal_date: date, basis: SignalBasis = SignalBasis.EVENING
    ) -> SignalRead:
        """예약 실행이 모델 빌드를 기다릴 때 묻는다. 준비 판정은 신호 어댑터 한 곳이 한다."""
        return self._signals.read(signal_date, basis)

    def plan(self, plan_id: str) -> tuple[PlanRecord | None, Approval | None]:
        return self._plans.load(plan_id), self._plans.approval(plan_id)
