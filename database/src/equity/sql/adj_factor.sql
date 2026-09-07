-- adj_factor (S06, 2차 = apply_date · S06-2 v3 = KRX 기준가 원천) — 조정계수.
-- grain (ticker, effective_date, event_id). DESIGN v1.2 §4-2 · GATES §3-⑩ · EG3-P04 · EG8.
--
-- 원천: equity corp_event(MVP 4종 split · reverse_split · bonus · capred 만 계수를 낸다) · trading_calendar
--   (세션 축) · price_daily(close · price_kind — 계수를 **어느 세션에 적용할지** 가격으로 가린다 ·
--   base_price_krw · shares_out — S06-2 KRX 기준가 원천) · stg_event_cr(감자 결정공시 본문 cr_mth·cr_rs —
--   유·무상 축) · security(sec_type — ETF 는 기준가 원천 밖 · corp_code — 신규 행) · security_span(구간
--   첫날 — 재상장 첫 행은 직전 행이 옛 구간이라 기준가 후보 밖).
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
--   dev = |close_t / 직전 **행** 종가 / price_factor − 1| 로 적용 세션을 가린다. 분모가 직전 거래 종가가
--   아니라 직전 행(참고가 행 포함)인 이유(3차, 서버 2차 실측): KRX 는 정지 중 참고가(reference) 행의 close 에
--   새 기준가를 먼저 싣는다 — 시계열의 점프는 거래 재개일이 아니라 그 전 무거래 행에서 일어나고, 뷰가
--   조정하는 대상이 price_daily 의 행 시계열이므로 매칭·EG8 전부 행 대 행이어야 한다(거래 유무 무관).
--   후보 세션도 거래 행만이 아니라 창 안 모든 가격 행이다. 허용치는 조정 후 잔여 기준
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
--   no_share_change      : ratio = 1 — 주식수가 안 바뀐 사건(액면가만 바뀐 KRX 관측 등). 계수 1 이라 조정할
--                          것이 없고, ok 로 두면 EG8 이 그날 원수익률(정지 뒤 재개일 ±)을 점프로 잰다
--   no_price_match       : 위 (d)
--   same_day_suppressed  : 위 같은 apply_date 규칙
--   사유 우선순위: near_dup_suppressed > ratio_null > capred_paid > no_share_change > no_price_match
--   > same_day_suppressed.
--   ok 가 아닌 행의 apply_date 는 명목 세션, apply_basis 는 no_price_match 만 'unmatched' 나머지 'nominal'.
--
-- available_date = min(announce_date, apply_date 다음 세션) · basis derived — 공시가 없어도 KRX 가격·주식수
--   변화가 그 다음 세션에 관측된다. 회고 기재 원천(자본변동, announce 가 수년 뒤)은 available < announce
--   가 정상이라 EG2-P02 announce 축을 못 쓴다(content_date_column 없음) — EG3_adj_factor 가 독립 재계산.
--
-- ── S06-2 KRX 기준가 원천 `krx_base_price` (v3, 09-05) — 위 사건 매칭은 기준가 사건이 없을 때의 폴백 ──
--   price_daily.base_price_krw(= close − change_krw) 가 그날 KRX 기준가라, 사건이 실제로 가격에 적용된 세션과
--   비율을 KRX 가 직접 준다. 후보 = 같은 티커의 직전 **행** close 대비 r = base / prev_close 가 1 ± base_price_tol_rel
--   밖인 (ticker, date). 후보에서 빼는 것: ETF(security.sec_type = 'etf' — 분배락이 기준가를 바꾸고 상장좌수는
--   설정·환매로 매일 바뀌어 주식수 변화가 사건의 증거가 아니다; 절단본 069500 후보 36 중 14 가 곱 검사를 우연히
--   통과한다) · 구간 첫날(security_span.first_date — 재상장 첫 행의 '직전 행' 은 수년 전 옛 구간 종가).
--   같은 날 주식수 변화비 S = shares_out / 직전 행 shares_out (|S − 1| > corp_event.krx_share_change_tol 일 때만).
--   분류(우선순위):
--   (a) 사건 교체 — 계수 후보(base_source mktcap_neutral, same_day 억제 제외; 성분 combined 은 한 단위로 곱)의
--       apply_date(미매칭은 명목 세션) ± base_match_window_sessions 안에 있고 |r / pf − 1| ≤ tol(위 판정식과 같은
--       허용치)이면 그 단위의 계수를 기준가로 교체: price_factor = r · share_factor = S(없으면 1/r) · apply_date =
--       그날 · apply_basis 'krx_base_price'. 단위 ↔ 후보는 1:1(dev, 거리 최소). 성분은 멤버 전부 같은 날로 옮기고
--       루트(min event_id)가 잔여 r / Π(다른 멤버 pf) 를 갖는다(다른 멤버는 원래 pf·ratio). 곱 |r × S − 1| ≤
--       factor_product_tol_base 면 mktcap_neutral, 밖이면 krx_base_inconsistent(ok=false, 계수 1). 기존
--       no_price_match 사건도 창 안에 기준가 사건이 있으면 여기서 살아난다.
--       기준가 후보가 ok 사건의 apply_date 에 있는데 비율이 안 맞아 묶이지 않은 사건은 기준가가 계수를 반증한
--       것이라 krx_base_inconsistent 로 내린다(같은 날 두 원천이 다른 값을 낸 채 둘 다 ok 일 수 없다 — 이중 적용).
--   (b) 사건과 안 맞고 S 가 있으면 신규 행 event_id '<ticker>:krx_base:<date>' · event_type 'unknown_krx' ·
--       effective = announce = apply = date · corp_code = security.corp_code · 계수 (r, S) · ok 는 곱 검사
--       (2010~2014 DART 공백기의 액면분할·감자 변경상장일이 여기로 온다).
--   (c) 사건과 안 맞고 S 없음 · 직전 행이 reference(전일 무거래) → 정지 재개 가격 재발견, 행 없음(EG3 기록형).
--   (d) 그 외(주식수 불변 · 전일 거래) → 신규 행 event_type 'unknown_price_only' · ok=false · 계수 1
--       (유상증자 권리락·주식배당락 등 MVP 밖 — 시총 불변이 아니라 계수를 만들지 않는다).
--   (b)(d) 의 available_date = 다음 세션 · (a) 는 min(announce, 다음 세션) 그대로. 상수는 전부 _const.
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
           CAST(price_match_tol_abs AS DOUBLE)           AS tol_abs,
           CAST(base_price_tol_rel AS DOUBLE)            AS base_tol,        -- S06-2 기준가 원천
           CAST(base_match_window_sessions AS INTEGER)   AS base_win,
           CAST(factor_product_tol_base AS DOUBLE)       AS prod_tol,
           CAST(krx_share_change_tol AS DOUBLE)          AS share_tol
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
                WHEN r.ratio = 1              THEN 'no_share_change'
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
-- ── 가격 축: 후보 티커의 가격 행 + 직전 행 종가(참고가 행 포함, 행 대 행) ────────
px AS (
    SELECT p.ticker, p.date, p.close, p.price_kind,
           lag(p.close) OVER (PARTITION BY p.ticker ORDER BY p.date)                    AS prev_close
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
    -- (a) 명목 세션의 잔여 (기준가가 안 바뀐 정지일 reference 행은 close = 직전 행 종가라 dev = |1/pf − 1|)
    SELECT c.event_id, abs(x.close / x.prev_close / c.pf - 1) AS dev
    FROM cand c
    JOIN px x ON x.ticker = c.ticker AND x.date = c.nominal_date
    WHERE x.prev_close > 0
),
step_a AS (
    SELECT c.event_id
    FROM cand c CROSS JOIN k
    LEFT JOIN nom_dev d ON d.event_id = c.event_id
    WHERE c.jump_mag <= k.tol_abs OR d.dev <= c.tol
),
win AS (
    -- (b) 창 안 가격 행(참고가 행 포함)의 잔여 (개별)
    SELECT c.event_id, x.date, c.tol, abs(x.close / x.prev_close / c.pf - 1) AS dev
    FROM cand c CROSS JOIN k
    JOIN cal w ON w.n BETWEEN c.n0 - k.win_before AND c.n0 + k.win_after
    JOIN px x ON x.ticker = c.ticker AND x.date = w.date
    WHERE x.prev_close > 0
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
           abs(x.close / x.prev_close / g.pf_prod - 1)                                    AS dev,
           greatest(k.tol_rel * abs(least(g.pf_prod, 1 / g.pf_prod) - 1), k.tol_abs)   AS tol
    FROM groups g CROSS JOIN k
    JOIN cal w ON w.n BETWEEN g.n_min - k.win_before AND g.n_max + k.win_after
    JOIN px x ON x.ticker = g.ticker AND x.date = w.date
    WHERE x.prev_close > 0
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
),
-- ── S06-2 KRX 기준가 원천 ───────────────────────────────────────────────────
bpx AS (
    -- 비ETF 전 가격 행 + 직전 행(참고가 행 포함)의 close·shares_out·price_kind
    SELECT p.ticker, p.date, p.close, p.base_price_krw, p.shares_out, p.price_kind, c.n,
           lag(p.close) OVER w        AS prev_close,
           lag(p.shares_out) OVER w   AS prev_shares,
           lag(p.price_kind) OVER w   AS prev_kind
    FROM price_daily p
    JOIN cal c ON c.date = p.date
    WHERE p.ticker NOT IN (SELECT ticker FROM security WHERE sec_type = 'etf')
    WINDOW w AS (PARTITION BY p.ticker ORDER BY p.date)
),
bp AS (
    -- 후보: r = 기준가 / 직전 행 close 가 1 ± base_tol 밖. 구간 첫날(재상장 첫 행)은 제외
    SELECT x.ticker, x.date, x.n, x.prev_kind,
           x.base_price_krw / x.prev_close                                             AS r,
           CASE WHEN x.prev_shares > 0 AND abs(x.shares_out / x.prev_shares - 1) > k.share_tol
                THEN x.shares_out / x.prev_shares END                                  AS share_ratio
    FROM bpx x CROSS JOIN k
    WHERE x.prev_close > 0 AND x.base_price_krw IS NOT NULL
      AND abs(x.base_price_krw / x.prev_close - 1) > k.base_tol
      AND NOT EXISTS (SELECT 1 FROM security_span sp
                      WHERE sp.ticker = x.ticker AND sp.first_date = x.date)
),
elig AS (
    -- (a) 대상 단위: 계수 후보(same_day 억제 제외). 성분(combined)은 (ticker, apply_date) 한 단위
    SELECT f.event_id, f.ticker, f.apply_date, f.factor_source, 1 / f.ratio AS pf, f.ratio,
           CASE WHEN f.apply_basis = 'price_matched_combined'
                THEN f.ticker || '@' || CAST(f.apply_date AS VARCHAR) ELSE f.event_id END AS unit_id
    FROM final f
    WHERE f.factor_source IN ('mktcap_neutral', 'no_price_match')
),
unit AS (
    SELECT unit_id, any_value(ticker) AS ticker, any_value(apply_date) AS apply_date,
           product(pf) AS pf, product(ratio) AS sf, count(*) AS n_members, min(event_id) AS root_id
    FROM elig GROUP BY unit_id
),
pair AS (
    SELECT u.unit_id, b.ticker, b.date, b.r, abs(b.r / u.pf - 1) AS dev, abs(b.n - cu.n) AS dist,
           greatest(k.tol_rel * abs(least(u.pf, 1 / u.pf) - 1), k.tol_abs)              AS tol
    FROM unit u CROSS JOIN k
    JOIN cal cu ON cu.date = u.apply_date
    JOIN bp b ON b.ticker = u.ticker AND abs(b.n - cu.n) <= k.base_win
),
matched AS (
    -- 단위 ↔ 기준가 후보 1:1 — 양쪽 모두에서 (dev, 거리) 최소인 쌍만
    SELECT unit_id, ticker, date, r
    FROM pair
    WHERE dev <= tol
    QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY dev, dist, date) = 1
        AND row_number() OVER (PARTITION BY ticker, date ORDER BY dev, dist, unit_id) = 1
),
replaced AS (
    SELECT e.event_id, m.date AS apply_date,
           CASE WHEN u.n_members = 1        THEN m.r
                WHEN e.event_id = u.root_id THEN m.r / (u.pf / e.pf)
                ELSE e.pf END                                                          AS price_factor,
           CASE WHEN u.n_members = 1        THEN coalesce(b.share_ratio, 1 / m.r)
                WHEN e.event_id = u.root_id THEN coalesce(b.share_ratio, 1 / m.r) / (u.sf / e.ratio)
                ELSE e.ratio END                                                       AS share_factor,
           abs(m.r * coalesce(b.share_ratio, 1 / m.r) - 1) <= k.prod_tol                AS product_ok
    FROM elig e
    JOIN unit u ON u.unit_id = e.unit_id
    JOIN matched m ON m.unit_id = e.unit_id
    JOIN bp b ON b.ticker = m.ticker AND b.date = m.date
    CROSS JOIN k
),
conflict AS (
    -- ok 사건의 apply_date 에 기준가 후보가 있는데 (a) 로 묶이지 않음 → 기준가가 계수를 반증
    SELECT e.event_id
    FROM elig e
    JOIN bp b ON b.ticker = e.ticker AND b.date = e.apply_date
    WHERE e.factor_source = 'mktcap_neutral'
      AND e.unit_id NOT IN (SELECT unit_id FROM matched)
),
bp_new AS (
    -- (a) 에 쓰이지 않은 후보 → (b) unknown_krx · (c) 재발견(행 없음) · (d) unknown_price_only
    SELECT b.ticker, b.date, b.r, b.share_ratio,
           CASE WHEN b.share_ratio IS NOT NULL THEN 'unknown_krx'
                WHEN b.prev_kind = 'reference'  THEN NULL
                ELSE 'unknown_price_only' END                                          AS event_type
    FROM bp b
    WHERE NOT EXISTS (SELECT 1 FROM matched m WHERE m.ticker = b.ticker AND m.date = b.date)
),
out_events AS (
    SELECT f.ticker, f.effective_date, f.event_id, f.corp_code, f.event_type, f.announce_date,
           coalesce(rp.apply_date, f.apply_date)                                       AS apply_date,
           CASE WHEN rp.event_id IS NOT NULL THEN 'krx_base_price' ELSE f.apply_basis END AS apply_basis,
           CASE WHEN rp.event_id IS NOT NULL AND rp.product_ok THEN 'mktcap_neutral'
                WHEN rp.event_id IS NOT NULL                   THEN 'krx_base_inconsistent'
                WHEN cf.event_id IS NOT NULL                   THEN 'krx_base_inconsistent'
                ELSE f.factor_source END                                               AS factor_source,
           CASE WHEN rp.event_id IS NOT NULL THEN rp.price_factor ELSE 1 / f.ratio END AS pf_raw,
           CASE WHEN rp.event_id IS NOT NULL THEN rp.share_factor ELSE f.ratio END     AS sf_raw,
           FALSE                                                                       AS is_new
    FROM final f
    LEFT JOIN replaced rp ON rp.event_id = f.event_id
    LEFT JOIN conflict cf ON cf.event_id = f.event_id
),
out_new AS (
    SELECT b.ticker, b.date AS effective_date,
           b.ticker || ':krx_base:' || CAST(b.date AS VARCHAR)                         AS event_id,
           s.corp_code, b.event_type, b.date AS announce_date, b.date AS apply_date,
           'krx_base_price'                                                            AS apply_basis,
           CASE WHEN b.event_type = 'unknown_krx' AND abs(b.r * b.share_ratio - 1) <= k.prod_tol
                     THEN 'mktcap_neutral'
                WHEN b.event_type = 'unknown_krx' THEN 'krx_base_inconsistent'
                ELSE 'unknown_price_only' END                                          AS factor_source,
           b.r                                                                         AS pf_raw,
           b.share_ratio                                                               AS sf_raw,
           TRUE                                                                        AS is_new
    FROM bp_new b CROSS JOIN k
    LEFT JOIN security s ON s.ticker = b.ticker
    WHERE b.event_type IS NOT NULL
),
-- ── 재개 없음 축 (DEFECT-10) ────────────────────────────────────────────────
-- 티커별 마지막 실거래 세션. `price_kind='trade'` 만 센다 — 정지일 참고가 행(volume 0)은
-- 어댑터가 Bar 로 내지 않으므로 소비자에게는 존재하지 않는 세션이다.
last_trade AS (
    SELECT p.ticker, max(p.date) AS last_trade_date
    FROM price_daily p
    WHERE p.price_kind = 'trade'
    GROUP BY 1
),
out_all AS (
    SELECT * FROM out_events
    UNION ALL
    SELECT * FROM out_new
)
SELECT
    o.ticker,
    o.effective_date,
    o.event_id,
    o.corp_code,
    o.event_type,
    o.announce_date,
    o.apply_date,
    o.apply_basis,
    CASE WHEN o.factor_source = 'mktcap_neutral' THEN o.pf_raw ELSE 1 END             AS price_factor,
    CASE WHEN o.factor_source = 'mktcap_neutral' THEN o.sf_raw ELSE 1 END             AS share_factor,
    o.factor_source,
    (o.factor_source = 'mktcap_neutral')                                             AS factor_ok,
    -- DEFECT-10: 적용일 이후 그 종목의 실거래 세션이 하나도 없으면 참. 커널은 사건 시점 이후
    -- 바가 있는 세션을 반드시 찾으므로(engine/loop.py `_settlement_session`) 이런 행을 그냥
    -- 내보내면 run 전체가 죽는다. equity 는 버리지 않고 **사실을 싣는다** — 정지 중 감자는
    -- 보유 수량을 실제로 바꾸고, 26건 중 18건은 거래소가 정지 기간에 기준가를 공표했다.
    -- 소비자가 이 열로 거르거나 정산 정책을 고른다.
    (lt.last_trade_date IS NULL OR o.apply_date > lt.last_trade_date)                 AS no_bar_after_apply,
    -- 캘린더 마지막 세션의 기준가 사건은 다음 세션이 없다(서버 09-05 EG2 NULL 1) → 적용일(당일)로.
    coalesce(CASE WHEN o.is_new THEN nx.date ELSE least(o.announce_date, nx.date) END,
             o.apply_date)                                                            AS available_date,
    'derived'                                                                        AS available_basis,
    NULL::VARCHAR                                                                    AS reject_reason
FROM out_all o
JOIN cal ca ON ca.date = o.apply_date
LEFT JOIN cal nx ON nx.n = ca.n + 1
LEFT JOIN last_trade lt ON lt.ticker = o.ticker
