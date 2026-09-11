-- coverage_daily (S24) — WISE 애널리스트 커버 이력. 사용자 요청 2026-09-10.
-- grain (ticker, date) · date_axis. 입력은 stage WISE 3표뿐이다
-- (`stg_consensus_monthly`·`stg_analyst_summary`·`stg_wise_coverage`).
--
-- **이 표가 있는 이유**: 원장의 커버 판정은 `ws_coverage` 한 장뿐이고 그것은 `upsert` 라
-- **현재 상태만** 남는다(stage `stg_wise_coverage.status_current`). 그래서 "이 종목이 언제부터
-- 커버됐나 / 언제 커버가 끊겼나" 를 물을 축이 없다. 스냅샷 원문(`ws_raw` → 월별 컨센서스)은
-- 날짜축을 갖고 있으므로, 스냅샷 날마다 커버 여부를 세워 **이력 격자**로 만든다.
--
-- 격자 = (WISE 요청 유니버스 종목) × (우리가 스냅샷을 찍은 날). 종목 축에 `stg_wise_coverage`
-- 를 넣는 것이 중요하다 — 커버가 없는 종목은 스냅샷 표에 행이 아예 없거나 전값 NULL 이라
-- 관측 표만으로 격자를 세우면 `covered=false` 인 칸 자체가 사라져 커버율이 조용히 1 이 된다
-- (GATES §5-C6 생존편향과 같은 함정).
--
-- **`covered` 의 정의는 수집기 정본을 그대로 옮긴다**(`backfill_wise.is_covered`): cF5001 의
-- EPS·매출 추정 또는 목표주가 중 **하나라도 값이 있으면** 커버다. stage 에서는 그 세 신호가
-- `stg_consensus_monthly` 의 `consensus`(metric eps·revenue) 와 `target_price_krw` 다.
-- `metric='parse_failed'`(파서가 값 칸을 못 읽은 관측)도 커버로 센다 — 오판 비용이 비대칭이라
-- (false-none = 영구 결측) 수집기가 파싱 불능을 covered 로 두는 것과 같은 규약이다.
-- 절단본 교차확인: 이 술어가 `stg_wise_coverage.status_current` 7종목을 전건 재현한다
-- (covered 5 · none 2). 그 대조는 EG3_coverage_daily 가 기록형으로 매 빌드 센다.
--
-- **모든 이력 컬럼은 그 날짜까지만 본다(PIT)**. `first_covered_date`·`last_covered_date`·
-- `streak_days`·`first_estimate_month` 는 전 구간 집계가 아니라 `date` 이하 누적이다 — 종목별
-- 상수로 실으면 "2026-09-02 에 커버가 끊겼다" 를 2026-01 행이 이미 아는 look-ahead 가 된다.
--
-- `first_estimate_month`(월 단위) — 우리 스냅샷 이력은 2026-09-01 부터라 그 이전의 커버 시작은
-- 스냅샷 날짜축으로 알 수 없다. 대신 월별 컨센서스 시계열(`obs_date` = WISE 관측 라벨
-- '2025/08/29')의 **첫 non-null 달**이 그 종목에 추정치가 처음 붙은 달을 말해 준다. 날짜축이
-- 아니라 달축이므로 `first_estimate_basis='monthly'` 로 기준을 밝히고, 재료가 없으면 'none' 이다
-- (DESIGN §1 basis 규약 — 값 축과 지식 축을 섞지 않는다).
--
-- PIT: `available_date = date`(= WISE 화면 수집일), basis 'measured' — stage `lag_known=true`
-- 이고 `stg_consensus_monthly`·`stg_analyst_summary` 가 이미 measured 로 확정한 축이다.
WITH tick AS (
    -- 종목 축 = WISE 요청 유니버스(커버 + 무커버) ∪ 스냅샷에 실제로 나타난 종목
    SELECT DISTINCT ticker FROM stg_wise_coverage
    UNION
    SELECT DISTINCT ticker FROM stg_consensus_monthly
    UNION
    SELECT DISTINCT ticker FROM stg_analyst_summary
),
snap AS (
    -- 날짜 축 = 우리가 스냅샷을 찍은 날(`fetched_date`). 달력 거래일이 아니다.
    SELECT DISTINCT fetched_date AS date FROM stg_consensus_monthly
    UNION
    SELECT DISTINCT fetched_date AS date FROM stg_analyst_summary
),
grid AS (
    SELECT t.ticker, s.date FROM tick t CROSS JOIN snap s
),
cov AS (
    -- 그날 그 종목이 커버였는가 — `backfill_wise.is_covered` 와 같은 술어(위 주석)
    SELECT ticker, fetched_date AS date
    FROM stg_consensus_monthly
    WHERE consensus IS NOT NULL OR target_price_krw IS NOT NULL OR metric = 'parse_failed'
    GROUP BY ticker, fetched_date
),
est AS (
    -- 그 스냅샷이 보여 준 "추정치가 붙은 첫 달". 값이 있는 관측만 본다.
    SELECT ticker, fetched_date AS date,
           CAST(min(date_trunc('month', obs_date)) AS DATE) AS est_month
    FROM stg_consensus_monthly
    WHERE consensus IS NOT NULL AND obs_date IS NOT NULL
    GROUP BY ticker, fetched_date
),
cell AS (
    SELECT g.ticker,
           g.date,
           (c.ticker IS NOT NULL) AS covered,
           a.analyst_count,
           e.est_month
    FROM grid g
    LEFT JOIN cov c ON c.ticker = g.ticker AND c.date = g.date
    LEFT JOIN est e ON e.ticker = g.ticker AND e.date = g.date
    -- (ticker, fetched_date) 는 stage `key_unique=True` 라 팬아웃하지 않는다
    LEFT JOIN stg_analyst_summary a ON a.ticker = g.ticker AND a.fetched_date = g.date
),
run AS (
    SELECT c.*,
           min(CASE WHEN c.covered THEN c.date END) OVER w  AS first_covered_date,
           max(CASE WHEN c.covered THEN c.date END) OVER w  AS last_covered_date,
           min(c.est_month) OVER w                          AS first_estimate_month,
           -- gaps-and-islands: 무커버 행까지 포함한 누적 개수가 섬 번호다
           sum(CASE WHEN c.covered THEN 0 ELSE 1 END) OVER w AS gap_seq
    FROM cell c
    WINDOW w AS (PARTITION BY c.ticker ORDER BY c.date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
    r.ticker,
    r.date,
    r.covered,
    r.first_covered_date,
    r.last_covered_date,
    -- 연속 커버 스냅샷 수. 섬의 첫 행은 무커버 행이므로(섬 번호 > 0) 그 한 칸을 뺀다 —
    -- 무커버 행 자신은 섬 안 1번이라 0 이 되고, 처음부터 커버인 구간(섬 0)은 그대로 센다.
    CAST(row_number() OVER (PARTITION BY r.ticker, r.gap_seq ORDER BY r.date)
         - CASE WHEN r.gap_seq > 0 THEN 1 ELSE 0 END AS BIGINT)  AS streak_days,
    r.analyst_count,
    r.first_estimate_month,
    CASE WHEN r.first_estimate_month IS NULL THEN 'none' ELSE 'monthly' END
                                                                 AS first_estimate_basis,
    r.date                                                       AS available_date,
    'measured'                                                   AS available_basis,
    CAST(NULL AS VARCHAR)                                        AS reject_reason
FROM run r
