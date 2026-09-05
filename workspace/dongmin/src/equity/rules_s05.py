"""S05 기업행위 슬라이스 — `corp_event` MVP-B (DESIGN v1.2 §4-2 · GATES v1.0 §3-⑨ · WORKFLOW §3-5).

MVP 범위는 `split`·`reverse_split`·`bonus`·`capred` 4종이다. `event_type` 어휘 13종은 DESIGN
대로 선언하되(EVENT_TYPE_VOCAB) 나머지 9종의 행은 만들지 않는다 — 배당·락일·유상증자·CB·
자사주는 S16·후속 슬라이스. 산출식은 `sql/corp_event.sql` 하나, 상수는 `baseline_seed_s05.json`.

**모집단(pool) 과 범위(scope)**: 원천 행을 법인 → 티커로 전개한 leg 행 전부가 pool 이고,
조정할 가격이 없는 사건은 **범위 밖**(scope_out) 이라 격리도 산출도 아니다 — 서버 1차 빌드
(09-05)에서 사업보고서 증자(감자)현황이 창립 이래(1963~) 이력을 회고 기재해 격리 8,834 / 산출
3,865 로 EG7 이 깨진 것이 근거. 범위 밖 4종(`SCOPE_OUT_VOCAB`)은 EG3_corp_event 기록형 metric.
`stg_capital.isu_dcrs_stock_knd` 종류 어휘는 아래 튜플이 정본이고 `.sql` 의 IN 리터럴이 그것을
그대로 옮긴다(tests 가 대조). 보통주 계열인데 상장 보통주가 없을 때만 `ticker_unresolved` 격리.

EG1 우변은 GATES §3-⑨ 의 `_reg_corp_event_source` 역할을 하는 `SOURCES` 등록표 — **`.sql` 의
pool CTE(마커 앞부분)를 그대로 재사용**해 `scope_out IS NULL` 인 후보 행수를 원천별로 센다.
좌변은 `count(out) + Σ(n_src_rows − 1)`(접힌 행을 되돌린 수) 이므로 `Σ후보 − n_reject` 와 같아야
한다. 모집단 정의를 두 벌 손으로 베끼지 않는 대신, 전개 전 원천 행수(`RAW_SOURCE_ROWS`)는 stage
뷰만으로 독립 산출해 metric 으로 남긴다. 프레임 `_meta.n_dedup` 은 0 으로 고정돼 있어 dedup
건수는 EG3_corp_event 의 metric `n_dedup` 이 낸다.

테이블 특화 술어 EG3_corp_event: 어휘 폐쇄(event_type·effective_basis·source) · 티커 폭 ·
dedup 축 재계산(n_dup = 0) · ratio 양수 · **방향 불변식**(split·bonus → ratio > 1,
reverse_split·capred → ratio < 1 — 서버 실측 007195 2013-05-24 액면 5,000→1,000 인데
주식수 ×0.833 이 'split' 로 나가 S07 EGC-04 에서 거절된 사고의 회귀 게이트; KRX 유형은 주식수
비 방향으로 매기고 액면가 변화는 트리거일 뿐, 주식수 불변 액면 변경은 `krx_par_only` 로 범위 밖)
· KRX 행 announce = effective · event_id 결정성 · **산출 행이 캘린더 밖·상장 전이 아님(독립
재검사)**. 근접 중복(같은 티커·유형이
`near_dup_window_days` 안에 다른 원천으로 2건)은 기록형 — 절단본 실측: 우양에이치씨 2018 감자가
결정공시 cr_std 2018-10-12 · 자본변동 isu_dcrs_de 2018-10-13 으로 하루 어긋나 2행이 된다.
EG8-P04(기준가≠전일종가 recall)는 `price_daily` 가 필요하므로 S06 이후에 붙는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "corp_event.sql"

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
# 범위 밖 사유(격리 아님, 기록형) — sql/corp_event.sql pool.scope_out
SCOPE_OUT_VOCAB: tuple[str, ...] = ("out_of_calendar", "unlisted_class", "class_unknown",
                                    "pre_listing", "krx_par_only", "share_unchanged")
# 방향 불변식(EG3_corp_event 폐기형, 원천 무관): ratio 는 주식수 배수이므로 유형이 방향을 못 박는다.
RATIO_ABOVE_ONE: tuple[str, ...] = ("split", "bonus")
RATIO_BELOW_ONE: tuple[str, ...] = ("reverse_split", "capred")
TICKER_LEN = 6

# `stg_capital.isu_dcrs_stock_knd` 종류 어휘 — 서버 실측(09-05, MVP 유형 행): 보통주 20,820 ·
# 우선주 2,252 · 보통주식 336 · 상환전환우선주 213 · 전환상환우선주 110 · 전환우선주 100 · '-' 63 ·
# 기명식보통주 56 · 우선주식 56 · RCPS 43. 보통주 계열 → 상장 보통주 leg, 우선주 계열 → 상장 우선주
# leg(없으면 unlisted_class), 비상장 종류 → unlisted_class, 그 외('-' 포함) → class_unknown.
COMMON_KINDS: tuple[str, ...] = ("보통주", "보통주식", "기명식보통주")
PREFERRED_KINDS: tuple[str, ...] = ("우선주", "우선주식")
UNLISTED_KINDS: tuple[str, ...] = ("상환전환우선주", "전환상환우선주", "전환우선주", "RCPS")

# ── 모집단 SQL 재사용 (sql/corp_event.sql 의 pool CTE 까지) ───────────────────
_POOL_MARKER = "-- ==== eg1:"


def _pool_prefix() -> str:
    """`.sql` 의 첫 줄부터 pool CTE 닫는 괄호까지 — 뒤에 `SELECT … FROM pool` 을 붙여 쓴다."""
    text = SQL_PATH.read_text(encoding="utf-8")
    head, sep, _ = text.partition(_POOL_MARKER)
    if not sep:
        raise ValueError(f"pool marker not found in {SQL_PATH}: expected a line starting with "
                         f"{_POOL_MARKER!r} right after the pool CTE")
    return head


def pool_sql(select: str) -> str:
    """pool 위의 단일 SELECT. 예: pool_sql(\"count(*) … WHERE scope_out IS NULL\")."""
    return f"{_pool_prefix()}\nSELECT {select}"


_CAPITAL_MVP_PREDICATE = ("isu_dcrs_stle LIKE '%감자%' OR (isu_dcrs_stle LIKE '%무상증자%' "
                          "AND isu_dcrs_stle NOT LIKE '%유상%')")

# 원천 등록표 (GATES §3-⑨ `_reg_corp_event_source`) — 원천별 **범위 안 후보** 행수.
SOURCES: tuple[tuple[str, str], ...] = tuple(
    (s, pool_sql(f"count(*) FROM pool WHERE scope_out IS NULL AND source = '{s}'"))
    for s in SOURCE_VOCAB)

# 전개 전 원천 행수 — stage 뷰만 읽는 독립 산출(기록형). krx 는 액면가 변경일 수.
RAW_SOURCE_ROWS: tuple[tuple[str, str], ...] = (
    ("event_fric", "SELECT count(*) FROM stg_event_fric"),
    ("event_pifric", "SELECT count(*) FROM stg_event_pifric"),
    ("event_cr", "SELECT count(*) FROM stg_event_cr"),
    ("capital", f"SELECT count(*) FROM stg_capital WHERE {_CAPITAL_MVP_PREDICATE}"),
    ("krx_listing", (
        "SELECT count(*) FROM ("
        "SELECT ticker, date, par_value_krw, lag(par_value_krw) OVER w AS prev_par, "
        "lag(date) OVER w AS prev_date FROM stg_listing_daily "
        "WINDOW w AS (PARTITION BY ticker ORDER BY date)) l "
        "JOIN trading_calendar c ON c.date = l.date "
        "WHERE l.prev_par IS NOT NULL AND l.par_value_krw IS NOT NULL "
        "AND l.par_value_krw <> l.prev_par AND l.prev_date = c.prev_td")),
)

EG1_RHS_SQL = pool_sql("count(*) FROM pool WHERE scope_out IS NULL")
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


def _scope_counts(ctx: EquityGateContext) -> dict[str, dict[str, int]]:
    """pool 을 (scope, source) 로 센다. 범위 안은 'in_scope'."""
    out: dict[str, dict[str, int]] = {}
    for scope, source, n in ctx.con.execute(pool_sql(
            "coalesce(scope_out, 'in_scope'), source, count(*) FROM pool "
            "GROUP BY 1, 2 ORDER BY 1, 2")).fetchall():
        out.setdefault(str(scope), {})[str(source)] = int(str(n))
    return out


def eg3_corp_event(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P13 + GATES §3-⑨ dedup 재계산 + 범위 밖·원천별 행수·n_dedup 기록."""
    v = ctx.out_view
    known_kinds = _vocab_sql(COMMON_KINDS + PREFERRED_KINDS + UNLISTED_KINDS)
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
        # 방향 불변식 — 서버 실측 007195:split:2013-05-24 share_factor 0.833(액면 5,000→1,000 인데
        # 주식수 27,011→22,505)이 S07 EGC-04 에서 format_error 로 거절된 사고의 회귀 게이트
        "n_direction_violation": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE ratio IS NOT NULL AND ('
                 f"(event_type IN ({_vocab_sql(RATIO_ABOVE_ONE)}) AND ratio <= 1) OR "
                 f"(event_type IN ({_vocab_sql(RATIO_BELOW_ONE)}) AND ratio >= 1))"),
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
        # 범위 규칙의 독립 재검사 — 산출 행은 캘린더 안이고 그 티커의 첫 존재일 이후여야 한다
        "n_out_off_calendar": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE effective_date < '
                 "(SELECT min(date) FROM trading_calendar) OR effective_date > "
                 "(SELECT max(date) FROM trading_calendar)"),
        "n_out_pre_listing": _n(
            ctx, f'SELECT count(*) FROM "{v}" e JOIN (SELECT ticker, min(first_date) AS fd '
                 "FROM security_span GROUP BY ticker) s USING (ticker) "
                 "WHERE e.effective_date < s.fd"),
    }
    src_counts = {name: _n(ctx, sql) for name, sql in SOURCES}
    raw_counts = {name: _n(ctx, sql) for name, sql in RAW_SOURCE_ROWS}
    scope = _scope_counts(ctx)
    window = ctx.baseline.get(ctx.rule.name, "near_dup_window_days")
    n_near_dup: int | None = None
    if window is not None:
        w = int(str(window))
        n_near_dup = _n(ctx, f'SELECT count(*) FROM "{v}" a JOIN "{v}" b '
                             "ON a.ticker = b.ticker AND a.event_type = b.event_type "
                             "AND a.source <> b.source AND a.effective_date < b.effective_date "
                             f"AND date_diff('day', a.effective_date, b.effective_date) <= {w}")
    unknown_kinds = [str(r[0]) for r in ctx.con.execute(
        "SELECT coalesce(isu_dcrs_stock_knd, '<NULL>') AS k, count(*) AS c FROM stg_capital "
        f"WHERE ({_CAPITAL_MVP_PREDICATE}) AND coalesce(isu_dcrs_stock_knd, '<NULL>') "
        f"NOT IN ({known_kinds}) GROUP BY 1 ORDER BY c DESC, k").fetchall()]
    metrics: dict[str, object] = {
        "n_src_by_source": src_counts, "n_src_total": sum(src_counts.values()),
        "n_raw_rows_by_source": raw_counts,
        "n_pool_by_scope": {k: sum(d.values()) for k, d in scope.items()},
        "n_pool_by_scope_source": scope,
        "n_krx_par_only": sum(scope.get("krx_par_only", {}).values()),
        "n_share_unchanged": sum(scope.get("share_unchanged", {}).values()),
        "krx_share_change_tol": ctx.baseline.get(ctx.rule.name, "krx_share_change_tol"),
        "n_dedup": _n(ctx, f'SELECT coalesce(sum(n_src_rows - 1), 0) FROM "{v}"'),
        "n_out_by_source": _counts(ctx, "source"),
        "n_by_event_type": _counts(ctx, "event_type"),
        "n_by_effective_basis": _counts(ctx, "effective_basis"),
        "n_ratio_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE ratio IS NULL'),
        "max_announce_minus_effective_days": _n(
            ctx, f"SELECT coalesce(max(date_diff('day', effective_date, announce_date)), 0) "
                 f'FROM "{v}"'),
        "n_near_dup_cross_source": n_near_dup, "near_dup_window_days": window,
        "class_unknown_kinds": unknown_kinds,
        "reject_by_reason": dict(ctx.reject_by_reason),
        "event_type_vocab": list(EVENT_TYPE_VOCAB), "mvp_event_types": list(MVP_EVENT_TYPES),
        "scope_out_vocab": list(SCOPE_OUT_VOCAB),
    }
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG3_corp_event", GateStatus.PASS,
                          "기업행위 어휘·dedup 축·범위 불변식 성립", merged)
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
            "stg_listing_daily", "corp_ticker", "trading_calendar", "security_span"),
    partition_class="receipt_axis",
    # receipt 축이지만 키 식은 year(announce_date) — KRX 파생행은 rcept_no 가 없고, DART 행은
    # rcept_no 앞 4자리 = 접수연도 = year(rcept_dt) 라 같은 값이다 (DESIGN §4-2).
    partition_key_expr="year(announce_date)",
    available_rule="column:announce_date — DART rcept_dt(derived) / KRX 관측일(default)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=EG1_RHS_SQL,
    sql_path=SQL_PATH,
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
        "trading_calendar": ("date", "prev_td"),
        "security_span": ("ticker", "first_date")},
    available_basis=("derived", "default"),
    content_date_column="announce_date",
    reject_reasons=REJECT_REASONS,
    consts=("effective_before_announce_max_days", "krx_share_change_tol"),
    extra_gates=(eg3_corp_event,),
))

TABLES: tuple[EquityTable, ...] = (CORP_EVENT,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s05.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "COMMON_KINDS", "CORP_EVENT", "EVENT_TYPE_VOCAB", "MVP_EVENT_TYPES",
           "PREFERRED_KINDS", "RATIO_ABOVE_ONE", "RATIO_BELOW_ONE", "RAW_SOURCE_ROWS",
           "SCOPE_OUT_VOCAB", "SOURCES", "TABLES", "UNLISTED_KINDS", "pool_sql"]
