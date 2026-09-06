"""S10 `credit_daily` — stage 절단본 위 실제 `build_table` 왕복 · 부정 픽스처
(DESIGN §4-3 · GATES §3 ⑪ · EG7-P06 · §4 FX-3-008).

손계산 기대값은 `stg_credit_daily`·`stg_units_kis` 원자료와 상류 `universe_daily` 를 직접 열어
확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  격자 36,972 = `universe_daily` 41,066 − ETF 069500 4,094 (14 티커) ·
  원장 24,722 = measured 24,628 + `_reject/pre_calendar` 85(2009년, 6 티커) +
  `_reject/off_grid` 9(상장 전·재상장 공백 회신, 전부 `_src_flag='partial'`) ·
  fill_kind = measured 24,628 / src_omitted 62 / not_collected 12,282(= 우선주 3종 × 4,094,
  credit 유닛 0건) / empty_response 0 ·
  원장 일괄 결측일 3(2018-03-28 · 2026-08-19 · 2026-08-20 — 뒤 둘은 P16 백필 08-18 종료) ·
  잔고 음수 0 · 잔고 > 상장주식수 0 · KIS 수정종가 ≠ 원주가 5,005 ·
  net_buy 후보(신규 − 상환 = 잔고 증감) 융자 17,364 / 24,711 · 대주 24,672 / 24,711.
`credit_daily` 는 equity `trading_calendar`·`universe_daily`·`price_daily` 를 입력으로 읽으므로
상류를 같은 equity_root 에 먼저 빌드한다(universe_daily 체인이 8테이블이라 전부 앞세운다).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import (
    build,
    rules_s01,
    rules_s02,
    rules_s03,
    rules_s04,
    rules_s05,
    rules_s06,
    rules_s10,
)
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"


def _seed() -> Baseline:
    """S01·S02·S03·S05·S06 상류 seed + S10 자기 seed 를 테이블 단위로 병합."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED,
              rules_s10.BASELINE_SEED):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP, rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR, rules_s03.UNIVERSE_DAILY)
CREDIT = rules_s10.CREDIT_DAILY
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG1_credit_daily", "EG3_credit_daily",
              "EG4", "EG5a"]

N_UNIVERSE = 41066                   # universe_daily 전 행 (test_equity_s03_universe.N_GRID)
N_ETF = 4094                         # 069500 격자 행 — 격자에서 빠진다
N_GRID = N_UNIVERSE - N_ETF          # 36,972
N_YEARS = 17                         # 2010 ~ 2026
N_SRC = 24722                        # stg_credit_daily 전 행
N_MEASURED = 24628
N_PRE_CALENDAR = 85
N_OFF_GRID = 9
N_SRC_OMITTED = 62
N_NOT_COLLECTED = 12282              # 003545·003547·005935 × 4,094 (credit 유닛 0건)
N_CLOSE_NE = 5005                    # KIS 수정종가(adjusted_asof_collect) ≠ 원주가
# 원장 일괄 결측일 — 격자에 종목이 있는데 measured 셀이 하나도 없는 날. 0 채움 금지의 근거다.
LEDGER_GAP_DATES = ["2018-03-28", "2026-08-19", "2026-08-20"]
# `_reject/off_grid` 9행 — 상장 전(구간 first_date 이전)·재상장 공백 기간 회신 (손계산)
OFF_GRID_ROWS = {
    ("000030", date(2014, 11, 17)), ("000030", date(2014, 11, 18)),   # 구간 시작 11-19
    ("036220", date(2024, 3, 11)), ("036220", date(2024, 3, 12)),     # 재상장 03-13
    ("101970", date(2025, 3, 26)), ("101970", date(2025, 3, 27)),     # 재상장 03-30
    ("161890", date(2012, 10, 17)), ("161890", date(2012, 10, 18)),   # 상장 10-19
    ("247540", date(2019, 3, 4)),                                     # 상장 03-05
}
# `_reject/pre_calendar` 85행 — 캘린더 하한 2010-01-04 이전 (티커별 손계산)
PRE_CALENDAR_BY_TICKER = {"000660": 13, "003540": 13, "005930": 13, "036220": 13,
                          "900050": 25, "900060": 8}
# fill_kind='src_omitted' 62행 (티커별 손계산)
SRC_OMITTED_BY_TICKER = {"000030": 3, "0001A0": 21, "000660": 3, "003540": 3, "005930": 3,
                         "036220": 4, "101970": 16, "161890": 3, "247540": 2, "900050": 2,
                         "900060": 2}
# net_buy 판정 근거 — 잔고 증감 = 신규 − 상환 이 성립하는 연속 쌍(원장 위 손계산)
N_STEP_PAIRS = 24711
N_STEP_MATCH_LOAN = 17364
N_STEP_MATCH_STLN = 24672


def _build_upstream(equity_root: Path) -> None:
    for i, rule in enumerate(UPSTREAM):
        r = build.build_table(rule, STAGE_SLICE, equity_root, SEED, build_id=f"b_up_{i:02d}")
        assert r.ok, (rule.name, [(g.name, g.status.value, g.detail) for g in r.gates])


def _baseline_for(rule: EquityTable) -> Baseline:
    """변종 테이블은 이름이 달라 baseline 을 못 찾는다 — `credit_daily` 항목을 그 이름으로 복제한다.

    복제하지 않으면 EG7 임계가 코드 기본값 0.001 로 떨어져 부정 픽스처가 노리는 게이트 앞에서
    EG7 이 먼저 폐기하고, 무엇이 잡혔는지 알 수 없게 된다.
    """
    if rule.name == CREDIT.name:
        return SEED
    return Baseline({**SEED.data, rule.name: dict(SEED.table(CREDIT.name))})


def _build_credit(equity_root: Path, rule: EquityTable | None = None,
                  build_id: str = "b_s10_credit") -> build.BuildResult:
    r = rule or CREDIT
    return build.build_table(r, STAGE_SLICE, equity_root, _baseline_for(r), build_id=build_id)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    """정상 왕복 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s10") / "equity"
    _build_upstream(eq)
    return _build_credit(eq)


@pytest.fixture
def chain(tmp_path: Path) -> Path:
    """부정 픽스처용 — 상류만 지어 둔 새 equity_root."""
    eq = tmp_path / "equity"
    _build_upstream(eq)
    return eq


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**CREDIT.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return CREDIT.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW cd AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        for t in ("stg_credit_daily", "stg_units_kis"):
            con.execute(f"CREATE OR REPLACE TEMP VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{STAGE_SLICE / t}/**/*.parquet', hive_partitioning=true, "
                        "union_by_name=true)")
        rej = out_dir / "_reject"
        if rej.exists():
            con.execute("CREATE OR REPLACE TEMP VIEW rej AS SELECT * FROM read_parquet("
                        f"'{rej}/**/*.parquet', hive_partitioning=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _metrics(r: build.BuildResult, name: str) -> dict[str, object]:
    return dict(_gate(r, name).metrics)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == GATE_ORDER
    assert not [g for g in r.gates if g.status is GateStatus.FAIL]
    assert r.n_rows == N_GRID and r.n_reject == N_PRE_CALENDAR + N_OFF_GRID
    eg1 = _metrics(r, "EG1")
    assert eg1["lhs"] == N_GRID and eg1["rhs"] == N_GRID + N_PRE_CALENDAR + N_OFF_GRID
    assert eg1["delta"] == 0
    assert set(r.inputs) == {"trading_calendar", "universe_daily", "price_daily",
                             "stg_credit_daily", "stg_units_kis"}
    assert r.inputs["universe_daily"] == "b_up_08"      # equity 입력도 고정된다


def test_date_axis는_연도_디렉토리로_갈린다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert len(r.partitions) == N_YEARS
    assert sum(int(str(p["n_rows"])) for p in r.partitions) == N_GRID
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE year <> year(date)") == [(0,)]


def test_격자는_universe_daily의_비ETF_상장행과_같다(built: build.BuildResult) -> None:
    """⑪ (a) 를 산출 밖에서 다시 센다 — ETF 는 한 행도 없고 티커 14종이다."""
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(DISTINCT ticker) FROM cd") == [(14,)]
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE ticker = '069500'") == [(0,)]
    m = _metrics(r, "EG3_credit_daily")
    assert m["n_grid_cells"] == N_GRID
    assert m["n_grid_missing"] == 0 and m["n_grid_extra"] == 0


def test_원장_행은_measured거나_격리된다(built: build.BuildResult) -> None:
    """⑪ (b) 원장 보존. 24,722 = 24,628 + 85 + 9 — 세 항을 각각 독립으로 센다."""
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM stg_credit_daily") == [(N_SRC,)]
    assert _query(r.out_dir,
                  "SELECT count(*) FROM cd WHERE fill_kind.kind = 'measured'") == [(N_MEASURED,)]
    assert r.n_reject == N_PRE_CALENDAR + N_OFF_GRID
    assert N_SRC == N_MEASURED + N_PRE_CALENDAR + N_OFF_GRID
    m = _metrics(r, "EG1_credit_daily")
    assert m["n_ledger_delta"] == 0 and m["n_reject_outside_declared"] == 0
    assert m["n_reject_by_reason"] == {"pre_calendar": N_PRE_CALENDAR, "off_grid": N_OFF_GRID}


def test_격리는_사유별_디렉토리로_간다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert sorted(p.name for p in (r.out_dir / "_reject").iterdir()) == [
        "reject_reason=off_grid", "reject_reason=pre_calendar"]
    off = _query(r.out_dir, "SELECT ticker, date FROM rej "
                            "WHERE reject_reason = 'off_grid' ORDER BY ticker, date")
    assert {(str(t), d) for t, d in off} == OFF_GRID_ROWS
    pre = _query(r.out_dir, "SELECT ticker, count(*) FROM rej "
                            "WHERE reject_reason = 'pre_calendar' GROUP BY 1 ORDER BY 1")
    assert {str(t): int(str(n)) for t, n in pre} == PRE_CALENDAR_BY_TICKER
    # 격리 사유는 배타적이고 캘린더 하한이 가른다
    assert _query(r.out_dir, "SELECT count(*) FROM rej WHERE reject_reason = 'pre_calendar' "
                             "AND date >= DATE '2010-01-04'") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM rej WHERE reject_reason = 'off_grid' "
                             "AND date < DATE '2010-01-04'") == [(0,)]


def test_fill_kind는_수집로그로_갈린다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    got = {f"{k}/{e}": int(str(n)) for k, e, n in _query(
        r.out_dir, "SELECT fill_kind.kind, fill_kind.evidence, count(*) FROM cd "
                   "GROUP BY 1, 2 ORDER BY 1, 2")}
    assert got == {"measured/unit_ok": N_MEASURED, "not_collected/none": N_NOT_COLLECTED,
                   "src_omitted/unit_ok": N_SRC_OMITTED}
    # not_collected 는 credit 유닛이 하나도 없는 우선주 3종 전 구간이다
    assert _query(r.out_dir, "SELECT count(*) FROM stg_units_kis WHERE dataset = 'credit' "
                             "AND ticker IN ('003545', '003547', '005935')") == [(0,)]
    assert {str(t): int(str(n)) for t, n in _query(
        r.out_dir, "SELECT ticker, count(*) FROM cd WHERE fill_kind.kind = 'not_collected' "
                   "GROUP BY 1 ORDER BY 1")} == {"003545": N_ETF, "003547": N_ETF,
                                                 "005935": N_ETF}
    assert {str(t): int(str(n)) for t, n in _query(
        r.out_dir, "SELECT ticker, count(*) FROM cd WHERE fill_kind.kind = 'src_omitted' "
                   "GROUP BY 1 ORDER BY 1")} == SRC_OMITTED_BY_TICKER
    # 절단본에는 empty 유닛만 덮는 셀이 없다(0001A0 의 empty 창을 ok 창이 덮는다)
    assert _query(r.out_dir,
                  "SELECT count(*) FROM cd WHERE fill_kind.kind = 'empty_response'") == [(0,)]


def test_measured_셀은_원장_값을_그대로_나른다(built: build.BuildResult) -> None:
    """원값 보존 — 17축을 원장에 다시 조인해 다른 행 0 (EG3_credit_daily 와 독립 검사)."""
    r = built
    assert r.out_dir is not None
    cols = ("stlm_date", *rules_s10.MEASURE_COLUMNS)
    diff = " OR ".join(f'c."{k}" IS DISTINCT FROM s."{k}"' for k in cols)
    assert _query(r.out_dir, f"""
        SELECT count(*) FROM cd c JOIN stg_credit_daily s USING (ticker, date)
         WHERE c.fill_kind.kind = 'measured' AND ({diff})""") == [(0,)]
    row = _query(r.out_dir, "SELECT whol_loan_rmnd_stcn_shr, whol_loan_rmnd_amt, "
                            "whol_stln_rmnd_stcn_shr, stlm_date FROM cd "
                            "WHERE ticker = '005930' AND date = DATE '2020-01-02'")
    assert row == [(Decimal("2435031"), Decimal("11467248"), Decimal("29538"),
                    date(2020, 1, 6))]


def test_measured가_아닌_셀은_값이_없다(built: build.BuildResult) -> None:
    """0 을 굽지 않는다 — src_omitted·not_collected 셀은 17축 전부 NULL."""
    r = built
    assert r.out_dir is not None
    notnull = " OR ".join(f'"{c}" IS NOT NULL' for c in rules_s10.MEASURE_COLUMNS)
    assert _query(r.out_dir, f"""
        SELECT count(*) FROM cd
         WHERE fill_kind.kind <> 'measured' AND (({notnull}) OR stlm_date IS NOT NULL)
        """) == [(0,)]
    # 지식 축은 그대로 남는다 — 행은 있고, 어느 축도 NULL 이 아니다
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE fill_kind.kind <> 'measured' "
                             "AND (amt_basis IS NULL OR available_date IS NULL "
                             "OR available_basis IS NULL)") == [(0,)]
    assert _metrics(r, "EG3_credit_daily")["n_unmeasured_value_present"] == 0


def test_원장_일괄_결측일은_0으로_채우지_않는다(built: build.BuildResult) -> None:
    """2018-03-28(전 종목 결측) · 2026-08-19·20(P16 백필 08-18 종료).

    0 채움 규약을 그대로 옮겼다면 이 사흘의 전 종목 잔고가 0 이 되어 신용잔고 팩터에 가짜 급락·
    급반등이 실린다. 앞뒤 거래일에는 원장 행이 있으므로 '원천이 0 을 생략했다' 로 읽을 수 없다.
    """
    r = built
    assert r.out_dir is not None
    assert _metrics(r, "EG3_credit_daily")["ledger_gap_dates"] == LEDGER_GAP_DATES
    assert _query(r.out_dir, "SELECT count(*) FROM stg_credit_daily "
                             "WHERE date = DATE '2018-03-28'") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM stg_credit_daily "
                             "WHERE date IN (DATE '2018-03-27', DATE '2018-03-29')") == [(10,)]
    assert _query(r.out_dir, """
        SELECT count(*), count(*) FILTER (WHERE whol_loan_rmnd_stcn_shr IS NULL)
          FROM cd WHERE date = DATE '2018-03-28'""") == [(8, 8)]


def test_잔고는_음수가_아니고_상장주식수를_넘지_않는다(built: build.BuildResult) -> None:
    r = built
    m = _metrics(r, "EG3_credit_daily")
    assert m["n_balance_negative"] == 0
    assert m["n_balance_over_shares_out"] == 0
    assert m["n_shares_out_null_measured"] == 0
    # 신규·상환·증감율의 음수는 원천 사실이라 폐기형이 아니라 기록형이다
    assert m["n_negative_by_column"] == {
        "whol_loan_new_stcn_shr": 5, "whol_loan_rdmp_stcn_shr": 4, "whol_loan_gvrt_pct": 4,
        "whol_stln_new_stcn_shr": 0, "whol_stln_rdmp_stcn_shr": 0, "whol_stln_gvrt_pct": 0}


def test_available_date는_date이고_basis는_default(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE available_date IS DISTINCT FROM date "
                             "OR available_basis IS DISTINCT FROM 'default'") == [(0,)]
    # stlm_date 는 미래(T+2 이상)지만 PIT 축이 아니다 — available_date 를 밀지 않는다
    assert _query(r.out_dir, "SELECT count(*) FROM cd WHERE stlm_date <= date") == [(0,)]
    assert _query(r.out_dir, "SELECT min(date_diff('day', date, stlm_date)) FROM cd "
                             "WHERE stlm_date IS NOT NULL") == [(2,)]
    assert _gate(r, "EG2").status is GateStatus.PASS


def test_amt_컬럼은_단위_미상이라_krw_접미사가_없다(built: build.BuildResult) -> None:
    """FX-3-009 규약 — 단위 미확정 컬럼에 단위 접미사를 붙이지 않는다."""
    amt = [c for c in CREDIT.columns if c.endswith("_amt")]
    assert len(amt) == 6
    assert not [c for c in CREDIT.columns if c.endswith("_krw")]
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(DISTINCT amt_basis) FROM cd") == [(1,)]
    assert _query(r.out_dir, "SELECT DISTINCT amt_basis FROM cd") == [("unknown",)]
    # 금액 / (잔고주수 × 원주가) 이 1 근처가 아니다 = 원화 금액이 아니다(단위 미상의 실측 근거)
    ratio = _metrics(r, "EG3_credit_daily")["loan_amt_per_market_value"]
    assert isinstance(ratio, dict)
    assert float(str(ratio["p50"])) < 0.001


def test_net_buy_축은_원장에_없다(built: build.BuildResult) -> None:
    """FIELD_MAP §2 `credit.net_buy` 판정(S10) = 부재.

    원장 39컬럼에 순매수 축이 없고, 유일한 후보 `신규 − 상환` 은 잔고 증감과도 맞지 않는다.
    """
    assert not [c for c in CREDIT.columns if "net" in c]
    r = built
    m = _metrics(r, "EG3_credit_daily")
    assert m["net_buy_axis"] == "absent"
    assert m["loan_balance_step"] == {"n_pairs": N_STEP_PAIRS,
                                      "n_step_equals_new_minus_rdmp": N_STEP_MATCH_LOAN}
    assert m["stln_balance_step"] == {"n_pairs": N_STEP_PAIRS,
                                      "n_step_equals_new_minus_rdmp": N_STEP_MATCH_STLN}
    assert N_STEP_MATCH_LOAN / N_STEP_PAIRS < 0.75


def test_KIS_수정종가는_나르지_않고_기록만_한다(built: build.BuildResult) -> None:
    """`price_basis_close='adjusted_asof_collect'` — PIT 축이 아니고 원주가 정본은 price_daily."""
    assert not [c for c in CREDIT.columns if c in ("close", "close_krw", "open", "volume_shr")]
    m = _metrics(built, "EG3_credit_daily")
    assert m["n_close_compared"] == N_MEASURED
    assert m["n_close_ne_price_daily"] == N_CLOSE_NE


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    got = [str(c[0]) for c in _query(r.out_dir, "DESCRIBE cd")]
    # `v`·`year` 는 하이브 경로 축이라 선언 스키마가 아니다
    assert [c for c in got if c not in ("year", "v")] == list(CREDIT.columns)


def test_격자_술어는_rules_선언과_SQL_리터럴이_같다() -> None:
    """`grid_predicate()` 가 SQL·EG1 우변·EG3 재계산의 단일 정의라는 것을 글자로 확인한다."""
    assert rules_s10.grid_predicate("u") in _body()          # sql/credit_daily.sql `grid` CTE
    assert f"dataset = '{rules_s10.UNIT_DATASET}'" in _body()
    assert rules_s10.grid_predicate("u") in CREDIT.eg1_rhs_sql
    assert rules_s10.grid_predicate() in CREDIT.eg1_rhs_sql


def test_상수는_seed에_임계_하나뿐이다() -> None:
    assert CREDIT.consts == ()
    seed = load(rules_s10.BASELINE_SEED)
    assert seed.get("credit_daily", "threshold_EG7") == 0.005
    # 절단본 격리 비율이 코드 기본값 0.001 을 구조적으로 넘는다 — 그래서 seed 가 필요하다
    assert (N_PRE_CALENDAR + N_OFF_GRID) / (N_GRID + N_PRE_CALENDAR + N_OFF_GRID) > 0.001


def test_재빌드는_같은_파티션_해시를_낸다(chain: Path) -> None:
    """EG5a — 같은 inputs·같은 규칙 판본이면 파티션 content_hash 전량 동일."""
    first = _build_credit(chain, build_id="b_s10_a")
    assert first.ok, [(g.name, g.status.value, g.detail) for g in first.gates]
    assert _gate(first, "EG5a").status is GateStatus.SKIP
    second = _build_credit(chain, build_id="b_s10_b")
    assert second.ok, [(g.name, g.status.value, g.detail) for g in second.gates]
    eg5a = _gate(second, "EG5a")
    assert eg5a.status is GateStatus.PASS, eg5a.detail
    assert eg5a.metrics["n_changed_partitions"] == 0
    assert second.content_hash == first.content_hash


def test_meta에_게이트_판정이_실린다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    meta = json.loads((r.out_dir / "year=2020" / "_meta.json").read_text(encoding="utf-8"))
    assert [g["name"] for g in meta["gates"]] == GATE_ORDER
    assert meta["n_reject_by_reason"] == {"off_grid": N_OFF_GRID,
                                          "pre_calendar": N_PRE_CALENDAR}
    assert meta["table"] == "credit_daily"


# ── 부정 픽스처 (GATES §7-5) ─────────────────────────────────────────────────

def test_격자에서_한_행을_빼면_EG1이_폐기한다(chain: Path, tmp_path: Path) -> None:
    keep = "SELECT g.date, g.ticker, NULL::VARCHAR AS reject_reason\n    FROM grid g"
    sql = _body().replace(
        keep,
        keep + "\n    WHERE NOT (g.ticker = '005930' AND g.date = DATE '2020-01-02')")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_missing_row", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL
    assert eg1.metrics["delta"] == -1


def test_ETF를_격자에_넣으면_EG1이_폐기한다(chain: Path, tmp_path: Path) -> None:
    """격자 술어를 늦추면 ETF 4,094행이 들어와 좌변이 커진다 — 생존편향의 반대 방향 사고."""
    sql = _body().replace("AND u.sec_type IS DISTINCT FROM 'etf'", "")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_with_etf", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL
    assert eg1.metrics["delta"] == N_ETF


def test_격자_밖_원장행을_버리면_EG1이_폐기한다(chain: Path, tmp_path: Path) -> None:
    """격리 대신 조용히 버리면 EG1 우변(격자 + 격자 밖 원장 행)이 남아 등식이 깨진다."""
    body = _body()
    start = body.index("    UNION ALL\n    SELECT c.date, c.ticker,")
    end = body.index("),\ncell AS (")
    sql = body[:start] + body[end:]
    r = _build_credit(chain, _variant(tmp_path, "credit_drop_offgrid", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL
    assert eg1.metrics["delta"] == -(N_PRE_CALENDAR + N_OFF_GRID)


def test_measured를_미수집으로_적으면_EG1_credit_daily가_폐기한다(chain: Path,
                                                              tmp_path: Path) -> None:
    """⑪ (b) 전용 부정 픽스처 — 행수(a)는 그대로라 프레임 EG1 은 통과한다."""
    sql = _body().replace(
        "CASE WHEN c.has_row    THEN 'measured'",
        "CASE WHEN c.has_row AND NOT (c.ticker = '005930' "
        "AND c.date = DATE '2020-01-02') THEN 'measured'")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_lost_measured", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    g = _gate(r, "EG1_credit_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_ledger_delta"] == 1
    assert g.metrics["n_measured_cells"] == N_MEASURED - 1


def test_src_omitted에_0을_채우면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    """0 채움 규약(FX-3-001)을 신용 격자에 옮기면 원장 결측일이 잔고 0 으로 굳는다."""
    sql = _body().replace(
        "    whol_loan_rmnd_stcn_shr,\n",
        "    CASE WHEN kind = 'src_omitted' THEN 0 ELSE whol_loan_rmnd_stcn_shr END "
        "AS whol_loan_rmnd_stcn_shr,\n")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_zero_fill", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_unmeasured_value_present"] == N_SRC_OMITTED


def test_잔고가_상장주식수를_넘으면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    sql = _body().replace(
        "    whol_loan_rmnd_stcn_shr,\n",
        "    whol_loan_rmnd_stcn_shr * 1000 AS whol_loan_rmnd_stcn_shr,\n")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_over_shares", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert int(str(g.metrics["n_balance_over_shares_out"])) > 0


def test_잔고가_음수면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    sql = _body().replace(
        "    whol_stln_rmnd_stcn_shr,\n",
        "    -whol_stln_rmnd_stcn_shr AS whol_stln_rmnd_stcn_shr,\n")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_negative_balance", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert int(str(g.metrics["n_balance_negative"])) > 0


def test_어휘_밖_fill_kind는_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    sql = _body().replace("WHEN c.unit_ok    THEN 'src_omitted'",
                          "WHEN c.unit_ok    THEN 'zero_filled'")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_bad_kind", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_fill_kind_outside_vocab"] == N_SRC_OMITTED


def test_stlm_date를_거래일보다_이르게_하면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    sql = _body().replace(
        "    stlm_date,\n",
        "    CASE WHEN stlm_date IS NULL THEN NULL ELSE date - 1 END AS stlm_date,\n")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_early_stlm", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert int(str(g.metrics["n_stlm_date_before_date"])) > 0


def test_격자_밖_행을_채택하면_EG3_credit_daily가_폐기한다(chain: Path, tmp_path: Path) -> None:
    """off_grid 를 격리하지 않고 채택하면 행수 등식은 그대로 닫힌다 — 격자 술어만이 잡는다.

    좌변 36,981 = 격자 36,972 + off_grid 9, 우변 37,066 − 격리 85 = 36,981. EG1 이 통과하므로
    "격자 밖 셀이 산출에 섞였다" 는 EG3_credit_daily 의 `n_grid_extra` 가 유일한 방어선이다.
    """
    sql = _body().replace("ELSE 'off_grid' END", "ELSE NULL END")
    assert sql != _body()
    r = _build_credit(chain, _variant(tmp_path, "credit_offgrid_adopted", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    assert r.n_rows == N_GRID + N_OFF_GRID and r.n_reject == N_PRE_CALENDAR
    assert _gate(r, "EG1").status is GateStatus.PASS
    g = _gate(r, "EG3_credit_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_grid_extra"] == N_OFF_GRID
