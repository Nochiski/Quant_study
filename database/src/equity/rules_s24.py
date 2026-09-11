"""S24 커버 이력 슬라이스 — `coverage_daily` (사용자 요청 2026-09-10).

grain (`ticker`, `date`) · date_axis `year(date)`. 입력은 stage WISE 3표뿐이라 1단계 산출을 읽지
않는다. 산출식은 `sql/coverage_daily.sql` 하나이고 baseline 상수는 없다.

**왜 만드나** — "애널리스트가 몇 명 붙어 추정치를 내는지" 를 데이터 밀도 지표로 쓰려면 수준값
(`opinion_daily.analyst_count`)만으로는 부족하고 **언제부터 붙었나 / 언제 끊겼나** 가 필요하다.
그런데 원장의 커버 판정(`ws_coverage`)은 `upsert` 라 현재 상태 한 줄만 남는다(stage
`stg_wise_coverage.status_current`, `available=AVAILABLE_NONE`). 스냅샷 원문은 날짜축을 갖고
있으므로 equity 가 스냅샷 날짜별 커버 격자를 세워 이력을 복원한다.

**커버 판정은 수집기 정본을 옮긴다**(`backfill_wise.is_covered`, 09-10 핫픽스 뒤 3개년 판정):
cF5001 의 EPS·매출 추정 또는 목표주가 중 하나라도 값이 있으면 covered. stage 에서 그 세 신호는
`stg_consensus_monthly.consensus`(metric eps·revenue)와 `target_price_krw` 이고, 파싱 불능
(`metric='parse_failed'`)도 covered 로 센다 — 오판 비용이 비대칭(false-none = 영구 결측)이라
수집기가 그렇게 두는 것과 같은 규약이다. 절단본에서 이 술어는 `status_current` 7종목을 전건
재현한다(covered 5 · none 2).

**이력 컬럼은 전부 PIT 누적**이다 — `first_covered_date`·`last_covered_date`·`streak_days`·
`first_estimate_month` 는 `date` 이하만 본다. 종목별 상수로 실으면 뒤에 일어난 커버 상실을 과거
행이 이미 아는 look-ahead 가 된다(`disclosure_version` 이 `has_correction` 을 두지 않는 것과 같은
이유, DEFECT-E01).

`first_estimate_month` 는 **달 단위 축**이다. 우리 스냅샷 이력은 2026-09-01 부터라 그 이전의 커버
시작일을 날짜로 말할 수 없고, 월별 컨센서스 시계열(`obs_date`)의 첫 non-null 달이 대신 답한다.
기준이 다르므로 `first_estimate_basis` 로 그 사실을 기계가 읽게 한다('monthly' | 'none').

테이블 특화 술어(`extra_gates`):
  EG3_coverage_daily — 폐기형: 어휘 폐쇄(`first_estimate_basis`)·ticker 폭(EG3-P07)·날짜 범위
                       (스냅샷 축 밖 0)·격자 양방향 일치·커버 판정 재계산 일치·이력 컬럼 PIT
                       불변식(커버일 ≤ date, first ≤ last, 무커버 행 streak 0)·`analyst_count`
                       원값 보존. 기록형: 커버율·날짜별 커버 수·streak 분포·`analyst_count`
                       분위·`stg_wise_coverage.status_current` 대조.

**S19 `dataset_profile` 에는 아직 안 올린다** — `rules_s19.SOURCE_TABLES` 가 코드 고정 목록이고
그 파일은 이 작업의 소유 밖이다. `consensus.coverage_*` 소비 계약은 `EQUITY_FIELD_MAP.md` 끝
절에 적어 두었고, 프로파일 등재는 후속이다(`field_profiles=()` 인 이유).
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# ── 폐쇄 어휘 ────────────────────────────────────────────────────────────────
# `first_estimate_month` 의 기준. 'monthly' = 월별 컨센서스 시계열의 첫 non-null 달,
# 'none' = 그 종목에 값 있는 관측이 아직 하나도 없다(달을 말할 재료가 없다).
FIRST_ESTIMATE_BASIS_VOCAB: tuple[str, ...] = ("monthly", "none")
# 격리 사유 없음 — 이 표는 격자 산출뿐이라 버릴 원장 행이 없다.
REJECT_REASONS: tuple[str, ...] = ()

# ── `.sql` 과 공유하는 술어·축 (테스트가 글자로 대조한다) ─────────────────────
# 커버 판정. `backfill_wise.is_covered` 의 세 신호를 stage 컬럼으로 옮긴 것이다.
COVERED_PREDICATE = ("consensus IS NOT NULL OR target_price_krw IS NOT NULL "
                     "OR metric = 'parse_failed'")
# 종목 축 = WISE 요청 유니버스 ∪ 스냅샷에 나타난 종목. `stg_wise_coverage` 를 빼면 무커버 종목이
# 격자에서 사라져 커버율이 조용히 1 이 된다.
TICKER_AXIS_SQL = ("SELECT DISTINCT ticker FROM stg_wise_coverage\n"
                   "    UNION\n"
                   "    SELECT DISTINCT ticker FROM stg_consensus_monthly\n"
                   "    UNION\n"
                   "    SELECT DISTINCT ticker FROM stg_analyst_summary")
# 날짜 축 = 스냅샷을 찍은 날. 거래일 달력이 아니다(WISE 는 우리 수집 일정이 축이다).
DATE_AXIS_SQL = ("SELECT DISTINCT fetched_date AS date FROM stg_consensus_monthly\n"
                 "    UNION\n"
                 "    SELECT DISTINCT fetched_date AS date FROM stg_analyst_summary")

# 기록형 분포에 실을 날짜 표본 상한 — `_meta.json` 이 부풀지 않게 자른다.
_DATE_SAMPLE = 40


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def grid_sql() -> str:
    """격자를 stage 뷰에서 다시 만든다 — 산출을 읽지 않으므로 EG1 우변·EG3 대조가 독립이다."""
    return (f"SELECT t.ticker, s.date FROM ({TICKER_AXIS_SQL}) t "
            f"CROSS JOIN ({DATE_AXIS_SQL}) s")


def covered_sql() -> str:
    """그날 커버였던 (ticker, date) — `.sql` `cov` CTE 와 같은 술어를 한 곳에서 만든다."""
    return ("SELECT ticker, fetched_date AS date FROM stg_consensus_monthly "
            f"WHERE {COVERED_PREDICATE} GROUP BY ticker, fetched_date")


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s10 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


# ── EG3_coverage_daily ───────────────────────────────────────────────────────

def eg3_coverage_daily(ctx: EquityGateContext) -> GateResult:
    """어휘·티커 폭·날짜 범위·격자·커버 재계산·이력 PIT 불변식. 산출식을 재계산하지 않는다.

    폐기형은 **입력에서 독립으로 다시 셀 수 있는 축**에만 건다(GATES §1). 커버 판정은 stage 에서
    다시 계산해 대조하고(항진명제가 아니다 — `.sql` 은 격자를 거쳐 오고 여기는 원표를 직접 본다),
    이력 컬럼은 "그 날짜를 넘어서는 값이 없다" 는 PIT 불변식으로만 본다.
    """
    v = _q(ctx.out_view)
    grid = grid_sql()
    cov = covered_sql()

    # ① 어휘 폐쇄 + 키 폭 + 날짜 범위(스냅샷 축 밖 0)
    n_basis_vocab, n_basis_mismatch, n_ticker_bad, n_date_outside = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE first_estimate_basis IS NULL
               OR first_estimate_basis NOT IN ({_vocab_sql(FIRST_ESTIMATE_BASIS_VOCAB)})),
          (SELECT count(*) FROM {v}
            WHERE (first_estimate_month IS NOT NULL) <> (first_estimate_basis = 'monthly')),
          (SELECT count(*) FROM {v}
            WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
               OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v} c
            WHERE NOT EXISTS (SELECT 1 FROM ({DATE_AXIS_SQL}) s WHERE s.date = c.date))""")

    # ② 격자 양방향 — stage 뷰에서 격자를 다시 만들어 anti-join
    n_grid_missing, n_grid_extra, n_grid = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM ({grid}) g
            WHERE NOT EXISTS (SELECT 1 FROM {v} c
                               WHERE c.ticker = g.ticker AND c.date = g.date)),
          (SELECT count(*) FROM {v} c
            WHERE NOT EXISTS (SELECT 1 FROM ({grid}) g
                               WHERE g.ticker = c.ticker AND g.date = c.date)),
          (SELECT count(*) FROM ({grid}))""")

    # ③ 커버 판정 재계산 양방향 + `analyst_count` 원값 보존
    n_covered_missing, n_covered_extra, n_analyst_mismatch = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM ({cov}) x
            WHERE NOT EXISTS (SELECT 1 FROM {v} c
                               WHERE c.ticker = x.ticker AND c.date = x.date AND c.covered)),
          (SELECT count(*) FROM {v} c WHERE c.covered
             AND NOT EXISTS (SELECT 1 FROM ({cov}) x
                              WHERE x.ticker = c.ticker AND x.date = c.date)),
          (SELECT count(*) FROM {v} c
             LEFT JOIN stg_analyst_summary a
                    ON a.ticker = c.ticker AND a.fetched_date = c.date
            WHERE c.analyst_count IS DISTINCT FROM a.analyst_count)""")

    # ④ 이력 컬럼 PIT 불변식 — 그 날짜를 넘어서는 값이 없고, 커버 축과 어긋나지 않는다
    (n_future_cover, n_first_after_last, n_covered_bad_dates, n_uncovered_streak,
     n_future_month) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE first_covered_date > date OR last_covered_date > date),
          (SELECT count(*) FROM {v} WHERE first_covered_date > last_covered_date),
          (SELECT count(*) FROM {v} WHERE covered
             AND (first_covered_date IS NULL OR last_covered_date IS DISTINCT FROM date)),
          (SELECT count(*) FROM {v} WHERE NOT covered AND streak_days <> 0),
          (SELECT count(*) FROM {v} WHERE first_estimate_month > date)""")

    checks = {
        "n_basis_outside_vocab": int(str(n_basis_vocab)),
        "n_basis_month_mismatch": int(str(n_basis_mismatch)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
        "n_date_outside_snapshot_axis": int(str(n_date_outside)),
        "n_grid_missing": int(str(n_grid_missing)),
        "n_grid_extra": int(str(n_grid_extra)),
        "n_covered_missing": int(str(n_covered_missing)),
        "n_covered_extra": int(str(n_covered_extra)),
        "n_analyst_count_mismatch": int(str(n_analyst_mismatch)),
        "n_cover_date_after_date": int(str(n_future_cover)),
        "n_first_after_last_covered": int(str(n_first_after_last)),
        "n_covered_without_dates": int(str(n_covered_bad_dates)),
        "n_uncovered_with_streak": int(str(n_uncovered_streak)),
        "n_estimate_month_after_date": int(str(n_future_month)),
    }

    # ── 기록형 ──────────────────────────────────────────────────────────────
    n_ticker, n_date, n_covered, n_analyst = _row(ctx, f"""
        SELECT (SELECT count(DISTINCT ticker) FROM {v}),
               (SELECT count(DISTINCT date) FROM {v}),
               (SELECT count(*) FROM {v} WHERE covered),
               (SELECT count(*) FROM {v} WHERE analyst_count IS NOT NULL)""")
    by_date = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT date, count(*) FILTER (WHERE covered) FROM {v} "
        f"GROUP BY 1 ORDER BY 1 LIMIT {_DATE_SAMPLE}").fetchall()}
    streaks = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT streak_days, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()}
    months = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT first_estimate_month, count(DISTINCT ticker) FROM {v} "
        "WHERE first_estimate_month IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()}
    # `stg_wise_coverage` 는 이력이 없는 현재 상태 한 줄이다 — 그 종목의 `checked_date_current`
    # 와 같은 날짜의 격자 행만 비교한다. 다른 날과 비교하면 "상태가 그 뒤에 바뀌었다" 를 오차로
    # 읽는다. 기록형인 것은 원천이 비동기로 덮어쓰기 때문이지 이 표의 결함이 아니어서다.
    n_status_cmp, n_status_mismatch = _row(ctx, f"""
        SELECT count(*), count(*) FILTER (WHERE c.covered <> (w.status_current = 'covered'))
        FROM {v} c JOIN stg_wise_coverage w
          ON w.ticker = c.ticker AND w.checked_date_current = c.date""")
    n_rows = int(str(n_ticker)) * int(str(n_date))
    metrics: dict[str, object] = {
        "n_grid_cells": int(str(n_grid)),
        "n_ticker": int(str(n_ticker)),
        "n_snapshot_date": int(str(n_date)),
        "n_covered_cells": int(str(n_covered)),
        "coverage_rate": round(int(str(n_covered)) / n_rows, 6) if n_rows else None,
        "n_analyst_count_present": int(str(n_analyst)),
        "covered_by_date": by_date,
        "streak_days_counts": streaks,
        "first_estimate_month_tickers": months,
        "first_estimate_basis_vocab": list(FIRST_ESTIMATE_BASIS_VOCAB),
        "covered_predicate": COVERED_PREDICATE,
        # 원장 현재 상태와의 대조 — 같은 날짜만 센다(위 주석)
        "n_wise_status_compared": int(str(n_status_cmp)),
        "n_wise_status_mismatch": int(str(n_status_mismatch)),
    }
    return _result("EG3_coverage_daily", checks, metrics,
                   "어휘·격자·커버 재계산·이력 PIT 불변식 성립")


eg3_coverage_daily.gate_name = "EG3_coverage_daily"     # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

COVERAGE_DAILY = register(EquityTable(
    name="coverage_daily",
    grain=("ticker", "date"),
    # 순서 = `.sql` SELECT 순서. 커버 축 → 이력 축 → 밀도 축 → 달 축 → PIT 2.
    columns={"ticker": "VARCHAR", "date": "DATE",
             "covered": "BOOLEAN",
             "first_covered_date": "DATE", "last_covered_date": "DATE",
             "streak_days": "BIGINT",
             "analyst_count": "DECIMAL(5,0)",
             "first_estimate_month": "DATE", "first_estimate_basis": "VARCHAR",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    # `stg_wise_coverage` 는 종목 축(요청 유니버스)이자 EG3 기록형 대조축이다.
    inputs=("stg_consensus_monthly", "stg_analyst_summary", "stg_wise_coverage"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule=("column:date — WISE 화면 수집일(fetched_date). stage 가 lag_known=true 로 잰 "
                    "축이라 basis 'measured'"),
    # 좌변 = 격자 행수. 격리가 없으므로 우변은 종목 × 스냅샷 날짜 곱 그대로다.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=(f"SELECT (SELECT count(*) FROM ({TICKER_AXIS_SQL})) "
                 f"* (SELECT count(*) FROM ({DATE_AXIS_SQL}))"),
    sql_path=SQL_DIR / "coverage_daily.sql",
    input_columns={
        "stg_consensus_monthly": ("ticker", "fetched_date", "metric", "obs_date", "consensus",
                                  "target_price_krw"),
        "stg_analyst_summary": ("ticker", "fetched_date", "analyst_count"),
        "stg_wise_coverage": ("ticker", "status_current", "checked_date_current")},
    available_basis=("measured",),
    # 내용일 = 관측일 자체다(스냅샷 날짜). available_date 와 같은 축이라 EG2-P02 는 항상 성립한다.
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_coverage_daily,),
))

TABLES: tuple[EquityTable, ...] = (COVERAGE_DAILY,)

__all__ = ["COVERAGE_DAILY", "COVERED_PREDICATE", "DATE_AXIS_SQL",
           "FIRST_ESTIMATE_BASIS_VOCAB", "REJECT_REASONS", "TABLES", "TICKER_AXIS_SQL",
           "covered_sql", "grid_sql"]
