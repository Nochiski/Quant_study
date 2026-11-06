"""투자자별 순매수: 시장·기간별 합, 측정 비중, 한 줄 요약 문장.

원장 `flow_daily`는 종목별 값이다. 어댑터가 (세션, 시장)별로 측정 셀만 더한 행을 주면, 여기서
기간(당일·5세션·20세션) 합과 측정 비중 판정, 요약 문장을 정한다. 합은 원 단위 원값으로 더하고
반올림하지 않는다 — 억·조 표기는 화면이 한 번만 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from ._won import won_words


class Investor(StrEnum):
    INDIVIDUAL = "individual"
    FOREIGN = "foreign"
    INSTITUTION = "institution"
    OTHER_CORPORATION = "other_corporation"
    # 내외국인(외국인 등록 없이 국내 거주하는 외국인 등). 화면에는 각주로만 보인다.
    RESIDENT_FOREIGN = "resident_foreign"
    # 기관 세부 8주체. 합이 기관(`INSTITUTION`)이다.
    FINANCIAL_INVESTMENT = "financial_investment"
    PENSION = "pension"
    INVESTMENT_TRUST = "investment_trust"
    PRIVATE_EQUITY = "private_equity"
    INSURANCE = "insurance"
    BANK = "bank"
    OTHER_FINANCIAL = "other_financial"
    GOVERNMENT = "government"


class InvestorGroup(StrEnum):
    """주체의 묶음: 네 주체(합하면 내외국인과 함께 0)·기관 세부(합이 기관)·그 밖(내외국인).

    응답이 주체마다 싣는다 — 화면은 묶음을 다시 적지 않는다.
    """

    MAIN = "main"
    INSTITUTION_PART = "institution_part"
    FOOTNOTE = "footnote"


# 화면 네 줄과 요약 문장이 보는 주체. 모두 더하면(내외국인 포함) 0이다.
MAIN_INVESTORS = (
    Investor.INDIVIDUAL,
    Investor.FOREIGN,
    Investor.INSTITUTION,
    Investor.OTHER_CORPORATION,
)
INSTITUTION_PARTS = (
    Investor.FINANCIAL_INVESTMENT,
    Investor.PENSION,
    Investor.INVESTMENT_TRUST,
    Investor.PRIVATE_EQUITY,
    Investor.INSURANCE,
    Investor.BANK,
    Investor.OTHER_FINANCIAL,
    Investor.GOVERNMENT,
)
# 요약 문장(backend 가 완성하는 한글 문장)에 쓰는 이름. 표 줄 이름은 frontend 문구
# (`market.investor.*`)가 갖는다 — 같은 낱말인지는 `tests/domain/test_market.py` 가 그 ko 문구와
# 대조한다. 시장 이름도 같다(`market.market.*`).
SUMMARY_LABELS: Mapping[Investor, str] = {
    Investor.INDIVIDUAL: "개인",
    Investor.FOREIGN: "외국인",
    Investor.INSTITUTION: "기관",
    Investor.OTHER_CORPORATION: "기타법인",
}


# 주체마다 묶음 하나 — 주체를 더하면 여기에 함께 적는다(테스트가 전수를 본다).
INVESTOR_GROUPS: Mapping[Investor, InvestorGroup] = {
    **dict.fromkeys(MAIN_INVESTORS, InvestorGroup.MAIN),
    **dict.fromkeys(INSTITUTION_PARTS, InvestorGroup.INSTITUTION_PART),
    Investor.RESIDENT_FOREIGN: InvestorGroup.FOOTNOTE,
}


class FlowMarket(StrEnum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


MARKET_LABELS: Mapping[FlowMarket, str] = {FlowMarket.KOSPI: "코스피", FlowMarket.KOSDAQ: "코스닥"}


class FlowPeriod(StrEnum):
    DAY = "day"
    WEEK = "five_sessions"
    MONTH = "twenty_sessions"


PERIOD_SESSIONS: Mapping[FlowPeriod, int] = {
    FlowPeriod.DAY: 1,
    FlowPeriod.WEEK: 5,
    FlowPeriod.MONTH: 20,
}
FLOW_SESSIONS = max(PERIOD_SESSIONS.values())
# 기준 장에서 측정 셀의 시가총액 비중이 이보다 낮으면 합계를 보이지 않는다. 빈 셀을 0으로 치지
# 않으므로, 비중이 낮은 날의 합은 시장 전체 흐름을 말하지 못한다.
COVERAGE_MIN_SHARE = 0.95


@dataclass(frozen=True)
class FlowDay:
    """한 세션·한 시장에서 측정 셀만 더한 값과 격자 크기."""

    session: date
    market: FlowMarket
    grid_securities: int
    measured_securities: int
    grid_mktcap_krw: int
    measured_mktcap_krw: int
    amounts: Mapping[Investor, int]


@dataclass(frozen=True)
class FlowTotal:
    period: FlowPeriod
    start: date
    end: date
    sessions: int
    amounts: Mapping[Investor, int]
    summary: str


@dataclass(frozen=True)
class FlowCoverage:
    session: date
    grid_securities: int
    measured_securities: int
    mktcap_share: float

    @property
    def sufficient(self) -> bool:
        return self.mktcap_share >= COVERAGE_MIN_SHARE


@dataclass(frozen=True)
class MarketFlow:
    market: FlowMarket
    session: date
    coverage: FlowCoverage
    # 측정 비중이 모자라면 둘 다 비어 있다(합도 날짜별 값도 보이지 않는다).
    totals: tuple[FlowTotal, ...]
    days: tuple[FlowDay, ...]


def summarize_flow(market: FlowMarket, days: Sequence[FlowDay]) -> MarketFlow | None:
    """그 시장의 최근 세션 행들로 기간 합을 만든다. 행이 없으면 None.

    기간은 행이 있는 세션을 뒤에서부터 센다 — 어댑터가 거래일 달력의 최근 세션만 준다.
    """
    ordered = sorted((day for day in days if day.market is market), key=lambda day: day.session)
    if not ordered:
        return None
    last = ordered[-1]
    share = last.measured_mktcap_krw / last.grid_mktcap_krw if last.grid_mktcap_krw > 0 else 0.0
    coverage = FlowCoverage(
        session=last.session,
        grid_securities=last.grid_securities,
        measured_securities=last.measured_securities,
        mktcap_share=share,
    )
    if not coverage.sufficient:
        return MarketFlow(market, last.session, coverage, (), ())
    totals = tuple(
        _total(market, period, ordered[-count:]) for period, count in PERIOD_SESSIONS.items()
    )
    return MarketFlow(market, last.session, coverage, totals, tuple(ordered))


def _total(market: FlowMarket, period: FlowPeriod, days: Sequence[FlowDay]) -> FlowTotal:
    amounts = {investor: sum(day.amounts.get(investor, 0) for day in days) for investor in Investor}
    return FlowTotal(
        period=period,
        start=days[0].session,
        end=days[-1].session,
        sessions=len(days),
        amounts=amounts,
        summary=flow_summary(market, period, days[-1].session, amounts),
    )


def flow_summary(
    market: FlowMarket, period: FlowPeriod, end: date, amounts: Mapping[Investor, int]
) -> str:
    """ "코스피 10-30: 외국인이 1조 4,542억 원어치를 가장 많이 더 팔았고, 개인·기관이 더 샀어요."

    판단(신호·전망) 문장은 쓰지 않고 누가 더 사고 팔았는지만 말한다. 주체 이름은 모두 받침으로
    끝나 조사는 '이'로 고정한다.
    """
    when = (
        end.strftime("%m-%d")
        if period is FlowPeriod.DAY
        else f"최근 {PERIOD_SESSIONS[period]}거래일"
    )
    head = f"{MARKET_LABELS[market]} {when}: "
    values = {investor: amounts.get(investor, 0) for investor in MAIN_INVESTORS}
    buyers = [SUMMARY_LABELS[investor] for investor, value in values.items() if value > 0]
    seller = min(values, key=lambda investor: values[investor])
    if values[seller] >= 0:
        if not buyers:
            return head + "주체별로 더 사거나 판 금액이 없어요."
        top = max(values, key=lambda investor: values[investor])
        return (
            head + f"{SUMMARY_LABELS[top]}이 {won_words(values[top])} 원어치를 가장 많이 더 샀어요."
        )
    sold = f"{SUMMARY_LABELS[seller]}이 {won_words(-values[seller])} 원어치를 가장 많이 더 팔았고"
    if not buyers:
        return head + sold.removesuffix("고") + "어요."
    return head + f"{sold}, {'·'.join(buyers)}이 더 샀어요."
