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

입력 — equity `corp_event`·`price_daily`(close·price_kind: 매칭 축 + EG8)·`trading_calendar` +
`stg_event_cr`(감자 유·무상 판정 축 `cr_mth`·`cr_rs`: corp_event 에는 구분 컬럼이 없다). 상수는
`corp_event.near_dup_window_days` 와 `adj_factor.price_match_*` 4개를 `_const` 로 읽는다
(build.make_consts 의 `<table>.<metric>` 키).

테이블 특화 술어(`extra_gates`):
  EG3_adj_factor — 어휘 폐쇄(factor_source·apply_basis·event_type) · EG3-P04 시총 불변
                   `price×share = 1`(허용오차 FACTOR_PRODUCT_TOL) · ok 행 `share_factor = ratio` ·
                   not-ok 행 계수 1 · apply_date 불변식(캘린더 세션 · 명목 세션 기준 창 안 ·
                   nominal 은 명목 세션 · 복합 성분은 같은 apply_date · 개별 ok 2건이 같은 날 0) ·
                   `available_date = min(announce, apply_date 다음 세션)` 독립 재계산(EG2-P02
                   대체 — announce 축은 회고 기재 원천에서 available < announce 가 정상이라 못
                   쓴다) · corp_event 정합. 기록형: 사유·apply_basis 별 건수, 오프셋 분포.
  EG8            — P02 수정수익률 점프·P03 조정 거래량 점프를 **apply_date** 에서 잰다(`views` 의
                   같은 템플릿을 TEMP MACRO 로 올려 계산). 거래량 중앙값 0 인 이벤트는 P03 분모에서
                   빼고 건수만 기록. 상수 미등재면 skip(no_baseline) 이되 **metric 은 항상 계산**.
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

# 계수를 내는 이벤트 어휘 (GATES §3-⑩ `_reg_vocab('factor_bearing_event')`) = S05 MVP 4종.
FACTOR_BEARING_EVENTS: tuple[str, ...] = MVP_EVENT_TYPES
# factor_source 폐쇄 어휘 — mktcap_neutral 만 factor_ok=true 다(사유 우선순위는 sql/adj_factor.sql).
FACTOR_SOURCE_VOCAB: tuple[str, ...] = ("mktcap_neutral", "ratio_null", "capred_paid",
                                        "near_dup_suppressed", "no_price_match",
                                        "same_day_suppressed")
OK_FACTOR_SOURCE = "mktcap_neutral"
# apply_basis 폐쇄 어휘. ok 행은 앞 3개, no_price_match 행만 unmatched, 그 외 not-ok 행은 nominal.
APPLY_BASIS_VOCAB: tuple[str, ...] = ("nominal", "price_matched", "price_matched_combined",
                                      "unmatched")
OK_APPLY_BASIS: tuple[str, ...] = ("nominal", "price_matched", "price_matched_combined")
# baseline 상수 — 가격 매칭 창·허용치 (adj_factor 네임스페이스) + 근접 중복 창 (corp_event)
PRICE_MATCH_CONSTS: tuple[str, ...] = ("price_match_tol_rel", "price_match_tol_abs",
                                       "price_match_window_sessions",
                                       "price_match_lookback_sessions")
# EG3-P04 허용오차 — baseline 상수가 아니라 **산출 정밀도** 다: price_factor = 1/ratio 를 DOUBLE 로
# 두므로 곱은 1 ± 몇 ulp 다(실측 max |1/x·x − 1| = 1.1e-16, x ∈ [1, 1e5]). 1e-12 는 그 1e4 배
# 여유이고 계수 방향 오류(역수·제곱)는 1e-12 로는 절대 못 숨긴다.
FACTOR_PRODUCT_TOL = 1e-12
# EG8-P03 중앙값 창(직전 거래일 수, GATES §1 EG8 M03 의 'ROWS BETWEEN 20 PRECEDING'). 임계가 아니라
# 측정 방법의 일부라 코드 상수다.
VOLUME_MEDIAN_WINDOW = 20
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
    """EG3-P04·P07·P13 + 계수·apply_date 불변식 + available_date 독립 재계산 + corp_event 정합."""
    v = _q(ctx.out_view)
    (n_src_vocab, n_basis_vocab, n_type_vocab, n_ticker_bad, n_product_off, n_not_ok_ne_one,
     n_ok_source, n_ok_basis_bad, n_unmatched_mismatch, n_avail_null, n_basis, n_apply_null,
     max_dev) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE factor_source IS NULL
             OR factor_source NOT IN ({_vocab_sql(FACTOR_SOURCE_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE apply_basis IS NULL
             OR apply_basis NOT IN ({_vocab_sql(APPLY_BASIS_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE event_type IS NULL
             OR event_type NOT IN ({_vocab_sql(FACTOR_BEARING_EVENTS)})),
          (SELECT count(*) FROM {v} WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
             OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v} WHERE factor_ok
             AND (price_factor IS NULL OR share_factor IS NULL
                  OR abs(price_factor * share_factor - 1) > {FACTOR_PRODUCT_TOL!r})),
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
    win_sql = ("count(*) FILTER (WHERE n_apply IS NOT NULL AND n_nom IS NOT NULL AND "
               f"(n_apply < n_nom - {int(lookback)} OR n_apply > n_nom + {int(window)}))"
               if lookback is not None and window is not None else "NULL")
    (n_event_mismatch, n_share_ne_ratio, n_avail_mismatch, n_avail_before_announce,
     n_apply_off_cal, n_nominal_ne, n_apply_outside, max_off, n_off_pos, med_off) = _row(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             j AS (
          SELECT a.*, e.event_id AS e_id, e.ticker AS e_ticker, e.effective_date AS e_eff,
                 e.event_type AS e_type, e.announce_date AS e_ann, e.corp_code AS e_corp, e.ratio,
                 ca.n AS n_apply,
                 (SELECT min(c.n) FROM cal c WHERE c.date >= a.effective_date) AS n_nom,
                 (SELECT c.date FROM cal c WHERE c.n = ca.n + 1) AS next_session
          FROM {v} a
          LEFT JOIN corp_event e ON e.event_id = a.event_id
          LEFT JOIN cal ca ON ca.date = a.apply_date)
        SELECT
          count(*) FILTER (WHERE e_id IS NULL OR ticker IS DISTINCT FROM e_ticker
                              OR effective_date IS DISTINCT FROM e_eff
                              OR event_type IS DISTINCT FROM e_type
                              OR announce_date IS DISTINCT FROM e_ann
                              OR corp_code IS DISTINCT FROM e_corp),
          count(*) FILTER (WHERE factor_ok AND share_factor IS DISTINCT FROM ratio),
          count(*) FILTER (WHERE available_date IS DISTINCT FROM least(e_ann, next_session)),
          count(*) FILTER (WHERE available_date < e_ann),
          count(*) FILTER (WHERE n_apply IS NULL),
          count(*) FILTER (WHERE apply_basis = 'nominal' AND n_apply IS DISTINCT FROM n_nom),
          {win_sql},
          coalesce(max(n_apply - n_nom) FILTER (WHERE factor_ok), 0),
          count(*) FILTER (WHERE factor_ok AND n_apply <> n_nom),
          coalesce(median(n_apply - n_nom) FILTER (WHERE factor_ok AND n_apply <> n_nom), 0)
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
    basis_by_type = {f"{r[0]}:{r[1]}": int(str(r[2])) for r in ctx.con.execute(
        f"SELECT event_type, apply_basis, count(*) FROM {v} WHERE factor_ok "
        "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()}
    tol_abs = _const_or_none(ctx, ctx.rule.name, "price_match_tol_abs")
    n_small_nominal = (_n(ctx, f"""
        SELECT count(*) FROM {v} WHERE factor_ok AND apply_basis = 'nominal'
          AND abs(least(price_factor, share_factor) - 1) <= {tol_abs!r}""")
        if tol_abs is not None else None)
    # no_price_match 중 창 안에 거래 행이 아예 없는 사건(상장폐지 기간 등) — 매칭 실패가 아니라
    # 가격 부재
    n_unmatched_no_price = _n(ctx, f"""
        WITH cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             u AS (SELECT a.ticker, a.event_id,
                          (SELECT min(x.n) FROM cal x WHERE x.date >= a.effective_date) AS n_nom
                   FROM {v} a WHERE factor_source = 'no_price_match')
        SELECT count(*) FROM u
        WHERE NOT EXISTS (
          SELECT 1 FROM price_daily p JOIN cal c ON c.date = p.date
          WHERE p.ticker = u.ticker AND p.price_kind = 'trade'
            AND c.n BETWEEN u.n_nom - {int(lookback) if lookback is not None else 0}
                        AND u.n_nom + {int(window) if window is not None else 0})""")
    metrics: dict[str, object] = {
        "n_ok": _n(ctx, f"SELECT count(*) FROM {v} WHERE factor_ok"),
        "n_by_factor_source": by_source,
        "n_by_apply_basis": by_basis,
        "n_ok_by_event_type_apply_basis": basis_by_type,
        "n_by_event_type": _counts(ctx, "event_type"),
        "n_near_dup_suppressed": by_source.get("near_dup_suppressed", 0),
        "n_same_day_suppressed": by_source.get("same_day_suppressed", 0),
        "n_ratio_null": by_source.get("ratio_null", 0),
        "n_capred_paid": by_source.get("capred_paid", 0),
        "n_no_price_match": by_source.get("no_price_match", 0),
        "n_no_price_match_no_price_rows": n_unmatched_no_price,
        "n_nominal_small_expected": n_small_nominal,
        "n_ok_apply_ne_nominal": int(str(n_off_pos)),
        "apply_offset_sessions_max": int(str(max_off)),
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
    """이벤트별 수정수익률·조정 거래량 점프를 `_eg8` 임시 테이블로. 뷰 템플릿을 TEMP MACRO 로
    올려 쓴다. 측정 세션은 apply_date(캘린더 세션임은 EG3_adj_factor 가 보장)."""
    views.install_temp_macros(ctx.con, {"price_daily": "price_daily", "adj_factor": ctx.out_view,
                                        "trading_calendar": "trading_calendar"})
    v = _q(ctx.out_view)
    w = VOLUME_MEDIAN_WINDOW
    ctx.con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _eg8 AS
        WITH ev AS (SELECT ticker, effective_date, apply_date, apply_basis, event_id, event_type,
                           factor_ok FROM {v}),
             tk AS (SELECT DISTINCT ticker FROM ev),
             ap AS (SELECT a.* FROM v_adj_price(DATE '{asof}') a JOIN tk USING (ticker)),
             av AS (SELECT a.* FROM v_adj_volume(DATE '{asof}') a JOIN tk USING (ticker)),
             ci AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar),
             evn AS (SELECT ev.*, ci.n FROM ev JOIN ci ON ci.date = ev.apply_date),
             ret AS (
               SELECT e.event_id, cur.adj_close, prv.adj_close AS prev_adj_close,
                      cur.close AS raw_close, prv.close AS raw_prev_close
               FROM evn e
               JOIN ap cur ON cur.ticker = e.ticker AND cur.date = e.apply_date
               LEFT JOIN ci p ON p.n = e.n - 1
               LEFT JOIN ap prv ON prv.ticker = e.ticker AND prv.date = p.date),
             med AS (
               SELECT e.event_id, median(av.adj_volume) AS med_window,
                      count(av.adj_volume) AS n_window
               FROM evn e
               JOIN ci wd ON wd.n BETWEEN e.n - {w} AND e.n - 1
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
               vol.adj_volume, m.med_window, m.n_window,
               vol.adj_volume / nullif(m.med_window, 0)          AS volume_jump
        FROM evn e
        LEFT JOIN ret r USING (event_id)
        LEFT JOIN med m USING (event_id)
        LEFT JOIN vol USING (event_id)""")


def eg8_adj_jump(ctx: EquityGateContext) -> GateResult:
    """EG8-P02·P03 — factor_ok 이벤트의 **apply_date** 에서 |수정수익률| ≤ `adj_return_jump_max` ∧
    조정 거래량 / 직전 20거래일 중앙값 ≤ `adj_volume_jump_max` (asof = `asof_for_jump_check`).

    상수가 없어도 측정은 한다: asof 는 price_daily 의 max(date) 로 대신 잡고(`asof_basis`),
    결과는 skip(no_baseline) 의 metrics 에 남는다(GATES §7-3 ①). 거래량 중앙값이 0 인 이벤트
    (정지 뒤 재개 등)는 P03 을 판정할 수 없어 분모에서 빼고 `n_ok_median_zero` 로 기록한다.
    """
    asof = ctx.baseline.get(ctx.rule.name, "asof_for_jump_check")
    asof_basis = "baseline"
    if asof is None:
        asof = str(_row(ctx, "SELECT CAST(max(date) AS VARCHAR) FROM price_daily")[0])
        asof_basis = "max_price_date"
    _jump_table(ctx, str(asof))
    (n_ok, n_ok_price, n_ok_no_prev, n_med_zero, n_vol_judged, max_ret, max_vol, n_unadj_price,
     max_raw_unadj, n_apply_ne_eff) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {_q(ctx.out_view)} WHERE factor_ok),
          count(*) FILTER (WHERE factor_ok AND adj_close IS NOT NULL),
          count(*) FILTER (WHERE factor_ok AND adj_close IS NOT NULL AND prev_adj_close IS NULL),
          count(*) FILTER (WHERE factor_ok AND adj_volume IS NOT NULL
                             AND coalesce(med_window, 0) = 0),
          count(*) FILTER (WHERE factor_ok AND volume_jump IS NOT NULL),
          coalesce(max(abs(adj_return)) FILTER (WHERE factor_ok), 0),
          coalesce(max(volume_jump) FILTER (WHERE factor_ok), 0),
          count(*) FILTER (WHERE NOT factor_ok AND adj_close IS NOT NULL),
          coalesce(max(abs(raw_return)) FILTER (WHERE NOT factor_ok), 0),
          count(*) FILTER (WHERE factor_ok AND apply_date <> effective_date)
        FROM _eg8""")
    sample = [dict(zip(("event_id", "apply_basis", "adj_return", "raw_return", "volume_jump",
                        "factor_ok"), r, strict=True))
              for r in ctx.con.execute(f"""
        SELECT event_id, apply_basis, adj_return, raw_return, volume_jump, factor_ok FROM _eg8
        WHERE adj_close IS NOT NULL
        ORDER BY abs(adj_return) DESC NULLS LAST, event_id LIMIT {JUMP_SAMPLE_ROWS}""").fetchall()]
    metrics: dict[str, object] = {
        "asof_used": str(asof), "asof_basis": asof_basis,
        "n_ok_events": int(str(n_ok)), "n_ok_events_with_price": int(str(n_ok_price)),
        "n_ok_no_prev_price": int(str(n_ok_no_prev)),
        "n_ok_median_zero": int(str(n_med_zero)),
        "n_ok_volume_judged": int(str(n_vol_judged)),
        "max_abs_adj_return_jump": float(str(max_ret)),
        "max_adj_volume_jump": float(str(max_vol)),
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
    vol_max = require_const(ctx, "adj_volume_jump_max", metrics)
    n_ret_over, n_vol_over = _row(ctx, f"""
        SELECT count(*) FILTER (WHERE factor_ok AND abs(adj_return) > {ret_max!r}),
               count(*) FILTER (WHERE factor_ok AND coalesce(med_window, 0) > 0
                                  AND volume_jump > {vol_max!r})
        FROM _eg8""")
    checks = {"n_return_jump_over": int(str(n_ret_over)),
              "n_volume_jump_over": int(str(n_vol_over))}
    metrics.update({"adj_return_jump_max": ret_max, "adj_volume_jump_max": vol_max})
    return _result("EG8", checks, metrics, "적용 세션 수정수익률·조정 거래량 점프 이내")


eg8_adj_jump.gate_name = "EG8"                  # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

ADJ_FACTOR = register(EquityTable(
    name="adj_factor",
    grain=("ticker", "effective_date", "event_id"),
    columns={"ticker": "VARCHAR", "effective_date": "DATE", "event_id": "VARCHAR",
             "corp_code": "VARCHAR", "event_type": "VARCHAR", "announce_date": "DATE",
             "apply_date": "DATE", "apply_basis": "VARCHAR",
             "price_factor": "DOUBLE", "share_factor": "DOUBLE", "factor_source": "VARCHAR",
             "factor_ok": "BOOLEAN", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("corp_event", "price_daily", "trading_calendar", "stg_event_cr"),
    partition_class="date_axis",
    partition_key_expr="year(effective_date)",
    available_rule=("derived: min(corp_event.announce_date, apply_date 다음 세션) — "
                    "EG2-P02 축 없음(회고 기재 원천은 available < announce 가 정상), "
                    "EG3_adj_factor 가 독립 재계산"),
    # GATES §3-⑩: 좌변 행수 = corp_event 의 계수 대상 이벤트 수(격리 없음).
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=("SELECT count(*) FROM corp_event WHERE event_type IN "
                 f"({_vocab_sql(FACTOR_BEARING_EVENTS)})"),
    sql_path=SQL_DIR / "adj_factor.sql",
    input_columns={
        "corp_event": ("event_id", "ticker", "corp_code", "event_type", "announce_date",
                       "effective_date", "effective_basis", "ratio", "rcept_no", "source"),
        "price_daily": ("ticker", "date", "open", "high", "low", "close", "volume_shr",
                        "price_kind"),
        "trading_calendar": ("date",),
        "stg_event_cr": ("rcept_no", "corp_code", "cr_mth", "cr_rs")},
    available_basis=("derived",),
    content_date_column=None,
    reject_reasons=(),
    consts=("corp_event.near_dup_window_days", *PRICE_MATCH_CONSTS),
    extra_gates=(eg3_adj_factor, eg8_adj_jump),
))

TABLES: tuple[EquityTable, ...] = (ADJ_FACTOR,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s06.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측 + 서버 재측정 표기). 승인 뒤 `baseline.json`
에 병합."""

__all__ = ["ADJ_FACTOR", "APPLY_BASIS_VOCAB", "BASELINE_SEED", "FACTOR_BEARING_EVENTS",
           "FACTOR_PRODUCT_TOL", "FACTOR_SOURCE_VOCAB", "OK_APPLY_BASIS", "OK_FACTOR_SOURCE",
           "PRICE_MATCH_CONSTS", "TABLES", "VOLUME_MEDIAN_WINDOW"]
