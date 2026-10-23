-- fin_ratio_wise (S26) — WISE 재무비율(cF4002) 연간 열을 행으로 편 판. 플랜 `2026-10-22-equity-wise-fields` T1.
-- grain (ticker, fetched_date, seq, slot) · date_axis year(fetched_date). 입력은 stage `stg_fin_wise` 의 ep='cF4002'.
--
-- stage 는 계정(seq) 한 행에 연간 열 6개(`period_label_i`·`val_i`, 과거 5기 + 추정 1기)를 넓게 싣는다. 여기서 라벨이
-- 있는 열만 행으로 편다(slot = i, 빈 문자열 라벨은 빈 열이다 — 서버 10-21 판 1.98만 칸). period 는 라벨 앞 'YYYY/MM' 의
-- YYYYMM, is_estimate 는 라벨의 '(E)'.
-- PER·PBR·배당수익률(382100·382500·431800)의 실적 열은 결산기 말 주가 기준이라 그해 안에서는 바뀌지 않는다.
--
-- stage 가 같은 원문을 접어(stage 2.7.0) fetched_date 는 그 원문을 처음 본 날이다 — 다음 판이 올 때까지 값이 이어진다.
-- WISE 가 종목을 더 보여 주지 않아도 옛 판이 남으므로, 최신값으로 읽는 소비자는 이월을 스스로 정한다(플랜 D2).
--
-- PIT(플랜 D1, 사용자 10-23 "신선도 최대한"): 가용일 = 수집 시각으로 행마다 센 '지식 세션'이고 랙은 1 세션이다.
--   장 시작(09:00 KST = 00:00 UTC) 전에 받은 값은 직전 세션 마감 뒤 지식이라 그날 바로 쓰고, 그 뒤에 받은 값은
--   다음 세션부터 쓴다 — 수집 시각의 UTC 날짜(known_date) 이하 마지막 거래일이 지식 세션이다.
--   수집 시각이 없으면 수집일(stage measured, 보수). known_date 가 이 판 달력의 마지막 거래일 뒤면(휴장인지 아직
--   안 들어온 날인지 가를 수 없다) 당기지 않고 known_date 그대로 둔다. 같은 규칙을 EG3 가 독립 재계산으로 대조한다.
-- 접힌 판의 수집 시각은 그 원문을 처음 본 blob 의 시각이다.
WITH cal AS (SELECT max(date) AS last_session FROM trading_calendar),
known AS (
    SELECT s.*, CAST(s.fetched_at_utc AS DATE) AS known_date FROM stg_fin_wise s
    WHERE s.ep = 'cF4002'
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
),
wide AS (
    SELECT ticker, fetched_date, CAST(seq AS INTEGER) AS seq, accode, acc_nm, unt_typ,
           list_value(period_label_1, period_label_2, period_label_3,
                      period_label_4, period_label_5, period_label_6) AS labels,
           list_value(val_1, val_2, val_3, val_4, val_5, val_6) AS vals,
           fetched_at_utc, pit_date, pit_basis
    FROM pit
),
cells AS (
    SELECT w.*, unnest(range(1, len(w.labels) + 1)) AS slot FROM wide w
)
SELECT
    ticker,
    fetched_date,
    seq,
    CAST(slot AS INTEGER)                                         AS slot,
    accode,
    acc_nm,
    CAST(unt_typ AS INTEGER)                                      AS unt_typ,
    labels[slot]                                                  AS period_label,
    nullif(replace(regexp_extract(labels[slot], '^[0-9]{4}/[0-9]{2}'), '/', ''), '') AS period,
    labels[slot] LIKE '%(E)%'                                     AS is_estimate,
    vals[slot]                                                    AS value,
    fetched_at_utc,
    pit_date                                                      AS available_date,
    pit_basis                                                     AS available_basis,
    CAST(NULL AS VARCHAR)                                         AS reject_reason   -- 격리 사유 없음(REJECT_REASONS=())
FROM cells
WHERE coalesce(labels[slot], '') <> ''
