"""전수조사 대상 선언 — 코드가 아니라 목록이다 (2026-09-01 플랜 v3).

여기 적힌 것만 조사한다. 규칙 추가 = 이 파일에 한 줄. 실행 로직은 survey_*.py.
나중에 UNIFIED_RULES 를 테스트 스위트로 옮길 때 이 선언부가 씨앗이 된다.
"""
import os

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "data", "raw")

DBS = {
    "krx":    os.path.join(RAW, "krx.db"),
    "kiwoom": os.path.join(RAW, "kiwoom.db"),
    "kis":    os.path.join(RAW, "kis.db"),
    "dart":   os.path.join(RAW, "dart.db"),
    "wise":   os.path.join(RAW, "wisereport.db"),
}

# (db, table, ticker_col, date_col, 자연키 후보 tuple|None)
# ticker+date 가 둘 다 있으면 격자 완전성 검사가 붙는다.
TABLES = [
    # ── KRX ──
    ("krx", "krx_stk_bydd_trd",     "ISU_CD", "BAS_DD", ("ISU_CD", "BAS_DD")),
    ("krx", "krx_ksq_bydd_trd",     "ISU_CD", "BAS_DD", ("ISU_CD", "BAS_DD")),
    ("krx", "krx_etf_bydd_trd",     "ISU_CD", "BAS_DD", ("ISU_CD", "BAS_DD")),
    ("krx", "krx_stk_isu_base_info", "ISU_SRT_CD", "bas_dd_req", ("ISU_SRT_CD", "bas_dd_req")),
    ("krx", "krx_ksq_isu_base_info", "ISU_SRT_CD", "bas_dd_req", ("ISU_SRT_CD", "bas_dd_req")),
    # ── 키움 ──
    ("kiwoom", "ka10060_investor_flows",   "ticker", "dt", ("ticker", "dt")),
    ("kiwoom", "ka10014_short_selling",    "ticker", "dt", ("ticker", "dt")),
    ("kiwoom", "ka10008_foreign_holdings", "ticker", "dt", ("ticker", "dt")),
    ("kiwoom", "ka20068_lending_balance",  "ticker", "dt", ("ticker", "dt")),
    ("kiwoom", "ka10099_stock_master",     "code",   "snap_date", ("snap_date", "code")),
    # ── KIS ──
    ("kis", "kis_investor_flow",  "req_ticker", "stck_bsop_date", None),
    ("kis", "kis_short_sale",     "req_ticker", "stck_bsop_date", None),
    ("kis", "kis_loan_trans",     "req_ticker", "bsop_date",      None),
    ("kis", "kis_credit_balance", "req_ticker", "deal_date",      None),
    ("kis", "kis_stock_info",     "req_ticker", None,             ("req_ticker",)),
    # ── DART ── (연도축이라 격자 검사 없음 — date_col=None)
    ("dart", "dart_fin_raw",    None, None, None),
    ("dart", "dart_dividend",   None, None, None),
    ("dart", "dart_shares",     None, None, None),
    ("dart", "dart_capital",    None, None, None),
    ("dart", "dart_tesstk",     None, None, None),
    ("dart", "dart_hyslr",      None, None, None),
    ("dart", "dart_audit",      None, None, None),
    ("dart", "dart_elestock",   None, None, None),
    ("dart", "dart_majorstock", None, None, None),
    ("dart", "dart_disclosure", "stock_code", None, ("rcept_no", "corp_code", "req_page_no")),
    ("dart", "dart_company",    None, None, ("corp_code",)),
    ("dart", "dart_tsstk_aq_decsn", None, None, ("rcept_no",)),
    ("dart", "dart_piic_decsn",     None, None, ("rcept_no",)),
    ("dart", "dart_cvbd_is_decsn",  None, None, ("rcept_no",)),
    ("dart", "dart_fric_decsn",     None, None, ("rcept_no",)),
    ("dart", "dart_pifric_decsn",   None, None, ("rcept_no",)),
    ("dart", "dart_cr_decsn",       None, None, ("rcept_no",)),
    ("dart", "dart_cmp_mg_decsn",   None, None, ("rcept_no",)),
    ("dart", "dart_cmp_dv_decsn",   None, None, ("rcept_no",)),
    ("dart", "dart_cmp_dvmg_decsn", None, None, ("rcept_no",)),
    ("dart", "dart_stk_extr_decsn", None, None, ("rcept_no",)),
    ("dart", "dart_tsstk_dp_decsn", None, None, ("rcept_no",)),
    ("dart", "dart_ctrcvs_bgrq",    None, None, ("rcept_no",)),
    ("dart", "dart_df_ocr",         None, None, ("rcept_no",)),
    ("dart", "dart_ds_rs_ocr",      None, None, ("rcept_no",)),
    ("dart", "dart_bnk_mngt_pcbg",  None, None, ("rcept_no",)),
    # ── WISE (v3 미러 — 파싱된 정형. ws_raw 원문은 stage 파싱 후 별도) ──
    ("wise", "v3_consensus_revision_daily", "stock_code", "base_date", ("stock_code", "base_date")),
    ("wise", "v3_analyst_opinions",         "stock_code", "snapshot_date", ("stock_code", "snapshot_date")),
    ("wise", "v3_consensus_annual",         "stock_code", None, ("sync_date", "stock_code", "period", "period_type")),
    ("wise", "ws_coverage",                 "cmp_cd", None, ("cmp_cd",)),
]

# 도메인 불변식 — (라벨, 위반 조건 SQL). "위반을 세는" 조건으로 적는다.
# TEXT 원장이므로 CAST 는 콤마 제거 후. 빈값/대시는 불변식 대상에서 제외(결측 축이 담당).
def _n(col):  # 콤마 제거 숫자화
    return f"CAST(REPLACE({col},',','') AS REAL)"

INVARIANTS = {
    "krx_stk_bydd_trd": [
        ("고가<저가", f"{_n('TDD_HGPRC')} < {_n('TDD_LWPRC')} AND TDD_HGPRC<>'' AND TDD_LWPRC<>''"),
        ("종가가 고저 밖", f"TDD_CLSPRC<>'' AND TDD_HGPRC<>'' AND TDD_LWPRC<>'' AND {_n('TDD_HGPRC')}>0 "
                        f"AND ({_n('TDD_CLSPRC')} > {_n('TDD_HGPRC')} OR {_n('TDD_CLSPRC')} < {_n('TDD_LWPRC')})"),
        ("거래량 음수", f"ACC_TRDVOL<>'' AND {_n('ACC_TRDVOL')} < 0"),
        ("시총 음수", f"MKTCAP<>'' AND {_n('MKTCAP')} < 0"),
    ],
    "krx_ksq_bydd_trd": [
        ("고가<저가", f"{_n('TDD_HGPRC')} < {_n('TDD_LWPRC')} AND TDD_HGPRC<>'' AND TDD_LWPRC<>''"),
        ("거래량 음수", f"ACC_TRDVOL<>'' AND {_n('ACC_TRDVOL')} < 0"),
    ],
    "kis_credit_balance": [
        ("잔고 음수", f"whol_loan_rmnd_stcn<>'' AND {_n('whol_loan_rmnd_stcn')} < 0"),
        ("결제일<매매일", "stlm_date<>'' AND deal_date<>'' AND stlm_date < deal_date"),
    ],
    "kis_short_sale": [
        ("누적공매도 음수", f"acml_ssts_cntg_qty<>'' AND {_n('acml_ssts_cntg_qty')} < 0"),
    ],
    "dart_cr_decsn": [
        ("감자 후>전", f"bfcr_tisstk_ostk NOT IN ('','-') AND atcr_tisstk_ostk NOT IN ('','-') "
                     f"AND {_n('atcr_tisstk_ostk')} > {_n('bfcr_tisstk_ostk')}"),
    ],
    "ka10099_stock_master": [
        ("상장주식수 0이하", f"listCount<>'' AND {_n('listCount')} <= 0"),
    ],
}

# 크로스소스 대조 — 같은 개념, 다른 원장. transform: abs | none. tol: 절대 허용오차.
# note 는 리포트에 그대로 실린다 (해석 주의점).
CROSS_CHECKS = [
    dict(concept="종가 (KRX vs 키움)", tol=0, transform_b="abs",
         a=("krx", "krx_stk_bydd_trd", "ISU_CD", "BAS_DD", "TDD_CLSPRC"),
         b=("kiwoom", "ka10060_investor_flows", "ticker", "dt", "cur_prc"),
         note="키움 부호=방향 → abs. 기존 실측 753만행 일치의 전수 재검"),
    dict(concept="종가 (KRX vs KIS신용)", tol=0, transform_b="none",
         a=("krx", "krx_stk_bydd_trd", "ISU_CD", "BAS_DD", "TDD_CLSPRC"),
         b=("kis", "kis_credit_balance", "req_ticker", "deal_date", "stck_prpr"),
         note="deal_date 키 정합의 전수 증명 (기존은 표본)"),
    dict(concept="거래량 (KRX vs 키움'대금'컬럼)", tol=0, transform_b="abs",
         a=("krx", "krx_stk_bydd_trd", "ISU_CD", "BAS_DD", "ACC_TRDVOL"),
         b=("kiwoom", "ka10060_investor_flows", "ticker", "dt", "acc_trde_prica"),
         note="컬럼명 오표기 실증 — 이름은 대금, 실측은 거래량 (804/804 → 전수)"),
    dict(concept="공매도 수량 (키움 vs KIS)", tol=0, transform_b="none",
         a=("kiwoom", "ka10014_short_selling", "ticker", "dt", "ovr_shrts_qty"),
         b=("kis", "kis_short_sale", "req_ticker", "stck_bsop_date", "acml_ssts_cntg_qty"),
         note="겹치는 (종목,일) 만. 키움 리셋 결함(77.7%) 확인된 컬럼 — 불일치 패턴이 곧 진단"),
    dict(concept="상장주식수 (KRX 08-20 vs 키움마스터 최신)", tol=0, transform_b="none",
         a=("krx", "krx_stk_isu_base_info", "ISU_SRT_CD", None, "LIST_SHRS",
            "AND bas_dd_req=(SELECT MAX(bas_dd_req) FROM src.krx_stk_isu_base_info)"),
         b=("kiwoom", "ka10099_stock_master", "code", None, "listCount",
            "AND snap_date=(SELECT MAX(snap_date) FROM src.ka10099_stock_master)"),
         note="스냅샷 일자가 다름(08-20 vs 최신) — 불일치=그 사이 기업행위. 정보성 대조"),
]

MISSING_MARKERS = ("", "-", "0")   # NULL 은 별도 카운트
SAMPLE_ROWS = 120_000              # 패턴 분류 표본 (행 무작위 stride — 기업 표본 아님)
