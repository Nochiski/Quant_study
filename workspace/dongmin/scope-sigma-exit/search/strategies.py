"""후보를 신호·지수 비중으로 바꿔 돌린다(PROTOCOL.md "공통 시뮬레이션")."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from search.family import Candidate
from search.features import (
    clean_index,
    composite,
    core_satellite,
    first_bar,
    pct_rank,
    price_features,
    tilted_caps,
    top_by_cap,
    vol60,
)
from search.panel import SECTORS, Panel
from sim.configs import Variant
from sim.engine import BP, START_CAPITAL, Result, Signal, signal_dates, simulate

SIGNAL_A = Path(os.path.expanduser("~/quant-data/research/2026-10-26-trading-rules/signal_a/scores.parquet"))
FIRST_SIGNAL = date(2020, 1, 31)
SECTOR_LOOKBACK, SECTOR_TOP, TREND_WINDOW = 126, 3, 200
ETF_EXPENSE = 0.0015


def month_ends(panel: Panel, start: date, end: date) -> list[int]:
    s = panel.market.sessions
    days = signal_dates(s, "monthly")
    return sorted(s.index(d) for d in days if start <= d <= end)


def stock_variant(c: Candidate, cost_mult: float = 1.0) -> Variant:
    common = dict(rebalance="monthly", fee_bps=15.0 * cost_mult, slippage_bps=10.0 * cost_mult)
    if c.kind == "stock_ew":
        return Variant(name=c.cid, axis="F", slots=200, keep_rank=200, band=0.0, cash=0.02, universe_ew=True, **common)
    if c.kind in ("cap", "tilt", "exclude", "core_sat", "avoid"):  # F2.md · F4.md · F7_EVENTS.md
        return Variant(name=c.cid, axis="F", band=0.25, cash=0.01, universe_ew=True, weighting="proportional", **common)
    return Variant(name=c.cid, axis="F", slots=30, keep_rank=60, band=0.25, cash=0.02, **common)


class SignalBuilder:
    """신호일마다 유니버스·지표·합성 순위를 만든다. 가격 지수·신호 A 팩터는 한 번만 읽는다."""

    def __init__(self, panel: Panel, days: list[int], alt=None) -> None:
        m = panel.market
        self.panel = panel
        self.alt = alt  # F3: search.alt.AltFeatures
        self.p = clean_index(m.adj_close, m.unadjusted_jump)
        self.start = first_bar(m.adj_close)
        self.bucket = self._signal_a([m.sessions[i] for i in days])
        self._feats: dict[int, dict[str, np.ndarray]] = {}
        self._sat: dict[str, frozenset[int]] = {}  # F4: 후보별 직전 위성(신호일 순서로 부른다)

    def _signal_a(self, days: list[date]) -> dict[date, dict[str, np.ndarray]]:
        t_index = self.panel.market.ticker_index()
        n = len(self.panel.market.tickers)
        out = {d: {"QUAL": np.full(n, np.nan), "VALU": np.full(n, np.nan)} for d in days}
        con = duckdb.connect()
        import pyarrow as pa

        con.register("days", pa.table({"d": pa.array(days, pa.date32())}))
        for d, t, q, v in con.execute(
            f"select date, ticker, quality, valuation from read_parquet('{SIGNAL_A}') s join days on s.date = days.d"
        ).fetchall():
            j = t_index.get(t)
            if j is not None:
                out[d]["QUAL"][j] = np.nan if q is None else q
                out[d]["VALU"][j] = np.nan if v is None else v
        return out

    def members(self, universe: str, i: int) -> np.ndarray:
        pn, m = self.panel, self.panel.market
        alive = pn.common[i] & ~np.isnan(m.adj_close[i])
        if universe == "U200":
            return top_by_cap(pn.mktcap[i], alive & pn.on_kospi[i], 200)
        if universe == "U500":
            return top_by_cap(pn.mktcap[i], alive, 500)
        if universe == "UQ150":  # 보통주는 시장이 늘 있으므로 코스피가 아니면 코스닥
            return top_by_cap(pn.mktcap[i], alive & ~pn.on_kospi[i], 150)
        raise ValueError(f"unknown universe — {universe}")

    def signal(self, c: Candidate, i: int) -> Signal:
        m = self.panel.market
        members = self.members(c.universe, i)
        if c.kind == "stock_ew":
            order = np.flatnonzero(members)
            return Signal(ranked=tuple((m.tickers[j], 0.0) for j in sorted(order, key=lambda j: m.tickers[j])))
        if c.kind == "core_sat":
            score = composite(self._features(i), c.spec, members)
            w, self._sat[c.cid] = core_satellite(self.panel.mktcap[i], members, score, prev=self._sat.get(c.cid, frozenset()))
            idx = sorted(w, key=lambda j: (-w[j], m.tickers[j]))
            return Signal(ranked=tuple((m.tickers[j], w[j]) for j in idx))
        if c.kind == "avoid":  # F7: 표시(값 1)된 종목을 빼고 시총 가중
            flag = self._features(i)[c.spec[0][0]]
            raw = np.where(members & (flag != 1), self.panel.mktcap[i], np.nan)
            idx = sorted(np.flatnonzero(np.isfinite(raw) & (raw > 0)), key=lambda j: (-raw[j], m.tickers[j]))
            return Signal(ranked=tuple((m.tickers[j], float(raw[j])) for j in idx))
        if c.kind in ("cap", "tilt", "exclude"):
            r = self._r(c, i, members) if c.spec else np.full(len(m.tickers), np.nan)
            raw = np.where(members, tilted_caps(self.panel.mktcap[i], r, mode=c.kind), np.nan)
            idx = sorted(np.flatnonzero(np.isfinite(raw) & (raw > 0)), key=lambda j: (-raw[j], m.tickers[j]))
            return Signal(ranked=tuple((m.tickers[j], float(raw[j])) for j in idx))
        score = composite(self._features(i), c.spec, members)
        idx = np.flatnonzero(np.isfinite(score))
        idx = sorted(idx, key=lambda j: (-score[j], m.tickers[j]))
        return Signal(ranked=tuple((m.tickers[j], float(score[j])) for j in idx))

    def _features(self, i: int) -> dict[str, np.ndarray]:
        m = self.panel.market
        if i not in self._feats:
            feats = {"VOL60": vol60(m.adj_close, m.unadjusted_jump, i), **price_features(self.p, self.start, i)}
            self._feats[i] = feats | self.bucket[m.sessions[i]] | (self.alt.features(i) if self.alt else {})
        return self._feats[i]

    def _r(self, c: Candidate, i: int, members: np.ndarray) -> np.ndarray:
        """합성 지표의 유니버스 안 백분위(0~1). 합성 지표 = 성분 백분위 평균을 다시 백분위로."""
        return pct_rank(composite(self._features(i), c.spec, members), members)


def run_stock(
    c: Candidate, panel: Panel, builder: SignalBuilder, days: list[int], end: date, tax, cost_mult: float = 1.0,
    start: date = FIRST_SIGNAL,
) -> Result:
    signals = {panel.market.sessions[i]: builder.signal(c, i) for i in days}
    return simulate(stock_variant(c, cost_mult), panel.market, signals, start=start, end=end, tax_schedule=tax)


def index_targets(c: Candidate, panel: Panel, i: int) -> dict[str, float]:
    if c.kind == "sector":
        mom = {n: panel.index_close[n][i] / panel.index_close[n][i - SECTOR_LOOKBACK] - 1.0 for n in SECTORS}
        top = sorted(mom, key=lambda n: (-mom[n], n))[:SECTOR_TOP]
        return {n: 1.0 / SECTOR_TOP for n in top}
    if c.kind == "trend":
        k = panel.index_close["코스피 200"]
        return {"코스피 200": 1.0} if k[i] > float(np.mean(k[i - TREND_WINDOW + 1 : i + 1])) else {}
    raise ValueError(f"not an index candidate — {c.cid}")


def run_index(c: Candidate, panel: Panel, days: list[int], end: date, cost_mult: float = 1.0, start: date = FIRST_SIGNAL) -> Result:
    """ETF 가정 지수 전략. 신호일 장 마감 목표 → 다음 거래일 시가 체결, 거래세 없음, 보수 연 0.15%."""
    s = panel.market.sessions
    first = s.index(start)
    last = max(i for i, d in enumerate(s) if d <= end)
    rate = (15.0 + 10.0) * cost_mult / BP
    units: dict[str, float] = {}
    cash = START_CAPITAL
    pending: dict[int, dict[str, float]] = {i + 1: index_targets(c, panel, i) for i in days if i + 1 <= last}
    out = Result(variant=Variant(name=c.cid, axis="F"), sessions=s[first : last + 1], equity=np.zeros(last - first + 1))
    def fill(n: str, i: int) -> float:  # 지수 시가가 빈 날은 그날 종가로 체결(F6_FULL.md)
        o = panel.index_open[n][i]
        return float(o) if np.isfinite(o) and o > 0 else float(panel.index_close[n][i])  # 시가 0 도 결측으로 본다

    for i in range(first, last + 1):
        if i in pending:
            target = pending[i]
            value_open = cash + sum(u * fill(n, i) for n, u in units.items())
            current = {n: u * fill(n, i) for n, u in units.items()}
            goal = {n: w * value_open for n, w in target.items()}
            traded = sum(abs(goal.get(n, 0.0) - current.get(n, 0.0)) for n in sorted(set(goal) | set(current)))
            cost = traded * rate
            out.traded_krw += traded
            out.fees_krw += cost
            invest = value_open - cost
            units = {n: w * invest / fill(n, i) for n, w in target.items()}
            cash = invest - sum(w * invest for w in target.values())
        held = sum(u * panel.index_close[n][i] for n, u in units.items())
        if i > first and held > 0:  # 보수: 그날 보유 평가액의 연 0.15% ÷ 252
            fee = held * ETF_EXPENSE / 252
            scale = (held - fee) / held
            units = {n: u * scale for n, u in units.items()}
            held -= fee
            out.fees_krw += fee
        out.equity[i - first] = cash + held
    return out
