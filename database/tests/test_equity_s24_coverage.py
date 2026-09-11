"""S24 `coverage_daily` — stage 절단본 위 실제 `build_table` 왕복 · 부정 픽스처.

손계산 기대값은 `stg_consensus_monthly`·`stg_analyst_summary`·`stg_wise_coverage` 원자료를 직접
열어 확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  종목 축 7(`stg_wise_coverage` 7 = 스냅샷 표 7) × 스냅샷 날짜 2(2026-09-01·02) = 격자 14 ·
  커버 5종목(000660·003540·005930·161890·247540) × 2일 = 10 · 무커버 2종목(036220·101970) ·
  `analyst_count` 있는 셀 10(무커버 2종목은 c1010001 을 안 받는다) ·
  월별 시계열 첫 non-null 달은 커버 5종목 전부 2025-08.
이 표는 stage 입력만 읽으므로 상류 equity 빌드가 필요 없다.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from equity import build, rules_s24
from equity.baseline import Baseline
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
COVERAGE_FIXTURES = Path(rules_s24.__file__).parent / "fixtures" / "coverage_daily.json"
WISE_TABLES = ("stg_consensus_monthly", "stg_analyst_summary", "stg_wise_coverage")

COVERAGE = rules_s24.COVERAGE_DAILY
# 격리 사유가 없는 표라 EG7 은 비율 0 으로 통과하고 baseline 상수도 필요 없다.
SEED = Baseline({})
GATE_ORDER = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_coverage_daily", "EG4", "EG5a"]

N_TICKER = 7
N_DATE = 2
N_GRID = N_TICKER * N_DATE                     # 14
DATES = (date(2026, 9, 1), date(2026, 9, 2))
COVERED_TICKERS = {"000660", "003540", "005930", "161890", "247540"}
UNCOVERED_TICKERS = {"036220", "101970"}
N_COVERED_CELLS = len(COVERED_TICKERS) * N_DATE       # 10
FIRST_ESTIMATE_MONTH = date(2025, 8, 1)
# (ticker, date) → analyst_count (stg_analyst_summary 손계산)
ANALYST = {("000660", DATES[0]): 24, ("000660", DATES[1]): 23,
           ("003540", DATES[0]): 3, ("003540", DATES[1]): 3,
           ("005930", DATES[0]): 24, ("005930", DATES[1]): 22,
           ("161890", DATES[0]): 19, ("161890", DATES[1]): 19,
           ("247540", DATES[0]): 14, ("247540", DATES[1]): 14}


def _build(equity_root: Path, rule: EquityTable | None = None,
           stage_root: Path | None = None, build_id: str = "b_s24") -> build.BuildResult:
    return build.build_table(rule or COVERAGE, stage_root or STAGE_SLICE, equity_root, SEED,
                             build_id=build_id)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    """정상 왕복 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    return _build(tmp_path_factory.mktemp("s24") / "equity")


@pytest.fixture
def chain(tmp_path: Path) -> Path:
    return tmp_path / "equity"


def _variant(tmp_path: Path, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**COVERAGE.__dict__, "name": name, "sql_path": p})


def _body() -> str:
    return COVERAGE.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW cv AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        for t in WISE_TABLES:
            con.execute(f"CREATE OR REPLACE TEMP VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{STAGE_SLICE / t}/**/*.parquet', hive_partitioning=true, "
                        "union_by_name=true)")
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
    assert r.n_rows == N_GRID and r.n_reject == 0
    eg1 = _metrics(r, "EG1")
    assert eg1["lhs"] == N_GRID and eg1["rhs"] == N_GRID and eg1["delta"] == 0
    assert set(r.inputs) == set(WISE_TABLES)


def test_격자는_요청_유니버스_곱_스냅샷_날짜다(built: build.BuildResult) -> None:
    """무커버 종목이 격자에 남아야 커버율이 뜻을 갖는다(GATES §5-C6 생존편향의 반대 방향)."""
    r = built
    assert r.out_dir is not None
    got = _query(r.out_dir, "SELECT count(DISTINCT ticker), count(DISTINCT date), count(*) "
                            "FROM cv")
    assert got == [(N_TICKER, N_DATE, N_GRID)]
    tickers = {str(t) for (t,) in _query(r.out_dir, "SELECT DISTINCT ticker FROM cv")}
    assert tickers == COVERED_TICKERS | UNCOVERED_TICKERS
    dates = tuple(d for (d,) in _query(r.out_dir, "SELECT DISTINCT date FROM cv ORDER BY 1"))
    assert dates == DATES
    m = _metrics(r, "EG3_coverage_daily")
    assert m["n_grid_missing"] == 0 and m["n_grid_extra"] == 0
    assert m["n_date_outside_snapshot_axis"] == 0


def test_커버_판정은_원장_현재_상태를_재현한다(built: build.BuildResult) -> None:
    """`covered` 술어(= `backfill_wise.is_covered` 세 신호)가 `stg_wise_coverage` 7종목을 전건
    맞힌다. 원장 쪽은 이력이 없는 현재 상태 한 줄이라 `checked_date_current` 와 같은 날만 센다."""
    r = built
    assert r.out_dir is not None
    covered = {str(t) for (t,) in _query(r.out_dir, "SELECT DISTINCT ticker FROM cv "
                                                    "WHERE covered")}
    assert covered == COVERED_TICKERS
    assert _query(r.out_dir, "SELECT count(*) FROM cv WHERE covered") == [(N_COVERED_CELLS,)]
    assert _query(r.out_dir, """
        SELECT count(*) FROM cv c JOIN stg_wise_coverage w USING (ticker)
        WHERE w.checked_date_current = c.date
          AND c.covered <> (w.status_current = 'covered')""") == [(0,)]
    m = _metrics(r, "EG3_coverage_daily")
    assert m["n_covered_missing"] == 0 and m["n_covered_extra"] == 0
    assert m["n_wise_status_compared"] == N_TICKER      # checked_date 가 09-02 인 7종목
    assert m["n_wise_status_mismatch"] == 0
    assert m["n_covered_cells"] == N_COVERED_CELLS


def test_이력_컬럼은_그_날짜까지만_본다(built: build.BuildResult) -> None:
    """PIT — 커버일·연속일수는 `date` 이하 누적이다. 종목 상수로 실으면 뒤에 일어난 커버 상실을
    과거 행이 미리 아는 look-ahead 가 된다(DEFECT-E01 과 같은 부류)."""
    r = built
    assert r.out_dir is not None
    rows = _query(r.out_dir, "SELECT ticker, date, covered, first_covered_date, "
                             "last_covered_date, streak_days FROM cv ORDER BY ticker, date")
    for ticker, d, covered, first, last, streak in rows:
        tk, day = str(ticker), d
        if tk in UNCOVERED_TICKERS:
            assert (covered, first, last, streak) == (False, None, None, 0), tk
            continue
        assert covered is True
        assert first == DATES[0] and last == day
        assert streak == (1 if day == DATES[0] else 2)
    m = _metrics(r, "EG3_coverage_daily")
    for key in ("n_cover_date_after_date", "n_first_after_last_covered",
                "n_covered_without_dates", "n_uncovered_with_streak",
                "n_estimate_month_after_date"):
        assert m[key] == 0, key


def test_analyst_count는_stage_값_그대로이고_없으면_NULL(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    got = {(str(t), d): (None if n is None else int(n)) for t, d, n in _query(
        r.out_dir, "SELECT ticker, date, analyst_count FROM cv")}
    assert {k: v for k, v in got.items() if v is not None} == ANALYST
    assert all(got[(tk, d)] is None for tk in UNCOVERED_TICKERS for d in DATES)
    assert _metrics(r, "EG3_coverage_daily")["n_analyst_count_mismatch"] == 0
    assert _metrics(r, "EG3_coverage_daily")["n_analyst_count_present"] == len(ANALYST)


def test_first_estimate_month는_월_단위이고_basis가_기준을_밝힌다(
        built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    rows = _query(r.out_dir, "SELECT ticker, first_estimate_month, first_estimate_basis "
                             "FROM cv ORDER BY ticker, date")
    for ticker, month, basis in rows:
        if str(ticker) in UNCOVERED_TICKERS:
            assert month is None and str(basis) == "none"
        else:
            assert month == FIRST_ESTIMATE_MONTH and str(basis) == "monthly"
    # 달 축이므로 언제나 그 달 1일이다
    assert _query(r.out_dir, "SELECT count(*) FROM cv WHERE first_estimate_month IS NOT NULL "
                             "AND day(first_estimate_month) <> 1") == [(0,)]
    m = _metrics(r, "EG3_coverage_daily")
    assert m["n_basis_outside_vocab"] == 0 and m["n_basis_month_mismatch"] == 0
    assert m["first_estimate_month_tickers"] == {str(FIRST_ESTIMATE_MONTH): len(COVERED_TICKERS)}


def test_available_date는_date이고_basis는_measured(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM cv WHERE available_date <> date "
                             "OR available_basis <> 'measured'") == [(0,)]
    assert _gate(r, "EG2").status is GateStatus.PASS


def test_컬럼_선언순서가_산출과_같다(built: build.BuildResult) -> None:
    r = built
    assert r.out_dir is not None
    got = [str(c[0]) for c in _query(r.out_dir, "DESCRIBE cv")]
    assert [c for c in got if c not in ("year", "v")] == list(COVERAGE.columns)


def test_술어는_rules_선언과_SQL_리터럴이_같다() -> None:
    """커버 술어·두 축이 `.sql`·EG1 우변·EG3 재계산의 단일 정의라는 것을 글자로 확인한다."""
    body = _body()
    assert rules_s24.COVERED_PREDICATE in body
    assert rules_s24.TICKER_AXIS_SQL in body
    assert rules_s24.DATE_AXIS_SQL in body
    assert rules_s24.TICKER_AXIS_SQL in COVERAGE.eg1_rhs_sql
    assert rules_s24.DATE_AXIS_SQL in COVERAGE.eg1_rhs_sql
    assert rules_s24.COVERED_PREDICATE in rules_s24.covered_sql()
    assert COVERAGE.reject_reasons == ()          # 격리가 없는 격자 산출이다
    assert COVERAGE.field_profiles == ()          # S19 등재는 후속(rules_s19.SOURCE_TABLES)


def test_재빌드는_같은_파티션_해시를_낸다(chain: Path) -> None:
    """EG5a — 같은 inputs·같은 규칙 판본이면 파티션 content_hash 전량 동일."""
    first = _build(chain, build_id="b_s24_a")
    assert first.ok, [(g.name, g.status.value, g.detail) for g in first.gates]
    assert _gate(first, "EG5a").status is GateStatus.SKIP
    second = _build(chain, build_id="b_s24_b")
    assert second.ok, [(g.name, g.status.value, g.detail) for g in second.gates]
    assert _gate(second, "EG5a").status is GateStatus.PASS
    assert second.content_hash == first.content_hash


def test_CLI_레지스트리에_등록된다() -> None:
    from equity import __main__ as cli
    assert cli.RULES["coverage_daily"] is COVERAGE


# ── 부정 픽스처 (GATES §7-5) ─────────────────────────────────────────────────

def test_무커버_종목을_격자에서_빼면_EG1이_폐기한다(chain: Path, tmp_path: Path) -> None:
    """종목 축에서 `stg_wise_coverage` 를 빼도 절단본은 행수가 같다(스냅샷 표에 7종목이 다 있다)
    — 그래서 더 센 변종을 쓴다: 커버된 종목만 격자에 세우면 무커버 2종목이 사라진다."""
    sql = _body().replace(rules_s24.TICKER_AXIS_SQL,
                          "SELECT DISTINCT ticker FROM stg_consensus_monthly\n"
                          "    WHERE consensus IS NOT NULL")
    assert sql != _body()
    r = _build(chain, _variant(tmp_path, "coverage_covered_only", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL
    assert eg1.metrics["delta"] == -len(UNCOVERED_TICKERS) * N_DATE


def test_커버_술어를_행_존재로_늦추면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    """무커버 종목도 cF5001 3콜을 받아 stage 에 **전값 NULL 행**을 남긴다 — "행이 있으면 커버"
    로 읽으면 036220·101970 이 커버로 뒤집힌다. 절단본 실측이 그 함정의 증거다."""
    sql = _body().replace(f"    WHERE {rules_s24.COVERED_PREDICATE}\n", "")
    assert sql != _body()
    r = _build(chain, _variant(tmp_path, "coverage_row_exists", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_coverage_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_covered_extra"] == len(UNCOVERED_TICKERS) * N_DATE
    assert g.metrics["n_wise_status_mismatch"] == len(UNCOVERED_TICKERS)


def test_이력을_전구간_집계로_만들면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    """창을 `UNBOUNDED FOLLOWING` 으로 열면 첫날 행이 이미 마지막 커버일을 안다 — look-ahead."""
    sql = _body().replace("ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
                          "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING")
    assert sql != _body()
    r = _build(chain, _variant(tmp_path, "coverage_lookahead", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_coverage_daily")
    assert g.status is GateStatus.FAIL
    # 커버 5종목의 09-01 행이 09-02 를 last_covered_date 로 들고 있다
    assert g.metrics["n_cover_date_after_date"] == len(COVERED_TICKERS)


def test_무커버_행에_연속일수를_굽으면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    sql = _body().replace("         - CASE WHEN r.gap_seq > 0 THEN 1 ELSE 0 END AS BIGINT)",
                          "         AS BIGINT)")
    assert sql != _body()
    r = _build(chain, _variant(tmp_path, "coverage_bad_streak", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_coverage_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_uncovered_with_streak"] == len(UNCOVERED_TICKERS) * N_DATE


def test_analyst_count를_0으로_채우면_EG3가_폐기한다(chain: Path, tmp_path: Path) -> None:
    """결측은 결측 — 무커버 종목의 기관 수를 0 으로 구우면 밀도 지표가 조용히 거짓이 된다."""
    sql = _body().replace("    r.analyst_count,\n",
                          "    coalesce(r.analyst_count, 0) AS analyst_count,\n")
    assert sql != _body()
    r = _build(chain, _variant(tmp_path, "coverage_zero_fill", sql))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_coverage_daily")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_analyst_count_mismatch"] == len(UNCOVERED_TICKERS) * N_DATE


def test_스냅샷이_한_날뿐이면_연속일수가_1에서_시작한다(chain: Path, tmp_path: Path) -> None:
    """날짜 축이 하나뿐인 stage(첫 실행 직후)에서도 격자·이력이 성립한다 — 회귀 방어."""
    root = tmp_path / "stage_one_day"
    for table in WISE_TABLES:
        shutil.copytree(STAGE_SLICE / table, root / table)
    for table in ("stg_consensus_monthly", "stg_analyst_summary"):
        for part in (root / table).glob("v=*/year=*/*.parquet"):
            con = duckdb.connect()
            try:
                con.execute(f"CREATE TEMP TABLE t AS SELECT * FROM read_parquet('{part}') "
                            f"WHERE fetched_date = DATE '{DATES[0].isoformat()}'")
                con.execute(f"COPY t TO '{part}' (FORMAT PARQUET)")
            finally:
                con.close()
    # 골든 픽스처는 표 전체를 대상으로 하므로 09-02 행이 없는 이 stage 에서는 09-01 것만 쓴다.
    fx = [e for e in json.loads(COVERAGE_FIXTURES.read_text(encoding="utf-8"))
          if e["key"]["date"] == DATES[0].isoformat()]
    fx_path = tmp_path / "coverage_one_day.json"
    fx_path.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
    r = build.build_table(COVERAGE, root, chain, SEED, build_id="b_s24_one",
                          fixtures_path=fx_path)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == N_TICKER
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM cv WHERE covered AND streak_days <> 1") == [(0,)]
    assert _query(r.out_dir, "SELECT analyst_count FROM cv WHERE ticker = '005930'") == [
        (Decimal(24),)]
