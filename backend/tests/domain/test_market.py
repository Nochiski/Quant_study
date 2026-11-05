"""홈 시장 블록의 순수 계산: 지수 카드, 순매수 기간 합·측정 비중·요약, 섹터 가중."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from strategy_workbench.domain.market._flow import MARKET_LABELS, SUMMARY_LABELS
from strategy_workbench.domain.market.facade.market import (
    INDEX_HISTORY_SESSIONS,
    FlowDay,
    FlowMarket,
    FlowPeriod,
    IndexBar,
    Investor,
    SectorMember,
    build_sector_map,
    flow_summary,
    sector_securities,
    summarize_flow,
    summarize_index,
    won_words,
)

REPO = Path(__file__).resolve().parents[3]
EOK = 100_000_000
D0 = date(2026, 10, 1)


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        (14_542 * EOK, "1조 4,542억"),
        (10_000 * EOK, "1조"),
        (179_349 * EOK, "17조 9,349억"),
        (4_632 * EOK + 40_000_000, "4,632억"),
        (30_000_000, "1억 미만"),
        (-581 * EOK, "581억"),
    ],
)
def test_won_words_rounds_to_eok_and_reads_jo(amount: int, words: str) -> None:
    assert won_words(amount) == words


def test_index_card_keeps_the_last_sessions_and_reads_close_extremes() -> None:
    bars = [
        IndexBar(D0 + timedelta(days=i), 100.0 + i, 1.0, 0.5, 10 * EOK)
        for i in range(INDEX_HISTORY_SESSIONS + 5)
    ]
    summary = summarize_index("kospi", "코스피", list(reversed(bars)))
    assert summary is not None
    assert len(summary.history) == INDEX_HISTORY_SESSIONS
    assert summary.history[0].session == bars[5].session
    assert (summary.session, summary.close) == (bars[-1].session, bars[-1].close)
    assert (summary.low_close, summary.high_close) == (105.0, bars[-1].close)
    assert summarize_index("kospi", "코스피", []) is None


def _day(
    offset: int,
    amounts: dict[Investor, int],
    *,
    market: FlowMarket = FlowMarket.KOSPI,
    measured_cap: int = 99,
) -> FlowDay:
    return FlowDay(
        session=D0 + timedelta(days=offset),
        market=market,
        grid_securities=10,
        measured_securities=9,
        grid_mktcap_krw=100,
        measured_mktcap_krw=measured_cap,
        amounts=amounts,
    )


def test_flow_periods_sum_raw_values_over_the_latest_sessions() -> None:
    days = [
        _day(i, {Investor.FOREIGN: -(i + 1) * EOK + 1, Investor.INDIVIDUAL: (i + 1) * EOK})
        for i in range(25)
    ]
    other_market = _day(30, {Investor.FOREIGN: 999 * EOK}, market=FlowMarket.KOSDAQ)
    flow = summarize_flow(FlowMarket.KOSPI, [*days, other_market])
    assert flow is not None
    assert flow.session == days[-1].session
    by_period = {total.period: total for total in flow.totals}
    day, week, month = (
        by_period[FlowPeriod.DAY],
        by_period[FlowPeriod.WEEK],
        by_period[FlowPeriod.MONTH],
    )
    assert (day.sessions, week.sessions, month.sessions) == (1, 5, 20)
    assert week.start == days[-5].session and week.end == days[-1].session
    # 원값을 더한다 — 일별로 억 반올림한 뒤 더하지 않는다(1원씩 5번 = 5원)
    assert week.amounts[Investor.FOREIGN] == -sum(range(21, 26)) * EOK + 5
    assert month.amounts[Investor.INDIVIDUAL] == sum(range(6, 26)) * EOK
    assert day.amounts[Investor.INSTITUTION] == 0


def test_flow_hides_totals_when_the_measured_market_cap_share_is_low() -> None:
    flow = summarize_flow(FlowMarket.KOSPI, [_day(0, {Investor.FOREIGN: EOK}, measured_cap=94)])
    assert flow is not None
    assert not flow.coverage.sufficient
    # 합도 날짜별 값도 보이지 않는다
    assert (flow.totals, flow.days) == ((), ())
    assert summarize_flow(FlowMarket.KOSDAQ, [_day(0, {})]) is None


def test_flow_summary_names_the_biggest_seller_and_every_buyer() -> None:
    amounts = {
        Investor.INDIVIDUAL: 4_632 * EOK,
        Investor.FOREIGN: -14_542 * EOK,
        Investor.INSTITUTION: 7_231 * EOK,
        Investor.OTHER_CORPORATION: 2_681 * EOK,
    }
    assert flow_summary(FlowMarket.KOSPI, FlowPeriod.DAY, date(2026, 10, 30), amounts) == (
        "코스피 10-30: 외국인이 1조 4,542억 원어치를 가장 많이 더 팔았고, "
        "개인·기관·기타법인이 더 샀어요."
    )
    assert flow_summary(FlowMarket.KOSDAQ, FlowPeriod.WEEK, date(2026, 10, 30), {}) == (
        "코스닥 최근 5거래일: 주체별로 더 사거나 판 금액이 없어요."
    )
    only_sell = {Investor.FOREIGN: -EOK}
    assert flow_summary(FlowMarket.KOSPI, FlowPeriod.MONTH, D0, only_sell) == (
        "코스피 최근 20거래일: 외국인이 1억 원어치를 가장 많이 더 팔았어요."
    )


def _member(
    ticker: str,
    l2: str,
    *,
    shares: int,
    prev: float | None,
    close: float | None,
    traded: bool = True,
    l1: str = "G45",
) -> SectorMember:
    return SectorMember(
        ticker=ticker,
        name=f"종목{ticker}",
        l1_code=l1,
        l1_name="IT",
        l2_code=l2,
        l2_name=f"WICS {l2}이름",
        float_shares=shares,
        prev_close=prev,
        close=close,
        traded=traded,
    )


def test_sector_weights_size_and_return_by_float_shares_times_previous_close() -> None:
    members = [
        # 가중 100×10=1,000, +10%
        _member("A", "S1", shares=100, prev=10.0, close=11.0),
        # 가중 300×10=3,000, −10%
        _member("B", "S1", shares=300, prev=10.0, close=9.0),
        # 거래정지: 크기에는 넣고 등락에서는 뺀다
        _member("C", "S1", shares=600, prev=10.0, close=10.0, traded=False),
        # 가격이 없으면 묶음에서 빠진다
        _member("D", "S1", shares=600, prev=None, close=None),
        _member("E", "S2", shares=10, prev=5.0, close=5.0, l1="G15"),
    ]
    sector_map = build_sector_map(D0, D0, members)
    parent = sector_map.parents[0]
    assert parent.group.code == "G45" and parent.group.name == "IT"
    child = parent.children[0]
    assert child.name == "S1이름"  # 'WICS ' 접두어를 뗀다
    assert child.size_weight_krw == 10_000.0
    assert child.return_weight_krw == 4_000.0
    assert child.return_pct == pytest.approx((1_000 * 10 + 3_000 * -10) / 4_000)
    assert (child.securities, child.untraded) == (3, 1)
    assert (sector_map.securities, sector_map.untraded) == (4, 1)
    # 보합 종목만 있는 업종은 0%(방향 없음), None 이 아니다
    assert sector_map.parents[1].children[0].return_pct == 0.0


def test_sector_securities_sorted_by_size_with_untraded_moves_empty() -> None:
    members = [
        _member("A", "S1", shares=1, prev=10.0, close=12.0),
        _member("B", "S1", shares=5, prev=10.0, close=10.0, traded=False),
        _member("Z", "S9", shares=50, prev=10.0, close=10.0),
    ]
    moves = sector_securities(members, "S1")
    assert [move.ticker for move in moves] == ["B", "A"]
    assert moves[0].return_pct is None
    assert moves[1].return_pct == pytest.approx(20.0)


def test_summary_sentence_names_match_the_screen_row_names() -> None:
    # 요약 문장은 backend 가, 표 줄 이름은 frontend 가 쓴다 — 같은 화면에서 다른 낱말이 되지 않게
    messages = (REPO / "frontend" / "src" / "shared" / "config" / "messages.ts").read_text(
        encoding="utf-8"
    )
    for investor, label in SUMMARY_LABELS.items():
        assert f'"market.investor.{investor.value}": "{label}",' in messages, investor
    for market, label in MARKET_LABELS.items():
        assert f'"market.market.{market.value}": "{label}",' in messages, market
