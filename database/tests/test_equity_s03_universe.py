"""S03·S03B·S03B-2·S03C `universe_daily` v4 — 절단본 위 실빌드 왕복 · 합성 stage 위 상태 규칙 ·
부정 픽스처 (DESIGN §4-1 · GATES §3 ⑦ · §1 EG3-P09 · §4 FX-1-006·011·012·013~016 · §5-B8).

절단본 실측(손계산, 빌드 SQL 과 독립): 격자 41,066 = Σ security_span.n_days · 정지 126 거래일
(8 구간) · KOSDAQ 관리·투자주의환기 74 거래일 · 정리매매 18 거래일 · 신호 22/12/0/3/21.
S03B: 무거래 run {1×7, 3×3, 10, 12, 22, 49, 55, 66, 116}, k=5 에서 run 으로만 suspended 201행
(000030 18 · 101970 1 · 900050 180 · 900060 2) → suspended 327 · adv20 NULL 323 = 17구간 × 19 ·
가격 결측 0.
S03C: 무거래 346행이 이유별로 갈린다 — halt_disclosed 117(036220 56 · 101970 51 · 900060 10) ·
corp_action_window 6(005930·005935 2018-04-30~05-03, 50:1 분할 apply 05-04) · illiquid 223
(000030 22 · 101970 1 · 900050 198 · 900060 2) · liquidation·admin 0(절단본엔 겹치는 무거래가
없다 — 합성 stage 가 본다). status suspended 333 = halt 126 + illiquid∧run≥k 201 +
corp_action 6. `adv20_rank_pct` 모집단은 005930 이 3세션 빠져 21,492 / NULL 19,574 · 모집단이
있는 날 4,075(캘린더 첫 19일은 비어 있다) · 날짜별 모집단 크기 4×695 · 5×2,061 · 6×942 · 7×261 ·
8×116 · 순위 최솟값 1/8 · 최댓값 1 · rank ≥ 0.5 행 13,660(파이썬 독립 계산).
절단본에 없는 축(KOSPI 관리종목 창 · 해제 공시로 닫히는 정지 · 비거래일 접수 · master 측정값
우선 · 열린 정지 · 가격 행 결측 · S03C 의 liquidation·admin 이유와 창 밖 판정)은 `make_stage_tree`
합성 stage 위에서 검사한다. 이 슬라이스는 equity 산출(`security_span`·`trading_calendar`·
`security`·`price_daily`·`adj_factor`)을 입력으로 읽으므로 상류를 같은 equity_root 에 먼저
빌드한다(adj_factor 체인 때문에 `corp`·`corp_ticker`·`corp_event` 도 함께).
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s03, rules_s04, rules_s05, rules_s06
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"


def _seed() -> Baseline:
    """S01·S02·S03·S05·S06 seed 를 테이블 단위로 병합 — S03C 로 adj_factor 체인이 상류에 들어와
    S05·S06 상수까지 필요해졌다(test_equity_s21_workbench.seed 와 같은 규약)."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()
# S03C: universe_daily 가 equity adj_factor 를 읽으므로 계수 체인을 통째로 앞세운다
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP, rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR)
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
# ── S03C no_trade_reason — 무거래 346행(ZERO_RUNS)을 HALT_RUNS·adj_factor 적용일과 겹쳐 손계산 ──
# halt_disclosed = 무거래 ∩ halt_state: 036220 01-29(1) + 02-02~04-25(55) = 56 · 101970
# 06-12~08-19(48) + 2015-03-03~03-05(3) = 51 · 900060 09-09~09-25(10). 101970 08-20 은 해제일이라
# halt false → illiquid, 900060 09-26·27 은 해제 뒤라 illiquid.
REASON_HALT_ROWS = {"036220": 56, "101970": 51, "900060": 10}
N_HALT_DISCLOSED = 117
# corp_action_window = adj_factor 005930/005935:split:2018-05-04 의 apply_date 앞 무거래 3세션씩
CORP_ACTION_ROWS = {"005930": 3, "005935": 3}
N_CORP_ACTION = 6
# illiquid = 나머지 무거래. 000030 22 · 101970 1(08-20) · 900050 198(전 run) · 900060 2
ILLIQUID_ROWS = {"000030": 22, "101970": 1, "900050": 198, "900060": 2}
N_ILLIQUID = 223
N_SUSPENDED = N_HALT + N_BY_RUN + N_CORP_ACTION        # 126 + 201 + 6
# 절단본에는 halt 가 앞서지 않는 정리매매 무거래도, 지정 신호 5세션 안 무거래도 없다
N_LIQUIDATION_REASON = 0
N_ADMIN_REASON = 0
# 우선순위 ①↔② 를 맞바꾼 사본이 갈리는 행 = 무거래 ∩ halt ∩ 정리매매
# (036220 2016-04-22·04-25 · 101970 2015-03-04·03-05)
N_HALT_AND_LIQUIDATION = 4
CORP_ACTION_LOOKBACK, CORP_ACTION_LOOKAHEAD, ADMIN_SIGNAL_WINDOW = 5, 45, 5
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
# S03B-2 adv20_rank_pct — 파이썬 독립 계산(모집단 = 보통주 ∧ listed ∧ adv20 있음, cume_dist)
_POP = "sec_type = 'common' AND status = 'listed' AND adv20_krw IS NOT NULL"
N_RANK_POP = 21492                   # S03C 로 005930 이 2018-04-30·05-02·05-03 3세션 빠졌다
N_RANK_NULL = N_GRID - N_RANK_POP
N_DATES_WITH_POP = 4075              # 캘린더 4,094 − 첫 19일(전 종목 adv20 NULL)
POP_SIZE_HIST = {4: 695, 5: 2061, 6: 942, 7: 261, 8: 116}
N_RANK_TOP_HALF = 13660              # rank ≥ 0.5 — 005930 3행이 빠진 만큼 그날 모집단이 4로 줄어
                                     # 나머지 행의 순위가 올라 총계는 그대로다(손계산 −3 +3)
# 2018-05-03 모집단 4 — 005930 은 분할 창 무거래(corp_action_window)라 suspended → 모집단 밖
RANK_20180503 = {"003540": 0.25, "161890": 0.5, "000030": 0.75, "000660": 1.0}
# backfill_end 모집단 8 — 000660 이 최댓값, 005930 은 7/8
RANK_20260820 = {"036220": 0.125, "101970": 0.25, "0001A0": 0.375, "003540": 0.5, "247540": 0.625,
                 "161890": 0.75, "005930": 0.875, "000660": 1.0}


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
    # equity 입력 5개가 stage 입력과 같은 규약으로 고정된다 (inputs.source_root)
    assert built.inputs["security_span"] == "b_security_span"
    assert built.inputs["trading_calendar"] == "b_trading_calendar"
    assert built.inputs["security"] == "b_security"
    assert built.inputs["price_daily"] == "b_price_daily"
    assert built.inputs["adj_factor"] == "b_adj_factor"          # S03C
    assert set(built.inputs) == set(UNIVERSE.inputs)
    assert not {"stg_price_daily", "stg_etf_price_daily"} & set(built.inputs)


def test_EG1_우변은_span_n_days_합이다(built: build.BuildResult) -> None:
    eg1 = _gate(built, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["delta"]) == (N_GRID, N_GRID, 0)
    assert "sum(n_days)" in str(eg1["rhs_sql"]) and "security_span" in str(eg1["rhs_sql"])


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    """S03 컬럼 → S03B 4개(+ S03B-2 `adv20_rank_pct` 는 `adv20_krw` 다음, S03C `no_trade_reason`
    은 `adv20_rank_pct` 다음) → available 2개 (DESIGN §4-1 순서)."""
    assert built.out_dir is not None
    cols = [str(r[0]) for r in _query(built.out_dir, "DESCRIBE u")]
    assert cols == list(UNIVERSE.columns)
    assert cols[-8:] == ["mktcap_krw", "adv20_krw", "adv20_rank_pct", "no_trade_reason",
                         "listing_age_days", "no_trade_run", "available_date", "available_basis"]


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


def test_status는_halt_기업행위창_또는_이유없는_무거래로_정지다(built: build.BuildResult) -> None:
    """S03C 규칙: suspended ⇔ halt_state ∨ corp_action_window ∨ (illiquid ∧ run ≥ k).
    k 임계는 이유를 모르는 무거래에만 걸린다 — run 으로만 잡히는 201행 + 기업행위 창 6행."""
    assert built.out_dir is not None
    m = _gate(built, "EG3_universe").metrics
    assert m["no_trade_run_k"] == NO_TRADE_RUN_K
    assert m["status_counts"] == {"listed": N_GRID - N_SUSPENDED, "suspended": N_SUSPENDED}
    assert m["n_suspended_by_run"] == N_BY_RUN and m["n_status_halt_mismatch"] == 0
    assert m["n_suspended_by_corp_action"] == N_CORP_ACTION
    got = dict(_query(built.out_dir, "SELECT ticker, count(*) FROM u WHERE status = 'suspended' "
                                     "AND NOT halt_state AND no_trade_reason = 'illiquid' "
                                     "GROUP BY 1"))
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


def test_adv20_rank_pct는_같은날_보통주_listed_모집단의_cume_dist(built: build.BuildResult) -> None:
    """S03B-2. 모집단 = sec_type='common' ∧ status='listed' ∧ adv20 있음, 그 안에서
    cume_dist = (adv20 ≤ 자기 행인 모집단 행수) / 모집단 행수 — (0, 1], 최댓값 1, 최솟값 1/n.
    모집단 밖(ETF·우선주·외국주·정지·창 미달)은 NULL. 전 행을 파이썬으로 독립 재계산해 대조한다."""
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT date, ticker, sec_type, status, adv20_krw, adv20_rank_pct "
                                 "FROM u ORDER BY 1, 2")
    pop: dict[object, list[tuple[object, object]]] = {}
    for d, t, st, s, adv, _ in rows:
        if st == "common" and s == "listed" and adv is not None:
            pop.setdefault(d, []).append((t, adv))
    n_bad = 0
    for d, t, _st, _s, adv, r in rows:
        lst = pop.get(d, [])
        exp = (sum(1 for _, a in lst if a <= adv) / len(lst)          # type: ignore[operator]
               if (t, adv) in lst else None)
        if (r is None) != (exp is None) or (r is not None and abs(r - exp) > 1e-12):  # type: ignore[operator]
            n_bad += 1
    assert n_bad == 0
    assert sum(len(v) for v in pop.values()) == N_RANK_POP and len(pop) == N_DATES_WITH_POP
    assert _query(built.out_dir, "SELECT count(adv20_rank_pct), count(*) - count(adv20_rank_pct), "
                                 "min(adv20_rank_pct), max(adv20_rank_pct) FROM u") == [
        (N_RANK_POP, N_RANK_NULL, 0.125, 1.0)]
    # 날짜별 모집단 크기 분포 · 모집단 밖은 어떤 sec_type/status 든 NULL
    assert dict(_query(built.out_dir, "SELECT n, count(*) FROM (SELECT date, count(adv20_rank_pct) "
                                      "AS n FROM u GROUP BY 1) WHERE n > 0 GROUP BY 1")) == \
        POP_SIZE_HIST
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE adv20_rank_pct IS NOT NULL "
                                 f"AND NOT ({_POP})") == [(0,)]
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE adv20_rank_pct IS NULL "
                                 f"AND {_POP}") == [(0,)]
    # 손계산 두 날짜 — 005930 이 항상 1 은 아니다(2026-08-20 은 000660)
    by_date = ("SELECT ticker, adv20_rank_pct FROM u WHERE date = DATE '{}' "
               "AND adv20_rank_pct IS NOT NULL")
    assert dict(_query(built.out_dir, by_date.format("2018-05-03"))) == RANK_20180503
    assert dict(_query(built.out_dir, by_date.format("2026-08-20"))) == RANK_20260820
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE adv20_rank_pct >= 0.5") == [
        (N_RANK_TOP_HALF,)]
    # 정지 행(000030 2019-01-15 run 5)·우선주·ETF 는 adv20 이 있어도 NULL
    assert _query(built.out_dir, "SELECT adv20_krw IS NOT NULL, adv20_rank_pct FROM u WHERE "
                                 "(ticker, date) IN (('000030', DATE '2019-01-15'), "
                                 "('005935', DATE '2018-05-03'), ('069500', DATE '2018-05-03')) "
                  ) == [(True, None)] * 3
    m = _gate(built, "EG3_universe").metrics
    assert m["n_adv20_rank_null"] == N_RANK_NULL
    assert m["n_dates_with_rank_pop"] == N_DATES_WITH_POP
    assert m["n_dates_without_rank_pop"] == 4094 - N_DATES_WITH_POP
    assert m["adv20_rank_pop_size"] == {"min": 4, "p50": 5.0, "max": 8}
    assert all(m[k] == 0 for k in ("n_adv20_rank_out_of_range", "n_adv20_rank_null_mismatch",
                                   "n_adv20_rank_max_not_one", "n_adv20_rank_pop_mismatch",
                                   "n_adv20_rank_order_violation"))


def test_no_trade_reason은_무거래_행만_우선순위대로_분류한다(built: build.BuildResult) -> None:
    """S03C. 거래 행·가격 없는 날은 none, 무거래 행은 ① halt ② 정리매매 ③ 기업행위 창 ④ 관리종목
    지정 직후 ⑤ 나머지. 절단본은 ①③⑤ 만 나오고 ②④ 는 합성 stage 가 본다."""
    assert built.out_dir is not None
    got = dict(_query(built.out_dir, "SELECT no_trade_reason, count(*) FROM u GROUP BY 1"))
    assert got == {"none": N_TRADE_DAYS, "halt_disclosed": N_HALT_DISCLOSED,
                   "corp_action_window": N_CORP_ACTION, "illiquid": N_ILLIQUID}
    assert sum(got.values()) == N_GRID
    for reason, rows in (("halt_disclosed", REASON_HALT_ROWS),
                         ("corp_action_window", CORP_ACTION_ROWS), ("illiquid", ILLIQUID_ROWS)):
        assert dict(_query(built.out_dir, "SELECT ticker, count(*) FROM u WHERE no_trade_reason = "
                                          f"'{reason}' GROUP BY 1")) == rows
    # 거래 행은 halt 여도 none (101970 지정일 06-11 은 거래량 185,252), 무거래 행은 none 이 아니다
    assert _query(built.out_dir, "SELECT date, no_trade_run, no_trade_reason, status FROM u "
                                 "WHERE ticker = '101970' AND date BETWEEN '2014-06-11' "
                                 "AND '2014-06-13' ORDER BY 1") == [
        (date(2014, 6, 11), 0, "none", "suspended"), (date(2014, 6, 12), 1, "halt_disclosed",
                                                      "suspended"),
        (date(2014, 6, 13), 2, "halt_disclosed", "suspended")]
    # ③ 기업행위 창 — 분할 apply_date(2018-05-04) 앞 3세션. run 3 < k 여도 suspended
    assert _query(built.out_dir, "SELECT date, no_trade_run, no_trade_reason, status FROM u "
                                 "WHERE ticker = '005930' AND date BETWEEN '2018-04-27' "
                                 "AND '2018-05-04' ORDER BY 1") == [
        (date(2018, 4, 27), 0, "none", "listed"),
        (date(2018, 4, 30), 1, "corp_action_window", "suspended"),
        (date(2018, 5, 2), 2, "corp_action_window", "suspended"),
        (date(2018, 5, 3), 3, "corp_action_window", "suspended"),
        (date(2018, 5, 4), 0, "none", "listed")]
    # ⑤ illiquid — 신호도 계수도 없는 무거래. run < k 는 listed, run ≥ k 부터 suspended
    assert _query(built.out_dir, "SELECT date, no_trade_run, no_trade_reason, status FROM u "
                                 "WHERE ticker = '900050' AND date BETWEEN '2017-04-04' "
                                 "AND '2017-04-05' ORDER BY 1") == [
        (date(2017, 4, 4), 4, "illiquid", "listed"), (date(2017, 4, 5), 5, "illiquid",
                                                      "suspended")]
    m = _gate(built, "EG3_universe").metrics
    assert m["no_trade_reason_counts"] == got
    assert m["no_trade_reason_vocab"] == list(rules_s03.NO_TRADE_REASON_VOCAB)
    assert (m["corp_action_lookback_sessions"], m["corp_action_lookahead_sessions"],
            m["admin_signal_window_sessions"]) == (CORP_ACTION_LOOKBACK, CORP_ACTION_LOOKAHEAD,
                                                   ADMIN_SIGNAL_WINDOW)
    # illiquid 의 run 분포 — run ≥ k 부분(20+33+148)이 곧 n_suspended_by_run
    assert m["no_trade_reason_run_hist_illiquid"] == {"1-1": 10, "2-4": 12, "5-9": 20,
                                                      "10-19": 33, "20+": 148}
    assert sum(m["no_trade_reason_run_hist_illiquid"].values()) == N_ILLIQUID
    assert m["no_trade_reason_run_hist_illiquid"]["5-9"] + m["no_trade_reason_run_hist_illiquid"][
        "10-19"] + m["no_trade_reason_run_hist_illiquid"]["20+"] == N_BY_RUN
    assert m["n_adj_apply_rows"] == 8 and m["n_adj_apply_rows_not_ok"] == 5
    assert m["adj_apply_event_types"] == {"bonus": 1, "capred": 5, "split": 2}
    assert m["n_liquid_excluded_by_illiquid"] == N_RANK_TOP_HALF - 13656
    assert all(m[k] == 0 for k in (
        "n_no_trade_reason_outside_vocab", "n_no_trade_reason_null",
        "n_no_trade_reason_trade_not_none", "n_no_trade_reason_no_trade_none",
        "n_no_trade_reason_halt_mismatch", "n_no_trade_reason_recompute_mismatch",
        "n_status_reason_mismatch", "n_adj_apply_off_calendar"))


def test_기업행위_창은_ok가_아닌_계수_행도_센다(built: build.BuildResult) -> None:
    """설계 ③ 「ok 여부 무관」 — 절단본 adj_factor 8행 중 5행이 factor_ok=false(101970 감자)다.
    101970 의 적용일(2015-11-26~2018-10-15)은 구간(2015-03-16 폐지) 밖이라 격자에 닿지 않아
    corp_action_window 는 005930·005935 6행뿐이지만, 창 계산이 ok 행만 볼 경우 서버에서 감자 정지가
    통째로 illiquid 로 떨어진다 — 게이트 재계산도 같은 규칙을 쓴다."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE ticker = '101970' "
                                 "AND no_trade_reason = 'corp_action_window'") == [(0,)]
    assert _gate(built, "EG3_universe").metrics["n_adj_apply_rows_not_ok"] == 5


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
    # S03C 입력 — 이 시나리오엔 기업행위가 없다(격자에 없는 티커 1행으로 표만 채운다)
    _equity_table(tmp_path, root, make_stage_tree, "adj_factor", [
        {"ticker": "Z99999", "apply_date": TD[0], "factor_ok": True, "event_type": "split"}])
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps([{"case": "k_td1", "key": {"date": "2020-01-06", "ticker": "K00010"},
                               "column": "status", "expect": "suspended", "source": "hand"}]),
                  encoding="utf-8")
    bl = Baseline({UNIVERSE.name: {"admin_window_td": 3, "no_trade_run_k": SYN_K,
                                   "adv_window_td": 20, "corp_action_lookback_sessions": 5,
                                   "corp_action_lookahead_sessions": 45,
                                   "admin_signal_window_sessions": 5}})
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
    S03B: run td6..td9 = 1,2,3,4. **S03C**: td8·td9 는 정리매매 개시 뒤라 이유가 liquidation 이고,
    k 임계는 illiquid 에만 걸리므로 run 3·4 ≥ k=3 이어도 status 는 listed 다(S03B 는 정지였다).
    정리매매 종목은 `universe_policy` 의 `NOT liquidation_window` 가 투자 유니버스에서 뺀다."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "Q00020", "halt_state") == [False] * 5 + [True, True, False, False]
    assert _cell(r.out_dir, "Q00020", "no_trade_run") == [0] * 5 + [1, 2, 3, 4]
    assert _cell(r.out_dir, "Q00020", "no_trade_reason") == ["none"] * 5 + [
        "halt_disclosed", "halt_disclosed", "liquidation", "liquidation"]
    assert _cell(r.out_dir, "Q00020", "status") == ["listed"] * 5 + ["suspended"] * 2 + [
        "listed"] * 2
    assert _cell(r.out_dir, "Q00020", "liquidation_window") == [False] * 7 + [True, True]
    m = _gate(r, "EG3_universe").metrics
    assert m["n_halt_open_at_delist"] == 0 and m["n_suspended_by_run"] == 0
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
    # adv20 이 전부 NULL 이면 순위 모집단도 비어 rank 전부 NULL — 게이트 pass(빈 모집단은 위반 아님)
    assert m["n_adv20_rank_null"] == 39 and m["adv20_rank_pop_size"] is None
    assert m["n_dates_with_rank_pop"] == 0 and m["n_dates_without_rank_pop"] == 10


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


# ── 합성 stage — S03C 이유 우선순위 5부류 ─────────────────────────────────────
# 캘린더 20거래일 2021-03-01(월) ~ 03-26(금), 주말만 뺀다. rtd1..rtd20 = RTD[0..19].
RTD = [date(2021, 3, d) for d in
       (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 24, 25, 26)]
REASON_K = 5                         # 합성 baseline no_trade_run_k — 절단본·서버와 같은 값
REASON_LOOKAHEAD = 45                # apply 앞 45세션 · 뒤 5세션


def _reason_synthetic(tmp_path: Path, make_stage_tree) -> build.BuildResult:
    """이유 5부류를 한 격자에 모은 합성 stage — 20세션 × 5종목 = 100행.

    C00010 기업행위: rtd9~rtd13 무거래 + `adj_factor` 적용일 rtd14(factor_ok=false) → 창 안이라
                     corp_action_window·suspended. rtd20 은 창(rtd14 + lookback 5 = rtd19) 밖이라
                     illiquid — 같은 종목·같은 무거래인데 창 하나로 갈린다.
    I00020 비유동  : 신호도 계수도 없이 rtd11~rtd16 6세션 무거래 → 전부 illiquid, run 1~6.
                     run 3(rtd13) 은 listed, run 5·6(rtd15·rtd16) 은 suspended (k=5).
    H00030 정지    : rtd6 정지 공시 + rtd6~rtd10 무거래 → halt_disclosed·suspended.
    A00040 관리    : rtd5 관리종목지정 공시(KOSPI 창 규칙) → rtd6·rtd7 무거래는 지정 신호가 5세션
                     안이라 admin, rtd13 무거래는 admin_state 가 true 여도 신호가 8세션 전이라
                     illiquid. 셋 다 status listed(run < k) — 관리종목은 정책이 뺀다.
    L00050 정리매매: rtd11 정리매매 개시 공시 + rtd12~rtd20 9세션 무거래 → liquidation. run 이 9까지
                     가도 status 는 listed — k 는 illiquid 에만 걸린다.
    """
    root = tmp_path / "equity"
    root.mkdir()
    st = tmp_path / "stg"
    listing = [
        *({"ticker": t, "date": d, "market": "KOSPI", "sect_tp": "", "sect_available": False,
           "list_date": RTD[0]} for t in ("C00010", "H00030", "A00040") for d in RTD),
        *({"ticker": t, "date": d, "market": "KOSDAQ", "sect_tp": "벤처기업부",
           "sect_available": True, "list_date": RTD[0]} for t in ("I00020", "L00050")
          for d in RTD)]
    trees = [
        make_stage_tree(st, "stg_listing_daily", listing, partition_class="date_axis"),
        # master 측정축은 이 시나리오 밖 — 격자에 없는 티커 1행으로 표만 채운다
        make_stage_tree(st, "stg_master_daily", [
            {"ticker": "Z99999", "date": RTD[0], "is_admin_issue": False, "is_trade_halt": False,
             "is_liquidation": False}]),
        make_stage_tree(st, "stg_disclosure", [
            {"rcept_no": "20210305000001", "rcept_dt": RTD[4], "ticker": "A00040",
             "has_ticker": True, "report_nm": "관리종목지정"},
            {"rcept_no": "20210308000001", "rcept_dt": RTD[5], "ticker": "H00030",
             "has_ticker": True, "report_nm": "주권매매거래정지(투자자보호)"},
            {"rcept_no": "20210315000001", "rcept_dt": RTD[10], "ticker": "L00050",
             "has_ticker": True, "report_nm": "기타시장안내(정리매매 개시)"}]),
    ]
    stage_root = trees[0].stage_root
    _equity_table(tmp_path, root, make_stage_tree, "trading_calendar", [{"date": d} for d in RTD])
    _equity_table(tmp_path, root, make_stage_tree, "security_span", [
        {"ticker": t, "span_seq": 1, "first_date": RTD[0], "last_date": RTD[19], "n_days": 20,
         "end_reason": "coverage_gap"}
        for t in ("C00010", "I00020", "H00030", "A00040", "L00050")])
    _equity_table(tmp_path, root, make_stage_tree, "security", [
        {"ticker": t, "sec_type": "common"}
        for t in ("C00010", "I00020", "H00030", "A00040", "L00050")])
    no_trade = {"C00010": set(RTD[8:13]) | {RTD[19]}, "I00020": set(RTD[10:16]),
                "H00030": set(RTD[5:10]), "A00040": {RTD[5], RTD[6], RTD[12]},
                "L00050": set(RTD[11:])}
    _equity_table(tmp_path, root, make_stage_tree, "price_daily", [
        _price(t, d, 0 if d in zero else 100) for t, zero in no_trade.items() for d in RTD])
    _equity_table(tmp_path, root, make_stage_tree, "adj_factor", [
        {"ticker": "C00010", "apply_date": RTD[13], "factor_ok": False, "event_type": "capred"}])
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps([
        {"case": "corp_action", "key": {"date": RTD[8].isoformat(), "ticker": "C00010"},
         "column": "no_trade_reason", "expect": "corp_action_window", "source": "hand"},
        {"case": "corp_action_outside", "key": {"date": RTD[19].isoformat(), "ticker": "C00010"},
         "column": "no_trade_reason", "expect": "illiquid", "source": "hand"}]), encoding="utf-8")
    bl = Baseline({UNIVERSE.name: {
        "admin_window_td": 365, "no_trade_run_k": REASON_K, "adv_window_td": 20,
        "corp_action_lookback_sessions": 5, "corp_action_lookahead_sessions": REASON_LOOKAHEAD,
        "admin_signal_window_sessions": 5}})
    return build.build_table(UNIVERSE, stage_root, root, bl, build_id="b_reason",
                             fixtures_path=fx)


def test_이유_합성_빌드가_전_게이트를_통과한다(tmp_path: Path, make_stage_tree) -> None:
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.ok, _fails(reason_built)
    assert reason_built.n_rows == 5 * len(RTD)
    m = _gate(reason_built, "EG3_universe").metrics
    # 무거래 29행 = C00010 6 + I00020 6 + H00030 5 + A00040 3 + L00050 9, 나머지 71 은 거래
    assert m["no_trade_reason_counts"] == {"none": 71, "corp_action_window": 5, "illiquid": 8,
                                           "halt_disclosed": 5, "admin": 2, "liquidation": 9}
    assert sum(m["no_trade_reason_counts"].values()) == 5 * len(RTD)
    assert all(m[k] == 0 for k in (
        "n_no_trade_reason_outside_vocab", "n_no_trade_reason_null",
        "n_no_trade_reason_trade_not_none", "n_no_trade_reason_no_trade_none",
        "n_no_trade_reason_halt_mismatch", "n_no_trade_reason_recompute_mismatch",
        "n_status_reason_mismatch", "n_status_halt_mismatch", "n_adj_apply_off_calendar"))


def test_기업행위_창_안팎이_같은_무거래를_가른다(tmp_path: Path, make_stage_tree) -> None:
    """③ — C00010 의 적용일 rtd14 기준 창 [rtd14 − 45, rtd14 + 5] = [rtd1, rtd19].
    rtd9~rtd13 은 창 안(corp_action_window·suspended, run 1~5), rtd20 은 창 밖(illiquid·run 1)."""
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.out_dir is not None
    assert _cell(reason_built.out_dir, "C00010", "no_trade_reason") == (
        ["none"] * 8 + ["corp_action_window"] * 5 + ["none"] * 6 + ["illiquid"])
    assert _cell(reason_built.out_dir, "C00010", "status") == (
        ["listed"] * 8 + ["suspended"] * 5 + ["listed"] * 7)
    # 창 안 첫날은 run 1 인데도 suspended — k 는 illiquid 에만 건다
    assert _cell(reason_built.out_dir, "C00010", "no_trade_run") == (
        [0] * 8 + [1, 2, 3, 4, 5] + [0] * 6 + [1])
    m = _gate(reason_built, "EG3_universe").metrics
    assert m["n_suspended_by_corp_action"] == 5 and m["n_adj_apply_rows_not_ok"] == 1


def test_신호_없는_무거래는_run_k_에서_정지가_된다(tmp_path: Path, make_stage_tree) -> None:
    """⑤ — I00020 은 6세션 무거래 전부 illiquid. run 3 은 listed, run 5·6 은 suspended(k=5)."""
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.out_dir is not None
    assert _cell(reason_built.out_dir, "I00020", "no_trade_reason") == (
        ["none"] * 10 + ["illiquid"] * 6 + ["none"] * 4)
    assert _cell(reason_built.out_dir, "I00020", "no_trade_run") == (
        [0] * 10 + [1, 2, 3, 4, 5, 6] + [0] * 4)
    assert _cell(reason_built.out_dir, "I00020", "status") == (
        ["listed"] * 14 + ["suspended"] * 2 + ["listed"] * 4)
    m = _gate(reason_built, "EG3_universe").metrics
    assert m["n_suspended_by_run"] == 2
    # illiquid 8 = I00020 run 1~6 + C00010 rtd20 run 1 + A00040 rtd13 run 1
    assert m["no_trade_reason_run_hist_illiquid"] == {"1-1": 3, "2-4": 3, "5-9": 2, "10-19": 0,
                                                      "20+": 0}


def test_정지_공시가_있으면_halt_disclosed가_우선한다(tmp_path: Path, make_stage_tree) -> None:
    """① — H00030 rtd6 지정, rtd6~rtd10 무거래, rtd11 거래로 암묵 해제."""
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.out_dir is not None
    assert _cell(reason_built.out_dir, "H00030", "no_trade_reason") == (
        ["none"] * 5 + ["halt_disclosed"] * 5 + ["none"] * 10)
    assert _cell(reason_built.out_dir, "H00030", "status") == (
        ["listed"] * 5 + ["suspended"] * 5 + ["listed"] * 10)


def test_관리종목_지정_직후_무거래만_admin이다(tmp_path: Path, make_stage_tree) -> None:
    """④ — A00040 rtd5 지정. rtd6·rtd7(신호 1·2세션 전) 은 admin, rtd13(8세션 전) 은 admin_state 가
    여전히 true 여도 illiquid. 셋 다 run < k 라 status listed — 관리종목은 정책이 뺀다."""
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.out_dir is not None
    assert _cell(reason_built.out_dir, "A00040", "admin_state")[4:] == [True] * 16
    assert _cell(reason_built.out_dir, "A00040", "no_trade_reason") == (
        ["none"] * 5 + ["admin"] * 2 + ["none"] * 5 + ["illiquid"] + ["none"] * 7)
    assert set(_cell(reason_built.out_dir, "A00040", "status")) == {"listed"}
    assert set(_cell(reason_built.out_dir, "A00040",
                     "admin_state_basis")) == {"derived_kospi_window"}


def test_정리매매_무거래는_run이_길어도_정지가_아니다(tmp_path: Path, make_stage_tree) -> None:
    """② + status 규칙 — L00050 rtd11 개시 공시, rtd12~rtd20 9세션 무거래. run 이 9 까지 가도
    이유가 liquidation 이라 status 는 listed 다(정리매매 제외는 universe_policy 몫)."""
    reason_built = _reason_synthetic(tmp_path, make_stage_tree)
    assert reason_built.out_dir is not None
    assert _cell(reason_built.out_dir, "L00050", "no_trade_reason") == (
        ["none"] * 11 + ["liquidation"] * 9)
    assert _cell(reason_built.out_dir, "L00050", "no_trade_run")[11:] == list(range(1, 10))
    assert set(_cell(reason_built.out_dir, "L00050", "status")) == {"listed"}
    assert _cell(reason_built.out_dir, "L00050", "liquidation_window") == (
        [False] * 10 + [True] * 10)


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
    assert eg3.metrics["n_status_halt_mismatch"] == N_SUSPENDED


def test_run이_k_이상인데_listed면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                       tmp_path: Path) -> None:
    """S03 규칙(halt 만)으로 되돌린 변종 — run 으로 잡혀야 할 201행 + 기업행위 창 6행이 mismatch."""
    r = _variant(built, tmp_path, "universe_s03_status",
                 "WITH base AS ({body}) SELECT * REPLACE (CASE WHEN halt_state THEN 'suspended' "
                 "ELSE 'listed' END AS status) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_status_halt_mismatch"] == N_BY_RUN + N_CORP_ACTION
    assert eg3.metrics["n_suspended_by_run"] == 0
    assert eg3.metrics["n_suspended_by_corp_action"] == 0
    assert _gate(r, "EG4").detail == "upstream_failed"


def test_이유_우선순위를_바꾸면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                 tmp_path: Path) -> None:
    """S03C ①②를 맞바꾼 사본 — 정지와 정리매매가 겹친 무거래 4행(036220 2016-04-22·04-25 ·
    101970 2015-03-04·03-05)이 halt_disclosed 대신 liquidation 이 된다. 게이트가 입력에서 우선순위를
    다시 계산하므로 갈리고, status 규칙(halt 우선)은 그대로라 status 술어는 조용하다 — 이유 재계산
    술어가 유일한 방어다."""
    r = _variant(built, tmp_path, "universe_reason_swapped",
                 "WITH base AS ({body}) SELECT * REPLACE (CASE WHEN no_trade_reason = "
                 "'halt_disclosed' AND liquidation_window THEN 'liquidation' "
                 "ELSE no_trade_reason END AS no_trade_reason) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_no_trade_reason_recompute_mismatch"] == N_HALT_AND_LIQUIDATION
    assert eg3.metrics["n_no_trade_reason_halt_mismatch"] == N_HALT_AND_LIQUIDATION
    assert eg3.metrics["n_status_halt_mismatch"] == 0
    assert eg3.metrics["n_status_reason_mismatch"] == 0


def test_기업행위_창을_무시하면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                 tmp_path: Path) -> None:
    """③ 을 뺀 사본(corp_action_window → illiquid) — 6행이 재계산과 갈리고, 그 6행은 run 1~3 <
    k 라 status='suspended' 가 산출 이유와도 어긋난다(n_status_halt_mismatch)."""
    r = _variant(built, tmp_path, "universe_reason_no_ca",
                 "WITH base AS ({body}) SELECT * REPLACE (CASE WHEN no_trade_reason = "
                 "'corp_action_window' THEN 'illiquid' ELSE no_trade_reason END "
                 "AS no_trade_reason) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_no_trade_reason_recompute_mismatch"] == N_CORP_ACTION
    assert eg3.metrics["n_status_halt_mismatch"] == N_CORP_ACTION
    assert eg3.metrics["n_status_reason_mismatch"] == 0      # 재계산 이유로는 status 가 맞다


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


def test_percent_rank로_순위를_내면_최솟값_0이라_EG3_universe가_폐기한다(built: build.BuildResult,
                                                                 tmp_path: Path) -> None:
    """정의를 percent_rank((rank−1)/(n−1)) 로 바꾼 변종 — 모집단이 있는 날마다 최솟값 행이 0 이라
    `(0, 1]` 밖(4,075 = 날짜 수, 최솟값 동률 없음). rank × n 도 정수가 아니라 pop_mismatch 도
    뜬다."""
    r = _variant(built, tmp_path, "universe_rank_percent",
                 f"WITH base AS ({{body}}) SELECT * REPLACE (CASE WHEN {_POP} THEN percent_rank() "
                 f"OVER (PARTITION BY date, {_POP} ORDER BY adv20_krw) END AS adv20_rank_pct) "
                 "FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_adv20_rank_out_of_range"] == N_DATES_WITH_POP
    assert eg3.metrics["n_adv20_rank_pop_mismatch"] > 0
    assert eg3.metrics["n_adv20_rank_null_mismatch"] == 0
    assert eg3.metrics["n_adv20_rank_max_not_one"] == 0


def test_모집단을_전_종목으로_잡으면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                     tmp_path: Path) -> None:
    """ETF·우선주·외국주·정지 행까지 넣고 순위를 낸 변종 — 모집단 밖인데 순위가 있는 행
    41,066 − 323(adv20 NULL) − 21,495 = 19,248 이 null_mismatch. 모집단 크기도 달라 정수 검사가
    깨진다."""
    r = _variant(built, tmp_path, "universe_rank_all",
                 "WITH base AS ({body}) SELECT * REPLACE (CASE WHEN adv20_krw IS NOT NULL THEN "
                 "cume_dist() OVER (PARTITION BY date, adv20_krw IS NOT NULL ORDER BY adv20_krw) "
                 "END AS adv20_rank_pct) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_adv20_rank_null_mismatch"] == N_GRID - N_SPANS * 19 - N_RANK_POP
    assert eg3.metrics["n_adv20_rank_pop_mismatch"] > 0
    assert eg3.metrics["n_adv20_rank_out_of_range"] == 0


def test_순위_NULL을_1로_채우면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                tmp_path: Path) -> None:
    """모집단 밖을 '최상위' 로 채운 변종 — 19,571행 null_mismatch. 순위값은 범위 안이라 다른 술어는
    조용하다(NULL ⇔ 모집단 밖 술어가 유일한 방어)."""
    r = _variant(built, tmp_path, "universe_rank_filled",
                 "WITH base AS ({body}) SELECT * REPLACE (coalesce(adv20_rank_pct, 1) "
                 "AS adv20_rank_pct) FROM base")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_adv20_rank_null_mismatch"] == N_RANK_NULL
    assert eg3.metrics["n_adv20_rank_out_of_range"] == 0
    assert eg3.metrics["n_adv20_rank_max_not_one"] == 0


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
