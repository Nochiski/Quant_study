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


# ── EG13 미래 공개일 (K1-3a · T-12: 기준일 = 입력 stage 스냅샷의 KST 날짜) ─────────────
# `_OK_SQL` 의 공개일은 2020-01-03·2020-01-06 이다. 스냅샷 id 는 UTC 라 KST 날짜로 바꿔 견준다.
SNAP_0106_KST = "snap_20200106T000000Z"          # 2020-01-06 09:00 KST


def _snap_pinned(tmp_path: Path, make_stage_tree, snapshot_id: str = SNAP_0106_KST,
                 table: str = "stg_sample") -> dict[str, inputs.PinnedBuild]:
    """stage 판 1개를 `snapshot_id` 로 지어 `_pinned/` 에 고정한다 — EG13 의 기준일 출처."""
    tree = make_stage_tree(tmp_path, table, ROWS, snapshot_id=snapshot_id)
    return {table: inputs.pin(tree.stage_root, tmp_path / "equity", table)}


def _eg13(con: duckdb.DuckDBPyConnection, rule: EquityTable,
          pinned: dict[str, inputs.PinnedBuild]) -> GateResult:
    return gates.eg13_available_future(_ctx(
        con, rule, pinned=pinned, inputs={t: pb.build_id for t, pb in pinned.items()}))


def test_eg13_공개일이_스냅샷_KST_날짜_이하면_pass(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                          make_stage_tree) -> None:
    r = _eg13(con, _rule(), _snap_pinned(tmp_path, make_stage_tree))
    assert r.status is GateStatus.PASS, r.detail
    assert r.metrics["snapshot_kst_date"] == "2020-01-06"
    assert r.metrics["n_available_after_snapshot"] == 0
    assert r.metrics["input_snapshot_ids"] == [SNAP_0106_KST]


def test_eg13_음성대조_미래_공개일_한_행이면_fail(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                         make_stage_tree) -> None:
    """정상 픽스처에 스냅샷 다음 날 공개일 행 하나를 심는다 — 그 판의 원장에 있을 수 없는 행."""
    _out(con, f"{_OK_SQL} UNION ALL SELECT 3, 'c', DATE '2020-01-07', 'measured'")
    r = _eg13(con, _rule(), _snap_pinned(tmp_path, make_stage_tree))
    assert r.status is GateStatus.FAIL
    assert r.metrics["n_available_after_snapshot"] == 1
    assert r.metrics["max_available_date"] == "2020-01-07"
    assert "n_available_after_snapshot=1" in r.detail and "2020-01-06" in r.detail


def test_eg13_기준일은_UTC가_아니라_KST_날짜다(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                       make_stage_tree) -> None:
    """아침 체인 스냅샷(06:00 KST)은 UTC 로 전날이다 — UTC 날짜로 자르면 그날 행이 미래로 잡힌다."""
    _out(con, f"{_OK_SQL} UNION ALL SELECT 3, 'c', DATE '2020-01-07', 'measured'")
    pinned = _snap_pinned(tmp_path, make_stage_tree, "snap_20200106T210000Z")
    r = _eg13(con, _rule(), pinned)                  # 2020-01-07 06:00 KST
    assert r.status is GateStatus.PASS, r.detail
    assert r.metrics["snapshot_kst_date"] == "2020-01-07"


def test_eg13_입력_스냅샷이_여럿이면_가장_늦은_날짜가_기준(
        con: duckdb.DuckDBPyConnection, tmp_path: Path, make_stage_tree) -> None:
    """한 표가 오늘 실패해 어제 판에 머문 stage 입력과 오늘 판을 함께 읽을 수 있다. 판이 알 수 있는
    상한은 가장 늦은 스냅샷이다 — 가장 이른 것을 쓰면 오늘 판 행을 미래로 잘못 버린다."""
    _out(con, f"{_OK_SQL} UNION ALL SELECT 3, 'c', DATE '2020-01-07', 'measured'")
    pinned = {**_snap_pinned(tmp_path, make_stage_tree, SNAP_0106_KST, "stg_a"),
              **_snap_pinned(tmp_path, make_stage_tree, "snap_20200107T000000Z", "stg_b")}
    r = _eg13(con, _rule(inputs=("stg_a", "stg_b")), pinned)
    assert r.status is GateStatus.PASS, r.detail
    assert r.metrics["snapshot_kst_date"] == "2020-01-07"
    assert r.metrics["input_snapshot_ids"] == [SNAP_0106_KST, "snap_20200107T000000Z"]


def test_eg13_equity_내부_입력은_그_판의_stage_스냅샷까지_따라간다(
        con: duckdb.DuckDBPyConnection, tmp_path: Path, make_stage_tree) -> None:
    """S23 `price_adj_daily` 처럼 stage 입력을 직접 갖지 않는 표도 기준일이 있어야 한다."""
    from equity import build, rules_sample

    snap = "snap_20210104T000000Z"                   # ROWS 최대 공개일 2021-01-04 와 같은 날
    tree = make_stage_tree(tmp_path, "stg_sample", ROWS, snapshot_id=snap)
    eq = tmp_path / "equity"
    (eq / "fixtures").mkdir(parents=True)
    (eq / "fixtures" / "sample_table.json").write_text(
        '[{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]',
        encoding="utf-8")
    up = build.build_table(rules_sample.SAMPLE_TABLE, tree.stage_root, eq, Baseline(),
                           build_id="b_up_1")
    assert up.ok, [(g.name, g.status.value, g.detail) for g in up.gates]
    pinned = {"sample_table": inputs.pin(tree.stage_root, eq, "sample_table")}
    _out(con, f"{_OK_SQL} UNION ALL SELECT 3, 'c', DATE '2021-01-05', 'measured'")
    r = _eg13(con, _rule(inputs=("sample_table",)), pinned)
    assert r.status is GateStatus.FAIL and r.metrics["n_available_after_snapshot"] == 1
    assert r.metrics["input_snapshot_ids"] == [snap]
    assert r.metrics["snapshot_kst_date"] == "2021-01-04"


def test_eg13_스냅샷_id를_해석할_수_없으면_fail(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                         make_stage_tree) -> None:
    """기준일이 모호하면 통과로 두지 않는다(P1) — skip 은 통과로 집계된다(K1-7)."""
    r = _eg13(con, _rule(), _snap_pinned(tmp_path, make_stage_tree, "snap_test"))
    assert r.status is GateStatus.FAIL
    assert r.metrics["unparsed_snapshot_ids"] == {"stg_sample@b_stage_0001": "snap_test"}


def test_eg13_입력이_없으면_기준일이_없어_fail(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg13_available_future(_ctx(con, _rule()))
    assert r.status is GateStatus.FAIL and r.metrics["input_snapshot_ids"] == []


def test_eg13_upstream_고정이_사라졌으면_fail(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                       make_stage_tree) -> None:
    """equity 내부 입력의 stage 고정본을 GC 가 지웠으면 기준일을 못 구한다 — 예외 대신 FAIL."""
    pb = inputs.PinnedBuild("sample_table", "b_up_1", tmp_path / "equity" / "_pinned" /
                            "sample_table" / "v=b_up_1", (), {},
                            inputs={"stg_sample": "b_gone"})
    r = _eg13(con, _rule(inputs=("sample_table",)), {"sample_table": pb})
    assert r.status is GateStatus.FAIL and "stg_sample" in r.detail


def test_eg13_공개일_NULL_행은_EG2_규약을_따라_세지_않는다(
        con: duckdb.DuckDBPyConnection, tmp_path: Path, make_stage_tree) -> None:
    """NULL 은 EG2-P01 이 판정한다(basis='unknown' 일 때만 허용).
    EG13 은 미래값만 세고 NULL 수는 남긴다."""
    _out(con, f"{_OK_SQL} UNION ALL SELECT 3, 'c', NULL::DATE, 'unknown'")
    r = _eg13(con, _rule(available_basis=()), _snap_pinned(tmp_path, make_stage_tree))
    assert r.status is GateStatus.PASS, r.detail
    assert r.metrics["n_available_null"] == 1


def test_eg13_차원테이블은_skip(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg13_available_future(_ctx(con, _rule(available_rule=AVAILABLE_NONE)))
    assert r.status is GateStatus.SKIP and r.detail == "dimension_table"


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
def _pinned_kw(tmp_path: Path, make_stage_tree) -> dict[str, object]:
    """EG13 기준일이 서는 고정 입력 — 없으면 EG13 이 FAIL 해 뒤 게이트가 upstream_failed 로
    빠지고 순서만 맞는 항진 테스트가 된다."""
    pinned = _snap_pinned(tmp_path, make_stage_tree)
    return {"pinned": pinned, "inputs": {t: pb.build_id for t, pb in pinned.items()}}


def test_run_all_실행순서는_GATES_7_1(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                  make_stage_tree) -> None:
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    results = gates.run_all(_ctx(con, _rule(inputs=("stg_sample",)), fixtures=fx,
                                 **_pinned_kw(tmp_path, make_stage_tree)))
    assert [g.name for g in results] == ["EG0", "EG7", "EG1", "EG2", "EG13", "EG3", "EG4", "EG5a"]
    assert not [g.name for g in results if g.detail == "upstream_failed"]   # 전부 실제로 돌았다


def test_앞_게이트_실패시_뒤는_skip_upstream_failed(con: duckdb.DuckDBPyConnection) -> None:
    rule = _rule(inputs=("stg_missing",))
    results = gates.run_all(_ctx(con, rule, inputs={"stg_missing": "b_nope"}))
    assert results[0].name == "EG0" and results[0].status is GateStatus.FAIL
    assert all(g.status is GateStatus.SKIP and g.detail == "upstream_failed"
               for g in results[1:])


def test_extra_gates_훅이_EG3_뒤에_붙는다(con: duckdb.DuckDBPyConnection, tmp_path: Path,
                                    make_stage_tree) -> None:
    def eg6_version(ctx: EquityGateContext) -> GateResult:
        return GateResult("EG6", GateStatus.PASS, "판본 선택", {})

    eg6_version.gate_name = "EG6"   # pyright: ignore[reportFunctionMemberAccess]  # reason: 훅 규약
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    results = gates.run_all(_ctx(con, _rule(inputs=("stg_sample",), extra_gates=(eg6_version,)),
                                 fixtures=fx, **_pinned_kw(tmp_path, make_stage_tree)))
    assert [g.name for g in results] == ["EG0", "EG7", "EG1", "EG2", "EG13", "EG3", "EG6", "EG4",
                                         "EG5a"]
    eg6 = next(g for g in results if g.name == "EG6")
    assert eg6.status is GateStatus.PASS and eg6.detail == "판본 선택"    # 훅이 실제로 돌았다


def test_없는_상수는_skip_no_baseline_이지만_허용표_밖이라_FAIL이고_metrics를_남긴다(
        con: duckdb.DuckDBPyConnection, tmp_path: Path, make_stage_tree) -> None:
    """K1-7a: 게이트는 SKIP(no_baseline) 을 내지만 층 판정은 FAIL 이다.
    뒤 게이트는 upstream_failed."""
    def eg9_coverage(ctx: EquityGateContext) -> GateResult:
        gates.require_const(ctx, "coverage_min", {"measured_coverage": 0.97})
        return GateResult("EG9", GateStatus.PASS, "커버율", {})

    eg9_coverage.gate_name = "EG9"  # pyright: ignore[reportFunctionMemberAccess]  # reason: 훅 규약
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    pinned = _snap_pinned(tmp_path, make_stage_tree)       # EG13 기준일 — 없으면 EG13 이 막는다
    results = gates.run_all(_ctx(con, _rule(inputs=("stg_sample",), extra_gates=(eg9_coverage,)),
                                 fixtures=fx, pinned=pinned,
                                 inputs={t: pb.build_id for t, pb in pinned.items()}))
    eg9 = next(g for g in results if g.name == "EG9")
    assert eg9.status is GateStatus.FAIL and "no_baseline" in eg9.detail
    assert eg9.metrics == {"missing_metric": "t.coverage_min", "measured_coverage": 0.97,
                           "skip_reason": "no_baseline", "skip_not_allowed": True}
    after = results[[g.name for g in results].index("EG9") + 1:]
    assert [(g.name, g.status, g.detail) for g in after] == [
        ("EG4", GateStatus.SKIP, "upstream_failed"), ("EG5a", GateStatus.SKIP, "upstream_failed")]


def test_허용표_안_SKIP_만_있으면_판정은_통과(con: duckdb.DuckDBPyConnection) -> None:
    """선언표 EG1 · 차원 표 EG2 · 첫 빌드 EG5a — 셋 다 SKIP 이고 FAIL 은 0 이다(K1-7a 시드)."""
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    rule = _rule(declaration_table=True, available_rule=AVAILABLE_NONE)
    got = {g.name: (g.status, g.detail) for g in gates.run_all(_ctx(con, rule, fixtures=fx))}
    assert got["EG1"] == (GateStatus.SKIP, "declaration_table")
    assert got["EG2"] == (GateStatus.SKIP, "dimension_table")
    assert got["EG5a"] == (GateStatus.SKIP, "no_previous_build")
    assert not [n for n, (s, _) in got.items() if s is GateStatus.FAIL]


def test_표_한정_허용은_그_표에서만_SKIP이다(con: duckdb.DuckDBPyConnection) -> None:
    """음성 대조 — EG21 no_coverage 는 opinion_daily 에만 허용이다. 같은 SKIP 이 다른 표면 FAIL."""
    fx = [{"case": "k1", "key": {"k": "1"}, "column": "val", "expect": "a", "source": "hand"}]
    base = Baseline({name: {"recent_grid_window": 3, "recent_grid_baseline_window": 20,
                            "recent_grid_row_ratio_min": 0.8, "recent_grid_lag_sessions": 0}
                     for name in ("opinion_daily", "price_daily")})

    def eg21(name: str) -> GateResult:
        rule = _rule(name=name, content_date_column="available_date",
                     extra_gates=(gates.eg21_recent_grid,))
        return next(g for g in gates.run_all(_ctx(con, rule, fixtures=fx, baseline=base))
                    if g.name == "EG21")

    assert eg21("opinion_daily").status is GateStatus.SKIP
    bad = eg21("price_daily")
    assert bad.status is GateStatus.FAIL and bad.metrics["skip_reason"] == "no_coverage"


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


# ── EG21 최신 구간 격자 행수 (09-19 감사 DEFECT-C06) ──────────────────────────

_EG21_CONSTS = {"recent_grid_window": 3, "recent_grid_baseline_window": 20,
                "recent_grid_row_ratio_min": 0.8, "recent_grid_lag_sessions": 0}


def _eg21_sql(counts: list[int]) -> str:
    """`counts[i]` 행을 가진 세션 i(오래된 것부터)로 합성 격자를 만든다."""
    vals = ", ".join(f"(DATE '2026-01-01' + {i}, {j})"
                     for i, n in enumerate(counts) for j in range(n))
    return f"SELECT * FROM (VALUES {vals}) AS t(date, k)"


def _eg21_ctx(con: duckdb.DuckDBPyConnection, counts: list[int],
              consts: dict[str, object] | None = None) -> EquityGateContext:
    _out(con, _eg21_sql(counts))
    rule = _rule(name="grid", columns={"date": "DATE", "k": "BIGINT"},
                 content_date_column="date")
    bl = Baseline({"grid": {**_EG21_CONSTS, **(consts or {})}})
    return _ctx(con, rule, baseline=bl)


def test_eg21_최신_세션_행수가_유지되면_pass(con: duckdb.DuckDBPyConnection) -> None:
    r = gates.eg21_recent_grid(_eg21_ctx(con, [1000] * 23))
    assert r.status is GateStatus.PASS
    assert r.metrics["n_thin_recent_sessions"] == 0
    assert r.metrics["baseline_median_rows"] == 1000


def test_eg21_최신_세션이_반쪽이면_fail(con: duckdb.DuckDBPyConnection) -> None:
    """EG5a 는 일일 운영에서 항상 skip(inputs_changed) 이고 EG5c 표본은 과거로 굳어 있다.
    격자 표에서 '어제 들어온 것이 반쯤 비었다' 를 보는 게이트가 이것 하나다(DEFECT-C06)."""
    r = gates.eg21_recent_grid(_eg21_ctx(con, [1000] * 20 + [1000, 1000, 500]))
    assert r.status is GateStatus.FAIL
    assert r.metrics["n_thin_recent_sessions"] == 1
    assert r.metrics["thin_sessions"] == ["2026-01-23"]
    assert "n_thin_recent_sessions" in r.detail


def test_eg21_lag_안쪽_세션은_판정하지_않는다(con: duckdb.DuckDBPyConnection) -> None:
    """신용잔고는 실입수가 T+3 이라 최신 3세션이 아직 안 찬 것이 정상이다(DEFECT-E01)."""
    counts = [1000] * 20 + [1000, 1000, 1000] + [1, 1, 1]
    assert gates.eg21_recent_grid(
        _eg21_ctx(con, counts, {"recent_grid_lag_sessions": 3})).status is GateStatus.PASS
    assert gates.eg21_recent_grid(
        _eg21_ctx(con, counts, {"recent_grid_lag_sessions": 0})).status is GateStatus.FAIL


def test_eg21_상수가_없으면_skip(con: duckdb.DuckDBPyConnection) -> None:
    _out(con, _eg21_sql([10] * 23))
    rule = _rule(name="grid", columns={"date": "DATE", "k": "BIGINT"},
                 content_date_column="date")
    with pytest.raises(SkipGate) as e:
        gates.eg21_recent_grid(_ctx(con, rule))
    assert e.value.reason == "no_baseline"


def test_eg21_세션이_모자라면_skip(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(SkipGate) as e:
        gates.eg21_recent_grid(_eg21_ctx(con, [10] * 5))
    assert e.value.reason == "no_coverage"


def test_eg21은_저녁_잠정_행을_판정에서_뺀다(con: duckdb.DuckDBPyConnection) -> None:
    """`basis` 열이 있는 표에서는 확정(krx) 행만 센다 — 저녁 T 세션은 커버가 구조적으로 작다."""
    base = _eg21_sql([1000] * 23)
    _out(con, f"SELECT date, k, 'krx' AS basis FROM ({base}) "
              "UNION ALL SELECT DATE '2026-01-24', 1, 'evening'")
    rule = _rule(name="grid", columns={"date": "DATE", "k": "BIGINT", "basis": "VARCHAR"},
                 content_date_column="date")
    r = gates.eg21_recent_grid(_ctx(con, rule, baseline=Baseline({"grid": _EG21_CONSTS})))
    assert r.status is GateStatus.PASS
    assert r.metrics["recent_sessions"][0]["date"] == "2026-01-23"
