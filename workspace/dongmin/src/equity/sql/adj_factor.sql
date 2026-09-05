-- adj_factor (S06, 2차 = apply_date) — 조정계수. grain (ticker, effective_date, event_id).
-- DESIGN v1.2 §4-2 · GATES §3-⑩ · EG3-P04 · EG8.
--
-- 원천: equity corp_event(MVP 4종 split · reverse_split · bonus · capred 만 계수를 낸다) · trading_calendar
--   (세션 축) · price_daily(close · price_kind — 계수를 **어느 세션에 적용할지** 가격으로 가린다) ·
--   stg_event_cr(감자 결정공시 본문 cr_mth·cr_rs — 유·무상 축).
--
-- 계수 정의 (결정 6 · 원칙 ②): 이벤트 **후** 기준으로 이전 가격·거래량을 맞추는 곱셈 계수.
--   share_factor = corp_event.ratio (= 이벤트 후 주식수 / 전 주식수)  · price_factor = 1 / ratio
--   split 50:1 → (1/50, 50) · bonus 1주당 r → (1/(1+r), 1+r) · reverse_split·무상 capred → 역방향(ratio < 1).
--   시총 불변 이벤트는 price_factor × share_factor = 1 (EG3-P04, 허용오차 = DOUBLE 역수 곱 정밀도,
--   rules_s06.FACTOR_PRODUCT_TOL). 가격·거래량 둘 다 **곱셈**.
--
-- apply_date (계수를 가격에 적용하는 세션, 서버 1차 실측 뒤 신설 — GATES §9 S06 2차):
--   명목 효력일(effective_date, 감자는 기준일)에 기준가가 바뀌지 않는 사건이 많다 — 감자는 매매거래정지 뒤
--   재개일에 조정되고(명목일 원수익률 0.0 = 정지, 최적일 중앙값 +13 세션), 감자병합은 개별 비율로는 어느
--   날도 맞지 않는다(복합 사건). 그래서 명목 세션(캘린더에서 effective_date 이상 첫 세션)의 **잔여 수익률**
--   dev = |close_t / 직전 거래 종가 / price_factor − 1| 로 적용 세션을 가린다. 허용치는 조정 후 잔여 기준
--   tol = max(tol_rel × m, tol_abs), m = |min(pf, 1/pf) − 1| (기대 점프 크기 — 50:1 분할 0.98 · 10:1 감자
--   0.9 · 5% 무상증자 0.048). 원수익률 기준 |r − pf| ≤ max(0.15|pf−1|, 0.05)(서버 실측에 쓴 식)과 감자에선
--   같고 분할에선 더 엄격하다 — 원수익률 기준은 50:1 분할에 r ∈ [0, 0.167] 을 허용해 조정 후 +735% 까지
--   통과시킨다(EG8-P02 가 잡을 구멍을 판정에서 미리 막는다).
--   (a) nominal        : 명목 세션의 dev ≤ tol. 기대 점프가 tol_abs 이하인 소액 이벤트(m ≤ tol_abs, 예 2~5%
--                        무상증자)는 가격으로 날짜를 가릴 수 없으므로 항상 명목 세션(창 탐색은 잡음 매칭이
--                        된다 — 서버 실측 (3)).
--   (b) price_matched  : 창 [n0 − lookback, n0 + window] 세션의 거래 행 중 dev 최소 세션이 tol 안.
--   (c) price_matched_combined : (a)(b) 실패 후보 중 같은 티커·명목 세션 거리 ≤ window 로 이어진 성분
--                        (연결 성분, 재귀 CTE)의 계수 곱으로 성분 창 [min n0 − lookback, max n0 + window] 에서
--                        한 세션을 찾아 성분 전부에 같은 apply_date — 감자 + 액면병합 같은 복합 사건은 개별
--                        비율로는 어느 날도 맞지 않고 따로 적용하면 이중 계산이다.
--   (d) unmatched      : 못 찾으면 factor_ok=false · factor_source='no_price_match' · 계수 1 — 틀린 날에
--                        적용하는 것보다 안 하는 게 낫다. apply_date 는 명목 세션(기록용).
--   같은 티커의 개별 매칭(nominal·price_matched) ok 이벤트 2건이 같은 apply_date 에 닿으면 같은 사건을 두 원천
--   ·두 유형이 따로 실은 것(감자 cr + KRX 액면병합)이라 우선순위 낮은 쪽을 same_day_suppressed 로 누른다
--   (성분 매칭은 하나의 복합 사건이라 제외). 창 폭·허용치는 _const(baseline adj_factor.price_match_*).
--
-- factor_ok=false 인 행 (격리가 아니다 — 이벤트는 실재하나 계수를 못 낸다. 계수 1 · 사유는 factor_source):
--   near_dup_suppressed  : 같은 (ticker, event_type) 이 **다른 원천**에서 near_dup_window_days 안에 2건
--                          (101970 2018-10-12 결정공시 / 10-13 자본변동) — 우선순위 낮은 쪽. 같은 원천의
--                          근접 2건은 다른 접수번호의 다른 사건이라 누르지 않는다(101970 2015-11-26·11-28).
--                          우선순위 = effective_basis(disclosure_body > krx_shares_change > krx_notice >
--                          unconfirmed) → ratio 있는 쪽 → source(결정공시 > 자본변동 > KRX) → announce_date
--                          → effective_date → event_id
--   ratio_null           : 자본변동(stg_capital) 단독 행은 전 주식수가 없어 ratio NULL (원칙 ④)
--   capred_paid          : 유상감자 — 시총 불변이 아니고 KRX 기준가 산식이 달라 price_factor 를 ratio 로 못
--                          낸다. corp_event 에 구분 축이 없으므로 결정공시 본문 stg_event_cr.cr_mth·cr_rs 에
--                          '유상' 이 있는 event_cr 행으로 판정한다
--   no_price_match       : 위 (d)
--   same_day_suppressed  : 위 같은 apply_date 규칙
--   사유 우선순위: near_dup_suppressed > ratio_null > capred_paid > no_price_match > same_day_suppressed.
--   ok 가 아닌 행의 apply_date 는 명목 세션, apply_basis 는 no_price_match 만 'unmatched' 나머지 'nominal'.
--
-- available_date = min(announce_date, apply_date 다음 세션) · basis derived — 공시가 없어도 KRX 가격·주식수
--   변화가 그 다음 세션에 관측된다. 회고 기재 원천(자본변동, announce 가 수년 뒤)은 available < announce
--   가 정상이라 EG2-P02 announce 축을 못 쓴다(content_date_column 없음) — EG3_adj_factor 가 독립 재계산.
-- 숫자 리터럴은 0·1·2 만 쓴다(test_sql파일에_상수_하드코딩_없음) — 창 폭·허용치는 _const 로만 들어온다.
WITH RECURSIVE
cal AS (
    SELECT date, row_number() OVER (ORDER BY date) AS n FROM trading_calendar
),
k AS (
    SELECT CAST(near_dup_window_days AS INTEGER)         AS near_dup_days,
           CAST(price_match_window_sessions AS INTEGER)  AS win_after,
           CAST(price_match_lookback_sessions AS INTEGER) AS win_before,
           CAST(price_match_tol_rel AS DOUBLE)           AS tol_rel,
           CAST(price_match_tol_abs AS DOUBLE)           AS tol_abs
    FROM _const
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
    -- 우선순위 키. STRUCT 비교는 필드 순 사전식 — 작은 쪽이 이긴다.
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
     AND abs(date_diff('day', a.effective_date, b.effective_date)) <= (SELECT near_dup_days FROM k)
     AND b.rank_key < a.rank_key
    GROUP BY a.event_id
),
judged AS (
    SELECT r.*,
           CASE WHEN s.event_id IS NOT NULL THEN 'near_dup_suppressed'
                WHEN r.ratio IS NULL          THEN 'ratio_null'
                WHEN r.capred_paid            THEN 'capred_paid'
                ELSE 'mktcap_neutral' END AS base_source
    FROM ranked r
    LEFT JOIN suppressed s ON s.event_id = r.event_id
),
nominal AS (
    -- 명목 세션 = 캘린더에서 effective_date 이상 첫 세션 (corp_event 범위 규칙이 캘린더 안을 보장한다)
    SELECT j.*, c.n AS n0, c.date AS nominal_date
    FROM judged j
    ASOF JOIN cal c ON j.effective_date <= c.date
),
-- ── 가격 축: 후보 티커의 가격 행 + 직전 거래 종가 ───────────────────────────
px AS (
    SELECT p.ticker, p.date, p.close, p.price_kind,
           last_value(CASE WHEN p.price_kind = 'trade' THEN p.close END IGNORE NULLS)
             OVER (PARTITION BY p.ticker ORDER BY p.date
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)                    AS prev_trade_close
    FROM price_daily p
    WHERE p.ticker IN (SELECT DISTINCT ticker FROM nominal WHERE base_source = 'mktcap_neutral')
),
cand AS (
    -- 계수 후보(base ok) + 기대 점프 크기 m 과 허용치
    SELECT n.*, 1 / n.ratio AS pf,
           abs(least(1 / n.ratio, n.ratio) - 1)                                          AS jump_mag,
           greatest(k.tol_rel * abs(least(1 / n.ratio, n.ratio) - 1), k.tol_abs)        AS tol
    FROM nominal n CROSS JOIN k
    WHERE n.base_source = 'mktcap_neutral'
),
nom_dev AS (
    -- (a) 명목 세션의 잔여 (정지일 reference 행은 close = 직전 종가라 dev = |1/pf − 1|)
    SELECT c.event_id, abs(x.close / x.prev_trade_close / c.pf - 1) AS dev
    FROM cand c
    JOIN px x ON x.ticker = c.ticker AND x.date = c.nominal_date
    WHERE x.prev_trade_close > 0
),
step_a AS (
    SELECT c.event_id
    FROM cand c CROSS JOIN k
    LEFT JOIN nom_dev d ON d.event_id = c.event_id
    WHERE c.jump_mag <= k.tol_abs OR d.dev <= c.tol
),
win AS (
    -- (b) 창 안 거래 세션의 잔여 (개별)
    SELECT c.event_id, x.date, c.tol, abs(x.close / x.prev_trade_close / c.pf - 1) AS dev
    FROM cand c CROSS JOIN k
    JOIN cal w ON w.n BETWEEN c.n0 - k.win_before AND c.n0 + k.win_after
    JOIN px x ON x.ticker = c.ticker AND x.date = w.date
    WHERE x.price_kind = 'trade' AND x.prev_trade_close > 0
      AND c.event_id NOT IN (SELECT event_id FROM step_a)
),
step_b AS (
    SELECT event_id, date AS apply_date
    FROM win
    WHERE dev <= tol
    QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY dev, date) = 1
),
unm AS (
    -- (a)(b) 둘 다 실패한 후보 → 복합 사건 성분 탐색 대상
    SELECT c.* FROM cand c
    WHERE c.event_id NOT IN (SELECT event_id FROM step_a)
      AND c.event_id NOT IN (SELECT event_id FROM step_b)
),
links AS (
    SELECT a.event_id AS a_id, b.event_id AS b_id
    FROM unm a JOIN unm b ON b.ticker = a.ticker AND b.event_id <> a.event_id
    CROSS JOIN k
    WHERE abs(a.n0 - b.n0) <= k.win_after
),
comp AS (
    -- 연결 성분: 같은 티커에서 창 안으로 이어진 후보 전부가 한 성분, 루트 = 성분의 최소 event_id
    SELECT event_id, event_id AS root FROM unm
    UNION
    SELECT l.b_id, c.root FROM comp c JOIN links l ON l.a_id = c.event_id
),
comp_root AS (
    SELECT event_id, min(root) AS root FROM comp GROUP BY event_id
),
groups AS (
    SELECT r.root, any_value(u.ticker) AS ticker, product(u.pf) AS pf_prod,
           min(u.n0) AS n_min, max(u.n0) AS n_max
    FROM comp_root r JOIN unm u ON u.event_id = r.event_id
    GROUP BY r.root
    HAVING count(*) > 1
),
gwin AS (
    -- (c) 성분 창에서 계수 곱과 맞는 세션
    SELECT g.root, x.date,
           abs(x.close / x.prev_trade_close / g.pf_prod - 1)                              AS dev,
           greatest(k.tol_rel * abs(least(g.pf_prod, 1 / g.pf_prod) - 1), k.tol_abs)   AS tol
    FROM groups g CROSS JOIN k
    JOIN cal w ON w.n BETWEEN g.n_min - k.win_before AND g.n_max + k.win_after
    JOIN px x ON x.ticker = g.ticker AND x.date = w.date
    WHERE x.price_kind = 'trade' AND x.prev_trade_close > 0
),
step_c AS (
    SELECT root, date AS apply_date
    FROM gwin
    WHERE dev <= tol
    QUALIFY row_number() OVER (PARTITION BY root ORDER BY dev, date) = 1
),
resolved AS (
    SELECT c.event_id, c.ticker, c.rank_key,
           CASE WHEN a.event_id IS NOT NULL THEN c.nominal_date
                WHEN b.event_id IS NOT NULL THEN b.apply_date
                WHEN sc.root IS NOT NULL    THEN sc.apply_date
                ELSE c.nominal_date END                                   AS apply_date,
           CASE WHEN a.event_id IS NOT NULL THEN 'nominal'
                WHEN b.event_id IS NOT NULL THEN 'price_matched'
                WHEN sc.root IS NOT NULL    THEN 'price_matched_combined'
                ELSE 'unmatched' END                                      AS apply_basis
    FROM cand c
    LEFT JOIN step_a a ON a.event_id = c.event_id
    LEFT JOIN step_b b ON b.event_id = c.event_id
    LEFT JOIN comp_root cr ON cr.event_id = c.event_id
    LEFT JOIN step_c sc ON sc.root = cr.root
),
same_day AS (
    -- 개별 매칭 ok 이벤트가 같은 (ticker, apply_date) 에 2건 이상 → 우선순위 낮은 쪽 억제
    SELECT event_id,
           row_number() OVER (PARTITION BY ticker, apply_date ORDER BY rank_key) AS rn
    FROM resolved
    WHERE apply_basis IN ('nominal', 'price_matched')
),
final AS (
    SELECT n.*,
           coalesce(r.apply_date, n.nominal_date)                          AS apply_date,
           coalesce(r.apply_basis, 'nominal')                               AS apply_basis,
           CASE WHEN n.base_source <> 'mktcap_neutral' THEN n.base_source
                WHEN r.apply_basis = 'unmatched'        THEN 'no_price_match'
                WHEN sd.rn > 1                          THEN 'same_day_suppressed'
                ELSE 'mktcap_neutral' END                                   AS factor_source
    FROM nominal n
    LEFT JOIN resolved r ON r.event_id = n.event_id
    LEFT JOIN same_day sd ON sd.event_id = n.event_id
)
SELECT
    f.ticker,
    f.effective_date,
    f.event_id,
    f.corp_code,
    f.event_type,
    f.announce_date,
    f.apply_date,
    f.apply_basis,
    CASE WHEN f.factor_source = 'mktcap_neutral' THEN 1 / f.ratio ELSE 1 END         AS price_factor,
    CASE WHEN f.factor_source = 'mktcap_neutral' THEN f.ratio     ELSE 1 END         AS share_factor,
    f.factor_source,
    (f.factor_source = 'mktcap_neutral')                                             AS factor_ok,
    least(f.announce_date, nx.date)                                                  AS available_date,
    'derived'                                                                        AS available_basis,
    NULL::VARCHAR                                                                    AS reject_reason
FROM final f
JOIN cal ca ON ca.date = f.apply_date
LEFT JOIN cal nx ON nx.n = ca.n + 1
