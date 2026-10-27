"""시장 단면 지표 — 눌림목 점수와 투매일 비율(연구 PREREG2 의 P1·P2 와 같은 정의).

- 유니버스: 그날 보통주·상장·비정지·비관리, 상장 60거래일 이상, 20일 평균 거래대금 10억 원 이상.
- 눌림목 점수: 유니버스 안 백분위 pct(P52) + pct(−R20) 를 다시 유니버스 안 백분위(0~1)로.
  P52 = 종가 ÷ 최근 250봉 고가 최댓값, R20 = 종가 ÷ 20봉 전 종가 − 1. 상위 20% = 눌림목, 하위 30% = 피할 쪽.
- 투매일: 유니버스 중 RSI14 < 30 종목 비율 ≥ 25%.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from . import data
from . import indicators as ind

PANIC_FRACTION = 0.25
TOP_SHARE = 0.20
BOTTOM_SHARE = 0.30
ADV_KRW = 1e9
LISTING_AGE = 60
LOOKBACK_ROWS = 260


def pct_rank(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """mask 안 종목끼리 0~1 백분위(평균 순위). 밖·NaN 은 NaN."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape[0], np.nan)
    idx = np.flatnonzero(mask & ~np.isnan(x))
    if idx.size == 0:
        return out
    v = x[idx]
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(idx.size)
    ranks[order] = np.arange(idx.size, dtype=float)
    _, inv, counts = np.unique(v, return_inverse=True, return_counts=True)
    ranks = np.bincount(inv, weights=ranks)[inv] / counts[inv]
    out[idx] = ranks / max(idx.size - 1, 1)
    return out


def pullback_score(p52: np.ndarray, r20: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """눌림목 점수(유니버스 안 0~1 백분위)."""
    ok = mask & ~np.isnan(p52) & ~np.isnan(r20)
    s = pct_rank(p52, ok) + pct_rank(-np.asarray(r20, dtype=float), ok)
    return pct_rank(s, ok)


def place(p52_x: float, r20_x: float, p52: np.ndarray, r20: np.ndarray, mask: np.ndarray) -> float:
    """유니버스 밖 종목(ETF·우선주 등)을 유니버스 분포에 놓았을 때의 눌림목 점수(참고용)."""
    ok = mask & ~np.isnan(p52) & ~np.isnan(r20)
    if not ok.any() or np.isnan(p52_x) or np.isnan(r20_x):
        return float("nan")
    a = pct_rank(p52, ok)[ok]
    b = pct_rank(-np.asarray(r20, dtype=float), ok)[ok]
    s_uni = a + b
    s_x = float((p52[ok] < p52_x).mean()) + float((-r20[ok] < -r20_x).mean())
    return float((s_uni < s_x).mean())


def pullback_label(score: float) -> str:
    """점수 → 화면 라벨. 상위 20%(≥0.8) 눌림목, 하위 30%(≤0.3) 피할 쪽."""
    if np.isnan(score):
        return "계산 불가"
    if score >= 1 - TOP_SHARE:
        return "눌림목 상위 20%"
    if score <= BOTTOM_SHARE:
        return "하위 30% — 피할 쪽"
    return "중간"


def panic_fraction(rsi: np.ndarray, mask: np.ndarray) -> float:
    """유니버스 중 RSI14 < 30 비율(RSI 가 정의된 종목만)."""
    ok = mask & ~np.isnan(rsi)
    return float((rsi[ok] < 30).mean()) if ok.any() else float("nan")


@dataclass(frozen=True, eq=False)
class Snapshot:
    date: str
    tickers: np.ndarray
    in_universe: np.ndarray
    p52: np.ndarray
    r20: np.ndarray
    rsi: np.ndarray
    score: np.ndarray  # 유니버스 안 눌림목 점수, 밖은 NaN
    panic: float

    def panic_tickers(self) -> list[str]:
        ok = self.in_universe & ~np.isnan(self.rsi) & (self.rsi < 30)
        return [str(t) for t in self.tickers[ok]]


def _cache_dir() -> Path:
    return Path("~/quant-data/technical-analysis/cache").expanduser()


def snapshot(root: Path) -> Snapshot:
    """연구 DB 마지막 날의 단면. 같은 날·같은 빌드면 캐시를 읽는다."""
    con = duckdb.connect()
    prices = data.current_files(root, "price_adj_daily")
    build = json.loads((root / "price_adj_daily" / "MANIFEST.json").read_text())["current_build"]
    last = str(con.execute("SELECT max(date) FROM read_parquet(?)", [prices]).fetchone()[0])
    cache = _cache_dir() / f"market_{last}_{build}.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        return Snapshot(last, z["tickers"], z["in_universe"], z["p52"], z["r20"], z["rsi"], z["score"], float(z["panic"]))
    cal = [r[0] for r in con.execute(
        "SELECT date FROM read_parquet(?) WHERE date <= ? ORDER BY date DESC LIMIT ?",
        [data.current_files(root, "trading_calendar"), last, LOOKBACK_ROWS + 40],
    ).fetchall()]
    since = cal[-1]
    age_cut = cal[LISTING_AGE] if len(cal) > LISTING_AGE else since
    uni = con.execute(
        """SELECT u.ticker FROM read_parquet(?) u
           LEFT JOIN (SELECT ticker, max(list_date) AS ld FROM read_parquet(?) GROUP BY ticker) s USING (ticker)
           WHERE u.date = ? AND u.sec_type = 'common' AND u.status = 'listed' AND NOT u.halt_state AND NOT u.admin_state
             AND (s.ld IS NULL OR s.ld <= ?)""",
        [data.current_files(root, "universe_daily"), data.current_files(root, "security"), last, age_cut],
    ).fetchall()
    adv = dict(con.execute(
        """SELECT ticker, CASE WHEN count(value_krw) >= 20 THEN avg(value_krw) END FROM (
               SELECT ticker, value_krw, row_number() OVER (PARTITION BY ticker ORDER BY date DESC) rn
               FROM read_parquet(?) WHERE date >= ? AND date <= ?) WHERE rn <= 20 GROUP BY ticker""",
        [data.current_files(root, "price_daily"), cal[30], last],
    ).fetchall())
    universe = {t for (t,) in uni if adv.get(t) is not None and float(adv[t]) >= ADV_KRW}
    rows = con.execute(
        """SELECT ticker, adj_high, adj_close FROM read_parquet(?)
           WHERE date >= ? AND date <= ? AND adj_close IS NOT NULL AND adj_high IS NOT NULL ORDER BY ticker, date""",
        [prices, since, last],
    ).fetchall()
    by: dict[str, tuple[list[float], list[float], bool]] = {}
    last_rows = {t for (t,) in con.execute(
        "SELECT ticker FROM read_parquet(?) WHERE date = ? AND adj_close IS NOT NULL", [prices, last]
    ).fetchall()}
    for t, h, c in rows:
        e = by.setdefault(t, ([], [], t in last_rows))
        e[0].append(float(h))
        e[1].append(float(c))
    tickers = np.array(sorted(by))
    n = tickers.shape[0]
    p52 = np.full(n, np.nan)
    r20 = np.full(n, np.nan)
    rsi = np.full(n, np.nan)
    in_u = np.zeros(n, dtype=bool)
    for i, t in enumerate(tickers):
        h, c, traded_today = by[t]
        if not traded_today:
            continue
        ha, ca = np.array(h), np.array(c)
        if ca.shape[0] >= 250:
            p52[i] = ca[-1] / ha[-250:].max()
        if ca.shape[0] > 20:
            r20[i] = ca[-1] / ca[-21] - 1.0
        rsi[i] = ind.rsi(ca, 14)[-1]
        in_u[i] = t in universe
    score = pullback_score(p52, r20, in_u)
    snap = Snapshot(last, tickers, in_u, p52, r20, rsi, score, panic_fraction(rsi, in_u))
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, tickers=tickers, in_universe=in_u, p52=p52, r20=r20, rsi=rsi, score=score, panic=snap.panic)
    return snap


def log_panic(path: Path, date: str, fraction: float, tickers: list[str]) -> bool:
    """투매일 전진 관찰 기록(날짜당 한 줄). 새로 썼으면 True."""
    exists = path.exists()
    if exists:
        with path.open() as f:
            if any(row.get("date") == date for row in csv.DictReader(f)):
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["date", "fraction", "n", "tickers"])
        w.writerow([date, f"{fraction:.4f}", len(tickers), " ".join(tickers)])
    return True
