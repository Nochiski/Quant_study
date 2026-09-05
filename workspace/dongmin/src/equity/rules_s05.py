"""S05 기업행위 슬라이스 — `corp_event` MVP-B (DESIGN v1.2 §4-2 · GATES v1.0 §3-⑨ · WORKFLOW §3-5).

MVP 범위는 `split`·`reverse_split`·`bonus`·`capred` 4종이다. `event_type` 어휘 13종은 DESIGN
대로 선언하되(EVENT_TYPE_VOCAB) 나머지 9종의 행은 만들지 않는다 — 배당·락일·유상증자·CB·
자사주는 S16·후속 슬라이스. 산출식은 `sql/corp_event.sql` 하나, 상수는 `baseline_seed_s05.json`.

EG1 우변은 GATES §3-⑨ 의 `_reg_corp_event_source` 역할을 하는 `SOURCES` 등록표 — 원천별
후보 행수(법인 → 티커 전개 뒤, reject·dedup 전)의 합이다. 좌변은 `count(out) + Σ(n_src_rows − 1)`
(접힌 행을 되돌린 수) 이므로 `Σ원천 − n_reject` 와 같아야 한다. 프레임 `_meta.n_dedup` 은 0 으로
고정돼 있어 dedup 건수는 EG3_corp_event 의 metric `n_dedup` 이 낸다.

테이블 특화 술어 EG3_corp_event: 어휘 폐쇄(event_type·effective_basis·source) · 티커 폭 ·
dedup 축 재계산(n_dup = 0) · ratio 양수 · KRX 행 announce = effective · event_id 결정성.
근접 중복(같은 티커·유형이 `near_dup_window_days` 안에 다른 원천으로 2건)은 **기록형** metric —
절단본 실측: 우양에이치씨 2018 감자가 결정공시 cr_std 2018-10-12 · 자본변동 isu_dcrs_de
2018-10-13 으로 하루 어긋나 2행이 된다(정확 축 dedup 의 한계, DESIGN §4-2 에 기록).
EG8-P04(기준가≠전일종가 recall)는 `price_daily` 가 필요하므로 S06 이후에 붙는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-2 — event_type 어휘 13종. MVP 는 앞 4종만 산출한다.
EVENT_TYPE_VOCAB: tuple[str, ...] = (
    "split", "reverse_split", "bonus", "capred", "rights", "spinoff", "merger",
    "stock_dividend", "cash_dividend", "cb_issue", "treasury_buy", "treasury_sell", "other")
MVP_EVENT_TYPES: tuple[str, ...] = ("split", "reverse_split", "bonus", "capred")
EFFECTIVE_BASIS_VOCAB: tuple[str, ...] = (
    "disclosure_body", "krx_shares_change", "krx_notice", "unconfirmed")
SOURCE_VOCAB: tuple[str, ...] = ("event_fric", "event_pifric", "event_cr", "capital",
                                 "krx_listing")
REJECT_REASONS: tuple[str, ...] = ("ticker_unresolved", "effective_unresolved", "ratio_unparsed",
                                   "effective_before_announce")
TICKER_LEN = 6

# ── 원천 등록표 (GATES §3-⑨ `_reg_corp_event_source`) ─────────────────────────
# 각 항목은 (source, 후보 행수 SQL). SQL 은 stage 뷰·앞선 equity 뷰만 읽고 `sql/corp_event.sql`
# 의 class-row → leg 전개를 독립 재계산한다. leg 가 없는 class-row 도 1행(ticker_unresolved 로
# 격리될 후보)으로 센다.
_LEGS_SQL = (
    "SELECT corp_code, "
    "CASE WHEN is_common OR isin8 NOT LIKE 'KR7%' THEN 'common' ELSE 'preferred' END AS cls "
    "FROM corp_ticker WHERE corp_code IS NOT NULL")


def _leg_count_sql(class_rows_sql: str) -> str:
    """class-row 마다 leg 수(최소 1)를 더한다 — `.sql` 의 LEFT JOIN legs 와 같은 값."""
    return (f"WITH legs AS ({_LEGS_SQL}) "
            "SELECT coalesce(sum(greatest(1, (SELECT count(*) FROM legs l "
            "WHERE l.corp_code = c.corp_code AND l.cls = c.cls))), 0) "
            f"FROM ({class_rows_sql}) c")


_CAPITAL_MVP_PREDICATE = ("isu_dcrs_stle LIKE '%감자%' OR (isu_dcrs_stle LIKE '%무상증자%' "
                          "AND isu_dcrs_stle NOT LIKE '%유상%')")

SOURCES: tuple[tuple[str, str], ...] = (
    ("event_fric", _leg_count_sql(
        "SELECT corp_code, 'common' AS cls FROM stg_event_fric "
        "UNION ALL SELECT corp_code, 'preferred' FROM stg_event_fric "
        "WHERE coalesce(nstk_estk_cnt, 0) > 0 OR nstk_ascnt_ps_estk_ratio IS NOT NULL")),
    ("event_pifric", _leg_count_sql(
        "SELECT corp_code, 'common' AS cls FROM stg_event_pifric "
        "UNION ALL SELECT corp_code, 'preferred' FROM stg_event_pifric "
        "WHERE coalesce(fric_nstk_estk_cnt, 0) > 0 "
        "OR fric_nstk_ascnt_ps_estk_ratio IS NOT NULL")),
    ("event_cr", _leg_count_sql(
        "SELECT corp_code, 'common' AS cls FROM stg_event_cr "
        "UNION ALL SELECT corp_code, 'preferred' FROM stg_event_cr "
        "WHERE coalesce(crstk_estk_cnt, 0) > 0 OR cr_rt_estk_pct IS NOT NULL")),
    ("capital", _leg_count_sql(
        "SELECT corp_code, CASE WHEN isu_dcrs_stock_knd = '보통주' THEN 'common' "
        "WHEN isu_dcrs_stock_knd = '우선주' THEN 'preferred' ELSE 'none' END AS cls "
        f"FROM stg_capital WHERE {_CAPITAL_MVP_PREDICATE}")),
    ("krx_listing", (
        "SELECT count(*) FROM ("
        "SELECT ticker, date, par_value_krw, lag(par_value_krw) OVER w AS prev_par, "
        "lag(date) OVER w AS prev_date FROM stg_listing_daily "
        "WINDOW w AS (PARTITION BY ticker ORDER BY date)) l "
        "JOIN trading_calendar c ON c.date = l.date "
        "WHERE l.prev_par IS NOT NULL AND l.par_value_krw IS NOT NULL "
        "AND l.par_value_krw <> l.prev_par AND l.prev_date = c.prev_td")),
)

EG1_RHS_SQL = "SELECT " + " + ".join(f"({sql})" for _, sql in SOURCES)
EG1_LHS_SQL = "SELECT count(*) + coalesce(sum(n_src_rows - 1), 0) FROM out_pq"


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _outside_vocab(ctx: EquityGateContext, column: str, values: tuple[str, ...]) -> int:
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" WHERE "{column}" IS NULL '
                   f'OR "{column}" NOT IN ({_vocab_sql(values)})')


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f'SELECT "{column}", count(*) FROM "{ctx.out_view}" GROUP BY 1 ORDER BY 1').fetchall()}


def eg3_corp_event(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P13 + GATES §3-⑨ dedup 재계산 + 원천별 행수·n_dedup 기록."""
    v = ctx.out_view
    checks = {
        "n_event_type_outside_vocab": _outside_vocab(ctx, "event_type", EVENT_TYPE_VOCAB),
        "n_event_type_outside_mvp": _outside_vocab(ctx, "event_type", MVP_EVENT_TYPES),
        "n_effective_basis_outside_vocab": _outside_vocab(ctx, "effective_basis",
                                                          EFFECTIVE_BASIS_VOCAB),
        "n_source_outside_vocab": _outside_vocab(ctx, "source", SOURCE_VOCAB),
        "n_ticker_bad_width": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE ticker IS NULL '
                                      f"OR typeof(ticker) <> 'VARCHAR' OR length(ticker) <> "
                                      f"{TICKER_LEN}"),
        # GATES §3-⑨ — 선언 dedup 축 (ticker, event_type, effective_date) 재계산
        "n_dup_key": _n(ctx, "SELECT count(*) FROM (SELECT ticker, event_type, effective_date, "
                             f'count(*) AS c FROM "{v}" GROUP BY ALL HAVING c > 1)'),
        "n_ratio_nonpositive": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                       "WHERE ratio IS NOT NULL AND ratio <= 0"),
        "n_krx_announce_ne_effective": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE source = \'krx_listing\' '
                 "AND (announce_date <> effective_date "
                 "OR effective_basis <> 'krx_shares_change')"),
        "n_src_rows_lt_1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_src_rows < 1'),
        "n_event_id_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE event_id <> ticker || \':\' || event_type '
                 "|| ':' || CAST(effective_date AS VARCHAR)"),
        "n_available_ne_announce": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date <> announce_date"),
    }
    src_counts = {name: _n(ctx, sql) for name, sql in SOURCES}
    window = ctx.baseline.get(ctx.rule.name, "near_dup_window_days")
    n_near_dup: int | None = None
    if window is not None:
        w = int(str(window))
        n_near_dup = _n(ctx, f'SELECT count(*) FROM "{v}" a JOIN "{v}" b '
                             "ON a.ticker = b.ticker AND a.event_type = b.event_type "
                             "AND a.source <> b.source AND a.effective_date < b.effective_date "
                             f"AND date_diff('day', a.effective_date, b.effective_date) <= {w}")
    metrics: dict[str, object] = {
        "n_src_by_source": src_counts, "n_src_total": sum(src_counts.values()),
        "n_dedup": _n(ctx, f'SELECT coalesce(sum(n_src_rows - 1), 0) FROM "{v}"'),
        "n_out_by_source": _counts(ctx, "source"),
        "n_by_event_type": _counts(ctx, "event_type"),
        "n_by_effective_basis": _counts(ctx, "effective_basis"),
        "n_ratio_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE ratio IS NULL'),
        "max_announce_minus_effective_days": _n(
            ctx, f"SELECT coalesce(max(date_diff('day', effective_date, announce_date)), 0) "
                 f'FROM "{v}"'),
        "n_near_dup_cross_source": n_near_dup, "near_dup_window_days": window,
        "reject_by_reason": dict(ctx.reject_by_reason),
        "event_type_vocab": list(EVENT_TYPE_VOCAB), "mvp_event_types": list(MVP_EVENT_TYPES),
    }
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG3_corp_event", GateStatus.PASS, "기업행위 어휘·dedup 축 불변식 성립",
                          merged)
    return GateResult("EG3_corp_event", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


eg3_corp_event.gate_name = "EG3_corp_event"     # type: ignore[attr-defined]

CORP_EVENT = register(EquityTable(
    name="corp_event",
    grain=("event_id",),
    columns={"event_id": "VARCHAR", "ticker": "VARCHAR", "corp_code": "VARCHAR",
             "event_type": "VARCHAR", "announce_date": "DATE", "effective_date": "DATE",
             "effective_basis": "VARCHAR", "ratio": "DOUBLE", "amount_krw": "BIGINT",
             "rcept_no": "VARCHAR", "source": "VARCHAR", "n_src_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_event_fric", "stg_event_pifric", "stg_event_cr", "stg_capital",
            "stg_listing_daily", "corp_ticker", "trading_calendar"),
    partition_class="receipt_axis",
    # receipt 축이지만 키 식은 year(announce_date) — KRX 파생행은 rcept_no 가 없고, DART 행은
    # rcept_no 앞 4자리 = 접수연도 = year(rcept_dt) 라 같은 값이다 (DESIGN §4-2).
    partition_key_expr="year(announce_date)",
    available_rule="column:announce_date — DART rcept_dt(derived) / KRX 관측일(default)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=EG1_RHS_SQL,
    sql_path=SQL_DIR / "corp_event.sql",
    input_columns={
        "stg_event_fric": ("rcept_no", "corp_code", "nstk_asstd", "nstk_ascnt_ps_ostk_ratio",
                           "nstk_ascnt_ps_estk_ratio", "nstk_ostk_cnt", "nstk_estk_cnt",
                           "bfic_tisstk_ostk", "available_date", "available_basis"),
        "stg_event_pifric": ("rcept_no", "corp_code", "fric_nstk_asstd",
                             "fric_nstk_ascnt_ps_ostk_ratio", "fric_nstk_ascnt_ps_estk_ratio",
                             "fric_nstk_ostk_cnt", "fric_nstk_estk_cnt", "fric_bfic_tisstk_ostk",
                             "available_date", "available_basis"),
        "stg_event_cr": ("rcept_no", "corp_code", "cr_std", "bfcr_tisstk_ostk",
                         "atcr_tisstk_ostk", "cr_rt_ostk_pct", "bfcr_tisstk_estk",
                         "atcr_tisstk_estk", "cr_rt_estk_pct", "crstk_estk_cnt",
                         "available_date", "available_basis"),
        "stg_capital": ("rcept_no", "corp_code", "isu_dcrs_de", "isu_dcrs_stle",
                        "isu_dcrs_stock_knd", "available_date", "available_basis"),
        "stg_listing_daily": ("ticker", "date", "par_value_krw", "list_shrs",
                              "available_basis"),
        "corp_ticker": ("ticker", "isin8", "corp_code", "is_common"),
        "trading_calendar": ("date", "prev_td")},
    available_basis=("derived", "default"),
    content_date_column="announce_date",
    reject_reasons=REJECT_REASONS,
    consts=("effective_before_announce_max_days",),
    extra_gates=(eg3_corp_event,),
))

TABLES: tuple[EquityTable, ...] = (CORP_EVENT,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s05.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "CORP_EVENT", "EVENT_TYPE_VOCAB", "MVP_EVENT_TYPES", "SOURCES",
           "TABLES"]
