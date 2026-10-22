"""S26 WISE 3표(`consensus_matrix` · `consensus_annual` · `fin_ratio_wise`) — stage 절단본 위 실제 `build_table` 왕복
(플랜 `2026-10-22-equity-wise-fields` T1).

손계산 기대값은 stage 절단본 원자료를 직접 열어 확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  stg_consensus_matrix 1,350행 / 5종목 / 수집일 2026-09-01·02 / 결산기 202612·202712·202812
  stg_consensus_annual 70행
  stg_fin_wise cF4002 360행 × 라벨 6칸 전부 있음 → 2,160칸
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s19, rules_s26
from equity.baseline import Baseline
from equity.gates import GateStatus
from equity.model import RULES, EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
FIXTURES = Path(rules_s26.__file__).parent / "fixtures"
N_ROWS = {"consensus_matrix": 1350, "consensus_annual": 70, "fin_ratio_wise": 2160}


def _build(rule: EquityTable, equity_root: Path, *, fixtures: str | None = None) -> build.BuildResult:
    """운영 픽스처(`src/equity/fixtures`)로 짓는다 — 005930 2026-09-02 행은 절단본과 서버 판이 같은 원문이다."""
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
