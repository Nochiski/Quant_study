-- corp_event (S05, MVP-B) — grain event_id. 기업행위 4종: split · reverse_split · bonus · capred.
-- DESIGN v1.2 §4-2 · GATES v1.0 §3-⑨ · EG7-P08.
--
-- 원천 5 (선언 등록표 = rules_s05.SOURCES, EG1 우변은 그 표의 합):
--   event_fric   무상증자 결정(DS005)            → bonus
--   event_pifric 유무상증자 결정의 무상 부분(DS005) → bonus
--   event_cr     감자 결정(DS005)                → capred
--   capital      정기보고서 증자(감자)현황(비집계) → bonus(무상증자) · capred(감자)
--   krx_listing  KRX 액면가 변경일(par_value_krw 가 직전 거래일과 다름) → split · reverse_split
--
-- 법인 → 티커 전개: corp_ticker 로 corp_code 의 상장 종류주를 편다. 결정공시·자본변동은 주식 종류
-- 축(보통주 / 기타·우선주)을 따로 싣고 있으므로 class-row 단위로 전개한다 —
--   common    ← is_common(주식종류 '보통주') 또는 비KR7 단독 티커(외국주권)
--   preferred ← KR7 그룹의 비보통주(구형·신형·종류 우선주)
-- leg 가 없는 class-row(상환전환우선주·미상장 우선주·corp 미매핑)는 ticker NULL → ticker_unresolved.
--
-- effective_date = **가격 축 효력일** (계수는 date >= effective_date 인 가격에 곱한다, DESIGN §4-2):
--   bonus  : 권리락일 = 신주배정기준일 직전 거래일(trading_calendar) — 절단본 실측 247540 2022-06-27
--            (기준일 06-28, 종가 497,400 → 135,900). DART 자본변동의 isu_dcrs_de 도 배정기준일이라
--            두 원천이 같은 effective_date 로 접힌다.
--   capred : 감자기준일(cr_std / isu_dcrs_de). 기준일은 매매거래정지 구간 안이라 그 뒤 첫 거래일
--            (변경상장일)부터 계수가 적용된다.
--   split·reverse_split : KRX 액면가가 바뀐 날 = 변경상장일(정지 해제 첫 거래일). 005930 2018-05-04.
-- ratio = share_factor = 이벤트 후 주식수 / 이벤트 전 주식수 (1주 기준). 50:1 분할 50 · 무상증자
--   1주당 0.2 배정 1.2 · 10:1 감자 0.1. price_factor 는 S06 이 정한다(감자는 두 축 상이, FX-2-004).
--   자본변동(capital) 행은 전 주식수를 싣지 않아 ratio NULL 이다("결측은 결측", 원칙 ④) —
--   결정공시와 접히면 결정공시의 ratio 가 남는다.
-- announce_date = DART rcept_dt(stage available_date; 참조표 미스는 rcept_no 앞 8자리 접수일자로
--   보강, basis derived) / KRX 는 관측일 = 효력일. available_date = announce_date.
-- dedup 축 (ticker, event_type, effective_date): reject 없는 후보만 접는다. 우선순위 =
--   결정공시 > 자본변동 > KRX → announce_date 오름차순 → rcept_no. 접힌 수는 n_src_rows.
--   reject 후보는 접지 않고 각각 격리한다 → Σ원천 = Σ_out n_src_rows + n_reject (EG1).
-- 숫자 리터럴은 0·1·2 만 쓴다(test_sql파일에_상수_하드코딩_없음) — 임계는 _const 로만 들어온다.
WITH cal AS (
    SELECT date, prev_td FROM trading_calendar
),
legs AS (
    SELECT corp_code, ticker,
           CASE WHEN is_common OR isin8 NOT LIKE 'KR7%' THEN 'common' ELSE 'preferred' END AS cls
    FROM corp_ticker
    WHERE corp_code IS NOT NULL
),
-- ── DART class-row (원천별 1~2행) ────────────────────────────────────────────
fric AS (
    SELECT rcept_no, corp_code, 'event_fric' AS source, 'bonus' AS event_type, 'common' AS cls,
           nstk_asstd AS basis_date,
           CASE WHEN nstk_ascnt_ps_ostk_ratio IS NOT NULL THEN 1 + nstk_ascnt_ps_ostk_ratio
                WHEN bfic_tisstk_ostk > 0 THEN 1 + nstk_ostk_cnt / bfic_tisstk_ostk END AS ratio,
           TRUE AS ratio_given, available_date, available_basis
    FROM stg_event_fric
    UNION ALL
    SELECT rcept_no, corp_code, 'event_fric', 'bonus', 'preferred', nstk_asstd,
           CASE WHEN nstk_ascnt_ps_estk_ratio IS NOT NULL THEN 1 + nstk_ascnt_ps_estk_ratio END,
           TRUE, available_date, available_basis
    FROM stg_event_fric
    WHERE coalesce(nstk_estk_cnt, 0) > 0 OR nstk_ascnt_ps_estk_ratio IS NOT NULL
),
pifric AS (
    SELECT rcept_no, corp_code, 'event_pifric' AS source, 'bonus' AS event_type, 'common' AS cls,
           fric_nstk_asstd AS basis_date,
           CASE WHEN fric_nstk_ascnt_ps_ostk_ratio IS NOT NULL THEN 1 + fric_nstk_ascnt_ps_ostk_ratio
                WHEN fric_bfic_tisstk_ostk > 0
                     THEN 1 + fric_nstk_ostk_cnt / fric_bfic_tisstk_ostk END AS ratio,
           TRUE AS ratio_given, available_date, available_basis
    FROM stg_event_pifric
    UNION ALL
    SELECT rcept_no, corp_code, 'event_pifric', 'bonus', 'preferred', fric_nstk_asstd,
           CASE WHEN fric_nstk_ascnt_ps_estk_ratio IS NOT NULL
                THEN 1 + fric_nstk_ascnt_ps_estk_ratio END,
           TRUE, available_date, available_basis
    FROM stg_event_pifric
    WHERE coalesce(fric_nstk_estk_cnt, 0) > 0 OR fric_nstk_ascnt_ps_estk_ratio IS NOT NULL
),
cr AS (
    -- ratio 는 감자 전후 발행총수 비(stage G3 불변식 cr_shares_increase 의 두 컬럼). 감자비율(%)
    -- 폴백은 두지 않는다 — 단위 환산 상수(100)를 SQL 에 쓰지 않는다는 규약 때문이며, 전후 주식수가
    -- 없는 결정공시는 ratio_unparsed 로 격리된다.
    SELECT rcept_no, corp_code, 'event_cr' AS source, 'capred' AS event_type, 'common' AS cls,
           cr_std AS basis_date,
           CASE WHEN bfcr_tisstk_ostk > 0 THEN atcr_tisstk_ostk / bfcr_tisstk_ostk END AS ratio,
           TRUE AS ratio_given, available_date, available_basis
    FROM stg_event_cr
    UNION ALL
    SELECT rcept_no, corp_code, 'event_cr', 'capred', 'preferred', cr_std,
           CASE WHEN bfcr_tisstk_estk > 0 THEN atcr_tisstk_estk / bfcr_tisstk_estk END,
           TRUE, available_date, available_basis
    FROM stg_event_cr
    WHERE coalesce(crstk_estk_cnt, 0) > 0 OR cr_rt_estk_pct IS NOT NULL
),
capital AS (
    SELECT rcept_no, corp_code, 'capital' AS source,
           CASE WHEN isu_dcrs_stle LIKE '%감자%' THEN 'capred' ELSE 'bonus' END AS event_type,
           CASE WHEN isu_dcrs_stock_knd = '보통주' THEN 'common'
                WHEN isu_dcrs_stock_knd = '우선주' THEN 'preferred'
                ELSE 'none' END AS cls,
           isu_dcrs_de AS basis_date,
           NULL::DOUBLE AS ratio, FALSE AS ratio_given, available_date, available_basis
    FROM stg_capital
    WHERE isu_dcrs_stle LIKE '%감자%'
       OR (isu_dcrs_stle LIKE '%무상증자%' AND isu_dcrs_stle NOT LIKE '%유상%')
),
dart AS (
    SELECT * FROM fric
    UNION ALL SELECT * FROM pifric
    UNION ALL SELECT * FROM cr
    UNION ALL SELECT * FROM capital
),
dart_leg AS (
    -- announce 폴백: rcept_no 14자리 = 접수일 YYYYMMDD + 일련번호 6자리 → '%Y%m%d%f'(%f = 6자리)
    -- 로 한 번에 파싱해 날짜만 취한다(참조표 미스 → stage available_date NULL·basis unknown).
    SELECT l.ticker, d.corp_code, d.source, d.event_type, d.cls, d.basis_date, d.ratio,
           d.ratio_given, d.rcept_no,
           coalesce(d.available_date,
                    CAST(try_strptime(d.rcept_no, '%Y%m%d%f') AS DATE))    AS announce_date,
           CASE WHEN d.available_date IS NOT NULL THEN d.available_basis
                ELSE 'derived' END                                          AS available_basis,
           CASE WHEN d.event_type = 'bonus'
                THEN (SELECT max(c.date) FROM cal c WHERE c.date < d.basis_date)
                ELSE d.basis_date END                                       AS effective_date,
           'disclosure_body'                                                AS effective_basis
    FROM dart d
    LEFT JOIN legs l ON l.corp_code = d.corp_code AND l.cls = d.cls
),
-- ── KRX 액면가 변경 (직전 거래일 대비) ───────────────────────────────────────
listing AS (
    SELECT ticker, date, par_value_krw, list_shrs, available_basis,
           lag(par_value_krw) OVER w AS prev_par,
           lag(list_shrs)     OVER w AS prev_shrs,
           lag(date)          OVER w AS prev_date
    FROM stg_listing_daily
    WINDOW w AS (PARTITION BY ticker ORDER BY date)
),
krx AS (
    SELECT l.ticker, ct.corp_code, 'krx_listing' AS source,
           CASE WHEN l.par_value_krw < l.prev_par THEN 'split' ELSE 'reverse_split' END AS event_type,
           CASE WHEN l.prev_shrs > 0 THEN l.list_shrs / l.prev_shrs END AS ratio,
           TRUE AS ratio_given, NULL::VARCHAR AS rcept_no,
           l.date AS announce_date, l.available_basis,
           l.date AS effective_date, 'krx_shares_change' AS effective_basis
    FROM listing l
    JOIN cal c ON c.date = l.date
    LEFT JOIN corp_ticker ct ON ct.ticker = l.ticker
    WHERE l.prev_par IS NOT NULL AND l.par_value_krw IS NOT NULL
      AND l.par_value_krw <> l.prev_par
      AND l.prev_date = c.prev_td
),
cand AS (
    SELECT ticker, corp_code, source, event_type, ratio, ratio_given, rcept_no, announce_date,
           available_basis, effective_date, effective_basis
    FROM dart_leg
    UNION ALL
    SELECT ticker, corp_code, source, event_type, ratio, ratio_given, rcept_no, announce_date,
           available_basis, effective_date, effective_basis
    FROM krx
),
judged AS (
    SELECT c.*,
           CASE WHEN c.ticker IS NULL THEN 'ticker_unresolved'
                WHEN c.effective_date IS NULL OR c.announce_date IS NULL THEN 'effective_unresolved'
                WHEN c.ratio_given AND (c.ratio IS NULL OR c.ratio <= 0) THEN 'ratio_unparsed'
                WHEN date_diff('day', c.effective_date, c.announce_date)
                     > CAST(k.effective_before_announce_max_days AS INTEGER)
                     THEN 'effective_before_announce'
           END AS reject_reason
    FROM cand c CROSS JOIN _const k
),
ranked AS (
    -- 우선순위: 결정공시 > 자본변동 > KRX (FALSE < TRUE 정렬) → 가장 이른 announce → rcept_no
    SELECT *,
           row_number() OVER (PARTITION BY ticker, event_type, effective_date
                              ORDER BY (source = 'krx_listing'), (source = 'capital'),
                                       announce_date, rcept_no NULLS LAST)              AS rn,
           count(*)     OVER (PARTITION BY ticker, event_type, effective_date)          AS n_src_rows
    FROM judged
    WHERE reject_reason IS NULL
),
out AS (
    SELECT ticker, corp_code, event_type, announce_date, effective_date, effective_basis,
           ratio, rcept_no, source, n_src_rows, available_basis, reject_reason
    FROM ranked WHERE rn = 1
    UNION ALL
    SELECT ticker, corp_code, event_type, announce_date, effective_date, effective_basis,
           ratio, rcept_no, source, 1::BIGINT, available_basis, reject_reason
    FROM judged WHERE reject_reason IS NOT NULL
)
SELECT
    coalesce(ticker, '-') || ':' || event_type || ':'
        || coalesce(CAST(effective_date AS VARCHAR), '-')            AS event_id,
    ticker,
    corp_code,
    event_type,
    announce_date,
    effective_date,
    effective_basis,
    ratio,
    NULL::BIGINT                                                      AS amount_krw,   -- MVP 밖(유상감자 대가·배당)
    rcept_no,
    source,
    n_src_rows,
    announce_date                                                     AS available_date,
    available_basis,
    reject_reason
FROM out
