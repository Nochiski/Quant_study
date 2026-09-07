-- fin_std (S12) — 재무 PIT 표준화. DESIGN v1.2 §4-4 · GATES v1.0 §3-⑫ · EG7-P04.
-- grain (corp_code, period_end, report_code, fs_div, vintage_kind) · receipt_axis.
-- 모집단 = `stg_fin` 의 (corp_code, bsns_year, reprt_code, fs_div) 중 표준계정 행이 하나라도
-- 있는 그룹(= EG1 우변). 그룹당 `rcept_no` 는 하나다(API 는 요청한 보고서 하나를 돌려준다).
--
-- 계정 대응표 `_acct` 는 `src/fin_map.py` 가 정본이고 `rules_s12.py:acct_values_sql()` 이 만든
-- 문자열을 그대로 옮겼다(tests 가 대조). 탐색 순서는 tier(a_concept → b_concept_alt → c_nm →
-- d 보험 대체 → e 은행 대체, 정렬 가능한 라벨이라 min() 이 고른다) 다음 sj 순서(IS → CIS)다. 같은 우선순위에서 `pick` 은 값이 하나로
-- 모일 때만 채우고 갈리면 NULL(모호), `sum` 은 합산한다.
--
-- 기간: `period_end` = `stg_doc_meta.period_to`(main), `report_code` = `doc_acode`.
-- `doc_acode` 는 1분기·3분기를 둘 다 11013 으로 적으므로 `period_from → period_to` 개월 수로
-- 가른다(≤ `quarter_months` → 11013 · 그 밖 → 11014).
-- 문서가 없으면 후보 규칙: `corp.fiscal_month` 말일(bsns_year · bsns_year+1)에서 보고서 종류만큼
-- 당긴 달의 말일 중, 접수일까지 0~`period_end_lag_max_days` 일인 것. 정확히 하나면
-- `period_end_basis='inferred'`, 아니면 `period_unresolved` 격리.
--
-- 기간 어휘(DEFECT-C02): 손익 `thstrm_amount` 는 분기 3개월 · 사업보고서 12개월. 4분기 값은
-- `<계정>_q4_derived` = 사업보고서 − Σ(1Q·2Q·3Q) 이고 **셋 중 하나라도 없으면 NULL**(부분합
-- 금지). 현금흐름은 연초누계 `_ytd` 를 그대로 싣고 `_q` = 자기 누계 − 직전 보고서 누계
-- (11013 은 누계 자체가 분기값). 두 파생 블록은 `*_available_date`(구성 행 max)와 `*_n_rows`
-- 를 동반한다.
--
-- **재수집 판본 dedup**: `stg_fin`·`stg_disclosure` 는 `write_mode='append_only'` ·
-- `key_unique=False` 라(rules_dart.py) 같은 자연키가 여러 번 실릴 수 있다. `grp` 는 GROUP BY 라
-- 안전하지만 ① `fin` 을 안 접으면 `sum` 집계(lease_liab · 금융업 매출 대체)가 이중계상되고
-- ② `stg_disclosure` 를 그대로 조인하면 grp 행이 판본 수만큼 늘어 grain 이 깨진다(S11 이 서버
-- 1차 빌드에서 같은 함정으로 EG1 delta −26). `stg_doc_meta` 는 key_unique=True 지만 접수당 main
-- 멤버가 여럿일 수 있어 `doc` 이 이미 접는다.
--
-- PIT: `available_date = rcept_dt`(derived, `stg_disclosure`). `stg_rcept_dt_map` 은 stage 에
-- 실재하지 않는다(GATES §9). 판본은 `api_restated` 하나 · `restated_unknown = true`(4A).
-- 격리 4종: non_krw · period_unresolved · rcept_lag_out_of_range · duplicate_vintage.
WITH _acct(metric, tier, kind, tokens, sjs, agg, require_tag, basis, family) AS (
    VALUES
        ('revenue', 'a_concept', 'concept', ['Revenue'], ['IS', 'CIS'], 'pick', NULL, 'standard', 'flow'),
        ('revenue', 'c_nm', 'nm', ['매출액', '수익(매출액)', '영업수익'], ['IS', 'CIS'], 'pick', NULL, 'standard', 'flow'),
        ('cost_of_sales', 'a_concept', 'concept', ['CostOfSales'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('cost_of_sales', 'c_nm', 'nm', ['매출원가'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('gross_profit', 'a_concept', 'concept', ['GrossProfit'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('gross_profit', 'c_nm', 'nm', ['매출총이익', '매출총이익(손실)'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('op_profit', 'a_concept', 'concept', ['OperatingIncomeLoss', 'ProfitLossFromOperatingActivities'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('op_profit', 'c_nm', 'nm', ['영업이익', '영업이익(손실)'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('pretax_income', 'a_concept', 'concept', ['ProfitLossBeforeTax'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('pretax_income', 'c_nm', 'nm', ['법인세비용차감전순이익'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('net_income', 'a_concept', 'concept', ['ProfitLoss'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('net_income', 'c_nm', 'nm', ['당기순이익', '당기순이익(손실)', '연결당기순이익'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('net_income_owners', 'a_concept', 'concept', ['ProfitLossAttributableToOwnersOfParent'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('eps_basic', 'a_concept', 'concept', ['BasicEarningsLossPerShare'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('eps_basic', 'c_nm', 'nm', ['기본주당이익', '기본주당순이익', '보통주 기본주당이익', '보통주기본주당순손실'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('depreciation', 'a_concept', 'concept', ['DepreciationAndAmortisationExpense'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('depreciation', 'b_concept_alt', 'concept', ['DepreciationExpense'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('depreciation', 'c_nm', 'nm', ['감가상각비 및 상각비', '감가상각비'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('interest_expense', 'a_concept', 'concept', ['InterestExpense'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('interest_expense', 'c_nm', 'nm', ['이자비용'], ['IS', 'CIS'], 'pick', NULL, NULL, 'flow'),
        ('total_asset', 'a_concept', 'concept', ['Assets'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('total_asset', 'c_nm', 'nm', ['자산총계'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('total_liab', 'a_concept', 'concept', ['Liabilities'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('total_liab', 'c_nm', 'nm', ['부채총계'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('total_equity', 'a_concept', 'concept', ['Equity'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('total_equity', 'c_nm', 'nm', ['자본총계'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('equity_owners', 'a_concept', 'concept', ['EquityAttributableToOwnersOfParent'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('cash', 'a_concept', 'concept', ['CashAndCashEquivalents'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('cash', 'b_concept_alt', 'concept', ['CashAndDuefromBanks'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('cash', 'c_nm', 'nm', ['현금및현금성자산'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('inventories', 'a_concept', 'concept', ['Inventories'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('inventories', 'c_nm', 'nm', ['재고자산'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('current_assets', 'a_concept', 'concept', ['CurrentAssets'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('current_assets', 'c_nm', 'nm', ['유동자산'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('current_liab', 'a_concept', 'concept', ['CurrentLiabilities'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('current_liab', 'c_nm', 'nm', ['유동부채'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('lease_liab', 'a_concept', 'concept', ['CurrentLeaseLiabilities', 'NoncurrentLeaseLiabilities'], ['BS'], 'sum', NULL, NULL, 'stock'),
        ('lease_liab', 'b_concept_alt', 'concept', ['LeaseLiabilities'], ['BS'], 'sum', NULL, NULL, 'stock'),
        ('borrowings', 'a_concept', 'concept', ['Borrowings'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('borrowings', 'c_nm', 'nm', ['차입부채'], ['BS'], 'pick', NULL, NULL, 'stock'),
        ('cf_operating_ytd', 'a_concept', 'concept', ['CashFlowsFromUsedInOperatingActivities'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('cf_operating_ytd', 'c_nm', 'nm', ['영업활동현금흐름', '영업활동으로인한현금흐름', '영업활동순현금흐름'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('cf_investing_ytd', 'a_concept', 'concept', ['CashFlowsFromUsedInInvestingActivities'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('cf_investing_ytd', 'c_nm', 'nm', ['투자활동현금흐름', '투자활동으로인한현금흐름', '투자활동순현금흐름'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('cf_financing_ytd', 'a_concept', 'concept', ['CashFlowsFromUsedInFinancingActivities'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('cf_financing_ytd', 'c_nm', 'nm', ['재무활동현금흐름', '재무활동으로인한현금흐름', '재무활동순현금흐름'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('capex_ytd', 'a_concept', 'concept', ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('capex_ytd', 'c_nm', 'nm', ['유형자산의 취득', '유형자산의취득', '유형자산 취득'], ['CF'], 'pick', NULL, NULL, 'cf'),
        ('revenue', 'd_insurance_gross', 'concept', ['OperatingIncomeInsurance', 'InvestmentIncome'], ['IS', 'CIS'], 'sum', 'InvestmentIncome', 'insurance_gross', 'flow'),
        ('revenue', 'e_banking_gross', 'concept', ['RevenueFromInterest', 'FeeAndCommissionIncome', 'OperatingIncomeInsurance'], ['IS', 'CIS'], 'sum', 'RevenueFromInterest', 'banking_gross', 'flow')
),
_qcode(reprt_code) AS (
    -- 3개월 손익을 싣는 분기 판본. q4 파생은 이 셋이 다 있어야 선다(부분합 금지).
    VALUES ('11012'), ('11013'), ('11014')
),
_rcode(reprt_code) AS (
    -- 한 회계연도의 보고서 판본 전부 — q4_derived_n_rows 의 상한(넷)
    VALUES ('11011'), ('11012'), ('11013'), ('11014')
),
grp AS (
    -- 모집단. `WHERE account_std` 는 GATES §3-⑫ 우변과 같은 술어다.
    SELECT corp_code, bsns_year, reprt_code, fs_div,
           min(rcept_no)                                        AS rcept_no,
           min(currency)                                        AS currency,
           bool_and(is_krw)                                     AS is_krw_group
    FROM stg_fin
    GROUP BY corp_code, bsns_year, reprt_code, fs_div
    HAVING bool_or(account_std)
),
fin AS (
    SELECT f.corp_code, f.bsns_year, f.reprt_code, f.fs_div, f.sj_div,
           regexp_replace(f.account_id, '^(ifrs-full_|ifrs_|dart_)', '')  AS concept,
           f.account_nm,
           f.thstrm_amount
    FROM stg_fin f
    SEMI JOIN grp g
      ON g.corp_code = f.corp_code AND g.bsns_year = f.bsns_year
     AND g.reprt_code = f.reprt_code AND g.fs_div = f.fs_div
    -- 자연키 8열(rules_dart.STG_FIN natural_key)당 1행. first_write_wins(`observed_date` 최소).
    QUALIFY row_number() OVER (
        PARTITION BY f.corp_code, f.bsns_year, f.reprt_code, f.fs_div, f.sj_div,
                     f.account_id, f.account_detail, f.ord
        ORDER BY f.observed_date NULLS LAST, f.thstrm_amount NULLS LAST) = 1
),
dt AS (
    -- 접수일 원천. 접수번호당 1행으로 접어야 `head` 조인이 grp 행을 늘리지 않는다.
    SELECT rcept_no, rcept_dt
    FROM stg_disclosure
    QUALIFY row_number() OVER (PARTITION BY rcept_no
                               ORDER BY observed_date NULLS LAST, rcept_dt NULLS LAST) = 1
),
req AS (
    -- 대체 규칙의 발동 조건(fin_map.REVENUE_FALLBACK.require) — 태그 존재 여부
    SELECT corp_code, bsns_year, reprt_code, fs_div,
           bool_or(concept = 'InvestmentIncome')                 AS has_investment_income,
           bool_or(concept = 'RevenueFromInterest')              AS has_interest_revenue
    FROM fin
    WHERE sj_div IN ('IS', 'CIS')
    GROUP BY corp_code, bsns_year, reprt_code, fs_div
),
hit AS (
    SELECT f.corp_code, f.bsns_year, f.reprt_code, f.fs_div,
           a.metric, a.tier, a.agg, a.basis, a.family,
           list_position(a.sjs, f.sj_div)                        AS sj_rank,
           f.thstrm_amount                                       AS val
    FROM fin f
    JOIN _acct a
      ON list_position(a.sjs, f.sj_div) IS NOT NULL
     AND ((a.kind = 'concept' AND list_contains(a.tokens, f.concept))
       OR (a.kind = 'nm' AND list_contains(a.tokens, f.account_nm)))
    LEFT JOIN req r
      ON r.corp_code = f.corp_code AND r.bsns_year = f.bsns_year
     AND r.reprt_code = f.reprt_code AND r.fs_div = f.fs_div
    WHERE f.thstrm_amount IS NOT NULL
      AND (a.require_tag IS NULL
           OR (a.require_tag = 'InvestmentIncome' AND r.has_investment_income)
           OR (a.require_tag = 'RevenueFromInterest' AND r.has_interest_revenue))
),
best_tier AS (
    -- 우선순위 ① tier — 앞선 tier 가 한 건이라도 있으면 거기서 끝난다(fin_map concept_alt·nm 규칙)
    SELECT corp_code, bsns_year, reprt_code, fs_div, metric, min(tier) AS tier
    FROM hit
    GROUP BY corp_code, bsns_year, reprt_code, fs_div, metric
),
best AS (
    -- 우선순위 ② 같은 tier 안에서 sj 순서(IS → CIS)
    SELECT h.corp_code, h.bsns_year, h.reprt_code, h.fs_div, h.metric, h.tier,
           min(h.sj_rank)                                        AS sj_rank
    FROM hit h
    JOIN best_tier b
      ON b.corp_code = h.corp_code AND b.bsns_year = h.bsns_year
     AND b.reprt_code = h.reprt_code AND b.fs_div = h.fs_div AND b.metric = h.metric
     AND b.tier = h.tier
    GROUP BY h.corp_code, h.bsns_year, h.reprt_code, h.fs_div, h.metric, h.tier
),
val AS (
    SELECT h.corp_code, h.bsns_year, h.reprt_code, h.fs_div, h.metric,
           min(h.family)                                         AS family,
           min(h.basis)                                          AS basis,
           CASE WHEN min(h.agg) = 'sum' THEN sum(h.val)
                WHEN count(DISTINCT h.val) = 1 THEN min(h.val)
           END                                                   AS v
    FROM hit h
    JOIN best b
      ON b.corp_code = h.corp_code AND b.bsns_year = h.bsns_year
     AND b.reprt_code = h.reprt_code AND b.fs_div = h.fs_div AND b.metric = h.metric
     AND b.tier = h.tier AND b.sj_rank = h.sj_rank
    GROUP BY h.corp_code, h.bsns_year, h.reprt_code, h.fs_div, h.metric
),
doc AS (
    SELECT rcept_no, period_from, period_to, doc_acode
    FROM stg_doc_meta
    WHERE member_role = 'main' AND period_to IS NOT NULL AND period_from IS NOT NULL
      AND doc_acode IN ('11011', '11012', '11013')
    QUALIFY row_number() OVER (PARTITION BY rcept_no
                               ORDER BY period_to, period_from, doc_acode) = 1
),
head AS (
    SELECT g.*,
           d.rcept_dt,
           m.period_from, m.period_to, m.doc_acode,
           c.fiscal_month
    FROM grp g
    LEFT JOIN dt d ON d.rcept_no = g.rcept_no
    LEFT JOIN doc m ON m.rcept_no = g.rcept_no
    LEFT JOIN corp c ON c.corp_code = g.corp_code
),
pe_cand AS (
    -- 문서 없는 그룹의 후보 기간 말일 — 결산월 말일(bsns_year · bsns_year+1)에서 보고서 종류만큼 당긴다
    SELECT h.corp_code, h.bsns_year, h.reprt_code, h.fs_div,
           last_day(make_date(CAST(h.bsns_year AS INTEGER) + y.off, h.fiscal_month, 1)
                    - INTERVAL (CASE h.reprt_code WHEN '11011' THEN 0
                                                  WHEN '11012' THEN k.half_months
                                                  WHEN '11013' THEN k.three_quarter_months
                                                  ELSE k.quarter_months END) MONTH)  AS pe
    FROM head h
    CROSS JOIN _const k
    CROSS JOIN (SELECT unnest([0, 1]) AS off) y
    WHERE h.period_to IS NULL AND h.fiscal_month IS NOT NULL AND h.rcept_dt IS NOT NULL
),
pe_inf AS (
    SELECT c.corp_code, c.bsns_year, c.reprt_code, c.fs_div,
           count(*)                                              AS n_cand,
           min(c.pe)                                             AS pe
    FROM pe_cand c
    JOIN head h
      ON h.corp_code = c.corp_code AND h.bsns_year = c.bsns_year
     AND h.reprt_code = c.reprt_code AND h.fs_div = c.fs_div
    CROSS JOIN _const k
    WHERE date_diff('day', c.pe, h.rcept_dt) BETWEEN 0 AND CAST(k.period_end_lag_max_days AS BIGINT)
    GROUP BY c.corp_code, c.bsns_year, c.reprt_code, c.fs_div
),
resolved AS (
    SELECT h.corp_code, h.bsns_year, h.reprt_code, h.fs_div, h.rcept_no, h.rcept_dt,
           h.currency, h.is_krw_group,
           h.period_from                                         AS period_start,
           CASE WHEN h.period_to IS NOT NULL THEN h.period_to
                WHEN i.n_cand = 1 THEN i.pe END                  AS period_end,
           CASE WHEN h.period_to IS NOT NULL THEN 'document'
                WHEN i.n_cand = 1 THEN 'inferred' END            AS period_end_basis,
           CASE WHEN h.period_to IS NOT NULL THEN
                     CASE WHEN h.doc_acode <> '11013' THEN h.doc_acode
                          -- doc_acode 11013 은 1분기·3분기 공용. 3개월이면 1분기, 아니면 3분기.
                          WHEN date_diff('month', h.period_from, h.period_to) + 1
                               <= k.quarter_months THEN '11013'
                          ELSE '11014' END
                WHEN i.n_cand = 1 THEN h.reprt_code END          AS report_code
    FROM head h
    CROSS JOIN _const k
    LEFT JOIN pe_inf i
      ON i.corp_code = h.corp_code AND i.bsns_year = h.bsns_year
     AND i.reprt_code = h.reprt_code AND i.fs_div = h.fs_div
),
judged AS (
    SELECT r.*,
           CASE WHEN NOT coalesce(r.is_krw_group, FALSE)         THEN 'non_krw'
                WHEN r.period_end IS NULL                        THEN 'period_unresolved'
                WHEN r.rcept_dt IS NULL
                     OR date_diff('day', r.period_end, r.rcept_dt) < 0
                     OR date_diff('day', r.period_end, r.rcept_dt)
                        > CAST(k.rcept_lag_p99_days AS BIGINT)   THEN 'rcept_lag_out_of_range'
           END                                                   AS pre_reject
    FROM resolved r
    CROSS JOIN _const k
),
dup AS (
    SELECT corp_code, period_end, report_code, fs_div, count(*) AS n_grain
    FROM judged
    WHERE pre_reject IS NULL
    GROUP BY corp_code, period_end, report_code, fs_div
),
q4src AS (
    -- 1Q·2Q(반기)·3Q 의 3개월 손익 합. 세 판본이 다 있어야(n_q = 3) q4 가 선다(부분합 금지).
    SELECT corp_code, bsns_year, fs_div, metric,
           sum(v)                                                AS sum_q,
           count(v)                                              AS n_q
    FROM val
    WHERE family = 'flow' AND reprt_code IN (SELECT reprt_code FROM _qcode)
    GROUP BY corp_code, bsns_year, fs_div, metric
),
q4 AS (
    SELECT a.corp_code, a.bsns_year, a.fs_div, a.metric,
           a.v - q.sum_q                                         AS v
    FROM val a
    JOIN q4src q
      ON q.corp_code = a.corp_code AND q.bsns_year = a.bsns_year
     AND q.fs_div = a.fs_div AND q.metric = a.metric
    WHERE a.reprt_code = '11011' AND a.family = 'flow' AND a.v IS NOT NULL
      AND q.n_q = (SELECT count(*) FROM _qcode)
),
q4meta AS (
    -- 구성 보고서 수(최대 4)와 그 접수일의 max — 파생 컬럼의 공개시점(DESIGN §3)
    SELECT corp_code, bsns_year, fs_div,
           count(DISTINCT reprt_code)                            AS n_rows,
           max(rcept_dt)                                         AS available_date
    FROM head
    WHERE reprt_code IN (SELECT reprt_code FROM _rcode)
    GROUP BY corp_code, bsns_year, fs_div
),
cfq AS (
    SELECT s.corp_code, s.bsns_year, s.reprt_code, s.fs_div, s.metric,
           CASE WHEN s.reprt_code = '11013' THEN s.v
                ELSE s.v - p.v END                               AS v
    FROM val s
    LEFT JOIN val p
      ON p.corp_code = s.corp_code AND p.bsns_year = s.bsns_year
     AND p.fs_div = s.fs_div AND p.metric = s.metric
     AND p.reprt_code = CASE s.reprt_code WHEN '11012' THEN '11013'
                                          WHEN '11014' THEN '11012'
                                          WHEN '11011' THEN '11014' END
    WHERE s.family = 'cf'
),
cfqmeta AS (
    SELECT s.corp_code, s.bsns_year, s.reprt_code, s.fs_div,
           CASE WHEN s.reprt_code = '11013' THEN 1
                WHEN p.rcept_no IS NOT NULL THEN 2 ELSE 1 END    AS n_rows,
           greatest(s.rcept_dt, coalesce(p.rcept_dt, s.rcept_dt)) AS available_date
    FROM head s
    LEFT JOIN head p
      ON p.corp_code = s.corp_code AND p.bsns_year = s.bsns_year AND p.fs_div = s.fs_div
     AND p.reprt_code = CASE s.reprt_code WHEN '11012' THEN '11013'
                                          WHEN '11014' THEN '11012'
                                          WHEN '11011' THEN '11014' END
),
wide AS (
    SELECT corp_code, bsns_year, reprt_code, fs_div,
           max(v) FILTER (WHERE metric = 'revenue')              AS revenue,
           max(basis) FILTER (WHERE metric = 'revenue' AND v IS NOT NULL) AS revenue_basis,
           max(v) FILTER (WHERE metric = 'cost_of_sales')        AS cost_of_sales,
           max(v) FILTER (WHERE metric = 'gross_profit')         AS gross_profit,
           max(v) FILTER (WHERE metric = 'op_profit')            AS op_profit,
           max(v) FILTER (WHERE metric = 'pretax_income')        AS pretax_income,
           max(v) FILTER (WHERE metric = 'net_income')           AS net_income,
           max(v) FILTER (WHERE metric = 'net_income_owners')    AS net_income_owners,
           max(v) FILTER (WHERE metric = 'eps_basic')            AS eps_basic,
           max(v) FILTER (WHERE metric = 'depreciation')         AS depreciation,
           max(v) FILTER (WHERE metric = 'interest_expense')     AS interest_expense,
           max(v) FILTER (WHERE metric = 'total_asset')          AS total_asset,
           max(v) FILTER (WHERE metric = 'total_liab')           AS total_liab,
           max(v) FILTER (WHERE metric = 'total_equity')         AS total_equity,
           max(v) FILTER (WHERE metric = 'equity_owners')        AS equity_owners,
           max(v) FILTER (WHERE metric = 'cash')                 AS cash,
           max(v) FILTER (WHERE metric = 'inventories')          AS inventories,
           max(v) FILTER (WHERE metric = 'current_assets')       AS current_assets,
           max(v) FILTER (WHERE metric = 'current_liab')         AS current_liab,
           max(v) FILTER (WHERE metric = 'lease_liab')           AS lease_liab,
           max(v) FILTER (WHERE metric = 'borrowings')           AS borrowings,
           max(v) FILTER (WHERE metric = 'cf_operating_ytd')     AS cf_operating_ytd,
           max(v) FILTER (WHERE metric = 'cf_investing_ytd')     AS cf_investing_ytd,
           max(v) FILTER (WHERE metric = 'cf_financing_ytd')     AS cf_financing_ytd,
           max(v) FILTER (WHERE metric = 'capex_ytd')            AS capex_ytd
    FROM val
    GROUP BY corp_code, bsns_year, reprt_code, fs_div
),
q4wide AS (
    SELECT corp_code, bsns_year, fs_div,
           max(v) FILTER (WHERE metric = 'revenue')              AS revenue_q4_derived,
           max(v) FILTER (WHERE metric = 'cost_of_sales')        AS cost_of_sales_q4_derived,
           max(v) FILTER (WHERE metric = 'gross_profit')         AS gross_profit_q4_derived,
           max(v) FILTER (WHERE metric = 'op_profit')            AS op_profit_q4_derived,
           max(v) FILTER (WHERE metric = 'pretax_income')        AS pretax_income_q4_derived,
           max(v) FILTER (WHERE metric = 'net_income')           AS net_income_q4_derived,
           max(v) FILTER (WHERE metric = 'net_income_owners')    AS net_income_owners_q4_derived,
           max(v) FILTER (WHERE metric = 'eps_basic')            AS eps_basic_q4_derived,
           max(v) FILTER (WHERE metric = 'depreciation')         AS depreciation_q4_derived,
           max(v) FILTER (WHERE metric = 'interest_expense')     AS interest_expense_q4_derived
    FROM q4
    GROUP BY corp_code, bsns_year, fs_div
),
cfqwide AS (
    SELECT corp_code, bsns_year, reprt_code, fs_div,
           max(v) FILTER (WHERE metric = 'cf_operating_ytd')     AS cf_operating_q,
           max(v) FILTER (WHERE metric = 'cf_investing_ytd')     AS cf_investing_q,
           max(v) FILTER (WHERE metric = 'cf_financing_ytd')     AS cf_financing_q,
           max(v) FILTER (WHERE metric = 'capex_ytd')            AS capex_q
    FROM cfq
    GROUP BY corp_code, bsns_year, reprt_code, fs_div
)
SELECT
    j.corp_code,
    j.period_end,
    j.report_code,
    j.fs_div,
    'api_restated'                                              AS vintage_kind,
    j.bsns_year,
    j.rcept_no,
    j.rcept_dt,
    j.period_start,
    j.period_end_basis,
    j.currency,
    coalesce(w.revenue_basis, 'unavailable')                    AS revenue_basis,
    TRUE                                                        AS restated_unknown,
    w.revenue, w.cost_of_sales, w.gross_profit, w.op_profit, w.pretax_income,
    w.net_income, w.net_income_owners, w.eps_basic, w.depreciation, w.interest_expense,
    w.total_asset, w.total_liab, w.total_equity, w.equity_owners, w.cash, w.inventories,
    w.current_assets, w.current_liab, w.lease_liab, w.borrowings,
    w.cf_operating_ytd, w.cf_investing_ytd, w.cf_financing_ytd, w.capex_ytd,
    q.revenue_q4_derived, q.cost_of_sales_q4_derived, q.gross_profit_q4_derived,
    q.op_profit_q4_derived, q.pretax_income_q4_derived, q.net_income_q4_derived,
    q.net_income_owners_q4_derived, q.eps_basic_q4_derived, q.depreciation_q4_derived,
    q.interest_expense_q4_derived,
    CASE WHEN j.reprt_code = '11011' THEN qm.n_rows END          AS q4_derived_n_rows,
    CASE WHEN j.reprt_code = '11011' THEN qm.available_date END  AS q4_derived_available_date,
    cq.cf_operating_q, cq.cf_investing_q, cq.cf_financing_q, cq.capex_q,
    cm.n_rows                                                   AS cf_q_n_rows,
    cm.available_date                                           AS cf_q_available_date,
    j.rcept_dt                                                  AS available_date,
    'derived'                                                   AS available_basis,
    coalesce(j.pre_reject,
             CASE WHEN d.n_grain > 1 THEN 'duplicate_vintage' END) AS reject_reason
FROM judged j
LEFT JOIN wide w
  ON w.corp_code = j.corp_code AND w.bsns_year = j.bsns_year
 AND w.reprt_code = j.reprt_code AND w.fs_div = j.fs_div
LEFT JOIN q4wide q
  ON j.reprt_code = '11011' AND q.corp_code = j.corp_code AND q.bsns_year = j.bsns_year
 AND q.fs_div = j.fs_div
LEFT JOIN q4meta qm
  ON qm.corp_code = j.corp_code AND qm.bsns_year = j.bsns_year AND qm.fs_div = j.fs_div
LEFT JOIN cfqwide cq
  ON cq.corp_code = j.corp_code AND cq.bsns_year = j.bsns_year
 AND cq.reprt_code = j.reprt_code AND cq.fs_div = j.fs_div
LEFT JOIN cfqmeta cm
  ON cm.corp_code = j.corp_code AND cm.bsns_year = j.bsns_year
 AND cm.reprt_code = j.reprt_code AND cm.fs_div = j.fs_div
LEFT JOIN dup d
  ON d.corp_code = j.corp_code AND d.period_end = j.period_end
 AND d.report_code = j.report_code AND d.fs_div = j.fs_div
