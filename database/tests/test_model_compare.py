"""model.compare 대조 도구 테스트 — 합성 sqlite 두 개로 분류·판정·리포트를 고정한다.

열 이름은 v3 `backend/db/schema.py:155-187` + `backend/db/migration_sql.py` 원문에서
베꼈다(부분집합).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from model.compare import CompareError, CompareResult, compare, main, write_report

DATE = "2026-09-23"

# v3 score_history(48열) 중 테스트에 필요한 최소 부분집합 — 이름·타입은 원문 그대로.
SH_DDL = """
CREATE TABLE score_history (
    stock_code TEXT(6) NOT NULL,
    score_date TEXT NOT NULL,
    momentum_score REAL,
    revision_score REAL,
    flow_score REAL,
    valuation_score REAL,
    composite_score REAL NOT NULL,
    rank INTEGER,
    quality_score REAL,
    r1m REAL,
    flow_inst_5d REAL,
    val_per REAL,
    op_1w_flag TEXT,
    PRIMARY KEY (stock_code, score_date)
) WITHOUT ROWID
"""

# v3 score_history_v2(21열) 중 부분집합.
SH_V2_DDL = """
CREATE TABLE score_history_v2 (
    stock_code TEXT(6) NOT NULL,
    score_date TEXT NOT NULL,
    momentum_score REAL,
    growth_score REAL,
    flow_score REAL,
    value_score REAL,
    total_score REAL NOT NULL,
    rank INTEGER,
    r1m REAL,
    per_cur REAL,
    PRIMARY KEY (stock_code, score_date)
) WITHOUT ROWID
"""

DP_DDL = """
CREATE TABLE daily_prices (
    stock_code TEXT(6) NOT NULL,
    trade_date TEXT NOT NULL,
    open INTEGER NOT NULL,
    high INTEGER NOT NULL,
    low INTEGER NOT NULL,
    close INTEGER NOT NULL,
    volume INTEGER NOT NULL,
    amount INTEGER,
    adj_close REAL,
    PRIMARY KEY (stock_code, trade_date)
) WITHOUT ROWID
"""


def _sh_row(code: str, rank: int, composite: float, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stock_code": code,
        "score_date": DATE,
        "momentum_score": 1.0 * rank,
        "revision_score": 0.5,
        "flow_score": 0.2,
        "valuation_score": -0.1,
        "composite_score": composite,
        "rank": rank,
        "quality_score": 0.3,
        "r1m": 0.11,
        "flow_inst_5d": 0.02,
        "val_per": 12.0,
        "op_1w_flag": "normal",
    }
    row.update(over)
    return row


def _base_rows() -> list[dict[str, object]]:
    """rank 1~6, composite 내림차순."""
    return [_sh_row(f"00000{i}", i, 3.0 - 0.5 * i) for i in range(1, 7)]


def _v2_row(code: str, rank: int, total: float, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stock_code": code,
        "score_date": DATE,
        "momentum_score": 60.0 - rank,
        "growth_score": 50.0,
        "flow_score": 40.0,
        "value_score": 30.0,
        "total_score": total,
        "rank": rank,
        "r1m": 0.07,
        "per_cur": 11.0,
    }
    row.update(over)
    return row


def _write_db(
    path: Path,
    ddl: str,
    table: str,
    rows: list[dict[str, object]],
    prices: list[tuple[str, str, int, float]] | None = None,
) -> str:
    conn = sqlite3.connect(path)
    try:
        conn.execute(ddl)
        if rows:
            cols = list(rows[0])
            sql = (f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                   f"VALUES ({', '.join('?' for _ in cols)})")
            conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        if prices is not None:
            conn.execute(DP_DDL)
            conn.executemany(
                "INSERT INTO daily_prices "
                "(stock_code, trade_date, open, high, low, close, volume, amount, adj_close) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(c, d, close, close, close, close, 1, 1, adj) for c, d, close, adj in prices],
            )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _swap_ranks(rows: list[dict[str, object]], code_a: str, code_b: str) -> None:
    by_code = {str(r["stock_code"]): r for r in rows}
    by_code[code_a]["rank"], by_code[code_b]["rank"] = (
        by_code[code_b]["rank"], by_code[code_a]["rank"])


def _causes(result: CompareResult) -> dict[str, str]:
    return {a.stock_code: a.cause for a in result.rank_alerts}


# ── (a) 완전 동일 ────────────────────────────────────────────────────────────
def test_identical_tables_give_spearman_one_and_pass(tmp_path: Path) -> None:
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", _base_rows())

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    assert res.spearman == pytest.approx(1.0)
    assert res.top_overlap[3] == 3
    assert res.top_overlap[50] == 6
    assert res.n_common == 6
    assert res.score_abs_max == 0.0
    assert res.rank_alerts == ()
    assert res.verdict == "pass"
    assert res.reasons == ()
    # 총점·rank·stock_code·score_date·*_flag 는 팩터 표에서 빠진다
    factor_cols = {f.column for f in res.factors}
    assert "composite_score" not in factor_cols
    assert "rank" not in factor_cols
    assert "op_1w_flag" not in factor_cols
    assert {"momentum_score", "r1m", "val_per"} <= factor_cols


# ── (b) 한쪽만 있는 종목 → universe ──────────────────────────────────────────
def test_left_only_ticker_is_classified_as_universe(tmp_path: Path) -> None:
    left_rows = [*_base_rows(), _sh_row("000099", 7, 0.1)]
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", left_rows)
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", _base_rows())

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    assert res.left_only == ("000099",)
    assert res.right_only == ()
    assert res.n_left == 7
    assert _causes(res)["000099"] == "universe"
    alert = next(a for a in res.rank_alerts if a.stock_code == "000099")
    assert alert.left_rank == 7
    assert alert.right_rank is None
    assert alert.rank_diff is None


# ── (c) raw 열 하나만 다름 → input:<열> ─────────────────────────────────────
def test_single_raw_column_difference_is_classified_as_input(tmp_path: Path) -> None:
    right_rows = _base_rows()
    _swap_ranks(right_rows, "000003", "000005")
    for row in right_rows:
        if row["stock_code"] == "000003":
            row["val_per"] = 20.0
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", right_rows)

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    alert = next(a for a in res.rank_alerts if a.stock_code == "000003")
    assert alert.cause == "input:val_per"
    assert alert.input_columns == ("val_per",)
    assert alert.rank_diff == -2  # 왼쪽 3 − 오른쪽 5
    val_per = next(f for f in res.factors if f.column == "val_per")
    assert val_per.n_over_tol == 1
    assert val_per.max_abs == pytest.approx(8.0)


# ── (d) raw 동일·순위만 다름 → unclassified + fail ──────────────────────────
def test_rank_only_difference_is_unclassified_and_fails(tmp_path: Path) -> None:
    right_rows = _base_rows()
    _swap_ranks(right_rows, "000002", "000004")
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", right_rows)

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    causes = _causes(res)
    assert causes["000002"] == "unclassified"
    assert causes["000004"] == "unclassified"
    assert res.n_unclassified == 2
    assert res.verdict == "fail"
    assert any("unclassified" in r for r in res.reasons)


# ── (e) NULL 불일치 집계 ────────────────────────────────────────────────────
def test_null_mismatch_is_counted_per_column(tmp_path: Path) -> None:
    right_rows = _base_rows()
    for row in right_rows:
        if row["stock_code"] == "000002":
            row["r1m"] = None
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", right_rows)

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    r1m = next(f for f in res.factors if f.column == "r1m")
    assert r1m.n_null_mismatch == 1
    assert r1m.n_both == 5
    assert r1m.n_over_tol == 0
    others = [f for f in res.factors if f.column != "r1m"]
    assert all(f.n_null_mismatch == 0 for f in others)


# ── (f) v2 표 경로 ──────────────────────────────────────────────────────────
def test_v2_table_uses_total_score(tmp_path: Path) -> None:
    rows = [_v2_row(f"00000{i}", i, 90.0 - i) for i in range(1, 7)]
    left = _write_db(tmp_path / "left.db", SH_V2_DDL, "score_history_v2", rows)
    right = _write_db(tmp_path / "right.db", SH_V2_DDL, "score_history_v2", rows)

    res = compare(left, right, DATE, table="score_history_v2", top_ns=(3,),
                  rank_alert=1, top50_min=6)

    assert res.table == "score_history_v2"
    assert res.spearman == pytest.approx(1.0)
    factor_cols = {f.column for f in res.factors}
    assert "total_score" not in factor_cols
    assert {"growth_score", "value_score", "per_cur"} <= factor_cols
    assert res.verdict == "pass"


# ── (g) CLI rc ──────────────────────────────────────────────────────────────
def test_cli_returns_zero_on_pass_and_one_on_fail(tmp_path: Path) -> None:
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    same = _write_db(tmp_path / "same.db", SH_DDL, "score_history", _base_rows())
    diff_rows = _base_rows()
    _swap_ranks(diff_rows, "000002", "000004")
    diff = _write_db(tmp_path / "diff.db", SH_DDL, "score_history", diff_rows)
    out = tmp_path / "out"

    common = ["--date", "20260923", "--left", left, "--out", str(out),
              "--rank-alert", "1", "--top50-min", "6"]
    assert main([*common, "--right", same]) == 0
    assert main([*common, "--right", diff]) == 1
    assert main([*common, "--right", str(tmp_path / "없는파일.db")]) == 2


# ── (h) md·json 산출 ────────────────────────────────────────────────────────
def test_write_report_emits_markdown_and_json(tmp_path: Path) -> None:
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right_rows = _base_rows()
    _swap_ranks(right_rows, "000002", "000004")
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", right_rows)
    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    md_path, json_path = write_report(res, tmp_path / "out")

    assert md_path.name == "2026-09-23_score_history.md"
    assert json_path.name == "2026-09-23_score_history.json"
    md = md_path.read_text(encoding="utf-8")
    assert "판정" in md and "Spearman" in md and "unclassified" in md
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "fail"
    assert payload["date"] == "2026-09-23"
    assert len(payload["rank_alerts"]) == 2


# ── (i) adj_close 비율 불일치 → v3_defect 후보 표식 ─────────────────────────
def test_adj_close_ratio_mismatch_marks_v3_defect(tmp_path: Path) -> None:
    right_rows = _base_rows()
    _swap_ranks(right_rows, "000002", "000004")
    # 왼쪽(v3)은 무상증자 전 비율로 굳어 있고, 오른쪽은 갱신된 비율(GAP-4)
    left_prices = [("000002", DATE, 1000, 1000.0), ("000004", DATE, 1000, 1000.0)]
    right_prices = [("000002", DATE, 1000, 500.0), ("000004", DATE, 1000, 1000.0)]
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows(),
                     prices=left_prices)
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", right_rows,
                      prices=right_prices)

    res = compare(left, right, DATE, top_ns=(3,), rank_alert=1, top50_min=6)

    marks = {a.stock_code: a.v3_defect for a in res.rank_alerts}
    assert marks["000002"] is True
    assert marks["000004"] is False


# ── 입력 오류 ───────────────────────────────────────────────────────────────
def test_missing_date_rows_raise_compare_error(tmp_path: Path) -> None:
    left = _write_db(tmp_path / "left.db", SH_DDL, "score_history", _base_rows())
    right = _write_db(tmp_path / "right.db", SH_DDL, "score_history", [])

    with pytest.raises(CompareError) as err:
        compare(left, right, DATE)
    assert "score_history" in str(err.value)
