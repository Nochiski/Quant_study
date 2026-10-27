"""시장 단면 지표 — 눌림목 점수와 투매일 비율(연구 PREREG2 의 P1·P2 와 같은 정의).

- 유니버스: 그날 보통주·상장·비정지·비관리, 상장 60거래일 이상, 20일 평균 거래대금 10억 원 이상.
- 눌림목 점수: 유니버스 안 백분위 pct(P52) + pct(−R20) 를 다시 유니버스 안 백분위(0~1)로.
  P52 = 종가 ÷ 최근 250봉 고가 최댓값, R20 = 종가 ÷ 20봉 전 종가 − 1. 상위 20% = 눌림목, 하위 30% = 피할 쪽.
- 투매일: 유니버스 중 RSI14 < 30 종목 비율 ≥ 25%.
- 52주 고점 근접·가격 기반 종합은 연구 3차(PREREG3)의 E11·F2 와 같은 정의(상위 20% 대 하위 20%).
- 국면 점수·시장폭·수급 순위·피해야 할 상태는 연구 6차(2010–15 발견, 2020–26 검증) D02·D06·F01·A06·C03·B06 정의.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from . import avoid
from . import data
from . import indicators as ind

PANIC_FRACTION = 0.25
TOP_SHARE = 0.20
BOTTOM_SHARE = 0.30
ADV_KRW = 1e9
LISTING_AGE = 60
LOOKBACK_ROWS = 260
PANIC_BUY_FRACTION = 0.20  # 6차 D08: 이 비율을 넘는 날의 RSI<30 종목
FLOW_BOTTOM = 0.05  # 6차 F01: 외국인+기관 5일 순매수/거래대금 유니버스 하위 5%


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


def price_composite(
    p52: np.ndarray, r20: np.ndarray, vol60: np.ndarray, max20: np.ndarray, mad: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """가격 기반 종합(연구 3차 F2): 저변동·최근 눌림·52주 고점 근접·낮은 MAX·높은 MAD 백분위의 평균을 다시 백분위로.

    성분이 하나라도 없으면 NaN.
    """
    parts = [pct_rank(-np.asarray(vol60, dtype=float), mask), pct_rank(-np.asarray(r20, dtype=float), mask),
             pct_rank(p52, mask), pct_rank(-np.asarray(max20, dtype=float), mask), pct_rank(mad, mask)]
    stack = np.vstack(parts)
    s = np.where(np.isnan(stack).any(axis=0), np.nan, stack.mean(axis=0))
    return pct_rank(s, mask & ~np.isnan(s))


def quintile_label(score: float) -> str:
    """연구 판정이 상위 20% 대 하위 20% 였던 지표의 라벨."""
    if np.isnan(score):
        return "계산 불가"
    if score >= 0.8:
        return "상위 20%"
    if score <= 0.2:
        return "하위 20%"
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
    vol60: np.ndarray
    max20: np.ndarray
    mad: np.ndarray
    composite: np.ndarray  # 유니버스 안 가격 기반 종합 백분위, 밖은 NaN
    bull: bool  # 코스피 종가 > 코스피 200일 평균(6차 D02 국면)
    regime_score: np.ndarray  # 상승장 = 52주 고점 근접(종가/252봉 최고 종가) 순위, 하락장 = −20일 수익 순위
    breadth200: float  # 유니버스 중 종가 > 200봉 평균 비율
    flow_rank: np.ndarray  # 외국인+기관 5일 순매수 ÷ (5 × 20일 평균 거래대금)의 유니버스 안 백분위(낮을수록 많이 팜)
    today: dict[str, np.ndarray]  # 오늘(마지막 봉) 피해야 할 상태 A06·C03·B06·BRK20(D06 은 BRK20 & 시장폭 약함)

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
    cache = _cache_dir() / f"market_v3_{last}_{build}.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        return Snapshot(
            last, z["tickers"], z["in_universe"], z["p52"], z["r20"], z["rsi"], z["score"], float(z["panic"]),
            z["vol60"], z["max20"], z["mad"], z["composite"], bool(z["bull"]), z["regime_score"], float(z["breadth200"]),
            z["flow_rank"], {k: z[f"today_{k}"] for k in ("A06", "C03", "B06", "BRK20")},
        )
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
        """SELECT ticker, adj_high, adj_close, adj_open, adj_volume_shr FROM read_parquet(?)
           WHERE date >= ? AND date <= ? AND adj_close IS NOT NULL AND adj_high IS NOT NULL ORDER BY ticker, date""",
        [prices, since, last],
    ).fetchall()
    by: dict[str, tuple[list[float], list[float], bool, list[float], list[float]]] = {}
    last_rows = {t for (t,) in con.execute(
        "SELECT ticker FROM read_parquet(?) WHERE date = ? AND adj_close IS NOT NULL", [prices, last]
    ).fetchall()}
    for t, h, c, o, v in rows:
        e = by.setdefault(t, ([], [], t in last_rows, [], []))
        e[0].append(float(h))
        e[1].append(float(c))
        e[3].append(float("nan") if o is None else float(o))
        e[4].append(float("nan") if v is None else float(v))
    tickers = np.array(sorted(by))
    n = tickers.shape[0]
    p52 = np.full(n, np.nan)
    r20 = np.full(n, np.nan)
    rsi = np.full(n, np.nan)
    vol60 = np.full(n, np.nan)
    max20 = np.full(n, np.nan)
    mad = np.full(n, np.nan)
    in_u = np.zeros(n, dtype=bool)
    near52c = np.full(n, np.nan)
    above200 = np.full(n, np.nan)
    today = {k: np.zeros(n, dtype=bool) for k in ("A06", "C03", "B06", "BRK20")}
    for i, t in enumerate(tickers):
        h, c, traded_today, o, v = by[t]
        if not traded_today:
            continue
        ha, ca = np.array(h), np.array(c)
        if ca.shape[0] >= 120:
            near52c[i] = ca[-1] / ca[-252:].max()
        if ca.shape[0] >= 200:
            above200[i] = float(ca[-1] > ca[-200:].mean())
        fl = avoid.flags(np.array(o), ca, np.array(v))
        for k in ("A06", "C03", "B06"):
            today[k][i] = bool(fl[k][-1])
        today["BRK20"][i] = bool(avoid.breakout20(ca)[-1])
        if ca.shape[0] >= 250:
            p52[i] = ca[-1] / ha[-250:].max()
        if ca.shape[0] > 20:
            r20[i] = ca[-1] / ca[-21] - 1.0
        rsi[i] = ind.rsi(ca, 14)[-1]
        if ca.shape[0] > 60:
            vol60[i] = np.diff(np.log(ca[-61:])).std()
        if ca.shape[0] > 20:
            max20[i] = (ca[-20:] / ca[-21:-1] - 1.0).max()
        if ca.shape[0] >= 200:
            mad[i] = ca[-21:].mean() / ca[-200:].mean()
        in_u[i] = t in universe
    score = pullback_score(p52, r20, in_u)
    comp = price_composite(p52, r20, vol60, max20, mad, in_u)
    kospi = [r[0] for r in con.execute(
        "SELECT close_idx FROM read_parquet(?) WHERE index_name = '코스피' AND date <= ? ORDER BY date DESC LIMIT 200",
        [data.current_files(root, "index_daily"), last],
    ).fetchall()]
    bull = len(kospi) == 200 and float(kospi[0]) > float(np.mean(kospi))
    regime = regime_score(bull, near52c, r20, in_u)
    ok200 = in_u & ~np.isnan(above200)
    breadth = float(above200[ok200].mean()) if ok200.any() else float("nan")
    flow = _flow_rank(con, root, cal, tickers, in_u, adv)
    snap = Snapshot(
        last, tickers, in_u, p52, r20, rsi, score, panic_fraction(rsi, in_u), vol60, max20, mad, comp,
        bull, regime, breadth, flow, today,
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache, tickers=tickers, in_universe=in_u, p52=p52, r20=r20, rsi=rsi, score=score, panic=snap.panic,
        vol60=vol60, max20=max20, mad=mad, composite=comp, bull=bull, regime_score=regime, breadth200=breadth,
        flow_rank=flow, **{f"today_{k}": v for k, v in today.items()},
    )
    return snap


def regime_score(bull: bool, near52c: np.ndarray, r20: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """6차 D02 — 상승장이면 52주 고점 근접 순위, 하락장이면 20일 반전(−R20) 순위(유니버스 안 0~1)."""
    return pct_rank(near52c, mask) if bull else pct_rank(-r20, mask)


def _flow_rank(
    con: duckdb.DuckDBPyConnection, root: Path, cal: list[object], tickers: np.ndarray, mask: np.ndarray, adv: dict[str, object]
) -> np.ndarray:
    """6차 F01 — 외국인+기관(결측 0) 최근 5거래일 순매수 합(3일 이상 있을 때) ÷ (5 × 20일 평균 거래대금)의 유니버스 안 백분위."""
    days = cal[:5]
    got = dict((t, (s, n)) for t, s, n in con.execute(
        f"""SELECT ticker, sum(frgnr_invsr_krw + coalesce(orgn_krw, 0)), count(frgnr_invsr_krw) FROM read_parquet(?)
            WHERE src = 'kiwoom' AND date IN ({", ".join("?" for _ in days)}) GROUP BY ticker""",
        [data.current_files(root, "flow_daily"), *days],
    ).fetchall())
    x = np.full(tickers.shape[0], np.nan)
    for i, t in enumerate(tickers):
        s_n, a = got.get(str(t)), adv.get(str(t))
        if s_n and s_n[0] is not None and s_n[1] >= 3 and a:
            x[i] = float(s_n[0]) / (5 * float(a))
    return pct_rank(x, mask)


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


def observe_rows(snap: Snapshot) -> list[tuple[str, list[str]]]:
    """6차 검증 신호의 오늘 해당 종목(전진 관찰용). (신호 이름, 티커 목록)."""
    u = snap.in_universe
    tk = snap.tickers

    def pick(m: np.ndarray) -> list[str]:
        return [str(t) for t in tk[u & m]]

    rs = np.nan_to_num(snap.regime_score, nan=0.5)
    weak = snap.breadth200 <= avoid.BREADTH_WEAK
    return [
        ("REGIME", ["bull" if snap.bull else "bear", f"breadth={snap.breadth200:.3f}", f"panic={snap.panic:.3f}"]),
        ("D02_TOP20", pick(rs >= 0.8)),
        ("D02_BOTTOM20", pick(rs <= 0.2)),
        ("A06", pick(snap.today["A06"])),
        ("D06", pick(snap.today["BRK20"]) if weak else []),
        ("C03", pick(snap.today["C03"])),
        ("B06", pick(snap.today["B06"])),
        ("D08", pick(np.nan_to_num(snap.rsi, nan=50) < 30) if snap.panic > PANIC_BUY_FRACTION else []),
        ("F01", pick(np.nan_to_num(snap.flow_rank, nan=1) <= FLOW_BOTTOM)),
    ]


def log_observe(path: Path, date: str, rows: list[tuple[str, list[str]]]) -> bool:
    """6차 전진 관찰 기록(날짜당 신호마다 한 줄). 그날이 이미 있으면 쓰지 않고 False."""
    exists = path.exists()
    if exists:
        with path.open() as f:
            if any(row.get("date") == date for row in csv.DictReader(f)):
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["date", "signal", "n", "tickers"])
        for name, ts in rows:
            w.writerow([date, name, len(ts), " ".join(ts)])
    return True
