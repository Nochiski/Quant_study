-- holder_daily (S15) — 지분 공시 스트림. DESIGN v1.2 §4-5 · GATES §3-⑮ · WORKFLOW §3-1 S15.
-- grain (`rcept_no`, `repror`, `src`) · receipt_axis(`substr(rcept_no,1,4)`).
--
-- 모집단 = `stg_holder_elestock`(임원·주요주주 소유상황) ∪ `stg_holder_majorstock`(5% 대량보유)
-- **전건**. 두 원천은 축이 같고(접수 1건 × 보고자 1명) 겹치지 않는 보고 제도라 dedup 하지 않고
-- `src` 를 키에 넣어 나란히 싣는다 — 같은 (rcept_no, repror) 가 양쪽에 있어도 서로 다른 행이다.
--
-- **네 숫자축 통합**: 두 원천의 컬럼명은 다르지만 축은 같다 — 보유수량 / 수량 증감 / 지분율 /
-- 지분율 증감. 통합 이름(`qty_shr`·`qty_change_shr`·`rate_pct`·`rate_change_pct`)으로 싣고
-- 원천 고유 컬럼은 반대편을 NULL 로 둔다(elestock: 직위·등기 여부·주요주주 여부 / majorstock:
-- 보고구분·보고사유·주요계약 수량·비율). **UNION ALL 의 타입은 원천 DECIMAL 을 그대로 승격시킨다**
-- — 명시 캐스팅을 쓰지 않는다(S12 선례: 폭을 못 박으면 원장 정밀도가 잘린다).
--
-- `qty_prev_shr`·`rate_prev_pct` 는 "지분 전"(DESIGN §4-5 "지분 전/후")이다: 보고 후 값에서
-- 증감을 뺀다. 증감이 NULL 이면 전 값도 NULL — 0 으로 보간하지 않는다.
--
-- **판본 dedup**: 두 stage 테이블은 `write_mode='append_only'` · `key_unique=False`
-- ("한 접수에 보고자 행이 하나뿐인지 미실측", rules_dart.py) 라 같은 자연키가 재수집 판본만큼
-- 쌓일 수 있다. grain 이 (rcept_no, repror, src) 이므로 **접기 전에 세면** 산출이 부풀고 EG1 이
-- 어긋난다(S11 서버 1차 빌드 delta −26 과 같은 함정). first_write_wins(`observed_date` 최소,
-- EG6-P04 규약)로 접고 동률은 투영 컬럼 전체로 깬다 — 판본끼리 payload 가 같으면 어느 쪽을
-- 골라도 산출이 같아 EG5a 가 선다. 접힌 수는 `n_source_rows` 가 행마다 남긴다(절단본 전부 1).
--
-- PIT: `available_date = rcept_dt`(derived). stage 가 `rcept_dt` 를 required 로 두므로 결측은
-- 구조적으로 오지 않지만, 오면 available_date 를 가질 수 없으므로 `rcept_dt_missing` 로 격리한다.
WITH ele AS (
    SELECT e.rcept_no,
           'elestock'                                                   AS src,
           e.repror,
           e.corp_code,
           e.rcept_dt,
           e.isu_exctv_ofcps                                            AS ofcps,
           e.isu_exctv_rgist_at                                         AS exec_rgist,
           e.isu_main_shrholdr                                          AS main_shrholdr,
           NULL                                                         AS report_tp,
           NULL                                                         AS report_resn,
           e.sp_stock_lmp_cnt_shr                                       AS qty_shr,
           e.sp_stock_lmp_irds_cnt_shr                                  AS qty_change_shr,
           e.sp_stock_lmp_rate_pct                                      AS rate_pct,
           e.sp_stock_lmp_irds_rate_pct                                 AS rate_change_pct,
           NULL                                                         AS ctr_qty_shr,
           NULL                                                         AS ctr_rate_pct,
           e.observed_date
    FROM stg_holder_elestock e
),
mjr AS (
    SELECT m.rcept_no,
           'majorstock'                                                 AS src,
           m.repror,
           m.corp_code,
           m.rcept_dt,
           NULL                                                         AS ofcps,
           NULL                                                         AS exec_rgist,
           NULL                                                         AS main_shrholdr,
           m.report_tp,
           m.report_resn,
           m.stkqy_shr                                                  AS qty_shr,
           m.stkqy_irds_shr                                             AS qty_change_shr,
           m.stkrt_pct                                                  AS rate_pct,
           m.stkrt_irds_pct                                             AS rate_change_pct,
           m.ctr_stkqy_shr                                              AS ctr_qty_shr,
           m.ctr_stkrt_pct                                              AS ctr_rate_pct,
           m.observed_date
    FROM stg_holder_majorstock m
),
pool AS (
    SELECT * FROM ele
    UNION ALL
    SELECT * FROM mjr
),
folded AS (
    SELECT p.*,
           count(*) OVER (PARTITION BY p.rcept_no, p.repror, p.src)      AS n_source_rows
    FROM pool p
    QUALIFY row_number() OVER (
        PARTITION BY p.rcept_no, p.repror, p.src
        ORDER BY p.observed_date NULLS LAST, p.rcept_dt NULLS LAST, p.corp_code NULLS LAST,
                 p.ofcps NULLS LAST, p.exec_rgist NULLS LAST, p.main_shrholdr NULLS LAST,
                 p.report_tp NULLS LAST, p.report_resn NULLS LAST,
                 p.qty_shr NULLS LAST, p.qty_change_shr NULLS LAST,
                 p.rate_pct NULLS LAST, p.rate_change_pct NULLS LAST,
                 p.ctr_qty_shr NULLS LAST, p.ctr_rate_pct NULLS LAST) = 1
)
SELECT f.rcept_no,
       f.src,
       f.repror,
       f.corp_code,
       f.rcept_dt,
       f.ofcps,
       f.exec_rgist,
       f.main_shrholdr,
       f.report_tp,
       f.report_resn,
       f.qty_shr,
       f.qty_change_shr,
       f.qty_shr - f.qty_change_shr                                     AS qty_prev_shr,
       f.rate_pct,
       f.rate_change_pct,
       f.rate_pct - f.rate_change_pct                                   AS rate_prev_pct,
       f.ctr_qty_shr,
       f.ctr_rate_pct,
       f.n_source_rows,
       f.rcept_dt                                                       AS available_date,
       'derived'                                                        AS available_basis,
       CASE WHEN f.rcept_dt IS NULL THEN 'rcept_dt_missing' END         AS reject_reason
FROM folded f
