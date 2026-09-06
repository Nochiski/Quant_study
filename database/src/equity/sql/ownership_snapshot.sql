-- ownership_snapshot (S15) — 최대주주·특수관계인 지분 현황. DESIGN v1.2 §4-5 · GATES §3-⑯.
-- grain (`corp_code`, `bsns_year`, `reprt_code`, `nm`, `stock_knd`) · receipt_axis.
--
-- 모집단 = `stg_hyslr` 의 **비집계 행**(`row_kind <> 'aggregate'`). 집계행은 stage 가
-- `nm ∈ {합계, 계, 총계, 소계}` 로 표시한 원장 소계이고(rules_dart.py `_row_kind('nm')`),
-- 같이 실으면 무필터 SUM 이 2중 계상된다(DESIGN §4-5 "row_kind='aggregate' 제외").
-- 절단본 995행 = 집계 169 + 비집계 826.
--
-- **grain 에 `stock_knd` 를 넣는다(DESIGN §4-5 초안 정정 — GATES §9 기록)**: 초안 grain
-- (corp, bsns_year, reprt_code, `nm`) 은 유일하지 않다. 한 주주가 보통주·우선주를 나눠 신고하면
-- 같은 이름으로 2행이 온다(절단본: 양홍석 2015 보통주 6.92% / 우선주 0.00% — 비집계 826행이
-- 이름 축으로는 733개로 접힌다, 충돌 93행). `stock_knd` 는 stage 자연키에도 들어 있고
-- (rules_dart.py `natural_key`), 넣으면 절단본에서 826 = 826 으로 유일해진다. 접으면 종류주
-- 지분율이 조용히 사라진다.
--
-- **판본·동명 dedup**: `stg_hyslr` 은 append_only · `key_unique=False`("동명이인·같은 이름의
-- 법인 주주가 갈리는지 미실측") 라 같은 자연키가 여러 행일 수 있다(서버 1차 풀 빌드 G6: 13행).
-- grain 이 유일해야 하므로 first_write_wins(`observed_date` 최소) → 원장 PK `row_hash` 순으로
-- 한 행만 남기고, 접힌 수를 `n_source_rows` 로 남긴다(절단본 전부 1). 이 접힘은 재수집 판본이
-- 아니라 **서로 다른 원장 행**일 수 있으므로 `EG3_ownership_snapshot.n_grain_folded` 가
-- 기록형으로 감시한다 — 0 이 아니면 그만큼 지분율이 산출에서 빠졌다는 뜻이다.
--
-- PIT: `available_date = rcept_dt`(derived) — stage 가 접수번호로 조회해 채운 컬럼이다
-- (`stg_hyslr` 에는 `rcept_dt` 컬럼이 없다). 내용일은 `stlm_dt`(결산기준일)이고 EG2-P02 가
-- `available_date >= stlm_dt` 를 본다(절단본 위반 0).
--
-- 격리 2종: `rcept_dt_missing`(available_date 없음 = PIT 축 부재) · `pct_out_of_range`
-- (지분율이 [`pct_min`, `pct_max`] 밖 — EG7-P07, 상한은 `_const`).
WITH detail AS (
    SELECT h.corp_code,
           h.bsns_year,
           h.reprt_code,
           h.nm,
           h.stock_knd,
           h.rcept_no,
           h.relate,
           h.stlm_dt,
           h.bsis_posesn_stock_co_shr                                   AS bsis_qty_shr,
           h.bsis_posesn_stock_qota_rt_pct                              AS bsis_rate_pct,
           h.trmend_posesn_stock_co_shr                                 AS trmend_qty_shr,
           h.trmend_posesn_stock_qota_rt_pct                            AS trmend_rate_pct,
           h.rm,
           h.available_date,
           h.observed_date,
           h.row_hash
    FROM stg_hyslr h
    WHERE h.row_kind IS DISTINCT FROM 'aggregate'
),
folded AS (
    SELECT d.*,
           count(*) OVER (PARTITION BY d.corp_code, d.bsns_year, d.reprt_code,
                                       d.nm, d.stock_knd)               AS n_source_rows
    FROM detail d
    QUALIFY row_number() OVER (
        PARTITION BY d.corp_code, d.bsns_year, d.reprt_code, d.nm, d.stock_knd
        ORDER BY d.observed_date NULLS LAST, d.row_hash NULLS LAST) = 1
)
SELECT f.corp_code,
       f.bsns_year,
       f.reprt_code,
       f.nm,
       f.stock_knd,
       f.rcept_no,
       f.relate,
       f.stlm_dt,
       f.bsis_qty_shr,
       f.bsis_rate_pct,
       f.trmend_qty_shr,
       f.trmend_rate_pct,
       f.rm,
       f.n_source_rows,
       f.available_date,
       'derived'                                                        AS available_basis,
       CASE WHEN f.available_date IS NULL THEN 'rcept_dt_missing'
            WHEN f.bsis_rate_pct   < c.pct_min OR f.bsis_rate_pct   > c.pct_max
              OR f.trmend_rate_pct < c.pct_min OR f.trmend_rate_pct > c.pct_max
            THEN 'pct_out_of_range' END                                 AS reject_reason
FROM folded f
CROSS JOIN _const c
