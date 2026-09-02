"""빌더 옵션 3건 — 5단계 에이전트 리뷰(DART 이벤트·KRX/키움)에서 나온 결함의 재현·수정 테스트.

- 정규화의 태그 제거는 옵트인(`strip_tags`): DART 서술 컬럼의 `<주1>` 각주는 태그가 아니다.
- 내용일 하한 1990 은 상장일(삼성전자 19750611)을 격리한다 → KRX 개장 1956.
- `coverage_from`(§3 temporality ⓑ) 은 `_meta.json` 에 남아야 한다.
"""
import json
import sqlite3
from pathlib import Path

import duckdb
from stage import build, gates, model, snapshot


def _write(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (k TEXT, note TEXT, lbl TEXT, ld TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?)", [
        ("a", "감자 사유 <주1> 참조", "2026/12(E)<br />(IFRS연결)", "19750611",
         "2026-08-30T10:00:00"),
        ("b", "정상", "x", "19550101", "2026-08-30T10:00:00"),      # 1956 이전 → 격리
    ])
    con.commit()
    con.close()


RULE = model.TableRule(
    name="stg_probe_opts",
    sources=(model.SourceRef("x", "t", "t"),),
    columns=(
        model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
        model.ColumnRule("note", "note", model.KIND_TEXT, normalize_text=True),
        model.ColumnRule("lbl", "lbl", model.KIND_TEXT, normalize_text=True, strip_tags=True),
        model.ColumnRule("ld", "list_date", model.KIND_DATE_YMD8),
    ),
    natural_key=("k",),
    partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="collected_at", write_mode="first_write_wins", fanout=1,
    payload_exclude=("collected_at",), lag_known=False, available=model.AVAILABLE_NONE,
    coverage_from="2026-09-01",
)


def _built(tmp_path: Path) -> build.BuildResult:
    d = tmp_path / "raw"
    d.mkdir()
    _write(d / "x.db")
    s = snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")
    r = build.build_table(RULE, s, tmp_path / "stage", gate_thresholds={"G7": 1.0})
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    return r


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}')")
    return con


def test_normalize_text_keeps_angle_bracket_footnotes_unless_strip_tags(tmp_path: Path) -> None:
    con = _read(tmp_path, _built(tmp_path))
    note, lbl = con.execute("SELECT note, lbl FROM t WHERE k='a'").fetchone()
    assert note == "감자 사유 <주1> 참조"          # 각주 보존
    assert lbl == "2026/12(E)(IFRS연결)"           # strip_tags 옵트인만 태그 제거


def test_content_date_lower_bound_is_krx_opening_year(tmp_path: Path) -> None:
    assert gates.YEAR_RANGE_CONTENT[0] == 1956
    r = _built(tmp_path)
    con = _read(tmp_path, r)
    got = con.execute("SELECT k, list_date, miss_kind.list_date FROM t ORDER BY k").fetchall()
    assert str(got[0][1]) == "1975-06-11" and got[0][2] is None     # 삼성전자 상장일 보존
    assert got[1][1] is None and got[1][2] == "out_of_range"


def test_coverage_from_is_written_to_meta(tmp_path: Path) -> None:
    r = _built(tmp_path)
    assert r.out_dir is not None
    meta = json.loads((r.out_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["coverage_from"] == "2026-09-01"
