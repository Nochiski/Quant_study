"""S06 `adj_factor` — 절단본 위 `build_table` 왕복 + apply_date 판정 합성 SQL 검사
(DESIGN §4-2 · GATES §3-⑩ · EG3-P04 · EG8 · §9 S06 2차).

상류 6테이블(`trading_calendar`·`security`·`security_span`·`corp_ticker`·`price_daily`·
`corp_event`)을 같은 equity_root 에 먼저 짓고 그 위에 `adj_factor` 를 올린다. 손계산 기대값은
절단본 `stg_event_cr`·`stg_capital`·`stg_listing_daily`·`stg_price_daily` 원자료를 직접 열어
확인한 값이다.

절단본 실측(corp_event 8행 — S05 2차 범위 규칙: 000030 유상감자·0001A0 무상증자는 상장 전·
비상장 종류라 모집단 밖 — → adj_factor 8행, 격리 0, 2차 apply_date 판정 뒤):
  ok 3 = 005930·005935 split(1/50, 50) · 247540 bonus(1/4, 4) — 전부 nominal
  no_price_match 3 = 101970 capred 2015-11-26·11-28·2018-10-12 — 상장폐지 기간 사건이라 창 안에
       거래 행이 없다(apply_basis unmatched, 계수 1)
  ratio_null 1 = 101970 2018-02-23 (자본변동 단독 행) · near_dup_suppressed 1 = 101970 2018-10-13
  EG8(apply_date): 가격 행 있는 ok 이벤트 3 — 최대 |수정수익률| 0.0929 · 거래량 점프 3.43
가격 매칭(price_matched·combined·소액 nominal·same_day)은 절단본에 사례가 없어 합성 가격 위에서
`sql/adj_factor.sql` 을 직접 돌려 검사한다(FX-2-004 두 축 상이 포함).
"""
from __future__ import annotations

import datetime as dt
import os
import re
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s04, rules_s05, rules_s06
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
ADJ = rules_s06.ADJ_FACTOR
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT)
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_adj_factor", "EG8", "EG4", "EG5a"]
N_EVENTS = 8
N_OK = 3
OK_IDS = {"005930:split:2018-05-04", "005935:split:2018-05-04", "247540:bonus:2022-06-27"}
NO_MATCH_IDS = {"101970:capred:2015-11-26", "101970:capred:2015-11-28",
                "101970:capred:2018-10-12"}
RATIO_NULL_IDS = {"101970:capred:2018-02-23"}
NEAR_DUP_IDS = {"101970:capred:2018-10-13"}
# 손계산: 247540 권리락일 조정 전일 497,400 × 0.25 = 124,350 → 135,900 · 005930 53,000 → 51,900
MAX_ADJ_RETURN = 135900 / 124350 - 1
MAX_RAW_RETURN_005930 = 51900 / 2650000 - 1


def seed() -> Baseline:
    """S01·S02·S05·S06 seed 병합 — 오케스트레이터가 baseline.json 에 병합하는 것과 같은 모양."""
    merged: dict[str, object] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED):
        merged.update({k: v for k, v in load(p).data.items()
                       if not k.startswith("_") and k != "measured_at"})
    return Baseline(merged)


def build_upstream(stage_root: Path, equity_root: Path, baseline: Baseline) -> None:
    for t in UPSTREAM:
        r = build.build_table(t, stage_root, equity_root, baseline, build_id=f"b_{t.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]


def build_chain(stage_root: Path, equity_root: Path, baseline: Baseline,
                rule: EquityTable = ADJ, build_id: str = "b_s06_adj",
                **kw: object) -> build.BuildResult:
    build_upstream(stage_root, equity_root, baseline)
    return build.build_table(rule, stage_root, equity_root, baseline, build_id=build_id,
                             **kw)   # type: ignore[arg-type]


def rows(out_dir: Path) -> dict[str, dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * EXCLUDE (v) FROM "
                          f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY event_id")
        cols = [d[0] for d in rel.description]
        return {str(r[cols.index("event_id")]): dict(zip(cols, r, strict=True))
                for r in rel.fetchall()}
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**ADJ.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return ADJ.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


def _seed_as(bl: Baseline, name: str) -> Baseline:
    """변종 테이블 이름으로 adj_factor 상수를 다시 등재한다(`_const` 는 rule.name 네임스페이스)."""
    return Baseline({**bl.data, name: bl.table("adj_factor")})


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s06") / "equity"
    return build_chain(STAGE_SLICE, root, seed())


@pytest.fixture(scope="module")
def factors(built: build.BuildResult) -> dict[str, dict[str, object]]:
    assert built.ok and built.out_dir is not None
    return rows(built.out_dir)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, [(g.name, g.status.value, g.detail) for g in built.gates]
    assert [g.name for g in built.gates] == GATE_ORDER
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        n: "pass" for n in GATE_ORDER if n != "EG5a"}
    assert built.n_rows == N_EVENTS and built.n_reject == 0
    assert set(built.inputs) == {"corp_event", "price_daily", "trading_calendar", "stg_event_cr"}
    assert built.inputs["corp_event"] == "b_corp_event"       # equity 입력도 고정된다


def test_EG1은_corp_event의_MVP_이벤트_수이고_격리는_없다(built: build.BuildResult) -> None:
    eg1 = _gate(built, "EG1").metrics
    assert eg1["lhs"] == eg1["rhs"] == N_EVENTS and eg1["n_reject"] == 0
    assert _gate(built, "EG7").metrics["reject_by_reason"] == {}


def test_삼성전자_분할_계수는_1_50과_50이고_명목_세션에_적용된다(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-001 · FX-2-009. 05-04 원수익률 −98.04% 가 기대 −98% 와 맞아 nominal.
    KRX 관측행은 announce = 관측일 = apply_date 라 available 도 그날이다."""
    for eid in ("005930:split:2018-05-04", "005935:split:2018-05-04"):
        f = factors[eid]
        assert f["price_factor"] == 1 / 50 and f["share_factor"] == 50.0
        assert f["price_factor"] * f["share_factor"] == 1          # type: ignore[operator]
        assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
        assert f["apply_basis"] == "nominal" and f["apply_date"] == date(2018, 5, 4)
        assert f["available_date"] == date(2018, 5, 4) == f["announce_date"]
        assert f["available_basis"] == "derived" and f["event_type"] == "split"
        assert f["corp_code"] == "00126380"


def test_무상증자는_1_4과_4이고_권리락일이_명목_세션이다(
        factors: dict[str, dict[str, object]]) -> None:
    """06-27 원수익률 −72.7% vs 기대 −75% → 잔여 0.093 ≤ max(0.15×0.75, 0.05)."""
    f = factors["247540:bonus:2022-06-27"]
    assert f["price_factor"] == 0.25 and f["share_factor"] == 4.0 and f["factor_ok"] is True
    assert f["apply_basis"] == "nominal" and f["apply_date"] == date(2022, 6, 27)
    # 공시일 < apply_date 다음 세션 06-28
    assert f["announce_date"] == date(2022, 6, 14) == f["available_date"]


def test_상장폐지_기간의_감자는_가격이_없어_no_price_match_계수_1이다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    """FX-2-004 대체. 101970 은 2015-03-16 폐지 → 2024-03-13 재상장: 세 감자 전부 창 안에 거래
    행이 없다 → 틀린 날에 적용하는 것보다 안 하는 게 낫다(2차 (d)). ratio 는 corp_event 에
    남는다."""
    assert {e for e, f in factors.items() if f["factor_source"] == "no_price_match"} == NO_MATCH_IDS
    for eid in NO_MATCH_IDS:
        f = factors[eid]
        assert f["factor_ok"] is False and f["apply_basis"] == "unmatched"
        assert f["price_factor"] == 1.0 and f["share_factor"] == 1.0
    # 미매칭 행의 apply_date 는 명목 세션 — 토요일 기준일 11-28 은 월요일 11-30
    assert factors["101970:capred:2015-11-26"]["apply_date"] == date(2015, 11, 26)
    assert factors["101970:capred:2015-11-28"]["apply_date"] == date(2015, 11, 30)
    assert factors["101970:capred:2018-10-12"]["apply_date"] == date(2018, 10, 12)
    x = _gate(built, "EG3_adj_factor").metrics
    assert x["n_no_price_match"] == 3 and x["n_no_price_match_no_price_rows"] == 3
    assert x["n_by_apply_basis"] == {"nominal": 5, "unmatched": 3}
    assert x["n_ok_by_event_type_apply_basis"] == {"bonus:nominal": 1, "split:nominal": 2}
    assert x["n_ok_apply_ne_nominal"] == 0 and x["apply_offset_sessions_max"] == 0


def test_ratio_NULL_행은_factor_ok_false_계수_1_격리_아님(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-007 대체(MVP 에 spinoff 가 없다): 자본변동 단독 행 1건(S05 2차 범위 규칙 뒤)."""
    assert {e for e, f in factors.items() if f["factor_source"] == "ratio_null"} == RATIO_NULL_IDS
    f = factors["101970:capred:2018-02-23"]
    assert f["factor_ok"] is False and f["price_factor"] == 1.0 and f["share_factor"] == 1.0
    assert f["apply_basis"] == "nominal" and f["apply_date"] == date(2018, 2, 23)
    # 회고 기재라 announce 가 사건보다 늦다 → apply_date 다음 세션이 available 이 된다
    assert f["announce_date"] == date(2019, 3, 13)         # 가장 이른 사업보고서
    assert f["available_date"] == date(2018, 2, 26)        # 금요일 → 월요일
    assert "000030:capred:2013-04-01" not in factors and "0001A0:bonus:2024-09-03" not in factors


def test_교차_원천_근접_중복은_가격_매칭보다_먼저_낮은_쪽을_누른다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    """결정공시 cr_std 2018-10-12 vs 자본변동 isu_dcrs_de 10-13 — 둘 다 곱하면 감자를 두 번
    곱는다. 눌린 행은 가격 매칭 대상이 아니라 apply_basis nominal."""
    loser, winner = factors["101970:capred:2018-10-13"], factors["101970:capred:2018-10-12"]
    assert loser["factor_source"] == "near_dup_suppressed" and loser["factor_ok"] is False
    assert loser["price_factor"] == 1.0 and loser["share_factor"] == 1.0
    assert loser["apply_basis"] == "nominal" and loser["apply_date"] == date(2018, 10, 15)
    assert winner["factor_source"] == "no_price_match"     # 이긴 쪽도 가격이 없어 못 낸다
    x = _gate(built, "EG3_adj_factor").metrics
    assert x["n_near_dup_suppressed"] == 1 and x["n_near_dup_both_ok"] == 0
    assert x["near_dup_window_days"] == 5                       # corp_event 네임스페이스 상수
    assert x["n_by_factor_source"] == {"mktcap_neutral": N_OK, "near_dup_suppressed": 1,
                                       "no_price_match": 3, "ratio_null": 1}
    assert {e for e, f in factors.items() if f["factor_ok"]} == OK_IDS


def test_available_date는_min_공시_apply_date_다음_세션_이다(built: build.BuildResult,
                                                   factors: dict[str, dict[str, object]]) -> None:
    """캘린더 원자료로 독립 재계산. EG2-P02 announce 축을 못 쓰는 대신 EG3_adj_factor 가 이 식을
    본다."""
    con = duckdb.connect()
    try:
        cal = sorted(r[0] for r in con.execute(
            "SELECT DISTINCT date FROM read_parquet("
            f"'{STAGE_SLICE / 'stg_index_daily' / 'v=*' / 'year=*' / '*.parquet'}')").fetchall())
    finally:
        con.close()
    for f in factors.values():
        assert f["apply_date"] in cal                             # 캘린더 세션
        nominal = next(d for d in cal if d >= f["effective_date"])   # type: ignore[operator]
        assert f["apply_date"] == nominal                         # 절단본은 전부 명목 세션
        nxt = cal[cal.index(f["apply_date"]) + 1]
        assert f["available_date"] == min(f["announce_date"], nxt)   # type: ignore[type-var]
        assert f["available_basis"] == "derived"
    x = _gate(built, "EG3_adj_factor").metrics
    # available < announce 4 = 101970 2015-11-26·11-28·2018-02-23·2018-10-13 (회고 기재)
    assert x["n_available_mismatch"] == 0 and x["n_available_before_announce"] == 4
    assert x["n_nominal_apply_ne_nominal_session"] == 0 and x["n_apply_outside_window"] == 0
    assert _gate(built, "EG2").metrics["content_date_column"] is None


def test_EG8_점프는_apply_date의_조정가로_재고_원주가_점프는_사라진다(
        built: build.BuildResult) -> None:
    """005930 2018-05-04: 원주가 −98% → 조정가 −2.1%. 247540: 원주가 −72.7% → 조정가 +9.3%."""
    eg8 = _gate(built, "EG8")
    assert eg8.status is GateStatus.PASS
    m = eg8.metrics
    assert m["asof_basis"] == "baseline" and m["asof_used"] == "2026-08-20"
    assert m["n_ok_events"] == N_OK and m["n_ok_events_with_price"] == 3
    assert m["n_return_jump_over"] == 0 and m["n_volume_ratio_out_of_band"] == 0
    assert m["n_ok_abs_adj_return_over_030"] == 0
    assert m["max_abs_adj_return_jump"] == pytest.approx(MAX_ADJ_RETURN)
    # P03 집합 통계: 이벤트별 조정 거래량 20세션 중앙값 비(후/전), 계수가 맞으면 1 근처
    assert m["n_ok_volume_ratio_judged"] == 3 and m["n_ok_volume_ratio_undefined"] == 0
    assert 1 / 3 <= m["adj_volume_ratio_median"] <= 3
    assert m["adj_volume_ratio_quantiles"]["max"] < 10 and m["n_ok_volume_ratio_over_10"] == 0
    assert 1 < m["max_adj_volume_jump_day"] < 10                # 하루 점프(기록형) 절단본 3.43
    assert m["n_ok_apply_ne_effective"] == 0
    ev = {e["event_id"]: e for e in m["events"]}                # type: ignore[union-attr]
    assert ev["005930:split:2018-05-04"]["raw_return"] == pytest.approx(MAX_RAW_RETURN_005930)
    assert ev["005930:split:2018-05-04"]["adj_return"] == pytest.approx(51900 / 53000 - 1)
    assert ev["247540:bonus:2022-06-27"]["adj_return"] == pytest.approx(MAX_ADJ_RETURN)
    assert ev["247540:bonus:2022-06-27"]["raw_return"] == pytest.approx(135900 / 497400 - 1)
    assert all(e["apply_basis"] == "nominal" for e in ev.values())


def test_점프_상수가_없으면_EG8은_skip이되_metric은_계산한다(tmp_path: Path) -> None:
    bl = seed()
    keep = {k: v for k, v in bl.table("adj_factor").items()
            if k in rules_s06.PRICE_MATCH_CONSTS}               # 매칭 상수는 산출식이 요구한다
    r = build_chain(STAGE_SLICE, tmp_path / "equity", Baseline({**bl.data, "adj_factor": keep}))
    assert r.ok
    eg8 = _gate(r, "EG8")
    assert eg8.status is GateStatus.SKIP and eg8.detail == "no_baseline"
    assert eg8.metrics["missing_metric"] == "adj_factor.asof_for_jump_check"
    assert eg8.metrics["asof_basis"] == "max_price_date"
    assert eg8.metrics["asof_used"] == "2026-08-20"
    assert eg8.metrics["max_abs_adj_return_jump"] == pytest.approx(MAX_ADJ_RETURN)


def test_파티션은_year_effective_date이고_컬럼_순서는_선언과_같다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    assert built.out_dir is not None
    for f in factors.values():
        assert int(str(f["year"])) == f["effective_date"].year   # type: ignore[union-attr]
    assert {p["path"].split("/")[1] for p in built.partitions} == {   # type: ignore[union-attr]
        "year=2015", "year=2018", "year=2022"}
    cols = [c for c in next(iter(factors.values())) if c != "year"]
    assert cols == list(ADJ.columns)


def test_같은_inputs_재빌드는_파티션_해시가_같다(built: build.BuildResult) -> None:
    root = built.out_dir.parent.parent            # type: ignore[union-attr]
    again = build.build_table(ADJ, STAGE_SLICE, root, seed(), build_id="b_s06_adj_2")
    assert again.ok, [(g.name, g.status.value, g.detail) for g in again.gates]
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS and eg5.metrics["n_changed_partitions"] == 0


def test_상수는_두_네임스페이스에서_복제_없이_읽는다() -> None:
    assert ADJ.consts == ("corp_event.near_dup_window_days", *rules_s06.PRICE_MATCH_CONSTS)
    assert build._split_const_key("adj_factor", "corp_event.near_dup_window_days") == (
        "corp_event", "near_dup_window_days")
    assert build._split_const_key("adj_factor", "own_metric") == ("adj_factor", "own_metric")
    con = duckdb.connect()
    try:
        bl = Baseline({"corp_event": {"near_dup_window_days": 7},
                       "adj_factor": {k: 1 for k in rules_s06.PRICE_MATCH_CONSTS}})
        build.make_consts(con, ADJ, bl)
        assert con.execute("SELECT near_dup_window_days, price_match_window_sessions FROM _const"
                           ).fetchone() == (7, 1)
        with pytest.raises(KeyError, match="corp_event.near_dup_window_days"):
            build.make_consts(con, ADJ, Baseline({"adj_factor": {
                "near_dup_window_days": 7, **{k: 1 for k in rules_s06.PRICE_MATCH_CONSTS}}}))
    finally:
        con.close()


# ── 유상감자 (손 트리) ───────────────────────────────────────────────────────

_CR_COMMON = {"bfcr_tisstk_estk": 0, "atcr_tisstk_estk": 0, "cr_rt_estk_pct": None,
              "crstk_estk_cnt": 0, "available_basis": "derived"}
HAND_CR: list[dict[str, object]] = [
    # 절단본의 실제 3건 (우양에이치씨, 원자료 값 그대로)
    {"rcept_no": "20160608000216", "corp_code": "00450931", "cr_std": date(2015, 11, 26),
     "cr_mth": "자기주식 전량소각, 주식병합", "cr_rs": "재무구조개선",
     "bfcr_tisstk_ostk": 25735667, "atcr_tisstk_ostk": 11016503, "cr_rt_ostk_pct": 57.19,
     "available_date": date(2016, 6, 8), **_CR_COMMON},
    {"rcept_no": "20160608000221", "corp_code": "00450931", "cr_std": date(2015, 11, 28),
     "cr_mth": "주식병합", "cr_rs": "재무구조개선",
     "bfcr_tisstk_ostk": 208859671, "atcr_tisstk_ostk": 20885490, "cr_rt_ostk_pct": 90.0,
     "available_date": date(2016, 6, 8), **_CR_COMMON},
    {"rcept_no": "20180724000175", "corp_code": "00450931", "cr_std": date(2018, 10, 12),
     "cr_mth": "보통주 10주를 동일한 액면주식 1주로 무상병합", "cr_rs": "경영 효율 제고",
     "bfcr_tisstk_ostk": 136561969, "atcr_tisstk_ostk": 13656196, "cr_rt_ostk_pct": 90.0,
     "available_date": date(2018, 7, 24), **_CR_COMMON},
    # 손 사례 — 유상감자 결정공시(cr_rs 에 '유상'). ratio 는 있지만 시총 불변이 아니라 계수를
    # 못 낸다
    {"rcept_no": "20190510000001", "corp_code": "00450931", "cr_std": date(2019, 6, 14),
     "cr_mth": "주식소각", "cr_rs": "주주환원 목적 유상감자",
     "bfcr_tisstk_ostk": 12290633, "atcr_tisstk_ostk": 11061570, "cr_rt_ostk_pct": 10.0,
     "available_date": date(2019, 5, 10), **_CR_COMMON},
]


def test_유상감자_결정공시는_capred_paid로_factor_ok_false(tmp_path: Path, make_stage_tree) -> None:
    """절단본의 `stg_event_cr` 만 손 트리로 바꿔 끼운다(다른 테이블은 심볼릭 링크). corp_event
    에는 유·무상 축이 없어 S06 이 결정공시 본문(cr_mth·cr_rs)으로 판정한다 — 가격 매칭보다 먼저."""
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    for d in STAGE_SLICE.iterdir():
        if d.is_dir() and d.name != "stg_event_cr":
            os.symlink(d, stage_root / d.name)
    make_stage_tree(tmp_path, "stg_event_cr", HAND_CR, "receipt_axis", build_id="b_hand_cr")
    r = build_chain(stage_root, tmp_path / "equity", seed())
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == N_EVENTS + 1 and r.out_dir is not None
    f = rows(r.out_dir)
    paid = f["101970:capred:2019-06-14"]
    assert paid["factor_source"] == "capred_paid" and paid["factor_ok"] is False
    assert paid["price_factor"] == 1.0 and paid["share_factor"] == 1.0
    assert paid["apply_basis"] == "nominal" and paid["apply_date"] == date(2019, 6, 14)
    assert paid["available_date"] == date(2019, 5, 10)          # 공시일 < apply_date 다음 세션
    assert {e for e, x in f.items() if x["factor_ok"]} == OK_IDS
    x = _gate(r, "EG3_adj_factor").metrics
    assert x["n_capred_paid"] == 1 and x["n_by_factor_source"]["capred_paid"] == 1


# ── apply_date 판정 — 합성 가격 위에서 산출 SQL 직접 실행 ───────────────────

_CONST = {"near_dup_window_days": 5, "price_match_tol_rel": 0.15, "price_match_tol_abs": 0.05,
          "price_match_window_sessions": 40, "price_match_lookback_sessions": 5}
_EV_DEFAULT = {"corp_code": "C0000001", "effective_basis": "disclosure_body", "rcept_no": None,
               "announce_date": date(2019, 12, 2)}


def sessions(n: int, start: date = date(2020, 1, 6)) -> list[date]:
    """월~금 n 세션(공휴일 없음)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def _view(con: duckdb.DuckDBPyConnection, name: str, rows_: list[dict[str, object]],
          types: dict[str, str]) -> None:
    cols = list(types)
    values = ", ".join("(" + ", ".join(f"CAST({_lit(r.get(c))} AS {types[c]})" for c in cols) + ")"
                       for r in rows_)
    con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM (VALUES {values}) "
                f"AS t({', '.join(cols)})")


def _setup(con: duckdb.DuckDBPyConnection, events: list[dict[str, object]],
           prices: list[dict[str, object]], cal: list[date],
           cr: list[dict[str, object]] | None, const: dict[str, object] | None) -> None:
    """합성 입력 뷰(corp_event·price_daily·trading_calendar·stg_event_cr) + `_const`."""
    ev_types = {"event_id": "VARCHAR", "ticker": "VARCHAR", "corp_code": "VARCHAR",
                "event_type": "VARCHAR", "announce_date": "DATE", "effective_date": "DATE",
                "effective_basis": "VARCHAR", "ratio": "DOUBLE", "rcept_no": "VARCHAR",
                "source": "VARCHAR"}
    evs = []
    for e in events:
        full = {**_EV_DEFAULT, **e}
        full["event_id"] = f"{full['ticker']}:{full['event_type']}:{full['effective_date']}"
        evs.append(full)
    _view(con, "corp_event", evs, ev_types)
    _view(con, "price_daily", prices,
          {"ticker": "VARCHAR", "date": "DATE", "close": "DECIMAL(9,0)",
           "price_kind": "VARCHAR"})
    _view(con, "trading_calendar", [{"date": d} for d in cal], {"date": "DATE"})
    _view(con, "stg_event_cr", cr or [{"rcept_no": "x", "corp_code": "x", "cr_mth": None,
                                       "cr_rs": None}],
          {"rcept_no": "VARCHAR", "corp_code": "VARCHAR", "cr_mth": "VARCHAR",
           "cr_rs": "VARCHAR"})
    k = {**_CONST, **(const or {})}
    con.execute("CREATE OR REPLACE TEMP TABLE _const AS SELECT "
                + ", ".join(f"{_lit(v)} AS {c}" for c, v in k.items()))


def run_adj_sql(events: list[dict[str, object]], prices: list[dict[str, object]],
                cal: list[date], cr: list[dict[str, object]] | None = None,
                const: dict[str, object] | None = None) -> dict[str, dict[str, object]]:
    """`sql/adj_factor.sql` 을 합성 입력 뷰 위에서 그대로 실행한다(프레임·게이트 없이 산출식만)."""
    con = duckdb.connect()
    try:
        _setup(con, events, prices, cal, cr, const)
        rel = con.execute(_body())
        cols = [d[0] for d in rel.description]
        return {str(r[cols.index("event_id")]): dict(zip(cols, r, strict=True))
                for r in rel.fetchall()}
    finally:
        con.close()


def run_eg3(events: list[dict[str, object]], prices: list[dict[str, object]],
            cal: list[date], const: dict[str, object] | None = None):
    """같은 합성 입력 위에서 산출을 `out_pq` 로 올리고 `EG3_adj_factor` 만 돌린다."""
    from equity.gates import EquityGateContext

    con = duckdb.connect()
    try:
        _setup(con, events, prices, cal, None, const)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {_body()}")
        n_out = con.execute("SELECT count(*) FROM out_pq").fetchone()[0]   # type: ignore[index]
        k = {**_CONST, **(const or {})}
        bl = Baseline({"corp_event": {"near_dup_window_days": k["near_dup_window_days"]},
                       "adj_factor": {c: k[c] for c in rules_s06.PRICE_MATCH_CONSTS}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=int(n_out), n_reject=0, reject_by_reason={},
                                inputs={}, partition_hashes={}, baseline=bl)
        return rules_s06.eg3_adj_factor(ctx)
    finally:
        con.close()


def flat_prices(ticker: str, cal: list[date], close: float, *, jumps: dict[int, float],
                halt: tuple[int, int] | None = None) -> list[dict[str, object]]:
    """세션 i 의 종가 = 직전 종가 × jumps[i](없으면 1). halt=(a, b) 구간은 reference 행 — 종가는
    유지하되 jumps 가 있으면 참고가 행에도 새 기준가를 싣는다(KRX 관례)."""
    out: list[dict[str, object]] = []
    c = close
    for i, d in enumerate(cal):
        c = c * jumps.get(i, 1.0)
        kind = "reference" if halt and halt[0] <= i <= halt[1] else "trade"
        out.append({"ticker": ticker, "date": d, "close": round(c), "price_kind": kind})
    return out


def test_합성_명목_세션의_점프가_계수와_맞으면_nominal(tmp_path: Path) -> None:
    cal = sessions(80)
    ev = [{"ticker": "A00001", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00001", cal, 10000, jumps={30: 0.51})    # 기대 0.5, 잔여 0.02
    f = run_adj_sql(ev, px, cal)[f"A00001:split:{cal[30]}"]
    assert f["apply_basis"] == "nominal" and f["apply_date"] == cal[30]
    assert f["factor_ok"] is True and (f["price_factor"], f["share_factor"]) == (0.5, 2.0)
    assert f["available_date"] == cal[30]                        # min(announce, cal[31])


def test_합성_정지_뒤_재개일에_기준가가_바뀌는_감자는_price_matched_두_축_상이(
        tmp_path: Path) -> None:
    """FX-2-004. 기준일(세션 20)부터 정지(reference), 세션 31 재개일에 ×9.8 → 잔여 0.02 ≤ 0.135.
    명목 세션의 원수익률은 0(정지) 이라 nominal 이 아니다. 오프셋 +11 세션."""
    cal = sessions(80)
    ev = [{"ticker": "A00002", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000001", "announce_date": cal[5]}]
    px = flat_prices("A00002", cal, 1000, jumps={31: 9.8}, halt=(19, 30))
    f = run_adj_sql(ev, px, cal)[f"A00002:capred:{cal[20]}"]
    assert f["apply_basis"] == "price_matched" and f["apply_date"] == cal[31]
    assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
    assert (f["price_factor"], f["share_factor"]) == (10.0, 0.1)     # 두 축 상이
    assert f["available_date"] == cal[5]                          # 공시일 < cal[32]
    # 창 [−5, +40] 밖(세션 61)에서만 점프가 나면 못 찾는다
    px2 = flat_prices("A00002", cal, 1000, jumps={61: 9.8}, halt=(19, 30))
    g = run_adj_sql(ev, px2, cal)[f"A00002:capred:{cal[20]}"]
    assert g["apply_basis"] == "unmatched" and g["factor_source"] == "no_price_match"
    assert g["factor_ok"] is False and (g["price_factor"], g["share_factor"]) == (1.0, 1.0)
    assert g["apply_date"] == cal[20] and g["available_date"] == cal[5]


def test_합성_참고가_행에_먼저_실린_기준가는_그_행이_apply_date다(tmp_path: Path) -> None:
    """3차(서버 2차 실측): KRX 는 정지 중 참고가 행의 close 에 새 기준가를 먼저 싣는다 — 시계열
    점프는 거래 재개일(세션 31)이 아니라 정지 중 세션 27(reference) 에서 일어난다. 행 대 행 정의라
    세션 27 이 apply_date 이고, 재개일은 잔여 0 이라 후보가 아니다."""
    cal = sessions(80)
    ev = [{"ticker": "A00006", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000006", "announce_date": cal[5]}]
    px = flat_prices("A00006", cal, 1000, jumps={27: 9.9, 31: 1.02}, halt=(19, 30))
    assert px[27]["price_kind"] == "reference" and px[27]["close"] == 9900
    f = run_adj_sql(ev, px, cal)[f"A00006:capred:{cal[20]}"]
    assert f["apply_basis"] == "price_matched" and f["apply_date"] == cal[27]
    assert f["factor_ok"] is True and (f["price_factor"], f["share_factor"]) == (10.0, 0.1)
    # 직전 거래 종가 정의였다면 재개일 31 이 |1.02·9.9/10 − 1| = 0.01 로 매칭돼 정지 중 4개 참고가
    # 행(9,900)이 조정 전 가격으로 남았을 것이다 — 뷰가 조정하는 것은 행 시계열이다


def test_합성_ratio_1_은_주식수_불변이라_no_share_change(tmp_path: Path) -> None:
    """서버 2차 001360:split pf 1.0 — 액면가만 바뀌고 주식수는 그대로인 KRX 관측. 계수 1 이라 조정할
    것이 없고 ok 로 두면 EG8 이 그날 원수익률(재개일 +36%)을 점프로 잰다."""
    cal = sessions(80)
    ev = [{"ticker": "A00007", "event_type": "split", "effective_date": cal[30], "ratio": 1.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00007", cal, 10000, jumps={30: 1.36})
    f = run_adj_sql(ev, px, cal)[f"A00007:split:{cal[30]}"]
    assert f["factor_source"] == "no_share_change" and f["factor_ok"] is False
    assert (f["price_factor"], f["share_factor"]) == (1.0, 1.0)
    assert f["apply_basis"] == "nominal" and f["apply_date"] == cal[30]


def test_합성_복합_사건은_계수_곱으로_한_세션에_같이_적용된다(tmp_path: Path) -> None:
    """감자(cr, ratio 0.5) + KRX 액면병합(reverse_split, ratio 0.5) 명목 세션 20·22. 실제 점프는
    세션 26 에 ×4(= 2 × 2) 한 번 — 개별 비율(×2)로는 어느 날도 안 맞고 곱으로만 맞는다."""
    cal = sessions(80)
    ev = [{"ticker": "A00003", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000002", "announce_date": cal[5]},
          {"ticker": "A00003", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00003", cal, 1000, jumps={26: 4.0}, halt=(19, 25))
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00003:capred:{cal[20]}"], f[f"A00003:reverse_split:{cal[22]}"]
    for x in (a, b):
        assert x["apply_basis"] == "price_matched_combined" and x["apply_date"] == cal[26]
        assert x["factor_ok"] is True and (x["price_factor"], x["share_factor"]) == (2.0, 0.5)
    assert a["available_date"] == cal[5] and b["available_date"] == cal[22]
    # 점프가 ×2 뿐이면 둘 다 개별 매칭이 같은 날 → 우선순위 낮은 KRX 행을 same_day_suppressed
    px2 = flat_prices("A00003", cal, 1000, jumps={26: 2.0}, halt=(19, 25))
    g = run_adj_sql(ev, px2, cal)
    a2, b2 = g[f"A00003:capred:{cal[20]}"], g[f"A00003:reverse_split:{cal[22]}"]
    assert a2["apply_basis"] == "price_matched" and a2["factor_ok"] is True
    assert a2["apply_date"] == cal[26]
    assert b2["factor_source"] == "same_day_suppressed" and b2["factor_ok"] is False
    assert b2["apply_basis"] == "price_matched" and b2["apply_date"] == cal[26]
    assert (b2["price_factor"], b2["share_factor"]) == (1.0, 1.0)


def test_합성_명목일이_떨어진_성분의_공통_apply_date는_성분_창_기준이다(tmp_path: Path) -> None:
    """4차(서버 3차 EG3 FAIL `n_apply_outside_window` 3): 감자(명목 세션 20)와 액면병합(명목 50)이
    뒤쪽 명목일 +20 세션(70)에서 한 번에 ×4 조정 — 앞 멤버 자기 창 [15, 60] 밖이지만 성분 창
    [15, 90] 안이라 combined 유효(EG3 도 성분 창으로 판정). 점프가 성분 창 밖(95)이면 성분 전체
    no_price_match."""
    cal = sessions(120)
    ev = [{"ticker": "A00008", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000008", "announce_date": cal[5]},
          {"ticker": "A00008", "event_type": "reverse_split", "effective_date": cal[50],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[50]}]
    px = flat_prices("A00008", cal, 1000, jumps={70: 4.0}, halt=(19, 69))
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00008:capred:{cal[20]}"], f[f"A00008:reverse_split:{cal[50]}"]
    for x in (a, b):
        assert x["apply_basis"] == "price_matched_combined" and x["apply_date"] == cal[70]
        assert x["factor_ok"] is True and (x["price_factor"], x["share_factor"]) == (2.0, 0.5)
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    m = g.metrics
    assert m["n_apply_outside_window"] == 0 and m["n_combined_apply_inconsistent"] == 0
    assert m["apply_offset_sessions_max"] == 50            # 앞 멤버 기준 실측(창 40 초과)
    assert m["apply_offset_sessions_max_individual"] == 0
    assert m["apply_offset_sessions_max_combined"] == 50
    assert m["n_by_apply_basis"] == {"price_matched_combined": 2}
    # 성분 창 [15, 90] 밖(95)에서만 점프 → 성분 전체 no_price_match
    px2 = flat_prices("A00008", cal, 1000, jumps={95: 4.0}, halt=(19, 94))
    h = run_adj_sql(ev, px2, cal)
    assert {x["factor_source"] for x in h.values()} == {"no_price_match"}
    assert {x["apply_basis"] for x in h.values()} == {"unmatched"}
    g2 = run_eg3(ev, px2, cal)
    assert g2.status is GateStatus.PASS and g2.metrics["n_apply_outside_window"] == 0
    # 개별 매칭이 자기 창 밖에 놓이는 산출은 만들어질 수 없다 — 억지로 만들면 EG3 가 잡는다
    con = duckdb.connect()
    try:
        _setup(con, ev, px, cal, None, None)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {_body()}")
        con.execute("UPDATE out_pq SET apply_basis = 'price_matched'")   # 성분 → 개별로 위장
        from equity.gates import EquityGateContext

        bl = Baseline({"corp_event": {"near_dup_window_days": 5},
                       "adj_factor": {c: _CONST[c] for c in rules_s06.PRICE_MATCH_CONSTS}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=2, n_reject=0, reject_by_reason={}, inputs={},
                                partition_hashes={}, baseline=bl)
        bad = rules_s06.eg3_adj_factor(ctx)
    finally:
        con.close()
    assert bad.status is GateStatus.FAIL and bad.metrics["n_apply_outside_window"] == 1
    assert bad.metrics["n_ok_same_apply_date_individual"] == 1


def test_합성_소액_이벤트는_가격으로_못_가리므로_항상_nominal(tmp_path: Path) -> None:
    """5% 무상증자(m 0.048 ≤ tol_abs 0.05): 권리락일 원수익률이 +3%(잡음)라도 창 탐색 없이 명목."""
    cal = sessions(80)
    ev = [{"ticker": "A00004", "event_type": "bonus", "effective_date": cal[30], "ratio": 1.05,
           "source": "event_fric", "rcept_no": "20191202000003", "announce_date": cal[10]}]
    px = flat_prices("A00004", cal, 10000, jumps={30: 1.03, 45: 0.952})   # 45 세션에 '정답' 점프
    f = run_adj_sql(ev, px, cal)[f"A00004:bonus:{cal[30]}"]
    assert f["apply_basis"] == "nominal" and f["apply_date"] == cal[30]
    assert f["factor_ok"] is True and f["share_factor"] == 1.05
    # 같은 가격에 큰 비율(1주당 1주 = ratio 2, m 0.5)이면 명목일 +3% 는 못 맞고 창에서 못 찾는다
    ev2 = [{**ev[0], "ratio": 2.0}]
    g = run_adj_sql(ev2, px, cal)[f"A00004:bonus:{cal[30]}"]
    assert g["apply_basis"] == "unmatched" and g["factor_source"] == "no_price_match"


def test_합성_창_밖_뒤쪽_경계는_lookback_상수다(tmp_path: Path) -> None:
    """명목 세션 30, 점프가 세션 26(−4)이면 lookback 5 안 → price_matched; lookback 3 이면
    못 찾는다."""
    cal = sessions(80)
    ev = [{"ticker": "A00005", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000004", "announce_date": cal[10]}]
    px = flat_prices("A00005", cal, 10000, jumps={26: 0.5})
    f = run_adj_sql(ev, px, cal)[f"A00005:bonus:{cal[30]}"]
    assert f["apply_basis"] == "price_matched" and f["apply_date"] == cal[26]
    assert f["available_date"] == cal[10]
    g = run_adj_sql(ev, px, cal, const={"price_match_lookback_sessions": 3})
    assert g[f"A00005:bonus:{cal[30]}"]["apply_basis"] == "unmatched"


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_FX_N_003_share_factor를_역수로_두면_EG3_adj_factor만_fail(tmp_path: Path) -> None:
    """GATES §4 FX-N-003 → EG3-P04(시총 불변 곱 = 1) 위반. 앞 게이트는 그대로 pass, 뒤는
    upstream_failed."""
    sql = _body().replace("THEN f.ratio     ELSE 1 END         AS share_factor",
                          "THEN 1 / f.ratio ELSE 1 END         AS share_factor")
    assert sql != _body()
    rule = _variant(tmp_path, "adj_factor_n003", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    st = {g.name: g.status.value for g in r.gates}
    assert st["EG3_adj_factor"] == "fail" and st["EG8"] == "skip"
    assert all(st[n] == "pass" for n in ("EG0", "EG7", "EG1", "EG2", "EG3"))
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_ok_product_off_one"] == N_OK and m["n_ok_share_factor_ne_ratio"] == N_OK
    assert m["max_ok_product_dev"] > 0.9
    assert _gate(r, "EG8").detail == "upstream_failed"


def test_not_ok_행의_계수가_1이_아니면_EG3_adj_factor_fail(tmp_path: Path) -> None:
    sql = _body().replace("THEN f.ratio     ELSE 1 END         AS share_factor",
                          "THEN f.ratio     ELSE 2 END         AS share_factor")
    rule = _variant(tmp_path, "adj_factor_notok", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_adj_factor").metrics["n_not_ok_factor_ne_one"] == N_EVENTS - N_OK


def test_available_date를_공시일로_두면_EG3_adj_factor가_잡는다(tmp_path: Path) -> None:
    """EG2-P02 의 announce 축 대체 검사. 프레임 EG2 는 content_date_column 이 없어 통과한다."""
    sql = _body().replace("least(f.announce_date, nx.date)", "f.announce_date")
    assert sql != _body()
    rule = _variant(tmp_path, "adj_factor_avail", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG2").status is GateStatus.PASS
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_available_mismatch"] == 4 and m["n_available_before_announce"] == 0


def test_apply_date를_효력일로_두면_명목_세션_검사가_잡는다(tmp_path: Path) -> None:
    """토요일 기준일(2015-11-28·2018-10-13)을 그대로 apply_date 로 쓰면 캘린더 밖 2건 +
    nominal 행(10-13 near_dup)의 명목 세션 불일치 1건."""
    sql = _body().replace(
        "coalesce(r.apply_date, n.nominal_date)                          AS apply_date",
        "n.effective_date AS apply_date")
    assert sql != _body()
    sql = sql.replace("JOIN cal ca ON ca.date = f.apply_date",
                      "LEFT JOIN cal ca ON ca.date = f.apply_date")
    rule = _variant(tmp_path, "adj_factor_apply", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_apply_off_calendar"] == 2 and m["n_nominal_apply_ne_nominal_session"] == 1


def test_sql파일에_상수_하드코딩_없음() -> None:
    text = re.sub(r"--[^\n]*", "", ADJ.sql_path.read_text(encoding="utf-8"))
    nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text))
    assert nums <= {"0", "1", "2", "-1"}, sorted(nums)
