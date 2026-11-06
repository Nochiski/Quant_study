"""F3 지표: 신용잔고·대차잔고·외국인 수급·영업이익 변화(F3.md "지표" 절)."""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import numpy as np

from search.panel import LEDGER, Panel
from sim.data import _paths

PUB_LAG, CHG_WINDOW, FLOW_WINDOW, FLOW_MIN, REPORT_MAX_AGE = 2, 20, 60, 40, 120


def lagged_change(mat: np.ndarray, i: int, lag: int, window: int) -> np.ndarray:
    """mat[i − lag] − mat[i − lag − window]."""
    return mat[i - lag] - mat[i - lag - window]


def rolling_sum(mat: np.ndarray, i: int, window: int, min_obs: int) -> np.ndarray:
    """i 까지 window 세션 합. 값 있는 날이 min_obs 미만이면 nan."""
    w = mat[i - window + 1 : i + 1]
    n = np.isfinite(w).sum(axis=0)
    return np.where(n >= min_obs, np.nansum(w, axis=0), np.nan)


@dataclass(frozen=True)
class Report:
    rcept_dt: date
    bsns_year: int
    report_code: str
    fs_div: str
    op_profit: float


def op_yoy(reports: list[Report], d: date, max_age_days: int = REPORT_MAX_AGE) -> float:
    """공시일 ≤ d 인 가장 최근 보고서(같은 기간이면 연결 우선)의 영업이익 − 전년 같은 보고서·같은 기준 영업이익.

    `reports` 는 공시일 오름차순. 없거나 오래됐거나 전년 값이 없으면 nan.
    """
    seen = [r for r in reports[: bisect_right([r.rcept_dt for r in reports], d)]]
    if not seen:
        return float("nan")
    latest = max(seen, key=lambda r: (r.rcept_dt, r.fs_div == "CFS"))
    same_period = [r for r in seen if (r.bsns_year, r.report_code) == (latest.bsns_year, latest.report_code)]
    cur = next((r for r in same_period if r.fs_div == "CFS"), same_period[0])
    if (d - cur.rcept_dt).days > max_age_days:
        return float("nan")
    prev = [r for r in seen if (r.bsns_year, r.report_code, r.fs_div) == (cur.bsns_year - 1, cur.report_code, cur.fs_div)]
    if not prev or not np.isfinite(cur.op_profit) or not np.isfinite(prev[-1].op_profit):
        return float("nan")
    return cur.op_profit - prev[-1].op_profit


class AltFeatures:
    """원장에서 신용·대차·수급 일별 판과 정기보고서 목록을 한 번 읽는다."""

    def __init__(self, panel: Panel) -> None:
        self.panel = panel
        meta = json.loads((LEDGER / "_catalog_meta.json").read_text())
        self.builds = meta["builds"]
        self.con = duckdb.connect()
        self.credit = self._daily("credit_daily", "whol_loan_rmnd_rate_pct")
        self.lend = self._daily("short_daily", "lending_balance_kiwoom_krw")
        self.frgn = self._daily("flow_daily", "frgnr_invsr_krw")
        self.reports = self._reports()

    def _daily(self, table: str, column: str) -> np.ndarray:
        m = self.panel.market
        s_index, t_index = m.session_index(), m.ticker_index()
        out = np.full((len(m.sessions), len(m.tickers)), np.nan)
        for d, t, v in self.con.execute(
            f"select date, ticker, {column}::double from read_parquet('{_paths(LEDGER, self.builds, table)}', union_by_name = true) "
            f"where date between ? and ? and {column} is not null",
            [m.sessions[0], m.sessions[-1]],
        ).fetchall():
            i, j = s_index.get(d), t_index.get(t)
            if i is not None and j is not None:
                out[i, j] = v
        return out

    def _reports(self) -> dict[str, list[Report]]:
        t_index = self.panel.market.ticker_index()
        rows = self.con.execute(
            f"""
            select c.ticker, f.rcept_dt, f.bsns_year, f.report_code, f.fs_div, f.op_profit::double
            from read_parquet('{_paths(LEDGER, self.builds, 'fin_std')}', union_by_name = true) f
            join read_parquet('{_paths(LEDGER, self.builds, 'corp_ticker')}', union_by_name = true) c
              on c.corp_code = f.corp_code and c.is_common
            where f.rcept_dt is not null
            order by c.ticker, f.rcept_dt
            """
        ).fetchall()
        out: dict[str, list[Report]] = {}
        for t, rd, y, code, div, op in rows:
            if t in t_index:
                out.setdefault(t, []).append(Report(rd, int(y), str(code), str(div), float("nan") if op is None else op))
        return out

    def features(self, i: int) -> dict[str, np.ndarray]:
        m, cap = self.panel.market, self.panel.mktcap
        d = m.sessions[i]
        with np.errstate(invalid="ignore", divide="ignore"):
            lend_ratio = self.lend / cap
            frgn = rolling_sum(self.frgn, i, FLOW_WINDOW, FLOW_MIN) / cap[i]
            op = np.array([op_yoy(self.reports.get(t, []), d) for t in m.tickers]) / cap[i]
        return {
            "CRDT_LVL": self.credit[i - PUB_LAG],
            "CRDT_CHG": lagged_change(self.credit, i, PUB_LAG, CHG_WINDOW),
            "LEND_CHG": lagged_change(lend_ratio, i, PUB_LAG, CHG_WINDOW),
            "FRGN60": frgn,
            "OP_YOY": op,
        }


CB_WINDOW, BUY_WINDOW = 365, 182


def event_flags(events: dict[str, list[tuple[date, float]]], tickers: tuple[str, ...], d: date, window_days: int) -> tuple[np.ndarray, np.ndarray]:
    """종목별 (d − window, d] 안 공시 수와 금액 합. events 는 종목 → (공시일, 금액) 오름차순."""
    lo = d - timedelta(days=window_days)
    count = np.zeros(len(tickers))
    amount = np.zeros(len(tickers))
    for j, t in enumerate(tickers):
        for day, amt in events.get(t, ()):
            if lo < day <= d:
                count[j] += 1
                amount[j] += amt
    return count, amount


class EventFeatures:
    """F7: corp_event 의 CB 발행·자사주 취득 결정 공시."""

    def __init__(self, panel: Panel) -> None:
        self.panel = panel
        builds = json.loads((LEDGER / "_catalog_meta.json").read_text())["builds"]
        self.events: dict[str, dict[str, list[tuple[date, float]]]] = {"cb_issue": {}, "treasury_buy": {}}
        for kind, t, day, amt in duckdb.connect().execute(
            f"select event_type, ticker, announce_date, coalesce(amount_krw, 0)::double "
            f"from read_parquet('{_paths(LEDGER, builds, 'corp_event')}', union_by_name = true) "
            f"where event_type in ('cb_issue', 'treasury_buy') and announce_date is not null order by announce_date"
        ).fetchall():
            self.events[kind].setdefault(t, []).append((day, amt))

    def features(self, i: int) -> dict[str, np.ndarray]:
        m = self.panel.market
        d = m.sessions[i]
        cb, _ = event_flags(self.events["cb_issue"], m.tickers, d, CB_WINDOW)
        n_buy, amt = event_flags(self.events["treasury_buy"], m.tickers, d, BUY_WINDOW)
        with np.errstate(invalid="ignore", divide="ignore"):
            buy = np.where(n_buy > 0, amt / self.panel.mktcap[i], np.nan)
        return {"CB12": (cb > 0).astype(float), "BUYAMT": buy}
