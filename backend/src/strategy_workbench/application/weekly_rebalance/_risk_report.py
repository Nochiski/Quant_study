"""S07 일일 위험 보고(2026-10-31 사용자 승인). 판단에 쓰지 않는 보고용이다.

장 마감 기록(대사가 맞은 날)의 보유·현금과 지금 낙폭 단계, 원장 가격 이력으로 보고 하나를 만들어
남긴다. 주문·리스크 판정·주문안은 바꾸지 않고, 실패해도 보고만 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance._risk import current_state
from strategy_workbench.application.weekly_rebalance.ports.outgoing.plans import PlanStore
from strategy_workbench.application.weekly_rebalance.ports.outgoing.records import (
    DayRecord,
    ExecutionLog,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.risk import (
    RiskStore,
    RiskUnavailableError,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.risk_report import (
    PriceHistorySource,
    RiskReportStore,
)
from strategy_workbench.domain.risk.facade.risk import (
    HISTORY_SCENARIOS,
    Holding,
    RiskReport,
    build_report,
)

# 가격 이력은 가장 이른 시나리오 시작일부터 읽는다(2020-01-22 — 홀드아웃 하한 2020-01-02 뒤다)
HISTORY_SINCE = min(window.start for window in HISTORY_SCENARIOS)


class RiskReportStatus(StrEnum):
    OK = "ok"
    NO_DAY = "no_day"  # 그날 장 마감 기록이 없다
    NOT_MATCHED = "not_matched"  # 대사가 어긋난 날이라 보유를 믿을 수 없다
    PRICES_UNAVAILABLE = "prices_unavailable"
    RISK_UNAVAILABLE = "risk_unavailable"  # 한도·리스크 기록을 읽지 못했거나 보고를 쓰지 못했다


@dataclass(frozen=True)
class RiskReportResult:
    status: RiskReportStatus
    report: RiskReport | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is RiskReportStatus.OK


class RiskReportService:
    def __init__(
        self,
        log: ExecutionLog,
        risk: RiskStore,
        reports: RiskReportStore,
        prices: PriceHistorySource,
        plans: PlanStore,
    ) -> None:
        self._log = log
        self._risk = risk
        self._reports = reports
        self._prices = prices
        self._plans = plans

    def build(self, day: date | None = None) -> RiskReportResult:
        """그날(생략하면 마지막) 장 마감 기록으로 보고를 만들어 남긴다."""
        try:
            days = self._log.days()
        except (OSError, ValueError, KeyError, TypeError) as error:
            return RiskReportResult(
                RiskReportStatus.NO_DAY, detail=f"day records unreadable — {error!r}"
            )
        record = next((d for d in reversed(days) if day is None or d.day == day), None)
        if record is None:
            return RiskReportResult(RiskReportStatus.NO_DAY, detail=f"no day record — day={day}")
        if not record.matched:
            return RiskReportResult(
                RiskReportStatus.NOT_MATCHED,
                detail=f"reconciliation mismatch — day={record.day} detail={record.detail}",
            )
        try:
            limits = self._risk.limits()
            # 그날 장 마감까지 접은 단계(지난 날을 다시 만들 때 뒤 기록을 넣지 않는다)
            before = datetime.combine(
                record.day + timedelta(days=1), time.min, record.recorded_at.tzinfo
            )
            state = current_state(self._risk, self._log, limits, before=before).state
        except RiskUnavailableError as error:
            return RiskReportResult(RiskReportStatus.RISK_UNAVAILABLE, detail=str(error))
        symbols = [symbol for symbol, shares, _ in record.holdings if shares]
        history = self._prices.history(symbols, HISTORY_SINCE)
        if not history.ok:
            return RiskReportResult(RiskReportStatus.PRICES_UNAVAILABLE, detail=history.detail)
        report = build_report(
            day=record.day,
            holdings=[Holding(symbol, shares, close) for symbol, shares, close in record.holdings],
            cash_krw=record.cash_krw,
            equity_krw=record.equity_krw,
            names=self._names(record),
            closes=history.closes,
            index=history.index,
            account=[(d.day, d.equity_krw) for d in days if d.matched and d.day <= record.day],
            state=state,
            limits=limits.drawdown,
        )
        try:
            self._reports.save_report(report)
        except RiskUnavailableError as error:
            return RiskReportResult(RiskReportStatus.RISK_UNAVAILABLE, report, str(error))
        return RiskReportResult(RiskReportStatus.OK, report, f"prices={history.source}")

    def saved(self, day: date) -> RiskReportResult:
        try:
            report = self._reports.report(day)
        except RiskUnavailableError as error:
            return RiskReportResult(RiskReportStatus.RISK_UNAVAILABLE, detail=str(error))
        if report is None:
            return RiskReportResult(RiskReportStatus.NO_DAY, detail=f"no risk report — day={day}")
        return RiskReportResult(RiskReportStatus.OK, report)

    def _names(self, record: DayRecord) -> dict[str, str]:
        """종목 이름은 그날 주문안 신호에서(보고에만 쓰여 없으면 비운다)."""
        try:
            plan = self._plans.load(record.plan_id)
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        return {} if plan is None else dict(plan.signal.names)
