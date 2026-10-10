"""compat.v3_replay — v3 사본 대 compat 반영본 대조기(컷오버 트랙 QL-G · COMPAT_LAYER §7).

픽스처는 작은 sqlite 두 개다: v3 사본(`v3_schema.sql` + 행)과 compat 반영본(사본의 복사본 + 바꾼 행 + `_compat_meta`
기록 1행). equity 루트는 `price_daily`(K 사슬 — v3_backfill)·`security`(신규 스팩·T 당일 상장) 두 표만 짓는다.
범주마다 행 하나를 일부러 만들어 두고 범주·미설명 수와 rc 를 리터럴로 고정한다.

기준일 D = 2026-10-08(목), 다음 거래일 = 2026-10-12(10-09 휴장). 09-14(T-33) 뒤는 post 구간이다.
  005930 10-06  수준만 다르고 adj/close 비·거래량 같다              → T33
  000270 10-06  같은 경우인데 비 차이가 1원 밖·0.15% 안(상대 갈래)  → T33
  000660 10-06  거래량만 다르다(09-14 뒤)                          → 미설명 T33_ratio_or_volume
  035720 D      거래량만 다르다(v3 20:05 값)                       → base_day_unconfirmed(다음 사본 없음)
  068270 08-03  v3 adj_close NULL                                  → v3_adj_null
  A00001·A00002 03-27 종가 전부 다름(등록 오류일)                  → v3_bad_day ×2
  A00001·A00002 03-26 종가 전부 다름(미등록 오류일)                → 미설명 pre_mismatch ×2, 탐지 목록에는 든다
  051910 07-01  v3 종가 = 원종가 × K(d)/K(07-09)(분할 2:1)          → v3_backfill
  005380 10-05  거래대금만 다르다                                  → 미설명 amount
  005380 08-04  v3 거래대금 NULL                                   → 미설명 amount_null_v3
  005380 01-05  compat 에만(v3 첫 날 03-26 전)                     → 미설명 only_in_compat_before_v3
  005380 08-05  compat 에만                                        → 미설명 only_in_compat
  096770 D      v3 에만                                            → 미설명 only_in_v3
  123450 D      신규 스팩(security spac · v3 stocks 없음)           → new_spac(stocks 도)
  005490 08-06  adj_close 0.04% 차이                               → 허용 오차 안
  stocks        005930 시총 +5 → 미설명 column:market_cap, 000660 +1 → 허용 오차 안, updated_at 은 대조 밖
  score_history D 에 v3 3종목 · compat 2종목(값 ×10, val_ev_ebitda NULL) → score_universe 1 · score_common 2 ·
                score_val_ev_ebitda_null 2, 10-07 행 차이 → 미설명 score_date_not_D
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
from compat.v3_post import TABLES
from conftest import _make_stage_tree
from model.contracts import V2_SCORE_COLUMNS, V3_SCORE_COLUMNS

D, D_ISO, NEXT_ISO = "20261008", "2026-10-08", "2026-10-12"
EQ_BUILD = "m_20261009T000000_000000Z"


# ── 행 ────────────────────────────────────────────────────────────────────────
def _px(code: str, day: str, close: float, *, vol: int = 1000, amount: int | None = 10,
        adj: float | None = -1.0, ohl: float | None = None) -> tuple:
    """daily_prices 9열 — 시·고·저 기본 = 종가, adj 기본 = 종가."""
    o = close if ohl is None else ohl
    return (code, day, o, o, o, close, vol, amount, close if adj == -1.0 else adj)


V3_PRICES = [
    _px("005930", "2026-10-06", 70_500, adj=35_250),
    _px("000270", "2026-10-06", 705_000, adj=352_500),
    _px("000660", "2026-10-06", 100_000, vol=2000),
    _px("035720", D_ISO, 50_000, vol=900),
    _px("068270", "2026-08-03", 1000, adj=None),
    _px("A00001", "2026-03-27", 2000), _px("A00002", "2026-03-27", 3000),
    _px("A00001", "2026-03-26", 2000), _px("A00002", "2026-03-26", 3000),
    _px("051910", "2026-07-01", 500, vol=2000, adj=500),
    _px("000001", "2026-07-01", 100), _px("000002", "2026-07-01", 100),   # 07-01 이 오류일로 잡히지 않게
    _px("005380", "2026-10-05", 9000, amount=100),
    _px("005380", "2026-08-04", 9000, amount=None),
    _px("096770", D_ISO, 7000),
    _px("005490", "2026-08-06", 1000, adj=1000.0),
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
    ("A00001", "2026-03-26"): _px("A00001", "2026-03-26", 2100),
    ("A00002", "2026-03-26"): _px("A00002", "2026-03-26", 3100),
    ("051910", "2026-07-01"): _px("051910", "2026-07-01", 1000, adj=500),
    ("005380", "2026-10-05"): _px("005380", "2026-10-05", 9000, amount=150),
    ("005380", "2026-08-04"): _px("005380", "2026-08-04", 9000, amount=0),
    ("005380", "2026-01-05"): _px("005380", "2026-01-05", 9000),
    ("005380", "2026-08-05"): _px("005380", "2026-08-05", 9000),
    ("096770", D_ISO): None,
    ("123450", D_ISO): _px("123450", D_ISO, 2000),
    ("005490", "2026-08-06"): _px("005490", "2026-08-06", 1000, adj=1000.4),
}
STOCK_COLS = ("stock_code", "stock_name", "market", "sector", "market_cap", "listed_date", "is_active",
              "delisted_date", "updated_at")


def _stock(code: str, cap: int, updated: str) -> tuple:
    return (code, f"이름{code}", "KOSPI", "전기/전자", cap, "2000-01-04", 1, None, updated)


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
COMPAT_SCORES = [_score(V3_SCORE_COLUMNS, "composite_score", c, D_ISO, t, None)
                 for c, t in (("005930", 30.0), ("000660", 20.0))]
COMPAT_SCORES.append(_score(V3_SCORE_COLUMNS, "composite_score", "005930", "2026-10-07", 6.0, 7.5))


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
        _insert(con, "stocks", STOCK_COLS, [_stock("005930", 1000, "2026-10-08 11:05:00"),
                                            _stock("000660", 2000, "2026-10-08 11:05:00")])
        _insert(con, "score_history", V3_SCORE_COLUMNS, V3_SCORES)
        _insert(con, "score_history_v2", V2_SCORE_COLUMNS,
                [_score(V2_SCORE_COLUMNS, "total_score", "005930", D_ISO, 50.0, None)])
        con.commit()
    finally:
        con.close()
    return path


def _meta(path: Path, basis: str = "morning", t_rows: dict | None = None, day: str = D_ISO) -> None:
    con = sqlite3.connect(str(path), isolation_level=None)
    try:
        _write_meta(con, ExportResult(
            date=day, basis=basis, target=str(path), exported_at="2026-10-09T00:10:00.000000+00:00",
            window={"days": 14, "full": False, "from_date": "2026-09-24", "to_date": day},
            consensus_asof=day, tables={"daily_prices": TableResult(1, 0, {}, {}, t_rows)}))
    finally:
        con.close()


def _compat_file(v3: Path, path: Path, prices: dict, *, basis: str = "morning",
                 t_rows: dict | None = None) -> Path:
    shutil.copy(v3, path)
    con = sqlite3.connect(str(path))
    try:
        for (code, day), row in prices.items():
            con.execute("DELETE FROM daily_prices WHERE stock_code = ? AND trade_date = ?", (code, day))
            if row is not None:
                _insert(con, "daily_prices", BY_TABLE["daily_prices"].columns, [row])
        _insert(con, "stocks", STOCK_COLS, [_stock("005930", 1005, "2026-10-09T00:10:00"),
                                            _stock("000660", 2001, "2026-10-09T00:10:00"),
                                            _stock("123450", 50, "2026-10-09T00:10:00")])
        con.execute("DELETE FROM score_history")
        _insert(con, "score_history", V3_SCORE_COLUMNS, COMPAT_SCORES)
        con.commit()
    finally:
        con.close()
    _meta(path, basis, t_rows)
    return path


def _eq(ticker: str, day: dt.date, close: int, base: int) -> dict:
    row = tce._price_row(ticker, day, close)
    row["base_price_krw"] = base
    return row


@pytest.fixture(scope="module")
def equity_root(tmp_path_factory) -> Path:
    """price_daily — 051910 07-01~07-09 7행, 07-09 에 기준가 500(전일 종가 1,000 → 분할 2:1, ks = 2).
    security — 123450 스팩, 777770 상장일 = D."""
    base = tmp_path_factory.mktemp("eq")
    days = [dt.date(2026, 7, d) for d in (1, 2, 3, 6, 7, 8, 9)]
    prices = [_eq("051910", d, 1000, 1000) if d.day < 9 else _eq("051910", d, 500, 500) for d in days]
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
    return _compat_file(v3, base / "compat.db", COMPAT_PRICES), v3


def _tables(report: dict, table: str) -> tuple[dict, dict]:
    t = report["tables"][table]
    return {k: v["rows"] for k, v in t["categories"].items()}, t["other"]


# ── 범주·미설명 ───────────────────────────────────────────────────────────────
def test_daily_prices_categories_follow_qle_order(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "daily_prices")
    assert cats == {"T33": 2, "base_day_unconfirmed": 1, "new_spac": 1, "v3_adj_null": 1,
                    "v3_backfill": 1, "v3_bad_day": 2}
    assert other == {"T33_ratio_or_volume": 1, "amount": 1, "amount_null_v3": 1, "only_in_compat": 1,
                     "only_in_compat_before_v3": 1, "only_in_v3": 1, "pre_mismatch": 2}
    t = rep["tables"]["daily_prices"]
    assert t["n_within_tol"] == 1                                   # 005490 adj 0.04%
    assert t["n_diff"] == sum(cats.values()) + sum(other.values()) + 1
    assert rep["daily_prices"]["bad_days_detected"] == ["2026-03-26", "2026-03-27"]
    assert rep["daily_prices"]["bad_days_unregistered"] == ["2026-03-26"]
    assert rep["daily_prices"]["v3_first_trade_date"] == "2026-03-26"
    bad = {(o["key"]["stock_code"], o["key"]["trade_date"]): o["reason"] for o in rep["other"]
           if o["table"] == "daily_prices"}
    assert bad[("000660", "2026-10-06")] == "T33_ratio_or_volume"
    assert bad[("A00001", "2026-03-26")] == "pre_mismatch"


def test_stocks_new_spac_rounding_and_ignored_write_time(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "stocks")
    assert cats == {"new_spac": 1}
    assert other == {"column:market_cap": 1}                         # 005930 +5억원
    assert rep["tables"]["stocks"]["n_within_tol"] == 1              # 000660 +1억원(반올림)
    assert "updated_at" in rep["ignored_columns"]["stocks"]


def test_score_tables_universe_common_ev_null_and_spearman(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    cats, other = _tables(rep, "score_history")
    assert cats == {"score_common": 2, "score_universe": 1, "score_val_ev_ebitda_null": 2}
    assert other == {"score_date_not_D": 1}
    s = rep["scores"]["score_history"]
    assert (s["spec"], s["total_column"], s["n_common"], s["n_v3_only"], s["n_compat_only"]) == \
        ("scope@1.0", "composite_score", 2, 1, 0)
    assert (s["v3_only"], s["compat_only"]) == (["035720"], [])
    assert s["spearman"] == pytest.approx(1.0)
    assert (s["judged"], s["ok"]) == (False, True)                   # 기본 0 = 기록형
    assert rep["tables"]["score_history_v2"]["n_diff"] == 0


def test_rc_and_totals(pair, equity_root) -> None:
    rep = rp.compare(pair[0], pair[1], D, equity_root)
    assert rep["n_other"] == 10 and rep["rc"] == 1 and rep["status"] == "other"
    assert rep["basis"] == "morning"
    assert rep["undetermined"] == {"base_day_unconfirmed": 1}
    assert rep["categories_total"]["new_spac"] == 2                  # daily_prices + stocks


def test_identical_files_are_clean(tmp_path: Path, equity_root) -> None:
    v3 = _v3_file(tmp_path / "v3.db", V3_PRICES)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    _meta(compat)
    rep = rp.compare(compat, v3, D, equity_root)
    assert (rep["rc"], rep["n_other"], rep["categories_total"]) == (0, 0, {})
    assert all(t["n_diff"] == 0 for t in rep["tables"].values())


def test_spearman_floor_turns_rank_flip_into_other(tmp_path: Path, equity_root) -> None:
    v3 = _v3_file(tmp_path / "v3.db", V3_PRICES)
    compat = tmp_path / "compat.db"
    shutil.copy(v3, compat)
    con = sqlite3.connect(str(compat))
    con.execute("UPDATE score_history SET composite_score = -composite_score WHERE score_date = ?", (D_ISO,))
    con.commit()
    con.close()
    _meta(compat)
    loose = rp.compare(compat, v3, D, equity_root)
    assert loose["rc"] == 0 and loose["scores"]["score_history"]["spearman"] == pytest.approx(-1.0)
    strict = rp.compare(compat, v3, D, equity_root, min_spearman=0.975)
    assert strict["rc"] == 1
    assert strict["tables"]["score_history"]["other"] == {"spearman_below_min": 1}


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
    assert rep["undetermined"] == {"base_day_unconfirmed": 0}


def test_base_day_row_missing_in_next_copy_is_other(pair, equity_root, tmp_path: Path) -> None:
    nxt = _v3_file(tmp_path / "quant_20261012.db", [])
    _, other = _tables(rp.compare(pair[0], pair[1], D, equity_root, next_v3_db=nxt), "daily_prices")
    assert other["base_day_next_missing"] == 1


# ── 장 마감 판 T 행(§7 장 마감 판 · daily_prices 5) ───────────────────────────
def test_evening_t_rows(tmp_path: Path, equity_root) -> None:
    v3_rows = [_px("P00001", D_ISO, 10_100, vol=1200), _px("P00002", D_ISO, 10_000, ohl=9_900),
               _px("P00003", D_ISO, 10_000), _px("K00001", D_ISO, 10_000, vol=900),
               _px("M00001", D_ISO, 10_000), _px("777770", D_ISO, 10_000), _px("X00001", D_ISO, 10_000)]
    v3 = _v3_file(tmp_path / "v3.db", v3_rows)
    con = sqlite3.connect(str(v3))
    for code in ("P00001", "K00001"):
        con.execute(f"INSERT INTO investor_detail_flows (stock_code, trade_date, {', '.join(_FLOW)}) "
                    f"VALUES (?, ?, {', '.join('?' * len(_FLOW))})", (code, D_ISO, *[100] * len(_FLOW)))
    con.commit()
    con.close()
    compat = tmp_path / "compat.db"
    _compat_file(v3, compat, {
        ("P00001", D_ISO): _px("P00001", D_ISO, 10_000, vol=1000),       # postclose — KRX 종가·16:00 전 거래량
        ("P00002", D_ISO): _px("P00002", D_ISO, 10_000, amount=11),       # 시·고·저 = 종가, 거래대금 근사
        ("P00003", D_ISO): _px("P00003", D_ISO, 10_000, adj=None),        # T 단계 미상
        ("K00001", D_ISO): _px("K00001", D_ISO, 10_000, vol=1000),        # 21:05 원장 — v3 20:05 거래량과 다름
        ("M00001", D_ISO): None, ("777770", D_ISO): None, ("X00001", D_ISO): None,
    }, basis="evening", t_rows={"kiwoom_2105_tickers": ["K00001"], "missing_tickers": ["M00001"]})
    con = sqlite3.connect(str(compat))
    con.execute(f"UPDATE investor_detail_flows SET {_FLOW[0]} = 999 WHERE trade_date = ?", (D_ISO,))
    con.commit()
    con.close()
    rep = rp.compare(compat, v3, D, equity_root)
    assert rep["basis"] == "evening"
    cats, other = _tables(rep, "daily_prices")
    assert cats == {"evening_t_postclose": 1, "evening_t_fill": 1, "evening_t_step_null": 1,
                    "base_day_unconfirmed": 1, "evening_t_missing": 1, "evening_t_new_listing": 1}
    assert other == {"only_in_v3": 1}
    cats, other = _tables(rep, "investor_detail_flows")
    assert cats == {"evening_t_postclose": 1}
    assert other == {f"column:{_FLOW[0]}": 1}                       # 21:05 원장 종목 수급은 v3 와 같아야 한다


_FLOW = tuple(c for c in BY_TABLE["investor_detail_flows"].columns if c not in ("stock_code", "trade_date"))


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


def test_tolerance_and_ignored_columns_exist_in_mappings() -> None:
    for table, cols in {**rp.ROUNDED, **{t: set(c) for t, c in rp.IGNORED.items()}}.items():
        assert cols, table
        assert set(cols) <= set(BY_TABLE[table].columns), table
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
    assert (doc["rc"], doc["n_other"], doc["tool"], doc["schema"]) == (1, 10, "compat.v3_replay", 1)
    assert set(doc["registry"]) == {c.key for c in rp.CATEGORIES}
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
    assert second[:6] == ["20261008", "other", "1", "10", "1.0000", "-"]   # v2 공통 1종목 → Spearman 없음
    assert "new_spac=2" in second[6] and "daily_prices.pre_mismatch=2" in second[7]
