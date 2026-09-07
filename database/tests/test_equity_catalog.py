"""T0.9 골격 — `equity.duckdb` 매크로 카탈로그. 데이터는 없고 매크로만 산다 (DESIGN §2 [결정 1])."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, catalog, rules_sample
from equity.baseline import Baseline

ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]


def _built(tmp_path: Path, make_stage_tree) -> Path:
    """샘플 테이블 1개를 커밋한 equity 루트."""
    import json

    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    fx = eq / "fixtures" / "sample_table.json"
    fx.parent.mkdir(parents=True)
    fx.write_text(json.dumps([{"case": "k1_val", "key": {"k": "1"}, "column": "val",
                               "expect": "a", "source": "hand"}]), encoding="utf-8")
    r = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                          build_id="b_eq_1")
    if not r.ok:
        raise AssertionError([(g.name, g.status.value, g.detail) for g in r.gates])
    return eq


def _macro(eq: Path) -> dict[str, str]:
    glob = str(eq / "sample_table" / "v=b_eq_1" / "*.parquet")
    return {"v_sample(k_min)": f"SELECT * FROM read_parquet('{glob}') WHERE k >= k_min"}


def test_파일DB_read_only_재오픈_호출(tmp_path: Path, make_stage_tree) -> None:
    eq = _built(tmp_path, make_stage_tree)
    path = catalog.write_catalog(eq, _macro(eq))
    ro = duckdb.connect(str(path), read_only=True)
    try:
        assert ro.execute("SELECT count(*) FROM v_sample(2)").fetchone() == (2,)
    finally:
        ro.close()


def test_os_replace_교체_후에도_옛_리더는_살아있다(tmp_path: Path, make_stage_tree) -> None:
    eq = _built(tmp_path, make_stage_tree)
    ro = duckdb.connect(str(catalog.write_catalog(eq, _macro(eq))), read_only=True)
    try:
        catalog.write_catalog(eq, _macro(eq))          # 새 파일로 통째 교체
        assert ro.execute("SELECT count(*) FROM v_sample(1)").fetchone() == (3,)
    finally:
        ro.close()


def test_snapshot_id는_전_테이블_current_build_해시다(tmp_path: Path, make_stage_tree) -> None:
    eq = _built(tmp_path, make_stage_tree)
    builds = catalog.table_builds(eq)
    assert builds == {"sample_table": "b_eq_1"}        # `_pinned`·`_tmp` 는 테이블이 아니다
    assert catalog.snapshot_id(builds) == catalog.snapshot_id({"sample_table": "b_eq_1"})
    assert catalog.snapshot_id(builds) != catalog.snapshot_id({"sample_table": "b_eq_2"})


def test_catalog_meta에_snapshot_id를_남긴다(tmp_path: Path, make_stage_tree) -> None:
    import json

    eq = _built(tmp_path, make_stage_tree)
    catalog.write_catalog(eq, _macro(eq))
    meta = json.loads((eq / catalog.META_NAME).read_text(encoding="utf-8"))
    assert meta["snapshot_id"] == catalog.snapshot_id({"sample_table": "b_eq_1"})
    assert meta["builds"] == {"sample_table": "b_eq_1"}
    assert meta["macros"] == ["v_sample(k_min)"]


def test_잘못된_매크로_이름은_ValueError(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid macro name"):
        catalog.write_catalog(tmp_path, {"DROP TABLE x; --": "SELECT 1"})


def test_매크로가_없어도_빈_카탈로그를_만든다(tmp_path: Path) -> None:
    path = catalog.write_catalog(tmp_path, {})
    ro = duckdb.connect(str(path), read_only=True)
    try:
        assert ro.execute("SELECT 1").fetchone() == (1,)
    finally:
        ro.close()
    assert catalog.table_builds(tmp_path) == {}
