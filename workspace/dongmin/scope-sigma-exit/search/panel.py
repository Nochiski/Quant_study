"""알파 탐색용 판(PROTOCOL.md). 시장 판은 sim.data.load_market 그대로, 여기에 시총·소속 시장·보통주 여부·지수를 더한다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from sim.data import Market, _paths, _ticker_table, load_market

PRIOR = Path(os.path.expanduser("~/quant-data/research/2026-10-26-trading-rules"))
LEDGER = PRIOR / "ledger" / "equity"
RESEARCH = Path(os.path.expanduser("~/quant-data/research/2026-10-30-alpha-search"))
PANEL = RESEARCH / "panel.npz"
LOAD_START = date(2018, 11, 1)  # 지표 입력(252세션)용. 2020 이전 날짜의 점수·수익률은 만들지 않는다
INDEX_NAMES = (
    ("KOSPI", "코스피 200"), ("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("KOSDAQ", "코스닥 150"),
    ("KOSPI", "코스피 200 건설"), ("KOSPI", "코스피 200 경기방어소비재지수"), ("KOSPI", "코스피 200 경기소비재"),
    ("KOSPI", "코스피 200 금융"), ("KOSPI", "코스피 200 산업재"), ("KOSPI", "코스피 200 생활소비재"),
    ("KOSPI", "코스피 200 에너지/화학"), ("KOSPI", "코스피 200 정보기술"), ("KOSPI", "코스피 200 중공업"),
    ("KOSPI", "코스피 200 철강/소재"), ("KOSPI", "코스피 200 커뮤니케이션서비스"), ("KOSPI", "코스피 200 헬스케어"),
)
SECTORS = tuple(name for _, name in INDEX_NAMES[4:])


@dataclass(frozen=True, eq=False)
class Panel:
    market: Market
    mktcap: np.ndarray  # (세션, 종목) 원, 행 없으면 nan
    on_kospi: np.ndarray  # (세션, 종목) bool, 행 없는 날은 직전 값
    common: np.ndarray  # (세션, 종목) bool — 그날 sec_type 이 common
    index_open: dict[str, np.ndarray]  # 지수 이름 → (세션,)
    index_close: dict[str, np.ndarray]


def build_panel(start: date = LOAD_START, end: date | None = None, path: Path = PANEL,
                names: tuple[tuple[str, str], ...] = INDEX_NAMES) -> None:
    meta = json.loads((LEDGER / "_catalog_meta.json").read_text())
    builds = meta["builds"]
    con = duckdb.connect()
    tickers = [
        r[0]
        for r in con.execute(
            f"select distinct ticker from read_parquet('{_paths(LEDGER, builds, 'price_adj_daily')}') where date >= ? order by ticker",
            [start],
        ).fetchall()
    ]
    if end is None:
        end = con.execute(f"select max(date) from read_parquet('{_paths(LEDGER, builds, 'price_adj_daily')}')").fetchone()[0]
    m = load_market(LEDGER, tickers, start, end, start)
    s_index, t_index = m.session_index(), m.ticker_index()
    shape = (len(m.sessions), len(m.tickers))
    mktcap = np.full(shape, np.nan)
    code = np.full(shape, -1, dtype=np.int8)
    common = np.zeros(shape, dtype=bool)
    con.register("wanted", _ticker_table(list(m.tickers)))
    for ticker, day, cap, market, sec_type in con.execute(
        f"""
        select u.ticker, u.date, u.mktcap_krw, u.market, u.sec_type
        from read_parquet('{_paths(LEDGER, builds, 'universe_daily')}', union_by_name = true) u
        join wanted w on w.ticker = u.ticker where u.date between ? and ?
        """,
        [start, end],
    ).fetchall():
        i = s_index.get(day)
        if i is None:
            continue
        j = t_index[ticker]
        mktcap[i, j] = np.nan if cap is None else float(cap)
        if market is not None:
            code[i, j] = 1 if market == "KOSPI" else 0
        common[i, j] = sec_type == "common"
    for i in range(1, shape[0]):
        unknown = code[i] < 0
        code[i, unknown] = code[i - 1, unknown]
    opens = {n: np.full(shape[0], np.nan) for _, n in names}
    closes = {n: np.full(shape[0], np.nan) for _, n in names}
    wanted = set(names)
    for cls, name, day, o, c in con.execute(
        f"select index_class, index_name, date, open_idx::double, close_idx::double "
        f"from read_parquet('{_paths(LEDGER, builds, 'index_daily')}', union_by_name = true) where date between ? and ?",
        [start, end],
    ).fetchall():
        i = s_index.get(day)
        if i is None or (cls, name) not in wanted:
            continue
        opens[name][i], closes[name][i] = o, c
    # 지수가 판 시작 뒤에 생긴 경우(예: 코스피200 업종 일부 2010-07-01)는 앞쪽 빈칸을 허용하고, 시작 뒤 빈칸만 막는다
    missing = {}
    for n, v in closes.items():
        valid = np.flatnonzero(~np.isnan(v))
        gaps = int(np.isnan(v[valid[0]:]).sum()) if len(valid) else len(v)
        if gaps:
            missing[n] = gaps
    if missing:
        raise ValueError(f"index closes missing after first value — {missing} sessions={len(m.sessions)}")
    np.savez_compressed(
        path,
        snapshot_id=m.snapshot_id,
        sessions=np.array([d.isoformat() for d in m.sessions]),
        tickers=np.array(m.tickers),
        adj_open=m.adj_open, adj_close=m.adj_close, volume=m.volume, adv20=m.adv20,
        unadjusted_jump=m.unadjusted_jump, last_bar=m.last_bar,
        mktcap=mktcap, on_kospi=code == 1, common=common,
        index_names=np.array([n for _, n in names]),
        index_open=np.stack([opens[n] for _, n in names]),
        index_close=np.stack([closes[n] for _, n in names]),
    )
    print(f"panel {path} snapshot={m.snapshot_id} sessions={shape[0]} tickers={shape[1]} {m.sessions[0]}~{m.sessions[-1]}")


def load_panel(path: Path = PANEL) -> Panel:
    z = np.load(path, allow_pickle=False)
    sessions = tuple(date.fromisoformat(s) for s in z["sessions"])
    names = [str(n) for n in z["index_names"]]
    k200 = names.index("코스피 200")
    market = Market(
        snapshot_id=str(z["snapshot_id"]),
        sessions=sessions,
        tickers=tuple(str(t) for t in z["tickers"]),
        adj_open=z["adj_open"], adj_close=z["adj_close"], volume=z["volume"], adv20=z["adv20"],
        unadjusted_jump=z["unadjusted_jump"], last_bar=z["last_bar"],
        index_sessions=sessions, kospi=z["index_close"][names.index("코스피")], kospi200=z["index_close"][k200],
    )
    return Panel(
        market=market, mktcap=z["mktcap"], on_kospi=z["on_kospi"], common=z["common"],
        index_open={n: z["index_open"][k] for k, n in enumerate(names)},
        index_close={n: z["index_close"][k] for k, n in enumerate(names)},
    )


if __name__ == "__main__":
    RESEARCH.mkdir(parents=True, exist_ok=True)
    build_panel()
