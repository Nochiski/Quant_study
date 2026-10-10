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
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG13", "EG3", "EG3_adj_factor", "EG8", "EG4", "EG5a"]
N_CORP_EVENTS = 8
# S06-2: 기준가 원천 신규 행 2(unknown_price_only, ok=false) — 247540 2022-05-09 · 900050 2011-02-16
PRICE_ONLY_IDS = {"247540:krx_base:2022-05-09", "900050:krx_base:2011-02-16"}
N_EVENTS = N_CORP_EVENTS + len(PRICE_ONLY_IDS)
N_OK = 3
OK_IDS = {"005930:split:2018-05-04", "005935:split:2018-05-04", "247540:bonus:2022-06-27"}
NO_MATCH_IDS = {"101970:capred:2015-11-26", "101970:capred:2015-11-28",
                "101970:capred:2018-10-12"}
RATIO_NULL_IDS = {"101970:capred:2018-02-23"}
NEAR_DUP_IDS = {"101970:capred:2018-10-13"}
# 손계산(S06-2, 기준가 원천): 247540 권리락일 KRX 기준가 124,700(= 135,900 − 11,200) / 직전 종가
# 497,400 → price_factor 0.2507(1/4 = 124,350 이 아니다) · share_factor = 1/price_factor(같은 날
# 주식수 불변) · 005930 53,000 → 51,900
PF_247540 = 124700 / 497400
SF_247540 = 497400 / 124700
MAX_ADJ_RETURN = 135900 / 124700 - 1
MAX_RAW_RETURN_005930 = 51900 / 2650000 - 1
# 절단본 기준가 후보(비ETF·구간 첫날 제외) 7 = (a) 3 + (c) 재발견 2(101970 2014-08-21 · 900050
# 2016-07-29: 직전 행 reference) + (d) 2. ETF 069500 후보 36 · 재상장 첫 행 2(036220 2024-03-13 ·
# 101970 2025-03-28)는 후보 밖
N_BP_CANDIDATES, N_BP_ETF, N_BP_SPAN_START, N_BP_REDISCOVERY = 7, 36, 2, 2


def seed() -> Baseline:
    """S01·S02·S05·S06 seed 병합 — 오케스트레이터가 baseline.json 에 병합하는 것과 같은 모양."""
    merged: dict[str, object] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s04.BASELINE_SEED, rules_s05.BASELINE_SEED,
              rules_s06.BASELINE_SEED):
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
    assert set(built.inputs) == {"corp_event", "price_daily", "trading_calendar", "stg_event_cr",
                                 "security", "security_span"}
    assert built.inputs["corp_event"] == "b_corp_event"       # equity 입력도 고정된다


def test_EG1은_corp_event의_MVP_이벤트_수_더하기_기준가_신규_행이고_격리는_없다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    """S06-2: 우변 = corp_event MVP 8 + 기준가 후보 중 (a) 에 소비되지 않고 (c) 재발견도 아닌
    것 2."""
    eg1 = _gate(built, "EG1").metrics
    assert eg1["lhs"] == eg1["rhs"] == N_EVENTS and eg1["n_reject"] == 0
    assert _gate(built, "EG7").metrics["reject_by_reason"] == {}
    assert {e for e in factors if ":krx_base:" in e} == PRICE_ONLY_IDS
    assert len([e for e in factors if ":krx_base:" not in e]) == N_CORP_EVENTS


def test_삼성전자_분할_계수는_1_50과_50이고_명목_세션에_적용된다(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-001 · FX-2-009. S06-2: 05-04 KRX 기준가 53,000(005935 42,500) = 직전 행(정지 마지막 날
    참고가) 2,650,000(2,125,000) × 0.02, 같은 날 주식수 ×50 → 곱 정확히 1 — 사건 비율(1/50)과
    맞아 계수·세션을 기준가로 교체(krx_base_price). 폴백(원수익률 −98.04% → nominal)은 안 쓴다.
    KRX 관측행은 announce = 관측일 = apply_date 라 available 도 그날이다."""
    for eid in ("005930:split:2018-05-04", "005935:split:2018-05-04"):
        f = factors[eid]
        assert f["price_factor"] == 1 / 50 and f["share_factor"] == 50.0
        assert f["price_factor"] * f["share_factor"] == 1          # type: ignore[operator]
        assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
        assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == date(2018, 5, 4)
        assert f["available_date"] == date(2018, 5, 4) == f["announce_date"]
        assert f["available_basis"] == "derived" and f["event_type"] == "split"
        assert f["corp_code"] == "00126380"


def test_무상증자는_1_4과_4이고_권리락일이_명목_세션이다(
        factors: dict[str, dict[str, object]]) -> None:
    """S06-2: 권리락일 06-27 KRX 기준가 124,700 / 직전 종가 497,400 = 0.2507 — 사건 비율 1/4 과
    잔여 0.0028 로 맞아 기준가로 교체. 같은 날 주식수 변화 없음(신주 상장 07-15) → share_factor =
    1/price_factor = 3.9888(corp_event.ratio 4.0 과 0.28% 차이, EG3 기록형). 폴백이었다면 원수익률
    −72.7% vs 기대 −75% → 잔여 0.093 ≤ max(0.15×0.75, 0.05) 로 nominal."""
    f = factors["247540:bonus:2022-06-27"]
    assert f["price_factor"] == pytest.approx(PF_247540) and f["factor_ok"] is True
    assert f["share_factor"] == pytest.approx(SF_247540)
    assert f["price_factor"] * f["share_factor"] == pytest.approx(1.0)   # type: ignore[operator]
    assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == date(2022, 6, 27)
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
    # S06-2: ok 3 은 전부 기준가 교체 · 신규 unknown_price_only 2 도 krx_base_price
    assert x["n_by_apply_basis"] == {"krx_base_price": 5, "nominal": 2, "unmatched": 3}
    assert x["n_ok_by_event_type_apply_basis"] == {"bonus:krx_base_price": 1,
                                                   "split:krx_base_price": 2}
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
                                       "no_price_match": 3, "ratio_null": 1,
                                       "unknown_price_only": 2}
    assert {e for e, f in factors.items() if f["factor_ok"]} == OK_IDS


def test_available_date는_min_공시_apply_date_다음_세션_이다(built: build.BuildResult,
                                                   factors: dict[str, dict[str, object]]) -> None:
    """캘린더 원자료로 독립 재계산. EG2-P02 announce 축을 못 쓰는 대신 EG3_adj_factor 가 이 식을
    본다. C-07(e1.25.0): KRX 기준가가 확정한 사건 교체 ok 행은 min(공시, apply_date) — 절단본 3건은
    공시 ≤ apply 라 값은 옛 식과 같다(공시가 늦은 경우는 합성 테스트)."""
    con = duckdb.connect()
    try:
        cal = sorted(r[0] for r in con.execute(
            "SELECT DISTINCT date FROM read_parquet("
            f"'{STAGE_SLICE / 'stg_index_daily' / 'v=*' / 'year=*' / '*.parquet'}')").fetchall())
    finally:
        con.close()
    for eid, f in factors.items():
        assert f["apply_date"] in cal                             # 캘린더 세션
        nominal = next(d for d in cal if d >= f["effective_date"])   # type: ignore[operator]
        assert f["apply_date"] == nominal                         # 절단본은 전부 명목 세션
        nxt = cal[cal.index(f["apply_date"]) + 1]
        if eid in PRICE_ONLY_IDS:            # ⑤ 계수 행(e1.26.0): min(공시, 적용일) = 적용일
            assert f["available_date"] == f["apply_date"] == f["announce_date"]
            assert f["available_date"] != nxt                     # 옛 식(다음 세션)이 아니다
        elif f["apply_basis"] == "krx_base_price" and f["factor_ok"]:   # C-07
            assert f["available_date"] == min(f["announce_date"],        # type: ignore[type-var]
                                              f["apply_date"])
        else:
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
    # 규칙 e1.15.0 — as-of 는 상수가 아니라 KRX 확정 행의 최신일에서 유도한다
    assert m["asof_basis"] == "derived:max(price_daily.date WHERE basis='krx')"
    assert m["asof_used"] == "2026-08-20"
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
    assert all(e["apply_basis"] == "krx_base_price" for e in ev.values() if e["factor_ok"])
    # S06-2 신규 unknown_price_only 2 는 ok=false 라 원수익률만 기록된다 (900050 2011-02-16 +5.4%)
    assert m["n_unadjusted_events_with_price"] == 2
    assert m["max_abs_raw_return_unadjusted"] == pytest.approx(10800 / 10250 - 1)


def run_eg8(events: list[dict[str, object]], prices: list[dict[str, object]],
            cal: list[date], jump_max: float):
    """합성 입력 위에서 산출을 `out_pq` 로 올리고 EG8 만 돌린다. EG8 이 읽는 가격 축(OHLC·거래량·
    basis·표식)은 같은 합성 종가로 채운다. 상수는 이 호출의 baseline 으로만 준다(운영 상수 불변)."""
    from equity.gates import EquityGateContext

    con = duckdb.connect()
    try:
        _setup(con, events, prices, cal, None, None)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {_body()}")
        con.execute("CREATE TEMP TABLE px_full AS SELECT ticker, date, close AS open, "
                    "close AS high, close AS low, close, "
                    "CAST(1000 AS DECIMAL(13,0)) AS volume_shr, price_kind, 'krx' AS basis, "
                    "FALSE AS corp_action_pending FROM price_daily")
        con.execute("DROP VIEW price_daily")
        con.execute("CREATE TEMP VIEW price_daily AS SELECT * FROM px_full")
        n_out = con.execute("SELECT count(*) FROM out_pq").fetchone()[0]   # type: ignore[index]
        bl = Baseline({"adj_factor": {"adj_return_jump_max": jump_max,
                                      "adj_volume_ratio_band": 3.0}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=int(n_out), n_reject=0, reject_by_reason={},
                                inputs={}, partition_hashes={}, baseline=bl)
        return rules_s06.eg8_adj_jump(ctx)
    finally:
        con.close()


def test_D6_5_계수_행_적용일_수정수익률이_상한을_넘으면_EG8이_폐기한다(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """D6-5 폐기형(10-09 승격): 계수 행(unknown_price_only, 기준가 ×0.9)의 적용일 수정수익률 =
    종가 ÷ 기준가 − 1 = +60%. 상한 0.5 면 FAIL(`n_price_only_return_jump_over` 1), 상한 1.0 이면
    PASS. 스위치(PRICE_ONLY_JUMP_GATE)를 끄면 같은 입력이 기록형으로만 남아 PASS 한다 — 스위치가
    실제로 판정을 바꾼다."""
    cal = sessions(80)
    px = flat_prices("A00036", cal, 10000, jumps={40: 0.9 * 1.6}, base={40: 0.9})
    f = run_adj_sql([], px, cal)[f"A00036:krx_base:{cal[40]}"]
    assert f["price_resolution"] == "price_only" and f["price_only_factor"] == 0.9
    over = run_eg8([], px, cal, jump_max=0.5)
    assert over.status is GateStatus.FAIL
    assert over.metrics["n_price_only_return_jump_over"] == 1
    assert over.detail.startswith("n_price_only_return_jump_over=1")
    assert over.metrics["max_abs_price_only_adj_return"] == pytest.approx(14400 / 9000 - 1)
    assert over.metrics["n_return_jump_over"] == 0               # ok 계수 축은 깨끗하다
    under = run_eg8([], px, cal, jump_max=1.0)
    assert under.status is GateStatus.PASS
    assert under.metrics["n_price_only_return_jump_over"] == 0
    monkeypatch.setattr(rules_s06, "PRICE_ONLY_JUMP_GATE", False)
    off = run_eg8([], px, cal, jump_max=0.5)
    assert off.status is GateStatus.PASS and off.metrics["price_only_jump_gate"] is False
    assert off.metrics["n_price_only_return_jump_over"] == 1     # 기록은 남는다


def test_점프_상수가_없으면_EG8은_폐기되지만_metric은_계산한다(tmp_path: Path) -> None:
    """상수 미등재 SKIP(no_baseline)은 허용표 밖이라 판을 폐기한다(K1-7a) — 측정치는 남긴다."""
    bl = seed()
    keep = {k: v for k, v in bl.table("adj_factor").items()
            if k in rules_s06.PRICE_MATCH_CONSTS
            or k in rules_s06.BASE_PRICE_CONSTS}            # 매칭·기준가 상수는 산출식이 요구한다
    r = build_chain(STAGE_SLICE, tmp_path / "equity", Baseline({**bl.data, "adj_factor": keep}))
    assert not r.ok
    eg8 = _gate(r, "EG8")
    assert eg8.status is GateStatus.FAIL and eg8.metrics["skip_reason"] == "no_baseline"
    assert eg8.metrics["missing_metric"] == "adj_factor.adj_return_jump_max"
    # as-of 는 상수가 아니라 유도값이라 상수를 다 빼도 계속 잡힌다(e1.15.0)
    assert eg8.metrics["asof_used"] == "2026-08-20"
    assert eg8.metrics["asof_basis"] == "derived:max(price_daily.date WHERE basis='krx')"
    assert eg8.metrics["asof_used"] == "2026-08-20"
    assert eg8.metrics["max_abs_adj_return_jump"] == pytest.approx(MAX_ADJ_RETURN)


def test_파티션은_year_effective_date이고_컬럼_순서는_선언과_같다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    assert built.out_dir is not None
    for f in factors.values():
        assert int(str(f["year"])) == f["effective_date"].year   # type: ignore[union-attr]
    assert {p["path"].split("/")[1] for p in built.partitions} == {   # type: ignore[union-attr]
        "year=2011", "year=2015", "year=2018", "year=2022"}       # 2011 = 900050 기준가 신규 행
    cols = [c for c in next(iter(factors.values())) if c != "year"]
    assert cols == list(ADJ.columns)


def test_같은_inputs_재빌드는_파티션_해시가_같다(built: build.BuildResult) -> None:
    root = built.out_dir.parent.parent            # type: ignore[union-attr]
    again = build.build_table(ADJ, STAGE_SLICE, root, seed(), build_id="b_s06_adj_2")
    assert again.ok, [(g.name, g.status.value, g.detail) for g in again.gates]
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS and eg5.metrics["n_changed_partitions"] == 0


def test_상수는_두_네임스페이스에서_복제_없이_읽는다() -> None:
    own = (*rules_s06.PRICE_MATCH_CONSTS, *rules_s06.BASE_PRICE_CONSTS)
    assert ADJ.consts == ("corp_event.near_dup_window_days", *own,
                          "corp_event.krx_share_change_tol")
    assert build._split_const_key("adj_factor", "corp_event.near_dup_window_days") == (
        "corp_event", "near_dup_window_days")
    assert build._split_const_key("adj_factor", "own_metric") == ("adj_factor", "own_metric")
    con = duckdb.connect()
    try:
        bl = Baseline({"corp_event": {"near_dup_window_days": 7, "krx_share_change_tol": 0.001},
                       "adj_factor": dict.fromkeys(own, 1)})
        build.make_consts(con, ADJ, bl)
        assert con.execute("SELECT near_dup_window_days, price_match_window_sessions, "
                           "CAST(krx_share_change_tol AS DOUBLE), base_match_window_sessions "
                           "FROM _const").fetchone() == (7, 1, 0.001, 1)
        with pytest.raises(KeyError, match="corp_event.near_dup_window_days"):
            build.make_consts(con, ADJ, Baseline({
                "corp_event": {"krx_share_change_tol": 0.001},
                "adj_factor": {"near_dup_window_days": 7, **dict.fromkeys(own, 1)}}))
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
          "price_match_window_sessions": 40, "price_match_lookback_sessions": 5,
          # S06-2 기준가 원천 (seed 값) + corp_event.krx_share_change_tol
          "base_price_tol_rel": 0.002, "base_match_window_sessions": 5,
          "factor_product_tol_base": 0.01, "krx_share_change_tol": 0.001}
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
    if not rows_:                                   # 빈 입력(사건 없음)도 타입 있는 빈 뷰로
        sel = ", ".join(f"CAST(NULL AS {types[c]}) AS {c}" for c in cols)
        con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT {sel} WHERE FALSE")
        return
    values = ", ".join("(" + ", ".join(f"CAST({_lit(r.get(c))} AS {types[c]})" for c in cols) + ")"
                       for r in rows_)
    con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM (VALUES {values}) "
                f"AS t({', '.join(cols)})")


def _setup(con: duckdb.DuckDBPyConnection, events: list[dict[str, object]],
           prices: list[dict[str, object]], cal: list[date],
           cr: list[dict[str, object]] | None, const: dict[str, object] | None,
           spans: list[tuple[str, int, date]] | None = None,
           sec_types: dict[str, str] | None = None) -> None:
    """합성 입력 뷰(corp_event·price_daily·trading_calendar·stg_event_cr·security·security_span)
    + `_const`. `spans` 를 주면 그 (ticker, span_seq, first_date) 가 security_span(S06-2 구간 첫날
    제외 축), 없으면 티커별 첫 가격 행 하나. 티커가 'ETF' 로 시작하면 sec_type etf, `sec_types`
    로 티커별 종류를 덮어쓴다(⑤ D6-1 종류 축)."""
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
    # S06-2: 기준가·주식수 축. 합성 행이 안 주면 base = close(기준가 사건 없음) · shares_out NULL
    prices = [{"base_price_krw": p.get("close"), **p} for p in prices]
    _view(con, "price_daily", prices,
          {"ticker": "VARCHAR", "date": "DATE", "close": "DECIMAL(9,0)",
           "price_kind": "VARCHAR", "base_price_krw": "DECIMAL(10,0)",
           "shares_out": "DECIMAL(13,0)"})
    _view(con, "trading_calendar", [{"date": d} for d in cal], {"date": "DATE"})
    tickers = sorted({str(p["ticker"]) for p in prices} | {str(e["ticker"]) for e in evs})
    _view(con, "security", [{"ticker": t,
                             "sec_type": (sec_types or {}).get(
                                 t, "etf" if t.startswith("ETF") else "common"),
                             "corp_code": "C" + t} for t in tickers],
          {"ticker": "VARCHAR", "sec_type": "VARCHAR", "corp_code": "VARCHAR"})
    span_rows: list[dict[str, object]] = (
        [{"ticker": t, "span_seq": s, "first_date": d} for t, s, d in spans] if spans else
        [{"ticker": t, "span_seq": 1,
          "first_date": min(p["date"] for p in prices if p["ticker"] == t)}
         for t in tickers if any(p["ticker"] == t for p in prices)])
    _view(con, "security_span",
          span_rows or [{"ticker": "x", "span_seq": 1, "first_date": cal[0]}],
          {"ticker": "VARCHAR", "span_seq": "BIGINT", "first_date": "DATE"})
    _view(con, "stg_event_cr", cr or [{"rcept_no": "x", "corp_code": "x", "cr_mth": None,
                                       "cr_rs": None}],
          {"rcept_no": "VARCHAR", "corp_code": "VARCHAR", "cr_mth": "VARCHAR",
           "cr_rs": "VARCHAR"})
    k = {**_CONST, **(const or {})}
    con.execute("CREATE OR REPLACE TEMP TABLE _const AS SELECT "
                + ", ".join(f"{_lit(v)} AS {c}" for c, v in k.items()))


def run_adj_sql(events: list[dict[str, object]], prices: list[dict[str, object]],
                cal: list[date], cr: list[dict[str, object]] | None = None,
                const: dict[str, object] | None = None,
                spans: list[tuple[str, int, date]] | None = None,
                sec_types: dict[str, str] | None = None) -> dict[str, dict[str, object]]:
    """`sql/adj_factor.sql` 을 합성 입력 뷰 위에서 그대로 실행한다(프레임·게이트 없이 산출식만)."""
    con = duckdb.connect()
    try:
        _setup(con, events, prices, cal, cr, const, spans, sec_types)
        rel = con.execute(_body())
        cols = [d[0] for d in rel.description]
        return {str(r[cols.index("event_id")]): dict(zip(cols, r, strict=True))
                for r in rel.fetchall()}
    finally:
        con.close()


def run_eg3(events: list[dict[str, object]], prices: list[dict[str, object]],
            cal: list[date], const: dict[str, object] | None = None,
            spans: list[tuple[str, int, date]] | None = None,
            sec_types: dict[str, str] | None = None, tamper: str | None = None,
            sql: str | None = None):
    """같은 합성 입력 위에서 산출을 `out_pq` 로 올리고 `EG3_adj_factor` 만 돌린다. `tamper` 는
    산출을 올린 뒤 실행할 변조 SQL, `sql` 은 산출 SQL 변형(둘 다 부정 픽스처)."""
    from equity.gates import EquityGateContext

    con = duckdb.connect()
    try:
        _setup(con, events, prices, cal, None, const, spans, sec_types)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {sql or _body()}")
        if tamper is not None:
            con.execute(tamper)
        n_out = con.execute("SELECT count(*) FROM out_pq").fetchone()[0]   # type: ignore[index]
        k = {**_CONST, **(const or {})}
        bl = Baseline({"corp_event": {"near_dup_window_days": k["near_dup_window_days"],
                                      "krx_share_change_tol": k["krx_share_change_tol"]},
                       "adj_factor": {c: k[c] for c in (*rules_s06.PRICE_MATCH_CONSTS,
                                                        *rules_s06.BASE_PRICE_CONSTS)}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=int(n_out), n_reject=0, reject_by_reason={},
                                inputs={}, partition_hashes={}, baseline=bl)
        return rules_s06.eg3_adj_factor(ctx)
    finally:
        con.close()


def flat_prices(ticker: str, cal: list[date], close: float, *, jumps: dict[int, float],
                halt: tuple[int, int] | None = None, base: dict[int, float] | None = None,
                shares: float = 1000.0, share_jumps: dict[int, float] | None = None,
                ) -> list[dict[str, object]]:
    """세션 i 의 종가 = 직전 종가 × jumps[i](없으면 1). halt=(a, b) 구간은 reference 행 — 종가는
    유지하되 jumps 가 있으면 참고가 행에도 새 기준가를 싣는다(KRX 관례).

    S06-2: `base[i]` = 세션 i 의 KRX 기준가 / 직전 종가(없으면 기준가 = 직전 종가 = 기준가 사건
    없음) · `shares_out` = shares × Π share_jumps(같은 날 주식수 변화)."""
    out: list[dict[str, object]] = []
    c = close
    s = shares
    for i, d in enumerate(cal):
        prev = c
        c = c * jumps.get(i, 1.0)
        s = s * (share_jumps or {}).get(i, 1.0)
        kind = "reference" if halt and halt[0] <= i <= halt[1] else "trade"
        b = round(prev * base[i]) if base and i in base else round(prev)
        out.append({"ticker": ticker, "date": d, "close": round(c), "price_kind": kind,
                    "base_price_krw": b, "shares_out": round(s)})
    return out


def test_합성_명목_세션의_기준가가_계수와_맞으면_그_세션(tmp_path: Path) -> None:
    """(a) 명목 세션 기준가 ×0.5 · 주식수 ×2 → 그 세션(S06-2 (a) 가 기준가 비로 교체). E-1: 원수익률만
    ×0.51 로 맞고 기준가가 그대로면(조정 없음) ok 가 아니다 — no_base_price_evidence."""
    cal = sessions(80)
    ev = [{"ticker": "A00001", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00001", cal, 10000, jumps={30: 0.51}, base={30: 0.5},
                     share_jumps={30: 2.0})
    f = run_adj_sql(ev, px, cal)[f"A00001:split:{cal[30]}"]
    assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == cal[30]
    assert f["factor_ok"] is True and (f["price_factor"], f["share_factor"]) == (0.5, 2.0)
    assert f["available_date"] == cal[30]                        # min(announce, apply)
    g = run_adj_sql(ev, flat_prices("A00001", cal, 10000, jumps={30: 0.51}), cal)
    x = g[f"A00001:split:{cal[30]}"]
    assert x["factor_source"] == "no_base_price_evidence" and x["factor_ok"] is False
    assert x["apply_basis"] == "unmatched" and x["apply_date"] == cal[30]


def test_합성_정지_뒤_재개일에_기준가가_바뀌는_감자는_창에서_찾고_두_축_상이(
        tmp_path: Path) -> None:
    """FX-2-004. 기준일(세션 20)부터 정지(reference), 세션 31 재개일에 기준가 ×10 · 주식수 ×0.1
    (종가 ×9.8). 명목 세션은 기준가가 안 바뀌어(정지) (a) 가 아니고 (b) 창 [15, 60] 에서 기준가로
    찾는다(±base_match_window_sessions 5 밖 — 오프셋 +11 세션)."""
    cal = sessions(80)
    ev = [{"ticker": "A00002", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000001", "announce_date": cal[5]}]
    px = flat_prices("A00002", cal, 1000, jumps={31: 9.8}, halt=(19, 30), base={31: 10.0},
                     share_jumps={31: 0.1})
    f = run_adj_sql(ev, px, cal)[f"A00002:capred:{cal[20]}"]
    assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == cal[31]
    assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
    assert (f["price_factor"], f["share_factor"]) == (10.0, 0.1)     # 두 축 상이
    assert f["available_date"] == cal[5]                          # 공시일 < apply
    # 창 [−5, +40] 밖(세션 61)에서만 점프가 나면 못 찾는다
    px2 = flat_prices("A00002", cal, 1000, jumps={61: 9.8}, halt=(19, 30))
    g = run_adj_sql(ev, px2, cal)[f"A00002:capred:{cal[20]}"]
    assert g["apply_basis"] == "unmatched" and g["factor_source"] == "no_price_match"
    assert g["factor_ok"] is False and (g["price_factor"], g["share_factor"]) == (1.0, 1.0)
    assert g["apply_date"] == cal[20] and g["available_date"] == cal[5]


def test_합성_참고가_행에_먼저_실린_기준가는_그_행이_apply_date다(tmp_path: Path) -> None:
    """3차(서버 2차 실측): KRX 는 정지 중 참고가 행의 close 에 새 기준가를 먼저 싣는다 — 시계열
    점프는 거래 재개일(세션 31)이 아니라 정지 중 세션 27(reference) 에서 일어난다. 행 대 행 정의라
    세션 27 이 apply_date 이고, 재개일은 잔여 0 이라 후보가 아니다. E-1: 기준가도 세션 27 참고가
    행에서 바뀐다(재개일 31 은 기준가 = 직전 행 종가라 기준가 후보 밖)."""
    cal = sessions(80)
    ev = [{"ticker": "A00006", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000006", "announce_date": cal[5]}]
    px = flat_prices("A00006", cal, 1000, jumps={27: 9.9, 31: 1.02}, halt=(19, 30),
                     base={27: 9.9})
    assert px[27]["price_kind"] == "reference" and px[27]["close"] == 9900
    f = run_adj_sql(ev, px, cal)[f"A00006:capred:{cal[20]}"]
    assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == cal[27]
    assert f["factor_ok"] is True and f["price_factor"] == pytest.approx(9.9)
    assert f["share_factor"] == pytest.approx(1 / 9.9)        # 같은 날 주식수 불변 → 1/r
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
    세션 26 에 기준가 ×4(= 2 × 2) 한 번 — 개별 비율(×2)로는 어느 날도 안 맞고 곱으로만 맞는다.
    (c) 가 그 세션을 찾고 S06-2 (a) 가 성분을 한 단위로 기준가에 교체한다."""
    cal = sessions(80)
    ev = [{"ticker": "A00003", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000002", "announce_date": cal[5]},
          {"ticker": "A00003", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00003", cal, 1000, jumps={26: 4.0}, halt=(19, 25), base={26: 4.0},
                     share_jumps={26: 0.25})
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00003:capred:{cal[20]}"], f[f"A00003:reverse_split:{cal[22]}"]
    for x in (a, b):
        assert x["apply_basis"] == "krx_base_price" and x["apply_date"] == cal[26]
        assert x["factor_ok"] is True and (x["price_factor"], x["share_factor"]) == (2.0, 0.5)
    assert a["available_date"] == cal[5] and b["available_date"] == cal[22]
    # 기준가가 ×2 뿐이면 둘 다 개별 매칭이 같은 날 → 우선순위 낮은 KRX 행을 same_day_suppressed
    px2 = flat_prices("A00003", cal, 1000, jumps={26: 2.0}, halt=(19, 25), base={26: 2.0},
                      share_jumps={26: 0.5})
    g = run_adj_sql(ev, px2, cal)
    a2, b2 = g[f"A00003:capred:{cal[20]}"], g[f"A00003:reverse_split:{cal[22]}"]
    assert a2["apply_basis"] == "krx_base_price" and a2["factor_ok"] is True
    assert a2["apply_date"] == cal[26]
    assert b2["factor_source"] == "same_day_suppressed" and b2["factor_ok"] is False
    assert b2["apply_basis"] == "price_matched" and b2["apply_date"] == cal[26]
    assert (b2["price_factor"], b2["share_factor"]) == (1.0, 1.0)


def test_합성_명목일이_떨어진_성분의_공통_apply_date는_성분_창_기준이다(tmp_path: Path) -> None:
    """4차(서버 3차 EG3 FAIL `n_apply_outside_window` 3): 감자(명목 세션 20)와 액면병합(명목 50)이
    뒤쪽 명목일 +20 세션(70)에서 한 번에 ×4 조정 — 앞 멤버 자기 창 [15, 60] 밖이지만 성분 창
    [15, 90] 안이라 combined 유효(EG3 도 성분 창으로 판정). 점프가 성분 창 밖(95)이면 성분 전체
    no_price_match. E-1: 세션 70 의 기준가 ×4 · 주식수 ×0.25 가 성분 곱의 근거라 ok 행은
    krx_base_price 로 교체된다(EG3 는 교체 행도 같은 (ticker, apply_date) 단위 창으로 본다)."""
    cal = sessions(120)
    ev = [{"ticker": "A00008", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000008", "announce_date": cal[5]},
          {"ticker": "A00008", "event_type": "reverse_split", "effective_date": cal[50],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[50]}]
    px = flat_prices("A00008", cal, 1000, jumps={70: 4.0}, halt=(19, 69), base={70: 4.0},
                     share_jumps={70: 0.25})
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00008:capred:{cal[20]}"], f[f"A00008:reverse_split:{cal[50]}"]
    for x in (a, b):
        assert x["apply_basis"] == "krx_base_price" and x["apply_date"] == cal[70]
        assert x["factor_ok"] is True and (x["price_factor"], x["share_factor"]) == (2.0, 0.5)
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    m = g.metrics
    assert m["n_apply_outside_window"] == 0 and m["n_combined_apply_inconsistent"] == 0
    assert m["apply_offset_sessions_max"] == 50            # 앞 멤버 기준 실측(창 40 초과)
    assert m["apply_offset_sessions_max_individual"] == 0
    assert m["n_by_apply_basis"] == {"krx_base_price": 2}
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


def test_합성_창_밖_뒤쪽_경계는_lookback_상수다(tmp_path: Path) -> None:
    """명목 세션 30, 기준가 사건이 세션 23(−7). lookback 7 이면 (b) 창 안 → 그 세션; 기본 5 면 창
    밖이고 ±base_match_window_sessions(5) 로도 안 닿아 못 찾는다(기준가는 신규 unknown_krx 로
    따로 선다)."""
    cal = sessions(80)
    ev = [{"ticker": "A00005", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000004", "announce_date": cal[10]}]
    px = flat_prices("A00005", cal, 10000, jumps={23: 0.5}, base={23: 0.5}, share_jumps={23: 2.0})
    f = run_adj_sql(ev, px, cal, const={"price_match_lookback_sessions": 7})
    x = f[f"A00005:bonus:{cal[30]}"]
    assert x["apply_basis"] == "krx_base_price" and x["apply_date"] == cal[23]
    assert x["available_date"] == cal[10] and set(f) == {f"A00005:bonus:{cal[30]}"}
    g = run_adj_sql(ev, px, cal)
    assert g[f"A00005:bonus:{cal[30]}"]["apply_basis"] == "unmatched"
    assert g[f"A00005:krx_base:{cal[23]}"]["event_type"] == "unknown_krx"


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_FX_N_003_share_factor를_역수로_두면_EG3_adj_factor만_fail(tmp_path: Path) -> None:
    """GATES §4 FX-N-003 → EG3-P04(시총 불변 곱 = 1) 위반. 앞 게이트는 그대로 pass, 뒤는
    upstream_failed."""
    sql = _body().replace("THEN o.sf_raw ELSE 1 END             AS share_factor",
                          "THEN 1 / o.sf_raw ELSE 1 END         AS share_factor")
    assert sql != _body()
    rule = _variant(tmp_path, "adj_factor_n003", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    st = {g.name: g.status.value for g in r.gates}
    assert st["EG3_adj_factor"] == "fail" and st["EG8"] == "skip"
    assert all(st[n] == "pass" for n in ("EG0", "EG7", "EG1", "EG2", "EG3"))
    m = _gate(r, "EG3_adj_factor").metrics
    # S06-2: ok 3 행은 전부 krx_base_price 라 share=ratio 검사 밖(기록형 dev) — 곱 검사(tol_base
    # 0.01)가 잡는다. 가격 축은 그대로라 기준가 재계산 검사는 0
    assert m["n_ok_product_off_one"] == N_OK and m["n_ok_share_factor_ne_ratio"] == 0
    assert m["n_krx_price_factor_mismatch"] == 0
    assert m["max_ok_product_dev"] > 0.9
    assert _gate(r, "EG8").detail == "upstream_failed"


def test_not_ok_행의_계수가_1이_아니면_EG3_adj_factor_fail(tmp_path: Path) -> None:
    sql = _body().replace("THEN o.sf_raw ELSE 1 END             AS share_factor",
                          "THEN o.sf_raw ELSE 2 END             AS share_factor")
    assert sql != _body()
    rule = _variant(tmp_path, "adj_factor_notok", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_adj_factor").metrics["n_not_ok_factor_ne_one"] == N_EVENTS - N_OK


def test_available_date를_공시일로_두면_EG3_adj_factor가_잡는다(tmp_path: Path) -> None:
    """EG2-P02 의 announce 축 대체 검사. 프레임 EG2 는 content_date_column 이 없어 통과한다."""
    sql = _body().replace("least(o.announce_date, nx.date)", "o.announce_date")
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
    sql = sql.replace("JOIN cal ca ON ca.date = o.apply_date",
                      "LEFT JOIN cal ca ON ca.date = o.apply_date")
    rule = _variant(tmp_path, "adj_factor_apply", sql)
    r = build_chain(STAGE_SLICE, tmp_path / "equity", _seed_as(seed(), rule.name), rule=rule)
    assert r.status is build.BuildStatus.GATE_FAILED
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_apply_off_calendar"] == 2 and m["n_nominal_apply_ne_nominal_session"] == 1


# ── S06-2 KRX 기준가 원천 — 절단본 분류 + 합성 (a)(b)(c)(d) · 부정 픽스처 ─────────

def test_절단본_기준가_후보_분류_a3_b0_c2_d2(built: build.BuildResult,
                                       factors: dict[str, dict[str, object]]) -> None:
    """절단본 기준가 ≠ 직전 행 close(직전 행 있음) 59행 중 비ETF 9 = 후보 밖 2(재상장 첫 행 036220
    2024-03-13 · 101970 2025-03-28) + 후보 7 = (a) 3 + (c) 2 + (d) 2. ETF 069500 의 36 후보(분배락)
    는 원천 밖 — 그중 14 는 상장좌수 변화와 곱이 우연히 0.01 안이라 (b) 로 넣으면 가짜 ok 계수가
    된다."""
    x = _gate(built, "EG3_adj_factor").metrics
    assert x["n_base_price_candidates"] == N_BP_CANDIDATES
    assert x["n_base_price_etf_excluded"] == N_BP_ETF
    assert x["n_base_price_span_start_excluded"] == N_BP_SPAN_START
    assert x["n_base_price_replaced_units"] == 3 and x["n_krx_replaced_rows"] == 3
    assert x["n_unknown_krx"] == 0 and x["n_base_price_share_change_free"] == 0
    assert x["n_base_price_rediscovery"] == N_BP_REDISCOVERY
    assert x["n_unknown_price_only"] == 2 and x["n_base_price_only_free"] == 2
    assert x["n_base_price_only_by_month"] == {"2011-02": 1, "2022-05": 1}
    assert x["n_krx_base_inconsistent"] == 0 and x["n_ok_without_base_price_event"] == 0
    assert x["krx_share_factor_ratio_dev_max"] == pytest.approx(abs(SF_247540 / 4 - 1))
    assert x["n_krx_by_event_type_factor_source"] == {
        "bonus:mktcap_neutral": 1, "split:mktcap_neutral": 2,
        "unknown_price_only:unknown_price_only": 2}
    for eid in PRICE_ONLY_IDS:
        f = factors[eid]
        assert f["event_type"] == "unknown_price_only" and f["factor_ok"] is False
        assert f["factor_source"] == "unknown_price_only" and f["apply_basis"] == "krx_base_price"
        assert (f["price_factor"], f["share_factor"]) == (1.0, 1.0)
        assert f["effective_date"] == f["announce_date"] == f["apply_date"]
    f = factors["247540:krx_base:2022-05-09"]
    # ⑤ 계수 행이라 공개일이 적용일(e1.26.0, 옛 식은 다음 세션 05-10)
    assert f["apply_date"] == date(2022, 5, 9) and f["available_date"] == date(2022, 5, 9)
    assert f["corp_code"] == factors["247540:bonus:2022-06-27"]["corp_code"]   # security 에서
    assert "036220:krx_base:2024-03-13" not in factors        # 재상장 첫 행
    assert not any(e.startswith("069500:") for e in factors)   # ETF


def test_합성_기준가_사건이_창_안에_있으면_no_price_match_사건이_살아난다(tmp_path: Path) -> None:
    """(iv) 감자(ratio 0.1, 명목 세션 20), 정지 뒤 재개일 25 의 원수익률 ×12.5(재개 첫날 +25%,
    제한폭 없음)라 사건 매칭(tol 0.135)은 어느 날도 못 찾는다 → 2차라면 no_price_match. KRX 기준가는
    25 에 정확히 ×10 → (a) 교체: price_factor 10 · share_factor = 같은 날 주식수 비 0.1 ·
    apply 25."""
    cal = sessions(80)
    ev = [{"ticker": "A00010", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000010", "announce_date": cal[5]}]
    px_no_base = flat_prices("A00010", cal, 1000, jumps={25: 12.5}, halt=(19, 24))
    g = run_adj_sql(ev, px_no_base, cal)[f"A00010:capred:{cal[20]}"]
    assert g["factor_source"] == "no_price_match" and g["apply_basis"] == "unmatched"
    px = flat_prices("A00010", cal, 1000, jumps={25: 12.5}, halt=(19, 24), base={25: 10.0},
                     share_jumps={25: 0.1})
    f = run_adj_sql(ev, px, cal)[f"A00010:capred:{cal[20]}"]
    assert f["apply_basis"] == "krx_base_price" and f["apply_date"] == cal[25]
    assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
    assert (f["price_factor"], f["share_factor"]) == (10.0, 0.1)
    assert f["available_date"] == cal[5]                    # min(공시, 다음 세션) 그대로
    # 주식수 변화가 같은 날 없으면 share_factor = 1/price_factor (시총 불변)
    px2 = flat_prices("A00010", cal, 1000, jumps={25: 12.5}, halt=(19, 24), base={25: 10.0})
    f2 = run_adj_sql(ev, px2, cal)[f"A00010:capred:{cal[20]}"]
    assert f2["apply_basis"] == "krx_base_price" and (f2["price_factor"], f2["share_factor"]) == (
        10.0, 0.1)
    # E-1: ±5 밖(세션 27)이어도 사건 창 [15, 60] 안이면 (b) 가 기준가로 찾는다 — 신규 행 없음
    px27 = flat_prices("A00010", cal, 1000, jumps={27: 12.5}, halt=(19, 26), base={27: 10.0},
                       share_jumps={27: 0.1})
    h27 = run_adj_sql(ev, px27, cal)
    assert set(h27) == {f"A00010:capred:{cal[20]}"}
    assert h27[f"A00010:capred:{cal[20]}"]["apply_date"] == cal[27]
    # 사건 창 밖(세션 62)이면 못 살아난다 → (b) 신규 unknown_krx 가 대신 선다
    px3 = flat_prices("A00010", cal, 1000, jumps={62: 12.5}, halt=(19, 61), base={62: 10.0},
                      share_jumps={62: 0.1})
    h = run_adj_sql(ev, px3, cal)
    assert h[f"A00010:capred:{cal[20]}"]["factor_source"] == "no_price_match"
    n = h[f"A00010:krx_base:{cal[62]}"]
    assert n["event_type"] == "unknown_krx" and n["factor_ok"] is True
    assert (n["price_factor"], n["share_factor"]) == (10.0, 0.1)
    g3 = run_eg3(ev, px3, cal)
    assert g3.status is GateStatus.PASS, g3.detail
    assert g3.metrics["n_unknown_krx_ok"] == 1 and g3.metrics["n_no_price_match"] == 1


def test_합성_기준가_비율과_사건_비율이_어긋나면_krx_base_inconsistent(tmp_path: Path) -> None:
    """(i) 분할 ratio 2(pf 0.5) 명목 세션 30, 종가도 ×0.5(2차라면 nominal ok). 기준가 r = 0.5 로
    사건과 맞지만 같은 날 주식수가 ×3 → 곱 1.5 ≠ 1 → 교체하되 ok=false(계수 1). 기준가가 r = 0.8 로
    사건 비율과 안 맞으면 (a) 로 묶이지 않고, ok 사건의 apply_date 에 기준가 후보가 있으므로 사건은
    krx_base_inconsistent, 후보는 (d) unknown_price_only 신규 행 — 같은 날 둘 다 ok 일 수 없다."""
    cal = sessions(80)
    ev = [{"ticker": "A00011", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00011", cal, 10000, jumps={30: 0.5}, base={30: 0.5}, share_jumps={30: 3.0})
    f = run_adj_sql(ev, px, cal)
    a = f[f"A00011:split:{cal[30]}"]
    assert a["factor_source"] == "krx_base_inconsistent" and a["factor_ok"] is False
    assert a["apply_basis"] == "krx_base_price" and a["apply_date"] == cal[30]
    assert (a["price_factor"], a["share_factor"]) == (1.0, 1.0)
    assert len(f) == 1                                        # 후보는 (a) 에 소비됐다
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_krx_base_inconsistent"] == 1 and g.metrics["n_ok"] == 0
    # 비율 불일치(r 0.8 vs pf 0.5) — 반증 규칙
    px2 = flat_prices("A00011", cal, 10000, jumps={30: 0.5}, base={30: 0.8})
    h = run_adj_sql(ev, px2, cal)
    b = h[f"A00011:split:{cal[30]}"]
    assert b["factor_source"] == "krx_base_inconsistent" and b["factor_ok"] is False
    assert b["apply_basis"] == "nominal"                       # 매칭 결과 유지
    d = h[f"A00011:krx_base:{cal[30]}"]
    assert d["event_type"] == "unknown_price_only" and d["factor_ok"] is False
    g2 = run_eg3(ev, px2, cal)
    assert g2.status is GateStatus.PASS, g2.detail
    assert g2.metrics["n_ok"] == 0 and g2.metrics["n_unknown_price_only"] == 1
    # E-1: 기준가 사건이 없으면 원수익률만 맞아도 ok 가 아니다(옛 2차 폴백 nominal ok 폐지)
    c = run_adj_sql(ev, flat_prices("A00011", cal, 10000, jumps={30: 0.5}), cal)
    assert c[f"A00011:split:{cal[30]}"]["factor_source"] == "no_base_price_evidence"
    assert c[f"A00011:split:{cal[30]}"]["factor_ok"] is False


def test_합성_정정_공시가_적용일보다_늦어도_기준가로_확정된_계수는_적용일에_공개된다(
        tmp_path: Path) -> None:
    """C-07(N-26 4.1): 002070 2026-07-31 무상증자는 announce 가 정정 공시 접수일이라 apply_date
    보다 늦고, 옛 규칙 available = min(announce, apply 다음 세션) 이 다음 세션이 되어 전방 조정이
    하루 늦게 접혔다(07-31 수정수익률 −35.0% → 08-03 +159.6%). KRX 기준가가 apply 세션에 비율을
    확인한 계수(사건 교체 krx_base_price, ok)는 그 세션에 알 수 있다 → available = min(announce,
    apply_date). 공시가 앞선 사건은 그대로 공시일이고, 기준가 근거가 없는 미해결 행(E-1 뒤로는
    원수익률만 맞던 옛 nominal 계수가 여기로 온다)은 옛 규칙 그대로."""
    from equity.gates import EquityGateContext

    cal = sessions(80)
    ev = [{"ticker": "A00016", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000016", "announce_date": cal[33]}]
    px = flat_prices("A00016", cal, 10000, jumps={30: 0.5}, base={30: 0.5})
    f = run_adj_sql(ev, px, cal)[f"A00016:bonus:{cal[30]}"]
    assert f["apply_basis"] == "krx_base_price" and f["factor_ok"] is True
    assert f["apply_date"] == cal[30] and f["announce_date"] == cal[33]
    assert f["available_date"] == cal[30]                    # 옛 규칙이면 다음 세션 cal[31]
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_available_mismatch"] == 0
    # 공시가 apply 보다 앞서면 공시일 그대로(다른 행 불변)
    early = run_adj_sql([{**ev[0], "announce_date": cal[10]}], px, cal)
    assert early[f"A00016:bonus:{cal[30]}"]["available_date"] == cal[10]
    # 기준가 사건이 없으면(E-1: 미해결 no_base_price_evidence) 옛 규칙 — 다음 세션
    nom = run_adj_sql(ev, flat_prices("A00016", cal, 10000, jumps={30: 0.5}), cal)
    assert nom[f"A00016:bonus:{cal[30]}"]["factor_source"] == "no_base_price_evidence"
    assert nom[f"A00016:bonus:{cal[30]}"]["available_date"] == cal[31]
    # EG3 독립 재계산도 새 규칙이다 — 산출을 옛 규칙(다음 세션)으로 되돌리면 잡는다
    con = duckdb.connect()
    try:
        _setup(con, ev, px, cal, None, None)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {_body()}")
        con.execute(f"UPDATE out_pq SET available_date = DATE '{cal[31]}'")
        bl = Baseline({"corp_event": {"near_dup_window_days": 5, "krx_share_change_tol": 0.001},
                       "adj_factor": {c: _CONST[c] for c in (*rules_s06.PRICE_MATCH_CONSTS,
                                                             *rules_s06.BASE_PRICE_CONSTS)}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=1, n_reject=0, reject_by_reason={}, inputs={},
                                partition_hashes={}, baseline=bl)
        bad = rules_s06.eg3_adj_factor(ctx)
    finally:
        con.close()
    assert bad.status is GateStatus.FAIL and bad.metrics["n_available_mismatch"] == 1


def test_합성_기준가_신규_unknown_krx_정상_행은_적용일에_공개되고_나머지_신규_행은_다음_세션(
        tmp_path: Path) -> None:
    """C-07 후속(N-26 4.1): unknown_krx 정상 행(곱 검사 통과)은 기준가 r 과 주식수 비 S 가 모두
    그날 KRX 일별 행에서 오므로 available = apply_date(= announce = 그날). 정상 아닌 신규 행
    (unknown_krx krx_base_inconsistent · unknown_price_only)은 ⑤ 계수 행(e1.26.0)이면 같은 이유로
    그날, 계수 행이 아니면(D6-1 제외 종류 등) 옛 식 그대로 다음 세션."""
    cal = sessions(80)
    ok = run_adj_sql([], flat_prices("A00019", cal, 10000, jumps={40: 0.1}, base={40: 0.1},
                                     share_jumps={40: 10.0}), cal)[f"A00019:krx_base:{cal[40]}"]
    assert ok["event_type"] == "unknown_krx" and ok["factor_ok"] is True
    assert ok["available_date"] == cal[40] == ok["apply_date"]       # 옛 규칙이면 cal[41]
    # 곱 검사 실패(r 0.1 × S 3 = 0.3) → krx_base_inconsistent — ⑤ 계수 행이라 그날
    bad_px = flat_prices("A00019", cal, 10000, jumps={40: 0.1}, base={40: 0.1},
                         share_jumps={40: 3.0})
    bad = run_adj_sql([], bad_px, cal)[f"A00019:krx_base:{cal[40]}"]
    assert bad["event_type"] == "unknown_krx" and bad["factor_source"] == "krx_base_inconsistent"
    assert bad["price_resolution"] == "price_only" and bad["available_date"] == cal[40]
    # 회귀 가드: 계수 행이 아니면(펀드 — D6-1) 옛 식 다음 세션
    fund = {"A00019": "fund"}
    bad_f = run_adj_sql([], bad_px, cal, sec_types=fund)[f"A00019:krx_base:{cal[40]}"]
    assert bad_f["price_resolution"] == "unresolved" and bad_f["available_date"] == cal[41]
    # 주식수 불변 → unknown_price_only — 계수 행이면 그날, 아니면 다음 세션
    po_px = flat_prices("A00019", cal, 10000, jumps={40: 0.9}, base={40: 0.9})
    po = run_adj_sql([], po_px, cal)[f"A00019:krx_base:{cal[40]}"]
    assert po["event_type"] == "unknown_price_only" and po["available_date"] == cal[40]
    po_f = run_adj_sql([], po_px, cal, sec_types=fund)[f"A00019:krx_base:{cal[40]}"]
    assert po_f["available_date"] == cal[41]
    for px in (flat_prices("A00019", cal, 10000, jumps={40: 0.1}, base={40: 0.1},
                           share_jumps={40: 10.0}),
               flat_prices("A00019", cal, 10000, jumps={40: 0.1}, base={40: 0.1},
                           share_jumps={40: 3.0})):
        g = run_eg3([], px, cal)
        assert g.status is GateStatus.PASS, g.detail
        assert g.metrics["n_available_mismatch"] == 0


@pytest.mark.parametrize(("r", "expect"), [(0.52, True), (0.56, False)])
def test_C04_기준가_확인과_adj_factor_짝_규칙은_같은_경계를_낸다(r: float, expect: bool) -> None:
    """ratio 2.0(pf 0.5, m 0.5) → tol = max(0.15 × 0.5, 0.05) = 0.075. D 기준가 r 0.52(잔여 0.04)는
    corp_event 가 D 로 확인하고 adj_factor (a) 도 같은 날 기준가로 교체한다. r 0.56(잔여 0.12)은
    둘 다 아니다. 한쪽 식(예: corp_event 의 m = |min(ratio, 1/ratio) − 1|)만 바뀌면 여기서
    갈린다."""
    from test_equity_s05_event import run_event_sql

    cal = sessions(40)
    d_end = cal[-1]
    px = flat_prices("A00020", cal, 10000, jumps={39: r}, base={39: r})
    listing = [{"ticker": "A00020", "date": d, "list_shrs": 1_000_000} for d in cal]
    fric = [{"rcept_no": "20200301000020", "corp_code": "CA00020",
             "nstk_asstd": d_end + dt.timedelta(days=3), "nstk_ascnt_ps_ostk_ratio": 1.0,
             "available_date": cal[30]}]
    ce = [e for e in run_event_sql(cal, listing, fric=fric, prices=px).values()
          if e["event_type"] == "bonus"]
    confirms = [e["effective_date"] for e in ce] == [d_end]
    ev = [{"ticker": "A00020", "event_type": "bonus", "effective_date": d_end, "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20200301000020", "announce_date": cal[30]}]
    pairs = run_adj_sql(ev, px, cal)[f"A00020:bonus:{d_end}"]["apply_basis"] == "krx_base_price"
    assert (confirms, pairs) == (expect, expect)


def test_C04_미래_기준일_소액_무상증자는_캘린더_끝에_ok_계수를_만들지_않는다() -> None:
    """C-04(N-25 Q3): 1주당 0.03주(ratio 1.03, m 0.029 ≤ tol_abs) 무상증자의 기준일이 캘린더 끝 D
    뒤 10일. 옛 corp_event 는 효력일 D 를 냈고 adj_factor 의 소액 경로(jump_mag ≤ tol_abs, 가격
    확인 없음)가 D 에 ok 계수를 붙였다 — 가짜 조정. 고친 뒤 D 기준가가 1/1.03 을 확인할 때만
    효력일 D(→ 기준가 교체 ok), 아니면 corp_event 에서 대기라 adj_factor 행이 없다."""
    from test_equity_s05_event import run_event_sql

    cal = sessions(60)
    d_end = cal[-1]
    listing = [{"ticker": "A00017", "date": d, "list_shrs": 1_000_000} for d in cal]
    fric = [{"rcept_no": "20200301000017", "corp_code": "CA00017",
             "nstk_asstd": d_end + dt.timedelta(days=10), "nstk_ascnt_ps_ostk_ratio": 0.03,
             "available_date": cal[50]}]

    def chain(px: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        ev = [e for e in run_event_sql(cal, listing, fric=fric, prices=px).values()
              if e["event_type"] == "bonus"]
        return run_adj_sql(ev, px, cal)

    flat = chain(flat_prices("A00017", cal, 10000, jumps={}))
    assert [e for e, x in flat.items() if x["factor_ok"] and x["apply_date"] == d_end] == []
    # D 의 기준가가 1/1.03 을 확인하면 효력일 D — 기준가 교체 ok 계수
    ok = chain(flat_prices("A00017", cal, 10000, jumps={59: 1 / 1.03}, base={59: 1 / 1.03}))
    x = ok[f"A00017:bonus:{d_end}"]
    assert x["factor_ok"] is True and x["apply_basis"] == "krx_base_price"
    assert x["apply_date"] == d_end


def test_합성_기준가만_바뀌고_주식수_불변_전일_거래면_unknown_price_only(tmp_path: Path) -> None:
    """(ii) 사건 없음. 세션 40 기준가 ×0.9(권리락 류) — 신규 행 ok=false · 계수 1 · effective =
    announce = apply = 그날 · corp_code 는 security. ⑤(e1.26.0): 이 행이 계수 행(r 0.9)이라
    available 도 그날(옛 식은 다음 세션)."""
    cal = sessions(80)
    px = flat_prices("A00012", cal, 10000, jumps={40: 0.9}, base={40: 0.9})
    f = run_adj_sql([], px, cal)
    assert set(f) == {f"A00012:krx_base:{cal[40]}"}
    d = f[f"A00012:krx_base:{cal[40]}"]
    assert d["event_type"] == "unknown_price_only" and d["factor_ok"] is False
    assert d["factor_source"] == "unknown_price_only" and d["apply_basis"] == "krx_base_price"
    assert (d["price_factor"], d["share_factor"]) == (1.0, 1.0)
    assert d["effective_date"] == d["announce_date"] == d["apply_date"] == cal[40]
    assert d["available_date"] == cal[40] and d["corp_code"] == "CA00012"
    assert d["price_resolution"] == "price_only" and d["price_only_factor"] == 0.9
    g = run_eg3([], px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_unknown_price_only"] == 1
    assert g.metrics["n_base_price_only_by_month"] == {cal[40].strftime("%Y-%m"): 1}
    # 같은 날 주식수가 바뀌면 (b) unknown_krx — 곱 검사 통과 시 ok
    px_b = flat_prices("A00012", cal, 10000, jumps={40: 0.1}, base={40: 0.1},
                       share_jumps={40: 10.0})
    b = run_adj_sql([], px_b, cal)[f"A00012:krx_base:{cal[40]}"]
    assert b["event_type"] == "unknown_krx" and b["factor_ok"] is True
    assert (b["price_factor"], b["share_factor"]) == (0.1, 10.0)
    assert b["available_date"] == cal[40]          # C-07 후속: 정상 unknown_krx 는 그날
    # ETF 는 후보 밖 — 분배락 + 설정·환매 좌수 변화가 곱을 우연히 통과해도 행이 없다
    px_etf = flat_prices("ETF001", cal, 10000, jumps={40: 0.998}, base={40: 0.998},
                         share_jumps={40: 1.002})
    assert run_adj_sql([], px_etf, cal) == {}
    g_etf = run_eg3([], px_etf, cal)
    assert g_etf.status is GateStatus.PASS
    assert g_etf.metrics["n_base_price_etf_excluded"] == 1
    assert g_etf.metrics["n_base_price_candidates"] == 0


def test_합성_전일_무거래_뒤_기준가_변화는_재발견이라_행이_없다(tmp_path: Path) -> None:
    """(iii) 정지(35~39) 뒤 세션 40 기준가 ×0.7, 주식수 불변 → 정지 재개 가격 재발견. 행은 없고
    EG3 이 n_base_price_rediscovery 로 센다. 재상장 첫 행(구간 첫날)도 후보 밖."""
    cal = sessions(80)
    px = flat_prices("A00013", cal, 10000, jumps={40: 0.7}, base={40: 0.7}, halt=(35, 39))
    assert run_adj_sql([], px, cal) == {}
    g = run_eg3([], px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_base_price_candidates"] == 1
    assert g.metrics["n_base_price_rediscovery"] == 1 and g.metrics["n_unknown_price_only"] == 0
    # 구간 첫날: 세션 0~20 구간 1, 세션 50~ 구간 2 — 50 의 기준가(×5, 옛 구간 종가 대비)는 후보 밖
    px2 = [p for i, p in enumerate(flat_prices("A00013", cal, 10000, jumps={50: 5.0},
                                                base={50: 5.0}, share_jumps={50: 0.2}))
           if i <= 20 or i >= 50]
    spans = [("A00013", 1, cal[0]), ("A00013", 2, cal[50])]
    assert run_adj_sql([], px2, cal, spans=spans) == {}
    g2 = run_eg3([], px2, cal, spans=spans)
    assert g2.status is GateStatus.PASS
    assert g2.metrics["n_base_price_span_start_excluded"] == 1
    # 구간을 하나로 보면 같은 행이 (b) unknown_krx ok 가 된다 — 구간 축이 가르는 것을 확인
    h = run_adj_sql([], px2, cal, spans=[("A00013", 1, cal[0])])
    assert set(h) == {f"A00013:krx_base:{cal[50]}"}
    assert h[f"A00013:krx_base:{cal[50]}"]["factor_ok"] is True


def test_합성_복합_성분은_기준가_곱으로_같이_교체된다(tmp_path: Path) -> None:
    """감자(0.5) + 액면병합(0.5) 명목 20·22, 종가 세션 26 ×4(2차: combined). 기준가 26 ×4 =
    성분 곱 → 멤버 전부 apply 26 · krx_base_price. 루트(min event_id = capred)가 잔여를 갖고 다른
    멤버는 원래 계수 — 성분 곱 = r · share 곱 = 같은 날 주식수 비 0.25."""
    cal = sessions(80)
    ev = [{"ticker": "A00014", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000014", "announce_date": cal[5]},
          {"ticker": "A00014", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00014", cal, 1000, jumps={26: 4.0}, halt=(19, 25), base={26: 4.0},
                     share_jumps={26: 0.25})
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00014:capred:{cal[20]}"], f[f"A00014:reverse_split:{cal[22]}"]
    for x in (a, b):
        assert x["apply_basis"] == "krx_base_price" and x["apply_date"] == cal[26]
        assert x["factor_ok"] is True
        assert x["price_factor"] * x["share_factor"] == pytest.approx(1.0)  # type: ignore[operator]
    assert a["price_factor"] * b["price_factor"] == pytest.approx(4.0)    # type: ignore[operator]
    assert a["share_factor"] * b["share_factor"] == pytest.approx(0.25)   # type: ignore[operator]
    assert (b["price_factor"], b["share_factor"]) == (2.0, 0.5)
    assert len(f) == 2
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_base_price_replaced_units"] == 1 and g.metrics["n_krx_replaced_rows"] == 2
    assert g.metrics["n_apply_outside_window"] == 0


def test_기준가_행_계수를_기준가_비율과_다르게_두면_EG3가_잡는다(tmp_path: Path) -> None:
    """부정 픽스처: 산출의 krx_base_price ok 행 price_factor 를 2배로 (share 도 맞춰 곱은 1) →
    `n_krx_price_factor_mismatch`(price_daily 기준가에서 독립 재계산) 가 잡는다."""
    from equity.gates import EquityGateContext

    cal = sessions(80)
    px = flat_prices("A00015", cal, 10000, jumps={40: 0.1}, base={40: 0.1}, share_jumps={40: 10.0})
    con = duckdb.connect()
    try:
        _setup(con, [], px, cal, None, None)
        con.execute(f"CREATE OR REPLACE TEMP TABLE out_pq AS {_body()}")
        con.execute("UPDATE out_pq SET price_factor = price_factor * 2, "
                    "share_factor = share_factor / 2 WHERE factor_ok")
        bl = Baseline({"corp_event": {"near_dup_window_days": 5, "krx_share_change_tol": 0.001},
                       "adj_factor": {c: _CONST[c] for c in (*rules_s06.PRICE_MATCH_CONSTS,
                                                             *rules_s06.BASE_PRICE_CONSTS)}})
        ctx = EquityGateContext(con=con, rule=ADJ, out_view="out_pq", reject_view=None,
                                pinned={}, n_out=1, n_reject=0, reject_by_reason={}, inputs={},
                                partition_hashes={}, baseline=bl)
        bad = rules_s06.eg3_adj_factor(ctx)
    finally:
        con.close()
    assert bad.status is GateStatus.FAIL
    assert bad.metrics["n_krx_price_factor_mismatch"] == 1
    assert bad.metrics["n_unknown_krx_ok_share_factor_bad"] == 1
    assert bad.metrics["n_ok_product_off_one"] == 0                # 곱은 여전히 1


def test_정지_뒤_재개하지_않는_종목의_사건은_no_bar_after_apply(tmp_path: Path) -> None:
    """DEFECT-10 — 적용일 이후 실거래 세션이 없으면 표시한다. 사건을 버리지는 않는다.

    커널은 사건 시점 이후 그 종목의 바가 있는 세션을 반드시 찾는다
    (`backtest_engine/engine/loop.py::_settlement_session`). 못 찾으면 예외를 던져 **run 전체**가
    죽는다 — 그 종목 하나를 건너뛰는 것이 아니다. 어댑터는 `price_kind='reference'` 행을 Bar 로
    내지 않으므로, 정지된 뒤 데이터 끝까지 재개하지 않은 종목의 감자·병합이 정확히 그 경우다
    (서버 실측 26건 · 25종목, 전부 `status='suspended'`).

    그렇다고 사건을 버릴 수는 없다 — 정지 중이라도 보유 수량은 실제로 바뀌고, 서버 26건 중 18건은
    거래소가 정지 기간에 기준가를 공표했다. 그래서 equity 는 **사실만 싣고** 정산 정책은 소비자와
    커널이 고른다.
    """
    cal = sessions(80)
    # 2:1 병합 — 주식수 ×0.5, 기준가 ×2 로 시총이 보존된다(계수가 성립하는 사건).
    merge = dict(jumps={55: 2.0}, base={55: 2.0}, share_jumps={55: 0.5})
    # 세션 50 에서 거래가 끊기고 캘린더 끝까지 참고가 행만 남는다 — 재개하지 않는다.
    px = flat_prices("A00013", cal, 10000, halt=(50, 79), **merge)
    row = run_adj_sql([], px, cal)[f"A00013:krx_base:{cal[55]}"]
    assert row["factor_ok"] is True, "계수 자체는 나온다 — 표시는 계수 성립과 별개다"
    assert (row["price_factor"], row["share_factor"]) == (2.0, 0.5)  # 곱 = 1
    assert row["apply_date"] == cal[55]
    assert row["no_bar_after_apply"] is True

    # 대조군: 같은 사건이지만 정지가 끝나고 다시 거래되면 거짓이다.
    px_ok = flat_prices("A00014", cal, 10000, halt=(50, 60), **merge)
    ok = run_adj_sql([], px_ok, cal)[f"A00014:krx_base:{cal[55]}"]
    assert ok["factor_ok"] is True and ok["no_bar_after_apply"] is False


# ── E-1 KRX 기준가 근거 요구(QL-E 리뷰 MAJOR-2, e1.28.0) ─────────────────────────
# 옛 판정은 창 안 **원수익률**이 기대 점프와 맞는 세션을 골랐다 — 그날 KRX 기준가가 직전 종가와
# 같아(조정 없음) 시장 등락이 우연히 맞은 날에도 ok 계수를 접었다. 새 판정은 (a)(b)(c) 모두 고른
# 세션의 기준가 비(기준가 ÷ 직전 행 종가)가 계수와 맞아야 하고, 못 찾으면 no_base_price_evidence.
# 아래 픽스처는 리뷰어가 로컬 판·v3 사본으로 재현한 네 사건의 모양이다(가격은 실측 비율).

def _ev_capred(ticker: str, n0: int, ratio: float, cal: list[date]) -> dict[str, object]:
    return {"ticker": ticker, "event_type": "capred", "effective_date": cal[n0], "ratio": ratio,
            "source": "event_cr", "rcept_no": f"2019120200{ticker[-4:]}", "announce_date": cal[5]}


@pytest.mark.parametrize(("ratio", "noise_n", "noise_ret", "shares_n"), [
    (31_0 / 34_6, 55, 11980 / 10920, 30),    # 069080: 소각 34.6M→31.0M, 06-15 +9.7% 에 접혔다
    (50 / 55, 40, 7410 / 7040, 28),          # 091700: 55M→50M, 05-22 +5.3% 에 접혔다
])
def test_E1_기준가가_안_바뀐_자기주식_소각형_감자는_no_base_price_evidence(
        ratio: float, noise_n: int, noise_ret: float, shares_n: int) -> None:
    """069080·091700: 주식수만 줄고(자기주식 소각) KRX 기준가는 창 안 어디서도 안 바뀌었다. 옛
    판정은 (b) 창 탐색이 시장 등락일(원수익률 잔여 ≤ 0.05)을 골라 ok 로 접어 그날 수익률을
    −1.8%·−4.3% 로 바꿨다. 새 판정: 기준가 근거가 없으니 미해결 · 계수 1 · 적용일은 명목 세션."""
    cal = sessions(80)
    ev = [_ev_capred("A00069", 20, ratio, cal)]
    px = flat_prices("A00069", cal, 10000, jumps={noise_n: noise_ret},
                     share_jumps={shares_n: ratio})
    f = run_adj_sql(ev, px, cal)
    assert set(f) == {f"A00069:capred:{cal[20]}"}               # 기준가 사건 없음 → 신규 행 없음
    x = f[f"A00069:capred:{cal[20]}"]
    assert x["factor_ok"] is False and x["factor_source"] == "no_base_price_evidence"
    assert x["apply_basis"] == "unmatched" and x["apply_date"] == cal[20]
    assert (x["price_factor"], x["share_factor"], x["price_only_factor"]) == (1.0, 1.0, 1.0)
    assert x["price_resolution"] == "unresolved"
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_ok"] == 0
    assert g.metrics["n_by_factor_source"] == {"no_base_price_evidence": 1}


def test_E1_240600형_무상증자는_권리락일_기준가에_접히고_뒤_급락에는_안_접힌다() -> None:
    """240600: 무상증자 1주당 0.2(ratio 1.2) 권리락 07-29 — 기준가 2,095 ÷ 전일 종가 2,505 = 0.8363,
    그날 종가 1,948(원수익률 −22.2%, 잔여 0.067 > 0.05 라 옛 (a) 실패). 신주 상장 08-25(주식수만
    ×1.198). 옛 판정은 (b) 가 09-15 급락(−17.0%, 잔여 0.004)을 골라 ok 로 접고, 07-29 기준가는
    unknown_price_only 로 따로 남겼다 — 같은 사건이 두 번(⑤ 가격 축 0.836 + 계수 1.2). 새 판정:
    (a) 명목 세션 기준가 비가 맞아 그날 교체(krx_base_price, 계수 = 기준가 비), 09-15 는 원수익률
    그대로, 07-29 기준가는 이 사건이 소비해 신규 행이 없다."""
    cal = sessions(80)
    ev = [{"ticker": "A00240", "event_type": "bonus", "effective_date": cal[30], "ratio": 1.2,
           "source": "event_fric", "rcept_no": "20191202000240", "announce_date": cal[20]}]
    px = flat_prices("A00240", cal, 2505, jumps={30: 1948 / 2505, 63: 1281 / 1544},
                     base={30: 2095 / 2505}, share_jumps={49: 1.1979})
    f = run_adj_sql(ev, px, cal)
    assert set(f) == {f"A00240:bonus:{cal[30]}"}                # 07-29 기준가는 이 사건 몫
    x = f[f"A00240:bonus:{cal[30]}"]
    assert x["factor_ok"] is True and x["factor_source"] == "mktcap_neutral"
    assert x["apply_basis"] == "krx_base_price" and x["apply_date"] == cal[30]
    assert x["price_factor"] == pytest.approx(2095 / 2505)
    assert x["share_factor"] == pytest.approx(2505 / 2095)      # 같은 날 주식수 불변 → 1/r
    assert x["available_date"] == cal[20] and x["price_resolution"] == "factor"
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_unknown_price_only"] == 0 and g.metrics["n_krx_replaced_rows"] == 1


def test_E1_291230형_명목일_급등은_기준가_근거가_없어_감자는_미해결이고_실제_감자일은_가격_전용_계수(
        ) -> None:
    """291230: 감자 결정(ratio 0.7927, 기준일 07-14)의 명목 세션 원수익률 +29.9% 가 기대 +26.2% 와
    잔여 0.03 으로 맞아 옛 (a) nominal 로 접혔다(그날 수정수익률 +3.0%) — 그날 기준가는 전일 종가
    그대로였다. 실제 감자는 정지 뒤 08-14(기준가 ×5 · 주식수 ×0.5445, 시총 불변 아님 → KRX 액면병합
    행과 성분으로 묶어도 곱 2.32 가 기준가 비 5 와 안 맞는다). 새 판정: 감자는 기준가 근거 없음 →
    미해결(no_base_price_evidence)이되 창 안 ⑤ 단위가 있어 price_only_near, 08-14 는 기준가 신규 행이
    ⑤ 계수 행(r = 5)."""
    cal = sessions(90)
    ev = [_ev_capred("A00291", 20, 0.7927, cal),
          {"ticker": "A00291", "event_type": "reverse_split", "effective_date": cal[43],
           "ratio": 0.5445, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[43]}]
    px = flat_prices("A00291", cal, 485, jumps={20: 630 / 485, 43: 2740 / 588}, halt=(32, 42),
                     base={43: 5.0}, share_jumps={43: 0.5445})
    f = run_adj_sql(ev, px, cal)
    cr, rs = f[f"A00291:capred:{cal[20]}"], f[f"A00291:reverse_split:{cal[43]}"]
    kb = f[f"A00291:krx_base:{cal[43]}"]
    assert cr["factor_ok"] is False and cr["factor_source"] == "no_base_price_evidence"
    assert cr["apply_basis"] == "unmatched" and cr["apply_date"] == cal[20]
    assert cr["price_resolution"] == "price_only_near"
    assert rs["factor_source"] == "no_price_match" and rs["price_resolution"] == "price_only_dup"
    assert kb["event_type"] == "unknown_krx" and kb["factor_source"] == "krx_base_inconsistent"
    assert kb["price_resolution"] == "price_only" and kb["price_only_factor"] == 5.0
    assert not any(x["factor_ok"] for x in f.values())
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_E1_소액_이벤트도_명목_세션_근처_기준가로만_확인한다() -> None:
    """소액(m ≤ tol_abs) 은 창 탐색을 안 한다(옛 규칙 그대로 — 2~5% 는 창 안 다른 권리락과 방향이
    반대여도 tol_abs 안에 들어 잡음 매칭이 된다). 옛 규칙은 명목 세션에 무조건 ok 였다(기준가 무관 —
    자기주식 소각 감자 0.1~4.5% 다수). 새 규칙: 명목 세션 또는 그 ± base_match_window_sessions 의
    기준가가 맞을 때만 ok, 아니면 no_base_price_evidence."""
    cal = sessions(80)
    ev = [{"ticker": "A00004", "event_type": "bonus", "effective_date": cal[30], "ratio": 1.05,
           "source": "event_fric", "rcept_no": "20191202000003", "announce_date": cal[10]}]
    key = f"A00004:bonus:{cal[30]}"
    # 기준가 근거 없음(명목일 원수익률 +3% 잡음 · 세션 45 원수익률 '정답' 점프만) → 미해결
    px_raw = flat_prices("A00004", cal, 10000, jumps={30: 1.03, 45: 0.952})
    none = run_adj_sql(ev, px_raw, cal)[key]
    assert none["factor_ok"] is False and none["factor_source"] == "no_base_price_evidence"
    assert none["apply_basis"] == "unmatched" and none["apply_date"] == cal[30]
    # 같은 가격에 큰 비율(ratio 2, m 0.5)은 원수익률도 어느 날도 안 맞는다 → no_price_match 그대로
    big = run_adj_sql([{**ev[0], "ratio": 2.0}], px_raw, cal)[key]
    assert big["apply_basis"] == "unmatched" and big["factor_source"] == "no_price_match"
    # 명목 세션 기준가 ÷1.05 → 그날
    at = run_adj_sql(ev, flat_prices("A00004", cal, 10000, jumps={30: 0.96},
                                     base={30: 1 / 1.05}), cal)[key]
    assert at["factor_ok"] is True and at["apply_basis"] == "krx_base_price"
    assert at["apply_date"] == cal[30]
    # ± base_match_window_sessions(5) 안(세션 33) → 그날(S06-2 (a))
    near = run_adj_sql(ev, flat_prices("A00004", cal, 10000, jumps={33: 0.96},
                                       base={33: 1 / 1.05}), cal)[key]
    assert near["factor_ok"] is True and near["apply_date"] == cal[33]
    # 창 안이지만 ±5 밖(세션 45)이면 소액은 못 찾는다 — 그 기준가는 unknown_price_only
    far = run_adj_sql(ev, flat_prices("A00004", cal, 10000, jumps={45: 0.96},
                                      base={45: 1 / 1.05}), cal)
    assert far[key]["factor_source"] == "no_base_price_evidence"
    assert far[f"A00004:krx_base:{cal[45]}"]["event_type"] == "unknown_price_only"


def test_E1_큰_사건은_창_안_어디든_기준가가_맞는_세션을_찾는다() -> None:
    """정지 뒤 재개(명목 +12 세션)에 기준가 ×10 인 감자 — 그 세션이 ± base_match_window_sessions
    밖이어도 (b) 창 [n0 − lookback, n0 + window] 에서 기준가로 찾는다(옛 규칙은 원수익률이 안 맞으면
    no_price_match 로 두고 기준가는 unknown_krx 로 따로 섰다). 원수익률은 재개 첫날 제한폭이 없어
    ×12.5 여도 상관없다."""
    cal = sessions(80)
    ev = [_ev_capred("A00010", 20, 0.1, cal)]
    px = flat_prices("A00010", cal, 1000, jumps={32: 12.5}, halt=(19, 31), base={32: 10.0},
                     share_jumps={32: 0.1})
    f = run_adj_sql(ev, px, cal)
    assert set(f) == {f"A00010:capred:{cal[20]}"}
    x = f[f"A00010:capred:{cal[20]}"]
    assert x["factor_ok"] is True and x["apply_basis"] == "krx_base_price"
    assert x["apply_date"] == cal[32] and (x["price_factor"], x["share_factor"]) == (10.0, 0.1)
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_E1_원수익률_세션이_명목이_아닌_기준가_후보면_그_세션에서_반증한다() -> None:
    """(d) 의 's 가 bp' 경로 중 옛 (b) 창 세션(raw_win): 분할 ratio 2(pf 0.5) 명목 30 은 변화 없고
    세션 33 에 종가 ×0.5(원수익률 잔여 0)·기준가 ×0.8(비율 안 맞음). 기준가로는 못 찾고 옛 원수익률
    세션 s = 33 이 기준가 후보라 옛 경로 그대로 그 세션에 두고 conflict 가 krx_base_inconsistent 로
    내린다(apply_basis price_matched). 33 의 기준가는 unknown_price_only ⑤ 계수 행, 사건은
    price_only_dup — s 를 명목 세션으로 두면 기준가 근거 없는 ok 가 된다(리뷰 변이 M10)."""
    cal = sessions(80)
    ev = [{"ticker": "A00033", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00033", cal, 10000, jumps={33: 0.5}, base={33: 0.8})
    f = run_adj_sql(ev, px, cal)
    x, kb = f[f"A00033:split:{cal[30]}"], f[f"A00033:krx_base:{cal[33]}"]
    assert x["factor_ok"] is False and x["factor_source"] == "krx_base_inconsistent"
    assert x["apply_basis"] == "price_matched" and x["apply_date"] == cal[33]
    assert x["price_resolution"] == "price_only_dup"
    assert kb["event_type"] == "unknown_price_only" and kb["price_resolution"] == "price_only"
    assert kb["price_only_factor"] == 0.8
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


@pytest.mark.parametrize("share_jumps", [{26: 0.5}, {40: 0.5}], ids=["같은날_주식수", "뒤_상장"])
def test_E1_같은_날_억제는_기준가로_찾은_사건이_소유자다(share_jumps: dict[int, float]) -> None:
    """E-1 리뷰 MAJOR-1: 결정공시 감자 A(ratio 0.4, 우선순위 높음)는 정지 뒤 세션 26 종가 ×2.4 와
    원수익률로만 맞고(잔여 0.04) 기준가 ×2 와는 안 맞아 반증 경로 후보다. KRX 액면병합 B(ratio 0.5)
    는 기준가 ×2 와 정확히 맞는다. 옛 순서(우선순위만)는 A 가 이겨 B 를 누르고 A 는 conflict 로
    krx_base_inconsistent — 26 의 기준가는 주식수가 같은 날 바뀌면 unknown_krx 로 따로 서고, 아니면
    직전 행이 참고가라 (c) 재발견(행 없음)이라 어디에도 안 접혔다. 새 순서는 기준가로 찾은 B 가
    소유자다."""
    cal = sessions(80)
    ev = [_ev_capred("A00777", 20, 0.4, cal),
          {"ticker": "A00777", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00777", cal, 1000, jumps={26: 2.4}, halt=(19, 25), base={26: 2.0},
                     share_jumps=share_jumps)
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00777:capred:{cal[20]}"], f[f"A00777:reverse_split:{cal[22]}"]
    assert set(f) == {f"A00777:capred:{cal[20]}", f"A00777:reverse_split:{cal[22]}"}
    assert b["factor_ok"] is True and b["apply_basis"] == "krx_base_price"
    assert b["apply_date"] == cal[26] and (b["price_factor"], b["share_factor"]) == (2.0, 0.5)
    assert a["factor_ok"] is False and a["factor_source"] == "same_day_suppressed"
    assert a["apply_date"] == cal[26] and a["price_resolution"] == "factor_near"
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_E1_성분도_기준가_근거가_있어야_ok다() -> None:
    """(c) 성분 탐색도 기준가 후보 세션만 본다. 감자 0.5 + 액면병합 0.5(명목 20·22), 정지 뒤 세션 26
    종가 ×4 인데 기준가는 직전 종가 그대로(조정 없음) — 옛 (c) 는 원수익률 곱 ×4 로 둘 다 combined
    ok 였다(리뷰 변이 M13). 성분 원수익률은 사유 판정에 안 쓰고 개별 원수익률(×2 기대)은 안 맞아
    둘 다 no_price_match."""
    cal = sessions(80)
    ev = [_ev_capred("A00044", 20, 0.5, cal),
          {"ticker": "A00044", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00044", cal, 1000, jumps={26: 4.0}, halt=(19, 25))
    f = run_adj_sql(ev, px, cal)
    assert len(f) == 2
    for x in f.values():
        assert x["factor_ok"] is False and x["factor_source"] == "no_price_match"
        assert x["apply_basis"] == "unmatched"


def test_E1_명목_세션은_원수익률과_무관하게_기준가_비로_판정하고_창보다_먼저다() -> None:
    """(a) 는 명목 세션의 기준가 비로 판정한다. 분할 ratio 2(pf 0.5) 명목 30 기준가 ×0.52(잔여
    0.04 ≤ 0.075)인데 그날 종가 ×0.6(원수익률 잔여 0.2). 세션 40(명목 ± 5 밖)에 기준가 ×0.5(잔여 0)가
    또 있어도 명목 30 이 먼저다 — (a) 를 원수익률로 재면 (b) 가 40 을 고른다(리뷰 변이 M11). 40 의
    기준가는 unknown_price_only 로 따로 선다."""
    cal = sessions(80)
    ev = [{"ticker": "A00052", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00052", cal, 10000, jumps={30: 0.6, 40: 0.5}, base={30: 0.52, 40: 0.5})
    f = run_adj_sql(ev, px, cal)
    x = f[f"A00052:split:{cal[30]}"]
    assert x["factor_ok"] is True and x["apply_basis"] == "krx_base_price"
    assert x["apply_date"] == cal[30] and x["price_factor"] == pytest.approx(0.52)
    assert f[f"A00052:krx_base:{cal[40]}"]["event_type"] == "unknown_price_only"


def test_E1_기준가_NULL_저녁_잠정_행은_근거가_아니라_미해결이다() -> None:
    """기준가 NULL 은 저녁 잠정 T 행뿐이다(price_daily 가 KRX 기본정보 없이 만든다 — KRX 행 기준가
    채움 2010~2026 100%, 로컬 10-03 판). 명목 세션이 그 행이면 원수익률(키움 종가)이 맞아도 ok 가
    아니다(P1 — 다음 아침 KRX 행이 기준가로 다시 판정). 옛 규칙은 nominal ok 였다."""
    cal = sessions(40)
    ev = [{"ticker": "A00039", "event_type": "split", "effective_date": cal[39], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[39]}]
    px = flat_prices("A00039", cal, 10000, jumps={39: 0.5})
    px[39] = {**px[39], "base_price_krw": None, "shares_out": None}     # 저녁 잠정 행
    x = run_adj_sql(ev, px, cal)[f"A00039:split:{cal[39]}"]
    assert x["factor_ok"] is False and x["factor_source"] == "no_base_price_evidence"
    assert x["apply_basis"] == "unmatched" and x["apply_date"] == cal[39]
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_E1_부정_기준가_근거_없는_세션의_ok_행은_EG3가_잡는다() -> None:
    """옛 산출 모양(069080: 기준가가 안 바뀐 시장 등락일에 price_matched ok)을 되살리면
    EG3_adj_factor 의 `n_ok_apply_basis_bad` 가 잡는다 — ok 행의 apply_basis 는 이제
    krx_base_price 뿐이다(기준가 근거 = 그 세션이 기준가 후보이고 계수 = 기준가 비, 기존
    `n_krx_row_out_of_scope`·`n_krx_price_factor_mismatch` 가 함께 묶는다)."""
    cal = sessions(80)
    ratio = 310 / 346
    ev = [_ev_capred("A00069", 20, ratio, cal)]
    px = flat_prices("A00069", cal, 10000, jumps={55: 11980 / 10920}, share_jumps={30: ratio})
    tamper = ("UPDATE out_pq SET factor_ok = TRUE, factor_source = 'mktcap_neutral', "
              f"apply_basis = 'price_matched', apply_date = DATE '{cal[55]}', "
              f"price_factor = {1 / ratio!r}, share_factor = {ratio!r}, "
              "price_resolution = 'factor'")
    g = run_eg3(ev, px, cal, tamper=tamper)
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_ok_apply_basis_bad"] == 1
    assert g.metrics["n_unmatched_source_mismatch"] == 0 and g.metrics["n_available_mismatch"] == 0


# ── ⑤ 가격 전용 계수(N-32 ②·N-33, e1.26.0) ───────────────────────────────────
# 미해결(factor_ok=false) 사건 중 그날 KRX 기준가 근거가 있는 (종목, 날짜) 단위마다 한 행에만
# `price_only_factor` = 그날 기준가 ÷ 직전 행 종가를 싣는다. 보유 수량 경로(factor_ok·price_factor·
# share_factor·factor_source)는 그대로다. 표식 `price_resolution` 이 가격 축 해소를 말한다.

# 옛 산출(e1.25.0, eae8b217)의 절단본 10행 — 옛 열 15개 전부. ⑤ 는 계수 행 2개의 available_date
# 만 바꾸고 나머지는 한 칸도 바꾸지 않는다(회귀 가드).
OLD_ADJ_COLUMNS = ("ticker", "effective_date", "event_id", "corp_code", "event_type",
                   "announce_date", "apply_date", "apply_basis", "price_factor", "share_factor",
                   "factor_source", "factor_ok", "no_bar_after_apply", "available_date",
                   "available_basis")
OLD_SLICE_ROWS: dict[str, tuple[object, ...]] = {
    "005930:split:2018-05-04": (
        "005930", date(2018, 5, 4), "005930:split:2018-05-04", "00126380", "split",
        date(2018, 5, 4), date(2018, 5, 4), "krx_base_price", 0.02, 50.0, "mktcap_neutral", True,
        False, date(2018, 5, 4), "derived"),
    "005935:split:2018-05-04": (
        "005935", date(2018, 5, 4), "005935:split:2018-05-04", "00126380", "split",
        date(2018, 5, 4), date(2018, 5, 4), "krx_base_price", 0.02, 50.0, "mktcap_neutral", True,
        False, date(2018, 5, 4), "derived"),
    "101970:capred:2015-11-26": (
        "101970", date(2015, 11, 26), "101970:capred:2015-11-26", "00450931", "capred",
        date(2016, 6, 8), date(2015, 11, 26), "unmatched", 1.0, 1.0, "no_price_match", False,
        False, date(2015, 11, 27), "derived"),
    "101970:capred:2015-11-28": (
        "101970", date(2015, 11, 28), "101970:capred:2015-11-28", "00450931", "capred",
        date(2016, 6, 8), date(2015, 11, 30), "unmatched", 1.0, 1.0, "no_price_match", False,
        False, date(2015, 12, 1), "derived"),
    "101970:capred:2018-02-23": (
        "101970", date(2018, 2, 23), "101970:capred:2018-02-23", "00450931", "capred",
        date(2019, 3, 13), date(2018, 2, 23), "nominal", 1.0, 1.0, "ratio_null", False, False,
        date(2018, 2, 26), "derived"),
    "101970:capred:2018-10-12": (
        "101970", date(2018, 10, 12), "101970:capred:2018-10-12", "00450931", "capred",
        date(2018, 7, 24), date(2018, 10, 12), "unmatched", 1.0, 1.0, "no_price_match", False,
        False, date(2018, 7, 24), "derived"),
    "101970:capred:2018-10-13": (
        "101970", date(2018, 10, 13), "101970:capred:2018-10-13", "00450931", "capred",
        date(2019, 3, 13), date(2018, 10, 15), "nominal", 1.0, 1.0, "near_dup_suppressed", False,
        False, date(2018, 10, 16), "derived"),
    "247540:bonus:2022-06-27": (
        "247540", date(2022, 6, 27), "247540:bonus:2022-06-27", "01160363", "bonus",
        date(2022, 6, 14), date(2022, 6, 27), "krx_base_price", 0.2507036590269401,
        3.988773055332799, "mktcap_neutral", True, False, date(2022, 6, 14), "derived"),
    "247540:krx_base:2022-05-09": (
        "247540", date(2022, 5, 9), "247540:krx_base:2022-05-09", "01160363",
        "unknown_price_only", date(2022, 5, 9), date(2022, 5, 9), "krx_base_price", 1.0, 1.0,
        "unknown_price_only", False, False, date(2022, 5, 10), "derived"),
    "900050:krx_base:2011-02-16": (
        "900050", date(2011, 2, 16), "900050:krx_base:2011-02-16", "00722500",
        "unknown_price_only", date(2011, 2, 16), date(2011, 2, 16), "krx_base_price", 1.0, 1.0,
        "unknown_price_only", False, False, date(2011, 2, 17), "derived"),
}
# 절단본 ⑤ 손계산: 247540 2022-05-09 기준가 491,300(= 481,000 − (−10,300)) / 직전 종가 498,500 ·
# 900050 2011-02-16 기준가 10,150 / 직전 종가 10,250 (둘 다 unknown_price_only, 보통주·외국기업)
PO_247540 = 491300 / 498500
PO_900050 = 10150 / 10250


def test_절단본_계수_행은_247540_900050이고_옛_열은_공개일_밖에_그대로다(
        built: build.BuildResult, factors: dict[str, dict[str, object]]) -> None:
    """회귀 가드: ok 3행·미해결 5행은 옛 열 15개 전부 그대로(⑤ 표식 'factor'·'unresolved', 계수 1),
    계수 행 2개는 공개일만 다음 세션 → 적용일."""
    assert set(factors) == set(OLD_SLICE_ROWS)
    for eid, old in OLD_SLICE_ROWS.items():
        got = tuple(factors[eid][c] for c in OLD_ADJ_COLUMNS)
        if eid in PRICE_ONLY_IDS:
            i = OLD_ADJ_COLUMNS.index("available_date")
            assert got[:i] + got[i + 1:] == old[:i] + old[i + 1:], eid
            assert got[i] == factors[eid]["apply_date"] != old[i]
        else:
            assert got == old, eid
    assert {e: (f["price_resolution"], f["price_only_factor"]) for e, f in factors.items()} == {
        **{e: ("factor", 1.0) for e in OK_IDS},
        **{e: ("unresolved", 1.0) for e in (NO_MATCH_IDS | RATIO_NULL_IDS | NEAR_DUP_IDS)},
        "247540:krx_base:2022-05-09": ("price_only", PO_247540),
        "900050:krx_base:2011-02-16": ("price_only", PO_900050)}
    x = _gate(built, "EG3_adj_factor").metrics
    assert x["n_by_price_resolution"] == {"factor": 3, "price_only": 2, "unresolved": 5}
    assert x["n_price_only_by_source_sec_type"] == {"unknown_price_only:common": 1,
                                                    "unknown_price_only:foreign": 1}
    assert x["n_unit_without_carrier_row"] == 0
    # 101970 근접 중복의 형제(10-12)는 no_price_match 라 C-05 원안 밖 — unresolved 로 남는다
    assert x["n_unresolved_by_factor_source"] == {"near_dup_suppressed": 1,
                                                  "no_price_match": 3, "ratio_null": 1}


def test_절단본_EG8은_계수_행_적용일_수정수익률을_기록한다(built: build.BuildResult) -> None:
    """D6-5: 계수 행의 적용일 수정수익률 = 종가 ÷ 그날 기준가 − 1 — 폐기형(10-09 6-5 서버 재연
    초과 0, 최대 0.300 → ok 계수와 같은 상수 adj_return_jump_max). 900050 2011-02-16 원수익률
    +5.4% → 10,800/10,150 − 1 = +6.4%, 247540 05-09 −3.51% → 481,000/491,300 − 1 = −2.10%."""
    m = _gate(built, "EG8").metrics
    assert m["n_price_only_events"] == 2 and m["n_price_only_with_price"] == 2
    assert m["max_abs_price_only_adj_return"] == pytest.approx(10800 / 10150 - 1)
    assert m["n_price_only_abs_adj_return_over_030"] == 0
    assert m["n_price_only_return_jump_over"] == 0
    assert m["price_only_jump_gate"] is True and rules_s06.PRICE_ONLY_JUMP_GATE is True
    ev = {e["event_id"]: e for e in m["events"]}                # type: ignore[union-attr]
    assert ev["247540:krx_base:2022-05-09"]["adj_return"] == pytest.approx(481000 / 491300 - 1)
    assert ev["247540:krx_base:2022-05-09"]["raw_return"] == pytest.approx(481000 / 498500 - 1)


def test_계수_행_종류_화이트리스트는_SQL과_rules_s06_상수가_같다() -> None:
    """D6-1: 주식 계열만(common·preferred·spac·foreign·dr). SQL 의 문자열 목록과 게이트 상수를
    묶는다 — 한쪽만 바뀌면 게이트가 다른 단위를 다시 만든다."""
    text = ADJ.sql_path.read_text(encoding="utf-8")
    m = re.search(r"po_kind AS \(.*?sec_type IN \(([^)]*)\)", text, re.S)
    assert m is not None
    assert tuple(re.findall(r"'([a-z_]+)'", m.group(1))) == rules_s06.PRICE_ONLY_SEC_TYPES
    assert rules_s06.PRICE_ONLY_SEC_TYPES == ("common", "preferred", "spac", "foreign", "dr")
    assert set(rules_s06.PRICE_RESOLUTION_VOCAB) == {
        "factor", "price_only", "price_only_dup", "price_only_near", "factor_near", "unresolved"}
    # D6-3 (나)(다) 제외 사유 — 최종 CASE 의 목록과 게이트 상수(근처 표식 폐기형이 쓴다)
    m2 = re.search(r"WHEN o\.factor_source NOT IN \(([^)]*)\)", text, re.S)
    assert m2 is not None
    assert tuple(re.findall(r"'([a-z_]+)'", m2.group(1))) == (
        *rules_s06.SUPPRESSED_SOURCES, *rules_s06.FACTOR_NEAR_EXCLUDED_SOURCES)


def _carrier(out: dict[str, dict[str, object]]) -> list[str]:
    return [e for e, f in out.items() if f["price_resolution"] == "price_only"]


def test_G1_207940형_사건_없는_기준가_unknown_krx_정상아님은_계수_행이고_적용일에_공개된다(
        ) -> None:
    """207940 2025-11-24 · 000880 2026-08-25 형(인적분할 류): 사건 없이 기준가 ×1.465, 같은 날
    주식수 ×1.2 → unknown_krx 곱 검사 실패(krx_base_inconsistent, ok=false). 보유 수량 축은
    그대로(계수 1·factor_ok false)이고 가격 축 계수 행 r = 1.465, 공개일 = 적용일."""
    cal = sessions(80)
    px = flat_prices("A00021", cal, 100000, jumps={30: 1.465 * 0.996}, base={30: 1.465},
                     share_jumps={30: 1.2})
    f = run_adj_sql([], px, cal)[f"A00021:krx_base:{cal[30]}"]
    assert f["event_type"] == "unknown_krx" and f["factor_source"] == "krx_base_inconsistent"
    assert f["factor_ok"] is False and (f["price_factor"], f["share_factor"]) == (1.0, 1.0)
    assert f["price_resolution"] == "price_only" and f["price_only_factor"] == 1.465
    assert f["available_date"] == cal[30]                     # 옛 식이면 다음 세션 cal[31]
    g = run_eg3([], px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_by_price_resolution"] == {"price_only": 1}
    assert g.metrics["n_available_mismatch"] == 0


def test_G1_084010형_같은_날_bonus_반증과_unknown_price_only는_KRX_행_하나에만_싣는다() -> None:
    """084010 2026-01-05 형: 무상증자(ratio 2, 종가 ×0.5 로 명목 매칭)의 적용일 기준가 r 0.6667 이
    비율 0.5 와 안 맞아 사건은 krx_base_inconsistent(apply_basis nominal), 기준가 후보는 신규
    unknown_price_only. ⑤ 단위 (종목, 날짜) 하나 → 계수 행은 KRX 행 1개, bonus 는
    price_only_dup(계수 1)."""
    cal = sessions(80)
    ev = [{"ticker": "A00022", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000022", "announce_date": cal[20]}]
    px = flat_prices("A00022", cal, 10000, jumps={30: 0.5}, base={30: 0.6667})
    f = run_adj_sql(ev, px, cal)
    bonus, krx = f[f"A00022:bonus:{cal[30]}"], f[f"A00022:krx_base:{cal[30]}"]
    assert bonus["factor_source"] == "krx_base_inconsistent" and bonus["apply_basis"] == "nominal"
    assert krx["event_type"] == "unknown_price_only"
    assert _carrier(f) == [f"A00022:krx_base:{cal[30]}"]
    assert krx["price_only_factor"] == 0.6667 and krx["available_date"] == cal[30]
    assert bonus["price_resolution"] == "price_only_dup" and bonus["price_only_factor"] == 1.0
    assert bonus["available_date"] == cal[20]                 # 계수 행 아닌 행은 옛 식 그대로
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_by_price_resolution"] == {"price_only": 1, "price_only_dup": 1}


def test_G1_111610형_성분_감자_2행은_계수_행_하나이고_값은_그날_기준가_비율이다() -> None:
    """111610 2015-08-17 형: 감자 + 액면병합 성분(곱 4)이 기준가 ×4 로 교체됐는데 같은 날 주식수
    비 0.9 라 곱 3.6 ≠ 1 → 두 행 다 krx_base_inconsistent(krx_base_price). 계수 행은 최소
    event_id(capred) 1행, 값은 멤버 계수가 아니라 그날 r = 4.0."""
    cal = sessions(80)
    ev = [{"ticker": "A00023", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000023", "announce_date": cal[5]},
          {"ticker": "A00023", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00023", cal, 1000, jumps={26: 4.0}, halt=(19, 25), base={26: 4.0},
                     share_jumps={26: 0.9})
    f = run_adj_sql(ev, px, cal)
    a, b = f[f"A00023:capred:{cal[20]}"], f[f"A00023:reverse_split:{cal[22]}"]
    for x in (a, b):
        assert x["factor_source"] == "krx_base_inconsistent" and x["apply_date"] == cal[26]
        assert x["apply_basis"] == "krx_base_price" and x["factor_ok"] is False
    assert _carrier(f) == [f"A00023:capred:{cal[20]}"]
    assert a["price_only_factor"] == 4.0 and b["price_only_factor"] == 1.0
    assert b["price_resolution"] == "price_only_dup"
    assert a["available_date"] == cal[5]                      # min(공시, 적용일) — 공시가 앞선다
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_G1_작은_권리락_r_0_9923도_계수_행이다() -> None:
    """기준가 −0.77%(207940 2026-10-02 형, 0단계 대조: 키움도 같은 비율로 조정). tol 0.2% 밖이면
    크기와 무관하게 싣는다."""
    cal = sessions(80)
    px = flat_prices("A00024", cal, 10000, jumps={40: 0.9923 * 1.01}, base={40: 0.9923})
    f = run_adj_sql([], px, cal)[f"A00024:krx_base:{cal[40]}"]
    assert f["event_type"] == "unknown_price_only" and f["factor_ok"] is False
    assert f["price_resolution"] == "price_only" and f["price_only_factor"] == 0.9923
    assert f["available_date"] == cal[40]
    g = run_eg3([], px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_price_only_r_dev_le_005"] == 1


@pytest.mark.parametrize("sec_type", ["fund", "reit", "ship_fund"])
def test_D6_1_펀드_리츠_선박펀드는_계수_행이_아니라_unresolved(sec_type: str) -> None:
    """D6-1: 분배·배당락 추정의 반복 하락 — 접으면 adj_close 가 분배 재투자 축이 된다."""
    cal = sessions(80)
    px = flat_prices("A00025", cal, 10000, jumps={40: 0.97}, base={40: 0.97})
    st = {"A00025": sec_type}
    f = run_adj_sql([], px, cal, sec_types=st)[f"A00025:krx_base:{cal[40]}"]
    assert f["price_resolution"] == "unresolved" and f["price_only_factor"] == 1.0
    assert f["available_date"] == cal[41]                     # 옛 식 그대로
    g = run_eg3([], px, cal, sec_types=st)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_by_price_resolution"] == {"unresolved": 1}


def _ok_fold_overlap() -> tuple[list[date], list[dict[str, object]], list[dict[str, object]]]:
    """e1.26.0 의 ③ 회귀 모양: 무상증자(ratio 2, 명목 30 원수익률 ×0.5, 정정 공시 35)와 다음 세션
    31 의 기준가 후보(r 0.95, 비율 안 맞음). 옛 판정은 명목 세션에 원수익률로 ok 를 붙여 접힘일이
    31 이 됐고 31 의 unknown_price_only 와 겹쳤다. E-1 뒤로는 30 에 기준가 근거가 없어 사건이
    미해결이라 이 겹침이 생기지 않는다(ok 행은 기준가 세션에서 그날 접힌다)."""
    cal = sessions(80)
    ev = [{"ticker": "A00026", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000026", "announce_date": cal[35]}]
    px = flat_prices("A00026", cal, 10000, jumps={30: 0.5, 31: 0.95}, base={31: 0.95})
    return cal, ev, px


def test_회귀_E1_옛_ok_접힘일_겹침_모양은_사건_미해결이고_다음_세션_기준가는_계수_행() -> None:
    cal, ev, px = _ok_fold_overlap()
    f = run_adj_sql(ev, px, cal)
    ev_row, po = f[f"A00026:bonus:{cal[30]}"], f[f"A00026:krx_base:{cal[31]}"]
    assert ev_row["factor_ok"] is False and ev_row["factor_source"] == "no_base_price_evidence"
    assert ev_row["available_date"] == cal[31]                # 옛 식(다음 세션) 그대로
    assert ev_row["price_resolution"] == "price_only_near"
    assert po["event_type"] == "unknown_price_only"
    assert po["price_resolution"] == "price_only" and po["price_only_factor"] == 0.95
    assert po["available_date"] == cal[31]                    # ⑤ 계수 행 — 그날
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_회귀_기준가_근거_없는_no_price_match는_unresolved() -> None:
    cal = sessions(80)
    ev = [{"ticker": "A00027", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000027", "announce_date": cal[5]}]
    f = run_adj_sql(ev, flat_prices("A00027", cal, 1000, jumps={}), cal)
    x = f[f"A00027:capred:{cal[20]}"]
    assert x["factor_source"] == "no_price_match"
    assert (x["price_resolution"], x["price_only_factor"]) == ("unresolved", 1.0)


def test_D6_2_날짜가_다른_DART_행은_창_안_계수_단위가_있고_c_후보가_없을_때만_price_only_near(
        ) -> None:
    """DART 감자(명목 20, 가격 매칭 실패 → no_price_match). 같은 종목 세션 50(창 [15, 60] 안)에
    ⑤ 단위(unknown_price_only r 0.8) → price_only_near. 같은 창에 (c) 재발견 후보(정지 뒤 기준가
    리셋, 행 없음)가 있으면 숨은 점프일 수 있어 unresolved. 단위가 창 밖(61)이어도 unresolved."""
    cal = sessions(80)
    ev = [{"ticker": "A00028", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000028", "announce_date": cal[5]}]
    eid = f"A00028:capred:{cal[20]}"
    near = run_adj_sql(ev, flat_prices("A00028", cal, 1000, jumps={50: 0.8}, base={50: 0.8}),
                       cal)
    assert near[eid]["factor_source"] == "no_price_match"
    assert near[eid]["price_resolution"] == "price_only_near"
    assert near[eid]["price_only_factor"] == 1.0
    assert _carrier(near) == [f"A00028:krx_base:{cal[50]}"]
    g = run_eg3(ev, flat_prices("A00028", cal, 1000, jumps={50: 0.8}, base={50: 0.8}), cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_near_by_factor_source"] == {"price_only_near:no_price_match": 1}
    guarded = run_adj_sql(ev, flat_prices("A00028", cal, 1000, jumps={50: 0.8, 45: 0.7},
                                          base={50: 0.8, 45: 0.7}, halt=(43, 44)), cal)
    assert guarded[f"A00028:krx_base:{cal[50]}"]["price_resolution"] == "price_only"
    assert f"A00028:krx_base:{cal[45]}" not in guarded            # (c) — 행이 없다
    assert guarded[eid]["price_resolution"] == "unresolved"
    far = run_adj_sql(ev, flat_prices("A00028", cal, 1000, jumps={61: 0.8}, base={61: 0.8}), cal)
    assert far[eid]["price_resolution"] == "unresolved"


def test_D6_3_ok_형제가_있는_억제_중복본은_factor_near_형제가_미해결이면_unresolved() -> None:
    """C-05 원안(감사 C-05: 정상 처리된 사건의 중복본 near_dup_suppressed·same_day_suppressed).
    ① 결정공시 감자(20)와 자본변동 감자(21, ratio NULL)는 근접 중복 — 결정공시가 기준가로 ok 면
    자본변동 행은 factor_near. 형제가 no_price_match 면 그대로 unresolved(절단본 101970 도 같다).
    ② 같은 날 개별 매칭 2건 중 눌린 쪽(same_day_suppressed)도 이긴 쪽이 ok 면 factor_near."""
    cal = sessions(80)
    ev = [{"ticker": "A00029", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000029", "announce_date": cal[5]},
          {"ticker": "A00029", "event_type": "capred", "effective_date": cal[21], "ratio": None,
           "source": "capital", "rcept_no": "20200302000029", "announce_date": cal[60]}]
    win, lose = f"A00029:capred:{cal[20]}", f"A00029:capred:{cal[21]}"
    px = flat_prices("A00029", cal, 1000, jumps={25: 10.0}, halt=(19, 24), base={25: 10.0},
                     share_jumps={25: 0.1})
    f = run_adj_sql(ev, px, cal)
    assert f[win]["factor_ok"] is True and f[lose]["factor_source"] == "near_dup_suppressed"
    assert (f[lose]["price_resolution"], f[lose]["price_only_factor"]) == ("factor_near", 1.0)
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_near_by_factor_source"] == {"factor_near:near_dup_suppressed": 1}
    flat = run_adj_sql(ev, flat_prices("A00029", cal, 1000, jumps={}), cal)
    assert flat[win]["factor_source"] == "no_price_match"
    assert flat[lose]["price_resolution"] == "unresolved"
    # ② 같은 날 억제 — 감자(0.5) + 액면병합(0.5) 이 둘 다 세션 26 ×2 에 개별 매칭
    ev2 = [{"ticker": "A00030", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
            "source": "event_cr", "rcept_no": "20191202000030", "announce_date": cal[5]},
           {"ticker": "A00030", "event_type": "reverse_split", "effective_date": cal[22],
            "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
            "announce_date": cal[22]}]
    h = run_adj_sql(ev2, flat_prices("A00030", cal, 1000, jumps={26: 2.0}, halt=(19, 25),
                                     base={26: 2.0}, share_jumps={26: 0.5}), cal)
    sup = h[f"A00030:reverse_split:{cal[22]}"]
    assert sup["factor_source"] == "same_day_suppressed"
    assert sup["price_resolution"] == "factor_near"
    assert h[f"A00030:capred:{cal[20]}"]["price_resolution"] == "factor"


def _capred_ok_at_25(extra: list[dict[str, object]], **px_kw: object
                     ) -> tuple[list[date], list[dict[str, object]], list[dict[str, object]]]:
    """결정공시 감자(명목 20, ratio 0.1)가 정지 뒤 재개일 25 기준가 ×10 · 주식수 ×0.1 로 ok(기준가
    교체). `extra` 사건과 가격 변형(px_kw: halt·jumps·base 덮어쓰기)을 더한다."""
    cal = sessions(80)
    ev = [{"ticker": "A00031", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000031", "announce_date": cal[5]},
          *extra]
    jumps = {25: 10.0, **dict(px_kw.pop("jumps", {}))}           # type: ignore[call-overload]
    base = {25: 10.0, **dict(px_kw.pop("base", {}))}             # type: ignore[call-overload]
    halt = px_kw.pop("halt", (19, 24))
    px = flat_prices("A00031", cal, 1000, jumps=jumps, base=base, halt=halt,  # type: ignore[arg-type]
                     share_jumps={25: 0.1})
    return cal, ev, px


def test_G1_D6_3나_ok_계수_적용일이_창_안인_DART_명목_행은_factor_near() -> None:
    """D6-3 (나): 자본변동 감자(ratio NULL, 명목 30 — 결정공시와 14일 떨어져 근접 중복이 아니다)는
    정상 처리된 같은 감자의 회고 기재 행이다. 그날 기준가 후보가 없고 창 [25, 70] 안에 ok 계수의
    적용일(25)이 있으며 (c) 후보가 없다 → factor_near(f5e14b8e 는 unresolved)."""
    cal = sessions(80)
    retro = {"ticker": "A00031", "event_type": "capred", "effective_date": cal[30], "ratio": None,
             "source": "capital", "rcept_no": "20200302000031", "announce_date": cal[70]}
    cal, ev, px = _capred_ok_at_25([retro])
    f = run_adj_sql(ev, px, cal)
    ok, r = f[f"A00031:capred:{cal[20]}"], f[f"A00031:capred:{cal[30]}"]
    assert ok["factor_ok"] is True and ok["apply_date"] == cal[25]
    assert r["factor_source"] == "ratio_null" and r["apply_date"] == cal[30]
    assert (r["price_resolution"], r["price_only_factor"]) == ("factor_near", 1.0)
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_factor_near_by_branch"] == {"near_ok_apply": 1}


def test_회귀_D6_3나_같은_창에_c_후보가_있으면_unresolved() -> None:
    """같은 자본변동 행이라도 창 안에 정지 뒤 기준가 리셋((c), 행 없음)이 있으면 숨은 점프일 수
    있다 → unresolved."""
    cal = sessions(80)
    retro = {"ticker": "A00031", "event_type": "capred", "effective_date": cal[30], "ratio": None,
             "source": "capital", "rcept_no": "20200302000031", "announce_date": cal[70]}
    cal, ev, px = _capred_ok_at_25([retro], halt=(19, 24), jumps={42: 0.7}, base={42: 0.7})
    px = [{**p, "price_kind": "reference"} if p["date"] in (cal[40], cal[41]) else p for p in px]
    f = run_adj_sql(ev, px, cal)
    assert f"A00031:krx_base:{cal[42]}" not in f                # (c) — 행이 없다
    assert f[f"A00031:capred:{cal[30]}"]["price_resolution"] == "unresolved"


def test_회귀_D6_3_형제가_미해결인_근접_중복본은_ok_계수가_가까이_있어도_unresolved() -> None:
    """근접 중복본은 형제(누른 쪽)로만 판정한다 — 형제가 no_price_match 면 근처의 다른 ok 계수
    (분할, 세션 30)로 factor_near 가 되지 않는다(코디네이터 지시: 형제 미해결 near_dup 4행 유지)."""
    cal = sessions(80)
    ev = [{"ticker": "A00032", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000032", "announce_date": cal[5]},
          {"ticker": "A00032", "event_type": "capred", "effective_date": cal[21], "ratio": None,
           "source": "capital", "rcept_no": "20200302000032", "announce_date": cal[60]},
          {"ticker": "A00032", "event_type": "split", "effective_date": cal[30], "ratio": 2.0,
           "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[30]}]
    px = flat_prices("A00032", cal, 10000, jumps={30: 0.5}, base={30: 0.5}, share_jumps={30: 2.0})
    f = run_adj_sql(ev, px, cal)
    assert f[f"A00032:split:{cal[30]}"]["factor_ok"] is True
    assert f[f"A00032:capred:{cal[20]}"]["factor_source"] == "no_price_match"
    sup = f[f"A00032:capred:{cal[21]}"]
    assert sup["factor_source"] == "near_dup_suppressed"
    assert sup["price_resolution"] == "unresolved"
    # 형제(결정공시 감자)는 미해결이지만 사건 행 자체는 (나) 로 ok 분할 근처 → factor_near
    assert f[f"A00032:capred:{cal[20]}"]["price_resolution"] == "factor_near"
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail


def test_회귀_D6_3_ok_계수_근처의_유상감자는_unresolved() -> None:
    """유상감자(capred_paid)는 시총 불변 사건이 아니라 '정상 사건의 중복본'으로 볼 근거가 약하다 —
    창 [22, 67] 안에 ok 계수 적용일(25)이 있어도 (나)(다) 로 factor_near 가 되지 않는다(P1)."""
    cal = sessions(80)
    paid = {"ticker": "A00031", "event_type": "capred", "effective_date": cal[27], "ratio": 0.9,
            "source": "event_cr", "rcept_no": "20200302000033", "announce_date": cal[22]}
    cr = [{"rcept_no": "20200302000033", "corp_code": "C0000001", "cr_mth": "주식소각",
           "cr_rs": "주주환원 목적 유상감자"}]
    cal, ev, px = _capred_ok_at_25([paid])
    f = run_adj_sql(ev, px, cal, cr=cr)
    assert f[f"A00031:capred:{cal[20]}"]["factor_ok"] is True
    x = f[f"A00031:capred:{cal[27]}"]
    assert x["factor_source"] == "capred_paid" and x["apply_date"] == cal[27]   # 창 [22, 67] ∋ 25
    assert (x["price_resolution"], x["price_only_factor"]) == ("unresolved", 1.0)


def test_D6_3다_ok_계수가_접히는_날의_DART_행은_factor_near() -> None:
    """D6-3 (다): 자본변동 감자(ratio NULL)의 명목일 = ok 계수(결정공시 감자, 기준가 교체)의 적용일
    25 — 그날 기준가 후보는 ok 계수가 이미 썼다(⑤ 단위 ③ 으로 빠지는 날)."""
    cal = sessions(80)
    retro = {"ticker": "A00031", "event_type": "capred", "effective_date": cal[25], "ratio": None,
             "source": "capital", "rcept_no": "20200302000031", "announce_date": cal[70]}
    cal, ev, px = _capred_ok_at_25([retro])
    f = run_adj_sql(ev, px, cal)
    r = f[f"A00031:capred:{cal[25]}"]
    assert r["factor_source"] == "ratio_null" and r["apply_date"] == cal[25]
    assert r["price_resolution"] == "factor_near"
    g = run_eg3(ev, px, cal)
    assert g.status is GateStatus.PASS, g.detail
    assert g.metrics["n_factor_near_by_branch"] == {"ok_fold_day": 1}


# ── ⑤ 부정 픽스처 — EG3_adj_factor 가 FAIL 해야 한다 ─────────────────────────

def _g1_084010() -> tuple[list[date], list[dict[str, object]], list[dict[str, object]]]:
    cal = sessions(80)
    ev = [{"ticker": "A00022", "event_type": "bonus", "effective_date": cal[30], "ratio": 2.0,
           "source": "event_fric", "rcept_no": "20191202000022", "announce_date": cal[20]}]
    return cal, ev, flat_prices("A00022", cal, 10000, jumps={30: 0.5}, base={30: 0.6667})


def test_부정_계수를_1_r로_뒤집으면_EG3가_잡는다() -> None:
    cal, ev, px = _g1_084010()
    g = run_eg3(ev, px, cal, tamper="UPDATE out_pq SET price_only_factor = 1 / price_only_factor "
                                    "WHERE price_resolution = 'price_only'")
    assert g.status is GateStatus.FAIL and g.metrics["n_price_only_factor_mismatch"] == 1


def test_부정_한_단위에_계수_행이_2개면_EG3가_잡는다() -> None:
    cal, ev, px = _g1_084010()
    g = run_eg3(ev, px, cal, tamper="UPDATE out_pq SET price_resolution = 'price_only', "
                                    "price_only_factor = 0.6667 "
                                    "WHERE price_resolution = 'price_only_dup'")
    assert g.status is GateStatus.FAIL and g.metrics["n_price_only_unit_carrier_ne_one"] == 1


def test_부정_ok_접힘일에_계수_행을_두면_EG3가_잡는다() -> None:
    """ok 계수(감자, 세션 26 기준가 ×2)가 접히는 날의 같은 날 억제 행(액면병합)을 계수 행으로
    위장 — E-1 뒤로는 ok 행이 늘 자기 기준가 세션에서 접혀 ② 사유 행과 같은 날에 설 수 없으므로
    억제 행으로 겹침을 만든다."""
    cal = sessions(80)
    ev = [{"ticker": "A00030", "event_type": "capred", "effective_date": cal[20], "ratio": 0.5,
           "source": "event_cr", "rcept_no": "20191202000030", "announce_date": cal[5]},
          {"ticker": "A00030", "event_type": "reverse_split", "effective_date": cal[22],
           "ratio": 0.5, "source": "krx_listing", "effective_basis": "krx_shares_change",
           "announce_date": cal[22]}]
    px = flat_prices("A00030", cal, 1000, jumps={26: 2.0}, halt=(19, 25), base={26: 2.0},
                     share_jumps={26: 0.5})
    g = run_eg3(ev, px, cal, tamper="UPDATE out_pq SET price_resolution = 'price_only', "
                                    "price_only_factor = 2.0 "
                                    "WHERE factor_source = 'same_day_suppressed'")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_price_only_on_ok_fold"] == 1
    assert g.metrics["n_price_only_outside_unit"] == 1


def test_부정_계수_행을_factor_ok_true로_두면_EG3가_잡는다() -> None:
    cal, ev, px = _g1_084010()
    g = run_eg3(ev, px, cal, tamper="UPDATE out_pq SET factor_ok = TRUE "
                                    "WHERE price_resolution = 'price_only'")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_price_resolution_factor_mismatch"] == 1
    assert g.metrics["n_price_only_carrier_bad"] == 1


# ── 근처 표식의 비보수 방향 폐기형(리뷰 중-1) — 산출 SQL 변형이 EG3 에 걸려야 한다 ───────────

def _no_c_guard() -> str:
    """(c) 가드를 지운 변형 — 재발견 후보 집합을 비운다."""
    old = "SELECT ticker, n FROM bp_new WHERE event_type IS NULL"
    assert _body().count(old) == 1
    return _body().replace(old, "SELECT ticker, n FROM bp_new WHERE FALSE")


def _flipped_window() -> str:
    """창 부호를 뒤집은 변형 — [n − lookback, n + window] 를 [n − window, n + lookback] 로."""
    body = _body()
    a, b = "x.n_apply - k.win_before", "x.n_apply + k.win_after"
    assert body.count(a) == body.count(b) == 3
    return (body.replace(a, "x.n_apply - k.@A@").replace(b, "x.n_apply + k.@B@")
            .replace("k.@A@", "k.win_after").replace("k.@B@", "k.win_before"))


def test_부정_c_가드를_지운_변형은_근처_표식을_EG3가_잡는다() -> None:
    """창 안 (c) 재발견 후보가 있는데 근처로 표시하면(숨은 점프를 해소로 셈) 폐기."""
    cal = sessions(80)
    ev = [{"ticker": "A00028", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000028", "announce_date": cal[5]}]
    px = flat_prices("A00028", cal, 1000, jumps={50: 0.8, 45: 0.7}, base={50: 0.8, 45: 0.7},
                     halt=(43, 44))
    assert run_eg3(ev, px, cal).status is GateStatus.PASS        # 정본은 unresolved
    g = run_eg3(ev, px, cal, sql=_no_c_guard())
    assert g.status is GateStatus.FAIL and g.metrics["n_price_only_near_bad"] == 1
    # (나) factor_near 도 같다 — ok 계수(25) 근처 자본변동 행, 창 안 (c)(42)
    retro = {"ticker": "A00031", "event_type": "capred", "effective_date": cal[30], "ratio": None,
             "source": "capital", "rcept_no": "20200302000031", "announce_date": cal[70]}
    cal, ev2, px2 = _capred_ok_at_25([retro], halt=(19, 24), jumps={42: 0.7}, base={42: 0.7})
    px2 = [{**p, "price_kind": "reference"} if p["date"] in (cal[40], cal[41]) else p
           for p in px2]
    assert run_eg3(ev2, px2, cal).status is GateStatus.PASS
    h = run_eg3(ev2, px2, cal, sql=_no_c_guard())
    assert h.status is GateStatus.FAIL and h.metrics["n_factor_near_bad"] == 1


def test_부정_창_부호를_뒤집은_변형은_근처_표식을_EG3가_잡는다() -> None:
    """창 [n − 5, n + 40] 를 [n − 40, n + 5] 로 뒤집으면 사건 전 계수 행·ok 적용일을 근처로 센다."""
    cal = sessions(80)
    ev = [{"ticker": "A00034", "event_type": "capred", "effective_date": cal[20], "ratio": 0.1,
           "source": "event_cr", "rcept_no": "20191202000034", "announce_date": cal[5]}]
    px = flat_prices("A00034", cal, 1000, jumps={10: 0.8}, base={10: 0.8})   # ⑤ 단위 세션 10
    assert run_adj_sql(ev, px, cal)[f"A00034:capred:{cal[20]}"]["price_resolution"] == (
        "unresolved")
    assert run_eg3(ev, px, cal).status is GateStatus.PASS
    g = run_eg3(ev, px, cal, sql=_flipped_window())
    assert g.status is GateStatus.FAIL and g.metrics["n_price_only_near_bad"] == 1
    # (나): ok 분할 적용일 10 이 사건(20) 앞 — 뒤집힌 창에서만 근처
    ev2 = [{**ev[0], "ticker": "A00035", "rcept_no": "20191202000035"},
           {"ticker": "A00035", "event_type": "split", "effective_date": cal[10], "ratio": 2.0,
            "source": "krx_listing", "effective_basis": "krx_shares_change",
            "announce_date": cal[10]}]
    px2 = flat_prices("A00035", cal, 1000, jumps={10: 0.5}, base={10: 0.5},
                      share_jumps={10: 2.0})
    f2 = run_adj_sql(ev2, px2, cal)
    assert f2[f"A00035:split:{cal[10]}"]["factor_ok"] is True
    assert f2[f"A00035:capred:{cal[20]}"]["price_resolution"] == "unresolved"
    h = run_eg3(ev2, px2, cal, sql=_flipped_window())
    assert h.status is GateStatus.FAIL and h.metrics["n_factor_near_bad"] == 1


def test_sql파일에_상수_하드코딩_없음() -> None:
    text = re.sub(r"--[^\n]*", "", ADJ.sql_path.read_text(encoding="utf-8"))
    nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text))
    assert nums <= {"0", "1", "2", "-1"}, sorted(nums)
