-- opinion_broker_daily (S18) — 제공처(브로커)별 투자의견·목표주가.
-- DESIGN v1.2 §4-6 · GATES v1.0 §3 ㉑ · FX-5-008.
-- grain (ticker, fetched_date, broker, opinion_date) = stage 자연키 그대로 1:1. 원천은
-- stg_analyst_broker(WISE c1010001 cTB24) 하나뿐이라 판본 선택이 없다(EG6 skip(no_multi_version)).
--
-- 값 컬럼은 stage 를 그대로 나른다. 등급은 **원문 보존 + stage 분류 계승** 이다 —
--   opinion·prev_opinion            : 제공처 표기 원문(BUY / Buy / 매수 / 보유 / Outperform …)
--   opinion_class·prev_opinion_class: stage rules_wise._OPINION_CLASS 가 접은 폐쇄 어휘
--                                     {buy, hold, sell, other} + 공란 NULL
-- equity 는 이 어휘를 다시 계산하지 않고(§1 "게이트 술어를 산출식으로 재계산" 의 대칭 금지)
-- 폐쇄만 판정한다(EG3_opinion_broker_daily.n_opinion_class_outside_vocab).
--
-- 파생 하나 — prev_opinion_date (FX-5-008):
--   WISE 는 prev_opinion·prev_target_price_krw·change_pct 를 주면서 **그 직전 의견이 언제 것인지**는
--   주지 않는다. 간격을 모르면 change_pct 는 해석할 수 없다(하루 만의 20% 하향과 반년에 걸친
--   20% 하향이 같은 값으로 보인다). 그래서 우리 관측 이력에서 같은 (ticker, broker) 의
--   **직전 opinion_date** 를 되찾아 싣는다. 되찾지 못하면 NULL 이고, 그 NULL 이 곧
--   "이 change_pct 는 기간 없이 읽으면 안 된다" 는 근거다.
--   PIT 폐쇄: 후보를 fetched_date <= 이 행의 fetched_date 로 묶는다. 이 제약이 없으면 나중에
--   긁은 이력으로 과거 행을 채우는 look-ahead 가 된다.
--   동반 컬럼 prev_opinion_date_available_date (DESIGN §3 · EG2-P05): 구성 행들
--   (그 prev_opinion_date 를 실제로 실어 나른 stage 행들 중 이 행의 fetched_date 이하)의
--   available_date 최댓값과 자기 행 available_date 의 max. 지름길로 fetched_date 를 복사하지 않고
--   구성 행에서 집계해 둔다 — EG2-P05 가 그 항등을 독립 재계산으로 확인한다.
--
-- PIT: available_date = fetched_date(우리가 화면을 긁은 날, stage lag_known=true) · basis
--      'measured'. 내용일 축은 opinion_date(제공처 최종일자)이고 EG2-P02 가 available >= 내용일을 본다.
--
-- 격리(EG7 격리형):
--   nonpositive_target_price = target_price_krw <= 0 ∨ prev_target_price_krw <= 0
--   opinion_date_after_fetch = opinion_date > fetched_date (관측일 역전 — 긁은 날보다 뒤의
--                              의견일. 살려 두면 EG2-P02 가 테이블 전체를 폐기한다)
WITH prev_date AS (
    -- 같은 (ticker, broker) 의 직전 의견일. 후보는 이 행 시점까지 관측된 것뿐이다.
    SELECT r.ticker            AS ticker,
           r.fetched_date      AS fetched_date,
           r.broker            AS broker,
           r.opinion_date      AS opinion_date,
           max(p.opinion_date) AS prev_opinion_date
    FROM stg_analyst_broker r
    JOIN stg_analyst_broker p
      ON p.ticker = r.ticker AND p.broker = r.broker
     AND p.opinion_date < r.opinion_date
     AND p.fetched_date <= r.fetched_date
    GROUP BY r.ticker, r.fetched_date, r.broker, r.opinion_date
),
prev_src AS (
    -- 그 prev_opinion_date 를 실제로 실어 나른 관측 행들의 available_date(= fetched_date) 최댓값.
    SELECT q.ticker                                          AS ticker,
           q.fetched_date                                    AS fetched_date,
           q.broker                                          AS broker,
           q.opinion_date                                    AS opinion_date,
           q.prev_opinion_date                               AS prev_opinion_date,
           max(p.fetched_date)                               AS prev_src_fetched_date
    FROM prev_date q
    JOIN stg_analyst_broker p
      ON p.ticker = q.ticker AND p.broker = q.broker
     AND p.opinion_date = q.prev_opinion_date
     AND p.fetched_date <= q.fetched_date
    GROUP BY q.ticker, q.fetched_date, q.broker, q.opinion_date, q.prev_opinion_date
),
prev_av AS (
    -- 동반 available_date = 구성 행 available 의 max (자기 행 available 포함, DESIGN §3·EG2-P05).
    SELECT s.ticker                                          AS ticker,
           s.fetched_date                                    AS fetched_date,
           s.broker                                          AS broker,
           s.opinion_date                                    AS opinion_date,
           s.prev_opinion_date                               AS prev_opinion_date,
           greatest(s.fetched_date, s.prev_src_fetched_date) AS prev_opinion_date_available_date
    FROM prev_src s
)
SELECT
    r.ticker,
    r.fetched_date,
    r.broker,
    r.opinion_date,
    r.target_price_krw,
    r.prev_target_price_krw,
    r.change_pct,
    r.opinion,
    r.opinion_class,
    r.prev_opinion,
    r.prev_opinion_class,
    a.prev_opinion_date,
    a.prev_opinion_date_available_date,
    r.observed_date,
    r.fetched_date                                   AS available_date,
    'measured'                                       AS available_basis,
    CASE WHEN r.target_price_krw <= 0 OR r.prev_target_price_krw <= 0
              THEN 'nonpositive_target_price'
         WHEN r.opinion_date > r.fetched_date THEN 'opinion_date_after_fetch'
    END                                              AS reject_reason
FROM stg_analyst_broker r
LEFT JOIN prev_av a
       ON a.ticker = r.ticker AND a.fetched_date = r.fetched_date
      AND a.broker = r.broker AND a.opinion_date = r.opinion_date
