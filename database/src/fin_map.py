"""계정 → 지표 매핑 규칙. 스테이지(long) → 통합층(wide) 변환의 사전(辭典)이다.

규칙은 실측으로 정해졌다. 파일럿 8사 × FY2015~2025 에서 핵심 11항목 330/330 이
잡혔고 회계 항등식 오차가 0원이었다.

지켜야 할 것 넷:
  1. 완전일치만 쓴다. 부분일치는 오탐 48종을 끌어들인다 —
     '영업이익' 으로 찾으면 '중단영업이익', '유형자산' 으로 찾으면 '유형자산처분이익'.
     특히 '자본총계' 부분일치는 '부채와자본총계'(=자산총계)를 잡아 장부가가 1.3~2.2배가 된다.
  2. sj_div 를 항목마다 명시한다. 없으면 330칸 중 54칸이 값 모호가 된다.
  3. 손익은 IS 가 없으면 CIS 로 간다. 파일럿 9사 중 4사가 IS 0행이다.
  4. account_nm 폴백은 표준태그가 없을 때만. 지배주주순이익에는 쓰지 않는다 —
     '지배기업소유주지분' 이 순이익과 총포괄손익 양쪽 이름이라 부호가 뒤집힌다(57건 실측).

이름에 기간을 싣는다 (DEFECT-C02):
  분기 보고서에서 손익(IS·CIS)은 당분기 3개월인데 현금흐름(CF)은 연초누계다.
  10사 FY2025 전건에서 성립했다 — 분기 IS·CIS 1,090행 중 1,088행에 누계 필드
  (thstrm_add_amount)가 따로 오고, CF 1,410행은 전건 그 필드가 오지 않는다.
  같은 행에 기간이 다른 값이 섞이므로 CF 항목은 `_ytd` 접미어로, 손익 기간은
  financials.is_months 컬럼으로 명시한다. 3개월 환산은 두 보고서를 결합해야
  나오는 파생이라 사실 층이 아니라 팩터 계산 층에서 한다.
"""

# concept = account_id 에서 ifrs-full_ / ifrs_ / dart_ 접두어를 벗긴 것
# nm      = account_nm 완전일치 폴백 (표준태그 미사용 행 대비)
# sj      = 허용 재무제표. 앞에서부터 찾는다 (IS → CIS 순)
# agg     = pick(하나 고름) | sum(합산)
# concept_alt = concept 가 한 건도 안 잡힐 때만 쓰는 2차 태그 목록 (합산 이중계상 방지)
FIN_MAP = {
    # ── 손익계산서 ────────────────────────────────────────────
    "revenue": dict(
        # 여기서 못 찾으면 REVENUE_FALLBACK 으로 넘어간다 (은행·보험)
        concept=["Revenue"], nm=["매출액", "수익(매출액)", "영업수익"],
        sj=["IS", "CIS"], agg="pick"),
    "cost_of_sales": dict(
        concept=["CostOfSales"], nm=["매출원가"], sj=["IS", "CIS"], agg="pick"),
    "gross_profit": dict(
        concept=["GrossProfit"], nm=["매출총이익", "매출총이익(손실)"],
        sj=["IS", "CIS"], agg="pick"),
    "op_profit": dict(
        concept=["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
        nm=["영업이익", "영업이익(손실)"], sj=["IS", "CIS"], agg="pick"),
    "pretax_income": dict(
        concept=["ProfitLossBeforeTax"], nm=["법인세비용차감전순이익"],
        sj=["IS", "CIS"], agg="pick"),
    "net_income": dict(
        # CF 를 넣으면 안 된다. 분기에서 CF 의 순이익은 3개월인데 흐름은 누계다
        concept=["ProfitLoss"], nm=["당기순이익", "당기순이익(손실)", "연결당기순이익"],
        sj=["IS", "CIS"], agg="pick"),
    "net_income_owners": dict(
        # nm 폴백 금지 — 총포괄손익과 한글명이 같다
        concept=["ProfitLossAttributableToOwnersOfParent"], nm=[],
        sj=["IS", "CIS"], agg="pick"),
    "eps_basic": dict(
        concept=["BasicEarningsLossPerShare"],
        nm=["기본주당이익", "기본주당순이익", "보통주 기본주당이익", "보통주기본주당순손실"],
        sj=["IS", "CIS"], agg="pick"),

    # ── 재무상태표 ────────────────────────────────────────────
    "total_asset": dict(
        concept=["Assets"], nm=["자산총계"], sj=["BS"], agg="pick"),
    "total_liab": dict(
        concept=["Liabilities"], nm=["부채총계"], sj=["BS"], agg="pick"),
    "total_equity": dict(
        # 'Equity' 완전일치. EquityAndLiabilities 는 자산총계라 절대 섞으면 안 된다
        concept=["Equity"], nm=["자본총계"], sj=["BS"], agg="pick"),
    "equity_owners": dict(
        concept=["EquityAttributableToOwnersOfParent"], nm=[], sj=["BS"], agg="pick"),
    "cash": dict(
        concept=["CashAndCashEquivalents"],
        # 은행 BS 의 현금 계정은 '현금 및 예치금' = CashAndDuefromBanks 다.
        # KB금융 4보고서 전건이 이 태그이고 CashAndCashEquivalents 는 0행이라
        # 여기가 없으면 은행의 현금이 통째로 NULL 이 된다(실측 252,708~347,769억).
        # 같은 목록에 나란히 두지 않고 concept_alt 로 미루는 이유: 둘 다 가진 회사가
        # 나오면 pick 이 모호(ambiguous)로 떨어져 기존에 차 있던 칸이 NULL 이 된다.
        # 예치금을 포함하는 넓은 정의보다 현금성자산이 업종 간 비교에 맞으므로
        # 표준 태그를 먼저 보고, 그게 없을 때만 은행 태그를 쓴다.
        concept_alt=["CashAndDuefromBanks"],
        nm=["현금및현금성자산"], sj=["BS"], agg="pick"),
    "inventories": dict(
        concept=["Inventories"], nm=["재고자산"], sj=["BS"], agg="pick"),
    "current_assets": dict(
        concept=["CurrentAssets"], nm=["유동자산"], sj=["BS"], agg="pick"),
    "current_liab": dict(
        concept=["CurrentLiabilities"], nm=["유동부채"], sj=["BS"], agg="pick"),
    "lease_liab": dict(
        # 유동·비유동이 한글명이 같아서 하나만 집으면 절반이 된다. 합산해야 한다
        concept=["CurrentLeaseLiabilities", "NoncurrentLeaseLiabilities"],
        # 보험사는 유동·비유동을 나누지 않고 LeaseLiabilities 한 행으로 온다
        # (삼성생명 4보고서 전건 3,339~3,664억). 위 합산 목록에 그냥 넣으면
        # 총계와 내역이 함께 오는 회사에서 이중계상되므로, 분할 표기가 한 건도
        # 없을 때만 총계를 쓴다.
        concept_alt=["LeaseLiabilities"],
        nm=[], sj=["BS"], agg="sum"),

    # ── 현금흐름표 ────────────────────────────────────────────
    # `_ytd` = 연초누계. 분기에서 11013 3개월 · 11012 6개월 · 11014 9개월 ·
    # 11011 12개월이다. 손익 컬럼(3개월)과 기간이 다르다 — DEFECT-C02.
    "cf_operating_ytd": dict(
        concept=["CashFlowsFromUsedInOperatingActivities"],
        nm=["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "cf_investing_ytd": dict(
        concept=["CashFlowsFromUsedInInvestingActivities"],
        nm=["투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "cf_financing_ytd": dict(
        concept=["CashFlowsFromUsedInFinancingActivities"],
        nm=["재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "capex_ytd": dict(
        # 부호는 회사마다 다르다(에코프로비엠 2020~22 음수 실측). 소비 측에서 abs()
        concept=["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
        nm=["유형자산의 취득", "유형자산의취득", "유형자산 취득"],
        sj=["CF"], agg="pick"),
}

# 금융업 매출액 대체 — FIN_MAP["revenue"] 가 값을 못 준 행에만 태운다.
# §FACTORS.md 8 참조. 은행·보험은 매출액 개념이 없다(팔 물건이 없다).
#
# 트리거는 섹터가 아니라 태그 부재다. 10사 FY2025 × 4보고서 실측:
#   ifrs-full_Revenue 있음 — 8사 전건(4/4). 증권(미래에셋 292,830억)도 여기 든다
#   ifrs-full_Revenue 없음 — KB금융·삼성생명 전건(0/4)
# RevenueFromInterest 존재로는 안 갈린다 — 미래에셋도 62,836억을 갖는다.
# Revenue 부재가 정확히 은행·보험만 집어내므로 WICS 섹터 수집이 선행 조건이 아니다.
#
# 은행식이냐 보험식이냐는 InvestmentIncome(투자서비스수익) 존재로 가른다:
#   삼성생명 272,479억 = 이자수익 84,066 + 수수료수익 21,772 + 파생 30,587 + … 로
#            하위 13계정 합이 원 단위까지 일치한다 → 이자·수수료를 따로 더하면 이중계상
#   KB금융   InvestmentIncome 4보고서 전건 부재 → 이자·수수료·보험수익을 따로 합산
#   (KB금융도 보험 태그를 갖는다 — KB라이프 연결. 그래서 보험 태그로는 못 가른다)
#
# 한계: 표본이 은행 1사 · 보험 1사다. FACTORS.md §8 이 "은행 합산식은 아직 확정
# 못 한다"로 남긴 미결을 이 규칙이 푸는 것이 아니다. 결측을 값으로 바꾸고 그 근거를
# revenue_basis 에 남기는 데까지만 한다. 실측 합계(FY2025 사업보고서):
#   KB금융 473,061억 · 삼성생명 371,383억.
# 컨센서스 KB 812,307억과 일치하지 않는다 — 애널리스트는 자회사 영업수익을 총액
# 합산하고 연결 손익계산서는 내부거래 상계 후라 집계 층위가 다르다(FACTORS.md §8).
# 원장에 원문이 다 있으므로 규칙이 확정되면 재빌드만으로 바꿀 수 있다.
REVENUE_FALLBACK = [
    dict(basis="insurance_gross", sj=["IS", "CIS"],
         require=["InvestmentIncome"],
         concept=["OperatingIncomeInsurance", "InvestmentIncome"]),
    dict(basis="banking_gross", sj=["IS", "CIS"],
         require=["RevenueFromInterest"],
         concept=["RevenueFromInterest", "FeeAndCommissionIncome",
                  "OperatingIncomeInsurance"]),
]

# DEFECT-S01 탐지용. 통합층 컬럼이 아니라 검사에만 쓴다 —
# EquityAndLiabilities(자본과부채총계)는 회계상 Assets 와 원 단위로 같아야 한다.
# 10사 40행 실측에서 concept 이 40/40 잡히고 38행이 일치, 어긋난 2행이 정확히
# 삼성생명 반기·3분기(DART 원본 stale)다.
# nm 폴백 목록은 실측 7종 전부 — 표준태그가 없던 2018년 이전 행 대비다.
CHECK_EQ_LIAB = dict(
    concept=["EquityAndLiabilities"],
    nm=["자본과부채총계", "부채와자본총계", "자본및부채총계", "부채및자본총계",
        "부채 및 자본 총계", "부채와 자본 총계", "부채 및 자본총계"],
    sj=["BS"], agg="pick")

CORE = ["revenue", "gross_profit", "op_profit", "net_income",
        "total_asset", "total_liab", "total_equity",
        "cf_operating_ytd", "cf_investing_ytd", "cf_financing_ytd", "capex_ytd"]
