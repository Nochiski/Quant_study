"""S26 WISE 컨센서스·재무비율 — `consensus_matrix` · `consensus_annual` · `fin_ratio_wise`
(플랜 `2026-10-22-equity-wise-fields` T1, 사용자 10-22 "리비전 데이터는 공개 데이터잖아" · #608).

세 표 모두 stage WISE 표 하나씩을 읽어 1단계 산출을 읽지 않는다(원장 → stage → 여기, 백필 없음 — 2026-09-01 부터).
TECH_DEBT B-23 의 equity 쪽 첫걸음이다 — fi·compat 가 '한시 예외'로 stage 를 직접 읽던 같은 원천을 equity 에 둔다
(그 둘의 읽기 전환은 이 범위 밖).

**무엇을 주나**
  consensus_matrix — 계정 9 × 비교 시점 5(current·1w·1m·3m·1y) × 결산기 3. 리비전(추정치 변화)의 재료다.
                     `fy_rank` = 수집 달 이후로 끝나는 결산기의 가까운 순서(compat·fi 엔진·#609 가 각자 쓰던 규칙).
  consensus_annual — 연간 실적(A)·추정(E) 표. 그날 수집분에 당해 12월기 추정이 있는지가 곧 추정치 보유 여부다.
  fin_ratio_wise   — 재무비율(cF4002) 연간 열을 행으로 편 것. PER·PBR·배당수익률의 실적 열은 결산기 말 주가 기준.

**PIT**(플랜 D1, 사용자 10-23 "데이터는 신선도 최대한 좋을 때 써야지") — 가용일은 stage 의 수집 시각
`fetched_at_utc`(stage 2.10.0)로 행마다 센 '지식 세션'이고 랙은 1 세션이다. 장 시작(09:00 KST = 00:00 UTC) 전에
받은 값은 직전 세션 마감 뒤 지식이라 그날 바로 쓰고, 그 뒤에 받은 값은 다음 세션부터 쓴다. 수집 시각은 운영 중에
06:00·18:05·15:52·11:33 으로 바뀌어 왔다 — 날짜 경계를 코드에 박지 않고 시각에서 센다. 수집 시각이 없으면
수집일(보수), 수집 시각의 날짜가 이 판 달력의 마지막 거래일 뒤면 당기지 않는다(`sql/*.sql` 머리 주석).

테이블 특화 술어(`extra_gates`):
  세 표 공통(폐기형) — 가용일 독립 재계산 불일치 0 · 가용일 > 수집일 0. 기록형: 수집 시각 없는 행 수.
  EG3_consensus_matrix — 폐기형: lookback 어휘 폐쇄 · lookback_idx ↔ lookback 대응 · fy_rank 가 지난 결산기에만
                         NULL · ticker 폭. 기록형: 수집일 수·종목 수·최신 수집일과 그날 FY1 영업이익 보유 종목 수.
  EG3_consensus_annual — 폐기형: period_kind 어휘(A·E) · period 6자리 · ticker 폭. 기록형: 최신 수집일의 당해
                         12월기 추정 보유 종목 수.
  EG3_fin_ratio_wise   — 폐기형: ticker 폭. 기록형: 라벨에서 결산기를 못 읽은 칸 수 · 최신 수집일 종목 수.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, FieldProfile, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# stage 파서(`stage/parsers.py` parse_consensus_matrix)의 VALn → lookback 대응 — 그쪽이 정본, 여기는 폐쇄 검사용 사본.
LOOKBACKS: dict[str, str] = {"1": "current", "2": "1w", "3": "1m", "4": "3m", "5": "1y"}
PERIOD_KINDS: tuple[str, ...] = ("A", "E")
REJECT_REASONS: tuple[str, ...] = ()     # 격리 사유 없음 — stage 행을 그대로 싣는다

# 리비전 필드가 읽는 계정 — WISE 매트릭스 acc_cd(c1050001 T4)
MATRIX_ACCOUNTS: dict[str, str] = {"op": "121500", "ni": "122710"}
# 재무비율 필드가 읽는 계정 — WISE cF4002 accode
RATIO_ACCOUNTS: dict[str, str] = {"per": "382100", "pbr": "382500", "dividend_yield": "431800"}

# EG1 우변 — `fin_ratio_wise` 가 펴는 칸 수(cF4002 행의 빈 문자열이 아닌 라벨 수). 산출을 읽지 않는다.
_RATIO_CELLS_SQL = ("SELECT coalesce(sum(" + " + ".join(
    f"CAST(coalesce(period_label_{i}, '') <> '' AS INTEGER)" for i in range(1, 7))
    + "), 0) FROM stg_fin_wise WHERE ep = 'cF4002'")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _int(value: object) -> int:
    return int(str(value))


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _pit_checks(ctx: EquityGateContext) -> tuple[dict[str, int], dict[str, object]]:
    """가용일을 산출 SQL(ASOF 조인)과 다른 식(상관 서브쿼리)으로 다시 세어 대조한다 — 세 `.sql` 이 같은 규칙인지."""
    v = _q(ctx.out_view)
    n_mismatch, n_after_fetch, n_no_time = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE available_date IS DISTINCT FROM (
              CASE WHEN fetched_at_utc IS NULL THEN fetched_date
                   WHEN CAST(fetched_at_utc AS DATE) > (SELECT max(date) FROM trading_calendar)
                        THEN CAST(fetched_at_utc AS DATE)
                   ELSE (SELECT max(t.date) FROM trading_calendar t WHERE t.date <= CAST(o.fetched_at_utc AS DATE))
              END)),
          count(*) FILTER (WHERE available_date > fetched_date),
          count(*) FILTER (WHERE fetched_at_utc IS NULL)
        FROM {v} o""")
    return ({"n_available_recompute_mismatch": _int(n_mismatch), "n_available_after_fetch": _int(n_after_fetch)},
            {"n_without_fetch_time": _int(n_no_time)})


def _latest(ctx: EquityGateContext) -> str:
    """최신 수집일 술어 — 기록형 지표가 '오늘 판' 을 보게 한다."""
    v = _q(ctx.out_view)
    return f"fetched_date = (SELECT max(fetched_date) FROM {v})"


# ── EG3_consensus_matrix ─────────────────────────────────────────────────────

def eg3_consensus_matrix(ctx: EquityGateContext) -> GateResult:
    """lookback 어휘·대응, fy_rank 가 지난 결산기에만 NULL, ticker 폭."""
    v = _q(ctx.out_view)
    pairs = " OR ".join(f"(lookback_idx = '{i}' AND lookback = '{lb}')" for i, lb in LOOKBACKS.items())
    n_vocab, n_pair, n_rank, n_ticker = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE lookback IS NULL OR lookback NOT IN ({_vocab_sql(tuple(LOOKBACKS.values()))})),
          count(*) FILTER (WHERE NOT coalesce({pairs}, false)),
          count(*) FILTER (WHERE (fy_rank IS NULL) <> (target_period < strftime(fetched_date, '%Y%m'))),
          count(*) FILTER (WHERE ticker IS NULL OR length(ticker) <> {TICKER_LEN})
        FROM {v}""")
    checks = {
        "n_lookback_outside_vocab": _int(n_vocab),
        "n_lookback_idx_mismatch": _int(n_pair),
        "n_fy_rank_inconsistent": _int(n_rank),
        "n_ticker_bad_width": _int(n_ticker),
    }
    pit_checks, pit_metrics = _pit_checks(ctx)
    n_days, n_tickers, latest = _row(ctx, f"""
        SELECT count(DISTINCT fetched_date), count(DISTINCT ticker), max(fetched_date) FROM {v}""")
    n_latest, n_latest_fy1_op = _row(ctx, f"""
        SELECT count(DISTINCT ticker),
               count(DISTINCT ticker) FILTER (WHERE fy_rank = 1 AND acc_cd = '{MATRIX_ACCOUNTS["op"]}'
                                              AND lookback = 'current' AND value IS NOT NULL)
        FROM {v} WHERE {_latest(ctx)}""")
    metrics: dict[str, object] = {
        "n_fetched_dates": _int(n_days), "n_tickers": _int(n_tickers),
        "latest_fetched_date": None if latest is None else str(latest),
        "n_tickers_latest": _int(n_latest), "n_tickers_latest_fy1_op": _int(n_latest_fy1_op), **pit_metrics,
    }
    return _result("EG3_consensus_matrix", {**checks, **pit_checks}, metrics,
                   "lookback 어휘·대응·fy_rank·폭·가용일 성립")


eg3_consensus_matrix.gate_name = "EG3_consensus_matrix"     # type: ignore[attr-defined]


# ── EG3_consensus_annual ─────────────────────────────────────────────────────

def eg3_consensus_annual(ctx: EquityGateContext) -> GateResult:
    """period_kind 어휘·period 6자리·ticker 폭."""
    v = _q(ctx.out_view)
    n_kind, n_period, n_ticker = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE period_kind IS NULL OR period_kind NOT IN ({_vocab_sql(PERIOD_KINDS)})),
          count(*) FILTER (WHERE period IS NULL OR NOT regexp_matches(period, '^[0-9]{{6}}$')),
          count(*) FILTER (WHERE ticker IS NULL OR length(ticker) <> {TICKER_LEN})
        FROM {v}""")
    checks = {
        "n_period_kind_outside_vocab": _int(n_kind),
        "n_period_bad_width": _int(n_period),
        "n_ticker_bad_width": _int(n_ticker),
    }
    n_latest, n_fy0 = _row(ctx, f"""
        SELECT count(DISTINCT ticker),
               count(DISTINCT ticker) FILTER (WHERE period_kind = 'E'
                                              AND period = strftime(fetched_date, '%Y') || '12'
                                              AND op IS NOT NULL AND ni IS NOT NULL)
        FROM {v} WHERE {_latest(ctx)}""")
    pit_checks, pit_metrics = _pit_checks(ctx)
    metrics: dict[str, object] = {"n_tickers_latest": _int(n_latest),
                                  "n_tickers_latest_fy0_estimate": _int(n_fy0), **pit_metrics}
    return _result("EG3_consensus_annual", {**checks, **pit_checks}, metrics,
                   "period_kind 어휘·period 폭·ticker 폭·가용일 성립")


eg3_consensus_annual.gate_name = "EG3_consensus_annual"     # type: ignore[attr-defined]


# ── EG3_fin_ratio_wise ───────────────────────────────────────────────────────

def eg3_fin_ratio_wise(ctx: EquityGateContext) -> GateResult:
    """ticker 폭(폐기형). 결산기를 못 읽은 라벨은 그 칸만 못 쓰는 것이라 기록형으로 센다."""
    v = _q(ctx.out_view)
    (n_ticker,) = _row(ctx, f"SELECT count(*) FROM {v} WHERE ticker IS NULL OR length(ticker) <> {TICKER_LEN}")
    n_unparsed, n_latest = _row(ctx, f"""
        SELECT (SELECT count(*) FROM {v} WHERE period IS NULL),
               (SELECT count(DISTINCT ticker) FROM {v} WHERE {_latest(ctx)})""")
    pit_checks, pit_metrics = _pit_checks(ctx)
    metrics: dict[str, object] = {"n_period_unparsed": _int(n_unparsed), "n_tickers_latest": _int(n_latest),
                                  **pit_metrics}
    return _result("EG3_fin_ratio_wise", {"n_ticker_bad_width": _int(n_ticker), **pit_checks}, metrics,
                   "ticker 폭·가용일 성립")


eg3_fin_ratio_wise.gate_name = "EG3_fin_ratio_wise"     # type: ignore[attr-defined]


# ── 필드 프로파일 ─────────────────────────────────────────────────────────────

_DISC = ("WISE 화면 수집 시각(measured, stage fetched_at_utc)으로 센 지식 세션 → 다음 세션부터 쓴다. 장 시작(09:00) "
         "전 수집분은 그날 쓰고 그 뒤 수집분은 다음 세션부터다(플랜 D1)")
_SINCE = "백필 없음 — 2026-09-01 수집분부터다."
_MATRIX_NAMES = {"op": "영업이익", "ni": "순이익"}
_LOOKBACK_NAMES = {"1w": "1주 전", "1m": "1개월 전", "3m": "3개월 전"}


def _matrix_field(metric: str, lookback: str) -> FieldProfile:
    name = _MATRIX_NAMES[metric]
    if lookback == "current":
        field_id, label = f"wise.consensus_{metric}", f"WISE 컨센서스 {name}(FY1)"
        extra = ""
    else:
        when = _LOOKBACK_NAMES[lookback]
        field_id, label = f"wise.consensus_{metric}_{lookback}_ago", f"WISE 컨센서스 {name}(FY1, {when})"
        extra = f"WISE 가 그 수집일에 함께 준 {when} 시점 값이고 우리 이력을 늦춰 만든 값이 아니다. "
    return FieldProfile(
        field_id=field_id, columns=("value",), label=label, unit="억원", value_type="amount",
        frequency="session", recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False, disclosure_basis=_DISC,
        evidence=(f"consensus_matrix.value WHERE acc_cd='{MATRIX_ACCOUNTS[metric]}' AND lookback='{lookback}' "
                  "AND fy_rank = 1 — 수집 달 이후로 끝나는 가장 가까운 결산기(FY1). " + extra + _SINCE),
        coverage_axis="grid_security", scope="internal",
        row_filter=f"acc_cd = '{MATRIX_ACCOUNTS[metric]}' AND lookback = '{lookback}' AND fy_rank = 1",
        axis_columns=("ticker", "fetched_date"))


def _annual_field(metric: str) -> FieldProfile:
    name = _MATRIX_NAMES[metric]
    return FieldProfile(
        field_id=f"wise.fy0_{metric}_estimate", columns=(metric,),
        label=f"WISE 당해 12월 결산 {name} 추정(그날 수집분)", unit="억원", value_type="amount",
        frequency="session", recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False, disclosure_basis=_DISC,
        evidence=(f"consensus_annual.{metric} WHERE period_kind='E' AND period = 수집 연도 12월. 그날 수집분에 "
                  "추정이 없으면 행이 없다 — 추정치를 지금 갖고 있는지 가를 때 쓴다(추정은 발표 뒤 약 3개월이면 "
                  "빠진다, DECISIONS U1). " + _SINCE),
        coverage_axis="grid_security", scope="internal",
        row_filter="period_kind = 'E' AND period = strftime(fetched_date, '%Y') || '12'",
        axis_columns=("ticker", "fetched_date"))


def _ratio_field(name: str, label: str, unit: str) -> FieldProfile:
    return FieldProfile(
        field_id=f"wise.{name}", columns=("value",), label=label, unit=unit, value_type="ratio",
        frequency="report", recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False, disclosure_basis=_DISC,
        evidence=(f"fin_ratio_wise.value WHERE accode='{RATIO_ACCOUNTS[name]}' AND NOT is_estimate AND 12월기 — "
                  "그 가운데 가장 늦은 결산기를 고른다. 결산기 말 주가 기준이라 그해 안에서는 바뀌지 않는다. "
                  "stage 가 같은 원문을 접어 fetched_date 는 그 원문을 처음 본 날이고, 다음 판이 올 때까지 값이 "
                  "이어진다(이월 한도는 소비 규칙, 플랜 D2). " + _SINCE),
        coverage_axis="grid_security", scope="internal",
        row_filter=f"accode = '{RATIO_ACCOUNTS[name]}' AND NOT is_estimate AND period LIKE '____12'",
        axis_columns=("ticker", "fetched_date"))


FIELDS_MATRIX: tuple[FieldProfile, ...] = tuple(
    _matrix_field(metric, lookback) for metric in MATRIX_ACCOUNTS for lookback in ("current", "1w", "1m", "3m"))
FIELDS_ANNUAL: tuple[FieldProfile, ...] = tuple(_annual_field(metric) for metric in MATRIX_ACCOUNTS)
FIELDS_RATIO: tuple[FieldProfile, ...] = (
    _ratio_field("per", "WISE PER(최근 12월 결산 실적)", "배"),
    _ratio_field("pbr", "WISE PBR(최근 12월 결산 실적)", "배"),
    _ratio_field("dividend_yield", "WISE 현금배당수익률(최근 12월 결산 실적)", "pct"),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

_AVAILABLE_RULE = ("derived:fetched_at_utc — 수집 시각의 UTC 날짜(장 시작 09:00 KST 경계) 이하 마지막 거래일. 시각이 "
                   "없으면 fetched_date(measured), 달력 끝 뒤면 그 날짜 그대로(플랜 D1)")
_PIT_BASIS = ("measured", "derived")

CONSENSUS_MATRIX = register(EquityTable(
    name="consensus_matrix",
    grain=("ticker", "fetched_date", "target_period", "acc_cd", "lookback_idx"),
    columns={"ticker": "VARCHAR", "fetched_date": "DATE", "target_period": "VARCHAR",
             "acc_cd": "VARCHAR", "lookback_idx": "VARCHAR", "lookback": "VARCHAR", "acc_nm": "VARCHAR",
             "base_date": "DATE", "value": "DECIMAL(24,6)", "fy_rank": "BIGINT",
             "fetched_at_utc": "TIMESTAMP", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_consensus_matrix", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(fetched_date)",
    available_rule=_AVAILABLE_RULE,
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM stg_consensus_matrix",
    sql_path=SQL_DIR / "consensus_matrix.sql",
    input_columns={"stg_consensus_matrix": ("ticker", "fetched_date", "target_period", "acc_cd", "lookback_idx",
                                            "lookback", "acc_nm", "base_date", "value", "fetched_at_utc"),
                  "trading_calendar": ("date",)},
    # 가용일이 수집일보다 이를 수 있어(장 전 수집 → 직전 세션) 내용일 하한(EG2-P02)은 걸지 않는다 —
    # 대신 EG3_* 가 가용일을 다시 세어 대조하고 가용일 ≤ 수집일을 본다.
    available_basis=_PIT_BASIS,
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_consensus_matrix,),
    field_profiles=FIELDS_MATRIX,
))

CONSENSUS_ANNUAL = register(EquityTable(
    name="consensus_annual",
    grain=("ticker", "fetched_date", "period_label"),
    columns={"ticker": "VARCHAR", "fetched_date": "DATE", "period_label": "VARCHAR", "period": "VARCHAR",
             "period_kind": "VARCHAR", "fs_basis": "VARCHAR",
             "revenue": "DECIMAL(20,4)", "yoy_pct": "DECIMAL(12,4)", "op": "DECIMAL(20,4)",
             "ni": "DECIMAL(20,4)", "eps": "DECIMAL(14,4)", "bps": "DECIMAL(14,4)",
             "per": "DECIMAL(12,4)", "pbr": "DECIMAL(12,4)", "roe_pct": "DECIMAL(12,4)",
             "ev_ebitda": "DECIMAL(12,4)",
             "fetched_at_utc": "TIMESTAMP", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_consensus_annual", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(fetched_date)",
    available_rule=_AVAILABLE_RULE,
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM stg_consensus_annual",
    sql_path=SQL_DIR / "consensus_annual.sql",
    input_columns={"stg_consensus_annual": ("ticker", "fetched_date", "period_label", "period", "period_kind",
                                            "fs_basis", "revenue", "yoy_pct", "op", "ni", "eps", "bps",
                                            "per", "pbr", "roe_pct", "ev_ebitda", "fetched_at_utc"),
                  "trading_calendar": ("date",)},
    # 가용일이 수집일보다 이를 수 있어(장 전 수집 → 직전 세션) 내용일 하한(EG2-P02)은 걸지 않는다 —
    # 대신 EG3_* 가 가용일을 다시 세어 대조하고 가용일 ≤ 수집일을 본다.
    available_basis=_PIT_BASIS,
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_consensus_annual,),
    field_profiles=FIELDS_ANNUAL,
))

FIN_RATIO_WISE = register(EquityTable(
    name="fin_ratio_wise",
    grain=("ticker", "fetched_date", "seq", "slot"),
    columns={"ticker": "VARCHAR", "fetched_date": "DATE", "seq": "INTEGER", "slot": "INTEGER",
             "accode": "VARCHAR", "acc_nm": "VARCHAR", "unt_typ": "INTEGER",
             "period_label": "VARCHAR", "period": "VARCHAR", "is_estimate": "BOOLEAN",
             "value": "DECIMAL(38,6)",
             "fetched_at_utc": "TIMESTAMP", "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_fin_wise", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(fetched_date)",
    available_rule=_AVAILABLE_RULE,
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=_RATIO_CELLS_SQL,
    sql_path=SQL_DIR / "fin_ratio_wise.sql",
    input_columns={"stg_fin_wise": ("ticker", "fetched_date", "ep", "seq", "accode", "acc_nm", "unt_typ",
                                    *(f"period_label_{i}" for i in range(1, 7)),
                                    *(f"val_{i}" for i in range(1, 7)), "fetched_at_utc"),
                  "trading_calendar": ("date",)},
    # 가용일이 수집일보다 이를 수 있어(장 전 수집 → 직전 세션) 내용일 하한(EG2-P02)은 걸지 않는다 —
    # 대신 EG3_* 가 가용일을 다시 세어 대조하고 가용일 ≤ 수집일을 본다.
    available_basis=_PIT_BASIS,
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_fin_ratio_wise,),
    field_profiles=FIELDS_RATIO,
))

TABLES: tuple[EquityTable, ...] = (CONSENSUS_MATRIX, CONSENSUS_ANNUAL, FIN_RATIO_WISE)

__all__ = ["CONSENSUS_ANNUAL", "CONSENSUS_MATRIX", "FIELDS_ANNUAL", "FIELDS_MATRIX", "FIELDS_RATIO",
           "FIN_RATIO_WISE", "LOOKBACKS", "MATRIX_ACCOUNTS", "PERIOD_KINDS", "RATIO_ACCOUNTS",
           "REJECT_REASONS", "TABLES"]
