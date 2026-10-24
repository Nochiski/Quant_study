"""S26 WISE 3표(`consensus_matrix` · `consensus_annual` · `fin_ratio_wise`) — stage 절단본 위 실제 `build_table` 왕복
(플랜 `2026-10-22-equity-wise-fields` T1).

손계산 기대값은 stage 절단본 원자료를 직접 열어 확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  stg_consensus_matrix 1,350행 / 5종목 / 수집일 2026-09-01·02 / 결산기 202612·202712·202812
  stg_consensus_annual 70행
  stg_fin_wise cF4002 360행 × 라벨 6칸 전부 있음 → 2,160칸
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s19, rules_s26
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import RULES, EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
FIXTURES = Path(rules_s26.__file__).parent / "fixtures"
N_ROWS = {"consensus_matrix": 1350, "consensus_annual": 70, "fin_ratio_wise": 2160}


SEED = Baseline({**load(Path(rules_s01.__file__).parent / "baseline_seed_s01.json").data,
                 **load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data})


def _calendar(equity_root: Path) -> None:
    """S26 은 가용일을 거래일 달력으로 센다 — 달력을 먼저 짓는다(절단본 달력은 2026-08-20 까지)."""
    if (equity_root / rules_s02.TRADING_CALENDAR.name).exists():
        return
    r = build.build_table(rules_s02.TRADING_CALENDAR, STAGE_SLICE, equity_root, SEED, build_id="b_s26_cal")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]


def _build(rule: EquityTable, equity_root: Path, *, fixtures: str | None = None) -> build.BuildResult:
    """운영 픽스처(`src/equity/fixtures`)로 짓는다 — 005930 2026-09-02 행은 절단본과 서버 판이 같은 원문이다."""
    _calendar(equity_root)
    name = fixtures or rule.name
    return build.build_table(rule, STAGE_SLICE, equity_root, Baseline({}), build_id=f"b_s26_{rule.name}",
                             fixtures_path=FIXTURES / f"{name}.json")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, build.BuildResult]]:
    tmp = tmp_path_factory.mktemp("s26")
    eq = tmp / "equity"
    return eq, {rule.name: _build(rule, eq) for rule in rules_s26.TABLES}


def _con(equity_root: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for rule in rules_s26.TABLES:
        con.execute(f"CREATE OR REPLACE TEMP VIEW {rule.name} AS SELECT * FROM read_parquet("
                    f"'{equity_root / rule.name}/**/*.parquet', hive_partitioning=false, union_by_name=true)")
    return con


def _variant(tmp_path: Path, rule: EquityTable, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 안 건드린다."""
    p = tmp_path / f"{rule.name}_variant.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rule.__dict__, "name": f"{rule.name}_variant", "sql_path": p})


def test_registered_in_build_order_and_profiled() -> None:
    order = [ln.strip() for ln in (Path(__file__).parent.parent / "scripts" / "equity_order.txt")
             .read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    for rule in rules_s26.TABLES:
        assert RULES[rule.name] is rule
        assert order.index(rule.name) < order.index("dataset_profile")
        assert rule.name in rules_s19.SOURCE_TABLES          # dataset_profile 이 필드 선언을 모은다


def test_build_passes_all_gates_with_stage_row_counts(built: tuple[Path, dict[str, build.BuildResult]]) -> None:
    equity_root, results = built
    for name, r in results.items():
        assert r.ok, (name, [(g.name, g.detail) for g in r.gates if g.status is GateStatus.FAIL])
        assert next(g for g in r.gates if g.name == f"EG3_{name}").status is GateStatus.PASS
    con = _con(equity_root)
    for name, n in N_ROWS.items():
        assert con.execute(f"SELECT count(*) FROM {name}").fetchone() == (n,)
    con.close()


def test_matrix_fy_rank_orders_periods_after_the_fetch_month(built: tuple[Path, dict[str, build.BuildResult]]) -> None:
    con = _con(built[0])
    rows = con.execute("SELECT DISTINCT target_period, fy_rank FROM consensus_matrix "
                       "WHERE ticker = '005930' AND fetched_date = DATE '2026-09-02' ORDER BY 1").fetchall()
    assert rows == [("202612", 1), ("202712", 2), ("202812", 3)]     # 수집 달 202609 이후 — 지난 기 없음
    got = con.execute("SELECT lookback, value FROM consensus_matrix WHERE ticker = '005930' "
                      "AND fetched_date = DATE '2026-09-02' AND acc_cd = '121500' AND fy_rank = 1 "
                      "ORDER BY lookback_idx").fetchall()
    assert got == [("current", Decimal("3933479.000000")), ("1w", Decimal("3917860.000000")),
                   ("1m", Decimal("3917860.000000")), ("3m", Decimal("3644258.000000")),
                   ("1y", Decimal("462063.000000"))]
    con.close()


def test_fin_ratio_spreads_annual_columns_into_periods(built: tuple[Path, dict[str, build.BuildResult]]) -> None:
    con = _con(built[0])
    rows = con.execute("SELECT slot, period, is_estimate, value FROM fin_ratio_wise WHERE ticker = '005930' "
                       "AND fetched_date = DATE '2026-09-02' AND accode = '382100' AND slot >= 5 "
                       "ORDER BY slot").fetchall()
    assert rows == [(5, "202512", False, Decimal("18.267500")), (6, "202612", True, Decimal("5.399410"))]
    con.close()


@pytest.mark.parametrize("profile", [*rules_s26.FIELDS_MATRIX, *rules_s26.FIELDS_ANNUAL],
                         ids=lambda p: p.field_id)
def test_grid_field_filter_selects_at_most_one_row_per_ticker_and_fetch(
        built: tuple[Path, dict[str, build.BuildResult]], profile) -> None:
    """워크벤치는 이 필드들을 (종목, 수집일) 격자로 읽는다 — 행 필터가 한 칸에 두 행을 남기면 값이 흔들린다."""
    con = _con(built[0])
    table = "consensus_matrix" if profile in rules_s26.FIELDS_MATRIX else "consensus_annual"
    n_cells, n_dup = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE n > 1) FROM (SELECT ticker, fetched_date, count(*) AS n "
        f"FROM {table} WHERE {profile.row_filter} GROUP BY 1, 2)").fetchone()
    assert n_cells > 0 and n_dup == 0
    con.close()


def test_field_ids_match_the_workbench_names() -> None:
    ids = [p.field_id for p in (*rules_s26.FIELDS_MATRIX, *rules_s26.FIELDS_ANNUAL, *rules_s26.FIELDS_RATIO)]
    assert ids == [
        "wise.consensus_op", "wise.consensus_op_1w_ago", "wise.consensus_op_1m_ago", "wise.consensus_op_3m_ago",
        "wise.consensus_ni", "wise.consensus_ni_1w_ago", "wise.consensus_ni_1m_ago", "wise.consensus_ni_3m_ago",
        "wise.fy0_op_estimate", "wise.fy0_ni_estimate", "wise.per", "wise.pbr", "wise.dividend_yield"]
    assert all(p.recommended_lag_sessions == 1 for p in
               (*rules_s26.FIELDS_MATRIX, *rules_s26.FIELDS_ANNUAL, *rules_s26.FIELDS_RATIO))


def _replace_once(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise AssertionError(f"variant anchor must appear once: anchor={old!r} count={sql.count(old)}")
    return sql.replace(old, new)


def test_eg3_matrix_rejects_a_missing_rank_on_an_upcoming_period(tmp_path: Path) -> None:
    sql = rules_s26.CONSENSUS_MATRIX.sql_path.read_text(encoding="utf-8")
    sql = _replace_once(sql, "THEN dense_rank()", "THEN NULL + dense_rank()")
    r = _build(_variant(tmp_path, rules_s26.CONSENSUS_MATRIX, sql), tmp_path / "equity",
               fixtures="consensus_matrix")
    gate = next(g for g in r.gates if g.name == "EG3_consensus_matrix")
    assert gate.status is GateStatus.FAIL and gate.metrics["n_fy_rank_inconsistent"] == N_ROWS["consensus_matrix"]


def test_eg3_annual_rejects_a_period_kind_outside_vocab(tmp_path: Path) -> None:
    sql = rules_s26.CONSENSUS_ANNUAL.sql_path.read_text(encoding="utf-8")
    sql = _replace_once(sql, "    period_kind,\n",
                        "    CASE WHEN period_kind = 'E' THEN 'F' ELSE period_kind END AS period_kind,\n")
    r = _build(_variant(tmp_path, rules_s26.CONSENSUS_ANNUAL, sql), tmp_path / "equity",
               fixtures="consensus_annual")
    gate = next(g for g in r.gates if g.name == "EG3_consensus_annual")
    assert gate.status is GateStatus.FAIL and gate.metrics["n_period_kind_outside_vocab"] > 0


def test_slice_rows_keep_the_fetch_date_beyond_the_calendar(built: tuple[Path, dict[str, build.BuildResult]]) -> None:
    """절단본 수집(2026-09-01·02 12시대 KST)은 달력 끝(2026-08-20) 뒤라 당기지 않고 수집 시각의 날짜 그대로다."""
    con = _con(built[0])
    for name in N_ROWS:
        rows = con.execute(f"SELECT count(*) FILTER (WHERE available_date <> fetched_date), "
                           f"count(DISTINCT available_basis), min(available_basis) FROM {name}").fetchone()
        assert rows == (0, 1, "derived"), name
    con.close()


_CAL = ("2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22")
# (fetched_date, fetched_at_utc, 기대 가용일) — 랙 1 이라 가용일 다음 세션부터 쓴다
_PIT_CASES = (
    ("2026-10-15", "2026-10-15 09:05:05", "2026-10-15"),   # 18:05 KST 장 마감 뒤 → 그 세션 지식, 10-16 부터
    ("2026-10-16", "2026-10-16 06:52:15", "2026-10-16"),   # 15:52 KST 장 마감 뒤 → 10-19 부터
    ("2026-10-19", "2026-10-19 02:33:48", "2026-10-19"),   # 11:33 KST 장중 → 다음 세션(10-20)부터, 보수
    ("2026-10-20", "2026-10-19 21:00:17", "2026-10-19"),   # 06:00 KST 장 전 → 직전 세션 지식, 그날(10-20) 쓴다
    ("2026-10-17", "2026-10-16 21:00:03", "2026-10-16"),   # 토요일 06:00 → 금요일 지식, 월요일(10-19) 쓴다
    ("2026-10-21", None, "2026-10-21"),                     # 수집 시각 없음 → 수집일(보수)
    ("2026-10-24", "2026-10-23 21:00:00", "2026-10-23"),   # 달력 끝(10-22) 뒤 → 당기지 않는다
)


def test_pit_rule_counts_the_knowledge_session_from_the_fetch_time() -> None:
    """`.sql` 을 손 입력으로 직접 돌린다 — 장 전 수집은 직전 세션, 그 밖은 수집 시각의 세션(달력 안에서)."""
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE trading_calendar AS SELECT CAST(d AS DATE) AS date FROM "
                f"(VALUES {', '.join(f'({d!r})' for d in _CAL)}) t(d)")
    rows = ", ".join(
        f"('005930', DATE '{fd}', '202612', '121500', '1', 'current', '영업이익(억원)', NULL, 1.0, "
        f"{'NULL' if at is None else repr(at)}::TIMESTAMP)" for fd, at, _ in _PIT_CASES)
    con.execute("CREATE TEMP TABLE stg_consensus_matrix AS SELECT * FROM (VALUES " + rows + ") t(ticker, fetched_date, "
                "target_period, acc_cd, lookback_idx, lookback, acc_nm, base_date, value, fetched_at_utc)")
    got = con.execute(f"SELECT fetched_date, available_date, available_basis FROM "
                      f"({rules_s26.CONSENSUS_MATRIX.sql_path.read_text(encoding='utf-8')}) ORDER BY fetched_date").fetchall()
    want = sorted((date.fromisoformat(fd), date.fromisoformat(av), "measured" if at is None else "derived")
                  for fd, at, av in _PIT_CASES)
    assert got == want
    con.close()


def test_eg3_rejects_a_pit_rule_that_pulls_past_the_calendar_end(tmp_path: Path) -> None:
    """세 `.sql` 이 같은 가용일 규칙인지는 EG3 의 독립 재계산이 본다 — 달력 끝을 넘어 당기는 변종을 잡는다."""
    sql = rules_s26.CONSENSUS_ANNUAL.sql_path.read_text(encoding="utf-8")
    sql = _replace_once(sql, "                WHEN k.known_date > c.last_session THEN k.known_date\n", "")
    r = _build(_variant(tmp_path, rules_s26.CONSENSUS_ANNUAL, sql), tmp_path / "equity", fixtures="consensus_annual")
    gate = next(g for g in r.gates if g.name == "EG3_consensus_annual")
    assert gate.status is GateStatus.FAIL and gate.metrics["n_available_recompute_mismatch"] == N_ROWS["consensus_annual"]
