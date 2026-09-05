"""S01 `corp` — stage 절단본 위 실빌드 + 손계산 대조 + 부정 픽스처.

절단본(`tests/fixtures/stage_slice/`)은 서버 current_build 에서 잘라낸 실물이다. 여기서
`build_table` 을 그대로 돌려 EG0~EG5a 전량이 pass/skip 인지 본다.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01
from equity.baseline import Baseline
from equity.baseline import load as baseline_load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
CORP = rules_s01.CORP


def _baseline() -> Baseline:
    return baseline_load(rules_s01.BASELINE_SEED)


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        # hive_partitioning=false — 최종 경로의 `v=<build_id>` 가 컬럼으로 붙는 것을 막는다
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{out_dir / '*.parquet'}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _fail_names(result: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in result.gates if g.status is GateStatus.FAIL]


@pytest.fixture
def built(tmp_path: Path) -> build.BuildResult:
    return build.build_table(CORP, STAGE_SLICE, tmp_path / "equity", _baseline(),
                             build_id="b_corp_1")


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────
def test_절단본_왕복이_ok이고_게이트는_pass나_skip뿐(built: build.BuildResult) -> None:
    assert built.ok, _fail_names(built)
    assert [g.name for g in built.gates] == [
        "EG0", "EG7", "EG1", "EG2", "EG3", "EG3_corp", "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "skip", "EG3": "pass",
        "EG3_corp": "pass", "EG4": "pass", "EG5a": "skip"}
    assert built.n_rows == 11 and built.n_reject == 0     # 손계산: stg_corp_map 11행
    assert built.out_dir is not None and (built.out_dir / "part0.parquet").exists()


def test_EG1_우변은_corp_map_distinct_corp_code(built: build.BuildResult) -> None:
    eg1 = next(g for g in built.gates if g.name == "EG1")
    assert eg1.metrics["lhs"] == 11 and eg1.metrics["rhs"] == 11
    assert eg1.metrics["n_reject"] == 0


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    got = [str(r[0]) for r in _query(built.out_dir, "DESCRIBE t")]
    assert got == list(CORP.columns)


# ── 손계산 (절단본 원자료 직접 조회로 확인한 값) ───────────────────────────────
def test_fiscal_month와_basis_손계산(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT corp_code, fiscal_month, fiscal_month_basis FROM t "
                                 "WHERE corp_code IN ('00694003', '00126380') ORDER BY 1")
    # stg_company.acc_mt: 00126380='12' · 00694003='03'(앞 0 소실 확인축)
    assert rows == [("00126380", 12, "current_snapshot"), ("00694003", 3, "current_snapshot")]


def test_induty_class는_KSIC_앞2자리_64_65_66만_financial(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT corp_code, induty_code, induty_class FROM t ORDER BY 1")
    got = {str(c): (str(code), str(cls)) for c, code, cls in rows}
    assert got["00110893"] == ("66121", "financial")        # 대신증권
    assert got["00254045"] == ("64121", "financial")        # 우리은행
    assert got["00694003"] == ("64992", "financial")        # 중국식품포장
    assert got["00126380"] == ("264", "nonfinancial")       # 삼성전자 — 3자리 코드
    assert got["01516933"] == ("352", "nonfinancial")       # 덕양에너젠
    assert sum(1 for _, cls in got.values() if cls == "financial") == 4


def test_corp_code가_grain이고_중복이_없다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert CORP.grain == ("corp_code",)
    n_dup = _query(built.out_dir, "SELECT count(*) FROM (SELECT corp_code, count(*) c "
                                  "FROM t GROUP BY 1 HAVING c > 1)")[0][0]
    assert n_dup == 0


def test_차원_테이블이라_EG2는_skip된다(built: build.BuildResult) -> None:
    assert not CORP.is_fact
    eg2 = next(g for g in built.gates if g.name == "EG2")
    assert eg2.status is GateStatus.SKIP and eg2.detail == "dimension_table"


# ── 상수 ──────────────────────────────────────────────────────────────────────
def test_seed_baseline이_선언한_상수를_전부_담는다() -> None:
    bl = _baseline()
    for rule in rules_s01.TABLES:
        for metric in rule.consts:
            assert bl.get(rule.name, metric) is not None, f"{rule.name}.{metric}"


def test_상수_미등재면_빌드가_거절된다(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="baseline constant not found"):
        build.build_table(CORP, STAGE_SLICE, tmp_path / "equity", Baseline())


# ── 부정 픽스처 — induty_code 공란이면 EG3_corp 가 폐기시킨다 ──────────────────
def _fake_stage(tmp_path: Path, make_stage_tree, induty: str) -> Path:
    make_stage_tree(tmp_path, "stg_corp_map",
                    [{"corp_code": "00000001", "ticker": "A00001", "corp_name_current": "가"},
                     {"corp_code": "00000002", "ticker": "A00002", "corp_name_current": "나"}])
    make_stage_tree(tmp_path, "stg_company", [
        {"corp_code": "00000001", "acc_mt": "12", "induty_code_current": "264",
         "observed_date": "2026-08-26"},
        {"corp_code": "00000002", "acc_mt": "12", "induty_code_current": induty,
         "observed_date": "2026-08-26"}])
    return tmp_path / "stage"


def _fixtures(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    p = tmp_path / "fx.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return p


def test_induty_code_공란은_EG3_corp_FAIL(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _fake_stage(tmp_path, make_stage_tree, "")
    fx = _fixtures(tmp_path, [{"case": "neg", "key": {"corp_code": "00000001"},
                               "column": "induty_class", "expect": "nonfinancial",
                               "source": "hand"}])
    r = build.build_table(CORP, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_neg", fixtures_path=fx)
    assert r.status is build.BuildStatus.GATE_FAILED
    bad = next(g for g in r.gates if g.name == "EG3_corp")
    assert bad.status is GateStatus.FAIL and bad.metrics["n_induty_code_blank"] == 1
    assert next(g for g in r.gates if g.name == "EG4").detail == "upstream_failed"
    assert not (tmp_path / "equity" / "corp" / "v=b_neg").exists()


def test_induty_class_어휘_밖이면_EG3_corp_FAIL(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _fake_stage(tmp_path, make_stage_tree, "264")
    broken = tmp_path / "corp_broken.sql"
    broken.write_text(
        "SELECT corp_code, min(corp_name_current) AS corp_name, 1::INTEGER AS fiscal_month, "
        "'current_snapshot' AS fiscal_month_basis, '264' AS induty_code, "
        "'utility' AS induty_class, NULL::VARCHAR AS reject_reason "
        "FROM stg_corp_map GROUP BY corp_code", encoding="utf-8")
    rule = EquityTable(**{**CORP.__dict__, "sql_path": broken})
    r = build.build_table(rule, stage_root, tmp_path / "equity", _baseline(), build_id="b_neg2")
    assert r.status is build.BuildStatus.GATE_FAILED
    bad = next(g for g in r.gates if g.name == "EG3_corp")
    assert bad.status is GateStatus.FAIL
    assert bad.metrics["n_induty_class_outside_vocab"] == 2
