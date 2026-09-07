-- treasury_stock (S16) — 정기보고서 「자기주식 취득·처분 현황」. DESIGN v1.2 §4-5 · GATES §3-⑯.
-- grain (corp_code, bsns_year, reprt_code, acqs_mth1, acqs_mth2, acqs_mth3, stock_knd) ·
-- receipt_axis · available_date = 접수일(stage derived 값 그대로).
--
-- 모집단 = `stg_tesstk` 의 **비집계 행**(`row_kind <> 'aggregate'`). 집계행은 취득방법 3축이
-- ('총계','총계','총계') 인 원장 총계 행이고, 그 값은 산출에 굽지 않는다 — 비집계 행의 합과
-- 같은지는 EG3_treasury_stock 이 원장(입력 뷰)을 다시 읽어 기록형으로 잰다(GATES FX-4B-001).
-- 원장에는 '소계' 행(직접취득 소계 · 신탁계약 소계)도 `row_kind='detail'` 로 실려 있으므로
-- 단순 합산은 이중계상이다 — 합산 축은 `acqs_mth3 <> '소계'` 인 잎 행뿐이다. stage 의
-- `_row_kind("acqs_mth1")`(rules_dart.py:291)은 1축만 보므로 3축이 '소계' 인 행을 못 잡는다.
-- 아래 마커 줄 앞까지가 모집단 정의이고 `rules_s16.py:pool_sql` 이 EG1 우변으로 재사용한다.
--
-- 컬럼은 DESIGN §4-5 의 "취득·처분·소각" + 기초·기말 잔량:
--   begin_shr    `bsis_qy_shr`          기초수량
--   acquired_shr `change_qy_acqs_shr`   변동수량 취득
--   disposed_shr `change_qy_dsps_shr`   변동수량 처분
--   retired_shr  `change_qy_incnr_shr`  변동수량 소각
--   end_shr      `trmend_qy_shr`        기말수량
--
-- **판본 중복**: 같은 접수 안에서도 (취득방법 3축, 주식종류) 가 되풀이되는 행이 있다 — 절단본
-- 58그룹(전부 `stock_knd='-'` · 값 전 컬럼 결측 · `row_hash` 만 다름). grain 에 `rcept_no` 도
-- `row_hash` 도 없으므로 S11 선례대로 QUALIFY 로 한 행만 남기고(값 있는 행 우선 = 값 컬럼
-- NULLS LAST), 접힌 수는 산출 컬럼 `n_src_rows` 와 EG3 기록형 metric 이 남긴다.
WITH src AS (
    SELECT corp_code,
           bsns_year,
           reprt_code,
           acqs_mth1,
           acqs_mth2,
           acqs_mth3,
           stock_knd,
           rcept_no,
           stlm_dt,
           bsis_qy_shr,
           change_qy_acqs_shr,
           change_qy_dsps_shr,
           change_qy_incnr_shr,
           trmend_qy_shr,
           available_date,
           available_basis,
           count(*) OVER (PARTITION BY corp_code, bsns_year, reprt_code,
                                       acqs_mth1, acqs_mth2, acqs_mth3, stock_knd) AS n_src_rows
    FROM stg_tesstk
    WHERE row_kind <> 'aggregate'
)
-- ==== eg1: 모집단 정의 끝. 위 CTE 만으로 EG1 우변이 선다(rules_s16.py:pool_sql) ====
SELECT
    s.corp_code,
    s.bsns_year,
    s.reprt_code,
    s.acqs_mth1,
    s.acqs_mth2,
    s.acqs_mth3,
    s.stock_knd,
    s.rcept_no,
    s.stlm_dt,
    s.bsis_qy_shr                                                AS begin_shr,
    s.change_qy_acqs_shr                                         AS acquired_shr,
    s.change_qy_dsps_shr                                         AS disposed_shr,
    s.change_qy_incnr_shr                                        AS retired_shr,
    s.trmend_qy_shr                                              AS end_shr,
    s.n_src_rows,
    s.available_date,
    s.available_basis,
    CASE WHEN s.available_date IS NULL THEN 'rcept_dt_missing' END AS reject_reason
FROM src s
QUALIFY row_number() OVER (
    PARTITION BY s.corp_code, s.bsns_year, s.reprt_code,
                 s.acqs_mth1, s.acqs_mth2, s.acqs_mth3, s.stock_knd
    ORDER BY s.available_date NULLS LAST, s.rcept_no NULLS LAST, s.stlm_dt NULLS LAST,
             s.trmend_qy_shr NULLS LAST, s.bsis_qy_shr NULLS LAST,
             s.change_qy_acqs_shr NULLS LAST, s.change_qy_dsps_shr NULLS LAST,
             s.change_qy_incnr_shr NULLS LAST) = 1
