"""홈 시장 블록 조회: 지수 카드·투자자별 순매수·섹터맵과 섹터 드릴다운.

카드마다 따로 실패한다 — 표 하나가 없어도 나머지 카드는 보인다. 화면 문장은 여기서 한글로
완성해 코드와 함께 보낸다. 연습용 원천(포트 없음)이면 숫자를 하나도 내지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TypeVar

from strategy_workbench.application.market_overview.ports.outgoing.market_data import (
    MarketDataPort,
    MarketTableMissingError,
)
from strategy_workbench.domain.market.facade.market import (
    FLOW_SESSIONS,
    INDEX_HISTORY_SESSIONS,
    MARKET_REFERENCE_INDICES,
    FlowMarket,
    IndexSummary,
    MarketFlow,
    SectorGroup,
    SectorMap,
    SecurityMove,
    build_sector_map,
    sector_securities,
    summarize_flow,
    summarize_index,
)

T = TypeVar("T")


class OverviewStatus(StrEnum):
    AVAILABLE = "available"
    PRACTICE_DATA = "practice_data"


class CardIssueCode(StrEnum):
    TABLE_MISSING = "table_missing"
    NO_ROWS = "no_rows"
    COVERAGE_LOW = "coverage_low"


PRACTICE_DATA_MESSAGE = (
    "시장 정보는 실제 데이터를 연결해야 보여요. 지금은 연습용 데이터로 백테스트만 돌아요."
)
TABLE_MISSING_MESSAGE = "이 데이터는 아직 받지 않았어요. DB 새로고침으로 받을 수 있어요."
NO_ROWS_MESSAGE = "기준 장의 데이터가 원장에 없어요."


@dataclass(frozen=True)
class CardIssue:
    code: CardIssueCode
    message: str


@dataclass(frozen=True)
class IndexCard:
    index_id: str
    index_name: str
    summary: IndexSummary | None
    issue: CardIssue | None


@dataclass(frozen=True)
class FlowCard:
    market: FlowMarket
    flow: MarketFlow | None
    issue: CardIssue | None


@dataclass(frozen=True)
class SectorCard:
    sector_map: SectorMap | None
    issue: CardIssue | None


@dataclass(frozen=True)
class MarketOverview:
    status: OverviewStatus
    message: str | None
    # 카드 날짜 중 가장 이른 장. 카드가 하나도 없으면 None.
    session: date | None
    indices: tuple[IndexCard, ...]
    flows: tuple[FlowCard, ...]
    sectors: SectorCard | None


@dataclass(frozen=True)
class SectorDetail:
    status: OverviewStatus
    message: str | None
    parent: SectorGroup | None
    group: SectorGroup | None
    session: date | None
    snapshot_date: date | None
    securities: tuple[SecurityMove, ...]


class UnknownSectorError(LookupError):
    def __init__(self, code: str) -> None:
        super().__init__(f"unknown sector — wics_l2_cd={code}")
        self.code = code


def _missing() -> CardIssue:
    return CardIssue(CardIssueCode.TABLE_MISSING, TABLE_MISSING_MESSAGE)


def _no_rows() -> CardIssue:
    return CardIssue(CardIssueCode.NO_ROWS, NO_ROWS_MESSAGE)


def _guarded(read: Callable[[], T]) -> T | CardIssue:
    try:
        return read()
    except MarketTableMissingError:
        return _missing()


class MarketOverviewService:
    def __init__(self, market_data: MarketDataPort | None) -> None:
        self._data = market_data

    def overview(self) -> MarketOverview:
        if self._data is None:
            return MarketOverview(
                OverviewStatus.PRACTICE_DATA, PRACTICE_DATA_MESSAGE, None, (), (), None
            )
        data = self._data
        session = data.latest_session()
        if session is None:
            issue = _no_rows()
            return MarketOverview(
                OverviewStatus.AVAILABLE,
                None,
                None,
                tuple(
                    IndexCard(i.index_id, i.index_name, None, issue)
                    for i in MARKET_REFERENCE_INDICES
                ),
                tuple(FlowCard(market, None, issue) for market in FlowMarket),
                SectorCard(None, issue),
            )
        indices = tuple(
            self._index_card(data, index.index_id, index.index_name, session)
            for index in MARKET_REFERENCE_INDICES
        )
        flows = self._flow_cards(data, session)
        sectors = self._sector_card(data, session)
        dates = [card.summary.session for card in indices if card.summary is not None]
        dates += [card.flow.session for card in flows if card.flow is not None]
        if sectors.sector_map is not None:
            dates.append(sectors.sector_map.session)
        return MarketOverview(
            OverviewStatus.AVAILABLE, None, min(dates, default=None), indices, flows, sectors
        )

    def sector(self, l2_code: str) -> SectorDetail:
        if self._data is None:
            return SectorDetail(
                OverviewStatus.PRACTICE_DATA, PRACTICE_DATA_MESSAGE, None, None, None, None, ()
            )
        session = self._data.latest_session()
        snapshot = None if session is None else self._data.sector_members(session)
        if snapshot is None:
            raise UnknownSectorError(l2_code)
        sector_map = build_sector_map(snapshot.snapshot_date, snapshot.session, snapshot.members)
        for parent in sector_map.parents:
            for child in parent.children:
                if child.code == l2_code:
                    return SectorDetail(
                        OverviewStatus.AVAILABLE,
                        None,
                        parent.group,
                        child,
                        snapshot.session,
                        snapshot.snapshot_date,
                        sector_securities(snapshot.members, l2_code),
                    )
        raise UnknownSectorError(l2_code)

    @staticmethod
    def _index_card(data: MarketDataPort, index_id: str, name: str, session: date) -> IndexCard:
        bars = _guarded(lambda: data.index_bars(name, session, INDEX_HISTORY_SESSIONS))
        if isinstance(bars, CardIssue):
            return IndexCard(index_id, name, None, bars)
        summary = summarize_index(index_id, name, bars)
        return IndexCard(index_id, name, summary, None if summary is not None else _no_rows())

    @staticmethod
    def _flow_cards(data: MarketDataPort, session: date) -> tuple[FlowCard, ...]:
        days = _guarded(lambda: data.flow_days(session, FLOW_SESSIONS))
        if isinstance(days, CardIssue):
            return tuple(FlowCard(market, None, days) for market in FlowMarket)
        cards: list[FlowCard] = []
        for market in FlowMarket:
            flow = summarize_flow(market, days)
            if flow is None:
                cards.append(FlowCard(market, None, _no_rows()))
            elif not flow.coverage.sufficient:
                cards.append(FlowCard(market, flow, _coverage_low(flow)))
            else:
                cards.append(FlowCard(market, flow, None))
        return tuple(cards)

    @staticmethod
    def _sector_card(data: MarketDataPort, session: date) -> SectorCard:
        snapshot = _guarded(lambda: data.sector_members(session))
        if isinstance(snapshot, CardIssue):
            return SectorCard(None, snapshot)
        if snapshot is None:
            return SectorCard(None, _no_rows())
        sector_map = build_sector_map(snapshot.snapshot_date, snapshot.session, snapshot.members)
        if not sector_map.parents:
            return SectorCard(None, _no_rows())
        return SectorCard(sector_map, None)


def _coverage_low(flow: MarketFlow) -> CardIssue:
    coverage = flow.coverage
    return CardIssue(
        CardIssueCode.COVERAGE_LOW,
        f"{coverage.session:%m-%d}은 수급이 측정된 종목이 시가총액 기준 "
        f"{coverage.mktcap_share:.0%}뿐이라 합계를 보여 드리지 않아요. "
        "다음 받기에서 채워질 수 있어요.",
    )
