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
-- PIT(플랜 D1, 사용자 10-23 "신선도 최대한"): 가용일 = 수집 시각으로 행마다 센 '지식 세션'이고 랙은 1 세션이다.
--   장 시작(09:00 KST = 00:00 UTC) 전에 받은 값은 직전 세션 마감 뒤 지식이라 그날 바로 쓰고, 그 뒤에 받은 값은
--   다음 세션부터 쓴다 — 수집 시각의 UTC 날짜(known_date) 이하 마지막 거래일이 지식 세션이다.
--   수집 시각이 없으면 수집일(stage measured, 보수). known_date 가 이 판 달력의 마지막 거래일 뒤면(휴장인지 아직
--   안 들어온 날인지 가를 수 없다) 당기지 않고 known_date 그대로 둔다. 같은 규칙을 EG3 가 독립 재계산으로 대조한다.
-- base_date 는 WISE 기준일(DT) 라벨이다 — 하루 중 언제 받아도 전 거래일이라 값이 언제 바뀌었는지의 증거가 아니다(N-53).
WITH cal AS (SELECT max(date) AS last_session FROM trading_calendar),
known AS (
    SELECT s.*, CAST(s.fetched_at_utc AS DATE) AS known_date FROM stg_consensus_matrix s
),
pit AS (
    SELECT k.*,
           CASE WHEN k.fetched_at_utc IS NULL THEN k.fetched_date
                WHEN k.known_date > c.last_session THEN k.known_date
                ELSE coalesce(t.date, k.known_date)
           END AS pit_date,
           CASE WHEN k.fetched_at_utc IS NULL THEN 'measured' ELSE 'derived' END AS pit_basis
    FROM known k
    CROSS JOIN cal c
    ASOF LEFT JOIN trading_calendar t ON k.known_date >= t.date
)
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
    fetched_at_utc,
    pit_date                    AS available_date,
    pit_basis                   AS available_basis,
    CAST(NULL AS VARCHAR)       AS reject_reason      -- 격리 사유 없음(REJECT_REASONS=()) — 빌드 규약상 컬럼은 있어야 한다
FROM pit
