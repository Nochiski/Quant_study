"""KRX stage 테이블 선언 (STAGE_DESIGN v2.2 §4 KRX). 코드가 아니라 목록이다.

(p,s)·결측·부호는 `survey_out/v2/krx.*.json` 전수 실측이 근거다. 단위 접미사는 SPEC §2-4
"KRX 금액 컬럼 전부 = 원"에 근거해 금액 컬럼만 `_krw`, 지수 포인트는 접미사 없이 `_idx`.
"""
from __future__ import annotations

from .model import (
    AVAILABLE_NONE,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    CrossCheck,
    ExtraColumn,
    Invariant,
    SourceRef,
    TableRule,
    p_headroom,
)

# ── stg_price_daily (KRX stk+ksq bydd, 17컬럼 UNION) ──────────────────────────
_PRICE_P, _PRICE_S = p_headroom(7)          # 가격 max 7자리
_FLUC_P, _FLUC_S = p_headroom(9, 2)         # FLUC_RT max 정수 7 + 소수 2 (survey p=9)
STG_PRICE_DAILY = TableRule(
    name="stg_price_daily",
    sources=(SourceRef("krx", "krx_stk_bydd_trd", "stk"),
             SourceRef("krx", "krx_ksq_bydd_trd", "ksq")),
    columns=(
        ColumnRule("BAS_DD", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("ISU_CD", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("ISU_NM", "name", KIND_TEXT, normalize_text=True),
        ColumnRule("MKT_NM", "market", KIND_TEXT, normalize_text=True),
        ColumnRule("SECT_TP_NM", "sect_tp", KIND_TEXT, normalize_text=True,
                   nonempty_flag="sect_available"),
        ColumnRule("TDD_CLSPRC", "close_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S),
        ColumnRule("CMPPREVDD_PRC", "change_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S),
        ColumnRule("FLUC_RT", "fluc_pct", KIND_NUMERIC, _FLUC_P, _FLUC_S),
        ColumnRule("TDD_OPNPRC", "open_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("TDD_HGPRC", "high_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("TDD_LWPRC", "low_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("ACC_TRDVOL", "volume_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("ACC_TRDVAL", "value_krw", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("MKTCAP", "mktcap_krw", KIND_NUMERIC, *p_headroom(16)),
        ColumnRule("LIST_SHRS", "list_shrs", KIND_NUMERIC, *p_headroom(10)),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(BAS_DD, 1, 4)",
    partition_src="BAS_DD",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("bas_dd_req", "collected_at"),
    lag_known=True,                      # 가격 = 당일 실시간 관측 실증 (결정 ⑦)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # (ISU_CD, BAS_DD) 유일 — S1 실측 dedup 0
    invariants=(
        Invariant("mktcap", "mktcap_krw <> close_krw * list_shrs"),
        Invariant("market_src", "NOT ((market = 'KOSPI' AND _src = 'stk') "
                                "OR (market = 'KOSDAQ' AND _src = 'ksq'))"),
        Invariant("ohl_pattern", "((open_krw IS NULL)::INT + (high_krw IS NULL)::INT "
                                 "+ (low_krw IS NULL)::INT) NOT IN (0, 3)"),
    ),
    cross_check=CrossCheck(
        db="kiwoom", table="ka10060_investor_flows",
        join_sql="o.ticker = s.ticker AND o.dt = strftime(s.date, '%Y%m%d')",
        close_match_sql="abs(TRY_CAST(o.cur_prc AS BIGINT)) = s.close_krw",
        volume_match_sql="TRY_CAST(o.acc_trde_prica AS BIGINT) = s.volume_shr",
    ),
)

# ── stg_etf_price_daily (KRX etf_bydd) ────────────────────────────────────────
# 가격 테이블과 불변식이 달라 분리 (§4). O/H/L '0' 정책만 가격과 같다 — 실측 n_zero
# 38,012(3컬럼 동일), 그중 거래량>0 이 2행. cross_check 없음(키움 ka10060 은 주식만).
STG_ETF_PRICE_DAILY = TableRule(
    name="stg_etf_price_daily",
    sources=(SourceRef("krx", "krx_etf_bydd_trd", "etf"),),
    columns=(
        ColumnRule("BAS_DD", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("ISU_CD", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("ISU_NM", "name", KIND_TEXT, normalize_text=True),
        ColumnRule("TDD_CLSPRC", "close_krw", KIND_NUMERIC, *p_headroom(7)),
        ColumnRule("CMPPREVDD_PRC", "change_krw", KIND_NUMERIC, *p_headroom(6)),
        ColumnRule("FLUC_RT", "fluc_pct", KIND_NUMERIC, *p_headroom(4, 2)),
        ColumnRule("NAV", "nav_krw", KIND_NUMERIC, *p_headroom(9, 2)),
        ColumnRule("TDD_OPNPRC", "open_krw", KIND_NUMERIC, *p_headroom(7),
                   zero_is_missing=True),
        ColumnRule("TDD_HGPRC", "high_krw", KIND_NUMERIC, *p_headroom(7),
                   zero_is_missing=True),
        ColumnRule("TDD_LWPRC", "low_krw", KIND_NUMERIC, *p_headroom(7),
                   zero_is_missing=True),
        ColumnRule("ACC_TRDVOL", "volume_shr", KIND_NUMERIC, *p_headroom(11)),
        ColumnRule("ACC_TRDVAL", "value_krw", KIND_NUMERIC, *p_headroom(13)),
        ColumnRule("MKTCAP", "mktcap_krw", KIND_NUMERIC, *p_headroom(14)),
        # 순자산총액 = ETF 원장의 고유 컬럼 (DATA_CATALOG ET-01: 종가·NAV·순자산총액·좌수)
        ColumnRule("INVSTASST_NETASST_TOTAMT", "netasset_krw", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("LIST_SHRS", "list_shrs", KIND_NUMERIC, *p_headroom(11)),
        ColumnRule("IDX_IND_NM", "obj_index_name", KIND_TEXT, normalize_text=True),
        # 기초지수 3컬럼 — 지수 포인트라 _krw 금지. 빈값 107,069행(ledger_blank)
        ColumnRule("OBJ_STKPRC_IDX", "obj_close_idx", KIND_NUMERIC, *p_headroom(8, 2)),
        ColumnRule("CMPPREVDD_IDX", "obj_change_idx", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("FLUC_RT_IDX", "obj_fluc_pct", KIND_NUMERIC, *p_headroom(6, 2)),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(BAS_DD, 1, 4)",
    partition_src="BAS_DD",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("bas_dd_req", "collected_at"),
    lag_known=True,                      # 가격류 — 당일 실시간 관측 (§6, 결정 ⑦)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 서버 실측 09-03: 키 중복 0 (upsert 원장)
    invariants=(                         # survey v2 전수 n_neg=0 인 컬럼만
        Invariant("volume_negative", "volume_shr < 0"),
        Invariant("mktcap_negative", "mktcap_krw < 0"),
        Invariant("nav_negative", "nav_krw < 0"),
    ),
)

# ── stg_index_daily (kospi_dd + kosdaq_dd UNION) ──────────────────────────────
# 컬럼 실명은 `IDX_NM` — `index_name` 은 원장에 없다(§11 도구 교훈 ③, v2 유령 컬럼).
# 키에 IDX_CLSS 가 필수: 업종지수명 20개가 양시장 중복 — (IDX_NM,BAS_DD) 충돌 71,158쌍.
# IDX_CLSS 길이는 시장별로 다르다(KOSPI 5 / KOSDAQ 6) → expected_len 선언 불가.
_IDX_P, _IDX_S = p_headroom(8, 2)        # 지수 포인트 max 정수 6 + 소수 2 (kospi survey)
STG_INDEX_DAILY = TableRule(
    name="stg_index_daily",
    sources=(SourceRef("krx", "krx_kospi_dd_trd", "kospi"),
             SourceRef("krx", "krx_kosdaq_dd_trd", "kosdaq")),
    columns=(
        ColumnRule("BAS_DD", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("IDX_CLSS", "index_class", KIND_TEXT, key=True),
        ColumnRule("IDX_NM", "index_name", KIND_TEXT, key=True),   # 조인 키 — 정규화 금지
        ColumnRule("CLSPRC_IDX", "close_idx", KIND_NUMERIC, _IDX_P, _IDX_S),
        ColumnRule("CMPPREVDD_IDX", "change_idx", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("FLUC_RT", "fluc_pct", KIND_NUMERIC, *p_headroom(4, 2)),
        ColumnRule("OPNPRC_IDX", "open_idx", KIND_NUMERIC, _IDX_P, _IDX_S),
        ColumnRule("HGPRC_IDX", "high_idx", KIND_NUMERIC, _IDX_P, _IDX_S),
        ColumnRule("LWPRC_IDX", "low_idx", KIND_NUMERIC, _IDX_P, _IDX_S),
        ColumnRule("ACC_TRDVOL", "volume_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("ACC_TRDVAL", "value_krw", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("MKTCAP", "mktcap_krw", KIND_NUMERIC, *p_headroom(16)),
    ),
    natural_key=("index_class", "index_name", "date"),
    partition_class="date_axis",
    partition_expr="substr(BAS_DD, 1, 4)",
    partition_src="BAS_DD",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("bas_dd_req", "collected_at"),
    lag_known=True,                      # 지수 = 가격류 (§6 주의 — 당일 실시간 관측)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 서버 실측 09-03: 키 중복 0 (upsert 원장)
    invariants=(
        Invariant("volume_negative", "volume_shr < 0"),
        Invariant("mktcap_negative", "mktcap_krw < 0"),
        Invariant("close_negative", "close_idx < 0"),
    ),
)

# ── stg_listing_daily (stk_isu + ksq_isu UNION) — 당일 상태 스냅샷 ─────────────
# 티커는 `ISU_SRT_CD`(len 6). `ISU_CD` 는 12자리 ISIN — 존재 검증을 통과하는 오답 컬럼이라
# `isin`(expected_len=12)으로만 싣는다(§11 도구 교훈 ②). isu_base 응답에는 BAS_DD 가 없어
# 날짜 축은 요청일 `bas_dd_req` 다(backfill_krx.py EPS 주석).
STG_LISTING_DAILY = TableRule(
    name="stg_listing_daily",
    sources=(SourceRef("krx", "krx_stk_isu_base_info", "stk"),
             SourceRef("krx", "krx_ksq_isu_base_info", "ksq")),
    columns=(
        ColumnRule("bas_dd_req", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("ISU_SRT_CD", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("ISU_CD", "isin", KIND_TEXT, expected_len=12),   # 12자 ISIN — 티커 아님
        ColumnRule("ISU_NM", "name", KIND_TEXT, normalize_text=True),
        ColumnRule("ISU_ABBRV", "name_abbrv", KIND_TEXT, normalize_text=True),
        ColumnRule("ISU_ENG_NM", "name_eng", KIND_TEXT, normalize_text=True),
        ColumnRule("LIST_DD", "list_date", KIND_DATE_YMD8),
        ColumnRule("MKT_TP_NM", "market", KIND_TEXT, normalize_text=True),
        ColumnRule("SECUGRP_NM", "secugrp", KIND_TEXT, normalize_text=True),
        # stk 는 전건 빈값(survey kind=empty) · ksq 만 유효 — 단독 필터 금지, 플래그 병기
        ColumnRule("SECT_TP_NM", "sect_tp", KIND_TEXT, normalize_text=True,
                   nonempty_flag="sect_available"),
        ColumnRule("KIND_STKCERT_TP_NM", "stkcert_tp", KIND_TEXT, normalize_text=True),
        # 비수치 어휘 73,615행(stk 31,487 + ksq 42,128) = cast_failed → par_value_kind 가 보존
        ColumnRule("PARVAL", "par_value_krw", KIND_NUMERIC, *p_headroom(7, 2)),
        ColumnRule("LIST_SHRS", "list_shrs", KIND_NUMERIC, *p_headroom(10)),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(bas_dd_req, 1, 4)",
    partition_src="bas_dd_req",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),   # bas_dd_req 는 키라 payload 에서 뺄 필요가 없다
    lag_known=False,                     # 마스터 = 공표 시점 미상 (§6 주의. T+1 08:00 은 카탈로그)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 서버 실측 09-03: 키 중복 0 (upsert 원장)
    extras=(
        # 액면가 범주: 수치면 'numeric', 아니면 원문 어휘 그대로(원문 유실 방지 — §5 신뢰 불가 값)
        ExtraColumn("par_value_kind",
                    "CASE WHEN s.\"PARVAL\" IS NULL THEN NULL "
                    "WHEN s.\"PARVAL\" = '' THEN 'blank' "
                    "WHEN TRY_CAST(replace(s.\"PARVAL\", ',', '') AS DECIMAL(9,2)) IS NOT NULL "
                    "THEN 'numeric' ELSE s.\"PARVAL\" END"),
    ),
    invariants=(
        Invariant("list_shrs_negative", "list_shrs < 0"),
        Invariant("par_value_negative", "par_value_krw < 0"),
    ),
)

# ── stg_ingest_krx (krx.ingest_log) — 수집 관리 테이블 ─────────────────────────
STG_INGEST_KRX = TableRule(
    name="stg_ingest_krx",
    sources=(SourceRef("krx", "ingest_log", "krx"),),
    columns=(
        ColumnRule("endpoint", "endpoint", KIND_TEXT, key=True),   # 식별자 — 정규화 금지
        ColumnRule("bas_dd", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(4)),
        ColumnRule("status", "status", KIND_TEXT),                 # ok / holiday / rate / error
        ColumnRule("note", "note", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("endpoint", "date"),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=AVAILABLE_NONE,            # 관리 테이블 — 비부여 (§6)
    key_unique=True,                     # 원장 PK (endpoint, bas_dd) — backfill_krx.py DDL
    invariants=(
        Invariant("n_rows_negative", "n_rows < 0"),
    ),
)

TABLES: tuple[TableRule, ...] = (STG_PRICE_DAILY, STG_ETF_PRICE_DAILY, STG_INDEX_DAILY,
                                 STG_LISTING_DAILY, STG_INGEST_KRX)
