-- sector_snapshot (S25) — WICS 섹터 구성 주간 스냅샷. 플랜 `2026-09-20-wics-weekly` T3.
-- grain (ticker, snapshot_date) · date_axis year(snapshot_date). 입력은 stage `stg_wics_components` 하나.
--
-- L2 코드(5자리)로 부른 행(`req_sec_cd <> sec_cd`)만 격자다 — 그 행에 L1(`sec_cd`·`sec_nm`)과 L2(`req_sec_cd`·`idx_nm`)가
-- 함께 실려 있다(WICS_PROBE §6: L2 라벨은 IDX_NM_KOR). L1 코드(3자리)로 부른 행은 검산축이라
-- EG3_sector_snapshot 이 "L1 에는 있고 L2 에는 없는 종목 = 0" 으로 대조한다.
-- 유동시총·유동주식수는 wiseindex 값 그대로(원·주). 스냅샷 사이 날짜는 여기서 채우지 않는다 —
-- 매크로 `v_sector(as_of)` 가 스냅샷일 ≤ as_of 최신 행과 `days_since_snapshot` 을 준다.
-- PIT: available_date = snapshot_date, basis 'convention'(WICS 일별 산출 관행, 공표 시각 미실측 —
-- 토요일 03:00 수집이므로 실사용 랙은 1일 이상이다).
SELECT
    ticker,
    date               AS snapshot_date,
    sec_cd             AS wics_l1_cd,
    sec_nm             AS wics_l1_nm,
    req_sec_cd         AS wics_l2_cd,
    idx_nm             AS wics_l2_nm,
    float_shares_shr,
    float_mktcap_krw,
    wgt_pct            AS wgt_in_l2_pct,
    date               AS available_date,
    'convention'       AS available_basis,
    CAST(NULL AS VARCHAR) AS reject_reason      -- 격리 사유 없음(REJECT_REASONS=()) — 빌드 규약상 컬럼은 있어야 한다
FROM stg_wics_components
WHERE req_sec_cd <> sec_cd          -- L2 행: 요청 코드(5자리)가 행의 L1 코드와 다르다. L1 행은 req_sec_cd = sec_cd
