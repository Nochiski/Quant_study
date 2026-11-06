"""σ 손절·분할 익절 시뮬레이터 확인. 실행: `backend/.venv/bin/python -m pytest workspace/dongmin/scope-sigma-exit/tests`."""

from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.configs import ARMS, BASELINE, V2_ARMS, V2_BASELINE, Variant, by_name  # noqa: E402  # reason: sys.path 뒤 import
from sim.data import Market  # noqa: E402  # reason: sys.path 뒤 import
from sim.engine import START_CAPITAL, Signal, excess_sigma, select, signal_dates, simulate  # noqa: E402  # reason: sys.path 뒤 import
from strategy_workbench.domain.rebalance._model import (  # noqa: E402  # reason: sys.path 뒤 import
    RebalanceAction,
    RebalancePolicy,
    plan_rebalance,
)


def _sessions(n: int, start: date = date(2020, 1, 1)) -> tuple[date, ...]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def _market(sessions, tickers, open_, close, volume=None, jump=None, index=None, kosdaq=None) -> Market:
    """`index` 는 (코스피, 코스닥) 종가 목록(시가도 같은 값). 없으면 둘 다 1,000 고정."""
    shape = (len(sessions), len(tickers))
    adj_close = np.array(close, dtype=float).reshape(shape)
    has_bar = ~np.isnan(adj_close)
    last_bar = np.where(has_bar.any(axis=0), len(sessions) - 1 - np.argmax(has_bar[::-1], axis=0), -1)
    levels = np.full((2, len(sessions)), 1_000.0) if index is None else np.array(index, dtype=float)
    return Market(
        snapshot_id="test",
        sessions=sessions,
        tickers=tuple(tickers),
        adj_open=np.array(open_, dtype=float).reshape(shape),
        adj_close=adj_close,
        volume=np.ones(shape) if volume is None else np.array(volume, dtype=float).reshape(shape),
        adv20=np.full(shape, 1e12),
        unadjusted_jump=np.zeros(shape, dtype=bool) if jump is None else np.array(jump).reshape(shape),
        last_bar=last_bar,
        index_sessions=sessions,
        kospi=np.ones(len(sessions)),
        kospi200=np.ones(len(sessions)),
        index_open=levels,
        index_close=levels,
        kosdaq=np.zeros(shape, dtype=bool) if kosdaq is None else np.array(kosdaq, dtype=bool).reshape(shape),
    )


def _history_then(path: list[float], n_history: int = 30, step: float = 0.01) -> list[float]:
    """하루 ±1% 를 번갈아 n_history 세션 움직인 뒤 `path` 를 잇는 종가."""
    prices = [100.0]
    for k in range(n_history - 1):
        prices.append(prices[-1] * (1.0 + (step if k % 2 == 0 else -step)))
    return prices + path


def _first_signal_after(sessions, n_history: int) -> dict[date, Signal]:
    """기준점 전 σ 입력이 n_history 세션 쌓인 뒤의 첫 주간 신호일부터 A 하나만 1위."""
    weekly = sorted(signal_dates(sessions, "weekly"))
    return {d: Signal(ranked=(("A", 1.0),)) for d in weekly if sessions.index(d) >= n_history - 1}


def _one(**rules) -> Variant:
    return Variant(name="t", axis="t", slots=1, keep_rank=1, **rules)


# --- 사전등록 고정 ---


def test_arms_are_preregistered():
    assert BASELINE.slots == 20 and BASELINE.keep_rank == 20 and math.isinf(BASELINE.band)
    assert BASELINE.cash == 0.02 and BASELINE.rebalance == "weekly" and not BASELINE.uses_rules
    rules = {v.name: (v.stop_sigma, v.take_sigma, v.trim_weight) for v in ARMS}
    assert rules == {"stop_only": (2.0, None, None), "take_only": (None, 3.0, 0.03), "full": (2.0, 3.0, 0.03)}
    prereg = {n for n in by_name() if "diag_" not in n}
    assert prereg == {
        "baseline", "stop_only", "take_only", "full", "full_s1.5_t2.5", "full_s2.5_t3.5",  # 1차
        "v2_baseline", "v2_full",  # 2차
        "v3_m1", "v3_m2",  # 3차
    }


# --- 매수 금액·비중 되돌리기 ---


def test_new_buys_are_4_9_percent_of_nav():
    sessions = _sessions(10)
    tickers = [f"{k:06d}" for k in range(20)]
    flat = [100.0] * (10 * 20)
    m = _market(sessions, tickers, flat, flat)
    sig = {d: Signal(ranked=tuple((t, 1.0) for t in tickers)) for d in sessions}
    r = simulate(BASELINE, m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    i = sessions.index(min(signal_dates(sessions, "weekly"))) + 1
    # 목표 금액 4.9% 를 수수료 포함 가격으로 나눈 수량 × 가격
    per_name = START_CAPITAL * 0.049 / 1.0015
    held_value = r.equity[i] * (1.0 - r.cash_ratio[i])
    assert held_value == pytest.approx(per_name * 20, rel=1e-9)


def test_kept_positions_are_not_resized():
    """첫 매수 뒤 값이 두 배가 돼도 다음 주 신호일에 사고팔지 않는다(band = ∞)."""
    sessions = _sessions(15)
    close = [100.0] * 7 + [200.0] * 8
    m = _market(sessions, ["A"], close, close)
    sig = {d: Signal(ranked=(("A", 1.0),)) for d in sessions}
    r = simulate(_one(), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.n_orders == 1


# --- σ ---


def test_excess_sigma_uses_window_before_anchor_and_skips_jumps():
    sessions = _sessions(40)
    close = _history_then([100.0] * 10)
    jump = [False] * 40
    jump[25] = True
    m = _market(sessions, ["A"], close, close, jump=[[x] for x in jump])
    s = excess_sigma(m, 0, 0, 30, window=60, min_obs=20)
    rets = [close[d] / close[d - 1] - 1.0 for d in range(1, 30) if d != 25]
    assert s == pytest.approx(float(np.std(rets, ddof=1)), rel=1e-12)
    assert math.isnan(excess_sigma(m, 0, 0, 15, window=60, min_obs=20))


# --- 규칙 ---


def test_sigma_stop_sells_at_next_open():
    n_hist = 30
    sessions = _sessions(45)
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (buy_i - len(close) + 1)  # 산 날까지 그대로
    close += [close[-1] * 0.95] * (45 - len(close))  # 산 다음 날 −5% → t=2, z ≈ −3.4
    m = _market(sessions, ["A"], close, close)
    r = simulate(_one(stop_sigma=2.0), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.rule_sales == {"stop": 1}
    sold_week = min(d for d in signal_dates(sessions, "weekly") if sessions.index(d) > buy_i + 1)
    assert r.holdings_by_week[sold_week] == frozenset()


def test_take_profit_halves_then_reanchors():
    n_hist = 30
    sessions = _sessions(45)
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (buy_i - len(close) + 1)
    up = close[-1] * 1.20  # 산 다음 날 +20% → z ≈ 14. 원래 기준점이면 며칠 더 +3σ 위지만 다시 잡은 기준점에선 0
    close += [up] * (45 - len(close))
    m = _market(sessions, ["A"], close, close)
    r = simulate(_one(take_sigma=3.0), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.rule_sales == {"take": 1}
    assert r.n_orders == 2  # 첫 매수 + 절반 매도. 남은 절반은 비중을 되돌리지 않는다


def test_take_profit_sells_all_when_half_would_be_under_trim():
    n_hist = 30
    sessions = _sessions(45)
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (buy_i - len(close) + 1)
    close += [close[-1] * 1.05] * (45 - len(close))
    m = _market(sessions, ["A"], close, close)
    # 비중 98% 의 절반 49% < 60% → 절반 대신 전량
    r = simulate(_one(take_sigma=3.0, trim_weight=0.60), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.rule_sales.get("take_full", 0) >= 1 and "take" not in r.rule_sales


def test_trim_sells_small_position():
    n_hist = 30
    sessions = _sessions(40)
    sig = _first_signal_after(sessions, n_hist)
    close = _history_then([100.0] * 10, n_hist)
    m = _market(sessions, ["A"], close, close)
    r = simulate(_one(trim_weight=0.99), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.rule_sales.get("trim", 0) >= 1


def test_no_rebuy_in_the_same_signal_day_rebalance():
    """신호일 장 마감에 손절이 나면 그 신호일 리밸런싱에선 다시 사지 않고, 다음 신호일에 산다."""
    n_hist = 30
    sessions = _sessions(60)
    weekly = sorted(signal_dates(sessions, "weekly"))
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    drop_day = next(d for d in weekly if sessions.index(d) > buy_i)
    k = sessions.index(drop_day)
    close = _history_then([], n_hist)
    close += [close[-1]] * (k - len(close))
    close += [close[-1] * 0.92] * (60 - len(close))  # 신호일 당일 −8% → z ≈ −3.5
    m = _market(sessions, ["A"], close, close)
    r = simulate(_one(stop_sigma=2.0), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.rule_sales == {"stop": 1}
    after = [d for d in weekly if d > drop_day]
    assert r.holdings_by_week[after[0]] == frozenset()  # 같은 신호일 리밸런싱으로 다시 사지 않음
    assert r.holdings_by_week[after[1]] == frozenset({"A"})  # 다음 신호일엔 순위만 보고 다시 산다
    assert len(r.rebuys) == 1


def test_short_history_skips_sigma_rules():
    sessions = _sessions(20)
    close = [100.0] * 5 + [90.0] * 15
    m = _market(sessions, ["A"], close, close)
    sig = {d: Signal(ranked=(("A", 1.0),)) for d in sessions}
    r = simulate(_one(stop_sigma=2.0), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    assert r.n_sigma_missing == 1 and r.rule_sales == {}


def test_kosdaq_stock_is_judged_against_kosdaq():
    """종목은 그대로, 코스닥 지수만 +5% → 코스닥 종목은 손절, 코스피 종목은 아니다."""
    n_hist = 30
    sessions = _sessions(45)
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (45 - len(close))
    kospi = [1_000.0] * 45
    kosdaq = [1_000.0] * (buy_i + 1) + [1_050.0] * (45 - buy_i - 1)
    on_kosdaq = _market(sessions, ["A"], close, close, index=[kospi, kosdaq], kosdaq=[[True]] * 45)
    on_kospi = _market(sessions, ["A"], close, close, index=[kospi, kosdaq])
    v = _one(stop_sigma=2.0)
    assert simulate(v, on_kosdaq, sig, start=sessions[0], end=sessions[-1], tax_schedule=()).rule_sales == {"stop": 1}
    assert simulate(v, on_kospi, sig, start=sessions[0], end=sessions[-1], tax_schedule=()).rule_sales == {}


# --- 2차(PREREG_v2): 100위 보유 한도 · 손절 5거래일째부터 · 손절 28일 재매수 금지 · 정리 1.5% ---


def test_v2_arms_are_preregistered():
    assert V2_BASELINE.keep_rank == 100 and V2_BASELINE.slots == 20 and not V2_BASELINE.uses_rules
    (full,) = V2_ARMS
    assert full.name == "v2_full" and full.keep_rank == 100
    assert (full.stop_sigma, full.take_sigma, full.trim_weight) == (2.0, 3.0, 0.015)
    assert (full.stop_min_days, full.stop_cooldown_days) == (5, 28)


def test_stop_waits_until_min_days():
    """산 다음 날(t=2) −5% 뒤 바로 회복 → 5거래일째부터 보면 손절 없음, 첫날부터 보면 손절."""
    n_hist = 30
    sessions = _sessions(45)
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (buy_i - len(close) + 1)
    flat = close[-1]
    close += [flat * 0.95] + [flat] * (45 - len(close) - 1)
    m = _market(sessions, ["A"], close, close)
    run = lambda v: simulate(v, m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())  # noqa: E731  # reason: 테스트 안 짧은 실행 도우미
    assert run(_one(stop_sigma=2.0)).rule_sales == {"stop": 1}
    assert run(_one(stop_sigma=2.0, stop_min_days=5)).rule_sales == {}


def test_stop_cooldown_blocks_rebuy_for_28_days():
    n_hist = 30
    sessions = _sessions(80)
    weekly = sorted(signal_dates(sessions, "weekly"))
    sig = _first_signal_after(sessions, n_hist)
    buy_i = sessions.index(min(sig)) + 1
    close = _history_then([], n_hist)
    close += [close[-1]] * (buy_i - len(close) + 1)
    close += [close[-1] * 0.95] * (80 - len(close))  # 산 다음 날 −5% → 손절
    m = _market(sessions, ["A"], close, close)
    r = simulate(_one(stop_sigma=2.0, stop_cooldown_days=28), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=())
    stop_day = sessions[buy_i + 1]
    first_allowed = min(d for d in weekly if (d - stop_day).days > 28)
    for d in weekly:
        if stop_day < d <= first_allowed:
            assert r.holdings_by_week[d] == frozenset(), d
    after = min(d for d in weekly if d > first_allowed)
    assert r.holdings_by_week[after] == frozenset({"A"})
    assert r.rule_sales == {"stop": 1}


# --- 10-26 시험에서 그대로 가져온 확인 ---


@pytest.mark.parametrize("seed", range(100))
def test_selection_matches_s07_plan_rebalance(seed):
    rng = random.Random(seed)
    universe = [f"{k:06d}" for k in range(80)]
    ranked = rng.sample(universe, rng.randint(20, 70))
    ranks = {t: r for r, t in enumerate(ranked, start=1)}
    held = rng.sample(universe, rng.randint(0, 25))
    blocked = {t for t in held + ranked if rng.random() < 0.08}
    policy = RebalancePolicy(slots=20, keep_rank=20)
    plan = plan_rebalance(
        policy,
        ranks=ranks,
        holdings={t: 10 for t in held},
        prices={t: 1_000 for t in universe},
        cash_krw=100_000_000,
        blocked={t: "x" for t in blocked},
    )
    expect_sell = {line.symbol for line in plan.lines if line.action == RebalanceAction.SELL}
    expect_keep = {line.symbol for line in plan.lines if line.action in (RebalanceAction.HOLD, RebalanceAction.RESIZE)}
    expect_buy = {line.symbol for line in plan.lines if line.action == RebalanceAction.BUY}
    buyable = {t for t in ranks if t not in blocked}
    sells, kept, new = select(ranks, held, {t for t in held if t in blocked}, buyable, policy.slots, policy.keep_rank)
    assert (set(sells), set(kept), set(new)) == (expect_sell, expect_keep, expect_buy)


def test_sell_tax_and_delisting():
    sessions = _sessions(12)
    close = [100.0] * 8 + [np.nan] * 4
    m = _market(sessions, ["A"], close, close)
    sig = {d: Signal(ranked=(("A", 1.0),)) for d in sessions[:7]}
    r = simulate(_one(), m, sig, start=sessions[0], end=sessions[-1], tax_schedule=((date.min, 20.0),))
    assert r.n_delisted_sales == 1 and r.taxes_krw > 0 and r.equity[-1] < START_CAPITAL


# --- 3차(PREREG_v3): 모멘텀 가중 줄이기 ---


def test_v3_arms_are_preregistered():
    from sim.configs import V3_ARMS

    m1, m2 = V3_ARMS
    assert dict(m1.factor_weights) == {"momentum": 0.30, "flow": 0.20, "quality": 0.25, "valuation": 0.25}
    assert dict(m2.factor_weights) == {"momentum": 0.125, "flow": 0.1875, "quality": 0.375, "valuation": 0.3125}
    assert dict(m1.factor_weights_b) == {"momentum": 0.30, "revision": 0.30, "flow": 0.20, "quality": 0.10, "valuation": 0.10}
    assert dict(m2.factor_weights_b) == {"momentum": 0.10, "revision": 0.20, "flow": 0.15, "quality": 0.30, "valuation": 0.25}
    for v in V3_ARMS:
        assert v.required_factors == ("momentum", "flow", "quality", "valuation")
        assert v.keep_rank == 100 and not v.uses_rules
        assert sum(w for _, w in v.factor_weights) == pytest.approx(1.0)


def test_rescore_reproduces_signal_a_composite():
    """신호 A 합성 점수 = 있는 팩터끼리 scope 가중(.30/.20/.10/.10) 재정규화. 2026-10-23 실제 두 행."""
    from sim.scoring import rescore

    w = (("momentum", 0.30), ("flow", 0.20), ("quality", 0.10), ("valuation", 0.10))
    rows = [
        ("0220W0", {"momentum": 2.6389966015277517, "flow": 2.270035836088208, "quality": None, "valuation": None}),
        ("048910", {"momentum": 2.8451679633042835, "flow": 3.0, "quality": 0.3492389358431551, "valuation": 0.37171538010466515}),
    ]
    got = dict(rescore(rows, w, required=()))
    assert got["0220W0"] == pytest.approx(2.4914122953519344, rel=1e-12)
    assert got["048910"] == pytest.approx(2.179494029408667, rel=1e-12)


def test_rescore_drops_missing_required_and_breaks_ties_by_ticker():
    from sim.scoring import rescore

    w = (("momentum", 0.5), ("quality", 0.5))
    rows = [
        ("B", {"momentum": 1.0, "quality": 1.0}),
        ("A", {"momentum": 1.0, "quality": 1.0}),
        ("C", {"momentum": 9.0, "quality": None}),
    ]
    assert rescore(rows, w, required=("momentum", "quality")) == [("A", 1.0), ("B", 1.0)]
    assert rescore(rows, w, required=())[0] == ("C", 9.0)  # 필수가 없으면 빈 팩터는 빼고 재정규화
