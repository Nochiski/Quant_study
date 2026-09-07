-- flow_daily (S08) — 수급 격자. DESIGN v1.2 §4-3 · GATES v1.0 §3 ⑪ · EG3-P06 · EG7-P06.
-- grain (date, ticker, src). 격자 = trading_calendar × universe_daily(status ∈ {listed,
-- suspended} ∧ sec_type <> 'etf'). 격자 축은 universe_daily 가 이미 캘린더 × security_span 이라
-- 여기서 캘린더를 다시 곱하지 않는다 — trading_calendar 는 pre_calendar 하한에만 쓴다.
--
-- 원천 2개(상보 결합, 겹침 0 = GATES EG8-P05):
--   kiwoom = stg_flow_daily_kiwoom(ka10060) 13주체 `_krw` 전부. stage 가 백만원 ×1e6 을 이미
--            했다(rules_kiwoom._flow_krw unit_scale) — 여기서 다시 곱하지 않는다.
--   kis    = stg_flow_split_daily(KIS FHPTJ04160001) `*_ntby_tr_pbmn_krw`. 이것도 stage 가
--            ×1e6 을 마쳤다(STAGE_HANDOFF §2 '×1e6(픽스처 13)') — 재환산 금지.
--            명세 기반 대응(검증축 없음, DESIGN §4-3): prsn→ind_invsr · frgn→frgnr_invsr ·
--            orgn→orgn · scrt→fnnc_invt · insu→insrnc · ivtr→invtrt · bank→bank ·
--            fund→penfnd_etc · pe_fund→samo_fund · etc_corp→etc_corp.
--            KIS 에 대응 주체가 없는 etc_fnnc·natn·natfor 는 NULL(0 채움 금지, 원칙 ④),
--            equity 에 대응 컬럼이 없는 mrbn·etc_orgt·etc 는 버린다.
--
-- 행 규칙:
--   ① 측정 행 = 격자 안 원장 행. 원천마다 1행이라 같은 셀에 두 원천이 다 있으면 2행이 되고
--      EG1 좌변(count(DISTINCT (date,ticker)))은 그대로다(GATES §5-B4). 겹침은 EG3_flow_daily
--      의 n_src_overlap 이 지목한다 — 조용히 하나를 고르지 않는다.
--   ② 미측정 셀 = 두 원장 어디에도 없는 격자 셀 1행. 값은 전부 NULL(결측은 결측, 원칙 ④ —
--      '미수집을 0 으로' 는 GATES EG9-P04 가 금지한다) + fill_kind 가 이유를 든다.
--   ③ 격리 = 격자 밖 원장 행. date < 캘린더 하한이면 pre_calendar, 아니면 off_grid
--      (폐지 구간·재상장 전 티커 재사용 행이 여기로 온다 — 격자로 새면 생존편향이다).
--
-- fill_kind(DESIGN §3·§4-3 어휘, 판정 순서):
--   measured                    원장 행이 있다. evidence 는 'none' — 로그 축 판정이 아니라
--                               원장 자신이 근거다.
--   src_omitted / shard_done    ka10060 샤드 status='done' ∧ 요청창 안. 엔진 CellKind =
--                               SOURCE_OMITTED_ZERO(FIELD_MAP §1) — 값은 여전히 NULL 이고
--                               '0 으로 읽어도 되는 결측' 이라는 라벨만 준다.
--   empty_response / shard_empty  샤드 status='empty' ∧ 요청창 안
--   src_omitted / unit_ok       KIS flow 유닛 status='ok' ∧ [window_from, window_to]
--   empty_response / unit_empty KIS flow 유닛 status='empty' ∧ 창
--   not_collected / none        어느 로그도 그 셀을 안 덮는다 (P3 샤드 미커버 1,070 티커)
--   키움 로그가 KIS 로그보다 앞선다 — 겹치는 셀 수는 EG3_flow_daily.n_cells_both_logs 기록형.
--   DESIGN §4-3 의 'KRX 가격 행 존재일' 조건은 술어에 넣지 않는다: 격자가 universe_daily 이고
--   그 EG1 우변이 Σ security_span.n_days = price_daily 행수라 격자 ⊆ 가격 행이 항등이다
--   (절단본 실측 36,972 셀 전부 가격 행 있음 — test_equity_s08_flow 가 독립 확인).
--
-- PIT: available_date = date, basis 'default' (stage 두 원천 모두 lag_known=false, 공표 시각
--      미제공 — 세션 랙은 뷰·dataset_profile 이 건다. DESIGN §2).
WITH cal AS (
    SELECT min(date) AS first_date FROM trading_calendar
),
grid AS (
    SELECT u.date, u.ticker
    FROM universe_daily u
    WHERE u.status IN ('listed', 'suspended') AND u.sec_type <> 'etf'
),
kiwoom AS (
    SELECT k.date, k.ticker, 'kiwoom' AS src,
           k.ind_invsr_krw, k.frgnr_invsr_krw, k.orgn_krw, k.fnnc_invt_krw, k.insrnc_krw,
           k.invtrt_krw, k.etc_fnnc_krw, k.bank_krw, k.penfnd_etc_krw, k.samo_fund_krw,
           k.natn_krw, k.etc_corp_krw, k.natfor_krw
    FROM stg_flow_daily_kiwoom k
),
kis AS (
    SELECT f.date, f.ticker, 'kis' AS src,
           f.prsn_ntby_tr_pbmn_krw     AS ind_invsr_krw,
           f.frgn_ntby_tr_pbmn_krw     AS frgnr_invsr_krw,
           f.orgn_ntby_tr_pbmn_krw     AS orgn_krw,
           f.scrt_ntby_tr_pbmn_krw     AS fnnc_invt_krw,
           f.insu_ntby_tr_pbmn_krw     AS insrnc_krw,
           f.ivtr_ntby_tr_pbmn_krw     AS invtrt_krw,
           NULL                        AS etc_fnnc_krw,     -- KIS 무대응 (기타금융)
           f.bank_ntby_tr_pbmn_krw     AS bank_krw,
           f.fund_ntby_tr_pbmn_krw     AS penfnd_etc_krw,
           f.pe_fund_ntby_tr_pbmn_krw  AS samo_fund_krw,
           NULL                        AS natn_krw,         -- KIS 무대응 (국가)
           f.etc_corp_ntby_tr_pbmn_krw AS etc_corp_krw,
           NULL                        AS natfor_krw        -- KIS 무대응 (내외국인)
    FROM stg_flow_split_daily f
),
ledger AS (
    SELECT * FROM kiwoom
    UNION ALL
    SELECT * FROM kis
),
shard_cover AS (
    SELECT g.date, g.ticker,
           bool_or(s.status = 'done')  AS shard_done,
           bool_or(s.status = 'empty') AS shard_empty
    FROM grid g
    JOIN stg_shards_kiwoom s
      ON s.ticker = g.ticker AND s.src_api = 'ka10060'
     AND g.date BETWEEN s.req_start AND s.req_end
    GROUP BY g.date, g.ticker
),
unit_cover AS (
    SELECT g.date, g.ticker,
           bool_or(u.status = 'ok')    AS unit_ok,
           bool_or(u.status = 'empty') AS unit_empty
    FROM grid g
    JOIN stg_units_kis u
      ON u.ticker = g.ticker AND u.dataset = 'flow'
     AND g.date BETWEEN u.window_from AND u.window_to
    GROUP BY g.date, g.ticker
),
evidence AS (
    SELECT g.date, g.ticker,
           CASE WHEN sc.shard_done OR sc.shard_empty THEN 'kiwoom'
                WHEN uc.unit_ok OR uc.unit_empty     THEN 'kis'
           END AS src,
           CASE WHEN sc.shard_done  THEN 'src_omitted'
                WHEN sc.shard_empty THEN 'empty_response'
                WHEN uc.unit_ok     THEN 'src_omitted'
                WHEN uc.unit_empty  THEN 'empty_response'
                ELSE 'not_collected'
           END AS kind,
           CASE WHEN sc.shard_done  THEN 'shard_done'
                WHEN sc.shard_empty THEN 'shard_empty'
                WHEN uc.unit_ok     THEN 'unit_ok'
                WHEN uc.unit_empty  THEN 'unit_empty'
                ELSE 'none'
           END AS evidence
    FROM grid g
    LEFT JOIN shard_cover sc ON sc.date = g.date AND sc.ticker = g.ticker
    LEFT JOIN unit_cover  uc ON uc.date = g.date AND uc.ticker = g.ticker
),
-- ── 외국인 보유 (S08-2, F02·F08) ─────────────────────────────────────────────
-- 원천은 `stg_foreign_daily`(키움 ka10008) 하나뿐이다 — 순매수 격자의 상보 결합과 달리 **원천
-- 축이 없다**. 그래서 `src='kiwoom'` 행에만 붙고 KIS 보완 행은 NULL 로 둔다(0 으로 채우지
-- 않는다). 격자 등식(EG1)은 (date, ticker) 축이라 행 수가 변하지 않는다.
foreign_own AS (
    SELECT ticker, date, wght_pct, limit_exh_rt_pct, poss_stkcnt_shr
    FROM stg_foreign_daily
)
-- ① 측정 행
SELECT l.date, l.ticker, l.src,
       l.ind_invsr_krw, l.frgnr_invsr_krw, l.orgn_krw, l.fnnc_invt_krw, l.insrnc_krw,
       l.invtrt_krw, l.etc_fnnc_krw, l.bank_krw, l.penfnd_etc_krw, l.samo_fund_krw,
       l.natn_krw, l.etc_corp_krw, l.natfor_krw,
       CASE WHEN l.src = 'kiwoom' THEN fo.wght_pct END          AS foreign_wght_pct,
       CASE WHEN l.src = 'kiwoom' THEN fo.limit_exh_rt_pct END  AS foreign_limit_exh_pct,
       CASE WHEN l.src = 'kiwoom' THEN fo.poss_stkcnt_shr END   AS foreign_poss_shr,
       {'kind': 'measured', 'evidence': 'none'} AS fill_kind,
       l.date     AS available_date,
       'default'  AS available_basis,
       NULL       AS reject_reason
FROM ledger l
LEFT JOIN foreign_own fo ON fo.ticker = l.ticker AND fo.date = l.date
WHERE EXISTS (SELECT 1 FROM grid g WHERE g.date = l.date AND g.ticker = l.ticker)

UNION ALL

-- ② 미측정 셀
SELECT e.date, e.ticker, e.src,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, NULL, NULL,
       {'kind': e.kind, 'evidence': e.evidence} AS fill_kind,
       e.date     AS available_date,
       'default'  AS available_basis,
       NULL       AS reject_reason
FROM evidence e
WHERE NOT EXISTS (SELECT 1 FROM ledger l WHERE l.date = e.date AND l.ticker = e.ticker)

UNION ALL

-- ③ 격리 — 격자 밖 원장 행
SELECT l.date, l.ticker, l.src,
       l.ind_invsr_krw, l.frgnr_invsr_krw, l.orgn_krw, l.fnnc_invt_krw, l.insrnc_krw,
       l.invtrt_krw, l.etc_fnnc_krw, l.bank_krw, l.penfnd_etc_krw, l.samo_fund_krw,
       l.natn_krw, l.etc_corp_krw, l.natfor_krw,
       NULL, NULL, NULL,
       {'kind': 'measured', 'evidence': 'none'} AS fill_kind,
       l.date     AS available_date,
       'default'  AS available_basis,
       CASE WHEN l.date < (SELECT first_date FROM cal) THEN 'pre_calendar'
            ELSE 'off_grid'
       END        AS reject_reason
FROM ledger l
WHERE NOT EXISTS (SELECT 1 FROM grid g WHERE g.date = l.date AND g.ticker = l.ticker)
