"""S09 `short_daily` — 절단본 위 실빌드 왕복(체인 10테이블) · 합성 stage 위 결측 3분류 · 부정 픽스처
(DESIGN v1.2 §4-3 · GATES v1.0 §3 ⑪ · §2 13행 · §4 FX-3-001·002·003·009 · §1 EG7-P06).

절단본 실측(손계산, 빌드 SQL 과 독립 — `SELECT … FROM stg_* ⋈ universe_daily` 로 직접 셈):
격자 **36,972** = `universe_daily` 41,066 − ETF(069500) 4,094. 14티커(069500 제외)의 구간 행수 합과
같다. 격리 **1,042** = 전건 `pre_calendar`(키움 ka10014 의 2008-06-23~2009-12-30: 000660 363 ·
003540 300 · 005930 379) · `off_grid` 0.
원장 보존: 키움 18,265 = 격자 measured 17,223 + pre_calendar 1,042 / KIS 공매도 3,891 = measured
3,891 / KIS 대차 1,959 = measured 1,959. 재수집 접힘(dedup)은 절단본에 0 이다.
`fill_kind` — 키움: measured 17,223 · src_omitted 3,576(샤드가 덮는데 원장 행 없음: 036220 1,912 ·
003540 764 · 101970 676 · 247540 86 · 161890 77 · 000660 34 · 005930 22 · 0001A0 5) ·
not_collected 16,173(ka10014 샤드가 없는 6티커: 003545·003547·005935 각 4,094 · 900050 1,916 ·
000030 1,037 · 900060 938). KIS 공매도: measured 3,891 · not_collected 33,081. KIS 대차:
measured 1,959 · not_collected 35,013. 두 원천이 같은 (ticker, date) 를 잰 겹침 셀은 **0** —
KIS 는 폐지 3종, 키움은 존속 8종으로 커버가 갈린다. `empty_response`(샤드·유닛 empty)와 재수집 접힘,
겹침 구간의 상관·비율 분위수는 절단본에 사례가 없어 `make_stage_tree` 합성 stage 가 본다.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
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
    rules_s09,
)
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import BASIS_VOCAB, FILL_EVIDENCE, FILL_KINDS, EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SHORT = rules_s09.SHORT_DAILY
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG1_short_daily", "EG3_short_daily",
              "EG4", "EG5a"]
# S09 격자는 universe_daily(S03) 위에 선다 — S03C 가 adj_factor 를 읽으므로 계수 체인까지 앞세운다
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP, rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR, rules_s03.UNIVERSE_DAILY)

N_UNIVERSE = 41066                   # test_equity_s03_universe.N_GRID
N_ETF = 4094                         # 069500 구간 행수
N_GRID = N_UNIVERSE - N_ETF          # 36,972 — S09 격자
N_REJECT = 1042                      # 전건 pre_calendar
PRE_CALENDAR_BY_TICKER = {"000660": 363, "003540": 300, "005930": 379}
N_SRC = {"short_kiwoom": 18265, "short_kis": 3891, "loan_kis": 1959}
N_MEASURED = {"short_kiwoom": 17223, "short_kis": 3891, "loan_kis": 1959}
# 티커별 격자 행수 = universe_daily 구간 행수(test_equity_s03_universe.N_ROWS_BY_TICKER − ETF)
N_ROWS_BY_TICKER = {
    "000030": 1037, "0001A0": 135, "000660": 4094, "003540": 4094, "003545": 4094,
    "003547": 4094, "005930": 4094, "005935": 4094, "036220": 2163, "101970": 989,
    "161890": 3396, "247540": 1834, "900050": 1916, "900060": 938}
KIWOOM_MEASURED_BY_TICKER = {"0001A0": 130, "000660": 4060, "003540": 3330, "005930": 4072,
                             "036220": 251, "101970": 313, "161890": 3319, "247540": 1748}
KIWOOM_SRC_OMITTED_BY_TICKER = {"0001A0": 5, "000660": 34, "003540": 764, "005930": 22,
                                "036220": 1912, "101970": 676, "161890": 77, "247540": 86}
# ka10014 샤드가 없는 6티커 = 그 티커의 격자 행 전부가 not_collected
KIWOOM_NOT_COLLECTED_BY_TICKER = {"000030": 1037, "003545": 4094, "003547": 4094,
                                  "005935": 4094, "900050": 1916, "900060": 938}
N_LENDING_NEGATIVE = 5               # 900050 2017-09-20~09-26 (stage 가 keep 한 원장 음수)
N_LENDING_KRW_NEGATIVE = 4
LENDING_MIN_SHR = "-1408804"
N_KIS_AVG_PRICE_NULL = 1476          # 원장 '0' → stage ledger_zero → NULL. ssts_cntg_qty 0 과 동수
N_KIS_ACML_INVALID = 33              # 요청 창 첫 행(acml_valid=false)
N_YEARS = 17                         # 2010~2026


def _seed() -> Baseline:
    """S01·S02·S03·S05·S06(상류 체인) + S09 seed 병합 — `_const`·게이트 임계가 테이블 단위라
    이름이 겹치지 않는다(test_equity_s03_universe._seed 와 같은 규약)."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED,
              rules_s09.BASELINE_SEED):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW o AS SELECT * FROM "
                    f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=false)")
        rej = out_dir / "_reject" / "reject_reason=*" / "*.parquet"
        if (out_dir / "_reject").exists():
            con.execute(f"CREATE VIEW rej AS SELECT * FROM read_parquet('{rej}', "
                        "hive_partitioning=true)")
        for t in ("stg_short_daily_kiwoom", "stg_short_daily_kis", "stg_loan_daily_kis",
                  "stg_units_kis", "stg_shards_kiwoom"):
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{STAGE_SLICE / t}/**/*.parquet', hive_partitioning=true, "
                        "union_by_name=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _fails(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is GateStatus.FAIL]


def _seed_as(name: str) -> Baseline:
    """변종 이름으로 같은 임계를 재등재 — `threshold_EG7` 조회 키가 `{table}` 이라 없으면 코드
    기본값 0.001 로 떨어져 정상 빌드가 EG7 에서 폐기된다."""
    return Baseline({**SEED.data, name: SEED.table(SHORT.name)})


def _variant(built: build.BuildResult, tmp_path: Path, name: str, body_wrap: str,
             **overrides: object) -> build.BuildResult:
    """산출 SQL 만 바꾼 변종을 같은 equity_root 에 빌드한다(정본 MANIFEST 를 건드리지 않는다)."""
    assert built.out_dir is not None
    body = SHORT.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    p = tmp_path / f"{name}.sql"
    p.write_text(body_wrap.format(body=body), encoding="utf-8")
    rule = EquityTable(**{**SHORT.__dict__, "name": name, "sql_path": p, **overrides})
    return build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], _seed_as(name),
                             build_id=f"b_{name}",
                             fixtures_path=Path(rules_s09.__file__).parent / "fixtures" /
                             "short_daily.json")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s09") / "equity"
    for rule in UPSTREAM:
        r = build.build_table(rule, STAGE_SLICE, root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, _fails(r)
    return build.build_table(SHORT, STAGE_SLICE, root, SEED, build_id="b_s09_short")


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, _fails(built)
    assert [g.name for g in built.gates] == GATE_ORDER
    assert {g.name: g.status.value for g in built.gates if g.status is not GateStatus.PASS} == {
        "EG5a": "skip"}                                   # 첫 빌드 — no_previous_build
    assert (built.n_rows, built.n_reject) == (N_GRID, N_REJECT)
    assert len(built.partitions) == N_YEARS


def test_입력은_equity_2와_stage_5다(built: build.BuildResult) -> None:
    """팩트 원천 3 + `fill_kind` 증거 축 2(샤드·유닛). 증거 축이 빠지면 결측 3분류가 무너진다."""
    assert set(built.inputs) == set(SHORT.inputs)
    assert built.inputs["universe_daily"] == "b_universe_daily"
    assert built.inputs["trading_calendar"] == "b_trading_calendar"
    assert {t for t in SHORT.inputs if t.startswith("stg_")} == {
        "stg_short_daily_kiwoom", "stg_short_daily_kis", "stg_loan_daily_kis",
        "stg_shards_kiwoom", "stg_units_kis"}
    # 가격·거래량 축은 price_daily 정본이 이미 가지고 있다 — 원천 재수록 금지
    assert not {"stg_price_daily", "price_daily"} & set(SHORT.inputs)


def test_격자는_universe_daily의_비ETF_상장구간이다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    got = dict(_query(built.out_dir, "SELECT ticker, count(*) FROM o GROUP BY 1"))
    assert got == N_ROWS_BY_TICKER
    assert sum(got.values()) == N_GRID
    assert "069500" not in got                              # ETF 는 격자 밖(DESIGN §4-3)
    assert _query(built.out_dir, "SELECT min(date), max(date) FROM o") == [
        (date(2010, 1, 4), date(2026, 8, 20))]


def test_EG1_우변은_격자에_격자밖_원장셀을_더한_값이다(built: build.BuildResult) -> None:
    eg1 = _gate(built, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["expect"], eg1["delta"]) == (
        N_GRID, N_GRID + N_REJECT, N_GRID, 0)
    assert "universe_daily" in str(eg1["rhs_sql"])           # 우변은 격자를 독립 재계산한다


def test_소스별_원장_보존_등식이_성립한다(built: build.BuildResult) -> None:
    """GATES §3 ⑪ (b). 원장 행은 격자 셀 · 접힘 · 격리 셋 중 하나로만 간다."""
    m = _gate(built, "EG1_short_daily").metrics
    for label in N_SRC:
        assert m[f"delta_{label}"] == 0
        assert m[f"n_src_{label}"] == N_SRC[label]
        assert m[f"n_measured_{label}"] == N_MEASURED[label]
        assert m[f"n_dedup_{label}"] == 0                    # 절단본엔 재수집 판본이 없다
    assert m["n_reject_pre_calendar_short_kiwoom"] == N_REJECT
    assert m["n_reject_off_grid_short_kiwoom"] == 0
    assert m["n_reject_pre_calendar_short_kis"] == 0
    assert m["n_reject_pre_calendar_loan_kis"] == 0
    assert m["reject_by_reason"] == {"pre_calendar": N_REJECT}


def test_fill_kind는_로그축이_판정한다(built: build.BuildResult) -> None:
    """원장 행이 있으면 measured, 없으면 샤드·유닛이 말하는 대로. 로그가 없으면 not_collected."""
    m = _gate(built, "EG3_short_daily").metrics
    assert m["fill_kind_short_kiwoom"] == {
        "measured:shard_done": 17223, "src_omitted:shard_done": 3576,
        "not_collected:none": 16173}
    assert m["fill_kind_short_kis"] == {"measured:unit_ok": 3891, "not_collected:none": 33081}
    assert m["fill_kind_loan_kis"] == {"measured:unit_ok": 1959, "not_collected:none": 35013}
    assert sum(m["fill_kind_short_kiwoom"].values()) == N_GRID   # type: ignore[union-attr]
    assert m["n_measured_with_empty_evidence_short_kiwoom"] == 0
    assert m["n_grid_without_price_axis"] == 0                   # 격자가 가격 축을 보장한다
    assert (m["n_kiwoom_shard_rows"], m["n_kiwoom_shard_tickers"]) == (8, 8)  # 접힘 없음


def test_샤드가_없는_티커는_src_omitted가_아니라_not_collected다(built: build.BuildResult) -> None:
    """FX-3-003. '수집했는데 원천이 안 줬다' 와 '수집 자체를 안 했다' 를 가르는 축이다."""
    assert built.out_dir is not None
    by_kind = {
        kind: dict(_query(built.out_dir, "SELECT ticker, count(*) FROM o "
                                         f"WHERE fill_kind_short_kiwoom.kind = '{kind}' "
                                         "GROUP BY 1"))
        for kind in ("measured", "src_omitted", "not_collected")}
    assert by_kind["measured"] == KIWOOM_MEASURED_BY_TICKER
    assert by_kind["src_omitted"] == KIWOOM_SRC_OMITTED_BY_TICKER
    assert by_kind["not_collected"] == KIWOOM_NOT_COLLECTED_BY_TICKER
    assert set(by_kind["not_collected"]) & set(by_kind["src_omitted"]) == set()
    # 그 6티커는 ka10014 샤드 자체가 없다 (독립 재확인)
    shard_tickers = {str(r[0]) for r in _query(
        built.out_dir, "SELECT DISTINCT ticker FROM stg_shards_kiwoom WHERE src_api = 'ka10014'")}
    assert shard_tickers & set(KIWOOM_NOT_COLLECTED_BY_TICKER) == set()


def test_세_원천의_원값을_그대로_나른다(built: build.BuildResult) -> None:
    """조인·격자만 한다(DESIGN §1) — 산출을 stage 에 독립 재조인해 다른 셀 0."""
    assert built.out_dir is not None
    assert _query(built.out_dir, """
        SELECT count(*) FROM o JOIN stg_short_daily_kiwoom s USING (ticker, date)
        WHERE o.short_volume_kiwoom_shr IS DISTINCT FROM s.shrts_qty_shr
           OR o.short_value_kiwoom_krw IS DISTINCT FROM s.shrts_trde_prica_krw
           OR o.short_weight_kiwoom_pct IS DISTINCT FROM s.trde_wght_pct
           OR o.short_avg_price_kiwoom_raw IS DISTINCT FROM s.shrts_avg_pric""") == [(0,)]
    assert _query(built.out_dir, """
        SELECT count(*) FROM o JOIN stg_short_daily_kis s USING (ticker, date)
        WHERE o.short_volume_kis_shr IS DISTINCT FROM s.ssts_cntg_qty_shr
           OR o.short_value_kis_krw IS DISTINCT FROM s.ssts_tr_pbmn_krw
           OR o.short_volume_ratio_kis_pct IS DISTINCT FROM s.ssts_vol_rlim_pct
           OR o.short_value_ratio_kis_pct IS DISTINCT FROM s.ssts_tr_pbmn_rlim_pct
           OR o.short_avg_price_kis_krw IS DISTINCT FROM s.avrg_prc_krw""") == [(0,)]
    assert _query(built.out_dir, """
        SELECT count(*) FROM o JOIN stg_loan_daily_kis s USING (ticker, date)
        WHERE o.lending_new_kis_shr IS DISTINCT FROM s.new_stcn_shr
           OR o.lending_redeem_kis_shr IS DISTINCT FROM s.rdmp_stcn_shr
           OR o.lending_balance_kis_shr IS DISTINCT FROM s.rmnd_stcn_shr
           OR o.lending_balance_kis_krw IS DISTINCT FROM s.rmnd_amt_krw""") == [(0,)]


def test_pre_calendar_격리는_캘린더_하한_이전_키움행이다(built: build.BuildResult) -> None:
    """EG7-P06. 버리지 않고 같은 스키마로 `_reject/reject_reason=pre_calendar/` 에 남긴다."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT reject_reason, count(*) FROM rej GROUP BY 1") == [
        ("pre_calendar", N_REJECT)]
    assert dict(_query(built.out_dir, "SELECT ticker, count(*) FROM rej GROUP BY 1")) == \
        PRE_CALENDAR_BY_TICKER
    assert _query(built.out_dir, "SELECT max(date) FROM rej") == [(date(2009, 12, 30),)]
    # 격리 행도 원천 값과 fill_kind 를 그대로 싣는다 — 사유만 붙는다
    assert _query(built.out_dir, """
        SELECT count(*) FROM rej WHERE fill_kind_short_kiwoom.kind <> 'measured'""") == [(0,)]
    assert set(SHORT.reject_reasons) == {"pre_calendar", "off_grid"}


def test_단위_미측정_축은_raw와_basis로_간다(built: build.BuildResult) -> None:
    """FX-3-009 규약. stage 가 단위를 못 잰 `shrts_avg_pric` 에 `_krw` 를 붙이지 않는다."""
    assert built.out_dir is not None
    assert "short_avg_price_kiwoom_raw" in SHORT.columns
    assert not any(c.startswith("short_avg_price_kiwoom") and c.endswith("_krw")
                   for c in SHORT.columns)
    assert rules_s09.UNIT_LABELS["short_avg_price_kiwoom_raw"] == "unknown"
    assert dict(_query(built.out_dir, "SELECT short_avg_price_kiwoom_basis, count(*) FROM o "
                                      "GROUP BY 1")) == {"unknown": 17223, None: 19749}
    assert _query(built.out_dir, """
        SELECT count(*) FROM o
        WHERE (short_avg_price_kiwoom_raw IS NULL) <> (short_avg_price_kiwoom_basis IS NULL)""") \
        == [(0,)]
    assert "unknown" in BASIS_VOCAB


def test_대차_음수_잔고는_보존하고_건수만_기록한다(built: build.BuildResult) -> None:
    """HANDOFF §3 「rmnd_stcn_shr 음수 2,691 keep」. abs·0 클립은 사실 왜곡이다."""
    m = _gate(built, "EG3_short_daily").metrics
    assert m["n_lending_balance_negative"] == N_LENDING_NEGATIVE
    assert m["n_lending_balance_krw_negative"] == N_LENDING_KRW_NEGATIVE
    assert m["lending_balance_min_shr"] == LENDING_MIN_SHR
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT ticker, count(*) FROM o "
                                 "WHERE lending_balance_kis_shr < 0 GROUP BY 1") == [
        ("900050", N_LENDING_NEGATIVE)]


def test_KIS_공매도_평균가의_원장_0은_NULL이다(built: build.BuildResult) -> None:
    """stage 가 '0' → miss_kind='ledger_zero' 로 NULL 처리한 축(SPEC §2-10). 0원 체결가가 아니다."""
    m = _gate(built, "EG3_short_daily").metrics
    assert m["n_short_avg_price_kis_null_measured"] == N_KIS_AVG_PRICE_NULL
    assert m["n_kis_acml_invalid"] == N_KIS_ACML_INVALID
    assert built.out_dir is not None
    # 평균가 NULL 행 = 그날 공매도 체결이 0 인 행. 둘의 행수가 정확히 같다
    assert _query(built.out_dir, """
        SELECT count(*) FROM o WHERE fill_kind_short_kis.kind = 'measured'
          AND (short_avg_price_kis_krw IS NULL) <> (short_volume_kis_shr = 0)""") == [(0,)]


def test_겹침_구간이_없으면_상관은_NULL로_기록된다(built: build.BuildResult) -> None:
    """두 원천의 커버가 갈려 있다(KIS 폐지 3종 · 키움 존속 8종) — 지표를 0 으로 굳히지 않는다."""
    m = _gate(built, "EG3_short_daily").metrics
    assert m["n_overlap_src_measured"] == 0
    assert m["corr_short_volume_kis_kiwoom"] is None
    assert m["n_overlap_ratio_rows"] == 0
    assert set(m["short_volume_ratio_quantiles"].values()) == {None}  # type: ignore[union-attr]


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    """키움 5 → KIS 공매도 5 → KIS 대차 4 → fill_kind 3 → available 2 (DESIGN §4-3 순서)."""
    assert built.out_dir is not None
    cols = [str(r[0]) for r in _query(built.out_dir, "DESCRIBE o")]
    assert cols == list(SHORT.columns)
    assert cols[-5:] == ["fill_kind_short_kiwoom", "fill_kind_short_kis", "fill_kind_loan_kis",
                         "available_date", "available_basis"]
    assert [c for c in cols if c.endswith("_kiwoom_shr") or c.endswith("_kiwoom_krw")]
    assert [c for c in cols if c.endswith("_kis_shr") or c.endswith("_kis_krw")]


def test_available_date는_date이고_basis는_default다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FROM o WHERE available_date <> date "
                                 "OR available_basis <> 'default'") == [(0,)]
    assert SHORT.available_basis == ("default",)
    assert _gate(built, "EG2").metrics["n_available_before_content"] == 0


def test_격자_어휘는_상류_어휘의_부분집합이다() -> None:
    """격자 술어가 `universe_daily` 어휘에서 갈라지면 조용히 빈 격자가 된다."""
    assert set(rules_s09.GRID_STATUSES) <= set(rules_s03.STATUS_VOCAB)
    assert set(rules_s09.GRID_EXCLUDED_SEC_TYPES) <= set(rules_s01.SEC_TYPE_VOCAB)
    assert "delisted" not in rules_s09.GRID_STATUSES
    assert set(rules_s09.UNIT_LABELS) < set(SHORT.columns)
    assert {fk for _l, _s, fk, _v in rules_s09.SOURCES} <= set(SHORT.columns)


def test_같은_inputs_재빌드는_파티션_해시가_같다(built: build.BuildResult) -> None:
    root = built.out_dir.parent.parent            # type: ignore[union-attr]
    again = build.build_table(SHORT, STAGE_SLICE, root, SEED, build_id="b_s09_short_2")
    assert again.ok, _fails(again)
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS and eg5.metrics["n_changed_partitions"] == 0
    assert again.content_hash == built.content_hash


# ── 부정 픽스처 (§7-5) ────────────────────────────────────────────────────────

def test_격자에서_행을_빼면_EG1이_잡는다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "short_drop_ticker",
                 "SELECT * FROM ({body}) WHERE NOT (ticker = '005935' AND reject_reason IS NULL)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL and eg1.metrics["delta"] == -N_ROWS_BY_TICKER["005935"]


def test_격리행을_채택행으로_올리면_소스별_등식이_깨진다(built: build.BuildResult,
                                                tmp_path: Path) -> None:
    """캘린더 하한 밖 행을 격자에 섞으면 프레임 EG1(행수)은 통과한다 — (b) 가 잡는 자리다."""
    r = _variant(built, tmp_path, "short_keep_pre_calendar",
                 "SELECT * REPLACE (NULL::VARCHAR AS reject_reason) FROM ({body})")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    eg1b = _gate(r, "EG1_short_daily")
    assert eg1b.status is GateStatus.FAIL
    assert eg1b.metrics["delta_short_kiwoom"] == -N_REJECT
    assert eg1b.metrics["n_measured_short_kiwoom"] == N_SRC["short_kiwoom"]
    assert _gate(r, "EG3_short_daily").detail == "upstream_failed"


def test_격자_티커를_바꾸면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path) -> None:
    """행수가 같아 EG1 은 통과하고 원천이 없는 티커라 (b) 도 통과한다 — 격자 술어만 잡는다."""
    r = _variant(built, tmp_path, "short_swap_ticker",
                 "SELECT * REPLACE (CASE WHEN ticker = '005935' AND reject_reason IS NULL "
                 "THEN 'ZZZZZZ' ELSE ticker END AS ticker) FROM ({body})")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    assert _gate(r, "EG1_short_daily").status is GateStatus.PASS
    eg3 = _gate(r, "EG3_short_daily")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_grid_missing"] == N_ROWS_BY_TICKER["005935"]
    assert eg3.metrics["n_grid_extra"] == N_ROWS_BY_TICKER["005935"]


def test_fill_kind_어휘를_깨면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "short_bad_evidence",
                 "SELECT * REPLACE (struct_pack(kind := fill_kind_short_kiwoom.kind, "
                 "evidence := 'shard') AS fill_kind_short_kiwoom) FROM ({body})")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_short_daily")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_fill_kind_outside_vocab"] == N_GRID
    assert "shard" not in FILL_EVIDENCE and "shard_done" in FILL_EVIDENCE


def test_단위_basis_어휘를_깨면_EG3가_잡는다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "short_bad_basis",
                 "SELECT * REPLACE ('원' AS short_avg_price_kiwoom_basis) FROM ({body})")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_short_daily")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_unit_basis_outside_vocab"] == N_GRID


def test_미수집_셀을_0으로_채우면_기록형_지표가_움직인다(built: build.BuildResult,
                                                tmp_path: Path) -> None:
    """EG9-P04(미수집→0 금지)의 예비 측정 + 골든 픽스처의 이중 방어.

    EG3_short_daily 는 어휘·격자만 폐기하므로 이 변종을 **통과시키고 지표만 올린다**(서버 baseline
    승격 대기). 실제로 막는 것은 골든 픽스처 `fx3_001_src_omitted_value_null` 이다 — 미수집 셀이
    NULL 이어야 한다는 기대를 EG4 가 폐기형으로 들고 있다.
    """
    r = _variant(built, tmp_path, "short_zero_fill",
                 "SELECT * REPLACE (coalesce(short_volume_kiwoom_shr, 0) "
                 "AS short_volume_kiwoom_shr) FROM ({body})")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_short_daily")
    assert eg3.status is GateStatus.PASS                     # 어휘·격자는 멀쩡하다
    assert eg3.metrics["n_zero_without_log_evidence_short_kiwoom"] == sum(
        KIWOOM_NOT_COLLECTED_BY_TICKER.values())
    assert _gate(built, "EG3_short_daily").metrics["n_zero_without_log_evidence_short_kiwoom"] == 0
    eg4 = _gate(r, "EG4")
    assert eg4.status is GateStatus.FAIL and "short_volume_kiwoom_shr" in eg4.detail


# ── 합성 stage (절단본에 없는 축) ─────────────────────────────────────────────

SD = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
      date(2024, 1, 8), date(2024, 1, 9)]
SD0 = date(2023, 12, 29)             # 캘린더 하한 이전
SYN_GRID = 18                        # A00001·B00002·D00005 × 6세션
SYN_REJECT = 2                       # pre_calendar 1 + off_grid 1


def _equity_table(tmp_path: Path, equity_root: Path, make_stage_tree, table: str,
                  rows: list[dict[str, object]], partition_class: str = "whole") -> None:
    """앞서 커밋된 equity 테이블 흉내 — stage 규약 트리를 만들어 equity_root 로 옮긴다."""
    tree = make_stage_tree(tmp_path / f"eq_{table}", table, rows, build_id=f"b_{table}",
                           partition_class=partition_class)
    shutil.move(str(tree.table_root), str(equity_root / table))


def _kw(ticker: str, d: date, qty: int) -> dict[str, object]:
    return {"ticker": ticker, "date": d, "shrts_qty_shr": qty,
            "shrts_trde_prica_krw": qty * 1000, "trde_wght_pct": 1.5,
            "shrts_avg_pric": 1000, "observed_date": date(2024, 2, 1), "observed_n": 1}


def _ks(ticker: str, d: date, qty: int, observed: date) -> dict[str, object]:
    return {"ticker": ticker, "date": d, "ssts_cntg_qty_shr": qty,
            "ssts_tr_pbmn_krw": qty * 900, "ssts_vol_rlim_pct": 2.0,
            "ssts_tr_pbmn_rlim_pct": 2.5, "avrg_prc_krw": 900, "acml_valid": True,
            "observed_date": observed, "observed_n": 1}


def _ln(ticker: str, d: date, bal: int) -> dict[str, object]:
    return {"ticker": ticker, "date": d, "new_stcn_shr": 10, "rdmp_stcn_shr": 5,
            "rmnd_stcn_shr": bal, "rmnd_amt_krw": bal * 100,
            "observed_date": date(2024, 2, 1), "observed_n": 1}


def _synthetic(tmp_path: Path, make_stage_tree) -> build.BuildResult:
    """A00001(키움 샤드 done · KIS 유닛 empty) · B00002(샤드 empty · KIS 유닛 ok, 재수집 2판본) ·
    D00005(두 원천이 같은 4세션을 잰 겹침 구간) + 격자 밖 E00003(ETF)·F00006(delisted).

    절단본에 없는 축만 본다: `empty_response` 두 갈래 · 재수집 판본 접힘 · `off_grid` 격리 ·
    겹침 구간의 상관·비율 분위수 · 다른 API 샤드/다른 dataset 유닛이 증거가 아님.
    """
    root = tmp_path / "equity"
    root.mkdir()
    st = tmp_path / "stg"
    trees = [
        make_stage_tree(st, "stg_short_daily_kiwoom",
                        [_kw("A00001", SD0, 5), _kw("A00001", SD[0], 10),
                         _kw("A00001", SD[1], 20), _kw("E00003", SD[0], 7),
                         *[_kw("D00005", SD[i], 100 * (i + 1)) for i in range(4)]],
                        partition_class="date_axis"),
        make_stage_tree(st, "stg_short_daily_kis",
                        [_ks("B00002", SD[0], 11, date(2024, 2, 1)),
                         _ks("B00002", SD[0], 99, date(2024, 3, 1)),      # 재수집 판본(최신)
                         _ks("B00002", SD[1], 22, date(2024, 2, 1)),
                         *[_ks("D00005", SD[i], 110 * (i + 1), date(2024, 2, 1))
                           for i in range(4)]],
                        partition_class="date_axis"),
        make_stage_tree(st, "stg_loan_daily_kis", [_ln("B00002", SD[0], 1000)],
                        partition_class="date_axis"),
        make_stage_tree(st, "stg_shards_kiwoom", [
            {"src_api": "ka10014", "ticker": "A00001", "req_start": SD[0], "req_end": SD[5],
             "status": "done"},
            {"src_api": "ka10014", "ticker": "B00002", "req_start": SD[0], "req_end": SD[5],
             "status": "empty"},
            {"src_api": "ka10014", "ticker": "D00005", "req_start": SD[0], "req_end": SD[5],
             "status": "done"},
            # 다른 API 의 샤드는 공매도 증거가 아니다 (src_api 필터)
            {"src_api": "ka10008", "ticker": "B00002", "req_start": SD[0], "req_end": SD[5],
             "status": "done"}]),
        make_stage_tree(st, "stg_units_kis", [
            {"dataset": "short", "ticker": "A00001", "status": "empty", "window_from": SD[0],
             "window_to": SD[5]},
            {"dataset": "short", "ticker": "B00002", "status": "ok", "window_from": SD[0],
             "window_to": SD[1]},
            {"dataset": "short", "ticker": "D00005", "status": "ok", "window_from": SD[0],
             "window_to": SD[3]},
            {"dataset": "loan", "ticker": "B00002", "status": "ok", "window_from": SD[0],
             "window_to": SD[1]},
            # 다른 dataset 의 유닛은 증거가 아니다 (dataset 필터)
            {"dataset": "credit", "ticker": "A00001", "status": "ok", "window_from": SD[0],
             "window_to": SD[5]}]),
    ]
    stage_root = trees[0].stage_root
    _equity_table(tmp_path, root, make_stage_tree, "trading_calendar", [{"date": d} for d in SD])
    universe: list[dict[str, object]] = []
    for i, d in enumerate(SD):
        universe += [
            {"date": d, "ticker": "A00001", "status": "listed", "sec_type": "common",
             "no_trade_run": None if i == 5 else 0},          # 가격 축 없는 격자일 1
            {"date": d, "ticker": "B00002", "status": "listed", "sec_type": "common",
             "no_trade_run": 0},
            {"date": d, "ticker": "D00005",
             "status": "suspended" if i == 5 else "listed", "sec_type": "common",
             "no_trade_run": 1 if i == 5 else 0},
            {"date": d, "ticker": "E00003", "status": "listed", "sec_type": "etf",
             "no_trade_run": 0},
            {"date": d, "ticker": "F00006", "status": "delisted", "sec_type": "common",
             "no_trade_run": 0}]
    _equity_table(tmp_path, root, make_stage_tree, "universe_daily", universe,
                  partition_class="date_axis")
    fx = tmp_path / "fx_short.json"
    fx.write_text(json.dumps([
        {"case": "syn_dedup_pit", "key": {"ticker": "B00002", "date": str(SD[0])},
         "column": "short_volume_kis_shr", "expect": "11",
         "source": "hand — 재수집 2판본 중 min(observed_date) 판본(2024-02-01)"},
        {"case": "syn_shard_empty", "key": {"ticker": "B00002", "date": str(SD[2])},
         "column": "fill_kind_short_kiwoom",
         "expect": "{'kind': empty_response, 'evidence': shard_empty}", "source": "hand"},
    ], ensure_ascii=False), encoding="utf-8")
    bl = Baseline({SHORT.name: {"thresholds": {"EG7": 0.5}}})   # 합성 격자가 작아 격리 비율이 크다
    return build.build_table(SHORT, stage_root, root, bl, build_id="b_syn_s09",
                             fixtures_path=fx)


@pytest.fixture
def syn(tmp_path: Path, make_stage_tree) -> build.BuildResult:
    """`make_stage_tree` 가 function 스코프라 합성 빌드도 테스트마다 새로 돈다(격자 18행, 0.2s)."""
    return _synthetic(tmp_path, make_stage_tree)


def test_합성_빌드가_전_게이트를_통과한다(syn: build.BuildResult) -> None:
    assert syn.ok, _fails(syn)
    assert (syn.n_rows, syn.n_reject) == (SYN_GRID, SYN_REJECT)


def test_ETF와_폐지상태는_격자_밖이다(syn: build.BuildResult) -> None:
    assert syn.out_dir is not None
    assert dict(_query(syn.out_dir, "SELECT ticker, count(*) FROM o GROUP BY 1")) == {
        "A00001": 6, "B00002": 6, "D00005": 6}
    # suspended 는 격자 안이다 — D00005 의 마지막 세션이 그 증거
    assert _query(syn.out_dir, f"SELECT count(*) FROM o WHERE ticker = 'D00005' "
                               f"AND date = DATE '{SD[5]}'") == [(1,)]


def test_off_grid와_pre_calendar가_사유별로_갈린다(syn: build.BuildResult) -> None:
    assert syn.out_dir is not None
    assert dict(_query(syn.out_dir, "SELECT reject_reason, count(*) FROM rej GROUP BY 1")) == {
        "pre_calendar": 1, "off_grid": 1}
    assert _query(syn.out_dir, "SELECT ticker, date FROM rej "
                               "WHERE reject_reason = 'off_grid'") == [("E00003", SD[0])]
    assert _query(syn.out_dir, "SELECT ticker, date FROM rej "
                               "WHERE reject_reason = 'pre_calendar'") == [("A00001", SD0)]


def test_샤드_empty와_유닛_empty는_empty_response다(syn: build.BuildResult) -> None:
    """절단본엔 사례가 없는 갈래. `not_collected`(로그 없음)와 구분되어야 한다."""
    m = _gate(syn, "EG3_short_daily").metrics
    assert m["fill_kind_short_kiwoom"] == {
        "empty_response:shard_empty": 6, "measured:shard_done": 6, "src_omitted:shard_done": 6}
    assert m["fill_kind_short_kis"] == {
        "empty_response:unit_empty": 6, "measured:unit_ok": 6, "not_collected:none": 6}
    assert m["fill_kind_loan_kis"] == {
        "measured:unit_ok": 1, "src_omitted:unit_ok": 1, "not_collected:none": 16}
    assert set(FILL_KINDS) >= {"measured", "src_omitted", "empty_response", "not_collected"}


def test_다른_API_샤드와_다른_dataset_유닛은_증거가_아니다(syn: build.BuildResult) -> None:
    """B00002 의 ka10008 샤드(done)와 A00001 의 credit 유닛(ok)이 공매도 셀을 덮으면 안 된다."""
    assert syn.out_dir is not None
    assert _query(syn.out_dir, "SELECT DISTINCT fill_kind_short_kiwoom.evidence FROM o "
                               "WHERE ticker = 'B00002'") == [("shard_empty",)]
    assert _query(syn.out_dir, "SELECT DISTINCT fill_kind_loan_kis.kind FROM o "
                               "WHERE ticker = 'A00001'") == [("not_collected",)]
    assert _gate(syn, "EG3_short_daily").metrics["n_kiwoom_shard_rows"] == 3   # ka10014 만


def test_재수집_판본은_PIT로_접힌다(syn: build.BuildResult) -> None:
    """STAGE_HANDOFF §2 「PIT = 같은 키의 min(observed_date)」. 최신 판본 선택은 DESIGN §1 금지."""
    assert syn.out_dir is not None
    m = _gate(syn, "EG1_short_daily").metrics
    assert (m["n_src_short_kis"], m["n_dedup_short_kis"], m["delta_short_kis"]) == (7, 1, 0)
    assert _query(syn.out_dir, f"SELECT short_volume_kis_shr FROM o "
                               f"WHERE ticker = 'B00002' AND date = DATE '{SD[0]}'") == [(11,)]


def test_겹침_구간의_상관과_비율_분위수를_기록한다(syn: build.BuildResult) -> None:
    """D00005 4세션에서 KIS/키움 = 1.1 로 일정 — 상관 1.0, 전 분위수 1.1."""
    m = _gate(syn, "EG3_short_daily").metrics
    assert (m["n_overlap_src_measured"], m["n_overlap_ratio_rows"]) == (4, 4)
    assert m["corr_short_volume_kis_kiwoom"] == pytest.approx(1.0)
    assert m["corr_short_value_kis_kiwoom"] == pytest.approx(1.0)
    q = m["short_volume_ratio_quantiles"]
    assert set(q) == {str(p) for p in rules_s09.RATIO_QUANTILES}    # type: ignore[arg-type]
    assert all(v == pytest.approx(1.1) for v in q.values())         # type: ignore[union-attr]


def test_가격_축이_없는_격자일을_기록한다(syn: build.BuildResult) -> None:
    """DESIGN §4-3 「KRX 가격 행 존재일」 조건 — 절단본에선 0, 합성에선 1(A00001 마지막 세션)."""
    assert _gate(syn, "EG3_short_daily").metrics["n_grid_without_price_axis"] == 1


# ── F05 재정의: 공매도 거래비중 (2026-09-07) ──────────────────────────────────


def test_공매도_거래량_필드가_선언된다(built: build.BuildResult) -> None:
    """F05 는 `short.short_balance_ratio` 라는 **존재하지 않는 필드**를 요구하고 있었다.

    그 이름을 만들지 않기로 한 것은 결정이다 — 분모(상장주식수)가 다른 표에 있어 셀 하나로
    굽지 않는다(`rules_s09` 주석). 그래서 팩터 쪽을 고친다: 요구 재료를
    `short.short_sale_volume` + `price.shares_outstanding` 로 바꾸고 나눗셈은 팩터층이 한다.

    이름도 정정한다 — 이 값은 공매도 **잔고**가 아니라 **거래량**이다. 진짜 잔고는 취득 불가로
    확정됐다(FACTORS §12 F45).
    """
    assert built.ok
    decl = {f.field_id: f for f in rules_s09.FIELDS}
    assert "short.short_sale_volume" in decl
    f = decl["short.short_sale_volume"]
    assert f.columns == ("short_volume_kiwoom_shr",)
    assert f.unit == "주" and f.value_type == "count"
    assert f.recommended_lag_sessions == 1 and not f.requires_confirmation
