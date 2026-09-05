-- corp_event (S05, MVP-B) — grain event_id. 기업행위 4종: split · reverse_split · bonus · capred.
-- DESIGN v1.2 §4-2 · GATES v1.0 §3-⑨ · EG7-P08.
--
-- 원천 5 (선언 등록표 = rules_s05.SOURCES, EG1 우변은 아래 pool 의 범위 안 행수):
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
-- 자본변동의 종류 어휘(isu_dcrs_stock_knd)는 rules_s05 의 COMMON_KINDS·PREFERRED_KINDS·
-- UNLISTED_KINDS 선언과 같은 리터럴이어야 한다(tests 가 대조).
--
-- **범위 밖(scope_out)은 격리가 아니라 모집단 밖이다** — 조정할 가격이 없는 사건(서버 1차 빌드
-- 실측: 사업보고서가 창립 이래 자본 변동을 회고 기재해 1963년 사건까지 실린다). 행을 내지 않고
-- EG3_corp_event 가 건수만 기록한다:
--   out_of_calendar  효력일이 trading_calendar [min, max] 밖(DART 결정공시·자본변동 공통)
--   unlisted_class   상장 티커가 없는 주식 종류(RCPS·전환우선주 등 UNLISTED_KINDS, 또는 우선주
--                    계열인데 법인에 상장 우선주가 없음, 결정공시의 기타주식도 같은 규칙)
--   class_unknown    종류 어휘 밖('-' 포함) — 어느 티커의 사건인지 정할 수 없다
--   pre_listing      티커는 있으나 효력일이 그 티커의 security_span 첫 존재일 이전
-- 범위 안 후보만 격리 판정을 받는다: ticker_unresolved(보통주 계열인데 상장 보통주 없음) ·
-- effective_unresolved(기준일 없음) · ratio_unparsed · effective_before_announce(사건 단위 —
-- 가장 이른 announce 기준, 아래 judged CTE).
--
-- effective_date = **가격 축 효력일** (계수는 date >= effective_date 인 가격·거래량에 곱한다):
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
--   reject 후보는 접지 않고 각각 격리한다 → 범위 안 후보 = Σ_out n_src_rows + n_reject (EG1).
-- 숫자 리터럴은 0·1·2 만 쓴다(test_sql파일에_상수_하드코딩_없음) — 임계는 _const 로만 들어온다.
-- 캘린더 경계는 _const 가 아니라 trading_calendar 뷰의 min/max 서브쿼리(S03 과 같은 방식).
WITH cal AS (
    SELECT date, prev_td FROM trading_calendar
),
cal_bounds AS (
    SELECT min(date) AS cal_min, max(date) AS cal_max FROM trading_calendar
),
first_listed AS (
    SELECT ticker, min(first_date) AS first_date FROM security_span GROUP BY ticker
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
           CASE WHEN isu_dcrs_stock_knd IN ('보통주', '보통주식', '기명식보통주') THEN 'common'
                WHEN isu_dcrs_stock_knd IN ('우선주', '우선주식') THEN 'preferred'
                WHEN isu_dcrs_stock_knd IN ('상환전환우선주', '전환상환우선주', '전환우선주', 'RCPS')
                     THEN 'unlisted'
                ELSE 'unknown' END AS cls,
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
    -- unlisted·unknown class-row 는 legs 에 없으므로 ticker NULL 1행으로 남아 scope_out 으로 간다.
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
           'common' AS cls, l.date AS basis_date,
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
-- ── 모집단(pool) + 범위 판정 ─────────────────────────────────────────────────
pool AS (
    SELECT p.*,
           CASE WHEN p.source <> 'krx_listing' AND p.basis_date IS NOT NULL
                     AND (p.effective_date IS NULL
                          OR p.effective_date < b.cal_min OR p.effective_date > b.cal_max)
                     THEN 'out_of_calendar'
                WHEN p.cls = 'unlisted' OR (p.cls = 'preferred' AND p.ticker IS NULL)
                     THEN 'unlisted_class'
                WHEN p.cls = 'unknown' THEN 'class_unknown'
                WHEN p.ticker IS NOT NULL AND p.effective_date < fl.first_date THEN 'pre_listing'
           END AS scope_out
    FROM (
        SELECT ticker, corp_code, source, event_type, cls, basis_date, ratio, ratio_given,
               rcept_no, announce_date, available_basis, effective_date, effective_basis
        FROM dart_leg
        UNION ALL
        SELECT ticker, corp_code, source, event_type, cls, basis_date, ratio, ratio_given,
               rcept_no, announce_date, available_basis, effective_date, effective_basis
        FROM krx
    ) p
    CROSS JOIN cal_bounds b
    LEFT JOIN first_listed fl ON fl.ticker = p.ticker
)
-- ==== eg1: 여기까지가 모집단(pool). rules_s05 가 이 앞부분을 EG1 우변·범위 metric 에 재사용한다 ====
, cand AS (
    SELECT * FROM pool WHERE scope_out IS NULL
),
judged0 AS (
    SELECT c.*,
           CASE WHEN c.ticker IS NULL THEN 'ticker_unresolved'
                WHEN c.effective_date IS NULL OR c.announce_date IS NULL THEN 'effective_unresolved'
                WHEN c.ratio_given AND (c.ratio IS NULL OR c.ratio <= 0) THEN 'ratio_unparsed'
           END AS reject_reason0
    FROM cand c
),
judged AS (
    -- EG7-P08 은 **사건 단위**: 같은 dedup 축의 후보 중 가장 이른 announce_date 가 효력일보다 임계
    -- 이상 늦을 때 그 사건의 후보 전부를 격리한다. 자본변동은 같은 사건을 매년 사업보고서마다 다시
    -- 기재하므로(서버 실측 공시−효력 최대 14,294일) 사본마다 판정하면 결정공시·앞선 보고서에
    -- 접힐 행이 격리로 새고, 격리 비율이 사건 수가 아니라 보고서 수에 비례해 부푼다.
    SELECT j.*,
           coalesce(j.reject_reason0,
                    CASE WHEN date_diff('day', j.effective_date,
                                        min(CASE WHEN j.reject_reason0 IS NULL
                                                 THEN j.announce_date END)
                                            OVER (PARTITION BY j.ticker, j.event_type,
                                                               j.effective_date))
                              > CAST(k.effective_before_announce_max_days AS INTEGER)
                         THEN 'effective_before_announce' END)                 AS reject_reason
    FROM judged0 j CROSS JOIN _const k
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
