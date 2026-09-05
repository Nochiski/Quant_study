"""S03 `universe_daily` v1 — 절단본 위 실빌드 왕복 · 합성 stage 위 상태 규칙 · 부정 픽스처
(DESIGN §4-1 · GATES §3 ⑦ · §1 EG3-P09 · §4 FX-1-006·011·013~016).

절단본 실측(손계산): 격자 41,066 = Σ security_span.n_days · 정지 126 거래일(8 구간) · KOSDAQ 관리·
투자주의환기 74 거래일 · 정리매매 18 거래일 · 신호 22/12/0/3/21. 절단본에 없는 축(KOSPI 관리종목
창 · 해제 공시로 닫히는 정지 · 비거래일 접수 · master 측정값 우선 · 열린 정지)은 `make_stage_tree`
합성 stage 위에서 검사한다. 이 슬라이스가 처음으로 equity 산출(`security_span`·`trading_calendar`·
`security`)을 입력으로 읽으므로 상류 3테이블을 같은 equity_root 에 먼저 빌드한다.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s03
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = Baseline({**load(rules_s01.BASELINE_SEED).data,
                 **load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data,
                 **load(rules_s03.BASELINE_SEED).data})
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN)
UNIVERSE = rules_s03.UNIVERSE_DAILY
BACKFILL_END = date(2026, 8, 20)

N_GRID = 41066                       # = 절단본 Σ n_days (test_equity_s02_span.N_EXIST_PAIRS)
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
          SELECT ticker, date, {flag}{', ' + extra if extra else ''},
                 row_number() OVER (PARTITION BY ticker ORDER BY date)
                 - row_number() OVER (PARTITION BY ticker, {flag} ORDER BY date) AS grp
          FROM u) WHERE {flag} GROUP BY {cols}, grp"""))


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
    assert [g.name for g in built.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_universe",
                                             "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates if g.status is not GateStatus.PASS} == {
        "EG5a": "skip"}
    assert built.n_rows == N_GRID and built.n_reject == 0
    # equity 입력 3개가 stage 입력과 같은 규약으로 고정된다 (inputs.source_root)
    assert built.inputs["security_span"] == "b_security_span"
    assert built.inputs["trading_calendar"] == "b_trading_calendar"
    assert built.inputs["security"] == "b_security"
    assert set(built.inputs) == set(UNIVERSE.inputs)


def test_EG1_우변은_span_n_days_합이다(built: build.BuildResult) -> None:
    eg1 = _gate(built, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["delta"]) == (N_GRID, N_GRID, 0)
    assert "sum(n_days)" in str(eg1["rhs_sql"]) and "security_span" in str(eg1["rhs_sql"])


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert [str(r[0]) for r in _query(built.out_dir, "DESCRIBE u")] == list(UNIVERSE.columns)


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
    """[지정, min(해제, 다음 volume>0)) 합집합. 정리매매 개시 공시는 해제가 아니다."""
    assert built.out_dir is not None
    assert _runs(built.out_dir, "halt_state") == HALT_RUNS
    assert _gate(built, "EG3_universe").metrics["n_halt_days"] == 126
    assert sum(n for *_, n in HALT_RUNS) == 126


def test_지정_해제_동일일은_그날_정지가_아니다(built: build.BuildResult) -> None:
    """FX-1-014 — '매매거래정지및정지해제' 는 양쪽 신호 true, halt_state false."""
    assert built.out_dir is not None
    rows = _query(built.out_dir, "SELECT ticker, date, halt_state FROM u "
                                 "WHERE signal_halt AND signal_halt_release ORDER BY 1, 2")
    assert len(rows) == 10 and all(not h for *_, h in rows)      # 절단본의 동일일 10건 전부
    assert ("900050", date(2010, 11, 25), False) in rows


def test_status는_S03에서_halt_state와_동치다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _gate(built, "EG3_universe").metrics["status_counts"] == {
        "listed": N_GRID - 126, "suspended": 126}
    assert _query(built.out_dir, "SELECT count(*) FROM u WHERE (status = 'suspended') <> "
                                 "halt_state") == [(0,)]


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


def _equity_table(tmp_path: Path, equity_root: Path, make_stage_tree, table: str,
                  rows: list[dict[str, object]]) -> None:
    """앞서 커밋된 equity 테이블 흉내 — stage 규약 트리를 만들어 equity_root 로 옮긴다."""
    tree = make_stage_tree(tmp_path / f"eq_{table}", table, rows, build_id=f"b_{table}")
    shutil.move(str(tree.table_root), str(equity_root / table))


def _synthetic(tmp_path: Path, make_stage_tree, *, d_end_reason: str) -> build.BuildResult:
    """K00010(KOSPI) · Q00020(KOSDAQ, td1~td9 폐지) · E00030(ETF) · D00040(KOSPI, 열린 정지).

    D00040 은 td9 지정 뒤 해제도 거래도 없이 구간이 끝난다 — `d_end_reason` 이 coverage_gap 이면
    정상(현재 정지 중), data_gap 이면 EG3-P09 폐기 대상.
    """
    root = tmp_path / "equity"
    root.mkdir()
    st = tmp_path / "stg"
    trees = [
        make_stage_tree(st, "stg_listing_daily", [
            *({"ticker": "K00010", "date": d, "market": "KOSPI", "sect_tp": "",
               "sect_available": False} for d in TD),
            *({"ticker": "Q00020", "date": d, "market": "KOSDAQ",
               "sect_tp": ADMIN_SECT if d in TD[3:5] else "벤처기업부",
               "sect_available": True} for d in TD[:9]),
            *({"ticker": "D00040", "date": d, "market": "KOSPI", "sect_tp": "",
               "sect_available": False} for d in TD)], partition_class="date_axis"),
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
        make_stage_tree(st, "stg_price_daily", [
            *({"ticker": "K00010", "date": d, "volume_shr": 0 if d == TD[1] else 100}
              for d in TD),
            *({"ticker": "Q00020", "date": d, "volume_shr": 0 if d in TD[5:9] else 100}
              for d in TD[:9]),
            *({"ticker": "D00040", "date": d, "volume_shr": 0 if d in TD[8:] else 100}
              for d in TD)], partition_class="date_axis"),
        make_stage_tree(st, "stg_etf_price_daily", [
            {"ticker": "E00030", "date": d, "volume_shr": 100} for d in TD]),
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
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps([{"case": "k_td1", "key": {"date": "2020-01-06", "ticker": "K00010"},
                               "column": "status", "expect": "suspended", "source": "hand"}]),
                  encoding="utf-8")
    bl = Baseline({UNIVERSE.name: {"admin_window_td": 3}})
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


def test_해제_공시가_거래보다_먼저면_해제일에_닫힌다(tmp_path: Path, make_stage_tree) -> None:
    """Q00020: td6 지정, td6~td9 무거래, td8 해제 → true td6·td7, false td8·td9."""
    r = _synthetic(tmp_path, make_stage_tree, d_end_reason="coverage_gap")
    assert r.ok and r.out_dir is not None
    assert _cell(r.out_dir, "Q00020", "halt_state") == [False] * 5 + [True, True, False, False]
    assert _cell(r.out_dir, "Q00020", "liquidation_window") == [False] * 7 + [True, True]
    assert _gate(r, "EG3_universe").metrics["n_halt_open_at_delist"] == 0


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
    assert built.out_dir is not None
    body = UNIVERSE.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    p = tmp_path / "universe_drop_one.sql"
    p.write_text(f"WITH base AS ({body}) SELECT * FROM base "
                 "WHERE NOT (ticker = '005930' AND date = DATE '2018-05-04')", encoding="utf-8")
    rule = EquityTable(**{**UNIVERSE.__dict__, "name": "universe_drop_one", "sql_path": p})
    r = build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], _seed_as(rule.name),
                          build_id="b_drop_one")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL and eg1.metrics["delta"] == -1
    assert _gate(r, "EG2").detail == "upstream_failed"


def test_status가_halt_state와_어긋나면_EG3_universe가_폐기한다(built: build.BuildResult,
                                                          tmp_path: Path) -> None:
    assert built.out_dir is not None
    body = UNIVERSE.sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    p = tmp_path / "universe_bad_status.sql"
    p.write_text(f"WITH base AS ({body}) SELECT * REPLACE ('listed' AS status) FROM base",
                 encoding="utf-8")
    rule = EquityTable(**{**UNIVERSE.__dict__, "name": "universe_bad_status", "sql_path": p})
    r = build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], _seed_as(rule.name),
                          build_id="b_bad_status")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG1").status is GateStatus.PASS
    eg3 = _gate(r, "EG3_universe")
    assert eg3.status is GateStatus.FAIL and eg3.metrics["n_status_halt_mismatch"] == 126
