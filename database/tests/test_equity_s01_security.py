"""S01 `security` — 절단본 실빌드 · sec_type 어휘 전수 · 폐지 두 축 · 부정 픽스처.

`sec_type` 매핑은 P11 의 `secugrp × stkcert_tp` 전 이력 어휘를 덮어야 하는데 절단본에는
주권·외국주권 두 종류밖에 없다 → 어휘 전수는 `make_stage_tree` 로 만든 합성 stage 위에서
검사하고, 실물 대조(폐지·문자 티커·ETF)는 절단본 위에서 한다.
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
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SECURITY = rules_s01.SECURITY
BACKFILL_END = date(2026, 8, 20)          # baseline_seed_s01.json security.backfill_end


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
    return build.build_table(SECURITY, STAGE_SLICE, tmp_path / "equity", _baseline(),
                             build_id="b_sec_1")


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────
def test_절단본_왕복이_ok이고_게이트는_pass나_skip뿐(built: build.BuildResult) -> None:
    assert built.ok, _fail_names(built)
    assert {g.name: g.status.value for g in built.gates} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "skip", "EG3": "pass",
        "EG3_security": "pass", "EG4": "pass", "EG5a": "skip"}
    # 손계산: listing 14티커 + etf 1티커, 교집합 없음
    assert built.n_rows == 15 and built.n_reject == 0
    eg1 = next(g for g in built.gates if g.name == "EG1")
    assert eg1.metrics["lhs"] == 15 and eg1.metrics["rhs"] == 15


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert [str(r[0]) for r in _query(built.out_dir, "DESCRIBE t")] == list(SECURITY.columns)


def test_절단본_sec_type_분포_손계산(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    got = dict(_query(built.out_dir, "SELECT sec_type, count(*) FROM t GROUP BY 1 ORDER BY 1"))
    # 보통주 9(000030·0001A0·000660·003540·005930·036220·101970·161890·247540)
    # 우선주 3(003545 구형·003547 신형·005935 구형) · 외국주권 2(900050·900060) · ETF 1
    assert got == {"common": 9, "preferred": 3, "foreign": 2, "etf": 1}


def test_문자_티커_0001A0가_VARCHAR6로_남는다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, typeof(ticker), length(ticker), isin, sec_type "
                                 "FROM t WHERE ticker = '0001A0'")
    assert rows == [("0001A0", "VARCHAR", 6, "KR70001A0001", "common")]


def test_ETF는_list_date가_없고_basis가_unknown(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT sec_type, isin, list_date, list_date_basis, corp_code "
                                 "FROM t WHERE ticker = '069500'")
    assert rows == [("etf", None, None, "unknown", None)]


def test_최신_행_기준_이름과_상장일(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, name_current, list_date FROM t "
                                 "WHERE ticker IN ('036220', '101970', '000660') ORDER BY 1")
    # 036220 인포피아(2007-06-05) → 오상헬스케어(2024-03-13 재상장) · 101970 재상장 2025-03-28
    assert rows == [("000660", "에스케이하이닉스보통주", date(1996, 12, 26)),
                    ("036220", "오상헬스케어", date(2024, 3, 13)),
                    ("101970", "우양에이치씨", date(2025, 3, 28))]


# ── 폐지 두 축 ────────────────────────────────────────────────────────────────
def test_폐지일은_KRX_다음거래일이고_KIS와_대조된다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir,
                  "SELECT ticker, delist_date_krx, delist_date_kis, delist_conflict, "
                  "delist_date, delist_date_basis FROM t WHERE delist_date IS NOT NULL ORDER BY 1")
    # listing 마지막 존재일 → stg_index_daily 다음 거래일. KIS lstg_abol_dt 와 전건 일치
    assert rows == [
        ("000030", date(2019, 2, 13), date(2019, 2, 13), False, date(2019, 2, 13), "derived"),
        ("900050", date(2017, 9, 27), date(2017, 9, 27), False, date(2017, 9, 27), "derived"),
        ("900060", date(2013, 10, 11), date(2013, 10, 11), False, date(2013, 10, 11), "derived")]


def test_마지막_존재일이_backfill_end면_생존이다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, delist_date, delist_date_basis FROM t "
                                 "WHERE ticker IN ('036220', '101970', '069500') ORDER BY 1")
    assert rows == [("036220", None, "unknown"), ("069500", None, "unknown"),
                    ("101970", None, "unknown")]
    # 규칙 e1.15.0 — 백필 상한은 baseline 상수가 아니라 `security.sql` 의 거래일 축 max 다.
    assert SECURITY.consts == ()
    assert _baseline().get("security", "backfill_end") is None


def test_KIS가_폐지라_한_티커는_delist_date를_갖는다(built: build.BuildResult) -> None:
    gate = next(g for g in built.gates if g.name == "EG3_security")
    assert gate.metrics["n_delisted_without_delist_date"] == 0
    assert gate.metrics["n_delisted"] == 3
    assert gate.metrics["n_delist_conflict"] == 0            # EG3-P11 기록형


# ── sec_type 어휘 전수 (합성 stage) ───────────────────────────────────────────
_LISTING_VOCAB: list[dict[str, object]] = [
    # ticker, secugrp, sect_tp, stkcert_tp, name → 기대 sec_type
    {"t": "T00001", "g": "주권", "s": "", "k": "보통주", "n": "가나전자", "want": "common"},
    {"t": "T00002", "g": "주권", "s": "", "k": "구형우선주", "n": "가나전자1우",
     "want": "preferred"},
    {"t": "T00003", "g": "주권", "s": "", "k": "신형우선주", "n": "가나전자2우",
     "want": "preferred"},
    {"t": "T00004", "g": "주권", "s": "", "k": "종류주권", "n": "가나전자3우", "want": "preferred"},
    {"t": "T00005", "g": "부동산투자회사", "s": "", "k": "보통주", "n": "가나리츠", "want": "reit"},
    {"t": "T00006", "g": "선박투자회사", "s": "", "k": "보통주", "n": "가나선박",
     "want": "ship_fund"},
    {"t": "T00007", "g": "투자회사", "s": "", "k": "보통주", "n": "가나투자", "want": "fund"},
    {"t": "T00008", "g": "사회간접자본투융자회사", "s": "", "k": "보통주", "n": "가나SOC",
     "want": "fund"},
    {"t": "T00009", "g": "외국주권", "s": "", "k": "보통주", "n": "가나차이나", "want": "foreign"},
    {"t": "T00010", "g": "주식예탁증권", "s": "", "k": "보통주", "n": "가나DR", "want": "dr"},
    {"t": "T00011", "g": "주식예탁증서", "s": "", "k": "보통주", "n": "가나DR2", "want": "dr"},
    # spac 은 common 보다 우선한다 — 소속부 축과 이름 축 둘 다
    {"t": "T00012", "g": "주권", "s": "SPAC(소속부없음)", "k": "보통주", "n": "가나스팩",
     "want": "spac"},
    {"t": "T00013", "g": "주권", "s": "우량기업부", "k": "보통주", "n": "가나기업인수목적1호",
     "want": "spac"},
    # 어휘 밖은 격리하지 않고 'other' 로 남긴다 (GATES §5-C6 생존편향)
    {"t": "T00014", "g": "신주인수권증서", "s": "", "k": "보통주", "n": "가나워런트",
     "want": "other"},
]


def _vocab_stage(tmp_path: Path, make_stage_tree) -> Path:
    make_stage_tree(tmp_path, "stg_listing_daily", [
        {"ticker": r["t"], "date": BACKFILL_END, "isin": "KR7" + str(r["t"]) + "000",
         "name": r["n"], "list_date": date(2015, 1, 5), "secugrp": r["g"], "sect_tp": r["s"],
         "stkcert_tp": r["k"]} for r in _LISTING_VOCAB])
    make_stage_tree(tmp_path, "stg_etf_price_daily",
                    [{"ticker": "E00001", "date": BACKFILL_END, "name": "가나ETF"}])
    make_stage_tree(tmp_path, "stg_index_daily",
                    [{"date": date(2026, 8, 19)}, {"date": BACKFILL_END}])
    make_stage_tree(tmp_path, "stg_delisted_master",
                    [{"ticker": "Z00001", "lstg_abol_dt": date(2020, 3, 2)}])
    make_stage_tree(tmp_path, "stg_corp_map",
                    [{"ticker": "T00001", "corp_code": "00000001"}])
    return tmp_path / "stage"


def _fixtures(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    p = tmp_path / "fx.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return p


def test_sec_type_매핑_어휘_전수(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _vocab_stage(tmp_path, make_stage_tree)
    fx = _fixtures(tmp_path, [{"case": "vocab", "key": {"ticker": r["t"]}, "column": "sec_type",
                               "expect": r["want"], "source": "hand"}
                              for r in _LISTING_VOCAB]
                   + [{"case": "etf", "key": {"ticker": "E00001"}, "column": "sec_type",
                       "expect": "etf", "source": "hand"}])
    r = build.build_table(SECURITY, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_vocab", fixtures_path=fx)
    assert r.ok, _fail_names(r)
    assert r.out_dir is not None
    got = dict(_query(r.out_dir, "SELECT ticker, sec_type FROM t ORDER BY 1"))
    want = {str(row["t"]): row["want"] for row in _LISTING_VOCAB} | {"E00001": "etf"}
    assert got == want
    assert set(got.values()) == set(rules_s01.SEC_TYPE_VOCAB)     # 10 어휘 전부 등장


def test_spac이_common보다_우선한다(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _vocab_stage(tmp_path, make_stage_tree)
    fx = _fixtures(tmp_path, [{"case": "spac", "key": {"ticker": "T00012"}, "column": "sec_type",
                               "expect": "spac", "source": "hand"}])
    r = build.build_table(SECURITY, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_spac", fixtures_path=fx)
    assert r.ok, _fail_names(r)
    assert r.out_dir is not None
    # 둘 다 secugrp='주권' ∧ stkcert_tp='보통주' 라 common 분기에도 걸린다
    rows = _query(r.out_dir, "SELECT ticker, sec_type FROM t WHERE ticker IN "
                             "('T00012', 'T00013') ORDER BY 1")
    assert rows == [("T00012", "spac"), ("T00013", "spac")]


def test_어휘_밖_secugrp는_other로_행이_남는다(tmp_path: Path, make_stage_tree) -> None:
    stage_root = _vocab_stage(tmp_path, make_stage_tree)
    fx = _fixtures(tmp_path, [{"case": "other", "key": {"ticker": "T00014"},
                               "column": "sec_type", "expect": "other", "source": "hand"}])
    r = build.build_table(SECURITY, stage_root, tmp_path / "equity", _baseline(),
                          build_id="b_other", fixtures_path=fx)
    assert r.ok, _fail_names(r)
    assert r.n_reject == 0                       # 격리하면 생존편향을 게이트가 만든다
    gate = next(g for g in r.gates if g.name == "EG3_security")
    assert gate.metrics["n_sec_type_other"] == 1     # 기록형 metric


# ── 부정 픽스처 — sec_type 이 어휘 밖이면 EG3_security 가 폐기시킨다 ───────────
_BROKEN_SEC_TYPE_SQL = """
WITH t AS (SELECT DISTINCT ticker FROM stg_listing_daily
           UNION SELECT DISTINCT ticker FROM stg_etf_price_daily)
SELECT ticker, NULL::VARCHAR AS corp_code, NULL::VARCHAR AS isin,
       NULL::VARCHAR AS name_current, 'warrant' AS sec_type, NULL::DATE AS list_date,
       'unknown' AS list_date_basis, NULL::DATE AS delist_date_krx,
       NULL::DATE AS delist_date_kis, false AS delist_conflict, NULL::DATE AS delist_date,
       'unknown' AS delist_date_basis, NULL::VARCHAR AS reject_reason
FROM t
"""


def test_sec_type_어휘_밖이면_EG3_security_FAIL(tmp_path: Path) -> None:
    broken = tmp_path / "security_broken.sql"
    broken.write_text(_BROKEN_SEC_TYPE_SQL, encoding="utf-8")
    rule = EquityTable(**{**SECURITY.__dict__, "sql_path": broken})
    r = build.build_table(rule, STAGE_SLICE, tmp_path / "equity", _baseline(),
                          build_id="b_neg_sec")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert next(g for g in r.gates if g.name == "EG1").status is GateStatus.PASS
    bad = next(g for g in r.gates if g.name == "EG3_security")
    assert bad.status is GateStatus.FAIL
    assert bad.metrics["n_sec_type_outside_vocab"] == 15
    assert next(g for g in r.gates if g.name == "EG4").detail == "upstream_failed"
    assert not (tmp_path / "equity" / "security" / "v=b_neg_sec").exists()


def test_폐지_티커에_delist_date가_없으면_EG3_security_FAIL(tmp_path: Path) -> None:
    # KIS 가 폐지라고 말한 000030 의 delist_date 를 지운 산출을 흉내낸다 (EG3-P10 축)
    broken = tmp_path / "security_no_delist.sql"
    broken.write_text(
        Path(SECURITY.sql_path).read_text(encoding="utf-8").replace(
            "coalesce(delist_date_krx, delist_date_kis)                            AS delist_date",
            "NULL::DATE                                                            AS delist_date"),
        encoding="utf-8")
    rule = EquityTable(**{**SECURITY.__dict__, "sql_path": broken})
    r = build.build_table(rule, STAGE_SLICE, tmp_path / "equity", _baseline(),
                          build_id="b_neg_del")
    assert r.status is build.BuildStatus.GATE_FAILED
    bad = next(g for g in r.gates if g.name == "EG3_security")
    assert bad.status is GateStatus.FAIL
    assert bad.metrics["n_delisted_without_delist_date"] == 3
