"""S3 `stg_consensus_monthly` — ws_raw cF5001+cF5002 blob 언네스트 (DESIGN §1 예외 c·e, §4, G8)."""
import json
import sqlite3
import zlib
from pathlib import Path

import duckdb
import pytest
from stage import build, gates, parsers, rules, snapshot

L1, L2, L3 = "2026/06/30", "2026/07/31", "2026/08/31"


def _blob(charts: dict[str, dict]) -> bytes:
    """실물 인코딩 재현: zlib( json({chart1: json(inner), chart2: json(inner)}) )."""
    outer = {k: json.dumps(v, ensure_ascii=False) for k, v in charts.items()}
    return zlib.compress(json.dumps(outer, ensure_ascii=False).encode("utf-8"))


def c5001(name: str, unit: str, cats: list[str], vals: list, close: list, tgt: list) -> dict:
    return {"select_item_unit": unit, "select_item_name": name, "categories": cats,
            "close_price": close, "target_price": tgt, "select_item": vals}


def c5002(name: str, unit: str, cats: list[str], avg: list, mm: list) -> dict:
    return {"item_unit": unit, "item_name": name, "categories": cats, "avg": avg, "min_max": mm}


SAMSUNG_5001 = _blob({
    "chart1": c5001("EPS", "원", [L1, L2, L3], [44699.61, 47928.74, 48338.64],
                    [334000.0, 262500.0, 260000.0], [465208.0, 493542.0, 493958.0]),
    "chart2": c5001("매출액", "억원", [L1, L2, L3], [7062391.00, 7378930.54, 7397267.52],
                    [334000.0, 262500.0, 260000.0], [465208.0, 493542.0, 493958.0]),
})
SAMSUNG_5002 = _blob({   # chart1: 말미 라벨 중복(같은 값) · chart2: 빈 배열 → 0행
    "chart1": c5002("EPS", "원", [L2, L3, L3], [47928.74, 48338.64, 48338.64],
                    [[40489.0, 77719.53], [43509.24, 77719.53], [43509.24, 77719.53]]),
    "chart2": c5002("매출액", "억원", [], [], []),
})
UNCOVERED_5001 = _blob({   # 무커버 프로브: 컨센서스·목표주가 None, 종가는 실값
    "chart1": c5001("EPS", "원", [L1, L2, L3], [None, None, None], [5010.0, 4680.0, 5180.0],
                    [None, None, None]),
    "chart2": c5001("매출액", "억원", [L1, L2, L3], [None, None, None], [5010.0, 4680.0, 5180.0],
                    [None, None, None]),
})
MISMATCH_5002 = _blob({    # 5001 과 다른 값 → G8 실패
    "chart1": c5002("EPS", "원", [L3], [1.0], [[0.0, 2.0]]),
    "chart2": c5002("매출액", "억원", [], [], []),
})
UNKNOWN_5001 = _blob({     # 알 수 없는 항목명 → metric=parse_failed 로 보존, G8 카운트
    "chart1": c5001("PER", "배", [L3], [10.5], [100.0], [None]),
    "chart2": c5001("매출액", "억원", [L3], [1.0], [100.0], [None]),
})

WS_COLS = ["cmp_cd", "ep", "pkey", "fetched_date", "body", "sha256", "bytes", "fetched_at"]


def _row(cmp: str, ep: str, body: bytes, pkey: str = "202612", fd: str = "2026-09-01",
         at: str = "2026-09-01T03:20:50") -> tuple:
    return (cmp, ep, pkey, fd, body, f"sha-{cmp}-{ep}-{pkey}-{fd}", len(body), at)


def _write_wise(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ws_raw (cmp_cd TEXT, ep TEXT, pkey TEXT, fetched_date TEXT,"
                " body BLOB, sha256 TEXT, bytes INTEGER, fetched_at TEXT)")
    con.executemany(f"INSERT INTO ws_raw VALUES ({','.join('?' * len(WS_COLS))})", rows)
    con.commit()
    con.close()


BASE_ROWS = [
    _row("005930", "cF5001", SAMSUNG_5001),
    _row("005930", "cF5002", SAMSUNG_5002),
    _row("000020", "cF5001", UNCOVERED_5001, at="2026-09-01T15:30:00"),   # KST 09-02 00:30
]


@pytest.fixture
def snap(tmp_path: Path) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir()
    _write_wise(d / "wisereport.db", BASE_ROWS)
    return snapshot.make_snapshot({"wise": d / "wisereport.db"}, tmp_path / "snapshots",
                                  snapshot_id="snap_wise")


def _build(snap: snapshot.Snapshot, tmp_path: Path, **kw: object) -> build.BuildResult:
    return build.build_table(rules.RULES["stg_consensus_monthly"], snap, tmp_path / "stage", **kw)


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "year=*" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


# ── parser ─────────────────────────────────────────────────────────────────────
def _raw(cmp: str, ep: str, body: bytes, pkey: str = "202612") -> parsers.RawBlob:
    return parsers.RawBlob(cmp_cd=cmp, ep=ep, pkey=pkey, fetched_date="2026-09-01", body=body,
                           fetched_at="2026-09-01T03:20:50")


def test_parser_merges_5001_and_5002_by_label_not_index() -> None:
    res = parsers.parse_consensus_monthly([_raw("005930", "cF5001", SAMSUNG_5001),
                                           _raw("005930", "cF5002", SAMSUNG_5002)])
    rows = {(r["metric"], r["obs_label"]): r for r in res.rows}
    assert len(rows) == 6                       # eps·revenue × L1·L2·L3 (좌표 합집합)
    eps_l3 = rows[("eps", L3)]
    assert eps_l3["consensus"] == "48338.64" and eps_l3["consensus_min"] == "43509.24"
    assert eps_l3["consensus_max"] == "77719.53" and eps_l3["target_price_krw"] == "493958.0"
    assert eps_l3["close_price_krw"] == "260000.0" and eps_l3["unit"] == "원"
    assert eps_l3["in_5001"] == "true" and eps_l3["in_5002"] == "true"
    eps_l1 = rows[("eps", L1)]                  # 5002 축에 없는 라벨 — 5001 만
    assert eps_l1["consensus"] == "44699.61" and eps_l1["consensus_min"] is None
    assert eps_l1["in_5002"] == "false"
    rev_l3 = rows[("revenue", L3)]
    assert rev_l3["unit"] == "억원" and rev_l3["consensus"] == "7397267.52"
    assert rev_l3["consensus_min"] is None      # 5002 chart2 빈 배열 → 0행
    assert res.metrics["n_blobs"] == {"cF5001": 1, "cF5002": 1}
    assert res.metrics["n_dup_labels_folded"] == 1
    assert res.metrics["n_value_mismatch"] == 0
    assert res.metrics["n_parse_failed"] == 0


def test_parser_keeps_uncovered_probe_rows_with_null_consensus() -> None:
    res = parsers.parse_consensus_monthly([_raw("000020", "cF5001", UNCOVERED_5001)])
    assert len(res.rows) == 6
    r = next(x for x in res.rows if x["metric"] == "eps" and x["obs_label"] == L2)
    assert r["consensus"] is None and r["target_price_krw"] is None
    assert r["close_price_krw"] == "4680.0"


def test_parser_counts_value_mismatch_and_unknown_metric() -> None:
    res = parsers.parse_consensus_monthly([_raw("005930", "cF5001", SAMSUNG_5001),
                                           _raw("005930", "cF5002", MISMATCH_5002),
                                           _raw("000660", "cF5001", UNKNOWN_5001)])
    assert res.metrics["n_value_mismatch"] == 1
    assert res.metrics["n_metric_unknown"] == 1
    bad = next(x for x in res.rows if x["cmp_cd"] == "000660" and x["unit"] == "배")
    assert bad["metric"] == "parse_failed"
    eps_l3 = next(x for x in res.rows if x["cmp_cd"] == "005930" and x["metric"] == "eps"
                  and x["obs_label"] == L3)
    assert eps_l3["consensus"] == "48338.64"   # 불일치 시 5001 값 유지


def test_parser_reports_undecodable_blob_as_parse_failed() -> None:
    res = parsers.parse_consensus_monthly([_raw("005930", "cF5001", b"not zlib at all")])
    assert res.rows == [] and res.metrics["n_parse_failed"] == 1


# ── rules ──────────────────────────────────────────────────────────────────────
def test_rules_consensus_monthly_declares_blob_source_and_keys() -> None:
    rule = rules.RULES["stg_consensus_monthly"]
    assert rule.blob_source is not None
    assert rule.blob_source.table == "ws_raw" and rule.blob_source.eps == ("cF5001", "cF5002")
    assert rule.natural_key == ("ticker", "fetched_date", "target_period", "metric", "obs_label")
    assert rule.partition_expr == "substr(fetched_date, 1, 4)"
    assert rule.available.kind == "column" and rule.available.basis == "measured"
    assert rule.observed_src == "fetched_at"
    assert rule.write_mode == "append_only"


# ── build ──────────────────────────────────────────────────────────────────────
def test_build_unnests_blobs_into_coordinate_rows(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    r = _build(snap, tmp_path)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert r.n_rows == 12 and r.n_dedup == 0 and r.n_reject == 0
    con = _read(tmp_path, r)
    row = con.execute("SELECT consensus, consensus_min, consensus_max, close_price_krw,"
                      " target_price_krw, unit, obs_date, available_date, available_basis,"
                      " observed_date, in_5001, in_5002, year FROM t WHERE ticker='005930'"
                      f" AND metric='eps' AND obs_label='{L3}'").fetchone()
    assert row is not None
    assert str(row[0]) == "48338.6400" and str(row[1]) == "43509.2400"
    assert str(row[3]) == "260000.00" and str(row[4]) == "493958.00" and row[5] == "원"
    assert str(row[6]) == "2026-08-31"
    assert (str(row[7]), row[8]) == ("2026-09-01", "measured")
    assert str(row[9]) == "2026-09-01"          # fetched_at 03:20Z → KST 12:20 같은 날
    assert (row[10], row[11], row[12]) == (True, True, 2026)
    unc = con.execute("SELECT consensus, close_price_krw, observed_date FROM t WHERE"
                      f" ticker='000020' AND metric='revenue' AND obs_label='{L2}'").fetchone()
    assert unc is not None and unc[0] is None and str(unc[1]) == "4680.00"
    assert str(unc[2]) == "2026-09-02"          # 15:30Z → KST 다음 날


def test_build_g8_parse_equation_passes_and_records_blob_accounting(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build(snap, tmp_path)
    g = _gate(r, "G8")
    assert g.status is gates.GateStatus.PASS
    assert g.metrics["n_blobs"] == {"cF5001": 2, "cF5002": 1}
    assert g.metrics["n_rows_emitted"] == 12 and g.metrics["n_dup_labels_folded"] == 1
    assert g.metrics["n_value_mismatch"] == 0 and g.metrics["n_parse_failed"] == 0
    assert _gate(r, "G6").status is gates.GateStatus.PASS
    assert _gate(r, "G9").status is gates.GateStatus.SKIP


def test_build_g8_fails_on_value_mismatch_between_endpoints(tmp_path: Path) -> None:
    d = tmp_path / "raw2"
    d.mkdir()
    _write_wise(d / "wisereport.db", [_row("005930", "cF5001", SAMSUNG_5001),
                                      _row("005930", "cF5002", MISMATCH_5002)])
    s = snapshot.make_snapshot({"wise": d / "wisereport.db"}, tmp_path / "snapshots",
                               snapshot_id="s2")
    r = _build(s, tmp_path)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "G8").metrics["n_value_mismatch"] == 1


def test_ledger_file_map_matches_survey_targets() -> None:
    """stage 의 db alias→파일 매핑은 survey targets.DBS 와 같아야 한다 (SoT 드리프트 감지)."""
    import os

    from targets import DBS

    assert {k: os.path.basename(v) for k, v in DBS.items()} == rules.LEDGER_FILES
