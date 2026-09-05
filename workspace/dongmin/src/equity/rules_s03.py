"""S03·S03B 슬라이스 선언 — 유니버스 존재·상태·시장 파생 `universe_daily` v2 · 정책 선언표
`universe_policy` (DESIGN v1.2 §4-1, GATES v1.0 §3 ⑦·㉒, §1 EG3-P09, §4 FX-1-006·011~017, §5-B8).

`universe_daily` 는 처음으로 **앞서 커밋된 equity 테이블을 입력으로 읽는** 테이블이다 —
`security_span`·`trading_calendar`(S02)·`security`(S01)·`price_daily`(S04)가 `stg_*` 와 같은
규약으로 `_pinned/` 에 고정된다(inputs.source_root). 격자 = span × 캘린더라 EG1 우변은
Σ `security_span.n_days` 이고 coverage_gap·delisted 행은 만들지 않는다(GAP-21, 캘린더 max =
backfill_end).

S03B(시장 파생, 09-05): S03 컬럼 뒤에 `mktcap_krw`·`adv20_krw`·`listing_age_days`·`no_trade_run`
을 붙이고 `status='suspended'` 판정을 `halt_state ∨ (price_kind='reference' ∧ no_trade_run ≥ k)`
로 완성한다. 가격 축은 stage 두 원장 대신 equity `price_daily`(EG20 으로 stage 와 동일) 하나에서
읽는다.

S03B-2(날짜별 유동성 순위, 09-05 사용자 결정 "날짜별 상위 비율 기준"): `adv20_krw` 다음에
`adv20_rank_pct` — 같은 날 모집단(`sec_type='common' ∧ status='listed' ∧ adv20_krw IS NOT NULL`)
안 `adv20_krw` 의 cume_dist((0, 1], 클수록 유동성 큼, 날짜별 최댓값 1). 모집단 밖 행은 NULL.
`universe_policy` 에 `liquid` 정책(investable 4행 + `adv20_rank_pct >= 1 − liquid_top_pct` 1행,
`threshold_kind='quantile'`, `threshold_value` = baseline `universe_policy.liquid_top_pct`)을
등재한다.

산출 규칙 상수(게이트 임계 아님, GATES §5-B8 부류)는 `baseline_seed_s03.json` → `_const`:
  `universe_daily.admin_window_td` (KOSPI·소속부 공란 행의 관리종목 창) ·
  `universe_daily.no_trade_run_k` (무거래 연속 임계, 사람 승인) ·
  `universe_daily.adv_window_td` (adv20 창 폭 — 컬럼 이름이 못박은 20, SQL 리터럴 금지 통로) ·
  `universe_policy.version` · `universe_policy.liquid_top_pct` (liquid 상위 비율, 사용자 선택).

테이블 특화 술어(`extra_gates`):
  EG3_universe — 어휘 폐쇄(status·market·sec_type·admin_state_basis) · 불린 팩트 NULL 0 ·
                 status ⇔ (halt_state ∨ run 판정) · 격자 ⊆ 구간 · **EG3-P09** halt 열린 채
                 폐지·coverage_gap 아닌 사유로 끝난 구간 0 · S03B 재계산 술어(`mktcap` 는 같은 날
                 `price_daily` 값, `no_trade_run` 은 부호·NULL 이 `price_kind` 와 정합, `adv20`
                 NULL 은 창 미달만, `listing_age_days` ≥ 0 ∧ 같은 날 listing 과 일치) · S03B-2
                 순위 술어(`adv20_rank_pct` ∈ (0, 1] · NULL ⇔ 모집단 밖 · 날짜별 최댓값 1 ·
                 순위 × 재계산 모집단 크기 = 정수 · 같은 날 adv20 순으로 단조). 열린 halt 건수·run
                 으로만 suspended 된 건수·run 히스토그램·adv20 분위수·날짜별 모집단 크기 분포·순위
                 NULL 건수는 기록형.
  EG3_policy   — 어휘 폐쇄(policy·threshold_kind·basis) · universe_id 문법 · 'all' 행 존재 ·
                 임계 종류와 값의 정합(quantile 은 (0, 1]) · predicate 가 universe_daily 스키마
                 위에서 바인딩되는가.
`universe_policy` 는 선언표라 EG1 을 `skip(declaration_table)` 한다(`declaration_table=True`).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, require_const
from .model import AVAILABLE_NONE, BASIS_VOCAB, EquityTable, register
from .rules_s01 import SEC_TYPE_VOCAB, TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-1 — 'delisted' 는 예약: 폐지일에는 구간이 없어 S03 격자에는 나오지 않는다.
STATUS_VOCAB: tuple[str, ...] = ("listed", "suspended", "delisted")
MARKET_VOCAB: tuple[str, ...] = ("KOSPI", "KOSDAQ")
# measured = master is_admin_issue ∨ KOSDAQ 소속부 일별 / derived_kospi_window = 지정 신호 창
# / convention = ETF(지정 대상 아님) / unknown = 판정축 없음(admin_state NULL)
ADMIN_STATE_BASIS_VOCAB: tuple[str, ...] = (
    "measured", "derived_kospi_window", "convention", "unknown")
# FIELD_MAP §1 universe_id 어휘의 policy 부분. 'common-stock' 은 소비자 계약 `krx.common-stock` 이
# 정책표로 풀리도록 S03B 에서 추가(sec_type='common' ∧ status='listed'). 'liquid' 는 S03B-2 에서
# 행 등재 — 절대 금액 임계가 아니라 날짜별 상위 비율(`adv20_rank_pct >= 1 − liquid_top_pct`).
POLICY_VOCAB: tuple[str, ...] = ("all", "common-stock", "investable", "liquid")
THRESHOLD_KIND_VOCAB: tuple[str, ...] = ("quantile", "absolute", "flag")
UNIVERSE_ID_PREFIX = "krx."          # FIELD_MAP §1 — universe_id = '<market>.<policy>'

_BOOL_FACTS: tuple[str, ...] = (
    "halt_state", "liquidation_window", "signal_halt", "signal_halt_release", "signal_admin",
    "signal_liquidation", "signal_delist")
# no_trade_run 히스토그램 구간(기록형) — k 승인 근거. 상한은 열려 있다.
_RUN_BUCKETS: tuple[tuple[int, int | None], ...] = ((1, 1), (2, 4), (5, 9), (10, 19), (20, None))
# adv20_rank_pct = k/n (cume_dist) 를 DOUBLE 로 저장하므로 rank × n 은 정수 ± 몇 ulp 다. 게이트
# 임계가 아니라 산출 정밀도 상수(rules_s06.FACTOR_PRODUCT_TOL 부류) — n ≤ 10⁴ 에서 오차 ≤ 1e-12.
RANK_INTEGRAL_TOL = 1e-9


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _n(ctx: EquityGateContext, sql: str) -> int:
    return int(str(_row(ctx, sql)[0]))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _outside_vocab(ctx: EquityGateContext, column: str, values: tuple[str, ...]) -> int:
    """`column` 값 중 어휘 밖 건수. NULL 은 세지 않는다(NULL 허용 여부는 따로 판정)."""
    v = _q(ctx.out_view)
    return _n(ctx, f"SELECT count(*) FROM {v} WHERE {_q(column)} IS NOT NULL "
                   f"AND {_q(column)} NOT IN ({_vocab_sql(values)})")


def _null(ctx: EquityGateContext, column: str) -> int:
    return _n(ctx, f"SELECT count(*) FROM {_q(ctx.out_view)} WHERE {_q(column)} IS NULL")


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 그대로 싣는다."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL,
                      "; ".join(f"{k}={v}" for k, v in sorted(bad.items())), merged)


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT {_q(column)}, count(*) FROM {_q(ctx.out_view)} GROUP BY 1 ORDER BY 1").fetchall()}


# ── universe_daily ───────────────────────────────────────────────────────────

def _market_checks(ctx: EquityGateContext, k: int,
                   w: int) -> tuple[dict[str, int], dict[str, object]]:
    """S03B 컬럼의 재계산 술어 — 산출 행을 입력 `price_daily`·`stg_listing_daily`·`security_span`
    에 다시 조인해 한 번에 센다(10.9M 행 위에서 조인 1회 + 창 1회). `k` = no_trade_run_k,
    `w` = adv_window_td (둘 다 baseline).

    `no_trade_run` 은 구간 안 run 자체를 다시 세지 않고 부호·NULL 정합만 본다(0 ⇔ trade ·
    >0 ⇔ reference · NULL ⇔ 가격 없음). `adv20` 은 창 안 value 행수를 독립 창으로 세어 NULL 여부만
    대조한다(평균값 자체는 픽스처가 손계산으로 본다).
    """
    v = _q(ctx.out_view)
    (n_status, n_run_neg, n_run_null, n_run_sign, n_mktcap_null, n_mktcap_diff,
     n_age_null, n_age_neg, n_age_diff, n_by_run, n_age_fb_stock, n_age_fb, n_run_null_rows,
     run_max) = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE (u.status = 'suspended') <> coalesce(u.halt_state
                                 OR (p.price_kind = 'reference' AND u.no_trade_run >= {k}), false)),
          count(*) FILTER (WHERE u.no_trade_run < 0),
          count(*) FILTER (WHERE (u.no_trade_run IS NULL) <> (p.price_kind IS NULL)),
          count(*) FILTER (WHERE (u.no_trade_run = 0 AND p.price_kind <> 'trade')
                              OR (u.no_trade_run > 0 AND p.price_kind <> 'reference')),
          count(*) FILTER (WHERE (u.mktcap_krw IS NULL) <> (p.mktcap_krw IS NULL)),
          count(*) FILTER (WHERE u.mktcap_krw IS DISTINCT FROM p.mktcap_krw),
          count(*) FILTER (WHERE u.listing_age_days IS NULL),
          count(*) FILTER (WHERE u.listing_age_days < 0),
          count(*) FILTER (WHERE l.list_date IS NOT NULL
                             AND u.listing_age_days <> date_diff('day', l.list_date, u.date)),
          count(*) FILTER (WHERE u.status = 'suspended' AND NOT u.halt_state),
          count(*) FILTER (WHERE l.list_date IS NULL AND u.sec_type <> 'etf'),
          count(*) FILTER (WHERE l.list_date IS NULL),
          count(*) FILTER (WHERE u.no_trade_run IS NULL),
          coalesce(max(u.no_trade_run), 0)
        FROM {v} u
        LEFT JOIN price_daily p       ON p.ticker = u.ticker AND p.date = u.date
        LEFT JOIN stg_listing_daily l ON l.ticker = u.ticker AND l.date = u.date""")
    n_adv_null_mismatch, n_adv_null = _row(ctx, f"""
        WITH x AS (
          SELECT u.adv20_krw,
                 count(p.value_krw) OVER (PARTITION BY u.ticker, s.span_seq ORDER BY u.date
                                          ROWS BETWEEN {w - 1} PRECEDING
                                          AND CURRENT ROW) AS n_value
          FROM {v} u
          JOIN security_span s ON s.ticker = u.ticker
                              AND u.date BETWEEN s.first_date AND s.last_date
          LEFT JOIN price_daily p ON p.ticker = u.ticker AND p.date = u.date)
        SELECT count(*) FILTER (WHERE (adv20_krw IS NULL) <> (n_value < {w})),
               count(*) FILTER (WHERE adv20_krw IS NULL)
        FROM x""")
    hist = {f"{lo}-{hi}" if hi else f"{lo}+": _n(
        ctx, f"SELECT count(*) FROM {v} WHERE no_trade_run >= {lo}"
             + (f" AND no_trade_run <= {hi}" if hi else "")) for lo, hi in _RUN_BUCKETS}
    quantiles = _row(ctx, f"""
        SELECT quantile_cont(adv20_krw, [0.1, 0.25, 0.5, 0.75, 0.9])
        FROM {v} WHERE sec_type = 'common' AND adv20_krw IS NOT NULL""")[0]
    checks = {
        # S03B 규칙: status='suspended' ⇔ halt_state ∨ (reference ∧ run ≥ k)
        "n_status_halt_mismatch": int(str(n_status)),
        "n_no_trade_run_negative": int(str(n_run_neg)),
        "n_no_trade_run_null_mismatch": int(str(n_run_null)),
        "n_no_trade_run_sign_mismatch": int(str(n_run_sign)),
        "n_mktcap_null_mismatch": int(str(n_mktcap_null)),
        "n_mktcap_price_mismatch": int(str(n_mktcap_diff)),
        "n_adv20_null_mismatch": int(str(n_adv_null_mismatch)),
        "n_listing_age_null": int(str(n_age_null)),
        "n_listing_age_negative": int(str(n_age_neg)),
        "n_listing_age_listing_mismatch": int(str(n_age_diff)),
    }
    metrics: dict[str, object] = {
        "no_trade_run_k": k,
        "adv_window_td": w,
        "n_suspended_by_run": int(str(n_by_run)),
        "n_no_trade_run_null": int(str(n_run_null_rows)),
        "no_trade_run_max": int(str(run_max)),
        "no_trade_run_hist": hist,
        "n_adv20_null": int(str(n_adv_null)),
        "n_mktcap_null": _null(ctx, "mktcap_krw"),
        "n_listing_age_fallback": int(str(n_age_fb)),
        "n_listing_age_fallback_stock": int(str(n_age_fb_stock)),
        "adv20_common_quantiles": (None if quantiles is None
                                   else [float(str(x)) for x in quantiles]),
    }
    return checks, metrics


def _rank_checks(ctx: EquityGateContext) -> tuple[dict[str, int], dict[str, object]]:
    """S03B-2 `adv20_rank_pct` 술어 — 산출 컬럼만으로 모집단(보통주 ∧ listed ∧ adv20 있음)을 다시
    가르고, 순위값이 그 모집단의 cume_dist 답게 생겼는지 본다. cume_dist 자체를 다시 돌리지 않고
    (§5-C7) 성질만 본다: 범위 (0, 1] · NULL ⇔ 모집단 밖 · 날짜별 최댓값 1 · 순위 × 모집단 크기 =
    정수(모집단을 다르게 잡은 순위는 대부분 여기서 갈린다) · 같은 날 adv20 오름차순으로 단조.
    정확한 값은 픽스처(손계산)가 본다. 창은 좁은 투영(date·adv20·rank) 위 1개."""
    v = _q(ctx.out_view)
    pop_sql = f"""
        WITH x AS (
          SELECT date, adv20_krw, adv20_rank_pct AS r,
                 coalesce(sec_type = 'common' AND status = 'listed' AND adv20_krw IS NOT NULL,
                          false) AS in_pop
          FROM {v}),
        p AS (
          SELECT date, count(*) FILTER (WHERE in_pop) AS n_pop, max(r) AS r_max
          FROM x GROUP BY date)"""
    n_range, n_null_mismatch, n_not_integral, n_null = _row(ctx, pop_sql + f"""
        SELECT count(*) FILTER (WHERE x.r <= 0 OR x.r > 1),
               count(*) FILTER (WHERE (x.r IS NULL) <> NOT x.in_pop),
               count(*) FILTER (WHERE x.r IS NOT NULL AND abs(x.r * p.n_pop - round(x.r * p.n_pop))
                                      > {RANK_INTEGRAL_TOL!r}),
               count(*) FILTER (WHERE x.r IS NULL)
        FROM x JOIN p USING (date)""")
    n_max_not_one, n_dates_pop, n_dates_no_pop, pop_min, pop_p50, pop_max = _row(
        ctx, pop_sql + """
        SELECT count(*) FILTER (WHERE n_pop > 0 AND r_max IS DISTINCT FROM 1),
               count(*) FILTER (WHERE n_pop > 0),
               count(*) FILTER (WHERE n_pop = 0),
               min(n_pop) FILTER (WHERE n_pop > 0),
               quantile_cont(n_pop, 0.5) FILTER (WHERE n_pop > 0),
               max(n_pop) FILTER (WHERE n_pop > 0)
        FROM p""")
    n_order = _n(ctx, pop_sql + """
        SELECT count(*) FROM (
          SELECT r, lag(r) OVER (PARTITION BY date ORDER BY adv20_krw, r) AS r_prev
          FROM x WHERE in_pop)
        WHERE r < r_prev""")
    checks = {
        "n_adv20_rank_out_of_range": int(str(n_range)),
        "n_adv20_rank_null_mismatch": int(str(n_null_mismatch)),
        "n_adv20_rank_max_not_one": int(str(n_max_not_one)),
        "n_adv20_rank_pop_mismatch": int(str(n_not_integral)),
        "n_adv20_rank_order_violation": int(str(n_order)),
    }
    metrics: dict[str, object] = {
        "n_adv20_rank_null": int(str(n_null)),
        "n_dates_with_rank_pop": int(str(n_dates_pop)),
        "n_dates_without_rank_pop": int(str(n_dates_no_pop)),
        "adv20_rank_pop_size": (None if pop_min is None else {
            "min": int(str(pop_min)), "p50": float(str(pop_p50)), "max": int(str(pop_max))}),
    }
    return checks, metrics


def eg3_universe(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P09·P13 + S03 상태 규칙 정합 + S03B 시장 파생 재계산 술어 + S03B-2 순위 술어.

    P09 는 GATES §1 술어 그대로다 — `end_reason ∈ {delisted, coverage_gap}` 인 구간 끝의 열린
    halt 는 정상(폐지까지 재거래 없음 930건·현재 정지 중)이라 술어 밖이고, 그 건수는 기록형으로
    남긴다. 술어 안에 남는 것은 `data_gap`(예약 어휘) 구간 끝뿐이다.
    S03B 판정에 쓰는 k(`no_trade_run_k`)·창 폭(`adv_window_td`)은 baseline — 미등재면 게이트
    전체가 `skip(no_baseline)`.
    """
    k = int(require_const(ctx, "no_trade_run_k"))
    w = int(require_const(ctx, "adv_window_td"))
    v = _q(ctx.out_view)
    checks = {
        "n_ticker_bad_width": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR' "
                 f"OR length(ticker) <> {TICKER_LEN}"),
        "n_status_outside_vocab": _outside_vocab(ctx, "status", STATUS_VOCAB),
        "n_status_null": _null(ctx, "status"),
        "n_market_outside_vocab": _outside_vocab(ctx, "market", MARKET_VOCAB),
        "n_sec_type_outside_vocab": _outside_vocab(ctx, "sec_type", SEC_TYPE_VOCAB),
        "n_sec_type_null": _null(ctx, "sec_type"),
        "n_admin_basis_outside_vocab": _outside_vocab(ctx, "admin_state_basis",
                                                      ADMIN_STATE_BASIS_VOCAB),
        "n_admin_basis_null": _null(ctx, "admin_state_basis"),
        # admin_state NULL ⇔ basis unknown — 판정축이 없을 때만 결측을 허용한다
        "n_admin_state_null_mismatch": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE (admin_state IS NULL) <> "
                 "(admin_state_basis = 'unknown')"),
        "n_bool_fact_null": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE "
                 + " OR ".join(f"{_q(c)} IS NULL" for c in _BOOL_FACTS)),
        # 격자 ⊆ 구간 — EG1 은 합만 보므로 구간 밖 날짜가 다른 결손과 상쇄되면 못 본다
        "n_rows_off_span": _n(
            ctx, f"SELECT count(*) FROM {v} u WHERE NOT EXISTS (SELECT 1 FROM security_span s "
                 "WHERE s.ticker = u.ticker AND u.date BETWEEN s.first_date AND s.last_date)"),
        # EG3-P09 (GATES §1)
        "n_halt_open_at_data_gap": _n(
            ctx, f"SELECT count(*) FROM security_span s JOIN {v} u "
                 "ON u.ticker = s.ticker AND u.date = s.last_date "
                 "WHERE u.halt_state AND s.end_reason NOT IN ('delisted', 'coverage_gap')"),
    }
    market_checks, market_metrics = _market_checks(ctx, k, w)
    checks.update(market_checks)
    rank_checks, rank_metrics = _rank_checks(ctx)
    checks.update(rank_checks)
    metrics: dict[str, object] = {
        "n_halt_open_at_coverage_end": _n(
            ctx, f"SELECT count(*) FROM security_span s JOIN {v} u "
                 "ON u.ticker = s.ticker AND u.date = s.last_date "
                 "WHERE u.halt_state AND s.end_reason = 'coverage_gap'"),
        "n_halt_open_at_delist": _n(
            ctx, f"SELECT count(*) FROM security_span s JOIN {v} u "
                 "ON u.ticker = s.ticker AND u.date = s.last_date "
                 "WHERE u.halt_state AND s.end_reason = 'delisted'"),
        "n_halt_days": _n(ctx, f"SELECT count(*) FROM {v} WHERE halt_state"),
        "n_liquidation_days": _n(ctx, f"SELECT count(*) FROM {v} WHERE liquidation_window"),
        "n_admin_days_by_basis": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f"SELECT admin_state_basis, count(*) FROM {v} WHERE admin_state "
            "GROUP BY 1 ORDER BY 1").fetchall()},
        "signal_counts": {c: _n(ctx, f"SELECT count(*) FROM {v} WHERE {_q(c)}")
                          for c in ("signal_halt", "signal_halt_release", "signal_admin",
                                    "signal_liquidation", "signal_delist")},
        "n_admin_flag_not_null": _n(ctx, f"SELECT count(*) FROM {v} WHERE admin_flag IS NOT NULL"),
        "n_market_null": _null(ctx, "market"),
        "status_counts": _counts(ctx, "status"),
        "admin_state_basis_counts": _counts(ctx, "admin_state_basis"),
        "sec_type_counts": _counts(ctx, "sec_type"),
        "status_vocab": list(STATUS_VOCAB),
        "admin_state_basis_vocab": list(ADMIN_STATE_BASIS_VOCAB),
        **market_metrics,
        **rank_metrics,
    }
    return _result("EG3_universe", checks, metrics,
                   "유니버스 상태·시장 파생·유동성 순위 불변식 성립")


eg3_universe.gate_name = "EG3_universe"         # type: ignore[attr-defined]

UNIVERSE_DAILY = register(EquityTable(
    name="universe_daily",
    grain=("date", "ticker"),
    # 순서 = DESIGN §4-1: S03 컬럼 → S03B 시장 파생 4개(+ S03B-2 `adv20_rank_pct` 는 `adv20_krw`
    # 바로 다음) → PIT 2개. `adv20_krw` 는 duckdb avg(DECIMAL) 의 반환 타입(DOUBLE)을 그대로 받는다
    # (정밀도 리터럴 캐스팅 금지). `adv20_rank_pct` 는 cume_dist 반환 타입(DOUBLE).
    columns={"date": "DATE", "ticker": "VARCHAR", "status": "VARCHAR", "market": "VARCHAR",
             "sec_type": "VARCHAR", "halt_state": "BOOLEAN", "admin_state": "BOOLEAN",
             "admin_state_basis": "VARCHAR", "liquidation_window": "BOOLEAN",
             "signal_halt": "BOOLEAN", "signal_halt_release": "BOOLEAN",
             "signal_admin": "BOOLEAN", "signal_liquidation": "BOOLEAN",
             "signal_delist": "BOOLEAN", "admin_flag": "BOOLEAN",
             "mktcap_krw": "DECIMAL(18,0)", "adv20_krw": "DOUBLE", "adv20_rank_pct": "DOUBLE",
             "listing_age_days": "BIGINT", "no_trade_run": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("security_span", "trading_calendar", "security", "price_daily", "stg_listing_daily",
            "stg_master_daily", "stg_disclosure"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    # build.py 가 연도 루프를 아직 지원하지 않는다(NotImplementedError). 서버는 security_span 의
    # 10.89M 행 조인을 RSS 694MB 로 처리했으므로 whole-SQL 로 간다 — 스필은 temp_directory.
    build_by_year=False,
    available_rule="column:date — KRX 일별 스냅샷 관례(stage lag_known=false), 행 = date(default)",
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    # GATES §3 ⑦ (정정판): Σ n_days. 캘린더 max = backfill_end(EG17) 라 '≤ backfill_end' 는
    # 항등이고, 두 입력 판본이 어긋나면(구간이 캘린더보다 길다) 격자가 작아져 여기서 깨진다.
    eg1_rhs_sql="SELECT coalesce(sum(n_days), 0) FROM security_span",
    sql_path=SQL_DIR / "universe_daily.sql",
    input_columns={
        "security_span": ("ticker", "span_seq", "first_date", "last_date", "n_days",
                          "end_reason"),
        "trading_calendar": ("date",),
        "security": ("ticker", "sec_type"),
        "price_daily": ("ticker", "date", "volume_shr", "value_krw", "mktcap_krw", "price_kind"),
        "stg_listing_daily": ("ticker", "date", "market", "sect_tp", "sect_available",
                              "list_date"),
        "stg_master_daily": ("ticker", "date", "is_admin_issue", "is_trade_halt",
                             "is_liquidation"),
        "stg_disclosure": ("rcept_no", "rcept_dt", "ticker", "has_ticker", "report_nm")},
    available_basis=("default",),
    content_date_column="date",
    consts=("admin_window_td", "no_trade_run_k", "adv_window_td"),
    extra_gates=(eg3_universe,),
))


# ── universe_policy ──────────────────────────────────────────────────────────

def _predicate_unbound(con: duckdb.DuckDBPyConnection, predicate: str) -> bool:
    """predicate 가 `universe_daily` 스키마 위에서 바인딩되는가 — 1행만 물어 결합만 본다."""
    try:
        con.execute("SELECT count(*) FROM (SELECT * FROM universe_daily LIMIT 1) u "
                    f"WHERE ({predicate})").fetchone()
    except duckdb.Error:
        return True
    return False


def eg3_policy(ctx: EquityGateContext) -> GateResult:
    """EG3-P13 어휘 + 선언표 정합. `all` 행이 없으면 어댑터의 `krx.all` 이 빈 유니버스가 된다."""
    v = _q(ctx.out_view)
    rows = ctx.con.execute(f"SELECT policy, rule_seq, predicate FROM {v} ORDER BY 1, 2").fetchall()
    unbound = [f"{p}#{s}" for p, s, pred in rows if _predicate_unbound(ctx.con, str(pred))]
    checks = {
        "n_policy_outside_vocab": _outside_vocab(ctx, "policy", POLICY_VOCAB),
        "n_threshold_kind_outside_vocab": _outside_vocab(ctx, "threshold_kind",
                                                         THRESHOLD_KIND_VOCAB),
        "n_basis_outside_vocab": _outside_vocab(ctx, "basis", BASIS_VOCAB),
        "n_null_key_or_predicate": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE policy IS NULL OR rule_seq IS NULL "
                 "OR threshold_kind IS NULL OR basis IS NULL "
                 "OR coalesce(trim(predicate), '') = ''"),
        "n_rule_seq_not_positive": _n(ctx, f"SELECT count(*) FROM {v} WHERE rule_seq < 1"),
        "n_universe_id_bad_grammar": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE universe_id IS DISTINCT FROM "
                 f"'{UNIVERSE_ID_PREFIX}' || policy"),
        # flag 는 임계가 없고, quantile·absolute 는 임계값이 있어야 한다
        "n_threshold_value_mismatch": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE (threshold_kind = 'flag') <> "
                 "(threshold_value IS NULL)"),
        # quantile 임계는 비율 — (0, 1] 밖이면 술어가 전부/전무를 뽑는다(S03B-2 liquid_top_pct)
        "n_quantile_threshold_out_of_range": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE threshold_kind = 'quantile' "
                 "AND NOT (threshold_value > 0 AND threshold_value <= 1)"),
        "n_all_missing": int(not any(p == "all" for p, _, _ in rows)),
        "n_predicate_unbound": len(unbound),
    }
    metrics: dict[str, object] = {
        "n_rows": len(rows), "policy_counts": _counts(ctx, "policy"),
        "unbound_predicates": unbound, "policy_vocab": list(POLICY_VOCAB),
        "threshold_kind_vocab": list(THRESHOLD_KIND_VOCAB),
        "quantile_rows": {f"{r[0]}#{r[1]}": float(str(r[2])) for r in ctx.con.execute(
            f"SELECT policy, rule_seq, threshold_value FROM {v} "
            "WHERE threshold_kind = 'quantile' ORDER BY 1, 2").fetchall()},
    }
    return _result("EG3_policy", checks, metrics, "정책 선언표 정합")


eg3_policy.gate_name = "EG3_policy"             # type: ignore[attr-defined]

UNIVERSE_POLICY = register(EquityTable(
    name="universe_policy",
    grain=("policy", "rule_seq"),
    columns={"universe_id": "VARCHAR", "policy": "VARCHAR", "rule_seq": "BIGINT",
             "predicate": "VARCHAR", "threshold_kind": "VARCHAR", "threshold_value": "DOUBLE",
             "basis": "VARCHAR", "measured_at": "DATE", "version": "VARCHAR"},
    inputs=("universe_daily",),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,      # 선언표 — 공개시점이 없다
    eg1_lhs_sql="",
    eg1_rhs_sql="",
    sql_path=SQL_DIR / "universe_policy.sql",
    input_columns={"universe_daily": ()},   # predicate 바인딩 검사용 — 전 컬럼
    consts=("version", "liquid_top_pct"),
    extra_gates=(eg3_policy,),
    declaration_table=True,
))

TABLES: tuple[EquityTable, ...] = (UNIVERSE_DAILY, UNIVERSE_POLICY)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s03.json"
"""이 슬라이스가 요구하는 `_const` 상수의 초기값. 승인 뒤 `data/equity/baseline.json` 에 병합."""

__all__ = ["BASELINE_SEED", "TABLES", "UNIVERSE_DAILY", "UNIVERSE_POLICY"]
