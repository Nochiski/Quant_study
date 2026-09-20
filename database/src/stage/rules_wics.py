"""WICS 섹터 구성 스냅샷 — 원장 `wiseindex.db` `wics_raw`(wiseindex `GetIndexComponets` 원문, 주 1회 토요일).

`stg_wics_components`: (기준일 dt, 요청 섹터코드 req_sec_cd, 종목) 한 행. L1 코드(3자리)로 부른 blob 과
L2 코드(5자리)로 부른 blob 이 같은 종목을 담으므로 `req_sec_cd` 가 키에 들어간다 — L1 행은 검산축이고
equity `sector_snapshot` 은 L2 행만 격자로 쓴다(플랜 wics-weekly T3). L2 라벨은 행의 `SEC_CD` 가 아니라
`IDX_CD`·`IDX_NM_KOR` 에 온다(WICS_PROBE §6). `MKT_VAL` 은 유동시총 백만원 → ×1e6 `_krw`, `APT_SHR_CNT` 는
유동주식수(주). 종목명 `CMP_KOR` 은 현재값이라 보존만 하고 조인하지 않는다(`_current` 접미).
available = `date`(기준일, basis default — WICS 일별 산출 관행이라 공표 시각 실측은 없다, lag_known=False).
백필은 하지 않는다(사용자 09-20) — 2026-09-18 부터만 있다.
"""
from __future__ import annotations

from .model import (
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    BlobSource,
    ColumnRule,
    SourceRef,
    TableRule,
)

_WICS = SourceRef("wiseindex", "wics_raw", "wics_raw")

STG_WICS_COMPONENTS = TableRule(
    name="stg_wics_components",
    sources=(_WICS,),
    columns=(
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("dt", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("req_sec_cd", "req_sec_cd", KIND_TEXT, key=True),          # 요청 코드(L1 3자리 / L2 5자리)
        ColumnRule("idx_cd", "idx_cd", KIND_TEXT),                             # 요청 코드의 지수 코드(= req_sec_cd)
        ColumnRule("idx_nm", "idx_nm", KIND_TEXT, normalize_text=True),        # 'WICS 반도체와반도체장비' — L2 라벨은 여기
        ColumnRule("sec_cd", "sec_cd", KIND_TEXT),                             # 행의 L1 코드(G45)
        ColumnRule("sec_nm", "sec_nm", KIND_TEXT, normalize_text=True),        # L1 라벨(IT)
        ColumnRule("mkt_val", "float_mktcap_krw", KIND_NUMERIC, 20, 0, unit_scale=1_000_000),   # 백만원 → 원
        ColumnRule("all_mkt_val", "all_float_mktcap_krw", KIND_NUMERIC, 20, 0, unit_scale=1_000_000),
        ColumnRule("wgt", "wgt_pct", KIND_NUMERIC, 12, 6),                     # 요청 지수 안 비중(%)
        ColumnRule("s_wgt", "s_wgt_pct", KIND_NUMERIC, 12, 6),                 # 누적 비중(%)
        ColumnRule("apt_shr_cnt", "float_shares_shr", KIND_NUMERIC, 20, 0),    # 유동주식수(주)
        ColumnRule("top60", "top60", KIND_TEXT),                               # 의미 미상 — 원문 보존
        ColumnRule("cal_wgt", "cal_wgt", KIND_TEXT),                           # 의미 미상 — 원문 보존
        ColumnRule("cmp_kor", "cmp_kor_current", KIND_TEXT, normalize_text=True),   # 현재 종목명 — 조인 금지
    ),
    natural_key=("ticker", "date", "req_sec_cd"),
    partition_class="date_axis",
    partition_expr="substr(dt, 1, 4)",
    partition_src="dt",
    observed_src="fetched_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("fetched_at",),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=True,
    blob_source=BlobSource(
        "wiseindex", "wics_raw", ("GetIndexComponets",), "parse_wics_components",
        required_columns=("dt", "sec_cd", "collected_at", "http_status", "n_rows", "body"),
        # 코드별 최신 판본(collected_at 최대)만 — 같은 (dt, sec_cd) 재수집은 새 판본으로 공존하지만 stage 는
        # 마지막 관측을 쓴다(같은 날 판본 접기 계약, STAGE_SPEC §2-12). 빈 응답(n_rows=0)은 제외.
        select_sql=(
            "SELECT sec_cd AS cmp_cd, 'GetIndexComponets' AS ep, dt AS pkey, dt AS fetched_date, body, "
            "collected_at AS fetched_at FROM {src} r WHERE http_status = 200 AND n_rows > 0 "
            "AND collected_at = (SELECT max(collected_at) FROM {src} x WHERE x.dt = r.dt AND x.sec_cd = r.sec_cd "
            "AND x.http_status = 200 AND x.n_rows > 0)"),
    ),
)

TABLES: tuple[TableRule, ...] = (STG_WICS_COMPONENTS,)
