"""KRX stage 테이블 선언 (STAGE_DESIGN v2.2 §4 KRX). 코드가 아니라 목록이다."""
from __future__ import annotations

from .model import (
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    CrossCheck,
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

TABLES: tuple[TableRule, ...] = (STG_PRICE_DAILY,)
