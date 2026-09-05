"""S03·S03B `universe_daily` v2 — 절단본 위 실빌드 왕복 · 합성 stage 위 상태 규칙 · 부정 픽스처
(DESIGN §4-1 · GATES §3 ⑦ · §1 EG3-P09 · §4 FX-1-006·011·012·013~016 · §5-B8).

절단본 실측(손계산, 빌드 SQL 과 독립): 격자 41,066 = Σ security_span.n_days · 정지 126 거래일
(8 구간) · KOSDAQ 관리·투자주의환기 74 거래일 · 정리매매 18 거래일 · 신호 22/12/0/3/21.
S03B: 무거래 run {1×7, 3×3, 10, 12, 22, 49, 55, 66, 116}, k=5 에서 run 으로만 suspended 201행
(000030 18 · 101970 1 · 900050 180 · 900060 2) → suspended 327 · adv20 NULL 323 = 17구간 × 19 ·
가격 결측 0.
절단본에 없는 축(KOSPI 관리종목 창 · 해제 공시로 닫히는 정지 · 비거래일 접수 · master 측정값
우선 · 열린 정지 · 가격 행 결측)은 `make_stage_tree` 합성 stage 위에서 검사한다. 이 슬라이스는
equity 산출(`security_span`·`trading_calendar`·`security`·`price_daily`)을 입력으로 읽으므로
상류 4테이블을 같은 equity_root 에 먼저 빌드한다.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
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
            rules_s04.PRICE_DAILY)
UNIVERSE = rules_s03.UNIVERSE_DAILY
BACKFILL_END = date(2026, 8, 20)
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_universe", "EG4", "EG5a"]

N_GRID = 41066                       # = 절단본 Σ n_days (test_equity_s02_span.N_EXIST_PAIRS)
N_SPANS = 17                         # 15 티커 + 재상장 2
# 티커별 격자 행수 = 구간 n_days 합 (원자료 stg_listing_daily ∪ stg_etf_price_daily 존재일 수)
N_ROWS_BY_TICKER = {
    "000030": 1037, "0001A0": 135, "000660": 4094, "003540": 4094, "003545": 4094,
    "003547": 4094, "005930": 4094, "005935": 4094, "036220": 2163, "069500": 4094,
    "101970": 989, "161890": 3396, "247540": 1834, "900050": 1916, "900060": 938}
# halt_state 연속 구간 (ticker, 시작, 끝, 거래일 수) — 공시·거래량 원자료에서 손계산
HALT_RUNS = {
    ("036220", date(2016, 1, 28), date(2016, 4, 25), 58),   # 01-28 지정·02-01 재지정 → 04-26 거래
    ("101970", date(2014, 6, 11), date(2014, 8, 19), 49),   # 08-20 해제 공시
    ("101970", date(2015, 3, 2), date(2015, 3, 5), 4),      # 03-04 정리매매 개시 ≠ 해제, 03-06 거래
    ("247540", date(2020, 2, 3), date(2020, 2, 3), 1),      # 장중 정지, 다음 날 거래
    ("247540", date(2022, 4, 6), date(2022, 4, 6), 1),
    ("900050", date(2015, 4, 24), date(2015, 4, 24), 1),    # '…매매거래정지기간 등 변경 안내' 매칭
    ("900060", date(2013, 6, 20), date(2013, 6, 20), 1),
    ("900060", date(2013, 9, 6), date(2013, 9, 25), 11),    # 09-26 해제 공시
}
N_HALT = 126
# 무거래(volume_shr = 0) 연속 구간 — stg_price_daily 에서 손계산. no_trade_run > 0 인 run 과 같다
ZERO_RUNS = {
    ("000030", date(2019, 1, 9), date(2019, 2, 12), 22),    # 폐지 전, 공시 신호 없음
    ("005930", date(2018, 4, 30), date(2018, 5, 3), 3),     # 50:1 분할 정지, 공시 신호 없음
    ("005935", date(2018, 4, 30), date(2018, 5, 3), 3),
    ("036220", date(2016, 1, 29), date(2016, 1, 29), 1),    # 01-28 지정 다음날 무거래, 02-01 거래
    ("036220", date(2016, 2, 2), date(2016, 4, 25), 55),
    ("101970", date(2014, 6, 12), date(2014, 8, 20), 49),   # 해제일 08-20 도 무거래
    ("101970", date(2015, 3, 3), date(2015, 3, 5), 3),
    ("900050", date(2010, 11, 26), date(2010, 11, 26), 1),
    ("900050", date(2012, 2, 24), date(2012, 2, 24), 1),
    ("900050", date(2012, 4, 19), date(2012, 4, 19), 1),
    ("900050", date(2014, 11, 20), date(2014, 11, 20), 1),
    ("900050", date(2014, 12, 4), date(2014, 12, 4), 1),
    ("900050", date(2015, 4, 28), date(2015, 5, 13), 10),
    ("900050", date(2015, 11, 12), date(2015, 11, 12), 1),
    ("900050", date(2016, 4, 25), date(2016, 7, 28), 66),
    ("900050", date(2017, 3, 30), date(2017, 9, 15), 116),
    ("900060", date(2013, 9, 9), date(2013, 9, 27), 12),    # halt 09-06~09-25, 해제 뒤 이틀 무거래
}
N_TRADE_DAYS = 40720                 # volume_shr > 0 행 (= 41,066 − 346 reference)
NO_TRADE_RUN_K = 5                   # baseline_seed_s03 제안값 (사람 승인 전)
# run ≥ k 이면서 halt_state 가 아닌 행 — k=5 손계산 (000030 5~22일 18 · 101970 08-20 1 ·
# 900050 10일 run 6 + 66일 run 62 + 116일 run 112 · 900060 09-26·27 2)
BY_RUN_ROWS = {"000030": 18, "101970": 1, "900050": 180, "900060": 2}
N_BY_RUN = 201
ADMIN_RUNS = {
    ("036220", "measured", date(2016, 1, 29), date(2016, 5, 4), 64),   # 관리 34 + 투자주의환기 30
    ("101970", "measured", date(2015, 3, 3), date(2015, 3, 16), 10),
}
LIQUIDATION_RUNS = {
    ("036220", date(2016, 4, 22), date(2016, 5, 4), 9),
    ("101970", date(2015, 3, 4), date(2015, 3, 16), 9),
}
# signal_halt 22 = 지정 12 + 지정·해제 동일일 10(양쪽 신호) · release 12 = 동일일 10 + 순수 해제 2
SIGNAL_COUNTS = {"signal_halt": 22, "signal_halt_release": 12, "signal_admin": 0,
                 "signal_liquidation": 3, "signal_delist": 21}


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW u AS SELECT * FROM "
                    f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=false)")
        for t in ("stg_price_daily", "stg_etf_price_daily", "stg_listing_daily"):
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
    """변종 이름으로 같은 상수를 재등재 — `_const` 키가 `{table}.{metric}` 이라 없으면 KeyError."""
    return Baseline({**SEED.data, name: SEED.table(UNIVERSE.name)})


def _runs(out_dir: Path, flag: str, extra: str = "") -> set[tuple[object, ...]]:
    """`flag` 가 true 인 연속 거래일 구간 (ticker[, extra], 시작, 끝, 일수)."""
    cols = f"ticker{', ' + extra if extra else ''}"
    return set(_query(out_dir, f"""
        SELECT {cols}, min(date), max(date), count(*) FROM (
          SELECT ticker, date, {flag} AS f{', ' + extra if extra else ''},
                 row_number() OVER (PARTITION BY ticker ORDER BY date)
                 - row_number() OVER (PARTITION BY ticker, {flag} ORDER BY date) AS grp
          FROM u) WHERE f GROUP BY {cols}, grp"""))


def _variant(built: build.BuildResult, tmp_path: Path, name: str, body_wrap: str,
             **overrides: object) -> build.BuildResult:
    """산출 SQL 만 바꾼 변종을 같은 equity_root 에 빌드한다(정본 MANIFEST 를 건드리지 않는다)."""
    assert built.out_dir is not None
    body = UNIVERSE.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    p = tmp_path / f"{name}.sql"
    p.write_text(body_wrap.format(body=body), encoding="utf-8")
    rule = EquityTable(**{**UNIVERSE.__dict__, "name": name, "sql_path": p, **overrides})
    return build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], _seed_as(name),
                             build_id=f"b_{name}")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s03") / "equity"
    for rule in UPSTREAM:
        r = build.build_table(rule, STAGE_SLICE, root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, _fails(r)
    return build.build_table(UNIVERSE, STAGE_SLICE, root, SEED, build_id="b_s03_ud")


# ── 절단본 왕복 ───────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, _fails(built)
    assert [g.name for g in built.gates] == GATE_ORDER
    assert {g.name: g.status.value for g in built.gates if g.status is not GateStatus.PASS} == {
        "EG5a": "skip"}
    assert built.n_rows == N_GRID and built.n_reject == 0
    # equity 입력 4개가 stage 입력과 같은 규약으로 고정된다 (inputs.source_root)
    assert built.inputs["security_span"] == "b_security_span"
    assert built.inputs["trading_calendar"] == "b_trading_calendar"
    assert built.inputs["security"] == "b_security"
    assert built.inputs["price_daily"] == "b_price_daily"
    assert set(built.inputs) == set(UNIVERSE.inputs)
    assert not {"stg_price_daily", "stg_etf_price_daily"} & set(built.inputs)


def test_EG1_우변은_span_n_days_합이다(built: build.BuildResult) -> None:
    eg1 = _gate(built, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["delta"]) == (N_GRID, N_GRID, 0)
    assert "sum(n_days)" in str(eg1["rhs_sql"]) and "security_span" in str(eg1["rhs_sql"])


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    """S03 컬럼 → S03B 4개 → available 2개 (DESIGN §4-1 순서)."""
    assert built.out_dir is not None
    cols = [str(r[0]) for r in _query(built.out_dir, "DESCRIBE u")]
    assert cols == list(UNIVERSE.columns)
    assert cols[-6:] == ["mktcap_krw", "adv20_krw", "listing_age_days", "no_trade_run",
                         "available_date", "available_basis"]


def test_격자는_구간과_일치하고_backfill_end_뒤_행이_없다(built: build.BuildResult) -> None:
    """coverage_gap 행 0 (GAP-21). 티커별 행수 = 구간 n_days 합."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT min(date), max(date) FROM u") == [
        (date(2010, 1, 4), BACKFILL_END)]
    got = dict(_query(built.out_dir, "SELECT ticker, count(*) FROM u GROUP BY 1"))
    assert got == N_ROWS_BY_TICKER
    assert sum(N_ROWS_BY_TICKER.values()) == N_GRID
    # 재상장 공백(036220 2016-05-05 ~ 2024-03-12)에는 행이 없다
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE ticker = '036220' "
                                 "AND date BETWEEN '2016-05-05' AND '2024-03-12'") == [(0,)]


def test_halt_state_구간_손계산(built: build.BuildResult) -> None:
    """[지정, min(해제, 다음 volume>0)) 합집합. 정리매매 개시 공시는 해제가 아니다. S03 회귀."""
    assert built.out_dir is not None
    assert _runs(built.out_dir, "halt_state") == HALT_RUNS
    assert _gate(built, "EG3_universe").metrics["n_halt_days"] == N_HALT
    assert sum(n for *_, n in HALT_RUNS) == N_HALT


def test_지정_해제_동일일은_그날_정지가_아니다(built: build.BuildResult) -> None:
    """FX-1-014 — '매매거래정지및정지해제' 는 양쪽 신호 true, halt_state false."""
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, date, halt_state FROM u "
                                 "WHERE signal_halt AND signal_halt_release ORDER BY 1, 2")
    assert len(rows) == 10 and all(not h for *_, h in rows)      # 절단본의 동일일 10건 전부
    assert ("900050", date(2010, 11, 25), False) in rows


def test_status는_halt_또는_무거래_run으로_suspended(built: build.BuildResult) -> None:
    """S03B 규칙: suspended ⇔ halt_state ∨ (reference ∧ run ≥ k). run 으로만 잡히는 201행."""
    assert built.out_dir is not None
    m = _gate(built, "EG3_universe").metrics
    assert m["no_trade_run_k"] == NO_TRADE_RUN_K
    assert m["status_counts"] == {"listed": N_GRID - N_HALT - N_BY_RUN,
                                  "suspended": N_HALT + N_BY_RUN}
    assert m["n_suspended_by_run"] == N_BY_RUN and m["n_status_halt_mismatch"] == 0
    got = dict(_query(built.out_dir, "SELECT ticker, count(*) FROM u "
                                     "WHERE status = 'suspended' AND NOT halt_state GROUP BY 1"))
    assert got == BY_RUN_ROWS
    # halt_state 인 행은 run 과 무관하게 suspended (S03 회귀)
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE halt_state "
                                 "AND status <> 'suspended'") == [(0,)]
    # 000030: run 1~4 (01-09~01-14) 는 listed, run 5 (01-15) 부터 suspended
    assert _query(built.out_dir, "SELECT date, status, no_trade_run FROM u WHERE ticker = '000030' "
                                 "AND date BETWEEN '2019-01-14' AND '2019-01-15' ORDER BY 1") == [
        (date(2019, 1, 14), "listed", 4), (date(2019, 1, 15), "suspended", 5)]


def test_no_trade_run_손계산(built: build.BuildResult) -> None:
    """FX-1-012. run > 0 인 연속 구간 = stage volume_shr=0 연속 구간(구간 안, D 포함).
    거래일은 0."""
    assert built.out_dir is not None
    assert _runs(built.out_dir, "no_trade_run > 0") == ZERO_RUNS
    assert _query(built.out_dir, "SELECT count(*) FILTER (WHERE no_trade_run = 0), "
                                 "count(*) FILTER (WHERE no_trade_run IS NULL), max(no_trade_run) "
                                 "FROM u") == [(N_TRADE_DAYS, 0, 116)]
    # run 값은 구간 안 연속 무거래일의 서수 — 000030 22일 run 의 마지막 날 22, 900050 116
    assert _query(built.out_dir, "SELECT no_trade_run FROM u WHERE (ticker, date) IN "
                                 "(('000030', DATE '2019-02-12'), ('900050', DATE '2017-09-15'), "
                                 "('101970', DATE '2014-08-20'), ('101970', DATE '2014-08-21')) "
                                 "ORDER BY ticker, date") == [(22,), (49,), (0,), (116,)]
    m = _gate(built, "EG3_universe").metrics
    assert m["no_trade_run_max"] == 116 and m["n_no_trade_run_null"] == 0
    # run 값별 행수(손계산): 1:17 · 2:10 · 3:10 · 4:7 · 5~10:7 · 11~12:6 · 13~22:5 · 23~49:4 ·
    # 50~55:3 · 56~66:2 · 67~116:1
    assert m["no_trade_run_hist"] == {"1-1": 17, "2-4": 27, "5-9": 35, "10-19": 54, "20+": 213}
    assert sum(m["no_trade_run_hist"].values()) == N_GRID - N_TRADE_DAYS


def test_mktcap은_같은날_price_daily_시총_그대로(built: build.BuildResult) -> None:
    """원주가 × KRX 주식수, 조정 없음. 절단본은 가격 결측 0 이라 NULL 0. stage MKTCAP 과 전건
    일치."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE mktcap_krw IS NULL") == [(0,)]
    assert _query(built.out_dir, "SELECT count(*) FROM u LEFT JOIN ("
                                 "SELECT ticker, date, mktcap_krw FROM stg_price_daily UNION ALL "
                                 "SELECT ticker, date, mktcap_krw FROM stg_etf_price_daily) s "
                                 "USING (ticker, date) "
                                 "WHERE u.mktcap_krw IS DISTINCT FROM s.mktcap_krw") == [(0,)]
    # FX-2-002 분할 전후 — 시총은 연속(주식수 50배 · 가격 1/50)
    assert _query(built.out_dir, "SELECT date, mktcap_krw FROM u WHERE ticker = '005930' "
                                 "AND date IN (DATE '2018-05-03', DATE '2018-05-04') ORDER BY 1"
                  ) == [
        (date(2018, 5, 3), 340224209100000), (date(2018, 5, 4), 333162951930000)]
    m = _gate(built, "EG3_universe").metrics
    assert m["n_mktcap_null"] == 0 and m["n_mktcap_price_mismatch"] == 0


def test_adv20은_구간_안_20거래일_평균이고_창_미달은_NULL(built: build.BuildResult) -> None:
    """[D−19, D] 20행 전부 있어야 값. 구간마다 첫 19행 NULL → 17 × 19 = 323. 재상장 구간은
    새로 센다."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE adv20_krw IS NULL") == [
        (N_SPANS * 19,)]
    # 005930 손계산: 2010-01-04~01-29 20일 합 6,215,851,518,366 / 20 · 01-28 은 19행이라 NULL
    assert _query(built.out_dir, "SELECT date, adv20_krw FROM u WHERE ticker = '005930' "
                                 "AND date BETWEEN '2010-01-28' AND '2010-02-01' ORDER BY 1") == [
        (date(2010, 1, 28), None), (date(2010, 1, 29), 310792575918.3),
        (date(2010, 2, 1), 320557341731.9)]
    # 036220 둘째 구간(2024-03-13~) — 2016 구간 값이 새지 않는다: 첫 19일 NULL, 20일째 04-09
    assert _query(built.out_dir, "SELECT count(*) FILTER (WHERE adv20_krw IS NULL), min(date) "
                                 "FILTER (WHERE adv20_krw IS NOT NULL) FROM u "
                                 "WHERE ticker = '036220' AND date >= '2024-03-13'") == [
        (19, date(2024, 4, 9))]
    assert _query(built.out_dir, "SELECT adv20_krw FROM u WHERE ticker = '036220' "
                                 "AND date = '2024-04-09'") == [(39505356477.0,)]
    # 기준가 날(value 0)도 평균에 들어간다 — 000030 무거래 20일째(2019-02-08) 창 전부 0 → 0
    assert _query(built.out_dir, "SELECT adv20_krw FROM u WHERE ticker = '000030' "
                                 "AND date = '2019-02-08'") == [(0.0,)]
    m = _gate(built, "EG3_universe").metrics
    assert m["n_adv20_null"] == N_SPANS * 19 and m["n_adv20_null_mismatch"] == 0
    assert len(m["adv20_common_quantiles"]) == 5


def test_listing_age는_같은날_listing_상장일_기준_역일(built: build.BuildResult) -> None:
    """재상장 2종은 구간마다 list_date 가 다르다 — security.list_date(최신)를 쓰면 첫 구간이
    음수."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FILTER (WHERE listing_age_days < 0), "
                                 "count(*) FILTER (WHERE listing_age_days IS NULL) FROM u") == [
        (0, 0)]
    assert _query(built.out_dir, "SELECT ticker, date, listing_age_days FROM u "
                                 "WHERE (ticker, date) IN "
                                 "(('005930', DATE '2010-01-04'), ('036220', DATE '2016-05-04'), "
                                 "('036220', DATE '2024-03-13'), ('101970', DATE '2015-03-16'), "
                                 "('0001A0', DATE '2026-01-30')) ORDER BY 1, 2") == [
        ("0001A0", date(2026, 1, 30), 0), ("005930", date(2010, 1, 4), 12626),
        ("036220", date(2016, 5, 4), 3256), ("036220", date(2024, 3, 13), 0),
        ("101970", date(2015, 3, 16), 963)]
    # 주식은 같은 날 listing.list_date 와 전건 일치
    assert _query(built.out_dir, "SELECT count(*) FROM u "
                                 "JOIN stg_listing_daily l USING (ticker, date) "
                                 "WHERE listing_age_days <> date_diff('day', l.list_date, u.date)"
                  ) == [(0,)]
    # ETF 는 listing 에 없어 구간 first_date(2010-01-04) 기준 — 하한
    assert _query(built.out_dir, "SELECT min(listing_age_days), max(listing_age_days) FROM u "
                                 "WHERE ticker = '069500'") == [(0, 6072)]
    m = _gate(built, "EG3_universe").metrics
    assert m["n_listing_age_fallback"] == 4094 and m["n_listing_age_fallback_stock"] == 0


def test_admin_state_KOSDAQ_소속부는_measured(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _runs(built.out_dir, "admin_state", "admin_state_basis") == ADMIN_RUNS
    assert _gate(built, "EG3_universe").metrics["n_admin_days_by_basis"] == {"measured": 74}


def test_admin_state_basis는_측정축_유무로_갈린다(built: build.BuildResult) -> None:
    """KOSPI 전부 + KOSDAQ 소속부 공란(~2011-04-29) → derived_kospi_window · ETF → convention."""
    assert built.out_dir is not None
    got = {(t, b): n for t, b, n in _query(
        built.out_dir, "SELECT ticker, admin_state_basis, count(*) FROM u GROUP BY 1, 2")}
    assert got[("005930", "derived_kospi_window")] == 4094
    assert got[("036220", "derived_kospi_window")] == 332          # 2010-01-04 ~ 2011-04-29
    assert got[("036220", "measured")] == 2163 - 332
    assert got[("069500", "convention")] == 4094
    assert got[("0001A0", "measured")] == 135
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE admin_state IS NULL") == [(0,)]
    # 절단본에는 관리종목 공시가 없어 창 규칙으로 true 가 된 행은 없다
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE admin_state AND "
                                 "admin_state_basis = 'derived_kospi_window'") == [(0,)]


def test_liquidation_window는_개시_공시부터_구간_끝까지(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _runs(built.out_dir, "liquidation_window") == LIQUIDATION_RUNS
    # 재상장 둘째 구간으로 새지 않는다
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE ticker = '036220' "
                                 "AND date >= '2024-03-13' AND liquidation_window") == [(0,)]


def test_신호_건수와_접두어_제거_매칭(built: build.BuildResult) -> None:
    """`[기재정정]주권매매거래정지해제(…정리매매 개시)` 2016-04-29 가 접두어 제거 뒤 매칭된다."""
    assert built.out_dir is not None
    assert _gate(built, "EG3_universe").metrics["signal_counts"] == SIGNAL_COUNTS
    assert _query(built.out_dir, "SELECT signal_liquidation, signal_halt_release FROM u "
                                 "WHERE ticker = '036220' AND date = '2016-04-29'") == [
        (True, False)]


def test_ETF_행이_포함되고_market은_결측(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*), count(market), bool_and(sec_type = 'etf'), "
                                 "bool_and(NOT admin_state), "
                                 "bool_and(admin_state_basis = 'convention') "
                                 "FROM u WHERE ticker = '069500'") == [(4094, 0, True, True, True)]
    assert _gate(built, "EG3_universe").metrics["n_market_null"] == 4094


def test_available은_date_default_이고_admin_flag는_전부_NULL(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FILTER (WHERE available_date <> date "
                                 "OR available_basis <> 'default'), count(admin_flag) FROM u") == [
        (0, 0)]
    assert _gate(built, "EG2").status is GateStatus.PASS


# ── 합성 stage — 절단본에 없는 규칙 축 ────────────────────────────────────────
# 캘린더 10거래일 2020-01-06(월) ~ 01-17(금). td1..td10.
TD = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 1, 8), date(2020, 1, 9), date(2020, 1, 10),
      date(2020, 1, 13), date(2020, 1, 14), date(2020, 1, 15), date(2020, 1, 16),
      date(2020, 1, 17)]
ADMIN_SECT = "관리종목(소속부없음)"
LIST_DATE = date(2019, 12, 2)        # 합성 상장일 — td1 까지 역일 35
SYN_K = 3                            # 합성 baseline 의 no_trade_run_k


def _equity_table(tmp_path: Path, equity_root: Path, make_stage_tree, table: str,
                  rows: list[dict[str, object]]) -> None:
    """앞서 커밋된 equity 테이블 흉내 — stage 규약 트리를 만들어 equity_root 로 옮긴다."""
    tree = make_stage_tree(tmp_path / f"eq_{table}", table, rows, build_id=f"b_{table}")
    shutil.move(str(tree.table_root), str(equity_root / table))


def _price(ticker: str, d: date, vol: int | None) -> dict[str, object]:
    """가짜 price_daily 1행 — S04 규칙대로 price_kind = volume 부호, value = 0 이면 0."""
    return {"ticker": ticker, "date": d, "volume_shr": vol,
            "value_krw": None if vol is None else vol * 10, "mktcap_krw": 1000,
            "price_kind": None if vol is None else ("trade" if vol > 0 else "reference")}


def _synthetic(tmp_path: Path, make_stage_tree, *, d_end_reason: str) -> build.BuildResult:
    """K00010(KOSPI) · Q00020(KOSDAQ, td1~td9 폐지) · E00030(ETF) · D00040(KOSPI, 열린 정지).

    D00040 은 td9 지정 뒤 해제도 거래도 없이 구간이 끝난다 — `d_end_reason` 이 coverage_gap 이면
    정상(현재 정지 중), data_gap 이면 EG3-P09 폐기 대상.
    S03B 축: K00010 td5 는 가격 행 없음(run NULL·끊김) · Q00020 td8·td9 는 해제 뒤 무거래
    (run 3·4 ≥ k) · E00030 은 listing 이 없어 listing_age 가 구간 first_date 기준.
    """
    root = tmp_path / "equity"
    root.mkdir()
    st = tmp_path / "stg"
    trees = [
        make_stage_tree(st, "stg_listing_daily", [
            *({"ticker": "K00010", "date": d, "market": "KOSPI", "sect_tp": "",
               "sect_available": False, "list_date": LIST_DATE} for d in TD),
            *({"ticker": "Q00020", "date": d, "market": "KOSDAQ",
               "sect_tp": ADMIN_SECT if d in TD[3:5] else "벤처기업부",
               "sect_available": True, "list_date": LIST_DATE} for d in TD[:9]),
            *({"ticker": "D00040", "date": d, "market": "KOSPI", "sect_tp": "",
               "sect_available": False, "list_date": LIST_DATE} for d in TD)],
            partition_class="date_axis"),
        make_stage_tree(st, "stg_master_daily", [
            {"ticker": "K00010", "date": TD[9], "is_admin_issue": True, "is_trade_halt": False,
             "is_liquidation": False}]),
        make_stage_tree(st, "stg_disclosure", [
            # 토요일 접수 → td1 에 얹힘. 접두어 2개 제거 뒤 매칭
            {"rcept_no": "20200104000001", "rcept_dt": date(2020, 1, 4), "ticker": "K00010",
             "has_ticker": True, "report_nm": "[기재정정][첨부추가]주권매매거래정지(투자자보호)"},
            {"rcept_no": "20200108000001", "rcept_dt": TD[2], "ticker": "K00010",
             "has_ticker": True, "report_nm": "관리종목지정"},
            {"rcept_no": "20200114000001", "rcept_dt": TD[6], "ticker": "K00010",
             "has_ticker": True, "report_nm": "매매거래정지및정지해제(조회공시)"},
            {"rcept_no": "20200113000001", "rcept_dt": TD[5], "ticker": "Q00020",
             "has_ticker": True, "report_nm": "주권매매거래정지(상장폐지)"},
            {"rcept_no": "20200115000001", "rcept_dt": TD[7], "ticker": "Q00020",
             "has_ticker": True, "report_nm": "주권매매거래정지해제(상장폐지)"},
            {"rcept_no": "20200115000002", "rcept_dt": TD[7], "ticker": "Q00020",
             "has_ticker": True, "report_nm": "기타시장안내(정리매매 개시)"},
            {"rcept_no": "20200116000001", "rcept_dt": TD[8], "ticker": "D00040",
             "has_ticker": True, "report_nm": "주권매매거래정지(회생절차개시신청)"},
            # has_ticker=false 는 무시된다 (K00010 을 다시 정지시키지 않는다)
            {"rcept_no": "20200116000002", "rcept_dt": TD[8], "ticker": "K00010",
             "has_ticker": False, "report_nm": "주권매매거래정지(오염)"}]),
    ]
    stage_root = trees[0].stage_root
    _equity_table(tmp_path, root, make_stage_tree, "trading_calendar",
                  [{"date": d} for d in TD])
    _equity_table(tmp_path, root, make_stage_tree, "security_span", [
        {"ticker": "K00010", "span_seq": 1, "first_date": TD[0], "last_date": TD[9],
         "n_days": 10, "end_reason": "coverage_gap"},
        {"ticker": "Q00020", "span_seq": 1, "first_date": TD[0], "last_date": TD[8],
         "n_days": 9, "end_reason": "delisted"},
        {"ticker": "E00030", "span_seq": 1, "first_date": TD[0], "last_date": TD[9],
         "n_days": 10, "end_reason": "coverage_gap"},
        {"ticker": "D00040", "span_seq": 1, "first_date": TD[0], "last_date": TD[9],
         "n_days": 10, "end_reason": d_end_reason}])
    _equity_table(tmp_path, root, make_stage_tree, "security", [
        {"ticker": "K00010", "sec_type": "common"}, {"ticker": "Q00020", "sec_type": "common"},
        {"ticker": "E00030", "sec_type": "etf"}, {"ticker": "D00040", "sec_type": "common"}])
    _equity_table(tmp_path, root, make_stage_tree, "price_daily", [
        *(_price("K00010", d, 0 if d == TD[1] else 100) for d in TD if d != TD[4]),   # td5 결측
        *(_price("Q00020", d, 0 if d in TD[5:9] else 100) for d in TD[:9]),
        *(_price("D00040", d, 0 if d in TD[8:] else 100) for d in TD),
        *(_price("E00030", d, 100) for d in TD)])
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps([{"case": "k_td1", "key": {"date": "2020-01-06", "ticker": "K00010"},
                               "column": "status", "expect": "suspended", "source": "hand"}]),
                  encoding="utf-8")
    bl = Baseline({UNIVERSE.name: {"admin_window_td": 3, "no_trade_run_k": SYN_K,
                                   "adv_window_td": 20}})
    return build.build_table(UNIVERSE, stage_root, root, bl, build_id="b_syn", fixtures_path=fx)


def _cell(out_dir: Path, ticker: str, col: str) -> list[object]:
    return [r[0] for r in _query(out_dir, f"SELECT {col} FROM u WHERE ticker = '{ticker}' "
                                          "ORDER BY date")]


def test_합성_빌드가_통과하고_격자는_39행(tmp_path: Path, make_stage_tree) -> None:
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok, _fails(r)
    assert r.n_rows == 10 + 9 + 10 + 10
    m = _gate(r, "EG3_universe").metrics
    assert m["n_halt_open_at_coverage_end"] == 1 and m["n_halt_open_at_data_gap"] == 0


def test_비거래일_접수는_다음_거래일_지정이고_거래일에_닫힌다(tmp_path: Path,
                                                        make_stage_tree) -> None:
    """토요일 접수 → td1 true(당일 거래는 못 닫는다) · td2 무거래 true · td3 거래 false."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "K00010", "signal_halt") == [True, False, False, False, False, False,
                                                         True, False, False, False]
    assert _cell(r.out_dir, "K00010", "halt_state") == [True, True, False, False, False, False,
                                                        False, False, False, False]


def test_해제_공시가_거래보다_먼저면_해제일에_닫히고_무거래_run이_잇는다(
        tmp_path: Path, make_stage_tree) -> None:
    """Q00020: td6 지정, td6~td9 무거래, td8 해제 → halt true td6·td7, false td8·td9.
    S03B: run td6..td9 = 1,2,3,4 → td8(3 ≥ k=3)·td9 는 run 으로 suspended."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "Q00020", "halt_state") == [False] * 5 + [True, True, False, False]
    assert _cell(r.out_dir, "Q00020", "no_trade_run") == [0] * 5 + [1, 2, 3, 4]
    assert _cell(r.out_dir, "Q00020", "status") == ["listed"] * 5 + ["suspended"] * 4
    assert _cell(r.out_dir, "Q00020", "liquidation_window") == [False] * 7 + [True, True]
    m = _gate(r, "EG3_universe").metrics
    assert m["n_halt_open_at_delist"] == 0 and m["n_suspended_by_run"] == 2
    # D00040: td9 지정·무거래(run 1·2 < k) — halt 로 suspended, run 으로는 아니다
    assert _cell(r.out_dir, "D00040", "status") == ["listed"] * 8 + ["suspended"] * 2
    assert _cell(r.out_dir, "D00040", "no_trade_run") == [0] * 8 + [1, 2]


def test_가격_행이_없는_날은_run_NULL_이고_run을_끊는다(tmp_path: Path, make_stage_tree) -> None:
    """K00010 td5 가격 결측: run NULL·mktcap NULL·status listed. td2 무거래 run 1 은 td3 거래로
    끊긴다. adv20 은 구간 10일이라 전부 NULL. listing_age 는 역일(td1 35 … td10 46), ETF 는
    first_date 기준."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "K00010", "no_trade_run") == [0, 1, 0, 0, None, 0, 0, 0, 0, 0]
    assert _cell(r.out_dir, "K00010", "mktcap_krw") == [1000] * 4 + [None] + [1000] * 5
    assert _cell(r.out_dir, "K00010", "status")[4] == "listed"
    assert set(_cell(r.out_dir, "K00010", "adv20_krw")) == {None}
    assert _cell(r.out_dir, "K00010", "listing_age_days") == [35, 36, 37, 38, 39, 42, 43, 44,
                                                              45, 46]
    assert _cell(r.out_dir, "E00030", "listing_age_days") == [0, 1, 2, 3, 4, 7, 8, 9, 10, 11]
    m = _gate(r, "EG3_universe").metrics
    assert m["n_no_trade_run_null"] == 1 and m["n_mktcap_null"] == 1
    assert m["n_listing_age_fallback"] == 10 and m["n_listing_age_fallback_stock"] == 0
    assert m["n_adv20_null"] == 39 and m["adv20_common_quantiles"] is None


def test_KOSPI_관리종목은_지정_신호_창_derived(tmp_path: Path, make_stage_tree) -> None:
    """창 3거래일: td3 지정 → td3·td4·td5 true, td6 false. 창 밖은 measured 도 아니다."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "K00010", "admin_state") == [False, False, True, True, True, False,
                                                         False, False, False, True]
    basis = _cell(r.out_dir, "K00010", "admin_state_basis")
    assert basis[:9] == ["derived_kospi_window"] * 9 and basis[9] == "measured"


def test_master_행이_있으면_측정값이_우선하고_admin_flag가_채워진다(tmp_path: Path,
                                                             make_stage_tree) -> None:
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "K00010", "admin_flag") == [None] * 9 + [True]
    assert _gate(r, "EG3_universe").metrics["n_admin_flag_not_null"] == 1


def test_KOSDAQ_소속부_measured_와_ETF_convention(tmp_path: Path, make_stage_tree) -> None:
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "Q00020", "admin_state") == [False, False, False, True, True, False,
                                                         False, False, False]
    assert set(_cell(r.out_dir, "Q00020", "admin_state_basis")) == {"measured"}
    assert set(_cell(r.out_dir, "E00030", "admin_state_basis")) == {"convention"}
    assert set(_cell(r.out_dir, "E00030", "market")) == {None}
    assert set(_cell(r.out_dir, "E00030", "sec_type")) == {"etf"}


def test_has_ticker_false_공시는_신호가_아니다(tmp_path: Path, make_stage_tree) -> None:
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "K00010", "halt_state")[8] is False


# ── 부정 픽스처 (게이트가 fail 을 내야 통과) ─────────────────────────────────

def test_정지가_열린_채_data_gap으로_끝나면_EG3_universe가_폐기한다(tmp_path: Path,
                                                               make_stage_tree) -> None:
    """EG3-P09 — 같은 격자, 입력 `security_span.end_reason` 만 data_gap. 다른 게이트는 pass."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="data_gap")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert [g.status.value for g in r.gates[:5]] == ["pass"] * 5      # EG0·EG7·EG1·EG2·EG3
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_halt_open_at_data_gap"] == 1 and "n_halt_open_at_data_gap=1" in eg3.detail
    assert eg3.metrics["n_halt_open_at_coverage_end"] == 0
    assert _gate(r, "EG4").detail == "upstream_failed"


def test_격자_행이_하나_빠지면_EG1이_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    """FX-N-001 — 임의 1행 삭제. Σ n_days 우변은 그대로라 delta = −1."""
    r = _variant(built, tmp_path, "universe_drop_one", "WITH base AS ({body}) SELECT * FROM base "
                 "WHERE NOT (ticker = '005930' AND date = DATE '2018-05-04')")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL and eg1.metrics["delta"] == -1
    assert _gate(r, "EG2").detail == "upstream_failed"


def test_status가_전부_listed면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                    tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "universe_bad_status",
                 "WITH base AS ({body}) SELECT * REPLACE ('listed' AS status) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_status_halt_mismatch"] == N_HALT + N_BY_RUN


def test_run이_k_이상인데_listed면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                       tmp_path: Path) -> None:
    """S03 규칙(halt 만)으로 되돌린 변종 — run 으로 잡혀야 할 201행이 mismatch."""
    r = _variant(built, tmp_path, "universe_s03_status",
                 "WITH base AS ({body}) SELECT * REPLACE (CASE WHEN halt_state THEN 'suspended' "
                 "ELSE 'listed' END AS status) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_status_halt_mismatch"] == N_BY_RUN
    assert eg3.metrics["n_suspended_by_run"] == 0
    assert _gate(r, "EG4").detail == "upstream_failed"


def test_창_미달_adv20을_만들어_내면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                        tmp_path: Path) -> None:
    """NULL 을 0 으로 채운 변종 — 창 미달 323행이 n_adv20_null_mismatch."""
    r = _variant(built, tmp_path, "universe_adv20_filled",
                 "WITH base AS ({body}) SELECT * REPLACE (coalesce(adv20_krw, 0) AS adv20_krw) "
                 "FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_adv20_null_mismatch"] == N_SPANS * 19
    assert eg3.metrics["n_status_halt_mismatch"] == 0


def test_security_list_date로_상장일수를_재면_재상장_첫_구간이_음수라_폐기한다(
        built: build.BuildResult, tmp_path: Path) -> None:
    """DESIGN 의 '`security.list_date`' 문구를 글자 그대로 구현한 변종 — 036220 1,570 +
    101970 648 행 음수. security.list_date 는 티커당 1값(최신 listing 행)이라 재상장 전 구간에서
    미래 상장일이 된다."""
    r = _variant(built, tmp_path, "universe_age_security",
                 "WITH base AS ({body}) SELECT base.* REPLACE (date_diff('day', x.list_date, "
                 "base.date) AS listing_age_days) FROM base "
                 "JOIN security x ON x.ticker = base.ticker",
                 input_columns={**UNIVERSE.input_columns,
                                "security": ("ticker", "sec_type", "list_date")})
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_listing_age_negative"] == 1570 + 648
    assert eg3.metrics["n_listing_age_listing_mismatch"] == 1570 + 648
    assert eg3.metrics["n_listing_age_null"] == 4094             # ETF: security.list_date 도 NULL


def test_no_trade_run_k가_baseline에_없으면_빌드가_거절된다(built: build.BuildResult,
                                                        tmp_path: Path) -> None:
    """산출 규칙 상수는 `_const` 로만 들어온다 — 미등재는 KeyError(사람 승인 지점)."""
    assert built.out_dir is not None
    bl = Baseline({UNIVERSE.name: {"admin_window_td": 365}})
    with pytest.raises(KeyError, match="no_trade_run_k"):
        build.build_table(UNIVERSE, STAGE_SLICE, built.out_dir.parents[1], bl,
                          build_id="b_no_k")
