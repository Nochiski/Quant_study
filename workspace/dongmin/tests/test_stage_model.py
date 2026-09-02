"""공통 확장 (5단계 준비): 단위 스케일 · 한글 날짜 · 비키 날짜 범위 격리 · 규칙 레지스트리."""
import sqlite3
from pathlib import Path

import duckdb
from stage import build, gates, model, rules, rules_dart, rules_krx, rules_wise, snapshot


def _write(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (k TEXT, amt TEXT, d TEXT, kd TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?)", [
        ("a", "12", "20160108", "2016년 01월 08일", "2026-08-30T10:00:00"),
        ("b", "-3", "29230101", "2106년 01월 08일", "2026-08-30T10:00:00"),  # 연도 범위 밖 2건
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


def _built(tmp_path: Path, **kw: object) -> build.BuildResult:
    d = tmp_path / "raw"
    d.mkdir()
    _write(d / "x.db")
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    return build.build_table(RULE, s, tmp_path / "stage", **kw)


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}')")
    return con


def test_unit_scale_multiplies_before_the_decimal_cast(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 1.0})
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con = _read(tmp_path, r)
    got = con.execute("SELECT amt_krw FROM t ORDER BY k").fetchall()
    assert got == [(12_000_000,), (-3_000_000,)]


def test_korean_date_kind_parses_yyyy_mm_dd_labels(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 1.0})
    con = _read(tmp_path, r)
    assert str(con.execute("SELECT kd FROM t WHERE k='a'").fetchone()[0]) == "2016-01-08"


def test_non_key_date_out_of_range_is_isolated_not_rejected(tmp_path: Path) -> None:
    r = _built(tmp_path, gate_thresholds={"G7": 1.0})
    assert r.n_rows == 2 and r.n_reject == 0          # 행은 남고 셀만 격리
    con = _read(tmp_path, r)
    row = con.execute("SELECT d, kd, miss_kind.d, miss_kind.kd, _src_flag FROM t WHERE k='b'"
                      ).fetchone()
    assert row == (None, None, "out_of_range", "out_of_range", "ok")   # 캐스팅 실패가 아님
    g7 = next(g for g in r.gates if g.name == "G7")
    assert g7.metrics["n_out_of_range"] == 2 and g7.metrics["n_out_of_range_rows"] == 0


def test_g7_default_threshold_fails_on_isolated_cells(tmp_path: Path) -> None:
    r = _built(tmp_path)                                # 기본 0.1% — 2/2 셀 격리는 초과
    assert r.status is build.BuildStatus.GATE_FAILED
    assert next(g for g in r.gates if g.name == "G7").status is gates.GateStatus.FAIL


def test_registry_assembles_per_source_modules() -> None:
    names = {t.name for mod in (rules_krx, rules_dart, rules_wise) for t in mod.TABLES}
    assert set(rules.RULES) == names
    assert rules_krx.TABLES[0].name == "stg_price_daily"
    assert {t.name for t in rules_dart.TABLES} >= {"stg_rcept_dt_map", "stg_fin"}
    assert rules_wise.TABLES[0].name == "stg_consensus_monthly"
    assert model.RULES_VERSION == "2.2.2"
