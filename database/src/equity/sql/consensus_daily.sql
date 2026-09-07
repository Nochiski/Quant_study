-- consensus_daily (S17) — 컨센서스 관측점별 최초 관측. DESIGN v1.2 §4-6 · GATES v1.0 §3 ⑲ ·
-- EG6-P01/P02 · EG8-P07 · EG9-P05 · FX-5-001…006.
-- grain (ticker, obs_month, target_period, metric, src) · 파티션 year(obs_month)(PIT 축 아님).
--
-- 두 원천을 접지 않고 나란히 싣는다(src 가 grain 에 있다, FX-5-004):
--   wise = stg_consensus_monthly  — 월말 관측(obs_date) × 수집 판본(fetched_date). 같은 관측점을
--          여러 fetched_date 가 덮어쓰므로 min(fetched_date) 판본이 PIT (HANDOFF §3 규약,
--          EG6-P01). 뒤 판본이 값을 바꿔도 최초 관측은 바뀌지 않는다 — 그것이 이 테이블의 존재
--          이유다(메모리 "consensus-revision-db": v3 는 2027E·2028E 이력을 매일 덮어쓴다).
--   v3   = stg_v3_revision_daily  — 일별 wide 스냅샷(동결 사본 2026-04-03~09-02). 8지표로 unpivot
--          하고 그 달에 그 지표가 처음 관측된 날(min date)의 행을 고른다(EG6-P02).
--
-- PIT: 두 원천 모두 stage 가 이미 낸 available_date/available_basis 를 그대로 나른다.
--   wise: available_date = fetched_date(measured). 고른 행이 min(fetched_date) 행이라 EG6-P01 등식이 선다.
--   v3  : available_date = collected_date(measured), NULL 이면 date(default) + coverage_degraded
--         (stage AvailableRule.fallback_column, FX-5-005). equity 는 재계산하지 않는다.
--
-- 단위 — v3 에는 단위 컬럼이 없어 카탈로그 지식으로 부여한다(STAGE_HANDOFF §4 "WISE T2Y/T2Q
--   값(억원·원은 카탈로그 지식)" · rules_wise._periodic_columns 주석):
--   revenue·op·ni = 억원 · eps·bps = 원 · per·pbr = 배 · roe_pct = %.
--   wise 는 stage unit 컬럼(데이터 값, 스케일 변환 금지)을 그대로 나른다.
--
-- target_period 어휘 = WISE 계열 정본 'YYYYMM' (stg_consensus_monthly.pkey ·
--   stg_consensus_matrix.target_period · stg_consensus_annual.period 전부 6자리). v3 만
--   'YYYY/MM' 라벨 표기라 슬래시를 지워 같은 어휘로 맞춘다 — 맞추지 않으면 EG8-P07(v3 ⋈ wise)
--   이 공허하게 통과한다. 그래도 6자리가 아니면 격리 target_period_invalid (EG7 격리형).
--
-- obs_month = 관측일의 월 첫날. 날짜 축이 아니다(DESIGN §4-6) — PIT 절단은 available_date 로만
--   한다. obs_month 로 자르면 wise 구간에서 2026-09-01 에야 알 수 있던 2025-08 관측이 2025-08 에
--   보이는 look-ahead 가 된다(EG-C ⑨).
WITH wise_obs AS (
    SELECT m.ticker,
           CAST(date_trunc('month', m.obs_date) AS DATE) AS obs_month,
           m.target_period,
           m.metric,
           m.obs_date,
           m.fetched_date,
           m.unit,
           m.consensus       AS est_mean,
           m.consensus_min   AS est_min,
           m.consensus_max   AS est_max,
           m.available_date,
           m.available_basis
    FROM stg_consensus_monthly m
),
-- unpivot 대응표 (FX-5-006). 지표 8 = rules_s17.V3_METRICS 와 순서까지 같아야 한다
-- (test_v3_unpivot_대응표는_rules_선언과_SQL이_같다). opinion 은 여기 없다 — S18 의견 축이다.
v3_long AS (
    SELECT ticker, date, replace(target_period, '/', '') AS target_period,
           'revenue' AS metric, '억원' AS unit, revenue AS est_mean,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE revenue IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'op', '억원', op,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE op IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'ni', '억원', ni,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE ni IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'eps', '원', eps,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE eps IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'bps', '원', bps,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE bps IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'per', '배', per,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE per IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'pbr', '배', pbr,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE pbr IS NOT NULL
  UNION ALL
    SELECT ticker, date, replace(target_period, '/', ''), 'roe_pct', '%', roe_pct,
           coverage_degraded, available_date, available_basis
    FROM stg_v3_revision_daily WHERE roe_pct IS NOT NULL
),
v3_obs AS (
    SELECT ticker,
           CAST(date_trunc('month', date) AS DATE) AS obs_month,
           target_period,
           metric,
           date AS obs_date,
           unit,
           est_mean,
           coverage_degraded,
           available_date,
           available_basis
    FROM v3_long
),
-- ==== eg1: 여기까지가 모집단 정의다. rules_s17.population_sql() 이 이 앞부분을 그대로 재사용해
--           EG1 우변(원천별 distinct 키 수)을 센다 — 모집단 정의를 두 곳에 두지 않는다.
wise_pick AS (
    SELECT * EXCLUDE (rn) FROM (
        SELECT w.*, row_number() OVER (
                   PARTITION BY w.ticker, w.obs_month, w.target_period, w.metric
                   ORDER BY w.fetched_date, w.obs_date) AS rn
        FROM wise_obs w)
    WHERE rn = 1
),
v3_pick AS (
    SELECT * EXCLUDE (rn) FROM (
        SELECT v.*, row_number() OVER (
                   PARTITION BY v.ticker, v.obs_month, v.target_period, v.metric
                   ORDER BY v.obs_date) AS rn
        FROM v3_obs v)
    WHERE rn = 1
),
picked AS (
    SELECT ticker, obs_month, target_period, metric, 'wise' AS src, obs_date,
           est_mean, est_min, est_max, unit, FALSE AS coverage_degraded,
           available_date, available_basis
    FROM wise_pick
  UNION ALL
    -- v3 는 min/max 를 주지 않는다(DESIGN §4-6) — 0 으로 채우지 않고 결측으로 둔다(원칙 ④).
    SELECT ticker, obs_month, target_period, metric, 'v3', obs_date,
           est_mean, NULL, NULL, unit, coverage_degraded,
           available_date, available_basis
    FROM v3_pick
)
SELECT p.ticker,
       p.obs_month,
       p.target_period,
       p.metric,
       p.src,
       p.obs_date,
       p.est_mean,
       p.est_min,
       p.est_max,
       p.unit,
       p.coverage_degraded,
       p.available_date,
       p.available_basis,
       CASE WHEN NOT regexp_matches(p.target_period, '^[0-9]{6}$')
            THEN 'target_period_invalid' END AS reject_reason
FROM picked p
