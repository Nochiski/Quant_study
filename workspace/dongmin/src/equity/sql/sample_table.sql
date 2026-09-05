-- 테스트 전용 샘플 규칙 (T0 build→gate→commit 왕복). 실제 테이블은 T1 부터.
SELECT *, NULL::VARCHAR AS reject_reason FROM stg_sample
