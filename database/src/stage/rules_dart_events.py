"""DART DS005 주요사항보고서 이벤트 15테이블 선언 (STAGE_DESIGN v2.2 §4 DART · SPEC §2-14·§2-19).

코드가 아니라 목록이다. 15테이블은 골격이 같다 — 키 `rcept_no` 단독(중복 0 전수 실측,
`docs/archive/dart_census_DS005.md`), 파티션 receipt_axis, `observed_src=collected_at`,
write_mode append_only, available_date 는 `stg_rcept_dt_map` 룩업(내용 테이블에 `rcept_dt`
컬럼이 없다 — §6. 미스는 NULL + basis `unknown`).

컬럼 종류·(p,s)·날짜 포맷은 전부 `survey_out/v2/dart.<원장>.json` 전수 측정치다:
- 숫자 컬럼 `n_nonnum` 이 15테이블 전건 0 → 예상 cast_failed 0 (G2)
- 날짜 컬럼은 (단일 포맷 | `'-'` | `''` | NULL) 로 전수 분해되고 컬럼 내 포맷 혼재는 0.
  KIND 는 컬럼마다 `n_kor`/`n_ymd8` 로 고른다 — DESIGN §4 의 "한글 날짜 단일 포맷(정규 날짜컬럼
  119개)"은 실측과 다르다: 한글 118 + `piic`·`pifric` 의 `ssl_bgd`·`ssl_edd` 4컬럼 YYYYMMDD(387행).
- `bnk_mngt_pcbg.mngt_pd` 는 기간표기(`'… ~ …'`)라 날짜가 아니라 KIND_TEXT (§4)
- `'-'` 결측 마커가 전 테이블에 실재한다(`cvbd_is.pymd` 229행 등) — 빌더가 ledger_dash 로 분류
- 전 행이 `'-'` 인 13컬럼(survey kind=empty — `cmp_dvmg` 의 `ffdtl_*`·`nmgcmp_*`·`dvfcmp_*` 11 ·
  `cmp_mg.nmgcmp_nbsn_rsl` · `stk_extr.popt_ctr_cn`)은 측정된 (p,s)가 없어 KIND_TEXT 로 싣는다.
  같은 이름이 다른 테이블에선 숫자다(`cmp_mg.ffdtl_cpt`) — 테이블별 실측을 따른 결과다

단위 접미사는 **비율 컬럼에만** 붙인다(`_pct` · `_ratio`). 금액·주식수 컬럼은 원장 단위를
측정한 근거가 없어 접미사를 달지 않는다(§5 "단위를 모르면 접미사 금지"). `_pct` 근거는
survey `max_int_digits` ≥ 2 — 0~1 분수라면 정수부가 1자리여야 한다 — 와 census 실측치
(`cr_rt_ostk` 67.18·68.37 · `cvisstk_tisstk_vs` "(%)"). `unit_scale` 선언은 없다.
"""
from __future__ import annotations

from .model import (
    KIND_DATE_KOREAN,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    Invariant,
    SourceRef,
    TableRule,
    p_headroom,
)

# 공통 골격 — 원장 32테이블 중 rcept_dt 를 가진 5개에 DS005 는 없다(§4) → 참조표 유도
_AVAILABLE = AvailableRule("lookup", table="stg_rcept_dt_map", local_key="rcept_no",
                           lookup_key="rcept_no", lookup_value="rcept_dt")
_PAYLOAD_EXCLUDE = ("row_hash", "dup_seq", "collected_at")   # req_* 는 빌더가 항상 제외
# 선두 4컬럼은 DS005 36엔드포인트 공통(census). corp_cls·corp_name 은 수집 시점 값이라
# `_current` 강제 — §3 temporality ⓐ(corp_cls='E' 의 26%가 과거 상장사)와 stg_disclosure 선례.
_HEAD: tuple[ColumnRule, ...] = (
    ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
    ColumnRule("corp_code", "corp_code", KIND_TEXT, expected_len=8),
    ColumnRule("corp_cls", "corp_cls_current", KIND_TEXT),
    ColumnRule("corp_name", "corp_name_current", KIND_TEXT, normalize_text=True),
)


def _num(src: str, precision: int, scale: int, suffix: str = "") -> ColumnRule:
    """숫자 컬럼 — (precision, scale) 은 survey v2 전수 측정치, 여유는 p_headroom 이 더한다."""
    return ColumnRule(src, src + suffix, KIND_NUMERIC, *p_headroom(precision, scale))


def _txt(src: str) -> ColumnRule:
    """텍스트 컬럼 — 원문 보존.

    정규화를 붙이지 않는다: 빌더의 태그 제거(`<[^>]+>`)가 DART 서술 컬럼의 `<주1>` 류
    각주 표기를 지울 수 있고, 서버 접근이 없어 실재 여부를 측정할 수 없다.
    """
    return ColumnRule(src, src, KIND_TEXT)


def _kdate(src: str) -> ColumnRule:
    """`YYYY년 MM월 DD일` (survey n_kor)."""
    return ColumnRule(src, src, KIND_DATE_KOREAN)


def _ymd8(src: str) -> ColumnRule:
    """`YYYYMMDD` (survey n_ymd8) — piic·pifric 의 공매도 기간 컬럼."""
    return ColumnRule(src, src, KIND_DATE_YMD8)


def _event(name: str, table: str, tag: str, columns: tuple[ColumnRule, ...],
           invariants: tuple[Invariant, ...] = ()) -> TableRule:
    """DS005 공통 골격. 테이블마다 다른 것은 이름·원장·컬럼·불변식뿐이다."""
    return TableRule(
        name=name,
        sources=(SourceRef("dart", table, tag),),
        columns=_HEAD + columns,
        natural_key=("rcept_no",),
        partition_class="receipt_axis",
        partition_expr="substr(rcept_no, 1, 4)",
        partition_src="rcept_no",
        observed_src="collected_at",
        write_mode="append_only",
        fanout=1,
        payload_exclude=_PAYLOAD_EXCLUDE,
        lag_known=False,             # 공개일은 참조표 유도(derived) — 랙 판단은 엔진 (§6)
        available=_AVAILABLE,
        key_unique=False,            # append_only: 재수집 판본은 G6 의 (키, observed_date) 축 (§7)
        invariants=invariants,
    )


# stg_event_tsstk_aq — dart_tsstk_aq_decsn · survey 1951행 · 컬럼 29
STG_EVENT_TSSTK_AQ = _event("stg_event_tsstk_aq", "dart_tsstk_aq_decsn", "tsstk_aq", (
    _txt("adt_a_atn"),
    _kdate("aq_dd"),
    _txt("aq_mth"),
    _txt("aq_pp"),
    _num("aq_wtn_div_estk", 8, 0),
    _num("aq_wtn_div_estk_rt", 4, 2, "_pct"),
    _num("aq_wtn_div_ostk", 9, 0),
    _num("aq_wtn_div_ostk_rt", 10, 7, "_pct"),
    _kdate("aqexpd_bgd"),
    _kdate("aqexpd_edd"),
    _num("aqpln_prc_estk", 13, 0),
    _num("aqpln_prc_ostk", 14, 0),
    _num("aqpln_stk_estk", 8, 0),
    _num("aqpln_stk_ostk", 8, 0),
    _txt("cs_iv_bk"),
    _num("d1_prodlm_estk", 6, 0),
    _num("d1_prodlm_ostk", 7, 0),
    _num("eaq_estk", 7, 0),
    _num("eaq_estk_rt", 4, 2, "_pct"),
    _num("eaq_ostk", 9, 0),
    _num("eaq_ostk_rt", 7, 5, "_pct"),
    _kdate("hdexpd_bgd"),
    _kdate("hdexpd_edd"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 2, 0),
))

# stg_event_piic — dart_piic_decsn · survey 5538행 · 컬럼 19
STG_EVENT_PIIC = _event("stg_event_piic", "dart_piic_decsn", "piic", (
    _num("bfic_tisstk_estk", 8, 0),
    _num("bfic_tisstk_ostk", 11, 0),
    _num("fdpp_bsninh", 12, 0),
    _num("fdpp_dtrp", 13, 0),
    _num("fdpp_etc", 13, 0),
    _num("fdpp_fclt", 14, 0),
    _num("fdpp_ocsa", 13, 0),
    _num("fdpp_op", 13, 0),
    _num("fv_ps", 8, 0),
    _txt("ic_mthn"),
    _num("nstk_estk_cnt", 9, 0),
    _num("nstk_ostk_cnt", 10, 0),
    _txt("ssl_at"),
    _ymd8("ssl_bgd"),
    _ymd8("ssl_edd"),
))

# stg_event_cvbd_is — dart_cvbd_is_decsn · survey 5386행 · 컬럼 46
STG_EVENT_CVBD_IS = _event("stg_event_cvbd_is", "dart_cvbd_is_decsn", "cvbd_is", (
    _txt("abmg"),
    _num("act_mktprcfl_cvprc_lwtrsprc", 6, 0),
    _txt("act_mktprcfl_cvprc_lwtrsprc_bs"),
    _txt("adt_a_atn"),
    _num("atcsc_rmislmt", 15, 0),
    _num("bd_fta", 13, 0),
    _num("bd_intr_ex", 5, 3, "_pct"),
    _num("bd_intr_sf", 5, 3, "_pct"),
    _txt("bd_knd"),
    _kdate("bd_mtd"),
    _txt("bd_tm"),
    _kdate("bddd"),
    _txt("bdis_mthn"),
    _num("cv_prc", 6, 0),
    _num("cv_rt", 5, 2, "_pct"),
    _num("cvisstk_cnt", 9, 0),
    _txt("cvisstk_knd"),
    _num("cvisstk_tisstk_vs", 7, 4, "_pct"),
    _kdate("cvrqpd_bgd"),
    _kdate("cvrqpd_edd"),
    _txt("ex_sm_r"),
    _num("fdpp_bsninh", 12, 0),
    _num("fdpp_dtrp", 12, 0),
    _num("fdpp_etc", 13, 0),
    _num("fdpp_fclt", 12, 0),
    _num("fdpp_ocsa", 12, 0),
    _num("fdpp_op", 13, 0),
    _txt("ftc_stt_atn"),
    _txt("grint"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
    _num("ovis_fta", 11, 0),
    _txt("ovis_fta_crn"),
    _txt("ovis_isar"),
    _txt("ovis_ltdtl"),
    _txt("ovis_mktnm"),
    _num("ovis_ster", 4, 0),
    _kdate("pymd"),
    _num("rmislmt_lt70p", 13, 0),
    _txt("rpmcmp"),
    _txt("rs_sm_atn"),
    _kdate("sbd"),
))

# stg_event_fric — dart_fric_decsn · survey 758행 · 컬럼 19
STG_EVENT_FRIC = _event("stg_event_fric", "dart_fric_decsn", "fric", (
    _txt("adt_a_atn"),
    _kdate("bddd"),
    _num("bfic_tisstk_estk", 8, 0),
    _num("bfic_tisstk_ostk", 9, 0),
    _num("fv_ps", 7, 0),
    _num("nstk_ascnt_ps_estk", 3, 2, "_ratio"),
    _num("nstk_ascnt_ps_ostk", 12, 10, "_ratio"),
    _kdate("nstk_asstd"),
    _kdate("nstk_dividrk"),
    _kdate("nstk_dlprd"),
    _num("nstk_estk_cnt", 8, 0),
    _kdate("nstk_lstprd"),
    _num("nstk_ostk_cnt", 9, 0),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
))

# stg_event_pifric — dart_pifric_decsn · survey 106행 · 컬럼 34
STG_EVENT_PIFRIC = _event("stg_event_pifric", "dart_pifric_decsn", "pifric", (
    _txt("fric_adt_a_atn"),
    _kdate("fric_bddd"),
    _num("fric_bfic_tisstk_estk", 7, 0),
    _num("fric_bfic_tisstk_ostk", 9, 0),
    _num("fric_fv_ps", 4, 0),
    _num("fric_nstk_ascnt_ps_estk", 9, 8, "_ratio"),
    _num("fric_nstk_ascnt_ps_ostk", 11, 10, "_ratio"),
    _kdate("fric_nstk_asstd"),
    _kdate("fric_nstk_dividrk"),
    _kdate("fric_nstk_dlprd"),
    _num("fric_nstk_estk_cnt", 6, 0),
    _kdate("fric_nstk_lstprd"),
    _num("fric_nstk_ostk_cnt", 8, 0),
    _num("fric_od_a_at_b", 1, 0),
    _num("fric_od_a_at_t", 1, 0),
    _num("piic_bfic_tisstk_estk", 7, 0),
    _num("piic_bfic_tisstk_ostk", 8, 0),
    _num("piic_fdpp_bsninh", 11, 0),
    _num("piic_fdpp_dtrp", 12, 0),
    _num("piic_fdpp_etc", 11, 0),
    _num("piic_fdpp_fclt", 12, 0),
    _num("piic_fdpp_ocsa", 12, 0),
    _num("piic_fdpp_op", 12, 0),
    _num("piic_fv_ps", 4, 0),
    _txt("piic_ic_mthn"),
    _num("piic_nstk_estk_cnt", 6, 0),
    _num("piic_nstk_ostk_cnt", 8, 0),
    _txt("ssl_at"),
    _ymd8("ssl_bgd"),
    _ymd8("ssl_edd"),
))

# stg_event_cr — dart_cr_decsn · survey 720행 · 컬럼 36
STG_EVENT_CR = _event("stg_event_cr", "dart_cr_decsn", "cr", (
    _txt("adt_a_atn"),
    _num("atcr_cpt", 12, 0),
    _num("atcr_tisstk_estk", 8, 0),
    _num("atcr_tisstk_ostk", 9, 0),
    _kdate("bddd"),
    _num("bfcr_cpt", 13, 0),
    _num("bfcr_tisstk_estk", 8, 0),
    _num("bfcr_tisstk_ostk", 10, 0),
    _kdate("cdobprpd_bgd"),
    _kdate("cdobprpd_edd"),
    _txt("cr_mth"),
    _txt("cr_rs"),
    _num("cr_rt_estk", 6, 3, "_pct"),
    _num("cr_rt_ostk", 13, 10, "_pct"),
    _kdate("cr_std"),
    _kdate("crsc_gmtsck_prd"),
    _kdate("crsc_nstkdlprd"),
    _kdate("crsc_nstklstprd"),
    _txt("crsc_osprpd"),
    _kdate("crsc_osprpd_bgd"),
    _kdate("crsc_osprpd_edd"),
    _txt("crsc_trnmsppd"),
    _txt("crsc_trspprpd"),
    _kdate("crsc_trspprpd_bgd"),
    _kdate("crsc_trspprpd_edd"),
    _num("crstk_estk_cnt", 8, 0),
    _num("crstk_ostk_cnt", 10, 0),
    _txt("ftc_stt_atn"),
    _num("fv_ps", 4, 0),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
    _txt("ospr_nstkdl_pl"),
), invariants=(
    # survey/targets.py INVARIANTS["dart_cr_decsn"] "감자 후>전" 을 stage 이름으로 이식.
    # 결측('-'·'')은 stage 에서 NULL 이라 비교도 NULL → 위반에서 빠진다(원 SQL 의 NOT IN 과 동치).
    Invariant("cr_shares_increase", "atcr_tisstk_ostk > bfcr_tisstk_ostk"),
))

# stg_event_cmp_mg — dart_cmp_mg_decsn · survey 1395행 · 컬럼 69
STG_EVENT_CMP_MG = _event("stg_event_cmp_mg", "dart_cmp_mg_decsn", "cmp_mg", (
    _txt("adt_a_atn"),
    _txt("aprskh_ctref"),
    _num("aprskh_plnprc", 8, 0),
    _txt("aprskh_pym_plpd_mth"),
    _kdate("bddd"),
    _txt("bdlst_atn"),
    _txt("eadtat_intn"),
    _txt("eadtat_op"),
    _txt("ex_sm_r"),
    _txt("exevl_atn"),
    _txt("exevl_bs_rs"),
    _txt("exevl_intn"),
    _txt("exevl_pd"),
    _num("ffdtl_cpt", 10, 0),
    _kdate("ffdtl_std"),
    _num("ffdtl_tast", 11, 0),
    _num("ffdtl_tdbt", 11, 0),
    _num("ffdtl_teqt", 11, 0),
    _txt("mg_mth"),
    _txt("mg_pp"),
    _txt("mg_rt"),
    _txt("mg_rt_bs"),
    _txt("mg_stn"),
    _num("mgnstk_cstk_cnt", 8, 0),
    _num("mgnstk_ostk_cnt", 9, 0),
    _txt("mgptncmp_cmpnm"),
    _txt("mgptncmp_mbsn"),
    _txt("mgptncmp_rl_cmpn"),
    _kdate("mgsc_aprskh_expd_bgd"),
    _kdate("mgsc_aprskh_expd_edd"),
    _kdate("mgsc_cdobprpd_bgd"),
    _kdate("mgsc_cdobprpd_edd"),
    _kdate("mgsc_ergmd"),
    _kdate("mgsc_gmtsck_prd"),
    _kdate("mgsc_mgctrd"),
    _kdate("mgsc_mgdt"),
    _kdate("mgsc_mgop_rcpd_bgd"),
    _kdate("mgsc_mgop_rcpd_edd"),
    _kdate("mgsc_mgrgsprd"),
    _kdate("mgsc_nstkdlprd"),
    _kdate("mgsc_nstklstprd"),
    _kdate("mgsc_osprpd_bgd"),
    _kdate("mgsc_osprpd_edd"),
    _kdate("mgsc_shclspd_bgd"),
    _kdate("mgsc_shclspd_edd"),
    _kdate("mgsc_shddstd"),
    _kdate("mgsc_trspprpd_bgd"),
    _kdate("mgsc_trspprpd_edd"),
    _txt("nmgcmp_cmpnm"),
    _txt("nmgcmp_mbsn"),
    _txt("nmgcmp_nbsn_rsl"),
    _txt("nmgcmp_rlst_atn"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
    _txt("otcpr_bdlst_sf_atn"),
    _txt("popt_ctr_atn"),
    _txt("popt_ctr_cn"),
    _num("rbsnfdtl_cpt", 13, 0),
    _num("rbsnfdtl_nic", 13, 0),
    _num("rbsnfdtl_sl", 14, 0),
    _num("rbsnfdtl_tast", 15, 0),
    _num("rbsnfdtl_tdbt", 14, 0),
    _num("rbsnfdtl_teqt", 14, 0),
    _txt("rs_sm_atn"),
    _txt("exevl_op"),
))

# stg_event_cmp_dv — dart_cmp_dv_decsn · survey 414행 · 컬럼 49
STG_EVENT_CMP_DV = _event("stg_event_cmp_dv", "dart_cmp_dv_decsn", "cmp_dv", (
    _txt("abcr_crrt"),
    _txt("abcr_nstkascnd"),
    _kdate("abcr_nstkasstd"),
    _kdate("abcr_nstkdlprd"),
    _kdate("abcr_nstklstprd"),
    _kdate("abcr_osprpd_bgd"),
    _kdate("abcr_osprpd_edd"),
    _txt("abcr_shstkcnt_rt_at_rs"),
    _kdate("abcr_trspprpd_bgd"),
    _kdate("abcr_trspprpd_edd"),
    _txt("adt_a_atn"),
    _txt("atdv_excmp_atdv_lstmn_atn"),
    _txt("atdv_excmp_cmpnm"),
    _num("atdv_excmp_exbsn_rsl", 15, 0),
    _txt("atdv_excmp_mbsn"),
    _num("atdvfdtl_cpt", 13, 0),
    _kdate("atdvfdtl_std"),
    _num("atdvfdtl_tast", 15, 0),
    _num("atdvfdtl_tdbt", 15, 0),
    _num("atdvfdtl_teqt", 15, 0),
    _kdate("bddd"),
    _kdate("cdobprpd_bgd"),
    _kdate("cdobprpd_edd"),
    _txt("dv_impef"),
    _txt("dv_mth"),
    _txt("dv_rt"),
    _txt("dv_trfbsnprt_cn"),
    _kdate("dvdt"),
    _txt("dvfcmp_cmpnm"),
    _txt("dvfcmp_mbsn"),
    _num("dvfcmp_nbsn_rsl", 14, 0),
    _txt("dvfcmp_rlst_atn"),
    _kdate("dvrgsprd"),
    _txt("ex_sm_r"),
    _num("ffdtl_cpt", 12, 0),
    _kdate("ffdtl_std"),
    _num("ffdtl_tast", 14, 0),
    _num("ffdtl_tdbt", 14, 0),
    _num("ffdtl_teqt", 14, 0),
    _kdate("gmtsck_prd"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
    _txt("popt_ctr_atn"),
    _txt("popt_ctr_cn"),
    _txt("rs_sm_atn"),
))

# stg_event_cmp_dvmg — dart_cmp_dvmg_decsn · survey 13행 · 컬럼 90
STG_EVENT_CMP_DVMG = _event("stg_event_cmp_dvmg", "dart_cmp_dvmg_decsn", "cmp_dvmg", (
    _txt("abcr_crrt"),
    _txt("abcr_nstkascnd"),
    _kdate("abcr_nstkasstd"),
    _kdate("abcr_nstkdlprd"),
    _kdate("abcr_nstklstprd"),
    _kdate("abcr_osprpd_bgd"),
    _kdate("abcr_osprpd_edd"),
    _txt("abcr_shstkcnt_rt_at_rs"),
    _kdate("abcr_trspprpd_bgd"),
    _kdate("abcr_trspprpd_edd"),
    _txt("adt_a_atn"),
    _txt("aprskh_ctref"),
    _txt("aprskh_ex_pc_mth_pd_pl"),
    _txt("aprskh_exrq"),
    _txt("aprskh_lmt"),
    _num("aprskh_plnprc", 6, 0),
    _txt("aprskh_pym_plpd_mth"),
    _txt("atdv_excmp_atdv_lstmn_atn"),
    _txt("atdv_excmp_cmpnm"),
    _num("atdv_excmp_exbsn_rsl", 13, 0),
    _txt("atdv_excmp_mbsn"),
    _num("atdvfdtl_cpt", 13, 0),
    _kdate("atdvfdtl_std"),
    _num("atdvfdtl_tast", 14, 0),
    _num("atdvfdtl_tdbt", 14, 0),
    _num("atdvfdtl_teqt", 14, 0),
    _kdate("bddd"),
    _txt("bdlst_atn"),
    _txt("dv_trfbsnprt_cn"),
    _txt("dvfcmp_atdv_lstmn_at"),
    _txt("dvfcmp_cmpnm"),
    _txt("dvfcmp_mbsn"),
    _txt("dvfcmp_nbsn_rsl"),
    _txt("dvmg_impef"),
    _txt("dvmg_mth"),
    _txt("dvmg_rt"),
    _txt("dvmg_rt_bs"),
    _num("dvmgnstk_cstk_cnt", 6, 0),
    _num("dvmgnstk_ostk_cnt", 8, 0),
    _kdate("dvmgsc_aprskh_expd_bgd"),
    _kdate("dvmgsc_aprskh_expd_edd"),
    _kdate("dvmgsc_cdobprpd_bgd"),
    _kdate("dvmgsc_cdobprpd_edd"),
    _kdate("dvmgsc_dvmgctrd"),
    _kdate("dvmgsc_dvmgdt"),
    _kdate("dvmgsc_dvmgop_rcpd_bgd"),
    _kdate("dvmgsc_dvmgop_rcpd_edd"),
    _kdate("dvmgsc_dvmgrgsprd"),
    _kdate("dvmgsc_ergmd"),
    _kdate("dvmgsc_gmtsck_prd"),
    _kdate("dvmgsc_shclspd_bgd"),
    _kdate("dvmgsc_shclspd_edd"),
    _kdate("dvmgsc_shddstd"),
    _txt("eadtat_intn"),
    _txt("eadtat_op"),
    _txt("ex_sm_r"),
    _txt("exevl_atn"),
    _txt("exevl_bs_rs"),
    _txt("exevl_intn"),
    _txt("exevl_op"),
    _txt("exevl_pd"),
    _txt("ffdtl_cpt"),
    _txt("ffdtl_std"),
    _txt("ffdtl_tast"),
    _txt("ffdtl_tdbt"),
    _txt("ffdtl_teqt"),
    _txt("mg_stn"),
    _txt("mgptncmp_cmpnm"),
    _txt("mgptncmp_mbsn"),
    _txt("mgptncmp_rl_cmpn"),
    _txt("nmgcmp_cmpnm"),
    _txt("nmgcmp_cpt"),
    _txt("nmgcmp_mbsn"),
    _txt("nmgcmp_rlst_atn"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
    _txt("otcpr_bdlst_sf_atn"),
    _txt("popt_ctr_atn"),
    _txt("popt_ctr_cn"),
    _num("rbsnfdtl_cpt", 13, 0),
    _num("rbsnfdtl_nic", 13, 0),
    _num("rbsnfdtl_sl", 14, 0),
    _num("rbsnfdtl_tast", 14, 0),
    _num("rbsnfdtl_tdbt", 13, 0),
    _num("rbsnfdtl_teqt", 13, 0),
    _txt("rs_sm_atn"),
))

# stg_event_stk_extr — dart_stk_extr_decsn · survey 112행 · 컬럼 56
STG_EVENT_STK_EXTR = _event("stg_event_stk_extr", "dart_stk_extr_decsn", "stk_extr", (
    _txt("adt_a_atn"),
    _txt("aprskh_ctref"),
    _txt("aprskh_lmt"),
    _num("aprskh_plnprc", 6, 0),
    _txt("aprskh_pym_plpd_mth"),
    _txt("atextr_cpcmpnm"),
    _kdate("bddd"),
    _txt("bdlst_atn"),
    _txt("ex_sm_r"),
    _txt("exevl_atn"),
    _txt("exevl_bs_rs"),
    _txt("exevl_intn"),
    _txt("exevl_op"),
    _txt("exevl_pd"),
    _txt("extr_pp"),
    _txt("extr_rt"),
    _txt("extr_rt_bs"),
    _txt("extr_sen"),
    _txt("extr_stn"),
    _txt("extr_tgcmp_cmpnm"),
    _txt("extr_tgcmp_mbsn"),
    _txt("extr_tgcmp_rl_cmpn"),
    _txt("extr_tgcmp_rp"),
    _num("extr_tgcmp_tisstk_cstk", 8, 0),
    _num("extr_tgcmp_tisstk_ostk", 9, 0),
    _kdate("extrsc_aprskh_expd_bgd"),
    _kdate("extrsc_aprskh_expd_edd"),
    _kdate("extrsc_extrctrd"),
    _kdate("extrsc_extrdt"),
    _kdate("extrsc_extrop_rcpd_bgd"),
    _kdate("extrsc_extrop_rcpd_edd"),
    _kdate("extrsc_gmtsck_prd"),
    _kdate("extrsc_nstkdlprd"),
    _kdate("extrsc_nstklstprd"),
    _kdate("extrsc_osprpd_bgd"),
    _kdate("extrsc_osprpd_edd"),
    _kdate("extrsc_shclspd_bgd"),
    _kdate("extrsc_shclspd_edd"),
    _kdate("extrsc_shddstd"),
    _kdate("extrsc_trspprpd"),
    _kdate("extrsc_trspprpd_bgd"),
    _kdate("extrsc_trspprpd_edd"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 2, 0),
    _txt("otcpr_bdlst_sf_atn"),
    _txt("popt_ctr_atn"),
    _txt("popt_ctr_cn"),
    _num("rbsnfdtl_cpt", 13, 0),
    _num("rbsnfdtl_tast", 15, 0),
    _num("rbsnfdtl_tdbt", 15, 0),
    _num("rbsnfdtl_teqt", 14, 0),
    _txt("rs_sm_atn"),
))

# stg_event_tsstk_dp — dart_tsstk_dp_decsn · survey 4069행 · 컬럼 32
STG_EVENT_TSSTK_DP = _event("stg_event_tsstk_dp", "dart_tsstk_dp_decsn", "tsstk_dp", (
    _txt("adt_a_atn"),
    _num("aq_wtn_div_estk", 8, 0),
    _num("aq_wtn_div_estk_rt", 9, 3, "_pct"),
    _num("aq_wtn_div_ostk", 9, 0),
    _num("aq_wtn_div_ostk_rt", 12, 5, "_pct"),
    _txt("cs_iv_bk"),
    _num("d1_slodlm_estk", 5, 0),
    _num("d1_slodlm_ostk", 7, 0),
    _kdate("dp_dd"),
    _num("dp_m_etc", 10, 0),
    _num("dp_m_mkt", 7, 0),
    _num("dp_m_otc", 9, 0),
    _num("dp_m_ovtm", 8, 0),
    _txt("dp_pp"),
    _num("dppln_prc_estk", 12, 0),
    _num("dppln_prc_ostk", 13, 0),
    _num("dppln_stk_estk", 7, 0),
    _num("dppln_stk_ostk", 8, 0),
    _kdate("dpprpd_bgd"),
    _kdate("dpprpd_edd"),
    _num("dpstk_prc_estk", 6, 0),
    _num("dpstk_prc_ostk", 10, 0),
    _num("eaq_estk", 7, 0),
    _num("eaq_estk_rt", 4, 2, "_pct"),
    _num("eaq_ostk", 9, 0),
    _num("eaq_ostk_rt", 8, 5, "_pct"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 2, 0),
))

# stg_event_ctrcvs_bgrq — dart_ctrcvs_bgrq · survey 168행 · 컬럼 9
STG_EVENT_CTRCVS_BGRQ = _event("stg_event_ctrcvs_bgrq", "dart_ctrcvs_bgrq", "ctrcvs_bgrq", (
    _txt("apcnt"),
    _txt("cpct"),
    _txt("ft_ctp_sc"),
    _txt("rq_rs"),
    _kdate("rqd"),
))

# stg_event_df_ocr — dart_df_ocr · survey 47행 · 컬럼 9
STG_EVENT_DF_OCR = _event("stg_event_df_ocr", "dart_df_ocr", "df_ocr", (
    _num("df_amt", 11, 0),
    _txt("df_bnk"),
    _txt("df_cn"),
    _txt("df_rs"),
    _kdate("dfd"),
))

# stg_event_ds_rs_ocr — dart_ds_rs_ocr · survey 134행 · 컬럼 9
STG_EVENT_DS_RS_OCR = _event("stg_event_ds_rs_ocr", "dart_ds_rs_ocr", "ds_rs_ocr", (
    _txt("adt_a_atn"),
    _txt("ds_rs"),
    _kdate("ds_rsd"),
    _num("od_a_at_b", 1, 0),
    _num("od_a_at_t", 1, 0),
))

# stg_event_bnk_mngt_pcbg — dart_bnk_mngt_pcbg · survey 15행 · 컬럼 9
STG_EVENT_BNK_MNGT_PCBG = _event("stg_event_bnk_mngt_pcbg", "dart_bnk_mngt_pcbg", "bnk_mngt_pcbg", (
    _kdate("cfd"),
    _txt("mngt_int"),
    _kdate("mngt_pcbg_dd"),
    _txt("mngt_pd"),
    _txt("mngt_rs"),
))

TABLES: tuple[TableRule, ...] = (
    STG_EVENT_TSSTK_AQ, STG_EVENT_PIIC,
    STG_EVENT_CVBD_IS, STG_EVENT_FRIC,
    STG_EVENT_PIFRIC, STG_EVENT_CR,
    STG_EVENT_CMP_MG, STG_EVENT_CMP_DV,
    STG_EVENT_CMP_DVMG, STG_EVENT_STK_EXTR,
    STG_EVENT_TSSTK_DP, STG_EVENT_CTRCVS_BGRQ,
    STG_EVENT_DF_OCR, STG_EVENT_DS_RS_OCR,
    STG_EVENT_BNK_MNGT_PCBG,
)
