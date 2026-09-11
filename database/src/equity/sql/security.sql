-- security (S01) — grain ticker. 모집단 = (stg_listing_daily ∪ stg_etf_price_daily) distinct ticker.
-- DESIGN v1.2 §4-1 · GATES §3-②. 숫자·날짜 상수는 없다 —
--   백필 상한은 규칙 e1.15.0 부터 baseline 상수(`security.backfill_end`)가 아니라 이 SQL 안의
--   거래일 축 `td` 의 max 에서 **유도**한다. 거래일이 하루 늘 때마다 사람이 상수를 올려야 하던
--   고장을 없앤다(플랜 v2 §4 B.2 · v1 §8 Task 5.1). `td` = stg_index_daily distinct date 이고
--   EG17 이 매 빌드 `max(trading_calendar) == max(stg_price_daily.date)` 를 증명하므로
--   두 층의 상한은 같은 날짜다.
--
-- 폐지일 두 축(정본 우선순위 = KRX):
--   delist_date_krx = listing/etf 마지막 존재일의 **다음 거래일**. 거래일 축은 stg_index_daily
--     distinct date 로 이 SQL 안에서 만든다(trading_calendar 는 S02 산출이라 입력이 될 수 없다).
--     마지막 존재일이 백필 상한(= max(td))이면 아직 살아 있는 것이므로 NULL/basis unknown.
--   delist_date_kis = stg_delisted_master.lstg_abol_dt (대조축).
-- sec_type 은 전 이력 어휘(P11) 매핑. spac 은 common 보다 우선하고, 어휘 밖은 'other' 로 두되
-- **행을 유지**한다(격리하면 게이트가 생존편향을 만든다 — GATES §5-C6).
WITH td AS (
    SELECT DISTINCT date FROM stg_index_daily
),
exist AS (
    SELECT ticker, date FROM stg_listing_daily
    UNION
    SELECT ticker, date FROM stg_etf_price_daily
),
last_day AS (
    SELECT ticker, max(date) AS last_date FROM exist GROUP BY ticker
),
lst AS (
    SELECT ticker, isin, name, list_date, secugrp, stkcert_tp
    FROM stg_listing_daily
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
),
spac AS (
    -- 소속부는 일자별로 바뀐다 — SPAC 판정은 전 이력 존재(EXISTS)로 본다.
    SELECT DISTINCT ticker
    FROM stg_listing_daily
    WHERE sect_tp = 'SPAC(소속부없음)' OR name LIKE '%기업인수목적%'
),
etf AS (
    SELECT ticker, name
    FROM stg_etf_price_daily
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
),
kis AS (
    SELECT ticker, min(lstg_abol_dt) AS delist_date_kis
    FROM stg_delisted_master
    WHERE lstg_abol_dt IS NOT NULL
    GROUP BY ticker
),
corp_map AS (
    SELECT ticker, min(corp_code) AS corp_code
    FROM stg_corp_map
    WHERE nullif(trim(ticker), '') IS NOT NULL
    GROUP BY ticker
),
resolved AS (
    SELECT
        d.ticker                                             AS ticker,
        cm.corp_code                                         AS corp_code,
        l.isin                                               AS isin,
        coalesce(l.name, e.name)                             AS name_current,
        CASE
            WHEN e.ticker IS NOT NULL THEN 'etf'
            WHEN s.ticker IS NOT NULL THEN 'spac'
            WHEN l.secugrp = '주권' AND l.stkcert_tp = '보통주' THEN 'common'
            WHEN l.stkcert_tp IN ('구형우선주', '신형우선주', '종류주권') THEN 'preferred'
            WHEN l.secugrp = '부동산투자회사' THEN 'reit'
            WHEN l.secugrp = '선박투자회사' THEN 'ship_fund'
            WHEN l.secugrp IN ('투자회사', '사회간접자본투융자회사') THEN 'fund'
            WHEN l.secugrp = '외국주권' THEN 'foreign'
            WHEN l.secugrp IN ('주식예탁증권', '주식예탁증서') THEN 'dr'
            ELSE 'other'
        END                                                  AS sec_type,
        l.list_date                                          AS list_date,
        CASE WHEN d.last_date >= (SELECT max(t.date) FROM td t) THEN NULL
             ELSE (SELECT min(t.date) FROM td t WHERE t.date > d.last_date) END
                                                             AS delist_date_krx,
        x.delist_date_kis                                    AS delist_date_kis
    FROM last_day d
    LEFT JOIN lst l ON l.ticker = d.ticker
    LEFT JOIN etf e ON e.ticker = d.ticker
    LEFT JOIN spac s ON s.ticker = d.ticker
    LEFT JOIN kis x ON x.ticker = d.ticker
    LEFT JOIN corp_map cm ON cm.ticker = d.ticker
)
SELECT
    ticker,
    corp_code,
    isin,
    name_current,
    sec_type,
    list_date,
    CASE WHEN list_date IS NULL THEN 'unknown' ELSE 'measured' END        AS list_date_basis,
    delist_date_krx,
    delist_date_kis,
    (delist_date_krx IS NOT NULL AND delist_date_kis IS NOT NULL
     AND delist_date_krx <> delist_date_kis)                              AS delist_conflict,
    coalesce(delist_date_krx, delist_date_kis)                            AS delist_date,
    CASE WHEN delist_date_krx IS NOT NULL THEN 'derived'
         WHEN delist_date_kis IS NOT NULL THEN 'measured'
         ELSE 'unknown' END                                               AS delist_date_basis,
    NULL::VARCHAR                                                         AS reject_reason
FROM resolved
