"""T1.2 — exporter 왕복 (플랜 `2026-09-24-v3-merge.md` §5 T1.2 1).

합성 equity 판 + 합성 stage 판을 `make_stage_tree`(루트만 다르게)로 만들고 `compat.export` 가
v3 `quant.db` 표를 채우는지 본다. 단위 환산은 **리터럴**로 고정한다(억원·백만원).

합성 입력 요약 (as_of = 2026-09-23)
  price_daily   005930·000660 × 09-21·09-22·09-23 (basis 'krx') + 000270 09-23 (basis 'evening')
  flow_daily    005930·000660 × 09-21~09-23 (kiwoom) + 전 주체 NULL 셀 1개(미측정)
  matrix        fetched 09-22·09-23 두 판 × target_period 202612(+ 과거 202512)
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from compat import CompatEmptyError, CompatSchemaError, export
from compat.__main__ import main as cli_main

AS_OF = "20260923"
D21, D22, D23 = dt.date(2026, 9, 21), dt.date(2026, 9, 22), dt.date(2026, 9, 23)
SESSIONS = (D21, D22, D23)

# 단위 검산용 리터럴 — 005930 09-23 행
VALUE_KRW = 1_234_000_000          # 거래대금 원      → v3 amount 1,234 백만원
MKTCAP_KRW = 12_345_678_900_000    # 시총 원          → v3 market_cap 123,457 억원
FRGN_KRW = -3_456_000_000          # 외국인 순매수 원 → v3 foreign_investor -3,456 백만원

_FLOW_SRC = ("ind_invsr_krw", "frgnr_invsr_krw", "orgn_krw", "fnnc_invt_krw", "insrnc_krw",
             "invtrt_krw", "etc_fnnc_krw", "bank_krw", "penfnd_etc_krw", "samo_fund_krw",
             "natn_krw", "etc_corp_krw", "natfor_krw")

_FIN_LABELS = ["2021/12(IFRS연결)", "2022/12(IFRS연결)", "2023/12(IFRS연결)",
               "2024/12(IFRS연결)", "2025/12(IFRS연결)", "2026/12(E)(IFRS연결)"]


def _price_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, ticker in enumerate(("005930", "000660")):
        for j, d in enumerate(SESSIONS):
            close = 70_000 + i * 1000 + j * 100
            rows.append({
                "ticker": ticker, "date": d, "open": close - 500, "high": close + 700,
                "low": close - 900, "close": close, "volume_shr": 1_000_000 + j,
                "value_krw": VALUE_KRW + j, "mktcap_krw": MKTCAP_KRW + i,
                "basis": "krx"})
    # 저녁 잠정 T 행 — KRX 기본정보가 없어 OHL·거래대금·시총이 전부 NULL (GAP-1/D-8)
    rows.append({"ticker": "000270", "date": D23, "open": None, "high": None, "low": None,
                 "close": 55_000, "volume_shr": 12_345, "value_krw": None, "mktcap_krw": None,
                 "basis": "evening"})
    return rows


def _flow_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, ticker in enumerate(("005930", "000660")):
        for j, d in enumerate(SESSIONS):
            row: dict[str, object] = {"date": d, "ticker": ticker, "src": "kiwoom"}
            for k, col in enumerate(_FLOW_SRC):
                row[col] = FRGN_KRW + (i + j + k) * 1_000_000
            rows.append(row)
    # 미측정 셀 — 전 주체 NULL. v3 는 수집한 행만 가지므로 내보내지 않는다.
    empty: dict[str, object] = {"date": D22, "ticker": "000270", "src": None}
    empty.update(dict.fromkeys(_FLOW_SRC))
    rows.append(empty)
    return rows


def _matrix_rows() -> list[dict[str, object]]:
    """(fetched_date, target_period, acc_cd, lookback) 좌표. op 121500 · ni 122710."""
    rows: list[dict[str, object]] = []
    plan = [
        # fetched, base, target_period, target_label, op_current, ni_current
        (D22, D21, "202612", "2026/12", 1000.0, 2000.0),
        (D23, D22, "202612", "2026/12", 1100.0, 2100.0),
        (D23, D22, "202512", "2025/12", 99.0, 88.0),      # 과거 기 — 선택되면 안 된다
    ]
    for fetched, base, period, label, op_cur, ni_cur in plan:
        for acc, cur in (("121500", op_cur), ("122710", ni_cur)):
            for idx, (lb, delta) in enumerate(
                    (("current", 0.0), ("1w", -100.0), ("1m", -200.0), ("3m", -300.0),
                     ("1y", -400.0)), start=1):
                rows.append({
                    "ticker": "005930", "fetched_date": fetched, "target_period": period,
                    "acc_cd": acc, "lookback_idx": str(idx), "lookback": lb,
                    "target_label": label, "base_date": base, "value": cur + delta})
    return rows


def _annual_rows() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for period, kind in (("202512", "A"), ("202612", "E")):
        out.append({
            "ticker": "005930", "fetched_date": D23,
            "period_label": f"{period[:4]}.{period[4:]}({kind})", "period": period,
            "period_kind": kind, "fs_basis": "IFRS연결", "revenue": 3_000_000.0,
            "yoy_pct": 10.5, "op": 500_000.0, "ni": 400_000.0, "eps": 6563.0, "bps": 60_000.0,
            "per": 18.27, "pbr": 1.3, "roe_pct": 11.1, "ev_ebitda": 7.53})
    return out


def _fin_wise_rows() -> list[dict[str, object]]:
    """cF3002 손익계산서 4계정 + cF4002 투자지표 8행(EPS 중복 포함)."""
    spec: list[tuple[str, str, str | None, list[float]]] = [
        ("cF3002", "200000", None, [2_796_047.99, 3_022_313.6, 2_589_354.94, 3_008_709.03,
                                    3_336_059.38, 7_397_267.52]),        # 매출액
        ("cF3002", "200810", None, [1_000_000.0] * 6),                    # 매출총이익
        ("cF3002", "201370", None, [516_339.0] * 6),                      # 영업이익
        ("cF3002", "203170", None, [399_075.0] * 6),                      # 당기순이익
        ("cF4002", "312000", None, [5777.37, 5000.0, 4000.0, 5500.0, 6563.57, 48_338.64]),
        ("cF4002", "312000", "382100", [1.0] * 6),                        # 중첩 EPS — 쓰지 않는다
        ("cF4002", "314000", None, [60_000.0] * 6),                       # BPS
        ("cF4002", "382100", None, [13.55, 14.0, 15.0, 16.0, 18.27, 5.38]),   # PER
        ("cF4002", "382500", None, [1.1] * 6),                            # PBR
        ("cF4002", "331000", None, [4.89] * 6),                           # EV/EBITDA
        ("cF4002", "431800", None, [1.84] * 6),                           # 현금배당수익률
        ("cF4002", "701250", None, [5_969_782_550.0] * 6),                # 발행주식수
    ]
    rows: list[dict[str, object]] = []
    for ep, accode, p_accode, vals in spec:
        row: dict[str, object] = {"ticker": "005930", "fetched_date": D23, "ep": ep,
                                  "accode": accode, "p_accode": p_accode,
                                  "fs_basis": "IFRS연결"}
        for i, label in enumerate(_FIN_LABELS, start=1):
            row[f"period_label_{i}"] = label
        for i, v in enumerate(vals, start=1):
            row[f"val_{i}"] = v
        rows.append(row)
    return rows


@pytest.fixture
def roots(tmp_path: Path, make_stage_tree) -> tuple[Path, Path]:
    """(equity_root, stage_root) — 같은 판 규약, 루트만 다르다."""
    eq, st = tmp_path / "eq", tmp_path / "st"
    equity = {
        "price_daily": _price_rows(),
        "price_adj_daily": [{"ticker": r["ticker"], "date": r["date"],
                             "adj_close": float(r["close"]) * 0.9}    # type: ignore[arg-type]
                            for r in _price_rows()],
        "universe_daily": [
            {"date": d, "ticker": t, "status": "listed", "market": m, "sec_type": s}
            for d in SESSIONS
            for t, m, s in (("005930", "KOSPI", "common"), ("000660", "KOSPI", "common"),
                            ("069500", "KOSPI", "etf"))],
        "security": [
            {"ticker": "005930", "name_current": "삼성전자", "list_date": dt.date(1975, 6, 11),
             "delist_date": None},
            {"ticker": "000660", "name_current": "SK하이닉스", "list_date": dt.date(1996, 12, 26),
             "delist_date": None},
            {"ticker": "069500", "name_current": "KODEX 200", "list_date": dt.date(2002, 10, 14),
             "delist_date": None},
            {"ticker": "900000", "name_current": "폐지종목", "list_date": dt.date(2010, 1, 4),
             "delist_date": dt.date(2026, 5, 1)},
        ],
        "sector_snapshot": [
            {"ticker": "005930", "snapshot_date": dt.date(2026, 9, 19), "wics_l1_nm": "IT"},
            {"ticker": "000660", "snapshot_date": dt.date(2026, 9, 19), "wics_l1_nm": "IT"},
        ],
        "flow_daily": _flow_rows(),
    }
    for table, rows in equity.items():
        make_stage_tree(eq, table, rows)
    stage = {
        "stg_consensus_matrix": _matrix_rows(),
        "stg_consensus_annual": _annual_rows(),
        "stg_fin_wise": _fin_wise_rows(),
    }
    for table, rows in stage.items():
        make_stage_tree(st, table, rows)
    return eq / "stage", st / "stage"


def _run(roots: tuple[Path, Path], target: Path, **kw):
    return export(equity_root=roots[0], stage_root=roots[1], date=AS_OF, basis="morning",
                  target=target, full=True, **kw)


def _rows(target: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(str(target))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── (a) 표별 upsert 결과·단위 ────────────────────────────────────────────────
def test_daily_prices_columns_rows_and_units(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices"])
    assert res.tables["daily_prices"].n_rows == 6          # krx 2종목 × 3세션
    got = _rows(target, "SELECT stock_code, trade_date, open, high, low, close, volume, "
                        "amount, adj_close FROM daily_prices "
                        "WHERE stock_code='005930' AND trade_date='2026-09-23'")
    assert got == [("005930", "2026-09-23", 69_700, 70_900, 69_300, 70_200, 1_000_002,
                    1234, pytest.approx(63_180.0))]


def test_evening_rows_are_excluded_and_counted(roots, tmp_path: Path) -> None:
    """GAP-1/D-8 — v3 daily_prices 는 open/high/low NOT NULL 이라 저녁 T 행을 못 받는다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices"])
    assert res.n_evening_rows_skipped == 1
    assert _rows(target, "SELECT count(*) FROM daily_prices WHERE stock_code='000270'") == [(0,)]
    meta = _rows(target, "SELECT n_evening_rows_skipped FROM _compat_meta")
    assert meta == [(1,)]


def test_stocks_snapshot_and_market_cap_in_eok(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"])
    assert res.tables["stocks"].n_rows == 2                # ETF 069500 제외
    got = _rows(target, "SELECT stock_code, stock_name, market, sector, market_cap, "
                        "listed_date, is_active, delisted_date FROM stocks ORDER BY stock_code")
    assert got == [
        ("000660", "SK하이닉스", "KOSPI", "IT", 123_457, "1996-12-26", 1, None),
        ("005930", "삼성전자", "KOSPI", "IT", 123_457, "1975-06-11", 1, None),
    ]


def test_investor_flows_in_million_krw(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["investor_detail_flows"])
    assert res.tables["investor_detail_flows"].n_rows == 6     # 미측정 셀 1개는 빠진다
    got = _rows(target, "SELECT individual, foreign_investor, institution_total, "
                        "etc_corporation FROM investor_detail_flows "
                        "WHERE stock_code='005930' AND trade_date='2026-09-21'")
    # ind = -3,456,000,000원 → -3,456 백만원. 주체마다 +1,000,000원(=+1 백만원)씩 어긋난다.
    assert got == [(-3456, -3455, -3454, -3445)]


def test_consensus_revision_daily_and_compare(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_revision_daily", "consensus_revision_compare"])
    assert _rows(target, "SELECT stock_code, base_date, target_period, op, ni, collected_date "
                         "FROM consensus_revision_daily") == [
        ("005930", "2026-09-22", "2026/12", 1100.0, 2100.0, "2026-09-23")]
    assert _rows(target, "SELECT target_period, op_1w, op_1m, op_3m, op_1y "
                         "FROM consensus_revision_compare") == [
        ("2026/12", 1000.0, 900.0, 800.0, 700.0)]


def test_consensus_annual_period_and_data_type(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_annual"])
    assert _rows(target, "SELECT period, period_type, data_type, op, ni, per "
                         "FROM consensus_annual ORDER BY period") == [
        ("2025/12", "annual", "actual", 500_000.0, 400_000.0, 18.27),
        ("2026/12", "annual", "estimate", 500_000.0, 400_000.0, 18.27)]


def test_financial_summary_scoring_window(roots, tmp_path: Path) -> None:
    """v3 quality·valuation 창(`period_type='annual' AND data_type IS NULL`)이 실제로 찬다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["financial_summary"])
    assert res.tables["financial_summary"].n_rows == 6          # 기간 슬롯 6개
    got = _rows(target, "SELECT period, revenue, op, ni, eps, bps, per, pbr, ev_ebitda, "
                        "dividend_yield, shares, gross_profit, accounting_standard "
                        "FROM financial_summary WHERE period_type='annual' "
                        "AND data_type IS NULL ORDER BY period DESC LIMIT 1")
    assert got == [("2025/12", 3_336_059, 516_339, 399_075, 6564, 60_000, 18.27, 1.1, 4.89,
                    1.84, 5_969_782_550, 1_000_000, "IFRS연결")]
    # (E) 슬롯은 data_type='estimate' 로 갈라져 scoring 창에 들지 않는다.
    assert _rows(target, "SELECT count(*) FROM financial_summary "
                         "WHERE data_type='estimate'") == [(1,)]


def test_financial_summary_null_columns_stay_null(roots, tmp_path: Path) -> None:
    """WISE cF3002/cF4002 에 재료가 없는 열은 0 이 아니라 NULL 이다(결측은 결측)."""
    from compat.mappings import BY_TABLE
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    nulls = BY_TABLE["financial_summary"].null_columns
    assert set(nulls) == {"roe", "roa", "debt_ratio", "fcf", "capex", "op_margin", "ni_margin",
                          "yoy", "total_assets"}
    cols = ", ".join(nulls)
    for row in _rows(target, f"SELECT {cols} FROM financial_summary"):
        assert all(v is None for v in row)


# ── (b) 멱등 ─────────────────────────────────────────────────────────────────
def test_second_run_is_idempotent(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    first = _run(roots, target)
    second = _run(roots, target)
    assert {t: r.n_rows for t, r in first.tables.items()} == \
           {t: r.n_rows for t, r in second.tables.items()}
    for table in first.tables:
        assert _rows(target, f"SELECT count(*) FROM {table}")[0][0] == first.tables[table].n_rows


# ── (c) 0행 ──────────────────────────────────────────────────────────────────
def test_empty_result_raises(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    with pytest.raises(CompatEmptyError, match="daily_prices"):
        export(equity_root=roots[0], stage_root=roots[1], date="20200101", basis="morning",
               target=target, tables=["daily_prices"], full=True)


# ── (d) _compat_meta ─────────────────────────────────────────────────────────
def test_compat_meta_records_builds_and_window(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = export(equity_root=roots[0], stage_root=roots[1], date=AS_OF, basis="evening",
                 target=target, tables=["daily_prices", "consensus_revision_daily"], full=True,
                 window_days=730, consensus_asof="20260922")
    row = _rows(target, 'SELECT date, basis, equity_builds, stage_builds, tables, "window", '
                        "consensus_asof FROM _compat_meta")[0]
    assert row[0] == "2026-09-23"
    assert row[1] == "evening"
    assert "price_daily" in row[2] and "stg_consensus_matrix" in row[3]
    assert '"daily_prices"' in row[4] and '"n_rows": 6' in row[4]
    assert '"days": 730' in row[5] and '"from_date": "2024-09-23"' in row[5]
    assert row[6] == "2026-09-22"
    assert res.equity_builds["price_daily"] == "b_stage_0001"


# ── (e) --consensus-asof ─────────────────────────────────────────────────────
def test_consensus_asof_shifts_matrix_snapshot(roots, tmp_path: Path) -> None:
    """G-M2: v3 09-23 점수의 리비전 입력이 09-22 자료였으므로 as-of 를 따로 줄 수 있어야 한다."""
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_revision_daily"], consensus_asof="20260922")
    assert _rows(target, "SELECT base_date, op, ni, collected_date "
                         "FROM consensus_revision_daily") == [
        ("2026-09-21", 1000.0, 2000.0, "2026-09-22")]


# ── (f) 스키마 불일치 ────────────────────────────────────────────────────────
def test_existing_schema_mismatch_raises(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    con = sqlite3.connect(str(target))
    con.execute("CREATE TABLE stocks (stock_code TEXT PRIMARY KEY, whatever TEXT)")
    con.commit()
    con.close()
    with pytest.raises(CompatSchemaError, match="stocks"):
        _run(roots, target, tables=["stocks"])


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_exports_and_prints_summary(roots, tmp_path: Path, capsys) -> None:
    target = tmp_path / "quant.db"
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(target), "--tables", "daily_prices,stocks", "--full"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "daily_prices=6" in out and "stocks=2" in out


def test_cli_returns_2_on_error(roots, tmp_path: Path) -> None:
    rc = cli_main(["export", "--date", "20200101", "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(tmp_path / "quant.db"), "--tables", "daily_prices", "--full"])
    assert rc == 2


def test_cli_rejects_unmapped_score_tables(roots, tmp_path: Path) -> None:
    """score_history 는 T2.7 이 채운다 — 지금 요청하면 조용히 건너뛰지 않고 rc 2 다."""
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(tmp_path / "quant.db"), "--tables", "score_history"])
    assert rc == 2
