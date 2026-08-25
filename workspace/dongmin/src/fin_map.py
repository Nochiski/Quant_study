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
"""

# concept = account_id 에서 ifrs-full_ / ifrs_ / dart_ 접두어를 벗긴 것
# nm      = account_nm 완전일치 폴백 (표준태그 미사용 행 대비)
# sj      = 허용 재무제표. 앞에서부터 찾는다 (IS → CIS 순)
# agg     = pick(하나 고름) | sum(합산)
FIN_MAP = {
    # ── 손익계산서 ────────────────────────────────────────────
    "revenue": dict(
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
        concept=["CashAndCashEquivalents"], nm=["현금및현금성자산"], sj=["BS"], agg="pick"),
    "inventories": dict(
        concept=["Inventories"], nm=["재고자산"], sj=["BS"], agg="pick"),
    "current_assets": dict(
        concept=["CurrentAssets"], nm=["유동자산"], sj=["BS"], agg="pick"),
    "current_liab": dict(
        concept=["CurrentLiabilities"], nm=["유동부채"], sj=["BS"], agg="pick"),
    "lease_liab": dict(
        # 유동·비유동이 한글명이 같아서 하나만 집으면 절반이 된다. 합산해야 한다
        concept=["CurrentLeaseLiabilities", "NoncurrentLeaseLiabilities"],
        nm=[], sj=["BS"], agg="sum"),

    # ── 현금흐름표 ────────────────────────────────────────────
    "cf_operating": dict(
        concept=["CashFlowsFromUsedInOperatingActivities"],
        nm=["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "cf_investing": dict(
        concept=["CashFlowsFromUsedInInvestingActivities"],
        nm=["투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "cf_financing": dict(
        concept=["CashFlowsFromUsedInFinancingActivities"],
        nm=["재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동순현금흐름"],
        sj=["CF"], agg="pick"),
    "capex": dict(
        # 부호는 회사마다 다르다(에코프로비엠 2020~22 음수 실측). 소비 측에서 abs()
        concept=["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
        nm=["유형자산의 취득", "유형자산의취득", "유형자산 취득"],
        sj=["CF"], agg="pick"),
}

# 금융업 매출액 대체 — 섹터별. §FACTORS.md 8 참조
# 은행·보험은 매출액 개념이 없다. 영업수익 총액이 실무(컨센서스) 표준이다.
REVENUE_BY_SECTOR = {
    "금융": dict(concept=["RevenueFromInterest", "FeeAndCommissionIncome",
                        "OperatingIncomeInsurance"], agg="sum", basis="banking_gross"),
    "보험": dict(concept=["OperatingIncomeInsurance", "InvestmentIncome"],
                agg="sum", basis="insurance_gross"),
    "증권": dict(concept=["Revenue"], agg="pick", basis="standard"),
}

CORE = ["revenue", "gross_profit", "op_profit", "net_income",
        "total_asset", "total_liab", "total_equity",
        "cf_operating", "cf_investing", "cf_financing", "capex"]
