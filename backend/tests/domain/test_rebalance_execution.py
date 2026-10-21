"""주간 리밸런싱 주문의 가격·수량·체결 품질(S07). 손으로 계산한 값으로 확인한다."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.rebalance.facade.rebalance import (
    ExecutionPolicy,
    Quote,
    affordable_shares,
    limit_price,
    missed_krw,
    shortfall_bp,
    spread_ticks,
)

BOOK = Quote("000001", asks=(10_010, 10_020, 10_030), bids=(10_000, 9_990, 9_980))


def test_limit_price_takes_the_level_on_the_opposite_side() -> None:
    assert limit_price("buy", BOOK, 2) == 10_020
    assert limit_price("sell", BOOK, 2) == 9_990
    assert limit_price("buy", Quote("x", (10_010,), ()), 2) == 10_010  # 2호가가 없으면 1호가
    assert limit_price("sell", Quote("x", (10_010,), ()), 2) is None


def test_spread_is_counted_in_ticks_from_the_book() -> None:
    assert spread_ticks(BOOK) == 1
    assert spread_ticks(Quote("x", (10_060, 10_070), (10_000, 9_990))) == 6
    assert spread_ticks(Quote("x", (10_010,), (10_000,))) is None  # 틱을 잴 이웃이 없다
    assert spread_ticks(Quote("x", (), (10_000, 9_990))) is None


def test_affordable_shares_include_the_fee() -> None:
    # 1,000,000 ÷ (10,000 × 1.0035) = 99.65 → 99주
    assert affordable_shares(1_000_000, 10_000, 35) == 99
    assert affordable_shares(0, 10_000, 35) == affordable_shares(-5, 10_000, 35) == 0


def test_fee_budget_rounds_up_and_covers_sell_tax() -> None:
    policy = ExecutionPolicy()
    assert policy.fee_budget("buy", 130, 10_020) == 5_211  # 1,302,600 × 0.4% = 5,210.4
    assert policy.fee_budget("sell", 100, 9_990) == 6_993  # 999,000 × 0.7%
    # 모의 실측 비용: 매도 0.35% + 거래세 0.2% = 5,494원 < 6,993원
    assert 999_000 * 55 // 10_000 < policy.fee_budget("sell", 100, 9_990)


def test_shortfall_is_positive_when_worse_than_the_reference() -> None:
    assert shortfall_bp("buy", 50, 501_000, 10_000) == 20  # 평균 10,020원에 샀다
    assert shortfall_bp("sell", 100, 999_000, 10_000) == 10  # 평균 9,990원에 팔았다
    assert shortfall_bp("buy", 10, 99_000, 10_000) == -100  # 기준가보다 싸게 샀다
    with pytest.raises(ValueError):
        shortfall_bp("buy", 0, 0, 10_000)


def test_missed_cost_is_positive_when_the_price_moved_away() -> None:
    assert missed_krw("buy", 80, 10_000, 10_100) == 8_000  # 못 산 사이 올랐다
    assert missed_krw("sell", 30, 10_000, 9_900) == 3_000  # 못 판 사이 내렸다
    assert missed_krw("buy", 80, 10_000, 9_900) == -8_000
