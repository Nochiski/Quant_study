-- factor_readiness (S20) — 팩터 준비도. grain (factor_id) · whole. DESIGN v1.2 §4-8 · GATES §6 EG10.
--
-- 선언(`_decl_factor`, `rules_s20.install_declarations` 가 올린다)과 `dataset_profile`(S19) 의
-- 조인 하나다. **판정은 여기서 계산된다** — 선언은 "이 팩터가 무슨 재료를 요구하나" 까지만 말하고,
-- 재료가 실제로 서 있는지(프로파일 행 존재·PIT·랙·커버율·확인 필요)는 프로파일이 답한다.
--
-- 사유 우선순위는 아래 CASE 의 순서가 정본이고 `rules_s20.BLOCKED_REASONS` 가 같은 순서를 어휘로
-- 갖는다. 사유 문자열은 `<토큰>: <걸린 field_id 들>` 이라 사람이 표만 보고 원인을 짚을 수 있고,
-- EG10 은 `split_part(blocked_reason, ':', 1)` 로 어휘를 닫는다.
--
-- `first_usable_date` = 요구 필드 `coverage_from` 의 최댓값 — 가장 늦게 열린 재료가 팩터의 시작일이다.
-- 요구 필드 중 하나라도 프로파일에 없거나(field_unavailable) 관측이 0 이면(no_observations) NULL 이다
-- — 재료가 없는데 시작일을 적으면 표가 거짓말을 한다. `partial_support` 로 막힌 행은 재료가 실재하므로
-- 날짜를 남긴다(조건이 풀리면 그 날부터 쓸 수 있다는 뜻).
WITH req AS (
    SELECT d.factor_id, u.field_id
    FROM _decl_factor d, unnest(d.required_field_ids) AS u(field_id)
),
joined AS (
    SELECT r.factor_id, r.field_id, p.table_name, p.column_scope, p.point_in_time,
           p.requires_confirmation, p.recommended_lag_sessions, p.estimated_coverage_pct,
           p.coverage_from
    FROM req r
    LEFT JOIN dataset_profile p ON p.field_id = r.field_id
),
agg AS (
    SELECT factor_id,
           count(*)                                            AS n_required,
           count(table_name)                                   AS n_found,
           list_sort(list(DISTINCT field_id) FILTER (
               WHERE table_name IS NULL))                      AS f_missing,
           list_sort(list(DISTINCT field_id) FILTER (
               WHERE table_name IS NOT NULL AND NOT point_in_time))       AS f_not_pit,
           list_sort(list(DISTINCT field_id) FILTER (
               WHERE table_name IS NOT NULL
                 AND recommended_lag_sessions IS NULL))                   AS f_lag,
           list_sort(list(DISTINCT field_id) FILTER (
               WHERE table_name IS NOT NULL AND estimated_coverage_pct = 0)) AS f_empty,
           list_sort(list(DISTINCT field_id) FILTER (
               WHERE requires_confirmation))                              AS f_partial,
           list_sort(list(DISTINCT table_name || '.' || column_scope) FILTER (
               WHERE table_name IS NOT NULL))                             AS required_columns,
           max(coverage_from)                                             AS coverage_from_max
    FROM joined GROUP BY factor_id
),
verdict AS (
    SELECT d.*, a.required_columns, a.coverage_from_max, a.n_required, a.n_found,
           -- 빈 FILTER 집계는 [] 가 아니라 NULL 이다 — coalesce 없이 비교하면 전 행이 NULL 이 된다
           coalesce(len(a.f_empty), 0) AS n_empty,
           CASE
             WHEN len(a.f_missing) > 0
                  THEN 'field_unavailable: ' || array_to_string(a.f_missing, ', ')
             WHEN len(a.f_not_pit) > 0
                  THEN 'not_point_in_time: ' || array_to_string(a.f_not_pit, ', ')
             WHEN len(a.f_lag) > 0
                  THEN 'lag_unresolved: ' || array_to_string(a.f_lag, ', ')
             WHEN len(a.f_empty) > 0
                  THEN 'no_observations: ' || array_to_string(a.f_empty, ', ')
             WHEN len(a.f_partial) > 0
                  THEN 'partial_support: ' || array_to_string(a.f_partial, ', ')
           END AS blocked_reason
    FROM _decl_factor d JOIN agg a ON a.factor_id = d.factor_id
)
SELECT
    v.factor_id                                                     AS factor_id,
    v.label                                                         AS label,
    v.registry_factor_id                                            AS registry_factor_id,
    coalesce(v.required_columns, [])                                AS required_columns,
    v.required_field_ids                                            AS required_field_ids,
    CASE WHEN v.blocked_reason IS NULL THEN 'ready' ELSE 'blocked' END AS status,
    v.blocked_reason                                                AS blocked_reason,
    v.owner                                                         AS owner,
    CASE WHEN v.n_found = v.n_required AND v.n_empty = 0
         THEN v.coverage_from_max END                               AS first_usable_date,
    v.caveat                                                        AS caveat,
    v.evidence                                                      AS evidence,
    NULL::VARCHAR                                                   AS reject_reason
FROM verdict v
