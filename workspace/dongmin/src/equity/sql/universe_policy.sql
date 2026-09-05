-- universe_policy (S03·S03B·S03B-2) — 정책 선언표. grain (policy, rule_seq) · whole. DESIGN v1.2 §4-1 · GATES §3 ㉒.
--
-- equity 는 표를 보관만 하고 적용은 팩터층(v_universe(:d, :policy) 는 편의 뷰). 한 정책의 행은 AND 로
-- 묶인다(rule_seq 는 술어 리스트 안 순서 — SQL 본문 숫자 리터럴 금지 규약 때문에 번호를 손으로 적지
-- 않고 generate_subscripts 로 만든다). 플래그형 술어는 threshold_kind='flag', threshold_value NULL.
--   all          : TRUE (정책 미적용)
--   common-stock : sec_type='common' ∧ status='listed' — 소비자 계약 UNIVERSE='krx.common-stock'
--                  (FIELD_MAP §1, backend/tests/contract) 이 정책표로 풀리도록 S03B 에서 추가.
--   investable   : common-stock + NOT admin_state + NOT liquidation_window.
--                  admin_state NULL(basis unknown) 행은 NOT NULL = NULL 이라 빠진다 — 서버 S03 실측
--                  (P19′) 에서 unknown 0 이라 실효 없음. 결측 정책이 필요해지면 행을 바꾼다.
--   liquid       : (S03B-2, 09-05 사용자 결정 "날짜별 상위 비율") investable 4행 + 임계 1행
--                  `adv20_rank_pct >= 1 − liquid_top_pct` (threshold_kind='quantile',
--                  threshold_value = liquid_top_pct). 임계는 절대 금액이 아니라 같은 날 보통주 모집단 안
--                  adv20 백분위(universe_daily.adv20_rank_pct, cume_dist) 라 시장 규모 변화·인플레이션에
--                  불변이고, 서버 P22′ 분위수(p50 8.85억)는 근거일 뿐 표에 박히지 않는다. 비율은 baseline
--                  universe_policy.liquid_top_pct(_const, 기본 0.5 = 상위 50%) — 사람이 고른 값이라
--                  basis='convention', measured_at NULL(잰 값이 아니다; 순위 자체는 날마다 재계산).
--                  술어 문자열에 1 − liquid_top_pct 를 박아 소비자(워크벤치 어댑터)가 표만 읽고 적용한다.
-- universe_id = 'krx.' || policy (FIELD_MAP §1 어휘). predicate 는 universe_daily 컬럼 위 SQL 불리언 식.
-- threshold_value 는 predicate 안 임계의 출처 기록, measured_at 은 그 임계를 잰 날(flag·convention 은 NULL).
-- version 은 정책 집합 판본(_const, baseline universe_policy.version) — 행이 늘거나 임계가 바뀌면 올린다.
-- 입력 universe_daily 는 EG3_policy 가 predicate 를 그 스키마 위에서 평가해 보기 위한 것이다.
WITH flag_rules AS (
    SELECT policy, predicates
    FROM (VALUES
        ('all',          ['TRUE']),
        ('common-stock', ['sec_type = ''common''', 'status = ''listed''']),
        ('investable',   ['sec_type = ''common''', 'status = ''listed''',
                          'NOT admin_state', 'NOT liquidation_window']),
        ('liquid',       ['sec_type = ''common''', 'status = ''listed''',
                          'NOT admin_state', 'NOT liquidation_window'])
    ) AS p(policy, predicates)
),
flag_rows AS (
    SELECT policy,
           CAST(generate_subscripts(predicates, 1) AS BIGINT)  AS rule_seq,
           unnest(predicates)                                  AS predicate,
           'flag'                                              AS threshold_kind,
           NULL::DOUBLE                                        AS threshold_value
    FROM flag_rules
),
quantile_rows AS (
    -- liquid 의 임계 행 — 플래그 술어 다음 번호. 1 − liquid_top_pct 는 _const 의 DECIMAL 산술이라
    -- 문자열이 깔끔하다(0.5 → 'adv20_rank_pct >= 0.5').
    SELECT f.policy,
           CAST(len(f.predicates) + 1 AS BIGINT)                          AS rule_seq,
           'adv20_rank_pct >= ' || CAST(1 - k.liquid_top_pct AS VARCHAR)  AS predicate,
           'quantile'                                                     AS threshold_kind,
           CAST(k.liquid_top_pct AS DOUBLE)                               AS threshold_value
    FROM flag_rules f
    CROSS JOIN _const k
    WHERE f.policy = 'liquid'
),
rules AS (
    SELECT * FROM flag_rows
    UNION ALL
    SELECT * FROM quantile_rows
)
SELECT
    'krx.' || r.policy   AS universe_id,
    r.policy             AS policy,
    r.rule_seq           AS rule_seq,
    r.predicate          AS predicate,
    r.threshold_kind     AS threshold_kind,
    r.threshold_value    AS threshold_value,
    'convention'         AS basis,
    NULL::DATE           AS measured_at,
    k.version            AS version,
    NULL::VARCHAR        AS reject_reason
FROM rules r
CROSS JOIN _const k
