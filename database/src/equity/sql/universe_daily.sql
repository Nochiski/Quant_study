-- universe_daily v4 (S03 존재·상태 + S03B 시장 파생 + S03B-2 유동성 순위 + S03C 무거래 이유) —
-- grain (date, ticker) · date_axis. DESIGN v1.2 §4-1 · GATES §3 ⑦ · §4 FX-1-012.
--
-- 격자 = security_span × trading_calendar (둘 다 앞서 커밋된 equity 테이블, _pinned/ 고정).
-- 행수 = Σ n_days — 구간 정의가 "캘린더 위 최대 연속 run" 이라 구간 안 거래일이 전부 존재일이다.
-- coverage_gap 행은 만들지 않는다(GAP-21): 캘린더 max = backfill_end 이므로 격자가 거기서 끝난다.
-- delisted 행도 만들지 않는다: 폐지일(= 마지막 존재일 + 1거래일)에는 구간이 없다.
--
-- 공시 신호(stg_disclosure.report_nm, has_ticker 만): 대괄호 접두어([기재정정] 등) 제거 뒤 매칭.
-- '매매거래정지및정지해제' 는 지정·해제 양쪽 신호(FX-1-014) — 그날 halt_state 는 빈 구간이라 false.
-- 접수일이 비거래일이면 다음 거래일 행에 얹는다(ASOF, 2건 실측). 캘린더 밖(backfill_end 이후)은 버린다.
--
-- 가격 축(S03B): 같은 날 equity price_daily 1행 — volume_shr(정지 종료의 거래 재개 축, EG20 으로 stage 와
-- 동일)·value_krw·mktcap_krw·price_kind. 가격 행이 없는 격자 날은 전부 NULL 이고 무거래로 세지 않는다.
--
-- 상태(전부 같은 구간 안 D 이전 정보만 — 재상장 구간으로 새지 않도록 (ticker, span_seq) 파티션):
--   halt_state       = [지정 신호일, min(해제 신호일, 다음 volume>0 거래일)) — 지정·해제 동일일이면
--                      빈 구간(그날 false). 여러 지정은 합집합: 마지막 지정 이후 닫힘 사건이 없으면 true.
--                      2026-09-01~ stg_master_daily 행이 있으면 is_trade_halt(측정)가 우선.
--   admin_state      = master 행 → is_admin_issue(measured)
--                    / KOSDAQ ∧ sect_tp 측정 → 소속부 ∈ {관리종목, 투자주의환기종목} (measured)
--                    / 그 외 상장주(KOSPI 전부·KOSDAQ 소속부 공란 2011-04 이전) → 지정 신호 후
--                      admin_window_td 거래일 창 (derived_kospi_window, 해제 공시 3건뿐이라 미반영)
--                    / ETF → false (convention: 관리종목 지정 대상 아님) / 그 외 → NULL (unknown)
--   liquidation_window = 정리매매 개시 신호일부터 구간 끝(= delist_date 직전 거래일)까지.
--                      master 행이 있으면 is_liquidation 우선.
--   no_trade_run     = 구간 안에서 D 까지(포함) 연속된 price_kind='reference'(volume_shr=0) 거래일 수.
--                      D 에 거래가 있으면 0. 가격 행 없음(또는 volume NULL → price_kind NULL)은 무거래로
--                      세지 않는다: 그날은 NULL 이고 run 을 끊는다(다음 무거래일은 1 부터) — NULL 을
--                      뒤로 전파하지 않는다.
--   no_trade_reason  = (S03C) 무거래(price_kind='reference') 행의 이유 1개. 거래 행·가격 행 없는 날은
--                      'none'(판정 대상이 아니다). 우선순위(먼저 맞는 것 하나):
--                        ① halt_state                      → 'halt_disclosed'
--                        ② liquidation_window              → 'liquidation'
--                        ③ 같은 티커 adj_factor 행(factor_ok 무관)의 apply_date 가
--                           [D − corp_action_lookback_sessions, D + corp_action_lookahead_sessions]
--                           세션 창 안                      → 'corp_action_window'
--                        ④ admin_state ∧ 지정 신호(signal_admin)가 D 전
--                           admin_signal_window_sessions 안 → 'admin'
--                        ⑤ 나머지                          → 'illiquid'
--                      ③ 은 사건 쪽에서 창을 펼친다(ca_win) — 적용일이 그 티커 구간 밖(폐지 기간)이어도
--                      구간 안 D 가 창에 들면 잡힌다. ok 여부를 안 보는 이유: 계수를 못 낸 사건일수록
--                      가격이 튀므로 오히려 창으로 막아야 한다(DESIGN S06-2 ⑸).
--   status           = suspended(halt_state ∨ no_trade_reason='corp_action_window'
--                                ∨ (no_trade_reason='illiquid' ∧ no_trade_run ≥ no_trade_run_k))
--                    / listed. k 는 _const(baseline universe_daily.no_trade_run_k, GATES §5-B8).
--                      S03C 변경: k 임계는 **이유를 모르는** 무거래에만 건다(사용자 09-05). 공시로
--                      설명되는 정지(halt·정리매매·관리종목)는 halt_state 만 정지로 남고, 나머지는
--                      universe_policy 의 investable 술어(NOT admin_state·NOT liquidation_window)가
--                      뺀다 — 같은 사실을 status 와 정책 양쪽에서 두 번 빼지 않는다.
--   mktcap_krw       = 같은 날 price_daily.mktcap_krw (원주가 × KRX 주식수, 조정 없음). 결측은 결측.
--   adv20_krw        = 같은 구간 [D−19, D] 20 거래일 value_krw 평균. 창에 value 있는 행이 20 미만이면
--                      NULL(구간 첫 19일·가격 결측일 뒤 19일). 랙은 뷰가 건다(available_date = date).
--                      기준가 날의 value_krw=0 도 평균에 들어간다(정지가 길면 자연히 0 에 수렴).
--                      창 폭 20 은 컬럼 이름에 박힌 정의이지만 SQL 리터럴 금지 규약 때문에 _const
--                      adv_window_td 로 들어온다 — 값을 바꾸면 컬럼 이름도 바꿔야 한다.
--   adv20_rank_pct   = (S03B-2) 같은 날 모집단(sec_type='common' ∧ status='listed' ∧ adv20_krw IS NOT NULL)
--                      안에서 adv20_krw 의 cume_dist — (adv20 ≤ 자기 행인 모집단 행수) / 모집단 행수,
--                      (0, 1]·클수록 유동성 큼·날짜별 최댓값 1. 동률은 큰 쪽 값을 같이 받는다(percent_rank 는
--                      최솟값이 0 이라 '0 초과' 를 못 만든다). 모집단 밖(ETF·우선주·외국주·정지·adv20 NULL)은
--                      NULL. 날짜 파티션 하나라 (ticker, span_seq) 창과 달리 재상장·구간 무관. 랙은 뷰가 건다.
--                      liquid 정책(universe_policy)이 adv20_rank_pct >= 1 − liquid_top_pct 로 자른다.
--   listing_age_days = D − 같은 날 stg_listing_daily.list_date (역일). 재상장은 구간마다 list_date 가
--                      다르므로(036220 2007-06-05 → 2024-03-13) security.list_date(최신 값)를 쓰지 않는다.
--                      list_date 가 없으면(ETF: listing 원장에 없음 P8) 구간 first_date 기준 — 2010-01-04
--                      이전 상장분은 하한이다. 어느 쪽인지는 security.list_date_basis / sec_type 로 가르고
--                      주식의 fallback 건수는 EG3_universe.n_listing_age_fallback_stock 이 기록한다.
-- available_date = date (default) — KRX 일별 스냅샷 관례(stage lag_known=false, T+1 08:00 은 카탈로그).
-- 파생 컬럼의 구성 행(price_daily [D−19, D])은 전부 available_date ≤ D 라 행 available 과 같다(EG2-P05).
WITH cal AS (
    SELECT date, row_number() OVER (ORDER BY date) AS td_seq
    FROM trading_calendar
),
grid AS (
    SELECT s.ticker, s.span_seq, s.first_date, c.date, c.td_seq
    FROM security_span s
    JOIN cal c ON c.date BETWEEN s.first_date AND s.last_date
),
ca_win AS (
    -- S03C ③ 의 창을 **사건 쪽에서** 펼친다: apply_date 가 [D − lookback, D + lookahead] 안이라는 조건은
    -- D 가 [apply − lookahead, apply + lookback] 안이라는 조건과 같다. 사건당 세션 51개(seed)라 집합이
    -- 작고, 격자와 (ticker, td_seq) 동등 조인으로 붙어 10.9M 행 위 범위 조인을 피한다. 격자가 아니라
    -- 캘린더에서 펼치므로 적용일이 그 티커의 구간 밖(폐지 기간)이어도 구간 안 D 가 창에 들면 잡힌다.
    -- apply_date 는 adj_factor 가 캘린더 세션으로 보장한다(EG3_adj_factor apply_date 불변식) —
    -- 그래도 캘린더 밖 행이 생기면 여기서 조용히 빠지므로 EG3_universe 가 건수를 기록한다.
    SELECT DISTINCT a.ticker, c.td_seq
    FROM adj_factor a
    CROSS JOIN _const k
    JOIN cal ca ON ca.date = a.apply_date
    JOIN cal c  ON c.td_seq BETWEEN ca.td_seq - k.corp_action_lookahead_sessions
                               AND ca.td_seq + k.corp_action_lookback_sessions
    -- 가격만 바뀐 기준가 사건(배당락·권리락, S06-2 unknown_price_only)은 거래정지를 동반하지 않는다 → 창 밖
    WHERE a.event_type <> 'unknown_price_only'
),
sig_raw AS (
    SELECT ticker, rcept_dt,
           regexp_replace(report_nm, '^(\[[^\]]*\])+', '') AS nm
    FROM stg_disclosure
    WHERE has_ticker
),
sig_day AS (
    SELECT ticker, rcept_dt,
           bool_or((nm LIKE '%매매거래정지%' AND nm NOT LIKE '%해제%')
                   OR nm LIKE '%매매거래정지및정지해제%')                       AS signal_halt,
           bool_or(nm LIKE '%정리매매 개시%')                                   AS signal_liquidation,
           bool_or((nm LIKE '%매매거래정지해제%' AND nm NOT LIKE '%정리매매 개시%')
                   OR nm LIKE '%매매거래정지및정지해제%')                       AS signal_halt_release,
           bool_or(nm LIKE '%관리종목%' AND nm NOT LIKE '%해제%')              AS signal_admin,
           bool_or(nm LIKE '%상장폐지%')                                        AS signal_delist
    FROM sig_raw
    GROUP BY ticker, rcept_dt
),
sig AS (
    SELECT d.ticker, c.date,
           bool_or(d.signal_halt)         AS signal_halt,
           bool_or(d.signal_halt_release) AS signal_halt_release,
           bool_or(d.signal_admin)        AS signal_admin,
           bool_or(d.signal_liquidation)  AS signal_liquidation,
           bool_or(d.signal_delist)       AS signal_delist
    FROM sig_day d
    ASOF JOIN cal c ON c.date >= d.rcept_dt
    GROUP BY d.ticker, c.date
),
base AS (
    SELECT g.ticker, g.span_seq, g.first_date, g.date, g.td_seq,
           l.market, l.sect_tp, l.sect_available, l.list_date,
           x.sec_type,
           coalesce(s.signal_halt, false)         AS signal_halt,
           coalesce(s.signal_halt_release, false) AS signal_halt_release,
           coalesce(s.signal_admin, false)        AS signal_admin,
           coalesce(s.signal_liquidation, false)  AS signal_liquidation,
           coalesce(s.signal_delist, false)       AS signal_delist,
           p.volume_shr, p.value_krw, p.mktcap_krw, p.price_kind,
           m.is_admin_issue, m.is_trade_halt, m.is_liquidation,
           (w.ticker IS NOT NULL)                 AS corp_action_near,
           k.admin_window_td, k.no_trade_run_k, k.adv_window_td,
           k.admin_signal_window_sessions
    FROM grid g
    LEFT JOIN stg_listing_daily l ON l.ticker = g.ticker AND l.date = g.date
    LEFT JOIN security x          ON x.ticker = g.ticker
    LEFT JOIN sig s               ON s.ticker = g.ticker AND s.date = g.date
    LEFT JOIN price_daily p       ON p.ticker = g.ticker AND p.date = g.date
    LEFT JOIN stg_master_daily m  ON m.ticker = g.ticker AND m.date = g.date
    LEFT JOIN ca_win w            ON w.ticker = g.ticker AND w.td_seq = g.td_seq
    CROSS JOIN _const k
),
run AS (
    SELECT b.*,
           max(td_seq) FILTER (WHERE signal_halt)         OVER w AS last_halt,
           max(td_seq) FILTER (WHERE signal_halt_release) OVER w AS last_release,
           max(td_seq) FILTER (WHERE volume_shr > 0)      OVER w AS last_trade,
           max(td_seq) FILTER (WHERE signal_admin)        OVER w AS last_admin,
           max(td_seq) FILTER (WHERE signal_liquidation)  OVER w AS last_liquidation,
           -- 마지막 '기준가 아님' 날(거래일 또는 가격 없는 날) — 무거래 run 의 시작점
           max(td_seq) FILTER (WHERE price_kind IS DISTINCT FROM 'reference') OVER w AS last_nonref,
           min(td_seq)                                    OVER w AS span_first_seq,
           count(value_krw)                               OVER w_adv AS n_value_adv,
           avg(value_krw)                                 OVER w_adv AS avg_value_adv
    FROM base b
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY td_seq
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
           w_adv AS (PARTITION BY ticker, span_seq ORDER BY td_seq
                     ROWS BETWEEN (adv_window_td - 1) PRECEDING AND CURRENT ROW)
),
state AS (
    SELECT r.*,
           coalesce(r.is_trade_halt,
                    r.last_halt IS NOT NULL
                    AND NOT coalesce(r.last_release >= r.last_halt, false)
                    AND NOT coalesce(r.last_trade > r.last_halt, false))       AS halt_state,
           CASE WHEN r.is_admin_issue IS NOT NULL THEN r.is_admin_issue
                WHEN r.market = 'KOSDAQ' AND r.sect_available
                     THEN r.sect_tp IN ('관리종목(소속부없음)', '투자주의환기종목(소속부없음)')
                WHEN r.market IS NOT NULL
                     THEN r.last_admin IS NOT NULL AND r.td_seq - r.last_admin < r.admin_window_td
                WHEN r.sec_type = 'etf' THEN false
                ELSE NULL END                                                    AS admin_state,
           CASE WHEN r.is_admin_issue IS NOT NULL THEN 'measured'
                WHEN r.market = 'KOSDAQ' AND r.sect_available THEN 'measured'
                WHEN r.market IS NOT NULL THEN 'derived_kospi_window'
                WHEN r.sec_type = 'etf' THEN 'convention'
                ELSE 'unknown' END                                               AS admin_state_basis,
           coalesce(r.is_liquidation, r.last_liquidation IS NOT NULL)            AS liquidation_window,
           CASE WHEN r.price_kind = 'reference'
                     THEN r.td_seq - coalesce(r.last_nonref, r.span_first_seq - 1)
                WHEN r.price_kind = 'trade' THEN 0
                ELSE NULL END                                                    AS no_trade_run,
           CASE WHEN r.n_value_adv = r.adv_window_td THEN r.avg_value_adv END    AS adv20_krw,
           date_diff('day', coalesce(r.list_date, r.first_date), r.date)         AS listing_age_days
    FROM run r
),
reasoned AS (
    -- S03C. CASE 의 순서가 곧 우선순위다 — 한 행에 여러 사유가 겹치면 위쪽이 이긴다.
    -- admin 은 '지정 신호가 최근' 일 때만 이유가 된다: 관리종목 상태는 몇 달씩 이어지는데 그 기간의
    -- 무거래를 전부 admin 으로 부르면 비유동(illiquid)과 구분이 사라진다. 창은 [지정일, 지정일 + n 세션]
    -- (td_seq 차 ≤ n, 지정일 당일 포함) — last_admin 은 같은 (ticker, span_seq) 안 마지막 신호일이라
    -- 재상장 구간으로 새지 않는다.
    SELECT s.*,
           CASE WHEN s.price_kind IS DISTINCT FROM 'reference' THEN 'none'
                WHEN s.halt_state                              THEN 'halt_disclosed'
                WHEN s.liquidation_window                      THEN 'liquidation'
                WHEN s.corp_action_near                        THEN 'corp_action_window'
                WHEN coalesce(s.admin_state, false)
                     AND s.last_admin IS NOT NULL
                     AND s.td_seq - s.last_admin <= s.admin_signal_window_sessions
                                                               THEN 'admin'
                ELSE 'illiquid' END                                               AS no_trade_reason
    FROM state s
),
scored AS (
    SELECT r.*,
           CASE WHEN r.halt_state
                     OR r.no_trade_reason = 'corp_action_window'
                     OR (r.no_trade_reason = 'illiquid' AND r.no_trade_run >= r.no_trade_run_k)
                THEN 'suspended' ELSE 'listed' END                                AS status
    FROM reasoned r
),
pop AS (
    -- adv20_rank_pct 모집단(S03B-2): 같은 날 보통주 ∧ listed ∧ adv20 있음. sec_type NULL 은 없지만
    -- (EG3_universe n_sec_type_null) 있더라도 모집단 밖으로 가게 coalesce 한다.
    SELECT c.*,
           coalesce(c.sec_type = 'common' AND c.status = 'listed' AND c.adv20_krw IS NOT NULL,
                    false)                                                        AS in_pop
    FROM scored c
),
ranked AS (
    -- 날짜별 창 1개(PARTITION BY date, in_pop): 모집단 밖 행은 자기들끼리 한 파티션에 모여 순위 계산에
    -- 섞이지 않고, 결과는 CASE 로 NULL 이 된다. cume_dist = (adv20 ≤ 자기 행인 모집단 행수) / 모집단
    -- 행수 — 동률은 같은 값(큰 쪽), 최댓값 행은 항상 1, 최솟값 행은 1/n > 0.
    SELECT p.*,
           CASE WHEN p.in_pop
                THEN cume_dist() OVER (PARTITION BY p.date, p.in_pop ORDER BY p.adv20_krw) END
                                                                                  AS adv20_rank_pct
    FROM pop p
)
SELECT
    date,
    ticker,
    status,
    market,
    sec_type,
    halt_state,
    admin_state,
    admin_state_basis,
    liquidation_window,
    signal_halt,
    signal_halt_release,
    signal_admin,
    signal_liquidation,
    signal_delist,
    is_admin_issue                                          AS admin_flag,
    mktcap_krw,
    adv20_krw,
    adv20_rank_pct,
    no_trade_reason,
    listing_age_days,
    no_trade_run,
    date                                                    AS available_date,
    'default'                                               AS available_basis,
    NULL::VARCHAR                                           AS reject_reason
FROM ranked
