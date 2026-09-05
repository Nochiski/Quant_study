"""T0.8 — `python -m equity <verb>` CLI."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from equity.__main__ import main
from equity.model import RULES
from stage import manifest

ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]


def _env(tmp_path: Path, make_stage_tree) -> tuple[list[str], Path]:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    fx = eq / "fixtures" / "sample_table.json"
    fx.parent.mkdir(parents=True)
    fx.write_text(json.dumps([{"case": "k1_val", "key": {"k": "1"}, "column": "val",
                               "expect": "a", "source": "hand"}]), encoding="utf-8")
    return ["--root", str(eq), "--stage-root", str(tree.stage_root)], eq


def test_서브커맨드_4개(tmp_path: Path, make_stage_tree, capsys) -> None:
    base, eq = _env(tmp_path, make_stage_tree)
    assert main([*base, "pin", "stg_sample"]) == 0
    assert "pinned table=stg_sample" in capsys.readouterr().out
    assert main([*base, "build", "sample_table", "--build-id", "b_eq_1"]) == 0
    assert "ok table=sample_table" in capsys.readouterr().out
    assert main([*base, "gate", "sample_table"]) == 0
    out = capsys.readouterr().out
    assert "ok table=sample_table" in out and "EG0" in out
    assert main([*base, "catalog"]) == 0
    assert "snapshot_id=" in capsys.readouterr().out


def test_없는_테이블은_argparse_에러(tmp_path: Path, make_stage_tree) -> None:
    base, _ = _env(tmp_path, make_stage_tree)
    with pytest.raises(SystemExit) as e:
        main([*base, "build", "없는테이블"])
    assert e.value.code == 2
    # RULES 는 import 된 rules_* 모듈이 채우는 전역 레지스트리라 같은 세션에서 S01·S02 를
    # import 하면 늘어난다. T0 회귀 축은 "sample_table 이 계속 등록돼 있다" 뿐이다.
    assert "sample_table" in RULES


def test_동사를_안_주면_argparse_에러() -> None:
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2


def test_게이트_실패는_종료코드_1(tmp_path: Path, make_stage_tree, capsys) -> None:
    base, eq = _env(tmp_path, make_stage_tree)
    (eq / "fixtures" / "sample_table.json").write_text(
        json.dumps([{"case": "k1_val", "key": {"k": "1"}, "column": "val", "expect": "틀린값",
                     "source": "hand"}]), encoding="utf-8")
    assert main([*base, "build", "sample_table", "--build-id", "b_eq_1"]) == 1
    assert "gate_failed" in capsys.readouterr().out
    assert manifest.load(eq / "sample_table" / "MANIFEST.json").current_build is None


def test_gate는_커밋된_빌드를_폐기하지_않는다(tmp_path: Path, make_stage_tree, capsys) -> None:
    base, eq = _env(tmp_path, make_stage_tree)
    assert main([*base, "build", "sample_table", "--build-id", "b_eq_1"]) == 0
    capsys.readouterr()
    (eq / "fixtures" / "sample_table.json").write_text(
        json.dumps([{"case": "k1_val", "key": {"k": "1"}, "column": "val", "expect": "틀린값",
                     "source": "hand"}]), encoding="utf-8")
    assert main([*base, "gate", "sample_table"]) == 1
    assert "gate_failed" in capsys.readouterr().out
    m = manifest.load(eq / "sample_table" / "MANIFEST.json")
    assert m.current_build == "b_eq_1"            # 재판정은 포인터를 건드리지 않는다
    assert (eq / "sample_table" / "v=b_eq_1").exists()


def test_gate는_커밋이_없으면_예외(tmp_path: Path, make_stage_tree) -> None:
    base, _ = _env(tmp_path, make_stage_tree)
    with pytest.raises(FileNotFoundError, match="no committed build to re-adjudicate"):
        main([*base, "gate", "sample_table"])


def test_gate는_빌드와_같은_const를_만든다(tmp_path: Path, make_stage_tree, capsys) -> None:
    """EG1 우변·extra_gates 가 `_const` 를 읽는 테이블(S05 corp_event 의 pool CTE)은 재판정 때도
    같은 상수가 있어야 한다 — 없으면 Binder 오류로 `gate` 가 죽는다."""
    from equity import rules_sample
    from equity.model import EquityTable, register

    base, eq = _env(tmp_path, make_stage_tree)
    sql = tmp_path / "const_gate_table.sql"
    sql.write_text("SELECT s.*, NULL::VARCHAR AS reject_reason FROM stg_sample s "
                   "CROSS JOIN _const c WHERE s.k >= c.k_min", encoding="utf-8")
    if "const_gate_table" not in RULES:
        register(EquityTable(**{
            **rules_sample.SAMPLE_TABLE.__dict__, "name": "const_gate_table",
            "consts": ("k_min",), "sql_path": sql,
            "eg1_rhs_sql": "SELECT count(*) FROM stg_sample s CROSS JOIN _const c "
                           "WHERE s.k >= c.k_min"}))
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps({"const_gate_table": {"k_min": 2}}), encoding="utf-8")
    (eq / "fixtures" / "const_gate_table.json").write_text(json.dumps(
        [{"case": "k2", "key": {"k": "2"}, "column": "val", "expect": "b", "source": "hand"}]),
        encoding="utf-8")
    assert main([*base, "--baseline", str(bl), "build", "const_gate_table",
                 "--build-id", "b_c1"]) == 0
    assert "ok table=const_gate_table" in capsys.readouterr().out
    assert main([*base, "--baseline", str(bl), "gate", "const_gate_table"]) == 0
    out = capsys.readouterr().out
    assert "ok table=const_gate_table" in out and "'lhs': 2, 'rhs': 2" in out
