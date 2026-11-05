"""홈 시장 블록 API: 지수·투자자별 순매수·섹터맵과 섹터 드릴다운.

값과 문장은 `application.market_overview`가 정한다. 여기서는 응답 모양으로 옮기기만 한다 — 금액은
원 단위 정수, 등락은 퍼센트 실수, 날짜는 ISO. 요약 문장은 backend 가 한글로 완성하고, 표 줄의 주체·
시장 이름(로케일 문구)은 frontend 가 갖는다. 주체를 어느 묶음에 두는지는 응답의 `group` 이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from pydantic import Field

from strategy_workbench.application.market_overview.facade.overview import (
    CardIssue,
    CardIssueCode,
    FlowCard,
    IndexCard,
    MarketOverview,
    MarketOverviewService,
    OverviewStatus,
    SectorDetail,
    UnknownSectorError,
)
from strategy_workbench.domain.market.facade.market import (
    INVESTOR_GROUPS,
    FlowDay,
    FlowMarket,
    FlowPeriod,
    Investor,
    InvestorGroup,
    SectorGroup,
    SectorMap,
)

MARKET_OVERVIEW_PATH = "/api/v1/market/overview"
MARKET_SECTOR_PATH = "/api/v1/market/sectors/{wics_l2_cd}"
SECTOR_NOT_FOUND_MESSAGE = "그 업종을 지금 데이터에서 찾지 못했어요. 섹터맵을 다시 열어 주세요."


@dataclass(frozen=True)
class CardIssueBody:
    code: CardIssueCode
    message: str


@dataclass(frozen=True)
class IndexPointBody:
    session: date
    close: float


@dataclass(frozen=True)
class IndexSummaryBody:
    session: date
    close: float
    change: float | None
    change_pct: float | None
    value_krw: int | None
    high_close: Annotated[
        float, Field(description="선(history) 안의 종가 최고. 당일 고가가 아니다.")
    ]
    low_close: Annotated[
        float, Field(description="선(history) 안의 종가 최저. 당일 저가가 아니다.")
    ]
    history: list[IndexPointBody]


@dataclass(frozen=True)
class IndexCardBody:
    index_id: str
    index_name: str
    summary: IndexSummaryBody | None
    issue: CardIssueBody | None


@dataclass(frozen=True)
class InvestorAmountBody:
    investor: Investor
    amount_krw: Annotated[
        int, Field(description="순매수(산 금액 − 판 금액), 원. 반올림하지 않은 합.")
    ]
    group: Annotated[
        InvestorGroup,
        Field(
            description="화면 묶음: 막대 네 줄(main)·기관 세부(institution_part)·각주(footnote)."
        ),
    ]


@dataclass(frozen=True)
class FlowTotalBody:
    period: FlowPeriod
    start: date
    end: date
    sessions: int
    amounts: list[InvestorAmountBody]
    summary: Annotated[str, Field(description="누가 더 사고 팔았는지 한 문장(backend 가 완성).")]


@dataclass(frozen=True)
class FlowDayBody:
    session: date
    amounts: list[InvestorAmountBody]


@dataclass(frozen=True)
class FlowCoverageBody:
    session: date
    grid_securities: Annotated[int, Field(description="합산 대상 종목 수(ETF·우선주 제외).")]
    measured_securities: Annotated[int, Field(description="그중 값이 있는 종목 수.")]
    mktcap_share: Annotated[float, Field(description="측정 종목의 시가총액 비중(0~1).")]


@dataclass(frozen=True)
class FlowCardBody:
    market: FlowMarket
    coverage: FlowCoverageBody | None
    totals: list[FlowTotalBody]
    days: list[FlowDayBody]
    issue: CardIssueBody | None


@dataclass(frozen=True)
class SectorGroupBody:
    code: str
    name: str
    size_weight_krw: Annotated[
        float, Field(description="유동주식수 × 전날 종가의 합. 칸 크기와 등락 가중이 같은 값이다.")
    ]
    return_weight_krw: Annotated[
        float, Field(description="등락 계산에 든(거래 있는) 종목의 같은 가중 합.")
    ]
    return_pct: float | None
    securities: int
    untraded: Annotated[int, Field(description="그날 거래가 없어 등락에서 뺀 종목 수.")]


@dataclass(frozen=True)
class SectorParentBody:
    group: SectorGroupBody
    children: list[SectorGroupBody]


@dataclass(frozen=True)
class SectorMapBody:
    snapshot_date: Annotated[date, Field(description="업종 분류·유동주식수 스냅샷 날짜(주 1회).")]
    session: Annotated[date, Field(description="등락을 잰 장.")]
    securities: int
    untraded: int
    parents: list[SectorParentBody]


@dataclass(frozen=True)
class SectorCardBody:
    sector_map: SectorMapBody | None
    issue: CardIssueBody | None


@dataclass(frozen=True)
class MarketOverviewBody:
    status: OverviewStatus
    message: str | None
    session: Annotated[date | None, Field(description="카드 날짜 중 가장 이른 장.")]
    indices: list[IndexCardBody]
    flows: list[FlowCardBody]
    sectors: SectorCardBody | None


@dataclass(frozen=True)
class SecurityMoveBody:
    ticker: str
    name: str
    size_weight_krw: float
    return_pct: float | None


@dataclass(frozen=True)
class MarketSectorBody:
    status: OverviewStatus
    message: str | None
    parent: SectorGroupBody | None
    group: SectorGroupBody | None
    session: date | None
    snapshot_date: date | None
    securities: list[SecurityMoveBody]


@dataclass(frozen=True)
class MarketErrorDetail:
    code: Literal["market.sector.not_found"]
    message: str


@dataclass(frozen=True)
class MarketErrorResponse:
    detail: MarketErrorDetail


def _issue_body(issue: CardIssue | None) -> CardIssueBody | None:
    return None if issue is None else CardIssueBody(issue.code, issue.message)


def _amounts(amounts: Mapping[Investor, int]) -> list[InvestorAmountBody]:
    return [
        InvestorAmountBody(investor, amounts.get(investor, 0), INVESTOR_GROUPS[investor])
        for investor in Investor
    ]


def _index(card: IndexCard) -> IndexCardBody:
    summary = card.summary
    return IndexCardBody(
        index_id=card.index_id,
        index_name=card.index_name,
        issue=_issue_body(card.issue),
        summary=None
        if summary is None
        else IndexSummaryBody(
            session=summary.session,
            close=summary.close,
            change=summary.change,
            change_pct=summary.change_pct,
            value_krw=summary.value_krw,
            high_close=summary.high_close,
            low_close=summary.low_close,
            history=[IndexPointBody(point.session, point.close) for point in summary.history],
        ),
    )


def _day(day: FlowDay) -> FlowDayBody:
    return FlowDayBody(day.session, _amounts(day.amounts))


def _flow(card: FlowCard) -> FlowCardBody:
    flow = card.flow
    if flow is None:
        return FlowCardBody(card.market, None, [], [], _issue_body(card.issue))
    coverage = flow.coverage
    return FlowCardBody(
        market=card.market,
        coverage=FlowCoverageBody(
            coverage.session,
            coverage.grid_securities,
            coverage.measured_securities,
            coverage.mktcap_share,
        ),
        totals=[
            FlowTotalBody(
                total.period,
                total.start,
                total.end,
                total.sessions,
                _amounts(total.amounts),
                total.summary,
            )
            for total in flow.totals
        ],
        days=[_day(day) for day in flow.days],
        issue=_issue_body(card.issue),
    )


def _group(group: SectorGroup) -> SectorGroupBody:
    return SectorGroupBody(
        code=group.code,
        name=group.name,
        size_weight_krw=group.size_weight_krw,
        return_weight_krw=group.return_weight_krw,
        return_pct=group.return_pct,
        securities=group.securities,
        untraded=group.untraded,
    )


def _sector_map(sector_map: SectorMap) -> SectorMapBody:
    return SectorMapBody(
        snapshot_date=sector_map.snapshot_date,
        session=sector_map.session,
        securities=sector_map.securities,
        untraded=sector_map.untraded,
        parents=[
            SectorParentBody(_group(parent.group), [_group(child) for child in parent.children])
            for parent in sector_map.parents
        ],
    )


def _overview(overview: MarketOverview) -> MarketOverviewBody:
    sectors = overview.sectors
    return MarketOverviewBody(
        status=overview.status,
        message=overview.message,
        session=overview.session,
        indices=[_index(card) for card in overview.indices],
        flows=[_flow(card) for card in overview.flows],
        sectors=None
        if sectors is None
        else SectorCardBody(
            sector_map=None if sectors.sector_map is None else _sector_map(sectors.sector_map),
            issue=_issue_body(sectors.issue),
        ),
    )


def _sector(detail: SectorDetail) -> MarketSectorBody:
    return MarketSectorBody(
        status=detail.status,
        message=detail.message,
        parent=None if detail.parent is None else _group(detail.parent),
        group=None if detail.group is None else _group(detail.group),
        session=detail.session,
        snapshot_date=detail.snapshot_date,
        securities=[
            SecurityMoveBody(move.ticker, move.name, move.size_weight_krw, move.return_pct)
            for move in detail.securities
        ],
    )


def register_market_routes(app: FastAPI, service: MarketOverviewService) -> None:
    @app.get(MARKET_OVERVIEW_PATH, operation_id="getMarketOverview")
    def get_overview() -> MarketOverviewBody:
        return _overview(service.overview())

    @app.get(
        MARKET_SECTOR_PATH,
        operation_id="getMarketSector",
        responses={404: {"model": MarketErrorResponse}},
    )
    def get_sector(wics_l2_cd: str) -> MarketSectorBody:
        try:
            return _sector(service.sector(wics_l2_cd))
        except UnknownSectorError as error:
            raise HTTPException(
                404,
                detail={"code": "market.sector.not_found", "message": SECTOR_NOT_FOUND_MESSAGE},
            ) from error
