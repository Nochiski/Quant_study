"""T0.9 골격 — `equity.duckdb` 매크로 카탈로그. 데이터는 없고 매크로만 산다 (DESIGN §2 [결정 1])."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, catalog, rules_sample
from equity.baseline import Baseline
from equity.gates import GateStatus

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


# ── DEFECT-C04: duckdb 자원 제한 (감사 09-19) ────────────────────────────────


def test_카탈로그_연결은_스레드와_메모리를_묶는다(tmp_path: Path) -> None:
    """DEFECT-C04 — `build.py` 는 `--threads 3 --memory-limit 8GB` 로 묶여 있는데 카탈로그·계약
    단계의 duckdb 연결은 기본값(코어 수 4 · RAM 80%)으로 열려 379% CPU 를 썼다.

    같은 4코어 N150 서버에서 kael-system-v3 가 평일 20:05 KST `daily_all` 체인을 돌린다 —
    빌드 락은 quant-ledger 안에서만 직렬화하므로 겹치면 v3 수집이 타임아웃할 수 있다.
    """
    assert (catalog.DUCKDB_THREADS, catalog.DUCKDB_MEMORY_LIMIT) == (3, "8GB")
    ctl = duckdb.connect()
    ctl.execute(f"SET memory_limit = '{catalog.DUCKDB_MEMORY_LIMIT}'")
    want_mem = ctl.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    default_mem = duckdb.connect().execute(
        "SELECT current_setting('memory_limit')").fetchone()[0]
    ctl.close()
    assert want_mem != default_mem            # 기본값(RAM 80%)과 다른 값을 실제로 건다
    with catalog.connect() as con:
        assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 3
        assert con.execute("SELECT current_setting('memory_limit')").fetchone()[0] == want_mem
    path = tmp_path / "c.duckdb"
    catalog.build_catalog_file(path, {})
    with catalog.connect(path, read_only=True) as con:
        assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 3


# ── DEFECT-C08: --rebase-asof 승인 기록 (감사 09-19) ──────────────────────────


def _asof_parquet(path: Path, sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


_ASOF_SQL = ("SELECT DATE '2026-08-20' AS as_of, '005930' AS ticker, "
             "DATE '2026-08-20' AS date, 1 AS v")


def _asof_env(tmp_path: Path, cur_sql: str, prev_sql: str) -> tuple[Path, dict]:
    eq = tmp_path / "equity"
    prev = eq / catalog.ASOF_DIR / "v_adj_price" / "sid_prev"
    _asof_parquet(prev / catalog.ASOF_PART, prev_sql)
    (prev / catalog.ASOF_META).write_text(
        '{"view": "v_adj_price", "snapshot_id": "sid_prev", "builds": {}, '
        '"written_at_utc": "2026-09-16T22:30:00+00:00"}', encoding="utf-8")
    cur = tmp_path / "cur.parquet"
    _asof_parquet(cur, cur_sql)
    return eq, {"v_adj_price": {"path": str(cur), "n_rows": 1, "content_hash": "1:x"}}


def test_EG5c는_컬럼_추가와_값_변경을_구분해_기록한다(tmp_path: Path) -> None:
    """DEFECT-C08 — EG5c 는 행 전체 해시를 비교하므로 `changed` 하나로는 '컬럼이 늘었다' 와
    '과거 값이 다시 쓰였다' 를 못 가른다. 9/16 의 n_diff=319,310 은 전자였는데 그 판정이
    승인 시점에 기록되지 않았다."""
    eq, written = _asof_env(tmp_path, f"{_ASOF_SQL}, 'krx' AS basis", _ASOF_SQL)
    g = catalog.eg5c_asof_invariance(eq, written, (["2026-08-20"], ["005930"]), False)
    assert g.status is GateStatus.FAIL
    view = g.metrics["views"]["v_adj_price"]
    assert view["schema_changed"] is True
    assert view["columns_added"] == ["basis"] and view["columns_removed"] == []
    assert view["diff_by_kind"] == {"changed": 1}
    assert "schema_changed" in g.detail


def test_값만_바뀌면_schema_changed가_아니다(tmp_path: Path) -> None:
    eq, written = _asof_env(tmp_path, _ASOF_SQL.replace("1 AS v", "2 AS v"), _ASOF_SQL)
    g = catalog.eg5c_asof_invariance(eq, written, (["2026-08-20"], ["005930"]), False)
    assert g.status is GateStatus.FAIL
    view = g.metrics["views"]["v_adj_price"]
    assert view["schema_changed"] is False and view["columns_added"] == []


def test_rebase_asof는_사유_없이는_거부된다(tmp_path: Path) -> None:
    """승인 사유·승인자·대상 diff 가 어디에도 남지 않으면 다음에 같은 규모의 차이가 떴을 때
    '지난번에도 이랬다' 로 넘어간다 — `_asof/<sid>/_meta.json` 은 keep=3(1.5일)이라 곧 사라진다."""
    with pytest.raises(ValueError, match="--reason"):
        catalog.publish(tmp_path / "equity", Baseline(), rebase_asof=True)


def test_rebase_승인은_영구_기록으로_남는다(tmp_path: Path, monkeypatch) -> None:
    eq, written = _asof_env(tmp_path, f"{_ASOF_SQL}, 'krx' AS basis", _ASOF_SQL)
    g = catalog.eg5c_asof_invariance(eq, written, (["2026-08-20"], ["005930"]), True)
    assert g.status is GateStatus.PASS and "rebased" in g.detail
    monkeypatch.setenv("USER", "kael")
    p = catalog.record_rebase_approval(eq, "e1.16.0 뷰 컬럼 추가", "sid_new", g)
    import json as _json
    rec = _json.loads(p.read_text(encoding="utf-8"))
    assert rec["reason"] == "e1.16.0 뷰 컬럼 추가" and rec["approver"] == "kael"
    assert rec["snapshot_id"] == "sid_new"
    assert rec["views"]["v_adj_price"]["previous_snapshot_id"] == "sid_prev"
    assert rec["views"]["v_adj_price"]["schema_changed"] is True
    assert rec["views"]["v_adj_price"]["columns_added"] == ["basis"]
    # GC 대상이 아니다 — `_asof/<view>/` 밖에 산다
    assert p.parent == eq / catalog.ASOF_DIR / catalog.ASOF_APPROVALS
