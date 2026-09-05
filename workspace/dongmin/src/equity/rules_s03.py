"""S03 슬라이스 선언 — 유니버스 존재·상태 `universe_daily` v1 · 정책 선언표 `universe_policy`
(DESIGN v1.2 §4-1, GATES v1.0 §3 ⑦·㉒, §1 EG3-P09, §4 FX-1-006·011·013~017).

`universe_daily` 는 처음으로 **앞서 커밋된 equity 테이블을 입력으로 읽는** 테이블이다 —
`security_span`·`trading_calendar`(S02)·`security`(S01)가 `stg_*` 와 같은 규약으로 `_pinned/` 에
고정된다(inputs.source_root). 격자 = span × 캘린더라 EG1 우변은 Σ `security_span.n_days` 이고
coverage_gap·delisted 행은 만들지 않는다(GAP-21, 캘린더 max = backfill_end).

산출 규칙 상수(게이트 임계 아님, GATES §5-B8 부류)는 `baseline_seed_s03.json` → `_const`:
  `universe_daily.admin_window_td` (KOSPI·소속부 공란 행의 관리종목 창) · `universe_policy.version`.

테이블 특화 술어(`extra_gates`):
  EG3_universe — 어휘 폐쇄(status·market·sec_type·admin_state_basis) · 불린 팩트 NULL 0 ·
                 status ⇔ halt_state(S03 규칙) · 격자 ⊆ 구간 · **EG3-P09** halt 열린 채
                 폐지·coverage_gap 아닌 사유로 끝난 구간 0. 열린 halt 의 건수는 기록형.
  EG3_policy   — 어휘 폐쇄(policy·threshold_kind·basis) · universe_id 문법 · 'all' 행 존재 ·
                 임계 종류와 값의 정합 · predicate 가 universe_daily 스키마 위에서 바인딩되는가.
`universe_policy` 는 선언표라 EG1 을 `skip(declaration_table)` 한다(`declaration_table=True`).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
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
POLICY_VOCAB: tuple[str, ...] = ("all", "investable", "liquid")
THRESHOLD_KIND_VOCAB: tuple[str, ...] = ("quantile", "absolute", "flag")
UNIVERSE_ID_PREFIX = "krx."          # FIELD_MAP §1 — universe_id = '<market>.<policy>'

_BOOL_FACTS: tuple[str, ...] = (
    "halt_state", "liquidation_window", "signal_halt", "signal_halt_release", "signal_admin",
    "signal_liquidation", "signal_delist")


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

def eg3_universe(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P09·P13 + S03 상태 규칙 정합. 상수를 안 쓰므로 baseline 유무와 무관하게 돈다.

    P09 는 GATES §1 술어 그대로다 — `end_reason ∈ {delisted, coverage_gap}` 인 구간 끝의 열린
    halt 는 정상(폐지까지 재거래 없음 930건·현재 정지 중)이라 술어 밖이고, 그 건수는 기록형으로
    남긴다. 술어 안에 남는 것은 `data_gap`(예약 어휘) 구간 끝뿐이다.
    """
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
        # S03 규칙: status='suspended' ⇔ halt_state. S03B 가 무거래 연속 판정을 더하면 술어를 넓힌다
        "n_status_halt_mismatch": _n(
            ctx, f"SELECT count(*) FROM {v} WHERE (status = 'suspended') <> halt_state"),
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
    }
    return _result("EG3_universe", checks, metrics, "유니버스 상태 불변식 성립")


eg3_universe.gate_name = "EG3_universe"         # type: ignore[attr-defined]

UNIVERSE_DAILY = register(EquityTable(
    name="universe_daily",
    grain=("date", "ticker"),
    columns={"date": "DATE", "ticker": "VARCHAR", "status": "VARCHAR", "market": "VARCHAR",
             "sec_type": "VARCHAR", "halt_state": "BOOLEAN", "admin_state": "BOOLEAN",
             "admin_state_basis": "VARCHAR", "liquidation_window": "BOOLEAN",
             "signal_halt": "BOOLEAN", "signal_halt_release": "BOOLEAN",
             "signal_admin": "BOOLEAN", "signal_liquidation": "BOOLEAN",
             "signal_delist": "BOOLEAN", "admin_flag": "BOOLEAN",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("security_span", "trading_calendar", "security", "stg_listing_daily",
            "stg_master_daily", "stg_disclosure", "stg_price_daily", "stg_etf_price_daily"),
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
        "stg_listing_daily": ("ticker", "date", "market", "sect_tp", "sect_available"),
        "stg_master_daily": ("ticker", "date", "is_admin_issue", "is_trade_halt",
                             "is_liquidation"),
        "stg_disclosure": ("rcept_no", "rcept_dt", "ticker", "has_ticker", "report_nm"),
        "stg_price_daily": ("ticker", "date", "volume_shr"),
        "stg_etf_price_daily": ("ticker", "date", "volume_shr")},
    available_basis=("default",),
    content_date_column="date",
    consts=("admin_window_td",),
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
        "n_all_missing": int(not any(p == "all" for p, _, _ in rows)),
        "n_predicate_unbound": len(unbound),
    }
    metrics: dict[str, object] = {
        "n_rows": len(rows), "policy_counts": _counts(ctx, "policy"),
        "unbound_predicates": unbound, "policy_vocab": list(POLICY_VOCAB),
        "threshold_kind_vocab": list(THRESHOLD_KIND_VOCAB),
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
    consts=("version",),
    extra_gates=(eg3_policy,),
    declaration_table=True,
))

TABLES: tuple[EquityTable, ...] = (UNIVERSE_DAILY, UNIVERSE_POLICY)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s03.json"
"""이 슬라이스가 요구하는 `_const` 상수의 초기값. 승인 뒤 `data/equity/baseline.json` 에 병합."""

__all__ = ["BASELINE_SEED", "TABLES", "UNIVERSE_DAILY", "UNIVERSE_POLICY"]
