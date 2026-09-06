"""S06 조정계수 슬라이스 — `adj_factor` (DESIGN v1.2 §4-2 · GATES v1.0 §3-⑩ · EG3-P04 · EG8).

원칙 ②(원주가 불변 + 계수 분리)의 계수 쪽이다. `price_daily` 는 손대지 않고, `corp_event` MVP 4종
(split·reverse_split·bonus·capred)에서 이벤트당 1행의 곱셈 계수를 낸다 — `share_factor = ratio`,
`price_factor = 1/ratio`(시총 불변). 계수를 못 내는 이벤트는 격리하지 않고 `factor_ok=false`·
계수 1 로 남긴다(사유 `factor_source`). 산출식은 `sql/adj_factor.sql`, 뷰 매크로는 `views.py`.

2차(서버 1차 빌드 EG8 실패 뒤): 계수를 **어느 세션에 적용할지**(`apply_date`)를 가격으로 가린다 —
명목 효력일에 기준가가 안 바뀌는 사건(감자는 정지 뒤 재개일, 감자병합은 복합 사건)이 많아
명목일 적용은 수정수익률 점프를 남긴다. `apply_basis` ∈ nominal · price_matched ·
price_matched_combined · unmatched(→ `factor_source='no_price_match'`, 계수 1). 규칙은 `.sql`
머리말.

3차(S06-2, 09-05): **KRX 기준가 원천 `krx_base_price`** — `price_daily.base_price_krw`(= close −
change_krw, 그날 KRX 기준가)가 직전 행 close 와 다른 (ticker, date) 를 사건으로 읽는다. (a) 창 안의
MVP 사건(성분은 곱)과 비율이 맞으면 그 사건의 계수·apply_date 를 기준가로 교체(`apply_basis=
'krx_base_price'`, share_factor = 같은 날 주식수 비 또는 1/price_factor) · (b) 사건 없이 주식수 변화
동반이면 신규 행 `unknown_krx`(ok = 곱 검사) · (c) 주식수 불변 ∧ 전일 무거래는 정지 재개 가격 재발견
(행 없음, 기록) · (d) 그 외는 `unknown_price_only` ok=false. 사건 매칭(2차)은 기준가 사건이 없을
때의 폴백으로 남는다. ETF(분배락·설정환매)와 재상장 첫 행은 후보 밖 — 근거는 `.sql` 머리말.

입력 — equity `corp_event`·`price_daily`(close·price_kind: 매칭 축 + EG8 · base_price_krw·
shares_out: 기준가 원천)·`trading_calendar`·`security`(sec_type·corp_code)·`security_span`(구간
첫날) + `stg_event_cr`(감자 유·무상 판정 축 `cr_mth`·`cr_rs`: corp_event 에는 구분 컬럼이 없다).
상수는 `corp_event.near_dup_window_days`·`corp_event.krx_share_change_tol` 와
`adj_factor.price_match_*` 4개 + `base_price_tol_rel`·`base_match_window_sessions`·
`factor_product_tol_base` 를 `_const` 로 읽는다(build.make_consts 의 `<table>.<metric>` 키).

테이블 특화 술어(`extra_gates`):
  EG3_adj_factor — 어휘 폐쇄(factor_source·apply_basis·event_type) · EG3-P04 시총 불변
                   `price×share = 1`(허용오차 FACTOR_PRODUCT_TOL) · ok 행 `share_factor = ratio` ·
                   not-ok 행 계수 1 · apply_date 불변식(캘린더 세션 · 명목 세션 기준 창 안 ·
                   nominal 은 명목 세션 · 복합 성분은 같은 apply_date · 개별 ok 2건이 같은 날 0) ·
                   `available_date = min(announce, apply_date 다음 세션)` 독립 재계산(EG2-P02
                   대체 — announce 축은 회고 기재 원천에서 available < announce 가 정상이라 못
                   쓴다) · corp_event 정합. 기록형: 사유·apply_basis 별 건수, 오프셋 분포.
  EG8            — P02 수정수익률(행 대 행) 점프를 **apply_date** 에서 건별로, P03 은 이벤트 집합의
                   조정 거래량 20세션 중앙값 비의 중앙값 ∈ [1/band, band](방향 오류 탐지, 3차)
                   (`views` 의 같은 템플릿을 TEMP MACRO 로 올려 계산). 상수 미등재면
                   skip(no_baseline) 이되 **metric 은 항상 계산**.
  EG8-P01(KIS 수정종가 대조)은 독립 KIS 가격 stage 테이블이 없어 여전히 붙이지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from . import views
from .gates import EquityGateContext, SkipGate, require_const
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN
from .rules_s05 import MVP_EVENT_TYPES

SQL_DIR = Path(__file__).parent / "sql"

# 계수를 내는 corp_event 이벤트 어휘 (GATES §3-⑩ `_reg_vocab('factor_bearing_event')`) = S05 MVP
# 4종. EG1 우변의 corp_event 쪽 항이다.
FACTOR_BEARING_EVENTS: tuple[str, ...] = MVP_EVENT_TYPES
# S06-2 — KRX 기준가 원천이 만드는 신규 행의 event_type. corp_event 에 없다(event_id
# `{ticker}:krx_base:{date}`).
#   unknown_krx        : 기준가 + 같은 날 주식수 변화 — 시총 불변 검사 통과 시 ok(DART 공백기
#                        분할·감자)
#   unknown_price_only : 기준가만 변화(주식수 불변·전일 거래) — 항상 ok=false(권리락·주식배당락 등
#                        MVP 밖)
KRX_BASE_EVENT_TYPES: tuple[str, ...] = ("unknown_krx", "unknown_price_only")
EVENT_TYPE_VOCAB: tuple[str, ...] = (*FACTOR_BEARING_EVENTS, *KRX_BASE_EVENT_TYPES)
KRX_BASE_EVENT_ID_INFIX = ":krx_base:"
# factor_source 폐쇄 어휘 — mktcap_neutral 만 factor_ok=true 다(사유 우선순위는 sql/adj_factor.sql).
# S06-2: krx_base_inconsistent(기준가 비율 × 주식수 비가 1 ± factor_product_tol_base 밖, 또는 ok
# 사건의 apply_date 에 기준가 후보가 있는데 비율이 안 맞음) · unknown_price_only(위 (d) 행의 사유).
FACTOR_SOURCE_VOCAB: tuple[str, ...] = ("mktcap_neutral", "ratio_null", "capred_paid",
                                        "near_dup_suppressed", "no_share_change",
                                        "no_price_match", "same_day_suppressed",
                                        "krx_base_inconsistent", "unknown_price_only")
OK_FACTOR_SOURCE = "mktcap_neutral"
# apply_basis 폐쇄 어휘. ok 행은 OK_APPLY_BASIS, no_price_match 행만 unmatched, 그 외 not-ok 행은
# 매칭 결과(nominal·price_matched·krx_base_price) 유지. S06-2: krx_base_price = KRX 기준가가 정한
# 세션.
APPLY_BASIS_VOCAB: tuple[str, ...] = ("nominal", "price_matched", "price_matched_combined",
                                      "unmatched", "krx_base_price")
OK_APPLY_BASIS: tuple[str, ...] = ("nominal", "price_matched", "price_matched_combined",
                                   "krx_base_price")
KRX_BASE_APPLY_BASIS = "krx_base_price"
# baseline 상수 — 가격 매칭 창·허용치 (adj_factor 네임스페이스) + 근접 중복 창 (corp_event)
PRICE_MATCH_CONSTS: tuple[str, ...] = ("price_match_tol_rel", "price_match_tol_abs",
                                       "price_match_window_sessions",
                                       "price_match_lookback_sessions")
# S06-2 기준가 원천 상수 (adj_factor 네임스페이스). 주식수 변화 허용치는 corp_event 의 것을 복제
# 없이 읽는다(선언의 consts).
BASE_PRICE_CONSTS: tuple[str, ...] = ("base_price_tol_rel", "base_match_window_sessions",
                                      "factor_product_tol_base")
# EG1 우변 · EG3 의 독립 재계산이 쓰는 기준가 후보 CTE — `sql/adj_factor.sql` 의 bpx·bp 와 같은
# 정의를 게이트 쪽에서 따로 적는다(산출 SQL 을 재사용하지 않는다). ETF·구간 첫날은 플래그로 남겨
# 제외 건수를 센다.
_BASE_PRICE_CANDIDATES_CTE = """
bpc AS (
    SELECT x.ticker, x.date, x.prev_kind, x.is_etf,
           x.base_price_krw / x.prev_close AS r,
           CASE WHEN x.prev_shares > 0 AND abs(x.shares_out / x.prev_shares - 1) > k.share_tol
                THEN x.shares_out / x.prev_shares END AS share_ratio,
           EXISTS (SELECT 1 FROM security_span sp
                   WHERE sp.ticker = x.ticker AND sp.first_date = x.date) AS span_start
    FROM (SELECT p.ticker, p.date, p.base_price_krw, p.shares_out,
                 lag(p.close) OVER w AS prev_close, lag(p.shares_out) OVER w AS prev_shares,
                 lag(p.price_kind) OVER w AS prev_kind,
                 p.ticker IN (SELECT ticker FROM security WHERE sec_type = 'etf') AS is_etf
          FROM price_daily p WINDOW w AS (PARTITION BY p.ticker ORDER BY p.date)) x
    CROSS JOIN (SELECT CAST(base_price_tol_rel AS DOUBLE) AS base_tol,
                       CAST(krx_share_change_tol AS DOUBLE) AS share_tol FROM _const) k
    WHERE x.prev_close > 0 AND x.base_price_krw IS NOT NULL
      AND abs(x.base_price_krw / x.prev_close - 1) > k.base_tol)"""
# 후보 중 원천이 실제로 쓰는 것(비ETF · 구간 첫날 아님)
_BP_IN_SCOPE = "NOT b.is_etf AND NOT b.span_start"
# EG3-P04 허용오차 — baseline 상수가 아니라 **산출 정밀도** 다: price_factor = 1/ratio 를 DOUBLE 로
# 두므로 곱은 1 ± 몇 ulp 다(실측 max |1/x·x − 1| = 1.1e-16, x ∈ [1, 1e5]). 1e-12 는 그 1e4 배
# 여유이고 계수 방향 오류(역수·제곱)는 1e-12 로는 절대 못 숨긴다.
FACTOR_PRODUCT_TOL = 1e-12
# EG8-P03 중앙값 창(전·후 세션 수, GATES §1 EG8 M03 의 20). 임계가 아니라 측정 방법의 일부라 코드
# 상수다. 아래 둘은 **기록형 보고 구간**(판정축 아님): 이벤트 거래량 비 > 10 건수 ·
# |조정수익률| > 0.30(일반 세션 가격제한폭 — 거래 재개 첫날은 제한폭이 없어 판정에 못 쓴다) 건수.
VOLUME_MEDIAN_WINDOW = 20
VOLUME_RATIO_REPORT_BAND = 10.0
RETURN_REPORT_LIMIT = 0.30
JUMP_SAMPLE_ROWS = 20


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


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT {_q(column)}, count(*) FROM {_q(ctx.out_view)} GROUP BY 1 ORDER BY 1").fetchall()}


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _const_or_none(ctx: EquityGateContext, table: str, metric: str) -> float | None:
    v = ctx.baseline.get(table, metric)
    return None if v is None else float(str(v))


# ── EG3_adj_factor ───────────────────────────────────────────────────────────

def eg3_adj_factor(ctx: EquityGateContext) -> GateResult:
    """EG3-P04·P07·P13 + 계수·apply_date 불변식 + available_date 독립 재계산 + corp_event 정합
    + S06-2 기준가 원천 불변식(ok 기준가 행의 계수 = price_daily 기준가/직전 행 close 독립 재계산 ·
    unknown_krx 의 share_factor = 같은 날 주식수 비 · unknown_price_only 는 ok 0 · ETF·구간 첫날
    아님 · 같은 날 이중 ok 없음) + 후보 분류 (a)(b)(c)(d) 기록."""
    v = _q(ctx.out_view)
    krx = KRX_BASE_APPLY_BASIS
    # EG3-P04 허용오차: corp_event ratio 행은 산출 정밀도(FACTOR_PRODUCT_TOL), 기준가 원천 행은
    # baseline
    # `factor_product_tol_base`(기준가 비율 × 주식수 비 — 호가단위 반올림·자기주식 신주 미배정 등
    # KRX 산식 잔여).
    prod_tol = _const_or_none(ctx, ctx.rule.name, "factor_product_tol_base")
    tol_expr = (f"CASE WHEN apply_basis = '{krx}' THEN {prod_tol!r} ELSE {FACTOR_PRODUCT_TOL!r} END"
                if prod_tol is not None else repr(FACTOR_PRODUCT_TOL))
    (n_src_vocab, n_basis_vocab, n_type_vocab, n_ticker_bad, n_product_off, n_not_ok_ne_one,
     n_ok_source, n_ok_basis_bad, n_unmatched_mismatch, n_avail_null, n_basis, n_apply_null,
     max_dev) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE factor_source IS NULL
             OR factor_source NOT IN ({_vocab_sql(FACTOR_SOURCE_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE apply_basis IS NULL
             OR apply_basis NOT IN ({_vocab_sql(APPLY_BASIS_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE event_type IS NULL
             OR event_type NOT IN ({_vocab_sql(EVENT_TYPE_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
             OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v} WHERE factor_ok
             AND (price_factor IS NULL OR share_factor IS NULL
                  OR abs(price_factor * share_factor - 1) > {tol_expr})),
          (SELECT count(*) FROM {v} WHERE NOT factor_ok
             AND (price_factor IS DISTINCT FROM 1 OR share_factor IS DISTINCT FROM 1)),
          (SELECT count(*) FROM {v}
             WHERE factor_ok IS DISTINCT FROM (factor_source = '{OK_FACTOR_SOURCE}')),
          (SELECT count(*) FROM {v} WHERE factor_ok
             AND apply_basis NOT IN ({_vocab_sql(OK_APPLY_BASIS)})),
          (SELECT count(*) FROM {v}
             WHERE (factor_source = 'no_price_match') IS DISTINCT FROM (apply_basis = 'unmatched')),
          (SELECT count(*) FROM {v} WHERE available_date IS NULL),
          (SELECT count(*) FROM {v} WHERE available_basis IS DISTINCT FROM 'derived'),
          (SELECT count(*) FROM {v} WHERE apply_date IS NULL),
          (SELECT coalesce(max(abs(price_factor * share_factor - 1)), 0) FROM {v}
             WHERE factor_ok)""")
    # corp_event · 캘린더 정합 — 키·속성·ratio · 명목 세션 · apply 창 · available 재계산.
    lookback = _const_or_none(ctx, ctx.rule.name, "price_match_lookback_sessions")
    window = _const_or_none(ctx, ctx.rule.name, "price_match_window_sessions")
    base_win = _const_or_none(ctx, ctx.rule.name, "base_match_window_sessions")
    # 창 검사: 개별(nominal·price_matched·unmatched)은 자기 명목 세션 창, 복합 성분(combined)은
    # **성분 창** [min(명목) − lookback, max(명목) + window] — 성분 = 같은 티커·같은 apply_date 의
    # combined 행(서버 3차: 멤버 명목일이 떨어진 성분의 공통 apply_date 가 멤버 자기 창 밖이라
    # FAIL 했다). S06-2 기준가 교체 행(krx_base_price, corp_event 있음)은 같은 (ticker, apply_date)
    # 단위 창에 ± base_match_window_sessions 를 더한다(교체 전 apply_date 가 창 안이고 기준가는 그
    # ± base_win 안). 기준가 신규 행은 effective = apply 라 거리 0.
    base_win_expr = (f"(CASE WHEN apply_basis = '{krx}' THEN {int(base_win)} ELSE 0 END)"
                     if base_win is not None else "0")
    win_sql = ("count(*) FILTER (WHERE n_apply IS NOT NULL AND n_lo IS NOT NULL AND "
               f"(n_apply < n_lo - {int(lookback)} - {base_win_expr} "
               f"OR n_apply > n_hi + {int(window)} + {base_win_expr}))"
               if lookback is not None and window is not None else "NULL")
    mvp_types = _vocab_sql(FACTOR_BEARING_EVENTS)
    krx_types = _vocab_sql(KRX_BASE_EVENT_TYPES)
    (n_event_mismatch, n_share_ne_ratio, n_avail_mismatch, n_avail_before_announce,
     n_apply_off_cal, n_nominal_ne, n_apply_outside, max_off, max_off_ind, max_off_comb,
     n_off_pos, med_off, n_krx_new_bad, n_price_only_ok, krx_sf_dev_max) = _row(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             j0 AS (
          SELECT a.*, e.event_id AS e_id, e.ticker AS e_ticker, e.effective_date AS e_eff,
                 e.event_type AS e_type, e.announce_date AS e_ann, e.corp_code AS e_corp, e.ratio,
                 ca.n AS n_apply,
                 (SELECT min(c.n) FROM cal c WHERE c.date >= a.effective_date) AS n_nom,
                 (SELECT c.date FROM cal c WHERE c.n = ca.n + 1) AS next_session
          FROM {v} a
          LEFT JOIN corp_event e ON e.event_id = a.event_id
          LEFT JOIN cal ca ON ca.date = a.apply_date),
             j AS (
          SELECT j0.*,
                 CASE WHEN apply_basis IN ('price_matched_combined', '{krx}')
                      THEN min(n_nom) OVER (PARTITION BY ticker, apply_date, apply_basis)
                      ELSE n_nom END AS n_lo,
                 CASE WHEN apply_basis IN ('price_matched_combined', '{krx}')
                      THEN max(n_nom) OVER (PARTITION BY ticker, apply_date, apply_basis)
                      ELSE n_nom END AS n_hi
          FROM j0)
        SELECT
          count(*) FILTER (WHERE event_type IN ({mvp_types})
                             AND (e_id IS NULL OR ticker IS DISTINCT FROM e_ticker
                              OR effective_date IS DISTINCT FROM e_eff
                              OR event_type IS DISTINCT FROM e_type
                              OR announce_date IS DISTINCT FROM e_ann
                              OR corp_code IS DISTINCT FROM e_corp)),
          count(*) FILTER (WHERE factor_ok AND apply_basis <> '{krx}'
                             AND share_factor IS DISTINCT FROM ratio),
          count(*) FILTER (WHERE available_date IS DISTINCT FROM
                                 coalesce(least(e_ann, next_session), apply_date)),
          count(*) FILTER (WHERE available_date < e_ann),
          count(*) FILTER (WHERE n_apply IS NULL),
          count(*) FILTER (WHERE apply_basis = 'nominal' AND n_apply IS DISTINCT FROM n_nom),
          {win_sql},
          coalesce(max(n_apply - n_nom) FILTER (WHERE factor_ok), 0),
          coalesce(max(n_apply - n_nom) FILTER (WHERE factor_ok
                     AND apply_basis IN ('nominal', 'price_matched')), 0),
          coalesce(max(n_apply - n_nom) FILTER (WHERE factor_ok
                     AND apply_basis = 'price_matched_combined'), 0),
          count(*) FILTER (WHERE factor_ok AND n_apply <> n_nom),
          coalesce(median(n_apply - n_nom) FILTER (WHERE factor_ok AND n_apply <> n_nom), 0),
          count(*) FILTER (WHERE event_type IN ({krx_types})
                             AND (e_id IS NOT NULL OR apply_basis <> '{krx}'
                              OR effective_date IS DISTINCT FROM apply_date
                              OR announce_date IS DISTINCT FROM apply_date
                              OR event_id IS DISTINCT FROM ticker || '{KRX_BASE_EVENT_ID_INFIX}'
                                 || CAST(apply_date AS VARCHAR))),
          count(*) FILTER (WHERE event_type = 'unknown_price_only' AND factor_ok),
          coalesce(max(abs(share_factor / ratio - 1)) FILTER (WHERE factor_ok
                     AND apply_basis = '{krx}' AND ratio IS NOT NULL), 0)
        FROM j""")
    # 복합 성분은 같은 apply_date · 개별 매칭 ok 2건이 같은 apply_date 를 나눠 갖지 않는다
    n_combined_inconsistent = _n(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             c AS (SELECT a.*,
                          (SELECT min(x.n) FROM cal x WHERE x.date >= a.effective_date) AS n_nom
                   FROM {v} a WHERE apply_basis = 'price_matched_combined')
        SELECT count(*) FROM c a JOIN c b ON b.ticker = a.ticker AND a.event_id < b.event_id
        WHERE abs(a.n_nom - b.n_nom) <= {int(window) if window is not None else 0}
          AND a.apply_date <> b.apply_date""") if window is not None else 0
    n_same_day_individual = _n(ctx, f"""
        SELECT count(*) FROM {v} a JOIN {v} b
          ON b.ticker = a.ticker AND b.apply_date = a.apply_date AND a.event_id < b.event_id
        WHERE a.factor_ok AND b.factor_ok
          AND a.apply_basis IN ('nominal', 'price_matched')
          AND b.apply_basis IN ('nominal', 'price_matched')""")
    near_dup = ctx.baseline.get("corp_event", "near_dup_window_days")
    checks: dict[str, int] = {
        "n_factor_source_outside_vocab": int(str(n_src_vocab)),
        "n_apply_basis_outside_vocab": int(str(n_basis_vocab)),
        "n_event_type_outside_factor_bearing": int(str(n_type_vocab)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
        "n_ok_product_off_one": int(str(n_product_off)),           # EG3-P04
        "n_not_ok_factor_ne_one": int(str(n_not_ok_ne_one)),
        "n_ok_source_mismatch": int(str(n_ok_source)),
        "n_ok_factor_one": _n(ctx, f"SELECT count(*) FROM {v} WHERE factor_ok "
                                   "AND share_factor = 1"),
        "n_ok_apply_basis_bad": int(str(n_ok_basis_bad)),
        "n_unmatched_source_mismatch": int(str(n_unmatched_mismatch)),
        "n_available_null": int(str(n_avail_null)),
        "n_basis_not_derived": int(str(n_basis)),
        "n_apply_date_null": int(str(n_apply_null)),
        "n_apply_off_calendar": int(str(n_apply_off_cal)),
        "n_nominal_apply_ne_nominal_session": int(str(n_nominal_ne)),
        "n_corp_event_mismatch": int(str(n_event_mismatch)),
        "n_ok_share_factor_ne_ratio": int(str(n_share_ne_ratio)),
        "n_available_mismatch": int(str(n_avail_mismatch)),        # EG2-P02 대체
        "n_combined_apply_inconsistent": n_combined_inconsistent,
        "n_ok_same_apply_date_individual": n_same_day_individual,
    }
    if n_apply_outside is not None:
        checks["n_apply_outside_window"] = int(str(n_apply_outside))
    if near_dup is not None:
        # 교차 원천 근접 쌍(S05 EG3_corp_event.n_near_dup_cross_source 와 같은 축)이 둘 다 ok 면
        # 이중 곱
        checks["n_near_dup_both_ok"] = _n(ctx, f"""
            SELECT count(*) FROM {v} a JOIN {v} b
              ON b.ticker = a.ticker AND b.event_type = a.event_type AND a.event_id < b.event_id
            JOIN corp_event ea ON ea.event_id = a.event_id
            JOIN corp_event eb ON eb.event_id = b.event_id
            WHERE ea.source <> eb.source
              AND abs(date_diff('day', a.effective_date, b.effective_date))
                  <= {int(str(near_dup))}
              AND a.factor_ok AND b.factor_ok""")
    by_source = _counts(ctx, "factor_source")
    by_basis = _counts(ctx, "apply_basis")
    by_type = _counts(ctx, "event_type")
    basis_by_type = {f"{r[0]}:{r[1]}": int(str(r[2])) for r in ctx.con.execute(
        f"SELECT event_type, apply_basis, count(*) FROM {v} WHERE factor_ok "
        "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()}
    tol_abs = _const_or_none(ctx, ctx.rule.name, "price_match_tol_abs")
    n_small_nominal = (_n(ctx, f"""
        SELECT count(*) FROM {v} WHERE factor_ok AND apply_basis = 'nominal'
          AND abs(least(price_factor, share_factor) - 1) <= {tol_abs!r}""")
        if tol_abs is not None else None)
    # no_price_match 중 창 안에 가격 행이 아예 없는 사건(상장폐지 기간 등) — 매칭 실패가 아니라
    # 가격 부재
    n_unmatched_no_price = _n(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             u AS (SELECT a.ticker, a.event_id,
                          (SELECT min(x.n) FROM cal x WHERE x.date >= a.effective_date) AS n_nom
                   FROM {v} a WHERE factor_source = 'no_price_match')
        SELECT count(*) FROM u
        WHERE NOT EXISTS (
          SELECT 1 FROM price_daily p JOIN cal c ON c.date = p.date
          WHERE p.ticker = u.ticker
            AND c.n BETWEEN u.n_nom - {int(lookback) if lookback is not None else 0}
                        AND u.n_nom + {int(window) if window is not None else 0})""")
    # ── S06-2 기준가 원천 — price_daily 에서 후보를 독립 재계산해 산출과 대조 ──
    # ok 기준가 행의 계수 = 그날 기준가 / 직전 행 close(성분은 같은 (ticker, apply_date) 곱) · ok
    # unknown_krx
    # 행의 share_factor = 같은 날 주식수 비 · 기준가 행은 ETF·구간 첫날이 아님 · ok unknown_krx 행은
    # 같은 날
    # 다른 ok 행과 겹치지 않음(이중 적용) · 후보 분류 건수(a)(b)(c)(d) · 기준가 사건 없는 ok 사건
    # 수.
    bp_base_win = int(base_win) if base_win is not None else 0
    (n_krx_pf_mismatch, n_unknown_krx_share_bad, n_krx_out_of_scope, n_unknown_krx_shared,
     n_bp_candidates, n_bp_etf, n_bp_span_start, n_bp_consumed, n_bp_rediscovery,
     n_bp_share_change, n_bp_price_only, n_ok_no_bp) = _row(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             {_BASE_PRICE_CANDIDATES_CTE},
             grp AS (
          SELECT ticker, apply_date, product(price_factor) AS pf_prod,
                 bool_or(event_type IN ({mvp_types})) AS has_event
          FROM {v} WHERE factor_ok AND apply_basis = '{krx}' GROUP BY ticker, apply_date),
             krx_rows AS (
          SELECT a.ticker, a.apply_date, a.event_id, a.event_type, a.factor_ok, a.share_factor,
                 b.r, b.share_ratio, b.is_etf, b.span_start
          FROM {v} a LEFT JOIN bpc b ON b.ticker = a.ticker AND b.date = a.apply_date
          WHERE a.apply_basis = '{krx}'),
             consumed AS (
          SELECT DISTINCT ticker, apply_date AS date FROM {v}
          WHERE apply_basis = '{krx}' AND event_type IN ({mvp_types})),
             scope AS (SELECT b.* FROM bpc b WHERE {_BP_IN_SCOPE}),
             free AS (SELECT s.* FROM scope s
                      WHERE NOT EXISTS (SELECT 1 FROM consumed c
                                        WHERE c.ticker = s.ticker AND c.date = s.date)),
             ok_ev AS (
          SELECT a.ticker, ca.n FROM {v} a JOIN cal ca ON ca.date = a.apply_date
          WHERE a.factor_ok AND a.apply_basis <> '{krx}')
        SELECT
          (SELECT count(*) FROM grp g
             LEFT JOIN bpc b ON b.ticker = g.ticker AND b.date = g.apply_date
            WHERE b.r IS NULL OR abs(g.pf_prod / b.r - 1) > {FACTOR_PRODUCT_TOL!r}),
          (SELECT count(*) FROM krx_rows
            WHERE factor_ok AND event_type = 'unknown_krx'
              AND (share_ratio IS NULL OR share_factor IS DISTINCT FROM share_ratio)),
          (SELECT count(*) FROM krx_rows WHERE r IS NULL OR is_etf OR span_start),
          (SELECT count(*) FROM krx_rows k
            WHERE k.factor_ok AND k.event_type = 'unknown_krx'
              AND EXISTS (SELECT 1 FROM {v} o WHERE o.factor_ok AND o.ticker = k.ticker
                          AND o.apply_date = k.apply_date AND o.event_id <> k.event_id)),
          (SELECT count(*) FROM scope),
          (SELECT count(*) FROM bpc b WHERE b.is_etf),
          (SELECT count(*) FROM bpc b WHERE NOT b.is_etf AND b.span_start),
          (SELECT count(*) FROM consumed),
          (SELECT count(*) FROM free WHERE share_ratio IS NULL AND prev_kind = 'reference'),
          (SELECT count(*) FROM free WHERE share_ratio IS NOT NULL),
          (SELECT count(*) FROM free
            WHERE share_ratio IS NULL AND prev_kind IS DISTINCT FROM 'reference'),
          (SELECT count(*) FROM ok_ev o
            WHERE NOT EXISTS (SELECT 1 FROM scope s JOIN cal c ON c.date = s.date
                              WHERE s.ticker = o.ticker AND abs(c.n - o.n) <= {bp_base_win}))""")
    price_only_by_month = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(f"""
        SELECT strftime(apply_date, '%Y-%m'), count(*) FROM {v}
        WHERE event_type = 'unknown_price_only' GROUP BY 1 ORDER BY 1""").fetchall()}
    by_type_source = {f"{r[0]}:{r[1]}": int(str(r[2])) for r in ctx.con.execute(f"""
        SELECT event_type, factor_source, count(*) FROM {v} WHERE apply_basis = '{krx}'
        GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()}
    checks.update({
        "n_krx_new_row_invariant_bad": int(str(n_krx_new_bad)),
        "n_unknown_price_only_ok": int(str(n_price_only_ok)),
        "n_krx_price_factor_mismatch": int(str(n_krx_pf_mismatch)),
        "n_unknown_krx_ok_share_factor_bad": int(str(n_unknown_krx_share_bad)),
        "n_krx_row_out_of_scope": int(str(n_krx_out_of_scope)),
        "n_unknown_krx_shared_apply_date": int(str(n_unknown_krx_shared)),
    })
    metrics: dict[str, object] = {
        # ── S06-2 기준가 원천 분류 (a)(b)(c)(d) ──
        "n_base_price_candidates": int(str(n_bp_candidates)),
        "n_base_price_etf_excluded": int(str(n_bp_etf)),
        "n_base_price_span_start_excluded": int(str(n_bp_span_start)),
        "n_base_price_replaced_units": int(str(n_bp_consumed)),                 # (a) 단위 수
        "n_krx_replaced_rows": sum(n for k, n in by_type_source.items()
                                   if k.split(":")[0] in FACTOR_BEARING_EVENTS),
        "n_unknown_krx": by_type.get("unknown_krx", 0),                          # (b) 행 수
        "n_unknown_krx_ok": by_type_source.get("unknown_krx:mktcap_neutral", 0),   # (b) ok
        "n_base_price_share_change_free": int(str(n_bp_share_change)),          # (b) 후보 수
        "n_base_price_rediscovery": int(str(n_bp_rediscovery)),                 # (c)
        "n_unknown_price_only": by_type.get("unknown_price_only", 0),           # (d)
        "n_base_price_only_free": int(str(n_bp_price_only)),
        "n_base_price_only_by_month": price_only_by_month,
        "n_krx_base_inconsistent": by_source.get("krx_base_inconsistent", 0),
        "n_krx_by_event_type_factor_source": by_type_source,
        "n_ok_without_base_price_event": int(str(n_ok_no_bp)),   # 폴백(사건 매칭)으로만 선 ok 행
        "krx_share_factor_ratio_dev_max": float(str(krx_sf_dev_max)),
        "base_match_window_sessions": base_win,
        "factor_product_tol_base": prod_tol,
        "base_price_tol_rel": _const_or_none(ctx, ctx.rule.name, "base_price_tol_rel"),
        "krx_share_change_tol": ctx.baseline.get("corp_event", "krx_share_change_tol"),
        "n_ok": _n(ctx, f"SELECT count(*) FROM {v} WHERE factor_ok"),
        "n_by_factor_source": by_source,
        "n_by_apply_basis": by_basis,
        "n_ok_by_event_type_apply_basis": basis_by_type,
        "n_by_event_type": by_type,
        "n_near_dup_suppressed": by_source.get("near_dup_suppressed", 0),
        "n_same_day_suppressed": by_source.get("same_day_suppressed", 0),
        "n_ratio_null": by_source.get("ratio_null", 0),
        "n_capred_paid": by_source.get("capred_paid", 0),
        "n_no_share_change": by_source.get("no_share_change", 0),
        "n_no_price_match": by_source.get("no_price_match", 0),
        "n_no_price_match_no_price_rows": n_unmatched_no_price,
        "n_nominal_small_expected": n_small_nominal,
        "n_ok_apply_ne_nominal": int(str(n_off_pos)),
        "apply_offset_sessions_max": int(str(max_off)),                 # combined 포함 실측
        "apply_offset_sessions_max_individual": int(str(max_off_ind)),  # 자기 창 기준(≤ window)
        "apply_offset_sessions_max_combined": int(str(max_off_comb)),   # 성분 창 기준
        "apply_offset_sessions_median_nonzero": float(str(med_off)),
        "max_ok_product_dev": float(str(max_dev)),
        "n_available_before_announce": int(str(n_avail_before_announce)),
        "near_dup_window_days": near_dup,
        "price_match_lookback_sessions": lookback, "price_match_window_sessions": window,
        "price_match_tol_abs": tol_abs,
        "factor_product_tol": FACTOR_PRODUCT_TOL,
        "factor_source_vocab": list(FACTOR_SOURCE_VOCAB),
        "apply_basis_vocab": list(APPLY_BASIS_VOCAB),
        "factor_bearing_events": list(FACTOR_BEARING_EVENTS),
    }
    return _result("EG3_adj_factor", checks, metrics,
                   "계수·apply_date 불변식·어휘·available 재계산 성립")


eg3_adj_factor.gate_name = "EG3_adj_factor"     # type: ignore[attr-defined]


# ── EG8 — 적용 세션 점프 ─────────────────────────────────────────────────────

def _jump_table(ctx: EquityGateContext, asof: str) -> None:
    """이벤트별 수정수익률·조정 거래량 통계를 `_eg8` 임시 테이블로. 뷰 템플릿을 TEMP MACRO 로
    올려 쓴다. 측정 세션은 apply_date(캘린더 세션임은 EG3_adj_factor 가 보장).

    수익률은 **행 대 행**(그 티커의 직전 가격 행, 참고가 행 포함) — KRX 는 정지 중 참고가 행에
    새 기준가를 먼저 싣고, 뷰가 조정하는 것도 행 시계열이다(GATES §9 S06 3차).
    거래량은 이벤트별 20세션 중앙값 비 `median_after / median_before`(둘 다 조정 거래량 —
    계수 방향이 뒤집히면 share_factor² 배로 튄다).
    """
    views.install_temp_macros(ctx.con, {"price_daily": "price_daily", "adj_factor": ctx.out_view,
                                        "trading_calendar": "trading_calendar"})
    v = _q(ctx.out_view)
    w = VOLUME_MEDIAN_WINDOW
    ctx.con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _eg8 AS
        WITH ev AS (SELECT ticker, effective_date, apply_date, apply_basis, event_id, event_type,
                           factor_ok FROM {v}),
             tk AS (SELECT DISTINCT ticker FROM ev),
             ap AS (SELECT a.*,
                           lag(a.adj_close) OVER (PARTITION BY a.ticker ORDER BY a.date)
                             AS prev_adj_close,
                           lag(a.close) OVER (PARTITION BY a.ticker ORDER BY a.date)
                             AS raw_prev_close
                    FROM v_adj_price(DATE '{asof}') a JOIN tk USING (ticker)),
             av AS (SELECT a.* FROM v_adj_volume(DATE '{asof}') a JOIN tk USING (ticker)),
             ci AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             evn AS (SELECT ev.*, ci.n FROM ev JOIN ci ON ci.date = ev.apply_date),
             ret AS (
               SELECT e.event_id, x.adj_close, x.prev_adj_close, x.close AS raw_close,
                      x.raw_prev_close
               FROM evn e JOIN ap x ON x.ticker = e.ticker AND x.date = e.apply_date),
             vb AS (
               SELECT e.event_id, median(av.adj_volume) AS med_before,
                      count(av.adj_volume) AS n_before
               FROM evn e
               JOIN ci wd ON wd.n BETWEEN e.n - {w} AND e.n - 1
               LEFT JOIN av ON av.ticker = e.ticker AND av.date = wd.date
               GROUP BY e.event_id),
             va AS (
               SELECT e.event_id, median(av.adj_volume) AS med_after,
                      count(av.adj_volume) AS n_after
               FROM evn e
               JOIN ci wd ON wd.n BETWEEN e.n AND e.n + {w} - 1
               LEFT JOIN av ON av.ticker = e.ticker AND av.date = wd.date
               GROUP BY e.event_id),
             vol AS (
               SELECT e.event_id, av.adj_volume
               FROM evn e JOIN av ON av.ticker = e.ticker AND av.date = e.apply_date)
        SELECT e.ticker, e.effective_date, e.apply_date, e.apply_basis, e.event_id, e.event_type,
               e.factor_ok,
               r.adj_close, r.prev_adj_close,
               r.adj_close / nullif(r.prev_adj_close, 0) - 1     AS adj_return,
               r.raw_close / nullif(r.raw_prev_close, 0) - 1     AS raw_return,
               vol.adj_volume, vb.med_before, vb.n_before, va.med_after, va.n_after,
               vol.adj_volume / nullif(vb.med_before, 0)         AS volume_jump,
               va.med_after / nullif(vb.med_before, 0)           AS volume_ratio
        FROM evn e
        LEFT JOIN ret r USING (event_id)
        LEFT JOIN vb USING (event_id)
        LEFT JOIN va USING (event_id)
        LEFT JOIN vol USING (event_id)""")


def eg8_adj_jump(ctx: EquityGateContext) -> GateResult:
    """EG8-P02·P03 (asof = `asof_for_jump_check`, factor_ok 이벤트의 **apply_date** 기준).

    P02 |수정수익률(행 대 행)| ≤ `adj_return_jump_max` — 건별.
    P03 이벤트 집합의 조정 거래량 20세션 중앙값 비 `median_after / median_before` 의 **중앙값** ∈
        [1/band, band], band = `adj_volume_ratio_band` — 집합 통계. 얇은 종목의 재개일 급증은
        정상이라 건별 임계는 의미가 없고, 목적은 계수 방향 오류(÷↔×, share_factor² 배) 탐지다.
        분포 p10/p50/p90/p99·max·> 10 건수·중앙값 미정의(정지 구간) 건수는 기록형.
    상수가 없어도 측정은 한다: asof 는 price_daily 의 max(date) 로 대신 잡고(`asof_basis`),
    결과는 skip(no_baseline) 의 metrics 에 남는다(GATES §7-3 ①). 기록형 보조 축:
    |수정수익률| > 0.30(일반 세션 가격제한폭) 건수 — 거래 재개 첫날은 제한폭이 없어 판정축이
    아니다.
    """
    asof = ctx.baseline.get(ctx.rule.name, "asof_for_jump_check")
    asof_basis = "baseline"
    if asof is None:
        asof = str(_row(ctx, "SELECT CAST(max(date) AS VARCHAR) FROM price_daily")[0])
        asof_basis = "max_price_date"
    _jump_table(ctx, str(asof))
    (n_ok, n_ok_price, n_ok_no_prev, max_ret, n_ret_over_030, n_ratio_judged, n_ratio_undef,
     ratio_med, q10, q90, q99, ratio_max, n_ratio_over, max_day_jump, n_unadj_price,
     max_raw_unadj, n_apply_ne_eff) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {_q(ctx.out_view)} WHERE factor_ok),
          count(*) FILTER (WHERE factor_ok AND adj_close IS NOT NULL),
          count(*) FILTER (WHERE factor_ok AND adj_close IS NOT NULL AND prev_adj_close IS NULL),
          coalesce(max(abs(adj_return)) FILTER (WHERE factor_ok), 0),
          count(*) FILTER (WHERE factor_ok AND abs(adj_return) > {RETURN_REPORT_LIMIT!r}),
          count(*) FILTER (WHERE factor_ok AND volume_ratio IS NOT NULL),
          count(*) FILTER (WHERE factor_ok AND volume_ratio IS NULL AND adj_close IS NOT NULL),
          median(volume_ratio) FILTER (WHERE factor_ok),
          quantile_cont(volume_ratio, 0.1) FILTER (WHERE factor_ok),
          quantile_cont(volume_ratio, 0.9) FILTER (WHERE factor_ok),
          quantile_cont(volume_ratio, 0.99) FILTER (WHERE factor_ok),
          max(volume_ratio) FILTER (WHERE factor_ok),
          count(*) FILTER (WHERE factor_ok AND volume_ratio > {VOLUME_RATIO_REPORT_BAND!r}),
          coalesce(max(volume_jump) FILTER (WHERE factor_ok), 0),
          count(*) FILTER (WHERE NOT factor_ok AND adj_close IS NOT NULL),
          coalesce(max(abs(raw_return)) FILTER (WHERE NOT factor_ok), 0),
          count(*) FILTER (WHERE factor_ok AND apply_date <> effective_date)
        FROM _eg8""")
    sample = [dict(zip(("event_id", "apply_basis", "adj_return", "raw_return", "volume_ratio",
                        "volume_jump", "factor_ok"), r, strict=True))
              for r in ctx.con.execute(f"""
        SELECT event_id, apply_basis, adj_return, raw_return, volume_ratio, volume_jump, factor_ok
        FROM _eg8 WHERE adj_close IS NOT NULL
        ORDER BY abs(adj_return) DESC NULLS LAST, event_id LIMIT {JUMP_SAMPLE_ROWS}""").fetchall()]
    ratio_median = None if ratio_med is None else float(str(ratio_med))
    metrics: dict[str, object] = {
        "asof_used": str(asof), "asof_basis": asof_basis,
        "n_ok_events": int(str(n_ok)), "n_ok_events_with_price": int(str(n_ok_price)),
        "n_ok_no_prev_price": int(str(n_ok_no_prev)),
        "max_abs_adj_return_jump": float(str(max_ret)),
        "n_ok_abs_adj_return_over_030": int(str(n_ret_over_030)),
        "adj_volume_ratio_median": ratio_median,
        "adj_volume_ratio_quantiles": {
            "p10": None if q10 is None else float(str(q10)),
            "p90": None if q90 is None else float(str(q90)),
            "p99": None if q99 is None else float(str(q99)),
            "max": None if ratio_max is None else float(str(ratio_max))},
        "n_ok_volume_ratio_judged": int(str(n_ratio_judged)),
        "n_ok_volume_ratio_undefined": int(str(n_ratio_undef)),
        "n_ok_volume_ratio_over_10": int(str(n_ratio_over)),
        "max_adj_volume_jump_day": float(str(max_day_jump)),
        "n_unadjusted_events_with_price": int(str(n_unadj_price)),
        "max_abs_raw_return_unadjusted": float(str(max_raw_unadj)),
        "n_ok_apply_ne_effective": int(str(n_apply_ne_eff)),
        "volume_median_window": VOLUME_MEDIAN_WINDOW,
        "events": sample,
    }
    if asof_basis != "baseline":
        raise SkipGate("no_baseline", {"missing_metric": f"{ctx.rule.name}.asof_for_jump_check",
                                       **metrics})
    ret_max = require_const(ctx, "adj_return_jump_max", metrics)
    band = require_const(ctx, "adj_volume_ratio_band", metrics)
    n_ret_over = _n(ctx, f"SELECT count(*) FROM _eg8 WHERE factor_ok "
                         f"AND abs(adj_return) > {ret_max!r}")
    out_of_band = int(ratio_median is not None and not (1 / band <= ratio_median <= band))
    checks = {"n_return_jump_over": n_ret_over,
              "n_volume_ratio_out_of_band": out_of_band}
    metrics.update({"adj_return_jump_max": ret_max, "adj_volume_ratio_band": band})
    return _result("EG8", checks, metrics, "적용 세션 수정수익률·조정 거래량 중앙값 비 이내")


eg8_adj_jump.gate_name = "EG8"                  # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

# ── S19 필드 선언 ────────────────────────────────────────────────────────────
# **없다.** 조정가 `price.adj_close` 의 선언은 S23 `price_adj_daily` 로 옮겼다(2026-09-06) —
# 조정가가 뷰가 아니라 표의 컬럼이 되었으므로 소유 테이블은 그 표이고, `dataset_profile` 의
# grain 이 field_id 라 두 표가 같은 field_id 를 선언하면 키가 중복된다. `adj_factor` 는 계수
# 표이고 소비자에게 나가는 필드가 없다(커널 어댑터가 `CorporateActionRecord` 로 읽는 축은
# field_id 가 아니다).

ADJ_FACTOR = register(EquityTable(
    name="adj_factor",
    grain=("ticker", "effective_date", "event_id"),
    columns={"ticker": "VARCHAR", "effective_date": "DATE", "event_id": "VARCHAR",
             "corp_code": "VARCHAR", "event_type": "VARCHAR", "announce_date": "DATE",
             "apply_date": "DATE", "apply_basis": "VARCHAR",
             "price_factor": "DOUBLE", "share_factor": "DOUBLE", "factor_source": "VARCHAR",
             "factor_ok": "BOOLEAN", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("corp_event", "price_daily", "trading_calendar", "stg_event_cr", "security",
            "security_span"),
    partition_class="date_axis",
    partition_key_expr="year(effective_date)",
    available_rule=("derived: min(corp_event.announce_date, apply_date 다음 세션) · 기준가 신규 "
                    "행은 apply_date 다음 세션 — EG2-P02 축 없음(회고 기재 원천은 available < "
                    "announce 가 정상), EG3_adj_factor 가 독립 재계산"),
    # GATES §3-⑩ (S06-2): 좌변 행수 = corp_event 의 계수 대상 이벤트 수 + 기준가 신규 행 수(격리
    # 없음). 신규 행 수 = price_daily 기준가 후보(비ETF·구간 첫날 아님) − (a) 로 사건에 붙은 날짜 −
    # (c) 재발견(주식수 불변 ∧ 직전 행 reference). 후보·재발견은 입력에서 독립 재계산하고 (a) 소비
    # 날짜만 산출의 (ticker, apply_date) 를 읽는다.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=(f"WITH {_BASE_PRICE_CANDIDATES_CTE} "
                 "SELECT (SELECT count(*) FROM corp_event WHERE event_type IN "
                 f"({_vocab_sql(FACTOR_BEARING_EVENTS)})) "
                 f"+ (SELECT count(*) FROM bpc b WHERE {_BP_IN_SCOPE} "
                 "AND NOT (b.share_ratio IS NULL AND b.prev_kind = 'reference') "
                 "AND NOT EXISTS (SELECT 1 FROM out_pq o WHERE o.ticker = b.ticker "
                 f"AND o.apply_date = b.date AND o.apply_basis = '{KRX_BASE_APPLY_BASIS}' "
                 f"AND o.event_type IN ({_vocab_sql(FACTOR_BEARING_EVENTS)})))"),
    sql_path=SQL_DIR / "adj_factor.sql",
    input_columns={
        "corp_event": ("event_id", "ticker", "corp_code", "event_type", "announce_date",
                       "effective_date", "effective_basis", "ratio", "rcept_no", "source"),
        "price_daily": ("ticker", "date", "open", "high", "low", "close", "volume_shr",
                        "price_kind", "base_price_krw", "shares_out"),
        "trading_calendar": ("date",),
        "stg_event_cr": ("rcept_no", "corp_code", "cr_mth", "cr_rs"),
        "security": ("ticker", "sec_type", "corp_code"),
        "security_span": ("ticker", "span_seq", "first_date")},
    available_basis=("derived",),
    content_date_column=None,
    reject_reasons=(),
    consts=("corp_event.near_dup_window_days", *PRICE_MATCH_CONSTS, *BASE_PRICE_CONSTS,
            "corp_event.krx_share_change_tol"),
    extra_gates=(eg3_adj_factor, eg8_adj_jump),
))

TABLES: tuple[EquityTable, ...] = (ADJ_FACTOR,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s06.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측 + 서버 재측정 표기). 승인 뒤 `baseline.json`
에 병합."""

__all__ = ["ADJ_FACTOR", "APPLY_BASIS_VOCAB", "BASELINE_SEED", "BASE_PRICE_CONSTS",
           "EVENT_TYPE_VOCAB", "FACTOR_BEARING_EVENTS", "FACTOR_PRODUCT_TOL",
           "FACTOR_SOURCE_VOCAB", "KRX_BASE_APPLY_BASIS", "KRX_BASE_EVENT_ID_INFIX",
           "KRX_BASE_EVENT_TYPES", "OK_APPLY_BASIS", "OK_FACTOR_SOURCE", "PRICE_MATCH_CONSTS",
           "RETURN_REPORT_LIMIT", "TABLES", "VOLUME_MEDIAN_WINDOW", "VOLUME_RATIO_REPORT_BAND"]
