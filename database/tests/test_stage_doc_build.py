"""stg_doc_* 4테이블 빌드 — 프리패스 캐시(JSONL) → FileSource → stage parquet (DOC_DESIGN §3·§4)."""
import json
import sqlite3
from pathlib import Path

import duckdb
import pytest
from test_stage_doc_prepass import _setup

from stage import build, doc_prepass, gates, rules, snapshot

DISC_COLS = ["row_hash", "corp_cls", "corp_code", "corp_name", "flr_nm", "rcept_dt", "rcept_no",
             "report_nm", "rm", "stock_code", "req_bgn_de", "req_end_de", "req_page_no", "dup_seq",
             "collected_at"]


def _write_disclosure(db: Path) -> None:
    con = sqlite3.connect(db)
    con.execute(f"CREATE TABLE dart_disclosure ({', '.join(c + ' TEXT' for c in DISC_COLS)})")
    con.executemany(f"INSERT INTO dart_disclosure VALUES ({','.join('?' * len(DISC_COLS))})", [
        ("h1", "Y", "00138057", "써니전자", "써니전자", "20200327", "20200327001141",
         "사업보고서 (2019.12)", "", "004770", "20200101", "20201231", "0001", "0",
         "2026-08-30T10:00:00"),
        ("h2", "K", "00151605", "동화기업", "동화기업", "20240311", "20240311901285",
         "주식분할결정", "", "025900", "20240101", "20241231", "0001", "0",
         "2026-08-30T10:00:00"),
    ])
    con.commit()
    con.close()


def _prepared(tmp_path: Path) -> tuple[snapshot.Snapshot, Path]:
    docs, db = _setup(tmp_path)          # doc_store + ZIP 2개 (ok 1 · html 1)
    _write_disclosure(db)
    snap = snapshot.make_snapshot({"dart": db}, tmp_path / "snapshots", snapshot_id="snap_doc")
    stage_root = tmp_path / "stage"
    r0 = build.build_table(rules.RULES["stg_rcept_dt_map"], snap, stage_root)
    assert r0.ok
    s = doc_prepass.run(snap.dir / "dart.db", docs, stage_root / "_tmp" / "doc", "snap_doc",
                        workers=1)
    assert s.status == "ok"
    return snap, stage_root


def _read(stage_root: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(stage_root / r.table / f"v={r.build_id}" / "year=*" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def test_rules_doc_declarations() -> None:
    for name in ("stg_doc_meta", "stg_doc_section", "stg_doc_correction", "stg_doc_parse_log"):
        rule = rules.RULES[name]
        assert rule.file_source is not None and rule.blob_source is None
        assert rule.partition_class == "receipt_axis"
        assert rule.partition_expr == "substr(rcept_no, 1, 4)"
        assert rule.observed_src == "fetched_at" and rule.write_mode == "append_only"
        assert rule.available.kind == "lookup" and rule.available.table == "stg_rcept_dt_map"
        assert rule.fanout == 1 and rule.key_unique
        assert set(rule.file_source.columns) == {c.src for c in rule.columns} | {"fetched_at"}


def test_build_meta_from_prepass_cache_passes_gates_and_derives_dates(tmp_path: Path) -> None:
    snap, stage_root = _prepared(tmp_path)
    r = build.build_table(rules.RULES["stg_doc_meta"], snap, stage_root)
    assert r.ok, [g.as_dict() for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert r.n_rows == 3 and r.n_src == 3 and r.n_reject == 0
    con = _read(stage_root, r)
    row = con.execute("SELECT gen, parse_mode, available_date, available_basis, observed_date, "
                      "n_xbrl_groups, has_correction_page, period_from, n_form_tables FROM t "
                      "WHERE rcept_no='20200327001141' AND member_role='main'").fetchone()
    assert row is not None
    assert row[0] == "dart3_hdr" and row[1] == "ok" and str(row[2]) == "2020-03-27"
    assert row[3] == "derived" and str(row[4]) == "2026-09-01"
    assert row[5] == 3 and row[6] is True and str(row[7]) == "2019-01-01" and row[8] == 2
    g8 = next(g for g in r.gates if g.name == "G8")
    assert g8.status is gates.GateStatus.PASS and g8.metrics["n_rows_emitted"] == 3
    meta = json.loads((stage_root / "stg_doc_meta" / f"v={r.build_id}" / "year=2020"
                       / "_meta.json").read_text(encoding="utf-8"))
    assert len(str(meta["doc_input_hash"])) == 16                   # D5 — 입력 집합 해시


def test_build_section_correction_and_parse_log(tmp_path: Path) -> None:
    snap, stage_root = _prepared(tmp_path)
    results = {name: build.build_table(rules.RULES[name], snap, stage_root)
               for name in ("stg_doc_section", "stg_doc_correction", "stg_doc_parse_log")}
    for name, n in (("stg_doc_section", 8), ("stg_doc_correction", 1), ("stg_doc_parse_log", 3)):
        r = results[name]
        assert r.ok and r.n_rows == n, (name, [g.as_dict() for g in r.gates])
    con = _read(stage_root, results["stg_doc_parse_log"])
    modes = dict(con.execute("SELECT parse_mode, count(*) FROM t GROUP BY 1").fetchall())
    assert modes == {"ok": 2, "html": 1}                            # 멤버 단위
    con = _read(stage_root, results["stg_doc_section"])
    span = con.execute("SELECT min(elem_start), max(elem_end) FROM t "
                       "WHERE rcept_no='20200327001141'").fetchone()
    assert span is not None and 0 < span[0] < span[1]
    con = _read(stage_root, results["stg_doc_correction"])
    corr = con.execute("SELECT filed_date, n_items FROM t").fetchone()
    assert corr is not None and str(corr[0]) == "2020-03-30" and corr[1] == 1


def test_build_without_prepass_cache_raises_with_instructions(tmp_path: Path) -> None:
    docs, db = _setup(tmp_path)
    _write_disclosure(db)
    snap = snapshot.make_snapshot({"dart": db}, tmp_path / "snapshots", snapshot_id="snap_x")
    stage_root = tmp_path / "stage"
    assert build.build_table(rules.RULES["stg_rcept_dt_map"], snap, stage_root).ok
    with pytest.raises(FileNotFoundError, match="doc_prepass"):
        build.build_table(rules.RULES["stg_doc_meta"], snap, stage_root)
    cache = stage_root / "_tmp" / "doc" / "snap_x"
    cache.mkdir(parents=True)
    (cache / "summary.json").write_text(json.dumps({"status": "gate_failed", "detail": "D0 x"}))
    with pytest.raises(RuntimeError, match="gate_failed"):
        build.build_table(rules.RULES["stg_doc_meta"], snap, stage_root)
