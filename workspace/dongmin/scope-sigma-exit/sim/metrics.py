"""지표와 검정. 정의는 CRITERIA.md "지표와 검정" 절이 정본이다."""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date

import numpy as np

from sim.engine import Result, weekly_dates

CYCLES = {
    "C2": (date(2020, 3, 19), date(2022, 9, 30)),
    "C3": (date(2022, 9, 30), date(2025, 4, 9)),
    "C4": (date(2025, 4, 9), date(2026, 8, 20)),
}
# 코스피 20% 등락 전환점(hypothesis/WINDOWS.md). 앞 구간 국면 = 그 전환점으로 끝나는 쪽
TURNS = (
    (date(2020, 3, 19), "down"),
    (date(2021, 7, 6), "up"),
    (date(2022, 9, 30), "down"),
    (date(2024, 7, 11), "up"),
    (date(2025, 4, 9), "down"),
    (date(2026, 6, 22), "up"),
    (date(2026, 7, 30), "down"),
)
AFTER_LAST_TURN = "up"


def _at_or_before(sessions, day: date) -> int:
    return bisect_right(sessions, day) - 1


def cagr_between(result: Result, start: date, end: date) -> float:
    s = result.sessions
    i, j = _at_or_before(s, start), _at_or_before(s, end)
    if i < 0:
        i = 0
    years = (s[j] - s[i]).days / 365.25
    return (result.equity[j] / result.equity[i]) ** (1.0 / years) - 1.0


def cagr(result: Result) -> float:
    return cagr_between(result, result.sessions[0], result.sessions[-1])


def mdd(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0))


def weekly_returns(result: Result) -> dict[date, float]:
    pos = {d: i for i, d in enumerate(result.sessions)}
    days = [d for d in weekly_dates(result.sessions) if d in pos]
    out: dict[date, float] = {}
    for a, b in zip(days, days[1:]):
        out[b] = result.equity[pos[b]] / result.equity[pos[a]] - 1.0
    return out


def newey_west_t(diffs: np.ndarray) -> tuple[float, float, int]:
    """평균 0 검정의 Newey-West t, 정규 근사 양측 p, 시차."""
    n = len(diffs)
    lag = int(math.floor(4 * (n / 100) ** (2 / 9)))
    mean = float(np.mean(diffs))
    e = diffs - mean
    gamma0 = float(np.dot(e, e)) / n
    s = gamma0
    for k in range(1, lag + 1):
        gk = float(np.dot(e[k:], e[:-k])) / n
        s += 2.0 * (1.0 - k / (lag + 1)) * gk
    se = math.sqrt(s / n) if s > 0 else float("nan")
    t = mean / se if se and se > 0 else float("nan")
    p = math.erfc(abs(t) / math.sqrt(2)) if not math.isnan(t) else float("nan")
    return t, p, lag


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm 단계 하강. 유의(True)인 이름."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    out = {k: False for k in order}
    for rank, name in enumerate(order):
        if pvalues[name] <= alpha / (m - rank):
            out[name] = True
        else:
            break
    return out


def sharpe(result: Result, base_rate) -> float:
    """일별 초과수익의 연율 샤프. 무위험수익률 = 직전 세션 기준금리 × 달력 일수 / 365."""
    s, eq = result.sessions, result.equity
    excess = []
    for i in range(1, len(s)):
        r = eq[i] / eq[i - 1] - 1.0
        rate = base_rate(s[i - 1])
        rf = (rate or 0.0) / 100.0 * (s[i] - s[i - 1]).days / 365.0
        excess.append(r - rf)
    x = np.array(excess)
    return float(np.mean(x) / np.std(x, ddof=1) * math.sqrt(252))


def turnover(result: Result) -> float:
    years = (result.sessions[-1] - result.sessions[0]).days / 365.25
    return result.traded_krw / float(np.mean(result.equity)) / years


def jaccard_mean(a: Result, b: Result) -> float:
    vals = []
    for day, held in a.holdings_by_week.items():
        other = b.holdings_by_week.get(day)
        if other is None:
            continue
        union = held | other
        if union:
            vals.append(len(held & other) / len(union))
    return float(np.mean(vals)) if vals else float("nan")


def cycle_phase(day: date) -> str:
    for turn, phase in TURNS:
        if day <= turn:
            return phase
    return AFTER_LAST_TURN


def regime_breakdown(result: Result, labels: dict[date, str]) -> dict[str, dict[str, float]]:
    """국면 이름별 연율 수익률(그 국면 날들의 일간 수익률 복리를 252일로 환산)과 날짜 수."""
    s, eq = result.sessions, result.equity
    buckets: dict[str, list[float]] = {}
    for i in range(1, len(s)):
        label = labels.get(s[i])
        if label is None:
            continue
        buckets.setdefault(label, []).append(eq[i] / eq[i - 1])
    out = {}
    for label, growth in buckets.items():
        total = float(np.prod(growth))
        out[label] = {"days": len(growth), "annualized": total ** (252 / len(growth)) - 1.0}
    return out


def index_cagr(days: tuple[date, ...], close: np.ndarray, start: date, end: date) -> float:
    i, j = _at_or_before(days, start), _at_or_before(days, end)
    years = (days[j] - days[i]).days / 365.25
    return (close[j] / close[i]) ** (1.0 / years) - 1.0
