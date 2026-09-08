-- short_daily (S09) — 공매도(키움 ka10014 · KIS kis_short_sale) + 대차(키움 ka20068 · KIS
-- kis_loan_trans) 일별 격자.
-- grain (date, ticker) · date_axis. DESIGN v1.2 §4-3 · GATES v1.0 §3 ⑪ · §4 FX-3-001·004·009.
--
-- 격자 = `universe_daily`(status ∈ {listed, suspended} ∧ sec_type <> 'etf'). universe_daily 자체가
-- 캘린더 × security_span 격자라 캘린더를 다시 곱하지 않는다 — `trading_calendar` 는 첫 세션(하한)을
-- 주어 격리 사유를 `pre_calendar` 와 `off_grid` 로 가르는 축으로만 쓴다(DESIGN §4-3 P11).
-- 격자 밖 원장 행은 버리지 않고 `reject_reason` 을 달아 같은 스키마로 내보낸다 — 빌더가
-- `_reject/reject_reason=<r>/` 로 가른다. 사유는 둘뿐이다: 캘린더 첫 세션 이전이면 `pre_calendar`,
-- 그 밖(유니버스에 없는 티커·구간 밖 날짜·ETF·backfill_end 이후)은 전부 `off_grid`.
--
-- **원천을 합치지 않는다**(사용자 확정 09-06): 같은 사실의 두 원천(키움·KIS 공매도, 키움·KIS 대차)은
-- 접미사 `_kiwoom`·`_kis` 로 나란히 살고, 어느 쪽을 믿을지는 소비자가 고른다. src 를 PK 에 넣는 대신
-- 컬럼을 벌리는 이유는 두 원천의 커버 구간이 갈라져 있어서다(절단본: KIS 는 폐지 3종, 키움은 존속 8종,
-- 겹침 0행) — src 축을 두면 격자 행이 원천 수만큼 불어나고 EG1 좌변이 count(DISTINCT (date,ticker))
-- 로 바뀐다. 단위를 stage 가 측정하지 못한 축은 원값 그대로 `_raw` 로 싣고 `*_basis` 를 동반한다
-- (`shrts_avg_pric` — STAGE_DESIGN §5 「단위를 모르면 접미사 금지」).
--
-- **키움 대차(ka20068)는 4번째 원천이다**(S09-2, 2026-09-08). 앞의 셋과 같은 규약으로 붙는다 —
-- 원장 행이 격자 셀 · 접힘 · 격리 중 하나로만 가고(EG1_short_daily), 결측 사유는 제 몫의
-- `fill_kind_lending_kiwoom` 이 나르며, 증거 축은 `stg_shards_kiwoom` 의 `src_api='ka20068'` 샤드다.
-- `rmnd` 는 **주 단위로 확정**되어 `_shr` 접미사를 받는다 — 근거는 rules_s09 의 field 선언.
--
-- 판본 선택: KIS 두 테이블은 stage 에서 `key_unique=false`(append_only 재수집)라 같은 (ticker, date)
-- 에 여러 판본이 올 수 있다. PIT 규약(STAGE_HANDOFF §2 「PIT = 같은 키의 min(observed_date)」)대로
-- 첫 관측 판본 하나만 남기고, 동률은 값 컬럼 전순서로 깬다(EG5a 재현성). 접힌 행수는
-- EG1_short_daily 가 `n_dedup_<src>` 로 세고 원장 보존 등식에 넣는다.
--
-- `fill_kind`(DESIGN §3·§4-3) — 원천마다 하나씩. 로그 축(키움 샤드 · KIS 유닛)이 그 셀을 덮는지로
-- 판정한다. 셀에 원장 행이 있으면 `measured`, 없으면 로그가 말하는 대로:
--     샤드 status='done' ∧ 요청창 → src_omitted(shard_done)   / 'empty' → empty_response(shard_empty)
--     유닛 status='ok'   ∧ 창     → src_omitted(unit_ok)      / 'empty' → empty_response(unit_empty)
--     로그 없음                    → not_collected(none)
-- `evidence` 는 원장 행 유무와 무관하게 로그 축이 말하는 것을 그대로 싣는다 — `measured` 인데 로그가
-- empty 인 모순은 EG3_short_daily 가 기록한다. DESIGN §4-3 의 「KRX 가격 행 존재일」 조건은 격자가
-- 이미 보장한다(격자 ⊆ security_span ⊆ 상장 존재일) — 위반 건수는 EG3 기록형
-- `n_grid_without_price_axis` 로 남긴다.
--
-- available_date = date (default) — KRX 일별 스냅샷과 같은 관례. stage 세 원천 모두
-- `lag_known=false`·`available_basis='default'` 라 공표 시각은 `dataset_profile`(S19) 몫이다.
WITH cal AS (
    SELECT min(date) AS first_session FROM trading_calendar
),
grid AS (
    SELECT date, ticker FROM universe_daily
    WHERE status IN ('listed', 'suspended') AND sec_type <> 'etf'
),
kw AS (
    SELECT ticker, date, shrts_qty_shr, shrts_trde_prica_krw, trde_wght_pct, shrts_avg_pric
    FROM stg_short_daily_kiwoom
    QUALIFY row_number() OVER (
        PARTITION BY ticker, date
        ORDER BY observed_date, observed_n, shrts_qty_shr, shrts_trde_prica_krw,
                 trde_wght_pct, shrts_avg_pric) = 1
),
ks AS (
    SELECT ticker, date, ssts_cntg_qty_shr, ssts_tr_pbmn_krw, ssts_vol_rlim_pct,
           ssts_tr_pbmn_rlim_pct, avrg_prc_krw
    FROM stg_short_daily_kis
    QUALIFY row_number() OVER (
        PARTITION BY ticker, date
        ORDER BY observed_date, observed_n, ssts_cntg_qty_shr, ssts_tr_pbmn_krw,
                 ssts_vol_rlim_pct, ssts_tr_pbmn_rlim_pct, avrg_prc_krw) = 1
),
ln AS (
    SELECT ticker, date, new_stcn_shr, rdmp_stcn_shr, rmnd_stcn_shr, rmnd_amt_krw
    FROM stg_loan_daily_kis
    QUALIFY row_number() OVER (
        PARTITION BY ticker, date
        ORDER BY observed_date, observed_n, new_stcn_shr, rdmp_stcn_shr, rmnd_stcn_shr,
                 rmnd_amt_krw) = 1
),
lk AS (
    SELECT ticker, date, rmnd, remn_amt_krw
    FROM stg_lending_daily
    QUALIFY row_number() OVER (
        PARTITION BY ticker, date
        ORDER BY observed_date, observed_n, rmnd, remn_amt_krw) = 1
),
shard AS (
    -- (src_api, ticker) 당 요청 1건이 정본이다(절단본 실측: ka10014 8티커 × 1행). 페이징이 여러 행으로
    -- 쪼개져 오더라도 격자 조인이 팬아웃하지 않도록 티커 단위로 접는다 — 창은 [min(req_start),
    -- max(req_end)] 로 넓어지고 status 는 done > empty > 그 외 우선순위의 최댓값을 쓴다.
    -- 접힌 행수는 EG3_short_daily 의 `n_kiwoom_shard_rows`/`n_kiwoom_shard_tickers` 가 기록한다.
    SELECT ticker, min(req_start) AS req_start, max(req_end) AS req_end,
           max(CASE status WHEN 'done' THEN 2 WHEN 'empty' THEN 1 ELSE 0 END) AS prio
    FROM stg_shards_kiwoom
    WHERE src_api = 'ka10014'
    GROUP BY ticker
),
shard_lend AS (
    -- 같은 규약의 대차 샤드(ka20068). `shard` 와 한 CTE 로 묶어 `src_api` 로 가르지 않는 이유는
    -- 한쪽 테이블에만 걸리는 조인 술어가 해시 조인을 NL 조인으로 떨어뜨리기 때문이다
    -- (EQUITY_HANDOFF §7 ③ — credit_daily 70분 사고). 미리 걸러 티커 등호 하나로 붙인다.
    SELECT ticker, min(req_start) AS req_start, max(req_end) AS req_end,
           max(CASE status WHEN 'done' THEN 2 WHEN 'empty' THEN 1 ELSE 0 END) AS prio
    FROM stg_shards_kiwoom
    WHERE src_api = 'ka20068'
    GROUP BY ticker
),
unit_day AS (
    -- KIS 유닛은 (dataset, ticker) 당 요청 창이 수십 개다(절단본 short 45 · loan 23). 창이 겹치는
    -- 재수집이 있어도 격자가 팬아웃하지 않도록 캘린더로 펼쳐 (dataset, ticker, date) 로 접는다.
    -- 창 하나가 세션 90여 개라 펼친 크기는 유닛 행수 × 90 수준이다 — 서버 short·loan 유닛은
    -- 티커 652·287 × 창 십수 개라 펼쳐도 100만 행대이고, 격자(1,040만)보다 작다.
    SELECT u.dataset, u.ticker, c.date,
           max(CASE u.status WHEN 'ok' THEN 2 WHEN 'empty' THEN 1 ELSE 0 END) AS prio
    FROM stg_units_kis u
    JOIN trading_calendar c ON c.date BETWEEN u.window_from AND u.window_to
    WHERE u.dataset IN ('short', 'loan')
    GROUP BY u.dataset, u.ticker, c.date
),
cells AS (
    SELECT date, ticker, true AS on_grid FROM grid
    UNION ALL
    SELECT s.date, s.ticker, false AS on_grid
    FROM (SELECT ticker, date FROM kw
          UNION SELECT ticker, date FROM ks
          UNION SELECT ticker, date FROM ln
          UNION SELECT ticker, date FROM lk) s
    WHERE NOT EXISTS (SELECT 1 FROM grid g WHERE g.ticker = s.ticker AND g.date = s.date)
),
joined AS (
    SELECT c.date, c.ticker, c.on_grid,
           kw.shrts_qty_shr, kw.shrts_trde_prica_krw, kw.trde_wght_pct, kw.shrts_avg_pric,
           (kw.ticker IS NOT NULL) AS kw_row,
           ks.ssts_cntg_qty_shr, ks.ssts_tr_pbmn_krw, ks.ssts_vol_rlim_pct,
           ks.ssts_tr_pbmn_rlim_pct, ks.avrg_prc_krw,
           (ks.ticker IS NOT NULL) AS ks_row,
           ln.new_stcn_shr, ln.rdmp_stcn_shr, ln.rmnd_stcn_shr, ln.rmnd_amt_krw,
           (ln.ticker IS NOT NULL) AS ln_row,
           lk.rmnd, lk.remn_amt_krw,
           (lk.ticker IS NOT NULL) AS lk_row,
           CASE WHEN c.date BETWEEN sh.req_start AND sh.req_end THEN sh.prio END AS kw_prio,
           us.prio AS ks_prio,
           ul.prio AS ln_prio,
           CASE WHEN c.date BETWEEN sl.req_start AND sl.req_end THEN sl.prio END AS lk_prio
    FROM cells c
    LEFT JOIN kw ON kw.ticker = c.ticker AND kw.date = c.date
    LEFT JOIN ks ON ks.ticker = c.ticker AND ks.date = c.date
    LEFT JOIN ln ON ln.ticker = c.ticker AND ln.date = c.date
    LEFT JOIN lk ON lk.ticker = c.ticker AND lk.date = c.date
    LEFT JOIN shard sh ON sh.ticker = c.ticker
    LEFT JOIN shard_lend sl ON sl.ticker = c.ticker
    LEFT JOIN unit_day us ON us.dataset = 'short' AND us.ticker = c.ticker AND us.date = c.date
    LEFT JOIN unit_day ul ON ul.dataset = 'loan' AND ul.ticker = c.ticker AND ul.date = c.date
)
SELECT
    date,
    ticker,
    shrts_qty_shr                                           AS short_volume_kiwoom_shr,
    shrts_trde_prica_krw                                    AS short_value_kiwoom_krw,
    trde_wght_pct                                           AS short_weight_kiwoom_pct,
    shrts_avg_pric                                          AS short_avg_price_kiwoom_raw,
    CASE WHEN shrts_avg_pric IS NULL THEN NULL ELSE 'unknown' END
                                                            AS short_avg_price_kiwoom_basis,
    ssts_cntg_qty_shr                                       AS short_volume_kis_shr,
    ssts_tr_pbmn_krw                                        AS short_value_kis_krw,
    ssts_vol_rlim_pct                                       AS short_volume_ratio_kis_pct,
    ssts_tr_pbmn_rlim_pct                                   AS short_value_ratio_kis_pct,
    avrg_prc_krw                                            AS short_avg_price_kis_krw,
    new_stcn_shr                                            AS lending_new_kis_shr,
    rdmp_stcn_shr                                           AS lending_redeem_kis_shr,
    rmnd_stcn_shr                                           AS lending_balance_kis_shr,
    rmnd_amt_krw                                            AS lending_balance_kis_krw,
    rmnd                                                    AS lending_balance_kiwoom_shr,
    remn_amt_krw                                            AS lending_balance_kiwoom_krw,
    struct_pack(
        kind := CASE WHEN kw_row THEN 'measured'
                     WHEN kw_prio = 2 THEN 'src_omitted'
                     WHEN kw_prio = 1 THEN 'empty_response'
                     ELSE 'not_collected' END,
        evidence := CASE WHEN kw_prio = 2 THEN 'shard_done'
                         WHEN kw_prio = 1 THEN 'shard_empty'
                         ELSE 'none' END)                   AS fill_kind_short_kiwoom,
    struct_pack(
        kind := CASE WHEN ks_row THEN 'measured'
                     WHEN ks_prio = 2 THEN 'src_omitted'
                     WHEN ks_prio = 1 THEN 'empty_response'
                     ELSE 'not_collected' END,
        evidence := CASE WHEN ks_prio = 2 THEN 'unit_ok'
                         WHEN ks_prio = 1 THEN 'unit_empty'
                         ELSE 'none' END)                   AS fill_kind_short_kis,
    struct_pack(
        kind := CASE WHEN ln_row THEN 'measured'
                     WHEN ln_prio = 2 THEN 'src_omitted'
                     WHEN ln_prio = 1 THEN 'empty_response'
                     ELSE 'not_collected' END,
        evidence := CASE WHEN ln_prio = 2 THEN 'unit_ok'
                         WHEN ln_prio = 1 THEN 'unit_empty'
                         ELSE 'none' END)                   AS fill_kind_loan_kis,
    struct_pack(
        kind := CASE WHEN lk_row THEN 'measured'
                     WHEN lk_prio = 2 THEN 'src_omitted'
                     WHEN lk_prio = 1 THEN 'empty_response'
                     ELSE 'not_collected' END,
        evidence := CASE WHEN lk_prio = 2 THEN 'shard_done'
                         WHEN lk_prio = 1 THEN 'shard_empty'
                         ELSE 'none' END)                   AS fill_kind_lending_kiwoom,
    date                                                    AS available_date,
    'default'                                               AS available_basis,
    CASE WHEN on_grid THEN NULL
         WHEN date < (SELECT first_session FROM cal) THEN 'pre_calendar'
         ELSE 'off_grid' END                                AS reject_reason
FROM joined
