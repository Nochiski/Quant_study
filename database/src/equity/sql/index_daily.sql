-- index_daily (S02) — stg_index_daily 사본. DESIGN v1.2 §4-1.
-- grain (index_class, index_name, date). index_name 은 조인 키라 정규화·번역 금지
-- (rules_krx STG_INDEX_DAILY: IDX_NM 실명). 파생 없음 — 컬럼 실명을 그대로 나른다.
--
-- PIT: 지수는 가격류라 stage 가 lag_known=true 로 싣는다(rules_krx). 원장이 공표 시각을 주지
-- 않으므로 available_date = date, basis 는 규약값 'default'(stage 가 이미 쓰는 값과 같다).
SELECT
    index_class,
    index_name,
    date,
    close_idx,
    change_idx,
    fluc_pct,
    open_idx,
    high_idx,
    low_idx,
    volume_shr,
    value_krw,
    mktcap_krw,
    date          AS available_date,
    'default'     AS available_basis,
    NULL::VARCHAR AS reject_reason
FROM stg_index_daily
