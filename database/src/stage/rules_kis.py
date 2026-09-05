"""KIS stage 테이블 선언 (STAGE_DESIGN v2.2 §4 KIS). 코드가 아니라 목록이다.

원장 `kis.db` 7테이블 → stage 7테이블. 전부 `write_mode="append_only"`(row_hash PK, §3).
관측 시각은 데이터 5테이블이 `collected_at`, 로그 2테이블이 `ts`(§3 observed_src 표).

단위 접미사 규약(§5) — **원장 단위가 실측된 컬럼에만** 붙인다.
- `_shr`: 주식 수량. 카운트라 10의 거듭제곱 모호성이 없다.
- `_krw`: 원. SPEC §2-4 가 "원"이라고 실측한 컬럼(`kis_short_sale.*_pbmn` ·
  `kis_investor_flow.acml_tr_pbmn`)과 주가·액면가·대용가.
- `_krw` + `unit_scale=1_000_000`: SPEC §2-4 가 "백만원"이라고 실측한 컬럼
  (`kis_investor_flow.*_ntby_tr_pbmn` · `kis_loan_trans.rmnd_amt`). §9 가 G4 픽스처를 강제한다.
- 접미사 없음: 금액인데 원장 단위 미측정(`kis_credit_balance.*_amt` 6컬럼 · flow 의
  seln/shnu/askp/bidp 대금 · loan `prdy_vrss`). §5 "싣는다 + 기계 판독" 처방.
"""
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

_MEGA = 1_000_000
_META = ("row_hash", "dup_seq", "collected_at")   # req_* 는 빌더가 항상 payload 에서 뺀다


def _num(src: str, name: str, precision: int, scale: int = 0) -> ColumnRule:
    """survey v2 (p,s) + 여유 자릿수. precision 은 정수부+소수부 합이다(`ps_table.json`)."""
    return ColumnRule(src, name, KIND_NUMERIC, *p_headroom(precision, scale))


def _shr(src: str, precision: int) -> ColumnRule:
    """수량 컬럼 — 주(株) 카운트라 스케일 모호성이 없다."""
    return _num(src, f"{src}_shr", precision)


def _krw(src: str, precision: int, scale: int = 0) -> ColumnRule:
    """원 단위 실측 컬럼 (SPEC §2-4). 스케일 변환 없음."""
    return _num(src, f"{src}_krw", precision, scale)


def _pct(src: str, precision: int, scale: int = 2) -> ColumnRule:
    """비율 컬럼. 실측 범위가 0~100 을 넘는다(§5 whol_loan_gvrt −594.76~+1120.92)."""
    return _num(src, f"{src}_pct", precision, scale)


def _unit_unknown(src: str, precision: int, scale: int = 0) -> ColumnRule:
    """금액인데 원장 단위 미측정 — 접미사 금지, 원문 스케일 그대로 싣는다 (§4 KIS·§5)."""
    return _num(src, src, precision, scale)


def _mn(src: str, precision: int) -> ColumnRule:
    """백만원 → 원 ×1,000,000 (SPEC §2-4). unit_scale 선언 = G4 골든 픽스처 강제 (§9)."""
    return ColumnRule(src, f"{src}_krw", KIND_NUMERIC, *p_headroom(precision + 6),
                      unit_scale=_MEGA)


def _price(src: str, name: str, precision: int, scale: int = 0) -> ColumnRule:
    """KIS 주가 — `'0'` 은 값이 아니라 결측 표현이다 (SPEC §2-10 · §2-9 KRX 종가 0 = 0행)."""
    return ColumnRule(src, name, KIND_NUMERIC, *p_headroom(precision, scale),
                      zero_is_missing=True)


_TICKER = ColumnRule("req_ticker", "ticker", KIND_TEXT, expected_len=6, key=True)

# ── stg_flow_split_daily (kis_investor_flow 110컬럼 = 데이터 101 + 수집 메타 9) ────────────────
# 자연키 (req_ticker, stck_bsop_date) 중복 52,347 · 값 충돌 0 (SPEC §2-12) → payload 접기.
# 수정주가 포함 응답이라 재수집하면 같은 좌표의 값이 바뀐다 — 판본 축은 observed_date (§4).
_FLOW_COLUMNS: tuple[ColumnRule, ...] = (
    _TICKER,
    ColumnRule("stck_bsop_date", "date", KIND_DATE_YMD8, key=True),
    # §5 응답 의미를 바꾸는 요청 파라미터는 컬럼으로 싣고 payload 에 남긴다.
    # FID_ORG_ADJ_PRC = 수정주가/원주가 구분, FID_ETC_CLS_CODE = 기타 구분 (KIS FHPTJ04160001).
    ColumnRule("req_fid_org_adj_prc", "fid_org_adj_prc", KIND_TEXT),
    ColumnRule("req_fid_etc_cls_code", "fid_etc_cls_code", KIND_TEXT),
    _price("stck_clpr", "close_krw", 7),        # 원장 '' 1,835 + '0' 96 공존 (SPEC §2-10)
    _price("stck_oprc", "open_krw", 7),
    _price("stck_hgpr", "high_krw", 7),
    _price("stck_lwpr", "low_krw", 7),
    _unit_unknown("prdy_vrss", 10),             # 전일 대비. 자릿수가 가격보다 커 단위 미확정
    ColumnRule("prdy_vrss_sign", "prdy_vrss_sign", KIND_TEXT),   # 등락 구분 코드 — 수치 아님
    _pct("prdy_ctrt", 9),
    _shr("acml_vol", 11),                       # '0' 129,084 중 KRX 거래 인정 59건 = 진짜 0
    _krw("acml_tr_pbmn", 13),                   # SPEC §2-4: 원 (명세의 '백만원'은 오류)
    ColumnRule("bold_yn", "bold_yn", KIND_TEXT),
    _shr("bank_ntby_qty", 8),
    _mn("bank_ntby_tr_pbmn", 5),
    _unit_unknown("bank_seln_tr_pbmn", 5),
    _shr("bank_seln_vol", 8),
    _unit_unknown("bank_shnu_tr_pbmn", 5),
    _shr("bank_shnu_vol", 7),
    _mn("etc_corp_ntby_tr_pbmn", 6),
    _shr("etc_corp_ntby_vol", 9),
    _unit_unknown("etc_corp_seln_tr_pbmn", 6),
    _shr("etc_corp_seln_vol", 9),
    _unit_unknown("etc_corp_shnu_tr_pbmn", 6),
    _shr("etc_corp_shnu_vol", 9),
    _shr("etc_ntby_qty", 9),
    _mn("etc_ntby_tr_pbmn", 7),
    _mn("etc_orgt_ntby_tr_pbmn", 6),
    _shr("etc_orgt_ntby_vol", 8),
    _unit_unknown("etc_orgt_seln_tr_pbmn", 6),
    _shr("etc_orgt_seln_vol", 8),
    _unit_unknown("etc_orgt_shnu_tr_pbmn", 5),
    _shr("etc_orgt_shnu_vol", 7),
    _unit_unknown("etc_seln_tr_pbmn", 7),
    _shr("etc_seln_vol", 9),
    _unit_unknown("etc_shnu_tr_pbmn", 6),
    _shr("etc_shnu_vol", 9),
    _unit_unknown("frgn_nreg_askp_pbmn", 6),
    _shr("frgn_nreg_askp_qty", 8),
    _unit_unknown("frgn_nreg_bidp_pbmn", 4),
    _shr("frgn_nreg_bidp_qty", 7),
    _unit_unknown("frgn_nreg_ntby_pbmn", 6),    # `_ntby_pbmn` — SPEC §2-4 글롭 밖 (보고서 5)
    _shr("frgn_nreg_ntby_qty", 8),
    _shr("frgn_ntby_qty", 8),
    _mn("frgn_ntby_tr_pbmn", 6),
    _unit_unknown("frgn_reg_askp_pbmn", 6),
    _shr("frgn_reg_askp_qty", 8),
    _unit_unknown("frgn_reg_bidp_pbmn", 6),
    _shr("frgn_reg_bidp_qty", 8),
    _unit_unknown("frgn_reg_ntby_pbmn", 6),     # 위와 동일 — 글롭 밖
    _shr("frgn_reg_ntby_qty", 8),
    _unit_unknown("frgn_seln_tr_pbmn", 6),
    _shr("frgn_seln_vol", 8),
    _unit_unknown("frgn_shnu_tr_pbmn", 6),
    _shr("frgn_shnu_vol", 8),
    _shr("fund_ntby_qty", 7),
    _mn("fund_ntby_tr_pbmn", 5),
    _unit_unknown("fund_seln_tr_pbmn", 5),
    _shr("fund_seln_vol", 7),
    _unit_unknown("fund_shnu_tr_pbmn", 5),
    _shr("fund_shnu_vol", 7),
    _shr("insu_ntby_qty", 7),
    _mn("insu_ntby_tr_pbmn", 6),
    _unit_unknown("insu_seln_tr_pbmn", 6),
    _shr("insu_seln_vol", 7),
    _unit_unknown("insu_shnu_tr_pbmn", 6),
    _shr("insu_shnu_vol", 7),
    _shr("ivtr_ntby_qty", 8),
    _mn("ivtr_ntby_tr_pbmn", 6),
    _unit_unknown("ivtr_seln_tr_pbmn", 6),
    _shr("ivtr_seln_vol", 8),
    _unit_unknown("ivtr_shnu_tr_pbmn", 6),
    _shr("ivtr_shnu_vol", 8),
    _shr("mrbn_ntby_qty", 8),
    _mn("mrbn_ntby_tr_pbmn", 6),
    _unit_unknown("mrbn_seln_tr_pbmn", 6),
    _shr("mrbn_seln_vol", 8),
    _unit_unknown("mrbn_shnu_tr_pbmn", 6),
    _shr("mrbn_shnu_vol", 8),
    _shr("orgn_ntby_qty", 8),
    _mn("orgn_ntby_tr_pbmn", 6),
    _unit_unknown("orgn_seln_tr_pbmn", 6),
    _shr("orgn_seln_vol", 8),
    _unit_unknown("orgn_shnu_tr_pbmn", 6),
    _shr("orgn_shnu_vol", 8),
    _mn("pe_fund_ntby_tr_pbmn", 6),
    _shr("pe_fund_ntby_vol", 8),
    _unit_unknown("pe_fund_seln_tr_pbmn", 6),
    _shr("pe_fund_seln_vol", 8),
    _unit_unknown("pe_fund_shnu_tr_pbmn", 6),
    _shr("pe_fund_shnu_vol", 8),
    _shr("prsn_ntby_qty", 9),
    _mn("prsn_ntby_tr_pbmn", 6),
    _unit_unknown("prsn_seln_tr_pbmn", 7),
    _shr("prsn_seln_vol", 10),
    _unit_unknown("prsn_shnu_tr_pbmn", 7),
    _shr("prsn_shnu_vol", 10),
    _shr("scrt_ntby_qty", 8),
    _mn("scrt_ntby_tr_pbmn", 6),
    _unit_unknown("scrt_seln_tr_pbmn", 6),
    _shr("scrt_seln_vol", 8),
    _unit_unknown("scrt_shnu_tr_pbmn", 6),
    _shr("scrt_shnu_vol", 8),
)

STG_FLOW_SPLIT_DAILY = TableRule(
    name="stg_flow_split_daily",
    sources=(SourceRef("kis", "kis_investor_flow", "kis_investor_flow"),),
    columns=_FLOW_COLUMNS,
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(stck_bsop_date, 1, 4)",
    partition_src="stck_bsop_date",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(*_META, "req_d1", "req_d2", "req_name"),
    lag_known=False,                     # 수급 = 공표 시점 미상 (§6 주의)
    available=AvailableRule("column", column="date"),
    # 명시 투영 — 기본 규칙은 `req_*` 를 전부 빼지만 수정주가 플래그는 남아야 한다 (§5).
    payload_columns=tuple(c.src for c in _FLOW_COLUMNS),
    key_unique=False,                    # 중복 52,347 (접기 대상) — §4
)

# ── stg_short_daily_kis (kis_short_sale 28컬럼 = 데이터 21 + 수집 메타 7) ────────────────────
STG_SHORT_DAILY_KIS = TableRule(
    name="stg_short_daily_kis",
    sources=(SourceRef("kis", "kis_short_sale", "kis_short_sale"),),
    columns=(
        _TICKER,
        ColumnRule("stck_bsop_date", "date", KIND_DATE_YMD8, key=True),
        _price("stck_clpr", "close_krw", 7),
        _price("stck_oprc", "open_krw", 7),
        _price("stck_hgpr", "high_krw", 7),
        _price("stck_lwpr", "low_krw", 7),
        _unit_unknown("prdy_vrss", 10),
        ColumnRule("prdy_vrss_sign", "prdy_vrss_sign", KIND_TEXT),
        _pct("prdy_ctrt", 9),
        # 공매도 당일분. avrg_prc 0 = 632,369 행은 ssts_cntg_qty 0 과 행수가 정확히 같다
        # → 공매도가 없던 날의 "평균가 없음" 이지 0원이 아니다 (SPEC §2-10 테이블별 재판정).
        _price("avrg_prc", "avrg_prc_krw", 7),
        _shr("ssts_cntg_qty", 7),
        _krw("ssts_tr_pbmn", 12),                 # SPEC §2-4: kis_short_sale.*_pbmn = 원
        _pct("ssts_tr_pbmn_rlim", 5),
        _pct("ssts_vol_rlim", 6),
        _shr("stnd_vol_smtn", 11),
        _krw("stnd_tr_pbmn_smtn", 14),
        # 누적 6컬럼 — 요청 창 첫 행에서 리셋된다 (SPEC §2-6). 판독 플래그는 extras.acml_valid.
        _shr("acml_ssts_cntg_qty", 8),
        _pct("acml_ssts_cntg_qty_rlim", 4),
        _krw("acml_ssts_tr_pbmn", 13),
        _pct("acml_ssts_tr_pbmn_rlim", 4),
        _shr("acml_vol", 11),
        _krw("acml_tr_pbmn", 13),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(stck_bsop_date, 1, 4)",
    partition_src="stck_bsop_date",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(*_META, "req_d1", "req_d2", "req_name"),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=False,        # append_only: 재수집 판본은 G6 축 (§7)
    extras=(
        # 같은 행 비교만으로 창 경계를 판정한다 — LAG 금지(§1 1:1). req_d1 = 요청 창 시작일.
        # 창 첫 행(stck_bsop_date = req_d1)의 acml_* 는 직전 누적과 이어지지 않는다.
        # req_d1 은 payload 밖이지만 중복 0 이라 접기가 이 값을 흔들 수 없다.
        ExtraColumn("acml_valid", 's."stck_bsop_date" > s."req_d1"'),
    ),
    invariants=(
        # survey/targets.py INVARIANTS["kis_short_sale"]
        Invariant("acml_ssts_qty_neg", "acml_ssts_cntg_qty_shr < 0"),
    ),
)

# ── stg_loan_daily_kis (kis_loan_trans 19컬럼 = 데이터 12 + 수집 메타 7) ─────────────────────
STG_LOAN_DAILY_KIS = TableRule(
    name="stg_loan_daily_kis",
    sources=(SourceRef("kis", "kis_loan_trans", "kis_loan_trans"),),
    columns=(
        _TICKER,
        ColumnRule("bsop_date", "date", KIND_DATE_YMD8, key=True),
        # 원장 리터럴은 '0.00' 이다 (survey 패턴 '9.99' 281건). 빌더의 `= '0'` 비교는
        # 발화하지 않아 현재는 0.00 이 그대로 실린다 — 보고서 "빌더 필요 기능" 참조.
        _price("stck_prpr", "close_krw", 9, 2),
        _unit_unknown("prdy_vrss", 11, 2),
        ColumnRule("prdy_vrss_sign", "prdy_vrss_sign", KIND_TEXT),
        _pct("prdy_ctrt", 7),
        _shr("acml_vol", 10),
        _shr("new_stcn", 9),
        _shr("rdmp_stcn", 8),
        _shr("rmnd_stcn", 9),            # 음수 2,691 keep — abs 금지 (SPEC §2-3)
        _unit_unknown("prdy_rmnd_vrss", 9),
        _mn("rmnd_amt", 7),              # SPEC §2-4: 백만원 → ×1,000,000
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(bsop_date, 1, 4)",
    partition_src="bsop_date",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(*_META, "req_d1", "req_d2", "req_name", "req_mrkt_div_cls_code"),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=False,        # append_only: 재수집 판본은 G6 축 (§7)
)

# ── stg_credit_daily (kis_credit_balance 33컬럼 = 데이터 26 + 수집 메타 7) ───────────────────
# 중복 566,795 = req_d2 만 상이한 순수 재수집. req_* 가 payload 밖이라 26컬럼 투영에서
# 전건 접힌다 (§5 실측: 동일 그룹 517,648/517,648, 접기 후 잔여 0).
STG_CREDIT_DAILY = TableRule(
    name="stg_credit_daily",
    sources=(SourceRef("kis", "kis_credit_balance", "kis_credit_balance"),),
    columns=(
        _TICKER,
        ColumnRule("deal_date", "date", KIND_DATE_YMD8, key=True),
        # 결제일(매매일+2~12일)이지 공개일이 아니다 — available_date 는 deal_date (§6).
        ColumnRule("stlm_date", "stlm_date", KIND_DATE_YMD8),
        # stck_prpr = 수정종가(조회 시점 의존), OHL = KRX 원주가 완전 일치 (§4).
        _price("stck_prpr", "close_krw", 8),      # '0' 561행 = KIS 결측 표현 (SPEC §2-10)
        _price("stck_oprc", "open_krw", 7),       # '0' 465행 — KRX O/H/L 규칙과 동렬 (§2-9)
        _price("stck_hgpr", "high_krw", 7),
        _price("stck_lwpr", "low_krw", 7),
        _unit_unknown("prdy_vrss", 10),
        ColumnRule("prdy_vrss_sign", "prdy_vrss_sign", KIND_TEXT),
        _pct("prdy_ctrt", 9),            # 비숫자 465행('-' + 3영문자) → cast_failed (보고서 4)
        _shr("acml_vol", 10),
        _pct("whol_loan_gvrt", 6),       # 전수 범위 −594.76~+1120.92 (§5)
        _unit_unknown("whol_loan_new_amt", 9),
        _shr("whol_loan_new_stcn", 8),
        _unit_unknown("whol_loan_rdmp_amt", 9),
        _shr("whol_loan_rdmp_stcn", 8),
        _unit_unknown("whol_loan_rmnd_amt", 9),
        _pct("whol_loan_rmnd_rate", 4),
        _shr("whol_loan_rmnd_stcn", 8),
        _pct("whol_stln_gvrt", 5),
        _unit_unknown("whol_stln_new_amt", 7),
        _shr("whol_stln_new_stcn", 8),
        _unit_unknown("whol_stln_rdmp_amt", 7),
        _shr("whol_stln_rdmp_stcn", 6),
        _unit_unknown("whol_stln_rmnd_amt", 7),
        _pct("whol_stln_rmnd_rate", 3),
        _shr("whol_stln_rmnd_stcn", 7),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(deal_date, 1, 4)",
    partition_src="deal_date",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(*_META, "req_d1", "req_d2", "req_name"),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=False,                    # 수정종가 재수집 판본이 생기면 접히지 않는다 (보고서 5)
    extras=(
        # 가격 기준은 **컬럼 단위** 라벨이다 — 행 단위 금지 (§1 · §4 v2 정정).
        ExtraColumn("price_basis_close", "'adjusted_asof_collect'"),
        ExtraColumn("price_basis_ohl", "'raw'"),
    ),
    invariants=(
        # survey/targets.py INVARIANTS["kis_credit_balance"] 2건
        Invariant("whol_loan_rmnd_stcn_neg", "whol_loan_rmnd_stcn_shr < 0"),
        Invariant("stlm_before_deal", "stlm_date < date"),
    ),
)

# ── stg_delisted_master (kis_stock_info 74컬럼 = 데이터 67 + 수집 메타 7, 652행) ──────────────
# 652행 전건 2026-08-26 단일 조회. 폐지 종목은 동결값·생존 종목은 현재값이 섞여 있다 (§4)
# → 재조회하면 덮이는 속성에는 `_current` 를 강제한다 (§3 temporality ⓐ).
# `_current` 미부여 = 발생 시점이 고정된 사실: 상장·폐지·설정·해지일, 발행가, 식별자.
_STATE = "_current"
# 날짜 컬럼 전부 zero_is_missing — KIS 결측 리터럴 '00000000' (K1, survey 168셀)
STG_DELISTED_MASTER = TableRule(
    name="stg_delisted_master",
    sources=(SourceRef("kis", "kis_stock_info", "kis_stock_info"),),
    columns=(
        _TICKER,
        # 식별자 — §5 "KIS pdno 뒤 6자". 원문을 싣고 파생은 extras.ticker_pdno 로 병기한다.
        ColumnRule("pdno", "pdno", KIND_TEXT, expected_len=12),
        ColumnRule("std_pdno", "std_pdno", KIND_TEXT),          # ISIN 12자
        # 상태 플래그 (Y/N)
        ColumnRule("admn_item_yn", f"admn_item{_STATE}", KIND_TEXT),
        ColumnRule("tr_stop_yn", f"tr_stop{_STATE}", KIND_TEXT),
        ColumnRule("nxt_tr_stop_yn", f"nxt_tr_stop{_STATE}", KIND_TEXT),
        ColumnRule("kospi200_item_yn", f"kospi200_item{_STATE}", KIND_TEXT),
        ColumnRule("crfd_item_yn", f"crfd_item{_STATE}", KIND_TEXT),
        ColumnRule("cptt_trad_tr_psbl_yn", f"cptt_trad_tr_psbl{_STATE}", KIND_TEXT),
        ColumnRule("dpsi_aptm_erlm_yn", f"dpsi_aptm_erlm{_STATE}", KIND_TEXT),
        ColumnRule("elec_scty_yn", f"elec_scty{_STATE}", KIND_TEXT),
        ColumnRule("etf_etn_ivst_heed_item_yn", f"etf_etn_ivst_heed_item{_STATE}", KIND_TEXT),
        ColumnRule("oilf_fund_yn", f"oilf_fund{_STATE}", KIND_TEXT),
        # 분류 코드 — 코드라 normalize_text 금지 (§5)
        ColumnRule("mket_id_cd", f"mket_id_cd{_STATE}", KIND_TEXT),
        ColumnRule("scty_grp_id_cd", f"scty_grp_id_cd{_STATE}", KIND_TEXT),
        ColumnRule("excg_dvsn_cd", f"excg_dvsn_cd{_STATE}", KIND_TEXT),
        ColumnRule("prdt_type_cd", f"prdt_type_cd{_STATE}", KIND_TEXT),
        ColumnRule("stck_kind_cd", f"stck_kind_cd{_STATE}", KIND_TEXT),
        ColumnRule("nwst_odst_dvsn_cd", f"nwst_odst_dvsn_cd{_STATE}", KIND_TEXT),
        ColumnRule("reits_kind_cd", f"reits_kind_cd{_STATE}", KIND_TEXT),
        ColumnRule("stln_int_rt_dvsn_cd", f"stln_int_rt_dvsn_cd{_STATE}", KIND_TEXT),
        ColumnRule("issu_istt_cd", f"issu_istt_cd{_STATE}", KIND_TEXT),
        ColumnRule("lstg_rqsr_issu_istt_cd", f"lstg_rqsr_issu_istt_cd{_STATE}", KIND_TEXT),
        ColumnRule("lstg_rqsr_item_cd", f"lstg_rqsr_item_cd{_STATE}", KIND_TEXT),
        ColumnRule("trst_istt_issu_istt_cd", f"trst_istt_issu_istt_cd{_STATE}", KIND_TEXT),
        ColumnRule("etf_dvsn_cd", f"etf_dvsn_cd{_STATE}", KIND_TEXT),
        ColumnRule("etf_txtn_type_cd", f"etf_txtn_type_cd{_STATE}", KIND_TEXT),
        ColumnRule("etf_type_cd", f"etf_type_cd{_STATE}", KIND_TEXT),
        ColumnRule("ocr_no", f"ocr_no{_STATE}", KIND_TEXT),
        ColumnRule("idx_bztp_lcls_cd", f"idx_bztp_lcls_cd{_STATE}", KIND_TEXT),
        ColumnRule("idx_bztp_mcls_cd", f"idx_bztp_mcls_cd{_STATE}", KIND_TEXT),
        ColumnRule("idx_bztp_scls_cd", f"idx_bztp_scls_cd{_STATE}", KIND_TEXT),
        ColumnRule("std_idst_clsf_cd", f"std_idst_clsf_cd{_STATE}", KIND_TEXT),
        # 2자리(월)·4자리(월일) 혼재 실측 — 날짜 파싱 금지, 원문 보존
        ColumnRule("setl_mmdd", f"setl_mmdd{_STATE}", KIND_TEXT),
        # 이름 — §5 문자열 정규화 대상
        ColumnRule("prdt_name", f"prdt_name{_STATE}", KIND_TEXT, normalize_text=True),
        ColumnRule("prdt_name120", f"prdt_name120{_STATE}", KIND_TEXT, normalize_text=True),
        ColumnRule("prdt_abrv_name", f"prdt_abrv_name{_STATE}", KIND_TEXT, normalize_text=True),
        ColumnRule("prdt_eng_name", f"prdt_eng_name{_STATE}", KIND_TEXT, normalize_text=True),
        ColumnRule("prdt_eng_name120", f"prdt_eng_name120{_STATE}", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("prdt_eng_abrv_name", f"prdt_eng_abrv_name{_STATE}", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("idx_bztp_lcls_cd_name", f"idx_bztp_lcls_name{_STATE}", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("idx_bztp_mcls_cd_name", f"idx_bztp_mcls_name{_STATE}", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("idx_bztp_scls_cd_name", f"idx_bztp_scls_name{_STATE}", KIND_TEXT,
                   normalize_text=True),
        ColumnRule("std_idst_clsf_cd_name", f"std_idst_clsf_name{_STATE}", KIND_TEXT,
                   normalize_text=True),
        # 시세·대용가 스냅샷 — 재조회로 덮인다. '0' 은 가격이 아니다 (SPEC §2-10)
        _price("bfdy_clpr", f"bfdy_clpr{_STATE}_krw", 5),
        _price("thdt_clpr", f"thdt_clpr{_STATE}_krw", 7),
        _price("sbst_pric", f"sbst_pric{_STATE}_krw", 1),
        _price("thco_sbst_pric", f"thco_sbst_pric{_STATE}_krw", 6),
        ColumnRule("clpr_chng_dt", f"clpr_chng_dt{_STATE}", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("thco_sbst_pric_chng_dt", f"thco_sbst_pric_chng_dt{_STATE}", KIND_DATE_YMD8,
                   zero_is_missing=True),
        # 규모 — 기업행위로 바뀌고 재조회로 덮인다
        _num("cpta", f"cpta{_STATE}_krw", 13),
        _num("lstg_cptl_amt", f"lstg_cptl_amt{_STATE}_krw", 13),
        _num("lstg_stqt", f"lstg_stqt{_STATE}_shr", 9),
        _num("papr", f"par_value{_STATE}_krw", 8),
        _num("frnr_psnl_lmt_rt", f"frnr_psnl_lmt_rt{_STATE}", 9, 8),
        _num("etf_cu_qty", f"etf_cu_qty{_STATE}_shr", 1),
        _num("etf_chas_erng_rt_dbnb", f"etf_chas_erng_rt_dbnb{_STATE}", 1),
        # 발행 시점 고정 사실
        _num("issu_pric", "issu_pric_krw", 6),
        # 사건 날짜 — `_current` 미부여
        ColumnRule("scts_mket_lstg_dt", "scts_mket_lstg_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("scts_mket_lstg_abol_dt", "scts_mket_lstg_abol_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("kosdaq_mket_lstg_dt", "kosdaq_mket_lstg_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("kosdaq_mket_lstg_abol_dt", "kosdaq_mket_lstg_abol_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("frbd_mket_lstg_dt", "frbd_mket_lstg_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("frbd_mket_lstg_abol_dt", "frbd_mket_lstg_abol_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("lstg_abol_dt", "lstg_abol_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("mfnd_opng_dt", "mfnd_opng_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("mfnd_end_dt", "mfnd_end_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
        ColumnRule("dpsi_erlm_cncl_dt", "dpsi_erlm_cncl_dt", KIND_DATE_YMD8,
                   zero_is_missing=True),
    ),
    natural_key=("ticker",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=(*_META, "req_d1", "req_d2", "req_name"),
    lag_known=False,
    # §6 의 4갈래(내용일 대용 · 결제일 · 참조표 게시일 · 수집일 실재) 중 어디에도 없다.
    # 행이 기술하는 날이 없어 내용일 대용이 불가능하고 원장에 공개일 사실 컬럼도 없다.
    # 같은 구조(종목당 1행 현재 상태)인 stg_company·stg_wise_coverage 와 동렬로 비부여한다.
    # "언제 알았나"는 observed_date(collected_at KST)가 담는다. §6 표 누락은 보고서 5.
    available=AVAILABLE_NONE,
    key_unique=False,                     # 종목당 1콜 1행 (targets.py 자연키 후보 req_ticker)
    extras=(
        ExtraColumn("ticker_pdno", 'right(s."pdno", 6)'),
    ),
)

# ── stg_calls_kis / stg_units_kis (수집 로그 2종 — 3분류 재료, §4) ────────────────────────────
# 원장에 행 식별자(row_hash·id)가 없고 판본 개념도 없다(`versioned=False` → G6 skip). 키는 항상
# 채워지는 데이터 컬럼만 — d1·d2 는 kis_stock_info 조회 652행에서 빈값이라 비키(ledger_blank)로
# 보존한다(K2). 같은 날 같은 내용의 재호출은 payload 접기로 observed_n 에 합산된다.
_LOG_KEYS = ("dataset", "ticker")
_LOG_HEAD: tuple[ColumnRule, ...] = (
    ColumnRule("name", "dataset", KIND_TEXT, key=True),
    ColumnRule("ticker", "ticker", KIND_TEXT, expected_len=6, key=True),
    ColumnRule("d1", "window_from", KIND_DATE_YMD8),
    ColumnRule("d2", "window_to", KIND_DATE_YMD8),
)

STG_CALLS_KIS = TableRule(
    name="stg_calls_kis",
    sources=(SourceRef("kis", "kis_call_log", "kis_call_log"),),
    columns=(
        *_LOG_HEAD,
        ColumnRule("verdict", "verdict", KIND_TEXT, key=True),
        ColumnRule("code", "code", KIND_TEXT, key=True),
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(2), key=True),
    ),
    natural_key=(*_LOG_KEYS, "verdict", "code", "n_rows"),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="ts",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("ts",),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,
    versioned=False,                     # 콜 로그 — 같은 키가 하루 여러 번, G6 skip
    invariants=(
        # SPEC §6 회귀 고정: 판정 ok 350,943 · empty 6,739 · 오류 0, n_rows='0' 6,739
        Invariant("verdict_vocab", "verdict NOT IN ('ok', 'empty')"),
        Invariant("empty_verdict_rows", "(verdict = 'empty') <> (n_rows = 0)"),
    ),
)

STG_UNITS_KIS = TableRule(
    name="stg_units_kis",
    sources=(SourceRef("kis", "kis_ingest_log", "kis_ingest_log"),),
    columns=(
        *_LOG_HEAD,
        ColumnRule("status", "status", KIND_TEXT, key=True),
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(2), key=True),
    ),
    natural_key=(*_LOG_KEYS, "status", "n_rows"),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="ts",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("ts",),
    lag_known=False,
    available=AVAILABLE_NONE,
    key_unique=False,
    versioned=False,                     # 유닛 로그 — G6 skip
    invariants=(
        # survey v2 실측: status 2어휘(2자 350,941 · 5자 4,086), n_rows='0' 4,086
        Invariant("status_vocab", "status NOT IN ('ok', 'empty')"),
        Invariant("empty_status_rows", "(status = 'empty') <> (n_rows = 0)"),
    ),
)

TABLES: tuple[TableRule, ...] = (STG_FLOW_SPLIT_DAILY, STG_SHORT_DAILY_KIS, STG_LOAN_DAILY_KIS,
                                 STG_CREDIT_DAILY, STG_DELISTED_MASTER, STG_CALLS_KIS,
                                 STG_UNITS_KIS)
