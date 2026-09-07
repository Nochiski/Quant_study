-- security_span (S02) — (listing ∪ etf) 존재일을 캘린더 순번에 얹은 최대 연속 run.
-- DESIGN v1.2 §4-1. grain (ticker, span_seq), span_seq 는 first_date 순 1부터.
--
-- 캘린더는 trading_calendar 와 같은 정의(stg_index_daily distinct date)를 여기서 다시 만든다.
-- equity 산출을 입력으로 읽지 않는다 — 층 계약이 입력을 stage 로 한정하고, 두 정의가 어긋나면
-- EG3x 의 "distinct date(listing) = 캘린더 거래일 수" 등식이 잡는다.
--
-- 연속 판정은 달력일이 아니라 거래일 순번(td_seq)으로 한다. 달력일로 하면 주말·연휴가 전부
-- 구간 절단으로 읽혀 종목마다 수백 구간이 나온다.
-- run_key = td_seq - row_number(): 연속 구간에서 상수, 한 칸이라도 비면 값이 바뀐다.
--
-- end_reason: 구간이 수집 상한(캘린더 max)에 붙어 있으면 폐지가 아니라 커버리지 끝이다.
-- 상한은 baseline 상수가 아니라 캘린더 자신의 max 로 잡는다 — 같은 값(backfill_end)을 두 곳에
-- 적어 두면 어긋날 때 조용히 갈린다. baseline 과의 일치는 EG3x 가 등식으로 본다.
-- 'data_gap' 은 어휘 예약 — listing 완결성 실측(P11) 때문에 지금은 산출되지 않는다.
WITH cal AS (
    SELECT date, dense_rank() OVER (ORDER BY date) AS td_seq
    FROM (SELECT DISTINCT date FROM stg_index_daily)
),
coverage AS (
    SELECT max(date) AS coverage_end FROM cal
),
exist AS (
    SELECT ticker, date FROM stg_listing_daily
    UNION
    SELECT ticker, date FROM stg_etf_price_daily
),
on_cal AS (
    SELECT e.ticker, e.date, c.td_seq
    FROM exist e JOIN cal c USING (date)
),
runs AS (
    SELECT ticker, date, td_seq,
           td_seq - row_number() OVER (PARTITION BY ticker ORDER BY td_seq) AS run_key
    FROM on_cal
),
spans AS (
    SELECT ticker, min(td_seq) AS start_seq, min(date) AS first_date,
           max(date) AS last_date, count(*) AS n_days
    FROM runs
    GROUP BY ticker, run_key
)
SELECT
    s.ticker,
    row_number() OVER (PARTITION BY s.ticker ORDER BY s.start_seq) AS span_seq,
    s.first_date,
    s.last_date,
    s.n_days,
    CASE WHEN s.last_date = cov.coverage_end THEN 'coverage_gap' ELSE 'delisted' END AS end_reason,
    NULL::VARCHAR AS reject_reason
FROM spans s CROSS JOIN coverage cov
