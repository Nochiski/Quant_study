-- universe_policy (S03·S03B) — 정책 선언표. grain (policy, rule_seq) · whole. DESIGN v1.2 §4-1 · GATES §3 ㉒.
--
-- equity 는 표를 보관만 하고 적용은 팩터층(v_universe(:d, :policy) 는 편의 뷰). 한 정책의 행은 AND 로
-- 묶인다(rule_seq 는 술어 리스트 안 순서 — SQL 본문 숫자 리터럴 금지 규약 때문에 번호를 손으로 적지
-- 않고 generate_subscripts 로 만든다). 전부 플래그형 술어(threshold_kind='flag', threshold_value NULL).
--   all          : TRUE (정책 미적용)
--   common-stock : sec_type='common' ∧ status='listed' — 소비자 계약 UNIVERSE='krx.common-stock'
--                  (FIELD_MAP §1, backend/tests/contract) 이 정책표로 풀리도록 S03B 에서 추가.
--   investable   : common-stock + NOT admin_state + NOT liquidation_window.
--                  admin_state NULL(basis unknown) 행은 NOT NULL = NULL 이라 빠진다 — 서버 S03 실측
--                  (P19′) 에서 unknown 0 이라 실효 없음. 결측 정책이 필요해지면 행을 바꾼다.
--   liquid       : 행 없음 — adv20_krw 분위수 임계는 서버 S03B 실측(EG3_universe.adv20_common_quantiles)
--                  뒤 등재한다(quantile, measured, measured_at = 잰 날, 별도 VALUES 블록으로 UNION ALL).
-- universe_id = 'krx.' || policy (FIELD_MAP §1 어휘). predicate 는 universe_daily 컬럼 위 SQL 불리언 식.
-- threshold_value 는 predicate 안 임계의 출처 기록, measured_at 은 그 임계를 잰 날(flag 는 NULL).
-- version 은 정책 집합 판본(_const, baseline universe_policy.version) — 행이 늘거나 임계가 바뀌면 올린다.
-- 입력 universe_daily 는 EG3_policy 가 predicate 를 그 스키마 위에서 평가해 보기 위한 것이다.
WITH flag_rules AS (
    SELECT policy, predicates
    FROM (VALUES
        ('all',          ['TRUE']),
        ('common-stock', ['sec_type = ''common''', 'status = ''listed''']),
        ('investable',   ['sec_type = ''common''', 'status = ''listed''',
                          'NOT admin_state', 'NOT liquidation_window'])
    ) AS p(policy, predicates)
)
SELECT
    'krx.' || r.policy                                    AS universe_id,
    r.policy                                              AS policy,
    CAST(generate_subscripts(r.predicates, 1) AS BIGINT)  AS rule_seq,
    unnest(r.predicates)                                  AS predicate,
    'flag'                                                AS threshold_kind,
    NULL::DOUBLE                                          AS threshold_value,
    'convention'                                          AS basis,
    NULL::DATE                                            AS measured_at,
    k.version                                             AS version,
    NULL::VARCHAR                                         AS reject_reason
FROM flag_rules r
CROSS JOIN _const k
