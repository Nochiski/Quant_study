-- price_adj_daily (S23) — 전방 조정(forward-adjusted) OHLCV. DESIGN v1.2 §4-2 · GATES §3-㉓.
-- grain (ticker, date) = price_daily 와 같은 축, 행수도 같다(격리 없음 — EG1 은 항등식).
--
-- **원주가는 싣지 않는다**. 정본은 price_daily 이고 같은 값을 두 표에 두면 드리프트가 생긴다.
-- 여기 있는 것은 조정값과 그 조정을 만든 누적계수·근거 수치뿐이다.
--
-- 전방 조정(사용자 결정 09-05, DESIGN §5·§11 ①): 종목의 **첫 관측 수준을 고정**하고 사건마다
-- 이후 가격을 누적 배수로 올린다.
--   adj_close(d) = close(d) × Π(share_factor : factor_ok ∧ 구간 안 ∧ fold_date ≤ d)
--   adj_volume_shr(d) = volume_shr(d) × Π(price_factor : 같은 집합)
--   fold_date = greatest(apply_date, available_date)  ← 적용 세션이 와도 아직 공개 전인 계수
--     (회고 기재 원천은 available = apply 다음 세션)는 **공개 세션부터** 접는다. 이 규약 때문에
--     행의 available_date 가 항상 date 로 떨어지고(아래), 값이 (ticker, date) 의 순수 함수가 된다.
-- OHLC 는 close 와 같은 계수를 쓴다(같은 날 같은 척도). 거래량만 반대 축(price_factor)이다 —
-- 분할 뒤 거래량을 분할 전 주식수 척도로 내린다. 나눗셈으로 쓰면 50:1 분할에서 2,500배 어긋난다.
--
-- **누적은 (ticker, span_seq) 안에서만** 한다. 재상장 2종(036220·101970)의 이전 구간 계수가
-- 넘어오면 새 구간의 수준이 통째로 틀어진다(첫 관측 = 그 구간의 첫 거래일이지 폐지 전 옛 구간이
-- 아니다). 구간 부여는 security_span 위 ASOF 다:
--   가격 행     : first_date ≤ date          인 마지막 구간   (구간 밖 = 첫 구간보다 이른 행은 0)
--   ok 계수     : first_date <  fold_date    인 마지막 구간   ← **엄격 부등호**. 구간 첫날에
--                 접히는 계수는 그 구간의 앵커(첫 관측)와 같은 날이라 접을 것이 없고, 접으면
--                 "구간 첫 행 누적 = 1" 이 깨진다. 앞 구간으로 가면 그 구간의 어떤 행보다도
--                 뒤라 결국 아무 행에도 곱해지지 않는다(= 폐기, 의도한 동작)
--   not-ok 사건 : first_date ≤ apply_date    인 마지막 구간   ← 비엄격. 미조정 사건은 수준을
--                 바꾸지 않고 **경고 신호**라 구간 첫날 사건도 그 구간 전체에 표시한다
-- 구간이 없는 행·계수는 span_seq 0 으로 같이 묶인다(첫 상장일 이전 가격 행 — listing 원장과
-- 가격 원장의 커버가 어긋나는 자리. 건수는 EG3_price_adj_daily.n_rows_without_span 이 센다).
--
-- n_factors_applied  = 그 행에 접힌 ok 계수 수. 0 이면 cum_* 는 정확히 1 이다.
-- n_unadjusted_events = 같은 구간에서 factor_ok=false 이고 apply_date ≤ d 인 사건 수.
--   0 이 아닌 구간의 조정 시계열은 **불완전**하다 — 사건은 실재하는데 계수를 못 냈다는 뜻이고
--   (사유는 adj_factor.factor_source), 소비자가 종목을 거를 축이다. 값을 만들어 채우지 않는다.
--
-- PIT: available_date = greatest(date, 접힌 계수의 available_date 최댓값) — 구성 행의 max 이므로
-- basis 는 'derived' 다(convention 이 아니라 계산 결과다, DESIGN §8-3 EG2-P05 규약). fold 규칙상
-- 접힌 계수는 전부 available_date ≤ fold_date ≤ date 라 결과는 **항상 date** 이고, 그 항등을
-- EG3_price_adj_daily 가 폐기형으로 다시 확인한다(선언이 아니라 산출로 증명한다).
--
-- 격리 없음: 조정은 가격 행 하나하나에 대한 순수 함수라 버릴 행이 없다. trading_calendar 는
-- 산출식이 아니라 게이트(EG3 의 캘린더 독립 재검사)가 읽는 입력이다.
-- 저녁 잠정 T 행(e1.15.0): `price_daily.basis`·`corp_action_pending` 을 **그대로 싣는다** — 이 표는
-- 소비자(워크벤치 `ADJ_TABLE`)가 직접 읽으므로 뷰만 표식을 통과시키면 잠정치가 확정치처럼 보인다
-- (검수 R2-04). T 행은 캘린더 밖이라 EG3 의 캘린더 검사는 basis='krx' 행에만 건다(검수 R2-01).
WITH px AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr,
           p.basis, p.corp_action_pending,
           coalesce(s.span_seq, 0) AS span_seq
    FROM price_daily p
    ASOF LEFT JOIN security_span s
      ON s.ticker = p.ticker AND p.date >= s.first_date
),
okf AS (
    SELECT f.ticker, greatest(f.apply_date, f.available_date) AS fold_date,
           f.price_factor, f.share_factor, f.available_date
    FROM adj_factor f
    WHERE f.factor_ok
),
oks AS (
    SELECT o.ticker, coalesce(s.span_seq, 0) AS span_seq, o.fold_date,
           o.price_factor, o.share_factor, o.available_date
    FROM okf o
    ASOF LEFT JOIN security_span s
      ON s.ticker = o.ticker AND o.fold_date > s.first_date
),
fac AS (                                    -- 같은 (구간, 접는 세션)의 두 계수는 곱으로 접는다
    SELECT ticker, span_seq, fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           count(*) AS n_fac, max(available_date) AS avail
    FROM oks
    GROUP BY ticker, span_seq, fold_date
),
cum AS (
    SELECT ticker, span_seq, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           sum(n_fac)  OVER w AS n_factors_applied,
           max(avail)  OVER w AS factor_available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
badf AS (
    SELECT e.ticker, e.apply_date FROM adj_factor e WHERE NOT e.factor_ok
),
bads AS (
    SELECT b.ticker, coalesce(s.span_seq, 0) AS span_seq, b.apply_date
    FROM badf b
    ASOF LEFT JOIN security_span s
      ON s.ticker = b.ticker AND b.apply_date >= s.first_date
),
bad AS (
    SELECT ticker, span_seq, apply_date, count(*) AS n_bad
    FROM bads
    GROUP BY ticker, span_seq, apply_date
),
badcum AS (
    SELECT ticker, span_seq, apply_date,
           sum(n_bad) OVER (PARTITION BY ticker, span_seq ORDER BY apply_date
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             AS n_unadjusted_events
    FROM bad
),
joined AS (
    SELECT p.ticker, p.date, p.span_seq, p.open, p.high, p.low, p.close, p.volume_shr,
           p.basis, p.corp_action_pending,
           coalesce(c.cum_price_factor, 1)  AS cum_price_factor,
           coalesce(c.cum_share_factor, 1)  AS cum_share_factor,
           coalesce(c.n_factors_applied, 0) AS n_factors_applied,
           c.factor_available_date
    FROM px p
    ASOF LEFT JOIN cum c
      ON c.ticker = p.ticker AND c.span_seq = p.span_seq AND p.date >= c.fold_date
),
unadj AS (
    SELECT j.*, coalesce(b.n_unadjusted_events, 0) AS n_unadjusted_events
    FROM joined j
    ASOF LEFT JOIN badcum b
      ON b.ticker = j.ticker AND b.span_seq = j.span_seq AND j.date >= b.apply_date
)
SELECT
    u.ticker,
    u.date,
    u.open       * u.cum_share_factor              AS adj_open,
    u.high       * u.cum_share_factor              AS adj_high,
    u.low        * u.cum_share_factor              AS adj_low,
    u.close      * u.cum_share_factor              AS adj_close,
    u.volume_shr * u.cum_price_factor              AS adj_volume_shr,
    u.cum_price_factor,
    u.cum_share_factor,
    CAST(u.n_factors_applied AS BIGINT)            AS n_factors_applied,
    CAST(u.n_unadjusted_events AS BIGINT)          AS n_unadjusted_events,
    greatest(u.date, coalesce(u.factor_available_date, u.date)) AS available_date,
    'derived'                                      AS available_basis,
    u.basis                                        AS basis,
    u.corp_action_pending                          AS corp_action_pending,
    NULL::VARCHAR                                  AS reject_reason
FROM unadj u
