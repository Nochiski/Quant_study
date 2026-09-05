"""T0.6 — 게이트 일반형과 부정 픽스처(결함 주입).

게이트가 통과했다는 사실만으로는 게이트가 작동한다는 증거가 없다(GATES §7-5).
게이트마다 정/부 한 쌍을 tmp 위 소형 데이터로 돌린다 — 서버 데이터 불필요.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import gates, inputs
from equity.baseline import Baseline
from equity.gates import EquityGateContext, GateStatus, SkipGate
from equity.model import AVAILABLE_NONE, RULES_VERSION, EquityTable
from stage.gates import GateResult
from stage.manifest import BuildRecord

ROWS: list[dict[str, object]] = [
    {"k": 1, "val": "a", "available_date": date(2020, 1, 3), "available_basis": "measured"},
    {"k": 2, "val": "b", "available_date": date(2020, 1, 6), "available_basis": "measured"},
    {"k": 3, "val": "c", "available_date": date(2021, 1, 4), "available_basis": "measured"},
]
OUT_COLS = {"k": "BIGINT", "val": "VARCHAR", "available_date": "DATE",
            "available_basis": "VARCHAR"}
_OK_SQL = ("SELECT * FROM (VALUES (1, 'a', DATE '2020-01-03', 'measured'), "
           "(2, 'b', DATE '2020-01-06', 'measured')) AS t(k, val, available_date, "
           "available_basis)")


def _rule(**over: object) -> EquityTable:
    kw: dict[str, object] = {
        "name": "t", "grain": ("k",), "columns": dict(OUT_COLS), "inputs": (),
        "partition_class": "whole", "partition_key_expr": None,
        "available_rule": "column:available_date",
        "eg1_lhs_sql": "SELECT count(*) FROM out_pq",
        "eg1_rhs_sql": "SELECT count(*) FROM out_pq",
        "sql_path": Path("/tmp/unused.sql"), "available_basis": ("measured",)}
    kw.update(over)
    return EquityTable(**kw)   # pyright: ignore[reportArgumentType]  # reason: 테스트 팩토리


def _ctx(con: duckdb.DuckDBPyConnection, rule: EquityTable, **over: object) -> EquityGateContext:
    kw: dict[str, object] = {
        "con": con, "rule": rule, "out_view": "out_pq", "reject_view": None, "pinned": {},
        "n_out": 2, "n_reject": 0, "reject_by_reason": {}, "inputs": {}, "partition_hashes": {},
        "baseline": Baseline(), "previous": None, "fixtures": None, "thresholds": {}}
    kw.update(over)
    return EquityGateContext(**kw)   # pyright: ignore[reportArgumentType]  # reason: 테스트 팩토리


@pytest.fixture
def con(request: pytest.FixtureRequest) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    request.addfinalizer(c.close)
    c.execute(f"CREATE OR REPLACE TEMP VIEW out_pq AS {_OK_SQL}")
    return c


def _out(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    con.execute(f"CREATE OR REPLACE TEMP VIEW out_pq AS {sql}")


# ── EG0 ──────────────────────────────────────────────────────────────────────
def _pinned_env(tmp_path: Path, make_stage_tree, gate_status: str = "pass"):
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS, gate_status=gate_status)
    pb = inputs.pin(tree.stage_root, tmp_path / "equity", "stg_sample")
    c = duckdb.connect()
    inputs.create_views(c, {"stg_sample": pb}, {"stg_sample": list(OUT_COLS)})
    c.execute("CREATE OR REPLACE TEMP VIEW out_pq AS SELECT k, val, available_date, "
              'available_basis FROM "stg_sample"')
    return c, pb


def test_eg0_고정된_입력은_pass(tmp_path: Path, make_stage_tree) -> None:
    c, pb = _pinned_env(tmp_path, make_stage_tree)
    try:
        rule = _rule(inputs=("stg_sample",), input_columns={"stg_sample": tuple(OUT_COLS)})
        r = gates.eg0_inputs(_ctx(c, rule, pinned={"stg_sample": pb},
                                  inputs={"stg_sample": pb.build_id}, n_out=3))
        assert r.status is GateStatus.PASS, r.detail
    finally:
        c.close()


def test_eg0_고정_안된_입력은_fail(tmp_path: Path, make_stage_tree) -> None:
    c, pb = _pinned_env(tmp_path, make_stage_tree)
    try:
        rule = _rule(inputs=("stg_sample",))
        r = gates.eg0_inputs(_ctx(c, rule, pinned={"stg_sample": pb},
                                  inputs={"stg_sample": "b_not_pinned"}, n_out=3))
        assert r.status is GateStatus.FAIL
        assert r.metrics["unpinned"] == ["stg_sample"]
    finally:
        c.close()


def test_eg0_stage_컬럼_오타는_fail(tmp_path: Path, make_stage_tree) -> None:
    c, pb = _pinned_env(tmp_path, make_stage_tree)
    try:
        # `stck_prpr` 은 원장 실명 — stage ColumnRule.name 이 아니다.
        rule = _rule(inputs=("stg_sample",),
                     input_columns={"stg_sample": ("k", "stck_prpr")})
        r = gates.eg0_inputs(_ctx(c, rule, pinned={"stg_sample": pb},
                                  inputs={"stg_sample": pb.build_id}, n_out=3))
        assert r.status is GateStatus.FAIL
        assert r.metrics["missing_columns"] == ["stg_sample.stck_prpr"]
    finally:
        c.close()


def test_eg0_입력_meta_gates_fail_이면_fail(tmp_path: Path, make_stage_tree) -> None:
    c, pb = _pinned_env(tmp_path, make_stage_tree, gate_status="fail")
    try:
        rule = _rule(inputs=("stg_sample",))
        r = gates.eg0_inputs(_ctx(c, rule, pinned={"stg_sample": pb},
                                  inputs={"stg_sample": pb.build_id}, n_out=3))
        assert r.status is GateStatus.FAIL
        assert r.metrics["input_gate_fail"] == ["stg_sample.G0"]
    finally:
        c.close()


def test_eg0_선언_밖_컬럼이_out에_있으면_fail(con: duckdb.DuckDBPyConnection) -> None:
    rule = _rule(columns={"k": "BIGINT", "val": "VARCHAR"})
    r = gates.eg0_inputs(_ctx(con, rule))
    assert r.status is GateStatus.FAIL
    assert r.metrics["actual_columns"] == list(OUT_COLS)


def test_eg0_파티션_축_선언과_실물이_다르면_fail(con: duckdb.DuckDBPyConnection) -> None:
    rule = _rule(partition_class="date_axis", partition_key_expr="year(available_date)")
    r = gates.eg0_inputs(_ctx(con, rule))
    assert r.status is GateStatus.FAIL and r.metrics["has_year_axis"] is False


# ── EG1 ──────────────────────────────────────────────────────────────────────
def test_eg1_등식_성립하면_pass(con: duckdb.DuckDBPyConnection) -> None:
    assert gates.eg1_equation(_ctx(con, _rule())).status is GateStatus.PASS


def test_eg1_등식_없으면_fail(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg1_equation(_ctx(con, _rule(eg1_lhs_sql="", eg1_rhs_sql="")))
    assert r.status is GateStatus.FAIL and "착수 금지" in r.detail


def test_eg1_선언표는_skip_declaration_table(con: duckdb.DuckDBPyConnection) -> None:
    """등식 SQL 이 비어 있어도 선언표(`declaration_table=True`)는 착수 금지가 아니라 skip 이다."""
    r = gates.eg1_equation(_ctx(con, _rule(declaration_table=True, eg1_lhs_sql="",
                                           eg1_rhs_sql="")))
    assert r.status is GateStatus.SKIP and r.detail == "declaration_table"
    assert r.metrics == {"n_out": 2, "n_reject": 0}


def test_eg1_행수_한개_모자라면_fail(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg1_equation(_ctx(con, _rule(eg1_rhs_sql="SELECT 3")))
    assert r.status is GateStatus.FAIL
    assert (r.metrics["lhs"], r.metrics["rhs"], r.metrics["delta"]) == (2, 3, -1)


def test_eg1_격리행은_우변에서_빠진다(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg1_equation(_ctx(con, _rule(eg1_rhs_sql="SELECT 3"), n_reject=1,
                                reject_by_reason={"off_grid": 1}))
    assert r.status is GateStatus.PASS and r.metrics["expect"] == 2


# ── EG2 ──────────────────────────────────────────────────────────────────────
def test_eg2_정상_행은_pass(con: duckdb.DuckDBPyConnection) -> None:
    rule = _rule(content_date_column="available_date")
    assert gates.eg2_pit(_ctx(con, rule)).status is GateStatus.PASS


def test_eg2_available_null인데_basis_measured면_fail(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, 'a', NULL::DATE, 'measured')) AS "
              "t(k, val, available_date, available_basis)")
    r = gates.eg2_pit(_ctx(con, _rule()))
    assert r.status is GateStatus.FAIL and r.metrics["n_available_null"] == 1


def test_eg2_available_null이어도_basis_unknown이면_pass(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, 'a', NULL::DATE, 'unknown')) AS "
              "t(k, val, available_date, available_basis)")
    assert gates.eg2_pit(_ctx(con, _rule(available_basis=()))).status is GateStatus.PASS


def test_eg2_basis_어휘_밖이면_fail(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, 'a', DATE '2020-01-03', 'guessed')) AS "
              "t(k, val, available_date, available_basis)")
    r = gates.eg2_pit(_ctx(con, _rule(available_basis=())))
    assert r.status is GateStatus.FAIL and r.metrics["n_basis_outside_vocab"] == 1


def test_eg2_available이_내용일보다_이르면_fail(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, DATE '2020-01-10', DATE '2020-01-03', 'measured')) AS "
              "t(k, content_date, available_date, available_basis)")
    rule = _rule(columns={"k": "BIGINT", "content_date": "DATE", "available_date": "DATE",
                          "available_basis": "VARCHAR"}, content_date_column="content_date")
    r = gates.eg2_pit(_ctx(con, rule))
    assert r.status is GateStatus.FAIL and r.metrics["n_available_before_content"] == 1


def test_eg2_차원테이블은_skip(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg2_pit(_ctx(con, _rule(available_rule=AVAILABLE_NONE)))
    assert r.status is GateStatus.SKIP and r.detail == "dimension_table"


def test_eg2_팩트인데_PIT컬럼이_없으면_fail(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1)) AS t(k)")
    r = gates.eg2_pit(_ctx(con, _rule(columns={"k": "BIGINT"})))
    assert r.status is GateStatus.FAIL
    assert r.metrics["missing_columns"] == ["available_date", "available_basis"]


# ── EG3 ──────────────────────────────────────────────────────────────────────
def test_eg3_유일키는_pass(con: duckdb.DuckDBPyConnection) -> None:
    assert gates.eg3_keys(_ctx(con, _rule())).status is GateStatus.PASS


def test_eg3_pk_중복은_fail(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, 'a', DATE '2020-01-03', 'measured'), "
              "(1, 'b', DATE '2020-01-06', 'measured')) AS "
              "t(k, val, available_date, available_basis)")
    r = gates.eg3_keys(_ctx(con, _rule()))
    assert r.status is GateStatus.FAIL and r.metrics["n_duplicate_keys"] == 1


def test_eg3_선언밖_격리사유는_fail(con: duckdb.DuckDBPyConnection) -> None:
    ctx = _ctx(con, _rule(reject_reasons=("off_grid",)), n_reject=1,
               reject_by_reason={"pre_calendar": 1})
    r = gates.eg3_keys(ctx)
    assert r.status is GateStatus.FAIL
    assert r.metrics["undeclared_reject_reasons"] == ["pre_calendar"]


# ── EG4 ──────────────────────────────────────────────────────────────────────
def test_eg4_픽스처_없으면_fail(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg4_fixtures(_ctx(con, _rule()))
    assert r.status is GateStatus.FAIL and "픽스처 없음" in r.detail


def test_eg4_픽스처_일치는_pass(con: duckdb.DuckDBPyConnection) -> None:
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    r = gates.eg4_fixtures(_ctx(con, _rule(), fixtures=fx))
    assert r.status is GateStatus.PASS and r.metrics["n_mismatch"] == 0


def test_eg4_픽스처_불일치는_fail(con: duckdb.DuckDBPyConnection) -> None:
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "z", "source": "hand"}]
    r = gates.eg4_fixtures(_ctx(con, _rule(), fixtures=fx))
    assert r.status is GateStatus.FAIL and r.metrics["n_mismatch"] == 1


def test_eg4_null_기대는_SQL_NULL과_맞춘다(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, "SELECT * FROM (VALUES (1, NULL::VARCHAR, DATE '2020-01-03', 'measured')) AS "
              "t(k, val, available_date, available_basis)")
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": None, "source": "hand"}]
    assert gates.eg4_fixtures(_ctx(con, _rule(), fixtures=fx)).status is GateStatus.PASS


def test_eg4_필수키_빠진_픽스처는_예외(con: duckdb.DuckDBPyConnection) -> None:
    fx = [{"key": {"k": "1"}, "column": "val", "expect": "a"}]
    with pytest.raises(ValueError, match="fixture entry missing keys"):
        gates.eg4_fixtures(_ctx(con, _rule(), fixtures=fx))


# ── EG5a ─────────────────────────────────────────────────────────────────────
def _record(build_id: str, inputs_map: dict[str, str], h: str,
            rules_version: str = RULES_VERSION) -> BuildRecord:
    return BuildRecord(build_id=build_id, snapshot_id="", rules_version=rules_version,
                       built_at_utc="2026-09-05T00:00:00+00:00", n_rows=2, content_hash=h,
                       partitions=[{"path": f"v={build_id}", "n_rows": 2, "content_hash": h}],
                       inputs=inputs_map)


def test_eg5a_직전빌드_없으면_skip(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg5a_reproducibility(_ctx(con, _rule(), partition_hashes={"whole": "2:ff"}))
    assert r.status is GateStatus.SKIP and r.detail == "no_previous_build"
    assert r.metrics["partition_hashes"] == {"whole": "2:ff"}


def test_eg5a_같은_inputs면_해시_동일이_pass(con: duckdb.DuckDBPyConnection) -> None:
    prev = _record("b1", {"stg_sample": "s1"}, "2:ff")
    r = gates.eg5a_reproducibility(_ctx(con, _rule(), previous=prev,
                                        inputs={"stg_sample": "s1"},
                                        partition_hashes={"whole": "2:ff"}))
    assert r.status is GateStatus.PASS


def test_eg5a_해시가_바뀌면_fail(con: duckdb.DuckDBPyConnection) -> None:
    prev = _record("b1", {"stg_sample": "s1"}, "2:ff")
    r = gates.eg5a_reproducibility(_ctx(con, _rule(), previous=prev,
                                        inputs={"stg_sample": "s1"},
                                        partition_hashes={"whole": "2:aa"}))
    assert r.status is GateStatus.FAIL and r.metrics["n_changed_partitions"] == 1


def test_eg5a_규칙_판본이_바뀌면_skip_rules_changed(con: duckdb.DuckDBPyConnection) -> None:
    prev = _record("b1", {"stg_sample": "s1"}, "2:ff", rules_version="e0.0.0")
    r = gates.eg5a_reproducibility(_ctx(con, _rule(), previous=prev,
                                        inputs={"stg_sample": "s1"},
                                        partition_hashes={"whole": "2:aa"}))
    assert r.status is GateStatus.SKIP and r.detail == "rules_changed"


def test_eg5a_inputs가_바뀌면_skip(con: duckdb.DuckDBPyConnection) -> None:
    prev = _record("b1", {"stg_sample": "s1"}, "2:ff")
    r = gates.eg5a_reproducibility(_ctx(con, _rule(), previous=prev,
                                        inputs={"stg_sample": "s2"},
                                        partition_hashes={"whole": "2:aa"}))
    assert r.status is GateStatus.SKIP and r.detail == "inputs_changed"


# ── EG7 ──────────────────────────────────────────────────────────────────────
def test_eg7_임계이하는_pass(con: duckdb.DuckDBPyConnection) -> None:
    ctx = _ctx(con, _rule(), n_out=999, n_reject=1, reject_by_reason={"off_grid": 1},
               thresholds={"EG7": 0.01})
    r = gates.eg7_range(ctx)
    assert r.status is GateStatus.PASS and r.metrics["threshold"] == 0.01


def test_eg7_격리비율_초과는_fail(con: duckdb.DuckDBPyConnection) -> None:
    ctx = _ctx(con, _rule(), n_out=9, n_reject=1, reject_by_reason={"off_grid": 1})
    r = gates.eg7_range(ctx)
    assert r.status is GateStatus.FAIL
    assert r.metrics["reject_ratio"] == pytest.approx(0.1)
    assert r.metrics["threshold"] == gates.DEFAULT_THRESHOLDS["EG7"]


def test_eg7_임계는_baseline이_코드기본값을_덮는다(con: duckdb.DuckDBPyConnection) -> None:
    bl = Baseline({"t": {"thresholds": {"EG7": 0.5}}})
    ctx = _ctx(con, _rule(), n_out=9, n_reject=1, reject_by_reason={"off_grid": 1}, baseline=bl)
    assert gates.eg7_range(ctx).status is GateStatus.PASS


# ── 프레임 ────────────────────────────────────────────────────────────────────
def test_run_all_실행순서는_GATES_7_1(con: duckdb.DuckDBPyConnection) -> None:
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    names = [g.name for g in gates.run_all(_ctx(con, _rule(), fixtures=fx))]
    assert names == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG4", "EG5a"]


def test_앞_게이트_실패시_뒤는_skip_upstream_failed(con: duckdb.DuckDBPyConnection) -> None:
    rule = _rule(inputs=("stg_missing",))
    results = gates.run_all(_ctx(con, rule, inputs={"stg_missing": "b_nope"}))
    assert results[0].name == "EG0" and results[0].status is GateStatus.FAIL
    assert all(g.status is GateStatus.SKIP and g.detail == "upstream_failed"
               for g in results[1:])


def test_extra_gates_훅이_EG3_뒤에_붙는다(con: duckdb.DuckDBPyConnection) -> None:
    def eg6_version(ctx: EquityGateContext) -> GateResult:
        return GateResult("EG6", GateStatus.PASS, "판본 선택", {})

    eg6_version.gate_name = "EG6"   # pyright: ignore[reportFunctionMemberAccess]  # reason: 훅 규약
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    names = [g.name for g in gates.run_all(
        _ctx(con, _rule(extra_gates=(eg6_version,)), fixtures=fx))]
    assert names == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG6", "EG4", "EG5a"]


def test_없는_상수는_skip_no_baseline이고_metrics를_남긴다(con: duckdb.DuckDBPyConnection) -> None:
    def eg9_coverage(ctx: EquityGateContext) -> GateResult:
        gates.require_const(ctx, "coverage_min", {"measured_coverage": 0.97})
        return GateResult("EG9", GateStatus.PASS, "커버율", {})

    eg9_coverage.gate_name = "EG9"  # pyright: ignore[reportFunctionMemberAccess]  # reason: 훅 규약
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    results = gates.run_all(_ctx(con, _rule(extra_gates=(eg9_coverage,)), fixtures=fx))
    eg9 = next(g for g in results if g.name == "EG9")
    assert eg9.status is GateStatus.SKIP and eg9.detail == "no_baseline"
    assert eg9.metrics == {"missing_metric": "t.coverage_min", "measured_coverage": 0.97}


def test_skip_사유는_폐쇄_어휘다() -> None:
    with pytest.raises(ValueError, match="skip reason outside vocabulary"):
        SkipGate("그냥_건너뜀")
    assert "no_baseline" in gates.SKIP_REASONS and "upstream_failed" in gates.SKIP_REASONS


def test_load_fixtures는_equity_root_폴백을_받는다(tmp_path: Path) -> None:
    import json

    assert gates.load_fixtures("nowhere", tmp_path) is None
    p = tmp_path / "fixtures" / "t.json"
    p.parent.mkdir()
    p.write_text(json.dumps([{"case": "c", "key": {}, "column": "k", "expect": "1",
                              "source": "hand"}]), encoding="utf-8")
    fx = gates.load_fixtures("t", tmp_path)
    assert fx is not None and fx[0]["case"] == "c"
