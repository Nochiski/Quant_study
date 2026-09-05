"""S01 `corp_ticker` — 절단본 실빌드 · isin8 그룹 규칙 · 부정 픽스처.

핵심 위험은 두 가지다. ① 우선주가 본주와 안 묶이면 기업 시총이 종류주 수만큼 빠진다.
② 비KR7 을 isin8 로 묶으면 서로 다른 발행사가 한 법인으로 합쳐진다(SPEC §2-17 HK000005).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01
from equity.baseline import load as baseline_load
from equity.gates import GateStatus

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
CORP_TICKER = rules_s01.CORP_TICKER


def _baseline():
    return baseline_load(rules_s01.BASELINE_SEED)


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{out_dir / '*.parquet'}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _fail_names(result: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in result.gates if g.status is GateStatus.FAIL]


@pytest.fixture
def built(tmp_path: Path) -> build.BuildResult:
    return build.build_table(CORP_TICKER, STAGE_SLICE, tmp_path / "equity", _baseline(),
                             build_id="b_ct_1")


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────
def test_절단본_왕복이_ok이고_게이트는_pass나_skip뿐(built: build.BuildResult) -> None:
    assert built.ok, _fail_names(built)
    assert {g.name: g.status.value for g in built.gates} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "skip", "EG3": "pass",
        "EG3_corp_ticker": "pass", "EG4": "pass", "EG5a": "skip"}
    assert built.n_rows == 15 and built.n_reject == 0     # security 와 같은 모집단


def test_EG1_우변은_security와_같은_모집단(built: build.BuildResult) -> None:
    eg1 = next(g for g in built.gates if g.name == "EG1")
    assert eg1.metrics["rhs"] == 15
    assert eg1.metrics["rhs_sql"] == rules_s01.SECURITY.eg1_rhs_sql


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert [str(r[0]) for r in _query(built.out_dir, "DESCRIBE t")] == list(CORP_TICKER.columns)


# ── isin8 그룹 손계산 ─────────────────────────────────────────────────────────
def test_KR7_1대N_그룹은_보통주_1개로_묶인다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir,
                  "SELECT ticker, isin8, corp_code, common_ticker, is_common, link_basis "
                  "FROM t WHERE isin8 = 'KR700354' ORDER BY 1")
    # 대신증권 본주 003540 · 구형우선주 003545 · 신형우선주 003547
    assert rows == [
        ("003540", "KR700354", "00110893", "003540", True, "isin8"),
        ("003545", "KR700354", "00110893", "003540", False, "isin8"),
        ("003547", "KR700354", "00110893", "003540", False, "isin8")]


def test_삼성전자_본주_우선주가_같은_isin8(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, isin8, corp_code, common_ticker FROM t "
                                 "WHERE ticker IN ('005930', '005935') ORDER BY 1")
    assert rows == [("005930", "KR700593", "00126380", "005930"),
                    ("005935", "KR700593", "00126380", "005930")]


def test_비KR7은_isin8이_같아도_단독_법인이다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir,
                  "SELECT ticker, isin8, corp_code, common_ticker, is_common, link_basis "
                  "FROM t WHERE isin8 = 'HK000005' ORDER BY 1")
    # 900050·900060 은 isin8 이 같지만 발행사가 다르다 — 묶으면 과합병
    assert rows == [("900050", "HK000005", "00722500", None, False, "corp_map"),
                    ("900060", "HK000005", "00694003", None, False, "corp_map")]
    assert rows[0][2] != rows[1][2]


def test_ETF는_isin8도_corp_code도_없다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT isin8, corp_code, common_ticker, is_common, link_basis "
                                 "FROM t WHERE ticker = '069500'")
    assert rows == [(None, None, None, False, "none")]


def test_문자_isin8도_그대로_묶인다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT isin8, corp_code, is_common, link_basis FROM t "
                                 "WHERE ticker = '0001A0'")
    assert rows == [("KR70001A", "01516933", True, "isin8")]


def test_KR7_그룹당_보통주는_정확히_1개(built: build.BuildResult) -> None:
    gate = next(g for g in built.gates if g.name == "EG3_corp_ticker")
    assert gate.metrics["n_kr7_group_common_not_one"] == 0
    assert gate.metrics["link_basis_counts"] == {"isin8": 12, "corp_map": 2, "none": 1}
    assert gate.metrics["map_rate"] == 1.0                 # KR7 12티커 전건 매핑


# ── security 와의 정의 일치 (중복 술어의 stale 방어) ──────────────────────────
def test_is_common은_security_sec_type_common과_일치한다(tmp_path: Path) -> None:
    """`is_common` 술어는 security.sql 의 common 분기와 같은 규칙을 다시 쓴 것이다.

    equity 산출은 다른 equity 테이블의 입력이 될 수 없어(빌더가 stage 만 고정한다) 복제가
    불가피하다 — 두 정의가 벌어지면 여기서 잡는다.
    """
    eq = tmp_path / "equity"
    sec = build.build_table(rules_s01.SECURITY, STAGE_SLICE, eq, _baseline(), build_id="b_s")
    ct = build.build_table(CORP_TICKER, STAGE_SLICE, eq, _baseline(), build_id="b_c")
    assert sec.ok and ct.ok, _fail_names(sec) + _fail_names(ct)
    assert sec.out_dir is not None and ct.out_dir is not None
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet("
                    f"'{sec.out_dir / '*.parquet'}', hive_partitioning=false)")
        con.execute(f"CREATE VIEW c AS SELECT * FROM read_parquet("
                    f"'{ct.out_dir / '*.parquet'}', hive_partitioning=false)")
        rows = con.execute("SELECT s.ticker, s.sec_type, c.is_common FROM s JOIN c USING (ticker) "
                           "WHERE (s.sec_type = 'common') <> c.is_common").fetchall()
    finally:
        con.close()
    assert rows == []


# ── 부정 픽스처 — 한 KR7 isin8 에 보통주 2개면 EG3_corp_ticker 가 폐기시킨다 ───
def _fake_stage(tmp_path: Path, make_stage_tree, isin_b: str) -> Path:
    make_stage_tree(tmp_path, "stg_listing_daily", [
        {"ticker": "A00011", "date": date(2026, 8, 20), "isin": "KR7000110000",
         "name": "가나전자", "secugrp": "주권", "sect_tp": "", "stkcert_tp": "보통주"},
        {"ticker": "A00012", "date": date(2026, 8, 20), "isin": isin_b,
         "name": "가나전자우", "secugrp": "주권", "sect_tp": "", "stkcert_tp": "보통주"}])
    make_stage_tree(tmp_path, "stg_etf_price_daily",
                    [{"ticker": "E00001", "date": date(2026, 8, 20)}])
    make_stage_tree(tmp_path, "stg_corp_map",
                    [{"ticker": "A00011", "corp_code": "00000001"}])
    return tmp_path / "stage"


def _fixtures(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    p = tmp_path / "fx.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return p


def test_같은_KR7_isin8에_보통주_2개면_EG3_corp_ticker_FAIL(tmp_path: Path,
                                                          make_stage_tree) -> None:
    # isin 뒤 4자리만 다르고 앞 8자가 같다 → 한 그룹에 보통주 2개
    stage_root = _fake_stage(tmp_path, make_stage_tree, "KR7000110001")
    fx = _fixtures(tmp_path, [{"case": "neg", "key": {"ticker": "A00011"}, "column": "isin8",
                               "expect": "KR700011", "source": "hand"}])
    r = build.build_table(CORP_TICKER, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_neg_ct", fixtures_path=fx)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert next(g for g in r.gates if g.name == "EG1").status is GateStatus.PASS
    bad = next(g for g in r.gates if g.name == "EG3_corp_ticker")
    assert bad.status is GateStatus.FAIL
    assert bad.metrics["n_kr7_group_common_not_one"] == 1
    assert next(g for g in r.gates if g.name == "EG4").detail == "upstream_failed"
    assert not (tmp_path / "equity" / "corp_ticker" / "v=b_neg_ct").exists()


def test_isin8이_다르면_같은_보통주_2종목도_통과한다(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _fake_stage(tmp_path, make_stage_tree, "KR7000120000")
    fx = _fixtures(tmp_path, [{"case": "pos", "key": {"ticker": "A00012"}, "column": "isin8",
                               "expect": "KR700012", "source": "hand"}])
    r = build.build_table(CORP_TICKER, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_pos_ct", fixtures_path=fx)
    assert r.ok, _fail_names(r)
    assert r.out_dir is not None
    rows = _query(r.out_dir, "SELECT ticker, isin8, corp_code, link_basis FROM t ORDER BY 1")
    # A00012 는 corp_map 매핑이 없어 corp_code NULL · link_basis 'none'
    assert rows == [("A00011", "KR700011", "00000001", "isin8"),
                    ("A00012", "KR700012", None, "none"),
                    ("E00001", None, None, "none")]
