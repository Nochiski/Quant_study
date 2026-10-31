"""S07 일일 위험 보고 서비스. 기록·가격 이력·보고 저장소는 메모리 대역이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    DayRecord,
    PriceHistory,
    PriceHistoryStatus,
    RiskReportService,
    RiskReportStatus,
    RiskUnavailableError,
)
from strategy_workbench.domain.risk.facade.risk import RiskLevel, RiskReport
from tests.application.test_weekly_rebalance import KST, Log, Plans, Risk
from tests.domain.test_risk import LIMITS

START = date(2026, 10, 26)


@dataclass
class Prices:
    status: PriceHistoryStatus = PriceHistoryStatus.OK
    asked: list[tuple[tuple[str, ...], date]] = field(default_factory=list)

    def history(self, symbols: Sequence[str], since: date) -> PriceHistory:
        self.asked.append((tuple(symbols), since))
        if self.status is not PriceHistoryStatus.OK:
            return PriceHistory(self.status, detail="price_adj_daily unreadable")
        days = [START + timedelta(days=i) for i in range(5)]
        closes = {s: {d: 100.0 + i for i, d in enumerate(days)} for s in symbols}
        return PriceHistory(
            self.status, closes, {d: 1_000.0 + i for i, d in enumerate(days)}, "m_1"
        )


@dataclass
class Reports:
    saved_: dict[date, RiskReport] = field(default_factory=dict)
    broken: bool = False

    def save_report(self, report: RiskReport) -> None:
        if self.broken:
            raise RiskUnavailableError("risk report not written — file=report.json")
        self.saved_[report.day] = report

    def report(self, day: date) -> RiskReport | None:
        return self.saved_.get(day)


def record(day: date, equity_krw: int, *, matched: bool = True) -> DayRecord:
    """현금 1천만 원 + A 1,000주 + 빈 줄 하나(주식 0주는 보유가 아니다)."""
    at = datetime.combine(day, datetime.min.time(), KST) + timedelta(hours=15, minutes=40)
    price = (equity_krw - 10_000_000) // 1_000
    holdings = (("A", 1_000, price), ("Z", 0, 1))
    return DayRecord(
        day, at, "weekly-2026-10-23", matched, "", 10_000_000, holdings, equity_krw, 0, ()
    )


def build(
    *records: DayRecord, prices: Prices | None = None, reports: Reports | None = None
) -> tuple[RiskReportService, Prices, Reports]:
    log = Log()
    for item in records:
        log.save_day(item)
    prices, reports = prices or Prices(), reports or Reports()
    return RiskReportService(log, Risk(limits_=LIMITS), reports, prices, Plans()), prices, reports


def test_report_uses_the_last_close_and_the_guard_state_of_that_day() -> None:
    service, prices, reports = build(
        record(START, 95_000_000), record(START + timedelta(days=1), 88_000_000)
    )
    result = service.build()
    assert result.status is RiskReportStatus.OK and result.report is not None
    report = result.report
    assert report.day == START + timedelta(days=1)
    assert (report.state.level, report.state.loss_bp) == (RiskLevel.WARN, 1_200)
    assert report.account_period is not None
    assert report.account_period.account_return == 88_000_000 / 95_000_000 - 1
    assert reports.saved_[report.day] is report
    # 보유(0주 줄 제외)만, 가장 이른 시나리오 시작일부터 묻는다
    assert prices.asked == [(("A",), date(2020, 1, 22))]
    assert result.detail == "prices=m_1"


def test_an_earlier_day_does_not_fold_later_closes() -> None:
    service, _, _ = build(record(START, 85_000_000), record(START + timedelta(days=1), 99_000_000))
    report = service.build(START).report
    assert report is not None
    assert (report.state.level, report.state.last_day) == (RiskLevel.REDUCE, START)


def test_mismatched_close_leaves_no_report() -> None:
    service, prices, reports = build(record(START, 95_000_000, matched=False))
    result = service.build()
    assert result.status is RiskReportStatus.NOT_MATCHED
    assert (prices.asked, reports.saved_) == ([], {})


def test_missing_day_and_unreadable_prices_leave_no_report() -> None:
    service, _, _ = build(record(START, 95_000_000))
    assert service.build(START + timedelta(days=3)).status is RiskReportStatus.NO_DAY
    service, _, reports = build(
        record(START, 95_000_000), prices=Prices(PriceHistoryStatus.UNAVAILABLE)
    )
    result = service.build()
    assert result.status is RiskReportStatus.PRICES_UNAVAILABLE
    assert result.detail == "price_adj_daily unreadable" and reports.saved_ == {}


def test_unwritable_report_is_a_failed_result_and_saved_reads_back() -> None:
    service, _, _ = build(record(START, 95_000_000), reports=Reports(broken=True))
    result = service.build()
    assert result.status is RiskReportStatus.RISK_UNAVAILABLE and result.report is not None
    service, _, _ = build(record(START, 95_000_000))
    made = service.build().report
    assert service.saved(START).report is made
    assert service.saved(START + timedelta(days=1)).status is RiskReportStatus.NO_DAY
