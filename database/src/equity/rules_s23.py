"""S23 전방 조정가 슬라이스 — `price_adj_daily` (DESIGN v1.2 §4-2 · GATES §3-㉓ · §9 S23).

조정가는 S21 후속(09-05)까지 **duckdb 매크로 `v_adj_price_fwd` 로만** 존재했다. parquet 을 직접
읽는 소비자(커널 pyarrow 어댑터·분석 노트북)는 못 봤고, 카탈로그가 낡거나 없으면 통째로
`unavailable` 이 됐다(`test_missing_or_stale_catalog_makes_adj_close_unavailable` 이 그 동작을
단언하고 있었다). 09-05 결정으로 **전방 조정**을 택해 값이 (ticker, date) 의 순수 함수가 됐으므로
이제 저장할 수 있다 — 이 표가 그 저장이다.

산출식은 `sql/price_adj_daily.sql` 머리말이 정본이다. 요약:
  adj_close(d)      = close(d)      × Π(share_factor : ok ∧ 같은 구간 ∧ fold_date ≤ d)
  adj_volume_shr(d) = volume_shr(d) × Π(price_factor : 같은 집합)
  fold_date = greatest(apply_date, available_date) · 누적은 (ticker, span_seq) 안에서만

**`price_daily` 에 컬럼을 더하지 않는다** — `adj_factor` 가 `price_daily` 를 입력으로 쓰므로
조정가를 그 표에 두면 price_daily → adj_factor → price_daily 순환이 된다. 원주가도 여기 싣지
않는다(정본은 `price_daily`, 복제는 드리프트를 부른다).

**엔진 커널에 연결하지 마라** — 커널(`backtest_engine`)은 원주가 bar + `CorporateActionEvent` 로
포지션 수량을 스스로 조정한다. 조정가를 주면 같은 사건이 두 번 반영된다(DESIGN §7).

테이블 특화 술어(`extra_gates`): `EG3_price_adj_daily`(폐기형) — 아래 `eg3_price_adj_daily` 참조.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from . import views
from .gates import EquityGateContext
from .model import EquityTable, FieldProfile, register
from .rules_s01 import TICKER_LEN
from .rules_s06 import FACTOR_PRODUCT_TOL

SQL_DIR = Path(__file__).parent / "sql"
TABLE_NAME = "price_adj_daily"

# `adj_factor` 네임스페이스의 상수를 복제 없이 읽는다 — 한 계수의 `price_factor × share_factor`
# 허용오차(기준가 원천은 KRX 산식 잔여 때문에 정확히 1 이 아니다, DESIGN §4-2 v3 ③).
PRODUCT_TOL_TABLE, PRODUCT_TOL_METRIC = "adj_factor", "factor_product_tol_base"
# 매크로 대조의 상대 허용오차. 임계가 아니라 **부동소수 동일성의 표현**이다 — 두 산출식이 같은
# 순서로 곱하면 비트까지 같지만, duckdb 가 창 집계를 병렬로 쪼개면 마지막 자리가 흔들릴 수 있다.
# 계수 방향 오류(역수·제곱)나 구간 누출은 1e-12 로는 절대 못 숨는다.
MACRO_MATCH_TOL = 1e-12
# 매크로 대조 대상 — 표가 커버하는 두 전방 조정 뷰.
MACRO_VIEWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("v_adj_price_fwd", ("adj_open", "adj_high", "adj_low", "adj_close",
                         "cum_price_factor", "cum_share_factor", "available_date")),
    ("v_adj_volume_fwd", ("adj_volume_shr", "cum_price_factor", "cum_share_factor",
                          "available_date")),
)
# 매크로 컬럼명 → 표 컬럼명 (거래량 축만 이름이 다르다 — 표는 stage 단위 접미사 규약 `_shr`).
MACRO_COLUMN_ALIAS: dict[str, str] = {"adj_volume_shr": "adj_volume"}
SAMPLE_DATES = ("trading_calendar", "asof_sample_dates")
SAMPLE_TICKERS = ("trading_calendar", "asof_sample_tickers")
RECALC_TABLE = "_s23_recalc"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _n(ctx: EquityGateContext, sql: str) -> int:
    return int(str(_row(ctx, sql)[0]))


def _lit_list(values: list[str], cast: str) -> str:
    inner = ", ".join(f"{cast} '{v}'" if cast else f"'{v}'" for v in values)
    return f"[{inner}]"


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def install_recalc(ctx: EquityGateContext) -> None:
    """산출과 **독립으로 다시 계산한** 조정값을 `_s23_recalc` 로 올린다.

    `sql/price_adj_daily.sql` 을 재사용하지 않는다 — 같은 식을 두 번 돌리면 항진명제다. 산출은
    ASOF JOIN + 창 누적곱이고 여기서는 **범위 조인 + GROUP BY 집계**로 같은 값을 만든다:

      구간 앵커  span_first = max(security_span.first_date : first_date ≤ date)  (범위 조인 집계)
      ok 계수    span_first < fold_date ≤ date                                   (구간 안 = 앵커 뒤)
      미조정     span_first ≤ apply_date ≤ date

    산출의 "구간 부여 후 span_seq 로 조인" 과 이 "행의 앵커로 구간을 자르기" 는 같은 집합이다:
    구간이 서로 겹치지 않고 first_date 순서라, 계수의 구간 = 행의 구간 ⟺ 앵커 < fold ≤ date.
    앵커가 없는 행(첫 구간보다 이른 가격 행)에서는 양변 다 공집합이다(산출은 계수 쪽 span_seq 를
    −1 로 두어 어떤 행과도 만나지 않게 한다).
    """
    ctx.con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {_q(RECALC_TABLE)} AS
        WITH anchor AS (
            SELECT p.ticker, p.date, max(s.first_date) AS span_first
            FROM price_daily p
            LEFT JOIN security_span s ON s.ticker = p.ticker AND s.first_date <= p.date
            GROUP BY p.ticker, p.date
        ),
        okf AS (
            SELECT ticker, greatest(apply_date, available_date) AS fold_date,
                   price_factor, share_factor, available_date
            FROM adj_factor WHERE factor_ok
        ),
        agg AS (
            SELECT a.ticker, a.date, a.span_first,
                   coalesce(product(f.price_factor), 1) AS cum_price_factor,
                   coalesce(product(f.share_factor), 1) AS cum_share_factor,
                   count(f.fold_date)                   AS n_factors_applied,
                   max(f.available_date)                AS factor_available_date,
                   coalesce(product(f.price_factor * f.share_factor), 1) AS product_of_products
            FROM anchor a
            LEFT JOIN okf f ON f.ticker = a.ticker
                 AND f.fold_date > a.span_first AND f.fold_date <= a.date
            GROUP BY a.ticker, a.date, a.span_first
        ),
        unadj AS (
            SELECT a.ticker, a.date, count(e.apply_date) AS n_unadjusted_events
            FROM anchor a
            LEFT JOIN (SELECT ticker, apply_date FROM adj_factor WHERE NOT factor_ok) e
                 ON e.ticker = a.ticker
                 AND e.apply_date >= a.span_first AND e.apply_date <= a.date
            GROUP BY a.ticker, a.date
        ),
        -- 구간 규칙을 **끄고** 잰 값 — 이전 구간 계수가 넘어왔을 때의 크기(기록형).
        free AS (
            SELECT a.ticker, a.date,
                   coalesce(product(f.share_factor), 1) AS cum_share_factor_span_free
            FROM anchor a
            LEFT JOIN okf f ON f.ticker = a.ticker AND f.fold_date <= a.date
            GROUP BY a.ticker, a.date
        )
        SELECT o.*, g.span_first, g.cum_price_factor AS r_cum_price_factor,
               g.cum_share_factor AS r_cum_share_factor,
               g.n_factors_applied AS r_n_factors_applied,
               g.product_of_products AS r_product_of_products,
               greatest(o.date, coalesce(g.factor_available_date, o.date)) AS r_available_date,
               u.n_unadjusted_events AS r_n_unadjusted_events,
               fr.cum_share_factor_span_free,
               p.open AS raw_open, p.high AS raw_high, p.low AS raw_low, p.close AS raw_close,
               p.volume_shr AS raw_volume_shr
        FROM {_q(ctx.out_view)} o
        JOIN agg g   ON g.ticker = o.ticker AND g.date = o.date
        JOIN unadj u ON u.ticker = o.ticker AND u.date = o.date
        JOIN free fr ON fr.ticker = o.ticker AND fr.date = o.date
        JOIN price_daily p ON p.ticker = o.ticker AND p.date = o.date""")


def _macro_mismatch(ctx: EquityGateContext) -> tuple[int, dict[str, object]]:
    """매크로 `v_adj_price_fwd`·`v_adj_volume_fwd` 와 이 표가 같은 값을 내는지 (as-of 표본 축).

    두 산출은 **다른 파일에 사는 같은 규칙**이다(표 = `sql/price_adj_daily.sql`, 매크로 =
    `views._FWD_CTE`). 표를 읽는 얇은 매크로로 합치지 않은 이유는 매크로의 `lag_override` 가
    PIT 계약의 일부이기 때문이다 — 기본값 밖 랙에서는 "아직 공개 전인 계수를 접지 않는다" 를
    계수 컷오프로 다시 계산해야 하는데(회귀 테스트가 그 동작을 단언한다) 표에는 랙 축이 없다.
    대신 **매 빌드 이 게이트가 기본 랙에서 두 산출의 동일성을 증명**한다.

    기본 랙에서 둘이 같은 것은 우연이 아니다: 매크로가 접는 집합은
    {계수 : fold ≤ d} ∩ {apply ≤ as_of ∧ available ≤ as_of} 인데 fold = greatest(apply, available)
    ≤ d ≤ as_of 이면 뒤 조건이 자동으로 선다. 그래서 d ≤ as_of 인 모든 행에서 같은 집합이다.
    """
    dates = ctx.baseline.get(*SAMPLE_DATES)
    tickers = ctx.baseline.get(*SAMPLE_TICKERS)
    basis = "baseline_asof_sample"
    if not isinstance(dates, list) or not dates:
        dates = [str(_row(ctx, "SELECT CAST(max(date) AS VARCHAR) FROM price_daily")[0])]
        basis = "max_price_date"
    if not isinstance(tickers, list) or not tickers:
        tickers = [str(r[0]) for r in
                   ctx.con.execute("SELECT DISTINCT ticker FROM adj_factor ORDER BY 1").fetchall()]
        basis += "+factor_tickers"
    made = views.install_temp_macros(ctx.con, {
        "price_daily": "price_daily", "adj_factor": "adj_factor",
        "trading_calendar": "trading_calendar", "security_span": "security_span"})
    absent = [v for v, _ in MACRO_VIEWS if v not in made]
    if absent:
        raise RuntimeError(f"forward-adjust macros could not be installed: missing={absent} "
                           f"made={made} — views.MACRO_INPUTS 와 이 표의 inputs 가 갈렸다")
    tk = _lit_list([str(t) for t in tickers], "")
    total_rows, total_bad, total_missing = 0, 0, 0
    per_view: dict[str, object] = {}
    max_dev = 0.0
    for view, columns in MACRO_VIEWS:
        for as_of in dates:
            num = [c for c in columns if c != "available_date"]
            conds = [f"((m.{_q(MACRO_COLUMN_ALIAS.get(c, c))} IS NULL) <> (t.{_q(c)} IS NULL) "
                     f"OR abs(coalesce(m.{_q(MACRO_COLUMN_ALIAS.get(c, c))}, 0) "
                     f"- coalesce(t.{_q(c)}, 0)) "
                     f"> {MACRO_MATCH_TOL!r} * greatest(abs(coalesce(t.{_q(c)}, 0)), 1))"
                     for c in num]
            if "available_date" in columns:
                conds.append("(m.available_date IS DISTINCT FROM t.available_date)")
            devs = ", ".join(
                f"abs(coalesce(m.{_q(MACRO_COLUMN_ALIAS.get(c, c))}, 0) - coalesce(t.{_q(c)}, 0)) "
                f"/ greatest(abs(coalesce(t.{_q(c)}, 0)), 1)" for c in num)
            n_rows, n_bad, dev, n_miss = _row(ctx, f"""
                WITH m AS (SELECT * FROM {view}(DATE '{as_of}')
                           WHERE ticker IN (SELECT unnest({tk}))),
                     t AS (SELECT * FROM {_q(ctx.out_view)}
                           WHERE date <= DATE '{as_of}' AND ticker IN (SELECT unnest({tk})))
                SELECT (SELECT count(*) FROM m),
                       (SELECT count(*) FROM m JOIN t USING (ticker, date)
                          WHERE {' OR '.join(conds)}),
                       (SELECT coalesce(max(greatest({devs})), 0)
                          FROM m JOIN t USING (ticker, date)),
                       (SELECT count(*) FROM m FULL JOIN t USING (ticker, date)
                          WHERE m.ticker IS NULL OR t.ticker IS NULL)""")
            total_rows += int(str(n_rows))
            total_bad += int(str(n_bad))
            total_missing += int(str(n_miss))
            max_dev = max(max_dev, float(str(dev)))
            per_view[f"{view}@{as_of}"] = {"n_rows": int(str(n_rows)), "n_diff": int(str(n_bad)),
                                           "n_row_set_diff": int(str(n_miss))}
    return total_bad + total_missing, {
        "macro_asof_basis": basis, "macro_asof_dates": [str(d) for d in dates],
        "macro_sample_tickers": len(tickers), "macro_compared_rows": total_rows,
        "macro_row_set_diff": total_missing, "macro_max_rel_dev": max_dev,
        "macro_match_tol": MACRO_MATCH_TOL, "macro_by_view": per_view}


def eg3_price_adj_daily(ctx: EquityGateContext) -> GateResult:
    """EG3_price_adj_daily (폐기형) — 전방 조정의 불변식 전부.

    폐기 술어
      ① `cum_price_factor > 0 ∧ cum_share_factor > 0`  (0·음수·NULL 계수는 조정이 아니다)
      ② `cum_price × cum_share = 1`  — 확정 계수는 전부 시총 불변이다. 허용오차는 한 계수의
         `adj_factor.factor_product_tol_base` 를 접힌 수만큼 복리로 편 `(1+tol)^n − 1` 이고,
         **접힌 계수가 없으면 정확히 1** 이다. 항등이 `Π(pf·sf)` 와 같은지도 함께 본다
      ③ 구간 첫 행의 누적 = 1 ∧ `n_factors_applied` = 0  (앵커 = 첫 관측)
      ④ 독립 재계산(`install_recalc`)과 조정값 8축 전건 일치 — 나눗셈으로 뒤집으면 여기서 걸린다
      ⑤ `n_unadjusted_events` 독립 재계산 일치
      ⑥ 구간 밖 계수 유입 0 — 접힌 수가 구간 안 계수 수를 넘지 않는다
      ⑦ `available_date = date` ∧ basis 'derived' (fold 규약의 귀결을 산출로 증명한다)
      ⑧ `date` 가 캘린더 세션 ∧ ticker 6자리
      ⑨ 매크로(`v_adj_price_fwd`·`v_adj_volume_fwd`) 정합 — `_macro_mismatch`

    기록형: 구간 없는 행 수 · 구간 규칙을 껐을 때 달라지는 행 수(= 이전 구간 계수 누출 크기) ·
    미조정 사건이 걸린 행·종목 수와 비율 · **사유(`factor_source`)별 내역** · 누적계수 분포 ·
    매크로 최대 상대편차.
    """
    v = _q(ctx.out_view)
    r = _q(RECALC_TABLE)
    install_recalc(ctx)
    base_tol = ctx.baseline.get(PRODUCT_TOL_TABLE, PRODUCT_TOL_METRIC)
    tol_basis = "baseline" if base_tol is not None else "code"
    tol = float(str(base_tol)) if base_tol is not None else FACTOR_PRODUCT_TOL
    # 접힌 계수 n 개면 각 계수의 곱 오차가 복리로 쌓인다. n = 0 이면 정확히 1 이어야 한다.
    tol_expr = (f"CASE WHEN n_factors_applied = 0 THEN {FACTOR_PRODUCT_TOL!r} "
                f"ELSE pow(1 + {tol!r}, n_factors_applied) - 1 END")
    (n_cum_bad, n_product_off, n_product_recalc_off, max_dev, n_avail_bad, n_basis_bad,
     n_ticker_bad, n_off_cal) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE cum_price_factor IS NULL OR cum_share_factor IS NULL
             OR cum_price_factor <= 0 OR cum_share_factor <= 0),
          (SELECT count(*) FROM {v}
             WHERE abs(cum_price_factor * cum_share_factor - 1) > {tol_expr}),
          (SELECT count(*) FROM {r}
             WHERE abs(cum_price_factor * cum_share_factor - r_product_of_products)
                   > {FACTOR_PRODUCT_TOL!r} * greatest(abs(r_product_of_products), 1)),
          (SELECT coalesce(max(abs(cum_price_factor * cum_share_factor - 1)), 0) FROM {v}),
          (SELECT count(*) FROM {v} WHERE available_date IS DISTINCT FROM date),
          (SELECT count(*) FROM {v} WHERE available_basis IS DISTINCT FROM 'derived'),
          (SELECT count(*) FROM {v} WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
             OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v} o
             WHERE NOT EXISTS (SELECT 1 FROM trading_calendar c WHERE c.date = o.date))""")
    # 독립 재계산 대조 — 값 8축 + available_date. NULL 은 NULL 로 같아야 한다.
    num_axes = (("adj_open", "raw_open * r_cum_share_factor"),
                ("adj_high", "raw_high * r_cum_share_factor"),
                ("adj_low", "raw_low * r_cum_share_factor"),
                ("adj_close", "raw_close * r_cum_share_factor"),
                ("adj_volume_shr", "raw_volume_shr * r_cum_price_factor"),
                ("cum_price_factor", "r_cum_price_factor"),
                ("cum_share_factor", "r_cum_share_factor"))
    axis_conds = " OR ".join(
        f"(({_q(c)} IS NULL) <> (({e}) IS NULL) "
        f"OR abs(coalesce({_q(c)}, 0) - coalesce({e}, 0)) "
        f"> {FACTOR_PRODUCT_TOL!r} * greatest(abs(coalesce({e}, 0)), 1))"
        for c, e in num_axes)
    (n_recalc, n_unadj_bad, n_avail_recalc, n_cross_span, n_no_span, n_span_free_diff,
     n_rows_unadj, n_tickers_unadj, n_rows_adjusted, cum_min, cum_max) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {r} WHERE {axis_conds}),
          (SELECT count(*) FROM {r} WHERE n_unadjusted_events IS DISTINCT FROM
                                          r_n_unadjusted_events),
          (SELECT count(*) FROM {r} WHERE available_date IS DISTINCT FROM r_available_date),
          (SELECT count(*) FROM {r} WHERE n_factors_applied > r_n_factors_applied),
          (SELECT count(*) FROM {r} WHERE span_first IS NULL),
          (SELECT count(*) FROM {r}
             WHERE abs(cum_share_factor - cum_share_factor_span_free)
                   > {FACTOR_PRODUCT_TOL!r} * greatest(abs(cum_share_factor), 1)),
          (SELECT count(*) FROM {v} WHERE n_unadjusted_events > 0),
          (SELECT count(DISTINCT ticker) FROM {v} WHERE n_unadjusted_events > 0),
          (SELECT count(*) FROM {v} WHERE n_factors_applied > 0),
          (SELECT coalesce(min(cum_share_factor), 1) FROM {v}),
          (SELECT coalesce(max(cum_share_factor), 1) FROM {v})""")
    # 구간 첫 행(= 각 (ticker, span_seq) 의 첫 가격 행)은 앵커라 누적이 1 이어야 한다.
    n_first_bad = _n(ctx, f"""
        WITH first_row AS (
            SELECT o.ticker, min(o.date) AS date
            FROM {r} o WHERE o.span_first IS NOT NULL
            GROUP BY o.ticker, o.span_first)
        SELECT count(*) FROM first_row f JOIN {v} o USING (ticker, date)
        WHERE o.cum_price_factor <> 1 OR o.cum_share_factor <> 1 OR o.n_factors_applied <> 0""")
    # 기록형 — 누적계수 분포(조정이 걸린 행만) · 미조정 사건의 사유별 내역.
    # 사유(`adj_factor.factor_source`)를 여기서 세는 이유는 소비자가 "조정이 틀렸다" 와 "MVP 가
    # 안 덮는 축이다" 를 구별해야 하기 때문이다 — 대부분은 `unknown_price_only`(유상증자
    # 권리락·주식배당락 등)라 계수를 못 낸 것이 아니라 MVP 범위 밖이라는 뜻이다.
    q10, q50, q90 = _row(ctx, f"""
        SELECT quantile_cont(cum_share_factor, 0.1), median(cum_share_factor),
               quantile_cont(cum_share_factor, 0.9)
        FROM {v} WHERE n_factors_applied > 0""")
    by_source = [dict(zip(("factor_source", "n_events", "n_events_counted", "n_tickers",
                           "n_row_hits"), r, strict=True))
                 for r in ctx.con.execute(f"""
        WITH ev AS (SELECT event_id, ticker, apply_date, factor_source
                    FROM adj_factor WHERE NOT factor_ok),
             hit AS (
                 -- 사건 축은 `event_id` 다. (ticker, apply_date) 로 묶으면 같은 날 두 사건이
                 -- 하나로 접혀 `n_unadjusted_events` 의 세는 축과 갈린다(서버 44건).
                 SELECT e.event_id, e.factor_source, e.ticker, count(a.date) AS n_rows
                 FROM ev e LEFT JOIN {r} a
                   ON a.ticker = e.ticker
                   AND e.apply_date >= a.span_first AND e.apply_date <= a.date
                 GROUP BY e.event_id, e.factor_source, e.ticker)
        SELECT factor_source, count(*), count(*) FILTER (WHERE n_rows > 0),
               count(DISTINCT ticker) FILTER (WHERE n_rows > 0),
               coalesce(sum(n_rows), 0)
        FROM hit GROUP BY factor_source ORDER BY factor_source""").fetchall()]
    n_macro_bad, macro_metrics = _macro_mismatch(ctx)
    n_out = ctx.n_out
    checks = {
        "n_cum_nonpositive": int(str(n_cum_bad)),
        "n_cum_product_off": int(str(n_product_off)),
        "n_cum_product_recalc_off": int(str(n_product_recalc_off)),
        "n_span_first_not_unit": n_first_bad,
        "n_recompute_mismatch": int(str(n_recalc)),
        "n_unadjusted_mismatch": int(str(n_unadj_bad)),
        "n_available_recompute_mismatch": int(str(n_avail_recalc)),
        "n_cross_span_factor": int(str(n_cross_span)),
        "n_available_ne_date": int(str(n_avail_bad)),
        "n_available_basis_not_derived": int(str(n_basis_bad)),
        "n_ticker_malformed": int(str(n_ticker_bad)),
        "n_off_calendar": int(str(n_off_cal)),
        "n_macro_mismatch": n_macro_bad,
    }
    metrics: dict[str, object] = {
        "n_rows": n_out,
        "n_rows_adjusted": int(str(n_rows_adjusted)),
        "n_rows_without_span": int(str(n_no_span)),
        "n_rows_span_free_diff": int(str(n_span_free_diff)),
        "n_rows_with_unadjusted_events": int(str(n_rows_unadj)),
        "n_tickers_with_unadjusted_events": int(str(n_tickers_unadj)),
        "unadjusted_row_ratio": (int(str(n_rows_unadj)) / n_out) if n_out else 0.0,
        "cum_share_factor_min": float(str(cum_min)),
        "cum_share_factor_max": float(str(cum_max)),
        "cum_share_factor_quantiles_adjusted_rows": {
            "p10": None if q10 is None else float(str(q10)),
            "p50": None if q50 is None else float(str(q50)),
            "p90": None if q90 is None else float(str(q90))},
        "unadjusted_events_by_factor_source": by_source,
        "max_cum_product_dev": float(str(max_dev)),
        "product_tol_basis": tol_basis,
        "factor_product_tol_base": tol,
        **macro_metrics,
    }
    return _result("EG3_price_adj_daily", checks, metrics,
                   "전방 조정 불변식·독립 재계산·매크로 정합 성립")


eg3_price_adj_daily.gate_name = "EG3_price_adj_daily"       # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

# S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `price.adj_close`).
# **S06 에서 옮겨 왔다** — 조정가는 이제 뷰가 아니라 이 표의 컬럼이라 `view_name` 이 없고
# 커버율도 자기 컬럼(`adj_close`)에서 직접 잰다(옛 선언은 매크로가 빌드 세션에 없어서
# `price_daily.close` 를 대리 분모로 썼다). 랙 0 세션: 값이 (ticker, date) 의 순수 함수이고
# 행의 `available_date` 가 항상 date 라 세션 랙을 더할 근거가 없다(DESIGN §5·§11 ①).
# `adj_open`·`adj_high`·`adj_low`·`adj_volume_shr` 는 **선언하지 않는다** — FIELD_MAP §2 어휘에도
# FACTORS 정본 54 의 재료에도 없다(M02 는 원주가 `price.high` 를 쓴다). 선언하면 소비도 없는
# 행이 `dataset_profile` 에 늘고 §3 집계를 다시 세야 한다.
FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="price.adj_close", columns=("adj_close",), label="조정 종가(전방 조정)",
        unit="KRW", value_type="price", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="원주가 세션 확정 + 계수 available_date(min(공시 접수일, apply_date "
                         "다음 세션)) 중 나중 — 산출 available_date 는 항상 date 다",
        evidence="price_adj_daily.adj_close = close × Π(share_factor : factor_ok ∧ 같은 "
                 "security_span 구간 ∧ greatest(apply_date, available_date) ≤ date). 첫 관측 "
                 "수준 고정이라 창·as_of 에 무관하다(결정 6, 09-05). 카탈로그 매크로 "
                 "v_adj_price_fwd 는 같은 값을 내는 읽기 경로이고 매 빌드 EG3_price_adj_daily 가 "
                 "동일성을 증명한다. **FIELD_MAP §2 의 42 어휘 밖**(equity 내부 스코프, §3) 이라 "
                 "field_scope='internal' 이다. n_unadjusted_events > 0 인 구간은 조정이 "
                 "불완전하다 — 소비자가 거를 축이다.",
        coverage_axis="grid_session", scope="internal", axis_columns=("ticker", "date")),
)


PRICE_ADJ_DAILY = register(EquityTable(
    name=TABLE_NAME,
    grain=("ticker", "date"),
    # 순서 = DESIGN §4-2 컬럼 순서. 원주가는 싣지 않는다(정본 price_daily).
    columns={"ticker": "VARCHAR", "date": "DATE",
             "adj_open": "DOUBLE", "adj_high": "DOUBLE", "adj_low": "DOUBLE",
             "adj_close": "DOUBLE", "adj_volume_shr": "DOUBLE",
             "cum_price_factor": "DOUBLE", "cum_share_factor": "DOUBLE",
             "n_factors_applied": "BIGINT", "n_unadjusted_events": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("price_daily", "adj_factor", "security_span", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule=("derived: greatest(date, 접힌 계수의 available_date 최댓값) — fold_date = "
                    "greatest(apply_date, available_date) 규약상 항상 date 로 떨어지고, 그 "
                    "항등은 EG3_price_adj_daily 가 폐기형으로 다시 확인한다"),
    # GATES §3-㉓: 조정은 가격 행 하나하나의 순수 함수라 행이 늘거나 줄지 않는다 — 항등식이다.
    # 격리가 없으므로 우변에서 뺄 것도 없다.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM price_daily",
    sql_path=SQL_DIR / f"{TABLE_NAME}.sql",
    input_columns={
        # `price_kind` 는 산출식이 아니라 EG3 의 매크로 정합 대조가 읽는다 — 매크로
        # `v_adj_price_fwd` 가 원주가 행 축을 그대로 싣기 때문이다(표는 안 싣는다).
        "price_daily": ("ticker", "date", "open", "high", "low", "close", "volume_shr",
                        "price_kind"),
        # `factor_source`·`event_id` 는 산출식이 아니라 EG3 기록형(미조정 사건의 사유별 내역)이
        # 읽는다 — 사건 축은 event_id 다(같은 날 두 사건을 접으면 세는 축이 갈린다).
        "adj_factor": ("ticker", "apply_date", "available_date", "price_factor", "share_factor",
                       "factor_ok", "factor_source", "event_id"),
        "security_span": ("ticker", "span_seq", "first_date"),
        "trading_calendar": ("date",)},
    available_basis=("derived",),
    content_date_column="date",
    reject_reasons=(),
    extra_gates=(eg3_price_adj_daily,),
    field_profiles=FIELDS,
))

TABLES: tuple[EquityTable, ...] = (PRICE_ADJ_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s23.json"
"""이 슬라이스가 요구하는 baseline 상수 — 없다. 파일이 그 사실과 이유를 기록한다."""

__all__ = ["BASELINE_SEED", "FIELDS", "MACRO_MATCH_TOL", "MACRO_VIEWS", "PRICE_ADJ_DAILY",
           "TABLES", "eg3_price_adj_daily", "install_recalc"]
