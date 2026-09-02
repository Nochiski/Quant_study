"""WISE 잔여 11테이블 — c1050001(T2Y·T2Q·T4)·cF3002/4002·c1010001 HTML 언네스트 + v3 4종 등.

blob 구조는 2026-09-02 서버 ws_raw 실측(§10). 픽스처는 실물 인코딩(zlib+JSON, HTML)을 재현한다.
"""
import json
import sqlite3
import zlib
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, model, parsers, rules, rules_wise, snapshot

WS_COLS = ["cmp_cd", "ep", "pkey", "fetched_date", "body", "sha256", "bytes", "fetched_at"]
AT = "2026-09-01T21:05:00"          # UTC → KST 09-02


def _z(obj: object) -> bytes:
    return zlib.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _t2(yymm: str, sales: str = "3,022,313.6", eps: str = "8,057", main: str = "IFRS연결") -> dict:
    return {"YYMM": yymm, "SALES": sales, "YOY": "8.09", "OP": "433,766.3", "NP": "547,300.2",
            "EPS": eps, "BPS": "50,817", "PER": "6.86", "PBR": "1.09", "ROE": "17.07",
            "EV": "3.23", "MAIN": main, "TOT_ROW": 7}


def _t4(acc: str, nm: str, vals: list, seq: int = 1, dt: str = "20260901") -> dict:
    row = {"SEQ": seq, "ACC_CD": acc, "ACC_NM": nm, "DT": dt, "YYMM": None}
    row.update({f"VAL{i}": v for i, v in enumerate(vals, 1)})
    return row


YYMM8 = ["2021/12<br />(IFRS연결)", "2022/12<br />(IFRS연결)", "2023/12<br />(IFRS연결)",
         "2024/12<br />(IFRS연결)", "2025/12<br />(IFRS연결)", "2026/12(E)<br />(IFRS연결)",
         "전년대비<br />(YoY)", "전년대비<br />(YoY)"]


def _fin_row(accode: str, nm: str, p: str | None = None, data5: object = 3336059.38) -> dict:
    return {"ACKIND": "A", "ACCODE": accode, "ACC_NM": nm, "LVL": 1, "GRP_TYP": 2, "UNT_TYP": 2,
            "P_ACCODE": p, "DATA1": 2796047.99, "DATA2": 3022313.6, "DATA3": 2589354.9400000004,
            "DATA4": 3008709.03, "DATA5": data5, "DATA6": 7397267.5217391318, "YYOY": 10.88,
            "YEYOY": 121.74, "DATAQ1": 745663.17, "DATAQ4": 1338734.44, "DATAQ5": 1714994.7,
            "DATAQ2": 860617.47, "DATAQ6": None, "QOQ": 28.11, "YOY": 130.0,
            "QOQ_COMMENT": "이전분기 : 1,338,734.44\r최근분기 : 1,714,994.70", "YOY_COMMENT": None,
            "QOQ_E": None, "YOY_E": None, "QOQ_E_COMMENT": None, "YOY_E_COMMENT": None,
            "POINT_CNT": 1}


def _matrix_body() -> bytes:
    return _z({"JsonData": [
        _t4("610100", "투자의견(점수)", [4.04545, 4.04167, 4.04167, 4.04167, 4.0]),
        _t4("121000", "매출액(억원)", [None, 7378931.0, None, 6846861.0, 3317192.0], 2)],
        "YYMM": "2026/12"})


CALL_COLS = ["ts", "cmp_cd", "ep", "pkey", "status", "bytes", "ms"]


def _fin_blob(rows: list[dict], yymm: list[str] = YYMM8) -> bytes:
    return _z({"YYMM": yymm, "DATA": rows, "FIN": "IFRS연결", "FRQ": "연간"})


def _html(cells: list[str] | str, base: str = "2026.09.01") -> bytes:
    if isinstance(cells, str):
        body = f'<tr><td width="353" colspan="5" class="center noline-bottom">{cells}</td></tr>'
    else:
        tds = "".join(f'<td class="noline-bottom line-right center">{c}</td>' for c in cells)
        body = f"<tr>{tds}</tr>"
    html = (
        '<!DOCTYPE HTML><html><body><div class="header-table"><dl><dd><h5><span>투자의견</span>'
        f' 컨센서스</h5></dd><dd class="header-table-cell unit"><p>[기준:{base}]</p></dd></dl>'
        '</div><table class="gHead all-width" id="cTB15" summary="투자의견">'
        '<caption class="blind">투자의견컨센서스</caption>'
        '<tr><td rowspan="2"><span id="pointerVal">4.05</span></td><th scope="col">투자의견</th>'
        '<th scope="col">목표주가<span class="span-sub">(원)</span></th><th>EPS</th><th>PER</th>'
        f'<th>추정기관수</th></tr>{body}</table>'
        '<table id="cTB24"><tr><td>LS</td><td>26/08/31</td></tr></table></body></html>')
    return zlib.compress(html.encode("utf-8"))


ALERT = b"<script>alert('x');location.replace('../company/c1010001.aspx');</script>"  # 142B 실물
SAMSUNG_CELLS = ["<b>4.05</b>", "487,045", "48,339", "5.40", "22"]


def _row(cmp: str, ep: str, pkey: str, body: bytes, fd: str = "2026-09-02", at: str = AT) -> tuple:
    return (cmp, ep, pkey, fd, body, f"sha-{cmp}-{ep}-{pkey}-{fd}", len(body), at)


def _blob(cmp: str, ep: str, pkey: str, body: bytes, fd: str = "2026-09-02") -> parsers.RawBlob:
    return parsers.RawBlob(cmp, ep, pkey, fd, body, AT)


def _write_wise(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ws_raw (cmp_cd TEXT, ep TEXT, pkey TEXT, fetched_date TEXT,"
                " body BLOB, sha256 TEXT, bytes INTEGER, fetched_at TEXT)")
    con.executemany(f"INSERT INTO ws_raw VALUES ({','.join('?' * len(WS_COLS))})", rows)
    con.commit()
    con.close()


def _snap(tmp_path: Path, rows: list[tuple], extra_tables: dict[str, tuple[list[str], list[tuple]]]
          | None = None) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    p = d / "wisereport.db"
    _write_wise(p, rows)
    if extra_tables:
        con = sqlite3.connect(p)
        for t, (cols, trs) in extra_tables.items():
            con.execute(f"CREATE TABLE {t} ({', '.join(c + ' TEXT' for c in cols)})")
            con.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(cols))})", trs)
        con.commit()
        con.close()
    return snapshot.make_snapshot({"wise": p}, tmp_path / "snapshots", snapshot_id="s")


def _build(name: str, snap: snapshot.Snapshot, tmp_path: Path, **kw: object) -> build.BuildResult:
    r = build.build_table(rules.RULES[name], snap, tmp_path / "stage", **kw)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    return r


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    root = tmp_path / "stage" / r.table / f"v={r.build_id}"
    glob = str(root / "year=*" / "*.parquet") if (root / "_meta.json").exists() is False or any(
        root.glob("year=*")) else str(root / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


# ── 파서 ─────────────────────────────────────────────────────────────────────────────────────
def test_periodic_parser_filters_pkey_and_derives_period_kind() -> None:
    blobs = [
        _blob("005930", "c1050001_data", "T2Y",
              _z({"JsonData": [_t2("2022.12(A)"), _t2("2026.12(E)")]})),
        _blob("005930", "c1050001_data", "T2Q", _z({"JsonData": [_t2("2025.09(A)")]})),  # 다른 pkey
        _blob("000020", "c1050001_data", "T2Y", _z({"JsonData": []})),                     # 빈 blob
        _blob("000020", "c1050001_data", "", _z({"JsonData": [{"YYMM": "202512", "CK": 0}]})),
    ]
    res = parsers.parse_consensus_annual(blobs)
    assert [(r["period_label"], r["period"], r["period_kind"]) for r in res.rows] == [
        ("2022.12(A)", "202212", "A"), ("2026.12(E)", "202612", "E")]
    assert res.rows[0]["revenue"] == "3,022,313.6" and res.rows[0]["fs_basis"] == "IFRS연결"
    assert res.metrics["n_blobs"] == 2 and res.metrics["n_skipped_pkey"] == 2
    assert res.metrics["n_empty"] == 1 and res.metrics["n_rows_emitted"] == 2
    assert res.metrics["n_parse_failed"] == 0
    q = parsers.parse_consensus_quarterly(blobs)
    assert [r["period_label"] for r in q.rows] == ["2025.09(A)"]


def test_matrix_parser_unnests_val1_to_5_with_measured_lookback_labels() -> None:
    body = _matrix_body()
    res = parsers.parse_consensus_matrix([_blob("005930", "c1050001_data", "T4:202612", body),
                                          _blob("005930", "c1050001_data", "T2Y", b"x"),
                                          _blob("000020", "c1050001_data", "T4:202712",
                                                _z({"JsonData": [], "YYMM": "2027/12"}))])
    assert res.metrics["n_rows_emitted"] == 10 and res.metrics["n_skipped_pkey"] == 1
    assert res.metrics["n_empty"] == 1 and res.metrics["n_parse_failed"] == 0
    r0 = res.rows[0]
    assert (r0["target_period"], r0["target_label"], r0["acc_cd"], r0["base_date"]) == (
        "202612", "2026/12", "121000", "20260901")           # 좌표 정렬: acc_cd 오름차순
    assert [(r["lookback_idx"], r["lookback"]) for r in res.rows[:5]] == [
        ("1", "current"), ("2", "1w"), ("3", "1m"), ("4", "3m"), ("5", "1y")]   # 실측 09-02 대조
    assert [r["value"] for r in res.rows[:5]] == [None, "7378931.0", None, "6846861.0", "3317192.0"]


def test_fin_wise_parser_keeps_one_row_per_data_entry_with_period_labels() -> None:
    body = _fin_blob([_fin_row("200000", "매출액(수익)"), _fin_row("200010", "*내수", "200000")])
    null_data = _z({"YYMM": [], "DATA": None, "FIN": "IFRS별도", "FRQ": "연간"})   # 082640 실측
    res = parsers.parse_fin_wise([_blob("005930", "cF3002", "Y", body),
                                  _blob("005930", "cF4002", "Y", _fin_blob([])),
                                  _blob("082640", "cF3002", "Y", null_data),
                                  _blob("000020", "cF3002", "Y", b"\x78\x9cbroken")])
    assert res.metrics["n_rows_emitted"] == 2 and res.metrics["n_empty"] == 2
    assert res.metrics["n_parse_failed"] == 1
    assert res.metrics["n_blobs"] == {"cF3002": 3, "cF4002": 1}
    r = res.rows[0]
    assert (r["ep"], r["seq"], r["accode"], r["p_accode"]) == ("cF3002", "0", "200000", None)
    assert r["period_label_6"] == "2026/12(E)<br />(IFRS연결)"
    assert r["period_label_1"].startswith("2021/12")
    assert r["val_3"] == "2589354.9400000004" and r["val_q5"] == "1714994.7" and r["val_q6"] is None
    assert r["qoq_comment"].startswith("이전분기")
    assert r["fs_basis"] == "IFRS연결" and r["freq"] == "연간"
    assert res.rows[1]["seq"] == "1" and res.rows[1]["p_accode"] == "200000"


def by_ticker(res: parsers.ParseResult) -> dict[str, dict[str, str | None]]:
    return {str(r["cmp_cd"]): r for r in res.rows}


def test_analyst_summary_parser_handles_three_html_shapes_and_alert_body() -> None:
    blobs = [_blob("005930", "c1010001", "", _html(SAMSUNG_CELLS)),
             _blob("000250", "c1010001", "", _html("최근3개월 이내에 제시된 의견이 없습니다")),
             _blob("000020", "c1010001", "", _html(["&nbsp;", "", "999", "12.34", ""])),
             _blob("000030", "c1010001", "", _html(["3.80", "1,000", "-120", "N/A", "3"])),
             _blob("082640", "c1010001", "", ALERT)]
    res = parsers.parse_analyst_summary(blobs)
    assert res.metrics["n_rows_emitted"] == 4 and res.metrics["n_no_data"] == 1
    assert res.metrics["n_na_cells"] == 1                       # PER 'N/A'(EPS 음수) → blank
    assert by_ticker(res)["000030"]["per"] == ""
    assert res.metrics["n_parse_failed"] == 0 and res.metrics["n_no_opinion"] == 1
    by = {r["cmp_cd"]: r for r in res.rows}
    s = by["005930"]
    assert (s["base_date"], s["opinion_score"], s["target_price_krw"], s["eps_krw"], s["per"],
            s["analyst_count"], s["no_opinion_note"]) == (
        "2026.09.01", "4.05", "487,045", "48,339", "5.40", "22", None)
    n = by["000250"]
    assert n["opinion_score"] is None and n["analyst_count"] is None
    assert n["no_opinion_note"] == "최근3개월 이내에 제시된 의견이 없습니다"
    e = by["000020"]
    assert (e["opinion_score"], e["target_price_krw"], e["eps_krw"], e["analyst_count"]) == (
        "", "", "999", "")


# ── 선언 ─────────────────────────────────────────────────────────────────────────────────────
def test_rules_wise_declares_all_twelve_tables_per_design_section_4() -> None:
    names = {t.name for t in rules_wise.TABLES}
    assert names == {"stg_consensus_monthly", "stg_consensus_annual", "stg_consensus_quarterly",
                     "stg_consensus_matrix", "stg_analyst_summary", "stg_fin_wise",
                     "stg_v3_revision_daily", "stg_v3_analyst_opinions", "stg_v3_consensus_annual",
                     "stg_v3_revision_compare", "stg_wise_coverage", "stg_calls_wise"}
    assert names <= set(rules.RULES)
    R = rules.RULES
    for n, ex in {"stg_consensus_annual": "substr(fetched_date, 1, 4)",
                  "stg_fin_wise": "substr(fetched_date, 1, 4)",
                  "stg_v3_revision_daily": "substr(base_date, 1, 4)",
                  "stg_v3_analyst_opinions": "substr(snapshot_date, 1, 4)",
                  "stg_v3_consensus_annual": "substr(sync_date, 1, 4)",
                  "stg_v3_revision_compare": "substr(sync_date, 1, 4)"}.items():
        assert R[n].partition_class == "date_axis" and R[n].partition_expr == ex, n
    for n in ("stg_wise_coverage", "stg_calls_wise"):
        assert R[n].partition_class == "whole" and R[n].available is model.AVAILABLE_NONE
    assert R["stg_consensus_matrix"].natural_key == ("ticker", "fetched_date", "target_period",
                                                     "acc_cd", "lookback_idx")
    assert R["stg_fin_wise"].natural_key == ("ticker", "fetched_date", "ep", "seq")
    assert R["stg_fin_wise"].column("val_1").decimal_type == "DECIMAL(38,6)"
    bs = R["stg_consensus_matrix"].blob_source
    assert bs is not None and bs.eps == ("c1050001_data",) and bs.parser == "parse_consensus_matrix"
    assert R["stg_v3_revision_daily"].available == model.AvailableRule(
        "column", column="collected_date", basis="measured", fallback_column="date")
    assert all(R[n].write_mode == "first_write_wins" for n in
               ("stg_v3_revision_daily", "stg_v3_analyst_opinions", "stg_v3_consensus_annual",
                "stg_v3_revision_compare"))
    assert R["stg_wise_coverage"].column("status_current").kind == model.KIND_TEXT
    assert R["stg_wise_coverage"].observed_src == "checked_at"
    assert R["stg_calls_wise"].observed_src == "ts"


# ── 빌드 (blob 5) ─────────────────────────────────────────────────────────────────────────────
def test_build_consensus_annual_and_quarterly(tmp_path: Path) -> None:
    rows = [_row("005930", "c1050001_data", "T2Y",
                 _z({"JsonData": [_t2("2022.12(A)"), _t2("2026.12(E)")]})),
            _row("005930", "c1050001_data", "T2Q",
                 _z({"JsonData": [_t2("2025.09(A)", sales="860,617.5")]})),
            _row("005930", "c1050001_data", "", _z({"JsonData": [{"YYMM": "202512", "CK": 0}]}))]
    snap = _snap(tmp_path, rows)
    a = _build("stg_consensus_annual", snap, tmp_path)
    assert a.n_rows == 2
    con = _read(tmp_path, a)
    got = con.execute("SELECT period, period_kind, revenue, eps, available_date, available_basis,"
                      " observed_date FROM t ORDER BY period").fetchall()
    assert str(got[0][2]) == "3022313.6000" and got[0][3] == 8057 and got[0][:2] == ("202212", "A")
    assert (str(got[0][4]), got[0][5], str(got[0][6])) == ("2026-09-02", "measured", "2026-09-02")
    g8 = next(g for g in a.gates if g.name == "G8")
    assert g8.status is gates.GateStatus.PASS and g8.metrics["n_skipped_pkey"] == 2
    q = _build("stg_consensus_quarterly", snap, tmp_path)
    assert q.n_rows == 1


def test_build_consensus_matrix_rows_and_null_values(tmp_path: Path) -> None:
    body = _matrix_body()
    snap = _snap(tmp_path, [_row("005930", "c1050001_data", "T4:202612", body)])
    r = _build("stg_consensus_matrix", snap, tmp_path)
    assert r.n_rows == 10
    con = _read(tmp_path, r)
    got = con.execute("SELECT lookback, value, miss_kind.value FROM t WHERE acc_cd='121000' "
                      "ORDER BY lookback_idx").fetchall()
    assert got[0] == ("current", None, "ledger_null") and str(got[1][1]) == "7378931.000000"
    assert con.execute("SELECT count(DISTINCT base_date), min(target_period) FROM t"
                       ).fetchone() == (1, "202612")


def test_build_fin_wise_rounds_float_artifacts_into_decimal_38_6(tmp_path: Path) -> None:
    rows = [_row("005930", "cF3002", "Y", _fin_blob([_fin_row("200000", "매출액(수익)"),
                                                     _fin_row("200010", "*내수", "200000")])),
            _row("005930", "cF4002", "Y", _fin_blob([_fin_row("312000", "EPS"),
                                                     _fin_row("312000", "EPS＜당기＞", "382100")]))]
    r = _build("stg_fin_wise", _snap(tmp_path, rows), tmp_path)
    assert r.n_rows == 4                                # cF4002 의 ACCODE 중복은 seq 키로 보존
    con = _read(tmp_path, r)
    got = con.execute("SELECT ep, seq, accode, p_accode, val_3, val_q5, period_label_6 FROM t "
                      "ORDER BY ep, seq").fetchall()
    assert got[0][:4] == ("cF3002", 0, "200000", None) and str(got[0][4]) == "2589354.940000"
    assert str(got[0][5]) == "1714994.700000" and got[0][6] == "2026/12(E)(IFRS연결)"   # 태그 제거
    assert got[3][:4] == ("cF4002", 1, "312000", "382100")


def test_build_analyst_summary_from_html(tmp_path: Path) -> None:
    rows = [_row("005930", "c1010001", "", _html(SAMSUNG_CELLS)),
            _row("000250", "c1010001", "", _html("최근3개월 이내에 제시된 의견이 없습니다")),
            _row("082640", "c1010001", "", ALERT)]
    r = _build("stg_analyst_summary", _snap(tmp_path, rows), tmp_path)
    assert r.n_rows == 2
    con = _read(tmp_path, r)
    s = con.execute("SELECT base_date, opinion_score, target_price_krw, eps_krw, per,"
                    " analyst_count FROM t WHERE ticker='005930'").fetchone()
    assert (str(s[0]), str(s[1]), s[2], s[3], str(s[4]), s[5]) == (
        "2026-09-01", "4.05", 487045, 48339, "5.40", 22)
    n = con.execute("SELECT analyst_count, miss_kind.analyst_count, no_opinion_note FROM t "
                    "WHERE ticker='000250'").fetchone()
    assert n == (None, "ledger_null", "최근3개월 이내에 제시된 의견이 없습니다")
    assert next(g for g in r.gates if g.name == "G8").metrics["n_no_data"] == 1


# ── 빌드 (평문 6) ────────────────────────────────────────────────────────────────────────────
V3D_COLS = ["stock_code", "base_date", "target_period", "opinion", "revenue", "op", "ni", "eps",
            "per", "bps", "pbr", "roe", "collected_date", "copied_at"]
V3O_COLS = ["stock_code", "snapshot_date", "opinion_score", "target_price", "estimated_eps",
            "estimated_per", "analyst_count", "copied_at"]
V3A_COLS = ["sync_date", "stock_code", "period", "period_type", "data_type", "revenue", "yoy", "op",
            "ni", "eps", "bps", "per", "pbr", "roe", "ev_ebitda", "accounting_standard", "row_hash",
            "copied_at"]
_H = ("1w", "1m", "3m", "1y")
V3C_COLS = ["sync_date", "stock_code", "target_period"] + [
    f"{m}_{h}" for m in ("opinion", "revenue", "op", "ni", "eps", "per", "bps", "pbr", "roe")
    for h in _H] + ["row_hash", "copied_at"]
COPIED = "2026-09-02 06:10:00"


def test_build_v3_revision_daily_falls_back_to_base_date_when_collected_null(
        tmp_path: Path) -> None:
    trs = [("005930", "2026-08-29", "2026/12", "4.0", "7394435.0", "551580.5", "409185.7", "48135",
            "5.4", "86052", "3.02", "17.5", "2026-09-01", COPIED),
           ("005930", "2026-04-03", "2026/12", None, "6846861.0", "500000.0", "380000.0", "43098",
            "6.0", "80000", "3.3", "16.0", None, COPIED)]       # collected_date NULL 1,390행 부류
    snap = _snap(tmp_path, [], {"v3_consensus_revision_daily": (V3D_COLS, trs)})
    r = _build("stg_v3_revision_daily", snap, tmp_path)
    assert r.n_rows == 2
    con = _read(tmp_path, r)
    got = con.execute("SELECT date, available_date, available_basis, coverage_degraded, opinion,"
                      " miss_kind.opinion FROM t ORDER BY date").fetchall()
    assert [str(g[0]) for g in got] == ["2026-04-03", "2026-08-29"]
    assert (str(got[0][1]), got[0][2], got[0][3], got[0][4], got[0][5]) == (
        "2026-04-03", "default", True, None, "ledger_null")
    assert (str(got[1][1]), got[1][2], got[1][3], str(got[1][4])) == (
        "2026-09-01", "measured", False, "4.00")
    g6 = next(g for g in r.gates if g.name == "G6")
    assert g6.status is gates.GateStatus.SKIP        # first_write_wins → G6 skip(write_mode)


def test_build_v3_opinions_annual_compare(tmp_path: Path) -> None:
    opin = [("005930", "2026-08-29", "4.05", "487045", "48339", "5.4", "22", COPIED),
            ("000020", "2026-08-29", None, None, None, None, None, COPIED)]
    annual = [("2026-09-01", "005930", "2026/12", "annual", "estimate", "7394435.0", "121.74",
               "551580.5", "409185.7", "48135", "86052", "5.4", "3.02", "17.5", "3.2",
               "IFRS연결", "h1", COPIED),
              ("2026-09-02", "005930", "2026/12", "annual", "estimate", "7384675.0", "121.5",
               "551580.5", "409185.7", "48139", "86052", "5.4", "3.02", "17.5", "3.2",
               "IFRS연결", "h2", COPIED)]
    comp = [("2026-09-02", "005930", "2026/12") + (None,) * 4 + ("7384675.0", "7378931.0",
            "6846861.0", "3317192.0") + ("1.0",) * 8 + ("48139", "47929", "43098", "5396")
            + ("5.4",) * 4 + ("86052",) * 4 + ("3.0",) * 4 + ("17.5",) * 4 + ("h3", COPIED)]
    snap = _snap(tmp_path, [], {"v3_analyst_opinions": (V3O_COLS, opin),
                                "v3_consensus_annual": (V3A_COLS, annual),
                                "v3_consensus_revision_compare": (V3C_COLS, comp)})
    o = _build("stg_v3_analyst_opinions", snap, tmp_path)
    con = _read(tmp_path, o)
    assert con.execute("SELECT count(*), sum(analyst_count), max(available_basis) FROM t"
                       ).fetchone() == (2, 22, "measured")
    a = _build("stg_v3_consensus_annual", snap, tmp_path)
    con = _read(tmp_path, a)
    assert con.execute("SELECT count(*), count(DISTINCT sync_date), min(eps) FROM t"
                       ).fetchone() == (2, 2, 48135)
    c = _build("stg_v3_revision_compare", snap, tmp_path)
    con = _read(tmp_path, c)
    got = con.execute("SELECT revenue_1w, eps_1y, opinion_1w, miss_kind.opinion_1w, available_date"
                      " FROM t").fetchone()
    assert (str(got[0]), got[1], got[2], got[3], str(got[4])) == (
        "7384675.0", 5396, None, "ledger_null", "2026-09-02")


def test_build_coverage_and_calls_as_whole_tables(tmp_path: Path) -> None:
    cov = [("005930", "ok", "2026-09-02T05:12:00"), ("0004Y0", "skipped", "2026-09-01T20:00:00")]
    calls = [("2026-09-01T21:00:01", "005930", "cF5001", "202612", "ok", "1234", "45"),
             ("2026-09-01T21:00:02", "005930", "c1010001", "", "ok", "18114", "120")]
    snap = _snap(tmp_path, [], {"ws_coverage": (["cmp_cd", "status", "checked_at"], cov),
                                "ws_call_log": (CALL_COLS, calls)})
    c = _build("stg_wise_coverage", snap, tmp_path)
    con = _read(tmp_path, c)
    got = con.execute("SELECT ticker, status_current, checked_date_current, observed_date,"
                      " available_date FROM t ORDER BY ticker").fetchall()
    assert (got[0][0], got[0][1], str(got[0][2]), str(got[0][3]), got[0][4]) == (
        "0004Y0", "skipped", "2026-09-02", "2026-09-02", None)      # UTC 20:00 → KST 09-02
    k = _build("stg_calls_wise", snap, tmp_path)
    con = _read(tmp_path, k)
    assert con.execute("SELECT count(*), sum(bytes), max(ms) FROM t").fetchone() == (2, 19348, 120)


def test_g4_requires_no_unit_scale_fixture_for_wise_tables() -> None:
    assert all(c.unit_scale is None for t in rules_wise.TABLES for c in t.columns)  # 단위=데이터


@pytest.mark.parametrize("name", ["stg_consensus_annual", "stg_consensus_quarterly",
                                  "stg_consensus_matrix", "stg_analyst_summary", "stg_fin_wise"])
def test_blob_tables_declare_measured_fetched_date_availability(name: str) -> None:
    r = rules.RULES[name]
    assert r.available == model.AvailableRule("column", column="fetched_date", basis="measured")
    assert r.observed_src == "fetched_at" and r.blob_source is not None and r.key_unique
