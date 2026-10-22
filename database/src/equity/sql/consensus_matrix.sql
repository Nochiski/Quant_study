-- consensus_matrix (S26) — WISE 컨센서스 매트릭스(c1050001 T4) 수집일별 판. 플랜 `2026-10-22-equity-wise-fields` T1.
-- grain (ticker, fetched_date, target_period, acc_cd, lookback_idx) · date_axis year(fetched_date).
-- 입력은 stage `stg_consensus_matrix` 하나 — 계정 9 × 비교 시점(lookback) 5 × 결산기 3.
--
-- 값은 stage 그대로다(억원·원·배·% — `acc_nm` 의 괄호가 단위). 1주·1개월·3개월·1년 전 값은 WISE 가 그 수집일에
-- 함께 준 비교값이지 우리가 이력을 늦춰 만든 값이 아니다.
--
-- fy_rank — 수집 달 이후로 끝나는 결산기를 가까운 순서로 1, 2, … (지난 결산기는 NULL). compat(`mappings.py` per)·
-- fi 엔진(`v3_zscore._consensus_pair`)이 각자 쓰던 "수집 달 이후 가장 가까운 기" 를 여기 한 번 적는다(fy_rank = 1).
--
-- PIT: stage 가 낸 available_date(= fetched_date)·basis(measured)를 그대로 나른다. base_date 는 WISE 기준일(DT)
-- 라벨이다 — 하루 중 언제 받아도 전 거래일이라 값이 언제 바뀌었는지의 증거가 아니다(DECISIONS N-53).
SELECT
    ticker,
    fetched_date,
    target_period,
    acc_cd,
    lookback_idx,
    lookback,
    acc_nm,
    base_date,
    value,
    CASE WHEN target_period >= strftime(fetched_date, '%Y%m')
         THEN dense_rank() OVER (PARTITION BY ticker, fetched_date, target_period >= strftime(fetched_date, '%Y%m')
                                 ORDER BY target_period)
    END                         AS fy_rank,
    available_date,
    available_basis,
    CAST(NULL AS VARCHAR)       AS reject_reason      -- 격리 사유 없음(REJECT_REASONS=()) — 빌드 규약상 컬럼은 있어야 한다
FROM stg_consensus_matrix
