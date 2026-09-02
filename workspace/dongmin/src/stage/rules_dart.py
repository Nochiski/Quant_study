"""DART stage 테이블 선언 (STAGE_DESIGN v2.2 §4 DART). 코드가 아니라 목록이다."""
from __future__ import annotations

from .model import (
    AVAILABLE_NONE,
    KIND_BOOL,
    KIND_DATE_DOT,
    KIND_DATE_ISO,
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
    key_unique=False,                    # append_only: 재수집 판본은 G6 축 (§7). S2 실측 위반 0
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

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 정기 보조원장 6종 (§4 DART "stg_dividend~stg_audit 6종" · SPEC §2-13·§2-14)
#
# 공통: 원장 dart · 파티션 receipt_axis `substr(rcept_no, 1, 4)` (§4 파티션 표) ·
#       observed_src collected_at · append_only · fanout 1 · available = 참조표 룩업 (§6) ·
#       lag_known False — rcept_dt 는 날짜뿐이고 접수 시각이 원장에 없다 (SPEC §2-19).
# ══════════════════════════════════════════════════════════════════════════════════════════════
_LEDGER_META = ("row_hash", "dup_seq", "collected_at")   # payload 제외 (§5). req_* 는 빌더 몫

# 요청축 3키 — stg_fin 과 같은 축 (§4 "키는 요청축"). 응답축 corp_code 는 `_resp` 로 따로 싣는다
_REQ_AXIS = (
    ColumnRule("req_corp_code", "corp_code", KIND_TEXT, expected_len=8, key=True),
    ColumnRule("req_bsns_year", "bsns_year", KIND_TEXT, expected_len=4, key=True),
    ColumnRule("req_reprt_code", "reprt_code", KIND_TEXT, expected_len=5, key=True),
)
# 응답 동봉 메타. corp_cls·corp_name 은 조회 시점의 현재값이라 `_current` 강제 (§3 temporality
# ⓐ — corp_cls='E' 의 26%가 과거 상장사). stlm_dt = 결산기준일, 가용일 유도 금지 (§4)
_RESP_META = (
    ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, required=True),
    ColumnRule("corp_code", "corp_code_resp", KIND_TEXT, expected_len=8),
    ColumnRule("corp_cls", "corp_cls_current", KIND_TEXT, expected_len=1),
    ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
    ColumnRule("stlm_dt", "stlm_dt", KIND_DATE_ISO),
)
_RCEPT_LOOKUP = AvailableRule("lookup", table="stg_rcept_dt_map", local_key="rcept_no",
                              lookup_key="rcept_no", lookup_value="rcept_dt")


def _row_kind(src: str) -> ExtraColumn:
    """집계행 카테고라이즈 (§5 · SPEC §2-13). 무필터 SUM 의 2~3중 계상을 막는 재료."""
    return ExtraColumn("row_kind", f"CASE WHEN trim(s.\"{src}\") IN ('합계', '계', '총계', '소계') "
                                   f"THEN 'aggregate' ELSE 'detail' END")


# ── stg_dividend ← dart_dividend (384,232행) ───────────────────────────────────────────────────
# se 는 단위 라벨을 품은 구분 문자열('(연결)당기순이익(백만원)'·'현금배당수익률(%)' 등 — survey
# 패턴)이라 금액 3컬럼의 단위가 행마다 다르다 → 단위 접미사 금지, unit_scale 금지 (§5).
# survey 전수: thstrm/frmtrm/lwfr 정수 11·소수 2, 비숫자 46~50행(캐스팅 실패 → G2).
_DIV_P, _DIV_S = p_headroom(13, 2)
STG_DIVIDEND = TableRule(
    name="stg_dividend",
    sources=(SourceRef("dart", "dart_dividend", "dividend"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("se", "se", KIND_TEXT, key=True),              # 구분 — 원문 보존(키 구성원)
        ColumnRule("stock_knd", "stock_knd", KIND_TEXT, key=True),  # '-' 270,320행은 그대로 둔다
        *_RESP_META,
        ColumnRule("thstrm", "thstrm", KIND_NUMERIC, _DIV_P, _DIV_S),
        ColumnRule("frmtrm", "frmtrm", KIND_NUMERIC, _DIV_P, _DIV_S),
        ColumnRule("lwfr", "lwfr", KIND_NUMERIC, _DIV_P, _DIV_S),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "se", "stock_knd"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # 유일성 미실측 — (se, stock_knd) 로 갈리는지 서버 대조 필요
)

# ── stg_shares ← dart_shares (97,194행) ────────────────────────────────────────────────────────
# 전 수량 컬럼이 주식수 → `_shr`. 콤마 표기 84~96%(SPEC §2-11)라 숫자 선언이 필수지만 각주
# 표기(survey 패턴 `가9)`·`(가9)`)가 섞여 컬럼당 240~3,316행이 cast_failed 가 된다 (G2 보고).
# se='합계' 집계행 (SPEC §2-13) → row_kind.
STG_SHARES = TableRule(
    name="stg_shares",
    sources=(SourceRef("dart", "dart_shares", "shares"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("se", "se", KIND_TEXT, key=True),               # 주식 종류 구분 + '합계'
        *_RESP_META,
        ColumnRule("isu_stock_totqy", "isu_stock_totqy_shr", KIND_NUMERIC, *p_headroom(15)),
        ColumnRule("now_to_isu_stock_totqy", "now_to_isu_stock_totqy_shr", KIND_NUMERIC,
                   *p_headroom(15)),
        ColumnRule("now_to_dcrs_stock_totqy", "now_to_dcrs_stock_totqy_shr", KIND_NUMERIC,
                   *p_headroom(15)),
        ColumnRule("redc", "redc_shr", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("profit_incnr", "profit_incnr_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("rdmstk_repy", "rdmstk_repy_shr", KIND_NUMERIC, *p_headroom(12)),
        ColumnRule("etc", "etc_shr", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("istc_totqy", "istc_totqy_shr", KIND_NUMERIC, *p_headroom(15)),
        ColumnRule("tesstk_co", "tesstk_co_shr", KIND_NUMERIC, *p_headroom(13)),
        ColumnRule("distb_stock_co", "distb_stock_co_shr", KIND_NUMERIC, *p_headroom(15)),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "se"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # 유일성 미실측
    extras=(_row_kind("se"),),
)

# ── stg_capital ← dart_capital (283,479행) ─────────────────────────────────────────────────────
# isu_dcrs_de 는 DART 유일의 점표기(`YYYY.MM.DD` 275,205 + '-' 8,274 — survey 전수, SPEC §2-14).
# 연도 오타 2120·2121·2202·2923 7행은 G7 이 셀만 격리한다(행 폐기 금지 — §9).
# 날짜가 키에 들어가야 증자 이벤트가 갈리는데 '-' 8,274행이 캐스팅되면 NULL 키로 reject 되므로
# 원문을 `isu_dcrs_de_raw` 로 한 번 더 실어 그쪽을 키로 쓴다 (stg_consensus_monthly 의
# obs_label/obs_date 선례와 같은 형태).
STG_CAPITAL = TableRule(
    name="stg_capital",
    sources=(SourceRef("dart", "dart_capital", "capital"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("isu_dcrs_de", "isu_dcrs_de_raw", KIND_TEXT, key=True),
        ColumnRule("isu_dcrs_stle", "isu_dcrs_stle", KIND_TEXT, key=True),
        ColumnRule("isu_dcrs_stock_knd", "isu_dcrs_stock_knd", KIND_TEXT, key=True),
        *_RESP_META,
        ColumnRule("isu_dcrs_de", "isu_dcrs_de", KIND_DATE_DOT),
        ColumnRule("isu_dcrs_qy", "isu_dcrs_qy_shr", KIND_NUMERIC, *p_headroom(14)),
        ColumnRule("isu_dcrs_mstvdv_amount", "isu_dcrs_mstvdv_amount_krw", KIND_NUMERIC,
                   *p_headroom(13)),
        ColumnRule("isu_dcrs_mstvdv_fval_amount", "isu_dcrs_mstvdv_fval_amount_krw", KIND_NUMERIC,
                   *p_headroom(11)),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "isu_dcrs_de_raw", "isu_dcrs_stle",
                 "isu_dcrs_stock_knd"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # 같은 날 같은 형태의 증자가 2건이면 충돌 — 미실측
)

# ── stg_tesstk ← dart_tesstk (328,704행) ───────────────────────────────────────────────────────
# 취득방법 3단(acqs_mth1 대분류 → 2 중분류 → 3 소분류)이 행을 가르고, acqs_mth1='총계' 가
# 집계행이다 (SPEC §2-13 — 명세가 "소계·총계 포함"을 명시). 수량 5컬럼 비숫자 0 (survey 전수).
STG_TESSTK = TableRule(
    name="stg_tesstk",
    sources=(SourceRef("dart", "dart_tesstk", "tesstk"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("acqs_mth1", "acqs_mth1", KIND_TEXT, key=True),
        ColumnRule("acqs_mth2", "acqs_mth2", KIND_TEXT, key=True),
        ColumnRule("acqs_mth3", "acqs_mth3", KIND_TEXT, key=True),
        ColumnRule("stock_knd", "stock_knd", KIND_TEXT, key=True),
        *_RESP_META,
        ColumnRule("bsis_qy", "bsis_qy_shr", KIND_NUMERIC, *p_headroom(13)),
        ColumnRule("change_qy_acqs", "change_qy_acqs_shr", KIND_NUMERIC, *p_headroom(12)),
        ColumnRule("change_qy_dsps", "change_qy_dsps_shr", KIND_NUMERIC, *p_headroom(12)),
        ColumnRule("change_qy_incnr", "change_qy_incnr_shr", KIND_NUMERIC, *p_headroom(8)),
        ColumnRule("trmend_qy", "trmend_qy_shr", KIND_NUMERIC, *p_headroom(13)),
        ColumnRule("rm", "rm", KIND_TEXT, normalize_text=True),      # 비고 — 설명류
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "acqs_mth1", "acqs_mth2", "acqs_mth3",
                 "stock_knd"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # 유일성 미실측
    extras=(_row_kind("acqs_mth1"),),
)

# ── stg_hyslr ← dart_hyslr (228,226행) ─────────────────────────────────────────────────────────
# 최대주주 현황. nm='계' 가 집계행 (SPEC §2-13 · survey 1글자 52,000행). 지분율 2컬럼은
# 정수 3·소수 2 인데 엑셀 오버플로 잔재 '#######' 가 5+4행 있어 cast_failed 가 된다 (G2 보고).
_RT_P, _RT_S = p_headroom(5, 2)
STG_HYSLR = TableRule(
    name="stg_hyslr",
    sources=(SourceRef("dart", "dart_hyslr", "hyslr"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("nm", "nm", KIND_TEXT, key=True),                # 성명 — 키라 정규화 금지
        ColumnRule("stock_knd", "stock_knd", KIND_TEXT, key=True),
        *_RESP_META,
        ColumnRule("relate", "relate", KIND_TEXT),                  # 관계 (NULL 51,993행)
        ColumnRule("bsis_posesn_stock_co", "bsis_posesn_stock_co_shr", KIND_NUMERIC,
                   *p_headroom(14)),
        ColumnRule("bsis_posesn_stock_qota_rt", "bsis_posesn_stock_qota_rt_pct", KIND_NUMERIC,
                   _RT_P, _RT_S),
        ColumnRule("trmend_posesn_stock_co", "trmend_posesn_stock_co_shr", KIND_NUMERIC,
                   *p_headroom(14)),
        ColumnRule("trmend_posesn_stock_qota_rt", "trmend_posesn_stock_qota_rt_pct", KIND_NUMERIC,
                   _RT_P, _RT_S),
        ColumnRule("rm", "rm", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "nm", "stock_knd"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # 동명이인·같은 이름의 법인 주주가 갈리는지 미실측
    extras=(_row_kind("nm"),),
)

# ── stg_audit ← dart_audit (93,037행) ──────────────────────────────────────────────────────────
# adt_opinion 은 enum 이 아니라 변형 434종 (SPEC §2-13, 09-02 실측). 원문을 그대로 싣고
# 분류만 병기한다: 공백 제거 후 **부적정 선매칭** → 의견거절 → 한정 → 적정, 그 외 other.
# ('LIKE %적정%' 를 먼저 쓰면 부적정이 적정으로 뒤집힌다 — SPEC 이 명시한 함정)
# 응답 bsns_year 는 연도가 아니라 기수 라벨('제99기(연결)' 형태 — survey 패턴, max 61자)이라
# 요청축 bsns_year 와 이름이 겹치지 않게 `_label` 로 싣고 날짜·숫자 파싱을 하지 않는다.
_ADT_NORM = "regexp_replace(s.\"adt_opinion\", '[[:space:]]', '', 'g')"
STG_AUDIT = TableRule(
    name="stg_audit",
    sources=(SourceRef("dart", "dart_audit", "audit"),),
    columns=(
        *_REQ_AXIS,
        ColumnRule("bsns_year", "bsns_year_label", KIND_TEXT, key=True),
        *_RESP_META,
        ColumnRule("adtor", "adtor", KIND_TEXT, normalize_text=True),          # 감사인
        ColumnRule("adt_opinion", "adt_opinion", KIND_TEXT),                   # 원문 보존
        ColumnRule("adt_reprt_spcmnt_matter", "adt_reprt_spcmnt_matter", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("emphs_matter", "emphs_matter", KIND_TEXT, normalize_text=True),
        ColumnRule("core_adt_matter", "core_adt_matter", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "bsns_year_label"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=_RCEPT_LOOKUP,
    key_unique=False,        # bsns_year_label 이 '-' 인 4,805행이 한 요청축에 몰리면 충돌
    extras=(
        ExtraColumn("adt_opinion_class",
                    f"CASE WHEN s.\"adt_opinion\" IS NULL THEN NULL "
                    f"WHEN {_ADT_NORM} LIKE '%부적정%' THEN '부적정' "
                    f"WHEN {_ADT_NORM} LIKE '%의견거절%' THEN '의견거절' "
                    f"WHEN {_ADT_NORM} LIKE '%한정%' THEN '한정' "
                    f"WHEN {_ADT_NORM} LIKE '%적정%' THEN '적정' ELSE 'other' END"),
    ),
)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 지분 공시 2종 — v2 ∪ v1 (§1 예외 a). v1 전용 194행 구제가 목적 (SPEC §3-6).
# v1 에는 row_hash·dup_seq·req_corp_code 가 없다 → 빌더가 NULL 패딩하므로 공통 컬럼만 선언한다.
# 두 판이 같은 레코드를 담으면 payload 투영이 같아 접힌다(§5) — 남는 것이 v1 전용분.
# rcept_dt 가 실재하고 ISO 10자리(survey 전수) → available = 그 컬럼, basis=measured (§6).
# ══════════════════════════════════════════════════════════════════════════════════════════════
STG_HOLDER_ELESTOCK = TableRule(
    name="stg_holder_elestock",
    sources=(SourceRef("dart", "dart_elestock", "v2"),
             SourceRef("dart", "dart_elestock_v1", "v1")),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("repror", "repror", KIND_TEXT, key=True),        # 보고자 — 키라 정규화 금지
        ColumnRule("rcept_dt", "rcept_dt", KIND_DATE_ISO, required=True),
        ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8),
        ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
        ColumnRule("isu_exctv_ofcps", "isu_exctv_ofcps", KIND_TEXT),      # 임원 직위
        ColumnRule("isu_exctv_rgist_at", "isu_exctv_rgist_at", KIND_TEXT),  # 등기임원 여부
        ColumnRule("isu_main_shrholdr", "isu_main_shrholdr", KIND_TEXT),  # 주요주주 여부
        ColumnRule("sp_stock_lmp_cnt", "sp_stock_lmp_cnt_shr", KIND_NUMERIC, *p_headroom(9)),
        ColumnRule("sp_stock_lmp_irds_cnt", "sp_stock_lmp_irds_cnt_shr", KIND_NUMERIC,
                   *p_headroom(9)),
        ColumnRule("sp_stock_lmp_rate", "sp_stock_lmp_rate_pct", KIND_NUMERIC, *p_headroom(14, 11)),
        ColumnRule("sp_stock_lmp_irds_rate", "sp_stock_lmp_irds_rate_pct", KIND_NUMERIC,
                   *p_headroom(15, 11)),
    ),
    natural_key=("rcept_no", "repror"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=AvailableRule("column", column="rcept_dt", basis="measured"),
    key_unique=False,        # 한 접수에 보고자 행이 하나뿐인지 미실측
)

STG_HOLDER_MAJORSTOCK = TableRule(
    name="stg_holder_majorstock",
    sources=(SourceRef("dart", "dart_majorstock", "v2"),
             SourceRef("dart", "dart_majorstock_v1", "v1")),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("repror", "repror", KIND_TEXT, key=True),
        ColumnRule("rcept_dt", "rcept_dt", KIND_DATE_ISO, required=True),
        ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8),
        ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
        ColumnRule("report_tp", "report_tp", KIND_TEXT),                  # 보고구분
        ColumnRule("report_resn", "report_resn", KIND_TEXT, normalize_text=True),   # 보고 사유
        ColumnRule("stkqy", "stkqy_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("stkqy_irds", "stkqy_irds_shr", KIND_NUMERIC, *p_headroom(9)),
        ColumnRule("stkrt", "stkrt_pct", KIND_NUMERIC, *p_headroom(13, 10)),
        ColumnRule("stkrt_irds", "stkrt_irds_pct", KIND_NUMERIC, *p_headroom(6, 4)),
        ColumnRule("ctr_stkqy", "ctr_stkqy_shr", KIND_NUMERIC, *p_headroom(9)),
        ColumnRule("ctr_stkrt", "ctr_stkrt_pct", KIND_NUMERIC, *p_headroom(5, 2)),
    ),
    natural_key=("rcept_no", "repror"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=AvailableRule("column", column="rcept_dt", basis="measured"),
    key_unique=False,
)

# ── stg_disclosure ← dart_disclosure (3,444,518행) ─────────────────────────────────────────────
# rcept_no 중복 618그룹(초과 620행)은 페이지 경계이고 req_page_no 는 req_* 라 빌더가 payload 에서
# 빼므로 전량 접힌다 — S1b 가 (rcept_no, rcept_dt) 투영으로 620행 접기·rcept_no 충돌 0 을 실측했다.
# rm 은 1~2글자 플래그(비블랭크 1,570,562행 중 1글자 1,441,757 · 2글자 128,805 — survey 전수,
# max_len 2)라 원문을 싣고 DART 공시검색 명세의 8코드를 불린으로 분해한다.
# is_correction 술어는 §4 가 글자 그대로 지정한 `report_nm LIKE '[%정정]%'` (577,072건=16.75%).
_RM_CODES = (("rm_kospi", "유"), ("rm_kosdaq", "코"), ("rm_bond", "채"), ("rm_konex", "넥"),
             ("rm_ftc", "공"), ("rm_consolidated", "연"), ("rm_corrected_later", "정"),
             ("rm_withdrawn", "철"))
STG_DISCLOSURE = TableRule(
    name="stg_disclosure",
    sources=(SourceRef("dart", "dart_disclosure", "disclosure"),),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("rcept_dt", "rcept_dt", KIND_DATE_YMD8, required=True),
        ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8),
        ColumnRule("corp_cls", "corp_cls_current", KIND_TEXT, expected_len=1),
        ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
        # 종목코드는 비상장·기타 공시에서 빈값 1,501,133행 → expected_len 금지, 존재 플래그 병기
        ColumnRule("stock_code", "ticker", KIND_TEXT, nonempty_flag="has_ticker"),
        ColumnRule("flr_nm", "flr_nm", KIND_TEXT, normalize_text=True),       # 공시 제출인
        ColumnRule("report_nm", "report_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("rm", "rm", KIND_TEXT),                                    # 코드 — 정규화 금지
    ),
    natural_key=("rcept_no",),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=AvailableRule("column", column="rcept_dt", basis="measured"),
    key_unique=False,        # append_only: 재수집 판본은 G6 축 (§7). S1b 실측 충돌 0
    extras=(
        ExtraColumn("is_correction", "s.\"report_nm\" LIKE '[%정정]%'"),
        *(ExtraColumn(name, f"contains(s.\"rm\", '{code}')") for name, code in _RM_CODES),
        ExtraColumn("rm_unknown",
                    "length(regexp_replace(s.\"rm\", '[유코채넥공연정철]', '', 'g')) > 0"),
    ),
)

# ── stg_company ← dart_company (3,478행) ───────────────────────────────────────────────────────
# 기업개황 = 재조회로 덮어써지는 단일 상태 → 가변 속성 전부 `_current` (§3 temporality ⓐ).
# 불변 사실만 접미사 없이 둔다: corp_code(키) · est_dt(설립일) · bizr_no · jurir_no · acc_mt(§4).
# rcept_no·rcept_dt 가 없어 available_date 비부여 (§4·§6).
# est_dt 는 YYYYMMDD 설립일(SPEC §2-14)이지만 날짜로 선언하면 빌더의 내용일 범위 [1990, 현재+40]
# 밖이라 전 행이 셀 격리된다(설립 연도는 대부분 1990년 이전) → 원문 8자리를 TEXT 로 보존한다.
_COMPANY_CURRENT = ("corp_name", "corp_name_eng", "stock_name", "ceo_nm", "adres", "hm_url",
                    "ir_url", "phn_no", "fax_no", "induty_code")
STG_COMPANY = TableRule(
    name="stg_company",
    sources=(SourceRef("dart", "dart_company", "company"),),
    columns=(
        ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8, key=True),
        ColumnRule("stock_code", "ticker_current", KIND_TEXT, expected_len=6),
        ColumnRule("corp_cls", "corp_cls_current", KIND_TEXT, expected_len=1),
        ColumnRule("acc_mt", "acc_mt", KIND_TEXT, expected_len=2),      # 결산월 (SPEC §2-15)
        ColumnRule("est_dt", "est_dt", KIND_TEXT, expected_len=8),
        ColumnRule("bizr_no", "bizr_no", KIND_TEXT),                    # 사업자등록번호 — 식별자
        ColumnRule("jurir_no", "jurir_no", KIND_TEXT),                  # 법인등록번호 — 식별자
        *(ColumnRule(c, c + "_current", KIND_TEXT, normalize_text=True)
          for c in _COMPANY_CURRENT),
    ),
    natural_key=("corp_code",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=_LEDGER_META,
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,        # append_only 재조회 판본은 G6 축 (§7). 3,478행 = 종목수
)

# ── stg_corp_map ← dart_corp_map (3,478행) ─────────────────────────────────────────────────────
# §1 예외 (d) 불변 참조표. 시각 컬럼 0 → observed_src None (observed_date NULL +
# `_meta.json` observed_date_exempt=true, §3). available_date 비부여 (§6).
STG_CORP_MAP = TableRule(
    name="stg_corp_map",
    sources=(SourceRef("dart", "dart_corp_map", "corp_map"),),
    columns=(
        ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8, key=True),
        ColumnRule("stock_code", "ticker", KIND_TEXT, expected_len=6),
        ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
    ),
    natural_key=("corp_code",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src=None,
    write_mode="append_only",
    fanout=1,
    payload_exclude=(),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=True,         # 참조표 계약 (§1 d) — corp_code 당 1행
)

# ── stg_doc_index ← doc_store (99,172행, ★수집 중) ─────────────────────────────────────────────
# ZIP 본문은 파일시스템에 있고 원장에는 메타만 있다. 시각 컬럼 실명 `fetched_at` (§3) —
# 같은 접수번호를 다시 받아도 내용이 같으면 payload 투영이 같아 접힌다(rcept_no 유일, §4).
STG_DOC_INDEX = TableRule(
    name="stg_doc_index",
    sources=(SourceRef("dart", "doc_store", "doc_store"),),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("bytes", "bytes", KIND_NUMERIC, *p_headroom(7)),
        ColumnRule("sha256", "sha256", KIND_TEXT),                 # 빈값 619행 = 수집 실패분
        ColumnRule("n_files", "n_files", KIND_NUMERIC, *p_headroom(1)),
        ColumnRule("zip_ok", "zip_ok", KIND_BOOL),
        ColumnRule("http_status", "http_status", KIND_NUMERIC, *p_headroom(3)),
    ),
    natural_key=("rcept_no",),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="fetched_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("fetched_at",),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,        # append_only: 재수집 판본은 G6 축 (§7). §4 rcept_no 유일
)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 수집 로그 2종 — 결측 3분류(equity)의 재료. whole · 시각 컬럼 실명 `ts` (§3·§4).
# 요청축 컬럼(corp_code·bsns_year·reprt_code·fs_div)은 빈값·NULL 이 25~80% 라 키로 쓰면
# 빌더가 key_missing/key_cast_failed 로 통째 reject 한다 → 항상 채워지는 축만 키로 둔다.
# `ts` 는 payload 에서 빼지 않는다: 빼면 다른 날의 같은 호출이 접혀 로그가 사라진다 (§1 1:1).
# ══════════════════════════════════════════════════════════════════════════════════════════════
STG_CALLS_DART = TableRule(
    name="stg_calls_dart",
    sources=(SourceRef("dart", "dart_call_log", "call_log"),),
    columns=(
        ColumnRule("endpoint", "endpoint", KIND_TEXT, key=True),
        ColumnRule("key_id", "key_id", KIND_TEXT, key=True),
        ColumnRule("corp_code", "corp_code", KIND_TEXT),      # 빈값 134,831행 — expected_len 금지
        ColumnRule("bsns_year", "bsns_year", KIND_TEXT),      # 4자리·8자리 혼재 → TEXT
        ColumnRule("reprt_code", "reprt_code", KIND_TEXT),    # 5자리·8자리 혼재 → TEXT
        ColumnRule("fs_div", "fs_div", KIND_TEXT),
        ColumnRule("status", "status", KIND_TEXT),            # 숫자 코드 + 문자열 3행 혼재
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(4)),
    ),
    natural_key=("endpoint", "key_id"),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="ts",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,        # 호출 로그에 자연키가 없다
    versioned=False,         # 판본 없음 → G6 skip(unversioned)
)

STG_UNITS_DART = TableRule(
    name="stg_units_dart",
    sources=(SourceRef("dart", "ingest_log", "ingest_log"),),
    columns=(
        ColumnRule("name", "name", KIND_TEXT, key=True),      # 적재 단위 이름
        ColumnRule("corp_code", "corp_code", KIND_TEXT),      # 빈값 67행 — expected_len 금지
        ColumnRule("bsns_year", "bsns_year", KIND_TEXT),
        ColumnRule("reprt_code", "reprt_code", KIND_TEXT),
        ColumnRule("fs_div", "fs_div", KIND_TEXT),
        ColumnRule("status", "status", KIND_TEXT),
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(5)),
        ColumnRule("note", "note", KIND_TEXT),                # 숫자 코드 + JSON 67행 혼재
    ),
    natural_key=("name",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="ts",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,
    versioned=False,         # 판본 없음 → G6 skip(unversioned)
)

TABLES: tuple[TableRule, ...] = (
    STG_RCEPT_DT_MAP, STG_FIN,
    STG_DIVIDEND, STG_SHARES, STG_CAPITAL, STG_TESSTK, STG_HYSLR, STG_AUDIT,
    STG_HOLDER_ELESTOCK, STG_HOLDER_MAJORSTOCK, STG_DISCLOSURE, STG_COMPANY, STG_CORP_MAP,
    STG_DOC_INDEX, STG_CALLS_DART, STG_UNITS_DART,
)
