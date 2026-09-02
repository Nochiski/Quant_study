"""WISE stage 테이블 선언 (STAGE_DESIGN v2.2 §4 WISE). 코드가 아니라 목록이다."""
from __future__ import annotations

from .model import (
    KIND_BOOL,
    KIND_DATE_ISO,
    KIND_DATE_SLASH,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    BlobSource,
    ColumnRule,
    SourceRef,
    TableRule,
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

TABLES: tuple[TableRule, ...] = (STG_CONSENSUS_MONTHLY,)
