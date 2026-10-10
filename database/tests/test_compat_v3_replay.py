"""compat.v3_replay — v3 사본 대 compat 반영본 대조기(컷오버 트랙 QL-G · T-45 · COMPAT_LAYER §7).

픽스처는 작은 sqlite 두 개다: v3 사본(`v3_schema.sql` + 행)과 compat 반영본(사본의 복사본 + 바꾼 행 + `_compat_meta`
기록). equity 루트는 `price_daily`(K 사슬·KRX 행·시총 종가)·`security`(신규 스팩·T 당일 상장) 두 표만 짓고, 대조기는
compat 기록의 `equity_builds` 판을 읽는다. 범주마다 행 하나를 일부러 만들어 두고 범주·갈래별 수와 rc 를 리터럴로 고정한다.

기준일 D = 2026-10-08(목), 다음 거래일 = 2026-10-12(10-09 휴장). 09-14(T-33) 뒤는 post 구간이다.
daily_prices(전 열이 매일 소비자 열 — §2-2 워치리스트)
  005930 10-06  수준만 다르고 adj/close 비·거래량 같다              → T33
  000270 10-06  같은 경우인데 비 차이가 1원 밖·0.15% 안(상대 갈래)  → T33
  000660 10-06  거래량만 다르다(09-14 뒤)                          → other T33_ratio_or_volume
  035720 D      거래량만 다르다(v3 20:05 값)                       → base_day_unconfirmed(다음 사본 없음)
  068270 08-03  v3 adj_close NULL                                  → v3_adj_null
  A00001·A00002 03-27 종가 다름, A00003 03-27 거래대금만 다름       → v3_bad_day ×3(등록 오류일)
  A00001·A00002 03-26 종가 전부 다름(미등록 오류일)                → other pre_mismatch ×2, 탐지 목록에는 든다
  051910 07-01  v3 종가 = 원종가 × K(d)/K(07-09)(분할 2:1)          → v3_backfill
  051910 07-02  compat 에만(v3 가 아는 종목, KRX 행 있음)           → v3_missed_day(T-45 ③)
  088280 D      compat 에만(v3 stocks 에 있음, KRX 행 있음)          → v3_missed_day
  000100 08-07  compat adj NULL — 창 밖 다시 맞춘 종목(기록 rebase)  → rebase_adj_null
  000101 08-07  같은 모양, rebase 밖                               → other pre_mismatch
  005380 10-05 거래대금만 · 08-04 v3 거래대금 NULL · 01-05 v3 첫 날 전 · 08-05 compat 에만(KRX 행 없음)
                → other amount · amount_null_v3 · only_in_compat_before_v3 · only_in_compat
  096770 D      v3 에만 → other only_in_v3 · 123450 D 신규 스팩 → new_spac · 005490 08-06 adj 0.04% → 허용 오차 안
  900000 10-07·D v3 에만(거래량 0) — compat 폐지일 10-07 ≤ 행 날짜 ≤ D → delisting_timing ×2(수급 D 행도)
  378800 08-12  adj 만 다르다(v3 가 사건 뒤 과거 수정주가를 다음 날 갱신) → 다음 거래일 사본 없으면
                v3_adj_next_day_unconfirmed, 있고 맞으면 v3_adj_next_day, 맞지 않으면 other pre_mismatch
stocks
  005930 시총 +5(종가 없음) → other column:market_cap, 상장일 다름 → no_consumer column:listed_date
  000660 시총 +1 → 허용 오차 안 · 051910 시총 100 → 98(종가 510 → 500) → market_cap_close_definition(T-45 ①)
  051911 시총 100 → 90(종가 255 → 250, 함의 주식수 다름) → other column:market_cap
  000880 v3 시총 NULL · v3 에 그날 행 있음 · KRX 근거 있음 → v3_missing_value(v3 값 결측을 compat 이 채움)
  000990 v3 시총 NULL · KRX 근거 없음 → other column:market_cap
  900000 compat 폐지(10-07)·is_active 0·시총 50, v3 1·NULL → delisting_timing(T-45 ②)
  088280 v3 시총 NULL · v3 그날 행 없음 · KRX 행 있음 → v3_missed_day · 123450 → new_spac · 777777 compat 에만 → other
consensus_revision_daily op 0.5% → manual_consumer(§2-3 S10) · eps +10 → no_consumer · financial_summary compat 에만 → no_consumer
score_history D 에 v3 3종목 · compat 2종목(값 ×10, 005930 만 val_ev_ebitda NULL) → score_universe 1 · score_common 2 ·
              score_val_ev_ebitda_null 1, 10-07 행 차이 → other score_date_not_D
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import test_compat_export as tce
from compat import v3_replay as rp
from compat.mappings import BY_TABLE, MAPPINGS
from compat.quant_db import SCHEMA_SQL_PATH, ExportResult, TableResult, _write_meta
from compat.t_rows import SOURCE_KIWOOM_2105
from compat.v3_post import TABLES
from conftest import _make_stage_tree
from model.contracts import V2_SCORE_COLUMNS, V3_SCORE_COLUMNS

D, D_ISO, NEXT_ISO = "20261008", "2026-10-08", "2026-10-12"
EQ_BUILD = "m_20261009T000000_000000Z"
BUILDS = {"price_daily": EQ_BUILD, "security": EQ_BUILD}
SAME = object()                                  # adj 기본값 — 종가와 같다


# ── 행 ────────────────────────────────────────────────────────────────────────
def _px(code: str, day: str, close: float, *, vol: int = 1000, amount: int | None = 10,
        adj: object = SAME, ohl: float | None = None) -> tuple:
    """daily_prices 9열 — 시·고·저 기본 = 종가, adj 기본 = 종가."""
    o = close if ohl is None else ohl
    return (code, day, o, o, o, close, vol, amount, close if adj is SAME else adj)


V3_PRICES = [
    _px("005930", "2026-10-06", 70_500, adj=35_250),
    _px("000270", "2026-10-06", 705_000, adj=352_500),
    _px("000660", "2026-10-06", 100_000, vol=2000),
    _px("035720", D_ISO, 50_000, vol=900),
    _px("068270", "2026-08-03", 1000, adj=None),
    _px("A00001", "2026-03-27", 2000), _px("A00002", "2026-03-27", 3000), _px("A00003", "2026-03-27", 4000),
    _px("A00001", "2026-03-26", 2000), _px("A00002", "2026-03-26", 3000),
    _px("051910", "2026-07-01", 500, vol=2000, adj=500),
    _px("000001", "2026-07-01", 100), _px("000002", "2026-07-01", 100),   # 07-01 이 오류일로 잡히지 않게
    _px("051910", D_ISO, 510), _px("051911", D_ISO, 255), _px("000880", D_ISO, 6000),
    _px("000100", "2026-08-07", 500), _px("000101", "2026-08-07", 500),
    _px("005380", "2026-10-05", 9000, amount=100),
    _px("005380", "2026-08-04", 9000, amount=None),
    _px("096770", D_ISO, 7000),
    _px("005490", "2026-08-06", 1000, adj=1000.0),
    _px("900000", "2026-10-07", 100, vol=0), _px("900000", D_ISO, 100, vol=0),
    _px("378800", "2026-08-12", 5000, adj=2094),
]
# compat 이 바꾼 값(키 → 행) — 없는 키는 v3 와 같다. None 은 compat 이 지운 행
COMPAT_PRICES = {
    ("005930", "2026-10-06"): _px("005930", "2026-10-06", 70_000, adj=35_000),
    ("000270", "2026-10-06"): _px("000270", "2026-10-06", 700_000, adj=350_300),
    ("000660", "2026-10-06"): _px("000660", "2026-10-06", 100_000, vol=1500),
    ("035720", D_ISO): _px("035720", D_ISO, 50_000, vol=1000),
    ("068270", "2026-08-03"): _px("068270", "2026-08-03", 1000, adj=1000.0),
    ("A00001", "2026-03-27"): _px("A00001", "2026-03-27", 2100),
    ("A00002", "2026-03-27"): _px("A00002", "2026-03-27", 3100),
    ("A00003", "2026-03-27"): _px("A00003", "2026-03-27", 4000, amount=99),
    ("A00001", "2026-03-26"): _px("A00001", "2026-03-26", 2100),
    ("A00002", "2026-03-26"): _px("A00002", "2026-03-26", 3100),
    ("051910", "2026-07-01"): _px("051910", "2026-07-01", 1000, adj=500),
    ("051910", "2026-07-02"): _px("051910", "2026-07-02", 1000),
    ("088280", D_ISO): _px("088280", D_ISO, 3000),
    ("000100", "2026-08-07"): _px("000100", "2026-08-07", 500, adj=None),
    ("000101", "2026-08-07"): _px("000101", "2026-08-07", 500, adj=None),
    ("005380", "2026-10-05"): _px("005380", "2026-10-05", 9000, amount=150),
    ("005380", "2026-08-04"): _px("005380", "2026-08-04", 9000, amount=0),
    ("005380", "2026-01-05"): _px("005380", "2026-01-05", 9000),
    ("005380", "2026-08-05"): _px("005380", "2026-08-05", 9000),
    ("096770", D_ISO): None,
    ("123450", D_ISO): _px("123450", D_ISO, 2000),
    ("005490", "2026-08-06"): _px("005490", "2026-08-06", 1000, adj=1000.4),
    ("900000", "2026-10-07"): None, ("900000", D_ISO): None,
    ("378800", "2026-08-12"): _px("378800", "2026-08-12", 5000, adj=10468),
}
REBASE = {"before": "2026-09-24", "tickers": ["000100"], "n_rows": 1, "n_null": 1}
STOCK_COLS = ("stock_code", "stock_name", "market", "sector", "market_cap", "listed_date", "is_active",
              "delisted_date", "updated_at")


def _stock(code: str, cap: int | None, updated: str, *, listed: str = "2000-01-04", active: int = 1,
           delisted: str | None = None) -> tuple:
    return (code, f"이름{code}", "KOSPI", "전기/전자", cap, listed, active, delisted, updated)


V3_TIME, X_TIME = "2026-10-08 11:05:00", "2026-10-09T00:10:00"
V3_STOCKS = [_stock("005930", 1000, V3_TIME), _stock("000660", 2000, V3_TIME), _stock("051910", 100, V3_TIME),
             _stock("051911", 100, V3_TIME), _stock("000880", None, V3_TIME), _stock("000990", None, V3_TIME),
             _stock("900000", None, V3_TIME), _stock("088280", None, V3_TIME)]
COMPAT_STOCKS = [_stock("005930", 1005, X_TIME, listed="1975-06-11"), _stock("000660", 2001, X_TIME),
                 _stock("051910", 98, X_TIME), _stock("051911", 90, X_TIME), _stock("000880", 60, X_TIME),
                 _stock("000990", 40, X_TIME),
                 _stock("900000", 50, X_TIME, active=0, delisted="2026-10-07"),
                 _stock("088280", 70, X_TIME), _stock("123450", 50, X_TIME), _stock("777777", 10, X_TIME)]
REVISION_COLS = BY_TABLE["consensus_revision_daily"].columns
_FLOW = tuple(c for c in BY_TABLE["investor_detail_flows"].columns if c not in ("stock_code", "trade_date"))


def _revision(op: float, eps: int) -> dict:
    row: dict = dict.fromkeys(REVISION_COLS)
    row.update(stock_code="005930", base_date="2026-10-07", target_period="2026/12", op=op, eps=eps,
               collected_date=D_ISO)
    return row


def _score(columns: tuple[str, ...], total_col: str, code: str, day: str, total: float, ev: float | None) -> dict:
    row: dict = dict.fromkeys(columns)
    row.update(stock_code=code, score_date=day, rank=1, momentum_score=total / 2)
    row[total_col] = total
    if "val_ev_ebitda" in row:
        row["val_ev_ebitda"] = ev
    return row


V3_SCORES = [_score(V3_SCORE_COLUMNS, "composite_score", c, D_ISO, t, 7.5)
             for c, t in (("005930", 3.0), ("000660", 2.0), ("035720", 1.0))]
V3_SCORES.append(_score(V3_SCORE_COLUMNS, "composite_score", "005930", "2026-10-07", 5.0, 7.5))
COMPAT_SCORES = [_score(V3_SCORE_COLUMNS, "composite_score", c, D_ISO, t, ev)
                 for c, t, ev in (("005930", 30.0, None), ("000660", 20.0, 8.0))]   # 000660 은 val_ev 값이 있다
COMPAT_SCORES.append(_score(V3_SCORE_COLUMNS, "composite_score", "005930", "2026-10-07", 6.0, 7.5))
V2_SCORES = [_score(V2_SCORE_COLUMNS, "total_score", c, D_ISO, t, None) for c, t in (("005930", 50.0),
                                                                                      ("000660", 40.0))]


# ── 파일 ──────────────────────────────────────────────────────────────────────
def _insert(con: sqlite3.Connection, table: str, columns: tuple[str, ...], rows: list) -> None:
    if not rows:
        return
    names = ", ".join(f'"{c}"' for c in columns)
    vals = [tuple(r[c] for c in columns) if isinstance(r, dict) else r for r in rows]
    con.executemany(f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({", ".join("?" * len(columns))})',
                    vals)


def _v3_file(path: Path, prices: list[tuple]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        _insert(con, "daily_prices", BY_TABLE["daily_prices"].columns, prices)
        _insert(con, "stocks", STOCK_COLS, V3_STOCKS)
        _insert(con, "consensus_revision_daily", REVISION_COLS, [_revision(100.0, 5000)])
        con.execute(f"INSERT INTO investor_detail_flows (stock_code, trade_date, {', '.join(_FLOW)}) "
                    f"VALUES ('900000', ?, {', '.join('0' * len(_FLOW))})", (D_ISO,))
        _insert(con, "score_history", V3_SCORE_COLUMNS, V3_SCORES)
        _insert(con, "score_history_v2", V2_SCORE_COLUMNS, V2_SCORES)
        con.commit()
    finally:
        con.close()
    return path


def _meta(path: Path, basis: str = "morning", t_rows: dict | None = None, day: str = D_ISO, *,
          rebase: dict | None = None, at: str = "2026-10-09T00:10:00.000000+00:00",
          builds: dict | None = None, fallback: tuple[str, ...] = ()) -> None:
    con = sqlite3.connect(str(path), isolation_level=None)
    try:
        _write_meta(con, ExportResult(
            date=day, basis=basis, target=str(path), exported_at=at,
            window={"days": 14, "full": False, "from_date": "2026-09-24", "to_date": day},
            consensus_asof=day, tables={"daily_prices": TableResult(1, 0, {}, {}, t_rows, rebase)},
            equity_builds=BUILDS if builds is None else builds, builds_fallback=fallback))
    finally:
        con.close()


def _compat_file(v3: Path, path: Path, prices: dict, *, basis: str = "morning",
                 t_rows: dict | None = None, rebase: dict | None = None) -> Path:
    shutil.copy(v3, path)
    con = sqlite3.connect(str(path))
    try:
        for (code, day), row in prices.items():
            con.execute("DELETE FROM daily_prices WHERE stock_code = ? AND trade_date = ?", (code, day))
            if row is not None:
                _insert(con, "daily_prices", BY_TABLE["daily_prices"].columns, [row])
        _insert(con, "stocks", STOCK_COLS, COMPAT_STOCKS)
        _insert(con, "consensus_revision_daily", REVISION_COLS, [_revision(100.5, 5010)])
        con.execute("DELETE FROM investor_detail_flows WHERE stock_code = '900000'")
        con.execute("INSERT INTO financial_summary (stock_code, period, period_type) "
                    "VALUES ('005930', '2025/12', 'annual')")
        con.execute("DELETE FROM score_history")
        _insert(con, "score_history", V3_SCORE_COLUMNS, COMPAT_SCORES)
        con.commit()
    finally:
        con.close()
    _meta(path, basis, t_rows, rebase=rebase)
    return path


def _eq(ticker: str, day: dt.date, close: int, base: int) -> dict:
    row = tce._price_row(ticker, day, close)
    row["base_price_krw"] = base
    return row


@pytest.fixture(scope="module")
def equity_root(tmp_path_factory) -> Path:
    """price_daily — 051910 07-01~07-09 7행(07-09 분할 2:1, ks = 2) · 051911 같은 7행에 07-07·07-08 두 번 분할(K 1,1,1,1,2,4,4)
    · 088280·000880·087010·087011 D 행(087010·087011 은 KRX 공식 종가 130,600). security — 123450 스팩, 777770 상장일 = D."""
    base = tmp_path_factory.mktemp("eq")
    days = [dt.date(2026, 7, d) for d in (1, 2, 3, 6, 7, 8, 9)]
    prices = [_eq("051910", d, 1000, 1000) if d.day < 9 else _eq("051910", d, 500, 500) for d in days]
    steps = {7: (500, 500), 8: (250, 250), 9: (250, 250)}
    prices += [_eq("051911", d, *steps.get(d.day, (1000, 1000))) for d in days]
    prices += [_eq(t, dt.date(2026, 10, 8), c, c) for t, c in (("088280", 3000), ("000880", 6000),
                                                                ("087010", 130_600), ("087011", 130_600))]
    _make_stage_tree(base, "price_daily", prices, build_id=EQ_BUILD)
    sec = [tce._sec_row("051910", "LG화학", dt.date(2001, 4, 25)),
           {**tce._sec_row("123450", "스팩1호", dt.date(2025, 3, 1)), "sec_type": "spac"},
           tce._sec_row("777770", "새종목", dt.date(2026, 10, 8))]
    _make_stage_tree(base, "security", sec, build_id=EQ_BUILD)
    return base / "stage"


@pytest.fixture(scope="module")
def pair(tmp_path_factory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("pair")
    v3 = _v3_file(base / "quant_20261008.db", V3_PRICES)
    return _compat_file(v3, base / "compat.db", COMPAT_PRICES, rebase=REBASE), v3


def _tables(report: dict, table: str, tier: str = "other") -> tuple[dict, dict]:
    t = report["tables"][table]
    return {k: v["rows"] for k, v in t["categories"].items()}, t[tier]


def _keys(report: dict, table: str, category: str) -> set[str]:
    return {s["key"]["stock_code"] for s in report["tables"][table]["categories"][category]["samples"]}


# ── 범주·미설명 ───────────────────────────────────────────────────────────────
def test_daily_prices_categories_follow_qle_order(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "daily_prices")
    assert cats == {"T33": 2, "base_day_unconfirmed": 1, "delisting_timing": 2, "new_spac": 1, "rebase_adj_null": 1,
                    "v3_adj_next_day_unconfirmed": 1, "v3_adj_null": 1, "v3_backfill": 1, "v3_bad_day": 3,
                    "v3_missed_day": 2}
    assert other == {"T33_ratio_or_volume": 1, "amount": 1, "amount_null_v3": 1, "only_in_compat": 1,
                     "only_in_compat_before_v3": 1, "only_in_v3": 1, "pre_mismatch": 3}
    t = rep["tables"]["daily_prices"]
    assert t["n_within_tol"] == 1                                   # 005490 adj 0.04%
    assert t["n_diff"] == sum(cats.values()) + sum(other.values()) + 1
    assert (t["manual_consumer"], t["no_consumer"]) == ({}, {})       # 전 열이 매일 소비자 열
    assert _keys(rep, "daily_prices", "v3_missed_day") == {"051910", "088280"}
    assert _keys(rep, "daily_prices", "v3_bad_day") == {"A00001", "A00002", "A00003"}
    assert _keys(rep, "daily_prices", "rebase_adj_null") == {"000100"}
    assert _keys(rep, "daily_prices", "delisting_timing") == {"900000"}
    assert _keys(rep, "daily_prices", "v3_adj_next_day_unconfirmed") == {"378800"}   # 000660(거래량만)은 안 든다
    assert _keys(rep, "investor_detail_flows", "delisting_timing") == {"900000"}
    assert rep["daily_prices"]["bad_days_detected"] == ["2026-03-26", "2026-03-27"]
    assert rep["daily_prices"]["bad_days_unregistered"] == ["2026-03-26"]
    assert rep["daily_prices"]["v3_first_trade_date"] == "2026-03-26"
    bad = {(o["key"]["stock_code"], o["key"]["trade_date"]): o["reason"] for o in rep["other"]
           if o["table"] == "daily_prices"}
    assert bad[("000660", "2026-10-06")] == "T33_ratio_or_volume"
    assert bad[("A00001", "2026-03-26")] == "pre_mismatch"
    assert bad[("000101", "2026-08-07")] == "pre_mismatch"
    assert bad[("005380", "2026-08-05")] == "only_in_compat"


def test_stocks_t45_categories_and_consumer_tiers(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "stocks")
    assert cats == {"delisting_timing": 1, "market_cap_close_definition": 1, "new_spac": 1, "v3_missed_day": 1,
                    "v3_missing_value": 1}
    assert other == {"column:market_cap": 3, "only_in_compat": 1}    # 005930(종가 없음)·051911·000990 · 777777
    assert _keys(rep, "stocks", "v3_missing_value") == {"000880"}
    assert rep["tables"]["stocks"]["no_consumer"] == {"column:listed_date": 1}
    assert rep["tables"]["stocks"]["n_within_tol"] == 1              # 000660 +1억원(반올림)
    assert _keys(rep, "stocks", "market_cap_close_definition") == {"051910"}
    assert _keys(rep, "stocks", "delisting_timing") == {"900000"}
    delist = rep["tables"]["stocks"]["categories"]["delisting_timing"]["samples"][0]["columns"]
    assert set(delist) == {"is_active", "market_cap", "delisted_date"}
    assert _keys(rep, "stocks", "v3_missed_day") == {"088280"}
    assert "updated_at" in rep["ignored_columns"]["stocks"]


def test_manual_and_no_consumer_tables_do_not_set_rc(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    _, other = _tables(rep, "consensus_revision_daily")
    assert other == {}
    assert rep["tables"]["consensus_revision_daily"]["manual_consumer"] == {"column:op": 1}   # §2-3 S10, 0.5%
    assert rep["tables"]["consensus_revision_daily"]["no_consumer"] == {"column:eps": 1}
    assert rep["tables"]["financial_summary"]["no_consumer"] == {"only_in_compat": 1}
    assert (rep["n_manual_consumer"], rep["n_no_consumer"]) == (1, 3)
    assert {o["reason"] for o in rep["manual_consumer"]} == {"column:op"}
    assert rp.consumer_tier("consensus_annual", None) == "no_consumer"
    assert rp.consumer_tier("stocks", ("updated_at", "delisted_date")) == "no_consumer"
    assert rp.consumer_tier("stocks", ("is_active",)) == "other"
    assert rp.consumer_tier("consensus_revision_compare", ("op_1w",)) == "manual_consumer"


def test_score_tables_universe_common_ev_null_and_spearman(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "score_history")
    assert cats == {"score_common": 2, "score_universe": 1, "score_val_ev_ebitda_null": 1}
    assert other == {"score_date_not_D": 1}
    assert _keys(rep, "score_history", "score_val_ev_ebitda_null") == {"005930"}   # 값 있는 val_ev 차이는 공통 값
    s = rep["scores"]["score_history"]
    assert (s["spec"], s["total_column"], s["n_common"], s["n_v3_only"], s["n_compat_only"]) == \
        ("scope@1.0", "composite_score", 2, 1, 0)
    assert (s["v3_only"], s["compat_only"]) == (["035720"], [])
    assert s["spearman"] == pytest.approx(1.0)
    assert (s["judged"], s["ok"]) == (False, True)                   # 기본 0 = 기록형
    assert rep["tables"]["score_history_v2"]["n_diff"] == 0


def test_rc_and_totals(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    assert rep["n_other"] == 14 and rep["rc"] == 1 and rep["status"] == "other"
    assert rep["basis"] == "morning"
    assert rep["undetermined"] == {"base_day_unconfirmed": 1, "v3_adj_next_day_unconfirmed": 1}
    assert rep["categories_total"]["new_spac"] == 2                  # daily_prices + stocks
    assert rep["inputs"]["price_daily_build"] == EQ_BUILD and rep["inputs"]["builds_fallback"] == []


def test_identical_files_are_clean(tmp_path: Path, equity_root) -> None:
    v3 = _v3_file(tmp_path / "v3.db", V3_PRICES)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    _meta(compat)
    rep = rp.compare(compat, v3, D, equity_root)
    assert (rep["rc"], rep["n_other"], rep["n_manual_consumer"], rep["n_no_consumer"]) == (0, 0, 0, 0)
    assert rep["categories_total"] == {}
    assert all(t["n_diff"] == 0 for t in rep["tables"].values())


def _bare_pair(tmp_path: Path, v3_rows: list[tuple], changes: dict, **meta) -> tuple[Path, Path]:
    v3 = _v3_file(tmp_path / "v3.db", v3_rows)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    con = sqlite3.connect(str(compat))
    for (code, day), row in changes.items():
        con.execute("DELETE FROM daily_prices WHERE stock_code = ? AND trade_date = ?", (code, day))
        if row is not None:
            _insert(con, "daily_prices", BY_TABLE["daily_prices"].columns, [row])
    con.commit()
    con.close()
    _meta(compat, **meta)
    return compat, v3


def test_tolerance_boundaries_bad_day_share_and_backfill_window(tmp_path: Path, equity_root) -> None:
    """±1 정확히(500 → 501, 0.2%) 안 · 2원(2%) 밖 · 0.12% 안 · 0.2% 밖. 오류일 탐지는 3/4 = 0.75 는 들고 2/4 = 0.5 는
    빠진다. 051911 07-01 의 v3 종가가 d 뒤 4번째 행 기준값과만 같으면 백필이 아니다(그건 T-41 덮어쓰기 자리다)."""
    cases = [("B00001", 500, 501), ("B00002", 100, 102), ("B00003", 10_000, 10_012), ("B00004", 10_000, 10_020)]
    v3_rows = [_px(c, "2026-08-10", v) for c, v, _ in cases]
    changes = {(c, "2026-08-10"): _px(c, "2026-08-10", x) for c, _, x in cases}
    for i in range(4):
        code = f"C0000{i}"
        v3_rows.append(_px(code, "2026-08-11", 100))
        if i < 3:
            changes[(code, "2026-08-11")] = _px(code, "2026-08-11", 110)
    v3_rows.append(_px("051911", "2026-07-01", 500, vol=2000, adj=500))
    changes[("051911", "2026-07-01")] = _px("051911", "2026-07-01", 1000, adj=500)
    compat, v3 = _bare_pair(tmp_path, v3_rows, changes)
    rep = rp.compare(compat, v3, D, equity_root)
    cats, other = _tables(rep, "daily_prices")
    assert cats == {}
    assert other == {"pre_mismatch": 6}
    bad = {o["key"]["stock_code"] for o in rep["other"] if o["table"] == "daily_prices"}
    assert bad == {"B00002", "B00004", "C00000", "C00001", "C00002", "051911"}
    assert rep["tables"]["daily_prices"]["n_within_tol"] == 2
    assert "2026-08-11" in rep["daily_prices"]["bad_days_detected"]
    assert "2026-08-10" not in rep["daily_prices"]["bad_days_detected"]


def test_spearman_floor_boundary_flip_and_none(tmp_path: Path, equity_root) -> None:
    v3 = _v3_file(tmp_path / "v3.db", V3_PRICES)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    _meta(compat)
    exact = rp.compare(compat, v3, D, equity_root, min_spearman=1.0)          # rho = 1.0 = 하한 — 통과
    assert exact["rc"] == 0 and exact["scores"]["score_history"]["ok"] is True
    con = sqlite3.connect(str(compat))
    con.execute("UPDATE score_history SET composite_score = -composite_score WHERE score_date = ?", (D_ISO,))
    con.execute("DELETE FROM score_history_v2")
    con.commit()
    con.close()
    loose = rp.compare(compat, v3, D, equity_root)
    assert loose["scores"]["score_history"]["spearman"] == pytest.approx(-1.0)
    assert loose["tables"]["score_history"]["other"] == {}                     # 기록형
    assert loose["tables"]["score_history_v2"]["other"] == {"spearman_none": 1}   # 공통 0 — 늘 미설명(P1)
    strict = rp.compare(compat, v3, D, equity_root, min_spearman=0.975)
    assert strict["rc"] == 1
    assert strict["tables"]["score_history"]["other"] == {"spearman_below_min": 1}


def test_latest_record_of_the_day_wins(tmp_path: Path, equity_root) -> None:
    compat, v3 = _bare_pair(tmp_path, V3_PRICES, {}, basis="evening",
                            t_rows={f"{SOURCE_KIWOOM_2105}_tickers": ["035720"]},
                            at="2026-10-08T12:00:00.000000+00:00")
    _meta(compat, "morning", at="2026-10-09T00:10:00.000000+00:00")
    assert rp.compare(compat, v3, D, equity_root)["basis"] == "morning"


# ── 기준일 행(§7 daily_prices 6) ──────────────────────────────────────────────
@pytest.mark.parametrize(("next_vol", "want_cat", "want_other"), [
    (1000, {"base_day_2005": 1}, {}),
    (950, {}, {"base_day_next:T33_ratio_or_volume": 1}),
])
def test_base_day_row_is_judged_with_next_copy(pair, equity_root, tmp_path: Path, next_vol: int,
                                               want_cat: dict, want_other: dict) -> None:
    nxt = _v3_file(tmp_path / "quant_20261012.db", [_px("035720", D_ISO, 50_000, vol=next_vol)])
    rep = rp.compare(pair[0], pair[1], D, equity_root, next_v3_db=nxt)
    cats, other = _tables(rep, "daily_prices")
    assert "base_day_unconfirmed" not in cats
    assert {k: v for k, v in cats.items() if k.startswith("base_day")} == want_cat
    assert {k: v for k, v in other.items() if k.startswith("base_day")} == want_other
    assert rep["undetermined"] == {"base_day_unconfirmed": 0, "v3_adj_next_day_unconfirmed": 0}


def test_base_day_row_missing_in_next_copy_is_other(pair, equity_root, tmp_path: Path) -> None:
    nxt = _v3_file(tmp_path / "quant_20261012.db", [])
    _, other = _tables(rp.compare(pair[0], pair[1], D, equity_root, next_v3_db=nxt), "daily_prices")
    assert other["base_day_next_missing"] == 1


@pytest.mark.parametrize(("next_adj", "want_cat", "want_pre"), [(10_468, {"v3_adj_next_day": 1}, 3),
                                                             (2094, {}, 4)])
def test_adj_updated_next_day_is_judged_with_next_copy(pair, equity_root, tmp_path: Path, next_adj: float,
                                                       want_cat: dict, want_pre: int) -> None:
    """378800 — 다음 거래일 사본의 같은 행이 compat 과 맞으면 v3_adj_next_day, 여전히 옛 값이면 미설명 pre_mismatch."""
    nxt = _v3_file(tmp_path / "quant_20261012.db", [_px("378800", "2026-08-12", 5000, adj=next_adj)])
    cats, other = _tables(rp.compare(pair[0], pair[1], D, equity_root, next_v3_db=nxt), "daily_prices")
    assert {k: v for k, v in cats.items() if k.startswith("v3_adj_next")} == want_cat
    assert other["pre_mismatch"] == want_pre


def test_delisting_window_and_adj_only_unconfirmed_bounds(tmp_path: Path, equity_root) -> None:
    """v3 에만 있는 폐지 종목 행은 compat 폐지일(10-07) ≤ 날짜 ≤ D 일 때만 delisting_timing — 10-06·10-12 는 미설명.
    다음 거래일 사본이 없을 때 미확정으로 두는 것은 adj_close 가 다른 행뿐이다(09-14 뒤 종가만 다르고 adj 가 같은 행은
    미설명 T33_ratio_or_volume)."""
    rows = [_px("900000", d, 100, vol=0) for d in ("2026-10-06", "2026-10-07", D_ISO, NEXT_ISO)]
    rows.append(_px("000270", "2026-10-05", 50_500, adj=50_000))
    changes: dict = {("900000", d): None for d in ("2026-10-06", "2026-10-07", D_ISO, NEXT_ISO)}
    changes[("000270", "2026-10-05")] = _px("000270", "2026-10-05", 50_000, adj=50_000)
    compat, v3 = _bare_pair(tmp_path, rows, changes)
    con = sqlite3.connect(str(compat))
    con.execute("UPDATE stocks SET is_active = 0, delisted_date = '2026-10-07' WHERE stock_code = '900000'")
    con.commit()
    con.close()
    rep = rp.compare(compat, v3, D, equity_root)
    assert _keys(rep, "daily_prices", "delisting_timing") == {"900000"}
    assert rep["tables"]["daily_prices"]["categories"]["delisting_timing"]["rows"] == 2
    dp_other = {(o["key"]["stock_code"], o["key"]["trade_date"]): o["reason"] for o in rep["other"]
                if o["table"] == "daily_prices"}
    assert dp_other == {("900000", "2026-10-06"): "only_in_v3", ("900000", NEXT_ISO): "only_in_v3",
                        ("000270", "2026-10-05"): "T33_ratio_or_volume"}


def test_market_cap_close_definition_rounding_bound(tmp_path: Path, equity_root) -> None:
    """서버 10-07 087010 — compat 30,452(KRX 종가 130,600) · v3 22,595(애프터마켓 96,900), 함의 주식수 같음.
    두 시총이 각각 억원 반올림이라 경계는 0.5·(1 + 종가 비) = 1.174 다: 이 행 |30,452 − 30,453.12| = 1.12 는 범주
    (옛 경계 ±1 로는 미설명), 087011 의 1.18(27,286 대 20,246 × 1.3478) 은 경계 밖이라 미설명."""
    rows = [_px("087010", D_ISO, 96_900), _px("087011", D_ISO, 96_900)]
    compat, v3 = _bare_pair(tmp_path, rows, {})
    for path, caps in ((v3, (22_595, 20_246)), (compat, (30_452, 27_286))):
        con = sqlite3.connect(str(path))
        _insert(con, "stocks", STOCK_COLS, [_stock("087010", caps[0], V3_TIME), _stock("087011", caps[1], V3_TIME)])
        con.commit()
        con.close()
    rep = rp.compare(compat, v3, D, equity_root)
    assert _keys(rep, "stocks", "market_cap_close_definition") == {"087010"}
    assert [(o["key"]["stock_code"], o["reason"]) for o in rep["other"] if o["table"] == "stocks"] == \
        [("087011", "column:market_cap")]


def test_builds_fallback_tier_is_recorded_not_judged(tmp_path: Path, equity_root) -> None:
    """--allow-current-builds 로 현판이 쓰인 원천 표(security)가 원천인 v3 표·열 차이는 builds_fallback 갈래(rc 밖)."""
    assert rp.fallback_columns(["security"]) == {
        "stocks": frozenset({"stock_name", "listed_date", "is_active", "delisted_date"}),
        "financial_summary": rp.ALL}
    assert rp.fallback_columns([]) == {}

    def run(fallback: tuple[str, ...]) -> dict:
        base = tmp_path / ("fb" if fallback else "plain")
        v3 = _v3_file(base / "v3.db", V3_PRICES)
        compat = base / "compat.db"
        shutil.copy(v3, compat)
        con = sqlite3.connect(str(compat))
        con.execute("UPDATE stocks SET stock_name = '국일제지' WHERE stock_code = '005930'")
        con.execute("UPDATE stocks SET market_cap = 1005 WHERE stock_code = '005930'")
        con.execute("INSERT INTO financial_summary (stock_code, period, period_type) VALUES ('005930', '2025/12', 'annual')")
        con.commit()
        con.close()
        _meta(compat, fallback=fallback)
        return rp.compare(compat, v3, D, equity_root)

    fb = run(("security",))
    assert fb["tables"]["stocks"]["builds_fallback"] == {"column:stock_name": 1}
    assert fb["tables"]["stocks"]["other"] == {"column:market_cap": 1}    # 시총은 security 가 원천이 아니다
    assert fb["tables"]["financial_summary"]["builds_fallback"] == {"only_in_compat": 1}
    assert (fb["n_builds_fallback"], fb["n_other"], fb["inputs"]["builds_fallback"]) == (2, 1, ["security"])
    plain = run(())
    assert plain["tables"]["stocks"]["other"] == {"column:market_cap": 1, "column:stock_name": 1}
    assert plain["n_builds_fallback"] == 0


# ── 장 마감 판 T 행(§7 장 마감 판 · daily_prices 5) ───────────────────────────
EVENING_V3 = [_px("P00001", D_ISO, 10_100, vol=1200), _px("P00002", D_ISO, 10_000, ohl=9_900),
              _px("P00003", D_ISO, 10_000), _px("K00001", D_ISO, 10_000, vol=900),
              _px("K00002", D_ISO, 10_000, ohl=9_900),
              _px("M00001", D_ISO, 10_000), _px("777770", D_ISO, 10_000), _px("X00001", D_ISO, 10_000)]
EVENING_COMPAT = {
    ("P00001", D_ISO): _px("P00001", D_ISO, 10_000, vol=1000),       # postclose — KRX 종가·16:00 전 거래량
    ("P00002", D_ISO): _px("P00002", D_ISO, 10_000, amount=11),       # 시·고·저 = 종가, 거래대금 근사
    ("P00003", D_ISO): _px("P00003", D_ISO, 10_000, adj=None),        # T 단계 미상
    ("K00001", D_ISO): _px("K00001", D_ISO, 10_000, vol=1000),        # 21:05 원장 — v3 20:05 거래량과 다름
    ("K00002", D_ISO): _px("K00002", D_ISO, 10_000),                  # 21:05 원장 — 채움만 다름
    ("M00001", D_ISO): None, ("777770", D_ISO): None, ("X00001", D_ISO): None,
}
EVENING_T_ROWS = {f"{SOURCE_KIWOOM_2105}_tickers": ["K00001", "K00002"], "missing_tickers": ["M00001"]}


def _evening_pair(tmp_path: Path) -> tuple[Path, Path]:
    v3 = _v3_file(tmp_path / "v3.db", EVENING_V3)
    con = sqlite3.connect(str(v3))
    for code in ("P00001", "K00001"):
        con.execute(f"INSERT INTO investor_detail_flows (stock_code, trade_date, {', '.join(_FLOW)}) "
                    f"VALUES (?, ?, {', '.join('?' * len(_FLOW))})", (code, D_ISO, *[100] * len(_FLOW)))
    con.commit()
    con.close()
    compat = _compat_file(v3, tmp_path / "compat.db", EVENING_COMPAT, basis="evening", t_rows=EVENING_T_ROWS)
    con = sqlite3.connect(str(compat))
    con.execute(f"UPDATE investor_detail_flows SET {_FLOW[0]} = 999 WHERE trade_date = ?", (D_ISO,))
    con.commit()
    con.close()
    return compat, v3


def test_evening_t_rows_row_by_row(tmp_path: Path, equity_root) -> None:
    compat, v3 = _evening_pair(tmp_path)
    rep = rp.compare(compat, v3, D, equity_root)
    assert rep["basis"] == "evening"
    by_row = {cat: _keys(rep, "daily_prices", cat) for cat in rep["tables"]["daily_prices"]["categories"]}
    assert by_row == {"evening_t_postclose": {"P00001"}, "evening_t_fill": {"P00002", "K00002"},
                      "evening_t_step_null": {"P00003"}, "base_day_unconfirmed": {"K00001"},
                      "evening_t_missing": {"M00001"}, "evening_t_new_listing": {"777770"}}
    assert rep["tables"]["daily_prices"]["other"] == {"only_in_v3": 1}
    assert _keys(rep, "investor_detail_flows", "evening_t_postclose") == {"P00001"}
    flows_other = [o for o in rep["other"] if o["table"] == "investor_detail_flows"]
    assert [(o["key"]["stock_code"], o["reason"]) for o in flows_other] == [("K00001", f"column:{_FLOW[0]}")]


def test_evening_kiwoom_row_is_confirmed_with_next_copy(tmp_path: Path, equity_root) -> None:
    compat, v3 = _evening_pair(tmp_path)
    nxt = _v3_file(tmp_path / "quant_20261012.db", [_px("K00001", D_ISO, 10_000, vol=1000, amount=77)])
    rep = rp.compare(compat, v3, D, equity_root, next_v3_db=nxt)
    assert _keys(rep, "daily_prices", "base_day_2005") == {"K00001"}       # 거래대금은 채움이라 보지 않는다


# ── 입력 오류(rc 2) ───────────────────────────────────────────────────────────
def test_missing_compat_record_is_input_error(tmp_path: Path, equity_root) -> None:
    v3 = _v3_file(tmp_path / "v3.db", V3_PRICES)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    with pytest.raises(rp.ReplayInputError, match="ok 반영 기록이 없다"):
        rp.compare(compat, v3, D, equity_root)
    _meta(compat, day="2026-10-07")                                 # 다른 날 기록
    with pytest.raises(rp.ReplayInputError, match="2026-10-08"):
        rp.compare(compat, v3, D, equity_root)


def test_equity_builds_come_from_the_compat_record(tmp_path: Path, equity_root) -> None:
    """NIT-1 — K 사슬·스팩 목록은 그날 compat 이 쓴 판으로 센다. 기록에 없는 판·사라진 판이면 입력 오류."""
    compat, v3 = _bare_pair(tmp_path, V3_PRICES, {}, builds={"price_daily": "m_20250101T000000_000000Z",
                                                             "security": EQ_BUILD})
    with pytest.raises(rp.ReplayInputError, match="m_20250101T000000_000000Z"):
        rp.compare(compat, v3, D, equity_root)
    compat2, v3b = _bare_pair(tmp_path / "b", V3_PRICES, {}, builds={"security": EQ_BUILD})
    with pytest.raises(rp.ReplayInputError, match="price_daily 판이 없다"):
        rp.compare(compat2, v3b, D, equity_root)


@pytest.mark.parametrize("case", ["no_file", "bad_date", "no_equity", "missing_table", "no_next"])
def test_input_errors_return_rc_2(pair, equity_root, tmp_path: Path, case: str, capsys) -> None:
    args = {"--compat-db": str(pair[0]), "--v3-db": str(pair[1]), "--date": D,
            "--equity-root": str(equity_root)}
    if case == "no_file":
        args["--v3-db"] = str(tmp_path / "none.db")
    elif case == "bad_date":
        args["--date"] = "2026-10-08"
    elif case == "no_equity":
        args["--equity-root"] = str(tmp_path)
    elif case == "missing_table":
        bare = tmp_path / "bare.db"
        sqlite3.connect(str(bare)).close()
        args["--v3-db"] = str(bare)
    else:
        args["--next-v3-db"] = str(tmp_path / "quant_20261012.db")
    out = tmp_path / "r.json"
    argv = ["compare", *[x for kv in args.items() for x in kv], "--json", str(out)]
    assert rp.main(argv) == 2
    assert "v3_replay 입력 오류" in capsys.readouterr().err
    assert not out.exists()


# ── 표 목록·규약은 compat 코드에서 온다 ─────────────────────────────────────────
def test_tables_come_from_compat_mappings(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    assert rp.TABLES is TABLES                                      # 대조기는 v3_post 의 반영 표 목록을 그대로 쓴다
    assert list(rep["tables"]) == list(TABLES) == [m.v3_table for m in MAPPINGS]
    for name, t in rep["tables"].items():
        assert tuple(t["pk"]) == BY_TABLE[name].pk


def test_tolerance_consumer_and_ignored_columns_exist_in_mappings() -> None:
    named = {**rp.ROUNDED, **{t: set(c) for t, c in rp.IGNORED.items()}, **rp.MANUAL_CONSUMER,
             **{t: c for t, c in rp.DAILY_CONSUMER.items() if c != rp.ALL}}
    for table, cols in named.items():
        assert cols, table
        assert set(cols) <= set(BY_TABLE[table].columns), table
    assert set(rp.DAILY_CONSUMER) | set(rp.MANUAL_CONSUMER) <= set(TABLES)
    assert rp.DAILY_PRICES_COLUMNS == BY_TABLE["daily_prices"].columns
    assert all(c.basis.startswith("§7") for c in rp.CATEGORIES)
    assert len({c.key for c in rp.CATEGORIES}) == len(rp.CATEGORIES)


# ── CLI·실행기 보조 ───────────────────────────────────────────────────────────
def test_cli_writes_json_and_returns_rc(pair, equity_root, tmp_path: Path, capsys) -> None:
    out = tmp_path / "d" / "v3_replay.json"
    rc = rp.main(["compare", "--compat-db", str(pair[0]), "--v3-db", str(pair[1]), "--date", D,
                  "--equity-root", str(equity_root), "--json", str(out)])
    assert rc == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert (doc["rc"], doc["n_other"], doc["tool"], doc["schema"]) == (1, 14, "compat.v3_replay", 3)
    assert set(doc["registry"]) == {c.key for c in rp.CATEGORIES}
    assert {"other", "manual_consumer", "no_consumer"} <= set(doc)
    assert "v3_replay D=2026-10-08 basis=morning rc=1" in capsys.readouterr().out


def test_dates_lists_trading_days_with_next(tmp_path: Path, capsys) -> None:
    cal = tmp_path / "cal"
    cal.mkdir()
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5] + ["20261009"]
    (cal / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}), encoding="utf-8")
    assert rp.main(["dates", "--from", "20261008", "--to", "20261012", "--calendar-dir", str(cal)]) == 0
    assert capsys.readouterr().out.splitlines() == ["20261008\t20261012", "20261012\t20261013"]
    assert rp.main(["dates", "--from", "20261230", "--to", "20261231", "--calendar-dir", str(cal)]) == 0
    assert capsys.readouterr().out.splitlines() == ["20261230\t20261231", "20261231\t-"]   # 2027 판 없음
    assert rp.main(["dates", "--from", "20270102", "--to", "20270105", "--calendar-dir", str(cal)]) == 2


def test_tsv_merges_reports_and_status_stubs(pair, equity_root, tmp_path: Path) -> None:
    full = tmp_path / "20261008" / "v3_replay.json"
    assert rp.main(["compare", "--compat-db", str(pair[0]), "--v3-db", str(pair[1]), "--date", D,
                    "--equity-root", str(equity_root), "--json", str(full)]) == 1
    stub = tmp_path / "20261007" / "v3_replay.json"
    stub.parent.mkdir()
    stub.write_text(json.dumps({"date": "20261007", "status": "no_copy", "rc": 1}), encoding="utf-8")
    out = tmp_path / "summary.tsv"
    assert rp.main(["tsv", "--out", str(out), str(full), str(stub)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == list(rp.TSV_HEAD)
    first, second = (ln.split("\t") for ln in lines[1:])
    assert first[:4] == ["20261007", "no_copy", "1", "-"]
    assert second[:9] == ["20261008", "other", "1", "14", "1", "3", "0", "1.0000", "1.0000"]
    assert "new_spac=2" in second[9] and "daily_prices.pre_mismatch=3" in second[10]
    assert "listed_date" not in second[10]                           # 소비자 없는 갈래는 other 열에 안 든다


# ── 그림자 날 합치기(COMPAT §8-2) ─────────────────────────────────────────────
def _staging(path: Path, scores: list[dict], tables: tuple[str, ...], at: str) -> Path:
    _v3_file(path, [_px("005930", D_ISO, 70_000)])
    con = sqlite3.connect(str(path))
    con.execute("DELETE FROM score_history")
    _insert(con, "score_history", V3_SCORE_COLUMNS, scores)
    con.commit()
    con.close()
    con = sqlite3.connect(str(path), isolation_level=None)
    _write_meta(con, ExportResult(date=D_ISO, basis="evening", target=str(path), exported_at=at,
                                  window={"from_date": "2026-09-24", "to_date": D_ISO}, consensus_asof=D_ISO,
                                  tables={t: TableResult(1, 0, {}) for t in tables}))
    con.close()
    return path


def test_shadow_merge_takes_scores_from_evening_and_the_rest_from_refill(tmp_path: Path) -> None:
    seven = tuple(t for t in TABLES if t not in ("score_history", "score_history_v2"))
    ev = _staging(tmp_path / "ev.db", COMPAT_SCORES, TABLES, "2026-10-08T07:00:00.000000+00:00")
    rf = _staging(tmp_path / "rf.db", V3_SCORES, seven, "2026-10-08T12:30:00.000000+00:00")
    con = sqlite3.connect(str(rf))
    con.execute("UPDATE daily_prices SET close = 70100 WHERE trade_date = ?", (D_ISO,))
    con.commit()
    con.close()
    before = {p: p.read_bytes() for p in (ev, rf)}
    out = tmp_path / "merged.db"
    assert rp.main(["shadow-merge", "--evening", str(ev), "--refill", str(rf), "--date", D, "--out", str(out)]) == 0
    assert {p: p.read_bytes() for p in (ev, rf)} == before            # 두 스테이징은 읽기만
    con = sqlite3.connect(str(out))
    d_scores = con.execute("SELECT stock_code, composite_score FROM score_history WHERE score_date = ? "
                           "ORDER BY 1", (D_ISO,)).fetchall()
    other_day = con.execute("SELECT composite_score FROM score_history WHERE score_date = '2026-10-07'").fetchall()
    close = con.execute("SELECT close FROM daily_prices WHERE trade_date = ?", (D_ISO,)).fetchall()
    metas = con.execute("SELECT exported_at FROM _compat_meta ORDER BY 1").fetchall()
    con.close()
    assert d_scores == [("000660", 20.0), ("005930", 30.0)]          # ⑥ 의 D 행(v3 의 035720 은 빠진다)
    assert other_day == [(5.0,)]                                      # 다른 날은 refill(=본 파일) 그대로
    assert close == [(70_100,)]
    assert len(metas) == 2


def test_shadow_merge_refuses_evening_without_scores(tmp_path: Path, capsys) -> None:
    seven = tuple(t for t in TABLES if t not in ("score_history", "score_history_v2"))
    ev = _staging(tmp_path / "ev.db", COMPAT_SCORES, seven, "2026-10-08T07:00:00.000000+00:00")
    rf = _staging(tmp_path / "rf.db", V3_SCORES, seven, "2026-10-08T12:30:00.000000+00:00")
    assert rp.main(["shadow-merge", "--evening", str(ev), "--refill", str(rf), "--date", D,
                    "--out", str(tmp_path / "m.db")]) == 2
    assert "점수 두 표 포함" in capsys.readouterr().err
