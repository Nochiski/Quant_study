"""S07 리스크 가드의 순수 판정. 손으로 계산한 작은 사례로 확인한다.

고점 1억 원, 경고 −10%·축소 −15%(노출 50%)·정지 −20%, 축소는 −10% 위로 회복하면 풀린다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from strategy_workbench.domain.risk.facade.risk import (
    ActionKind,
    DayClose,
    GuardReason,
    OrderVerdict,
    ReviewOrder,
    RiskAction,
    RiskLevel,
    SecurityFlag,
    SignalFacts,
    TradeStatus,
    ViWindow,
    check_order,
    drawdown_state,
    guard_targets,
    parse_limits,
    review_day,
)

KST = timezone(timedelta(hours=9))
DRAWDOWN: dict[str, int] = {
    "initial_peak_krw": 100_000_000,
    "warn_bp": 1_000,
    "reduce_bp": 1_500,
    "halt_bp": 2_000,
    "recover_bp": 1_000,
    "reduced_exposure_bp": 5_000,
}
PAYLOAD: dict[str, object] = {
    "schema": "s07-risk-limits/1",
    "version": 1,
    "drawdown": DRAWDOWN,
    "max_weight_bp": 1_500,
    "max_adv_participation_bp": 1_000,
    "near_limit_bp": 300,
    "vi_unreleased_hold_minutes": 5,
    "blocked_buy_flags": [
        "admin",
        "halted",
        "liquidation",
        "investment_warning",
        "investment_danger",
        "unknown",
    ],
    "changes": [{"version": 1, "date": "2026-10-26", "by": "정동민", "what": "처음 정함"}],
}
LIMITS = parse_limits(PAYLOAD, "ab" * 32)


def _closes(*equities: int) -> list[DayClose]:
    start = date(2026, 10, 26)
    return [
        DayClose(
            start + timedelta(days=index),
            datetime.combine(start + timedelta(days=index), time(15, 40), KST),
            equity,
        )
        for index, equity in enumerate(equities)
    ]


def _level(*equities: int) -> RiskLevel:
    return drawdown_state(_closes(*equities), (), LIMITS.drawdown).level


# --- 낙폭 단계 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("equity", "level", "loss_bp"),
    [
        (95_325_570, RiskLevel.NORMAL, 467),  # 10-26 장 마감: −4.67%
        (90_000_001, RiskLevel.NORMAL, 999),  # 손실을 bp로 내림해 −10%에 못 미친다
        (90_000_000, RiskLevel.WARN, 1_000),
        (85_000_000, RiskLevel.REDUCE, 1_500),
        (80_000_000, RiskLevel.HALT, 2_000),
    ],
)
def test_loss_from_the_initial_peak_sets_the_level_at_exact_boundaries(
    equity: int, level: RiskLevel, loss_bp: int
) -> None:
    state = drawdown_state(_closes(equity), (), LIMITS.drawdown)
    assert (state.level, state.loss_bp, state.peak_krw) == (level, loss_bp, 100_000_000)


def test_a_new_high_becomes_the_peak_the_loss_is_measured_from() -> None:
    state = drawdown_state(_closes(110_000_000, 99_000_000), (), LIMITS.drawdown)
    # 1.1억에서 9,900만은 −10% → 경고. 처음 고점 1억 기준이면 −1%라 정상이었다
    assert (state.peak_krw, state.peak_day, state.loss_bp) == (
        110_000_000,
        date(2026, 10, 26),
        1_000,
    )
    assert state.level is RiskLevel.WARN


def test_reduce_holds_until_the_loss_recovers_above_the_recover_line() -> None:
    assert _level(84_000_000, 88_000_000) is RiskLevel.REDUCE  # −12%로 올라와도 축소 유지
    assert _level(84_000_000, 90_000_100) is RiskLevel.NORMAL  # −9.99%면 정상
    assert _level(91_000_000, 88_000_000) is RiskLevel.WARN  # 축소를 거치지 않은 −12%는 경고


def test_halt_does_not_lift_by_itself_when_the_loss_recovers() -> None:
    assert _level(79_000_000, 95_000_000) is RiskLevel.HALT


def test_resume_turns_halt_into_reduce_and_halt_needs_a_new_crossing() -> None:
    closes = _closes(79_000_000, 78_000_000, 81_000_000, 79_500_000)
    resume = RiskAction(
        datetime(2026, 10, 26, 18, 0, tzinfo=KST), ActionKind.RESUME, "동민", "전략 확인"
    )
    # 첫날 정지 → 사람이 풀어 축소 → 둘째 날 −22%는 −20% 선을 새로 넘은 것이 아니라 축소 유지
    assert drawdown_state(closes[:2], (resume,), LIMITS.drawdown).level is RiskLevel.REDUCE
    # 셋째 날 −19%로 올라왔다가 넷째 날 −20.5%로 다시 넘으면 정지
    assert drawdown_state(closes, (resume,), LIMITS.drawdown).level is RiskLevel.HALT


def test_reset_moves_the_peak_to_the_given_equity_and_clears_the_level() -> None:
    reset = RiskAction(
        datetime(2026, 10, 26, 18, 0, tzinfo=KST),
        ActionKind.RESET,
        "동민",
        "입금 반영",
        peak_krw=79_000_000,
    )
    state = drawdown_state(_closes(79_000_000, 75_000_000), (reset,), LIMITS.drawdown)
    # 새 고점 7,900만 기준 7,500만은 −5.06% → 정상
    assert (state.level, state.peak_krw, state.loss_bp) == (RiskLevel.NORMAL, 79_000_000, 506)


def test_no_close_yet_starts_from_the_initial_peak() -> None:
    state = drawdown_state((), (), LIMITS.drawdown)
    assert (state.level, state.peak_krw, state.loss_bp, state.last_day) == (
        RiskLevel.NORMAL,
        100_000_000,
        0,
        None,
    )


# --- 주문안 목표 축소 ---------------------------------------------------------------------------

FACTS = {
    "000001": SignalFacts(adv20_krw=10_000_000_000, admin=False, halted=False),
    "000002": SignalFacts(adv20_krw=10_000_000_000, admin=False, halted=False),
    "000003": SignalFacts(adv20_krw=10_000_000_000, admin=False, halted=False),
}


def _guard(
    targets: dict[str, int],
    holdings: dict[str, int],
    *,
    level: RiskLevel = RiskLevel.NORMAL,
    facts: dict[str, SignalFacts] | None = None,
    equity: int = 10_000_000,
    frozen: frozenset[str] = frozenset(),
) -> dict[str, tuple[int, tuple[GuardReason, ...]]]:
    guarded = guard_targets(
        targets=targets,
        holdings=holdings,
        frozen=frozen,
        prices=dict.fromkeys(targets, 10_000),
        facts=FACTS if facts is None else facts,
        equity_krw=equity,
        level=level,
        limits=LIMITS,
    )
    return {line.symbol: (line.target, line.reasons) for line in guarded.lines}


def test_normal_level_keeps_targets_inside_the_limits() -> None:
    assert _guard({"000001": 100, "000002": 0}, {"000002": 50}) == {
        "000001": (100, ()),
        "000002": (0, ()),
    }


def test_reduce_halves_every_target_including_kept_holdings() -> None:
    lines = _guard(
        {"000001": 101, "000002": 100, "000003": 0},
        {"000002": 100, "000003": 40},
        level=RiskLevel.REDUCE,
    )
    # 새 매수 101주 → 50주(내림), 그대로 두던 보유 100주 → 50주 매도, 파는 종목은 그대로 0
    assert lines == {
        "000001": (50, (GuardReason.EXPOSURE,)),
        "000002": (50, (GuardReason.EXPOSURE,)),
        "000003": (0, ()),
    }


def test_halt_halves_targets_and_buys_nothing() -> None:
    lines = _guard({"000001": 100, "000002": 100}, {"000002": 60}, level=RiskLevel.HALT)
    # 보유 없는 000001: 절반 50주도 사지 않는다. 000002: 절반 50주로 줄이는 매도만
    assert lines == {
        "000001": (0, (GuardReason.EXPOSURE, GuardReason.HALT)),
        "000002": (50, (GuardReason.EXPOSURE,)),
    }


def test_names_the_plan_cannot_trade_are_left_as_they_are() -> None:
    # 주문안이 매매가 막혀 그대로 둔 종목(거래정지·신호일 가격 없음)은 축소해도 팔 주문을
    # 만들지 않는다
    lines = _guard(
        {"000001": 100}, {"000001": 100}, level=RiskLevel.REDUCE, frozen=frozenset({"000001"})
    )
    assert lines == {"000001": (100, ())}


def test_single_name_cap_limits_buys_but_not_existing_overweight() -> None:
    # 총자산 1,000만 × 15% = 150만 → 1만 원짜리 150주까지
    lines = _guard({"000001": 200, "000002": 200}, {"000002": 180})
    assert lines["000001"] == (150, (GuardReason.WEIGHT,))
    assert lines["000002"] == (180, (GuardReason.WEIGHT,))  # 이미 넘은 보유는 팔지 않는다


def test_trading_value_cap_limits_the_buy_amount() -> None:
    small = {"000001": SignalFacts(adv20_krw=5_000_000, admin=False, halted=False)}
    # 20일 평균 거래대금 500만 × 10% = 50만 → 1만 원짜리 50주까지 더 산다
    assert _guard({"000001": 120}, {"000001": 10}, facts=small) == {
        "000001": (60, (GuardReason.ADV,))
    }
    # 파는 쪽은 줄이지 않는다
    assert _guard({"000001": 0}, {"000001": 120}, facts=small) == {"000001": (0, ())}


def test_both_caps_apply_and_the_tighter_one_wins() -> None:
    small = {"000001": SignalFacts(adv20_krw=8_000_000, admin=False, halted=False)}
    # 비중 상한 150주, 거래대금 상한 80만 → 80주. 둘 다 사유에 남는다
    assert _guard({"000001": 200}, {}, facts=small) == {
        "000001": (80, (GuardReason.WEIGHT, GuardReason.ADV))
    }


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        ({"000001": SignalFacts(10_000_000_000, admin=True, halted=False)}, GuardReason.BLOCKED),
        ({"000001": SignalFacts(10_000_000_000, admin=False, halted=True)}, GuardReason.BLOCKED),
        ({"000001": SignalFacts(None, admin=False, halted=False)}, GuardReason.NO_FACTS),
        ({}, GuardReason.NO_FACTS),
    ],
)
def test_flagged_or_unknown_names_are_not_bought_but_can_be_sold(
    facts: dict[str, SignalFacts], reason: GuardReason
) -> None:
    assert _guard({"000001": 100}, {"000001": 30}, facts=facts) == {"000001": (30, (reason,))}
    assert _guard({"000001": 0}, {"000001": 30}, facts=facts) == {"000001": (0, ())}


# --- 주문 직전 거부권 ---------------------------------------------------------------------------

NINE_TEN = time(9, 10)


def _status(
    *flags: SecurityFlag, upper: int = 13_000, lower: int = 7_000, vi: tuple[ViWindow, ...] = ()
) -> TradeStatus:
    return TradeStatus("000001", frozenset(flags), upper, lower, vi)


@pytest.mark.parametrize(
    "flag",
    [
        SecurityFlag.ADMIN,
        SecurityFlag.HALTED,
        SecurityFlag.LIQUIDATION,
        SecurityFlag.INVESTMENT_WARNING,
        SecurityFlag.INVESTMENT_DANGER,
        SecurityFlag.UNKNOWN,
    ],
)
def test_blocked_flags_stop_buys_and_let_sells_through(flag: SecurityFlag) -> None:
    assert check_order("buy", 10_000, _status(flag), NINE_TEN, LIMITS).verdict is (
        OrderVerdict.BLOCK
    )
    assert check_order("sell", 10_000, _status(flag), NINE_TEN, LIMITS).verdict is (
        OrderVerdict.ALLOW
    )


def test_caution_flags_are_recorded_but_do_not_stop_a_buy() -> None:
    check = check_order("buy", 10_000, _status(SecurityFlag.OVERHEATED), NINE_TEN, LIMITS)
    assert check.verdict is OrderVerdict.ALLOW and check.notes


def test_a_buy_near_the_upper_limit_waits_for_the_next_round() -> None:
    # 상한가 13,000원의 97% = 12,610원
    assert check_order("buy", 12_610, _status(), NINE_TEN, LIMITS).verdict is OrderVerdict.HOLD
    assert check_order("buy", 12_600, _status(), NINE_TEN, LIMITS).verdict is OrderVerdict.ALLOW
    # 하한가 근처 매도는 막지 않고 적어 둔다(7,000 × 103% = 7,210원)
    sell = check_order("sell", 7_210, _status(), NINE_TEN, LIMITS)
    assert sell.verdict is OrderVerdict.ALLOW and sell.notes


@pytest.mark.parametrize(
    ("window", "verdict"),
    [
        (ViWindow(time(9, 8), time(9, 10, 30)), OrderVerdict.HOLD),  # 해제 전
        (ViWindow(time(9, 5), time(9, 7)), OrderVerdict.ALLOW),  # 이미 풀렸다
        (ViWindow(time(9, 6), None), OrderVerdict.HOLD),  # 해제 시각 없음, 발동 5분 안
        (ViWindow(time(9, 4), None), OrderVerdict.ALLOW),  # 해제 시각 없음, 5분 지남
        (ViWindow(time(9, 11), time(9, 13)), OrderVerdict.ALLOW),  # 아직 발동 전 시각
    ],
)
def test_an_active_vi_holds_both_sides(window: ViWindow, verdict: OrderVerdict) -> None:
    for side in ("buy", "sell"):
        assert check_order(side, 10_000, _status(vi=(window,)), NINE_TEN, LIMITS).verdict is (
            verdict
        )


# --- 체결 후 점검 -------------------------------------------------------------------------------


def test_review_flags_buys_beyond_the_guard_and_blocked_names_that_filled() -> None:
    review = review_day(
        (
            ReviewOrder("000001", "buy", 100, 50, 60, 12),  # 리스크가 50주로 줄였는데 60주 체결
            ReviewOrder("000002", "buy", 100, 100, 25, None),
            ReviewOrder("000003", "sell", 40, 40, 40, 5),
        ),
        blocked_buys={"000002"},
        holdings=(("000001", 60, 10_000), ("000003", 0, 10_000)),
        equity_krw=4_000_000,
        limits=LIMITS,
    )
    reviewed = {order.symbol: order for order in review.orders}
    assert (reviewed["000001"].reduced_by_risk, reviewed["000001"].unfilled_bp) == (50, 0)
    assert reviewed["000002"].unfilled_bp == 7_500
    assert len(review.violations) == 2
    assert "000001" in review.violations[0] and "000002" in review.violations[1]
    # 60주 × 1만 = 60만 ÷ 400만 = 15% → 상한과 같아 기록하지 않는다
    assert review.notes == ()


def test_review_notes_names_above_the_weight_cap_at_the_close() -> None:
    review = review_day(
        (), blocked_buys=(), holdings=(("000001", 61, 10_000),), equity_krw=4_000_000, limits=LIMITS
    )
    assert review.violations == () and len(review.notes) == 1 and "000001" in review.notes[0]


# --- 한도 파일 ---------------------------------------------------------------------------------


def test_limits_keep_version_and_fingerprint() -> None:
    assert (LIMITS.version, LIMITS.sha256, LIMITS.drawdown.halt_bp) == (1, "ab" * 32, 2_000)
    assert SecurityFlag.UNKNOWN in LIMITS.blocked_buy_flags


@pytest.mark.parametrize(
    "broken",
    [
        {"version": None},
        {"surprise": 1},
        {"schema": "s07-risk-limits/0"},
        {"max_weight_bp": 0},
        {"blocked_buy_flags": ["admin", "nonsense"]},
        {"drawdown": {**DRAWDOWN, "halt_bp": 1_400}},
        {"drawdown": {**DRAWDOWN, "recover_bp": 1_600}},
    ],
)
def test_limits_reject_missing_unknown_or_out_of_range_fields(broken: dict[str, object]) -> None:
    payload = {key: value for key, value in {**PAYLOAD, **broken}.items() if value is not None}
    with pytest.raises(ValueError):
        parse_limits(payload, "ab" * 32)
