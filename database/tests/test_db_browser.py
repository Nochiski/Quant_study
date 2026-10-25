"""scripts/db_browser.py — 조회 화면용 뷰 구성(화면 서버는 띄우지 않는다)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import duckdb

_SPEC = importlib.util.spec_from_file_location(
    "db_browser", Path(__file__).resolve().parents[1] / "scripts" / "db_browser.py")
assert _SPEC and _SPEC.loader
db_browser = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(db_browser)


def _table(root: Path, layer: str, name: str, builds: dict[str, list[tuple[str, int]]],
           current: str) -> None:
    """`data/<layer>/<name>/v=<bid>/year=<y>/part0.parquet` + MANIFEST (current_build)."""
    tdir = root / "data" / layer / name
    recs = []
    for bid, parts in builds.items():
        plist = []
        for year, val in parts:
            d = tdir / f"v={bid}" / f"year={year}"
            d.mkdir(parents=True)
            duckdb.sql(f"COPY (SELECT {val} AS x) TO '{d / 'part0.parquet'}' (FORMAT PARQUET)")
            plist.append({"path": f"v={bid}/year={year}", "n_rows": 1})
        recs.append({"build_id": bid, "n_rows": len(parts), "rules_version": "t1",
                     "partitions": plist, "max_available_date": "2026-09-26"})
    rej = tdir / f"v={current}" / "_reject" / "reject_reason=x"
    rej.mkdir(parents=True)
    duckdb.sql(f"COPY (SELECT 999 AS x) TO '{rej / 'part0.parquet'}' (FORMAT PARQUET)")
    (tdir / "MANIFEST.json").write_text(json.dumps(
        {"table": name, "current_build": current, "keep": 3, "builds": recs}), encoding="utf-8")


def test_views_point_at_current_build_and_skip_reject(tmp_path: Path) -> None:
    _table(tmp_path, "stage", "stg_a",
           {"b1": [("2025", 1)], "b2": [("2025", 2), ("2026", 3)]}, "b2")
    _table(tmp_path, "equity", "eq_a", {"m1": [("2026", 7)]}, "m1")
    (tmp_path / "data" / "equity" / "_failed").mkdir()          # 밑줄 폴더는 표가 아니다
    snap = tmp_path / "data" / "snapshots" / "snap_20260926T000000Z"
    snap.mkdir(parents=True)
    with sqlite3.connect(snap / "krx.db") as c:
        c.execute("CREATE TABLE ingest_log (bas_dd TEXT)")
        c.execute("INSERT INTO ingest_log VALUES ('20260923')")

    con = duckdb.connect(":memory:")
    rows = db_browser.build_views(con, tmp_path, None)

    # 옛 판(b1)과 격리(_reject)는 뷰에 들어가지 않는다
    assert sorted(con.execute("SELECT x FROM stage.stg_a").fetchall()) == [(2,), (3,)]
    assert con.execute("SELECT x FROM equity.eq_a").fetchall() == [(7,)]
    assert con.execute("SELECT bas_dd FROM raw_krx.ingest_log").fetchall() == [("20260923",)]
    cat = con.execute("SELECT layer, name, build FROM main.catalog ORDER BY 1, 2").fetchall()
    assert cat == [("equity", "eq_a", "m1"), ("stage", "stg_a", "b2"),
                   ("원장", "raw_krx", "snap_20260926T000000Z")]
    assert len(rows) == 3


def test_current_files_none_without_manifest_or_partitions(tmp_path: Path) -> None:
    assert db_browser.current_files(tmp_path) is None
    (tmp_path / "MANIFEST.json").write_text(json.dumps(
        {"current_build": "b1", "builds": [{"build_id": "b1", "partitions": []}]}),
        encoding="utf-8")
    assert db_browser.current_files(tmp_path) is None
