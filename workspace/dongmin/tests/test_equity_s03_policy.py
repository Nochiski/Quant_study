"""S03·S03B `universe_policy` — 선언표 스키마 + all · common-stock · investable 7행
(DESIGN §4-1 · GATES §3 ㉒ · §4 FX-1-017).

행수 등식이 없는 선언표라 EG1 은 `skip(declaration_table)` 이고, 대신 EG3_policy 가 어휘·문법·
'all' 존재·임계 정합·predicate 바인딩(입력 `universe_daily` 스키마 위)을 본다. 입력이 equity
산출이므로 절단본 위에 S01·S02·S04·S03 상류를 먼저 빌드한다. `liquid` 행은 adv20 분위수 실측 뒤.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s03, rules_s04
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = Baseline({**load(rules_s01.BASELINE_SEED).data,
                 **load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data,
                 **load(rules_s03.BASELINE_SEED).data})
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s04.PRICE_DAILY, rules_s03.UNIVERSE_DAILY)
POLICY = rules_s03.UNIVERSE_POLICY
VERSION = "s03b-v2"

_ROWS = [
    ("krx.all", "all", 1, "TRUE", "flag", None, "convention", None, VERSION),
    ("krx.common-stock", "common-stock", 1, "sec_type = 'common'", "flag", None, "convention",
     None, VERSION),
    ("krx.common-stock", "common-stock", 2, "status = 'listed'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 1, "sec_type = 'common'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 2, "status = 'listed'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 3, "NOT admin_state", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 4, "NOT liquidation_window", "flag", None, "convention",
     None, VERSION),
]


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{out_dir / '*.parquet'}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _fails(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is GateStatus.FAIL]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s03p") / "equity"
    for rule in UPSTREAM:
        r = build.build_table(rule, STAGE_SLICE, root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, _fails(r)
    return build.build_table(POLICY, STAGE_SLICE, root, SEED, build_id="b_s03_up")


def _variant(built: build.BuildResult, tmp_path: Path, name: str, values: str,
             universe_id: str = "'krx.' || p.policy") -> build.BuildResult:
    """VALUES 목록만 바꾼 변종을 같은 equity_root 에 빌드한다(상수는 변종 이름으로 재등재)."""
    assert built.out_dir is not None
    p = tmp_path / f"{name}.sql"
    p.write_text(f"""
        SELECT {universe_id} AS universe_id, p.policy, CAST(p.rule_seq AS BIGINT) AS rule_seq,
               p.predicate, p.threshold_kind, p.threshold_value, p.basis, p.measured_at,
               k.version, NULL::VARCHAR AS reject_reason
        FROM (VALUES {values}) AS p(policy, rule_seq, predicate, threshold_kind,
                                    threshold_value, basis, measured_at)
        CROSS JOIN _const k""", encoding="utf-8")
    rule = EquityTable(**{**POLICY.__dict__, "name": name, "sql_path": p})
    bl = Baseline({**SEED.data, name: SEED.table(POLICY.name)})
    return build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], bl,
                             build_id=f"b_{name}")


ALL = "('all', 1, 'TRUE', 'flag', NULL::DOUBLE, 'convention', NULL::DATE)"


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_빌드가_통과하고_EG1은_declaration_table_skip(built: build.BuildResult) -> None:
    assert built.ok, _fails(built)
    assert {g.name: (g.status.value, g.detail) for g in built.gates
            if g.status is not GateStatus.PASS} == {
        "EG1": ("skip", "declaration_table"), "EG2": ("skip", "dimension_table"),
        "EG5a": ("skip", "no_previous_build")}
    assert _gate(built, "EG1").metrics == {"n_out": len(_ROWS), "n_reject": 0}
    assert built.inputs == {"universe_daily": "b_universe_daily"}


def test_표는_all_common_stock_investable_7행이고_전부_flag(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT * FROM t ORDER BY policy, rule_seq") == _ROWS
    assert [str(r[0]) for r in _query(built.out_dir, "DESCRIBE t")] == list(POLICY.columns)
    assert _query(built.out_dir, "SELECT count(*) FROM t WHERE threshold_kind <> 'flag' "
                                 "OR threshold_value IS NOT NULL OR measured_at IS NOT NULL") == [
        (0,)]
    # liquid 는 어휘만 — 임계는 서버 실측 뒤
    assert _query(built.out_dir, "SELECT count(*) FROM t WHERE policy = 'liquid'") == [(0,)]
    assert "liquid" in rules_s03.POLICY_VOCAB


def test_universe_id는_소비자_계약_어휘와_같다(built: build.BuildResult) -> None:
    """FIELD_MAP §1 — `krx.common-stock` 이 정책표로 풀린다(하이픈 포함 policy 값 =
    'krx.' || policy)."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT DISTINCT universe_id FROM t ORDER BY 1") == [
        ("krx.all",), ("krx.common-stock",), ("krx.investable",)]


def test_predicate가_universe_daily_스키마에서_평가된다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_policy").metrics
    assert m["n_predicate_unbound"] == 0 and m["unbound_predicates"] == []
    assert m["policy_counts"] == {"all": 1, "common-stock": 2, "investable": 4}


def test_정책을_universe_daily에_적용하면_포함_관계가_선다(built: build.BuildResult) -> None:
    """all ⊇ common-stock ⊇ investable. 절단본: common 주식 행 중 suspended 327·ETF 4,094·우선주·
    관리·정리매매 행이 차례로 빠진다(적용은 팩터층 몫이지만 술어가 뜻대로 도는지는 여기서 본다)."""
    assert built.out_dir is not None
    root = built.out_dir.parents[1]
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW u AS SELECT * FROM read_parquet("
                    f"'{root / 'universe_daily' / 'v=b_universe_daily' / 'year=*' / '*.parquet'}', "
                    "hive_partitioning=false)")
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{built.out_dir / '*.parquet'}')")
        preds = {p: " AND ".join(f"({x})" for x in [r[0] for r in con.execute(
            f"SELECT predicate FROM t WHERE policy = '{p}' ORDER BY rule_seq").fetchall()])
            for p in ("all", "common-stock", "investable")}
        n = {p: con.execute(f"SELECT count(*) FROM u WHERE {w}").fetchone()[0]  # type: ignore[index]
             for p, w in preds.items()}
        n_common_listed = con.execute("SELECT count(*) FROM u WHERE sec_type = 'common' "
                                      "AND status = 'listed'").fetchone()[0]  # type: ignore[index]
        n_inv = con.execute("SELECT count(*) FROM u WHERE sec_type = 'common' "
                            "AND status = 'listed' AND NOT admin_state "
                            "AND NOT liquidation_window").fetchone()[0]  # type: ignore[index]
    finally:
        con.close()
    assert n["all"] == 41066
    assert n["all"] > n["common-stock"] == n_common_listed > n["investable"] == n_inv


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_없는_컬럼을_쓰는_predicate는_EG3_policy가_폐기한다(built: build.BuildResult,
                                                     tmp_path: Path) -> None:
    """아직 없는 컬럼(turnover_pct)을 predicate 에 쓰면 바인딩 실패 = 착수 순서 위반."""
    r = _variant(built, tmp_path, "policy_unbound",
                 f"{ALL}, ('liquid', 1, 'turnover_pct >= 0.2', 'quantile', 0.2, 'measured', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_predicate_unbound"] == 1
    assert eg3.metrics["unbound_predicates"] == ["liquid#1"]
    assert _gate(r, "EG3").status is GateStatus.PASS


def test_S03B_컬럼은_이제_바인딩된다(built: build.BuildResult, tmp_path: Path) -> None:
    """`adv20_krw` 위 quantile 행은 문법상 통과한다 — 임계 등재 자체는 서버 실측 뒤(여기선
    변종만)."""
    r = _variant(built, tmp_path, "policy_liquid_shape",
                 f"{ALL}, ('liquid', 1, 'adv20_krw >= 1000000', 'quantile', 0.2, 'measured', "
                 "DATE '2026-09-05')")
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.PASS and eg3.metrics["n_predicate_unbound"] == 0
    assert _gate(r, "EG4").status is GateStatus.FAIL          # 변종 이름엔 픽스처가 없다(정상)


def test_threshold_kind_어휘_밖이면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_bad_kind",
                 f"{ALL}, ('liquid', 1, 'halt_state = false', 'percentile', 0.2, 'measured', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_threshold_kind_outside_vocab"] == 1
    assert eg3.metrics["n_predicate_unbound"] == 0


def test_policy_어휘_밖이면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    """'common_stock'(밑줄) 은 어휘 밖 — universe_id 가 계약 값 'krx.common-stock' 과 달라진다."""
    r = _variant(built, tmp_path, "policy_bad_policy",
                 f"{ALL}, ('common_stock', 1, 'sec_type = ''common''', 'flag', NULL::DOUBLE, "
                 "'convention', NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_policy_outside_vocab"] == 1


def test_flag인데_임계값이_있으면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_flag_value",
                 "('all', 1, 'TRUE', 'flag', 1.0, 'convention', NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_threshold_value_mismatch"] == 1


def test_all_행이_없으면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    """`krx.all` 이 비면 정책 미적용 유니버스 자체가 사라진다."""
    r = _variant(built, tmp_path, "policy_no_all",
                 "('investable', 1, 'NOT halt_state', 'flag', NULL::DOUBLE, 'convention', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.metrics["n_all_missing"] == 1 and eg3.metrics["n_predicate_unbound"] == 0


def test_universe_id_문법이_어긋나면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_bad_id", ALL, universe_id="'krx_' || p.policy")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_universe_id_bad_grammar"] == 1
