# 문서층(L1) → equity 통합 제안서 v1 (2026-09-05)

> 대상: `EQUITY_DESIGN.md` v1.1 · `EQUITY_WORKFLOW.md` v1.1 을 stage 문서층 4테이블(P1 완료, `origin/stage/doc-p1`)과 P2·P3(미빌드)에 맞춰 고치는 안.
> 근거 표기: **(서버 09-05)** = 이 세션에 주어진 서버 실측 · **(DOC §x)** = `DOC_DESIGN.md` v1.1 · **(DESIGN §x)**·**(WORKFLOW §x)** = equity 설계서 v1.1 · **(HANDOFF §x)** = `STAGE_HANDOFF.md` v1 · **(코드 파일:라인)** = `origin/stage/doc-p1` 또는 `origin/main` 실물.
> 근거 없는 수치는 적지 않는다. 확인 못 한 것은 **미확인 + 검증 SQL** 로 남긴다. 결함 지적만 `.claude/rules/pr-review.md` 4요소 양식.

---

## 0. 요약 — 결정 요청 7건

| # | 결정 | 권고 | 걸린 절 |
|---|---|---|---|
| D-1 | `disclosure_version` grain | **(group_key, correction_seq) → (rcept_no)**. `correction_link` 는 별도 테이블이 아니라 이 표의 컬럼 11개 | §1 |
| D-2 | `fin_std` grain | **`vintage_kind`·`rcept_no` 를 PK 에 지금(P2 전에) 넣는다.** P2 전에는 `vintage_kind='api_restated'` 상수 1값 | §2 |
| D-3 | `fin_std.period_end` 정본 | **`stg_doc_meta.period_to`(문서 원문 TU)를 1순위, DESIGN §4-4 `bsns_year`×`acc_mt` 관례를 2순위 폴백**으로. `corp.fiscal_month` 는 검산축으로 강등 | §3 |
| D-4 | `fin_std.has_correction` | **정적 컬럼 폐기** → `first_correction_dt`·`n_corrections` 팩트 + 뷰가 asof 판정 (DEFECT-E01) | §1·§2 |
| D-5 | `stg_doc_section` | **equity 팩트 테이블에 넣지 않는다.** 예외 2곳(P3 좌표 조인, 6단계 profile evidence)은 팩트 아님을 명시 | §4 |
| D-6 | 단계 분해 | 현행 4단계를 **4A(지금 가능) / 4C(P2 대기) / 4B′(P3 대기)** 로 쪼개고, 4A 를 1단계 병렬로 올린다 | §5 |
| D-7 | 모집단 술어 | 정기보고서 모집단을 **5단 사다리**로 선언하고 각 단의 건수를 baseline 에 등재. 세 문서의 20,579 / 24,285 / 181,106 을 화해시키기 전에는 E-G6a·E-G7 임계를 걸지 않는다 (DEFECT-E03) | §1·§6 |

**한 문장**: P1 만으로 (a) 정정→원본 링크가 확정 술어로 서고, (b) `period_end` 가 관례 추정에서 문서 원문으로 바뀌고, (c) `fin_std ⋈ disclosure_version` 조인 키가 4키 문자열에서 `rcept_no` 로 내려온다 — **셋 다 P2 를 기다리지 않는다.** P2 가 여는 것은 판본 결합(`original`/`corrected`)뿐이고, P3 가 여는 것은 4-B 6테이블의 2011~2014 커버리지다.

---

## 1. `disclosure_version` 재설계

### 1.1 현행과의 관계 — 대체가 아니라 **흡수**

DESIGN §4-4 의 "group_key 문자열 그룹" 은 폐기 대상이 아니다. DOC §8.1 의 후보 술어가 **바로 그 그룹 술어**(같은 `corp_code` · 같은 `kind` · 같은 `period_label`)에 `corr_prefix ∉ {기재정정, 첨부정정}` 과 `rcept_no <` 두 조건을 더한 것이기 때문이다(DOC §8.1). 즉 그룹은 기계이고 링크는 그 안에서 해소된 방향 간선이다. 둘을 두 테이블로 나누면 1:0..1 조인이 하나 늘 뿐 얻는 게 없다.

바꾸는 것은 **grain 하나**다.

| | 현행 (DESIGN §4-4) | 제안 |
|---|---|---|
| grain | (`group_key`, `correction_seq`) | **(`rcept_no`)** |
| EG1 | = 정기보고서 3종 접수 행수 | = `count(DISTINCT rcept_no)` — **좌·우변이 같은 것을 세므로 항등** |
| PK 유일성 | 같은 그룹에 원본이 2건이면 (group_key, 0) 충돌 — 미확인 | `rcept_no` 는 stage 자연키. 충돌 불가능 |
| `fin_std` 조인 | 4키(corp_code, bsns_year, reprt_code, fs_div) 등가조인 | **`rcept_no` 등가조인** (`stg_fin.rcept_no` 는 required=True, `rules_dart.py:64`). 4키는 검산으로 강등 |
| 라벨 없는 586건(P12) | `group_key_basis='no_label'` 격리 = 행이 사라짐 | 행은 남고 `group_key IS NULL` + `group_key_basis='no_label'`. 링크만 `candidate_status='none'` |

grain 을 `rcept_no` 로 내리면 DESIGN §4-4 의 EG1 이 항등식이 되고(따라서 EG1 이 실제로 검사할 것은 **모집단 술어**뿐 — §1.5), `correction_seq` 는 파생 컬럼이 된다.

### 1.2 컬럼 명세

파티션 `receipt_axis`, `partition_expr = substr(rcept_no, 1, 4)` (DESIGN §2 · stage 와 동일).
`available_date = rcept_dt`, **basis = `measured`** — `stg_disclosure.available` 이 `AvailableRule("column", column="rcept_dt", basis="measured")` 이기 때문이다(`rules_dart.py` STG_DISCLOSURE). DESIGN §4-5 가 receipt_axis 표를 일괄 `derived` 로 적은 것과 다르다. 참조표를 거치지 않으므로 `measured` 가 맞다.

| 컬럼 | 타입 | 값 · 규칙 |
|---|---|---|
| `rcept_no` | TEXT(14) | PK. 14자리 고정폭이라 **문자열 대소 = 시간 순** (링크 술어가 이걸 쓴다) |
| `corp_code` | TEXT(8) | `stg_disclosure.corp_code` |
| `stock_code` · `has_ticker` | TEXT(6) · BOOL | 접수 시점 값. **모집단 필터로 쓰지 않는다**(§1.5) |
| `rcept_dt` | DATE | 접수일 = `available_date` |
| `report_nm_raw` | TEXT | 원문 (정규화는 stage 가 공백만) |
| `corr_prefix` | TEXT | `none` / `기재정정` / `첨부정정` / `첨부추가` / `기타` — 접두 **캡처는 제거 전에** |
| `kind` | TEXT | `annual`(사업) / `half`(반기) / `q`(분기) |
| `period_label` | TEXT | `(YYYY.MM)`, 없으면 NULL |
| `group_key` | TEXT | `corp_code‖'|'‖kind‖'|'‖period_label`, `period_label` NULL 이면 NULL |
| `group_key_basis` | TEXT | `label` / `no_label` |
| `bsns_year`·`reprt_code` | TEXT | 라벨 + `corp.fiscal_month` 역산 (현행 유지, **검산용**) |
| `is_correction` | BOOL | `corr_prefix IN ('기재정정','첨부정정')`. `stg_disclosure.is_correction`(= `report_nm LIKE '[%정정]%'`)과 일치해야 함 → E-G3i |
| `correction_seq` | INT | 원본 0. 정정은 `prior_corr_count + 1` (§1.3 정의로 통일) |
| `prior_corr_count` | INT | 같은 group 의 정정 접수 중 `rcept_no` 가 더 작은 것의 수 (DOC §8.1) |
| `orig_rcept_no` | TEXT(14) | 정정 행만. 원본 행은 NULL |
| `orig_rcept_dt` | DATE | 위의 접수일 |
| `candidate_status` | TEXT | `original` / `unique` / `none` / `multi_resolved` / `multi_unresolved` |
| `n_candidates` | INT | 후보 수 |
| `link_resolver` | TEXT | `unique` / `filed_date` / `period_to` / `none` — `multi_resolved` 가 무엇으로 풀렸나 (DOC 어휘 확장) |
| `none_reason` | TEXT | `ledger_floor`(원본이 원장 하한 밖) / `unmatched` / NULL — **게이트 분모를 가르는 축**(§1.4) |
| `has_zip` | BOOL | `stg_doc_index.zip_ok` (행 없으면 false) |
| `corr_page_found` | BOOL | `stg_doc_correction` 행 존재 (§1.6 DEFECT-E02) |
| `filed_date` · `filed_date_status` | DATE · TEXT | `stg_doc_correction` 계승 |
| `date_diff_days` | INT | `orig_rcept_dt − filed_date` (부호 보존) |
| `date_check` | TEXT | `exact`/`off_1d`/`off_2_7d`/`mismatch`/`unparsed`/`no_page`/`no_zip`/`n/a` |
| `n_corr_items` | INT | `stg_doc_correction.n_items` |
| `corr_has_fin_item` | BOOL | `items` 에 자산총계·당기순이익·매출액·영업이익 중 하나 이상 (서버 09-05: 2,238행) |
| `rm_corrected_later` | BOOL | `stg_disclosure` extras 계승. **PIT 금지 · 검산축 전용**(§1.4) |
| `doc_acode` | TEXT | ZIP 있는 접수만. `stg_doc_meta`(main) 계승 — `kind` 검산 (E-G3g) |
| `period_to_doc` | DATE | `stg_doc_meta.period_to` — `period_label` 검산 (E-G3h) · multi 해소 3순위 |

### 1.3 술어 (DuckDB)

**① 모집단 `_reg`** (§1.5 사다리의 L4)

```sql
CREATE OR REPLACE TABLE _reg AS
WITH one AS (                                   -- append_only 판본 접기: PIT = min(observed_date)
  SELECT * FROM (
    SELECT d.*, row_number() OVER (PARTITION BY rcept_no
                                   ORDER BY observed_date, rcept_dt) AS rn
    FROM stg_disclosure d) WHERE rn = 1),
p AS (
  SELECT rcept_no, corp_code, stock_code, has_ticker, rcept_dt, rm_corrected_later,
         report_nm AS report_nm_raw,
         coalesce(regexp_extract(report_nm, '^\[([^\]]*)\]', 1), '')      AS prefix_raw,
         regexp_replace(report_nm, '^(\[[^\]]*\])+', '')                  AS base_nm
  FROM one)
SELECT rcept_no, corp_code, stock_code, has_ticker, rcept_dt, rm_corrected_later, report_nm_raw,
  CASE prefix_raw WHEN '' THEN 'none' WHEN '기재정정' THEN '기재정정'
       WHEN '첨부정정' THEN '첨부정정' WHEN '첨부추가' THEN '첨부추가' ELSE '기타' END AS corr_prefix,
  CASE WHEN base_nm LIKE '사업보고서%' THEN 'annual'
       WHEN base_nm LIKE '반기보고서%' THEN 'half'
       WHEN base_nm LIKE '분기보고서%' THEN 'q'  END                       AS kind,
  nullif(regexp_extract(base_nm, '\((\d{4}\.\d{1,2})\)', 1), '')           AS period_label
FROM p
WHERE (base_nm LIKE '사업보고서%' OR base_nm LIKE '반기보고서%' OR base_nm LIKE '분기보고서%')
  AND base_nm NOT LIKE '%유동화전문회사%'          -- DESIGN §4-4 제외 4종
  AND base_nm NOT LIKE '회계법인사업보고서%'
  AND base_nm NOT LIKE '%제출기한연장신고서%'
  AND base_nm NOT LIKE '%국내신고%';
```

- `corr_prefix` 캡처를 **접두 제거보다 먼저** 한다. DESIGN §4-4 는 `^\[[^\]]*\]` 를 지운 뒤 종류를 뽑는다고만 적어 접두 자체가 버려진다 — `[첨부추가]` 를 원본으로 세려면 접두가 필요하다(DOC §1.7: `[첨부추가]` 는 새 접수가 아니라 원본에 붙는 라벨).
- 접두가 둘 이상 붙는지 **미확인**. 검증 SQL: `SELECT count(*) FROM stg_disclosure WHERE regexp_matches(report_nm, '^\[[^\]]*\]\[')` — 0 이 아니면 `prefix_raw` 를 LIST 로 바꾼다.
- 같은 `rcept_no` 가 관측에 따라 접두가 바뀌는지 **미확인**(DOC §1.7 이 5건 차이로 시사). 검증 SQL:
  ```sql
  SELECT count(*) FROM (SELECT rcept_no FROM stg_disclosure
    GROUP BY 1 HAVING count(DISTINCT report_nm) > 1);
  ```
  0 이 아니면 위 `min(observed_date)` 선택이 정본이며, 건수를 `_meta` 에 남긴다.

**② 후보 · `candidate_status`**

```sql
CREATE OR REPLACE TABLE _cand AS
SELECT c.rcept_no AS corr_rcept_no, o.rcept_no AS cand_rcept_no, o.rcept_dt AS cand_rcept_dt
FROM _reg c JOIN _reg o
  ON  o.corp_code    = c.corp_code
 AND  o.kind         = c.kind
 AND  o.period_label = c.period_label            -- NULL 은 조인되지 않음 → group_key_basis='no_label'
 AND  o.corr_prefix IN ('none', '첨부추가')       -- [첨부추가] 는 원본 라벨 (DOC §1.7)
 AND  o.rcept_no     < c.rcept_no                -- 14자리 고정폭 = 시간 순
WHERE c.corr_prefix IN ('기재정정', '첨부정정');

CREATE OR REPLACE TABLE _link AS
WITH n AS (SELECT corr_rcept_no, count(*) n_cand, min(cand_rcept_no) any_cand
           FROM _cand GROUP BY 1),
     dm AS (SELECT k.corr_rcept_no, count(*) n_hit, min(k.cand_rcept_no) hit
            FROM _cand k JOIN _corr f ON f.rcept_no = k.corr_rcept_no
            WHERE f.filed_date = k.cand_rcept_dt GROUP BY 1),
     pm AS (SELECT k.corr_rcept_no, count(*) n_hit, min(k.cand_rcept_no) hit
            FROM _cand k JOIN stg_doc_meta m
              ON m.rcept_no = k.cand_rcept_no AND m.member_role = 'main'
            JOIN _corr_meta cm ON cm.rcept_no = k.corr_rcept_no
            WHERE m.period_to = cm.period_to GROUP BY 1)
SELECT c.rcept_no,
  CASE WHEN n.n_cand  = 1 THEN 'unique'
       WHEN n.n_cand IS NULL THEN 'none'
       WHEN dm.n_hit = 1 THEN 'multi_resolved'
       WHEN pm.n_hit = 1 THEN 'multi_resolved'
       ELSE 'multi_unresolved' END AS candidate_status,
  CASE WHEN n.n_cand  = 1 THEN 'unique'
       WHEN dm.n_hit  = 1 THEN 'filed_date'
       WHEN pm.n_hit  = 1 THEN 'period_to' ELSE 'none' END AS link_resolver,
  CASE WHEN n.n_cand = 1 THEN n.any_cand
       WHEN dm.n_hit = 1 THEN dm.hit
       WHEN pm.n_hit = 1 THEN pm.hit END AS orig_rcept_no,
  coalesce(n.n_cand, 0) AS n_candidates
FROM _reg c LEFT JOIN n ON n.corr_rcept_no = c.rcept_no
            LEFT JOIN dm ON dm.corr_rcept_no = c.rcept_no
            LEFT JOIN pm ON pm.corr_rcept_no = c.rcept_no
WHERE c.corr_prefix IN ('기재정정', '첨부정정');
```

`period_to` 3순위는 **DOC §8.1 어휘 확장**이다. DOC 은 `multi_resolved` 를 `filed_date` 로만 정의했는데, ZIP 없는 정정(모집단의 14.5%·§1.5)은 `filed_date` 자체가 없어 자동으로 `multi_unresolved` 가 된다. `period_to` 는 원본 문서에서 오므로 정정 쪽 ZIP 이 없어도 후보를 좁힌다. C340 실측 multi = 0 이라 발화 빈도는 낮을 것으로 예상되나, 임계를 재기 전에 발화 건수를 `_meta` 에 남긴다.

**③ `correction_seq` · `prior_corr_count`** — 하나의 정의로 통일한다.

```sql
prior_corr_count = count(*) FILTER (WHERE p.rcept_no < c.rcept_no)   -- p: 같은 group 의 정정 접수
correction_seq   = CASE WHEN c.is_correction THEN prior_corr_count + 1 ELSE 0 END
```
DOC §8.1 의 `prior_corr_count`(ZIP 무관) 와 DESIGN §4-4 의 `correction_seq`(0 = 원본) 가 같은 축의 두 이름이었다. 하나로 묶으면 "정정의 정정" 도 자연스럽다 — `orig_rcept_no` 는 항상 최초 원본이고(후보 술어가 `corr_prefix ∈ {none, 첨부추가}` 만 보므로) `correction_seq` 가 체인 깊이를 준다(DOC §8.1 마지막 문장과 일치).

**④ `date_check`**

```sql
CASE WHEN orig_rcept_no IS NULL              THEN 'n/a'
     WHEN NOT has_zip                         THEN 'no_zip'
     WHEN NOT corr_page_found                 THEN 'no_page'
     WHEN filed_date IS NULL                  THEN 'unparsed'
     WHEN date_diff_days = 0                  THEN 'exact'
     WHEN abs(date_diff_days) = 1             THEN 'off_1d'
     WHEN abs(date_diff_days) <= 7            THEN 'off_2_7d'
     ELSE 'mismatch' END
```
`date_diff_days = orig_rcept_dt − filed_date` 를 **부호째** 싣는다. 제출일 ≤ 접수일이 관행이므로 분포가 한쪽으로 쏠려야 정상이고, 음수가 많으면 앵커 정규식(`parsers_doc.py:378` `최초\s*제출일\s*[:：]?[ ]*([^\n]{0,40})`)이 정정 자신의 제출일 표를 집었다는 신호다.

### 1.4 게이트

| 게이트 | 유형 | 술어 | 분모 정의 | 초기 임계 · 근거 |
|---|---|---|---|---|
| **E-G6a** | 첫 빌드 기록형 → 승인 후 **폐기형** | `candidate_status ∈ {unique, multi_resolved}` 비율 | `is_correction ∧ none_reason IS DISTINCT FROM 'ledger_floor'` — **ZIP 유무 무관**(후보 술어는 원장만 본다) | ≥ 99% (DOC C340 339/340 = 99.7%) |
| **E-G6b** | 기록형 → baseline | `date_check ∈ {exact, off_1d}` 비율 | `filed_date_status='parsed' ∧ candidate_status ∈ {unique, multi_resolved} ∧ corr_prefix='기재정정'` | 기록. C340 276/291 = 94.8%. **`[첨부정정]` 은 분모 밖**(감사보고서 제출일을 적는다 — DOC §1.7) |
| **E-G6c** | 폐기형 | 모집단 5단 사다리(§1.5)의 각 단 건수 = baseline | — | 첫 빌드 측정 → 등재 |
| **E-G7a** | 첫 빌드 기록형 → 폐기형 | `rm_corrected_later=true` 원본 중 링크로 도달되는 비율 | `correction_seq = 0 ∧ rm_corrected_later` | ≥ 99% (DOC §1.7: 2015~2024 사업보고서 6,156/6,162 = 99.9%) |
| **E-G7b** | 기록형 | 링크가 도달했는데 `rm_corrected_later=false` 인 원본 수 | — | 기록. DOC §1.7 표: 5/6,161 |
| **E-G3g** | 폐기형 | ZIP 있고 main `parse_mode ∈ (ok,lenient)` 인 접수에서 `kind` ↔ `doc_acode` 일치율 | main XML 168,938 (서버 09-05) | = 100% 기대. **문서층이 새로 여는 검산** — report_nm 파싱이 옳은지 문서 원문이 판정한다 |
| **E-G3h** | 폐기형 | 같은 분모에서 `period_label` = `strftime(period_to, '%Y.%m')` 일치율 | 동상 | ≥ baseline. group_key 의 정당성 = 링크의 정당성이므로 여기가 흔들리면 E-G6a 가 무의미 |
| **E-G3i** | 폐기형 | `is_correction`(equity 접두) = `stg_disclosure.is_correction`(`LIKE '[%정정]%'`) | 모집단 전건 | 위반 0 |

**`rm` 검산축 SQL** (E-G7a·b 한 번에):

```sql
WITH orig AS (SELECT rcept_no, rm_corrected_later FROM disclosure_version WHERE correction_seq = 0),
     hit  AS (SELECT DISTINCT orig_rcept_no FROM disclosure_version WHERE orig_rcept_no IS NOT NULL)
SELECT count(*) FILTER (WHERE o.rm_corrected_later)                                AS n_flag,
       count(*) FILTER (WHERE o.rm_corrected_later AND h.orig_rcept_no IS NOT NULL) AS n_flag_linked,
       count(*) FILTER (WHERE NOT o.rm_corrected_later AND h.orig_rcept_no IS NOT NULL) AS n_link_no_flag
FROM orig o LEFT JOIN hit h ON h.orig_rcept_no = o.rcept_no;
```

**`rm` 은 절대 PIT 축이 아니다.** `stg_disclosure.rm` 은 재수집 시점의 현재값이고(DOC §1.7: "현재 시점 값이라 PIT 에는 못 쓰지만"), `rm_corrected_later` 는 정의상 미래를 안다. `disclosure_version` 에 싣되 **`v_fin_latest`·`v_universe` 어느 뷰에도 노출하지 않는다**. EG-C 에 한 줄 추가 권고: `⑪ v_* 어느 뷰의 반환 컬럼에도 rm_* 가 없다`.

**`none_reason`**:
```sql
none_reason = CASE
  WHEN candidate_status <> 'none' THEN NULL
  -- 기간말 + 종류별 제출 랙(사업 100일·반기 60·분기 50, baseline) 이 원장 하한 이전
  WHEN period_end_est + INTERVAL (lag_days) DAY < DATE (SELECT value FROM baseline
         WHERE table='disclosure_version' AND metric='ledger_floor') THEN 'ledger_floor'
  ELSE 'unmatched' END
```
DOC C340 의 유일한 실패(`20100105000010`, 분기보고서 2009.09)가 정확히 이 경우다. 전량에서는 2010-01~06 접수의 정정이 계통적으로 여기 걸리므로, 이걸 분모에 두면 E-G6a 임계 99% 가 원장 하한 때문에 깨진다.

### 1.5 모집단 5단 사다리 — 임계보다 먼저 확정할 것

세 문서가 세 값을 낸다.

| 출처 | 값 | 술어 |
|---|---|---|
| DOC §1.7 | 정정 접수 **20,579** (= ZIP 17,603 + `014` 2,975 + doc_store 없음 1) | 사업/반기/분기 · 연장신고 제외 · **`stock_code <> ''`** · DISTINCT rcept_no |
| 서버 09-05 | 정정 정기보고서 접수 **24,285** | (술어 미기재) |
| HANDOFF §4 | 정기보고서 그룹 181,106 중 정정 있음 **20,759** | 그룹 축(접수 축 아님) |

이 셋이 화해되기 전에 E-G6a·E-G7a 에 임계를 거는 것은 게이트가 아니라 장식이다(DEFECT-E03). 사다리를 `_meta` 와 `baseline.json` 에 등재한다.

| 단 | 술어 | 대조 대상 |
|---|---|---|
| L0 | `stg_disclosure` 전건 | 3,444,101 (HANDOFF §3) |
| L1 | + `base_nm` ∈ {사업, 반기, 분기}보고서 | — |
| L2 | − 제외 4종(유동화전문회사·회계법인사업보고서·제출기한연장신고서·국내신고) | — |
| L3 | − `period_label IS NULL` (P12: 586) | 586 |
| **L4** | **= 모집단 A (equity EG1 좌변)** | — |
| L5 | L4 ∩ `has_ticker` | DOC §1.7 의 172,911(단 DOC 은 DISTINCT (rcept_no, 접두)라 ~5 이중계상) |
| L6 | L4 중 `corr_prefix ∈ {기재정정, 첨부정정}` | 20,579 / 24,285 대조 |
| L7 | L6 ∩ `has_zip` | 17,603(DOC) vs `stg_doc_correction` 17,600(서버 09-05) |

**권고: 모집단 = L4 (`has_ticker` 로 걸러지지 않는다).** 이유 셋 — (a) `fin_std` 는 `corp_code` 축이라 티커 유무와 무관하다, (b) 상장 전·비상장 시기 접수를 모집단에서 빼면 그 원본이 "정정 없음" 으로 읽혀 DOC §0 이 지목한 silent PIT 오염이 그대로 재발한다, (c) `corp_ticker` 매핑은 뷰가 한다(DESIGN §4-1). L5 는 DOC 대조용 진단 컬럼으로만 남긴다.

`has_zip` 은 L7 에서 **필터가 아니라 컬럼**이다 — DOC 결정 ④ 의 요지가 그것이다.

### 1.6 실측 92% vs C340 99.7% — 원인 후보와 측정 항목

**두 수치는 서로 다른 것을 재고 있다.**

| | C340 99.7% | 서버 09-05 92.1% |
|---|---|---|
| 잰 것 | `candidate_status ∈ {unique}` — **원장 술어**(corp_code · kind · period_label · 접두 · rcept_no <) | `(corp_code, 종류, filed_date = 원본 rcept_dt)` **등가조인** — 날짜를 키로 씀 |
| 분모 | ZIP 있는 정정 340 (날짜 해석 여부 무관) | `filed_date_status='parsed'` 15,225 |
| DOC 어휘로 | `candidate_status` | `date_check = 'exact'` |

C340 이 같은 축으로 잰 값은 **`exact` 273/291 = 93.8%** 이고(DOC §1.7), 09-05 의 14,026/15,225 = **92.1%** 는 그 옆에 있다. 즉 실측은 C340 을 반박하지 않고 **`date_check` 축에서 재현**한다. `filed_date` 를 조인 키로 쓰면 안 된다는 DOC 결정 ③("XML 날짜 우선은 불일치 18/291 을 링크 오류로 만듦")이 전량에서 그대로 성립한 셈이다.

남는 잔차(93.8% → 92.1%)와 링크 자체의 실패율에 대한 **원인 후보**:

1. **술어 차이 (지배적)** — 위 표. 측정 항목: 전량에서 DOC §8.1 규칙을 그대로 돌려 `candidate_status` 분포와 `date_check` 분포를 **따로** 낸다.
2. **표본 편의** — C340 = 17년 × 20 등간격(DOC §1.0). n=340 에서 99.7% 의 95% 신뢰구간은 대략 [98.4%, 100%]. 92% 가 링크 성공률이라면 구간 밖이지만 `exact` 율이라면 안이다. 측정 항목: C340 접수번호 340건을 전량 산출에서 다시 뽑아 **같은 행에서 두 축을 재측정**(표본 vs 전량 차이가 표본 편의인지 규칙 차이인지 분리).
3. **제출자 오기 · 제출일 ≠ 접수일** — DOC §1.7 이 열거(정정 자신의 날짜, `[첨부정정]` 이 감사보고서 제출일). 측정 항목: `date_diff_days` 부호별 히스토그램 × `corr_prefix`.
4. **정정의 정정** — `filed_date` 가 최초 원본이 아니라 직전 정정을 가리키면 등가조인이 빗나간다. 측정 항목: `prior_corr_count ≥ 1` 인 행의 `date_check` 분포 vs `= 0` 인 행.
5. **연도 상한/오기 파싱** — `_calendar_ymd`(`parsers_doc.py:409-420`)가 1990~2099 밖·13월·45일을 `unparsed` 로 떨어뜨린다. 15,225 / 17,600 = 86.5% 해석률은 DOC D6 의 86.4%·C340 86.6% 와 일치하므로 여기서 새는 것은 없다. 측정 항목: `filed_date_status='unparsed'` 2,375건의 `filed_raw` 상위 20 패턴.
6. **후보 0 의 하한 효과** — 1,199건(등가조인 기준) 중 몇 건이 `ledger_floor` 인지. 측정 항목: `none_reason` 분해.

**측정 산출물** (4A 착수 첫 산출, `_meta.gates[].metrics`):
`candidate_status × corr_prefix` 교차표 · `date_check × corr_prefix` 교차표 · `date_diff_days` 분위수(p1·p50·p99) · `link_resolver` 분포 · `none_reason` 분해 · `prior_corr_count` 분포 · C340 340건 재측정 2축.

### 1.7 `fin_std` 계약

```
has_correction_asof(fin_row, asof) := first_correction_dt IS NOT NULL AND first_correction_dt <= asof - lag
```
팩트 컬럼은 **날짜**를 싣고 판정은 뷰가 한다(DEFECT-E01).

| `fin_std` 컬럼 | 정의 |
|---|---|
| `rcept_no` | stage 값 그대로 = **DART 가 준 최신 판본의 접수번호** |
| `version_seq` | `disclosure_version.correction_seq` of `rcept_no` — 0 이면 이 행은 원본 접수에서 온 값 |
| `group_key` | 조인 편의 |
| `orig_rcept_no` · `orig_rcept_dt` | 그 group 의 `correction_seq=0` 접수 |
| `n_corrections` | group 의 `max(correction_seq)` (구 `correction_seq_max`) |
| `first_correction_dt` · `last_correction_dt` | group 의 정정 접수일 min·max |
| `first_material_correction_dt` | `corr_has_fin_item=true` 인 정정만의 min (§2.4) |
| `restated_lag_days` | `rcept_dt − orig_rcept_dt` — restated 값이 원본보다 며칠 늦은 딱지를 달고 있는지 |
| `restated_unknown` | P2 전 전 행 true (현행 유지) |

`v_fin_latest(asof, lag_override := NULL)` 반환에서 `has_correction` 을 **`has_correction_asof`·`n_corrections_asof`·`days_to_next_correction`** 로 교체한다.

### 1.8 결함

#### DEFECT-E01: `fin_std.has_correction` 정적 컬럼이 뷰를 통해 look-ahead 를 흘린다

- **상황**: DESIGN §4-4 대로 `fin_std` 를 빌드하면 `has_correction` 이 팩트 행에 고정 값으로 구워지고, DESIGN §5 `v_fin_latest(asof)` 의 반환 목록에 그대로 들어간다. 정정 타이밍 실측(HANDOFF §4): 정정 접수의 30% 가 원본 후 90일 이후, 10% 가 1년 이후.
- **인풋**:
  1. `v_fin_latest('2018-06-30')` 로 횡단면 패널 생성
  2. 팩터층이 `WHERE NOT has_correction` 로 거르거나 `has_correction` 을 피처로 사용
  3. 그 종목의 정정 공시는 2019-04 에 접수됨 (asof 이후)
- **에러 위치**: `workspace/dongmin/docs/EQUITY_DESIGN.md` §4-4 `fin_std` 컬럼 목록의 `has_correction`·`correction_seq_max`, 그리고 §5 `v_fin_latest` 시그니처 반환 목록. 같은 문서 §1 은 "임계값·판정 결과를 팩트 행에 굽기" 를 금지하고 §3 공통 골격은 "파생 컬럼의 `available_date` = 구성 행 `available_date` 의 max" 를 요구하는데, `has_correction` 의 구성 행(정정 접수)은 원본보다 최대 수년 뒤다.
- **위험성**: look-ahead. asof 시점에 존재하지 않는 공시를 아는 불린이 패널에 실린다. "나중에 정정될 회사" 는 부실 집중이라 이 컬럼으로 거르면 백테스트 성과가 체계적으로 과대해진다 — WORKFLOW §0-3 의 "look-ahead(정정 공시)" 행이 예고한 편향과 같은 종류이고 방향도 같다(과대). **EG2 가 이걸 못 잡는다**: EG2 는 행의 `available_date ≥ 내용일` 만 보고, `has_correction` 은 `fin_std` 행의 `available_date`(= 최신 판본 rcept_dt)보다 뒤의 정보를 담을 수 있다.
- **수정**: §1.7 계약 — 팩트는 `first_correction_dt`·`n_corrections`(날짜), 판정은 뷰(`has_correction_asof`). WORKFLOW §2 EG2 에 한 줄 추가: "불린 파생 컬럼은 구성 행 `available_date` 의 max 를 함께 싣거나, 날짜를 싣고 판정을 뷰로 미룬다."
- **재현 test**: `EG-C ⑦`(":asof 를 과거로 두면 그 뒤 공시·이벤트·판본이 안 보임")에 정정 케이스 1건을 추가 — DESIGN §8 4단계 픽스처의 "정정 2회 그룹" 을 asof = 원본일+1 과 asof = 2차 정정일+1 두 번 호출해 `has_correction_asof` 가 false → true 로 바뀌는지.

#### DEFECT-E02: `stg_doc_correction` 에 `page_found=false` 행이 없어 `no_page` 와 `no_zip` 이 접힌다

- **상황**: P1 풀 빌드 산출 `stg_doc_correction` 17,600행, `page_found` **전부 true** (서버 09-05). DOC §3.3 은 "`page_found=false` 행도 싣는다(`[기재정정]` 인데 첫 장이 없는 문서 — D6 이 센다)" 로 선언했다.
- **인풋**:
  1. equity 4A 가 모집단 L6 에 `stg_doc_correction` 을 `rcept_no` LEFT JOIN
  2. §1.3 ④ 의 `date_check` CASE 를 그대로 평가
  3. LEFT JOIN 이 미스한 행은 `has_zip` 을 안 보면 전부 같은 값으로 떨어짐
- **에러 위치**: `workspace/dongmin/src/stage/parsers_doc.py:423-427`(`correction_page()` 가 `root.find(".//CORRECTION")` 이 None 이면 `None` 반환) + 같은 파일 `:600-602`(`if corr is not None:` 일 때만 `out.correction.append`). 따라서 "ZIP 은 있는데 `CORRECTION` 요소가 없는" 문서는 표에 행 자체가 없다.
- **위험성**: 데이터 손실은 아니지만 **원인 분류가 접힌다**. `no_zip`(원장 결손 2,976건, 커버리지 지표)과 `no_page`(ZIP 은 있으나 문서에 정정 첫 장 없음, 파서·문서 품질 지표)는 성격이 완전히 다르다. 규모는 작다 — DOC §1.7 표의 ZIP 있는 정정 17,603 vs 산출 17,600 = **3건**이지만, C340 은 340 중 1건(0.3%)에서 관측했으므로 전량 기대치는 ~50건이었다. 즉 3 이라는 숫자 자체가 검증되지 않은 채로 남고, `date_check` 어휘가 8종에서 실질 7종으로 줄어 DOC §8.1 계약과 어긋난다.
- **수정 (equity 쪽, stage 수정 불필요)**: `corr_page_found = has_zip AND (stg_doc_correction 행 존재)` 로 정의하고 `date_check` 를 `no_zip` → `no_page` 순으로 평가한다(§1.3 ④ 가 이미 그 순서). 추가로 E-G6d 기록형: `count(*) FILTER (has_zip AND NOT corr_page_found)` 를 baseline 에 등재하고 3 ± 임계로 감시.

#### DEFECT-E03: 모집단 술어 3종이 게이트 임계와 어긋난다

- **상황**: DOC §8.1 이 E-G6a ≥99%·E-G7 ≥99% 를 **20,579 모집단** 위에서 계산한 C340·`rm` 실측으로 정당화했다. 서버 09-05 는 같은 이름의 집합을 24,285 로 잰다. HANDOFF §4 는 그룹 축으로 181,106 / 20,759 를 잰다.
- **인풋**:
  1. equity 4A 가 DESIGN §4-4 술어(제외 4종 + 접두 제거)로 모집단을 만든다 → L4
  2. `stock_code <> ''` 를 넣지 않으므로 DOC 의 20,579 와 다른 수가 나온다
  3. `E-G6a ≥ 0.99` 를 그 분모 위에서 평가
- **에러 위치**: `DOC_DESIGN.md` §8.1 게이트 줄(임계의 근거가 C340 표본) · `EQUITY_DESIGN.md` §4-4 `disclosure_version` EG1("= 정기보고서 3종 접수 행수(제외 종류 제외)", 건수 미기재) · `STAGE_HANDOFF.md` §4(그룹 181,106 / 20,759). 세 곳 어디에도 같은 SQL 이 없다.
- **위험성**: 분모가 커지면(24,285) 링크 실패가 희석돼 게이트가 **참인데 통과**할 수 있고, 반대로 `has_ticker` 로 좁히면 비상장 시기 정정이 통째로 모집단 밖으로 나가 그 원본이 "정정 없음" 으로 읽힌다 — DOC §0 이 명시한 silent PIT 오염(restated 값이 원본 행세). 게이트 임계가 어느 분모 위에 있는지 모르는 상태에서 99% 는 판정이 아니라 숫자다.
- **수정**: §1.5 의 5단(실은 8단) 사다리를 코드 하나로 만들고 각 단 건수를 `baseline.json` 에 `{table:'disclosure_version', metric:'population_L0..L7', sql:…}` 로 등재. **E-G6c 폐기형**. 등재 전에는 E-G6a·E-G7a 를 `skip(no_baseline)` 로 두고 측정치만 남긴다(WORKFLOW §2 첫 빌드 규약과 동일).

---

## 2. `fin_std` 판본 결합

### 2.1 grain 변경 (D-2)

```
현행: (corp_code, period_end, report_code, fs_div)
제안: (corp_code, period_end, report_code, fs_div, vintage_kind, rcept_no)
```

`vintage_kind` 어휘 (DOC §8.2):

| 값 | 원천 | `rcept_no` | `available_date` | 언제 |
|---|---|---|---|---|
| `api_restated` | `stg_fin` | DART 가 준 최신 판본 접수 | 그 접수의 `rcept_dt` (참조표, derived) | **지금** |
| `original` | `stg_fin_asreported` where `vintage_kind='original'` | 그 문서 접수 | 그 접수의 `rcept_dt` — api_restated 보다 **이르다** | P2 후 |
| `corrected` | `stg_fin_asreported` where `vintage_kind='corrected'` | 정정 접수 | 정정 접수일 | P2 후 |

`rcept_no` 가 PK 에 필요한 이유: 한 4키 그룹에 `corrected` 판본이 여럿 있을 수 있다(정정의 정정 — §1.3 ③ 의 `correction_seq` 가 2 이상). `vintage_kind` 만으로는 접히지 않는다.

**`fs_div` 어휘 확장** — `stg_fin_asreported.scope ∈ {C, S, U}`(DOC §1.9 결정표) 를 받으면 `fs_div ∈ {CFS, OFS, **UFS**}`. `U` 는 2010~2012 K-GAAP 개별(케이비증권 사례, DOC §1.9)이고 DOC §7 미결이다. 권고: `UFS` 로 싣고 `v_fin_latest` 의 우선순위를 **CFS → OFS → (UFS 는 선택하지 않음)** 로 두되 행은 보존, `EG7` 기록형으로 건수만. `fs_div_used` 반환에 `UFS` 가 나오면 안 된다(EG3 위반 0).

### 2.2 P2 전 임시 상태와 전환 절차

**임시 상태(지금)**: `vintage_kind` 컬럼을 만들고 **상수 `'api_restated'`** 를 넣는다. `restated_unknown = true` (현행 유지, SPEC §2-18).

컬럼을 지금 넣는 이유는 EG5c 때문이다. DESIGN §2 는 "`inputs` 변경 시 as-of 불변 검사(EG5c)" 를 걸어 두었고 WORKFLOW §2 EG5(c) 는 "결과 차이가 전부 '새 `rcept_dt`/`fetched_date` > asof' 로 설명, 설명 불가 행 = 0" 이다. **P2 투입은 이 술어를 반드시 깨뜨린다** — `original` 행의 `rcept_dt` 는 기존 `api_restated` 행보다 **이르므로**, 고정 asof 에서 값이 바뀌거나 없던 값이 생긴다. 이건 버그가 아니라 의도된 개선이지만, EG5c 가 문장 그대로면 폐기형 게이트가 fail 한다.

**전환 절차 (P2 후, 4C)**:

1. **정책 판본 선언**: `dataset_profile` 에 `(table='fin_std', column_scope='vintage')` 행을 두고 `evidence` 에 `vintage_policy=v1(api_restated only)` / `v2(pit)` 를 적는다. 정책 문자열은 `_meta.json` 에도 남긴다.
2. **EG5c 를 3분기로 분해** (WORKFLOW §2 수정안):
   - **EG5c-1** `inputs` 불변 → 파티션 `content_hash` 전량 동일 (현행 EG5a 와 동일, 유지)
   - **EG5c-2** `inputs` 변경 ∧ 정책 불변 → 결과 차이가 전부 "새 `rcept_dt` > asof" 로 설명, 설명 불가 0 (현행 문장 유지)
   - **EG5c-3 (신설)** 정책 전환(`v1 → v2`) → 차이 행 전부가 **아래 세 유형 중 하나**로 설명되고 그 밖 = 0:
     ```sql
     -- ① 새 판본 등장: vintage_kind IN ('original','corrected') AND available_date <= asof
     -- ② 판본 교체: 같은 4키에서 선택된 rcept_no 가 바뀌었고, 새 행의 available_date <= 옛 행의 available_date
     -- ③ 결측 해소: v1 에서 NULL 이던 4키에 v2 가 값을 준다 (available_date <= asof)
     -- ④ 그 밖 = 0   ← 폐기형
     ```
     ②의 부등호 방향이 핵심이다. 판본 교체가 **더 이른** 공개일로만 일어나야 한다 — 반대 방향이면 look-ahead 를 새로 들여온 것이다.
3. **뷰 이원화**: `v_fin_latest_restated(asof)` 를 v1 동작 그대로 동결하고, `v_fin_latest(asof, vintage := 'pit')` 를 기본으로 승격. `vintage ∈ {'pit', 'restated'}`.
   - `'pit'` = `vintage_kind IN ('original','corrected') ∧ available_date ≤ asof − lag` 중 `available_date` 최대. **`api_restated` 는 배제**(정의상 미래 정보를 담은 판본).
   - `'restated'` = 현행 (api_restated 우선, 없으면 pit).
   - 두 값의 차이가 곧 정정이 팩터에 준 영향이고, 팩터층이 restated-vs-PIT 성과 차이를 직접 잴 수 있다. WORKFLOW §0-3 의 "look-ahead(정정 공시) — 결측 치환" 행이 **측정 가능한 항목**으로 바뀐다.
4. **커버리지 폴백 계약**: `stg_fin_asreported` 는 XBRL 그룹이 있는 문서만 덮는다(DOC §0: 비XBRL 수기 재무표 제외). 서버 09-05: main XML 168,938 중 `n_xbrl_groups > 0` 153,970 = **91.1%**(≥4 는 153,963 — 임계 0/4 차이가 7건뿐이라 임계 선택이 결과를 안 바꾼다). 나머지 8.9% 는 `vintage_kind='api_restated'` 만 남는다 → `pit_available` BOOL 컬럼을 두고 `v_fin_latest(vintage:='pit')` 가 그 행을 **결측으로** 낸다(0 이나 restated 로 대체 금지, DESIGN §1 "결측은 결측").
   - 이 결측률은 연도별로 다르다(서버 09-05: XBRL 그룹 보유율 86.7%(2016) ~ 97.5%(2025)). **비랜덤 결측이므로 6단계 `dataset_profile.coverage_by_mktcap_quintile` 에 `fin_std · vintage=pit` 행을 신설**한다(SPAC·소형사에 몰린다 — DOC §1.9).

### 2.3 D9 를 equity 게이트로 받는 법

DOC D9 = "정정 없는 보고서(원장 `rm` 에 `정` 없음)·2015~·`account_norm` 이 양쪽에 있는 행의 `value_krw = stg_fin.thstrm_amount` 비율, 기대 100%, 기록형→폐기형".

D9 는 **stage 안에서 원문 라벨 축**으로 재고, equity 는 **`fin_map` 통과 후 표준 계정 축**으로 다시 잰다. 두 축이 재는 게 다르다.

| | D9 (stage) | E-G8d (equity, 신설) |
|---|---|---|
| 비교 축 | `account_norm`(원문 정규화) | `fin_map.py` 21+3 표준 계정 |
| 잡는 것 | 파싱·격자 전개·단위 스케일 오류 | 위 + **계정 매핑 오류** |
| 실패 처리 | 격리 없음, 비율만 | 격리 없음, `vintage_mismatch` 플래그 + 건수 |

**E-G8d** (EG8 교차 소스 계열, 첫 빌드 `skip(no_baseline)`):

```sql
WITH pair AS (
  SELECT a.corp_code, a.period_end, a.report_code, a.fs_div, a.account, 
         a.value AS v_orig, r.value AS v_api
  FROM   fin_std a                                   -- vintage_kind='original'
  JOIN   fin_std r USING (corp_code, period_end, report_code, fs_div, account)
  JOIN   disclosure_version dv ON dv.rcept_no = a.rcept_no
  WHERE  a.vintage_kind = 'original' AND r.vintage_kind = 'api_restated'
    AND  dv.correction_seq = 0
    AND  NOT EXISTS (SELECT 1 FROM disclosure_version x
                     WHERE x.group_key = dv.group_key AND x.correction_seq >= 1)
    AND  a.value IS NOT NULL AND r.value IS NOT NULL)
SELECT count(*) AS n, count(*) FILTER (WHERE v_orig = v_api) AS n_eq FROM pair;
```

- 분모 조건 "정정 없는 그룹" 을 `rm` 이 아니라 **링크(`disclosure_version`)** 로 잡는다. `rm` 은 현재값이라 게이트 술어에 쓰면 PIT 규약을 어긴다(§1.4). `rm` 은 E-G7 검산축 전용.
- 기대 100%. 미달분은 격리하지 않는다 — `fin_map` 의 `agg='pick'` 이 모호로 떨어진 칸과 진짜 불일치가 섞이므로, `vintage_mismatch_reason ∈ {value_diff, map_ambiguous, unit_scale}` 로 분해해 기록.
- 폐기형 승격 조건: `value_diff` 비율이 baseline 이하로 안정. `2010~2014` 는 `stg_fin` 이 없어 `skip(no_stg_fin)`(DOC D9 와 동일).

**추가로 받아야 할 것**: DOC D13(교차 제출 삼각검증)은 equity 가 다시 만들 필요가 없다 — stage 가 `account_norm` 축에서 이미 잰다. 다만 **수치를 `data/stage/baseline.json` 에 등재해 달라**(§6 요청 7). equity 는 HANDOFF §1 계약상 stage baseline 을 읽을 수 있다.

### 2.4 `stg_doc_correction.items` 의 정정 전 값 — 쓰는가

**기본 권고: 값은 `fin_std` 에 넣지 않는다. 플래그로만 쓴다.**

한계 셋(전부 실측·코드 근거):

1. **기간·범위·단위가 표 안에 없다.** `items` 는 정정사항 표의 헤더 라벨을 키로 한 행 목록이다(`parsers_doc.py:439-450`, 헤더에 `항목` 셀이 있는 첫 `TABLE`). 헤더는 `항목 · [정정요구ㆍ명령관련 여부(G3)] · [정정사유] · 정정 전 · 정정 후`(DOC §1.7). "자산총계" 가 당기인지 전기인지, 연결인지 별도인지, 원인지 백만원인지가 어디에도 없다. `fin_std` 의 grain 4키 중 **`period_end`·`fs_div` 를 채울 수 없다.**
2. **커버리지가 비랜덤이다.** 재무 항목 포함 행 2,238 / 정정 17,600 = **12.7%**(서버 09-05). 나머지 87.3% 는 오탈자·첨부 누락·비재무 정정이다. 부분 적재하면 "정정 폭" 분포가 재무 정정 쪽으로 편향된다.
3. **키 어휘가 세대별로 다르다.** 헤더 키는 `text_of(c).replace(" ","")` 로 만들어지고(`parsers_doc.py:444`) 사전이 없다. `정정전`/`정정후`/`변경전` 같은 변형을 equity 가 추측해야 한다(§6 요청 3).

**대신 쓰는 곳 두 군데** (여기가 진짜 값어치다):

**(가) `corr_has_fin_item` 플래그 — `has_material_correction` 축.**
`fin_std.has_correction` 은 지금 "이 그룹에 정정이 있다" 인데 정정 대부분은 재무를 안 건드린다. 재무 항목을 건드린 정정만 골라내면 look-ahead 위험이 실제로 어디에 있는지 보인다.

```sql
corr_has_fin_item :=
  regexp_matches(lower(items), '자산총계|당기순이익|매출액|영업이익|부채총계|자본총계')
```
(항목 어휘는 `baseline.json` 이 아니라 `src/equity/vocab/corr_fin_items.json` — 코드 상수 금지, DESIGN §2)

- `fin_std.first_material_correction_dt` 로 승격(§1.7).
- **WORKFLOW §3-4 통과 조건의 "PIT 결측률 교차표(has_correction × 접수지연 분위수)" 를 3축으로 확장**: `has_material_correction × 접수지연 분위수 × 시총 분위수`. 지금 교차표는 정정 12.7% 의 신호를 87.3% 의 잡음에 묻는다.
- basis: `derived` (구성 행 = 정정 접수 1건, available = 그 정정의 `rcept_dt`).

**(나) P2 이후 독립 검산축 E-G8e (기록형).**
`original` 값과 `corrected` 값이 다른 표준 계정의 집합이, `items` 가 이름을 댄 재무 항목 집합을 **포함하는가**(값 일치는 요구하지 않는다 — 단위·기간이 없으므로).

```sql
-- 방향만: items 가 '자산총계' 를 댔으면 total_asset 이 original ≠ corrected 여야 한다
SELECT count(*) AS n_claimed, count(*) FILTER (WHERE changed) AS n_confirmed
FROM  ( … items 의 항목 → fin_map 표준 계정 매핑 × 판본 diff … );
```
as-reported 파서가 정정 폭을 실제로 잡았는지를 **문서 자신이 쓴 글**로 재는 유일한 축이다. D9·D13 은 둘 다 값 축이라 이 방향을 못 본다.

**만약 값을 쓴다면** (권고하지 않음, 27번째 테이블):

| 항목 | 명세 |
|---|---|
| 테이블 | `fin_corr_hint` — grain (`rcept_no`, `item_ord`), receipt_axis |
| 컬럼 | `item_raw`·`before_raw`·`after_raw`·`before_num`·`after_num`(콤마 제거·괄호 음수만; **스케일 적용 금지**)·`account_std` NULL·`unit_basis='unknown'`·`period_basis='unknown'` |
| available | 정정 `rcept_dt` (measured) |
| basis | `unknown` — `measured`/`derived` 어느 쪽도 아니다. 단위·기간을 모르는 숫자는 사실이 아니다 |
| 금지 | `fin_std` 와 조인 금지 · `v_fin_latest` 노출 금지 · 팩터 재료 금지 |
| 용도 | (나) 검산축의 입력, 그리고 손 검수 |

---

## 3. `period_from/to` · `doc_acode` 를 `period_end`·`report_code` 정본 축으로 (D-3)

### 3.1 왜 바꾸는가

DESIGN §4-4 현행: `bsns_year` 는 회계연도 종료 연도(P2 실측) → 후보 2개(`bsns_year`, `bsns_year+1`) × `corp.fiscal_month` 말일 중 `0 ≤ rcept_dt − period_end ≤ 200` 인 쪽. 둘 다 아니면 `period_end_basis='unknown'` + EG7 격리.

이 규칙의 알려진 실패 셋 — 전부 DESIGN 자신이 적어 놓았다:
- P2 실측: 01·04·05월 결산(n = 6·7·10)에서 `rcept_dt − period_end` 중앙값 192~304일 → 200일 창을 벗어나 격리로 떨어진다.
- §4-1: `corp.fiscal_month` 는 `stg_company` 의 **현재값**이고 `fiscal_month_basis='current_snapshot'`. 결산월을 바꾼 기업의 과거 `period_end` 는 원리적으로 틀린다.
- §11: "결산월 변경 — 이력 원천 없음 → `period_end` 후보 규칙 + 격리로만 방어."

문서층이 이걸 원문으로 준다. `stg_doc_meta.period_from`/`period_to` 는 `TU[@AUNIT='PERIODFROM'|'PERIODTO']` 의 `AUNITVALUE`(`parsers_doc.py:272-276`)이고, DOC §1.6 은 "S26 의 XML 세대 G1·G2·G3 전부에 있고 비12월 결산(케이비증권 20090401~20100331)도 정확 → 보고기간 확정 축" 으로 못 박았다.

**커버리지(서버 09-05, 산술로 확인)**:
```
main 멤버                170,762
  − HTML(주요사항보고서)   1,824   (= doc_acode NULL 1,824 과 동수)
  = main XML             168,938
period_from 채움         168,938   → XML main 전건 100%
parse_mode ≠ ok           1,889   = HTML 1,824 + lenient 65 (DOC P1 실측과 일치)
```
즉 **파싱된 XML main 문서 전건에 보고기간이 있다.** 존재율은 확정이다.

### 3.2 규칙

```sql
-- fin_std 빌드 시
LEFT JOIN stg_doc_meta m
  ON m.rcept_no = f.rcept_no AND m.member_role = 'main' AND m.parse_mode IN ('ok','lenient')

period_end      = coalesce(m.period_to, <DESIGN §4-4 관례 규칙>)
period_end_basis = CASE WHEN m.period_to IS NOT NULL THEN 'doc_period_to'
                        WHEN <관례 규칙 성립>        THEN 'bsns_year_convention'
                        ELSE 'unknown' END           -- EG7 격리 (현행 유지)

report_code = CASE m.doc_acode
   WHEN '11011' THEN '11011'                                   -- 사업
   WHEN '11012' THEN '11012'                                   -- 반기
   WHEN '11013' THEN CASE WHEN datediff('month', m.period_from, m.period_to) <= 4
                          THEN '11013' ELSE '11014' END        -- 1Q / 3Q
   ELSE f.reprt_code END                                        -- 폴백 = stage 요청축
report_code_basis = CASE WHEN m.doc_acode IS NOT NULL THEN 'doc_acode' ELSE 'api_request' END
```

**`doc_acode` 는 1Q 와 3Q 를 구분하지 못한다.** 서버 09-05: main 의 `doc_acode` 는 `11013 분기 77,516`·`11011 사업 51,063`·`11012 반기 40,332`·`NULL 1,824` (합 170,735; 나머지 27건은 그 밖의 코드 — S26 2020-E 처럼 `[첨부정정]` 이 `00761` 을 다는 경우, DOC §1.1). DART API 의 `reprt_code` 는 11013(1분기)·11014(3분기)로 나뉘므로 `doc_acode` 단독으로는 부족하고 `period_from→period_to` 개월 수가 결정한다.

**`bsns_year` 는 키에서 뺀다.** `bsns_year = year(period_to)` 는 사업보고서에만 성립하고, 비12월 결산의 분·반기에서는 회계연도 라벨과 달력 연도가 갈린다. `fin_std` grain 은 이미 `period_end` 축이므로(DESIGN §4-4) `bsns_year` 는 **캐리 라벨**로만 싣고 조인·필터에 쓰지 않는다. WORKFLOW §1 금지 목록의 "`obs_month`·`bsns_year` 를 날짜 축으로 사용" 과 일관.

**조인 키가 4키에서 `rcept_no` 로 내려온다.** `stg_fin.rcept_no` 는 `required=True`(`rules_dart.py:64`)이고 `stg_doc_meta`·`disclosure_version` 둘 다 `rcept_no` 가 키다. DESIGN §4-4 의 "`fin_std ⋈ disclosure_version` 4키 등가조인" 은 **검산(E-G3j)** 으로 강등한다 — 문자열 라벨 파싱 두 번을 조인 키로 쓰는 것보다 stage 자연키 하나가 낫다.

### 3.3 게이트

| 게이트 | 유형 | 술어 | 임계 |
|---|---|---|---|
| **E-G1f** | 폐기형 | `fin_std` 행 중 `period_end_basis='doc_period_to'` 비율 | ≥ baseline (첫 빌드 측정). 상한은 §3.4 검증 4의 `stg_fin.rcept_no ⊆ stg_doc_meta(main)` 비율 |
| **E-G3f** | 기록형 | `report_code='11011'` 행에서 `month(period_end) = corp.fiscal_month` 비율 | 기록. **불일치는 격리하지 않는다** — 결산월 변경 후보이고, 문서가 옳고 `stg_company` 현재값이 틀린 경우다. 건수와 corp 목록을 `_meta` 에 |
| **E-G3g** | 폐기형 | `kind`(report_nm) ↔ `doc_acode` 일치율 (§1.4) | = 100% 기대 |
| **E-G3h** | 폐기형 | `period_label` = `strftime(period_to,'%Y.%m')` 일치율 | ≥ baseline |
| **E-G3j** | 기록형 | `rcept_no` 조인 결과 vs 4키 조인 결과의 집합 차 | 건수 기록. 0 이 아니면 라벨 파싱에 결함 |
| **E-G7c** | 격리형(현행) | `rcept_dt − period_end ∉ [0, baseline_p99]` 격리 | 현행 유지. **전환 전후 격리 건수를 함께 기록** — `doc_period_to` 도입의 이득 크기가 이 차이다 |

### 3.4 픽스처 · 미확인 항목

**픽스처** — stage D4 가 이미 `src/stage/fixtures/stg_doc_meta.json`(234건)에 고정한 S26 접수번호를 재사용한다(중복 노동 0, 두 층이 같은 문서를 본다).

| 접수번호 | 회사 | 검증 |
|---|---|---|
| `20100629000280` | 케이비증권 | **3월 결산 · G1 · `20090401~20100331`** → `period_end = 2010-03-31`, `report_code=11011`. DESIGN §4-4 관례 규칙이 `bsns_year` 라벨만으로 못 푸는 케이스 |
| `20240313000011` | 청보 | G3 사업 · `11011` |
| `20240510000724` | 마이크로컨텍솔 | G3 분기 · `doc_acode=11013` → `report_code` 가 11013 인지 11014 인지 개월 수로 판정 |
| `20240809000460` | 피엔티엠에스 | G3 반기 · `11012` |
| `20240314001248` | 메리츠금융지주 | **정정 · G3** — 정정 문서에도 `period_from/to` 가 있는지 |
| `20200330004604` | 한국테크놀로지 | **`[첨부정정]` · main 부재 · `doc_acode=00761`** — 폴백 경로(`report_code_basis='api_request'`) |
| `20220311000948` | 포스코DX | G2(헤더 래퍼 없음) `20210101~20211231` — 세대 무관 성립 |

**미확인 + 검증 SQL** (4A 착수 전 서버에서 돌린다, 콜 0):

1. **분기보고서의 `period_from` 이 회계연도 시작인가 분기 시작인가** — `report_code` 11013/11014 판정이 여기 달렸다.
   ```sql
   SELECT doc_acode, datediff('day', period_from, period_to) AS span_days, count(*)
   FROM stg_doc_meta WHERE member_role='main' AND parse_mode IN ('ok','lenient')
   GROUP BY 1,2 ORDER BY 1, 3 DESC;
   ```
   기대: 11011 ≈ 364 단봉 · 11012 ≈ 181 단봉 · 11013 **쌍봉 89 / 272**(회계연도 누계) 또는 **단봉 89**(분기만). 쌍봉이면 위 규칙 그대로, 단봉이면 `report_code` 를 `month(period_to) − 회계연도 시작월` 로 판정한다.
2. **`period_to` 가 표지 값인가** — `header_fields()`(`parsers_doc.py:272-276`)는 `root.iter("TU")` 로 **문서 전체 첫 매치**를 집는다. 표지 앞에 다른 `PERIODFROM` 이 오는 문서가 있으면 조용히 틀린다.
   ```sql
   SELECT count(*) FROM stg_doc_meta
   WHERE member_role='main' AND parse_mode IN ('ok','lenient')
     AND (period_to IS NULL
          OR strftime(period_to, '%Y.%m')
             <> regexp_extract(doc_name, '\((\d{4}\.\d{1,2})\)', 1));
   ```
   `doc_name`(= `DOCUMENT-NAME` 텍스트, 문서 자신의 이름)과의 자기대조라 `stg_disclosure` 없이 stage 안에서 돈다. 0 에 가까우면 표지 값이 맞다. 근본 해결은 §6 요청 5(`n_period_tu`).
3. **`stg_fin.rcept_no` 의 문서층 커버리지** — E-G1f 의 상한.
   ```sql
   SELECT count(DISTINCT f.rcept_no) AS n_fin_rcept,
          count(DISTINCT f.rcept_no) FILTER (WHERE m.rcept_no IS NOT NULL) AS n_covered
   FROM (SELECT DISTINCT rcept_no FROM stg_fin) f
   LEFT JOIN (SELECT DISTINCT rcept_no FROM stg_doc_meta
              WHERE member_role='main' AND parse_mode IN ('ok','lenient')) m USING (rcept_no);
   ```
   미커버분 = ZIP 없는 접수(원장 2,980건, DOC §1.7) + `stg_fin` 이 정기보고서 밖 접수를 가리키는 경우. 후자가 있으면 그 자체가 발견이다.
4. **관례 규칙 대비 이득 크기** — 비12월 105사에서 두 규칙의 결과가 갈리는 건수.
   ```sql
   SELECT c.fiscal_month, count(*) AS n,
          count(*) FILTER (WHERE m.period_to <> conv.period_end) AS n_differ,
          count(*) FILTER (WHERE conv.period_end IS NULL)        AS n_conv_fail
   FROM   … stg_fin ⋈ stg_doc_meta ⋈ corp ⋈ <관례 규칙> …
   WHERE  c.fiscal_month <> 12 GROUP BY 1 ORDER BY 1;
   ```
   DESIGN P2 가 01·04·05월(n = 6·7·10)을 격리 대상으로 지목했으므로 `n_conv_fail` 이 거기 몰려야 한다.

---

## 4. `stg_doc_section` — **equity 팩트 테이블에 쓰지 않는다** (D-5)

7,929,624행. 제안된 세 용도를 각각 기각한다.

### 4.1 `fin` 섹션 존재 = 재무제표 첨부 여부 플래그 → **기각**

- `doc_vocab/section_kind.json`(doc-p1)은 `D-0-3-2-0`·`D-0-3-4-0` → **`fin`**, `D-0-11-0-0` → **`fin_legacy`** 로 **다른 값**을 찍는다.
- DOC §1.2 전환표: 재무제표 위치는 2015-03(FV 2.5)에 `XI. 재무제표 등`(`D-0-11-0-0`) → `III-2/III-4`(`D-0-3-2-0/4-0`) 로 바뀌었다.
- 서버 09-05 `section_kind` 상위 15에 `fin_legacy` 가 없고, `fin` 252,238 = 제목 `4. 재무제표` 126,124 + `2. 연결재무제표` 126,112 로 **2015-03 이후 서식만**이다(문서당 정확히 2행 꼴). 상위 15 합 7,486,601 vs 전체 7,929,624 → 나머지 443,023 행에 `fin_legacy`·`affiliate`·`appendix` 등이 들어 있다.
- 따라서 `section_kind='fin'` 로 플래그를 만들면 **2010~2015-02 접수 전량이 "재무제표 없음"** 이 된다. 비랜덤 결측이고, 하필 XBRL 커버리지가 가장 낮은 초기 구간(서버 09-05: 2016 86.7% 최저)과 겹쳐 이중으로 편향된다.
- `IN ('fin','fin_legacy')` 로 고치더라도 **`stg_doc_meta.n_xbrl_groups` 가 이미 같은 일을 조인 없이 한다**(문서 1행, 서버 09-05: `>0` 153,970 · `≥4` 153,963 — 7건 차이라 임계 선택이 결과를 안 바꾼다). 790만 행 테이블을 조인해 얻을 것이 없다.
- **결론**: 재무제표 존재 플래그 = `stg_doc_meta.n_xbrl_groups >= 4` (DOC D8 과 같은 축). `stg_doc_section` 미사용.

### 4.2 `has_correction_page` 검산 → **기각**

- `toc_rows()` 는 `_SECTION_TAGS = ("SECTION-1","SECTION-2","SECTION-3")` 와 `TITLE` 만 훑는다(`parsers_doc.py:291`). 정정 첫 장은 `BODY/INSERTION/LIBRARY/CORRECTION`(G1·G2) 또는 `BODY/LIBRARY/CORRECTION`(G3)의 **별도 요소**라(DOC §1.7) `stg_doc_section` 에 행이 생기지 않는다. 검산축으로 쓸 자료 자체가 없다.
- 검산축은 이미 셋이다: `stg_doc_meta.has_correction_page`(main 17,176) · `stg_doc_correction` 행 존재(17,600) · `stg_disclosure` 접두(`[기재정정]`/`[첨부정정]`). 정합도 이미 확인된다 — 17,600 − 17,176 = 424 ≈ `[첨부정정]` ZIP 418(DOC §1.7 표), 171,179 − 170,762 = 417(main 없는 ZIP) 과 정합.
- **결론**: 미사용.

### 4.3 4-B 서식표 P3 좌표 → **P1 에서는 미사용. P3 에서 한 용도**

- `stg_doc_form_cell`(DOC §3.6) 키는 (`rcept_no`, `member`, `form_ord`, `row_ord`, `col_ord`)이고 컬럼에 **`section_code` 도 `elem_start` 도 없다**. `stg_doc_section.elem_start/elem_end` 구간으로 좌표를 되찾으려 해도 form_cell 쪽에 좌표가 없어 조인이 불가능하다. → §6 요청 2.
- P3 에서 쓸 곳은 하나뿐: `stg_doc_table.section_code` 가 `section_code_basis='derived'`(부모 절 첫 자식 코드에서 역산, DOC §3.7)인 행의 검산과 §8.3 군집 키. 이건 **자유표 값 층(P4)** 의 입력이지 equity 팩트가 아니다.

### 4.4 유일하게 남는 용도 — 6단계 `dataset_profile.evidence` (팩트 아님)

DESIGN §4-7 은 4-B 6테이블의 `coverage_from` 을 "FY2013~2015 · 지분 2024-08" 로만 적고 근거가 **API 하한**이다. `stg_doc_section` 은 절이 언제 생겼는지를 문서 축 전수로 준다(DOC §1.13 양식 커버리지 표의 전량판). 예: 최대주주·임원·주식총수 절의 `section_kind` 별 최초 등장 접수연도.

```sql
SELECT section_kind, min(substr(rcept_no,1,4)) AS first_year, count(DISTINCT rcept_no) AS n_docs
FROM stg_doc_section WHERE section_kind IS NOT NULL GROUP BY 1 ORDER BY 2;
```

- **팩트 컬럼으로 승격하지 않는다.** `dataset_profile.evidence` 텍스트와 `coverage_basis='doc_section_census'` 값으로만 쓴다. equity 팩트 테이블 26개 중 어느 것도 `stg_doc_section` 을 `source_stage_tables` 에 넣지 않는다.
- EG0(선언 컬럼 실재)은 `dataset_profile` 이 참조하는 stage 테이블도 검사하므로, `dataset_profile.source_stage_tables` 에는 넣되 `fin_std` 등 팩트 테이블에는 넣지 않는다는 구분을 §4-7 표에 한 줄로 명시한다.

**요약**: `stg_doc_section` 은 equity 팩트 26테이블 어디에도 들어가지 않는다. 이유는 (a) `fin`/`fin_legacy` 분열로 2010~2014 를 계통적으로 놓치고 `n_xbrl_groups` 가 더 낫다, (b) 정정 첫 장은 섹션이 아니라 검산 자료가 없다, (c) P3 좌표는 form_cell 에 좌표가 없어 조인 자체가 불가능하다.

---

## 5. 단계 순서 변경안 (D-6)

### 5.1 P2 를 기다려야 하는 것 / 지금 할 수 있는 것

| 작업 | 의존 | 산출 |
|---|---|---|
| **4A-1** `disclosure_version` 전량 (링크 포함) | **P1 만** — `stg_disclosure` · `stg_doc_correction` · `stg_doc_index` · `stg_doc_meta` | E-G6a/b/c · E-G7a/b · E-G3g/h/i |
| **4A-2** `fin_std.period_end`·`report_code` 정본화 | **P1 만** — `stg_doc_meta.period_to`·`doc_acode` | E-G1f · E-G3f/j · E-G7c 전후 비교 |
| **4A-3** `fin_std` 판본 딱지 (`first_correction_dt`·`orig_rcept_dt`·`restated_lag_days`·`first_material_correction_dt`) | **P1 만** | PIT 결측률 3축 교차표 |
| **4A-4** `vintage_kind` 컬럼 상수 `api_restated` | **P1 만** | grain 동결 (EG5c 대비) |
| **4B** DART 보조 6테이블 (API 하한 FY2013~2015) | 1단계만 (현행) | 현행 |
| **4C** `vintage_kind ∈ {original, corrected}` 적재 · `v_fin_latest(vintage:='pit')` · E-G8d · `restated_unknown=false` | **stage P2** | 재무 원본 판본 |
| **4B′** 4-B 6테이블의 2011~2014 확장 (`src` 를 PK 에 추가) | **stage P3** | E01~E04·I03~I05·V06 시작연도 2015 → 2011 |
| **P4 자유표 값 층** | stage P4 | equity 범위 밖 (DOC §8.3) |

**4B′ 의 크기**를 강조해 둔다. DOC §1.13 실측: 2011-03 서식 개정(FV 1.3)에 `TOT_STK`(주식총수)·`BSH_SPCL`·`SH5_PRE_STT`·`SH4_PRE_STT`(최대주주·5%주주)·`EMPLOYEE`(직원)·`OWN_SHR`(자기주식) 등 **13종이 일괄 도입**됐고 2011 커버리지가 0.93~0.96 이다. 반면 API 는 `stg_shares`·`stg_hyslr`·`stg_tesstk` 가 FY2015 하한(DESIGN §4-7). **2011~2014 4년치가 통째로 새 재료**다. 배당은 2010부터 1.00 이라 `dividend_event` 는 2010 까지 내려간다.

### 5.2 4A 는 1단계에서 풀린다

`period_end` 를 `stg_doc_meta.period_to` 로 뽑으면 `fin_std` 가 `corp.fiscal_month` 에 의존하지 않는다(검산으로만 씀). `disclosure_version` 은 원래 `corp_code` 축이라 `corp_ticker` 를 안 쓴다. 따라서 **4A 는 1단계 산출을 하나도 읽지 않는다** — WORKFLOW §4 의 "1단계가 유일한 직렬 병목" 에서 벗어난다.

단서 하나: EG0(입력 고정)은 `stg_doc_*` 4테이블도 `data/equity/_pinned/` 에 하드링크로 고정할 것을 요구한다. stage doc 테이블도 `keep=3` GC 대상이다(WORKFLOW §1). 4A 착수 첫 작업 = 핀 고정.

### 5.3 WORKFLOW §4 그래프 수정안

```
0 설계 ─┬─ 1 마스터·유니버스·캘린더·지수 ─┬─ 2 가격·이벤트·계수 ──────────┐
        │                                  ├─ 3 격자·결측 3분류 ──────────┤
        │                                  ├─ 4B 지분·감사·배당(API 하한)─┤
        │                                  └─ 5 컨센서스 ─────────────────┤
        │                                                                 │
        └─ 4A 재무 PIT 판본축·기간축 ───────────────────────────────────┤
             [stage P1 = 완료. 1단계 비의존]                              │
                                                                          │
  stage P2 ──► 4C fin_std 판본 결합(original/corrected) ──────────────────┤
  stage P3 ──► 4B′ 4-B 2011~2014 확장(src 를 PK 에) ──────────────────────┤
                                                                          ▼
                                                            6 profile ─ 7 마무리·인계
```

- **직렬 병목이 둘로 준다**: 1단계(2·3·4B·5) 와 4A(4C 의 선행). 둘이 병렬.
- **4C·4B′ 는 크리티컬 패스가 아니다** — 7단계 전에 합류하면 된다. 단 7단계 통과 조건에 한 줄 추가: *"4B′ 미착수면 `EQUITY_HANDOFF.md` 의 팩터 ID × 시작일 표에 E01~E04·I03~I05·V06 의 하한을 2015 로 명시하고, P3 후 내려갈 수 있음을 고지한다."*
- 서버 실측은 여전히 직렬(`flock`, RAM 15GB) — 병렬은 픽스처·TDD·설계뿐(WORKFLOW §4 현행 단서 유지).
- `stg_fin_asreported` long 2.2억 행(DOC §3.5·결정 ⑪)은 4C 에서 `memory_limit` 6GB 안에 들어와야 한다. **연도 파티션 단위 처리가 안전하다** — 한 문서의 재무표는 한 접수번호이므로 4키 그룹핑이 `receipt_axis` 샤드 안에서 닫힌다(교차 연도 조인이 없다). WORKFLOW §1 규모 절에 한 줄.

### 5.4 WORKFLOW §3-4 표 수정 요약

| 항목 | 변경 |
|---|---|
| 3-4 → **3-4A** | 제목 "재무 PIT — 판본축·기간축 (**1단계 비의존**)" |
| 입력 | + `stg_doc_meta`·`stg_doc_correction`·`stg_doc_index` (`_pinned/` 고정) |
| 산출 | `fin_std`(+`vintage_kind`·`period_end_basis`·`report_code_basis`·`first_correction_dt`·`orig_rcept_dt`) · `disclosure_version`(grain rcept_no, +11컬럼) |
| 작업 | `link_basis='grouped'` 문구 삭제 → `candidate_status`·`link_resolver`. `period_end` 는 `doc_period_to` 1순위 |
| 판단 | "오판율 표본 N(baseline)은 정정 ZIP 첫 장 정정신고 표와 대조" → **전량 대조**(표본 불필요, 17,600행 전건이 산출에 있다) |
| 픽스처 | + §3.4 표 7건(S26 재사용) |
| 통과 조건 | + E-G6a/b/c · E-G7a/b · E-G3f/g/h/i/j · E-G1f. **PIT 결측률 교차표를 3축**(`has_material_correction × 접수지연 분위수 × 시총 분위수`)으로 |
| **3-4C** (신설) | stage P2 의존. `vintage_kind` 적재 · EG5c-3 · E-G8d · `v_fin_latest(vintage:=)` |
| **3-4B′** (신설) | stage P3 의존. 4-B 6테이블 `src ∈ {api, doc}` PK 확장 · 2011~2014 |

---

## 6. stage 쪽 요청 목록

### 6.1 P2/P3 명세에 없는데 equity 가 필요한 것

| # | 요청 | 대상 | 사유 · 없으면 생기는 일 |
|---|---|---|---|
| 1 | **`stg_fin_asreported.scope_basis`** ∈ {`suffix_c`,`suffix_s`,`section_or_sibling`,`none`} | P2 (DOC §3.5) | `scope` 결정표(DOC §1.9) 네 규칙 중 무엇이 발화했는지가 없다. ③(섹션 코드·형제 추론)으로 잡힌 `C` 와 ①(접미 `_C`)로 잡힌 `C` 는 신뢰도가 다르고, `U` 는 DOC §7 미결이다. equity 가 `fs_div` 를 정할 때 근거 없이 추측하게 된다 |
| 2 | **`stg_doc_form_cell.section_code`** 또는 **`elem_start`** | P3 (DOC §3.6) | 현 컬럼 목록에 절 좌표가 없다(§4.3). 미등록 `ACLASS`(D12 대상, 111종 중 미사전화분)는 절 코드가 유일한 단서이고, 4B′ 가 "이 서식표가 VII. 주주 절인지 III. 재무 절인지" 를 문서 순서로만 추정해야 한다 |
| 3 | **`doc_vocab/corr_item_hdr.json`** — 정정사항 표 헤더 정규화 사전 | P1 후속 (또는 equity 가 만들어 기증) | `items` 키는 `text_of(c).replace(" ","")`(`parsers_doc.py:444`)로 만들어지고 세대별로 열이 다르다(G3 는 `정정요구ㆍ명령관련 여부` 추가, DOC §1.7). equity 가 `정정전`/`정정후` 변형을 추측하면 §2.4 (나) 검산축이 조용히 0건이 된다 |
| 4 | **`stg_doc_meta.n_period_tu`** — 문서 안 `PERIODFROM` TU 개수 | P1 후속(저비용) | `header_fields()` 가 문서 전체 첫 매치를 집는다(`parsers_doc.py:272-276`). 값이 2 이상인 문서 수를 세야 §3 정본화의 위험 규모가 나온다. 지금은 존재율 100% 만 알고 정확성을 못 잰다 |
| 5 | **`stg_fin_asreported.period_slot_basis`** ∈ {`제N기`, `col_order`} | P2 (DOC §3.5) | `period_slot` 은 열 라벨의 `제 N 기` 파싱에 의존하고 실패 시 열 순서로 폴백한다. 폴백이 발화한 행을 모르면 equity 가 **값을 옆 기간에 붙인다** — D11(격자)은 폭만 보고 기간은 안 본다. DOC §1.12 가 지목한 "P2 가 격자 전개 없이 열 순서로 기간을 붙이면 값이 옆 기간으로 밀린다" 의 잔여 위험 |
| 6 | **`{XBRL}IS1/IS2/IS3` 의미 확정 → `aclass_raw` 보존 확인** | P2 (DOC §7 미결) | `fin_map.py` 는 `sj=["IS","CIS"]` 순서로 손익을 찾는다. stage 가 셋을 `stmt='IS'` 로 접으면 손익과 포괄손익이 한 `stmt` 에 섞여 `agg='pick'` 이 모호로 떨어진다(`net_income` 이 통째로 NULL). DOC §3.5 는 `aclass_raw` 보존을 이미 선언했으므로 **확인만** 필요 |
| 7 | **D9·D13 수치를 `data/stage/baseline.json` 에 등재** | P2 | equity E-G8d 가 stage 축(원문 라벨)과 equity 축(표준 계정)을 분리해 보려면 stage 값이 상수로 있어야 한다. HANDOFF §1 상 equity 는 stage baseline 을 읽을 수 있다 |
| 8 | `page_found=false` 행 — **요청하지 않는다** | — | equity 가 `stg_doc_index.zip_ok` 로 `no_zip`/`no_page` 를 가른다(DEFECT-E02 수정). stage 재빌드 비용 대비 효과가 없다. 다만 계약을 문서에 남긴다 |

### 6.2 DOC §7 미결 중 equity 에 영향 있는 것

| DOC 미결 | equity 영향 | 4A/4C 착수 전 필요? |
|---|---|---|
| **"HANDOFF §4 의 정정 있음 그룹 20,759 vs §1.7 접수 20,579" (E-G7 분모)** | **최상**. §1.5 사다리 · DEFECT-E03. 임계가 어느 분모 위인지 모르면 E-G6a·E-G7a 가 판정이 아니다 | **예 — 4A 착수 전** |
| `[첨부정정]` 정정대상이 감사보고서일 때 `kind` 매칭 | 중. `target_raw` 상위에 `감사보고서` 110건(서버 09-05). equity 규칙 = **`kind` 는 정정 문서 자신의 `report_nm`(접두 제거 후)에서만 뽑고 `target_raw` 는 쓰지 않는다**(자유 텍스트: `사업보고서` 5,301 · NULL 2,377 · `2013년 사업보고서` 112 · `사업보고서(2021.12)` 68 …, 긴 꼬리 7,106). `date_check` 만 감사보고서 제출일 때문에 mismatch → E-G6b 분모에서 `[첨부정정]` 제외(§1.4) | 예 (규칙으로 이미 해소) |
| `IS2/IS3` 의미 | 중. §6.1 요청 6 | 4C 전 |
| 2010~2012 K-GAAP `SA` 표 · `scope='U'` | 중. `fs_div='UFS'` 어휘 신설(§2.1). 2010~2012 는 `stg_fin` 이 없어(FY2015 하한) **문서층만이 유일 원천**이다 | 4C 전 |
| `stg_fin_asreported` long 2.2억 행 벤치 | 중. 4C 의 메모리·시간. 연도 파티션 처리로 닫힌다(§5.3) | 4C 전 |
| 첨부 `TOT_ASSETS`·`TOT_DEBTS`·`TOT_SALES` 단위 (DOC §1.10) | 저. `stg_doc_meta.summary` JSON 은 4-B `audit_opinion` 의 **공짜 검산축**(`AUDIT_CIK`·`SUPV_OPIN`·`GMSH_DATE` ↔ `stg_audit`). 단위 미상이면 금액 3종만 못 쓴다 | 아니오 (검산축) |
| 킥오프 "정정 ZIP 18,013/21,138" 대응 | 저. §1.5 사다리가 흡수 | 아니오 |
| 규칙 5c 정규식 성능 · `t_*_ms` payload_exclude | **무관**. equity 는 `stg_doc_parse_log` 를 읽지 않는다. EG0 은 입력 `_meta.gates` fail 0 만 본다 | 아니오 |
| lenient 65건의 D10 을 기록형으로 낮출지 | 저. equity 는 `parse_mode IN ('ok','lenient')` 를 그대로 받는다(65/168,938 = 0.04%). `period_end_basis` 에 lenient 유래를 따로 찍을지는 건수가 무의미해 하지 않는다 | 아니오 |
| `section_kind=NULL` 소제목 상위 종류 상속 | **무관** — §4 에서 `stg_doc_section` 미사용을 확정했으므로 | 아니오 |

---

## 7. EG1 등식 · 파티션 · available 규칙 — 신규/변경 전 목록

**변경 (테이블별 한 줄)**

| 테이블 | 변경 | EG1 등식 (좌변 = equity 산출 행수) | 파티션 | available (basis) |
|---|---|---|---|---|
| `disclosure_version` | grain (group_key, correction_seq) → **(`rcept_no`)**, 컬럼 +25 (§1.2) | `= count(DISTINCT rcept_no)` over 모집단 L4 (§1.5 사다리, 각 단 baseline) | `receipt_axis` `substr(rcept_no,1,4)` — **빌드는 whole-table 로 계산**(정정과 원본이 다른 연도 파티션에 있다) | `rcept_dt` (**`measured`** — `stg_disclosure` 계승. DESIGN §4-5 의 일괄 `derived` 와 다름) |
| `fin_std` | grain + `vintage_kind`·`rcept_no`. `period_end` 정본 교체. `has_correction` → 날짜 컬럼 3종 | `= count(DISTINCT (corp_code, period_end, report_code, fs_div, vintage_kind, rcept_no))` with ≥1 매핑 성공 계정 **∧** 전단사 검사 `count(*) FILTER (vintage_kind='api_restated') = count(DISTINCT (corp_code, bsns_year, reprt_code, fs_div))(stg_fin, account_std)` — 불일치는 `period_end_collision` 로 `_meta` 기록(결산월 변경 후보, 조용히 접지 않는다) | `receipt_axis` (현행) · 4C 는 연도 샤드 루프 | `rcept_dt` (`derived`, 참조표). **`original`/`corrected` 행은 그 문서 자신의 접수일** — `api_restated` 보다 이르다 |
| `corp` | `fiscal_month` 를 `period_end` 산출 축에서 **검산축으로 강등** (`fiscal_month_basis='current_snapshot'` 유지) | 현행 (= distinct corp_code) | `whole` | 없음 (현행) |
| `dataset_profile` | 행 +4: `disclosure_version`·`fin_std · period_end`·`fin_std · vintage`·`fin_std · vintage=pit`(커버리지) | 현행 (선언표) | `whole` | — |
| 4-B 6테이블 (**4B′ 후**) | PK 에 **`src ∈ {api, doc}`** 추가 (DESIGN §3 "소스 상보 결합 테이블은 PK 에 src 포함" 규약 적용) | 현행 등식 **+ `stg_doc_form_cell` 유래 행(2011~2014)** | 현행 `receipt_axis` | `rcept_dt` (`derived`) — doc 유래 행도 같은 참조표 |

**신규 (선택 — 권고는 만들지 않음)**

| 테이블 | EG1 | 파티션 | available |
|---|---|---|---|
| `fin_corr_hint` (§2.4) | `= Σ n_items` over `stg_doc_correction` 중 `corr_has_fin_item` | `receipt_axis` | 정정 `rcept_dt` (`measured`). 값 컬럼의 단위·기간 basis 는 **`unknown`** |

**뷰 변경**

| 뷰 | 변경 |
|---|---|
| `v_fin_latest(asof, lag_override := NULL, **vintage := 'restated'**)` | 반환에서 `has_correction` 삭제 → `has_correction_asof`·`n_corrections_asof`·`days_to_next_correction`·`pit_available`·`period_end_basis` 추가. `fs_div_used` 는 `UFS` 를 선택하지 않는다 |
| `v_fin_latest_restated(asof, lag_override)` (신설, 4C) | v1 동작 동결 — EG5c-3 의 비교 기준 |
| EG-C ⑪ (신설) | `v_*` 어느 뷰의 반환 컬럼에도 `rm_*` 가 없다 (§1.4) |

**게이트 신규 (한 줄씩)**

| # | 유형 | 한 줄 |
|---|---|---|
| E-G1f | 폐기형 | `fin_std` 중 `period_end_basis='doc_period_to'` 비율 ≥ baseline |
| E-G3f | 기록형 | 11011 행에서 `month(period_end) = corp.fiscal_month` 비율 (불일치 = 결산월 변경 후보, 격리 아님) |
| E-G3g | 폐기형 | `kind`(report_nm) ↔ `doc_acode` 일치율 = 100% |
| E-G3h | 폐기형 | `period_label` = `strftime(period_to,'%Y.%m')` 일치율 ≥ baseline |
| E-G3i | 폐기형 | equity `is_correction` = `stg_disclosure.is_correction` 위반 0 |
| E-G3j | 기록형 | `rcept_no` 조인 vs 4키 조인 집합 차 건수 |
| E-G6a | 기록형→폐기형 | `candidate_status ∈ {unique, multi_resolved}` ≥ 99% (분모에서 `none_reason='ledger_floor'` 제외) |
| E-G6b | 기록형 | `date_check ∈ {exact, off_1d}` / parsed·`[기재정정]` (C340 94.8%) |
| E-G6c | 폐기형 | 모집단 사다리 L0~L7 각 단 건수 = baseline |
| E-G6d | 기록형 | `has_zip ∧ NOT corr_page_found` 건수 (기대 3, DEFECT-E02) |
| E-G7a | 기록형→폐기형 | `rm_corrected_later` 원본 중 링크 도달률 ≥ 99% |
| E-G7b | 기록형 | 링크 도달했는데 `rm` 플래그 없는 원본 수 |
| E-G7c | 격리형 | `rcept_dt − period_end ∉ [0, baseline_p99]` — **전환 전후 격리 건수 병기** |
| E-G8d | 기록형→폐기형 (**4C**) | 정정 없는 그룹에서 `original` = `api_restated` 비율 (D9 의 표준 계정판) |
| E-G8e | 기록형 (**4C**) | `items` 가 이름 댄 재무 항목 ⊆ `original`≠`corrected` 인 계정 집합 |
| EG5c-3 | 폐기형 (**4C**) | 정책 전환 diff 가 ①새 판본 ②더 이른 공개일로의 판본 교체 ③결측 해소 셋으로만 설명, 그 밖 0 |
| EG-C ⑪ | 폐기형(테스트) | 뷰 반환에 `rm_*` 없음 |

---

## 부록 A. 이 제안이 의존하는 실측 — 출처별

| 값 | 출처 |
|---|---|
| main 170,762 · HTML 1,824 · main XML 168,938 · `period_from` 채움 168,938 · `parse_mode≠ok` 1,889 | 서버 09-05 (+ 산술: 170,762 − 1,824 = 168,938; 1,889 = 1,824 + lenient 65) |
| `doc_acode` main 11013 77,516 · 11011 51,063 · 11012 40,332 · NULL 1,824 (합 170,735, 잔여 27) | 서버 09-05 |
| `n_xbrl_groups>0` 153,970 · `≥4` 153,963 (차이 7) · 연도별 86.7%(2016)~97.5%(2025) | 서버 09-05 |
| `section_kind` 상위 15 (합 7,486,601 / 전체 7,929,624 → 잔여 443,023) · `fin` 252,238 = 126,124 + 126,112 | 서버 09-05 |
| `D-0-11-0-0` → `fin_legacy`, `D-0-3-2-0/4-0` → `fin` | `src/stage/doc_vocab/section_kind.json` (origin/stage/doc-p1) |
| `stg_doc_correction` 17,600 · `page_found` 전부 true · parsed 15,225 / unparsed 2,375 · 재무 항목 2,238 | 서버 09-05 |
| `page_found` 이 항상 true 인 코드 근거 | `parsers_doc.py:423-427`, `:600-602` |
| 링크 실험 14,026 / 1,199 (분모 15,225) | 서버 09-05 |
| C340: 후보 유일 339/340 · exact 273/291 · exact+1d 276/291 · 최초제출일 해석 291/336 | DOC §1.7 |
| `rm` 검산: 플래그→정정 6,156/6,162 · 정정→플래그 6,156/6,161 · `[첨부정정]` 빼면 96.0% | DOC §1.7 |
| 원장 정정 20,579 = ZIP 17,603 + `014` 2,975 + 없음 1 | DOC §1.7 |
| 정정 정기보고서 접수 24,285 | 서버 09-05 |
| 정기보고서 그룹 181,106 / 정정 있음 20,759 | HANDOFF §4 |
| 정정 타이밍 7일 32% · 90일+ 30% · 1년+ 10% | HANDOFF §4 |
| D6 168,938 / 17,176 / 17,188 / 14,856 · D8 2015 = 2,850/2,412 | 서버 09-05 (`doc_checks.json`) · SQL 은 `doc_checks.py` |
| 2011-03 서식 13종 일괄 도입 · 2011 커버리지 0.93~0.96 · 배당 2010 부터 1.00 | DOC §1.13 |
| `bsns_year` = 종료 연도 · 01·04·05월 결산 중앙값 192~304일 | DESIGN §10 P2 |
| `stg_fin.rcept_no` required · `stg_disclosure` extras(`is_correction`·`rm_corrected_later`) | `rules_dart.py:64` · `:449-451`(`_RM_CODES`) · `:479-482`(extras) |
| 정기보고서 접두 P12: `[기재정정]` 11,942/6,763/3,287 · `[첨부추가]` 2,666 · `[첨부정정]` 969 · 라벨 없음 586 | DESIGN §10 P12 |

## 부록 B. 산술 정합 확인 (이 제안이 스스로 검산한 것)

| 등식 | 값 | 해석 |
|---|---|---|
| 170,762 − 1,824 | = 168,938 | main XML = `period_from` 채움 = D6 `n_main_parsed`. **보고기간 존재율 100%** |
| 1,824 + 65 | = 1,889 | `parse_mode≠ok` = HTML + lenient (DOC P1 실측과 일치) |
| 171,179 − 170,762 | = 417 | main 멤버 없는 ZIP ≈ `[첨부정정]` ZIP 418 (DOC §1.7 표) |
| 17,600 − 17,176 | = 424 | 첫 장이 main 밖(첨부 멤버)에 있는 정정 ≈ 위 418 |
| 17,603 − 17,600 | = 3 | ZIP 있는데 `CORRECTION` 요소 없음 → DEFECT-E02 감시 대상 (C340 0.3% 로는 ~50 기대였다) |
| 17,600 − 17,188 | = 412 | main 파싱본이 없는 정정 접수 |
| 15,225 / 17,600 | = 86.5% | `filed_date` 해석률 — D6 86.4% · C340 86.6% 와 일치 |
| 14,026 / 15,225 | = 92.1% | **`date_check='exact'` 축** — C340 의 93.8% 옆. 링크 성공률 99.7% 와 다른 축(§1.6) |
| 51,063 + 40,332 + 77,516 | = 168,911 | main XML 168,938 − 27 = 정기보고서 3종 밖 코드 27건 |
| 153,970 / 168,938 | = 91.1% | P2 as-reported 의 상한 커버리지 → `pit_available=false` 8.9% |
| 2,238 / 17,600 | = 12.7% | 재무를 건드린 정정 비중 → `has_material_correction` 의 값어치 |
| 17,600 / 20,579 · 17,600 / 24,285 | = 85.5% · 72.5% | 모집단이 어느 쪽이냐에 따라 `no_zip` 비율이 13%p 흔들린다 → DEFECT-E03 |
