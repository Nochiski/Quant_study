"""S05 `corp_event` — stage 절단본 위 실제 `build_table` 왕복 (DESIGN §4-2 · GATES §3-⑨ · EG7-P08).

앞선 equity 테이블(`trading_calendar`·`corp_ticker`·`security_span`)을 같은 equity_root 에 먼저
빌드하고 그 위에 `corp_event` 를 올린다. 손계산 기대값은 절단본 `stg_capital`·`stg_event_cr`·
`stg_event_pifric`·`stg_listing_daily` 원자료를 직접 열어 확인한 값이다.

절단본 실측 — 모집단(pool, 법인 → 티커 전개 뒤 leg 행) 34:
  capital 28 = 우리은행 유상감자 2013-04-01 × 5 사업보고서 × (보통주 1 + 우선주 1) 10
             + 우양에이치씨 무상감자 4건 × 3 보고서 12 + 에코프로비엠 무상증자 4
             + 덕양에너젠 2(보통주·RCPS)
  event_cr 3 · event_pifric 1 · event_fric 0 · krx_listing 2 (005930·005935 액면분할 2018-05-04)
범위 밖 12(격리도 산출도 아님): pre_listing 6 = 우리은행 보통주 5(000030 첫 존재일 2014-11-19 이후
  사건 아님) + 덕양 보통주 1(0001A0 2026-01-30 상장 전) · unlisted_class 6 = 우리은행 우선주 5
  (절단본에 우선주 티커 없음) + 덕양 RCPS 1.
범위 안 후보 22 → 격리 0 · dedup 14 · 산출 8.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s05
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
CORP_EVENT = rules_s05.CORP_EVENT

N_POOL = 34
N_SRC = 22                                     # 범위 안 후보 = EG1 우변
N_SRC_BY_SOURCE = {"event_fric": 0, "event_pifric": 1, "event_cr": 3, "capital": 16,
                   "krx_listing": 2}
N_RAW_BY_SOURCE = {"event_fric": 0, "event_pifric": 1, "event_cr": 3, "capital": 28,
                   "krx_listing": 2}
N_SCOPE_OUT = {"in_scope": 22, "pre_listing": 6, "unlisted_class": 6}
N_OUT = 8
N_REJECT = 0
N_DEDUP = 14
N_BY_TYPE = {"bonus": 1, "capred": 5, "split": 2}


def _seed() -> Baseline:
    """S01·S02·S05 seed 병합 — 오케스트레이터가 baseline.json 에 병합하는 것과 같은 모양."""
    merged: dict[str, object] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s05.BASELINE_SEED):
        merged.update({k: v for k, v in load(p).data.items()
                       if not k.startswith("_") and k != "measured_at"})
    return Baseline(merged)


def _with(bl: Baseline, **corp_event: object) -> Baseline:
    return Baseline({**bl.data, "corp_event": {**bl.table("corp_event"), **corp_event}})


def _build_chain(stage_root: Path, equity_root: Path, baseline: Baseline,
                 rule: EquityTable = CORP_EVENT, **kw: object) -> build.BuildResult:
    for t in (rules_s02.TRADING_CALENDAR, rules_s01.CORP_TICKER, rules_s02.SECURITY_SPAN):
        r = build.build_table(t, stage_root, equity_root, baseline, build_id=f"b_{t.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    return build.build_table(rule, stage_root, equity_root, baseline, build_id="b_s05_event",
                             **kw)   # type: ignore[arg-type]


def _rows(out_dir: Path, where: str = "TRUE") -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        # 커밋 경로는 `v=<build>` 하이브 축(v)도 컬럼으로 붙인다 — 선언 스키마 대조에서 뺀다
        rel = con.execute("SELECT * EXCLUDE (v) FROM "
                          f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', "
                          f"hive_partitioning=true) WHERE {where} ORDER BY event_id")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _rejects(out_dir: Path) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT event_id, corp_code, event_type, source, reject_reason FROM "
                          f"read_parquet('{out_dir / '_reject' / '*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY 1, 2")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s05") / "equity"
    return _build_chain(STAGE_SLICE, root, _seed())


@pytest.fixture(scope="module")
def events(built: build.BuildResult) -> dict[str, dict[str, object]]:
    assert built.ok and built.out_dir is not None
    return {str(r["event_id"]): r for r in _rows(built.out_dir)}


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, [(g.name, g.status.value, g.detail) for g in built.gates]
    assert [g.name for g in built.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_corp_event",
                                             "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "pass", "EG3": "pass",
        "EG3_corp_event": "pass", "EG4": "pass"}
    assert built.n_rows == N_OUT and built.n_reject == N_REJECT


def test_EG1은_범위_안_후보에서_dedup과_격리를_뺀_것이다(built: build.BuildResult) -> None:
    """좌변 = count(out) + Σ(n_src_rows − 1). 우변 = pool 의 scope_out IS NULL 행수(원천별 합)."""
    eg1 = _gate(built, "EG1").metrics
    assert eg1["rhs"] == N_SRC and eg1["lhs"] == N_SRC - N_REJECT
    assert eg1["lhs"] == N_OUT + N_DEDUP
    x = _gate(built, "EG3_corp_event").metrics
    assert x["n_src_by_source"] == N_SRC_BY_SOURCE and x["n_src_total"] == N_SRC
    assert x["n_raw_rows_by_source"] == N_RAW_BY_SOURCE
    assert x["n_dedup"] == N_DEDUP
    assert x["n_by_event_type"] == N_BY_TYPE
    assert x["n_out_by_source"] == {"capital": 2, "event_cr": 3, "event_pifric": 1,
                                    "krx_listing": 2}
    assert x["n_by_effective_basis"] == {"disclosure_body": 6, "krx_shares_change": 2}


def test_범위_밖_행은_격리도_산출도_아니고_metric에만_남는다(
        built: build.BuildResult, events: dict[str, dict[str, object]]) -> None:
    """우리은행 2013 유상감자(상장 2014-11-19 전) · 덕양 2024 무상증자(상장 2026-01-30 전) ·
    상장 티커 없는 우선주·RCPS class-row — 조정할 가격이 없으니 모집단 밖이다."""
    x = _gate(built, "EG3_corp_event").metrics
    assert x["n_pool_by_scope"] == N_SCOPE_OUT
    assert sum(x["n_pool_by_scope"].values()) == N_POOL
    assert x["n_pool_by_scope_source"]["pre_listing"] == {"capital": 6}
    assert x["n_pool_by_scope_source"]["unlisted_class"] == {"capital": 6}
    assert x["class_unknown_kinds"] == []
    assert x["n_out_off_calendar"] == 0 and x["n_out_pre_listing"] == 0
    assert built.n_reject == 0 and _gate(built, "EG7").metrics["reject_by_reason"] == {}
    assert not [e for e in events if e.startswith(("000030:", "0001A0:"))]
    assert {r["corp_code"] for r in events.values()} == {"00126380", "00450931", "01160363"}


def test_dedup_축_재계산은_0이고_event_id는_결정적이다(built: build.BuildResult,
                                                 events: dict[str, dict[str, object]]) -> None:
    """GATES §3-⑨: (ticker, event_type, effective_date) 로 다시 세면 중복 0."""
    x = _gate(built, "EG3_corp_event").metrics
    assert x["n_dup_key"] == 0 and x["n_event_id_mismatch"] == 0
    keys = {(r["ticker"], r["event_type"], r["effective_date"]) for r in events.values()}
    assert len(keys) == len(events) == N_OUT
    for eid, r in events.items():
        assert eid == f"{r['ticker']}:{r['event_type']}:{r['effective_date']}"
        assert r["n_src_rows"] >= 1 and r["available_date"] == r["announce_date"]
    assert sum(int(str(r["n_src_rows"])) - 1 for r in events.values()) == N_DEDUP


def test_삼성전자_50대1_액면분할은_KRX_관측만으로_본주_우선주_둘_다_잡힌다(
        events: dict[str, dict[str, object]]) -> None:
    """FX-2-001 의 원천. 결정공시(DS005)에 액면분할이 없고 자본변동에도 행이 없다."""
    for eid in ("005930:split:2018-05-04", "005935:split:2018-05-04"):
        r = events[eid]
        assert r["event_type"] == "split" and r["ratio"] == 50.0
        assert r["effective_basis"] == "krx_shares_change" and r["source"] == "krx_listing"
        assert r["announce_date"] == r["effective_date"] == date(2018, 5, 4)
        assert r["rcept_no"] is None and r["available_basis"] == "default"
        assert r["corp_code"] == "00126380" and r["n_src_rows"] == 1
    assert not [e for e in events if e.startswith("005930:") and "2018-05-04" not in e]


def test_무상증자는_공시일과_효력일이_다르고_효력일은_권리락일이다(
        events: dict[str, dict[str, object]]) -> None:
    """FX-2-008. 기준일 2022-06-28 직전 거래일 06-27 에 종가가 497,400 → 135,900 로 떨어진다."""
    r = events["247540:bonus:2022-06-27"]
    assert r["announce_date"] == date(2022, 6, 14) == r["available_date"]
    assert r["effective_date"] == date(2022, 6, 27)
    assert r["effective_basis"] == "disclosure_body" and r["available_basis"] == "derived"
    assert r["ratio"] == 4.0                      # 1주당 3.0 배정 → share_factor 4
    assert r["rcept_no"] == "20220614000068"
    assert "247540:bonus:2022-06-28" not in events


def test_같은_이벤트가_결정공시와_자본변동_다섯_행에서_와도_1행이고_결정공시가_이긴다(
        events: dict[str, dict[str, object]]) -> None:
    r = events["247540:bonus:2022-06-27"]
    assert r["source"] == "event_pifric" and r["n_src_rows"] == 5   # pifric 1 + 사업보고서 4
    c = events["101970:capred:2015-11-26"]
    assert c["source"] == "event_cr" and c["n_src_rows"] == 4       # cr 1 + 사업보고서 3
    assert c["announce_date"] == date(2016, 6, 8)                     # 결정공시의 rcept_dt
    assert c["ratio"] == 11016503 / 25735667                        # atcr / bfcr 발행총수


def test_감자_ratio는_총주식수_배수이고_자본변동만_있으면_NULL이다(
        events: dict[str, dict[str, object]]) -> None:
    r = events["101970:capred:2018-10-12"]
    assert r["ratio"] == 13656196 / 136561969 and r["source"] == "event_cr"
    assert r["announce_date"] == date(2018, 7, 24)
    cap = events["101970:capred:2018-02-23"]
    assert cap["ratio"] is None and cap["source"] == "capital" and cap["n_src_rows"] == 3
    assert cap["announce_date"] == date(2019, 3, 13)                 # 가장 이른 사업보고서
    assert all(r["amount_krw"] is None for r in events.values())


def test_상장폐지_구간_안_사건은_첫_존재일_뒤라_범위_안이다(
        events: dict[str, dict[str, object]]) -> None:
    """우양 101970: 2015-03-16 폐지 → 2025-03-28 재상장. 그 사이 감자 4건은 pre_listing 이 아니다 —
    재상장 뒤 가격과 폐지 전 가격을 이어 붙이려면 이 계수가 필요하다."""
    assert {e for e in events if e.startswith("101970:")} == {
        "101970:capred:2015-11-26", "101970:capred:2015-11-28", "101970:capred:2018-02-23",
        "101970:capred:2018-10-12", "101970:capred:2018-10-13"}


def test_근접_중복은_기록형_metric으로만_남는다(built: build.BuildResult,
                                     events: dict[str, dict[str, object]]) -> None:
    """우양 2018 감자: cr_std 2018-10-12 vs 자본변동 isu_dcrs_de 2018-10-13 — 정확 축은 못 접는다.

    두 행이 남으면 S06 이 감자를 두 번 곱한다 — 기록형 metric 이 승격 후보다(DESIGN §4-2).
    """
    assert "101970:capred:2018-10-12" in events and "101970:capred:2018-10-13" in events
    x = _gate(built, "EG3_corp_event").metrics
    assert x["n_near_dup_cross_source"] == 1 and x["near_dup_window_days"] == 5
    assert _gate(built, "EG3_corp_event").status is GateStatus.PASS


def test_파티션은_year_announce_date_이고_KRX_행도_연도를_갖는다(
        built: build.BuildResult, events: dict[str, dict[str, object]]) -> None:
    assert built.out_dir is not None
    for r in events.values():
        assert int(str(r["year"])) == r["announce_date"].year   # type: ignore[union-attr]
    assert events["005930:split:2018-05-04"]["year"] == 2018
    assert {p["path"].split("/")[1] for p in built.partitions} == {  # type: ignore[union-attr]
        "year=2016", "year=2018", "year=2019", "year=2022"}


def test_어휘는_MVP_4종_안이고_EG2_축은_announce_date다(built: build.BuildResult,
                                              events: dict[str, dict[str, object]]) -> None:
    assert {r["event_type"] for r in events.values()} <= set(rules_s05.MVP_EVENT_TYPES)
    assert set(rules_s05.MVP_EVENT_TYPES) <= set(rules_s05.EVENT_TYPE_VOCAB)
    assert len(rules_s05.EVENT_TYPE_VOCAB) == 13
    assert _gate(built, "EG2").metrics["content_date_column"] == "announce_date"
    assert all(r["announce_date"] <= r["available_date"] for r in events.values())


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    cols = [c for c in _rows(built.out_dir)[0] if c != "year"]
    assert cols == list(CORP_EVENT.columns)


def test_같은_inputs_재빌드는_파티션_해시가_같다(built: build.BuildResult) -> None:
    root = built.out_dir.parent.parent            # type: ignore[union-attr]
    again = build.build_table(CORP_EVENT, STAGE_SLICE, root, _seed(), build_id="b_s05_event_2")
    assert again.ok, [(g.name, g.status.value, g.detail) for g in again.gates]
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS and eg5.metrics["n_changed_partitions"] == 0


# ── 선언 대조 ────────────────────────────────────────────────────────────────

def test_주식종류_어휘는_rules_선언과_SQL_리터럴이_같다() -> None:
    """종류 매핑의 정본은 rules_s05 튜플 — `.sql` 의 IN 리스트가 어긋나면 여기서 잡힌다."""
    text = re.sub(r"--[^\n]*", "", CORP_EVENT.sql_path.read_text(encoding="utf-8"))
    lists = [tuple(re.findall(r"'([^']+)'", m))
             for m in re.findall(r"isu_dcrs_stock_knd IN \(([^)]*)\)", text)]
    assert lists == [rules_s05.COMMON_KINDS, rules_s05.PREFERRED_KINDS, rules_s05.UNLISTED_KINDS]
    assert not set(rules_s05.COMMON_KINDS) & set(rules_s05.PREFERRED_KINDS)
    assert not set(rules_s05.PREFERRED_KINDS) & set(rules_s05.UNLISTED_KINDS)


def test_EG1_우변은_SQL의_pool_CTE를_재사용한다() -> None:
    assert rules_s05.EG1_RHS_SQL.rstrip().endswith("FROM pool WHERE scope_out IS NULL")
    assert dict(rules_s05.SOURCES).keys() == set(rules_s05.SOURCE_VOCAB)
    assert "pool AS (" in rules_s05.EG1_RHS_SQL and "cand AS" not in rules_s05.EG1_RHS_SQL


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_효력일이_공시일보다_임계_이상_앞서면_effective_before_announce로_격리한다(
        tmp_path: Path) -> None:
    """EG7-P08 은 사건 단위(가장 이른 announce). 임계 100일이면 우양 감자 4건(가장 이른 공시가
    152~383일 뒤) 후보 14행이 통째로 격리되고, 에코프로비엠 무상증자는 결정공시(효력일 전)가 있어
    사업보고서 사본 4행(267일+)까지 접힌다 — 남는 산출은 KRX 2 + cr 2018-10-12 + pifric = 4."""
    r = _build_chain(STAGE_SLICE, tmp_path / "equity",
                     _with(_seed(), effective_before_announce_max_days=100))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg7 = _gate(r, "EG7")
    assert eg7.status is GateStatus.FAIL
    assert eg7.metrics["reject_by_reason"] == {"effective_before_announce": 14}
    assert r.n_rows == 4
    assert _gate(r, "EG1").detail == "upstream_failed"


HAND_FRIC: list[dict[str, object]] = [
    # A — FX-2-003: SK하이닉스 1주당 0.2 무상증자, 기준일 2021-03-10(수) → 권리락 03-09.
    #     참조표 미스(available NULL·unknown) → rcept_no 앞 8자리 접수일이 announce_date.
    #     기타주식 배정(0.5)은 preferred class-row 를 만들지만 SK하이닉스에 상장 우선주가 없다 →
    #     unlisted_class(범위 밖, 격리 아님)
    {"rcept_no": "20210222000001", "corp_code": "00164779", "nstk_asstd": date(2021, 3, 10),
     "nstk_ascnt_ps_ostk_ratio": 0.2, "nstk_ascnt_ps_estk_ratio": 0.5,
     "nstk_ostk_cnt": 145600473, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 728002365,
     "available_date": None, "available_basis": "unknown"},
    # B — 에코프로비엠 결정공시 중복(pifric 20220614000068 과 같은 기준일): 접히고 pifric 이 이긴다
    {"rcept_no": "20220615000009", "corp_code": "01160363", "nstk_asstd": date(2022, 6, 28),
     "nstk_ascnt_ps_ostk_ratio": 3.0, "nstk_ascnt_ps_estk_ratio": None,
     "nstk_ostk_cnt": 73351008, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 24450336,
     "available_date": date(2022, 6, 15), "available_basis": "derived"},
    # C — 배정비율도 전 주식수도 없다 → ratio_unparsed
    {"rcept_no": "20210510000003", "corp_code": "00164779", "nstk_asstd": date(2021, 6, 10),
     "nstk_ascnt_ps_ostk_ratio": None, "nstk_ascnt_ps_estk_ratio": None,
     "nstk_ostk_cnt": None, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 0,
     "available_date": date(2021, 5, 10), "available_basis": "derived"},
    # D — 배정기준일 없음 → effective_unresolved
    {"rcept_no": "20210901000004", "corp_code": "00164779", "nstk_asstd": None,
     "nstk_ascnt_ps_ostk_ratio": 0.3, "nstk_ascnt_ps_estk_ratio": None,
     "nstk_ostk_cnt": 1000, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 728002365,
     "available_date": date(2021, 9, 1), "available_basis": "derived"},
    # E — 기준일 2009-05-10: 캘린더(2010-01-04~) 밖 → out_of_calendar(범위 밖)
    {"rcept_no": "20090420000005", "corp_code": "00164779", "nstk_asstd": date(2009, 5, 10),
     "nstk_ascnt_ps_ostk_ratio": 0.1, "nstk_ascnt_ps_estk_ratio": None,
     "nstk_ostk_cnt": 1000, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 728002365,
     "available_date": date(2009, 4, 20), "available_basis": "derived"},
    # F — 에코프로비엠 상장(2019-03-05) 전 기준일 2018-06-01 → pre_listing(범위 밖)
    {"rcept_no": "20180510000006", "corp_code": "01160363", "nstk_asstd": date(2018, 6, 1),
     "nstk_ascnt_ps_ostk_ratio": 1.0, "nstk_ascnt_ps_estk_ratio": None,
     "nstk_ostk_cnt": 1000, "nstk_estk_cnt": 0, "bfic_tisstk_ostk": 1000,
     "available_date": date(2018, 5, 10), "available_basis": "derived"},
]

HAND_CAPITAL: list[dict[str, object]] = [
    # 1 — 캘린더 밖(2005) 회고 기재 → out_of_calendar
    {"rcept_no": "20130329000101", "corp_code": "00164779", "isu_dcrs_de": date(2005, 3, 10),
     "isu_dcrs_stle": "무상증자", "isu_dcrs_stock_knd": "보통주",
     "available_date": date(2013, 3, 29), "available_basis": "derived"},
    # 2 — 범위 안 보통주 무상증자(2013-05-10 기준일 → 권리락 05-09), ratio NULL
    {"rcept_no": "20140331000102", "corp_code": "00164779", "isu_dcrs_de": date(2013, 5, 10),
     "isu_dcrs_stle": "무상증자", "isu_dcrs_stock_knd": "보통주",
     "available_date": date(2014, 3, 31), "available_basis": "derived"},
    # 3 — 같은 사건의 우선주 행: SK하이닉스에 상장 우선주 없음 → unlisted_class
    {"rcept_no": "20140331000102", "corp_code": "00164779", "isu_dcrs_de": date(2013, 5, 10),
     "isu_dcrs_stle": "무상증자", "isu_dcrs_stock_knd": "우선주",
     "available_date": date(2014, 3, 31), "available_basis": "derived"},
    # 4 — 종류 '-' → class_unknown
    {"rcept_no": "20140331000102", "corp_code": "00164779", "isu_dcrs_de": date(2013, 6, 1),
     "isu_dcrs_stle": "무상감자", "isu_dcrs_stock_knd": "-",
     "available_date": date(2014, 3, 31), "available_basis": "derived"},
    # 5 — corp_ticker 에 없는 법인의 보통주 → 여전히 ticker_unresolved 격리
    {"rcept_no": "20140331000103", "corp_code": "09999999", "isu_dcrs_de": date(2013, 7, 1),
     "isu_dcrs_stle": "무상감자", "isu_dcrs_stock_knd": "보통주",
     "available_date": date(2014, 3, 31), "available_basis": "derived"},
]

HAND_FIXTURES: list[dict[str, object]] = [
    {"case": "FX-2-003-ratio", "key": {"event_id": "000660:bonus:2021-03-09"}, "column": "ratio",
     "expect": "1.2", "expect_source": "hand", "fixture_class": "positive",
     "source": "hand — 1 + 0.2. 엔진 임계 미만 이벤트도 행으로 존재한다"},
    {"case": "FX-2-003-type", "key": {"event_id": "000660:bonus:2021-03-09"},
     "column": "event_type", "expect": "bonus", "expect_source": "hand",
     "fixture_class": "positive", "source": "hand"},
    {"case": "hand-rcept-fallback", "key": {"event_id": "000660:bonus:2021-03-09"},
     "column": "announce_date", "expect": "2021-02-22", "expect_source": "hand",
     "fixture_class": "positive", "source": "hand — rcept_no 20210222000001 앞 8자리"},
    {"case": "hand-amount", "key": {"event_id": "000660:bonus:2021-03-09"},
     "column": "amount_krw", "expect": None, "expect_source": "hand",
     "fixture_class": "positive", "source": "hand"},
]


def test_손픽스처_FX_2_003과_범위_밖_4종과_격리_3종(tmp_path: Path, make_stage_tree) -> None:
    """절단본의 `stg_event_fric`(0행)·`stg_capital` 을 손 트리로 바꿔 끼운다(나머지는 심볼릭 링크).

    pool = fric 7(A 2 leg + B·C·D·E·F) + capital 5 + cr 3 + pifric 1 + krx 2 = 18.
    범위 밖 6: A-preferred·capital-3 unlisted_class 2 · E·capital-1 out_of_calendar 2 ·
      F pre_listing 1 · capital-4 class_unknown 1. 후보 12 → 격리 3(C ratio_unparsed ·
      D effective_unresolved · capital-5 ticker_unresolved) · dedup 1(B → pifric) · 산출 8.
    """
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    for d in STAGE_SLICE.iterdir():
        if d.is_dir() and d.name not in ("stg_event_fric", "stg_capital"):
            os.symlink(d, stage_root / d.name)
    make_stage_tree(tmp_path, "stg_event_fric", HAND_FRIC, "receipt_axis", build_id="b_hand_fric")
    make_stage_tree(tmp_path, "stg_capital", HAND_CAPITAL, "receipt_axis", build_id="b_hand_cap")
    fx = tmp_path / "corp_event.json"
    fx.write_text(json.dumps(HAND_FIXTURES, ensure_ascii=False), encoding="utf-8")
    r = _build_chain(stage_root, tmp_path / "equity", _with(_seed(), thresholds={"EG7": 0.5}),
                     fixtures_path=fx)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == 8 and r.n_reject == 3
    eg1 = _gate(r, "EG1").metrics
    assert eg1["rhs"] == 12 and eg1["lhs"] == 9
    x = _gate(r, "EG3_corp_event").metrics
    assert x["n_pool_by_scope"] == {"in_scope": 12, "unlisted_class": 2, "out_of_calendar": 2,
                                    "pre_listing": 1, "class_unknown": 1}
    assert x["n_pool_by_scope_source"]["out_of_calendar"] == {"capital": 1, "event_fric": 1}
    assert x["n_pool_by_scope_source"]["unlisted_class"] == {"capital": 1, "event_fric": 1}
    assert x["n_pool_by_scope_source"]["pre_listing"] == {"event_fric": 1}
    assert x["class_unknown_kinds"] == ["-"]
    assert x["n_src_by_source"] == {"event_fric": 4, "event_pifric": 1, "event_cr": 3,
                                    "capital": 2, "krx_listing": 2}
    assert x["n_raw_rows_by_source"]["event_fric"] == 6 and x["n_dedup"] == 1
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {
        "effective_unresolved": 1, "ratio_unparsed": 1, "ticker_unresolved": 1}
    assert r.out_dir is not None
    ev = {str(e["event_id"]): e for e in _rows(r.out_dir)}
    a = ev["000660:bonus:2021-03-09"]
    assert a["ratio"] == 1.2 and a["n_src_rows"] == 1
    assert a["announce_date"] == date(2021, 2, 22) and a["available_basis"] == "derived"
    cap = ev["000660:bonus:2013-05-09"]
    assert cap["ratio"] is None and cap["source"] == "capital"
    eco = ev["247540:bonus:2022-06-27"]
    assert eco["source"] == "event_pifric" and eco["n_src_rows"] == 2   # pifric + B
    assert eco["announce_date"] == date(2022, 6, 14)
    rej = {(str(e["event_id"]), str(e["reject_reason"])) for e in _rejects(r.out_dir)}
    assert rej == {("000660:bonus:2021-06-09", "ratio_unparsed"),
                   ("000660:bonus:-", "effective_unresolved"),
                   ("-:capred:2013-07-01", "ticker_unresolved")}
