-- credit_daily (S10) — 신용 일별 격자. DESIGN v1.2 §4-3 · GATES v1.0 §3 ⑪ · EG7-P06 · FX-3-008.
-- grain (date, ticker) · date_axis. 원천은 `stg_credit_daily` 단독(KIS 신용잔고), 격자는 equity
-- `universe_daily` × `trading_calendar`, 결측 이유는 `stg_units_kis`(dataset='credit') 수집 로그.
--
-- 격자 = universe_daily 의 `status ∈ {listed, suspended}` ∧ `sec_type ∉ {etf}` 행(DESIGN §4-3).
--   `sec_type` NULL 은 격자에 **남긴다**(`IS DISTINCT FROM 'etf'`) — `<> 'etf'` 로 쓰면 종류 미상
--   종목이 조용히 사라져 생존편향을 게이트가 만든다(GATES §5-C6 과 같은 부류). 절단본·서버 모두
--   universe_daily 의 sec_type NULL 은 0 이라 값은 같고, 규칙만 안전한 쪽으로 닫는다.
--   status 어휘에 'delisted' 는 없다(폐지일엔 구간이 없다, S03) — 술어는 그대로 둔다.
--
-- 격리(EG7-P06 격리형, 행을 버리지 않고 `_reject/<reason>/` 으로). 격자 밖 stage 행이 대상이다:
--   pre_calendar = `date < min(trading_calendar.date)` (캘린더 하한 이전. 절단본 85행 = 2009년)
--   off_grid     = 캘린더 안이지만 그 (ticker, date) 격자 셀이 없다 — 상장 전·재상장 공백 기간에
--                  KIS 가 돌려준 행(절단본 9행: 000030 2014-11-17·18 등, 전부 `_src_flag='partial'`)
--   두 사유는 배타적이고 순서대로 먼저 맞는 것 하나를 쓴다.
--
-- fill_kind STRUCT(kind, evidence) — DESIGN §3 어휘 × §4-3 판정. 원천이 KIS 하나뿐이라 키움 샤드
-- 가지는 없고 유닛 축만 쓴다:
--   stage 행 있음                      → measured      (evidence: 덮는 ok 유닛 있으면 unit_ok, 없으면 none)
--   없음 ∧ 'ok' 유닛 창이 덮음         → src_omitted    (unit_ok)   — 원천이 0 잔고 행을 생략한 것
--   없음 ∧ 'empty' 유닛 창만 덮음      → empty_response (unit_empty)
--   없음 ∧ 어떤 유닛도 안 덮음         → not_collected  (none)
-- 유닛 창은 (ticker, status) 별로 **구간 병합**한 뒤(gaps-and-islands) 등호 조인한다 — 셀 × 유닛
-- 범위 조인은 서버 격자(10.9M) × 유닛에서 터진다. 병합 뒤 같은 status 의 구간은 겹치지 않으므로
-- LEFT JOIN 이 팬아웃하지 않는다.
--
-- 값 채우기 — **measured 셀만 값을 갖고 나머지 셀은 전 축 NULL 이다. 0 을 굽지 않는다**(원칙 ④).
--   `src_omitted` 를 0 으로 채우는 규약(FX-3-001 은 short_daily 키움 샤드 축에 그렇게 적혀 있다)을
--   신용 격자에 그대로 옮기면 **원장 일괄 결측일에 시장 전체가 잔고 0 으로 굳는다** — 절단본
--   2018-03-28 은 전 종목 credit 행이 없고(그 전후 거래일은 전부 있다) 2026-08-19·20 도 마찬가지다
--   (P16: credit 백필 08-18 종료, 캘린더는 08-20). 유닛 창은 '요청 구간' 이라 그 날짜가 응답에
--   들었는지까지는 말해 주지 않으므로, `src_omitted` 는 값을 단정할 근거가 못 된다.
--   0 의 의미는 `fill_kind.kind='src_omitted'` 가 나르고, 엔진 `CellKind.SOURCE_OMITTED_ZERO`
--   대응(FIELD_MAP §1)이 소비 시점에 0 으로 읽는다 — 값 축과 지식 축을 섞지 않는다.
--   원장 일괄 결측 의심일(격자에 종목이 있는데 measured 가 0 인 날)은 EG3_credit_daily 기록형.
--
-- 나르지 않는 것: `stg_credit_daily` 의 가격 에코(close/open/high/low·prdy_*·acml_vol_shr).
--   `price_basis_close = 'adjusted_asof_collect'` — 수집 시점 기준 수정종가라 PIT 축이 아니고,
--   원주가 정본은 `price_daily`(원칙 ②)다. 두 축의 어긋남은 EG3_credit_daily 가 기록형으로 센다.
--   `*_amt` 6컬럼은 단위 미상(STAGE_HANDOFF §1·§4)이라 원값 그대로 나르고 `amt_basis='unknown'`
--   으로 표시한다 — 단위 접미사(`_krw`)를 붙이지 않는다(FX-3-009 규약).
--
-- PIT: `available_date = date`, basis 'default'. 신용잔고는 KRX 일별 스냅샷 관례를 따르고(stage
-- 가 이미 date·default 로 확정) 공표 랙(T+1)은 팩트 행에 굽지 않는다 — 랙은 `dataset_profile`
-- (S19)의 `recommended_lag_sessions` 와 뷰가 건다(FIELD_MAP §1 '랙 단위', price_daily 시총과 같은
-- 규약). `stlm_date`(결제일, date + 2~12일)는 미래 날짜지만 available 축이 아니라 **보존 컬럼**이다.
WITH cal AS (
    SELECT min(date) AS first_date FROM trading_calendar
),
grid AS (
    SELECT u.date, u.ticker
    FROM universe_daily u
    WHERE u.status IN ('listed', 'suspended') AND u.sec_type IS DISTINCT FROM 'etf'
),
unit AS (
    SELECT ticker, status, window_from AS wf, window_to AS wt
    FROM stg_units_kis
    WHERE dataset = 'credit'
),
unit_mark AS (
    -- 구간 병합 1단계: 정렬된 앞선 창들의 최대 끝보다 늦게 시작하면 새 섬이다.
    SELECT ticker, status, wf, wt,
           CASE WHEN wf <= max(wt) OVER w THEN 0 ELSE 1 END AS is_new
    FROM unit
    WINDOW w AS (PARTITION BY ticker, status ORDER BY wf, wt
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
unit_grp AS (
    SELECT ticker, status, wf, wt,
           sum(is_new) OVER (PARTITION BY ticker, status ORDER BY wf, wt
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS island_seq
    FROM unit_mark
),
island AS (
    SELECT ticker, status, min(wf) AS wf, max(wt) AS wt
    FROM unit_grp
    GROUP BY ticker, status, island_seq
),
base AS (
    -- 격자 셀(채택) + 격자 밖 stage 행(격리). 두 집합은 배타적이라 키가 겹치지 않는다.
    SELECT g.date, g.ticker, NULL::VARCHAR AS reject_reason
    FROM grid g
    UNION ALL
    SELECT c.date, c.ticker,
           CASE WHEN c.date < (SELECT first_date FROM cal) THEN 'pre_calendar'
                ELSE 'off_grid' END
    FROM stg_credit_daily c
    WHERE NOT EXISTS (SELECT 1 FROM grid g WHERE g.ticker = c.ticker AND g.date = c.date)
),
cell AS (
    SELECT b.date, b.ticker, b.reject_reason,
           (s.ticker IS NOT NULL)   AS has_row,
           (iok.ticker IS NOT NULL) AS unit_ok,
           (iem.ticker IS NOT NULL) AS unit_empty,
           s.stlm_date,
           s.whol_loan_new_stcn_shr, s.whol_loan_rdmp_stcn_shr, s.whol_loan_rmnd_stcn_shr,
           s.whol_loan_new_amt, s.whol_loan_rdmp_amt, s.whol_loan_rmnd_amt,
           s.whol_loan_rmnd_rate_pct, s.whol_loan_gvrt_pct,
           s.whol_stln_new_stcn_shr, s.whol_stln_rdmp_stcn_shr, s.whol_stln_rmnd_stcn_shr,
           s.whol_stln_new_amt, s.whol_stln_rdmp_amt, s.whol_stln_rmnd_amt,
           s.whol_stln_rmnd_rate_pct, s.whol_stln_gvrt_pct
    FROM base b
    LEFT JOIN stg_credit_daily s ON s.ticker = b.ticker AND s.date = b.date
    LEFT JOIN island iok ON iok.ticker = b.ticker AND iok.status = 'ok'
                        AND b.date BETWEEN iok.wf AND iok.wt
    LEFT JOIN island iem ON iem.ticker = b.ticker AND iem.status = 'empty'
                        AND b.date BETWEEN iem.wf AND iem.wt
),
kinded AS (
    SELECT c.*,
           CASE WHEN c.has_row    THEN 'measured'
                WHEN c.unit_ok    THEN 'src_omitted'
                WHEN c.unit_empty THEN 'empty_response'
                ELSE 'not_collected' END AS kind
    FROM cell c
)
-- 측정 축 16 + `stlm_date` 는 LEFT JOIN 이 이미 measured 셀에만 값을 남긴다 — 채우지도, 지우지도
-- 않는다(원천 행이 있으면 그 값, 없으면 NULL).
SELECT
    date,
    ticker,
    stlm_date,
    whol_loan_new_stcn_shr,
    whol_loan_rdmp_stcn_shr,
    whol_loan_rmnd_stcn_shr,
    whol_loan_new_amt,
    whol_loan_rdmp_amt,
    whol_loan_rmnd_amt,
    whol_loan_rmnd_rate_pct,
    whol_loan_gvrt_pct,
    whol_stln_new_stcn_shr,
    whol_stln_rdmp_stcn_shr,
    whol_stln_rmnd_stcn_shr,
    whol_stln_new_amt,
    whol_stln_rdmp_amt,
    whol_stln_rmnd_amt,
    whol_stln_rmnd_rate_pct,
    whol_stln_gvrt_pct,
    'unknown'                                              AS amt_basis,
    {'kind': kind,
     'evidence': CASE WHEN kind = 'measured' AND unit_ok THEN 'unit_ok'
                      WHEN kind = 'src_omitted'          THEN 'unit_ok'
                      WHEN kind = 'empty_response'       THEN 'unit_empty'
                      ELSE 'none' END}                     AS fill_kind,
    date                                                   AS available_date,
    'default'                                              AS available_basis,
    reject_reason
FROM kinded
