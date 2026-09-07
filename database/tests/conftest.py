"""database 테스트 공통 설정 — survey/·src/ 모듈을 import 경로에 올린다."""
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("survey", "src"):
    _p = os.path.join(_HERE, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── equity T0 ────────────────────────────────────────────────────────────────
# 가짜 stage 트리 생성기. equity 테스트는 서버 데이터 없이 이것만으로 돈다.

_DUCKDB_TYPES = ((bool, "BOOLEAN"), (int, "BIGINT"), (float, "DOUBLE"),
                 (dt.date, "DATE"), (str, "VARCHAR"))


@dataclass(frozen=True)
class StageTree:
    """`make_stage_tree` 가 만든 가짜 stage 테이블 1개."""

    stage_root: Path
    table: str
    build_id: str
    table_root: Path
    partitions: tuple[str, ...]


# 절단본 전용 게이트 하한 — 서버 확정값을 45행짜리 표본에 그대로 들이대면 표본 크기 때문에
# 빌드가 폐기된다. 값이 아니라 **표본 크기**가 이유이므로 시드(=서버 확정 기록)는 건드리지 않고
# 테스트에서만 낮춘다. 서버: 겹침 5,789행 일치율 0.9508 → 하한 0.93. 절단본: 45행 0.8222 → 0.80.
SLICE_BASELINE_OVERRIDE: dict[str, dict[str, object]] = {
    "consensus_daily": {"v3_wise_match_min": 0.80},
}


def apply_slice_override(merged: dict) -> dict:
    """시드 병합 결과에 절단본 하한을 덮어쓴다(제자리 수정하지 않는다)."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in merged.items()}
    for table, consts in SLICE_BASELINE_OVERRIDE.items():
        if table in out and isinstance(out[table], dict):
            out[table].update(consts)
    return out


def _duckdb_type(values: list[object]) -> str:
    for v in values:
        if v is None:
            continue
        for py, name in _DUCKDB_TYPES:
            if isinstance(v, py):
                return name
    return "VARCHAR"


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def _json_write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _make_stage_tree(tmp_path: Path, table: str, rows: list[dict[str, object]],
                     partition_class: str = "whole", build_id: str = "b_stage_0001",
                     gate_status: str = "pass", lag_known: bool = True) -> StageTree:
    """tmp_path 아래에 stage 규약 그대로의 테이블 1개를 만든다.

    `<tmp_path>/stage/<table>/v=<build_id>/` + 파티션 `_meta.json` + `MANIFEST.json`
    (stage `manifest.commit` 을 그대로 써서 실물과 같은 포인터 규약을 갖는다).
    partition_class: whole = `part0.parquet` 하나, date_axis = `year(date)`,
    receipt_axis = `substr(rcept_no,1,4)` 하이브 디렉토리.
    """
    import duckdb  # sys.path 조작 뒤에 import 해야 한다
    from stage import manifest

    if not rows:
        raise ValueError(f"make_stage_tree needs at least one row: table={table}")
    cols = list(rows[0])
    for i, r in enumerate(rows):
        if list(r) != cols:
            raise ValueError(f"row {i} has different columns: table={table} "
                             f"expected={cols} got={list(r)}")
    types = {c: _duckdb_type([r[c] for r in rows]) for c in cols}
    stage_root = Path(tmp_path) / "stage"
    table_root = stage_root / table
    vdir = table_root / f"v={build_id}"
    vdir.mkdir(parents=True, exist_ok=True)
    values = ", ".join("(" + ", ".join(f"CAST({_lit(r[c])} AS {types[c]})" for c in cols) + ")"
                       for r in rows)
    col_list = ", ".join(f'"{c}"' for c in cols)
    con = duckdb.connect()
    try:
        con.execute(f"CREATE TEMP TABLE _rows AS SELECT * FROM (VALUES {values}) AS t({col_list})")
        if partition_class == "whole":
            con.execute(f"COPY (SELECT * FROM _rows) TO '{vdir / 'part0.parquet'}' "
                        "(FORMAT PARQUET)")
            parts = [("", vdir, len(rows))]
        else:
            key = "year(date)" if partition_class == "date_axis" else "substr(rcept_no, 1, 4)"
            con.execute(f"CREATE TEMP VIEW _parted AS SELECT *, {key} AS year FROM _rows")
            con.execute(f"COPY (SELECT * FROM _parted) TO '{vdir}' (FORMAT PARQUET, "
                        "PARTITION_BY (year), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part')")
            parts = [(f"year={y}", vdir / f"year={y}", int(n)) for y, n in con.execute(
                "SELECT year, count(*) FROM _parted GROUP BY 1 ORDER BY 1").fetchall()]
        glob = str(vdir / ("*.parquet" if partition_class == "whole" else "year=*/*.parquet"))
        row = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM "
                          f"read_parquet('{glob}', hive_partitioning=true) t").fetchone()
        n, h = (0, None) if row is None else row
        content_hash = f"{int(n)}:{'0' if h is None else format(int(h), 'x')}"
    finally:
        con.close()
    records: list[dict[str, object]] = []
    for label, pdir, n_rows in parts:
        _json_write(pdir / "_meta.json", {
            "table": table, "build_id": build_id, "partition": label or "whole",
            "n_rows": n_rows, "content_hash": content_hash, "lag_known": lag_known,
            "coverage_from": None,
            "gates": [{"name": "G0", "status": gate_status, "detail": "", "metrics": {}}]})
        records.append({"path": f"v={build_id}" + (f"/{label}" if label else ""),
                        "n_rows": n_rows})
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id="snap_test", rules_version="v_test",
        built_at_utc="2026-09-05T00:00:00+00:00", n_rows=len(rows),
        content_hash=content_hash, partitions=records, gates=[]))
    return StageTree(stage_root, table, build_id, table_root,
                     tuple(label or "whole" for label, _, _ in parts))


@pytest.fixture
def make_stage_tree():
    """가짜 stage 트리 생성기 — equity 테스트는 전부 이 픽스처만 쓴다."""
    return _make_stage_tree
