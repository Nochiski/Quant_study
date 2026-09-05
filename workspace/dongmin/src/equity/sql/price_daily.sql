-- price_daily (S04) — 가격 정본. DESIGN v1.2 §4-2 · GATES v1.0 §3 ⑧ · EG7-P01 · EG20.
-- grain (ticker, date). 원천 stg_price_daily(주식) UNION ALL stg_etf_price_daily(ETF).
-- 두 원천의 (ticker, date) 교집합은 0 이어야 한다(P8) — 겹쳐도 dedup 하지 않는다. 겹치면 키가
-- 중복돼 EG3-P01 이 폐기하고, 건수는 EG3_price_daily.n_src_overlap 이 지목한다.
--
-- 원칙 ②(원주가 불변 + 계수 분리): open·high·low·close·volume_shr·value_krw 는 stage 값 그대로.
-- 소급 조정·결측 보간·0 채움 전부 금지 — 조정은 S06 adj_factor 와 v_adj_price/v_adj_volume 이 한다.
-- stage 가 O/H/L '0' 을 NULL(miss_kind=ledger_zero) 로 둔 것도 그대로 나른다(결측은 결측, 원칙 ④).
-- 산출이 stage 와 같은지는 EG20 이 독립 재조인으로 본다.
--
-- 파생은 셋뿐이다:
--   shares_out    = 같은 날 stg_listing_daily.list_shrs (KRX 정본). ETF 는 listing 원장에 없으므로
--                   stg_etf_price_daily.list_shrs(상장좌수) — 같은 KRX 원장이고 MKTCAP 항등이 선다.
--                   listing 에 그날 행이 없는 주식은 NULL (LEFT JOIN — 행을 버리면 EG1 이 깨진다).
--   par_value_krw = 같은 날 stg_listing_daily.par_value_krw (무액면 = stage NULL 그대로).
--                   ETF 는 액면 개념이 없어 NULL.
--   mktcap_krw    = close × shares_out. stage 자신의 mktcap_krw 를 복사하지 않는다 — listing 과
--                   가격 원장의 주식수가 어긋나면 EG3_price_daily.n_mktcap_stage_mismatch 가 드러낸다.
--                   타입 캐스팅을 쓰지 않는다(정밀도 리터럴도 하드코딩 상수 검사 대상) — duckdb 의
--                   DECIMAL 곱 타입을 그대로 받는다.
--   price_kind    : volume_shr > 0 → 'trade' · = 0 → 'reference'(기준가·정지일, 종가 보존 FX-2-006)
--                   · NULL → NULL (n_price_kind_null 기록). S03B 의 suspended 판정이 'reference' 를 읽는다.
--
-- PIT: 가격류 — stage lag_known=true, 공표 시각 미제공 → available_date = date, basis 'default'.
-- 격리(EG7-P01 격리형, 행을 버리지 않고 _reject/<reason>/ 으로):
--   nonpositive_price = close ≤ 0 ∨ open ≤ 0 ∨ high ≤ 0 ∨ low ≤ 0 (NULL 은 위반이 아니다)
--   off_calendar      = trading_calendar(equity 입력, = stg_index_daily distinct date) 에 없는 date
WITH stock AS (
    SELECT p.ticker, p.date,
           p.open_krw, p.high_krw, p.low_krw, p.close_krw, p.volume_shr, p.value_krw,
           l.list_shrs          AS shares_out,
           l.par_value_krw      AS par_value_krw
    FROM stg_price_daily p
    LEFT JOIN stg_listing_daily l ON l.ticker = p.ticker AND l.date = p.date
),
etf AS (
    SELECT e.ticker, e.date,
           e.open_krw, e.high_krw, e.low_krw, e.close_krw, e.volume_shr, e.value_krw,
           e.list_shrs          AS shares_out,
           NULL                 AS par_value_krw   -- UNION ALL 이 listing 의 DECIMAL(9,2) 로 맞춘다
    FROM stg_etf_price_daily e
),
src AS (
    SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw,
           shares_out, par_value_krw
    FROM stock
    UNION ALL
    SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw,
           shares_out, par_value_krw
    FROM etf
)
SELECT
    s.ticker,
    s.date,
    s.open_krw    AS open,
    s.high_krw    AS high,
    s.low_krw     AS low,
    s.close_krw   AS close,
    s.volume_shr,
    s.value_krw,
    s.close_krw * s.shares_out AS mktcap_krw,   -- DECIMAL 곱 그대로(절단본 실측 DECIMAL(18,0), 무손실)
    s.shares_out,
    s.par_value_krw,
    CASE WHEN s.volume_shr > 0 THEN 'trade'
         WHEN s.volume_shr = 0 THEN 'reference'
    END           AS price_kind,
    s.date        AS available_date,
    'default'     AS available_basis,
    CASE WHEN s.close_krw <= 0 OR s.open_krw <= 0 OR s.high_krw <= 0 OR s.low_krw <= 0
              THEN 'nonpositive_price'
         WHEN NOT EXISTS (SELECT 1 FROM trading_calendar c WHERE c.date = s.date)
              THEN 'off_calendar'
    END           AS reject_reason
FROM src s
