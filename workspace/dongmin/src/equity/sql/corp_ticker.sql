-- corp_ticker (S01) — grain ticker. 종류주 → 기업 이축(원칙 6 ③).
-- DESIGN v1.2 §4-1 · GATES §3-④ · EG3-P02.
--
-- isin8 = substr(isin, 1, isin8_len). **KR7 계열만 그룹**한다 — 비KR7 은 같은 isin8 을 다른
-- 발행사가 나눠 쓴다(SPEC §2-17: HK000005 를 900050·900060 이 공유) → 그룹핑하면 과합병이다.
-- 비KR7·ETF 는 단독이고 corp_code 는 corp_map 직접 매핑으로만 붙인다.
--
-- `is_common` 은 **주식종류**(stkcert_tp='보통주')다 — 종목 유형(sec_type: spac·reit·fund·dr)과 다르다.
-- 서버 실측(P11): KR7 isin8 그룹 3,438 전부 '보통주' 정확히 1. sec_type='common' 으로 정의하면
-- 스팩·리츠·펀드 그룹 474 개가 보통주 0 이 되어 EG3 이 깨진다(09-05 서버 1차 빌드 실측).
WITH lst AS (
    SELECT ticker, isin, secugrp, stkcert_tp
    FROM stg_listing_daily
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
),
spac AS (
    SELECT DISTINCT ticker
    FROM stg_listing_daily
    WHERE sect_tp = 'SPAC(소속부없음)' OR name LIKE '%기업인수목적%'
),
tick AS (
    SELECT DISTINCT ticker FROM stg_listing_daily
    UNION
    SELECT DISTINCT ticker FROM stg_etf_price_daily
),
corp_map AS (
    SELECT ticker, min(corp_code) AS corp_code
    FROM stg_corp_map
    WHERE nullif(trim(ticker), '') IS NOT NULL
    GROUP BY ticker
),
base AS (
    SELECT
        t.ticker                                                  AS ticker,
        substr(l.isin, 1, CAST(k.isin8_len AS INTEGER))           AS isin8,
        (s.ticker IS NOT NULL)                                    AS is_spac,
        l.secugrp                                                 AS secugrp,
        l.stkcert_tp                                              AS stkcert_tp
    FROM tick t
    LEFT JOIN lst l ON l.ticker = t.ticker
    LEFT JOIN spac s ON s.ticker = t.ticker
    CROSS JOIN _const k
),
flag AS (
    SELECT
        ticker,
        isin8,
        coalesce(isin8 LIKE 'KR7%', false)                        AS is_kr7,
        coalesce(stkcert_tp = '보통주', false)                     AS is_common
    FROM base
),
grp AS (
    SELECT isin8, min(ticker) FILTER (WHERE is_common) AS common_ticker
    FROM flag
    WHERE is_kr7
    GROUP BY isin8
)
SELECT
    f.ticker                                                      AS ticker,
    f.isin8                                                       AS isin8,
    coalesce(cmg.corp_code, cms.corp_code)                        AS corp_code,
    CASE WHEN f.is_kr7 THEN g.common_ticker ELSE NULL END        AS common_ticker,  -- 비KR7 단독
    f.is_common                                                   AS is_common,
    CASE WHEN cmg.corp_code IS NOT NULL THEN 'isin8'
         WHEN cms.corp_code IS NOT NULL THEN 'corp_map'
         ELSE 'none' END                                          AS link_basis,
    NULL::VARCHAR                                                 AS reject_reason
FROM flag f
LEFT JOIN grp g ON f.is_kr7 AND g.isin8 = f.isin8
LEFT JOIN corp_map cmg ON cmg.ticker = g.common_ticker
LEFT JOIN corp_map cms ON cms.ticker = f.ticker
