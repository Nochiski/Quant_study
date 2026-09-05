-- universe_policy (S03) — 정책 선언표. grain (policy, rule_seq) · whole. DESIGN v1.2 §4-1 · GATES §3 ㉒.
--
-- equity 는 표를 보관만 하고 적용은 팩터층(v_universe(:d, :policy) 는 편의 뷰). S03 은 스키마와
-- 'all' 1행만 낸다 — investable·liquid 의 분위수 임계는 S03B(mktcap·adv20) 실측 뒤 등재한다.
-- universe_id = 'krx.' || policy (FIELD_MAP §1 어휘: krx.all · krx.investable · krx.liquid;
-- krx.common-stock 은 sec_type 축이라 정책 행이 아니다 — S21 에서 판단).
-- predicate 는 universe_daily 컬럼 위 SQL 불리언 식. threshold_value 는 predicate 안 임계의 출처 기록,
-- measured_at 은 그 임계를 잰 날(임계 없는 'all' 은 NULL). version 은 정책 집합 판본(_const).
-- 입력 universe_daily 는 EG3_policy 가 predicate 를 그 스키마 위에서 평가해 보기 위한 것이다.
SELECT
    'krx.' || p.policy    AS universe_id,
    p.policy              AS policy,
    CAST(p.rule_seq AS BIGINT) AS rule_seq,
    p.predicate           AS predicate,
    p.threshold_kind      AS threshold_kind,
    p.threshold_value     AS threshold_value,
    p.basis               AS basis,
    p.measured_at         AS measured_at,
    k.version             AS version,
    NULL::VARCHAR         AS reject_reason
FROM (VALUES
    ('all', 1, 'TRUE', 'flag', NULL::DOUBLE, 'convention', NULL::DATE)
) AS p(policy, rule_seq, predicate, threshold_kind, threshold_value, basis, measured_at)
CROSS JOIN _const k
