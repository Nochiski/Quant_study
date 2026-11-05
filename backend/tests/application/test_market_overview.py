"""홈 시장 블록 조회: 카드마다 따로 실패하고, 연습용 원천은 숫자를 하나도 내지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from strategy_workbench.application.market_overview.facade.overview import (
    CardIssueCode,
    MarketDataUnreadableError,
    MarketOverviewService,
    MarketTableMissingError,
    OverviewStatus,
    SectorSnapshot,
    UnknownSectorError,
)
from strategy_workbench.domain.market.facade.market import (
    FlowDay,
    FlowMarket,
    IndexBar,
    Investor,
    SectorMember,
)

SESSION = date(2026, 10, 30)
EOK = 100_000_000


def _flow_day(market: FlowMarket, session: date, measured_cap: int = 100) -> FlowDay:
    return FlowDay(
        session=session,
        market=market,
        grid_securities=10,
        measured_securities=10,
        grid_mktcap_krw=100,
        measured_mktcap_krw=measured_cap,
        amounts={Investor.FOREIGN: -EOK, Investor.INDIVIDUAL: EOK},
    )


def _member(ticker: str, l2: str) -> SectorMember:
    return SectorMember(ticker, ticker, "G45", "IT", l2, f"WICS {l2}", 10, 100.0, 110.0, True)


@dataclass
class FakeMarketData:
    """`MarketDataPort` 대역. `missing` 에 든 메서드는 표가 없는 것처럼, `unreadable` 은 읽다 실패한
    것처럼 답한다."""

    session: date | None = SESSION
    flow_session: date = SESSION
    kosdaq_cap: int = 100
    missing: set[str] = field(default_factory=set)
    unreadable: set[str] = field(default_factory=set)

    def _check(self, name: str, table: str) -> None:
        if name in self.missing:
            raise MarketTableMissingError(table)
        if name in self.unreadable:
            raise MarketDataUnreadableError(table, "IOException('No files found')")

    def latest_session(self) -> date | None:
        return self.session

    def index_bars(self, index_name: str, end: date, sessions: int) -> tuple[IndexBar, ...]:
        self._check("index", "index_daily")
        if index_name == "코스닥":
            return ()
        return tuple(
            IndexBar(end - timedelta(days=i), 2_000.0 + i, 1.0, 0.1, EOK) for i in range(3)
        )

    def flow_days(self, end: date, sessions: int) -> tuple[FlowDay, ...]:
        self._check("flow", "flow_daily")
        return (
            _flow_day(FlowMarket.KOSPI, self.flow_session),
            _flow_day(FlowMarket.KOSDAQ, self.flow_session, self.kosdaq_cap),
        )

    def sector_members(self, session: date) -> SectorSnapshot | None:
        self._check("sector", "sector_snapshot")
        return SectorSnapshot(
            snapshot_date=session - timedelta(days=7),
            session=session,
            members=(_member("A", "G4530"), _member("B", "G4510")),
        )


def test_practice_data_answers_no_market_numbers() -> None:
    overview = MarketOverviewService(None).overview()
    assert overview.status is OverviewStatus.PRACTICE_DATA
    assert overview.message is not None
    assert (overview.indices, overview.flows, overview.sectors, overview.session) == (
        (),
        (),
        None,
        None,
    )
    assert MarketOverviewService(None).sector("G4530").securities == ()


def test_each_card_fails_on_its_own_and_the_rest_still_answer() -> None:
    unreadable = MarketOverviewService(FakeMarketData(unreadable={"sector"})).overview()
    # 읽다 실패한 표도 그 카드만 사유로 답한다(500 이 아니다)
    assert unreadable.sectors is not None and unreadable.sectors.issue is not None
    assert unreadable.sectors.issue.code is CardIssueCode.UNREADABLE
    assert all(card.flow is not None for card in unreadable.flows)
    overview = MarketOverviewService(FakeMarketData(missing={"flow"})).overview()
    assert overview.status is OverviewStatus.AVAILABLE
    kospi, kosdaq = overview.indices
    assert kospi.summary is not None and kospi.issue is None
    # 행이 없는 지수는 사유로 답한다
    assert kosdaq.summary is None and kosdaq.issue is not None
    assert kosdaq.issue.code is CardIssueCode.NO_ROWS
    assert {card.issue.code for card in overview.flows if card.issue} == {
        CardIssueCode.TABLE_MISSING
    }
    assert overview.sectors is not None and overview.sectors.sector_map is not None


def test_low_coverage_keeps_the_flow_but_hides_totals_with_a_reason() -> None:
    overview = MarketOverviewService(FakeMarketData(kosdaq_cap=90)).overview()
    kospi, kosdaq = overview.flows
    assert kospi.issue is None and kospi.flow is not None and kospi.flow.totals
    assert kosdaq.issue is not None and kosdaq.issue.code is CardIssueCode.COVERAGE_LOW
    assert "90%" in kosdaq.issue.message
    assert kosdaq.flow is not None and (kosdaq.flow.totals, kosdaq.flow.days) == ((), ())


def test_the_header_session_is_the_earliest_card_date() -> None:
    late_flow = MarketOverviewService(FakeMarketData(flow_session=SESSION - timedelta(days=1)))
    assert late_flow.overview().session == SESSION - timedelta(days=1)
    assert MarketOverviewService(FakeMarketData()).overview().session == SESSION


def test_no_session_answers_every_card_with_no_rows() -> None:
    overview = MarketOverviewService(FakeMarketData(session=None)).overview()
    assert overview.session is None
    assert overview.sectors is not None and overview.sectors.issue is not None
    assert all(card.issue is not None for card in (*overview.indices, *overview.flows))


def test_sector_detail_lists_one_sector_and_rejects_an_unknown_code() -> None:
    service = MarketOverviewService(FakeMarketData())
    detail = service.sector("G4530")
    assert detail.group is not None and detail.group.name == "G4530"
    assert detail.parent is not None and detail.parent.code == "G45"
    assert [move.ticker for move in detail.securities] == ["A"]
    with pytest.raises(UnknownSectorError):
        service.sector("G9999")
