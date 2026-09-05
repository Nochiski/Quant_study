"""S01 식별 슬라이스 — `corp` · `security` · `corp_ticker` (EQUITY_DESIGN v1.2 §4-1).

셋 다 차원 테이블(`AVAILABLE_NONE`)이고 파티션은 `whole` 이다. 산출식은 `sql/<table>.sql`,
숫자 상수는 전부 `baseline.json` → `_const` 로만 들어간다(`baseline_seed_s01.json` 참조).

테이블 특화 EG3 술어(GATES §1 EG3 표)는 `extra_gates` 훅으로 붙인다. 여기 있는 술어는 전부
상수 없이 판정 가능한 것뿐이다 — baseline 이 필요한 EG3-P10 `security.delisted_total`·
EG3-P11 `security.delist_conflict_max`·A4 `corp_ticker.map_rate_min` 은 서버 실측 뒤 등재이므로
지금은 **기록형 metric** 으로만 남긴다(GATES §0-1 기록형).
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import AVAILABLE_NONE, BASIS_VOCAB, EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-1 — P11 전 이력 어휘에서 나온 sec_type 10종. 'other' 는 격리가 아니라 행 유지다.
SEC_TYPE_VOCAB: tuple[str, ...] = (
    "common", "preferred", "reit", "ship_fund", "fund", "foreign", "dr", "spac", "etf", "other")
INDUTY_CLASS_VOCAB: tuple[str, ...] = ("financial", "nonfinancial")
LINK_BASIS_VOCAB: tuple[str, ...] = ("isin8", "corp_map", "none")

TICKER_LEN = 6                          # DESIGN §3 — `ticker` TEXT(6). 정수 캐스팅 금지


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _outside_vocab(ctx: EquityGateContext, column: str, values: tuple[str, ...]) -> int:
    """`column` 값 중 어휘 밖 건수. NULL 은 세지 않는다(컬럼별 NULL 허용은 따로 판정)."""
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" WHERE "{column}" IS NOT NULL '
                   f'AND "{column}" NOT IN ({_vocab_sql(values)})')


def _bad_ticker_len(ctx: EquityGateContext, column: str = "ticker") -> int:
    """EG3-P07 — `ticker` 가 VARCHAR(6). 정수 캐스팅되면 '0001A0' 가 조용히 사라진다."""
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" WHERE "{column}" IS NULL '
                   f"OR typeof(\"{column}\") <> 'VARCHAR' OR length(\"{column}\") <> {TICKER_LEN}")


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 그대로 싣는다."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    why = "; ".join(f"{k}={v}" for k, v in sorted(bad.items()))
    return GateResult(name, GateStatus.FAIL, why, merged)


# ── corp ─────────────────────────────────────────────────────────────────────
def eg3_corp(ctx: EquityGateContext) -> GateResult:
    """EG3-P08(`induty_code` 공란 0) + `induty_class` 어휘 폐쇄. `fiscal_month` 결측은 기록형."""
    v = ctx.out_view
    checks = {
        "n_induty_code_blank": _n(ctx, f"SELECT count(*) FROM \"{v}\" "
                                       "WHERE coalesce(trim(induty_code), '') = ''"),
        "n_induty_class_outside_vocab": _outside_vocab(ctx, "induty_class", INDUTY_CLASS_VOCAB),
    }
    metrics: dict[str, object] = {
        "n_fiscal_month_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE fiscal_month IS NULL'),
        "n_induty_class_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE induty_class IS NULL'),
        "n_financial": _n(ctx, f'SELECT count(*) FROM "{v}" '
                               "WHERE induty_class = 'financial'"),
        "induty_class_vocab": list(INDUTY_CLASS_VOCAB),
    }
    return _result("EG3_corp", checks, metrics, "법인 속성 불변식 성립")


eg3_corp.gate_name = "EG3_corp"                 # type: ignore[attr-defined]

CORP = register(EquityTable(
    name="corp",
    grain=("corp_code",),
    columns={"corp_code": "VARCHAR", "corp_name": "VARCHAR", "fiscal_month": "INTEGER",
             "fiscal_month_basis": "VARCHAR", "induty_code": "VARCHAR",
             "induty_class": "VARCHAR"},
    inputs=("stg_corp_map", "stg_company"),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,              # 차원 테이블 — EG2 skip(dimension_table)
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(DISTINCT corp_code) FROM stg_corp_map",
    sql_path=SQL_DIR / "corp.sql",
    input_columns={"stg_corp_map": ("corp_code", "corp_name_current"),
                   "stg_company": ("corp_code", "acc_mt", "induty_code_current",
                                   "observed_date")},
    consts=("financial_ksic_prefix",),
    extra_gates=(eg3_corp,),
))


# ── security ─────────────────────────────────────────────────────────────────
def eg3_security(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P10(부분)·P13 — 티커 폭, 폐지 종목의 `delist_date` 보유, 어휘 폐쇄.

    P10 의 모집단 등식(`delisted_total`)과 P11(`delist_conflict_max`)은 baseline 상수가
    필요해 기록형 metric 으로만 남긴다. 상수 없이도 성립하는 축 — "KIS 가 폐지라고 말한
    티커는 `delist_date` 를 가진다" — 만 폐기형으로 건다.
    """
    v = ctx.out_view
    basis = BASIS_VOCAB          # 파생 날짜 축의 basis — DESIGN §1 어휘 전체를 쓴다
    checks = {
        "n_sec_type_outside_vocab": _outside_vocab(ctx, "sec_type", SEC_TYPE_VOCAB),
        "n_sec_type_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE sec_type IS NULL'),
        "n_ticker_bad_width": _bad_ticker_len(ctx),
        "n_list_date_basis_outside_vocab": _outside_vocab(ctx, "list_date_basis", basis),
        "n_delist_date_basis_outside_vocab": _outside_vocab(ctx, "delist_date_basis", basis),
        "n_delisted_without_delist_date": _n(
            ctx, f'SELECT count(*) FROM "{v}" s JOIN (SELECT DISTINCT ticker '
                 "FROM stg_delisted_master WHERE lstg_abol_dt IS NOT NULL) d USING (ticker) "
                 "WHERE s.delist_date IS NULL"),
    }
    metrics: dict[str, object] = {
        "n_sec_type_other": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                    "WHERE sec_type = 'other'"),
        "n_delist_conflict": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE delist_conflict'),
        "n_delisted": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE delist_date IS NOT NULL'),
        "n_corp_code_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE corp_code IS NULL'),
        "sec_type_counts": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f'SELECT sec_type, count(*) FROM "{v}" GROUP BY 1 ORDER BY 1').fetchall()},
        "sec_type_vocab": list(SEC_TYPE_VOCAB),
    }
    return _result("EG3_security", checks, metrics, "종목 어휘·폐지축 불변식 성립")


eg3_security.gate_name = "EG3_security"         # type: ignore[attr-defined]

SECURITY = register(EquityTable(
    name="security",
    grain=("ticker",),
    columns={"ticker": "VARCHAR", "corp_code": "VARCHAR", "isin": "VARCHAR",
             "name_current": "VARCHAR", "sec_type": "VARCHAR", "list_date": "DATE",
             "list_date_basis": "VARCHAR", "delist_date_krx": "DATE",
             "delist_date_kis": "DATE", "delist_conflict": "BOOLEAN", "delist_date": "DATE",
             "delist_date_basis": "VARCHAR"},
    inputs=("stg_listing_daily", "stg_etf_price_daily", "stg_delisted_master",
            "stg_index_daily", "stg_corp_map"),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=("SELECT count(*) FROM (SELECT DISTINCT ticker FROM stg_listing_daily "
                 "UNION SELECT DISTINCT ticker FROM stg_etf_price_daily)"),
    sql_path=SQL_DIR / "security.sql",
    input_columns={
        "stg_listing_daily": ("ticker", "date", "isin", "name", "list_date", "secugrp",
                              "sect_tp", "stkcert_tp"),
        "stg_etf_price_daily": ("ticker", "date", "name"),
        "stg_delisted_master": ("ticker", "lstg_abol_dt"),
        "stg_index_daily": ("date",),
        "stg_corp_map": ("ticker", "corp_code")},
    consts=("backfill_end",),
    extra_gates=(eg3_security,),
))


# ── corp_ticker ──────────────────────────────────────────────────────────────
def eg3_corp_ticker(ctx: EquityGateContext) -> GateResult:
    """EG3-P02(KR7 isin8 그룹당 `is_common` 정확히 1) + `corp_code`·`link_basis` 정합.

    비KR7 은 술어 밖이다 — 제외가 아니라 그룹 축이 없다(같은 isin8 을 다른 발행사가 쓴다).
    """
    v = ctx.out_view
    checks = {
        "n_kr7_group_common_not_one": _n(
            ctx, "SELECT count(*) FROM (SELECT isin8, count(*) FILTER (WHERE is_common) AS c "
                 f'FROM "{v}" WHERE isin8 LIKE \'KR7%\' GROUP BY isin8 HAVING c <> 1)'),
        "n_ticker_bad_width": _bad_ticker_len(ctx),
        "n_link_basis_outside_vocab": _outside_vocab(ctx, "link_basis", LINK_BASIS_VOCAB),
        "n_link_basis_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE link_basis IS NULL'),
        # corp_code 가 붙은 행은 corp_map 에 실재해야 하고, 그 티커가 corp_map 에 직접
        # 있으면 값이 같아야 한다 (우선주는 corp_map 에 없으므로 후자는 조건부).
        "n_corp_code_not_in_map": _n(
            ctx, f'SELECT count(*) FROM "{v}" t WHERE t.corp_code IS NOT NULL AND NOT EXISTS '
                 "(SELECT 1 FROM stg_corp_map m WHERE m.corp_code = t.corp_code)"),
        "n_corp_code_conflicts_map": _n(
            ctx, f'SELECT count(*) FROM "{v}" t JOIN (SELECT ticker, min(corp_code) AS corp_code '
                 "FROM stg_corp_map WHERE nullif(trim(ticker), '') IS NOT NULL GROUP BY ticker) m "
                 "USING (ticker) WHERE t.corp_code IS DISTINCT FROM m.corp_code"),
        # link_basis='none' 인데 corp_code 가 붙어 있으면 근거 없는 링크다.
        "n_link_basis_none_with_corp": _n(
            ctx, f'SELECT count(*) FROM "{v}" '
                 "WHERE link_basis = 'none' AND corp_code IS NOT NULL"),
    }
    n_kr7 = _n(ctx, f"SELECT count(*) FROM \"{v}\" WHERE isin8 LIKE 'KR7%'")
    n_kr7_mapped = _n(ctx, f'SELECT count(*) FROM "{v}" '
                           "WHERE isin8 LIKE 'KR7%' AND corp_code IS NOT NULL")
    metrics: dict[str, object] = {
        "n_kr7": n_kr7, "n_kr7_mapped": n_kr7_mapped,
        # GATES §5-A4 — 매핑률 분모는 KR7 티커. 임계(`map_rate_min`)는 서버 실측 뒤 등재.
        "map_rate": (n_kr7_mapped / n_kr7) if n_kr7 else 0.0,
        "n_corp_code_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE corp_code IS NULL'),
        "link_basis_counts": {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
            f'SELECT link_basis, count(*) FROM "{v}" GROUP BY 1 ORDER BY 1').fetchall()},
    }
    return _result("EG3_corp_ticker", checks, metrics, "isin8 그룹·법인 링크 불변식 성립")


eg3_corp_ticker.gate_name = "EG3_corp_ticker"   # type: ignore[attr-defined]

CORP_TICKER = register(EquityTable(
    name="corp_ticker",
    grain=("ticker",),
    columns={"ticker": "VARCHAR", "isin8": "VARCHAR", "corp_code": "VARCHAR",
             "common_ticker": "VARCHAR", "is_common": "BOOLEAN", "link_basis": "VARCHAR"},
    inputs=("stg_listing_daily", "stg_etf_price_daily", "stg_corp_map"),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    # 우변은 `security` 행수인데(DESIGN §4-1) 게이트는 stage 뷰만 볼 수 있으므로 같은 값을
    # 내는 stage 식 — (listing ∪ etf) distinct ticker — 을 그대로 쓴다.
    eg1_rhs_sql=("SELECT count(*) FROM (SELECT DISTINCT ticker FROM stg_listing_daily "
                 "UNION SELECT DISTINCT ticker FROM stg_etf_price_daily)"),
    sql_path=SQL_DIR / "corp_ticker.sql",
    input_columns={
        "stg_listing_daily": ("ticker", "date", "isin", "name", "secugrp", "sect_tp",
                              "stkcert_tp"),
        "stg_etf_price_daily": ("ticker",),
        "stg_corp_map": ("ticker", "corp_code")},
    consts=("isin8_len",),
    extra_gates=(eg3_corp_ticker,),
))

TABLES: tuple[EquityTable, ...] = (CORP, SECURITY, CORP_TICKER)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s01.json"
"""이 슬라이스가 요구하는 `_const` 상수의 초기값. 승인 뒤 `data/equity/baseline.json` 에 병합."""
