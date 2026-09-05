"""S04 슬라이스 선언 — `price_daily` 가격 정본 (DESIGN v1.2 §4-2, GATES v1.0 §3 ⑧ · EG7-P01 · EG20).

원칙 ②("원주가 불변 + 계수 분리")를 코드로 고정하는 슬라이스다. OHLC·거래량·거래대금은 stage 값을
그대로 나르고(수정 없음), 조정은 S06 `adj_factor` 와 뷰가 한다. 파생은 셋뿐이다 —
`mktcap_krw = close × shares_out` · `price_kind`(trade/reference) · `available_date = date`.

입력 — stage 3(`stg_price_daily`·`stg_etf_price_daily`·`stg_listing_daily`) + equity
`trading_calendar`(캘린더 밖 날짜 격리 축). 숫자 상수는 없다(`baseline_seed_s04.json` 참조).

테이블 특화 술어(`extra_gates`):
  EG3_price_daily — 두 원천 (ticker,date) 교집합 0(GATES §3 ⑧ 두 번째 식) · `price_kind` 어휘
                    폐쇄 · ticker 폭. 나머지는 **기록형 metric**(GATES §0-1): GAP-14
                    `open IS NULL ∧ volume>0` · 격리 사유별 건수 · `shares_out`/`par_value_krw`
                    NULL · stage MKTCAP 과의 차이 · `price_kind` NULL
  EG20            — 원주가 불변: 산출 OHLC·`volume_shr`·`value_krw` 를 stage 와 독립 재조인해
                    다른 행 0
  EG8-P01(KIS 수정종가 대조)은 독립 KIS 가격 stage 테이블이 없고 계수(S06)가 있어야 대조가 되므로
  S06 이후로 미룬다 — 여기서는 붙이지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-2 — `price_kind` 폐쇄 어휘. 거래 있는 날 trade, `volume_shr = 0` 인 날 reference(기준가·
# 정지일, 종가 보존). `volume_shr` NULL 이면 price_kind 도 NULL — 0 으로 읽어 'reference' 로 굳히지
# 않는다.
PRICE_KINDS: tuple[str, ...] = ("trade", "reference")
# EG7-P01 격리 사유 + 캘린더 밖 날짜. `_reject/<reason>/` 디렉토리 이름이자 EG3 어휘 폐쇄 대상.
REJECT_REASONS: tuple[str, ...] = ("nonpositive_price", "off_calendar")

# EG20 이 대조하는 (산출 컬럼, stage 컬럼) 쌍 — 원칙 ② 가 지키는 원값 축 전부.
_RAW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("close", "close_krw"), ("volume_shr", "volume_shr"), ("open", "open_krw"),
    ("high", "high_krw"), ("low", "low_krw"), ("value_krw", "value_krw"))


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s01 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def eg3_price_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 원천 교집합 0 · `price_kind` 어휘 폐쇄 · ticker 폭 + 기록형 metric.

    폐기 조건은 산출식을 다시 계산하지 않는 축만 건다(§1 "게이트 술어를 산출식으로 재계산" 금지).
    `mktcap_krw` 가 stage MKTCAP 과 다른 행·listing 이 없어 `shares_out` 이 빈 주식 행은 KRX 두
    원장이 어긋난 사실이지 이 테이블의 결함이 아니므로 **기록**한다 — 서버 실측 뒤 baseline 승격.
    """
    v = _q(ctx.out_view)
    vocab = ", ".join(f"'{k}'" for k in PRICE_KINDS)
    n_overlap, n_kind_vocab, n_ticker_bad = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM (SELECT ticker, date FROM stg_price_daily
                                 INTERSECT SELECT ticker, date FROM stg_etf_price_daily)),
          (SELECT count(*) FROM {v}
            WHERE price_kind IS NOT NULL AND price_kind NOT IN ({vocab})),
          (SELECT count(*) FROM {v}
            WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
               OR length(ticker) <> {TICKER_LEN})""")
    (n_open_null_vol, n_kind_null, n_close_null, n_ref_with_value,
     n_shares_null_stock, n_shares_null_etf, n_par_null_stock,
     n_mktcap_mismatch, n_shares_mismatch) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE "open" IS NULL AND volume_shr > 0),
          (SELECT count(*) FROM {v} WHERE price_kind IS NULL),
          (SELECT count(*) FROM {v} WHERE "close" IS NULL),
          (SELECT count(*) FROM {v} WHERE price_kind = 'reference' AND value_krw > 0),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.shares_out IS NULL),
          (SELECT count(*) FROM {v} p JOIN stg_etf_price_daily e USING (ticker, date)
            WHERE p.shares_out IS NULL),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.par_value_krw IS NULL),
          (SELECT count(*) FROM {v} p
             LEFT JOIN stg_price_daily s ON s.ticker = p.ticker AND s.date = p.date
             LEFT JOIN stg_etf_price_daily e ON e.ticker = p.ticker AND e.date = p.date
            WHERE p.mktcap_krw IS DISTINCT FROM coalesce(s.mktcap_krw, e.mktcap_krw)),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.shares_out IS DISTINCT FROM s.list_shrs)""")
    kinds = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT price_kind, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()}
    checks = {
        "n_src_overlap": int(str(n_overlap)),
        "n_price_kind_outside_vocab": int(str(n_kind_vocab)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
    }
    metrics: dict[str, object] = {
        "n_open_null_volume_pos": int(str(n_open_null_vol)),        # GAP-14 (DESIGN §4-2)
        "n_nonpositive_price": int(ctx.reject_by_reason.get("nonpositive_price", 0)),
        "n_off_calendar": int(ctx.reject_by_reason.get("off_calendar", 0)),
        "n_price_kind_null": int(str(n_kind_null)),
        "n_close_null": int(str(n_close_null)),
        "n_reference_with_value": int(str(n_ref_with_value)),
        "n_shares_out_null_stock": int(str(n_shares_null_stock)),
        "n_shares_out_null_etf": int(str(n_shares_null_etf)),
        "n_par_value_null_stock": int(str(n_par_null_stock)),
        "n_mktcap_stage_mismatch": int(str(n_mktcap_mismatch)),
        "n_shares_out_stage_mismatch": int(str(n_shares_mismatch)),
        "price_kind_counts": kinds,
        "price_kind_vocab": list(PRICE_KINDS),
    }
    return _result("EG3_price_daily", checks, metrics, "원천 교집합 0 · price_kind 어휘 폐쇄")


eg3_price_daily.gate_name = "EG3_price_daily"   # type: ignore[attr-defined]


def eg20_raw_price(ctx: EquityGateContext) -> GateResult:
    """EG20 (GATES §6) — 원주가 불변. 산출 행을 두 stage 원천에 다시 조인해 원값 축이 다른 행 0.

    GATES 의 식은 close·volume_shr 두 축인데 원칙 ② 는 OHLC 전부이므로 open·high·low·value_krw 까지
    같은 규약으로 본다(컬럼별 건수를 metrics 에). 어느 원천에도 없는 행(산출이 만들어 낸 행)은
    coalesce 가 NULL 이라 `IS DISTINCT FROM` 에 걸려 같이 잡힌다.
    """
    v = _q(ctx.out_view)
    sel = ", ".join(f"count(*) FILTER (WHERE p.{_q(o)} IS DISTINCT FROM coalesce(s.{c}, e.{c}))"
                    for o, c in _RAW_COLUMNS)
    row = _row(ctx, f"""
        SELECT count(*), {sel}
        FROM {v} p
        LEFT JOIN stg_price_daily s ON s.ticker = p.ticker AND s.date = p.date
        LEFT JOIN stg_etf_price_daily e ON e.ticker = p.ticker AND e.date = p.date""")
    n_rows, *changed = (int(str(x)) for x in row)
    checks = {f"n_{o}_changed": n for (o, _), n in zip(_RAW_COLUMNS, changed, strict=True)}
    return _result("EG20", checks, {"n_rows_checked": n_rows,
                                    "raw_columns": [o for o, _ in _RAW_COLUMNS]},
                   "원주가·원거래량 불변")


eg20_raw_price.gate_name = "EG20"               # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

PRICE_DAILY = register(EquityTable(
    name="price_daily",
    grain=("ticker", "date"),
    # 순서 = DESIGN §4-2 컬럼 순서. OHLC 는 FIELD_MAP `price.close`·`price.open` 대응이라 접미사
    # 없음(원주가 KRW 는 자명), 나머지 수량·금액 컬럼은 stage 단위 접미사 규약(_shr·_krw) 유지.
    columns={"ticker": "VARCHAR", "date": "DATE",
             "open": "DECIMAL(9,0)", "high": "DECIMAL(9,0)", "low": "DECIMAL(9,0)",
             "close": "DECIMAL(9,0)", "volume_shr": "DECIMAL(13,0)",
             "value_krw": "DECIMAL(16,0)", "mktcap_krw": "DECIMAL(18,0)",
             "shares_out": "DECIMAL(13,0)", "par_value_krw": "DECIMAL(9,2)",
             "price_kind": "VARCHAR", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_price_daily", "stg_etf_price_daily", "stg_listing_daily", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule="column:date — 가격류(stage lag_known=true), 공표 시각 미제공 → basis default",
    # GATES §3 ⑧: 좌변 행수 = 두 원천 행수 합 − reject. 교집합 0 은 EG3-P01·EG3_price_daily 가 본다.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=("SELECT (SELECT count(*) FROM stg_price_daily) "
                 "+ (SELECT count(*) FROM stg_etf_price_daily)"),
    sql_path=SQL_DIR / "price_daily.sql",
    input_columns={
        "stg_price_daily": ("ticker", "date", "open_krw", "high_krw", "low_krw", "close_krw",
                            "volume_shr", "value_krw", "mktcap_krw", "list_shrs"),
        "stg_etf_price_daily": ("ticker", "date", "open_krw", "high_krw", "low_krw", "close_krw",
                                "volume_shr", "value_krw", "mktcap_krw", "list_shrs"),
        "stg_listing_daily": ("ticker", "date", "par_value_krw", "list_shrs"),
        "trading_calendar": ("date",)},
    available_basis=("default",),
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_price_daily, eg20_raw_price),
))

TABLES: tuple[EquityTable, ...] = (PRICE_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s04.json"
"""이 슬라이스가 요구하는 baseline 상수 — 없다. 파일은 그 사실과 이유를 기록한다."""

__all__ = ["PRICE_DAILY", "PRICE_KINDS", "REJECT_REASONS", "TABLES"]
