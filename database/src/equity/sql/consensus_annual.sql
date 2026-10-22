-- consensus_annual (S26) — WISE 연간 실적·추정 표(c1050001 T2Y) 수집일별 판. 플랜 `2026-10-22-equity-wise-fields` T1.
-- grain (ticker, fetched_date, period_label) · date_axis year(fetched_date). 입력은 stage `stg_consensus_annual` 하나.
--
-- 값은 stage 그대로다(매출·영업이익·순이익 억원, EPS·BPS 원, PER·PBR 배, ROE %). period_kind 는 A(실적)·E(추정).
-- 그날 수집분에 당해 12월기 추정(E)이 있는지가 곧 "추정치를 지금 갖고 있나" 라서 수집일 판을 접지 않는다 —
-- 추정은 발표 뒤 약 3개월이면 빠진다(DECISIONS U1 원인).
--
-- PIT: stage 가 낸 available_date(= fetched_date)·basis(measured)를 그대로 나른다.
SELECT
    ticker,
    fetched_date,
    period_label,
    period,
    period_kind,
    fs_basis,
    revenue,
    yoy_pct,
    op,
    ni,
    eps,
    bps,
    per,
    pbr,
    roe_pct,
    ev_ebitda,
    available_date,
    available_basis,
    CAST(NULL AS VARCHAR)       AS reject_reason      -- 격리 사유 없음(REJECT_REASONS=())
FROM stg_consensus_annual
