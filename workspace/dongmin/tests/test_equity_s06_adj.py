"""S06 `adj_factor` — 절단본 위 `build_table` 왕복 (DESIGN §4-2 · GATES §3-⑩ · EG3-P04 · EG8).

상류 6테이블(`trading_calendar`·`security`·`security_span`·`corp_ticker`·`price_daily`·
`corp_event`)을 같은 equity_root 에 먼저 짓고 그 위에 `adj_factor` 를 올린다. 손계산 기대값은
절단본 `stg_event_cr`·`stg_capital`·`stg_listing_daily`·`stg_price_daily` 원자료를 직접 열어
확인한 값이다.

절단본 실측(corp_event 8행 — S05 2차 범위 규칙: 000030 유상감자·0001A0 무상증자는 상장 전·
비상장 종류라 모집단 밖 — → adj_factor 8행, 격리 0):
  ok 6 = 005930·005935 split(1/50, 50) · 247540 bonus(1/4, 4) · 101970 capred 2015-11-26(1/0.428)
         · 2015-11-28(1/0.1) · 2018-10-12(1/0.1)
  ratio_null 1 = 101970 2018-02-23 (자본변동 단독 행)
  near_dup_suppressed 1 = 101970 2018-10-13(자본변동, 결정공시 10-12 와 하루 차)
  EG8: 가격 행이 있는 ok 이벤트 3(005930·005935·247540) — 최대 |수정수익률| 0.0929 · 거래량
       점프 3.43
"""
from __future__ import annotations

import os
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
N_OK = 6
OK_IDS = {"005930:split:2018-05-04", "005935:split:2018-05-04", "247540:bonus:2022-06-27",
          "101970:capred:2015-11-26", "101970:capred:2015-11-28", "101970:capred:2018-10-12"}
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


def test_삼성전자_분할_계수는_1_50과_50이고_곱은_1이다(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-001 · FX-2-009. KRX 관측행은 announce = 관측일 = 효력일이라 available 도 그날이다."""
    for eid in ("005930:split:2018-05-04", "005935:split:2018-05-04"):
        f = factors[eid]
        assert f["price_factor"] == 1 / 50 and f["share_factor"] == 50.0
        assert f["price_factor"] * f["share_factor"] == 1          # type: ignore[operator]
        assert f["factor_ok"] is True and f["factor_source"] == "mktcap_neutral"
        assert f["available_date"] == date(2018, 5, 4) == f["announce_date"]
        assert f["available_basis"] == "derived" and f["event_type"] == "split"
        assert f["corp_code"] == "00126380"


def test_무상증자는_1_4과_4이고_available은_공시일이다(
        factors: dict[str, dict[str, object]]) -> None:
    f = factors["247540:bonus:2022-06-27"]
    assert f["price_factor"] == 0.25 and f["share_factor"] == 4.0 and f["factor_ok"] is True
    # 공시일 < 효력일 다음 거래일 06-28
    assert f["announce_date"] == date(2022, 6, 14) == f["available_date"]


def test_감자는_두_축이_상이하고_같은_원천_근접_2건은_둘_다_ok다(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-004. 101970 2015-11-26(자기주식 소각+병합) 과 11-28(10:1 병합)은 접수번호가 다른
    두 사건."""
    f = factors["101970:capred:2018-10-12"]
    assert f["share_factor"] == 13656196 / 136561969
    assert f["price_factor"] == 1 / (13656196 / 136561969) and f["factor_ok"] is True
    assert f["share_factor"] != f["price_factor"]
    a, b = factors["101970:capred:2015-11-26"], factors["101970:capred:2015-11-28"]
    assert a["share_factor"] == 11016503 / 25735667 and a["factor_ok"] is True
    assert b["share_factor"] == 20885490 / 208859671 and b["factor_ok"] is True
    assert a["available_date"] == date(2015, 11, 27)      # 효력일 다음 거래일 < 공시 2016-06-08
    assert b["available_date"] == date(2015, 11, 30)           # 토요일 기준일 → 월요일


def test_ratio_NULL_행은_factor_ok_false_계수_1_격리_아님(
        factors: dict[str, dict[str, object]]) -> None:
    """FX-2-007 대체(MVP 에 spinoff 가 없다): 자본변동 단독 행 1건(S05 2차 범위 규칙 뒤)."""
    assert {e for e, f in factors.items() if f["factor_source"] == "ratio_null"} == RATIO_NULL_IDS
    for eid in RATIO_NULL_IDS:
        f = factors[eid]
        assert f["factor_ok"] is False and f["price_factor"] == 1.0 and f["share_factor"] == 1.0
    # 회고 기재라 announce 가 사건보다 늦다 → 효력일 다음 거래일이 available 이 된다
    f = factors["101970:capred:2018-02-23"]
    assert f["announce_date"] == date(2019, 3, 13)         # 가장 이른 사업보고서
    assert f["available_date"] == date(2018, 2, 26)        # 금요일 효력일 → 월요일
    assert "000030:capred:2013-04-01" not in factors and "0001A0:bonus:2024-09-03" not in factors


def test_교차_원천_근접_중복은_우선순위_낮은_쪽을_누른다(built: build.BuildResult,
                                            factors: dict[str, dict[str, object]]) -> None:
    """결정공시 cr_std 2018-10-12 vs 자본변동 isu_dcrs_de 10-13 — 둘 다 곱하면 감자를 두 번
    곱는다."""
    loser, winner = factors["101970:capred:2018-10-13"], factors["101970:capred:2018-10-12"]
    assert loser["factor_source"] == "near_dup_suppressed" and loser["factor_ok"] is False
    assert loser["price_factor"] == 1.0 and loser["share_factor"] == 1.0
    assert winner["factor_ok"] is True
    x = _gate(built, "EG3_adj_factor").metrics
    assert x["n_near_dup_suppressed"] == 1 and x["n_near_dup_both_ok"] == 0
    assert x["near_dup_window_days"] == 5                       # corp_event 네임스페이스 상수
    assert x["n_by_factor_source"] == {"mktcap_neutral": N_OK, "near_dup_suppressed": 1,
                                       "ratio_null": 1}
    assert {e for e, f in factors.items() if f["factor_ok"]} == OK_IDS


def test_available_date는_min_공시_효력일_다음_거래일_이다(built: build.BuildResult,
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
        nxt = next(d for d in cal if d > f["effective_date"])   # type: ignore[operator]
        assert f["available_date"] == min(f["announce_date"], nxt)   # type: ignore[type-var]
        assert f["available_basis"] == "derived"
    x = _gate(built, "EG3_adj_factor").metrics
    # available < announce 4 = 101970 2015-11-26·11-28·2018-02-23·2018-10-13 (회고 기재)
    assert x["n_available_mismatch"] == 0 and x["n_available_before_announce"] == 4
    assert _gate(built, "EG2").metrics["content_date_column"] is None


def test_EG8_점프는_조정가로_재고_원주가_점프는_사라진다(built: build.BuildResult) -> None:
    """005930 2018-05-04: 원주가 −98% → 조정가 −2.1%. 247540: 원주가 −72.7% → 조정가 +9.3%."""
    eg8 = _gate(built, "EG8")
    assert eg8.status is GateStatus.PASS
    m = eg8.metrics
    assert m["asof_basis"] == "baseline" and m["asof_used"] == "2026-08-20"
    assert m["n_ok_events"] == N_OK and m["n_ok_events_with_price"] == 3
    assert m["n_return_jump_over"] == 0 and m["n_volume_jump_over"] == 0
    assert m["max_abs_adj_return_jump"] == pytest.approx(MAX_ADJ_RETURN)
    assert 1 < m["max_adj_volume_jump"] < 10                    # 절단본 실측 3.43
    assert m["n_effective_off_calendar"] == 2                   # 2015-11-28 · 2018-10-13 토요일
    ev = {e["event_id"]: e for e in m["events"]}                # type: ignore[union-attr]
    assert ev["005930:split:2018-05-04"]["raw_return"] == pytest.approx(MAX_RAW_RETURN_005930)
    assert ev["005930:split:2018-05-04"]["adj_return"] == pytest.approx(51900 / 53000 - 1)
    assert ev["247540:bonus:2022-06-27"]["adj_return"] == pytest.approx(MAX_ADJ_RETURN)
    assert ev["247540:bonus:2022-06-27"]["raw_return"] == pytest.approx(135900 / 497400 - 1)


def test_상수가_없으면_EG8은_skip이되_metric은_계산한다(tmp_path: Path) -> None:
    bl = seed()
    no_s06 = Baseline({k: v for k, v in bl.data.items() if k != "adj_factor"})
    r = build_chain(STAGE_SLICE, tmp_path / "equity", no_s06)
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


def test_상수는_corp_event_네임스페이스에서_복제_없이_읽는다() -> None:
    assert ADJ.consts == ("corp_event.near_dup_window_days",)
    assert build._split_const_key("adj_factor", "corp_event.near_dup_window_days") == (
        "corp_event", "near_dup_window_days")
    assert build._split_const_key("adj_factor", "own_metric") == ("adj_factor", "own_metric")
    con = duckdb.connect()
    try:
        build.make_consts(con, ADJ, Baseline({"corp_event": {"near_dup_window_days": 7}}))
        assert con.execute("SELECT near_dup_window_days FROM _const").fetchone() == (7,)
        with pytest.raises(KeyError, match="corp_event.near_dup_window_days"):
            build.make_consts(con, ADJ, Baseline({"adj_factor": {"near_dup_window_days": 7}}))
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
    에는 유·무상 축이 없어 S06 이 결정공시 본문(cr_mth·cr_rs)으로 판정한다 — 절단본 3건은 전부
    무상이라 그대로 ok."""
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
    assert paid["available_date"] == date(2019, 5, 10)          # 공시일 < 효력일 다음 거래일
    assert {e for e, x in f.items() if x["factor_ok"]} == OK_IDS
    x = _gate(r, "EG3_adj_factor").metrics
    assert x["n_capred_paid"] == 1 and x["n_by_factor_source"]["capred_paid"] == 1


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_FX_N_003_share_factor를_역수로_두면_EG3_adj_factor만_fail(tmp_path: Path) -> None:
    """GATES §4 FX-N-003 → EG3-P04(시총 불변 곱 = 1) 위반. 앞 게이트는 그대로 pass, 뒤는
    upstream_failed."""
    sql = _body().replace("THEN j.ratio     ELSE 1 END         AS share_factor",
                          "THEN 1 / j.ratio ELSE 1 END         AS share_factor")
    assert sql != _body()
    r = build_chain(STAGE_SLICE, tmp_path / "equity", seed(),
                    rule=_variant(tmp_path, "adj_factor_n003", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    st = {g.name: g.status.value for g in r.gates}
    assert st["EG3_adj_factor"] == "fail" and st["EG8"] == "skip"
    assert all(st[n] == "pass" for n in ("EG0", "EG7", "EG1", "EG2", "EG3"))
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_ok_product_off_one"] == N_OK and m["n_ok_share_factor_ne_ratio"] == N_OK
    assert m["max_ok_product_dev"] > 0.9
    assert _gate(r, "EG8").detail == "upstream_failed"


def test_not_ok_행의_계수가_1이_아니면_EG3_adj_factor_fail(tmp_path: Path) -> None:
    sql = _body().replace("THEN j.ratio     ELSE 1 END         AS share_factor",
                          "THEN j.ratio     ELSE 2 END         AS share_factor")
    r = build_chain(STAGE_SLICE, tmp_path / "equity", seed(),
                    rule=_variant(tmp_path, "adj_factor_notok", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_adj_factor").metrics["n_not_ok_factor_ne_one"] == N_EVENTS - N_OK


def test_available_date를_공시일로_두면_EG3_adj_factor가_잡는다(tmp_path: Path) -> None:
    """EG2-P02 의 announce 축 대체 검사. 프레임 EG2 는 content_date_column 이 없어 통과한다."""
    sql = _body().replace(
        "least(j.announce_date,\n          (SELECT min(c.date) FROM cal c WHERE c.date > "
        "j.effective_date))           AS available_date",
        "j.announce_date AS available_date")
    assert sql != _body()
    r = build_chain(STAGE_SLICE, tmp_path / "equity", seed(),
                    rule=_variant(tmp_path, "adj_factor_avail", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG2").status is GateStatus.PASS
    m = _gate(r, "EG3_adj_factor").metrics
    assert m["n_available_mismatch"] == 4 and m["n_available_before_announce"] == 0


def test_sql파일에_상수_하드코딩_없음() -> None:
    import re

    text = re.sub(r"--[^\n]*", "", ADJ.sql_path.read_text(encoding="utf-8"))
    nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text))
    assert nums <= {"0", "1", "2", "-1"}, sorted(nums)
