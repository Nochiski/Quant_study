-- price_daily (S04) — 가격 정본. DESIGN v1.2 §4-2 · GATES v1.0 §3 ⑧ · EG7-P01 · EG20 · EG14.
-- grain (ticker, date). 원천 stg_price_daily(주식) UNION ALL stg_etf_price_daily(ETF)
--                        UNION ALL stg_flow_daily_kiwoom(저녁 잠정 T 행, e1.15.0).
-- 두 원천의 (ticker, date) 교집합은 0 이어야 한다(P8) — 겹쳐도 dedup 하지 않는다. 겹치면 키가
-- 중복돼 EG3-P01 이 폐기하고, 건수는 EG3_price_daily.n_src_overlap 이 지목한다.
--
-- 원칙 ②(원주가 불변 + 계수 분리): open·high·low·close·volume_shr·value_krw 는 stage 값 그대로.
-- 소급 조정·결측 보간·0 채움 전부 금지 — 조정은 S06 adj_factor 와 v_adj_price/v_adj_volume 이 한다.
-- stage 가 O/H/L '0' 을 NULL(miss_kind=ledger_zero) 로 둔 것도 그대로 나른다(결측은 결측, 원칙 ④).
-- 산출이 stage 와 같은지는 EG20 이 독립 재조인으로 본다.
--
-- 파생은 넷이다(S06-2 에서 base_price_krw 추가):
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
--   change_krw    = stage change_krw(KRX CMPPREVDD_PRC, 전일 대비) 그대로 · base_price_krw = close − change
--                   (S06-2). KRX 의 '전일 대비' 는 그날 **기준가** 대비라 close − change 가 그날 KRX 기준가다 —
--                   분할·무상증자·감자·정지 재개일에 기준가 ≠ 직전 종가(005930 2018-05-04: 51,900 − (−1,100)
--                   = 53,000 = 2,650,000 / 50). change NULL 이면 base 도 NULL(결측은 결측). S06 adj_factor 의
--                   krx_base_price 원천이 읽고, EG3_price_daily 가 base = 직전 행 close 비율을 기록한다.
--
-- 저녁 잠정 T 행 (규칙 e1.15.0 · 결정 V2-2 · 플랜 v2 §4 B.2):
--   KRX 공식 시세는 T+1 08:00 에 온다. 그날 저녁(18:15) 스코어링을 하려면 T 종가가 있어야 하므로,
--   `stg_price_daily` 에 **없는 최신 거래일 T** 가 키움 ka10060(`stg_flow_daily_kiwoom`)에 있으면
--   그 종가·거래량으로 T 행을 만든다. 다음 날 아침 확정판에서는 같은 (ticker, date) 가 KRX 원장에
--   있으므로 이 분기가 0행을 내고 KRX 행이 그 자리를 차지한다 — **자연 교체**이고 덮어쓰기가 없다.
--   컬럼 `basis` 가 행마다 원천을 말한다: 'krx'(KRX 원장) · 'evening'(키움 잠정).
--   evening 행은 KRX 기본정보가 없어 **OHL·거래대금·주식수·시총·전일대비·기준가·액면이 전부 NULL**
--   이다(결측은 결측, 원칙 ④ — 0 이나 직전 값으로 채우지 않는다).
--   `corp_action_pending` = 그 행이 기업행위 의심인가. 직전 KRX 종가 대비 절대수익률이
--   `_const.evening_jump_abs_max`(0.30 = 일반 세션 가격제한폭)를 넘거나 직전 종가 자체가 없으면 참.
--   저녁에는 KRX 기본정보가 없어 계수를 만들 수 없으므로 값을 고치지 않고 **표식만** 단다 —
--   소비자가 그날 스코어에서 뺀다(결정 V2-2). krx 행은 항상 거짓이다.
--
-- PIT: 가격류 — stage lag_known=true, 공표 시각 미제공 → available_date = date, basis 'default'.
-- 격리(EG7-P01 격리형, 행을 버리지 않고 _reject/<reason>/ 으로):
--   nonpositive_price = close ≤ 0 ∨ open ≤ 0 ∨ high ≤ 0 ∨ low ≤ 0 (NULL 은 위반이 아니다)
--   off_calendar      = trading_calendar(equity 입력, = stg_index_daily distinct date) 에 없는 date.
--                       **krx 행에만 건다** — 저녁 T 는 KRX 지수가 아직 안 와서 캘린더에 없는 것이
--                       정상이다(캘린더 상한 = stg_price_daily max, EG17). evening 행을 여기서
--                       격리하면 잠정판이 T 가격을 통째로 잃는다.
WITH stock AS (
    SELECT p.ticker, p.date,
           p.open_krw, p.high_krw, p.low_krw, p.close_krw, p.volume_shr, p.value_krw,
           p.change_krw,
           l.list_shrs          AS shares_out,
           l.par_value_krw      AS par_value_krw
    FROM stg_price_daily p
    LEFT JOIN stg_listing_daily l ON l.ticker = p.ticker AND l.date = p.date
),
etf AS (
    SELECT e.ticker, e.date,
           e.open_krw, e.high_krw, e.low_krw, e.close_krw, e.volume_shr, e.value_krw,
           e.change_krw,                        -- ETF DECIMAL(8,0) → UNION ALL 이 주식의 DECIMAL(9,0) 으로
           e.list_shrs          AS shares_out,
           NULL                 AS par_value_krw   -- UNION ALL 이 listing 의 DECIMAL(9,2) 로 맞춘다
    FROM stg_etf_price_daily e
),
krx AS (
    SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw,
           change_krw, shares_out, par_value_krw
    FROM stock
    UNION ALL
    SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw,
           change_krw, shares_out, par_value_krw
    FROM etf
),
kw AS (
    -- 저녁 잠정 T — KRX 가격 원장의 상한을 넘어선 **키움의 최신 날짜 하나**뿐이다. 두 조건을 다
    -- 걸어야 한다: `> KRX max` 만 걸면 키움이 앞서 있는 여러 날이 한꺼번에 들어오고,
    -- `= 키움 max` 만 걸면 아침 확정판에서 KRX 와 겹쳐 (ticker, date) 가 중복된다.
    SELECT f.ticker, f.date, f.close_krw, f.volume_shr
    FROM stg_flow_daily_kiwoom f
    WHERE f.date > (SELECT max(date) FROM stg_price_daily)
      AND f.date = (SELECT max(date) FROM stg_flow_daily_kiwoom)
),
kw_prev AS (
    -- 직전 종가 = 그 티커의 **마지막 KRX 종가**(참고가 행 포함). 정지 중이던 종목도 마지막으로
    -- 공표된 값을 기준으로 잡는다 — 세션 수가 아니라 '마지막으로 알던 값' 이 비교축이다.
    SELECT ticker, close_krw AS prev_close
    FROM krx
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
),
src AS (
    SELECT ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr, value_krw,
           change_krw, shares_out, par_value_krw,
           'krx'   AS basis, FALSE AS corp_action_pending
    FROM krx
    UNION ALL
    SELECT k.ticker, k.date,
           NULL AS open_krw, NULL AS high_krw, NULL AS low_krw,
           k.close_krw, k.volume_shr,
           NULL AS value_krw, NULL AS change_krw, NULL AS shares_out, NULL AS par_value_krw,
           'evening' AS basis,
           -- 2치로 닫는다: 직전 종가가 없거나 0 이하이거나 키움 종가가 NULL 이면 "판정 불가 = 의심"(TRUE).
           -- 3치 논리로 NULL 이 새면 소비자의 `IS NOT TRUE` 거름망을 통과한다(검수 R2-07).
           CASE WHEN p.prev_close IS NULL OR p.prev_close <= 0 OR k.close_krw IS NULL THEN TRUE
                ELSE abs(CAST(k.close_krw AS DOUBLE) / CAST(p.prev_close AS DOUBLE) - 1)
                     > c.evening_jump_abs_max END         AS corp_action_pending
    FROM kw k
    LEFT JOIN kw_prev p ON p.ticker = k.ticker
    CROSS JOIN _const c
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
    s.change_krw,
    s.close_krw - s.change_krw AS base_price_krw,   -- 그날 KRX 기준가 (DECIMAL 차 그대로, 절단본 DECIMAL(10,0))
    s.par_value_krw,
    CASE WHEN s.volume_shr > 0 THEN 'trade'
         WHEN s.volume_shr = 0 THEN 'reference'
    END           AS price_kind,
    s.basis,
    s.corp_action_pending,
    s.date        AS available_date,
    'default'     AS available_basis,
    CASE WHEN s.close_krw <= 0 OR s.open_krw <= 0 OR s.high_krw <= 0 OR s.low_krw <= 0
              THEN 'nonpositive_price'
         WHEN s.basis = 'krx'
              AND NOT EXISTS (SELECT 1 FROM trading_calendar c WHERE c.date = s.date)
              THEN 'off_calendar'
    END           AS reject_reason
FROM src s
