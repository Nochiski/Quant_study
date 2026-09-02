"""DART stage 테이블 선언 (STAGE_DESIGN v2.2 §4 DART). 코드가 아니라 목록이다."""
from __future__ import annotations

from .model import (
    AVAILABLE_NONE,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    ExtraColumn,
    Invariant,
    SourceRef,
    TableRule,
    p_headroom,
)

# ── stg_rcept_dt_map (참조표, §1 예외 d) — rcept_no → rcept_dt. disclosure 유래, 키당 값 불변 ──
STG_RCEPT_DT_MAP = TableRule(
    name="stg_rcept_dt_map",
    sources=(SourceRef("dart", "dart_disclosure", "disclosure"),),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("rcept_dt", "rcept_dt", KIND_DATE_YMD8, required=True),
    ),
    natural_key=("rcept_no",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("row_hash", "dup_seq", "collected_at"),
    lag_known=False,
    available=AVAILABLE_NONE,            # 참조표 — available_date 비부여 (§6)
    payload_columns=("rcept_no", "rcept_dt"),   # 페이지 경계 중복(618 그룹)은 이 투영에서 접힌다
    key_unique=True,                     # 실측: rcept_no 당 distinct rcept_dt > 1 = 0
)

# ── stg_fin (dart_fin_raw 28컬럼) — 골격 키 예외: ticker·date 없음, 키는 요청축 8컬럼 ─────────


def _amt(src: str) -> ColumnRule:
    """재무 금액 컬럼 — survey v2 전수: 정수 max 18·소수 2 → Decimal(38,4) (설계 확정)."""
    return ColumnRule(src, src, KIND_NUMERIC, 38, 4)


_SENTINEL = "-표준계정코드 미사용-"
STG_FIN = TableRule(
    name="stg_fin",
    sources=(SourceRef("dart", "dart_fin_raw", "fin"),),
    columns=(
        ColumnRule("req_corp_code", "corp_code", KIND_TEXT, expected_len=8, key=True),
        ColumnRule("req_bsns_year", "bsns_year", KIND_TEXT, expected_len=4, key=True),
        ColumnRule("req_reprt_code", "reprt_code", KIND_TEXT, expected_len=5, key=True),
        ColumnRule("req_fs_div", "fs_div", KIND_TEXT, key=True),
        ColumnRule("sj_div", "sj_div", KIND_TEXT, key=True),
        ColumnRule("account_id", "account_id", KIND_TEXT, key=True),        # 원문 — 정규화 금지
        ColumnRule("account_detail", "account_detail", KIND_TEXT, key=True),  # 원문 보존
        ColumnRule("ord", "ord", KIND_NUMERIC, *p_headroom(3), key=True),
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, required=True),
        ColumnRule("corp_code", "corp_code_resp", KIND_TEXT),
        ColumnRule("bsns_year", "bsns_year_resp", KIND_TEXT),
        ColumnRule("reprt_code", "reprt_code_resp", KIND_TEXT),
        ColumnRule("sj_nm", "sj_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("account_nm", "account_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("thstrm_nm", "thstrm_nm", KIND_TEXT, normalize_text=True),      # 날짜 파싱 금지
        _amt("thstrm_amount"),
        _amt("thstrm_add_amount"),
        ColumnRule("frmtrm_nm", "frmtrm_nm", KIND_TEXT, normalize_text=True),
        _amt("frmtrm_amount"),
        ColumnRule("frmtrm_q_nm", "frmtrm_q_nm", KIND_TEXT, normalize_text=True),
        _amt("frmtrm_q_amount"),
        _amt("frmtrm_add_amount"),
        ColumnRule("bfefrmtrm_nm", "bfefrmtrm_nm", KIND_TEXT, normalize_text=True),
        _amt("bfefrmtrm_amount"),
        ColumnRule("currency", "currency", KIND_TEXT),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
                 "account_detail", "ord"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("row_hash", "dup_seq", "collected_at"),
    lag_known=False,                     # 공개일은 참조표(derived)
    available=AvailableRule("lookup", table="stg_rcept_dt_map", local_key="rcept_no",
                            lookup_key="rcept_no", lookup_value="rcept_dt"),
    key_unique=True,                     # 실측: 8컬럼 자연키 위반 0 (15,375,024행)
    extras=(
        ExtraColumn("bsns_year_mismatch", 's."req_bsns_year" <> s."bsns_year"'),
        ExtraColumn("account_std", f"s.\"account_id\" <> '{_SENTINEL}'"),
        ExtraColumn("account_detail_path",
                    "CASE WHEN s.\"account_detail\" IN ('-', '') THEN NULL::VARCHAR[] "
                    "ELSE string_split(s.\"account_detail\", '|') END"),
        ExtraColumn("is_krw", "s.\"currency\" = 'KRW'"),
    ),
    invariants=(
        Invariant("currency_null", "currency IS NULL OR currency = ''"),
    ),
)

TABLES: tuple[TableRule, ...] = (STG_RCEPT_DT_MAP, STG_FIN,)
