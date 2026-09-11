-- dataset_profile (S19) — 공개시점 대장. grain (field_id) · whole. DESIGN v1.2 §4-7 · GATES §3 ㉒.
--
-- 두 임시 테이블의 조인 하나다. 둘 다 `rules_s19.install_declarations` 가 `_const` 와 같은 통로로
-- 올린다(EquityTable.declarations 훅):
--   `_decl_field`      각 rules_s*.py 의 `EquityTable.field_profiles` 선언 + 소유 테이블에서 유도한
--                      `source_stage_tables`·`available_date_basis`·`supported_cell_kinds`.
--                      랙·PIT·cell kind 는 여기서만 온다 — 이 파일에 리터럴을 쓰지 않는다.
--   `_field_coverage`  고정한 equity 파티션 실측 — 창(coverage_from·to)·커버율(%)·정수 분자·분모·
--                      시총 분위 5. 커버율은 정수 두 개의 순수 함수이고(파이썬 나눗셈 + 고정
--                      반올림) 그 항등은 EG2_dataset_profile 이 SQL 로 다시 확인한다 — 엔진의
--                      부동소수 축약 순서가 content_hash 에 섞이지 않게 하는 장치다(§9 S19 2차).
--                      필드마다 읽는 테이블·컬럼이 달라 SQL 한 벌로는 못 재므로 파이썬이 질의를
--                      만들어 재고 그 결과만 여기로 들어온다(값의 정의는 rules_s19 docstring).
--
-- 컬럼 순서 = DESIGN §4-7 목록 + `field_scope`(FIELD_MAP 42 어휘 / equity 내부 스코프)
-- + `basis`(e1.15.0 — 이 대장이 어느 판에서 나왔는가: manual · evening 저녁 잠정 · morning 아침 확정).
-- 판은 세션 테이블 `_build` 에서 온다(`build.make_build_meta`, `_const` 와 같은 통로). 소비자는
-- `list_fields()` 한 번으로 「내가 지금 보는 데이터셋이 잠정판인가」를 알 수 있어야 한다 —
-- 빌드 시각은 **여기 싣지 않는다**: 같은 입력으로 다시 지으면 값이 달라져 EG5a(같은 inputs →
-- content_hash 동일)가 매번 깨진다. 시각의 정본은 MANIFEST `built_at_utc` 와 `_catalog_meta.json`
-- 의 `written_at_utc` 다.
-- 격리는 하나뿐이다 — 창을 어디서도 못 잡은 필드(`no_coverage_window`). 커버율 0 은 격리가 아니라
-- 사실이고(선언은 있으나 행이 없다), 그 판정은 S20 EG10 이 blocked(no_observations)로 한다.
SELECT
    d.field_id                          AS field_id,
    d.table_name                        AS table_name,
    d.column_scope                      AS column_scope,
    d.source_stage_tables               AS source_stage_tables,
    d.label                             AS label,
    d.unit                              AS unit,
    d.value_type                        AS value_type,
    d.frequency                         AS frequency,
    d.available_date_basis              AS available_date_basis,
    d.recommended_lag_sessions          AS recommended_lag_sessions,
    d.recommended_lag_days              AS recommended_lag_days,
    d.disclosure_basis                  AS disclosure_basis,
    d.evidence                          AS evidence,
    d.point_in_time                     AS point_in_time,
    d.requires_confirmation             AS requires_confirmation,
    d.supported_cell_kinds              AS supported_cell_kinds,
    c.coverage_from                     AS coverage_from,
    c.coverage_to                       AS coverage_to,
    d.coverage_basis                    AS coverage_basis,
    c.estimated_coverage_pct            AS estimated_coverage_pct,
    c.n_observed                        AS n_observed,
    c.n_denominator                     AS n_denominator,
    c.coverage_by_mktcap_quintile       AS coverage_by_mktcap_quintile,
    d.field_scope                       AS field_scope,
    b.basis                             AS basis,
    CASE WHEN c.coverage_from IS NULL OR c.coverage_to IS NULL
         THEN 'no_coverage_window' END  AS reject_reason
FROM _decl_field d
LEFT JOIN _field_coverage c ON c.field_id = d.field_id
CROSS JOIN _build b
