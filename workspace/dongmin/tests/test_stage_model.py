"""5단계 공통 확장: 단위 스케일 · 날짜 종류 · 비키 날짜 격리 · UNION 패딩 · 레지스트리."""
import json
import sqlite3
from pathlib import Path

import duckdb
from stage import (
    build,
    gates,
    model,
    rules,
    rules_dart,
    rules_dart_events,
    rules_kis,
    rules_kiwoom,
    rules_krx,
    rules_wise,
    snapshot,
)


def _write(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (k TEXT, amt TEXT, d TEXT, kd TEXT, dd TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?)", [
        ("a", "12", "20160108", "2016년 01월 08일", "2016.01.08", "2026-08-30T10:00:00"),
        ("b", "-3", "29230101", "2106년 01월 08일", "2923.01.01", "2026-08-30T10:00:00"),
    ])
    con.commit()
    con.close()


RULE = model.TableRule(
    name="stg_probe",
    sources=(model.SourceRef("x", "t", "t"),),
    columns=(
        model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
        model.ColumnRule("amt", "amt_krw", model.KIND_NUMERIC, 20, 0, unit_scale=1_000_000),
        model.ColumnRule("d", "d", model.KIND_DATE_YMD8),
        model.ColumnRule("kd", "kd", model.KIND_DATE_KOREAN),
        model.ColumnRule("dd", "dd", model.KIND_DATE_DOT),
    ),
    natural_key=("k",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=model.AVAILABLE_NONE,
)


AMT_FIXTURE = [{"key": {"k": "a"}, "column": "amt_krw", "expect": "12000000",
                "measured_sql": "SELECT amt FROM t WHERE k='a'", "measured_at": "2026-09-02"}]


def _built(tmp_path: Path, fixtures: list[dict[str, object]] | None = AMT_FIXTURE,
           **kw: object) -> build.BuildResult:
    d = tmp_path / "raw"
    d.mkdir()
    _write(d / "x.db")
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    fp = None
    if fixtures is not None:
        fp = tmp_path / "fx.json"
        fp.write_text(json.dumps(fixtures), encoding="utf-8")
    return build.build_table(RULE, s, tmp_path / "stage", fixtures_path=fp, **kw)


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}')")
    return con


def test_unit_scale_multiplies_before_the_decimal_cast(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 2.0})
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con = _read(tmp_path, r)
    got = con.execute("SELECT amt_krw FROM t ORDER BY k").fetchall()
    assert got == [(12_000_000,), (-3_000_000,)]


def test_korean_date_kind_parses_yyyy_mm_dd_labels(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 2.0})
    con = _read(tmp_path, r)
    assert str(con.execute("SELECT kd FROM t WHERE k='a'").fetchone()[0]) == "2016-01-08"


def test_dot_date_kind_parses_yyyy_dot_mm_dot_dd(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 2.0})
    con = _read(tmp_path, r)
    assert str(con.execute("SELECT dd FROM t WHERE k='a'").fetchone()[0]) == "2016-01-08"


def test_non_key_date_out_of_range_is_isolated_not_rejected(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 2.0})
    assert r.n_rows == 2 and r.n_reject == 0          # 행은 남고 셀만 격리
    con = _read(tmp_path, r)
    row = con.execute("SELECT d, kd, dd, miss_kind.d, miss_kind.kd, miss_kind.dd, _src_flag "
                      "FROM t WHERE k='b'").fetchone()
    assert row == (None, None, None, "out_of_range", "out_of_range", "out_of_range", "ok")
    g7 = next(g for g in r.gates if g.name == "G7")
    assert g7.metrics["n_out_of_range"] == 3 and g7.metrics["n_out_of_range_rows"] == 0


def test_g4_fails_when_a_unit_scale_column_has_no_fixture(tmp_path: Path) -> None:
    r = _built(tmp_path, fixtures=None, gate_thresholds={"G7": 2.0})   # §9: unit≠1 = 픽스처 강제
    g4 = next(g for g in r.gates if g.name == "G4")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert g4.status is gates.GateStatus.FAIL and "amt_krw" in g4.detail
    assert g4.metrics["unit_scale_uncovered"] == ["amt_krw"]


def test_union_pads_columns_that_only_some_sources_have(tmp_path: Path) -> None:
    """elestock(+row_hash·dup_seq·req_corp_code) ∪ elestock_v1 — 비규칙 컬럼은 NULL 패딩."""
    d = tmp_path / "raw"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE t_new (k TEXT, val TEXT, row_hash TEXT, collected_at TEXT)")
    con.execute("CREATE TABLE t_v1 (k TEXT, val TEXT, collected_at TEXT)")
    con.execute("INSERT INTO t_new VALUES ('a', '1', 'h1', '2026-08-30T10:00:00')")
    con.execute("INSERT INTO t_v1 VALUES ('b', '2', '2026-08-30T10:00:00')")
    con.commit()
    con.close()
    rule = model.TableRule(
        name="stg_union_probe",
        sources=(model.SourceRef("x", "t_new", "new"), model.SourceRef("x", "t_v1", "v1")),
        columns=(model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
                 model.ColumnRule("val", "val", model.KIND_NUMERIC, 3, 0)),  # "v"=hive 충돌
        natural_key=("k",), partition_class="whole", partition_expr=None, partition_src=None,
        observed_src="collected_at", write_mode="append_only", fanout=1,
        payload_exclude=("collected_at", "row_hash"), lag_known=False,
        available=model.AVAILABLE_NONE)
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    r = build.build_table(rule, s, tmp_path / "stage")
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con2 = _read(tmp_path, r)
    assert con2.execute("SELECT k, val, _src FROM t ORDER BY k").fetchall() == [
        ("a", 1, "new"), ("b", 2, "v1")]


def test_g7_default_threshold_fails_on_isolated_cells(tmp_path: Path) -> None:
    r = _built(tmp_path)                                # 기본 0.1% — 2/2 셀 격리는 초과
    assert r.status is build.BuildStatus.GATE_FAILED
    assert next(g for g in r.gates if g.name == "G7").status is gates.GateStatus.FAIL


def test_registry_assembles_per_source_modules() -> None:
    names = {t.name for mod in (rules_krx, rules_kiwoom, rules_kis, rules_dart, rules_dart_events, rules_wise)
             for t in mod.TABLES}
    assert set(rules.RULES) == names
    assert rules_krx.TABLES[0].name == "stg_price_daily"
    assert {t.name for t in rules_krx.TABLES} >= {"stg_etf_price_daily", "stg_index_daily",
                                                  "stg_listing_daily", "stg_ingest_krx"}
    assert {t.name for t in rules_kiwoom.TABLES} == {
        "stg_flow_daily_kiwoom", "stg_short_daily_kiwoom", "stg_foreign_daily",
        "stg_lending_daily", "stg_master_daily", "stg_shards_kiwoom"}
    assert {t.name for t in rules_dart.TABLES} >= {"stg_rcept_dt_map", "stg_fin"}
    assert {t.name for t in rules_kis.TABLES} >= {"stg_flow_split_daily", "stg_credit_daily"}
    assert rules_wise.TABLES[0].name == "stg_consensus_monthly"
    assert model.RULES_VERSION == "2.2.3"
