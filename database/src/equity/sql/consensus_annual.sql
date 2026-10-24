-- consensus_annual (S26) — WISE 연간 실적·추정 표(c1050001 T2Y) 수집일별 판. 플랜 `2026-10-22-equity-wise-fields` T1.
-- grain (ticker, fetched_date, period_label) · date_axis year(fetched_date). 입력은 stage `stg_consensus_annual` 하나.
--
-- 값은 stage 그대로다(매출·영업이익·순이익 억원, EPS·BPS 원, PER·PBR 배, ROE %). period_kind 는 A(실적)·E(추정).
-- 그날 수집분에 당해 12월기 추정(E)이 있는지가 곧 "추정치를 지금 갖고 있나" 라서 수집일 판을 접지 않는다 —
-- 추정은 발표 뒤 약 3개월이면 빠진다(DECISIONS U1 원인).
--
-- PIT(플랜 D1, 사용자 10-23 "신선도 최대한"): 가용일 = 수집 시각으로 행마다 센 '지식 세션'이고 랙은 1 세션이다.
--   장 시작(09:00 KST = 00:00 UTC) 전에 받은 값은 직전 세션 마감 뒤 지식이라 그날 바로 쓰고, 그 뒤에 받은 값은
--   다음 세션부터 쓴다 — 수집 시각의 UTC 날짜(known_date) 이하 마지막 거래일이 지식 세션이다.
--   수집 시각이 없으면 수집일(stage measured, 보수). known_date 가 이 판 달력의 마지막 거래일 뒤면(휴장인지 아직
--   안 들어온 날인지 가를 수 없다) 당기지 않고 known_date 그대로 둔다. 같은 규칙을 EG3 가 독립 재계산으로 대조한다.
WITH cal AS (SELECT max(date) AS last_session FROM trading_calendar),
known AS (
    SELECT s.*, CAST(s.fetched_at_utc AS DATE) AS known_date FROM stg_consensus_annual s
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
    period_label,
    period,
    period_kind,
    fs_basis,
    revenue,
    yoy_pct,
    op,
    ni,
    eps,
    bps,
    per,
    pbr,
    roe_pct,
    ev_ebitda,
    fetched_at_utc,
    pit_date                    AS available_date,
    pit_basis                   AS available_basis,
    CAST(NULL AS VARCHAR)       AS reject_reason      -- 격리 사유 없음(REJECT_REASONS=())
FROM pit
