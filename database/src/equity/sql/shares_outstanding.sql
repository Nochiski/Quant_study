-- shares_outstanding (S16) — 정기보고서 「주식의 총수 현황」. DESIGN v1.2 §4-5 · GATES §3-⑯.
-- grain (corp_code, bsns_year, reprt_code, se) · receipt_axis(`substr(rcept_no,1,4)`) ·
-- available_date = 접수일(stage 가 rcept_dt 에서 derived 로 낸 값 그대로).
--
-- 모집단 = `stg_shares` 의 **비집계 행**(`row_kind <> 'aggregate'`). 집계행(`se='합계'`)만 빼고
-- 나머지는 전부 남긴다 — 값이 전부 결측인 `se='비고'` 행도 격리하지 않는다(원칙 ④ 결측은 결측,
-- S11 `no_label` 격리 폐기와 같은 근거: 빼면 그 접수가 모집단에서 사라져 생존편향이 된다).
-- 아래 마커 줄 앞까지가 모집단 정의이고 `rules_s16.py:pool_sql` 이 그 조각을 그대로 재사용해
-- EG1 우변을 만든다(정의를 두 벌 베끼지 않는다 — S05 `pool_sql` · S11 `population_sql` 규약).
--
-- 컬럼은 DESIGN §4-5 의 "발행·자기·유통" 셋뿐이다:
--   issued_shr      `istc_totqy_shr`      발행주식의 총수
--   treasury_shr    `tesstk_co_shr`       자기주식수
--   distributed_shr `distb_stock_co_shr`  유통주식수
-- 항등 issued = treasury + distributed 는 굽지 않고 EG3_shares_outstanding 이 기록형으로 잰다
-- (게이트 술어를 산출식으로 재계산하지 않는다 — DESIGN §1).
--
-- **판본 중복**: grain 에 `rcept_no` 가 없으므로 같은 (법인, 사업연도, 보고서, 종류)에 접수가
-- 둘 이상이면(원본 + 정정 재제출) 한 행만 남긴다 — S11 `stg_disclosure` 선례 그대로 QUALIFY
-- first_write_wins(`available_date` → `rcept_no` → 값 컬럼 순, NULLS LAST 로 전순서). 접힌 수는
-- 산출 컬럼 `n_src_rows` 와 EG3 기록형 metric 이 함께 남긴다. 절단본은 전 그룹 1행이다.
--
-- KRX 대조: DART 주식수는 **검산·보조**이고 정본은 KRX(`price_daily.shares_out`, DESIGN §4-2).
-- 그래서 값을 KRX 로 덮지 않고, `corp_ticker`·`price_daily` 를 입력으로 고정해
-- EG3_shares_outstanding 이 결산일 기준 대조 결과를 기록형으로만 싣는다.
WITH src AS (
    SELECT corp_code,
           bsns_year,
           reprt_code,
           se,
           rcept_no,
           stlm_dt,
           istc_totqy_shr,
           tesstk_co_shr,
           distb_stock_co_shr,
           available_date,
           available_basis,
           count(*) OVER (PARTITION BY corp_code, bsns_year, reprt_code, se) AS n_src_rows
    FROM stg_shares
    WHERE row_kind <> 'aggregate'
)
-- ==== eg1: 모집단 정의 끝. 위 CTE 만으로 EG1 우변이 선다(rules_s16.py:pool_sql) ====
SELECT
    s.corp_code,
    s.bsns_year,
    s.reprt_code,
    s.se,
    s.rcept_no,
    s.stlm_dt,
    s.istc_totqy_shr                                             AS issued_shr,
    s.tesstk_co_shr                                              AS treasury_shr,
    s.distb_stock_co_shr                                         AS distributed_shr,
    s.n_src_rows,
    s.available_date,
    s.available_basis,
    CASE WHEN s.available_date IS NULL THEN 'rcept_dt_missing' END AS reject_reason
FROM src s
QUALIFY row_number() OVER (
    PARTITION BY s.corp_code, s.bsns_year, s.reprt_code, s.se
    ORDER BY s.available_date NULLS LAST, s.rcept_no NULLS LAST, s.stlm_dt NULLS LAST,
             s.istc_totqy_shr NULLS LAST, s.tesstk_co_shr NULLS LAST,
             s.distb_stock_co_shr NULLS LAST) = 1
