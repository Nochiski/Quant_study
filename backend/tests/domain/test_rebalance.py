"""주간 리밸런싱 목표 계산(S07). 손으로 계산한 작은 사례로 확인한다."""

from __future__ import annotations

import pytest

from strategy_workbench.domain.rebalance.facade.rebalance import (
    LineCause,
    RebalanceAction,
    RebalancePlan,
    RebalancePolicy,
    plan_rebalance,
    signal_anomalies,
    week_targets,
)

POLICY = RebalancePolicy(slots=3, keep_rank=5)


def _codes(count: int) -> list[str]:
    return [f"{index:06d}" for index in range(1, count + 1)]


def _lines(plan: RebalancePlan) -> dict[str, tuple[RebalanceAction, int, int]]:
    return {
        line.symbol: (line.action, line.current_shares, line.target_shares) for line in plan.lines
    }


def test_initial_build_buys_the_top_slots_with_the_cash_reserve_and_fee() -> None:
    codes = _codes(6)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings={},
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=3_000_000,
        blocked={},
    )
    # 3,000,000 × 98% ÷ 3 = 980,000원. 980,000 ÷ (10,000 × 1.0035) = 97.66 → 97주
    assert (plan.equity_krw, plan.target_value_krw) == (3_000_000, 980_000)
    assert _lines(plan) == {code: (RebalanceAction.BUY, 0, 97) for code in codes[:3]}
    assert plan.target_shares() == dict.fromkeys(codes[:3], 97)


def test_held_names_inside_the_buffer_stay_and_outside_are_sold() -> None:
    codes = _codes(8)
    ranks = {code: rank for rank, code in enumerate(codes, 1)}
    plan = plan_rebalance(
        POLICY,
        ranks=ranks,
        # 000004(4위)는 완충 안, 000007(7위)은 5위 밖, 999999는 순위 없음
        holdings={"000004": 100, "000007": 100, "999999": 100},
        prices={**dict.fromkeys(codes, 10_000), "999999": 10_000},
        cash_krw=0,
        blocked={},
    )
    # 총자산 3,000,000원 → 종목당 980,000원. 4위 보유분 1,000,000원은 ±25% 안이라 그대로 둔다
    lines = _lines(plan)
    assert lines["000004"] == (RebalanceAction.HOLD, 100, 100)
    assert lines["000007"] == (RebalanceAction.SELL, 100, 0)
    assert lines["999999"] == (RebalanceAction.SELL, 100, 0)
    # 남은 두 자리는 보유하지 않은 1·2위로 채운다(3위는 자리가 없다)
    assert lines["000001"] == (RebalanceAction.BUY, 0, 97)
    assert lines["000002"] == (RebalanceAction.BUY, 0, 97)
    assert "000003" not in lines
    assert [line.action for line in plan.lines][:2] == [RebalanceAction.SELL, RebalanceAction.SELL]


def test_kept_name_is_resized_only_outside_the_band() -> None:
    codes = _codes(3)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        # 총자산 = 1,300,000 + 1,100,000 + 600,000 = 3,000,000 → 목표 980,000원(±245,000원)
        holdings={"000001": 130, "000002": 110, "000003": 60},
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=0,
        blocked={},
    )
    lines = _lines(plan)
    assert lines["000001"] == (RebalanceAction.RESIZE, 130, 97)  # +320,000원 > 245,000원
    assert lines["000002"] == (RebalanceAction.HOLD, 110, 110)  # +120,000원
    assert lines["000003"] == (RebalanceAction.RESIZE, 60, 97)  # −380,000원


def test_blocked_names_are_skipped_for_buying_and_held_ones_are_not_sold() -> None:
    codes = _codes(7)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings={"000007": 50},  # 7위라 원래 팔 종목이지만 매매가 막혔다
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=2_500_000,
        blocked={"000001": "정지", "000007": "정지"},
    )
    lines = _lines(plan)
    assert lines["000007"] == (RebalanceAction.HOLD, 50, 50)
    assert lines["000001"] == (RebalanceAction.SKIP, 0, 0)
    # 자리 두 개를 1위 대신 2·3위로 채운다
    assert lines["000002"][0] is lines["000003"][0] is RebalanceAction.BUY
    assert "000001" not in plan.target_shares()


def test_no_name_outside_the_buffer_is_bought_to_fill_a_slot() -> None:
    codes = _codes(7)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings={},
        prices={code: 10_000 for code in codes if code != "000002"},  # 2위는 가격이 없다
        cash_krw=3_000_000,
        blocked={"000001": "정지", "000003": "정지", "000004": "정지"},
    )
    lines = _lines(plan)
    assert lines["000002"] == (RebalanceAction.SKIP, 0, 0)
    assert lines["000005"] == (RebalanceAction.BUY, 0, 97)
    assert "000006" not in lines  # 6위는 완충(5위) 밖이라 사지 않는다
    assert sum(line.action is RebalanceAction.BUY for line in plan.lines) == 1


def test_more_kept_names_than_slots_sell_the_lowest_ranked() -> None:
    codes = _codes(5)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings=dict.fromkeys(codes[1:], 100),  # 2~5위 네 종목
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=0,
        blocked={},
    )
    lines = _lines(plan)
    assert lines["000005"] == (RebalanceAction.SELL, 100, 0)
    assert all(lines[code][0] is not RebalanceAction.SELL for code in codes[1:4])


def test_a_price_above_the_slot_value_is_skipped() -> None:
    plan = plan_rebalance(
        POLICY,
        ranks={"000001": 1, "000002": 2},
        holdings={},
        prices={"000001": 2_000_000, "000002": 10_000},
        cash_krw=3_000_000,
        blocked={},
    )
    lines = _lines(plan)
    assert lines["000001"] == (RebalanceAction.SKIP, 0, 0)
    assert lines["000002"] == (RebalanceAction.BUY, 0, 97)


@pytest.mark.parametrize(
    ("ranks", "holdings", "prices", "cash"),
    [
        ({"000001": 1}, {"000009": 10}, {}, 0),  # 보유 종목에 가격이 없다
        ({"000001": 1, "000002": 1}, {}, {}, 0),  # 순위가 겹친다
        ({"000001": 0}, {}, {}, 0),  # 순위는 1부터다
        ({}, {}, {}, -1),  # 음수 현금
    ],
)
def test_invalid_inputs_are_rejected(
    ranks: dict[str, int], holdings: dict[str, int], prices: dict[str, int], cash: int
) -> None:
    with pytest.raises(ValueError):
        plan_rebalance(
            POLICY, ranks=ranks, holdings=holdings, prices=prices, cash_krw=cash, blocked={}
        )


def test_policy_rejects_a_buffer_tighter_than_the_slots() -> None:
    with pytest.raises(ValueError):
        RebalancePolicy(slots=10, keep_rank=5)


def test_held_name_missing_from_the_signal_is_sold_but_flagged_for_review() -> None:
    plan = plan_rebalance(
        POLICY,
        ranks={"000001": 1},
        holdings={"000009": 10},
        prices={"000001": 10_000, "000009": 10_000},
        cash_krw=0,
        blocked={},
        previous_ranks={"000009": 25},
    )
    [line] = [line for line in plan.lines if line.symbol == "000009"]
    assert (line.action, line.target_shares, line.review) == (RebalanceAction.SELL, 0, True)
    assert line.reason == "신호에서 빠짐 — 이유 미상 (직전 빌드 25위)"
    assert not any(other.review for other in plan.lines if other.symbol != "000009")
    explained = plan_rebalance(
        POLICY,
        ranks={"000001": 1},
        holdings={"000009": 10},
        prices={"000001": 10_000, "000009": 10_000},
        cash_krw=0,
        blocked={},
        previous_ranks={"000009": 25},
        missing_reasons={"000009": "WISE 추정치 공백"},
    )
    [line] = [line for line in explained.lines if line.symbol == "000009"]
    assert line.reason == "신호에서 빠짐 — WISE 추정치 공백 (직전 빌드 25위)"


def test_held_name_priced_from_an_earlier_close_is_held_and_flagged_for_review() -> None:
    # 999999 는 신호일 가격 행이 없어(거래정지 등) 직전 종가로 평가한다(#603 리뷰).
    # 팔지도 사지도 않는다
    plan = plan_rebalance(
        POLICY,
        ranks={"000001": 1, "000002": 2},
        holdings={"999999": 100},
        prices={"000001": 10_000, "000002": 10_000, "999999": 12_000},
        cash_krw=1_800_000,
        blocked={"999999": "신호일 종가 없음(거래정지 등) — 10-20 종가로 평가"},
        stale_prices={"999999"},
    )
    [line] = [line for line in plan.lines if line.symbol == "999999"]
    assert (line.action, line.target_shares, line.review) == (RebalanceAction.HOLD, 100, True)
    assert plan.equity_krw == 3_000_000  # 1,800,000 + 100 × 12,000
    assert not any(other.review for other in plan.lines if other.symbol != "999999")


def _missing_held_plan() -> RebalancePlan:
    """000001(1위)은 남기고 999999 는 신호에서 빠져 팔며, 빈 두 자리를 2·3위로 채운다."""
    codes = _codes(6)
    return plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings={"000001": 100, "999999": 100},
        prices={**dict.fromkeys(codes, 10_000), "999999": 10_000},
        cash_krw=1_000_000,
        blocked={},
    )


def test_excluding_a_flagged_sale_keeps_the_name_and_drops_the_lowest_new_buy() -> None:
    plan = _missing_held_plan()
    assert _lines(plan)["999999"] == (RebalanceAction.SELL, 100, 0)
    week = week_targets(plan, {"999999"})
    # 999999 가 자리를 그대로 차지하므로 대체 매수 중 순위가 낮은 3위를 이번 주는 사지 않는다
    # (#606 리뷰)
    assert week.dropped_buys == ("000003",)
    assert week.targets == {"000001": 100, "000002": 97}


def test_excluding_a_buy_only_removes_that_buy() -> None:
    week = week_targets(_missing_held_plan(), {"000002"})
    assert week.dropped_buys == ()
    assert week.targets == {"000001": 100, "999999": 0, "000003": 97}


def test_excluding_a_sale_with_no_new_buy_holds_more_names_than_slots() -> None:
    codes = _codes(5)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1)},
        holdings=dict.fromkeys(codes[1:], 100),  # 2~5위 네 종목 → 5위를 자리 넘침으로 판다
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=0,
        blocked={},
    )
    week = week_targets(plan, {"000005"})
    # 뺀다고 다른 종목을 새로 팔지 않는다. 이번 주는 네 종목을 들고 간다
    assert week.dropped_buys == ()
    assert "000005" not in week.targets
    assert all(week.targets[code] == 100 for code in codes[1:4])


def test_every_line_carries_a_structured_cause() -> None:
    # 리뷰 #607 P3: 주문지가 사유 문장을 문자열로 쪼개 다시 판정했다. 원인을 칸으로 넘긴다
    codes = _codes(7)
    plan = plan_rebalance(
        POLICY,
        ranks={code: rank for rank, code in enumerate(codes, 1) if code != "000005"},
        holdings={"000002": 100, "000005": 100, "000007": 100},
        prices=dict.fromkeys(codes, 10_000),
        cash_krw=0,
        blocked={"000003": "정지"},
        previous_ranks={"000005": 4},
        missing_reasons={"000005": "WISE 추정치 공백"},
    )
    causes = {line.symbol: line.cause for line in plan.lines}
    assert causes == {
        "000001": LineCause.NEW_TOP,
        "000002": LineCause.IN_BAND,
        "000003": LineCause.BLOCKED,
        "000004": LineCause.FILL_LOWER,
        "000005": LineCause.SIGNAL_MISSING,
        "000007": LineCause.OUTSIDE_BUFFER,
    }
    [missing] = [line for line in plan.lines if line.symbol == "000005"]
    assert (missing.missing_why, missing.last_seen) == ("WISE 추정치 공백", "직전 빌드 4위")


def test_signal_anomalies_flag_bulk_disappearance_from_the_top() -> None:
    previous = {f"{index:06d}": index for index in range(1, 101)}
    one_gone = {code: rank for code, rank in previous.items() if code != "000003"}
    assert signal_anomalies(one_gone, previous) == ()
    two_gone = {code: rank for code, rank in one_gone.items() if code != "000012"}
    [reason] = signal_anomalies(two_gone, previous)
    assert "2종목" in reason and "000003" in reason and "000012" in reason
    shrunk = {code: rank for code, rank in previous.items() if rank <= 30 or rank > 34}
    assert "100 → 96" in signal_anomalies(shrunk, previous)[0]  # 4% 감소, 상위 30은 그대로
