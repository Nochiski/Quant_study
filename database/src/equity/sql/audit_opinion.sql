-- audit_opinion (S15) — 회계감사인의 감사의견. DESIGN v1.2 §4-5 · GATES §3-⑰.
-- grain (`corp_code`, `bsns_year`, `reprt_code`, `bsns_year_label`) · receipt_axis.
--
-- 모집단 = `stg_audit` 전건. `bsns_year` 는 **요청축**(어느 사업보고서에 실렸는가)이고
-- `bsns_year_label` 은 응답이 준 **기수 라벨**('제63기(전기)' — 연도가 아니다, rules_dart.py).
-- 한 사업보고서가 당기·전기·전전기 3개 기수를 함께 실으므로 두 축이 모두 키에 들어간다.
--
-- **의견 원문은 그대로 싣는다** — `adt_opinion` 은 enum 이 아니라 변형 434종이고(STAGE_SPEC
-- §2-13) 분류는 stage 가 붙인 `adt_opinion_class` 를 그대로 나른다. 여기서 다시 분류하지 않는다:
-- 두 벌을 만들면 '부적정 선매칭' 함정(LIKE '%적정%' 을 먼저 쓰면 부적정이 적정으로 뒤집힌다)을
-- 두 곳에서 지켜야 한다. 대신 `EG3_audit_opinion` 이 원문에서 독립 재분류해 대조한다(기록형).
--
-- **판본·연결/별도 dedup**: `stg_audit` 은 append_only · `key_unique=False`("bsns_year_label 이
-- '-' 인 4,805행이 한 요청축에 몰리면 충돌") 라 grain 이 유일하지 않다. 절단본 실측 291행 →
-- grain 226개(충돌 52그룹 65행). 원인 둘: ① 응답이 같은 행을 그대로 두 번 준다(31그룹 75행이
-- 투영 컬럼까지 전부 동일) ② **연결/별도 감사보고서 2행**인데 그 구분이 구조 컬럼이 아니라
-- 자유 텍스트(`emphs_matter`·`core_adt_matter` 안의 '(연결재무제표)'/'(별도재무제표)')에만 있다
-- (21그룹). 키로 쓸 컬럼이 없으므로 first_write_wins → `row_hash` 순으로 한 행만 남기고 접힌 수를
-- `n_source_rows` 로 남긴다. 절단본에서 접힌 행들의 `adt_opinion`·`adt_opinion_class` 는 그룹
-- 안에서 전부 같아(충돌 0) 의견 자체는 손실이 없다 — 갈리면 `EG3_audit_opinion` 의
-- `n_class_conflict_grain` 이 잡는다(GATES §9 · DESIGN §11 기록).
--
-- PIT: `available_date = rcept_dt`(derived, stage 가 접수번호로 조회). 내용일 `stlm_dt`.
-- 격리는 `rcept_dt_missing` 하나다.
WITH base AS (
    SELECT a.corp_code,
           a.bsns_year,
           a.reprt_code,
           a.bsns_year_label,
           a.rcept_no,
           a.stlm_dt,
           a.adtor,
           a.adt_opinion,
           a.adt_opinion_class,
           a.adt_reprt_spcmnt_matter,
           a.emphs_matter,
           a.core_adt_matter,
           a.available_date,
           a.observed_date,
           a.row_hash
    FROM stg_audit a
),
folded AS (
    SELECT b.*,
           count(*) OVER (PARTITION BY b.corp_code, b.bsns_year, b.reprt_code,
                                       b.bsns_year_label)               AS n_source_rows
    FROM base b
    QUALIFY row_number() OVER (
        PARTITION BY b.corp_code, b.bsns_year, b.reprt_code, b.bsns_year_label
        ORDER BY b.observed_date NULLS LAST, b.row_hash NULLS LAST) = 1
)
SELECT f.corp_code,
       f.bsns_year,
       f.reprt_code,
       f.bsns_year_label,
       f.rcept_no,
       f.stlm_dt,
       f.adtor,
       f.adt_opinion,
       f.adt_opinion_class,
       f.adt_reprt_spcmnt_matter,
       f.emphs_matter,
       f.core_adt_matter,
       f.n_source_rows,
       f.available_date,
       'derived'                                                        AS available_basis,
       CASE WHEN f.available_date IS NULL THEN 'rcept_dt_missing' END    AS reject_reason
FROM folded f
