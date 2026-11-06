"""빠른 시뮬레이터. 규칙은 CRITERIA.md "시뮬레이션"·"변형별 세부" 절이 정본이다.

수정주가 공간에서 소수 주식 수로 센다. 신호일 D 장 마감에 S07 `plan_rebalance` 와 같은 규칙으로 목표
주식 수를 정하고, 실행 시점 규칙의 가격에 슬리피지·수수료·거래세를 붙여 체결한다.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from sim.configs import Variant
from sim.data import Market

START_CAPITAL = 100_000_000.0
BP = 10_000.0
MA_WINDOW, VOL_WINDOW = 200, 60
VOL_SINCE = date(2011, 1, 3)
VOL_TOP_SHARE = 0.20
INV_VOL_WINDOW = 60
SMALL_BUY_SHARE = 0.03  # 진단: 이보다 작게 체결된 신규 매수는 다음 날 3% 정리에 걸린다


@dataclass(frozen=True)
class Signal:
    """신호일 하루의 순위표. `ranked` 는 1위부터 (종목, 합성 점수)."""

    ranked: tuple[tuple[str, float], ...]


@dataclass
class Result:
    variant: Variant
    sessions: tuple[date, ...]
    equity: np.ndarray
    traded_krw: float = 0.0
    fees_krw: float = 0.0
    taxes_krw: float = 0.0
    slippage_krw: float = 0.0
    n_orders: int = 0
    n_unfilled: int = 0
    n_jump_neutralized: int = 0
    n_delisted_sales: int = 0
    n_stop_sales: int = 0
    # σ 규칙·3% 정리(사전등록 "규칙" 절). 체결된 매도만 이유별로 센다: stop · take · take_full · trim
    rule_sales: dict[str, int] = field(default_factory=dict)
    n_sigma_missing: int = 0  # σ 를 못 낸 기준점 수(그 포지션엔 σ 규칙 없음)
    n_small_buys: int = 0  # 체결 금액이 직전 장 마감 NAV 의 3% 미만인 신규 매수 수
    # 규칙 전량 매도 뒤 바로 다음 리밸런싱에 다시 산 건: 다시 산 시가 ÷ 판 시가 − 1
    rebuys: list[float] = field(default_factory=list)
    # 규칙 판정 기록(검산용): (판정일, 종목, 이유, z, 비중, 기준점 날, σ)
    rule_events: list[tuple[date, str, str, float, float, date | None, float]] = field(default_factory=list)
    cash_ratio: np.ndarray = field(default_factory=lambda: np.zeros(0))
    exposure_by_signal: dict[date, float] = field(default_factory=dict)
    holdings_by_week: dict[date, frozenset[str]] = field(default_factory=dict)
    # 엔진 대조(G2)용 목표 테이프: 신호일 → (실행일, 종목별 목표 비중 = 목표 금액 ÷ 신호일 자산)
    targets_by_signal: dict[date, tuple[date, dict[str, float]]] = field(default_factory=dict)


FULL_RULE_SALES = ("stop", "take_full", "trim")


@dataclass(frozen=True)
class _Order:
    column: int
    delta_units: float  # 양수 매수, 음수 매도
    fraction: float | None = None  # 매도만: 체결 때 보유 수량의 이 비율을 판다(분할 익절)
    reason: str = "rebalance"  # rebalance · stop · take · take_full · trim


def signal_dates(sessions: tuple[date, ...], rule: str) -> set[date]:
    weekly = _last_of(sessions, lambda d: d.isocalendar()[:2])
    if rule == "daily":
        return set(sessions)
    if rule == "weekly":
        return set(weekly)
    if rule == "biweekly":
        return set(weekly[::2])
    if rule == "monthly":
        return set(_last_of(sessions, lambda d: (d.year, d.month)))
    if rule == "quarterly":
        return {d for d in _last_of(sessions, lambda d: (d.year, d.month)) if d.month in (3, 6, 9, 12)}
    raise ValueError(f"unknown rebalance rule — {rule}")


def weekly_dates(sessions: tuple[date, ...]) -> list[date]:
    return _last_of(sessions, lambda d: d.isocalendar()[:2])


def _last_of(sessions, key) -> list[date]:
    out: list[date] = []
    for i, d in enumerate(sessions):
        if i + 1 == len(sessions) or key(sessions[i + 1]) != key(d):
            out.append(d)
    return out


def sell_tax_bps(day: date, schedule: tuple[tuple[date, float], ...]) -> float:
    rate = 0.0
    for start, bps in schedule:
        if start <= day:
            rate = bps
    return rate


class _Regime:
    """코스피로 신호일 노출 배율을 정한다(PLAN 표 4, CRITERIA 국면 절)."""

    def __init__(self, market: Market) -> None:
        self._days = market.index_sessions
        close = market.kospi
        n = len(close)
        self._below_ma = np.zeros(n, dtype=bool)
        self._high_vol = np.zeros(n, dtype=bool)
        log_ret = np.full(n, np.nan)
        log_ret[1:] = np.log(close[1:] / close[:-1])
        history: list[float] = []
        for i in range(n):
            if i + 1 >= MA_WINDOW:
                self._below_ma[i] = close[i] < float(np.mean(close[i + 1 - MA_WINDOW : i + 1]))
            if i >= VOL_WINDOW:
                window = log_ret[i + 1 - VOL_WINDOW : i + 1]
                vol = float(np.std(window, ddof=1) * math.sqrt(252))
                if self._days[i] >= VOL_SINCE:
                    history.append(vol)
                    rank = sum(1 for v in history if v <= vol) / len(history)
                    self._high_vol[i] = rank > 1.0 - VOL_TOP_SHARE

    def exposure(self, day: date, rule: str | None) -> float:
        if rule is None:
            return 1.0
        i = bisect_right(self._days, day) - 1
        below, high = bool(self._below_ma[i]), bool(self._high_vol[i])
        if rule == "R1":
            return 0.5 if below else 1.0
        if rule == "R2":
            return 0.0 if below else 1.0
        if rule == "R3":
            return 0.5 if high else 1.0
        if rule == "R4":
            return (0.5 if below else 1.0) * (0.5 if high else 1.0)
        raise ValueError(f"unknown regime — {rule}")


def simulate(
    variant: Variant,
    market: Market,
    signals: dict[date, Signal],
    *,
    start: date,
    end: date,
    tax_schedule: tuple[tuple[date, float], ...],
    regime: _Regime | None = None,
    planner=None,
) -> Result:
    """`planner` 는 신호일 목표를 정하는 함수로 바꿔 끼우는 자리다(기본 `_plan`, 무작위 대조는 ROBUSTNESS.md 1절)."""
    plan_fn = planner or _plan
    sessions = market.sessions
    first = next(i for i, d in enumerate(sessions) if d >= start)
    last = max(i for i, d in enumerate(sessions) if d <= end)
    col = market.ticker_index()
    close = _forward_filled(market.adj_close)
    has_bar = ~np.isnan(market.adj_close)
    tradable = has_bar & ~(market.volume == 0)
    n = len(market.tickers)
    units = np.zeros(n)
    basis = np.zeros(n)
    cash = START_CAPITAL
    pending: dict[int, list[_Order]] = {}
    stopped: set[int] = set()
    rebalance_days = signal_dates(sessions, variant.rebalance) & set(signals)
    weeks = set(weekly_dates(sessions))
    regime = regime or (_Regime(market) if variant.regime else None)
    out = Result(variant=variant, sessions=sessions[first : last + 1], equity=np.zeros(last - first + 1))
    out.cash_ratio = np.zeros(last - first + 1)
    slip, fee = variant.slippage_bps / BP, variant.fee_bps / BP
    # 포지션 기준점(사전등록 "규칙" 절): 기준 세션 · 종목 기준가 · 지수 기준 · 소속 지수(0 코스피, 1 코스닥) · σ
    anchor_i = np.full(n, -1)
    anchor_px = np.full(n, np.nan)
    anchor_ix = np.full(n, np.nan)
    index_of = np.zeros(n, dtype=int)
    sigma = np.full(n, np.nan)
    if variant.uses_rules and (market.index_open is None or market.index_close is None or market.kosdaq is None):
        raise ValueError(f"rules need index and market boards — variant={variant.name}")
    rebalance_exec: set[int] = set()
    n_rebalance_exec = 0
    last_rule_sale: dict[int, tuple[float, int]] = {}  # 종목 → (판 시가, 그때까지 리밸런싱 실행 수)
    no_buy_until: dict[str, date] = {}  # 손절 재매수 금지: 종목 → 이 날까지의 신호일엔 사지 않음

    for i in range(first, last + 1):
        day = sessions[i]
        # 1) 원장이 접지 못한 기업행위 점프는 그날 수익률 0
        if i > first:
            jumped = np.flatnonzero(market.unadjusted_jump[i] & (units > 0))
            for j in jumped:
                ratio = close[i - 1, j] / close[i, j]
                units[j] *= ratio
                basis[j] /= ratio
                anchor_px[j] /= ratio
                out.n_jump_neutralized += 1
        # 2) 예약된 주문 체결(매도 먼저)
        if i in rebalance_exec:
            n_rebalance_exec += 1
        orders = pending.pop(i, [])
        if orders:
            price_row = market.adj_close[i] if variant.execution == "next_close" else market.adj_open[i]
            prev_equity = out.equity[i - first - 1] if i > first else START_CAPITAL
            fills: list[tuple[int, str, float, float]] = []
            cash = _execute(orders, price_row, units, basis, cash, day, slip, fee, tax_schedule, out, fills)
            for j, kind, qty, price in fills:
                if kind in FULL_RULE_SALES or kind == "take":
                    out.rule_sales[kind] = out.rule_sales.get(kind, 0) + 1
                if kind in FULL_RULE_SALES:
                    last_rule_sale[j] = (price, n_rebalance_exec)
                if kind == "open":
                    if qty * price < SMALL_BUY_SHARE * prev_equity:
                        out.n_small_buys += 1
                    sold = last_rule_sale.pop(j, None)
                    if sold is not None and sold[1] == n_rebalance_exec - 1:
                        out.rebuys.append(price / sold[0] - 1.0)
                if variant.uses_rules and (kind == "open" or (kind == "take" and units[j] > 0)):
                    k = 1 if market.kosdaq[i, j] else 0  # pyright: ignore[reportOptionalSubscript]  # reason: 위에서 판 존재 확인
                    anchor_i[j], anchor_px[j], index_of[j] = i, market.adj_open[i, j], k
                    anchor_ix[j] = market.index_open[k, i]  # pyright: ignore[reportOptionalSubscript]  # reason: 위에서 판 존재 확인
                    sigma[j] = excess_sigma(market, j, k, i, window=variant.sigma_window, min_obs=variant.sigma_min_obs)
                    if np.isnan(sigma[j]):
                        out.n_sigma_missing += 1
        # 3) 상장폐지: 마지막 가격 행 다음 날 마지막 종가로 판다
        gone = np.flatnonzero((units > 0) & (market.last_bar < i))
        for j in gone:
            value = units[j] * close[i, j]
            tax = value * sell_tax_bps(day, tax_schedule) / BP
            cash += value - value * fee - tax
            out.fees_krw += value * fee
            out.taxes_krw += tax
            out.traded_krw += value
            out.n_delisted_sales += 1
            units[j] = 0.0
            basis[j] = 0.0
        equity = cash + float(np.nansum(units * close[i]))
        out.equity[i - first] = equity
        out.cash_ratio[i - first] = cash / equity if equity > 0 else 0.0
        if day in weeks:
            out.holdings_by_week[day] = frozenset(market.tickers[j] for j in np.flatnonzero(units > 0))
        # 4) 손절 판정 → 다음 거래일 시가 매도
        if variant.stop is not None and i + 1 <= last:
            hit = np.flatnonzero(
                (units > 0) & has_bar[i] & (market.adj_close[i] <= basis * (1.0 - variant.stop))
            )
            for j in hit:
                if j in stopped:
                    continue
                pending.setdefault(i + 1, []).append(_Order(j, -units[j]))
                stopped.add(j)
                out.n_stop_sales += 1
        # 4b) σ 손절·분할 익절·3% 정리 판정 → 다음 거래일 시가 매도. 전량 매도가 절반 매도보다 먼저다
        full_sold_today: set[str] = set()
        if variant.uses_rules and i + 1 <= last:
            for j in np.flatnonzero((units > 0) & has_bar[i]):
                weight = units[j] * close[i, j] / equity
                z = _z(market, j, i, anchor_i, anchor_px, anchor_ix, index_of, sigma)
                held = i - anchor_i[j] + 1 if anchor_i[j] >= 0 else 0
                reason = _rule_reason(variant, weight, z, held)
                if reason is None:
                    continue
                anchor_day = sessions[anchor_i[j]] if anchor_i[j] >= 0 else None
                out.rule_events.append((day, market.tickers[j], reason, z, weight, anchor_day, float(sigma[j])))
                if reason == "take":
                    pending.setdefault(i + 1, []).append(_Order(j, -units[j] * 0.5, fraction=0.5, reason=reason))
                else:
                    pending.setdefault(i + 1, []).append(_Order(j, -units[j], reason=reason))
                    full_sold_today.add(market.tickers[j])
                if reason == "stop" and variant.stop_cooldown_days > 0:
                    no_buy_until[market.tickers[j]] = day + timedelta(days=variant.stop_cooldown_days)
        # 5) 신호일이면 목표 → 실행일 주문
        if day in rebalance_days:
            exec_i = i + (2 if variant.execution == "delay1" else 1)
            if exec_i > last:
                continue
            rebalance_exec.add(exec_i)
            exposure = regime.exposure(day, variant.regime) if regime else 1.0
            out.exposure_by_signal[day] = exposure
            projected = units.copy()
            for queued in pending.values():
                for order in queued:
                    projected[order.column] += order.delta_units
            plan, weights = plan_fn(
                variant, signals[day], col, projected, close[i], tradable[i], equity, exposure, market, i,
                exclude=full_sold_today | {t for t, until in no_buy_until.items() if day <= until},
            )
            out.targets_by_signal[day] = (sessions[exec_i], weights)
            if plan:
                pending.setdefault(exec_i, []).extend(plan)
            stopped.clear()
    return out


def _execute(orders, price_row, units, basis, cash, day, slip, fee, tax_schedule, out, fills=None) -> float:
    """`fills` 를 주면 체결마다 (종목, 이유, 수량, 시가)를 붙인다. 새 포지션이 열린 매수의 이유는 "open"."""
    sells = [o for o in orders if o.delta_units < 0]
    buys = [o for o in orders if o.delta_units > 0]
    tax_rate = sell_tax_bps(day, tax_schedule) / BP
    for order in sells:
        j, price = order.column, price_row[order.column]
        out.n_orders += 1
        if np.isnan(price) or units[j] <= 0:
            out.n_unfilled += 1
            continue
        qty = units[j] * order.fraction if order.fraction is not None else min(-order.delta_units, units[j])
        if fills is not None:
            fills.append((j, order.reason, qty, price))
        gross = qty * price * (1.0 - slip)
        cash += gross - gross * fee - gross * tax_rate
        out.traded_krw += gross
        out.fees_krw += gross * fee
        out.taxes_krw += gross * tax_rate
        out.slippage_krw += qty * price * slip
        units[j] -= qty
        if units[j] <= 1e-12:
            units[j] = 0.0
            basis[j] = 0.0
    fillable = [o for o in buys if not np.isnan(price_row[o.column])]
    out.n_orders += len(buys)
    out.n_unfilled += len(buys) - len(fillable)
    need = sum(o.delta_units * price_row[o.column] * (1.0 + slip) * (1.0 + fee) for o in fillable)
    scale = 1.0 if need <= cash or need <= 0 else max(cash, 0.0) / need
    for order in fillable:
        j, price = order.column, price_row[order.column]
        qty = order.delta_units * scale
        if qty <= 0:
            # 현금이 없어 살 수 없다. 평균 매입가를 0/0 으로 만들지 않게 건너뛴다(갈래 1 보고).
            continue
        paid = qty * price * (1.0 + slip)
        cash -= paid + paid * fee
        out.traded_krw += paid
        out.fees_krw += paid * fee
        out.slippage_krw += qty * price * slip
        if fills is not None and units[j] <= 0:
            fills.append((j, "open", qty, price))
        basis[j] = (units[j] * basis[j] + qty * price * (1.0 + slip)) / (units[j] + qty)
        units[j] += qty
    return cash


def excess_sigma(market: Market, j: int, k: int, i: int, *, window: int, min_obs: int) -> float:
    """기준점 세션 i 직전 `window` 세션의 하루 초과수익(종목 − 지수 k) 표본 표준편차. 유효 수가 모자라면 nan.

    가격 없는 날과 원장이 접지 못한 기업행위 점프일은 뺀다.
    """
    lo = max(1, i - window)
    stock = market.adj_close[lo - 1 : i, j]
    level = market.index_close[k, lo - 1 : i]  # pyright: ignore[reportOptionalSubscript]  # reason: 호출부(simulate)가 판 존재 확인
    with np.errstate(invalid="ignore", divide="ignore"):
        excess = stock[1:] / stock[:-1] - level[1:] / level[:-1]
    ok = np.isfinite(excess) & ~market.unadjusted_jump[lo:i, j]
    if int(ok.sum()) < min_obs:
        return float("nan")
    return float(np.std(excess[ok], ddof=1))


def _z(market: Market, j: int, i: int, anchor_i, anchor_px, anchor_ix, index_of, sigma) -> float:
    """기준점 이후 지수 대비 초과수익 ÷ (σ·√보유 세션 수). 기준점이나 σ 가 없으면 nan."""
    if anchor_i[j] < 0 or not np.isfinite(sigma[j]) or sigma[j] <= 0:
        return float("nan")
    k = index_of[j]
    excess = market.adj_close[i, j] / anchor_px[j] - market.index_close[k, i] / anchor_ix[j]  # pyright: ignore[reportOptionalSubscript]  # reason: 호출부(simulate)가 판 존재 확인
    return float(excess / (sigma[j] * math.sqrt(i - anchor_i[j] + 1)))


def _rule_reason(variant: Variant, weight: float, z: float, held_sessions: int) -> str | None:
    """그날 장 마감 판정. 전량 매도(손절·정리)가 절반 매도보다 먼저다. nan 비교는 거짓이라 σ 없으면 σ 규칙은 안 걸린다.

    손절은 기준점 이후 장 마감 수(`held_sessions`, 기준점 날 = 1)가 `stop_min_days` 이상일 때만 본다.
    """
    if variant.stop_sigma is not None and held_sessions >= variant.stop_min_days and z <= -variant.stop_sigma:
        return "stop"
    if variant.trim_weight is not None and weight < variant.trim_weight:
        return "trim"
    if variant.take_sigma is not None and z >= variant.take_sigma:
        if variant.trim_weight is not None and weight * 0.5 < variant.trim_weight:
            return "take_full"
        return "take"
    return None


def _plan(variant, signal, col, units, price, tradable, equity, exposure, market, i, exclude=frozenset()):
    """S07 `plan_rebalance` 와 같은 고르기. 반환은 (실행일 주문(주식 수 변화), 종목별 목표 비중).

    `exclude` 는 그날 규칙 전량 매도 신호가 난 종목 — 이 신호일엔 새로 사지 않는다.
    """
    ranked = [(t, s) for t, s in signal.ranked if t in col]
    if variant.min_adv_krw is not None:
        adv = market.adv20[i]
        ranked = [(t, s) for t, s in ranked if not np.isnan(adv[col[t]]) and adv[col[t]] >= variant.min_adv_krw]
    ranks = {t: r for r, (t, _) in enumerate(ranked, start=1)}
    scores = dict(ranked)
    held = [market.tickers[j] for j in np.flatnonzero(units > 0)]
    slots = len(ranked) if variant.universe_ew else variant.slots
    keep_rank = len(ranked) if variant.universe_ew else variant.keep_rank
    buyable = {
        t
        for t in ranks
        if tradable[col[t]] and not np.isnan(price[col[t]]) and price[col[t]] > 0 and t not in exclude
    }
    blocked = {t for t in held if not tradable[col[t]]}
    sells, kept, new = select(ranks, held, blocked, buyable, slots, keep_rank)

    investable = equity * (1.0 - variant.cash) * exposure
    selected = [t for t in kept if t not in blocked] + new
    weights = _weights(variant, selected, [t for t in kept if t in blocked], scores, ranks, ranked, market, i, col)
    per_slot = investable / slots if slots else 0.0
    orders: list[_Order] = []
    target_weights: dict[str, float] = {}
    for t in kept:
        if t in blocked:
            j = col[t]
            target_weights[t] = units[j] * price[j] / equity if equity > 0 else 0.0
    for t in sells:
        j = col[t]
        if units[j] > 0:
            orders.append(_Order(j, -units[j]))
    fee = variant.fee_bps / BP
    for t in selected:
        j = col[t]
        target_value = per_slot * len(selected) * weights[t]
        current_value = units[j] * price[j]
        target_units = target_value / (price[j] * (1.0 + fee))
        if units[j] > 0:
            if abs(current_value - target_value) <= target_value * variant.band:
                target_weights[t] = current_value / equity if equity > 0 else 0.0
                continue
        target_weights[t] = target_units * price[j] / equity if equity > 0 else 0.0
        delta = target_units - units[j]
        if abs(delta) > 1e-12:
            orders.append(_Order(j, delta))
    return orders, target_weights


def select(
    ranks: dict[str, int],
    held: list[str],
    blocked: set[str],
    buyable: set[str],
    slots: int,
    keep_rank: int,
) -> tuple[list[str], list[str], list[str]]:
    """(팔 종목, 남길 종목, 새로 살 종목). S07 `plan_rebalance` 의 고르기와 같다."""
    sells: list[str] = []
    kept: list[str] = []
    for t in sorted(held):
        r = ranks.get(t)
        if t in blocked or (r is not None and r <= keep_rank):
            kept.append(t)
        else:
            sells.append(t)
    kept.sort(key=lambda t: (t not in blocked, ranks.get(t, 0), t))
    sells.extend(kept[slots:])
    kept = kept[:slots]
    free = slots - len(kept)
    new: list[str] = []
    for r, t in sorted((r, t) for t, r in ranks.items()):
        if free == 0 or r > keep_rank:
            break
        if t in held or t not in buyable:
            continue
        new.append(t)
        free -= 1
    return sells, kept, new


def _weights(variant, selected, blocked_kept, scores, ranks, ranked, market, i, col) -> dict[str, float]:
    """선정 종목(막힌 보유 종목 제외) 비중. 합은 1."""
    if not selected:
        return {}
    if variant.weighting == "equal" or len(selected) == 1:
        return {t: 1.0 / len(selected) for t in selected}
    if variant.weighting == "proportional":  # 신호 점수가 곧 비중 원점수(알파 탐색 F2: 시총·기울인 시총)
        total = sum(scores[t] for t in selected)
        return {t: scores[t] / total for t in selected}
    if variant.weighting == "rank":
        order = sorted(selected, key=lambda t: (ranks.get(t, 10**9), t))
        raw = {t: float(len(order) - k) for k, t in enumerate(order)}
    elif variant.weighting == "score":
        raw = _margin_strengths(selected, scores, ranked)
    elif variant.weighting == "inv_vol":
        raw = {}
        for t in selected:
            j = col[t]
            window = market.adj_close[max(0, i - INV_VOL_WINDOW) : i + 1, j]
            window = window[~np.isnan(window)]
            rets = np.diff(np.log(window)) if len(window) > 2 else np.array([])
            vol = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
            raw[t] = 1.0 / vol if vol > 0 else 0.0
        if not any(raw.values()):
            raw = {t: 1.0 for t in selected}
        fallback = min((v for v in raw.values() if v > 0), default=1.0)
        raw = {t: (v if v > 0 else fallback) for t, v in raw.items()}
    else:
        raise ValueError(f"unknown weighting — {variant.weighting}")
    total = sum(raw.values())
    if total <= 0:
        return {t: 1.0 / len(selected) for t in selected}
    return {t: v / total for t, v in raw.items()}


def _margin_strengths(selected, scores, ranked) -> dict[str, float]:
    """워크벤치 `domain/portfolio/_compiler.py::_margin_strengths` 의 롱 쪽 규칙."""
    chosen = [scores[t] for t in selected]
    lowest, highest = min(chosen), max(chosen)
    reference = lowest - (highest - lowest) / (len(chosen) - 1)
    chosen_set = set(selected)
    below = [s for t, s in ranked if t not in chosen_set and s <= lowest]
    if below:
        reference = min(max(below), reference)
    strengths = {t: scores[t] - reference for t in selected}
    if not any(v > 0 for v in strengths.values()):
        return {t: 1.0 for t in selected}
    return strengths


def _forward_filled(values: np.ndarray) -> np.ndarray:
    out = values.copy()
    for i in range(1, out.shape[0]):
        missing = np.isnan(out[i])
        out[i, missing] = out[i - 1, missing]
    return out
