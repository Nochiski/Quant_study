"""T0.5 — 빌더 골격: 파티션 배치 · content_hash · 게이트 실패 시 폐기 · `_reject` · `_const`."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from equity import build, rules_sample
from equity.baseline import Baseline
from equity.gates import GateStatus
from equity.model import EquityTable
from stage import manifest

SQL_DIR = Path(rules_sample.__file__).parent / "sql"
ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]
DATED: list[dict[str, object]] = [
    {"date": date(2020, 1, 3), "ticker": "005930"},
    {"date": date(2021, 1, 4), "ticker": "005930"},
]
DATED_SQL = ("SELECT date, ticker, date AS available_date, 'default' AS available_basis, "
             "NULL::VARCHAR AS reject_reason FROM stg_dated")


def _write_fixtures(equity_root: Path, table: str, entries: list[dict[str, object]]) -> None:
    p = equity_root / "fixtures" / f"{table}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _sample_fixtures(equity_root: Path) -> None:
    _write_fixtures(equity_root, "sample_table",
                    [{"case": "k1_val", "key": {"k": "1"}, "column": "val", "expect": "a",
                      "source": "hand — conftest make_stage_tree 의 첫 행"}])


def _sql(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.sql"
    p.write_text(text, encoding="utf-8")
    return p


def _dated_rule(tmp_path: Path) -> EquityTable:
    return EquityTable(
        name="dated_table", grain=("date", "ticker"),
        columns={"date": "DATE", "ticker": "VARCHAR", "available_date": "DATE",
                 "available_basis": "VARCHAR"},
        inputs=("stg_dated",), partition_class="date_axis", partition_key_expr="year(date)",
        available_rule="column:available_date — date 그대로",
        eg1_lhs_sql="SELECT count(*) FROM out_pq",
        eg1_rhs_sql="SELECT count(*) FROM stg_dated",
        sql_path=_sql(tmp_path, "dated_table", DATED_SQL),
        input_columns={"stg_dated": ("date", "ticker")}, available_basis=("default",),
        content_date_column="date")


def test_whole은_part0_parquet(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    _sample_fixtures(eq)
    r = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                          build_id="b_eq_1")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None and (r.out_dir / "part0.parquet").exists()
    assert [p["path"] for p in r.partitions] == ["v=b_eq_1"]
    assert r.n_rows == 3 and r.n_reject == 0


def test_date_axis는_year디렉토리(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_dated", DATED, "date_axis")
    eq = tmp_path / "equity"
    rule = _dated_rule(tmp_path)
    _write_fixtures(eq, rule.name, [{"case": "first", "key": {"date": "2020-01-03"},
                                     "column": "ticker", "expect": "005930", "source": "hand"}])
    r = build.build_table(rule, tree.stage_root, eq, Baseline(), build_id="b_eq_1")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    assert sorted(p.name for p in r.out_dir.iterdir() if p.is_dir()) == ["year=2020", "year=2021"]
    assert [p["path"] for p in r.partitions] == ["v=b_eq_1/year=2020", "v=b_eq_1/year=2021"]


def test_content_hash가_build_id에_불변(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_dated", DATED, "date_axis")
    eq = tmp_path / "equity"
    rule = _dated_rule(tmp_path)
    _write_fixtures(eq, rule.name, [{"case": "first", "key": {"date": "2020-01-03"},
                                     "column": "ticker", "expect": "005930", "source": "hand"}])
    first = build.build_table(rule, tree.stage_root, eq, Baseline(), build_id="b_eq_1")
    second = build.build_table(rule, tree.stage_root, eq, Baseline(), build_id="b_eq_2")
    assert first.ok and second.ok
    assert first.content_hash == second.content_hash
    assert ([p["content_hash"] for p in first.partitions]
            == [p["content_hash"] for p in second.partitions])
    eg5a = next(g for g in second.gates if g.name == "EG5a")
    assert eg5a.status is GateStatus.PASS, eg5a.detail


def test_게이트_실패시_tmp폐기_MANIFEST불변(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    _sample_fixtures(eq)
    ok = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                           build_id="b_eq_1")
    assert ok.ok
    broken = EquityTable(**{**rules_sample.SAMPLE_TABLE.__dict__, "eg1_rhs_sql": "SELECT 99"})
    bad = build.build_table(broken, tree.stage_root, eq, Baseline(), build_id="b_eq_2")
    assert bad.status is build.BuildStatus.GATE_FAILED
    assert bad.failed_report is not None and bad.failed_report.exists()
    report = json.loads(bad.failed_report.read_text(encoding="utf-8"))
    assert report["first_failed_gate"] == "EG1" and report["snapshot_id"] == ""
    m = manifest.load(eq / "sample_table" / "MANIFEST.json")
    assert m.current_build == "b_eq_1"                       # 포인터 불변
    assert not (eq / "_tmp" / "b_eq_2").exists()             # tmp 폐기
    assert not (eq / "sample_table" / "v=b_eq_2").exists()
    later = [g for g in bad.gates if g.name in ("EG2", "EG3", "EG4", "EG5a")]
    assert {g.detail for g in later} == {"upstream_failed"}


def test_reject는_사유별_디렉토리(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    rule = EquityTable(**{
        **rules_sample.SAMPLE_TABLE.__dict__, "name": "rejecting_table",
        "sql_path": _sql(tmp_path, "rejecting_table",
                         "SELECT *, CASE WHEN k = 2 THEN 'off_grid' END AS reject_reason "
                         "FROM stg_sample"),
        "eg1_rhs_sql": "SELECT count(*) FROM stg_sample"})
    _write_fixtures(eq, rule.name, [{"case": "k1_val", "key": {"k": "1"}, "column": "val",
                                     "expect": "a", "source": "hand"}])
    r = build.build_table(rule, tree.stage_root, eq, Baseline(), build_id="b_eq_1",
                          gate_thresholds={"EG7": 0.5})
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == 2 and r.n_reject == 1
    assert r.out_dir is not None
    reject_dirs = sorted(p.name for p in (r.out_dir / "_reject").iterdir())
    assert reject_dirs == ["reject_reason=off_grid"]
    eg7 = next(g for g in r.gates if g.name == "EG7")
    assert eg7.metrics["reject_by_reason"] == {"off_grid": 1}


def test_격리비율이_임계를_넘으면_폐기(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    rule = EquityTable(**{
        **rules_sample.SAMPLE_TABLE.__dict__, "name": "rejecting_table",
        "sql_path": _sql(tmp_path, "rejecting_table",
                         "SELECT *, CASE WHEN k = 2 THEN 'off_grid' END AS reject_reason "
                         "FROM stg_sample")})
    r = build.build_table(rule, tree.stage_root, eq, Baseline(), build_id="b_eq_1")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert next(g for g in r.gates if g.name == "EG7").status is GateStatus.FAIL


def test_sql파일에_상수_하드코딩_없음() -> None:
    allowed = {"0", "1", "2", "-1"}          # 인덱스·부호·span_seq 초기값만
    for p in sorted(SQL_DIR.glob("*.sql")):
        text = re.sub(r"--[^\n]*", "", p.read_text(encoding="utf-8"))
        text = re.sub(r"'[^']*'", "''", text)     # 문자열 리터럴('2우선주' 어휘)은 상수가 아니다
        nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text))
        assert nums <= allowed, f"{p.name}: 하드코딩 상수 {sorted(nums - allowed)}"


def test_상수는_const_임시테이블로_주입된다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    rule = EquityTable(**{
        **rules_sample.SAMPLE_TABLE.__dict__, "name": "const_table", "consts": ("k_min",),
        "sql_path": _sql(tmp_path, "const_table",
                         "SELECT s.*, NULL::VARCHAR AS reject_reason FROM stg_sample s "
                         "CROSS JOIN _const c WHERE s.k >= c.k_min"),
        "eg1_rhs_sql": "SELECT count(*) FROM stg_sample WHERE k >= 2"})
    _write_fixtures(eq, rule.name, [{"case": "k2_val", "key": {"k": "2"}, "column": "val",
                                     "expect": "b", "source": "hand"}])
    bl = Baseline({"const_table": {"k_min": 2}})
    r = build.build_table(rule, tree.stage_root, eq, bl, build_id="b_eq_1")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == 2


def test_baseline_상수_미등재면_예외(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    rule = EquityTable(**{**rules_sample.SAMPLE_TABLE.__dict__, "consts": ("k_min",)})
    with pytest.raises(KeyError, match="baseline constant not found"):
        build.build_table(rule, tree.stage_root, tmp_path / "equity", Baseline())


def test_커밋은_inputs와_빈_snapshot_id를_싣는다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    eq = tmp_path / "equity"
    _sample_fixtures(eq)
    r = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                          build_id="b_eq_1")
    assert r.ok
    m = manifest.load(eq / "sample_table" / "MANIFEST.json")
    rec = next(b for b in m.builds if b.build_id == "b_eq_1")
    assert rec.inputs == {"stg_sample": tree.build_id}
    assert rec.snapshot_id == ""
    meta = json.loads((eq / "sample_table" / "v=b_eq_1" / "_meta.json").read_text(
        encoding="utf-8"))
    assert meta["inputs"] == {"stg_sample": tree.build_id}
    assert meta["partition"] == "whole" and meta["n_reject_by_reason"] == {}
    assert [g["name"] for g in meta["gates"]] == ["EG0", "EG7", "EG1", "EG2", "EG13", "EG3", "EG4",
                                                  "EG5a"]


# ── EG13 음성 대조 (K1-3a) — 판을 지은 원장 스냅샷보다 뒤의 공개일 = 미래 데이터 ─────────────
def test_EG13_정상_판은_통과하고_기준일이_meta에_남는다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS, snapshot_id="snap_20210104T000000Z")
    eq = tmp_path / "equity"
    _sample_fixtures(eq)
    r = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                          build_id="b_eq_1")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    eg13 = next(g for g in r.gates if g.name == "EG13")
    assert eg13.status is GateStatus.PASS
    assert eg13.metrics["snapshot_kst_date"] == "2021-01-04"


def test_EG13_미래_공개일_한_행을_심으면_판을_폐기하고_MANIFEST는_그대로(
        tmp_path: Path, make_stage_tree) -> None:
    """스냅샷 2021-01-04 09:00 KST 로 지은 stage 판에 공개일 2021-01-05 행 하나를 심는다."""
    snap = "snap_20210104T000000Z"
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS, snapshot_id=snap)
    eq = tmp_path / "equity"
    _sample_fixtures(eq)
    ok = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                           build_id="b_eq_1")
    assert ok.ok
    planted = [*ROWS, {"k": 4, "val": "d", "available_date": date(2021, 1, 5),
                       "available_basis": "measured"}]
    tree2 = make_stage_tree(tmp_path, "stg_sample", planted, build_id="b_stage_0002",
                            snapshot_id=snap)
    bad = build.build_table(rules_sample.SAMPLE_TABLE, tree2.stage_root, eq, Baseline(),
                            build_id="b_eq_2")
    assert bad.status is build.BuildStatus.GATE_FAILED
    assert bad.failed_report is not None
    report = json.loads(bad.failed_report.read_text(encoding="utf-8"))
    assert report["first_failed_gate"] == "EG13"
    eg13 = next(g for g in bad.gates if g.name == "EG13")
    assert eg13.metrics["n_available_after_snapshot"] == 1
    assert {g.name: g.status for g in bad.gates}["EG2"] is GateStatus.PASS   # 하한은 멀쩡하다
    m = manifest.load(eq / "sample_table" / "MANIFEST.json")
    assert m.current_build == "b_eq_1"                       # 폐기형 — 포인터 불변
    assert not (eq / "sample_table" / "v=b_eq_2").exists()


def test_build_by_year는_아직_구현되지_않았다(tmp_path: Path, make_stage_tree) -> None:
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS)
    rule = EquityTable(**{**rules_sample.SAMPLE_TABLE.__dict__, "build_by_year": True})
    with pytest.raises(NotImplementedError, match="build_by_year"):
        build.build_table(rule, tree.stage_root, tmp_path / "equity", Baseline())
