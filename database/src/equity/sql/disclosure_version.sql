-- disclosure_version (S11) — 정기보고서 접수 판본·정정 링크. DESIGN v1.2 §4-4 · GATES §3-⑬.
-- grain `rcept_no` · receipt_axis(`substr(rcept_no,1,4)`) · 입력은 stage 4테이블뿐(1단계 비의존).
--
-- 모집단 = `stg_disclosure` 정기보고서 3종(사업·반기·분기) 전건. 접두 `[…]` 를 벗긴 이름이
-- 3종 중 하나로 **시작**하고, 제외 어휘(연장신고·유동화전문회사·회계법인·해외신고)를 포함하지
-- 않는 행. 정정본(`[기재정정]`·`[첨부정정]`)도 원본과 같은 자격으로 한 행씩 남는다 — 정정을
-- 모집단에서 빼면 "정정 있음" 을 asof 로 판정할 재료가 사라진다(DEFECT-E01).
-- 아래 마커 줄 앞까지가 모집단 정의이고 `rules_s11.py:population_sql` 이 그 조각을 그대로
-- 재사용해 EG1 우변을 만든다(정의를 두 벌 베끼지 않는다 — S05 pool_sql 규약).
--
-- 링크(DOC §8.1): 정정 문서 c 의 원본 후보 o 는
--   같은 `group_key`(corp_code · kind · period_label)  ∧  `o.rcept_no < c.rcept_no`
--   ∧ o 자신이 정정이 아님(`corr_prefix ∉ {기재정정, 첨부정정}`; `[첨부추가]` 는 원본 라벨)
-- `kind` 는 **정정 문서 자신의 `report_nm`** 에서 읽는다 — `stg_doc_correction.target_raw` 는
-- 자유 텍스트('분 기 보 고 서'·'2009사업년도 사업보고서'·빈 문자열)라 쓰지 않는다.
-- 후보가 1건이면 `unique`, 여럿이면 ZIP 정정신고 페이지의 `filed_date` 와 `rcept_dt` 가 정확히
-- 일치하는 후보가 딱 하나일 때만 `multi_resolved`, 아니면 `multi_unresolved`(링크 없음).
--
-- `date_check` 는 링크가 성립한 뒤의 **날짜 확인축**이다(링크 성립 여부와 독립 — DOC 결정 ③:
-- 날짜가 어긋나도 링크는 유지한다). `no_zip`/`no_page` 는 `stg_doc_index.zip_ok` 로 가른다.
--
-- 원본 측 팩트 `first_correction_dt`·`n_corrections` 는 이 접수를 가리키는 정정들의 집계다.
-- **`has_correction` 같은 정적 판정 컬럼은 두지 않는다** — 기준일 D 의 "정정 있음" 은 뷰가
-- `first_correction_dt <= D` 로 판정해야 look-ahead 가 없다(DEFECT-E01).
--
-- 기간 라벨 `(YYYY.MM)` 이 없는 행은 **버리지 않는다**(DESIGN §4-4 확정) — `group_key` NULL +
-- `group_key_basis='no_label'` 로 남기고 링크 후보에서만 뺀다. 격리하면 그 접수가 모집단에서
-- 사라져 "정정 없음" 으로 보이는 생존편향을 게이트가 만든다(GATES §5-C6 과 같은 함정).
--
-- **모집단 dedup**: stage `stg_disclosure` 는 `write_mode='append_only'` · `key_unique=False`
-- 라(rules_dart.py) 같은 접수번호가 재수집 판본만큼 쌓인다 — 서버 실측 203건(정기보고서 안 26건),
-- 중복 쌍은 투영 컬럼이 전부 같다. grain 이 `rcept_no` 이므로 **모집단 CTE 에서** 접수번호당
-- 1행만 남긴다. 안 접으면 산출이 같은 행을 두 번 내고 EG1 이 그만큼 어긋난다(서버 1차 빌드
-- 198,189 vs count(DISTINCT rcept_no) 198,163, delta −26). `stg_doc_index` 도 같은 규약이라
-- 조인 전에 접는다(`idx`); `stg_doc_correction` 은 `key_unique=True` 라 접을 필요가 없다.
--
-- PIT: `available_date = rcept_dt`(derived). 격리는 `rcept_dt` 결측 하나뿐이다(그 행은
-- available_date 를 가질 수 없다).
WITH base AS (
    SELECT d.rcept_no,
           d.rcept_dt,
           d.corp_code,
           d.report_nm,
           d.is_correction,
           d.rm_corrected_later,
           nullif(regexp_extract(d.report_nm, '^\[([^\]]*)\]', 1), '')  AS corr_prefix,
           regexp_replace(d.report_nm, '^\[[^\]]*\]', '')               AS nm_clean
    FROM stg_disclosure d
    -- 판본 선택은 first_write_wins(GATES EG6-P04 규약, `observed_date` 최소). 동률은 투영 컬럼
    -- 전체로 깬다 — 판본끼리 payload 가 같으므로 어느 행을 골라도 산출이 같고 EG5a 가 선다.
    QUALIFY row_number() OVER (
        PARTITION BY d.rcept_no
        ORDER BY d.observed_date NULLS LAST, d.rcept_dt NULLS LAST, d.corp_code NULLS LAST,
                 d.report_nm NULLS LAST, d.is_correction NULLS LAST,
                 d.rm_corrected_later NULLS LAST) = 1
),
periodic AS (
    SELECT b.rcept_no, b.rcept_dt, b.corp_code, b.is_correction, b.rm_corrected_later,
           b.corr_prefix,
           CASE WHEN b.nm_clean LIKE '사업보고서%' THEN 'annual'
                WHEN b.nm_clean LIKE '반기보고서%' THEN 'half'
                ELSE 'quarter' END                                      AS kind,
           nullif(regexp_extract(b.nm_clean, '\((\d{4})\.(\d{2})\)', 0), '') AS period_label,
           -- 라벨의 연·월은 정규식 캡처 그룹으로 뽑는다(문자 위치 상수를 쓰지 않는다)
           nullif(regexp_extract(b.nm_clean, '\((\d{4})\.(\d{2})\)', 1), '') AS label_year,
           nullif(regexp_extract(b.nm_clean, '\((\d{4})\.(\d{2})\)', 2), '') AS label_month
    FROM base b
    WHERE (b.nm_clean LIKE '사업보고서%'
           OR b.nm_clean LIKE '반기보고서%'
           OR b.nm_clean LIKE '분기보고서%')
      AND b.nm_clean NOT LIKE '%연장신고%'
      AND b.nm_clean NOT LIKE '%유동화전문회사%'
      AND b.nm_clean NOT LIKE '%회계법인%'
      AND b.nm_clean NOT LIKE '%해외증권거래소%'
)
-- ==== eg1: 모집단 정의 끝. 위 CTE 만으로 EG1 우변이 선다(rules_s11.py:population_sql) ====
, labeled AS (
    SELECT p.*,
           CASE WHEN p.period_label IS NULL THEN NULL
                ELSE p.corp_code || '|' || p.kind || '|' || p.period_label END AS group_key,
           CASE WHEN p.period_label IS NULL THEN 'no_label' ELSE 'label' END    AS group_key_basis,
           -- 라벨 `(YYYY.MM)` → 그 달 말일 = 보고 대상 기간의 종료일(법정기한 계산의 기준)
           CASE WHEN p.period_label IS NULL THEN NULL
                ELSE last_day(make_date(CAST(p.label_year AS INTEGER),
                                        CAST(p.label_month AS INTEGER), 1))
           END                                                                  AS label_period_end
    FROM periodic p
),
corr AS (
    -- `stg_doc_correction` 은 natural_key (rcept_no) · `key_unique=True` 라 접수번호당 1행이
    -- 보장된다(rules_doc.py) — dedup 하지 않는다.
    SELECT rcept_no, page_found, filed_date, filed_date_status, reason_raw, items
    FROM stg_doc_correction
),
idx AS (
    -- `stg_doc_index` 는 append_only · key_unique=False → LEFT JOIN 팬아웃 방어
    SELECT rcept_no, zip_ok
    FROM stg_doc_index
    QUALIFY row_number() OVER (PARTITION BY rcept_no
                               ORDER BY observed_date NULLS LAST, zip_ok NULLS LAST) = 1
),
cand AS (
    SELECT c.rcept_no,
           o.rcept_no                                                   AS orig_rcept_no,
           o.rcept_dt                                                   AS orig_rcept_dt
    FROM labeled c
    JOIN labeled o
      ON o.group_key = c.group_key
     AND o.rcept_no < c.rcept_no
     AND (o.corr_prefix IS NULL OR o.corr_prefix NOT IN ('기재정정', '첨부정정'))
    WHERE c.is_correction
),
picked AS (
    -- 후보 집계. `n_date_hit` 은 ZIP 정정신고 페이지의 filed_date 와 정확히 같은 후보 수다.
    SELECT c.rcept_no,
           count(*)                                                     AS n_cand,
           count(*) FILTER (WHERE k.filed_date_status = 'parsed'
                              AND k.filed_date = c.orig_rcept_dt)       AS n_date_hit,
           max(c.orig_rcept_no)                                         AS sole_rcept_no,
           max(c.orig_rcept_dt)                                         AS sole_rcept_dt,
           max(c.orig_rcept_no) FILTER (WHERE k.filed_date_status = 'parsed'
                              AND k.filed_date = c.orig_rcept_dt)       AS hit_rcept_no,
           max(c.orig_rcept_dt) FILTER (WHERE k.filed_date_status = 'parsed'
                              AND k.filed_date = c.orig_rcept_dt)       AS hit_rcept_dt
    FROM cand c
    LEFT JOIN corr k ON k.rcept_no = c.rcept_no
    GROUP BY c.rcept_no
),
linked AS (
    SELECT l.*,
           k.rcept_no IS NOT NULL                                       AS has_corr_row,
           k.page_found,
           k.filed_date,
           k.filed_date_status,
           k.reason_raw,
           k.items,
           i.zip_ok,
           a.n_cand,
           a.n_date_hit,
           CASE WHEN NOT l.is_correction                    THEN 'n/a'
                WHEN a.n_cand IS NULL                       THEN 'none'
                WHEN a.n_cand = 1                           THEN 'unique'
                WHEN a.n_date_hit = 1                       THEN 'multi_resolved'
                ELSE 'multi_unresolved' END                             AS candidate_status,
           CASE WHEN NOT l.is_correction                    THEN NULL
                WHEN a.n_cand = 1                           THEN a.sole_rcept_no
                WHEN a.n_cand > 1 AND a.n_date_hit = 1      THEN a.hit_rcept_no END
                                                                        AS orig_rcept_no,
           CASE WHEN NOT l.is_correction                    THEN NULL
                WHEN a.n_cand = 1                           THEN a.sole_rcept_dt
                WHEN a.n_cand > 1 AND a.n_date_hit = 1      THEN a.hit_rcept_dt END
                                                                        AS orig_rcept_dt,
           -- 같은 그룹에서 이 접수보다 먼저 접수된 정정 건수. 라벨 없는 행은 자기 자신이 그룹이다.
           CAST(coalesce(sum(CASE WHEN l.is_correction THEN 1 ELSE 0 END) OVER (
               PARTITION BY coalesce(l.group_key, l.rcept_no)
               ORDER BY l.rcept_no
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
                AS BIGINT)                                              AS prior_corr_count
    FROM labeled l
    LEFT JOIN corr k ON k.rcept_no = l.rcept_no
    LEFT JOIN idx i ON i.rcept_no = l.rcept_no
    LEFT JOIN picked a ON a.rcept_no = l.rcept_no
),
back AS (
    -- 원본 측 팩트 — 이 접수를 가리키는 정정들의 최초 접수일·건수
    SELECT x.orig_rcept_no                                              AS rcept_no,
           min(x.rcept_dt)                                              AS first_correction_dt,
           count(*)                                                     AS n_corrections
    FROM linked x
    WHERE x.orig_rcept_no IS NOT NULL
    GROUP BY x.orig_rcept_no
)
SELECT
    v.rcept_no,
    v.corp_code,
    v.rcept_dt,
    v.kind,
    v.period_label,
    v.group_key,
    v.group_key_basis,
    v.is_correction,
    v.corr_prefix,
    v.orig_rcept_no,
    v.candidate_status,
    -- 날짜 확인축. `unparsed`·`no_page`·`no_zip`·`n/a` 는 E-G6b 분모에서 빠진다(GATES EG6-P06).
    -- 링크가 아예 없는 정정(`none`·`multi_unresolved`)은 대조할 원본 접수일이 없으므로
    -- `mismatch` 로 떨어진다 — 링크 성립률은 E-G6a(candidate_status)가 따로 잰다.
    -- 하루 차이는 `off_1d`, 그 위 `date_check_near_days`(어휘가 못박은 7일) 까지가 `off_2_7d` 다.
    CASE WHEN NOT v.is_correction                                     THEN 'n/a'
         WHEN NOT v.has_corr_row
              THEN CASE WHEN coalesce(v.zip_ok, FALSE) THEN 'no_page' ELSE 'no_zip' END
         WHEN NOT coalesce(v.page_found, FALSE)                       THEN 'no_page'
         WHEN v.filed_date_status <> 'parsed' OR v.filed_date IS NULL  THEN 'unparsed'
         WHEN v.orig_rcept_dt IS NULL                                 THEN 'mismatch'
         WHEN v.filed_date = v.orig_rcept_dt                          THEN 'exact'
         WHEN abs(date_diff('day', v.filed_date, v.orig_rcept_dt)) = 1 THEN 'off_1d'
         WHEN abs(date_diff('day', v.filed_date, v.orig_rcept_dt))
              BETWEEN 2 AND CAST(k.date_check_near_days AS BIGINT) THEN 'off_2_7d'
         ELSE 'mismatch' END                                          AS date_check,
    v.prior_corr_count,
    CASE WHEN v.is_correction THEN v.page_found END                   AS corr_page_found,
    CASE WHEN v.is_correction THEN v.filed_date END                   AS filed_date,
    CASE WHEN v.is_correction THEN nullif(trim(v.reason_raw), '') END AS reason_raw,
    -- 정정 항목에 재무표가 걸렸는가(기록형). 키워드는 rules_s11.FIN_ITEM_KEYWORDS 가 정본.
    CASE WHEN NOT v.is_correction OR v.items IS NULL THEN NULL
         ELSE (v.items LIKE '%재무제표%' OR v.items LIKE '%재무상태표%'
               OR v.items LIKE '%손익계산서%' OR v.items LIKE '%현금흐름표%'
               OR v.items LIKE '%자본변동표%' OR v.items LIKE '%요약재무%')
    END                                                               AS corr_has_fin_item,
    bk.first_correction_dt,
    CAST(coalesce(bk.n_corrections, 0) AS BIGINT)                      AS n_corrections,
    -- 법정 제출기한 = 대상 기간 말일 + (사업보고서 90일 / 반기·분기 45일). 상수는 _const.
    CASE WHEN v.label_period_end IS NULL THEN NULL
         WHEN v.kind = 'annual'
              THEN v.label_period_end + CAST(k.deadline_days_annual AS INTEGER)
         ELSE v.label_period_end + CAST(k.deadline_days_interim AS INTEGER) END
                                                                      AS legal_deadline,
    CAST(CASE WHEN v.label_period_end IS NULL THEN NULL
              WHEN v.kind = 'annual'
                   THEN date_diff('day', v.label_period_end
                                         + CAST(k.deadline_days_annual AS INTEGER), v.rcept_dt)
              ELSE date_diff('day', v.label_period_end
                                    + CAST(k.deadline_days_interim AS INTEGER), v.rcept_dt) END
         AS BIGINT)                                                   AS delay_days,
    CASE WHEN v.orig_rcept_no IS NOT NULL THEN 'parsed' ELSE 'n/a' END AS link_basis,
    v.rcept_dt                                                        AS available_date,
    'derived'                                                         AS available_basis,
    CASE WHEN v.rcept_dt IS NULL THEN 'rcept_dt_missing' END          AS reject_reason
FROM linked v
LEFT JOIN back bk ON bk.rcept_no = v.rcept_no
CROSS JOIN _const k
