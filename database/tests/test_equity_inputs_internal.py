"""equity 테이블을 다른 equity 테이블의 입력으로 — `stg_` 접두 없는 입력은 equity_root 에서 온다."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from equity import build, inputs, rules_sample
from equity.baseline import Baseline
from equity.model import EquityTable
from stage import manifest

ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]
DERIVED_SQL = ("SELECT k, val, available_date, available_basis, NULL::VARCHAR AS reject_reason "
               "FROM sample_table")
COLUMNS = {"k": "BIGINT", "val": "VARCHAR", "available_date": "DATE", "available_basis": "VARCHAR"}


def _fixtures(equity_root: Path, table: str) -> None:
    p = equity_root / "fixtures" / f"{table}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([{"case": "k1_val", "key": {"k": "1"}, "column": "val",
                              "expect": "a", "source": "hand — ROWS 첫 행"}]), encoding="utf-8")


def _derived_rule(tmp_path: Path) -> EquityTable:
    sql = tmp_path / "derived_table.sql"
    sql.write_text(DERIVED_SQL, encoding="utf-8")
    return EquityTable(
        name="derived_table", grain=("k",), columns=dict(COLUMNS), inputs=("sample_table",),
        partition_class="whole", partition_key_expr=None,
        available_rule="column:available_date — sample_table 그대로",
        eg1_lhs_sql="SELECT count(*) FROM out_pq", eg1_rhs_sql="SELECT count(*) FROM sample_table",
        sql_path=sql, input_columns={"sample_table": tuple(COLUMNS)}, available_basis=("measured",))


def _build_sample(tmp_path: Path, make_stage_tree) -> tuple[Path, Path]:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    _fixtures(eq, "sample_table")
    r = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                          build_id="b_eq_1")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    return tree.stage_root, eq


def test_source_root는_stg_접두로_출처를_고른다() -> None:
    assert inputs.source_root(Path("s"), Path("e"), "stg_listing_daily") == Path("s")
    assert inputs.source_root(Path("s"), Path("e"), "security_span") == Path("e")


def test_equity_입력은_equity_root의_current_build를_하드링크로_고정한다(tmp_path: Path,
                                                                   make_stage_tree) -> None:
    stage_root, eq = _build_sample(tmp_path, make_stage_tree)
    rule = _derived_rule(tmp_path)
    _fixtures(eq, rule.name)
    r = build.build_table(rule, stage_root, eq, Baseline(), build_id="b_eq_2")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.inputs == {"sample_table": "b_eq_1"}
    pinned = eq / "_pinned" / "sample_table" / "v=b_eq_1" / "part0.parquet"
    src = eq / "sample_table" / "v=b_eq_1" / "part0.parquet"
    assert pinned.exists() and os.stat(pinned).st_ino == os.stat(src).st_ino
    pinned_manifest = manifest.load(eq / "_pinned" / "sample_table" / "MANIFEST.json")
    assert pinned_manifest.current_build == "b_eq_1"
    eg0 = next(g for g in r.gates if g.name == "EG0")
    assert eg0.metrics["unpinned"] == [] and eg0.metrics["input_gate_fail"] == []
    assert r.out_dir is not None
    n = build.duckdb.sql(f"SELECT count(*) FROM '{r.out_dir}/part0.parquet'").fetchone()
    assert n is not None and n[0] == 3


def test_상위_equity_테이블이_재빌드돼도_고정된_입력_판본은_남는다(tmp_path: Path,
                                                             make_stage_tree) -> None:
    stage_root, eq = _build_sample(tmp_path, make_stage_tree)
    rule = _derived_rule(tmp_path)
    _fixtures(eq, rule.name)
    first = build.build_table(rule, stage_root, eq, Baseline(), build_id="b_eq_2")
    again = build.build_table(rules_sample.SAMPLE_TABLE, stage_root, eq, Baseline(),
                              build_id="b_eq_3")
    assert first.ok and again.ok
    second = build.build_table(rule, stage_root, eq, Baseline(), build_id="b_eq_4")
    assert second.inputs == {"sample_table": "b_eq_3"}
    assert (eq / "_pinned" / "sample_table" / "v=b_eq_1").exists()      # 첫 판본 그대로
    assert inputs.load_pinned(eq, "sample_table", "b_eq_1").build_id == "b_eq_1"


def test_커밋된_빌드가_없는_equity_입력은_이름을_들어_실패한다(tmp_path: Path,
                                                          make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    with pytest.raises(FileNotFoundError, match="table=sample_table"):
        inputs.pin(tree.stage_root, tmp_path / "equity", "sample_table")


def test_자기_자신을_입력으로_선언하면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read itself"):
        EquityTable(name="loop", grain=("k",), columns={"k": "BIGINT"}, inputs=("loop",),
                    partition_class="whole", partition_key_expr=None, available_rule="none",
                    eg1_lhs_sql="SELECT 1", eg1_rhs_sql="SELECT 1", sql_path=tmp_path / "loop.sql")
