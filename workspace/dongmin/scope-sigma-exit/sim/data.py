"""연구 DB(equity 고정본)에서 시뮬레이터가 읽는 일별 판을 만든다.

원장 표는 `_catalog_meta.json` 의 `builds` 가 가리키는 판(`<표>/v=<build>/**/*.parquet`)만 읽는다. 같은
폴더에 옛 판이 남아 있어 `**` 로 읽으면 날짜가 겹친다(메모리 ledger-read-current-build).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

KOSPI = "코스피"
KOSPI200 = "코스피 200"
# 그날 원장이 접지 못한 기업행위로 보는 일간 변화 문턱(가격제한폭 ±30%)
UNADJUSTED_JUMP = 0.30


@dataclass(frozen=True)
class Market:
    """세션 × 종목 판. 값이 없는 칸은 nan 이다."""

    snapshot_id: str
    sessions: tuple[date, ...]
    tickers: tuple[str, ...]
    adj_open: np.ndarray
    adj_close: np.ndarray
    volume: np.ndarray
    adv20: np.ndarray
    # 그날 수익률을 0 으로 볼 칸(원장이 접지 못한 기업행위 점프)
    unadjusted_jump: np.ndarray
    # 종목별 마지막 가격 행의 세션 번호(없으면 -1)
    last_bar: np.ndarray
    index_sessions: tuple[date, ...]
    kospi: np.ndarray
    kospi200: np.ndarray
    # 이번 시험에 더한 판. 세션 축은 `sessions` 와 같다. 지수 첫 축 0 = 코스피, 1 = 코스닥
    index_open: np.ndarray | None = None  # (2, 세션)
    index_close: np.ndarray | None = None  # (2, 세션)
    kosdaq: np.ndarray | None = None  # (세션, 종목) bool — 그날 universe_daily.market 이 KOSDAQ

    def session_index(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.sessions)}

    def ticker_index(self) -> dict[str, int]:
        return {t: i for i, t in enumerate(self.tickers)}


def _paths(root: Path, builds: dict[str, str], table: str) -> str:
    return str(root / table / f"v={builds[table]}" / "**" / "*.parquet")


def load_market(root: Path, tickers: list[str], start: date, end: date, index_start: date) -> Market:
    meta = json.loads((root / "_catalog_meta.json").read_text())
    builds = meta["builds"]
    con = duckdb.connect()
    sessions = [
        r[0]
        for r in con.execute(
            f"select date from read_parquet('{_paths(root, builds, 'trading_calendar')}') "
            "where date between ? and ? order by date",
            [start, end],
        ).fetchall()
    ]
    t_index = {t: i for i, t in enumerate(tickers)}
    s_index = {d: i for i, d in enumerate(sessions)}
    shape = (len(sessions), len(tickers))
    adj_open = np.full(shape, np.nan)
    adj_close = np.full(shape, np.nan)
    volume = np.full(shape, np.nan)
    adv20 = np.full(shape, np.nan)
    unadjusted = np.zeros(shape, dtype=np.int64)
    con.register("wanted", _ticker_table(tickers))
    rows = con.execute(
        f"""
        select a.ticker, a.date, a.adj_open, a.adj_close, a.n_unadjusted_events, p.volume_shr
        from read_parquet('{_paths(root, builds, 'price_adj_daily')}', union_by_name = true) a
        join wanted w on w.ticker = a.ticker
        left join read_parquet('{_paths(root, builds, 'price_daily')}', union_by_name = true) p
          on p.ticker = a.ticker and p.date = a.date
        where a.date between ? and ?
        """,
        [start, end],
    ).fetchall()
    for ticker, day, o, c, n_unadj, vol in rows:
        i, j = s_index.get(day), t_index[ticker]
        if i is None:
            continue
        adj_open[i, j] = np.nan if o is None else o
        adj_close[i, j] = np.nan if c is None else c
        volume[i, j] = np.nan if vol is None else vol
        unadjusted[i, j] = n_unadj or 0
    for ticker, day, adv in con.execute(
        f"""
        select u.ticker, u.date, u.adv20_krw
        from read_parquet('{_paths(root, builds, 'universe_daily')}', union_by_name = true) u
        join wanted w on w.ticker = u.ticker
        where u.date between ? and ?
        """,
        [start, end],
    ).fetchall():
        i = s_index.get(day)
        if i is not None and adv is not None:
            adv20[i, t_index[ticker]] = adv
    jump = _unadjusted_jumps(adj_close, unadjusted)
    has_bar = ~np.isnan(adj_close)
    last_bar = np.where(has_bar.any(axis=0), len(sessions) - 1 - np.argmax(has_bar[::-1], axis=0), -1)
    index_sessions, kospi, kospi200 = _indices(con, root, builds, index_start, end)
    return Market(
        snapshot_id=meta["snapshot_id"],
        sessions=tuple(sessions),
        tickers=tuple(tickers),
        adj_open=adj_open,
        adj_close=adj_close,
        volume=volume,
        adv20=adv20,
        unadjusted_jump=jump,
        last_bar=last_bar,
        index_sessions=index_sessions,
        kospi=kospi,
        kospi200=kospi200,
    )


def _ticker_table(tickers: list[str]):
    import pyarrow as pa

    return pa.table({"ticker": pa.array(tickers, pa.string())})


def _unadjusted_jumps(adj_close: np.ndarray, unadjusted: np.ndarray) -> np.ndarray:
    """누적 미조정 사건 수가 늘어난 날의 수정종가 변화가 가격제한폭을 넘으면 그날 수익률을 0 으로 본다."""
    out = np.zeros(adj_close.shape, dtype=bool)
    prev_close = _forward_filled(adj_close)
    with np.errstate(invalid="ignore", divide="ignore"):
        change = adj_close[1:] / prev_close[:-1] - 1.0
    increased = unadjusted[1:] > unadjusted[:-1]
    out[1:] = increased & (np.abs(change) > UNADJUSTED_JUMP)
    return out


def _forward_filled(values: np.ndarray) -> np.ndarray:
    out = values.copy()
    for i in range(1, out.shape[0]):
        missing = np.isnan(out[i])
        out[i, missing] = out[i - 1, missing]
    return out


def _indices(con, root: Path, builds: dict[str, str], start: date, end: date):
    rows = con.execute(
        f"""
        select date, index_name, close_idx
        from read_parquet('{_paths(root, builds, 'index_daily')}', union_by_name = true)
        where index_class = 'KOSPI' and index_name in (?, ?) and date between ? and ?
        order by date
        """,
        [KOSPI, KOSPI200, start, end],
    ).fetchall()
    days = sorted({r[0] for r in rows})
    pos = {d: i for i, d in enumerate(days)}
    kospi = np.full(len(days), np.nan)
    kospi200 = np.full(len(days), np.nan)
    for day, name, close in rows:
        (kospi if name == KOSPI else kospi200)[pos[day]] = close
    return tuple(days), kospi, kospi200


KOSDAQ = "코스닥"
# 지수 판 행 순서(Extras.index_open·index_close 의 첫 축). 앞 둘은 Market.index_open·index_close 와 같다
EXTRA_INDICES = (("KOSPI", KOSPI), ("KOSDAQ", KOSDAQ), ("KOSPI", KOSPI200))


@dataclass(frozen=True, eq=False)
class Extras:
    """이번 시험에 더한 판. 세션·종목 축은 기존 시장 판 캐시와 같다."""

    index_open: np.ndarray  # (3, 세션) 코스피 · 코스닥 · 코스피 200
    index_close: np.ndarray
    kosdaq: np.ndarray  # (세션, 종목) bool — 그날 소속 시장이 KOSDAQ(행이 없는 날은 직전 값)
    spac: np.ndarray  # (세션, 종목) bool — 그날 sec_type 이 spac


def load_extras(root: Path, sessions: tuple[date, ...], tickers: tuple[str, ...]) -> Extras:
    meta = json.loads((root / "_catalog_meta.json").read_text())
    builds = meta["builds"]
    con = duckdb.connect()
    s_index = {d: i for i, d in enumerate(sessions)}
    t_index = {t: i for i, t in enumerate(tickers)}
    index_open = np.full((len(EXTRA_INDICES), len(sessions)), np.nan)
    index_close = np.full((len(EXTRA_INDICES), len(sessions)), np.nan)
    rows = {key: k for k, key in enumerate(EXTRA_INDICES)}
    for cls, name, day, o, c in con.execute(
        f"""
        select index_class, index_name, date, open_idx, close_idx
        from read_parquet('{_paths(root, builds, 'index_daily')}', union_by_name = true)
        where date between ? and ?
        """,
        [sessions[0], sessions[-1]],
    ).fetchall():
        k, i = rows.get((cls, name)), s_index.get(day)
        if k is None or i is None:
            continue
        index_open[k, i] = np.nan if o is None else o
        index_close[k, i] = np.nan if c is None else c
    missing = int(np.isnan(index_close).sum() + np.isnan(index_open).sum())
    if missing:
        raise ValueError(f"index rows missing — n_missing={missing} sessions={len(sessions)} indices={EXTRA_INDICES}")
    code = np.full((len(sessions), len(tickers)), -1, dtype=np.int8)  # -1 모름 · 0 KOSDAQ 아님 · 1 KOSDAQ
    spac = np.zeros((len(sessions), len(tickers)), dtype=bool)
    con.register("wanted", _ticker_table(list(tickers)))
    for ticker, day, market, sec_type in con.execute(
        f"""
        select u.ticker, u.date, u.market, u.sec_type
        from read_parquet('{_paths(root, builds, 'universe_daily')}', union_by_name = true) u
        join wanted w on w.ticker = u.ticker
        where u.date between ? and ?
        """,
        [sessions[0], sessions[-1]],
    ).fetchall():
        i = s_index.get(day)
        if i is None:
            continue
        j = t_index[ticker]
        if market is not None:
            code[i, j] = 1 if market == "KOSDAQ" else 0
        spac[i, j] = sec_type == "spac"
    for i in range(1, code.shape[0]):
        unknown = code[i] < 0
        code[i, unknown] = code[i - 1, unknown]
    return Extras(index_open=index_open, index_close=index_close, kosdaq=code == 1, spac=spac)
