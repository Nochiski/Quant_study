-- adj_factor (S06) — 조정계수. grain (ticker, effective_date, event_id). DESIGN v1.2 §4-2 · GATES §3-⑩ · EG3-P04.
--
-- 원천: equity corp_event(MVP 4종 split · reverse_split · bonus · capred 만 계수를 낸다) · trading_calendar
--   (available_date 의 '효력일 다음 거래일' 축) · stg_event_cr(감자 결정공시 본문 cr_mth·cr_rs — 유·무상 축).
--   price_daily 는 산출식에 쓰지 않는다 — EG8 점프 게이트(rules_s06)만 읽는다.
--
-- 계수 정의 (결정 6 · 원칙 ②): 이벤트 **후** 기준으로 이전 가격·거래량을 맞추는 곱셈 계수.
--   share_factor = corp_event.ratio (= 이벤트 후 주식수 / 전 주식수)  · price_factor = 1 / ratio
--   split 50:1 → (1/50, 50) · bonus 1주당 r → (1/(1+r), 1+r) · reverse_split·무상 capred → 역방향(ratio < 1).
--   시총 불변 이벤트는 price_factor × share_factor = 1 (EG3-P04, 허용오차는 DOUBLE 역수 곱 정밀도 —
--   rules_s06.FACTOR_PRODUCT_TOL, baseline 상수가 아니다).
--   가격·거래량 둘 다 **곱셈** — v_adj_price = close × cum_price_factor · v_adj_volume = volume × cum_share_factor.
--
-- factor_ok=false 인 행 (격리가 아니다 — 이벤트는 실재하나 계수를 못 낸다. 계수 1 · 사유는 factor_source):
--   ratio_null           : 자본변동(stg_capital) 단독 행은 전 주식수가 없어 ratio NULL (원칙 ④ 결측은 결측)
--   capred_paid          : 유상감자 — 대가가 나가 시총 불변이 아니고 KRX 기준가 산식이 달라 price_factor 를
--                          ratio 로 못 낸다. corp_event 에는 구분 축이 없으므로(DESIGN §4-2) 결정공시 본문
--                          stg_event_cr.cr_mth·cr_rs 에 '유상' 이 있는 event_cr 행으로 판정한다
--   near_dup_suppressed  : 같은 (ticker, event_type) 이 **다른 원천**에서 near_dup_window_days 안에 2건
--                          (원천마다 기준일이 하루 어긋난 같은 사건, 101970 2018-10-12 결정공시 / 10-13 자본변동)
--                          — 우선순위가 낮은 쪽. 둘 다 곱하면 감자를 두 번 곱는다. 같은 원천의 근접 2건은
--                          다른 접수번호의 다른 사건이라 누르지 않는다(101970 2015-11-26·11-28 회생 감자 2건:
--                          자기주식 소각+병합 뒤 출자전환을 거쳐 이틀 뒤 다시 10:1 병합).
--                          우선순위 = effective_basis(disclosure_body > krx_shares_change > krx_notice >
--                          unconfirmed) → ratio 있는 쪽 → source(결정공시 > 자본변동 > KRX) → announce_date
--                          → effective_date → event_id
--   사유 우선순위: near_dup_suppressed > ratio_null > capred_paid (눌린 행은 사유가 무엇이든 중복이다).
--
-- available_date = min(announce_date, effective_date 다음 거래일) · basis derived — 공시가 없어도 KRX 주식수
--   변화가 다음 거래일에 관측되므로 그날엔 알 수 있었다. announce 가 회고 기재(자본변동, 최대 수년 뒤)면
--   다음 거래일이 이긴다 → available_date < announce_date 가 정상이라 EG2-P02 의 announce 축을 못 쓴다
--   (content_date_column 없음). 대신 EG3_adj_factor 가 이 식을 독립 재계산해 전건 일치를 요구한다.
-- 숫자 리터럴은 0·1·2 만 쓴다(test_sql파일에_상수_하드코딩_없음) — 창 길이는 _const 로 들어온다.
WITH cal AS (
    SELECT date FROM trading_calendar
),
ev AS (
    SELECT e.event_id, e.ticker, e.corp_code, e.event_type, e.announce_date, e.effective_date,
           e.effective_basis, e.ratio, e.source,
           coalesce(e.event_type = 'capred' AND e.source = 'event_cr'
                    AND (cr.cr_mth LIKE '%유상%' OR cr.cr_rs LIKE '%유상%'), FALSE) AS capred_paid
    FROM corp_event e
    LEFT JOIN stg_event_cr cr
           ON e.source = 'event_cr' AND cr.rcept_no = e.rcept_no AND cr.corp_code = e.corp_code
    WHERE e.event_type IN ('split', 'reverse_split', 'bonus', 'capred')
),
ranked AS (
    -- 근접 중복 우선순위 키. STRUCT 비교는 필드 순 사전식 — 작은 쪽이 이긴다.
    SELECT ev.*,
           row(list_position(['disclosure_body', 'krx_shares_change', 'krx_notice', 'unconfirmed'],
                             effective_basis),
               ratio IS NULL,
               list_position(['event_fric', 'event_pifric', 'event_cr', 'capital', 'krx_listing'],
                             source),
               announce_date, effective_date, event_id)                                   AS rank_key
    FROM ev
),
suppressed AS (
    SELECT a.event_id
    FROM ranked a
    JOIN ranked b
      ON b.ticker = a.ticker AND b.event_type = a.event_type AND b.event_id <> a.event_id
     AND b.source <> a.source
     AND abs(date_diff('day', a.effective_date, b.effective_date))
         <= CAST((SELECT near_dup_window_days FROM _const) AS INTEGER)
     AND b.rank_key < a.rank_key
    GROUP BY a.event_id
),
judged AS (
    SELECT r.*,
           CASE WHEN s.event_id IS NOT NULL THEN 'near_dup_suppressed'
                WHEN r.ratio IS NULL          THEN 'ratio_null'
                WHEN r.capred_paid            THEN 'capred_paid'
                ELSE 'mktcap_neutral' END AS factor_source
    FROM ranked r
    LEFT JOIN suppressed s ON s.event_id = r.event_id
)
SELECT
    j.ticker,
    j.effective_date,
    j.event_id,
    j.corp_code,
    j.event_type,
    j.announce_date,
    CASE WHEN j.factor_source = 'mktcap_neutral' THEN 1 / j.ratio ELSE 1 END         AS price_factor,
    CASE WHEN j.factor_source = 'mktcap_neutral' THEN j.ratio     ELSE 1 END         AS share_factor,
    j.factor_source,
    (j.factor_source = 'mktcap_neutral')                                             AS factor_ok,
    least(j.announce_date,
          (SELECT min(c.date) FROM cal c WHERE c.date > j.effective_date))           AS available_date,
    'derived'                                                                        AS available_basis,
    NULL::VARCHAR                                                                    AS reject_reason
FROM judged j
