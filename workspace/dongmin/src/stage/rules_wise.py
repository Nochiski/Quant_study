"""WISE stage 테이블 선언 (STAGE_DESIGN v2.2 §4 WISE). 코드가 아니라 목록이다."""
from __future__ import annotations

from .model import (
    AVAILABLE_NONE,
    KIND_BOOL,
    KIND_DATE_DOT,
    KIND_DATE_ISO,
    KIND_DATE_SLASH,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    BlobSource,
    ColumnRule,
    ExtraColumn,
    SourceRef,
    TableRule,
    p_headroom,
)

# ── stg_consensus_monthly (ws_raw cF5001+cF5002 blob 언네스트, §1 예외 c·e) ────────────────────
_CONS_P, _CONS_S = 20, 4                 # 실측 EPS 5자리·매출(억원) 7자리 + 소수 2 → 여유
STG_CONSENSUS_MONTHLY = TableRule(
    name="stg_consensus_monthly",
    sources=(SourceRef("wise", "ws_raw", "ws_raw"),),
    columns=(
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("pkey", "target_period", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("metric", "metric", KIND_TEXT, key=True),          # eps | revenue | parse_failed
        ColumnRule("obs_label", "obs_label", KIND_TEXT, key=True),    # 원문 라벨 보존
        ColumnRule("obs_label", "obs_date", KIND_DATE_SLASH),
        ColumnRule("unit", "unit", KIND_TEXT),                        # 데이터 값 — 스케일 변환 금지
        ColumnRule("consensus", "consensus", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("consensus_min", "consensus_min", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("consensus_max", "consensus_max", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("close_price_krw", "close_price_krw", KIND_NUMERIC, 14, 2),
        ColumnRule("target_price_krw", "target_price_krw", KIND_NUMERIC, 14, 2),
        ColumnRule("in_5001", "in_5001", KIND_BOOL),
        ColumnRule("in_5002", "in_5002", KIND_BOOL),
    ),
    natural_key=("ticker", "fetched_date", "target_period", "metric", "obs_label"),
    partition_class="date_axis",
    partition_expr="substr(fetched_date, 1, 4)",
    partition_src="fetched_date",
    observed_src="fetched_at",
    write_mode="append_only",
    fanout=1,                            # 파서 출력 행 기준. blob→행 계상은 G8
    payload_exclude=("fetched_at",),
    lag_known=True,                      # 06:00 KST 수집 = 그날 장 시작 전 가용 (측정된 수집 시각)
    available=AvailableRule("column", column="fetched_date", basis="measured"),
    key_unique=True,
    blob_source=BlobSource("wise", "ws_raw", ("cF5001", "cF5002"), "parse_consensus_monthly"),
)


# ── 공통 조각 ─────────────────────────────────────────────────────────────────────────────────────
_WS = SourceRef("wise", "ws_raw", "ws_raw")
_FETCHED_MEASURED = AvailableRule("column", column="fetched_date", basis="measured")


def _blob_table(name: str, columns: tuple[ColumnRule, ...], natural_key: tuple[str, ...],
                parser: str, eps: tuple[str, ...],
                extras: tuple[ExtraColumn, ...] = ()) -> TableRule:
    """ws_raw blob 언네스트 테이블의 공통 골격 (§1 예외 c). 파티션·시각·가용은 monthly 와 동일."""
    return TableRule(
        name=name, sources=(_WS,), columns=columns, natural_key=natural_key,
        partition_class="date_axis", partition_expr="substr(fetched_date, 1, 4)",
        partition_src="fetched_date", observed_src="fetched_at", write_mode="append_only",
        fanout=1, payload_exclude=("fetched_at",), lag_known=True, available=_FETCHED_MEASURED,
        key_unique=True, extras=extras, blob_source=BlobSource("wise", "ws_raw", eps, parser),
    )


# ── stg_consensus_annual / stg_consensus_quarterly (c1050001_data pkey=T2Y / T2Q) ────────────────
# 값은 WISE 표시 단위 그대로(매출·영업이익·순이익 억원, EPS·BPS 원 — T4 ACC_NM '매출액(억원)' 실측이
# 근거인 카탈로그 지식). 데이터에 단위 컬럼이 없으므로 접미사·스케일 변환 없이 싣는다(§4 WISE).
def _periodic_columns() -> tuple[ColumnRule, ...]:
    return (
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("period_label", "period_label", KIND_TEXT, key=True),   # '2022.12(A)' 원문
        ColumnRule("period", "period", KIND_TEXT, expected_len=6),          # 파서 유도 'YYYYMM'
        ColumnRule("period_kind", "period_kind", KIND_TEXT),                # A(실적) | E(추정)
        ColumnRule("fs_basis", "fs_basis", KIND_TEXT, normalize_text=True),  # IFRS연결·별도·GAAP
        ColumnRule("revenue", "revenue", KIND_NUMERIC, 20, 4),
        ColumnRule("yoy", "yoy_pct", KIND_NUMERIC, 12, 4),
        ColumnRule("op", "op", KIND_NUMERIC, 20, 4),
        ColumnRule("ni", "ni", KIND_NUMERIC, 20, 4),
        ColumnRule("eps", "eps", KIND_NUMERIC, 14, 4),
        ColumnRule("bps", "bps", KIND_NUMERIC, 14, 4),
        ColumnRule("per", "per", KIND_NUMERIC, 12, 4),
        ColumnRule("pbr", "pbr", KIND_NUMERIC, 12, 4),
        ColumnRule("roe", "roe_pct", KIND_NUMERIC, 12, 4),
        ColumnRule("ev_ebitda", "ev_ebitda", KIND_NUMERIC, 12, 4),
        ColumnRule("tot_row", "tot_row", KIND_NUMERIC, 5, 0),
    )


_PERIODIC_KEY = ("ticker", "fetched_date", "period_label")
STG_CONSENSUS_ANNUAL = _blob_table("stg_consensus_annual", _periodic_columns(), _PERIODIC_KEY,
                                   "parse_consensus_annual", ("c1050001_data",))
STG_CONSENSUS_QUARTERLY = _blob_table("stg_consensus_quarterly", _periodic_columns(), _PERIODIC_KEY,
                                      "parse_consensus_quarterly", ("c1050001_data",))

# ── stg_consensus_matrix (c1050001_data pkey='T4:YYYYMM' — 계정 9 × lookback 5) ─────────────────
STG_CONSENSUS_MATRIX = _blob_table(
    "stg_consensus_matrix",
    (
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("target_period", "target_period", KIND_TEXT, expected_len=6, key=True),  # YYYYMM
        ColumnRule("acc_cd", "acc_cd", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("lookback_idx", "lookback_idx", KIND_TEXT, expected_len=1, key=True),  # VALn
        ColumnRule("lookback", "lookback", KIND_TEXT),                  # current·1w·1m·3m·1y
        ColumnRule("target_label", "target_label", KIND_TEXT),          # 'YYYY/MM'
        ColumnRule("seq", "seq", KIND_NUMERIC, 4, 0),
        ColumnRule("acc_nm", "acc_nm", KIND_TEXT, normalize_text=True),  # 단위 포함 '매출액(억원)'
        ColumnRule("base_date", "base_date", KIND_DATE_YMD8),           # DT — WISE 기준일
        ColumnRule("value", "value", KIND_NUMERIC, 24, 6),
    ),
    ("ticker", "fetched_date", "target_period", "acc_cd", "lookback_idx"),
    "parse_consensus_matrix", ("c1050001_data",),
)

# ── stg_fin_wise (cF3002 재무제표 · cF4002 재무비율, pkey='Y') — ACCODE 기준 wide, Decimal(38,6) ──
_FIN_WISE_P, _FIN_WISE_S = 38, 6


def _fw(src: str, name: str | None = None) -> ColumnRule:
    return ColumnRule(src, name or src, KIND_NUMERIC, _FIN_WISE_P, _FIN_WISE_S)


STG_FIN_WISE = _blob_table(
    "stg_fin_wise",
    (
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("ep", "ep", KIND_TEXT, expected_len=6, key=True),        # cF3002 | cF4002
        ColumnRule("seq", "seq", KIND_NUMERIC, 5, 0, key=True),   # DATA 위치 — cF4002 ACCODE 중복
        ColumnRule("ackind", "ackind", KIND_TEXT),
        ColumnRule("accode", "accode", KIND_TEXT, expected_len=6),
        ColumnRule("acc_nm", "acc_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("lvl", "lvl", KIND_NUMERIC, 3, 0),
        ColumnRule("grp_typ", "grp_typ", KIND_NUMERIC, 3, 0),
        ColumnRule("unt_typ", "unt_typ", KIND_NUMERIC, 5, 0),
        ColumnRule("p_accode", "p_accode", KIND_TEXT),
        *(ColumnRule(f"period_label_{i}", f"period_label_{i}", KIND_TEXT, normalize_text=True)
          for i in range(1, 7)),
        *(_fw(f"val_{i}") for i in range(1, 7)),
        *(_fw(f"val_q{i}") for i in (1, 2, 4, 5, 6)),
        _fw("yyoy", "yyoy_pct"), _fw("yeyoy", "yeyoy_pct"), _fw("qoq", "qoq_pct"),
        _fw("yoy", "yoy_pct"),
        _fw("qoq_e", "qoq_e_pct"), _fw("yoy_e", "yoy_e_pct"),
        ColumnRule("qoq_comment", "qoq_comment", KIND_TEXT),        # 계산식 원문 — 개행 보존
        ColumnRule("yoy_comment", "yoy_comment", KIND_TEXT),
        ColumnRule("qoq_e_comment", "qoq_e_comment", KIND_TEXT),
        ColumnRule("yoy_e_comment", "yoy_e_comment", KIND_TEXT),
        ColumnRule("point_cnt", "point_cnt", KIND_NUMERIC, 5, 0),
        ColumnRule("fs_basis", "fs_basis", KIND_TEXT, normalize_text=True),   # FIN
        ColumnRule("freq", "freq", KIND_TEXT, normalize_text=True),           # FRQ
    ),
    ("ticker", "fetched_date", "ep", "seq"),
    "parse_fin_wise", ("cF3002", "cF4002"),
)

# ── stg_analyst_summary (c1010001 HTML cTB15 — 추정기관수) ────────────────────────────────────────
STG_ANALYST_SUMMARY = _blob_table(
    "stg_analyst_summary",
    (
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("base_date", "base_date", KIND_DATE_DOT),            # '[기준:YYYY.MM.DD]'
        ColumnRule("opinion_score", "opinion_score", KIND_NUMERIC, 6, 2),
        ColumnRule("target_price_krw", "target_price_krw", KIND_NUMERIC, 14, 0),   # 표 머리 '(원)'
        ColumnRule("eps_krw", "eps_krw", KIND_NUMERIC, 14, 0),
        ColumnRule("per", "per", KIND_NUMERIC, 10, 2),
        ColumnRule("analyst_count", "analyst_count", KIND_NUMERIC, 5, 0),
        ColumnRule("no_opinion_note", "no_opinion_note", KIND_TEXT, normalize_text=True),
    ),
    ("ticker", "fetched_date"),
    "parse_analyst_summary", ("c1010001",),
)

# ── v3 미러 4종 — 2026-04-03~09-02 동결 사본 (결정 ⑤ 09-02 개정), 규칙 분해는 §6 ─────────────────
_TICKER_KEY = ColumnRule("stock_code", "ticker", KIND_TEXT, expected_len=6, key=True)

STG_V3_REVISION_DAILY = TableRule(
    name="stg_v3_revision_daily",
    sources=(SourceRef("wise", "v3_consensus_revision_daily", "v3"),),
    columns=(
        _TICKER_KEY,
        ColumnRule("base_date", "date", KIND_DATE_ISO, key=True),         # 내용 기준일 라벨
        ColumnRule("target_period", "target_period", KIND_TEXT, expected_len=7, key=True),
        ColumnRule("opinion", "opinion", KIND_NUMERIC, *p_headroom(3, 2)),
        ColumnRule("revenue", "revenue", KIND_NUMERIC, *p_headroom(8, 1)),
        ColumnRule("op", "op", KIND_NUMERIC, *p_headroom(8, 1)),
        ColumnRule("ni", "ni", KIND_NUMERIC, *p_headroom(8, 1)),
        ColumnRule("eps", "eps", KIND_NUMERIC, *p_headroom(6, 0)),
        ColumnRule("per", "per", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("bps", "bps", KIND_NUMERIC, *p_headroom(7, 0)),
        ColumnRule("pbr", "pbr", KIND_NUMERIC, *p_headroom(5, 2)),
        ColumnRule("roe", "roe_pct", KIND_NUMERIC, *p_headroom(5, 2)),
        ColumnRule("collected_date", "collected_date", KIND_DATE_ISO),   # NULL 1,390행 (실측)
    ),
    natural_key=("ticker", "date", "target_period"),
    partition_expr="substr(base_date, 1, 4)", partition_src="base_date",
    payload_exclude=("copied_at",),
    # §6: 수집일 실재 = measured. NULL 행은 base_date 대용(default) + coverage_degraded — 실측
    # collected = base + 1영업일 100% 이므로 default 는 "당일 가용" 이 아니라 "사실 없음" 이다.
    available=AvailableRule("column", column="collected_date", basis="measured",
                            fallback_column="date"),
    extras=(ExtraColumn("coverage_degraded", 's."collected_date" IS NULL'),),
    partition_class="date_axis", observed_src="copied_at", write_mode="first_write_wins",
    fanout=1, lag_known=False,
)

STG_V3_ANALYST_OPINIONS = TableRule(
    name="stg_v3_analyst_opinions",
    sources=(SourceRef("wise", "v3_analyst_opinions", "v3"),),
    columns=(
        _TICKER_KEY,
        ColumnRule("snapshot_date", "date", KIND_DATE_ISO, key=True),
        ColumnRule("opinion_score", "opinion_score", KIND_NUMERIC, *p_headroom(3, 2)),
        ColumnRule("target_price", "target_price_krw", KIND_NUMERIC, *p_headroom(7, 0)),
        ColumnRule("estimated_eps", "estimated_eps", KIND_NUMERIC, *p_headroom(6, 0)),
        ColumnRule("estimated_per", "estimated_per", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("analyst_count", "analyst_count", KIND_NUMERIC, *p_headroom(2, 0)),
    ),
    natural_key=("ticker", "date"),
    partition_expr="substr(snapshot_date, 1, 4)", partition_src="snapshot_date",
    payload_exclude=("copied_at",),
    available=AvailableRule("column", column="date", basis="measured"),
    partition_class="date_axis", observed_src="copied_at", write_mode="first_write_wins",
    fanout=1, lag_known=False,
)

STG_V3_CONSENSUS_ANNUAL = TableRule(
    name="stg_v3_consensus_annual",
    sources=(SourceRef("wise", "v3_consensus_annual", "v3"),),
    columns=(
        ColumnRule("sync_date", "sync_date", KIND_DATE_ISO, key=True),   # 09-01·09-02 두 판본
        _TICKER_KEY,
        ColumnRule("period", "period", KIND_TEXT, expected_len=7, key=True),
        ColumnRule("period_type", "period_type", KIND_TEXT, key=True),
        ColumnRule("data_type", "data_type", KIND_TEXT, key=True),
        ColumnRule("revenue", "revenue", KIND_NUMERIC, *p_headroom(9, 1)),
        ColumnRule("yoy", "yoy_pct", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("op", "op", KIND_NUMERIC, *p_headroom(8, 1)),
        ColumnRule("ni", "ni", KIND_NUMERIC, *p_headroom(8, 1)),
        ColumnRule("eps", "eps", KIND_NUMERIC, *p_headroom(6, 0)),
        ColumnRule("bps", "bps", KIND_NUMERIC, *p_headroom(7, 0)),
        ColumnRule("per", "per", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("pbr", "pbr", KIND_NUMERIC, *p_headroom(6, 2)),
        ColumnRule("roe", "roe_pct", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("ev_ebitda", "ev_ebitda", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("accounting_standard", "accounting_standard", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("sync_date", "ticker", "period", "period_type", "data_type"),
    partition_expr="substr(sync_date, 1, 4)", partition_src="sync_date",
    payload_exclude=("copied_at", "row_hash"),
    available=AvailableRule("column", column="sync_date", basis="measured"),
    partition_class="date_axis", observed_src="copied_at", write_mode="first_write_wins",
    fanout=1, lag_known=False,
)

_COMPARE_PS = {"opinion": (3, 2), "revenue": (8, 1), "op": (8, 1), "ni": (8, 1), "eps": (6, 0),
               "per": (7, 2), "bps": (7, 0), "pbr": (5, 2), "roe": (6, 2)}   # survey v2 최댓값
STG_V3_REVISION_COMPARE = TableRule(
    name="stg_v3_revision_compare",
    sources=(SourceRef("wise", "v3_consensus_revision_compare", "v3"),),
    columns=(
        ColumnRule("sync_date", "sync_date", KIND_DATE_ISO, key=True),
        _TICKER_KEY,
        ColumnRule("target_period", "target_period", KIND_TEXT, expected_len=7, key=True),
        *(ColumnRule(f"{m}_{h}", f"{m}_{h}", KIND_NUMERIC, *p_headroom(*ps))
          for m, ps in _COMPARE_PS.items() for h in ("1w", "1m", "3m", "1y")),
    ),
    natural_key=("sync_date", "ticker", "target_period"),
    partition_expr="substr(sync_date, 1, 4)", partition_src="sync_date",
    payload_exclude=("copied_at", "row_hash"),
    available=AvailableRule("column", column="sync_date", basis="measured"),
    partition_class="date_axis", observed_src="copied_at", write_mode="first_write_wins",
    fanout=1, lag_known=False,
)

# ── stg_wise_coverage — 이력 아님, 종목당 1행 현재 상태 (§3 temporality ⓐ → _current) ────────────
_KST_DATE = 'CAST(TRY_CAST(s."checked_at" AS TIMESTAMP) + INTERVAL 9 HOUR AS DATE)'
STG_WISE_COVERAGE = TableRule(
    name="stg_wise_coverage",
    sources=(SourceRef("wise", "ws_coverage", "ws_coverage"),),
    columns=(
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("status", "status_current", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("ticker",),
    partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="checked_at",
    write_mode="upsert",                 # 재확인이 덮어쓴다 — version_loss_upstream
    fanout=1,
    payload_exclude=("checked_at",),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=True,                     # 실측 2,566행 = 2,566종목
    extras=(ExtraColumn("checked_date_current", _KST_DATE),),
)

# ── stg_calls_wise — ws_call_log, 3분류 재료 ────────────────────────────────────────────────
STG_CALLS_WISE = TableRule(
    name="stg_calls_wise",
    sources=(SourceRef("wise", "ws_call_log", "ws_call_log"),),
    columns=(
        ColumnRule("ts", "ts", KIND_TEXT, key=True),                       # 시계 컬럼 실명 (§3)
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("ep", "ep", KIND_TEXT, key=True),
        ColumnRule("pkey", "pkey", KIND_TEXT, key=True, blank_is_value=True),  # '' = 목록 호출
        ColumnRule("status", "status", KIND_TEXT, normalize_text=True),
        ColumnRule("bytes", "bytes", KIND_NUMERIC, *p_headroom(6, 0)),
        ColumnRule("ms", "ms", KIND_NUMERIC, *p_headroom(4, 0)),
    ),
    natural_key=("ts", "ticker", "ep", "pkey"),
    partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="ts",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(),
    lag_known=False,
    available=AVAILABLE_NONE,
)

TABLES: tuple[TableRule, ...] = (
    STG_CONSENSUS_MONTHLY, STG_CONSENSUS_ANNUAL, STG_CONSENSUS_QUARTERLY, STG_CONSENSUS_MATRIX,
    STG_ANALYST_SUMMARY, STG_FIN_WISE, STG_V3_REVISION_DAILY, STG_V3_ANALYST_OPINIONS,
    STG_V3_CONSENSUS_ANNUAL, STG_V3_REVISION_COMPARE, STG_WISE_COVERAGE, STG_CALLS_WISE,
)
