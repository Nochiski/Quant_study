"""주간 리밸런싱 주문안(S07). 신호를 읽고 목표를 계산해 주문 전에 남긴다.

주문안은 신호 날짜마다 하나이고, 한 번 쓰면 바꾸지 않는다. 그래서 결과보다 신호가 먼저 기록된다.
주문안과 함께 리스크 판정(낙폭 단계와 한도로 줄인 목표)을 한 번 남긴다. 판정을 못 하면 주문안도
남기지 않는다(2026-10-26 리스크 가드). 주문 전송은 이 서비스의 범위가 아니다(S07 실행기).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance._risk import current_state
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
from strategy_workbench.application.weekly_rebalance.ports.outgoing.records import (
    ExecutionLog,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.risk import (
    RiskStore,
    RiskUnavailableError,
    RiskVerdict,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.signal import (
    SignalBasis,
    SignalRead,
    SignalSnapshot,
    SignalSource,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    RebalancePolicy,
    plan_rebalance,
    scaled_targets,
    signal_anomalies,
    week_targets,
)
from strategy_workbench.domain.risk.facade.risk import exposure_bp, guard_targets

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
    RISK_UNAVAILABLE = "risk_unavailable"  # 한도·기록을 읽지 못해 리스크 판정을 못 했다


@dataclass(frozen=True)
class PrepareResult:
    status: PrepareStatus
    record: PlanRecord | None = None
    detail: str = ""
    verdict: RiskVerdict | None = None


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
        risk: RiskStore,
        log: ExecutionLog,
        now: Callable[[], datetime],
        policy: RebalancePolicy | None = None,
    ) -> None:
        self._signals = signals
        self._account = account
        self._plans = plans
        self._risk = risk
        self._log = log
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
            return self._existing(existing, signal_date, basis, "plan already recorded")
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
            verdict = self._verdict(record, signal)
        except (RiskUnavailableError, ValueError) as error:
            return PrepareResult(
                PrepareStatus.RISK_UNAVAILABLE, detail=f"{error} — plan_id={plan_id}"
            )
        try:
            self._plans.save(record)
        except PlanExistsError:
            # 다른 실행이 먼저 남겼다. 먼저 남은 것이 정본이다.
            stored = self._plans.load(plan_id)
            if stored is None:
                raise
            return self._existing(stored, signal_date, basis, "plan recorded concurrently")
        return self._save_verdict(PrepareStatus.OK, record, verdict)

    def _existing(
        self, record: PlanRecord, signal_date: date, basis: SignalBasis, detail: str
    ) -> PrepareResult:
        """이미 남은 주문안. 판정이 없으면(판정 저장 전에 멈춤) 같은 빌드일 때만 다시 판정한다
        (리뷰 P2-2). 판정을 못 하면 주문안이 있어도 만들지 못한 것으로 알린다."""
        try:
            stored = self._risk.verdict(record.plan_id)
        except RiskUnavailableError as error:
            return PrepareResult(PrepareStatus.RISK_UNAVAILABLE, record, str(error))
        if stored is not None:
            return self._bound(PrepareStatus.EXISTS, record, stored, detail)
        read = self._signals.read(signal_date, basis)
        if not read.ok or read.snapshot is None:
            return PrepareResult(
                PrepareStatus.RISK_UNAVAILABLE,
                record,
                f"plan has no risk verdict and the signal is unreadable — {read.detail}",
            )
        if read.snapshot.build_id != record.signal.build_id:
            return PrepareResult(
                PrepareStatus.RISK_UNAVAILABLE,
                record,
                f"plan has no risk verdict and the build moved — plan={record.signal.build_id} "
                f"signal={read.snapshot.build_id}",
            )
        try:
            verdict = self._verdict(record, read.snapshot)
        except (RiskUnavailableError, ValueError) as error:
            return PrepareResult(PrepareStatus.RISK_UNAVAILABLE, record, str(error))
        return self._save_verdict(PrepareStatus.EXISTS, record, verdict)

    def _save_verdict(
        self, status: PrepareStatus, record: PlanRecord, verdict: RiskVerdict
    ) -> PrepareResult:
        try:
            self._risk.save_verdict(verdict)
        except RiskUnavailableError as error:
            # 주문안은 남았다. 다시 부르면 같은 빌드로 판정을 다시 만든다
            return PrepareResult(PrepareStatus.RISK_UNAVAILABLE, record, str(error))
        except PlanExistsError:
            stored = self._risk.verdict(record.plan_id)
            if stored is None:
                raise
            return self._bound(status, record, stored, "verdict recorded concurrently")
        return PrepareResult(status, record, verdict=verdict)

    @staticmethod
    def _bound(
        status: PrepareStatus, record: PlanRecord, verdict: RiskVerdict, detail: str
    ) -> PrepareResult:
        """판정이 이 주문안의 것인가. 주문안을 지우고 다시 만들면 남은 옛 판정과 갈린다
        (리뷰 P3-2)."""
        if verdict.plan_created_at != record.created_at:
            return PrepareResult(
                PrepareStatus.RISK_UNAVAILABLE,
                record,
                f"risk verdict belongs to another plan — plan_id={record.plan_id} "
                f"plan_created_at={record.created_at.isoformat()} "
                f"verdict_plan_created_at={verdict.plan_created_at}",
            )
        return PrepareResult(status, record, detail, verdict)

    def _verdict(self, record: PlanRecord, signal: SignalSnapshot) -> RiskVerdict:
        """지금 낙폭 단계와 신호일 자료로 주문안 목표를 줄인다. 앞 주문안 뒤 찾은 위반을 싣는다.

        신호일 장 마감 기록이 없으면(close 실패·대사 불일치) 주문안 총자산(신호일 종가 평가)을
        그날 장 마감으로 접는다(리뷰 P1-1).
        """
        plan = record.plan
        signal_day = date.fromisoformat(record.signal.signal_date)
        limits = self._risk.limits()
        judged = current_state(
            self._risk,
            self._log,
            limits,
            close_on=(signal_day, plan.equity_krw, record.created_at.tzinfo),
        )
        level = judged.state.level
        guarded = guard_targets(
            targets=plan.target_shares(),
            # 노출을 줄이는 단계면 주문안을 그 비율의 자본으로 다시 잰다
            # (밴드·막힌 보유는 주문안 규칙)
            exposed=scaled_targets(plan, exposure_bp(level, limits.drawdown)),
            holdings=record.account.holdings,
            prices={
                line.symbol: line.reference_price_krw
                for line in plan.lines
                if line.reference_price_krw is not None
            },
            facts=signal.facts,
            equity_krw=plan.equity_krw,
            level=level,
            limits=limits,
        )
        earlier = [item for item in self._plans.plan_ids() if item < record.plan_id]
        previous = self._plans.load(earlier[-1]) if earlier else None
        since = None if previous is None else previous.created_at
        violations = tuple(
            violation
            for day in self._risk.days()
            if day.review is not None and (since is None or day.recorded_at > since)
            for violation in day.review.violations
        )
        return RiskVerdict(
            record.plan_id,
            self._now(),
            limits.version,
            limits.sha256,
            judged.state,
            guarded,
            violations,
            plan_created_at=record.created_at,
            close_from_plan=judged.from_plan,
        )

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

    def risk_verdict(self, plan_id: str) -> RiskVerdict | None:
        """주문안의 리스크 판정. 없으면 None, 읽지 못하면 `RiskUnavailableError`(실행기는 두
        경우 모두 주문하지 않는다)."""
        return self._risk.verdict(plan_id)
