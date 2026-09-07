"""S17 컨센서스 슬라이스 — `consensus_daily` (DESIGN v1.2 §4-6 · GATES v1.0 §3 ⑲ · WORKFLOW §3-2).

grain (`ticker`, `obs_month`, `target_period`, `metric`, `src`) · 파티션 `year(obs_month)`
(**PIT 축 아님** — 절단은 `available_date` 로만 한다, DESIGN §2 파티션 표).

**이 테이블이 있는 이유**는 리비전이 덮어써지기 때문이다(메모리 "consensus-revision-db"): v3 원장은
2027E·2028E 컨센서스를 매일 덮어써 과거 관측을 지운다. 그래서 equity 는 관측점(월)마다 **최초
관측 한 행**만 남긴다 —
  wise(`stg_consensus_monthly`) : 같은 (ticker, obs_month, target_period, metric) 을 여러
    `fetched_date` 가 덮어쓴다 → **min(fetched_date) 판본**(HANDOFF §3 "리비전 팩터는 (ticker, obs)
    의 min(fetched_date) 행이 PIT", EG6-P01). 절단본 실측: 같은 키에 09-01·09-02 두 판본이 있고
    값이 실제로 바뀐 키가 있다(000660 2026-08 202812 eps 458131.68 → 450128.71).
  v3(`stg_v3_revision_daily`)   : 일별 wide 스냅샷 → 지표 8로 unpivot 하고 **그 달에 그 지표가 처음
    관측된 날**(min date) 행(EG6-P02).

두 원천은 접지 않는다 — `src` 가 grain 에 있어 겹치는 달에는 같은 키가 2행이다(FX-5-004). 어느
쪽을 볼지는 소비자 뷰 `v_consensus` 가 정한다(먼저 알 수 있었던 행 = min(available_date)).

입력 — stage 2(`stg_consensus_monthly`·`stg_v3_revision_daily`) + equity `security_span`
(EG9 커버율 분모·미상장 티커 기록형; 산출식은 읽지 않는다). 숫자 상수는 `baseline_seed_s17.json`.

테이블 특화 술어(`extra_gates`):
  EG3_consensus_daily — 폐기형: `src`·`metric`·(v3)`unit` 어휘 폐쇄 · ticker 폭(EG3-P07) ·
                        `obs_month` = 월 첫날 = `date_trunc(month, obs_date)` ·
                        v3 는 `est_min`/`est_max` NULL(DESIGN §4-6) · wise 는 min ≤ mean ≤ max ·
                        `coverage_degraded` ⇔ stage `collected_date` NULL(FX-5-005 축) ·
                        `is_latest`/`_current` 컬럼 부재(EG6-P03).
                        기록형: 원천·지표·단위 분포 · `security_span` 밖 티커 · 한 달에 관측일이
                        둘인 키 · `est_mean` 결측 · 겹침 달 수.
  EG6_consensus_daily — EG6-P01/P02. 산출이 만든 CTE 가 아니라 **stage 뷰에서 다시 골라** 대조한다
                        (FX-N-005: max 선택으로 바꾸면 여기서 FAIL).
  EG8_consensus_daily — EG8-P07 v3 ⋈ wise 겹침. 같은 (ticker, 관측일, target_period, metric) 에서
                        wise 산출값과 v3 stage 값의 상대차 ≤ `v3_wise_value_tol_rel` 비율이
                        `v3_wise_match_min` 이상. 상수 미등재면 skip(no_baseline).
  EG9_consensus_daily — EG9-P05 월별 커버 종목수의 전월 대비 급락·급증 ≤ baseline. P06(시총 분위별
                        커버율)은 이 슬라이스 입력에 시총 축이 없어 `security_span` 분모 커버율
                        기록으로 대신하고 분위 축은 S19 `dataset_profile
                        .coverage_by_mktcap_quintile` 로 넘긴다(GATES §9).
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, SkipGate, require_const
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "consensus_daily.sql"

# ── 폐쇄 어휘 ────────────────────────────────────────────────────────────────
SRC_VOCAB: tuple[str, ...] = ("wise", "v3")

# v3 wide → long 대응표 (지표, stage 컬럼, 단위). `.sql` 의 v3_long UNION ALL 가지와 **순서까지**
# 같아야 한다(test_v3_unpivot_대응표는_rules_선언과_SQL이_같다). `opinion` 은 여기 없다 — 투자의견은
# S18 `opinion_daily` 축이고 컨센서스 추정치가 아니다(DESIGN §4-6 "v3 wide 8 → metric unpivot").
V3_METRICS: tuple[tuple[str, str, str], ...] = (
    ("revenue", "revenue", "억원"),
    ("op", "op", "억원"),
    ("ni", "ni", "억원"),
    ("eps", "eps", "원"),
    ("bps", "bps", "원"),
    ("per", "per", "배"),
    ("pbr", "pbr", "배"),
    ("roe_pct", "roe_pct", "%"),
)
# wise 쪽 지표 어휘 — `stage.rules_wise.STG_CONSENSUS_MONTHLY` 주석이 정본
# ("eps | revenue | parse_failed"). `parse_failed` 는 파서가 값 칸을 못 읽은 관측이다: 행을 버리면
# "그 달 관측 없음" 이 되어 커버율이 조용히 부풀므로 값 NULL 로 남긴다(원칙 ④).
WISE_METRICS: tuple[str, ...] = ("eps", "revenue", "parse_failed")
METRIC_VOCAB: tuple[str, ...] = tuple(dict.fromkeys(
    [m for m, _, _ in V3_METRICS] + list(WISE_METRICS)))
# v3 에 부여하는 단위 어휘(카탈로그 지식, STAGE_HANDOFF §4). wise `unit` 은 stage 데이터 값이라
# 폐기형으로 묶지 않고 기록형(`n_wise_unit_outside_vocab`)으로 둔다.
UNIT_VOCAB: tuple[str, ...] = tuple(dict.fromkeys(u for _, _, u in V3_METRICS))
# EG7 격리 사유. `_reject/<reason>/` 디렉토리 이름이자 EG3 어휘 폐쇄 대상.
REJECT_REASONS: tuple[str, ...] = ("target_period_invalid",)

# ── 모집단 SQL 재사용 (sql/consensus_daily.sql 의 v3_obs CTE 까지) ──────────────
POPULATION_MARKER = "-- ==== eg1:"


def _population_prefix() -> str:
    """`.sql` 첫 줄부터 `v3_obs` 를 닫는 괄호까지 — 뒤에 `SELECT …` 를 붙여 쓴다.

    `v3_obs` 뒤에는 산출용 CTE 가 더 이어지므로 원문은 `),` 로 끝난다 — 그 쉼표를 떼야
    `WITH … ) SELECT …` 가 된다.
    """
    text = SQL_PATH.read_text(encoding="utf-8")
    head, sep, _ = text.partition(POPULATION_MARKER)
    if not sep:
        raise ValueError(f"population marker not found in {SQL_PATH}: expected a line starting "
                         f"with {POPULATION_MARKER!r} right after the v3_obs CTE")
    head = head.rstrip()
    if not head.endswith(","):
        raise ValueError(f"population prefix must end with the v3_obs CTE comma: {SQL_PATH} "
                         f"tail={head[-80:]!r}")
    return head[:-1]


def population_sql(select: str) -> str:
    """`wise_obs`·`v3_long`·`v3_obs` 를 물고 있는 단일 SELECT. 모집단 정의를 두 곳에 두지 않는다."""
    return f"{_population_prefix()}\nSELECT {select}"


# GATES §3 ⑲ — 좌변 행수 = (wise distinct 키) + (v3 distinct 키) − reject.
# 우변은 `.sql` 의 모집단 CTE 를 그대로 재사용한다(S05·S11 규약). v3 쪽 distinct 는 unpivot 뒤라
# "그 달에 그 지표가 한 번이라도 관측된 조합" 을 센다 — 산출의 선택 규칙(그 조합의 첫 관측 1행)과
# 1:1 이다.
EG1_LHS_SQL = 'SELECT count(*) FROM "out_pq"'
EG1_RHS_SQL = population_sql(
    "(SELECT count(*) FROM (SELECT DISTINCT ticker, obs_month, target_period, metric "
    "FROM wise_obs)) + (SELECT count(*) FROM (SELECT DISTINCT ticker, obs_month, "
    "target_period, metric FROM v3_obs))")


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return row


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _counts(ctx: EquityGateContext, expr: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT {expr}, count(*) FROM {_q(ctx.out_view)} GROUP BY 1 ORDER BY 1").fetchall()}


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과 (rules_s01·s04 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _metric_case(alias: str, column_alias: str = "c") -> str:
    """`CASE <출력 metric> WHEN 'revenue' THEN r.revenue … END` — v3 대응표를 SQL 로."""
    whens = " ".join(f"WHEN '{m}' THEN {alias}.{col}" for m, col, _ in V3_METRICS)
    return f"CASE {column_alias}.metric {whens} END"


# ── EG3 특화 ─────────────────────────────────────────────────────────────────

def eg3_consensus_daily(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 어휘 폐쇄 · 관측 축 불변식 · 원천별 컬럼 계약 + 기록형 metric.

    폐기형은 **산출식을 다시 계산하지 않는 축**만 건다(§1). 값이 stage 와 같은지는 EG6 가,
    두 원천이 서로 맞는지는 EG8 이 본다.
    """
    v = _q(ctx.out_view)
    (n_src, n_metric, n_unit_v3, n_ticker, n_month_start, n_month_obs,
     n_v3_minmax, n_wise_band, n_cov_flag) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE src IS NULL
             OR src NOT IN ({_vocab_sql(SRC_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE metric IS NULL
             OR metric NOT IN ({_vocab_sql(METRIC_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE src = 'v3'
             AND (unit IS NULL OR unit NOT IN ({_vocab_sql(UNIT_VOCAB)}))),
          (SELECT count(*) FROM {v} WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
             OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v}
             WHERE obs_month IS DISTINCT FROM CAST(date_trunc('month', obs_month) AS DATE)),
          (SELECT count(*) FROM {v}
             WHERE obs_month IS DISTINCT FROM CAST(date_trunc('month', obs_date) AS DATE)),
          (SELECT count(*) FROM {v} WHERE src = 'v3'
             AND (est_min IS NOT NULL OR est_max IS NOT NULL)),
          (SELECT count(*) FROM {v} WHERE src = 'wise' AND est_mean IS NOT NULL
             AND est_min IS NOT NULL AND est_max IS NOT NULL
             AND NOT (est_min <= est_mean AND est_mean <= est_max)),
          (SELECT count(*) FROM {v} c WHERE c.src = 'v3'
             AND c.coverage_degraded IS DISTINCT FROM EXISTS (
                 SELECT 1 FROM stg_v3_revision_daily r
                 WHERE r.ticker = c.ticker AND r.date = c.obs_date
                   AND replace(r.target_period, '/', '') = c.target_period
                   AND r.collected_date IS NULL))""")
    # 산출이 만든 것이 아닌 축들 — 기록형
    (n_no_span, n_multi_obs, n_mean_null, n_overlap_keys, n_overlap_months,
     n_wise_unit_bad) = _row(ctx, f"""
        SELECT
          (SELECT count(DISTINCT c.ticker) FROM {v} c
            WHERE NOT EXISTS (SELECT 1 FROM security_span s WHERE s.ticker = c.ticker)),
          (SELECT count(*) FROM (
             SELECT ticker, obs_month, target_period, metric, src, count(DISTINCT obs_date) k
             FROM {v} GROUP BY ALL HAVING k > 1)),
          (SELECT count(*) FROM {v} WHERE est_mean IS NULL),
          (SELECT count(*) FROM (
             SELECT ticker, obs_month, target_period, metric, count(DISTINCT src) k
             FROM {v} GROUP BY ALL HAVING k > 1)),
          (SELECT count(*) FROM (
             SELECT obs_month FROM {v} GROUP BY obs_month
             HAVING count(DISTINCT src) > 1)),
          (SELECT count(*) FROM {v} WHERE src = 'wise'
             AND (unit IS NULL OR unit NOT IN ({_vocab_sql(UNIT_VOCAB)})))""")
    checks = {
        "n_src_outside_vocab": int(str(n_src)),
        "n_metric_outside_vocab": int(str(n_metric)),
        "n_v3_unit_outside_vocab": int(str(n_unit_v3)),
        "n_ticker_bad_width": int(str(n_ticker)),
        "n_obs_month_not_month_start": int(str(n_month_start)),
        "n_obs_month_ne_obs_date_month": int(str(n_month_obs)),
        "n_v3_minmax_present": int(str(n_v3_minmax)),
        "n_wise_band_violation": int(str(n_wise_band)),
        "n_coverage_degraded_mismatch": int(str(n_cov_flag)),
        # EG6-P03 — 최신판 컬럼을 두면 소비자가 과거를 현재값으로 읽는다
        "n_latest_current_columns": len([c for c in ctx.rule.columns
                                         if c.startswith("is_latest") or c.endswith("_current")]),
    }
    metrics: dict[str, object] = {
        "n_by_src": _counts(ctx, "src"),
        "n_by_metric": _counts(ctx, "metric"),
        "n_by_unit": _counts(ctx, "unit"),
        "n_by_available_basis": _counts(ctx, "available_basis"),
        "n_ticker_not_in_security_span": int(str(n_no_span)),
        "n_key_multi_obs_date": int(str(n_multi_obs)),
        "n_est_mean_null": int(str(n_mean_null)),
        "n_keys_with_both_src": int(str(n_overlap_keys)),
        "n_months_with_both_src": int(str(n_overlap_months)),
        "n_wise_unit_outside_vocab": int(str(n_wise_unit_bad)),
        "n_coverage_degraded": _n(ctx, f"SELECT count(*) FROM {v} WHERE coverage_degraded"),
        "src_vocab": list(SRC_VOCAB), "metric_vocab": list(METRIC_VOCAB),
        "unit_vocab": list(UNIT_VOCAB),
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_consensus_daily", checks, metrics,
                   "어휘 폐쇄 · 관측 축 불변식 · 원천별 컬럼 계약")


eg3_consensus_daily.gate_name = "EG3_consensus_daily"       # type: ignore[attr-defined]


# ── EG6 판본 선택 ────────────────────────────────────────────────────────────

def eg6_consensus_daily(ctx: EquityGateContext) -> GateResult:
    """EG6-P01(wise min(fetched_date)) · EG6-P02(v3 월 내 최초 관측) — stage 에서 독립 재선택.

    산출 CTE 를 재호출하지 않는다(§5-C 항진명제 금지): 같은 규칙을 stage 뷰 위에서 다시 실행해
    (`ORDER BY … LIMIT 1`) 고른 행이 산출 행과 같은지 본다. `arg_min`/`min_by` 를 쓰지 않는 이유는
    duckdb 가 인자 NULL 행을 건너뛰기 때문이다 — GATES §3 EG6-P02 초안 SQL
    `coalesce(min_by(r.collected_date, r.date), min(r.date))` 는 절단본에서 2026-04-03(첫 관측,
    collected_date NULL → fallback 2026-04-03) 대신 2026-04-10 을 돌려준다(§9 정정).
    """
    v = _q(ctx.out_view)
    case_r = _metric_case("r")
    n_wise_pick, n_v3_pick = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} c
            WHERE c.src = 'wise' AND (c.obs_date, c.est_mean, c.available_date, c.available_basis)
              IS DISTINCT FROM (
                SELECT (m.obs_date, m.consensus, m.available_date, m.available_basis)
                FROM stg_consensus_monthly m
                WHERE m.ticker = c.ticker AND m.target_period = c.target_period
                  AND m.metric = c.metric
                  AND strftime(m.obs_date, '%Y-%m') = strftime(c.obs_month, '%Y-%m')
                ORDER BY m.fetched_date, m.obs_date
                LIMIT 1)),
          (SELECT count(*) FROM {v} c
            WHERE c.src = 'v3' AND (c.obs_date, c.est_mean, c.available_date, c.available_basis)
              IS DISTINCT FROM (
                SELECT (r.date, {case_r}, r.available_date, r.available_basis)
                FROM stg_v3_revision_daily r
                WHERE r.ticker = c.ticker
                  AND replace(r.target_period, '/', '') = c.target_period
                  AND strftime(r.date, '%Y-%m') = strftime(c.obs_month, '%Y-%m')
                  AND {case_r} IS NOT NULL
                ORDER BY r.date
                LIMIT 1))""")
    # 기록형 — 접힌 판본 수와 "뒤 판본이 값을 바꾼" 관측점 수. 이 테이블의 존재 이유를 수치로.
    n_wise_versions, n_wise_revised = _row(ctx, """
        SELECT
          (SELECT count(*) - count(DISTINCT (ticker, strftime(obs_date, '%Y-%m'),
                                             target_period, metric))
           FROM stg_consensus_monthly),
          (SELECT count(*) FROM (
             SELECT ticker, strftime(obs_date, '%Y-%m') AS ym, target_period, metric
             FROM stg_consensus_monthly
             GROUP BY ALL HAVING count(DISTINCT consensus) > 1))""")
    checks = {"n_wise_pick_mismatch": int(str(n_wise_pick)),
              "n_v3_pick_mismatch": int(str(n_v3_pick))}
    metrics: dict[str, object] = {
        "n_wise_folded_versions": int(str(n_wise_versions)),
        "n_wise_observation_points_revised": int(str(n_wise_revised)),
    }
    return _result("EG6_consensus_daily", checks, metrics,
                   "최초 관측 선택 독립 재계산 일치")


eg6_consensus_daily.gate_name = "EG6_consensus_daily"       # type: ignore[attr-defined]


# ── EG8 교차 소스 ────────────────────────────────────────────────────────────

def eg8_overlap_sql(out_view: str, tol: float) -> str:
    """겹침 모집단 통계 1행. 허용오차는 baseline 값이라 코드에 숫자가 없다(GATES §0-3)."""
    rel = "abs(c.est_mean - v.est_mean) / nullif(abs(v.est_mean), 0)"
    both = "c.est_mean IS NOT NULL AND v.est_mean IS NOT NULL"
    return population_sql(f"""
    count(*) AS n_pop,
    count(*) FILTER (WHERE {both}) AS n_measured,
    count(*) FILTER (WHERE {both}
                       AND abs(c.est_mean - v.est_mean) <= {tol!r} * abs(v.est_mean)) AS n_match,
    max({rel}) AS max_rel,
    quantile_cont({rel}, 0.5) AS med_rel
FROM {_q(out_view)} c
JOIN v3_obs v ON v.ticker = c.ticker AND v.obs_date = c.obs_date
             AND v.target_period = c.target_period AND v.metric = c.metric
WHERE c.src = 'wise'
""")


def eg8_consensus_daily(ctx: EquityGateContext) -> GateResult:
    """EG8-P07 — v3 ⋈ WISE 겹침 일치율 ≥ `v3_wise_match_min`.

    모집단은 **같은 (ticker, 관측일, target_period, metric)** 이다. 산출의 obs_month 축으로 맞추면
    wise 는 월말·v3 는 월초 관측이라 서로 다른 날을 비교하게 된다(절단본: obs_month 로 맞추면
    같은 달 안에서도 값이 최대 10% 어긋난다). 왼쪽은 이 테이블의 wise 산출값, 오른쪽은 v3 stage
    값이라 두 원천이 실제로 만난다.
    """
    tol = require_const(ctx, "v3_wise_value_tol_rel")
    floor = require_const(ctx, "v3_wise_match_min", {"v3_wise_value_tol_rel": tol})
    n_pop, n_measured, n_match, max_rel, med_rel = _row(
        ctx, eg8_overlap_sql(ctx.out_view, float(tol)))
    n_pop, n_measured, n_match = (int(str(n_pop)), int(str(n_measured)), int(str(n_match)))
    rate = (n_match / n_measured) if n_measured else None
    metrics: dict[str, object] = {
        "n_overlap_rows": n_pop, "n_overlap_measured": n_measured, "n_match": n_match,
        "match_rate": rate, "max_rel_diff": None if max_rel is None else float(str(max_rel)),
        "median_rel_diff": None if med_rel is None else float(str(med_rel)),
        "v3_wise_value_tol_rel": tol, "v3_wise_match_min": floor}
    if rate is None:
        raise SkipGate("no_coverage", metrics)
    ok = rate >= floor
    return GateResult("EG8_consensus_daily", GateStatus.PASS if ok else GateStatus.FAIL,
                      "v3⋈wise 겹침 일치율 임계 이상" if ok else
                      f"match rate {rate:.6f} < min {floor} "
                      f"(n_match={n_match} n_measured={n_measured} tol={tol})", metrics)


eg8_consensus_daily.gate_name = "EG8_consensus_daily"       # type: ignore[attr-defined]


# ── EG9 커버리지 ─────────────────────────────────────────────────────────────

def eg9_consensus_daily(ctx: EquityGateContext) -> GateResult:
    """EG9-P05 — 월별 커버 종목수의 전월 대비 급락·급증 ≤ baseline. P06 은 기록형.

    커버 종목수가 한 달에 반토막 나거나 배로 뛰면 수집이 끊겼거나 유니버스가 바뀐 것이다 —
    그 구간의 컨센서스 팩터는 레짐 편향을 그대로 싣는다(GATES §0-3 「컨센서스 선택편향」).
    분위 축(P06 시총 5분위)은 이 슬라이스 입력에 시총이 없어 `security_span` 분모 커버율로
    대신하고, 분위별 기록은 S19 `dataset_profile.coverage_by_mktcap_quintile` 이 맡는다.
    """
    v = _q(ctx.out_view)
    drop_max = require_const(ctx, "cover_ratio_drop_max")
    rise_max = require_const(ctx, "cover_ratio_rise_max", {"cover_ratio_drop_max": drop_max})
    rows = ctx.con.execute(f"""
        WITH cov AS (SELECT obs_month, count(DISTINCT ticker) AS n_cov
                     FROM {v} GROUP BY obs_month),
             lst AS (SELECT c.obs_month, count(DISTINCT s.ticker) AS n_listed
                     FROM cov c LEFT JOIN security_span s
                       ON s.first_date <= last_day(c.obs_month) AND s.last_date >= c.obs_month
                     GROUP BY c.obs_month),
             j AS (SELECT cov.obs_month, cov.n_cov, lst.n_listed,
                          lag(cov.n_cov) OVER (ORDER BY cov.obs_month) AS prev_cov
                   FROM cov JOIN lst USING (obs_month))
        SELECT obs_month, n_cov, n_listed, prev_cov,
               CASE WHEN prev_cov > 0 THEN n_cov / CAST(prev_cov AS DOUBLE) END AS mom,
               CASE WHEN n_listed > 0 THEN n_cov / CAST(n_listed AS DOUBLE) END AS cover
        FROM j ORDER BY obs_month""").fetchall()
    by_month = [{"obs_month": str(r[0]), "n_cov": int(str(r[1])), "n_listed": int(str(r[2])),
                 "mom": None if r[4] is None else float(str(r[4])),
                 "cover_vs_span": None if r[5] is None else float(str(r[5]))} for r in rows]
    moms = [m["mom"] for m in by_month if m["mom"] is not None]
    covers = [m["cover_vs_span"] for m in by_month if m["cover_vs_span"] is not None]
    drop = max((1.0 - float(m) for m in moms), default=0.0)
    rise = max((float(m) - 1.0 for m in moms), default=0.0)
    min_mom = min(((m["mom"], m["obs_month"]) for m in by_month if m["mom"] is not None),
                  default=(None, None))
    metrics: dict[str, object] = {
        "n_months": len(by_month), "cover_drop_max_observed": drop,
        "cover_rise_max_observed": rise, "min_mom_month": min_mom[1],
        "cover_ratio_drop_max": drop_max, "cover_ratio_rise_max": rise_max,
        # P06 대용 기록 — 그 달 상장 종목 대비 컨센서스 커버율 (선택편향, DESIGN §11)
        "cover_vs_span_min": min(covers, default=None),
        "cover_vs_span_max": max(covers, default=None),
        "by_month": by_month}
    if not moms:
        raise SkipGate("no_coverage", metrics)
    ok = drop <= drop_max and rise <= rise_max
    return GateResult("EG9_consensus_daily", GateStatus.PASS if ok else GateStatus.FAIL,
                      "월별 커버 종목수 급락·급증 임계 이내" if ok else
                      f"coverage step out of band: drop={drop:.6f} (max {drop_max}) "
                      f"rise={rise:.6f} (max {rise_max}) min_mom_month={min_mom[1]}", metrics)


eg9_consensus_daily.gate_name = "EG9_consensus_daily"       # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

CONSENSUS_DAILY = register(EquityTable(
    name="consensus_daily",
    grain=("ticker", "obs_month", "target_period", "metric", "src"),
    # 순서 = `.sql` 의 SELECT 순서. `n_analyst` 는 없다(DESIGN §4-6) — 애널리스트 수 축은 S18
    # `opinion_daily.analyst_count` 다.
    columns={"ticker": "VARCHAR", "obs_month": "DATE", "target_period": "VARCHAR",
             "metric": "VARCHAR", "src": "VARCHAR", "obs_date": "DATE",
             "est_mean": "DECIMAL(20,4)", "est_min": "DECIMAL(20,4)",
             "est_max": "DECIMAL(20,4)", "unit": "VARCHAR",
             "coverage_degraded": "BOOLEAN",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_consensus_monthly", "stg_v3_revision_daily", "security_span"),
    partition_class="date_axis",
    # DESIGN §2: date_axis 이되 파티션 키 식은 `year(obs_month)` — **PIT 축이 아니다**.
    partition_key_expr="year(obs_month)",
    available_rule=("column:stage available_date — wise fetched_date(measured) / "
                    "v3 collected_date(measured), NULL 은 date(default) + coverage_degraded"),
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=EG1_RHS_SQL,
    sql_path=SQL_PATH,
    input_columns={
        "stg_consensus_monthly": ("ticker", "fetched_date", "target_period", "metric",
                                  "obs_date", "unit", "consensus", "consensus_min",
                                  "consensus_max", "available_date", "available_basis"),
        "stg_v3_revision_daily": ("ticker", "date", "target_period", "revenue", "op", "ni",
                                  "eps", "bps", "per", "pbr", "roe_pct", "collected_date",
                                  "coverage_degraded", "available_date", "available_basis"),
        # 산출식은 읽지 않는다 — EG3 기록형(미상장 티커)·EG9 커버율 분모가 읽는 축이다.
        "security_span": ("ticker", "first_date", "last_date")},
    available_basis=("measured", "default"),
    content_date_column="obs_date",
    reject_reasons=REJECT_REASONS,
    consts=(),
    extra_gates=(eg3_consensus_daily, eg6_consensus_daily, eg8_consensus_daily,
                 eg9_consensus_daily),
))

TABLES: tuple[EquityTable, ...] = (CONSENSUS_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s17.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "CONSENSUS_DAILY", "EG1_LHS_SQL", "EG1_RHS_SQL", "METRIC_VOCAB",
           "POPULATION_MARKER", "REJECT_REASONS", "SRC_VOCAB", "TABLES", "UNIT_VOCAB",
           "V3_METRICS", "WISE_METRICS", "eg8_overlap_sql", "population_sql"]
