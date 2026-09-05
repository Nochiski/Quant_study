-- universe_daily v1 (S03 존재·상태) — grain (date, ticker) · date_axis. DESIGN v1.2 §4-1 · GATES §3 ⑦.
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
--   status           = suspended(halt_state) / listed. 무거래 연속 판정은 S03B.
-- available_date = date (default) — KRX 일별 스냅샷 관례(stage lag_known=false, T+1 08:00 은 카탈로그).
WITH cal AS (
    SELECT date, row_number() OVER (ORDER BY date) AS td_seq
    FROM trading_calendar
),
grid AS (
    SELECT s.ticker, s.span_seq, c.date, c.td_seq
    FROM security_span s
    JOIN cal c ON c.date BETWEEN s.first_date AND s.last_date
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
vol AS (
    SELECT ticker, date, volume_shr FROM stg_price_daily
    UNION ALL
    SELECT ticker, date, volume_shr FROM stg_etf_price_daily
),
base AS (
    SELECT g.ticker, g.span_seq, g.date, g.td_seq,
           l.market, l.sect_tp, l.sect_available,
           x.sec_type,
           coalesce(s.signal_halt, false)         AS signal_halt,
           coalesce(s.signal_halt_release, false) AS signal_halt_release,
           coalesce(s.signal_admin, false)        AS signal_admin,
           coalesce(s.signal_liquidation, false)  AS signal_liquidation,
           coalesce(s.signal_delist, false)       AS signal_delist,
           v.volume_shr,
           m.is_admin_issue, m.is_trade_halt, m.is_liquidation
    FROM grid g
    LEFT JOIN stg_listing_daily l ON l.ticker = g.ticker AND l.date = g.date
    LEFT JOIN security x          ON x.ticker = g.ticker
    LEFT JOIN sig s               ON s.ticker = g.ticker AND s.date = g.date
    LEFT JOIN vol v               ON v.ticker = g.ticker AND v.date = g.date
    LEFT JOIN stg_master_daily m  ON m.ticker = g.ticker AND m.date = g.date
),
run AS (
    SELECT b.*,
           max(td_seq) FILTER (WHERE signal_halt)         OVER w AS last_halt,
           max(td_seq) FILTER (WHERE signal_halt_release) OVER w AS last_release,
           max(td_seq) FILTER (WHERE volume_shr > 0)      OVER w AS last_trade,
           max(td_seq) FILTER (WHERE signal_admin)        OVER w AS last_admin,
           max(td_seq) FILTER (WHERE signal_liquidation)  OVER w AS last_liquidation
    FROM base b
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY td_seq
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
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
                     THEN r.last_admin IS NOT NULL AND r.td_seq - r.last_admin < k.admin_window_td
                WHEN r.sec_type = 'etf' THEN false
                ELSE NULL END                                                    AS admin_state,
           CASE WHEN r.is_admin_issue IS NOT NULL THEN 'measured'
                WHEN r.market = 'KOSDAQ' AND r.sect_available THEN 'measured'
                WHEN r.market IS NOT NULL THEN 'derived_kospi_window'
                WHEN r.sec_type = 'etf' THEN 'convention'
                ELSE 'unknown' END                                               AS admin_state_basis,
           coalesce(r.is_liquidation, r.last_liquidation IS NOT NULL)            AS liquidation_window
    FROM run r
    CROSS JOIN _const k
)
SELECT
    date,
    ticker,
    CASE WHEN halt_state THEN 'suspended' ELSE 'listed' END AS status,
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
    date                                                    AS available_date,
    'default'                                               AS available_basis,
    NULL::VARCHAR                                           AS reject_reason
FROM state
