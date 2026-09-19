"""표별 데이터 신선도 허용 지연 — 건전성 C6 선언 (DEFECT-B01).

C1~C5 는 판이 **언제 커밋됐나**만 본다(`health.py` 머리말). 그래서 어떤 소스의 수집이 조용히
멈춰도 표는 매일 새 `v=` 판으로 다시 지어지고 계수도 안 움직여 C1·C3·C4 가 전부 통과한다 —
09-19 전수 감사 실측이 정확히 그 상태였다(`stg_short_daily_kis`·`stg_loan_daily_kis`·
`stg_flow_split_daily` available 2026-08-14, `stg_v3_*` 2026-09-01/02 인데 health 는 5/5 OK).
C6 는 판이 담은 **최신 사실의 날짜**(`manifest.BuildRecord.max_available_date`)를 대상일 D 와
대조해 그 무음 정지를 잡는다.

**허용 지연은 캘린더일 단순 정수다.** stage 는 캘린더를 모른다(거래일 계산은 daily 층의 일이고
stage 가 daily 를 import 하면 층 경계가 깨진다 — DESIGN §1). 그래서 주말·연휴와 소스별 도착
규약을 흡수할 만큼 넉넉히 잡는다. C6 는 "몇 시간 늦었나" 가 아니라 **"소스가 조용히 멈췄나"** 를
잡는 검사다 — 잡아야 할 실제 사고는 2~5주 정지였다. 아래 값은 전부 **저녁 판(D = 오늘 KST,
`build_evening.sh` 의 `is_trading_day` 가드 덕에 늘 거래일)** 에서 연휴 뒤 첫 거래일까지 정상
운영이 통과하도록 잡은 값이다. 아침 확정판은 D = 직전 거래일이라 항상 더 여유롭다.

판정하지 않는 표는 **사유를 남긴다** — 조용히 통과시키지 않는다(health 리포트 `skipped`,
`--skip` 규약과 같은 원칙).
"""
from __future__ import annotations

# ── 허용 지연(캘린더일). 판정 = max_available_date >= D − allow ─────────────────
#
# 최악의 정상 케이스는 **긴 연휴 뒤 첫 거래일 저녁 판**이다(저녁 체인은 `build_evening.sh` 의
# `is_trading_day` 가드 때문에 휴장일엔 아예 안 돈다 — D 는 늘 거래일이다). 설·추석은 연속 휴장
# 5일이 나오므로 그 다음 거래일의 직전 거래일이 D−6 이 된다. 아래 값은 거기에 여유를 더한 것이다.
#
# KRX 4표 10일  : KRX 는 T+1 08:00 도착(SPEC §2-20)이라 저녁 판(D=T)에는 D 행이 **아예 없다**.
#                 연휴 뒤 첫 거래일 저녁이면 최신이 D−6 → 10.
# 키움 5표 10일 : 저녁 수집이 당일 21:05(SPEC §2-21)이라 저녁 판에 D 가 그대로 들어온다. 다만
#                 외국인 보유(stg_foreign_daily)는 08:10 축(D−1)이라 연휴 5일 + 직전 2세션 결손이면
#                 7 로는 여유가 2일뿐이다 — KRX 와 같은 10(리뷰 REC-16).
# KIS 신용 10일 : 공표 T+2, 실입수 T+3(09-19 실측 전 구간 +3일 — 감사 E01). 조회창이 `d2=오늘`
#                 이라 `deal_date <= T-2` 만 오고, 연휴 뒤 첫 거래일이면 최신 deal_date 가 D−6 → 10.
# DART 10일     : 공시·보조원장은 접수일 축(rcept_dt)이라 영업일마다 들어온다. 연휴 여유 포함 10.
# 재무·주식수 150일: 정기보고서 주기 축이라 분기 공백이 정상이다. 11/14(3분기) → 3/31(사업보고서)
#                 사이가 최대 137 캘린더일이라 120 이면 2~3월에 오탐한다(리뷰 REC-16) → 150.
# WISE 10일     : `fetched_date` = 수집일 축, 18:05 저녁 슬롯. 연휴 여유 포함 10.
# 문서층 4표 10일: available 이 rcept_dt lookup — 공시와 같은 축.
#
# 값을 더 죄고 싶으면 거래일 캘린더가 먼저 필요하다. **지금은 느슨한 쪽이 옳다** — C6 가 잡아야
# 할 실제 사고는 2~5주 정지였고, 상시 오탐은 이 시스템을 여섯 번 멈춰 세운 실패 클래스다.
ALLOW_DAYS: dict[str, int] = {
    # KRX
    "stg_price_daily": 10, "stg_etf_price_daily": 10, "stg_index_daily": 10,
    "stg_listing_daily": 10,
    # 키움
    "stg_flow_daily_kiwoom": 10, "stg_short_daily_kiwoom": 10, "stg_foreign_daily": 10,
    "stg_lending_daily": 10, "stg_master_daily": 10,
    # KIS (일일 체인 대상은 신용잔고 하나 — 나머지 4표는 FROZEN)
    "stg_credit_daily": 10,
    # DART 공시·보조원장
    "stg_disclosure": 10, "stg_holder_elestock": 10, "stg_holder_majorstock": 10,
    "stg_dividend": 10, "stg_capital": 10, "stg_tesstk": 10, "stg_hyslr": 10,
    "stg_audit": 10,
    # DART 재무·주식수 (정기보고서 주기)
    "stg_fin": 150, "stg_shares": 150,
    # WISE
    "stg_consensus_monthly": 10, "stg_consensus_annual": 10, "stg_consensus_quarterly": 10,
    "stg_consensus_matrix": 10, "stg_analyst_summary": 10, "stg_analyst_broker": 10,
    "stg_fin_wise": 10,
    # 문서층
    "stg_doc_meta": 10, "stg_doc_section": 10, "stg_doc_correction": 10,
    "stg_doc_parse_log": 10,
}

_KIS_OUT_OF_SCOPE = (
    "일일 체인 범위 밖 — 플랜 2026-09-09-daily-incremental.md R6(KIS 는 credit 만 일일, "
    "flow·short·loan·master 4표는 폐지축이라 제외)·R10(범위 밖). 원장이 2026-08-14 에서 "
    "멈춘 것은 설계이지 정지가 아니다. 일일 수집에 편입하면 여기서 빼고 허용 지연을 선언할 것")
_V3_FROZEN = (
    "v3 미러 동결 — `sync_v3_wise.py` 가 2026-09-02 복사 이후 멈췄다(09-19 감사 E03). "
    "동결 유지 여부가 사용자 결정 대기라 판정하지 않는다. 재개 시 여기서 빼고 허용 지연 10 을 선언할 것")

# ── 동결(판정 안 함) — 사유 문자열 필수 ────────────────────────────────────────
FROZEN: dict[str, str] = {
    "stg_short_daily_kis": _KIS_OUT_OF_SCOPE,
    "stg_loan_daily_kis": _KIS_OUT_OF_SCOPE,
    "stg_flow_split_daily": _KIS_OUT_OF_SCOPE,
    "stg_delisted_master": _KIS_OUT_OF_SCOPE,
    "stg_v3_revision_daily": _V3_FROZEN,
    "stg_v3_analyst_opinions": _V3_FROZEN,
    "stg_v3_consensus_annual": _V3_FROZEN,
    "stg_v3_revision_compare": _V3_FROZEN,
}

# ── 희소 사건 표 — 최신 접수일이 수집 신선도가 아니다 ──────────────────────────
# DS005 주요사항보고 15종은 사건이 있어야 행이 생긴다. 09-19 실측 max(available_date)
# 분포가 2025-06-05(은행 관리절차 개시) ~ 2026-09-17 로, 1년 넘게 비어 있는 것이 정상이다.
_SPARSE_EVENT = ("희소 사건 표 — 최신 접수일이 수집 신선도가 아니다"
                 "(09-19 실측 max(available_date) 2025-06-05~2026-09-17). "
                 "이 축의 정지는 같은 원장을 쓰는 stg_disclosure 가 대신 잡는다")
SPARSE: dict[str, str] = {
    t: _SPARSE_EVENT for t in (
        "stg_event_tsstk_aq", "stg_event_piic", "stg_event_cvbd_is", "stg_event_fric",
        "stg_event_pifric", "stg_event_cr", "stg_event_cmp_mg", "stg_event_cmp_dv",
        "stg_event_cmp_dvmg", "stg_event_stk_extr", "stg_event_tsstk_dp",
        "stg_event_ctrcvs_bgrq", "stg_event_df_ocr", "stg_event_ds_rs_ocr",
        "stg_event_bnk_mngt_pcbg")
}

# ── available_date 를 부여하지 않는 표(rules `available.kind == "none"`) ────────
NO_AVAILABLE_REASON = "available_date 비부여 표(rules available.kind=none) — 판정 축이 없다"
NO_AVAILABLE: frozenset[str] = frozenset({
    "stg_ingest_krx", "stg_shards_kiwoom", "stg_calls_kis", "stg_units_kis",
    "stg_rcept_dt_map", "stg_company", "stg_corp_map", "stg_doc_index", "stg_calls_dart",
    "stg_units_dart", "stg_wise_coverage", "stg_calls_wise", "stg_delisted_master",
})

UNDECLARED = "허용 지연 미선언 — stage/freshness.py 에 등재할 것"
NO_RECORD = "신선도 기록 없음 — max_available_date 를 안 싣던 옛 판이다"


def judgement(table: str) -> tuple[int | None, str]:
    """`(허용 지연 일수, 사유)`. 일수가 None 이면 C6 가 판정하지 않고 사유를 리포트에 남긴다.

    동결·희소·비부여가 허용 지연 선언보다 우선한다 — 사유를 남기는 쪽이 정보가 많다.
    """
    for table_map in (FROZEN, SPARSE):
        if table in table_map:
            return None, table_map[table]
    if table in NO_AVAILABLE:
        return None, NO_AVAILABLE_REASON
    allow = ALLOW_DAYS.get(table)
    return (allow, "") if allow is not None else (None, UNDECLARED)
