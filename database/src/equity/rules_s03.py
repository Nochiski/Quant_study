"""S03·S03B·S03B-2·S03C 슬라이스 선언 — 유니버스 존재·상태·시장 파생 `universe_daily` v4 · 정책
선언표 `universe_policy` (DESIGN v1.2 §4-1, GATES v1.0 §3 ⑦·㉒, §1 EG3-P09, §4 FX-1-006·011~017,
§5-B8).

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

S03C(무거래 이유 분류, 09-05 사용자 결정 "무거래 연속 정지가 어떤 이유인지 데이터를 받고 판단"):
`adv20_rank_pct` 다음에 `no_trade_reason` ∈ {halt_disclosed, corp_action_window, liquidation,
admin, illiquid, none} — 무거래(`price_kind='reference'`) 행만 판정하고 거래 행은 `none`. 우선순위는
`sql/universe_daily.sql` 의 `reasoned` CTE 가 정본(halt → 정리매매 → 기업행위 창 → 관리종목 지정
직후 → 나머지). **`status` 규칙이 바뀐다**: `suspended` = `halt_state` ∨ `corp_action_window` ∨
(`illiquid` ∧ `no_trade_run ≥ k`) — k 임계는 이유를 모르는 무거래에만 건다. 기업행위 창을 넣기 위해
equity `adj_factor` 를 입력에 더한다(순환 없음 — `adj_factor` 는 `universe_daily` 를 읽지 않는다;
빌드 순서 price_daily → corp_event → adj_factor → universe_daily). `universe_policy` 의 `liquid`
에는 `no_trade_reason <> 'illiquid'` 술어가 붙는다(판본 s03c-v4).

산출 규칙 상수(게이트 임계 아님, GATES §5-B8 부류)는 `baseline_seed_s03.json` → `_const`:
  `universe_daily.admin_window_td` (KOSPI·소속부 공란 행의 관리종목 창) ·
  `universe_daily.no_trade_run_k` (무거래 연속 임계, 사람 승인) ·
  `universe_daily.adv_window_td` (adv20 창 폭 — 컬럼 이름이 못박은 20, SQL 리터럴 금지 통로) ·
  `universe_daily.corp_action_lookback_sessions`·`corp_action_lookahead_sessions` (S03C ③ 창) ·
  `universe_daily.admin_signal_window_sessions` (S03C ④ 창) ·
  `universe_policy.version` · `universe_policy.liquid_top_pct` (liquid 상위 비율, 사용자 선택).

테이블 특화 술어(`extra_gates`):
  EG3_universe — 어휘 폐쇄(status·market·sec_type·admin_state_basis) · 불린 팩트 NULL 0 ·
                 status ⇔ (halt_state ∨ run 판정) · 격자 ⊆ 구간 · **EG3-P09** halt 열린 채
                 폐지·coverage_gap 아닌 사유로 끝난 구간 0 · S03B 재계산 술어(`mktcap` 는 같은 날
                 `price_daily` 값, `no_trade_run` 은 부호·NULL 이 `price_kind` 와 정합, `adv20`
                 NULL 은 창 미달만, `listing_age_days` ≥ 0 ∧ 같은 날 listing 과 일치) · S03B-2
                 순위 술어(`adv20_rank_pct` ∈ (0, 1] · NULL ⇔ 모집단 밖 · 날짜별 최댓값 1 ·
                 순위 × 재계산 모집단 크기 = 정수 · 같은 날 adv20 순으로 단조) · S03C 이유 술어
                 (어휘 폐쇄 · NULL 0 · 무거래 아닌 행 = none · 무거래 행 ≠ none · halt 행 =
                 halt_disclosed · 입력 `adj_factor`·`trading_calendar` 에서 창을 다시 펼친 우선순위
                 독립 재계산 위반 0 · 새 status 규칙 재계산 위반 0). 열린 halt 건수·run
                 으로만 suspended 된 건수·run 히스토그램·adv20 분위수·날짜별 모집단 크기 분포·순위
                 NULL 건수·이유 부류별 행수·illiquid 의 run 분포·liquid 에서 빠지는 건수는 기록형.
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
from .model import AVAILABLE_NONE, BASIS_VOCAB, EquityTable, FieldProfile, register
from .rules_s01 import SEC_TYPE_VOCAB, TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-1 — 'delisted' 는 예약: 폐지일에는 구간이 없어 S03 격자에는 나오지 않는다.
STATUS_VOCAB: tuple[str, ...] = ("listed", "suspended", "delisted")
MARKET_VOCAB: tuple[str, ...] = ("KOSPI", "KOSDAQ")
# measured = master is_admin_issue ∨ KOSDAQ 소속부 일별 / derived_kospi_window = 지정 신호 창
# / convention = ETF(지정 대상 아님) / unknown = 판정축 없음(admin_state NULL)
ADMIN_STATE_BASIS_VOCAB: tuple[str, ...] = (
    "measured", "derived_kospi_window", "convention", "unknown")
# S03C 무거래 이유 폐쇄 어휘 — 나열 순서가 곧 판정 우선순위이고, 'none' 은 판정 대상이 아닌 행
# (거래일 · 가격 행 없는 날)이다. 우선순위 정본은 sql/universe_daily.sql 의 `reasoned` CTE.
NO_TRADE_REASON_VOCAB: tuple[str, ...] = (
    "halt_disclosed", "liquidation", "corp_action_window", "admin", "illiquid", "none")
NO_TRADE_REASON_NONE = "none"
# FIELD_MAP §1 universe_id 어휘의 policy 부분. 'common-stock' 은 소비자 계약 `krx.common-stock` 이
# 정책표로 풀리도록 S03B 에서 추가(sec_type='common' ∧ status='listed'). 'liquid' 는 S03B-2 에서
# 행 등재 — 날짜별 상위 비율(`adv20_rank_pct >= 1 − liquid_top_pct`) + S03C 무거래 술어.
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
     n_age_null, n_age_neg, n_age_diff, n_by_run, n_by_ca, n_age_fb_stock, n_age_fb,
     n_run_null_rows, run_max) = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE (u.status = 'suspended') <> coalesce(u.halt_state
                                 OR u.no_trade_reason = 'corp_action_window'
                                 OR (u.no_trade_reason = 'illiquid'
                                     AND u.no_trade_run >= {k}), false)),
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
          count(*) FILTER (WHERE u.status = 'suspended' AND NOT u.halt_state
                             AND u.no_trade_reason = 'illiquid'),
          count(*) FILTER (WHERE u.status = 'suspended' AND NOT u.halt_state
                             AND u.no_trade_reason = 'corp_action_window'),
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
        # S03C 규칙: status='suspended' ⇔ halt_state ∨ corp_action_window ∨ (illiquid ∧ run ≥ k).
        # 이름은 S03B 부터 쓰던 것을 유지한다(부정 픽스처·서버 실측 표가 이 키로 적혀 있다).
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
        # S03C 로 halt 밖 suspended 가 두 갈래로 갈린다 — 이름은 서버 실측 표(P22′ 74,824)와 잇기
        # 위해 유지하되 뜻은 '이유를 모르는 무거래(illiquid) ∧ run ≥ k' 로 좁아졌다
        "n_suspended_by_run": int(str(n_by_run)),
        "n_suspended_by_corp_action": int(str(n_by_ca)),
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


def _reason_checks(ctx: EquityGateContext, k: int, lb: int, la: int,
                   aw: int) -> tuple[dict[str, int], dict[str, object]]:
    """S03C `no_trade_reason` 술어 — 우선순위 전체를 입력에서 **다시 펼쳐** 재계산하고 대조한다.

    빌드 SQL 은 사건 창(`ca_win`)을 격자에 LEFT JOIN 해 플래그로 들고 다니는데, 여기서는 산출 행을
    캘린더·`security_span`·`price_daily`·`adj_factor` 에 다시 조인해 창과 `last_admin` 을 새로 짠다.
    같은 뜻의 다른 질의라 CTE 순서·조인 방향이 어긋나면 갈린다(부정 픽스처: 우선순위를 바꾼 사본).
    `k`·`lb`(lookback)·`la`(lookahead)·`aw`(admin 신호 창)는 전부 baseline `_const`.
    """
    v = _q(ctx.out_view)
    recalc = f"""
        WITH cal AS (
          SELECT date, row_number() OVER (ORDER BY date) AS td_seq FROM trading_calendar),
        ca AS (
          -- 적용 세션 ± 창 — apply_date ∈ [D − lb, D + la] ⇔ D ∈ [apply − la, apply + lb]
          SELECT DISTINCT a.ticker, c.td_seq
          FROM adj_factor a
          JOIN cal ap ON ap.date = a.apply_date
          JOIN cal c  ON c.td_seq BETWEEN ap.td_seq - {la} AND ap.td_seq + {lb}
          WHERE a.event_type <> 'unknown_price_only'),
        g AS (
          SELECT u.date, u.ticker, u.status, u.halt_state, u.liquidation_window, u.admin_state,
                 u.signal_admin, u.no_trade_run, u.no_trade_reason,
                 c.td_seq, s.span_seq, p.price_kind, (w.ticker IS NOT NULL) AS ca_near
          FROM {v} u
          JOIN cal c           ON c.date = u.date
          JOIN security_span s ON s.ticker = u.ticker
                              AND u.date BETWEEN s.first_date AND s.last_date
          LEFT JOIN price_daily p ON p.ticker = u.ticker AND p.date = u.date
          LEFT JOIN ca w          ON w.ticker = u.ticker AND w.td_seq = c.td_seq),
        x AS (
          SELECT g.*,
                 max(td_seq) FILTER (WHERE signal_admin)
                   OVER (PARTITION BY ticker, span_seq ORDER BY td_seq
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS last_admin
          FROM g),
        r AS (
          SELECT x.*,
                 CASE WHEN price_kind IS DISTINCT FROM 'reference' THEN 'none'
                      WHEN halt_state                              THEN 'halt_disclosed'
                      WHEN liquidation_window                      THEN 'liquidation'
                      WHEN ca_near                                 THEN 'corp_action_window'
                      WHEN coalesce(admin_state, false) AND last_admin IS NOT NULL
                           AND td_seq - last_admin <= {aw}         THEN 'admin'
                      ELSE 'illiquid' END AS want
          FROM x)"""
    (n_recalc, n_trade_not_none, n_ref_none, n_halt_bad, n_status_bad) = _row(ctx, recalc + f"""
        SELECT count(*) FILTER (WHERE no_trade_reason IS DISTINCT FROM want),
               count(*) FILTER (WHERE price_kind IS DISTINCT FROM 'reference'
                                  AND no_trade_reason <> '{NO_TRADE_REASON_NONE}'),
               count(*) FILTER (WHERE price_kind = 'reference'
                                  AND no_trade_reason = '{NO_TRADE_REASON_NONE}'),
               count(*) FILTER (WHERE price_kind = 'reference' AND halt_state
                                  AND no_trade_reason <> 'halt_disclosed'),
               count(*) FILTER (WHERE (status = 'suspended') <> coalesce(
                                        halt_state OR want = 'corp_action_window'
                                        OR (want = 'illiquid' AND no_trade_run >= {k}), false))
        FROM r""")
    hist = {f"{lo}-{hi}" if hi else f"{lo}+": _n(
        ctx, f"SELECT count(*) FROM {v} WHERE no_trade_reason = 'illiquid' AND no_trade_run >= {lo}"
             + (f" AND no_trade_run <= {hi}" if hi else "")) for lo, hi in _RUN_BUCKETS}
    n_apply, n_apply_not_ok, n_apply_off_cal = _row(ctx, """
        SELECT count(*), count(*) FILTER (WHERE NOT factor_ok),
               count(*) FILTER (WHERE apply_date NOT IN (SELECT date FROM trading_calendar))
        FROM adj_factor WHERE event_type <> 'unknown_price_only'""")
    checks = {
        "n_no_trade_reason_outside_vocab": _outside_vocab(ctx, "no_trade_reason",
                                                          NO_TRADE_REASON_VOCAB),
        "n_no_trade_reason_null": _null(ctx, "no_trade_reason"),
        "n_no_trade_reason_trade_not_none": int(str(n_trade_not_none)),
        "n_no_trade_reason_no_trade_none": int(str(n_ref_none)),
        "n_no_trade_reason_halt_mismatch": int(str(n_halt_bad)),
        "n_no_trade_reason_recompute_mismatch": int(str(n_recalc)),
        "n_status_reason_mismatch": int(str(n_status_bad)),
        # 창이 캘린더 밖 적용일을 조용히 흘리면 corp_action_window 가 통째로 비므로 폐기형이다
        "n_adj_apply_off_calendar": int(str(n_apply_off_cal)),
    }
    metrics: dict[str, object] = {
        "corp_action_lookback_sessions": lb,
        "corp_action_lookahead_sessions": la,
        "admin_signal_window_sessions": aw,
        "no_trade_reason_counts": _counts(ctx, "no_trade_reason"),
        "no_trade_reason_run_hist_illiquid": hist,
        # liquid 정책이 `no_trade_reason <> 'illiquid'` 로 빼는 행 — 순위 임계(liquid_top_pct) 이전
        # 기준이라 실제 제외분의 상계다(임계는 universe_policy 의 상수라 여기서 모른다)
        "n_liquid_excluded_by_illiquid": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE no_trade_reason = 'illiquid' "
                 "AND adv20_rank_pct IS NOT NULL AND NOT admin_state "
                 "AND NOT liquidation_window"),
        "n_adj_apply_rows": int(str(n_apply)),
        "n_adj_apply_rows_not_ok": int(str(n_apply_not_ok)),
        "adj_apply_event_types": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            "SELECT event_type, count(*) FROM adj_factor "
            "WHERE event_type <> 'unknown_price_only' GROUP BY 1 ORDER BY 1").fetchall()},
        "no_trade_reason_vocab": list(NO_TRADE_REASON_VOCAB),
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
    lb = int(require_const(ctx, "corp_action_lookback_sessions"))
    la = int(require_const(ctx, "corp_action_lookahead_sessions"))
    aw = int(require_const(ctx, "admin_signal_window_sessions"))
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
    reason_checks, reason_metrics = _reason_checks(ctx, k, lb, la, aw)
    checks.update(reason_checks)
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
        **reason_metrics,
    }
    return _result("EG3_universe", checks, metrics,
                   "유니버스 상태·시장 파생·유동성 순위·무거래 이유 불변식 성립")


eg3_universe.gate_name = "EG3_universe"         # type: ignore[attr-defined]

# ── S19 필드 선언 (DESIGN §4-7 EQD-07 — `universe_daily·core`·`mktcap,adv20`) ──
# 전부 **1 세션** 랙이다: 값 원천이 `stg_listing_daily`·`stg_master_daily`·`stg_disclosure`
# (stage `lag_known=false` = 공표 시점 미상, STAGE_HANDOFF §2)이고 관리종목 지정·정지 공시는
# 장중·장후 어느 쪽이든 날 수 있어 당일 지식으로 못 쓴다. 시총·adv20 은 `price_daily` 파생이라
# 값 자체는 종가 축이지만, 같은 격자 행에 실린 상태 컬럼과 랙을 갈라 두면 소비자가 한 행을 두 랙으로
# 읽어야 해서 격자 축 랙(1)으로 통일한다 — 종가 축이 필요하면 `price.market_cap` 을 쓴다.
_UAXIS: tuple[str, str] = ("ticker", "date")

FIELDS_UNIVERSE: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="universe.status", columns=("status",), label="종목 상태(상장·정지)", unit="",
        value_type="category", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="KRX 일별 마스터·정지/관리 공시 — 게시 시각 미측정",
        evidence="universe_daily.status ∈ {listed, suspended}. 정지 판정은 halt_state ∨ "
                 "corp_action_window ∨ (illiquid ∧ no_trade_run ≥ k)(DESIGN §4-1 S03C).",
        coverage_axis="grid_session", scope="internal", axis_columns=_UAXIS),
    FieldProfile(
        field_id="universe.admin_state", columns=("admin_state",), label="관리종목 여부",
        unit="", value_type="category", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=True,
        disclosure_basis="KOSDAQ 소속부 일별 스냅샷 / KOSPI 는 지정 공시 창(derived)",
        evidence="시장 비대칭이 남아 있다 — KOSPI 해제 공시가 3건뿐이라 상태 종료를 못 잰다"
                 "(GAP-06). basis 는 admin_state_basis 컬럼이 행마다 남긴다.",
        coverage_axis="grid_session", scope="internal", axis_columns=_UAXIS),
    FieldProfile(
        field_id="universe.delist_signal", columns=("signal_delist",), label="상장폐지 신호",
        unit="", value_type="category", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="거래소·DART 공시 접수일(stg_disclosure.rcept_dt)",
        evidence="universe_daily.signal_delist — FACTORS 정본 E07(상폐 위험)의 재료. 같은 축의 "
                 "signal_admin·signal_liquidation·liquidation_window 는 같은 행에 있다.",
        coverage_axis="grid_session", scope="internal", axis_columns=_UAXIS),
    FieldProfile(
        field_id="universe.mktcap", columns=("mktcap_krw",), label="시가총액(격자 축)",
        unit="KRW", value_type="amount", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 종가 확정 + KRX 상장주식수 게시",
        evidence="같은 날 price_daily.mktcap_krw 를 격자에 실은 값(EG3_universe 재계산 술어). "
                 "가격 행 축이 필요하면 price.market_cap 을 쓴다.",
        coverage_axis="grid_session", scope="internal", axis_columns=_UAXIS),
    FieldProfile(
        field_id="universe.adv20", columns=("adv20_krw",), label="20세션 평균 거래대금",
        unit="KRW", value_type="amount", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 마감 집계 20세션 이동평균",
        evidence="창 미달 구간은 NULL(0 으로 채우지 않는다). 같은 날 순위는 adv20_rank_pct"
                 "(cume_dist)이고 universe_policy 의 liquid 임계가 그 축을 쓴다.",
        coverage_axis="grid_session", scope="internal", axis_columns=_UAXIS),
)


UNIVERSE_DAILY = register(EquityTable(
    name="universe_daily",
    grain=("date", "ticker"),
    # 순서 = DESIGN §4-1: S03 컬럼 → S03B 시장 파생 4개(+ S03B-2 `adv20_rank_pct` 는 `adv20_krw`
    # 바로 다음, S03C `no_trade_reason` 은 `adv20_rank_pct` 바로 다음) → PIT 2개. `adv20_krw` 는
    # duckdb avg(DECIMAL) 의 반환 타입(DOUBLE)을 그대로 받는다(정밀도 리터럴 캐스팅 금지).
    # `adv20_rank_pct` 는 cume_dist 반환 타입(DOUBLE).
    columns={"date": "DATE", "ticker": "VARCHAR", "status": "VARCHAR", "market": "VARCHAR",
             "sec_type": "VARCHAR", "halt_state": "BOOLEAN", "admin_state": "BOOLEAN",
             "admin_state_basis": "VARCHAR", "liquidation_window": "BOOLEAN",
             "signal_halt": "BOOLEAN", "signal_halt_release": "BOOLEAN",
             "signal_admin": "BOOLEAN", "signal_liquidation": "BOOLEAN",
             "signal_delist": "BOOLEAN", "admin_flag": "BOOLEAN",
             "mktcap_krw": "DECIMAL(18,0)", "adv20_krw": "DOUBLE", "adv20_rank_pct": "DOUBLE",
             "no_trade_reason": "VARCHAR",
             "listing_age_days": "BIGINT", "no_trade_run": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    # S03C 로 equity `adj_factor` 가 입력에 들어온다 — 순환 없음(adj_factor 는 universe_daily 를
    # 읽지 않는다). 빌드 순서 price_daily → corp_event → adj_factor → universe_daily.
    inputs=("security_span", "trading_calendar", "security", "price_daily", "adj_factor",
            "stg_listing_daily", "stg_master_daily", "stg_disclosure"),
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
        # factor_ok·event_type 은 산출에 안 쓴다(창은 ok 무관) — EG3_universe 기록형 metric 용
        "adj_factor": ("ticker", "apply_date", "factor_ok", "event_type"),
        "stg_listing_daily": ("ticker", "date", "market", "sect_tp", "sect_available",
                              "list_date"),
        "stg_master_daily": ("ticker", "date", "is_admin_issue", "is_trade_halt",
                             "is_liquidation"),
        "stg_disclosure": ("rcept_no", "rcept_dt", "ticker", "has_ticker", "report_nm")},
    available_basis=("default",),
    content_date_column="date",
    consts=("admin_window_td", "no_trade_run_k", "adv_window_td",
            "corp_action_lookback_sessions", "corp_action_lookahead_sessions",
            "admin_signal_window_sessions"),
    extra_gates=(eg3_universe,),
    field_profiles=FIELDS_UNIVERSE,
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
