# Equity 층 게이트 명세 v1.0 (2026-09-05)

> `EQUITY_WORKFLOW.md` v1.2 §2 의 실행 사양. 초안(`reviews/2026-09-05-equity-gates-proposal.md`)을 오케스트레이터가 검증해 정정한 판. 정정 목록은 §9.

> `EQUITY_WORKFLOW.md` v1.1 §2·§2-1·§3 과 `EQUITY_DESIGN.md` v1.1 §8 의 게이트 조건을 **duckdb 1.5 술어**로 옮긴 구현 사양이다.
> 상위 문서가 "무엇을 지킬 것인가"라면 이 문서는 "어떤 쿼리가 몇을 내야 통과인가"다. 두 문서가 어긋나면 §5 감사표의 판정이 정본이고, 상위 문서를 고친다.
> 규약 근거: `src/stage/gates.py`(GateResult·GateStatus·run_all) · `src/stage/manifest.py`(BuildRecord) · `src/stage/build.py`(`_meta.json`·`_failed/`·`_reject/`) · `src/stage/baseline.py`(Metric·measure) · `STAGE_DESIGN.md` §9 · `DOC_DESIGN.md` §4·§8.1.
> **본문에 임계 숫자를 쓰지 않는다.** 모든 상수는 `bl('<table>','<metric>')` 로 참조하고, 이름은 `{table}.{metric}` 규약을 따른다.

---

## 0. 규약

### 0-1. 분류 어휘 (STAGE_DESIGN §9 계승 + DOC_DESIGN §4 의 3분류)

| 분류 | 실패 시 동작 | stage 선례 |
|---|---|---|
| **폐기형** | 새 버전 전체 폐기. `_tmp` rmtree, `_failed/<build_id>.json` 기록, MANIFEST 미교체 → 구 버전 무손 | G0·G1·G3·G4·G5·G6·G8·G9 |
| **격리형** | 위반 **행**을 `_reject/<reason>/` 로 옮기고 본체에서 뺀다. 셀 위반은 NULL + `miss_kind`. 격리 **비율**이 임계 초과일 때만 테이블 폐기 | G2·G7 |
| **기록형** | 판정하지 않고 `_meta.gates[].metrics` 에만 남긴다. baseline 승인 후 폐기형/격리형으로 승격 | DOC_DESIGN D6·D8·D12 |

`_reject/` 는 stage 가 `_reject/part.parquet` 단일 파일(`build.py:469-472`)이지만 **equity 는 `_reject/<reason>/part.parquet` 로 사유별 디렉토리를 쓴다**(`EQUITY_DESIGN.md` §2). 컬럼 `reject_gate`·`reject_reason` 는 stage 와 동일하게 유지하고, 원문 컬럼을 그대로 보존한다.

### 0-2. 판정 결과 표기 — `gates[]` 원소 (`gates.py:GateResult.as_dict`)

```json
{"name": "EG5", "status": "skip", "detail": "no_baseline", "metrics": {}}
```

`status ∈ {pass, fail, skip}`. **skip 사유는 `detail` 에 문자열 그대로 넣는다**(stage 가 `GateResult("G5", SKIP, "no_baseline", {})` 로 쓰는 형식). equity 가 쓰는 skip 사유 어휘를 폐쇄한다:

| skip 사유 | 뜻 |
|---|---|
| `no_baseline` | 이 게이트의 상수가 `baseline.json` 에 아직 없다. 측정치는 `metrics` 에 남긴다 |
| `no_fixtures` | 픽스처 파일 부재(EG4 는 아래 예외 참조) |
| `no_cross_source` | 교차 축이 원리적으로 없다(ETF 의 KIS 수정종가 등) |
| `no_multi_version` | 자연키당 판본이 1개뿐인 테이블(EG6) |
| `declaration_table` | 행수 등식이 정의되지 않는 선언표(`dataset_profile`·`factor_readiness`·`universe_policy`) |
| `not_grid` | 격자 테이블이 아니다(EG9) |
| `no_coverage` | 데이터 구간이 아직 없어 실행 불가(EG-C ⑥ 의 G05 등) |
| `not_built` | 의존 테이블 미착수 |
| `no_previous_snapshot` | 카탈로그 단계 EG5c 의 직전 `_asof/<view>/<snapshot_id>/` 표본이 없다(첫 catalog 실행, S06) |

**skip 은 통과가 아니다.** 7단계 최종 게이트(§7-4)가 "전 테이블 `gates[]` 에 `status='fail'` 0 **이면서** `status='skip'` 중 `no_baseline` 잔존 0" 을 요구한다.

### 0-3. 상수 참조 — `data/equity/baseline.json`

파일 형식은 stage 와 동일(`baseline.py:measure` 반환 구조): 최상위 `{table: {metric: value}}` + `_measured[]` 에 `{table, metric, db, sql, value, measured_at, growing}`. equity 는 `db` 대신 **`inputs`(고정 stage build)** 를 provenance 로 싣는다.

게이트 실행 전 다음을 만든다.

```sql
-- ① baseline 을 평면 테이블로
CREATE OR REPLACE TEMP TABLE _baseline AS
SELECT u."table" AS tbl, u.metric AS metric, u.value AS value,
       u.measured_at AS measured_at, coalesce(u.growing, false) AS growing
FROM (SELECT UNNEST(_measured) AS u
      FROM read_json('${EQ}/baseline.json', maximum_object_size := 67108864));

-- ② 상수 참조 매크로. 미등재는 NULL → 러너가 skip(no_baseline) 으로 처리한다
CREATE OR REPLACE MACRO bl(t, m) AS (
  SELECT value FROM _baseline WHERE tbl = t AND metric = m);
CREATE OR REPLACE MACRO bl_date(t, m) AS (
  SELECT CAST(value AS DATE) FROM _baseline WHERE tbl = t AND metric = m);
CREATE OR REPLACE MACRO bl_int(t, m) AS (
  SELECT CAST(value AS BIGINT) FROM _baseline WHERE tbl = t AND metric = m);
```

**규칙**: 게이트 SQL 이 `bl(...)` 를 참조하는데 결과가 NULL 이면 러너는 그 술어를 실행하지 않고 게이트 전체를 `skip(no_baseline)` 으로 기록한다(비교식에서 NULL 이 조용히 통과하는 것을 막는다). 술어마다 어떤 metric 을 쓰는지는 §1 각 항 "상수" 열에 적혀 있고, 전 목록은 §1-12 에 모아 뒀다.

### 0-4. 게이트가 읽는 보조 뷰 (러너가 미리 만든다)

```sql
-- 산출 테이블 = 테이블명 그대로의 뷰 (섀도 빌드의 tmp 경로를 가리킨다)
CREATE OR REPLACE TEMP VIEW universe_daily AS
  SELECT * FROM read_parquet('${TMP}/universe_daily/**/*.parquet', hive_partitioning := true);
-- 입력 stage 테이블 = 고정 build 의 _pinned 경로
CREATE OR REPLACE TEMP VIEW stg_listing_daily AS
  SELECT * FROM read_parquet('${EQ}/_pinned/stg_listing_daily/v=${BID}/**/*.parquet',
                             hive_partitioning := true);

-- 격리 행 (사유별)
CREATE OR REPLACE TEMP VIEW _reject AS
  SELECT regexp_extract(filename, '_reject/([^/]+)/', 1) AS reason, *
  FROM read_parquet('${TMP}/*/_reject/*/*.parquet', filename := true, union_by_name := true);
CREATE OR REPLACE TEMP VIEW _reject_counts AS
  SELECT regexp_extract(filename, '/([a-z_]+)/_reject/', 1) AS tbl, reason, count(*) AS n
  FROM _reject GROUP BY ALL;

-- 고정 stage 빌드의 파티션 메타 (lag_known·coverage_from·gates 를 SQL 로 읽는 유일한 경로)
CREATE OR REPLACE TEMP TABLE _stg_meta AS
  SELECT * FROM read_json('${EQ}/_pinned/*/v=*/**/_meta.json',
                          filename := true, union_by_name := true);
-- 이번 빌드가 쓴 equity 파티션 메타
CREATE OR REPLACE TEMP TABLE _eq_meta AS
  SELECT * FROM read_json('${TMP}/*/**/_meta.json', filename := true, union_by_name := true);
-- 커밋된 equity MANIFEST 전체 (7단계 EG5·최종 점검)
CREATE OR REPLACE TEMP TABLE _eq_manifest AS
  SELECT * FROM read_json('${EQ}/*/MANIFEST.json', filename := true, union_by_name := true);
CREATE OR REPLACE TEMP TABLE _pinned_manifest AS
  SELECT "table" AS tbl, UNNEST(builds) AS b
  FROM read_json('${EQ}/_pinned/*/MANIFEST.json', union_by_name := true);

-- 선언 레지스트리 — 코드(rules_equity.py)가 내보내는 표. EG0 의 대조축이다
--   _reg_table(table, stage, grain[], partition_class, available_rule, eg1_equation_id)
--   _reg_column(table, column, kind, unit_suffix, nullable, source_stage_table, source_stage_column)
--   _reg_input(table, stg_table, build_id)          -- BuildRecord.inputs 와 1:1
--   _reg_vocab(domain, value)                        -- basis·sec_type·status·event_type·fill_kind …
--   _reg_contract_column(from_table, to_stage, column)  -- 1단계 → 2~5 계약 컬럼 (WORKFLOW §3-0)
--   _reg_factor_material(factor_id, table, column, coverage_from)  -- §6 EG10
```

### 0-5. `_meta.json` 필드 (equity 판)

stage `build.py:516-527` 의 필드 중 의미가 살아남는 것만 계승하고 조인층 필드를 더한다.

| 필드 | 계승 여부 | 비고 |
|---|---|---|
| `table`·`build_id`·`partition`·`n_rows`·`content_hash`·`gates` | 계승 | 그대로 |
| `n_src`·`n_dedup`·`n_reject` | 계승 | `n_src` 는 **테이블별 EG1 우변**(§3), `n_dedup` 은 선언 dedup 건수 |
| `fanout` | **삭제** | 조인층에 1:1 팬아웃 개념이 없다(WORKFLOW §2 결정 2). 대신 `_reg_table.eg1_equation_id` |
| `snapshot_id` | 빈 문자열 | `BuildRecord.snapshot_id` 는 필수 필드라 `""` 를 명시적으로 넘긴다(WORKFLOW §1) |
| `inputs` | **신규** | `{stg_x: build_id}`. `BuildRecord.inputs` 와 동일 값 |
| `n_reject_by_reason` | **신규** | `{pre_calendar: n, off_grid: n, …}` — EG1 우변이 이 값을 쓴다 |
| `rules_version`·`coverage_from`·`lag_known` | 계승 | equity 자신의 값 |
| `version_loss_upstream`·`observed_date_exempt`·`rcept_map_miss`·`src_bytes`·`src_mtime` | **삭제** | stage 원장 축 전용. 재현성은 `inputs` + `content_hash` 가 대신한다 |

---

## 1. 게이트 사전

각 항 형식: **분류 · 적용 테이블 · 술어(ID 붙은 SQL) · 상수 · 첫 빌드 · 실패 시 · 잡는 위험**.
"잡는 위험" 은 `EQUITY_WORKFLOW.md` §0-3 표의 행 이름이다.

---

### EG0 — 입력 고정 · 선언 대조

- **분류** 폐기형
- **적용** 전 26 테이블 + `correction_link`
- **잡는 위험** §0-3 「재현성」(비결정)
- **첫 빌드** 실행 (상수 없음)
- **실패 시** 폐기. `_failed/<build>.json` 에 `missing_columns`·`unpinned_inputs` 를 싣는다

| ID | 술어 | 통과 |
|---|---|---|
| EG0-P01 | 고정 stage 빌드가 `_pinned/` 에 실존 | `n = 0` |
| EG0-P02 | 선언한 stage 입력 컬럼이 실재 (**stage 컬럼명 기준**) | `n = 0` |
| EG0-P03 | 입력 stage 빌드의 게이트에 fail 없음 | `n = 0` |
| EG0-P04 | 산출 테이블의 선언 컬럼이 실재 · 여분 컬럼 없음 | `n = 0` |
| EG0-P05 | 1단계 계약 컬럼이 2~5 단계 입력에 전부 실재 | `n = 0` |
| EG0-P06 | 파티션 클래스 선언과 실제 디렉토리 축이 일치 | `n = 0` |

```sql
-- EG0-P01 : inputs 의 전 stage 테이블이 _pinned 에 같은 build_id 로 존재
SELECT count(*) AS n
FROM _reg_input i
WHERE NOT EXISTS (SELECT 1 FROM _pinned_manifest p
                  WHERE p.tbl = i.stg_table AND p.b.build_id = i.build_id);

-- EG0-P02 : 선언 컬럼 실재. duckdb_columns() 는 위 §0-4 뷰들을 그대로 본다
SELECT count(*) AS n
FROM _reg_column r
WHERE r.source_stage_table IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM duckdb_columns() c
                  WHERE c.table_name = r.source_stage_table
                    AND c.column_name = r.source_stage_column);

-- EG0-P03 : 입력 stage 빌드 게이트 fail 0
SELECT count(*) AS n
FROM (SELECT UNNEST(gates) AS g FROM _stg_meta)
WHERE g.status = 'fail';

-- EG0-P04 : 산출 스키마 양방향 대조 (선언 밖 컬럼도 실패 — 조용한 컬럼 추가 차단)
WITH declared AS (SELECT "table" AS t, "column" AS c FROM _reg_column),
     actual   AS (SELECT table_name AS t, column_name AS c FROM duckdb_columns()
                  WHERE table_name IN (SELECT "table" FROM _reg_table))
SELECT (SELECT count(*) FROM (SELECT * FROM declared EXCEPT SELECT * FROM actual))
     + (SELECT count(*) FROM (SELECT * FROM actual   EXCEPT SELECT * FROM declared)) AS n;

-- EG0-P05 : 1단계 → 2~5 계약 컬럼
SELECT count(*) AS n
FROM _reg_contract_column k
WHERE NOT EXISTS (SELECT 1 FROM duckdb_columns() c
                  WHERE c.table_name = k.from_table AND c.column_name = k."column");

-- EG0-P06 : 파티션 축 대조 (receipt_axis 는 rcept_no 필수, date_axis 는 date 필수)
SELECT count(*) AS n FROM _reg_table t
WHERE (t.partition_class = 'receipt_axis'
       AND NOT EXISTS (SELECT 1 FROM duckdb_columns() c
                       WHERE c.table_name = t."table" AND c.column_name = 'rcept_no'))
   OR (t.partition_class = 'date_axis'
       AND NOT EXISTS (SELECT 1 FROM duckdb_columns() c
                       WHERE c.table_name = t."table" AND c.column_name = 'date'));
```

> **주의(§5-A1)**: `consensus_daily` 는 `date_axis` 인데 물리 파티션 키가 `obs_month` 의 연도라 `date` 컬럼이 없다(DESIGN §4-6). EG0-P06 은 `_reg_table.partition_key_expr` 을 선언 필드로 두고 그 식이 참조하는 컬럼 실재를 보는 형태로 고쳐야 한다.

---

### EG1 — 격자 등식

- **분류** 폐기형
- **적용** 25 테이블 (선언표 3 — `dataset_profile`·`factor_readiness`·`universe_policy` 는 `skip(declaration_table)`)
- **잡는 위험** §0-3 「생존편향」(행 소실) · 「레짐 편향」(격자 결손)
- **첫 빌드** 실행
- **실패 시** 폐기. `_failed` 에 좌·우변과 차이, 차집합 키 표본 200행
- **상수** 표별로 §3 참조

**일반형** — stage G1(`stage = n_src × fanout − dedup − reject`)의 조인층 판이다.

```
count(equity_table) = <선언 우변>  −  n_dedup  −  Σ n_reject_by_reason
```

**허용오차 0.** 세 항은 서로 다른 쿼리로 독립 산출한다(stage `gates.py:g1_row_equation` 과 같은 규약). 전수 SQL 은 §3.

> **결함(§5-B1)**: `EQUITY_WORKFLOW.md` §2-1 의 등식 21행 중 `_reject` 항을 명시한 것은 3단계 3테이블뿐이다. `fin_std`(EG7 `period_end` 격리)·`security`(EG7 `sec_type='other'` 격리)·`disclosure_version`(`no_label` 격리)은 격리 행이 좌변에서 빠지는데 우변에 뺄셈 항이 없어 **정상 빌드가 EG1 에서 실패**한다. 일반형이 정본이고 §2-1 표를 고쳐야 한다.

---

### EG2 — PIT 불변식

- **분류** 폐기형
- **적용** 전 팩트 테이블(차원표 `corp`·`security`·`corp_ticker`·`trading_calendar`·`universe_policy` 는 `available_date` 비부여 → P01~P03 `skip`, P04 는 전역 1회)
- **잡는 위험** §0-3 「look-ahead (공개시점)」
- **첫 빌드** 실행
- **실패 시** 폐기

| ID | 술어 | 통과 |
|---|---|---|
| EG2-P01 | `available_date` NOT NULL ∨ `available_basis='unknown'` | `n = 0` |
| EG2-P02 | `available_date ≥ 내용일` (테이블별 내용일 축 선언) | `n = 0` |
| EG2-P03 | `available_basis` 어휘 폐쇄 | `n = 0` |
| EG2-P04 | `lag_known=false` 인 stage 원천 → `dataset_profile` 행 ∧ **`recommended_lag_sessions ≥ 1`** | `n = 0` |
| EG2-P05 | 파생 컬럼 `available` = 구성 행 `available` 의 max | `n = 0` |
| EG2-P06 | `basis='default'` 인 profile 행에 `evidence` 필수 | `n = 0` |
| EG2-P07 | `coverage_from` 전수 (profile 전 행 NOT NULL) | `n = 0` |
| EG2-P08 | (S19 2차 신설) `estimated_coverage_pct` 가 정수 분자·분모의 순수 함수 — `0 ≤ n_observed ≤ n_denominator` ∧ `pct = round(100·n_observed/n_denominator, 6)` | `n = 0` |

```sql
-- EG2-P01
SELECT count(*) AS n FROM ${T}
WHERE available_date IS NULL AND available_basis IS DISTINCT FROM 'unknown';

-- EG2-P02 : 내용일 축은 _reg_table.content_date_column 이 선언한다.
--           corp_event·adj_factor 는 announce_date 축(effective_date 아님 — DESIGN §4-2)
SELECT count(*) AS n FROM ${T} WHERE available_date < ${CONTENT_DATE_COL};
--   corp_event  : ${CONTENT_DATE_COL} = announce_date
--   adj_factor  : ${CONTENT_DATE_COL} = (SELECT e.announce_date FROM corp_event e
--                                        WHERE e.event_id = adj_factor.event_id)
--   fin_std     : ${CONTENT_DATE_COL} = period_end
--   price_daily · universe_daily · index_daily · 격자 3종 : date

-- EG2-P03
SELECT count(*) AS n FROM ${T}
WHERE available_basis NOT IN (SELECT value FROM _reg_vocab WHERE domain = 'basis');
--   _reg_vocab('basis') = measured · derived · convention · default · unknown  (DESIGN §1)

-- EG2-P04 : stage _meta 의 lag_known 을 SQL 로 읽는다
SELECT count(*) AS n
FROM (SELECT DISTINCT "table" AS stg_table FROM _stg_meta WHERE lag_known = false) s
WHERE EXISTS (SELECT 1 FROM _reg_input i WHERE i.stg_table = s.stg_table)   -- 실제 입력만
  AND NOT EXISTS (
    SELECT 1 FROM dataset_profile p
    WHERE list_contains(p.source_stage_tables, s.stg_table)
      AND p.recommended_lag_sessions >= 1);
--   랙 정본은 **세션**이다(FIELD_MAP §1·DESIGN §4-7) — 초안의 `_days` 는 §9 S19 에서 정정.
--   `_stg_meta` 는 러너가 만드는 뷰지만 equity 빌드 세션에는 없다: 구현(`rules_s19`)은 고정한
--   equity 파티션의 `_meta.lag_known_inputs`(build.py 가 입력마다 stage lag_known 을 복사한 맵)를
--   합집합으로 읽어 같은 모집단을 만든다.

-- EG2-P05 : 파생 컬럼 동반 available. `<col>_available_date` 규약 (§5-C4 참조)
SELECT count(*) AS n FROM fin_std f
WHERE f.derived_n_rows IS NOT NULL
  AND f.q4_derived_available_date IS DISTINCT FROM (
    SELECT max(g.available_date) FROM fin_std g
    WHERE g.corp_code = f.corp_code AND g.fs_div = f.fs_div
      AND g.period_end IN (f.period_end, f.period_end - INTERVAL 3 MONTH,
                           f.period_end - INTERVAL 6 MONTH, f.period_end - INTERVAL 9 MONTH));

-- EG2-P06 / EG2-P07
SELECT count(*) FILTER (WHERE available_date_basis LIKE '%default%'
                           AND coalesce(trim(evidence), '') = '')
     + count(*) FILTER (WHERE coverage_from IS NULL) AS n
FROM dataset_profile;
--   컬럼명은 `available_date_basis` 다(초안의 `basis` 는 §9 S19 정정). 한 필드가 두 basis 를
--   섞어 볼 수 있어(`price.adj_close` = default|derived) 동등비교가 아니라 포함 검사다.
```

---

### EG3 — 키 · 불변식

- **분류** 폐기형
- **적용** 전 테이블(P01) + 테이블별 특수 불변식
- **잡는 위험** §0-3 「우선주 시총 누락」 · 「조정 오류」(계수 항등) · 「정리매매·거래정지 잔류」 · 「유니버스 정책 오염」
- **첫 빌드** 실행 (P04·P06 의 허용오차만 baseline → 없으면 그 술어만 `skip(no_baseline)`)
- **실패 시** 폐기

| ID | 대상 | 술어 | 상수 |
|---|---|---|---|
| EG3-P01 | 전 테이블 | PK 유일 | — |
| EG3-P02 | `corp_ticker` | KR7 isin8 그룹당 `is_common` 정확히 1 | — |
| EG3-P03 | `security_span` | 같은 티커 구간 비중첩 | — |
| EG3-P04 | `adj_factor` | 시총 불변 이벤트 `price_factor × share_factor = 1` | `adj_factor.factor_product_tol` |
| EG3-P05 | `corp_ticker`×`price_daily` | 기업 시총 합산 대상 = isin8 그룹 ∩ 그날 상장 종류주 | — |
| EG3-P06 | `flow_daily` | 키움 12주체 순매수 합 = 0 (`orgn` 제외) | `flow_daily.investor_sum_tol_krw` |
| EG3-P07 | 전 테이블 | `ticker` = VARCHAR(6) | — |
| EG3-P08 | `corp` | `induty_code` 공란 0 | — |
| EG3-P09 | `universe_daily` | `halt_state` 열린 채 폐지·gap 없이 끝난 구간 0 | — |
| EG3-P10 | `security` | 폐지 종목 전부 `delist_date` 보유 | `security.delisted_total` |
| EG3-P11 | `security` | `delist_conflict` 건수 ≤ baseline | `security.delist_conflict_max` |
| EG3-P12 | `security_span` | 폐지 종목 전부 span 종료 보유 | `security.delisted_total` |
| EG3-P13 | 전 테이블 | 어휘 폐쇄(`sec_type`·`status`·`end_reason`·`event_type`·`factor_source`·`fill_kind.*`) | — |

```sql
-- EG3-P01
SELECT count(*) AS n FROM (
  SELECT ${PK_COLS}, count(*) c FROM ${T} GROUP BY ALL HAVING c > 1);

-- EG3-P02  (비KR7 40티커는 단독 corp — 제외가 아니라 술어 밖. DESIGN §4-1)
SELECT count(*) AS n FROM (
  SELECT isin8, count(*) FILTER (WHERE is_common) AS n_common
  FROM corp_ticker WHERE isin8 LIKE 'KR7%' GROUP BY isin8 HAVING n_common <> 1);

-- EG3-P03
SELECT count(*) AS n
FROM security_span a JOIN security_span b
  ON a.ticker = b.ticker AND a.span_seq < b.span_seq
WHERE a.last_date >= b.first_date AND b.last_date >= a.first_date;

-- EG3-P04
SELECT count(*) AS n
FROM adj_factor a JOIN corp_event e USING (event_id)
WHERE e.event_type IN (SELECT value FROM _reg_vocab WHERE domain = 'mktcap_neutral_event')
  AND abs(a.price_factor * a.share_factor - 1) > (SELECT bl('adj_factor','factor_product_tol'));

-- EG3-P05 : v_firm_mktcap 을 그대로 재호출하면 항진명제다(§5-C7).
--           구성 집합을 독립 재계산해 비교한다
WITH d AS (SELECT bl_date('trading_calendar','fixture_date') AS d),
     expect AS (
       SELECT ct.isin8, count(*) AS n_leg, sum(p.mktcap_krw) AS mktcap
       FROM corp_ticker ct
       JOIN universe_daily u ON u.ticker = ct.ticker AND u.date = (SELECT d FROM d)
                            AND u.status IN ('listed','suspended')
       JOIN price_daily p ON p.ticker = ct.ticker AND p.date = (SELECT d FROM d)
       WHERE ct.isin8 LIKE 'KR7%' GROUP BY ct.isin8),
     got AS (SELECT corp_code, firm_mktcap_krw FROM v_firm_mktcap((SELECT d FROM d)))
SELECT count(*) AS n
FROM expect e JOIN corp_ticker ct ON ct.isin8 = e.isin8 AND ct.is_common
JOIN got g ON g.corp_code = ct.corp_code
WHERE g.firm_mktcap_krw IS DISTINCT FROM e.mktcap;

-- EG3-P06 : coalesce 로 NULL 을 0 으로 접으면 결측이 항등을 통과한다.
--           12컬럼 전부 NOT NULL 인 행만 검사하고, 제외 행수를 metrics 에 남긴다
WITH k AS (
  SELECT *, (ind_invsr_krw + frgnr_invsr_krw + fnnc_invt_krw + insrnc_krw + invtrt_krw
           + etc_fnnc_krw + bank_krw + penfnd_etc_krw + samo_fund_krw + natn_krw
           + etc_corp_krw + natfor_krw) AS s
  FROM flow_daily
  WHERE src = 'kiwoom'
    AND num_nulls(ind_invsr_krw, frgnr_invsr_krw, fnnc_invt_krw, insrnc_krw, invtrt_krw,
                  etc_fnnc_krw, bank_krw, penfnd_etc_krw, samo_fund_krw, natn_krw,
                  etc_corp_krw, natfor_krw) = 0)
SELECT count(*) FILTER (WHERE abs(s) > (SELECT bl('flow_daily','investor_sum_tol_krw'))) AS n,
       (SELECT count(*) FROM flow_daily WHERE src = 'kiwoom') - count(*) AS n_excluded_null
FROM k;

-- EG3-P07
SELECT count(*) AS n FROM ${T}
WHERE typeof(ticker) <> 'VARCHAR' OR length(ticker) <> 6;

-- EG3-P08
SELECT count(*) AS n FROM corp WHERE coalesce(trim(induty_code), '') = '';

-- EG3-P09 : 열린 halt_state 로 끝나는 구간
SELECT count(*) AS n
FROM security_span s
JOIN universe_daily u ON u.ticker = s.ticker AND u.date = s.last_date
WHERE u.halt_state AND s.end_reason NOT IN ('delisted','coverage_gap');

-- EG3-P10 / EG3-P12 : 폐지 모집단은 KIS 축 ∪ listing 소멸 축으로 독립 산출한다
WITH delisted AS (
  SELECT DISTINCT ticker FROM stg_delisted_master WHERE lstg_abol_dt IS NOT NULL
  UNION
  SELECT s.ticker FROM security_span s
  WHERE s.end_reason = 'delisted')
SELECT (SELECT count(*) FROM delisted) - (SELECT bl_int('security','delisted_total')) AS d_pop,
       (SELECT count(*) FROM delisted d
        JOIN security sec USING (ticker) WHERE sec.delist_date IS NULL) AS n_no_delist_date,
       (SELECT count(*) FROM delisted d
        WHERE NOT EXISTS (SELECT 1 FROM security_span s
                          WHERE s.ticker = d.ticker AND s.end_reason = 'delisted')) AS n_no_span_end;
-- 통과: d_pop = 0 ∧ n_no_delist_date = 0 ∧ n_no_span_end = 0

-- EG3-P11
SELECT count(*) FILTER (WHERE delist_conflict) AS n FROM security;
-- 통과: n <= bl('security','delist_conflict_max')

-- EG3-P13 : 어휘 폐쇄 (템플릿 — 컬럼×domain 쌍은 _reg_column.vocab_domain 이 선언)
SELECT count(*) AS n FROM ${T}
WHERE ${COL} IS NOT NULL
  AND ${COL} NOT IN (SELECT value FROM _reg_vocab WHERE domain = '${DOMAIN}');
```

---

### EG4 — 골든 픽스처

- **분류** 폐기형
- **적용** 전 26 테이블 + `correction_link`. 픽스처 파일 `data/equity/fixtures/<table>.json`
- **잡는 위험** §0-3 「생존편향」·「조정 오류」·「우선주 시총 누락」·「look-ahead (정정 공시)」(결측 재현)
- **첫 빌드** 실행
- **실패 시** 폐기
- **상수** 없음(기대값은 픽스처 파일 안 리터럴)

파일 포맷은 stage 와 동일(`src/stage/fixtures/stg_price_daily.json`):
`[{key: {…}, column, expect, measured_sql, measured_at, note}]`. `expect: null` 은 SQL NULL 기대다.
비교는 stage `gates.py:g4_fixtures` 규약대로 **양변을 VARCHAR 로 캐스팅**해서 한다.

**equity 추가 필드 3개**

| 필드 | 뜻 | 게이트 |
|---|---|---|
| `expect_source` | `hand`(손계산) / `doc:<문서 §절>`(실측 기록) / `stage:<table>.<column>`(stage 원장 단일 셀) | EG4-P03 |
| `asof` | as-of 뷰 픽스처면 그 날짜. 테이블 픽스처면 없음 | — |
| `fixture_class` | `positive`(기대값 일치) / `negative`(§7-5 부정 픽스처, 게이트가 fail 을 내야 통과) | EG4-P04 |

| ID | 술어 | 통과 |
|---|---|---|
| EG4-P01 | 픽스처 전건 일치 | `n_mismatch = 0` |
| EG4-P02 | §4 카탈로그의 필수 픽스처 ID 가 파일에 전부 있다 | `n_missing = 0` |
| EG4-P03 | **look-ahead 유래 기대값 금지** | `n = 0` |
| EG4-P04 | 단위 접미사(`_krw`·`_shr`·`_pct`) 컬럼과 계수 컬럼에 픽스처 필수 | `n_uncovered = 0` |

```sql
-- EG4-P03 : 기대값 출처가 look-ahead 테이블이면 픽스처 자체가 무효 (WORKFLOW §2 EG4)
--           금지 목록은 _reg_vocab('lookahead_source') = stg_v3_revision_compare
--                       · stg_consensus_monthly 의 lookback 계열 컬럼 · stg_v3_consensus_annual
SELECT count(*) AS n
FROM _fixtures f
WHERE f.expect_source LIKE 'stage:%'
  AND split_part(replace(f.expect_source, 'stage:', ''), '.', 1)
      IN (SELECT value FROM _reg_vocab WHERE domain = 'lookahead_source');

-- EG4-P04 : stage G4 의 unit_scale 강제(×1e6 오적용 방어)를 equity 로 옮긴 것
SELECT count(*) AS n
FROM _reg_column c
WHERE (regexp_matches(c."column", '_(krw|shr|pct)$') OR c."column" LIKE '%factor')
  AND NOT EXISTS (SELECT 1 FROM _fixtures f
                  WHERE f.tbl = c."table" AND f."column" = c."column");
```

---

### EG5 — 회귀 · 재현성

- **분류** 폐기형
- **적용** 전 테이블(a·b) / `fin_std`·`consensus_daily`·`universe_daily` (c)
- **잡는 위험** §0-3 「재현성」(비결정 · stage 재빌드가 과거 as-of 를 재작성)
- **첫 빌드** `skip(no_baseline)` — 직전 빌드가 없다
- **실패 시** 폐기

| ID | 술어 | 상수 |
|---|---|---|
| EG5a-P01 | `inputs` 불변이면 파티션 `content_hash` 전량 동일 | — |
| EG5b-P01 | `inputs` 변경이면 EG1 등식이 새 입력으로 성립 | — (EG1 재실행) |
| EG5c-P01 | as-of 불변: 고정 표본에서 뷰 결과 차이가 전부 "새 `rcept_dt`/`fetched_date` > asof" 로 설명 | `<view>.asof_sample_dates`·`<view>.asof_sample_tickers` |
| EG5c-P02 | `rcept_dt ≤ asof` 인데 값이 바뀐 건수 ≤ baseline | `fin_std.asof_value_change_max` |

```sql
-- EG5a-P01 : 직전 커밋 빌드와 파티션 단위 해시 대조
WITH prev AS (
  SELECT b.build_id, b.inputs, UNNEST(b.partitions) AS p
  FROM (SELECT "table", builds[-1] AS b FROM _eq_manifest WHERE "table" = '${T}')),
     cur AS (SELECT partition, content_hash FROM _eq_meta WHERE "table" = '${T}')
SELECT count(*) AS n
FROM prev JOIN cur ON cur.partition = regexp_extract(prev.p.path, '/([^/]+)$', 1)
WHERE prev.inputs = (SELECT inputs FROM _eq_meta WHERE "table" = '${T}' LIMIT 1)
  AND cur.content_hash IS DISTINCT FROM prev.p.content_hash;

-- EG5c-P01 : as-of 불변. 표본 (날짜 × 종목) 은 baseline 이 고정한다.
--   이전 빌드가 남긴 as-of 스냅샷 `${EQ}/_asof/<view>/<build>/…parquet` 와 이번 빌드 결과를 대조
WITH s AS (
  SELECT UNNEST(CAST((SELECT value FROM _baseline
                      WHERE tbl='v_fin_latest' AND metric='asof_sample_dates') AS DATE[])) AS asof),
     prev AS (SELECT * FROM read_parquet('${EQ}/_asof/v_fin_latest/${PREV_BID}/*.parquet')),
     cur  AS (SELECT s.asof, f.* FROM s, LATERAL v_fin_latest(s.asof) f)
SELECT count(*) AS n_unexplained
FROM (SELECT * FROM cur EXCEPT SELECT * FROM prev) d
LEFT JOIN prev p ON p.asof = d.asof AND p.corp_code = d.corp_code
                AND p.period_end = d.period_end AND p.fs_div_used = d.fs_div_used
WHERE p.corp_code IS NOT NULL          -- 새로 나타난 행이 아니라 값이 바뀐 행
   OR d.available_date <= d.asof;      -- asof 이후 공시로 설명되지 않는 신규 행
```

> **설계 요구(§5-C2)**: EG5c 는 "이전 빌드의 as-of 결과"를 물리적으로 갖고 있어야 성립한다. `_pinned/` 와 같은 격으로 `data/equity/_asof/<view>/<build_id>/` 를 남기는 규약이 WORKFLOW·DESIGN 어디에도 없다 — **신설이 필요**하다. 표본은 `bl('v_fin_latest','asof_sample_dates')` 와 `asof_sample_tickers` 로 고정한다.

---

### EG6 — 판본 선택

- **분류** 폐기형 (오판율 항만 첫 빌드 `skip(no_baseline)`)
- **적용** `fin_std`·`disclosure_version`·`correction_link`·`consensus_daily`·`opinion_daily`·`corp`·`security`
- **잡는 위험** §0-3 「look-ahead (정정 공시)」·「look-ahead (컨센서스)」
- **첫 빌드** 실행. `disclosure_version.link_misjudge_max`·`correction_link.link_rate_min` 은 `skip(no_baseline)`
- **실패 시** 폐기

| ID | 대상 | 술어 | 상수 |
|---|---|---|---|
| EG6-P01 | `consensus_daily`(wise) | 선택 행의 `fetched_date` = 그 키 그룹의 min | — |
| EG6-P02 | `consensus_daily`(v3) | 선택 행의 관측일 = 월 내 min(`date`) | — |
| EG6-P03 | 전 대상 | `is_latest`·`is_current` 류 컬럼 부재 | — |
| EG6-P04 | `corp`·`security` | first_write_wins: `observed_date` 최소 판본 채택 | — |
| EG6-P05 | `correction_link` | **E-G6a** 링크 성립률 ≥ baseline | `correction_link.link_rate_min` |
| EG6-P06 | `correction_link` | **E-G6b** `date_check ∈ {exact, off_1d}` 비율 (기록형) | `correction_link.date_exact_rate` |
| EG6-P07 | `disclosure_version` | 정정 그룹 표본 오판율 ≤ baseline | `disclosure_version.misjudge_rate_max` |
| EG6-P08 | `fin_std`⋈`disclosure_version` | 무매칭률 비12월 = 12월 ± baseline | `disclosure_version.nonmatch_rate_gap_max` |

```sql
-- EG6-P01 : "max 선택 0" 을 구현과 독립적으로 재계산해 확인한다(§5-C5 항진명제 해소)
SELECT count(*) AS n
FROM consensus_daily c
WHERE c.src = 'wise'
  AND c.available_date IS DISTINCT FROM (
    SELECT min(m.fetched_date) FROM stg_consensus_monthly m
    WHERE m.ticker = c.ticker AND m.target_period = c.target_period
      AND m.metric = c.metric
      AND strftime(m.obs_date, '%Y-%m') = strftime(c.obs_month, '%Y-%m'));

-- EG6-P02  ★ 이 초안 식은 틀렸다 — §9 "S17" 블록 참조. duckdb 의 min_by/arg_min 은 인자가
--          NULL 인 행을 건너뛰므로 최초 관측의 collected_date 가 NULL 이면 fallback 대신
--          '그 뒤 처음으로 collected_date 가 있는 행' 을 고른다. 구현은 ORDER BY date LIMIT 1.
SELECT count(*) AS n
FROM consensus_daily c
WHERE c.src = 'v3'
  AND c.available_date IS DISTINCT FROM (
    SELECT coalesce(min_by(r.collected_date, r.date), min(r.date))
    FROM stg_v3_revision_daily r
    WHERE r.ticker = c.ticker AND r.target_period = c.target_period
      AND strftime(r.date, '%Y-%m') = strftime(c.obs_month, '%Y-%m'));

-- EG6-P03
SELECT count(*) AS n FROM duckdb_columns()
WHERE table_name IN (SELECT "table" FROM _reg_table)
  AND (column_name LIKE 'is\_latest%' ESCAPE '\' OR column_name LIKE '%\_current' ESCAPE '\');
-- 예외: corp.corp_name·security.name_current 처럼 "현재값 라벨"로 선언한 컬럼은
--       _reg_column.current_label = true 로 등재하고 술어에서 뺀다

-- EG6-P05 : DOC_DESIGN §8.1 E-G6a
SELECT count(*) FILTER (WHERE candidate_status IN ('unique','multi_resolved'))::DOUBLE
       / nullif(count(*), 0) AS rate
FROM correction_link;
-- 통과: rate >= bl('correction_link','link_rate_min')

-- EG6-P06 : E-G6b (기록형 — 값만 남긴다)
SELECT count(*) FILTER (WHERE date_check IN ('exact','off_1d'))::DOUBLE
       / nullif(count(*) FILTER (WHERE date_check NOT IN ('unparsed','no_page','no_zip','n/a')), 0)
       AS date_exact_rate
FROM correction_link;

-- EG6-P08 : 무매칭률 비대칭 (DESIGN §4-4)
WITH j AS (
  SELECT f.corp_code, (c.fiscal_month = 12) AS dec_fy,
         EXISTS (SELECT 1 FROM disclosure_version v
                 WHERE v.corp_code = f.corp_code AND v.bsns_year = f.bsns_year
                   AND v.reprt_code = f.report_code) AS matched
  FROM fin_std f JOIN corp c USING (corp_code))
SELECT abs(
    (count(*) FILTER (WHERE NOT dec_fy AND NOT matched)::DOUBLE
     / nullif(count(*) FILTER (WHERE NOT dec_fy), 0))
  - (count(*) FILTER (WHERE dec_fy AND NOT matched)::DOUBLE
     / nullif(count(*) FILTER (WHERE dec_fy), 0))) AS gap
FROM j;
-- 통과: gap <= bl('disclosure_version','nonmatch_rate_gap_max')
```

---

### EG7 — 범위 · 부호

- **분류** **격리형**
- **적용** 전 테이블
- **잡는 위험** §0-3 「조정 오류」 · (간접) 「생존편향」
- **첫 빌드** 실행. 격리 비율 임계는 stage 초기값(`gates.py:DEFAULT_THRESHOLDS['G7']`)을 첫 빌드 기본으로 쓰고, 2회차부터 `bl('<table>','threshold_EG7')` 로 이관
- **실패 시** 격리. 위반 행은 `_reject/<reason>/`, 셀 위반은 NULL + `miss_kind='out_of_range'`. **비율 초과일 때만 테이블 폐기**

| ID | 대상 | 격리 사유(`reject_reason`) | 술어 |
|---|---|---|---|
| EG7-P01 | `price_daily` | `nonpositive_price` | `close <= 0 ∨ (open ≤ 0 ∧ open IS NOT NULL)` … |
| EG7-P02 | `adj_factor` | `nonpositive_factor` | `price_factor <= 0 ∨ share_factor <= 0` |
| EG7-P03 | `security` | `unknown_sec_type` | `sec_type = 'other'` |
| EG7-P04 | `fin_std` | `rcept_lag_out_of_range` | `rcept_dt − period_end ∉ [0, baseline p99]` |
| EG7-P05 | `disclosure_version` | `no_label` | 기간 라벨 `(YYYY.MM)` 없음 |
| EG7-P06 | 격자 3종 | `pre_calendar` / `off_grid` | 캘린더 하한 이전 / 유니버스 밖 |
| EG7-P07 | 비율 컬럼 전수 | `pct_out_of_range` | 선언 범위 밖 |
| EG7-P08 | `corp_event` | `effective_before_announce` | `effective_date < announce_date − baseline` |

```sql
-- EG7-P04 : 상한은 baseline p99, 하한은 등식 0 (음수 = 결산 전 접수 = 불가능)
SELECT count(*) AS n_reject
FROM fin_std
WHERE date_diff('day', period_end, rcept_dt) < 0
   OR date_diff('day', period_end, rcept_dt) > (SELECT bl_int('fin_std','rcept_lag_p99_days'));

-- EG7 공통 비율 판정 (stage gates.py:g7_range 와 같은 형태)
SELECT (SELECT sum(n) FROM _reject_counts WHERE tbl = '${T}')::DOUBLE
       / nullif((SELECT n_src FROM _eq_meta WHERE "table" = '${T}' LIMIT 1), 0) AS ratio;
-- 통과: ratio <= coalesce(bl('${T}','threshold_EG7'), 기본값)
```

> **주의(§5-C6)**: `sec_type='other'` 격리(EG7-P03)와 EG1 `security` 등식(= listing ∪ etf distinct ticker)이 충돌한다. 격리된 티커는 `security` 에서 빠지는데 그 티커의 가격 행은 `price_daily` 에 남고 `universe_daily` 격자에서 사라진다 → **생존편향을 게이트가 만든다**. `other` 는 격리가 아니라 **행 유지 + `sec_type_unknown` 플래그 + 건수 baseline** 이어야 한다. §5 에서 삭제 판정.

---

### EG8 — 교차 소스

- **분류** 폐기형
- **적용** `price_daily`·`adj_factor`·`corp_event`·`flow_daily`·`short_daily`·`consensus_daily`·`security`·`correction_link`
- **잡는 위험** §0-3 「조정 오류」·「생존편향」(정리매매·폐지 recall)
- **첫 빌드** `skip(no_baseline)` 전량. ETF 행은 `sec_type` 축으로 `skip(no_cross_source)`
- **실패 시** 폐기

| ID | 대상 | 술어 | 상수 |
|---|---|---|---|
| EG8-P01 | `price_daily`×`adj_factor` | `krx_kis_close_ratio` 대 누적계수 일치율 ≥ baseline | `price_daily.krx_kis_ratio_match_min` |
| EG8-P02 | `adj_factor` | 분할·무상증자 이벤트일 **수정수익률** 점프 절댓값 ≤ baseline | `adj_factor.adj_return_jump_max` |
| EG8-P03 | `adj_factor` | ok 이벤트 집합의 **조정 거래량** 20세션 중앙값 비(후/전)의 중앙값 ∈ [1/k, k] (3차 정정, §9 — 건별 하루 점프 M03 은 기록형) | `adj_factor.adj_volume_ratio_band` |
| EG8-P04 | `corp_event` | 기준가≠전일종가 사례의 이벤트 매칭 recall ≥ baseline | `corp_event.detect_recall_min`·`corp_event.base_price_anomaly_n` |
| EG8-P05 | `flow_daily` | 키움 ⋈ KIS `flow_split` 겹침 = 0 | — (등식) |
| EG8-P06 | `short_daily` | 키움 ⋈ KIS 겹침 = 0 | — (등식) |
| EG8-P07 | `consensus_daily` | v3 ⋈ WISE 겹침 구간 일치율 ≥ baseline | `consensus_daily.v3_wise_match_min` |
| EG8-P08 | `security` | 정리매매·폐지 recall ≥ baseline | `security.delist_signal_recall_min`·`security.delist_signal_window_days` |
| EG8-P09 | `correction_link` | **E-G7** 원장 `rm` 정정 원본 도달률 ≥ baseline | `correction_link.reach_rate_min` |

```sql
-- EG8-P02 : 이벤트일 수정수익률 점프. 원주가가 아니라 v_adj_price 로 본다
WITH ev AS (
  SELECT a.ticker, a.effective_date
  FROM adj_factor a JOIN corp_event e USING (event_id)
  WHERE e.event_type IN ('split','bonus','stock_dividend')),
     px AS (
  SELECT p.ticker, p.date, p.adj_close,
         lag(p.adj_close) OVER (PARTITION BY p.ticker ORDER BY p.date) AS prev_close
  FROM v_adj_price((SELECT bl_date('adj_factor','asof_for_jump_check'))) p)
SELECT max(abs(px.adj_close / nullif(px.prev_close, 0) - 1)) AS max_jump,
       count(*) FILTER (WHERE abs(px.adj_close / nullif(px.prev_close, 0) - 1)
                              > (SELECT bl('adj_factor','adj_return_jump_max'))) AS n
FROM ev JOIN px ON px.ticker = ev.ticker AND px.date = ev.effective_date;

-- EG8-P03 : 조정 거래량 점프 — 계수 방향(share_factor 곱셈)이 뒤집히면 여기서만 잡힌다
WITH ev AS (SELECT ticker, effective_date FROM adj_factor),
     vol AS (
  SELECT v.ticker, v.date, v.adj_volume,
         median(v.adj_volume) OVER (PARTITION BY v.ticker ORDER BY v.date
                                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS med20
  FROM v_adj_volume((SELECT bl_date('adj_factor','asof_for_jump_check'))) v)
SELECT count(*) AS n
FROM ev JOIN vol ON vol.ticker = ev.ticker AND vol.date = ev.effective_date
WHERE abs(vol.adj_volume / nullif(vol.med20, 0)) > (SELECT bl('adj_factor','adj_volume_jump_max'));

-- EG8-P04 : 탐지 recall. 분모(기준가≠전일종가)를 독립 산출한다
WITH anomaly AS (
  SELECT p.ticker, p.date
  FROM price_daily p
  JOIN price_daily q ON q.ticker = p.ticker
                    AND q.date = (SELECT prev_td FROM trading_calendar t WHERE t.date = p.date)
  WHERE p.price_kind = 'reference' AND p.close IS DISTINCT FROM q.close)
SELECT count(*) AS n_anomaly,
       count(*) FILTER (WHERE EXISTS (
          SELECT 1 FROM corp_event e
          WHERE e.ticker = anomaly.ticker AND e.effective_date = anomaly.date)) AS n_matched
FROM anomaly;
-- 통과: n_matched::DOUBLE / n_anomaly >= bl('corp_event','detect_recall_min')
--       ∧ n_anomaly = bl_int('corp_event','base_price_anomaly_n')   -- 분모 자체의 회귀

-- EG8-P05 : 겹침 0 (허용오차 0, 등식)
SELECT count(*) AS n
FROM (SELECT ticker, date FROM stg_flow_daily_kiwoom
      INTERSECT SELECT ticker, date FROM stg_flow_split_daily);

-- EG8-P08 : 폐지 recall. 창 길이는 baseline (본문 −30거래일 리터럴 금지)
WITH d AS (SELECT ticker, delist_date FROM security WHERE delist_date IS NOT NULL),
     w AS (SELECT d.ticker, d.delist_date,
                  (SELECT c.date FROM trading_calendar c WHERE c.date <= d.delist_date
                   ORDER BY c.date DESC
                   LIMIT 1 OFFSET (SELECT bl_int('security','delist_signal_window_days'))) AS from_d
           FROM d)
SELECT count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM universe_daily u
         WHERE u.ticker = w.ticker AND u.date BETWEEN w.from_d AND w.delist_date
           AND (u.signal_liquidation OR u.signal_delist)))::DOUBLE
       / nullif(count(*), 0) AS recall
FROM w;
-- 통과: recall >= bl('security','delist_signal_recall_min')
```

---

### EG9 — 레짐 커버리지

- **분류** 폐기형 (분위별 커버 경고 항만 기록형)
- **적용** 격자 3종(`flow_daily`·`short_daily`·`credit_daily`) + `consensus_daily`·`dataset_profile`. 그 밖은 `skip(not_grid)`
- **잡는 위험** §0-3 「레짐 편향」·「컨센서스 선택편향」
- **첫 빌드** `skip(no_baseline)`
- **실패 시** 폐기

| ID | 술어 | 상수 |
|---|---|---|
| EG9-P01 | 측정일 3개에서 커버율 ≥ baseline | `<table>.coverage_probe_dates`·`<table>.coverage_min` |
| EG9-P02 | `src_omitted` 판정 셀의 로그 근거율 `evidence_rate` ≥ baseline | `<table>.evidence_rate_min` |
| EG9-P03 | 월별 커버율 ↔ 시장 월수익률 상관 절댓값 ≤ baseline | `<table>.coverage_return_corr_max`·`<table>.corr_min_months` |
| EG9-P04 | "미수집 → 0" 행 0 | — (등식) |
| EG9-P05 | `consensus_daily` 월별 커버 종목수 급락·급증 | `consensus_daily.cover_ratio_drop_max`·`cover_ratio_rise_max` |
| EG9-P06 | 시총 분위별 커버율 기록 (기록형) | — |

```sql
-- EG9-P01
WITH probe AS (
  SELECT UNNEST(CAST((SELECT value FROM _baseline
                      WHERE tbl='${T}' AND metric='coverage_probe_dates') AS DATE[])) AS d)
SELECT p.d,
       count(*) FILTER (WHERE g.fill_kind.kind = 'measured')::DOUBLE / nullif(count(*), 0) AS cover
FROM probe p JOIN ${T} g ON g.date = p.d
GROUP BY p.d;
-- 통과: min(cover) >= bl('${T}','coverage_min')

-- EG9-P02 : evidence_rate — src_omitted 인데 evidence 가 none 이면 근거 없는 0 이다.
--           근거 없는 구간은 not_collected 로 강등해야 하므로 게이트가 잡는다
SELECT count(*) FILTER (WHERE fill_kind.kind = 'src_omitted'
                          AND fill_kind.evidence IN ('shard_done','unit_ok'))::DOUBLE
       / nullif(count(*) FILTER (WHERE fill_kind.kind = 'src_omitted'), 0) AS evidence_rate
FROM ${T};
-- 통과: evidence_rate >= bl('${T}','evidence_rate_min')

-- EG9-P03 : 상관. 시장 = 자기시장 지수(KOSPI/KOSDAQ), 월수 하한을 baseline 이 정한다
WITH m AS (
  SELECT date_trunc('month', g.date) AS ym, u.market,
         count(*) FILTER (WHERE g.fill_kind.kind = 'measured')::DOUBLE
           / nullif(count(*), 0) AS cover
  FROM ${T} g JOIN universe_daily u ON u.ticker = g.ticker AND u.date = g.date
  GROUP BY ALL),
     r AS (
  SELECT date_trunc('month', date) AS ym,
         CASE index_name WHEN '코스피' THEN 'KOSPI' WHEN '코스닥' THEN 'KOSDAQ' END AS market,
         last(close_idx ORDER BY date) / first(close_idx ORDER BY date) - 1 AS ret
  FROM index_daily WHERE index_name IN ('코스피','코스닥') GROUP BY ALL)
SELECT m.market, count(*) AS n_months, abs(corr(m.cover, r.ret)) AS abs_corr
FROM m JOIN r USING (ym, market) GROUP BY m.market;
-- 통과: 모든 market 에서 n_months >= bl_int('${T}','corr_min_months')
--       ∧ abs_corr <= bl('${T}','coverage_return_corr_max')
--       (n_months 미달이면 그 market 은 skip(no_coverage) 로 기록)

-- EG9-P04 : "미수집을 0 으로 적재" 금지. 구현과 독립된 로그 축에서 재판정한다(§5-C8)
SELECT count(*) AS n
FROM ${T} g
WHERE g.${VALUE_COL} = 0
  AND NOT EXISTS (   -- 키움 샤드 축
        SELECT 1 FROM stg_shards_kiwoom s
        WHERE s.ticker = g.ticker AND s.status = 'done'
          AND g.date BETWEEN s.req_start AND s.req_end)
  AND NOT EXISTS (   -- KIS 유닛 축
        SELECT 1 FROM stg_units_kis u
        WHERE u.ticker = g.ticker AND u.status = 'ok' AND u.date = g.date)
  AND g.fill_kind.kind <> 'measured';
```

---

### EG-C — 소비자 계약

- **분류** 폐기형(테스트). SQL 술어가 아니라 **pytest 케이스**이며, 판정 결과만 `gates[]` 에 `EG-C` 1행으로 접어 넣는다
- **적용** 7단계(전 테이블 커밋 후). ②③⑤⑩ 은 1단계, ④⑤ 는 2단계, ⑨ 는 5단계, ⑥⑧ 은 6단계에서 선행 실행
- **잡는 위험** §0-3 「look-ahead (공개시점)」·「유니버스 정책 오염」·「생존편향」
- **첫 빌드** 실행
- **실패 시** 폐기

| 항 | 테스트 ID | 실행 방법 | 상수 |
|---|---|---|---|
| ① | `EGC-01` | 엔진 저장소 `backend/tests/test_bar_source_contract.py::BUILDERS` 에 `equity_duckdb` 등록 → 전 케이스 통과. **엔진은 별도 저장소** → equity CI 는 엔진을 서브모듈/설치 의존성으로 잡고, 없으면 `skip(engine_absent)` | — |
| ② | `EGC-02` | `v_universe(:d,'all')` 티커 집합 = `stg_listing_daily(d − lag)` 집합. `d` 는 `bl('universe_daily','contract_probe_dates')` 전건 | `universe_daily.contract_probe_dates` |
| ③ | `EGC-03` | 재상장 2종(036220·101970) → `Membership` 2구간. `coverage_gap` 은 구간을 끊지 않고, `UniverseQuery.end > backfill_end` 는 거절 | `security_span.respan_count` |
| ④ | `EGC-04` | 분할 픽스처(FX-2-001) 포함 run 의 누적수익률 = 조정가 손계산. 수량·현금 이중조정 0 | `adj_factor.backtest_return_tol` |
| ⑤ | `EGC-05` | 거래정지 섞인 다종목 `BarQuery` → `LoadStatus.OK` ∧ `dropped_rows > 0` | — |
| ⑥ | `EGC-06` | 샘플 팩터 4개(V01·F01·M01·G05)를 profile 랙 적용 상태에서 손계산과 대조. **G05 는 컨센서스 2관측점이 필요** → 커버 구간 밖이면 `skip(no_coverage)` | `<factor>.hand_calc_tol` |
| ⑦ | `EGC-07` | `:asof` 를 과거로 두면 그 뒤 공시·이벤트·판본이 안 보인다: `v_fin_latest(asof)` 결과의 `max(available_date) ≤ asof − lag` | — |
| ⑧ | `EGC-08` | `dataset_profile.recommended_lag_days` 를 +1 하면 컬럼군별로 뷰 결과가 바뀐다. **바뀌지 않으면 랙이 배선되지 않은 것** — 컬럼군마다 최소 1행 차이 요구 | — |
| ⑨ | `EGC-09` | `obs_month` 를 날짜 축으로 계산한 G05 ≠ `v_consensus` 로 계산한 G05. **wise 구간에서만 유효**(DESIGN §4-6) → v3 구간은 `skip(no_coverage)`. 차이 ≥ baseline 인 종목 수 ≥ 1 | `consensus_daily.obs_month_bias_min` |
| ⑩ | `EGC-10` | 폐지 909 중 무작위 20종목 포함 전 구간 `BarQuery` → OK ∧ 반환 종목 집합 = 요청 집합. **무작위 시드는 baseline 고정**(재현성) | `security.delist_sample_seed`·`delist_sample_n` |

> WORKFLOW §2 의 EG-C 항 번호는 ①②③④⑤ 다음에 ⑩ 이 오고 그 뒤 ⑥⑦⑧⑨ 다. 이 문서는 ①~⑩ 순서로 재배열했고 내용은 동일하다.

---

### 1-12. baseline metric 등록부 (`{table}.{metric}`)

| metric | 쓰는 게이트 | 등재 단계 | 측정 SQL 요지 |
|---|---|---|---|
| `trading_calendar.backfill_end` | EG1·EG3 | 1 | `max(date)` of `stg_listing_daily` |
| `trading_calendar.gap_days` | EG1 | 1 | gap 축 거래일 수 |
| `trading_calendar.fixture_date` | EG3-P05 | 1 | 픽스처 기준일(고정) — **미등재**: S06 `EG3_firm_mktcap` 은 `trading_calendar.asof_sample_dates` 5일 전부를 대조 날짜로 쓴다(§9 S06) |
| `security.delisted_total` | EG3-P10·P12 | 1 | KIS 폐지 ∪ listing 소멸 distinct ticker |
| `security.delist_conflict_max` | EG3-P11 | 1 | krx≠kis 폐지일 건수 |
| `security.delist_signal_recall_min` | EG8-P08 | 1 | recall 초회 측정 |
| `security.delist_signal_window_days` | EG8-P08 | 1 | 창 길이(거래일) |
| `security.delist_sample_seed`·`delist_sample_n` | EG-C ⑩ | 1 | 표본 고정 |
| `security_span.respan_count` | EG1·EG-C ③ | 1 | 구간 2개 이상 티커 수 |
| `corp_ticker.map_rate_min` | 1단계 통과 조건 | 1 | `corp_code NOT NULL` 비율 (분모 정의는 §5-A4) |
| `universe_daily.no_trade_run_k` | `status='suspended'` 판정 · EG3_universe `n_status_halt_mismatch` | 1 | 무거래 연속 임계 — S03B 제안 5(P6 중앙값 4 + 1), 서버 `no_trade_run_hist` 로 승인 |
| `universe_daily.adv_window_td` | `adv20_krw` 창 · EG3_universe `n_adv20_null_mismatch` | 1 | 컬럼 이름이 못박은 20 — SQL 리터럴 금지 규약의 통로일 뿐 조정 상수가 아니다 |
| `universe_daily.corp_action_lookback_sessions` | `no_trade_reason='corp_action_window'` 창의 뒤쪽 폭(`ca_win`) · EG3_universe `n_no_trade_reason_recompute_mismatch` | 1 | S03C 제안 **5** — adj_factor 의 가격 대조 창 뒤쪽(−5, P23′)과 같은 값. 길게 잡으면 사건 뒤 비유동 구간이 통째로 corp_action_window 로 넘어간다 |
| `universe_daily.corp_action_lookahead_sessions` | 같은 창의 앞쪽 폭 | 1 | S03C 제안 **45** — 감자·병합 정지는 적용일 **앞**에 놓인다. P23′ ok 적용일 오프셋 중앙값 13.5 · max 개별 40 · 성분 48. 서버 `no_trade_reason_counts` 로 48 승격 판단 |
| `universe_daily.admin_signal_window_sessions` | `no_trade_reason='admin'` 판정(지정 신호 D 전 n 세션) | 1 | S03C 제안 **5** — 관리종목 *상태*(해제까지 지속)가 아니라 *지정 직후* 국면만 admin 으로 부른다. 상태 자체는 `universe_policy.investable` 의 `NOT admin_state` 가 뺀다 |
| `universe_policy.liquid_top_pct` | `liquid` 임계 행 술어(`adv20_rank_pct >= 1 − v`)·`threshold_value` · EG3_policy `n_quantile_threshold_out_of_range` | 1 | 사용자 선택 상위 비율(S03B-2, 09-05 결정) 0.5 — 잰 값이 아니라 convention. (0, 1] 밖이면 폐기. 근거 P22′ 보통주 adv20 분위수 |
| `universe_policy.version` | `version` 컬럼 | 1 | 정책 집합 판본 문자열 — s03-v1 → s03b-v2 → s03b-v3(liquid 등재) → **s03c-v4**(liquid 에 `no_trade_reason <> 'illiquid'` flag 1행, 13행·quantile rule_seq 6) |
| `universe_daily.contract_probe_dates` | EG-C ② | 1 | 계약 검사 날짜 배열 |
| `price_daily.krx_kis_ratio_match_min` | EG8-P01 | 2 | 일치율 |
| `adj_factor.factor_product_tol` | EG3-P04 | 2 | 부동소수 허용오차 — **미등재**: 산출 정밀도 상수 `rules_s06.FACTOR_PRODUCT_TOL = 1e-12`(DOUBLE 역수 곱 반올림 1.1e-16 실측, §9 S06) |
| `adj_factor.price_match_tol_rel`·`price_match_tol_abs` | apply_date 판정 (a)(b)(c) · EG3_adj_factor | 2 | 조정 후 잔여 허용치 max(tol_rel × m, tol_abs), m = \|min(pf, 1/pf) − 1\| — 서버 1차 실측(09-05) 0.15 · 0.05 (§9 S06 2차) |
| `adj_factor.price_match_window_sessions`·`price_match_lookback_sessions` | apply_date 판정 (b)(c) · EG3_adj_factor | 2 | 창 [n0 − lookback, n0 + window] 세션 — 서버 1차 실측 최적일 오프셋 p10 −5 · p90 +27~+31.5 → 5 · 40 |
| `adj_factor.base_price_tol_rel` | S06-2 `krx_base_price` 후보 판정 · EG1 우변(신규 행 수) · EG3_adj_factor 기준가 분류 | 2 | \|`base_price_krw` / 직전 행 close − 1\| > tol 인 (ticker, date) 가 후보 — seed 0.002(기준가는 정확값). 절단본: 기준가 ≠ 직전 close 59 중 tol 안 14 는 전부 ETF 소액 분배락, 비ETF 9 는 전부 후보(min 0.0098). ★ 서버 실측 뒤 후보 밖 불일치 건수 확인 (§9 S06-2) |
| `adj_factor.base_match_window_sessions` | S06-2 (a) 사건 교체 매칭 · EG3_adj_factor 창 여유·`n_ok_without_base_price_event` | 2 | \|n(기준가 날) − n(단위 apply_date)\| ≤ 창 — seed 5. 절단본 3건 거리 0. ★ 서버: no_price_match 830 중 살아나는 건수·거리 분포로 조정 |
| `adj_factor.factor_product_tol_base` | S06-2 기준가 행 ok 판정(mktcap_neutral vs `krx_base_inconsistent`) · EG3-P04 기준가 행 허용오차 | 2 | \|price_factor × share_factor − 1\| ≤ tol — seed 0.01(호가단위 반올림·자기주식 신주 미배정 등 KRX 산식 잔여; 247540 \|0.2507 × 4 − 1\| = 0.0028). 절단본 곱 정확히 1.0. ★ 서버 unknown_krx 곱 분포로 경계 확인 |
| `corp_event.krx_share_change_tol`(S06-2 재사용) | S06-2 같은 날 주식수 변화비 S 판정(\|S − 1\| > tol 일 때만 share_factor = S) | 1 | S05 의 상수를 `_const` 로 복제 없이 읽는다(`corp_event.` 접두 키) |
| `adj_factor.adj_return_jump_max` | EG8-P02 | 2 | 점프 상한 |
| `adj_factor.adj_volume_ratio_band` | EG8-P03 | 2 | ok 이벤트 집합의 조정 거래량 20세션 중앙값 비(후/전) 중앙값 밴드 [1/k, k] — 방향 오류 탐지(3차, §9). `adj_volume_jump_max`(건별 하루 점프)는 폐기 |
| `adj_factor.asof_for_jump_check` | EG8-P02·P03 | 2 | 검사용 고정 asof |
| `adj_factor.backtest_return_tol` | EG-C ④ | 7 | 누적수익률 허용오차 |
| `corp_event.detect_recall_min`·`base_price_anomaly_n` | EG8-P04 | 2 | recall·분모 |
| `flow_daily.investor_sum_tol_krw` | EG3-P06 | 3 | 12주체 합 허용오차 |
| `<격자>.coverage_probe_dates`·`coverage_min` | EG9-P01 | 3 | 측정일·커버율 하한 |
| `<격자>.evidence_rate_min` | EG9-P02 | 3 | 근거율 하한 |
| `<격자>.coverage_return_corr_max`·`corr_min_months` | EG9-P03 | 3 | 상관 상한·최소 월수 |
| `<table>.threshold_EG7` | EG7 | 전 단계 | 격리 비율 상한 |
| `fin_std.rcept_lag_p99_days` | EG7-P04 | 4 | `rcept_dt − period_end` **범위 상한**(이름은 p99 로 등재됐지만 값은 백분위가 아니다 — §9 S12 참조). seed 1826 |
| `fin_std.period_end_lag_max_days` | `period_end` 후보 선택 | 4 | 후보 판정 상한. seed 200 |
| `fin_std.quarter_months`·`half_months`·`three_quarter_months` | `period_end` 후보 식 · `doc_acode` 11013 의 1Q/3Q 분해 | 4 | 3·6·9 — 회계 달력의 정의이고 SQL 리터럴 금지 규약의 통로(`universe_daily.adv_window_td` 와 같은 취급) |
| `fin_std.nonmatch_rate_gap_max` | EG6-P08 | 4 | 결산월별 무매칭률 비대칭 상한(초안은 `disclosure_version.` 접두, §9 S12) |
| `fin_std.asof_value_change_max` | EG5c-P02 | 4 | 과거값 변경 허용 건수 |
| `v_fin_latest.asof_sample_dates`·`asof_sample_tickers` | EG5c-P01 | 4 | as-of 표본 |
| `disclosure_version.misjudge_rate_max` | EG6-P07 | 4 | 그룹 오판율 상한 — **미등재**(사람이 라벨한 표본이 있어야 재는 값이라 코드가 판정하지 않는다, §9 S11) |
| `disclosure_version.deadline_days_annual`·`deadline_days_interim` | `legal_deadline`·`delay_days` | 4 | 90·45(자본시장법 §159·§160). 규범이라 재측정 대상 아님 |
| `disclosure_version.date_check_near_days` | `date_check='off_2_7d'` 경계 | 4 | 7 — 어휘 라벨이 못박은 값, SQL 리터럴 금지 규약의 통로 |
| `disclosure_version.link_rate_min` | EG6-P05 (E-G6a) | 4 | 링크 성립률. 초안 키는 `correction_link.` 였다(§9 S11) |
| `disclosure_version.date_exact_rate` | EG6-P06 (E-G6b) | 4 | 기록형 |
| `disclosure_version.reach_rate_min` | EG8-P09 (E-G7) | 4 | `rm` 정정 플래그 도달률. 분모는 **원본만**(§9 S11) |
| `disclosure_version.misjudge_rate_max` | EG6-P07 | 4 | 그룹 오판율 상한 |
| `disclosure_version.nonmatch_rate_gap_max` | EG6-P08 | 4 | 비대칭 상한 |
| `correction_link.link_rate_min` | EG6-P05 (E-G6a) | 4 | 링크 성립률 |
| `correction_link.date_exact_rate` | EG6-P06 (E-G6b) | 4 | 기록형 |
| `correction_link.reach_rate_min` | EG8-P09 (E-G7) | 4 | 도달률 |
| `consensus_daily.v3_wise_value_tol_rel` | EG8-P07 | 5 | 두 원천 값이 '같다' 고 볼 상대 허용오차(S17 신설, §9) |
| `consensus_daily.v3_wise_match_min` | EG8-P07 | 5 | 겹침 일치율 |
| `consensus_daily.cover_ratio_drop_max`·`cover_ratio_rise_max` | EG9-P05 | 5 | 급락·급증 |
| `consensus_daily.obs_month_bias_min` | EG-C ⑨ | 5 | 편의 하한 |
| `<factor>.hand_calc_tol` | EG-C ⑥ | 7 | 손계산 허용오차 |
| `factor_readiness.ready_min` | EG10 | 6 | ready 팩터 수 하한. **미등재**(사람 승인 대기) — 초안의 `<table>.material_coverage_min` 은 필드 단위 커버율 하한이었으나 §9 S19·S20 에서 **팩터 단위 판정**으로 바뀌었다: 커버율 0 은 `dataset_profile` 이 그대로 싣고 EG10 이 `blocked(no_observations)` 로 옮긴다 |
| `security.pre_delist_price_days`·`pre_delist_price_min` | EG15 (§6) | 1 | 폐지 전 가격 존재 |
| `security_span.respan_verified_n` | EG16 (§6) | 1 | 검증된 재상장 수 |

---

## 2. 테이블별 게이트 매트릭스

기호: **●** 적용 · **skip(사유)** · **—** 해당없음 · 괄호 안은 §1 술어 ID.
`EG7` 은 전 테이블 실행이나 격리 사유가 없는 테이블은 위반 0 으로 통과한다.

| # | 테이블 | 단계 | EG0 | EG1 | EG2 | EG3 | EG4 | EG5 | EG6 | EG7 | EG8 | EG9 | EG-C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `corp` | 1 | ●(P01–P04) | ●(§3-①) | — 비팩트 | ●(P01,P07,P08) | ●(FX-1-004) | ●(a) | ●(P04) | ● | — | skip(not_grid) | — |
| 2 | `security` | 1 | ● | ●(§3-②) | — 비팩트 | ●(P01,P07,P10,P11,P13) | ●(FX-1-003,006,007) | ●(a) | ●(P04) | ●(P03→§5 삭제 판정) | ●(P08) | skip(not_grid) | — |
| 3 | `security_span` | 1 | ● | ●(§3-③) | — 비팩트 | ●(P01,P03,P12) | ●(FX-1-001,002,008) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | ●③⑩ |
| 4 | `corp_ticker` | 1 | ● | ●(§3-④) | — 비팩트 | ●(P01,P02,P07) | ●(FX-1-003,005) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 5 | `trading_calendar` | 1 | ● | ●(§3-⑤) | — 비팩트 | ●(P01) | ●(FX-1-009) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 6 | `index_daily` | 1 | ● | ●(§3-⑥) | ●(P01–P04) | ●(P01) | ●(FX-1-010) | ●(a) | skip(no_multi_version) | ● | skip(no_cross_source) | skip(not_grid) | — |
| 7 | `universe_daily` | 1 | ● | ●(§3-⑦) | ●(P01–P04) | ●(P01,P07,P09,P13) | ●(FX-1-006,011,012,013,014,015,016) | ●(a,c) | skip(no_multi_version) | ● | ●(P08 경유) | skip(not_grid) | ●②⑤⑩ |
| 8 | `universe_policy` | 1 | ● | skip(declaration_table) | — 비팩트 | ●(P01,P13) | ●(FX-1-017) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 9 | `price_daily` | 2 | ● | ●(§3-⑧) | ●(P01–P04) | ●(P01,P07) | ●(FX-2-001,002,005,006) | ●(a) | skip(no_multi_version) | ●(P01) | ●(P01) | skip(not_grid) | ●①⑤⑩ |
| 10 | `corp_event` | 2 | ● | ●(§3-⑨) | ●(P01–P04, 축=announce) | ●(P01,P07,P13) | ●(FX-2-003,008) | ●(a) | skip(no_multi_version) | ●(P08) | ●(P04) | skip(not_grid) | ●④ |
| 11 | `adj_factor` | 2 | ● | ●(§3-⑩) | ●(P01–P04, 축=announce) | ●(P01,P04,P13) | ●(FX-2-001,003,004,007) | ●(a) | skip(no_multi_version) | ●(P02) | ●(P01,P02,P03) | skip(not_grid) | ●④ |
| 12 | `flow_daily` | 3 | ● | ●(§3-⑪) | ●(P01–P04) | ●(P01,P06,P07,P13) | ●(FX-3-001,002,003,004,005,006,007) | ●(a) | skip(no_multi_version) | ●(P06,P07) | ●(P05) | ●(P01–P04) | — |
| 13 | `short_daily` | 3 | ● | ●(§3-⑪) | ●(P01–P04) | ●(P01,P07,P13) | ●(FX-3-001,003,004) | ●(a) | skip(no_multi_version) | ●(P06,P07) | ●(P06) | ●(P01–P04) | — |
| 14 | `credit_daily` | 3 | ● | ●(§3-⑪) | ●(P01–P04) | ●(P01,P07,P13) | ●(FX-3-008) | ●(a) | skip(no_multi_version) | ●(P06,P07) | skip(no_cross_source) | ●(P01,P03,P04) · P02 skip(no_log_axis) | — |
| 15 | `fin_std` | 4 | ● | ●(§3-⑫) | ●(P01–P05) | ●(P01,P13) + `EG3_fin_std` | ●(FX-4-001…008) | ●(a) · c 는 뷰(S12 후속) | ●(P08) | ●(P04 + non_krw·period_unresolved·duplicate_vintage) | skip(no_baseline)→D9 승격 시 ● | skip(not_grid) | ●⑥⑦ |
| 16 | `disclosure_version` | 4 | ● | ●(§3-⑬) | ●(P01–P04) | ●(P01) + `EG3_disclosure_version` | ●(FX-4-004,005,009,010) | ●(a) | ●(P05,P06) · P07 미등재 · P08 → `fin_std` | ●(격리 `rcept_dt_missing` 뿐 — P05 `no_label` 폐기) | ●(P09) | skip(not_grid) | ●⑦ |
| 17 | ~~`correction_link`~~ (16행에 흡수) | 4 | — | — | — | — | — | — | — | — | — | — | — |
| 18 | `holder_daily` | 4B | ● | ●(§3-⑮) | ●(P01–P04) | ●(P01,P07) | ●(FX-4B-003) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 19 | `ownership_snapshot` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-005) | ●(a) | skip(no_multi_version) | ●(P07) | — | skip(not_grid) | — |
| 20 | `shares_outstanding` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-006) | ●(a) | skip(no_multi_version) | ● | — (KRX 대조는 EG3 기록형) | skip(not_grid) | — |
| 21 | `treasury_stock` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-001) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 22 | `audit_opinion` | 4B | ● | ●(§3-⑰) | ●(P01–P04) | ●(P01,P13) | ●(FX-4B-002) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 23 | `dividend_event` | 4B | ● | ●(§3-⑱) | ●(P01–P04) | ●(P01) | ●(FX-4B-004) | ●(a) | skip(no_multi_version) | ●(P07) | — | skip(not_grid) | — |
| 24 | `consensus_daily` | 5 | ● | ●(§3-⑲) | ●(P01–P03) · P04 skip(profile 없음) | ●(P01,P07,P13) | ●(FX-5-001…006) | ●(a) · c 는 §9 S17(뷰가 `ASOF_VIEWS` 밖) | ●(P01,P02,P03) | ●(P07) | ●(P07) | ●(P05) · P06 은 §9 S17 대용 | ●⑥⑨ |
| 25 | `opinion_daily` | 5 | ● | ●(§3-⑳) | ●(P01–P04) | ●(P01,P07) | ●(FX-5-007) | ●(a) | ●(P01,P03) | ● | skip(no_baseline) | ●(P06) | — |
| 26 | `opinion_broker_daily` | 5 | ● | ●(§3-㉑) | ●(P01–P04) | ●(P01,P07) | ●(FX-5-008) | ●(a) | skip(no_multi_version) | ● | — | ●(P06) | — |
| 27 | `dataset_profile` | 6 | ● | skip(declaration_table) | skip(dimension_table) → `EG2_dataset_profile`(P04,P06,P07,P08 + 어휘·범위) | ●(P01) | ●(FX-6-001…009,016…018) | ●(a) | skip(no_multi_version) | ● | — | ●(P06 기록형, 시총 분위) | ●⑥⑧ |
| 28 | `factor_readiness` | 6 | ● | skip(declaration_table) | skip(dimension_table) | ●(P01) | ●(FX-6-010,011,013,015,019) | ●(a) | skip(no_multi_version) | ● | — | — | — · 판정은 **EG10**(§6) |
| 29 | `price_adj_daily` | 2 | ● | ●(§3-㉓ 항등식) | ●(P01–P04) | ●(P01) + `EG3_price_adj_daily` | ●(FX-2-011…017) | ●(a) | skip(no_multi_version) | ●(격리 사유 없음 — 항상 0) | — | skip(not_grid) | — · `price.adj_close` 는 §7 소비자 계약 |

**뷰 7종**(`v_universe`·`v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_fin_latest`·`v_consensus`·`v_firm_mktcap`; 전방 조정 2종 `v_adj_price_fwd`·`v_adj_volume_fwd` 는 S23 부터 표 `price_adj_daily`(29행)와 **매 빌드 대조**되므로 뷰 공백이 아니다)은 테이블이 아니므로 EG0~EG9 매트릭스에 행이 없고, **EG5c·EG-C·EG11(§6)** 이 담당한다. 이것이 현재 명세의 가장 큰 공백이다(§5-A6). 단 EG11·EG5c 의 고정 표본 규약은 키가 (as_of, ticker, **date**)라 일별 date 축이 있는 뷰만 받는다(`catalog.ASOF_VIEWS`) — `v_consensus` 는 그래서 밖이고 결정성은 S17 e2e 테스트가 대신 본다(§9 S17).

---

## 3. EG1 등식 전수

각 항: **좌변**(equity 산출) − **우변**(입력 재계산) − `n_dedup` − `Σ n_reject` = 0. 허용오차 0.

```sql
-- 공통 꼬리. 모든 등식에 붙는다
CREATE OR REPLACE MACRO eg1_tail(t) AS (
  coalesce((SELECT n_dedup FROM _eq_meta WHERE "table" = t LIMIT 1), 0)
+ coalesce((SELECT sum(n) FROM _reject_counts WHERE tbl = t), 0));
```

### ① `corp`
```sql
SELECT (SELECT count(*) FROM corp)
     - ((SELECT count(DISTINCT corp_code) FROM stg_corp_map) - eg1_tail('corp')) AS delta;
-- 보조: acc_mt NULL 0 · stg_fin.corp_code ⊆ corp_map
SELECT (SELECT count(*) FROM corp WHERE fiscal_month IS NULL) AS n_null_fiscal,
       (SELECT count(*) FROM (SELECT DISTINCT corp_code FROM stg_fin
                              EXCEPT SELECT corp_code FROM stg_corp_map)) AS n_unmapped;
```

### ② `security`
```sql
SELECT (SELECT count(*) FROM security)
     - ((SELECT count(*) FROM (SELECT DISTINCT ticker FROM stg_listing_daily
                               UNION SELECT DISTINCT ticker FROM stg_etf_price_daily))
        - eg1_tail('security')) AS delta;
-- 서로소 (P8)
SELECT count(*) AS n_overlap FROM (
  SELECT DISTINCT ticker FROM stg_listing_daily
  INTERSECT SELECT DISTINCT ticker FROM stg_etf_price_daily);
```

### ③ `security_span`
```sql
-- (a) Σ n_days = 존재 (ticker, date) 수
SELECT (SELECT sum(n_days) FROM security_span)
     - (SELECT count(*) FROM (SELECT ticker, date FROM stg_listing_daily
                              UNION SELECT ticker, date FROM stg_etf_price_daily)) AS delta_days;
-- (b) 비중첩 = EG3-P03
-- (c) 재상장 구간 수
SELECT (SELECT count(*) FROM (SELECT ticker FROM security_span GROUP BY ticker HAVING count(*) >= 2))
     - (SELECT bl_int('security_span','respan_count')) AS delta_respan;
-- (d) listing 완결성: distinct date = 거래일 수
SELECT (SELECT count(DISTINCT date) FROM stg_listing_daily)
     - (SELECT count(*) FROM trading_calendar WHERE calendar_source = 'krx_index') AS delta_cal;
-- (e) last_date 상한
SELECT count(*) AS n_over
FROM security_span WHERE last_date > (SELECT bl_date('trading_calendar','backfill_end'));
```

### ④ `corp_ticker`
```sql
SELECT (SELECT count(*) FROM corp_ticker)
     - ((SELECT count(*) FROM security) - eg1_tail('corp_ticker')) AS delta;
SELECT count(*) AS n_bad_basis FROM corp_ticker
WHERE link_basis NOT IN ('isin8','corp_map','none');
```

### ⑤ `trading_calendar`
```sql
WITH be AS (SELECT bl_date('trading_calendar','backfill_end') AS d)
SELECT (SELECT count(*) FROM trading_calendar)
     - (SELECT count(*) FROM (
          SELECT DISTINCT date FROM stg_index_daily
          UNION SELECT DISTINCT date FROM stg_flow_split_daily WHERE date > (SELECT d FROM be)
          UNION SELECT DISTINCT date FROM stg_credit_daily     WHERE date > (SELECT d FROM be)
       )) AS delta;
-- prev_td/next_td 체인 무결성 (§6 EG17 에서 강화)
SELECT count(*) AS n_chain FROM trading_calendar a
WHERE a.next_td IS DISTINCT FROM (SELECT min(b.date) FROM trading_calendar b WHERE b.date > a.date);
```

### ⑥ `index_daily`
```sql
SELECT (SELECT count(*) FROM index_daily)
     - ((SELECT count(*) FROM stg_index_daily) - eg1_tail('index_daily')) AS delta;
```

### ⑦ `universe_daily`
```sql
-- v1.0 정정(§9): coverage_gap 행을 만들지 않으므로(DESIGN v1.2 GAP-21) gap_cells 항이 없다.
-- 캘린더에 gap 축이 없어(P16) 캘린더 max = backfill_end 이고 '≤ backfill_end' 는 항등이다.
-- 우변은 S02 가 저장한 n_days 의 합 — 격자 조인을 다시 세면 항진명제라 쓰지 않는다.
SELECT (SELECT count(*) FROM universe_daily)
     - ((SELECT coalesce(sum(n_days), 0) FROM security_span)
        - eg1_tail('universe_daily')) AS delta;
-- 구현: rules_s03.UNIVERSE_DAILY.eg1_rhs_sql. 두 입력 판본이 어긋나면(구간이 캘린더보다 길다)
-- 격자가 작아져 delta < 0 으로 깨진다 — 의도된 실패.
```

### ⑧ `price_daily`
```sql
SELECT (SELECT count(*) FROM price_daily)
     - ((SELECT count(*) FROM stg_price_daily) + (SELECT count(*) FROM stg_etf_price_daily)
        - eg1_tail('price_daily')) AS delta;
SELECT count(*) AS n_overlap FROM (
  SELECT ticker, date FROM stg_price_daily
  INTERSECT SELECT ticker, date FROM stg_etf_price_daily);
```

### ⑨ `corp_event`
```sql
-- 선언 원천은 _reg_corp_event_source(view_name, predicate) 가 열거한다.
-- 러너가 그 표를 돌며 우변을 합산하고, dedup 은 _meta.n_dedup 이 낸다
WITH src AS (
  SELECT s.view_name, (SELECT count(*) FROM query_table(s.view_name)) AS n
  FROM _reg_corp_event_source s)
SELECT (SELECT count(*) FROM corp_event)
     - ((SELECT sum(n) FROM src) - eg1_tail('corp_event')) AS delta;
-- dedup 정의의 재계산 (선언 dedup 축 = ticker, event_type, effective_date)
SELECT count(*) AS n_dup FROM (
  SELECT ticker, event_type, effective_date, count(*) c
  FROM corp_event GROUP BY ALL HAVING c > 1);
```

### ⑩ `adj_factor`
```sql
-- S06-2(09-05): 우변 = corp_event 계수 대상 + KRX 기준가 신규 행((b) unknown_krx · (d) unknown_price_only).
-- 기준가 후보(bpc)는 price_daily·security·security_span 에서 독립 정의(rules_s06._BASE_PRICE_CANDIDATES_CTE):
--   |base_price_krw / 직전 행 close − 1| > base_price_tol_rel, 비ETF, 구간 첫날 아님.
-- (c) 재발견(주식수 불변 ∧ 직전 행 reference)은 행이 없고, (a) 로 사건에 붙은 날짜는 산출의
-- (ticker, apply_date, apply_basis='krx_base_price', MVP 유형) 로 뺀다 — 산출을 읽는 유일한 항.
WITH bpc AS (...)
SELECT (SELECT count(*) FROM adj_factor)
     - ((SELECT count(*) FROM corp_event
         WHERE event_type IN (SELECT value FROM _reg_vocab WHERE domain = 'factor_bearing_event'))
        + (SELECT count(*) FROM bpc b
           WHERE NOT b.is_etf AND NOT b.span_start
             AND NOT (b.share_ratio IS NULL AND b.prev_kind = 'reference')
             AND NOT EXISTS (SELECT 1 FROM adj_factor o
                             WHERE o.ticker = b.ticker AND o.apply_date = b.date
                               AND o.apply_basis = 'krx_base_price'
                               AND o.event_type IN (SELECT value FROM _reg_vocab
                                                    WHERE domain = 'factor_bearing_event')))
        - eg1_tail('adj_factor')) AS delta;
```

### ⑪ `flow_daily` · `short_daily` · `credit_daily`
```sql
-- (a) 격자 행수
WITH grid AS (
  SELECT u.date, u.ticker FROM universe_daily u
  WHERE u.status IN ('listed','suspended') AND u.sec_type <> 'etf')
SELECT (SELECT count(*) FROM ${T}) - (SELECT count(*) FROM grid) AS delta_grid;
--   주의: src 를 PK 에 포함하는 결합 테이블(flow_daily·short_daily)은 좌변이
--         count(DISTINCT (date, ticker)) 여야 한다. §5-B4 참조

-- (b) 원장 보존 등식 (소스별). "격자 매핑 + pre_calendar + off_grid"
SELECT (SELECT count(*) FROM stg_flow_daily_kiwoom)
     - ((SELECT count(*) FROM flow_daily
         WHERE src = 'kiwoom' AND fill_kind.kind = 'measured')
      + (SELECT coalesce(sum(n),0) FROM _reject_counts
         WHERE tbl = 'flow_daily' AND reason IN ('pre_calendar','off_grid'))) AS delta_src;

-- (c) 키움 13주체 컬럼 전부 계승 (flow_daily)
SELECT count(*) AS n_missing FROM (
  SELECT value AS c FROM _reg_vocab WHERE domain = 'kiwoom_investor_column'
  EXCEPT
  SELECT column_name FROM duckdb_columns() WHERE table_name = 'flow_daily');

-- (d) pre_calendar 격리 건수 회귀 (원천별)
SELECT reason, n FROM _reject_counts WHERE tbl = '${T}';
-- 통과: n = bl_int('${T}', 'reject_' || reason)
```

### ⑫ `fin_std`
```sql
SELECT (SELECT count(*) FROM fin_std)
     - ((SELECT count(*) FROM (
           SELECT DISTINCT corp_code, bsns_year, reprt_code, fs_div
           FROM stg_fin WHERE account_std))
        - eg1_tail('fin_std')) AS delta;
```

### ⑬ `disclosure_version`
```sql
WITH base AS (
  SELECT d.*, regexp_replace(d.report_nm, '^\[[^\]]*\]', '') AS nm_clean
  FROM stg_disclosure d),
     periodic AS (
  SELECT * FROM base b
  WHERE EXISTS (SELECT 1 FROM _reg_vocab v
                WHERE v.domain = 'periodic_report' AND b.nm_clean LIKE v.value || '%')
    AND NOT EXISTS (SELECT 1 FROM _reg_vocab v
                    WHERE v.domain = 'excluded_report' AND b.nm_clean LIKE '%' || v.value || '%'))
SELECT (SELECT count(*) FROM disclosure_version)
     - ((SELECT count(*) FROM periodic) - eg1_tail('disclosure_version')) AS delta;
-- 기간 라벨 없는 행은 _reject/no_label 로 세어져 eg1_tail 에 포함된다
```

### ⑭ `correction_link` (DOC_DESIGN §8.1)
```sql
WITH base AS (
  SELECT d.*, regexp_extract(d.report_nm, '^\[([^\]]*)\]', 1) AS prefix,
         regexp_replace(d.report_nm, '^\[[^\]]*\]', '') AS nm_clean
  FROM stg_disclosure d WHERE d.ticker <> '')
SELECT (SELECT count(*) FROM correction_link)
     - ((SELECT count(*) FROM base b
         WHERE b.prefix IN ('기재정정','첨부정정')
           AND EXISTS (SELECT 1 FROM _reg_vocab v
                       WHERE v.domain = 'periodic_report' AND b.nm_clean LIKE v.value || '%')
           AND NOT EXISTS (SELECT 1 FROM _reg_vocab v
                           WHERE v.domain = 'excluded_report'
                             AND b.nm_clean LIKE '%' || v.value || '%'))
        - eg1_tail('correction_link')) AS delta;
-- ZIP 있는 것만 모집단으로 삼으면 2015~2019 정정 원본이 "정정 없음"이 된다(DOC_DESIGN 결정 ④)
SELECT count(*) FILTER (WHERE date_check = 'no_zip') AS n_no_zip FROM correction_link;
```

### ⑮ `holder_daily`
```sql
SELECT (SELECT count(*) FROM holder_daily)
     - ((SELECT count(*) FROM stg_holder_elestock) + (SELECT count(*) FROM stg_holder_majorstock)
        - eg1_tail('holder_daily')) AS delta;
```

### ⑯ `ownership_snapshot` · `shares_outstanding` · `treasury_stock`
```sql
-- row_kind='aggregate' 제외는 이 3테이블(+ stg_hyslr·stg_shares·stg_tesstk)에만 있다
SELECT (SELECT count(*) FROM ownership_snapshot)
     - ((SELECT count(*) FROM (
           SELECT DISTINCT corp_code, bsns_year, reprt_code, nm
           FROM stg_hyslr WHERE row_kind <> 'aggregate'))
        - eg1_tail('ownership_snapshot')) AS delta;

SELECT (SELECT count(*) FROM shares_outstanding)
     - ((SELECT count(*) FROM (
           SELECT DISTINCT corp_code, bsns_year, reprt_code, se
           FROM stg_shares WHERE row_kind <> 'aggregate'))
        - eg1_tail('shares_outstanding')) AS delta;

SELECT (SELECT count(*) FROM treasury_stock)
     - ((SELECT count(*) FROM (
           SELECT DISTINCT corp_code, bsns_year, reprt_code,
                  acqs_mth1, acqs_mth2, acqs_mth3, stock_knd
           FROM stg_tesstk WHERE row_kind <> 'aggregate'))
        - eg1_tail('treasury_stock')) AS delta;
```

### ⑰ `audit_opinion`
```sql
SELECT (SELECT count(*) FROM audit_opinion)
     - ((SELECT count(*) FROM stg_audit) - eg1_tail('audit_opinion')) AS delta;
```

### ⑱ `dividend_event`
```sql
SELECT (SELECT count(*) FROM dividend_event)
     - ((SELECT count(*) FROM (
           SELECT DISTINCT corp_code, bsns_year, reprt_code, stock_knd
           FROM stg_dividend))
        - eg1_tail('dividend_event')) AS delta;
--   stg_dividend 에는 row_kind **열이 없다**(rules_dart.py:136 은 hyslr·shares·tesstk 3개만).
--   초안의 `WHERE row_kind IS DISTINCT FROM 'aggregate'` 는 Binder 오류로 실행 자체가 안 되므로
--   술어를 뺀 위 형태가 정본이다(S16 구현, §9). WORKFLOW §2-1 의 "비집계" 표현도
--   dividend_event 에 대해 성립하지 않는다 → §5-B5
```

### ⑲ `consensus_daily`
```sql
WITH wise AS (
  SELECT DISTINCT ticker, date_trunc('month', obs_date) AS obs_month, target_period, metric
  FROM stg_consensus_monthly),
     v3 AS (
  SELECT DISTINCT r.ticker, date_trunc('month', r.date) AS obs_month, r.target_period, m.metric
  FROM stg_v3_revision_daily r
  CROSS JOIN (SELECT value AS metric FROM _reg_vocab WHERE domain = 'v3_metric') m
  WHERE  -- unpivot 대상 컬럼이 NULL 이 아닌 조합만
    CASE m.metric WHEN 'revenue' THEN r.revenue WHEN 'op' THEN r.op WHEN 'ni' THEN r.ni
                  WHEN 'eps' THEN r.eps WHEN 'per' THEN r.per WHEN 'bps' THEN r.bps
                  WHEN 'pbr' THEN r.pbr WHEN 'roe_pct' THEN r.roe_pct END IS NOT NULL)
SELECT (SELECT count(*) FROM consensus_daily)
     - (((SELECT count(*) FROM wise) + (SELECT count(*) FROM v3)) - eg1_tail('consensus_daily'))
       AS delta;
```

### ⑳ `opinion_daily`
```sql
SELECT (SELECT count(*) FROM opinion_daily)
     - ((SELECT count(*) FROM stg_analyst_summary)
      + (SELECT count(*) FROM (SELECT DISTINCT ticker, date FROM stg_v3_analyst_opinions))
        - eg1_tail('opinion_daily')) AS delta;
```

### ㉑ `opinion_broker_daily`
```sql
SELECT (SELECT count(*) FROM opinion_broker_daily)
     - ((SELECT count(*) FROM stg_analyst_broker) - eg1_tail('opinion_broker_daily')) AS delta;
```

### ㉒ `dataset_profile` · `factor_readiness` · `universe_policy`
```sql
-- 행수 등식 없음 → skip(declaration_table) (`EquityTable.declaration_table=True`).
--   `dataset_profile`  : 행수 = 선언 필드 수(`rules_s19.owned_fields()`). 대신 EG2_dataset_profile
--                        이 P04/P06/P07 + 어휘·범위를 보고, EG9 가 시총 분위 커버율을 기록한다.
--   `factor_readiness` : 행수 = FACTORS.md 정본 54(`rules_s20.FACTORS`). 대신 EG10 이 전수·사유·
--                        소유자·시작일 + **ready 재료 컬럼 실물 실재**를 본다.
-- 둘 다 `_meta.n_src` 는 0 이다(EG1 이 안 도니 우변이 없다) — 행수는 gates[].metrics 가 남긴다.
```

---

### ㉓ `price_adj_daily`

```
count(price_adj_daily) = count(price_daily)
```

**항등식이다** — 조정은 가격 행 하나하나에 대한 순수 함수라 행이 늘거나 줄지 않고, 격리 사유가 없어 우변에서 뺄 것도 없다(`reject_reasons=()`, EG7 은 항상 0 으로 통과). 등식이 이렇게 약한 대신 값 축을 `EG3_price_adj_daily` 가 **독립 재계산**으로 전건 대조한다: 산출은 ASOF JOIN + 창 누적곱, 게이트는 범위 조인 + GROUP BY 집계로 같은 값을 만들어 값 7축(`adj_open`·`adj_high`·`adj_low`·`adj_close`·`adj_volume_shr`·`cum_price_factor`·`cum_share_factor`)을 비교하고, `n_factors_applied`·`n_unadjusted_events`·`available_date` 도 따로 재계산한다. `n_factors_applied` 은 **완전 일치**(`n_factor_count_mismatch`)와 **방향 있는 초과**(`n_cross_span_factor`) 둘로 센다 — 계수 값이 1 인 사건이 새거나 빠지면 값 축은 안 움직이고 이 수만 어긋난다.

---

## 4. 픽스처 카탈로그

파일: `data/equity/fixtures/<table>.json`. ID 규약 `FX-<단계>-<3자리>`.
**기대값 출처 규칙(EG4-P03)**: `stg_v3_revision_compare` · `stg_v3_consensus_annual` · `stg_consensus_monthly` 의 lookback 계열 컬럼에서 나온 값은 무효다. 컨센서스 리비전 기대값은 **서로 다른 두 관측점의 원본 값에서 손계산**한다.

### 1단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-1-001 | `security_span` | `ticker='036220'` | `count(span_seq)` | doc:DESIGN §10 P8 | 구간 2개, 첫 구간 `last_date`·둘째 `first_date` 가 P8 기록과 일치 |
| FX-1-002 | `security_span` | `ticker='101970'` | `count(span_seq)` | doc:DESIGN §10 P8 | 동일 |
| FX-1-003 | `corp_ticker` | `isin8` of `003540` | `count(*)`, `count(*) FILTER(is_common)` | doc:DESIGN §0-3 MS-03 | 1:N 그룹, 보통주 정확히 1 |
| FX-1-004 | `corp` | `corp_code` of 3월 결산 1사 | `fiscal_month`, `fiscal_month_basis` | stage:`stg_company.acc_mt` | `= 3`, basis `current_snapshot` |
| FX-1-005 | `corp_ticker` | `ticker IN ('900050','900060')` | `isin8`, `corp_code` | doc:SPEC §2-17 | 비KR7 → 각자 단독 corp, 두 행의 `corp_code` 상이 |
| FX-1-006 | `security`·`universe_daily` | `ticker='0001A0'` | `typeof(ticker)`, `length(ticker)` | hand | `VARCHAR`, `6` — 정수 캐스팅 금지의 유일 방어 |
| FX-1-007 | `security` | 우선주 폐지 1종(81 중) | `delist_date`, `delist_date_basis` | stage:`stg_delisted_master.lstg_abol_dt` | NOT NULL |
| FX-1-008 | `security_span` | 폐지 1종 | `last_date`, `end_reason` | stage:`stg_listing_daily` 마지막 존재일 | `end_reason='delisted'`, `last_date` = 마지막 존재일 |
| FX-1-009 | `trading_calendar` | `date` = 연휴 직후 1일 | `prev_td` | stage:`stg_index_daily` | 직전 거래일 |
| FX-1-010 | `index_daily` | (`코스피`, 고정일) | `close_idx` | stage:`stg_index_daily.close_idx` | 1:1 사본 |
| FX-1-011 | `universe_daily` | backfill_end × 살아있는 1종(005930) + 그 다음 날 | `status` | hand | backfill_end 행 `listed`, 2026-08-21 행 **부재**(NULL 기대) — coverage_gap 행을 만들지 않는다(§9 정정, GAP-21) |
| FX-1-012 | `universe_daily` | (`000030`, 2019-02-12·01-09·01-08) + k 경계 (01-15 / 01-14) + (`101970`, 2014-08-20·08-21) | `no_trade_run`, `status` | hand(가격 행 카운트) | 22 · 1 · 0 / run 5 = k → suspended, run 4 → listed / 해제일 무거래 run 49 → suspended, 첫 거래일 0 (S03B, §9) |
| FX-1-013 | `universe_daily` | 정지 지정 → 거래 재개 사례 1 | `halt_state` at 지정일·재개일 | doc:DESIGN §10 P6·P12 | 지정일 true, 첫 `volume>0` 일 false |
| FX-1-014 | `universe_daily` | 정지+해제 동일일 1 (1,214 중) | `halt_state`, `signal_halt`, `signal_halt_release` | doc:P12 | 당일 양쪽 신호 true, `halt_state` 규칙대로 |
| FX-1-015 | `universe_daily` | KOSDAQ 관리종목 소속부 1 | `admin_state`, `admin_state_basis` | stage:`stg_listing_daily.sect_tp` | true / `measured` |
| FX-1-016 | `universe_daily` | 정리매매 개시 1 (349 중) | `liquidation_window` | doc:P6 | 개시일~`delist_date` true |
| FX-1-017 | `universe_policy` | (`all`, 1) + S03B (`common-stock`, 1·2)·(`investable`, 3·4) + S03B-2 (`liquid`, 1·4·6) + S03C (`liquid`, 5)·(`investable`, 5 부재) | `predicate`, `threshold_kind`, `threshold_value`, `universe_id`, `measured_at` | hand · doc:FIELD_MAP §1 | 선언표 `all` 1행(`TRUE`·`flag`·`krx.all`) + `krx.common-stock`(`sec_type = 'common'`·`status = 'listed'`) + investable `NOT admin_state`·flag + `liquid`(k~s): rule 5 `no_trade_reason <> 'illiquid'`·`flag`(S03C), rule 6 `adv20_rank_pct >= 0.5`·`quantile`·`threshold_value` 0.5(= baseline `liquid_top_pct`)·`measured_at` NULL, rule 1 `krx.liquid`, rule 4 = investable 4행 그대로, investable rule 5 는 부재(§9 S03B-2·S03C) |
| S03B-2 `adv20_rank_pct` | `universe_daily` | (`003540`, 2018-05-03) · (`000660`, 2026-08-20) · NULL 6종(우선주·ETF·외국주·정지·창 미달·S03C 기업행위 창) | `adv20_rank_pct` | hand(파이썬 독립 cume_dist) | 2026-08-20 모집단 8 → 000660 = 1.0 / 005935·069500·900050·000030(2019-01-15 run 5)·005930(2010-01-28 창 미달, 2018-05-03 corp_action_window) NULL (§9 S03B-2·S03C) |
| S03C `no_trade_reason` | `universe_daily` | (`005930`, 2018-04-27·05-03) · (`101970`, 2014-06-11·06-12) · (`900050`, 2017-03-30) · (`000030`, 2019-01-15) | `no_trade_reason`, `status` | hand | 거래 행 = `none`(halt 지정일이어도) · 분할 apply 앞 무거래 = `corp_action_window`·suspended · 정지 공시 뒤 무거래 = `halt_disclosed` · 신호 없는 무거래 = `illiquid`(run 1 listed / run 5 = k suspended). 모집단 비의존 불변식만(§9 S03C) |

### 2단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-2-001 | `adj_factor` | (`005930`, 2018-05-04) | `price_factor`, `share_factor` | hand (50:1) | `price_factor × share_factor = 1`, `share_factor = 50` |
| FX-2-002 | `price_daily` | (`005930`, 2018-05-03) | `close` | stage:`stg_price_daily.close_krw` | 원주가 무수정 (stage FX 와 같은 값) |
| FX-2-003 | `corp_event` | 무상증자 1.2:1 사례 1 | `ratio`, `event_type` | doc:2단계 기록 | 엔진 임계 미만 이벤트도 존재 |
| FX-2-004 | `adj_factor` | 감자 1 | `price_factor`, `share_factor` | hand | 두 축 상이 |
| FX-2-005 | `price_daily` | ETF 1종 1일 | `close`, `price_kind` | stage:`stg_etf_price_daily` | 같은 테이블에 실림 |
| FX-2-006 | `price_daily` | `volume_shr = 0` 인 날 1 | `price_kind`, `close` | stage | `reference`, 종가 보존 |
| FX-2-007 | `adj_factor` | `207940` 인적분할 | `factor_ok`, `factor_source` | doc:SPEC §3-1 | `false` |
| FX-2-008 | `corp_event` | 공시일 ≠ 효력일 이벤트 1 | `announce_date`, `effective_date`, `effective_basis` | stage:`stg_event_*` | `available_date = announce_date` |
| FX-2-009 | `adj_factor` | KRX 관측만으로 만든 계수 1 | `available_date`, `available_basis` | hand | 효력일 + 1거래일 / `derived` |
| FX-2-010 | `v_adj_volume` | (`005930`, 2018-05-03, asof=2018-06-01) | `adj_volume` | hand (원 거래량 × 50) | **곱셈 방향 검증** — 나눗셈이면 여기서만 잡힌다 |
| FX-2-011 | `price_adj_daily` | (`005930`, 2018-05-03) | `adj_close` | hand (2,650,000 = 원주가) | **전방 조정의 앵커** — 계수는 05-04 부터 접힌다. base = as_of 축이면 53,000 이 된다 |
| FX-2-012 | `price_adj_daily` | (`005930`, 2018-05-04) | `adj_close`, `cum_share_factor`, `n_factors_applied` | hand (51,900 × 50 = 2,595,000 · 50.0 · 1) | 분할일 앞뒤가 −2.08% 로 이어진다 |
| FX-2-013 | `price_adj_daily` | (`005930`, 2018-05-04) | `adj_volume_shr` | hand (39,565,391 × 0.02) | **거래량만 반대 축**(price_factor) — FX-2-010 의 전방 판 |
| FX-2-014 | `price_adj_daily` | (`005930`, 2018-05-04) | `adj_open` | hand (53,000 × 50) | OHLC 는 close 와 같은 계수 |
| FX-2-015 | `price_adj_daily` | (`247540`, 2022-06-24·06-27) | `adj_close` | hand (497,400 · 135,900 × 3.988773…) | KRX 기준가 축(S06-2) — 배정비율 4.0 이 아니다 |
| FX-2-016 | `price_adj_daily` | (`247540`, 2022-06-27) | `n_unadjusted_events` | hand (1 = `247540:krx_base:2022-05-09`) | 조정 불완전 구간의 표식 |
| FX-2-017 | `price_adj_daily` | (`101970`, 2025-03-28) | `cum_share_factor`, `n_unadjusted_events` | hand (1.0 · 0) | **재상장 구간의 누적 초기화** — 폐지 구간 사건 5건이 넘어오지 않는다 |

### 3단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-3-001 | `short_daily` | 2020-06-30 공매도 0 종목 1 | `fill_kind` | doc:SPEC §5-3 | `{src_omitted, shard_done}`, 값 0 |
| FX-3-002 | `flow_daily` | KIS 유닛 `ok` 인 폐지 종목 1일 | `fill_kind`, `src` | stage:`stg_units_kis` | `{src_omitted, unit_ok}` / `kis` |
| FX-3-003 | `flow_daily` | 로그 없는 셀 1 (샤드 미커버 1,070 중) | `fill_kind` | doc:P3 | `{not_collected, none}`, 값 NULL |
| FX-3-004 | `short_daily` | 샤드 `empty` 셀 1 (ka10014 60건) | `fill_kind` | stage:`stg_shards_kiwoom.status` | `{empty_response, shard_empty}`, 값 NULL |
| FX-3-005 | `flow_daily` | 폐지 종목 KIS 만 있는 날 1 | `src` | stage | `kis` |
| FX-3-006 | `flow_daily` | 임의 (ticker,date) 1행 | 12주체 합 | hand | `= 0 ± bl` |
| FX-3-007 | `flow_daily` | KIS 행 1 | `ind_invsr_krw` | stage:`stg_flow_split_daily.prsn_ntby_tr_pbmn_krw` | 대응표대로 매핑, 대응 없는 주체는 NULL |
| FX-3-008 | `credit_daily` | 임의 1행 | `whol_loan_rmnd_stcn_shr` | stage:`stg_credit_daily` | 값·단위 접미사 보존 |
| FX-3-009 | `short_daily` | 키움 대차 1행 | `lending_balance_kiwoom_raw` | stage:`stg_lending_daily.rmnd` | **단위 미확정 컬럼에 접미사 금지** 확인 |

### 4단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-4-001 | `fin_std` | (`005930`, FY2025 2Q) | 손익 계정 | hand (1Q + 2Q = 반기 누계, 원 단위) | 분기 = 3개월 값 |
| FX-4-002 | `fin_std` | 3월 결산 1사 (32사 중) | `period_end`, `period_end_basis` | hand (`acc_mt` 말일) | `bsns_year` = 종료 연도 관례 |
| FX-4-003 | `fin_std`·`v_fin_latest` | 두산에너빌리티 FY2020, asof = 정정 전 | 전 계정 | hand | **D 시점 결측 재현** — 값이 나오면 look-ahead |
| FX-4-004 | `disclosure_version` | 정정 2회 그룹 1 | `correction_seq` | stage:`stg_disclosure` | `max = 2`, 원본 `= 0` |
| FX-4-005 | `disclosure_version` | `[기재정정]` 접두 1 | `group_key` | hand (정규화 후 라벨) | 원본과 같은 `group_key` |
| FX-4-006 | `fin_std` | 은행 1사 | `revenue_basis` | doc:FACTORS §8 | `banking_gross` |
| FX-4-007 | `fin_std` | 접수지연 최대 사례 | `available_date` | stage:`stg_rcept_dt_map.rcept_dt` | `= rcept_dt`, basis `derived` |
| FX-4-008 | `fin_std` | q4_derived 판본 혼합 1 | `*_q4_derived`, `derived_n_rows`, `q4_derived_available_date` | hand | 구성 4행 중 하나 결측이면 NULL(부분합 금지) |
| FX-4-009 | `correction_link` | `candidate_status='multi_resolved'` 사례 1 | `orig_rcept_no` | doc:DOC_DESIGN §1.7 C340 | 후보 2+ 중 `filed_date = rcept_dt` 1건 |
| FX-4-010 | `correction_link` | `date_check='mismatch'` 사례 1 (10건 중) | `orig_rcept_no`, `date_check` | doc:C340 | **링크는 성립하고 날짜만 어긋난다**(결정 ③) |

### 4-B단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-4B-001 | `treasury_stock` | 집계행 포함 원장 1그룹 | `sum(change_qy_acqs_shr)` | hand | 집계행 제외 후 합산이 원장 집계행 값과 일치 |
| FX-4B-002 | `audit_opinion` | 비적정 1건 | `adt_opinion_class` | stage:`stg_audit.adt_opinion_class` | 비적정 어휘 |
| FX-4B-003 | `holder_daily` | 5% 보고 1건 | `stkrt_pct` 전·후 | stage:`stg_holder_majorstock` | 1:1 |
| FX-4B-004 | `dividend_event` | `se` 3종 wide 1그룹 | `dps_krw`·`cash_total_krw`·`yield_pct` | stage:`stg_dividend` | 라벨 → 컬럼 전개 |
| FX-4B-005 | `ownership_snapshot` | 롤링 2년 창 첫날 종목 1 | `available_date`, `coverage_from` | doc:DESIGN §4-5 | 창 경계 밖은 결측 |
| FX-4B-006 | `shares_outstanding` | (`00126380`, 2018, 11011, `보통주`·`우선주`·`비고`) 외 (`00110893`, 2015) | `issued_shr`·`treasury_shr`·`distributed_shr`·`n_src_rows` | hand + stage:`stg_shares` | 발행 = 자기 + 유통(대신증권 2015: 50,773,400 = 10,103,074 + 40,670,326) · 원장 공란은 NULL · `se='비고'` 행 유지 · 삼성전자 2018 보통주 5,969,782,550 = KRX `list_shrs` 2018-12-28 (S16 신설 — §2 매트릭스 20행이 가리키던 FX-4B-001 은 `treasury_stock` 몫이다, §9) |

### 5단계

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-5-001 | `consensus_daily` | 같은 (ticker, obs_month, target, metric) 에 3판본 | `available_date`, `est_mean` | stage:`stg_consensus_monthly` min(fetched_date) 행 | **min 선택** |
| FX-5-002 | `consensus_daily` | 두 fetched_date 관측점 | `est_mean` 2행 | **hand** (두 원본 값에서 리비전 손계산) | `stg_v3_revision_compare` 유래 금지 |
| FX-5-003 | `consensus_daily` | EPS 1행 · 매출 1행 | `unit` | stage:`stg_consensus_monthly.unit` | 원 / 억원 |
| FX-5-004 | `consensus_daily` | v3→wise 경계 월 1종목 | `src` 2행 | hand | 구간 2행 규칙 (같은 키에 src 다른 2행) |
| FX-5-005 | `consensus_daily` | v3 `collected_date` NULL 행 1 | `available_date`, `available_basis` | stage:`stg_v3_revision_daily.date` | fallback + `coverage_degraded` |
| FX-5-006 | `consensus_daily` | v3 wide 1행 | metric 8행 | hand | unpivot 대응표 |
| FX-5-007 | `opinion_daily` | 임의 1행 | `analyst_count`, `available_date` | stage:`stg_analyst_summary` | `= fetched_date`, basis `measured` |
| FX-5-008 | `opinion_broker_daily` | 임의 1행 | `prev_opinion_date` | hand | 직전 `opinion_date` — 간격 없이 `change_pct` 해석 금지의 근거 |

### 6단계 (신설 — DESIGN §8 에 없다, §5-A5)

**초안 재정의(2026-09-06, §9 S19 1차·2차)**: ① 초안의 키는 (테이블, 컬럼군)이었으나 DESIGN §4-7
정본이 grain 을 `field_id` 로 정했고, FX-6-002 가 가리킨 `flow_daily` 는 아직 없는 테이블(S08)이다.
② **서버 1차 빌드가 EG4 로 막혔다** — `financial.borrowings.coverage_from` 을 절단본 값(2023-11-14)
으로 굳혔는데 서버는 2016-03-30 이었다. **골든 픽스처는 모집단 비의존 불변식만 담는다**: 창
(`coverage_from`·`coverage_to`)·커버율·분위 배열·거기서 파생된 판정(`status`·`no_observations`
사유·`first_usable_date`)은 모집단이 바뀌면 반드시 달라지므로 **절단본 전용 pytest 손계산**으로
옮겼다. 남은 것은 선언(랙·PIT·basis·cell kinds·`source_stage_tables`·`column_scope`·`table_name`·
`field_scope`·`requires_confirmation`)과 **선언 조인만으로 정해지는 판정**(요구 필드에 프로파일 행이
없으면 언제나 `field_unavailable`)뿐이다. 파일은 `src/equity/fixtures/<table>.json`.

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-6-001 | `dataset_profile` | `price.close` | `available_date_basis`, `recommended_lag_sessions` | decl:rules_s04 | `default`, `0`(가격류 당일 관측) |
| FX-6-002 | `dataset_profile` | `consensus.forward_eps` | `recommended_lag_sessions` | decl:rules_s17 | `1` — v3 는 collected = base + 1영업일 |
| FX-6-003 | `dataset_profile` | `price.market_cap` | `recommended_lag_sessions` | decl:rules_s04 | `1` — 주식수가 `stg_listing_daily`(lag_known=false) |
| FX-6-004 | `dataset_profile` | `event.buyback_amount` | `column_scope` | decl:rules_s05 | `amount_krw`(커버율 0 은 pytest 손계산으로 이동) |
| FX-6-005 | `dataset_profile` | `classification.sector` | `point_in_time` | decl:rules_s01 | `false`(GAP-07) |
| FX-6-006 | `dataset_profile` | `price.adj_close` | `table_name`, `available_date_basis`, `field_scope` | decl:rules_s06 · rules_s19 | `v_adj_price_fwd`, `default\|derived`, `internal` |
| FX-6-007 | `dataset_profile` | `event.insider_net_buy` | `column_scope` | decl:rules_s15 | `qty_change_shr`(롤링 2년 창은 pytest) |
| FX-6-008 | `dataset_profile` | `financial.borrowings` | `table_name` | decl:rules_s12 | `fin_std`(GAP-02 계정 · 첫 관측일은 pytest) |
| FX-6-009 | `dataset_profile` | `consensus.eps_dispersion` | `column_scope`, `coverage_basis` | decl:rules_s17 | `est_min,est_max`, `grid_security` |
| FX-6-016 | `dataset_profile` | `financial.revenue` | `requires_confirmation` | decl:rules_s12 | `true`(GAP-01) |
| FX-6-017 | `dataset_profile` | `price.volume` | `supported_cell_kinds`, `source_stage_tables` | decl:rules_s19 | 3종 · `price_daily` 전이 폐포 4(EG2-P04 대조축) |
| FX-6-018 | `dataset_profile` | `universe.admin_state` | `requires_confirmation` | decl:rules_s03 | `true`(GAP-06) |
| FX-6-010 | `factor_readiness` | `M01` | `required_columns`, `label` | decl:rules_s20 ⋈ profile | `[v_adj_price_fwd.adj_close]` — 결정 6 이 여기 박힌다 |
| FX-6-011 | `factor_readiness` | `F01` | `blocked_reason`, `status` | decl:프로파일 행 부재 | `field_unavailable: flow.foreign_net_buy`, `blocked` |
| FX-6-013 | `factor_readiness` | `R04` | `owner`, `blocked_reason` | decl:GAP-09 | `unavailable`, `field_unavailable: benchmark.close` |
| FX-6-015 | `factor_readiness` | `V01` | `registry_factor_id`, `required_field_ids` | decl:backend/FACTORS.md #11 | `financial.book_to_market`, `[price.market_cap, financial.book_equity]` |
| FX-6-019 | `factor_readiness` | `Q01` | `required_columns`, `owner` | decl:rules_s20 ⋈ profile | `[fin_std.net_income, fin_std.total_equity]`, `factor_layer` |

절단본 손계산으로 옮긴 실측 단언: `event.insider_net_buy` 창 2024-08-26~2026-08-26 · GAP-02
3계정 커버·첫 관측일 · `event.buyback_amount`·`event.capital_raise_amount` 커버 0 ·
M01/V05 `first_usable_date` · E05/E06 `no_observations` · 커버율 ↔ 정수 분자·분모 항등.

### 부정 픽스처 (§7-5)

| ID | 대상 게이트 | 주입할 결함 | 기대 |
|---|---|---|---|
| FX-N-001 | EG1 | `universe_daily` 에서 임의 1행 삭제 | EG1 fail |
| FX-N-002 | EG2 | `fin_std` 1행의 `available_date` 를 `period_end − 1일` 로 | EG2-P02 fail |
| FX-N-003 | EG3 | `adj_factor` 1행의 `share_factor` 를 역수로 | EG3-P04 fail |
| FX-N-004 | EG3 | `flow_daily` 1행의 주체 1개를 +1원 | EG3-P06 fail |
| FX-N-005 | EG6 | `consensus_daily` 를 max(fetched_date) 로 선택 | EG6-P01 fail |
| FX-N-006 | EG8 | `v_adj_volume` 을 나눗셈으로 | EG8-P03 fail ∧ FX-2-010 fail |
| FX-N-007 | EG9 | `not_collected` 셀에 0 을 적재 | EG9-P04 fail |
| FX-N-008 | EG13(§6) | `available_date` 1행을 미래 날짜로 | EG13 fail |
| FX-N-009 | EG-C ⑦ | 뷰에서 `available_date` 필터 제거 | EGC-07 fail |
| FX-N-010 | EG15(§6) | 폐지 직전 20거래일 가격 삭제 | EG15 fail |

---

## 5. 측정가능성 감사

WORKFLOW §3 단계별 통과 조건과 DESIGN §8 표를 한 줄씩 심사했다.
판정 어휘: **유지**(그대로 술어가 된다) / **술어화**(정의를 채워야 SQL 이 된다) / **기록으로 이동**(게이트 아님) / **삭제**(항진명제·중복·유해).

### 5-A. 정의가 비어 SQL 로 못 쓰는 조건

| # | 출처 | 조건 원문 | 문제 | 판정 | 조치 |
|---|---|---|---|---|---|
| A1 | DESIGN §2 파티션 표 | `consensus_daily` 는 `date_axis`(물리 키 = `obs_month` 연도) | `date` 컬럼이 없는데 `date_axis` 로 선언 → EG0-P06 이 항상 실패 | 술어화 | `_reg_table.partition_key_expr` 신설, EG0-P06 을 그 식이 참조하는 컬럼 실재로 |
| A2 | WORKFLOW §3-0 | "결정 5개가 '확정'(선택/대안/이유 3요소)" | 문서 상태 판정. SQL 대상 아님 | 기록으로 이동 | 0단계 체크리스트 |
| A3 | WORKFLOW §3-0 | "검수 findings open 0" | 동일 | 기록으로 이동 | 이슈 트래커 |
| A4 | WORKFLOW §3-1 | "`corp_ticker` 매핑률 ≥ baseline" | **분모 미정의**. 전 티커? ETF 제외? DART 매핑 가능 모집단? DESIGN §4-1 은 ETF·일부 외국주권의 `corp_code` NULL 을 정상으로 선언 | 술어화 | 분모 = `security` 중 `sec_type ∉ {etf}` ∧ `isin8 LIKE 'KR7%'`. metric `corp_ticker.map_rate_min` |
| A5 | DESIGN §8 6단계 행 | EG4 픽스처 열이 "—" | 선언표에도 픽스처가 필요하다(랙 값 오타가 전 팩터에 전파) | 술어화 | FX-6-001·002 신설(§4) |
| A6 | 전 문서 | 뷰 7종에 게이트 행이 없다 | 팩터층·엔진이 실제로 읽는 것은 뷰다. 테이블이 옳아도 뷰가 틀리면 전부 틀린다 | 술어화 | EG11(뷰 결정성)·EG19(as-of 단조성) 신설(§6) + EG5c·EG-C 로 커버 |
| A7 | WORKFLOW §2 EG9 | "측정일 3개(baseline 상수) 커버율" | **어느 테이블의 커버율인지, 분모가 격자인지 원장인지** 미정의 | 술어화 | 분모 = 격자 셀, 분자 = `fill_kind.kind='measured'`. metric `<table>.coverage_probe_dates`·`coverage_min` |
| A8 | WORKFLOW §2 EG9 | "월별 커버율 ↔ 시장 월수익률 상관 절댓값 ≤ baseline" | 상관 종류·시장 지수 지정·최소 표본 월수 미정의. 표본이 3개월이면 상관은 의미 없다 | 술어화 | Pearson `corr()`, 시장 = 자기시장(`코스피`/`코스닥`), `corr_min_months` 미달은 `skip(no_coverage)` (EG9-P03) |
| A9 | WORKFLOW §2 EG5(c) | "고정 asof 표본에서 뷰 결과 차이가 전부 '새 rcept_dt/fetched_date > asof' 로 설명" | **비교 대상(이전 빌드의 as-of 결과)을 어디에 보관하는지 규약 없음**. 다음 빌드에서 재실행하면 이미 값이 바뀐 뒤다 | 술어화 | `data/equity/_asof/<view>/<build_id>/` 스냅샷 규약 신설(§1 EG5 주석) |
| A10 | WORKFLOW §2 EG6 | "정정 그룹 링크 표본 오판율 ≤ baseline" | 표본 크기·추출 방법·정답 라벨 출처 미정의 | 술어화 | 표본 = DOC_DESIGN §1.0 C340 술어 재사용, 정답 = 정정 첫 장 원문(§1.7). metric `disclosure_version.misjudge_rate_max` |
| A11 | DESIGN §8 4단계 | "EG6 전수" | "전수"의 대상 불명(전 그룹? 전 정정?) | 술어화 | EG6-P01·P02 처럼 **구현과 독립한 재계산 등식**으로. 표본이 아니라 전 행 |
| A12 | WORKFLOW §2 EG-C ⑥ | "샘플 팩터 4개(V01·F01·M01·G05) 손계산 일치" | **G05 는 컨센서스 2관측점이 필요** — FACTORS §3 은 "2027년 이후" 사용 가능. 현재 데이터로 실행 불가 | 술어화 | 커버 밖이면 `skip(no_coverage)` 를 명시. 대체 팩터는 두지 않는다(임의 교체 금지) |
| A13 | WORKFLOW §2 EG-C ① | "`backend/tests/test_bar_source_contract.py::BUILDERS` 등록" | 엔진은 같은 저장소 `backend/`(`backend/tests/test_bar_source_contract.py`). 워크벤치 4포트 계약 테스트는 `backend/tests/contract/` | 술어화 | 같은 모노레포이므로 `skip(engine_absent)` 는 두지 않는다 — S07·S21 통과 조건에서 backend 테스트 전량 green 을 요구 |
| A14 | DESIGN §3-1 산출 | `index_daily` 의 `close_pt` | stage 실명은 `close_idx`(`rules_krx.py`) | 술어화 | 컬럼명 정정. EG0-P02 가 실제로 잡는다 |

### 5-B. 상수 출처가 없거나 등식이 틀린 조건

| # | 출처 | 조건 | 문제 | 판정 | 조치 |
|---|---|---|---|---|---|
| B1 | WORKFLOW §2-1 전체 | 등식 21행 | `_reject` 뺄셈 항이 3단계 3테이블에만 있다. `security`(EG7 `other` 격리)·`fin_std`(`period_end` 격리)·`disclosure_version`(`no_label` 격리)은 정상 빌드가 EG1 실패 | 술어화 | §1 EG1 일반형(`− n_dedup − Σ n_reject`)을 전 테이블에 적용, §2-1 표 개정 |
| B2 | WORKFLOW §3-1 | "폐지 909 전부 span 종료 보유" | **909 = 652 + 257 이 본문 리터럴**. 백필이 하루 더 돌면 값이 바뀐다 | 술어화 | `bl_int('security','delisted_total')` + 모집단 자체를 독립 재계산(EG3-P10) |
| B3 | WORKFLOW §2 EG7 | "격리 비율 임계는 stage 초기값(0.1%)" | 본문 리터럴 | 유지(첫 빌드 한정) | 첫 빌드만 `gates.DEFAULT_THRESHOLDS` 상속, 2회차부터 `bl('<t>','threshold_EG7')` |
| B4 | WORKFLOW §2-1 3단계 | "격자 행수 = Σ_d 활성 주권 티커 수" | `flow_daily`·`short_daily` 는 PK 에 `src` 를 포함한다(DESIGN §3). 같은 (date,ticker) 가 kiwoom·kis 2행이면 좌변이 격자보다 크다 | 술어화 | 좌변을 `count(DISTINCT (date, ticker))` 로. `src` 축 행수는 별도 metric |
| B5 | WORKFLOW §2-1 | `dividend_event` = "distinct(...) **비집계**" | `stg_dividend` 에 `row_kind` 가 없다(`rules_dart.py:136` — hyslr·shares·tesstk 3개만) | 술어화 | "비집계" 표현 삭제, 등식은 §3-⑱ 형태 |
| B6 | WORKFLOW §2-1 | `disclosure_version` = "정기보고서 접수 행수(**group 181,106 기준**)" | 리터럴 + "group" 이 행수인지 그룹수인지 모호 | 술어화 | §3-⑬ 형태. 리터럴은 `bl_int('disclosure_version','periodic_rows')` 로 회귀 확인만 |
| B7 | DESIGN §4-2 | `corp_event` EG8 "기준가≠전일종가 5,214건 매칭율" | 5,214 는 SPEC 실측 리터럴 | 술어화 | 분모를 EG8-P04 처럼 재계산하고 `bl_int('corp_event','base_price_anomaly_n')` 로 분모 자체를 회귀 감시 |
| B8 | DESIGN §4-1 | `status='suspended'` 판정의 `no_trade_run ≥ k` | k 가 "baseline" 이라고만 적혀 metric 이름이 없다 | 술어화 | `universe_daily.no_trade_run_k`. **게이트 상수가 아니라 산출 규칙 상수**라 `universe_policy` 가 아니라 baseline 에 두는 근거를 §12 EQD-10 처럼 남긴다 |
| B9 | DESIGN §8 2단계 vs WORKFLOW §3-2 | EG8 항 수가 4 vs 3 | DESIGN 은 "조정 거래량 점프"를 포함, WORKFLOW 는 누락 | 유지(DESIGN) | WORKFLOW §3-2 에 EG8-P03 추가 |
| B10 | DESIGN §8 5단계 vs WORKFLOW §3-5 | EG3 이 DESIGN 에만 있다 | 5단계 PK 검사가 WORKFLOW 통과 조건에서 빠짐 | 유지(DESIGN) | WORKFLOW §3-5 에 EG3 추가 |

### 5-C. 중복 · 항진명제 · 유해

| # | 출처 | 조건 | 문제 | 판정 | 조치 |
|---|---|---|---|---|---|
| C1 | WORKFLOW §3-4 | "EG6(`rcept_dt ≤ D` 선택 전수)" | 뷰 축 조건이라 EG-C ⑦·EG5c 와 중복. 테이블 게이트에서 `D` 가 무엇인지 정의되지 않는다 | 삭제 | EG6 은 판본 **선택 규칙** 재계산(EG6-P01·P02)만. as-of 는 EG5c·EGC-07 |
| C2 | WORKFLOW §3-4 | "PIT 결측률 교차표(has_correction × 접수지연 분위수) baseline 등재" | 통과/실패 기준 없는 산출물 | 기록으로 이동 | WORKFLOW §5 기록 규약 4단계 행으로. 단 **교차표가 없으면 4단계 미완**이라는 체크는 유지 |
| C3 | WORKFLOW §3-1 · DESIGN §8 1단계 | "폐지 909 전부 span 종료" vs "폐지 909 전부 `delist_date` 보유" | 서로 다른 술어인데 같은 문구로 읽힌다 | 유지(분리) | EG3-P12(span) / EG3-P10(`delist_date`) 로 ID 분리 |
| C4 | DESIGN §3 골격 | "파생 컬럼은 구성 행 `available_date` 의 max" | 테이블에 `available_date` 는 행당 1개인데 파생은 컬럼당이다 → **SQL 로 표현 불가** | 술어화 | `<col>_available_date` 동반 컬럼 규약 신설. EG2-P05 가 그걸 본다 |
| C5 | WORKFLOW §2 EG6 | "'max 선택' 행 0" | 빌더가 min 을 쓰면 자동으로 0 → **항진명제** | 술어화 | 원장에서 min 을 독립 재계산해 채택 행과 대조(EG6-P01·P02) |
| C6 | DESIGN §8 1단계 | "EG7 `sec_type='other'` 격리" | 격리하면 그 티커가 `security`·`universe_daily` 에서 사라지고 `price_daily` 에만 남는다 → **게이트가 생존편향을 만든다**. §0-3 1행과 정면 충돌 | 삭제 | 행 유지 + `sec_type='other'` 플래그 + 건수 `bl('security','sec_type_other_max')` 로 기록형. `_reject` 아님 |
| C7 | DESIGN §8 2단계 | "EG3 `v_firm_mktcap` 합산" | 뷰가 Σ 로 정의돼 있어 뷰를 다시 부르면 **항진명제** | 술어화 | 구성 집합을 corp_ticker×universe_daily×price_daily 로 독립 재계산(EG3-P05) + FX-1-003 손계산 |
| C8 | WORKFLOW §2 EG9 | "'미수집→0' 행 0" | `fill_kind` 를 만든 식으로 다시 판정하면 **항진명제** | 술어화 | 로그 축(`stg_shards_kiwoom`·`stg_units_kis`)에서 독립 재판정(EG9-P04) |
| C9 | WORKFLOW §2 EG3 | "`security_span` 비중첩" · "`corp_ticker` corp 당 보통주 1" | DESIGN §4-1 EG1 열에도 같은 조건이 있다 | 유지(EG3) | EG1 은 행수 등식만. DESIGN §4-1 의 중복 기술 정리 |
| C10 | WORKFLOW §2 EG2 | "basis ∈ {measured, derived, convention, default, unknown}" | 어휘 폐쇄가 EG2 와 EG3-P13 에 이중 | 유지(EG2) | `available_basis` 만 EG2-P03, 나머지 어휘(`sec_type`·`status`·…)는 EG3-P13 |
| C11 | WORKFLOW §2 EG-C ⑨ | "`obs_month` 로 계산한 G05 는 `v_consensus` 로 재현되지 않음" | "재현되지 않음"이 통과 조건이면 우연 일치 시 실패. 또 **v3 구간에서는 obs_month ≈ available 이라 원래 재현된다**(DESIGN §4-6) | 술어화 | wise 구간 한정 + "차이 ≥ `bl('consensus_daily','obs_month_bias_min')` 인 종목 ≥ 1" |
| C12 | DESIGN §8 4단계 | "EG3 PK · 조인 무매칭률 **비대칭**" | "비대칭"은 술어가 아니고, 성격상 EG3(키)이 아니라 교차 검사다 | 술어화 + 재배치 | EG6-P08 로 이동, `nonmatch_rate_gap_max` 로 판정 |
| C13 | WORKFLOW §2 EG-C ② | "`v_universe(:d,'all')` 티커 집합 = `stg_listing_daily(d − lag)` 집합" | ETF 는 `stg_listing_daily` 에 없다(P8: 0건). ETF 를 `universe_daily` 행에 포함(DESIGN §4-1)하므로 **항상 불일치** | 술어화 | 좌변을 `sec_type <> 'etf'` 로 제한하고 ETF 축은 `stg_etf_price_daily` 로 별도 등식 |
| C14 | WORKFLOW §2 EG0 | "맨 glob 검사는 CI lint 로 분리" | 게이트가 아니라고 명시했는데 §3-7 통과 조건에는 없다 | 기록으로 이동 | CI lint 항목으로 명시하고 7단계 통과 조건에 "lint green" 추가 |
| C15 | DESIGN §4 카탈로그 26 | `correction_link` 부재 | DOC_DESIGN §8.1·결정 ④ 는 **equity 가 만든다**고 못박았는데 DESIGN §4 테이블 목록에 없다 | 술어화 | 카탈로그를 27 로 늘리거나 `disclosure_version` 컬럼으로 흡수. 어느 쪽이든 EG1 등식(§3-⑭)이 필요 |
| C16 | WORKFLOW §3-3 픽스처 | "`truncated` 샤드 셀 1 → `not_collected`" | 샤드 `status` 실측 어휘는 `{done, empty}` 뿐(P3). `truncated` 는 존재하지 않는다 | 삭제 | FX-3-004(`shard_empty`)로 대체 |
| C17 | WORKFLOW §3-5 입력 | `stg_consensus_monthly/annual/quarterly/matrix` | ~~monthly 뿐~~ **기각** — 4테이블 전부 서버 stage 에 실재(HANDOFF §3: annual 11,270 · quarterly 11,284 · matrix 214,650; `rules_wise.py` 는 헬퍼로 선언) | 유지 | EG0-P02 가 실물 대조 |

### 5-D. 요약

| 판정 | 건수 | 대표 |
|---|---|---|
| 유지 | 4 | C3·C9·C10·B3 |
| 술어화 | 26 | A1·A4·A7~A14·B1·B2·B4~B8·C4·C5·C7·C8·C11~C13·C15 (C17 기각) |
| 기록으로 이동 | 4 | A2·A3·C2·C14 |
| 삭제 | 3 | C1·C6·C16 |

**착수 차단 항목**(이게 안 정해지면 그 단계 착수 금지, WORKFLOW §3 "통과 조건이 비어 있으면 착수 금지"):
1단계 — A4·B2·B8·C6·C13 / 2단계 — B7·C7 / 3단계 — A7·A8·B4·C8·C16 / 4단계 — A10·A11·C1·C4·C12·C15 / 5단계 — B10·C11·C17 / 6단계 — A5 / 7단계 — A6·A9·A12·A13.

---

## 6. 누락 게이트 제안

목적(54팩터 재료 · 백테스트 PIT 패널 · 엔진 소비) 관점에서 현 EG0~EG9·EG-C 가 못 잡는 것.

### EG10 — 팩터 준비도 (**구현 확정 2026-09-06, `rules_s20.eg10_factor_readiness`**)
- **분류** 폐기형(전수·사유·소유자·시작일·재료 컬럼 실재) + 기록형(ready 비율·사유 분포)
- **적용** `factor_readiness` 1테이블. 6단계
- **왜** FACTORS 54개 중 하나의 재료가 조용히 비어도 현재 어떤 게이트도 실패하지 않는다. 이 층의
  목적("54 재료가 충분한가")을 pass/fail 로 묻는 술어가 여기밖에 없다
- **상수** `factor_readiness.ready_min` (미등재 → 그 술어만 빠지고 나머지 폐기형은 그대로 돈다)

초안(`_reg_factor_material` 위의 필드 단위 커버율 + `<factor>.material_coverage_min`)에서
**팩터 단위 판정**으로 바뀌었다(§9 「EG10 이름」의 결정을 구현이 마저 밀어붙인 것): 커버율은
`dataset_profile.estimated_coverage_pct` 가 이미 재고 있으므로 EG10 은 그 숫자를 임계로 자르는 대신
`factor_readiness` 가 그것을 `blocked(no_observations)` 로 옮겼는지를 본다. 필드별 임계를 따로 두면
같은 사실에 문턱이 둘 생긴다.

```sql
-- (a) 폐기형 — ready 인데 재료 컬럼이 실물에 없다 (표가 거짓말을 하는 경우)
--     `table_name` 이 매크로면(v_adj_price_fwd) 빌드 세션에 실체가 없다 → views.SIGNATURES 등재
--     여부로 보고, 실체 판정은 카탈로그 단계 EG11 이 한다.
SELECT count(*) AS n FROM factor_readiness f, unnest(f.required_columns) AS u(c)
WHERE f.status = 'ready'
  AND NOT EXISTS (SELECT 1 FROM duckdb_columns() d
                  WHERE d.table_name = split_part(u.c, '.', 1)
                    AND d.column_name = split_part(u.c, '.', 2));
-- (b) 폐기형 — 전수·사유·소유자·시작일
SELECT (SELECT count(*) FROM factor_readiness) - 54
     + count(*) FILTER (WHERE status = 'blocked'
                          AND coalesce(trim(blocked_reason), '') = '')
     + count(*) FILTER (WHERE status = 'ready' AND first_usable_date IS NULL)
     + count(*) FILTER (WHERE status = 'ready' AND blocked_reason IS NOT NULL)
     + count(*) FILTER (WHERE owner IS NULL) AS n
FROM factor_readiness;
-- (c) 어휘 폐쇄 — 사유 문자열은 `<토큰>: <field_id 들>` 이라 앞부분만 본다
SELECT count(*) AS n FROM factor_readiness
WHERE blocked_reason IS NOT NULL
  AND split_part(blocked_reason, ':', 1) NOT IN
      ('field_unavailable', 'not_point_in_time', 'lag_unresolved', 'no_observations',
       'partial_support');
-- (d) 기록형 → baseline 승인 뒤 폐기형
--     통과: count(*) FILTER (WHERE status = 'ready') >= bl('factor_readiness', 'ready_min')
```

### EG11 — 뷰 결과 결정성
- **분류** 폐기형 · **적용** 뷰 7종 · 7단계
- **왜** 매크로 안 스칼라 서브쿼리(profile 조회)와 `read_parquet` 파일 순서가 결과 순서·값을 흔들 수 있다. `equity.duckdb` 를 `os.replace` 로 갈아끼우는 규약(DESIGN §2)은 리더가 중간 상태를 보는 창을 남긴다
- **상수** 없음(등식)

```sql
WITH a AS (SELECT * FROM v_fin_latest(bl_date('v_fin_latest','determinism_asof'))),
     b AS (SELECT * FROM v_fin_latest(bl_date('v_fin_latest','determinism_asof')))
SELECT (SELECT count(*) FROM (SELECT * FROM a EXCEPT ALL SELECT * FROM b))
     + (SELECT count(*) FROM (SELECT * FROM b EXCEPT ALL SELECT * FROM a)) AS n;
-- 별도 프로세스에서 read_only 재오픈 후 같은 해시가 나오는지도 확인(P1b 실측 경로)
```

### EG12 — 단위 접미사 전수
- **분류** 폐기형 · **적용** 전 테이블 · 전 단계
- **왜** stage 는 `_krw`·`_shr`·`_pct` 접미사와 `unit_scale`+G4 픽스처로 ×1e6 오적용을 막았다(`model.py:ColumnRule`). equity 는 조인·파생으로 컬럼을 새로 만드는데 그 방어가 없다. 키움 대차 `rmnd` 처럼 **단위 미측정이라 접미사를 붙이면 안 되는** 컬럼도 있다(HANDOFF §4)
- **상수** 없음(선언 대조)

```sql
SELECT count(*) AS n
FROM _reg_column c
WHERE c.kind = 'numeric'
  AND c.unit_suffix IS NULL                       -- 접미사 면제 선언이 없는데
  AND NOT regexp_matches(c."column", '_(krw|shr|pct|idx|days|seq|rt)$')  -- 접미사도 없다
UNION ALL
SELECT count(*) FROM _reg_column c
WHERE c.unit_suffix = 'unmeasured'                -- 단위 미측정 선언인데
  AND regexp_matches(c."column", '_(krw|shr|pct)$');  -- 접미사가 붙었다
```

### EG13 — `available_date` 미래값
- **분류** 폐기형 · **적용** 전 팩트 테이블 · 전 단계
- **왜** EG2 는 `available_date ≥ 내용일` 만 본다. **상한이 없다.** 참조표 조인 오류나 연도 오타(stage G7 이 실제로 겪은 2106·2923 사례)로 `available_date` 가 미래로 튀면 그 행은 어떤 asof 에서도 안 보이고 **조용한 결측**이 된다 — look-ahead 의 거울상
- **상수** `trading_calendar.backfill_end`

```sql
SELECT count(*) AS n FROM ${T}
WHERE available_date > (SELECT bl_date('trading_calendar','backfill_end'))
  AND available_basis <> 'unknown';
```

### EG14 — 파티션 경계 누락
- **분류** 폐기형 · **적용** `date_axis`·`receipt_axis` 테이블 · 전 단계
- **왜** `COPY … PARTITION_BY` 는 빈 파티션 디렉토리를 만들지 않는다(stage `build.py:477` 이 "전 행 reject" 를 특례 처리한 이유). 연도 하나가 통째로 비면 EG1 은 통과하고(우변도 같이 비면) **그 연도만 없는 패널**이 나간다
- **상수** `<table>.expected_partitions`

```sql
-- 선언 파티션 집합 = 실제 디렉토리 집합
WITH actual AS (SELECT DISTINCT partition FROM _eq_meta WHERE "table" = '${T}'),
     expect AS (SELECT UNNEST(CAST((SELECT value FROM _baseline
                 WHERE tbl='${T}' AND metric='expected_partitions') AS VARCHAR[])) AS partition)
SELECT (SELECT count(*) FROM (SELECT * FROM expect EXCEPT SELECT * FROM actual))
     + (SELECT count(*) FROM (SELECT * FROM actual EXCEPT SELECT * FROM expect)) AS n;
-- date_axis 는 연도 연속성도 본다
SELECT count(*) AS n_gap FROM (
  SELECT CAST(replace(partition, 'year=', '') AS INT) AS y FROM _eq_meta WHERE "table" = '${T}')
WHERE y + 1 NOT IN (SELECT CAST(replace(partition, 'year=', '') AS INT) FROM _eq_meta
                    WHERE "table" = '${T}')
  AND y < (SELECT max(CAST(replace(partition, 'year=', '') AS INT)) FROM _eq_meta
           WHERE "table" = '${T}');
```

### EG15 — 폐지 직전 가격 존재
- **분류** 격리형(부족 종목 격리 아님 — 기록형→폐기형 승격) · **적용** `price_daily`×`security` · 1·2단계
- **왜** §0-3 「생존편향」의 실측 근거가 **폐지 직전 20영업일 −95~−99% 소실**(MS-02)이다. 지금 게이트는 span 이 있는지만 보고 **그 구간에 가격 행이 실제로 있는지**는 보지 않는다. span 은 있는데 가격이 없으면 백테스트는 그 구간을 조용히 건너뛴다
- **상수** `security.pre_delist_price_days`·`security.pre_delist_price_min`

```sql
WITH d AS (SELECT ticker, delist_date FROM security WHERE delist_date IS NOT NULL),
     w AS (
  SELECT d.ticker, c.date
  FROM d JOIN trading_calendar c ON c.date < d.delist_date
  QUALIFY row_number() OVER (PARTITION BY d.ticker ORDER BY c.date DESC)
          <= (SELECT bl_int('security','pre_delist_price_days')))
SELECT count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM price_daily p WHERE p.ticker = w.ticker AND p.date = w.date))::DOUBLE
       / nullif(count(*), 0) AS rate
FROM w;
-- 통과: rate >= bl('security','pre_delist_price_min')
```

### EG16 — 가짜 재상장
- **분류** 폐기형 · **적용** `security_span` · 1단계
- **왜** span 은 "캘린더 기준 연속 존재 구간의 최대 run"으로 만든다(DESIGN §4-1). **원장 결손 하루가 재상장 2구간으로 읽힌다.** 실측 재상장은 2종뿐인데(P8) 수집 사고 하나가 이 수를 늘리면 `Membership` 이 쪼개져 백테스트 포지션이 강제 청산된다
- **상수** `security_span.respan_count`·`security_span.respan_verified_n`

```sql
-- 구간 2개 이상인 티커는 전부, 공백 구간이 상장폐지/재상장 축으로 설명돼야 한다
WITH multi AS (SELECT ticker FROM security_span GROUP BY ticker HAVING count(*) >= 2),
     gaps AS (
  SELECT s.ticker, s.last_date AS gap_from,
         lead(s.first_date) OVER (PARTITION BY s.ticker ORDER BY s.span_seq) AS gap_to
  FROM security_span s WHERE s.ticker IN (SELECT ticker FROM multi))
SELECT count(*) AS n_unexplained
FROM gaps g
WHERE g.gap_to IS NOT NULL
  AND NOT EXISTS (   -- 공백 시작 무렵 폐지 신호
        SELECT 1 FROM universe_daily u
        WHERE u.ticker = g.ticker AND u.date BETWEEN g.gap_from - 30 AND g.gap_from + 30
          AND (u.signal_delist OR u.signal_liquidation))
  AND NOT EXISTS (   -- 공백 끝 무렵 신규 상장일
        SELECT 1 FROM security s2
        WHERE s2.ticker = g.ticker AND s2.list_date BETWEEN g.gap_to - 30 AND g.gap_to);
-- 통과: n_unexplained = 0 ∧ count(multi) = bl_int('security_span','respan_count')
```

### EG17 — 캘린더 무결성
- **분류** 폐기형 · **적용** `trading_calendar`·격자 3종 · 1·3단계
- **왜** 격자 전체가 캘린더 위에 얹힌다. `prev_td` 체인이 한 칸 어긋나면 모든 룩백 팩터가 하루씩 밀리고 어떤 EG1 도 그것을 못 본다
- **상수** 없음(등식)

```sql
SELECT
  (SELECT count(*) FROM trading_calendar a
   WHERE a.next_td IS DISTINCT FROM (SELECT min(b.date) FROM trading_calendar b WHERE b.date > a.date))
+ (SELECT count(*) FROM trading_calendar a
   WHERE a.prev_td IS DISTINCT FROM (SELECT max(b.date) FROM trading_calendar b WHERE b.date < a.date))
+ (SELECT count(*) FROM trading_calendar WHERE dayofweek(date) IN (0, 6))   -- 주말
+ (SELECT count(*) FROM (SELECT date FROM ${GRID_T} EXCEPT SELECT date FROM trading_calendar))
  AS n;
```

### EG18 — 조인 팬아웃
- **분류** 폐기형 · **적용** 조인으로 만드는 전 테이블 · 전 단계
- **왜** stage G1 의 `fanout` 상수가 equity 에서 사라졌다(§0-5). 조인 한쪽에 중복 키가 생기면 행이 불어나는데, EG1 우변을 같은 조인으로 계산하면 둘 다 불어나 **등식이 통과한다**. 좌변 grain 이 변하지 않았음을 따로 봐야 한다
- **상수** 없음(등식)

```sql
-- 조인 전 좌축(driver) 행수 = 조인 후 행수
SELECT (SELECT count(*) FROM ${T})
     - (SELECT count(*) FROM ${DRIVER_VIEW}) AS delta;
--   universe_daily 의 driver = security_span × trading_calendar
--   fin_std      의 driver = distinct(corp_code, bsns_year, reprt_code, fs_div)
--   price_daily  의 driver = stg_price_daily ∪ stg_etf_price_daily
```

### EG19 — as-of 단조성
- **분류** 폐기형 · **적용** `v_fin_latest`·`v_consensus`·`v_cum_adj` · 7단계
- **왜** EG5c 는 "빌드 간" 불변을 본다. **같은 빌드 안에서 asof 를 늘렸을 때 정보가 줄어드는** 버그(랙 부호 실수, `available_date` 필터의 부등호 반전)는 안 잡힌다
- **상수** `<view>.monotonic_asof_pairs`

```sql
WITH p AS (SELECT UNNEST(CAST((SELECT value FROM _baseline
             WHERE tbl='v_fin_latest' AND metric='monotonic_asof_pairs') AS DATE[][])) AS pair)
SELECT count(*) AS n
FROM p,
LATERAL (SELECT count(*) FROM (
   SELECT corp_code, period_end FROM v_fin_latest(pair[1])
   EXCEPT
   SELECT corp_code, period_end FROM v_fin_latest(pair[2]))) AS lost(n_lost)
WHERE lost.n_lost > 0;   -- pair[1] < pair[2] 인데 이른 asof 에만 있는 (corp, period) = 0
```

### EG20 — 원주가 불변
- **분류** 폐기형 · **적용** `price_daily` · 2단계
- **왜** DESIGN 원칙 ②("원주가 불변 + 계수 분리")를 직접 검사하는 술어가 없다. `price_daily` 를 만들 때 실수로 수정주가를 넣어도 EG1(행수)·EG7(범위)·EG8(비율)은 통과할 수 있다
- **상수** 없음(등식, 전수)

```sql
SELECT count(*) AS n
FROM price_daily p
LEFT JOIN stg_price_daily s ON s.ticker = p.ticker AND s.date = p.date
LEFT JOIN stg_etf_price_daily e ON e.ticker = p.ticker AND e.date = p.date
WHERE p.close IS DISTINCT FROM coalesce(s.close_krw, e.close_krw)
   OR p.volume_shr IS DISTINCT FROM coalesce(s.volume_shr, e.volume_shr);
```

### 제안 게이트 요약

| ID | 이름 | 분류 | 단계 | 신규 상수 | 잡는 위험(§0-3) |
|---|---|---|---|---|---|
| EG10 | **팩터 준비도** | 폐기형+기록형 | 6 | `factor_readiness.ready_min`(미등재) | (신규) 재료 무성 소실 · 목적 판정 부재 |
| EG11 | 뷰 결과 결정성 | 폐기형 | 7 | `<view>.determinism_asof` | 재현성 |
| EG12 | 단위 접미사 전수 | 폐기형 | 전 | — | (신규) 단위 오적용 |
| EG13 | `available_date` 미래값 | 폐기형 | 전 | — (기존 상수 재사용) | look-ahead(거울상: 조용한 결측) |
| EG14 | 파티션 경계 누락 | 폐기형 | 전 | `<table>.expected_partitions` | 레짐 편향 |
| EG15 | 폐지 직전 가격 존재 | 기록형→폐기형 | 1·2 | `security.pre_delist_price_days`·`_min` | 생존편향 |
| EG16 | 가짜 재상장 | 폐기형 | 1 | `security_span.respan_verified_n` | 생존편향 |
| EG17 | 캘린더 무결성 | 폐기형 | 1·3 | — | 레짐 편향 |
| EG18 | 조인 팬아웃 | 폐기형 | 전 | — | 재현성 |
| EG19 | as-of 단조성 | 폐기형 | 7 | `<view>.monotonic_asof_pairs` | look-ahead |
| EG20 | 원주가 불변 | 폐기형 | 2 | — | 조정 오류 |

---

## 7. 실행 규약

### 7-1. 실행 순서

테이블 하나의 빌드 안에서 게이트는 **이 순서로만** 돈다. 앞 게이트가 실패하면 뒤는 실행하지 않고 `skip(upstream_failed)` 로 기록한다(stage 는 전량 실행하지만, 조인층은 EG0 실패 상태에서 EG1 을 돌리면 오해를 부르는 숫자가 나온다).

```
EG0  선언·입력 고정        ← 실패하면 나머지 전부 skip(upstream_failed)
 ↓
EG7  범위·부호 (격리형)     ← 먼저 격리해야 EG1 우변의 reject 항이 확정된다
 ↓
EG1  격자 등식
 ↓
EG18 조인 팬아웃 · EG14 파티션 경계 · EG17 캘린더 무결성
 ↓
EG2  PIT 불변식 · EG13 available 미래값
 ↓
EG3  키·불변식 · EG12 단위 접미사 · EG20 원주가 불변
 ↓
EG6  판본 선택
 ↓
EG4  골든 픽스처
 ↓
EG8  교차 소스 · EG9 레짐 커버리지 · EG15 폐지 전 가격 · EG16 가짜 재상장
 ↓
EG5  회귀·재현성 (a·b)
 ↓
[테이블 커밋]
 ↓
(6단계) EG2_dataset_profile · EG9 분위 커버 · **EG10 팩터 준비도**
 ↓
(7단계 전역) EG11 뷰 결정성 · EG19 as-of 단조성 · EG5c · EG-C ①~⑩
   (①②③④⑤⑩ 은 S07 부터 2단계 직후 `equity contract` 로 선행 실행 — §9 S07)
```

**EG7 이 EG1 보다 먼저**인 것이 stage 와 다른 점이다. stage 는 `stage_all` 한 번의 SELECT 에서 reject 를 갈라 두 게이트가 같은 스냅샷을 봤지만, equity 는 격리 사유가 테이블마다 달라 순서를 명시해야 한다.

### 7-2. 실패 리포트 `data/equity/_failed/<build_id>.json`

stage `build.py:498-502` 의 형식을 계승하고 조인층 필드를 더한다.

```json
{
  "table": "universe_daily",
  "build_id": "20260905T101500123456",
  "snapshot_id": "",
  "inputs": {"stg_listing_daily": "20260903T...", "stg_index_daily": "20260903T..."},
  "n_src": 10890251,
  "n_stage": 10890138,
  "n_reject": 113,
  "n_reject_by_reason": {"pre_calendar": 113},
  "first_failed_gate": "EG1",
  "failed_predicates": [
    {"gate": "EG1", "predicate_id": "§3-⑦", "expected": 10890251, "got": 10890138,
     "delta": -113, "sql": "…", "sample_path": "_failed/20260905T101500123456/EG1_sample.parquet"}
  ],
  "gates": [ {"name": "EG0", "status": "pass", "detail": "선언 대조", "metrics": {}}, … ]
}
```

- `failed_predicates[].sample_path` — 차집합 키 최대 200행을 parquet 으로 남긴다. **본문에 값을 싣지 않는다**(로그 폭주 방지, `.claude/rules/error-messages.md` 규약).
- `sql` 은 실제로 실행한 문자열 그대로. 재현이 가능해야 한다(baseline 의 `sql` 필드와 같은 이유).
- 실패 시 `_tmp` 를 지우고 MANIFEST 는 건드리지 않는다 → 구 버전 무손(stage 와 동일).

### 7-3. baseline 승인 루프

```
① 첫 빌드: baseline 미등재 게이트는 skip(no_baseline) + 측정치를 _meta.gates[].metrics 에
② 사람이 _meta 를 읽고 값을 판단 → data/equity/baseline.json 에 {table, metric, sql, value, measured_at, inputs}
③ 커밋: diff 를 커밋 메시지에 (STAGE_DESIGN §9 규약)
④ 2회차 빌드: 같은 게이트가 정식 pass/fail
```

**승인 없이 통과시키지 않는다.** `skip(no_baseline)` 이 남아 있는 테이블은 7단계 통과 조건(§7-4)에서 걸린다.
**baseline 을 느슨하게 고치는 것으로 실패를 해소하지 않는다** — WORKFLOW §3-3 판단 열의 "EG9 커버율 미달 시 임계를 낮추지 않고 한계로 기록" 을 전 게이트로 확장한다. 임계를 바꾸려면 PR body 에 `.claude/rules/pr-review.md` 4요소 양식으로 근거를 적는다.

`growing=true` metric(수집이 계속 자라는 축 — 컨센서스·문서 인덱스)은 "값이 달라졌다"가 실패가 아니다. stage `baseline.py:Metric.growing` 규약을 그대로 쓴다.

### 7-4. 7단계 최종 게이트

```sql
-- (a) 전 테이블 fail 0
SELECT count(*) AS n_fail FROM (
  SELECT UNNEST(builds[-1].gates) AS g FROM _eq_manifest) WHERE g.status = 'fail';
-- (b) no_baseline 잔존 0
SELECT count(*) AS n_pending FROM (
  SELECT UNNEST(builds[-1].gates) AS g FROM _eq_manifest) WHERE g.detail = 'no_baseline';
-- (c) 카탈로그 집합 차 0 (WORKFLOW §3-0 통과 조건의 술어판)
SELECT (SELECT count(*) FROM (SELECT "table" FROM _reg_table
                              EXCEPT SELECT "table" FROM _eq_manifest))
     + (SELECT count(*) FROM (SELECT "table" FROM _eq_manifest
                              EXCEPT SELECT "table" FROM _reg_table)) AS n;
-- (d) EG-C ①~⑩ 전량 pass (pytest 결과를 gates[] 에 접어 넣은 뒤)
```

### 7-5. 게이트 자체 테스트 — 부정 픽스처

**게이트가 통과했다는 사실만으로는 게이트가 작동한다는 증거가 없다.** §4 의 `FX-N-###` 를 `tests/test_equity_gates.py` 에서 돌린다.

- 실행 방식: 정상 픽스처 슬라이스(서버 stage 의 소형 절단본) 위에서 빌드 → parquet 을 복사한 뒤 **결함을 주입** → 게이트만 재실행 → **해당 게이트가 `fail` 을 내는지** 확인. 원본은 건드리지 않는다.
- 통과 조건: `FX-N-###` 전건이 지정한 게이트에서 `fail`, **그리고 다른 게이트는 그대로 `pass`**(결함 하나가 여러 게이트를 무차별로 넘어뜨리면 어느 게이트가 무엇을 잡는지 알 수 없다).
- 부정 픽스처가 없는 게이트는 CI 가 경고한다. 최소 요구: 항진명제 혐의를 받았던 게이트(EG3-P05·EG6-P01·EG9-P04 — §5-C5·C7·C8)와 방향 오류가 조용한 게이트(EG8-P03 조정 거래량, EG13 미래 available)는 **부정 픽스처 필수**.

### 7-6. 게이트 코드 배치

```
workspace/dongmin/src/equity/
  gates.py       # GateResult·GateStatus 는 stage 것을 import (같은 JSON 형식 유지)
                 # GateContext 는 신규 — 원장 ATTACH·fanout 축이 없다
  predicates/    # 게이트별 SQL 을 .sql 파일로. ${T}·${PK_COLS} 치환
    eg0.sql eg1/<table>.sql eg2.sql …
  baseline.py    # stage baseline.py 의 Metric·measure·write 규약 계승, 원장 대신 _pinned 위에서 측정
  build.py       # 신규 (stage build.py 는 원장 ATTACH·fanout 에 묶여 재사용 불가 — WORKFLOW §1)
  manifest.py    # stage 것을 그대로 import (BuildRecord.inputs 이미 존재)
```

`gates.py` 에서 stage 와 공유하는 것은 **`GateResult`·`GateStatus`·`as_dict()` 형식뿐**이다. `run_all` 은 equity 전용이며 §7-1 순서를 코드로 고정한다.

---

## 8. 미결 (이 문서가 결정하지 못한 것)

1. **`correction_link` 의 소유 층** — DOC_DESIGN §8.1·결정 ④ 는 equity 가 만든다고 했고 DESIGN §4 카탈로그에는 없다. 26 → 27 인지, `disclosure_version` 컬럼 흡수인지 사용자 확정 필요(§5-C15).
2. **`_asof/` 스냅샷 규약** — EG5c 가 성립하려면 필요한데 어느 문서에도 없다. 보관 기간·표본 크기·디스크 비용을 정해야 한다(§5-A9).
3. **`<col>_available_date` 동반 컬럼** — 파생 컬럼 available 규칙을 SQL 로 쓰려면 필요하다. 컬럼 수가 늘어난다(§5-C4).
4. **EG7 `sec_type='other'` 격리 삭제** — 생존편향을 게이트가 만드는 문제(§5-C6). 삭제 판정했으나 DESIGN §8 개정이 필요하다.
5. **EG-C ① 의 엔진 저장소 의존** — CI 에서 엔진을 어떻게 잡을지(서브모듈/설치 의존성) 미정(§5-A13).
6. **뷰 게이트의 실행 주체** — EG11·EG19 는 테이블 커밋 후 전역 단계에서 돈다. 뷰가 깨졌을 때 무엇을 폐기하는가(뷰 카탈로그만? 마지막 테이블도?) 미정.

---

## 9. 초안 → v1.0 정정 기록 (오케스트레이터 검증, 2026-09-05)

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| A13 | 엔진은 별도 저장소, `skip(engine_absent)` | 같은 모노레포(`backend/`) — skip 없음, S07·S21 이 backend 테스트 green 요구 | `git ls-files` 로 `backend/src/backtest_engine/ports/*.py`·`backend/tests/test_bar_source_contract.py` 확인 |
| C17 | `stg_consensus_monthly` 뿐 | 기각 — annual·quarterly·matrix 실재 | HANDOFF §3 행수, 서버 `ls data/stage` |
| C15 | `correction_link` 27번째 테이블 또는 흡수 | **흡수** — `disclosure_version` grain 을 `rcept_no` 로 내리고 링크 컬럼을 품는다(§2 매트릭스의 `correction_link` 행은 `disclosure_version` 링크 컬럼군으로 읽는다) | `reviews/2026-09-05-equity-doc-integration.md` §1 (DOC_DESIGN §8.1 흡수안) |
| §8-2 `_asof/` | 미결 | 채택 — `data/equity/_asof/<view>/<build_id>/` 에 고정 표본(baseline `asof_sample`: 날짜 5 × 종목 20) 결과 parquet, keep=3 | EG5c·EG11·EG19 실행 근거 |
| §8-3 동반 컬럼 | 미결 | 채택 — 파생 컬럼마다 `<col>_available_date` | EG2-P05 |
| §8-4 EG7 `sec_type='other'` | 삭제 판정 | 채택 — 행 유지·플래그·기록형 | 생존편향 |
| §8-5 엔진 의존 | 미결 | 같은 저장소(위 A13) | — |
| §8-6 뷰 게이트 실행 주체 | 미결 | 채택 — 7단계 카탈로그 생성 단계에서 실행, 실패 시 카탈로그(`equity.duckdb`)만 교체하지 않고 테이블은 유지. 카탈로그 없는 상태는 소비 불가로 간주 | 카탈로그는 파생물 |
| EG10 이름 | 팩터 재료 커버율 | **팩터 준비도** — `factor_readiness` 테이블(54행)을 6단계 산출로 두고 EG10 이 그 표를 판정 | WORKFLOW v1.2 S20 |
| §3-⑦ · FX-1-011 (S03 구현, 09-05) | EG1 우변에 `gap_cells`(backfill_end 이후 × 활성 티커) 항, FX-1-011 은 2026-08-21 행 `status='coverage_gap'` | **coverage_gap 행 폐기** — DESIGN v1.2 §4-1(GAP-21: 만들고 소비를 금지하는 데이터)과 P16(캘린더 gap 축 없음)에 맞춰 ⑦ = Σ `security_span.n_days`, FX-1-011 = backfill_end 행 `listed` + 다음 날 행 부재 | `rules_s03.UNIVERSE_DAILY.eg1_rhs_sql` · `fixtures/universe_daily.json` |
| §2 매트릭스 7·8행 · FX-1-017 (S03 구현, 09-05) | 7행 픽스처 `006,011,012,013,014` · 8행 `FX-1-015` · FX-1-017 키 (`liquid`, 1) | §4 카탈로그와 어긋났다 — FX-1-012(`no_trade_run`)는 S03B 컬럼, FX-1-015 는 KOSDAQ 관리종목(`universe_daily`), 정책표 픽스처는 FX-1-017. v1.2 가 S03 을 `all` 만으로 좁혔으므로 FX-1-017 키는 (`all`, 1) | WORKFLOW v1.2 §3-1 S03/S03B 분리 |
| EG3-P09 (S03 구현, 09-05) | 술어 그대로 | 술어는 유지하되 실효 범위를 명시 — `end_reason ∈ {delisted, coverage_gap}` 끝의 열린 정지는 정상(재거래 없음 930·현재 정지 중)이라 술어 밖이고 `n_halt_open_at_delist`·`n_halt_open_at_coverage_end` 로 기록. 술어가 잡는 것은 `data_gap` 끝뿐이며 부정 픽스처는 합성 `security_span` 입력의 `end_reason='data_gap'` | `rules_s03.eg3_universe` · `tests/test_equity_s03_universe.py` |

**S04 `price_daily` 구현 정정 (2026-09-05, `rules_s04.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG7-P01 술어 | `close <= 0 ∨ (open ≤ 0 ∧ open IS NOT NULL) …`(말줄임) | `close ≤ 0 ∨ open ≤ 0 ∨ high ≤ 0 ∨ low ≤ 0` (NULL 은 위반 아님 — stage 가 O/H/L '0' 을 이미 NULL 로 둔다) → `_reject/nonpositive_price/`. 격리 사유 `off_calendar`(`trading_calendar` 에 없는 date) 추가 | stage `rules_krx` O/H/L `zero_is_missing` · 절단본 `close ≤ 0` 0건 |
| §3-⑧ 두 번째 식(교집합 0) | 별도 SQL | 프레임 EG1 은 등식 1개만 받는다. 두 원천을 **UNION ALL(dedup 없음)** 하므로 겹치면 키 중복이 되어 EG3-P01 이 먼저 폐기하고, 명시 건수는 `EG3_price_daily.n_src_overlap` 이 싣는다(EG3 뒤에 돌아 겹침 시 `skip(upstream_failed)`). 절단본 교집합 0 | `tests/test_equity_s04_price.py::test_두_원천이_겹치면_키_중복으로_EG3가_폐기한다` |
| EG20 술어 | `close`·`volume_shr` 2축 | `open`·`high`·`low`·`value_krw` 까지 6축(원칙 ② 는 OHLC 전부). 컬럼별 건수 `n_<col>_changed` 를 metrics 에, 하나라도 >0 이면 FAIL. 어느 원천에도 없는 산출 행은 coalesce NULL 로 같이 잡힌다 | 분할 전 `close/50` 주입 → 2,060행 FAIL, 다른 축 0 |
| EG8-P01(`krx_kis_close_ratio` 대조) | 첫 빌드 `skip(no_baseline)` | **S04 미구현** — 독립 KIS 가격 stage 테이블이 없고 누적계수(S06)가 있어야 대조가 된다. S06 이후 `adj_factor` 와 함께 붙인다. `_meta` 에 EG8 항목 자체가 없다(skip 도 아님) | DESIGN §4-2 (`krx_kis_close_ratio` 컬럼 삭제) |
| FX-2-005 · FX-2-006 키 | "ETF 1종 1일" · "`volume_shr=0` 인 날 1" | (`069500`, 2010-01-04) · (`000030`, 2019-01-09, 폐지 전 거래정지 22일 중 첫날). FX-2-002 는 (`005930`, 2018-05-03·05-04) 쌍 — 05-03 은 `volume_shr=0`(분할 정지)이라 `reference` 이기도 하다 | `src/equity/fixtures/price_daily.json` |
| `_meta` 기록형 | `open IS NULL ∧ volume>0`(GAP-14)만 | `EG3_price_daily.metrics` 에 GAP-14 + `n_off_calendar`·`n_nonpositive_price`·`n_price_kind_null`·`n_shares_out_null_stock`·`n_par_value_null_stock`·`n_mktcap_stage_mismatch`·`n_shares_out_stage_mismatch`·`n_reference_with_value`. 전부 통과 조건 아님 — 서버 실측 뒤 승격 판단 | GATES §0-1 기록형 |
| baseline 상수 | — | S04 는 새 상수 없음(`baseline_seed_s04.json` 이 이유를 기록). EG7 비율은 코드 기본값, 2회차 이관은 오케스트레이터 | §1 EG7 |
| §3-⑨ `n_dedup` 출처 (S05, 09-05) | "dedup 은 `_meta.n_dedup` 이 낸다" | 프레임 `build.py` 가 `_meta.n_dedup` 을 0 으로 고정하므로 `corp_event` 는 산출 컬럼 `n_src_rows` 로 접힌 수를 싣고 EG1 좌변을 `count(out) + Σ(n_src_rows−1)` 로, `n_dedup` 은 `EG3_corp_event` metric 으로 낸다. 우변은 등록표 `rules_s05.SOURCES`(= `_reg_corp_event_source`) 합. reject 후보는 접지 않고 각각 격리해 `Σ후보 = Σ_out n_src_rows + n_reject` 가 성립한다 | DESIGN §4-2 · P21 (22 = 8 + 14 + 0) |
| ⑨ KRX 원천 유형·방향 불변식 (S05 3차, 09-05) | KRX 행은 액면가 방향으로 split/reverse_split, ratio 는 주식수 비 | 서버 EGC-04 fail(`007195:split:2013-05-24` share_factor 0.833 — 감자 뒤 액면 변경, 모순 11건/split 268). 유형은 **주식수 비 방향**(ratio > 1 split · < 1 reverse_split, 액면가 변화는 트리거만) · 주식수 불변 액면 변경(|ratio−1| ≤ `corp_event.krx_share_change_tol` 1e-3)은 범위 밖 `krx_par_only`(기록형) · **EG3_corp_event 에 방향 불변식 추가(폐기형, 원천 무관)**: split·bonus → ratio > 1, reverse_split·capred → ratio < 1. 재판정 CLI(`gate`)가 빌드와 같은 `_const` 를 만들도록 `__main__._cmd_gate` 에 `make_consts` 1줄 추가(EG1 우변이 pool CTE 의 상수를 읽는다) | DESIGN §4-2 · P21 |
| §3-⑨ 모집단 = 범위 안 후보 (S05 2차, 09-05) | "Σ 원천 행수" — 원천 행 전부가 모집단 | 서버 1차 빌드가 EG7 에서 폐기(격리 8,834 / 산출 3,865): 자본변동은 창립 이래 이력을 회고 기재해 캘린더 밖(1963~2009)·비상장 종류주·상장 전 사건이 격리로 쏟아졌다. 조정할 가격이 없는 행은 **격리가 아니라 모집단 밖**(`scope_out` 4종: out_of_calendar·unlisted_class·class_unknown·pre_listing, 기록형 metric)이고 EG1 우변은 `pool WHERE scope_out IS NULL`(원천별 합). 우변 SQL 은 `.sql` 의 pool CTE 를 재사용한다(모집단 정의 단일화) — 대신 전개 전 원천 행수를 stage 뷰로 독립 기록하고 산출 행의 캘린더·상장 전 위반 0 을 `EG3_corp_event` 가 독립 재검사한다 | DESIGN §4-2 · P21 |
| EG7 비율 분모 (S05) | `Σ reject / n_src`(EG1 우변) | 프레임 `gates.eg7_range` 는 `n_reject / (n_out + n_reject)` — dedup 이 있는 테이블에서는 두 정의가 갈린다(`corp_event` 절단본 0.176 vs 0.375). 프레임 미수정, `corp_event.threshold_EG7` seed 는 프레임 정의로 측정. 정본 정의는 미결(§8) | P21 |
| FX-2-003 (S05) | `corp_event` 무상증자 1.2:1 사례 1 (doc:2단계 기록) | 절단본·seed 픽스처 파일에는 사례가 없어 `tests/test_equity_s05_event.py` 의 `stg_event_fric` 손 트리(SK하이닉스 0.2 배정 → ratio 1.2)로 검증하고, 서버 빌드 뒤 실사례를 `fixtures/corp_event.json` 에 등재한다 | 절단본 `stg_event_fric` 0행 |
| EG8-P04 (S05) | `corp_event` 첫 빌드 skip(no_baseline) | 분모(기준가≠전일종가)가 `price_daily` 를 요구하므로 S05 에서는 술어를 붙이지 않고 S06 이후에 `corp_event` 재판정으로 붙인다 | DESIGN §4-2 |
| ⑨ 종류 어휘 대응표 (S05 후속, 09-05) | `stg_capital.isu_dcrs_stock_knd` 는 정규 표기 9종 리터럴만, 그 외 전부 `class_unknown`(서버 437 → stage 정규화 뒤 375) | stage 가 공백·개행·NFKC 를 정규화(PR #70)했으므로 equity 는 **의미 매핑만** 맡는다 — `rules_s05.COMMON_KINDS`(43)·`PREFERRED_KINDS`(34)·`UNLISTED_KINDS`(93) 명시 문자열 대응표(패턴 매칭 없음, `.sql` IN 리터럴과 순서까지 대조하는 `test_주식종류_어휘는_rules_선언과_SQL_리터럴이_같다`), 모호한 것('〃'·'-'·복수 종류·'종류주식'·'의결권 있는 주식'·집계 행·숫자)은 unknown 유지. `EG3_corp_event` 기록형에 **`n_class_unknown_mvp`**(MVP 유형 행 중 표 밖)·**`n_class_mapped_by_alias`**(`CANONICAL_KINDS` 9종 밖 별칭으로 대응된 행) 추가 — `class_unknown_kinds` 에 새 문자열이 늘거나 별칭 비중이 튀면 원천 서식 변화 신호. `.sql` 의 `trim` 은 옛 절단본 방어용이라 규칙의 본질이 아니다. `test_sql파일에_상수_하드코딩_없음` 은 문자열 리터럴('2우선주')을 상수로 세지 않도록 따옴표 안을 먼저 걷어낸다. 절단본 수치 불변(산출 8), 손 픽스처 4건(보퉁주 → 005930 · 2우선주 → 005935 · RCPS → unlisted_class · 〃 → class_unknown) | DESIGN §4-2 · P21⁵ |

**S03B `universe_daily` v2 · `universe_policy` 구현 정정 (2026-09-05, `rules_s03.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §5-B8 `no_trade_run_k` | metric 이름만 | `baseline_seed_s03.json` 에 **제안값 5** 등재(`_measured.note` 에 근거·"사람 승인 필요"). 절단본 무거래 run 분포 {1×7, 3×3, 10, 12, 22, 49, 55, 66, 116}, k=3/5/10/20 에서 run 으로만 suspended 211/201/181/148행. 승인 축은 서버 `EG3_universe.no_trade_run_hist`(1 / 2-4 / 5-9 / 10-19 / 20+)·`n_suspended_by_run` | DESIGN §4-1 S03B · §10 P6(첫 거래 중앙값 4일) |
| `universe_daily` 입력 | S03 `stg_price_daily` ∪ `stg_etf_price_daily`(`volume_shr`) + S03B 에 `price_daily` **추가** | stage 가격 두 원장을 equity `price_daily` 로 **대체**(입력 7개) — 같은 값(EG20)이고 거래 재개 축·시장 파생이 한 조인에서 나온다. `_reject`(nonpositive_price·off_calendar) 행은 격자 밖이거나 `volume>0 ∧ close≤0` 라 halt 닫힘 축에 실효 없음 | DESIGN §4-2 EG20 · 서버 RSS 3.9GB |
| `listing_age_days` 기준 | DESIGN "`D − security.list_date`" | **같은 날 `stg_listing_daily.list_date`**(PIT). `security.list_date` 는 티커당 최신 listing 행 값이라 재상장 2종의 첫 구간(036220 1,570 + 101970 648 = 2,218행)이 음수 — 부정 픽스처 `test_security_list_date로_상장일수를_재면_…`. 결측(ETF)은 구간 `first_date` 하한, basis 는 컬럼 없이 `security.list_date_basis`/`sec_type` + 기록형 `n_listing_age_fallback[_stock]` | 절단본 `stg_listing_daily` 036220 2007-06-05 → 2024-03-13 |
| `no_trade_run` 결측 | "NULL 전파 vs run 끊기" 미결 | 가격 행 없음(`price_kind` NULL)은 그날 NULL 이고 run 을 **끊는다**(다음 무거래일 1). 게이트 `n_no_trade_run_null_mismatch`(NULL ⇔ price_kind NULL)·`n_no_trade_run_sign_mismatch`(0 ⇔ trade, >0 ⇔ reference) | 합성 stage 테스트 K00010 td5 |
| EG3_universe | S03 술어 | `n_status_halt_mismatch` 를 `(status='suspended') ⇔ coalesce(halt_state ∨ (price_kind='reference' ∧ run ≥ k), false)` 로 재정의(`price_kind` 는 입력 `price_daily` 재조인). 추가 폐기형: `n_no_trade_run_negative`·`n_mktcap_null_mismatch`·`n_mktcap_price_mismatch`·`n_adv20_null_mismatch`(독립 창 카운트 = `adv_window_td`)·`n_listing_age_null`·`n_listing_age_negative`·`n_listing_age_listing_mismatch`. 기록형: `n_suspended_by_run`·`no_trade_run_hist`·`no_trade_run_max`·`n_adv20_null`·`n_mktcap_null`·`n_listing_age_fallback[_stock]`·`adv20_common_quantiles`(p10/25/50/75/90, `liquid` 임계 근거). k·창 폭 미등재면 게이트 전체 `skip(no_baseline)` | GATES §0-1 기록형 · §5-C7(항진명제 회피: run 자체는 재계산하지 않고 부호·NULL 정합만) |
| `adv20` 창 폭 리터럴 | SQL 에 `19 PRECEDING`·`= 20` | `test_equity_build::test_sql파일에_상수_하드코딩_없음`(허용 {0,1,2,-1}) 이 잡는다 → baseline `universe_daily.adv_window_td`=20 을 `_const` 로 주입, 프레임 경계 `(adv_window_td − 1) PRECEDING`(duckdb 컬럼식 프레임 지원 확인). 컬럼 이름 adv20 이 못박은 값이라 사실상 불변 | §1-12 등록부 |
| `universe_policy` 행 | `all` 1행, `common-stock` 은 sec_type 축(정책 행 아님, S21) | **7행** `all`·`common-stock`(sec_type='common' ∧ status='listed')·`investable`(+ NOT admin_state, NOT liquidation_window), 전부 flag. `POLICY_VOCAB` 에 `common-stock` 추가 → `universe_id = 'krx.' \|\| policy` 문법 그대로 `krx.common-stock`(소비자 계약 값). `rule_seq` 는 `generate_subscripts`(리터럴 금지). `liquid` 행은 서버 adv20 분위수 뒤. `version` s03-v1 → s03b-v2 | FIELD_MAP §1 · DESIGN §7 |
| §2 매트릭스 7행 · FX-1-012 · FX-1-017 | 012 는 S03B / 017 은 (`all`, 1) | 012 를 `universe_daily` 픽스처에 편입(a~g: run 22·1·0, k 경계 01-15/01-14, 101970 해제일 무거래) + mktcap 2·adv20 4·listing_age 5 케이스(총 52). 017 은 f~j(common-stock·investable) 추가 | `fixtures/universe_daily.json`·`universe_policy.json` |

**S03B-2 `universe_daily.adv20_rank_pct` · `universe_policy` `liquid` 구현 정정 (2026-09-05, `rules_s03.py` — 사용자 결정 "날짜별 상위 비율 기준으로 가자")**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| `liquid` 임계 종류 | `adv20_krw` 절대 금액 quantile(서버 P22′ 분위수를 표에 박는다) | **날짜별 상위 비율** — `universe_daily` 에 `adv20_rank_pct`(같은 날 모집단 `sec_type='common' ∧ status='listed' ∧ adv20_krw IS NOT NULL` 안 `adv20_krw` 의 **cume_dist**, (0, 1]·클수록 유동성 큼·날짜별 최댓값 1, 모집단 밖 NULL) 컬럼을 `adv20_krw` 다음에 두고, `liquid` = investable 4행 + `adv20_rank_pct >= 1 − liquid_top_pct` 1행(`threshold_kind='quantile'`, `threshold_value` = baseline `universe_policy.liquid_top_pct` **0.5**). 절대 금액은 15년 사이 물가·시장 규모로 뜻이 변하고 P22′ p50 은 전 기간 합산값이라 어느 해에도 중앙값이 아니다 | DESIGN §4-1 · §10 P22′·P26 |
| 순위 정의 | `percent_rank` 또는 `cume_dist` 중 택일 | **`cume_dist`** — 최솟값 행이 1/n > 0 이라 "(0, 1]" 을 만족하고 동률은 같은(큰 쪽) 값을 받아 임계에서 임의로 갈리지 않는다. `percent_rank` 는 최솟값 0(부정 픽스처 `n_adv20_rank_out_of_range` = 모집단 있는 날 4,075) | `test_percent_rank로_순위를_내면_…` |
| 창 비용 | — | 날짜 파티션 창 1개(`PARTITION BY date, in_pop ORDER BY adv20_krw`) — 모집단 밖 행은 자기들끼리 한 파티션이라 순위 계산에 섞이지 않고 CASE 로 NULL. 별도 좁은 투영 + 재조인 안(참조 2회 → CTE 재계산 위험)은 택하지 않음 | 절단본 0.8s → 1.3s |
| `liquid` 행 `basis`·`measured_at` | 초안 SQL 주석 "quantile·measured·measured_at = 잰 날" | **`convention`·NULL** — 비율은 잰 값이 아니라 사람이 고른 값이고 순위는 날마다 재계산되므로 "잰 날" 이 없다. P22′ 분위수는 baseline `_measured.note` 의 근거로만 남긴다 | FX-1-017p |
| 술어 문자열 | `adv20_rank_pct >= 1 - liquid_top_pct` 를 소비자가 baseline 을 읽어 푼다 | 표에 **`adv20_rank_pct >= 0.5`** 로 박는다(`'adv20_rank_pct >= ' \|\| CAST(1 − k.liquid_top_pct AS VARCHAR)`, `_const` DECIMAL 산술이라 문자열이 깔끔) — 워크벤치 어댑터는 정책표만 읽고 `_const` 를 모른다. 리터럴 금지 규약은 `_const` 통로로 지킨다 | `test_liquid_임계는_baseline_liquid_top_pct에서_온다`(0.2 → `>= 0.8`) |
| EG3_universe 추가 술어 | "모집단 크기 재계산" | 폐기형 `n_adv20_rank_out_of_range`(≤ 0 ∨ > 1) · `n_adv20_rank_null_mismatch`(NULL ⇔ 산출 컬럼으로 다시 가른 모집단 밖) · `n_adv20_rank_max_not_one`(모집단 있는 날의 max ≠ 1) · `n_adv20_rank_pop_mismatch`(rank × 재계산 모집단 크기가 정수가 아님, tol `RANK_INTEGRAL_TOL = 1e-9` — 산출 정밀도 상수) · `n_adv20_rank_order_violation`(같은 날 adv20 오름차순으로 rank 가 내려감, 좁은 투영 위 lag 창 1개). 기록형 `n_adv20_rank_null` · `n_dates_with_rank_pop` · `n_dates_without_rank_pop` · `adv20_rank_pop_size`{min, p50, max}. cume_dist 자체는 다시 돌리지 않는다(§5-C7) — 값은 FX 손계산 | 부정 픽스처 3(percent_rank · 전 종목 모집단 → null_mismatch 19,248 · NULL→1 → 19,571) |
| EG3_policy 추가 술어 | 바인딩만 | + `n_quantile_threshold_out_of_range`(quantile 의 `threshold_value` ∉ (0, 1] → 폐기; 1.5 면 `>= -0.5` 로 모집단 전부) · 기록형 `quantile_rows` | `test_liquid_top_pct가_비율_밖이면_…` |
| §1-12 등록부 | — | `universe_policy.liquid_top_pct`(convention, 사용자 선택) · `universe_policy.version` s03b-v3 추가 | seed `_measured` |
| FX 픽스처 | "2018-05-03 보통주 8종목 중 005930 = 1.0" | 절단본 2018-05-03 모집단은 **5**(900050 은 `foreign`, 036220·101970 은 구간 밖, 247540 은 2019-03 상장) — 005930 = 1.0 은 맞다. 8종목 날은 backfill_end 2026-08-20(000660 = 1.0, 005930 = 0.875). 11케이스 추가(총 63), 정책 6케이스 추가(k~p, 총 16) | `fixtures/universe_daily.json`·`universe_policy.json` |
| 서버 규모 | — | universe_daily 10.9M 행 위 날짜 창 1개 추가(창 입력 = 22컬럼 wide 행 전체 재정렬). 추정 RSS +1.0~1.5GB(현 4.5GB → 5.5~6GB, memory_limit 6GB 경계 — 스필은 temp_directory) · 시간 +20~40%. 6GB 에서 스필이 길면 `--memory-limit 8GB` 로 재시도. baseline 추가분: `universe_policy.liquid_top_pct` 0.5 · `version` s03b-v3 | P22′ 대비 추정, P26 |


**S03C `universe_daily.no_trade_reason` · `status` 규칙 · `universe_policy` liquid 구현 정정 (2026-09-05, `rules_s03.py`·`sql/universe_daily.sql`·`sql/universe_policy.sql` — 사용자 결정 "무거래 연속 정지가 어떤 이유인지 데이터를 받고 판단하는 로직")**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| 컬럼 위치 | "`adv20_rank_pct` 다음, `available_date` 앞"(두 문구가 범위를 다르게 읽힌다) | **`adv20_rank_pct` 바로 다음**(= `listing_age_days` 앞) — S03B-2 의 "`adv20_krw` 다음" 과 같은 규약으로 읽어 두 문구를 동시에 만족시킨다. 컬럼 23개 | `test_컬럼_선언순서가_산출과_같다` |
| ③ 창 구현 | "같은 티커 `adj_factor` 행의 `apply_date` 가 [D − lb, D + la] 안" | **사건 쪽에서 펼친다**(`ca_win` CTE): `apply_date ∈ [D − lb, D + la] ⇔ D ∈ [apply − la, apply + lb]` 를 캘린더 위에서 전개해 (ticker, td_seq) 동등 조인으로 붙인다. ① 10.9M 행 위 범위 조인을 피하고 ② 적용일이 그 티커의 **구간 밖**(폐지 기간)이어도 구간 안 D 가 창에 들면 잡힌다(격자에 플래그를 심는 방식은 이 경우를 놓친다) | 절단본 101970 감자 5건이 전부 폐지 뒤 적용일 |
| ④ admin 창 경계 | "`signal_admin` 이 D 전 n 세션 안"(부등호 미지정) | **`td_seq − last_admin ≤ n`** = 창 [지정일, 지정일 + n세션], 지정일 당일 포함. `admin_window_td`(365)가 쓰는 `<` 와 다르지만, S03C 의 두 창(③ 의 대괄호 표기·④)을 같은 눈금으로 맞췄다. 값이 5 라 경계 1세션 차이의 실질 영향은 admin ↔ illiquid 1일 | 설계 §4-1 대괄호 표기 |
| `price_kind` NULL 행 | "무거래 행만 판정, 거래 행은 none" | 가격 행이 없는 날(`price_kind` NULL, `no_trade_run` NULL)도 **`none`** — 판정 대상은 `price_kind='reference'` 뿐이라는 술어를 `IS DISTINCT FROM 'reference'` 로 닫는다. 어휘에 NULL 을 들이지 않는다 | EG3_universe `n_no_trade_reason_null` = 0 |
| `n_suspended_by_run` 뜻 | halt 밖 suspended 전부 | S03C 로 두 갈래가 되므로 **`illiquid ∧ run ≥ k` 로 좁히고** `n_suspended_by_corp_action` 을 새로 기록한다. 이름은 서버 P22′(74,824)와 잇기 위해 유지 | 절단본 201 + 6 |
| EG3_universe 추가 술어 | "어휘·거래 행 none·halt 행 halt_disclosed·우선순위 재계산" | 폐기형 6 + 1: `n_no_trade_reason_outside_vocab` · `n_no_trade_reason_null` · `n_no_trade_reason_trade_not_none` · `n_no_trade_reason_no_trade_none`(무거래인데 none) · `n_no_trade_reason_halt_mismatch` · `n_no_trade_reason_recompute_mismatch`(입력 `adj_factor`·`trading_calendar`·`security_span`·`price_daily` 에서 창과 `last_admin` 을 새로 짜 우선순위 전체를 재계산) · `n_status_reason_mismatch`(재계산 이유로 status 규칙 재판정) + `n_adj_apply_off_calendar`(적용일이 캘린더 밖이면 창이 조용히 비므로 폐기형). 기록형 `no_trade_reason_counts` · `no_trade_reason_run_hist_illiquid` · `n_liquid_excluded_by_illiquid` · `n_adj_apply_rows`·`_not_ok` · `adj_apply_event_types` · 창 상수 3 | 부정 픽스처 2(①↔② 스왑 → 4행 · ③ 제거 → 6행) |
| `status` 규칙 | S03B `halt ∨ (reference ∧ run ≥ k)` | **`halt ∨ corp_action_window ∨ (illiquid ∧ run ≥ k)`**. 정리매매·관리종목 무거래는 더 이상 status 로 빼지 않는다 — 같은 사실을 status 와 `universe_policy.investable`(`NOT liquidation_window`·`NOT admin_state`) 양쪽에서 두 번 빼면 두 축의 뜻이 겹친다. 합성 stage 로 확인: 정리매매 무거래 9세션(run 9 ≥ k)이 `listed` | `test_정리매매_무거래는_run이_길어도_정지가_아니다` |
| `universe_policy` liquid | investable 4행 + quantile 1행(rule_seq 5) | + **flag 행 `no_trade_reason <> 'illiquid'`(rule_seq 5)**, quantile 은 6 으로 밀린다(`len(predicates) + 1` 규약 그대로). 13행. `investable` 은 건드리지 않는다 — 유동성 조건이지 투자가능성 조건이 아니다 | FX-1-017q·r·s |
| FX 픽스처 | S03B-2 의 "005930 2018-05-03 `adv20_rank_pct` = 1.0" | 그 셀은 이제 **NULL** — 05-04 분할 apply 앞 무거래라 corp_action_window·suspended → 모집단 밖. 픽스처를 모집단 비의존 NULL 불변식으로 바꾸고(`adv20_rank_null_corp_action_005930_20180503`) `no_trade_reason` 8케이스를 더했다(총 67). 정책 6케이스 추가(총 19) | P26′ 모집단 의존 픽스처 실패 교훈 |
| 입력 추가 | — | equity **`adj_factor`**(`ticker`·`apply_date`·`factor_ok`·`event_type`) — 순환 없음(adj_factor 는 universe_daily 를 읽지 않는다). 테스트 상류 체인이 8테이블로 늘어난다(trading_calendar → security → security_span → corp → corp_ticker → price_daily → corp_event → adj_factor) | `rules_s03.UNIVERSE_DAILY.inputs` |
| §1-12 등록부 | — | `universe_daily.corp_action_lookback_sessions` 5 · `corp_action_lookahead_sessions` 45 · `admin_signal_window_sessions` 5(전부 convention·사람 승인 대기) · `universe_policy.version` s03c-v4 | seed `_measured` |
| 알려진 한계 | — | 거래소 매매거래정지 **현황 목록**은 미수집이라 공시 텍스트로 안 잡히는 정지는 `illiquid` 로 남는다. 절단본에서 900050(HK 상장사) 198행이 그 예 — 안전 쪽으로 실패한다(run ≥ k 면 여전히 suspended). stage 수집 계획 항목 | DESIGN §4-1 |


**S06 `adj_factor` · 뷰 매크로 · 카탈로그 게이트 구현 정정 (2026-09-05, `rules_s06.py`·`sql/adj_factor.sql`·`views.py`·`catalog.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG3-P04 상수 | `bl('adj_factor','factor_product_tol')` | **baseline 미등재**. `price_factor = 1/ratio` 를 DOUBLE 로 두므로 곱은 1 ± 몇 ulp 다 — 허용오차는 산출 정밀도 상수 `FACTOR_PRODUCT_TOL = 1e-12`(실측 max \|1/x·x−1\| = 1.1e-16, x ∈ [1, 1e5]). 방향 오류(역수·제곱)는 1e-12 로 못 숨긴다(FX-N-003 실측 dev > 0.9). `max_ok_product_dev` 를 metric 으로 기록 | DESIGN §4-2 · `test_equity_s06_adj.py::test_FX_N_003…` |
| EG2-P02 축(`adj_factor`) | `available_date ≥ corp_event.announce_date` | **축 없음**(`content_date_column=None`). `available_date = min(announce, 효력일 다음 거래일)` 이라 회고 기재 원천(자본변동, announce 가 사건보다 최대 수년 뒤)은 available < announce 가 정상(절단본 8행 중 4행). 대신 `EG3_adj_factor.n_available_mismatch` 가 캘린더로 독립 재계산해 전건 일치를 요구하고 `n_available_before_announce` 를 기록한다. DESIGN §4-2 의 "EG2 예외: ≥ announce_date 축" 문구는 이 정의와 모순이라 삭제 | 부정 픽스처 available=announce → mismatch 6 |
| `adj_factor` 컬럼·입력 | grain 3 + 계수 4 + available | `corp_code`·`event_type`·`announce_date` 를 함께 싣는다(EG8 필터·EG2 대체 검사·`_asof` 표본이 corp_event 조인 없이 선다). 입력에 **`stg_event_cr`**(`cr_mth`·`cr_rs`) 추가 — corp_event 에 감자 유·무상 축이 없어 결정공시 본문에 '유상' 이 있는 event_cr 행을 `capred_paid` 로 판정한다(brief 의 "입력 = equity 3테이블" 과 다름). `price_daily` 는 산출식에 안 쓰고 EG8 만 읽는다 | DESIGN §4-2 "감자는 S06 이 cr_mth·유무상으로" |
| `factor_source` 어휘(EG3-P13) | 미정 | `mktcap_neutral`(ok 유일) · `ratio_null` · `capred_paid` · `near_dup_suppressed`. 사유 우선순위 near_dup > ratio_null > capred_paid | `rules_s06.FACTOR_SOURCE_VOCAB` |
| 근접 중복 억제 | "같은 (ticker, event_type) 이 창 안에 2건이면 effective_basis 우선순위" | **교차 원천 쌍만**(S05 `n_near_dup_cross_source` 와 같은 축). 같은 원천의 근접 2건은 접수번호가 다른 다른 사건 — 101970 2015-11-26(자기주식 소각+병합, 20160608000216)·11-28(10:1 병합, 20160608000221) 회생 감자 2건을 누르면 10:1 병합이 사라진다. 우선순위 = effective_basis(disclosure_body > krx_shares_change > krx_notice > unconfirmed) → ratio 있는 쪽 → 결정공시 > 자본변동 > KRX → announce → effective → event_id(STRUCT 비교). 창은 `corp_event.near_dup_window_days` 를 `_const` 로 읽는다(`build.make_consts` `<table>.<metric>` 키 신설) · `EG3_adj_factor.n_near_dup_both_ok` = 0 폐기형, `n_near_dup_suppressed` 기록 | 절단본 1차 빌드에서 11-28 이 눌렸다 |
| EG1 ⑩ | `_reg_vocab('factor_bearing_event')` | = `rules_s05.MVP_EVENT_TYPES`(split·reverse_split·bonus·capred). 격리 사유 없음(reject 0) — ok=false 는 행 유지 | `rules_s06.FACTOR_BEARING_EVENTS` |
| EG8-P02 대상 | split·bonus·stock_dividend | **factor_ok 행 전부**(capred 도 시총 불변이라 조정가가 연속이어야 한다). 효력일이 비거래일이면(토요일 기준일 2015-11-28·2018-10-13) 그 뒤 첫 거래일에서 잰다(`n_effective_off_calendar` 기록). 전일은 캘린더 `prev_td` 행(정지일 reference 행 포함 — 005930 05-03 기준가 2,650,000 × 0.02 = 53,000 → 51,900 = −2.1%) | 절단본 실측 |
| EG8-P03 창 | `median(...) OVER (ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)` | 캘린더 인덱스로 이벤트별 직전 20거래일을 잘라 `median` 집계(전 행 윈도 함수 회피 — 서버 10.9M 행). 창 길이는 방법 상수 `VOLUME_MEDIAN_WINDOW = 20`(baseline 아님). 중앙값 0 인 이벤트는 `n_ok_median_zero` 기록 | 메모리 |
| EG8 첫 빌드 | `skip(no_baseline)` 전량 | skip 이되 **측정치를 항상 남긴다**: `asof_for_jump_check` 미등재면 `price_daily` max(date) 로 재고 `asof_basis='max_price_date'`. 세 상수(`asof_for_jump_check`·`adj_return_jump_max`·`adj_volume_jump_max`) 전부 있어야 판정. 계산은 `views` 템플릿을 TEMP MACRO 로 올려 뷰와 같은 식으로 | seed s06 |
| seed 값 | — | `asof_for_jump_check` 2026-08-20(= backfill_end, growing) · `adj_return_jump_max` **0.30**(KRX 가격제한폭 ±30% 가 조정 기준가 대비 한도 → 계수가 맞으면 물리적으로 못 넘는다; 절단본 max 0.0929) · `adj_volume_jump_max` **10**(절단본 max 3.43 의 ~3배). 둘 다 ★ 서버 p100 재측정. 한계: 1.2:1 미만 무상증자 누락(−17% · 1.44×)은 두 축 모두 못 잡는다 | `baseline_seed_s06.json` |
| EG3-P05 실행 주체·날짜 | `adj_factor`/`price_daily` 테이블 게이트, `bl_date('trading_calendar','fixture_date')` 1일 | **카탈로그 단계** `EG3_firm_mktcap`(뷰가 있어야 대조가 된다). 날짜는 `trading_calendar.asof_sample_dates` 5일 전부(`fixture_date` 상수 미등재). 뷰는 `common_ticker` 로 묶고 게이트는 `isin8` 로 묶어 항진명제를 피한다. `universe_daily` status 필터 대신 '그날 가격 행 존재'(listing 존재일 = 가격 (ticker,date) 전건, P20) — universe_daily 를 카탈로그가 요구하지 않게 | 부정 픽스처: 우선주 제외 → mismatch 10 |
| `v_firm_mktcap(d)` 컬럼 | `corp_code, firm_mktcap_krw` | `corp_code`(그룹 min) · `firm_ticker`(common_ticker 또는 자기 티커) · `date` · `n_leg` · `firm_mktcap_krw`. 비KR7·ETF 는 단독 행(corp_code NULL 가능) | DESIGN §5 |
| EG11 | `determinism_asof` 상수, 뷰당 1 as_of | 카탈로그 단계. 고정 표본 = `asof_sample_dates × asof_sample_tickers`(새 상수 없음), 임시 카탈로그를 **별도 read_only 연결 2개**로 열어 `count + bit_xor(hash(row))` 동일. 대상 뷰 `v_cum_adj`·`v_adj_price`(`catalog.ASOF_VIEWS`) | P1b 경로 |
| EG5c · `_asof/` | `_asof/<view>/<build>/`, 표본 5×20, 4단계부터 | `_asof/<view>/<snapshot_id>/part0.parquet` + `_meta.json`(builds·표본·content_hash·written_at), keep=3(`catalog --keep`). 직전 스냅샷과 (as_of, ticker, date) 키 위 행 해시로 `only_current`·`only_previous`·`changed` 를 센다. 첫 실행 `skip(no_previous_snapshot)`(§0-2 어휘 추가) · 차이 > 0 이면 **FAIL 이고 카탈로그를 교체하지 않는다**(`_failed/catalog_<snapshot_id>.json`) · 사람 승인은 `catalog --rebase-asof`(pass, 이번 표본이 새 기준). "새 rcept_dt > asof 로 설명되는 차이" 예외는 가격 뷰(랙 0)에 해당 없음 — 재무 뷰(S12)가 붙일 것 | §8-2 채택안 구체화 |
| 매크로 인자 이름 | `asof` | **`as_of`** — `asof` 는 duckdb 1.5 예약어(ASOF JOIN). §1 EG5c·EG8·§6 EG11/EG19 의 `v_*(asof)` 표기는 `as_of` 로 읽는다 | duckdb 파서 오류 실측 |
| FX-2-004 키 | 감자 1 | (`101970`, 2018-10-12) 결정공시 10주→1주 무상병합: `price_factor` 10.000000659 · `share_factor` 0.0999999934(단수주 절사라 정확히 10 이 아니다) | `fixtures/adj_factor.json` |
| FX-2-007 | `207940` 인적분할 `factor_ok=false` | MVP 에 spinoff 가 없다 → **대체**: (`101970`, capred 2018-02-23) 자본변동 단독 행 `ratio_null` → `factor_ok=false`·계수 1(S05 2차 범위 규칙 뒤 절단본의 유일한 자본변동 단독 사건 — 000030·0001A0 사건은 상장 전·비상장 종류라 모집단 밖). 207940 은 S05 가 spinoff 를 내는 슬라이스에서 복원 | 절단본 |
| FX-2-009 | KRX 관측만 계수 → `available = 효력일 + 1거래일` | KRX 파생행은 announce_date = 관측일 = 효력일이라 `min(announce, 다음 거래일)` = **효력일 당일**(005930 2018-05-04). '+1거래일' 은 회고 기재 원천(공시가 늦은 자본변동)에서만 실현된다(101970 2018-02-23(금) → 2018-02-26) | `fixtures/adj_factor.json` FX-2-009 |
| FX-2-010 키 | (`005930`, 2018-05-03, as_of 2018-06-01) 원 거래량 × 50 | **2018-04-27**(606,216 → 30,310,800). 05-03 은 분할 정지일이라 거래량 0 — 0 × 50 = 0 / 50 이라 방향을 못 가른다. 뷰 픽스처는 파일이 아니라 `test_equity_s06_views.py` 가 든다 | 절단본 실측 |
| FX-N-006 | `v_adj_volume` 나눗셈 → EG8-P03 fail ∧ FX-2-010 fail | 구현: `views.TEMPLATES['v_adj_volume']` 을 나눗셈으로 바꿔 `adj_factor` 재빌드 → **EG8 만 FAIL**(거래량 점프 > 1,000, 수익률 축 위반 0, 앞 게이트 pass) ∧ 04-27 조정 거래량 12,124 | `test_FX_N_006_…` |
| §2 매트릭스 11행 EG7 | `●(P02)` | 격리 사유 없음 — 비율 0 으로 통과. `adj_factor` 는 EG7 격리형 술어를 갖지 않는다(ok=false 는 격리가 아니다) | DESIGN §4-2 |
| `_catalog_meta.json` | snapshot_id·builds·macros | + `gates`(EG11·EG5c·EG3_firm_mktcap)·`asof`(뷰별 경로·행수·해시)·`macros_skipped`(입력 테이블 미커밋으로 못 만든 매크로와 이유) | `catalog.publish` |

**S06 2차 `adj_factor.apply_date` 정정 (2026-09-05, 서버 1차 빌드 EG8 실패 → `sql/adj_factor.sql`·`rules_s06.py`·`views.py`)**

서버 1차(명목 효력일 적용, 3,226행·ok 2,299): EG8 `n_return_jump_over` 520 · `n_volume_jump_over` 101 · `n_ok_median_zero` 833. 원인 실측(ok 중 가격 있는 2,173, 명목일 원수익률 vs 기대 `price_factor−1`, 허용 max(15%·|기대|, 0.05)): 감자 결정공시 명목일 일치 110/667(최적일 오프셋 p50 +13 세션 = 정지 뒤 재개일) · 액면병합 0/151(어느 날도 단독 비율과 안 맞음 = 감자와 복합) · 무상증자 불일치 136 은 대부분 2~5% 잡음.

| 항목 | 1차 | 정정 | 근거 |
|---|---|---|---|
| 컬럼 | 12 | + **`apply_date`**(계수를 가격에 적용하는 세션) · **`apply_basis`** ∈ {`nominal`, `price_matched`, `price_matched_combined`, `unmatched`}(어휘 폐쇄, EG3-P13). grain 불변 | 위 실측 |
| 판정 | 명목 효력일 적용 | 명목 세션 n0 = 캘린더에서 effective_date 이상 첫 세션. dev = \|close_t / 직전 거래 종가 / price_factor − 1\|, tol = max(`price_match_tol_rel` × m, `price_match_tol_abs`), m = \|min(pf, 1/pf) − 1\|. (a) n0 의 dev ≤ tol → nominal · (b) 창 [n0 − lookback, n0 + window] 거래 세션 argmin dev ≤ tol → price_matched · (c) (a)(b) 실패 후보의 연결 성분(같은 티커, n0 거리 ≤ window, 재귀 CTE)의 계수 곱으로 성분 창에서 한 세션 → 전부 price_matched_combined · (d) 못 찾으면 `factor_ok=false`·`factor_source='no_price_match'`·계수 1·apply_basis unmatched(apply_date 는 명목 세션) | 오케스트레이터 확정 규칙 1 |
| 허용치 정의 | 원수익률 \|r − pf\| ≤ max(0.15·\|pf−1\|, 0.05) | **조정 후 잔여** \|r/pf − 1\| ≤ max(0.15·m, 0.05). 감자(pf > 1)에선 같은 식이고 분할(pf < 1)에선 더 엄격 — 원수익률 기준은 50:1 에 r ∈ [0, 0.167] 을 허용해 조정 후 +735% 를 apply_date 로 받아들인다(EG8-P02 가 잡을 구멍을 판정에서 막는다) | 절단본 3건 잔여 0.021·0.034·0.093 → 전부 nominal |
| 소액 이벤트 | 규칙 없음 | m ≤ tol_abs(2~5% 무상증자)는 창 탐색 없이 **nominal** — 가격으로 날짜를 못 가리고 창 탐색은 잡음 매칭. 건수 `n_nominal_small_expected`(기록형) | 서버 실측 (3) |
| 같은 날 개별 매칭 | — | 개별 매칭(nominal·price_matched) ok 2건이 같은 (ticker, apply_date) → 같은 사건을 두 원천·두 유형이 실은 것(감자 cr + KRX 액면병합) → 우선순위 낮은 쪽 `factor_source='same_day_suppressed'`(ok=false, 계수 1, apply_basis 는 매칭 결과 유지). 성분 매칭은 제외. EG3 `n_ok_same_apply_date_individual` = 0 폐기형 | 이중 계산 방지 |
| `factor_source` 어휘 | 4 | 6 = + `no_price_match` · `same_day_suppressed`. 우선순위 near_dup_suppressed > ratio_null > capred_paid > no_price_match > same_day_suppressed | `rules_s06.FACTOR_SOURCE_VOCAB` |
| 상수 | `near_dup_window_days` | + `adj_factor.price_match_tol_rel` 0.15 · `price_match_tol_abs` 0.05 · `price_match_window_sessions` 40 · `price_match_lookback_sessions` 5(창 [−5, +40] 을 두 상수로, 비대칭) — `_const` 로 산출식에. 값 근거는 seed `_measured[].note`(서버 1차 실측 오프셋 p10/p90), ★ 서버 재측정 | §1-12 등록 |
| available_date | min(announce, effective_date 다음 거래일) | **min(announce, apply_date 다음 세션)**. EG3_adj_factor 재계산 술어 갱신. 절단본: 토요일 기준일 11-28 → 명목 11-30 → 12-01 | 오케스트레이터 확정 규칙 2 |
| 뷰 `v_cum_adj` | `d < effective_date ≤ as_of` | **`d < apply_date ≤ as_of`**, (ticker, apply_date) 로 접어 누적(복합 성분 = 같은 날 곱). `v_adj_price`·`v_adj_volume` 은 그대로 이것을 부른다 | 규칙 2 |
| EG8-P02/P03 | effective_date(비거래일이면 뒤 첫 거래일) | **apply_date** 에서 측정. P03 은 거래량 중앙값 0 인 이벤트를 분모에서 빼고 `n_ok_median_zero`·`n_ok_volume_judged` 기록. seed 유지 0.30 · 10(근거: 매칭 잔여 ≤ 0.15 구조 상한 + 소액 nominal 은 제한폭 30% + 절단본 max 0.093·3.43), ★ 서버 재측정 | 규칙 3 |
| EG3_adj_factor 추가 | — | apply_basis 어휘 · apply_date NOT NULL·캘린더 세션 · `n_apply_outside_window`(개별: n0 − lookback ≤ n ≤ n0 + window · combined: 성분 창 min(n0) − lookback ≤ n ≤ max(n0) + window, 4차) · `n_nominal_apply_ne_nominal_session` · `n_combined_apply_inconsistent`(성분 쌍 apply_date 상이 0) · `n_ok_same_apply_date_individual` · `n_unmatched_source_mismatch`(no_price_match ⇔ unmatched) · `n_ok_apply_basis_bad`. 기록형: `n_by_apply_basis`·`n_ok_by_event_type_apply_basis`·`n_no_price_match_no_price_rows`(창 안 거래 행 0 = 가격 부재, 매칭 실패 아님)·`apply_offset_sessions_max`·`_median_nonzero`·`n_nominal_small_expected` | 규칙 4 |
| FX-2-001 | — | + apply_basis nominal · apply_date 2018-05-04(직전 거래 종가는 정지 전 04-27 2,650,000, reference 행은 건너뛴다) | 절단본 |
| FX-2-004 | (`101970`, 2018-10-12) 두 축 상이 | 절단본 101970 은 폐지 기간 사건이라 창에 거래 행 0 → **`no_price_match`·unmatched·계수 1** 로 고정(가격 부재 사건은 적용하지 않는다). 두 축 상이는 합성 가격 SQL 테스트(정지 뒤 재개일 +11 세션 ×9.8 → price_matched (10, 0.1)) | `test_equity_s06_adj.py::test_합성_*` |
| 부정 픽스처 | FX-N-003·not-ok·available | + apply_date=effective_date 변종 → `n_apply_off_calendar` 2(토요일 기준일)·`n_nominal_apply_ne_nominal_session` 1 | — |
| 절단본 분포 | ok 6 | ok 3(nominal) · no_price_match 3 · ratio_null 1 · near_dup 1 · apply_basis nominal 5 / unmatched 3. `_asof` 표본 행수 111,305 불변(계수만 바뀐다) | DESIGN §10 P23 |
**S07 커널 어댑터 v0 · EG-C ①②③④⑤⑩ 구현 정정 (2026-09-05, `backend/src/backtest_engine/adapters/equity_duckdb.py`·`src/equity/contract.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG-C 실행 주체·시점 | 7단계 전역, "pytest 케이스" | 2단계 직후(S07) `python -m equity --root … contract [--engine-src]` — `src/equity/contract.py` 가 어댑터를 불러 항별 술어를 돌리고 `_contract_meta.json`(snapshot_id·builds·engine_src·`gates[]`·status) 에 쓴다. FAIL 이면 `_failed/contract_<snapshot_id>.json` 도 쓴다(테이블·카탈로그는 건드리지 않는다). backend pytest 는 별도 축(아래 ①) | WORKFLOW §3-0 "S07 앞당김" |
| 결과 기록 형식 | `gates[]` 에 `EG-C` 1행 | **항별 `EGC-01`…`EGC-10` 행**(각 `metrics`). 1행으로 접으면 항별 metric·skip 사유가 사라진다 | `_contract_meta.json` |
| ① `EGC-01` | `BUILDERS` 등록 → 전 케이스 통과 | 두 축으로 나눈다: (a) `backend/tests/test_bar_source_contract.py::BUILDERS['equity_duckdb']`(테스트 레지스트리 — 런타임 어댑터 레지스트리는 없다) 전 케이스 + `test_adapters_equity.py` = backend CI. (b) `contract` 의 EGC-01 = 표본 티커(`trading_calendar.asof_sample_tickers`) 전 구간 Bar O/H/L/C/V 가 **`price_daily` 의 고정 입력 `_pinned/stg_price_daily ∪ stg_etf_price_daily`**(캘린더 안 날짜) 원주가와 전건 동일 ∧ `dropped_rows` = 거래량 0·NULL 행 + GAP-14 류(OHLC NULL·≤0) 행. 대조축을 `price_daily` 자체로 두면 산출을 변조한 사본이 양쪽에 같이 보여 게이트가 항진명제가 된다(부정 픽스처가 잡히지 않았다) → stage 축(EG20 과 같은 축). 정책 CLAMP(서버 GAP-14 127행을 거절하지 않고 센다; `repaired_rows` 기록, h/l 이 보정되면 mismatch 로 FAIL) | 부정 픽스처 close+100 → mismatch 1 |
| ② `EGC-02` | `v_universe(:d,'all')` 티커 집합 = `stg_listing_daily(d − lag)` | `v_universe` 뷰(T9)가 없어 **`UniverseSource.members(d)` 티커 집합** = `security_span` 의 고정 입력 `_pinned/stg_listing_daily(d) ∪ stg_etf_price_daily(d)`(ETF 는 listing 에 없다, P8). 랙 0(상장·가격류 `lag_known=true`). d ∈ `universe_daily.contract_probe_dates`(`baseline_seed_s07.json`, 7일 = `asof_sample_dates` 5 + 재상장 경계 2016-05-04·2024-03-12) | FIELD_MAP §1 |
| ③ `EGC-03` | 재상장 2종 → Membership 2구간, coverage_gap 유지, `end > backfill_end` 거절 | 술어 그대로 + 명시: 구간 ≥ 2 티커 수 = `security_span.respan_count` ∧ 티커별 Membership = 구간(first,last) 전건 ∧ 구간 사이 공백(a.last < b.first) ∧ `coverage_gap` 구간 last_session = backfill_end ∧ `UniverseQuery(end=backfill_end+1)` → NO_DATA(detail 에 `backfill_end=`) ∧ `end=backfill_end` OK. 거절 상태는 `LoadStatus` 어휘 안 **NO_DATA**(커버리지 밖 = 데이터 없음; FORMAT_ERROR 는 원천 형식 오류) | 부정 픽스처 구간 합침 → respan 1/2 FAIL(② 도 공백 probe 에서 FAIL — 항 간 중복 검출 의도) |
| ④ `EGC-04` | 분할 픽스처 run 의 누적수익률 = 조정가 손계산, `adj_factor.backtest_return_tol` | **S07 은 좁힌다**: `CorporateActionEvent` 집합 = `adj_factor` factor_ok 행 {(ticker, `apply_date`\|`effective_date`, share_factor, event_id)} — ratio(Decimal) = share_factor 전건, not-ok 행 미방출, 유형 매핑 건수 기록(`by_type`). 엔진 run 누적수익률 대조는 전략·run 이 필요해 S21/S22 MVP-B 몫이며 `backtest_return_tol` 은 그때 등재 | 초기 지시(brief) |
| ⑤ `EGC-05` | 거래정지 섞인 다종목 BarQuery → OK ∧ dropped > 0 | reference 행 최다 5티커 + reference 없는 1티커 → OK ∧ `dropped_rows` = Σ(거래량 0·NULL + GAP-14 류) > 0(stage 축 재계산) | 절단본 6종목 dropped 340 |
| ⑩ `EGC-10` | 폐지 909 중 무작위 20, `security.delist_sample_seed`·`delist_sample_n` | 후보 = `security_span.end_reason='delisted'` 구간(KRX 정본, `{ticker}:{span_seq}`), `random.Random(seed).sample(정렬 후보, min(n, 후보수))` → 전 구간 BarQuery(CLAMP) OK ∧ 반환 symbol 집합 = 요청. seed 20260905 · n 20(`baseline_seed_s07.json`); 절단본 후보 5 전건 | 재현성 |
| `UniverseSource` 종목 id | — | `Membership.instrument.symbol = '{ticker}:{span_seq}'`(재상장은 구간마다 다른 InstrumentId). `BarQuery`·`CorporateActionQuery` 는 `{ticker}:{span_seq}`(그 구간만) 와 `{ticker}`(전 구간, 계약 테스트 표기) 둘 다 받는다 | FIELD_MAP §1 `security_id` |
| `CorporateActionEvent.ts` | `effective_date` | `adj_factor.apply_date` 컬럼이 있으면 그 값, 없으면 `effective_date`(컬럼 존재로 분기; ok 행의 `apply_date` NULL 은 FORMAT_ERROR). 감자는 기준일이 아니라 거래재개일에 가격이 조정된다(S06 후속) | 오케스트레이터 지시 09-05 |
| `event_type` → enum | 미정 | `split`·`bonus`→SPLIT, `reverse_split`·`capred`→REVERSE_SPLIT(`EVENT_TYPE_MAP`). 어휘 밖·방향 불일치 FORMAT_ERROR, `SHARE_COUNT_CHANGE` 미사용 | DESIGN §7 매핑표 |
| 엔진 의존 | §8-5 미결 | 같은 모노레포 `backend/src` 를 `contract.load_adapter(engine_src)` 가 `sys.path` 에 얹는다(equity → backend 의 유일한 import 경계). 기본 `<repo>/backend/src` 또는 `$QL_ENGINE_SRC`, 서버는 `--engine-src`. 엔진은 **numpy·pyarrow** 를 요구한다(`backtest_engine.types.market` 이 numpy import) — equity 테스트 명령에 `--with numpy` 추가. 없으면 skip 이 아니라 FileNotFoundError(A13) | §9 A13 |
| 상수 미등재 | — | `asof_sample_tickers`·`contract_probe_dates`·`respan_count`·`delist_sample_*` 미등재는 해당 항 `skip(no_baseline)`, 테이블 미커밋은 `skip(not_built)`, `_pinned/` 입력 없음은 `skip(no_cross_source)`; skip 은 실패가 아니다 | §0-2 어휘 |

**S06 3차 정정 — 행 대 행 매칭 · EG8-P03 집합 통계 · P02 임계 (2026-09-05, 서버 2차 빌드 EG8 실패 → `sql/adj_factor.sql`·`rules_s06.py`)**

서버 2차(apply_date 판, 3,226행·ok 1,360 · nominal 2,129 · price_matched 157 · combined 2 · unmatched 938): EG8 `n_return_jump_over` 4(max 0.86) · `n_volume_jump_over` 71(max 1,724). 원인: (1) |조정수익률| > 0.30 6건이 전부 "직전 거래 종가" 로 재면 맞고 "직전 행 종가" 로 재면 튀는 패턴 — KRX 는 정지 중 **참고가(reference) 행의 close 에 새 기준가를 먼저 싣는다**(071970 capred 행 대 행 원수익률 0.102 → 조정 −0.86 · 044180 · 004200 · 123420 combined · 001360 split pf 1.0). (2) 얇은 종목의 재개일 거래 급증은 정상이라 하루 점프 건별 임계(10)는 71건을 잡지만 전부 정상.

| 항목 | 2차 | 정정 | 근거 |
|---|---|---|---|
| 매칭 분모 | 직전 **거래** 종가(`last_value(... trade) IGNORE NULLS`), 후보는 거래 행만 | 직전 **행** 종가(`lag(close)`, 참고가 행 포함), 후보는 창 안 모든 가격 행. 뷰가 조정하는 대상이 `price_daily` 행 시계열이므로 매칭·apply_date·EG8-P02 전부 행 대 행 | 서버 2차 6건 |
| `no_share_change` | ratio = 1 인 split 이 ok(계수 1) → EG8 이 그날 원수익률을 잼 | `factor_source='no_share_change'`·ok=false(계수 1, apply_basis nominal). 우선순위 capred_paid 다음. EG3 `n_ok_factor_one` = 0 폐기형 · `n_no_share_change` 기록 | 001360:split pf 1.0 raw 0.363 |
| EG8-P02 | |수정수익률| ≤ 0.30, 전일 = 직전 캘린더 세션 행 | 전일 = 그 티커의 **직전 가격 행**(`lag` over ticker). seed `adj_return_jump_max` **1.0** — 거래 재개 첫날은 가격제한폭이 없고 기준가의 50~200% 에서 체결되므로 소액 nominal 행의 |조정수익률| 물리 상한 +1.0(−0.5); price_matched·combined 는 판정 잔여 ≤ 0.15 로 구조 상한. 0.30 은 기록형 `n_ok_abs_adj_return_over_030`. ★ 서버 p100 은 오케스트레이터 확정 | 절단본 max 0.0929(변화 없음 — 005930 직전 행 = 05-03 참고가) |
| EG8-P03 | 건별 하루 점프 / 직전 20거래일 중앙값 ≤ `adj_volume_jump_max`(10) | **집합 통계**: 이벤트별 `median(adj_volume [apply, apply+19]) / median(adj_volume [apply−20, apply−1])` 의 ok 전체 **중앙값** ∈ [1/`adj_volume_ratio_band`, band](seed 3). 방향 오류(÷↔×)면 share_factor² 배(50:1 → 2,500)로 튄다. 기록형: 분포 p10/p90/p99·max·> 10 건수(`n_ok_volume_ratio_over_10`)·미정의 건수(분모 0·행 없음, `n_ok_volume_ratio_undefined`)·하루 점프 max. `adj_volume_jump_max` 폐기 | 서버 2차 분포 p10 0.28 · p50 0.81 · p90 2.3 · p99 17 · max 115 |
| §1 EG8 표·§1-12 | P03 = "같은 날 조정 거래량 점프 ≤ baseline", `adj_factor.adj_volume_jump_max` | P03 = 집합 중앙값 비 밴드, `adj_factor.adj_volume_ratio_band`. §1 술어 SQL(M03 단일일)은 3차 정의로 읽는다 | 위 |
| FX-N-006 | `n_volume_jump_over ≥ 2` | `n_volume_ratio_out_of_band = 1` ∧ 집합 중앙값 > 1,000(절단본 50:1 뿐이라 ≈ 2,500) ∧ `n_ok_volume_ratio_over_10` 3 → 여전히 EG8 만 FAIL | `test_FX_N_006_*` |
| 합성 테스트 | 5 | + 정지 중 참고가 행(세션 27)에 먼저 실린 기준가 ×9.9 → apply_date = 그 참고가 행(재개일 31 은 잔여 0 이라 후보 아님) · ratio 1 → no_share_change | `test_합성_참고가_행_*`·`test_합성_ratio_1_*` |
| 절단본 EG8(3차) | max_abs_adj_return 0.0929 · 하루 점프 max 3.43 | max_abs_adj_return **0.0929**(동일) · `n_ok_abs_adj_return_over_030` 0 · 집합 중앙값 비 **1.232**(005930 1.232 · 005935 1.345 · 247540 1.111, p10 1.135 · p90 1.322 · max 1.345) · > 10 건수 0 · 미정의 0 → pass | DESIGN §10 P23 |

**S06 4차 정정 — 복합 성분의 창 판정 (2026-09-05, 서버 3차 빌드 EG3_adj_factor FAIL `n_apply_outside_window` 3 → `rules_s06.py`)**

서버 3차(행 대 행, 3,226행·ok 1,390 · combined 10 · `apply_offset_sessions_max` 48 > 창 40): 다른 EG3 술어는 전부 0. 산출식 (c) 는 성분 창 [min(명목) − lookback, max(명목) + window] 에서만 탐색하므로 창 밖 3행은 combined 성분의 공통 apply_date 가 **멤버 자기 창** 밖인 경우다(개별 매칭은 SQL 이 자기 창 안으로 제한한다).

| 항목 | 3차 | 정정 | 근거 |
|---|---|---|---|
| 규칙 | (암묵) 성분 창 탐색 | **확정**: 성분의 공통 apply_date 는 성분 전체의 [min(명목) − lookback, max(명목) + window] 안이면 유효. 성분 창 밖은 탐색 대상이 아니므로 성분 전체 no_price_match(산출식 gwin 그대로) | 오케스트레이터 확정 |
| EG3 `n_apply_outside_window` | 전 행 자기 명목 세션 창 | 개별(nominal·price_matched·unmatched·not-ok)은 자기 창, **combined 는 성분 창** — 성분은 같은 티커·같은 apply_date 의 combined 행으로 게이트가 독립 복원(재귀 CTE 재실행 없음) | 항진명제 회피 |
| 기록형 | `apply_offset_sessions_max` | + `apply_offset_sessions_max_individual`(자기 창 기준, ≤ window 여야 한다) · `apply_offset_sessions_max_combined`(성분 창 기준). 기존 max 는 combined 포함 실측 그대로 | 지시 |
| 합성 테스트 | — | 명목 세션 20(감자)·50(액면병합), 세션 70 에 ×4 한 번 → 둘 다 combined @70(앞 멤버 오프셋 50), EG3 pass·`n_apply_outside_window` 0·max 50/개별 0/성분 50 · 점프 95(성분 창 [15, 90] 밖) → 성분 전체 no_price_match · 성분 행을 `price_matched` 로 위장 → EG3 FAIL(창 밖 1 + 같은 날 개별 1) | `test_합성_명목일이_떨어진_성분_*` |
| 절단본 | combined 0 | 변화 없음(ok 3 nominal · EG8 3차 수치 동일) | DESIGN §10 P23 |
**S19 `dataset_profile` 구현 정정 (2026-09-06, `rules_s19.py`·`sql/dataset_profile.sql`, 규칙 판본 e1.3.1 → e1.4.0)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG2-P04 랙 축 | `recommended_lag_days ≥ 1` | **`recommended_lag_sessions ≥ 1`** — 랙 정본은 세션이다(FIELD_MAP §1 「랙 단위」·DESIGN §4-7 본문도 세션으로 적었다). 일 축은 병기만 한다 | DESIGN §4-7 · `rules_s19.eg2_dataset_profile` |
| EG2-P04 모집단 출처 | `_stg_meta`(러너가 만드는 `read_json('${EQ}/_pinned/*/v=*/**/_meta.json')`) | equity 빌드 세션에는 그 뷰가 없다. 고정한 **equity 파티션의 `_meta.lag_known_inputs`**(build.py 가 입력마다 stage `lag_known` 을 복사해 둔 맵)를 합집합으로 읽는다 — 같은 사실, 실재하는 통로 | `build.py:partitions[].lag_known_inputs` |
| EG2-P06 컬럼명 | `basis = 'default'` | **`available_date_basis LIKE '%default%'`** — DESIGN §4-7 정본 컬럼명이 `available_date_basis` 이고, 뷰 필드는 두 basis 를 합쳐 싣는다(`price.adj_close` = `default\|derived`)라 동등비교가 성립하지 않는다 | DESIGN §4-7 · §5 |
| EG2 프레임 적용 | 매트릭스 27행 `EG2 ●(P04,P06,P07)` | 프레임 `gates.eg2_pit` 는 `available_date`·`available_basis` 컬럼을 요구하는데 프로파일에는 없다(카탈로그라 행 자체에 공개시점이 없다) → `available_rule=AVAILABLE_NONE` 으로 `skip(dimension_table)`, P04/P06/P07 은 테이블 특화 `EG2_dataset_profile` 이 판정 | `gates.eg2_pit` |
| `coverage_by_mktcap_quintile` 타입 | `DOUBLE[5]` | **`DOUBLE[]` + 게이트가 길이 5 검사** — 고정 크기 배열의 parquet 왕복을 이 프레임에서 검증한 적이 없어 리스트로 두고 폭을 술어로 지켰다 | `EG2_dataset_profile.n_quintile_wrong_width` |
| 커버율 분모 | (미정의) | **`coverage_basis` 4종**. 특히 컨센서스·의견은 `grid_security`(격자의 종목 수) — 월 1회 관측을 일별 셀로 나누면 구조적으로 낮게 나온다(절단본 forward_eps 가 2.3% vs 33.3%). FIELD_MAP §3 의 "커버 종목 804/810" 이 이 축이다 | DESIGN §4-7 · `rules_s19.coverage_rows` |
| 커버 0 인 필드 | (미정의) | **격리하지 않는다** — 창은 값 → 소유 테이블 → 캘린더 순으로 물러나고 `estimated_coverage_pct = 0` 으로 남는다. 격리하면 EG7 비율이 터지고(66행 중 1건 = 1.5% > 0.001) "필드가 없다" 와 "값이 없다" 가 구별되지 않는다 | `sql/dataset_profile.sql` |
| FX-6-001·002 키 | (`price_daily`, `ohlcv`) · (`flow_daily`, `kiwoom`) | grain 이 `field_id` 라 키를 필드로 바꾸고, `flow_daily` 는 아직 없는 테이블(S08)이라 실재 필드로 옮겼다 — §4 6단계 표 재정의(FX-6-001~008) | §4 |
| FX-6-001 기대 basis | `convention` | **`default`** — `price_daily.available_basis` 는 `default` 다(가격류는 공표 시각 미제공, DESIGN §4-2). `convention` 은 equity 가 신설한 어휘지만 이 테이블은 쓰지 않는다 | `rules_s04.PRICE_DAILY.available_basis` |
| 절단본 한계 | — | stage 절단본 트리에 `_meta.json` 이 없어 `lag_known` 이 전부 미상이고 **EG2-P04 모집단이 빈다**(항진). 기록형 `lag_known_unmeasured_inputs`(24 테이블)로 그 사실을 남기고, 술어는 손으로 만든 `_meta` 하네스 2건으로 검증한다(`stg_fin` 커버 → PASS · `stg_flow_daily_kiwoom` 미커버 → FAIL). **서버 실측에서 모집단이 채워지는지 반드시 확인** | `tests/test_equity_s19_profile.py` |

**S19 서버 1차 실패 2건 — 정정 (2026-09-06 2차, `rules_s19.py`·`sql/dataset_profile.sql`·픽스처)**

| 항목 | 1차 | 정정 | 근거 |
|---|---|---|---|
| EG4 픽스처 축 | 절단본 실측값을 그대로 등재(`financial.borrowings.coverage_from` = 2023-11-14 · `event.insider_net_buy.coverage_from` · `event.buyback_amount.estimated_coverage_pct` · `M01`·`V05`·`E05` 의 시작일·사유) | **모집단 비의존 불변식만 남긴다.** 서버(법인 3,478)는 같은 필드의 첫 관측일이 2016-03-30 이라 EG4 가 폐기했다 — 창·커버율·분위와 거기서 파생된 판정은 모집단의 함수다. 옮긴 자리는 **절단본 전용 pytest 손계산**이고, 픽스처는 선언(랙·PIT·basis·cell kinds·`source_stage_tables`·`column_scope`)과 선언 조인만으로 정해지는 판정(`field_unavailable`)만 담는다 | §4 6단계 표 · `tests/test_equity_s19_profile.py` |
| 분위 `ntile` 의 ORDER BY | `PARTITION BY date ORDER BY mktcap_krw` · `ORDER BY mktcap_krw` | **총순서**로 못박는다 — `… ORDER BY mktcap_krw, ticker`. 유일하지 않은 ORDER BY 는 동률 행의 분위를 SQL 이 정하지 않는다는 뜻이고, 답이 엔진의 행 순서(병렬 스캔·정렬·스필)에 맡겨진다. 서버 같은 입력 두 빌드가 `66:63db8b7d7a57d2b6` / `66:ede4412907a3b47f` 로 갈렸고(EG5a 는 EG4 실패로 `skip(upstream_failed)` 라 잡지 못했다), 최소 재현(동률 6/10 이 경계를 가로지르는 표를 행 순서만 바꿔 3회)에서 tie-break 없으면 커버 배열 2종·있으면 1종임을 확정했다 | `test_분위_경계의_동률은_총순서로만_결정된다` |
| 분위 계산 횟수 | 필드마다(격자 창 정렬 22회) | **빌드당 1회** — `_grid_quintile`·`_grid_security_quintile` TEMP TABLE 을 굽고 창으로 자른다. 세션 축 ntile 은 `PARTITION BY date` 라 날짜를 잘라도 각 날의 분위가 변하지 않으므로 결과가 같다. 격자 셀 수·종목 수 분모도 창별로 캐시한다(서버 1차 63초의 주범) | `rules_s19.install_grid_quintiles` |
| 커버율 표현 | `100.0 * count / count` 를 엔진이 계산한 DOUBLE | **정수 분자·분모(`n_observed`·`n_denominator`)를 산출에 싣고** 비율은 파이썬 나눗셈 + 6자리 반올림(`COVERAGE_PCT_DECIMALS`). 분위 배열도 정수 두 개를 받아 같은 함수로 만든다 — 엔진의 DECIMAL/DOUBLE 축약이 값 경로에서 사라지고, 소비자가 표만 보고 재계산할 수 있다. 항등은 `EG2_dataset_profile.n_pct_not_derivable` 이 SQL 로 다시 본다 | DESIGN §4-7 |
| 재현성 회귀 검사 | 재빌드 1회(EG5a) | **같은 입력 5회 연속 빌드(threads=3) 해시 동일** 을 두 테이블 모두에 추가. 1회 비교로는 순서 의존이 우연히 같은 답을 낼 수 있다 | `test_같은_입력으로_다섯_번_지어도_해시가_같다` |
| `factor_readiness` 결정성 | 미점검(입력 미커밋으로 서버 미실행) | 리스트 컬럼은 전부 `list_sort(list(DISTINCT …))`(6곳), `first_usable_date` 는 `max(coverage_from)` 라 집계 순서에 무관함을 테스트로 고정 | `test_리스트_컬럼은_정렬돼_있다` |

**S20 `factor_readiness` 구현 정정 (2026-09-06, `rules_s20.py`·`sql/factor_readiness.sql`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG10 판정 축 | 필드별 커버율 ≥ `<factor>.material_coverage_min`(`_reg_factor_material` 위) | **팩터 단위 판정** — 커버율은 `dataset_profile` 이 이미 재므로 EG10 은 그 0 이 `blocked(no_observations)` 로 옮겨졌는지를 본다. 필드별 임계를 따로 두면 같은 사실에 문턱이 둘 생긴다. baseline 키도 `factor_readiness.ready_min` 하나로 줄었다 | §6 EG10 · §1-12 |
| `ready_min` 초기값 | 36 (DESIGN §4-8) | **미등재** — 36 은 제안서 §1-8 이 S08~S10 이 지어진 상태를 전제로 센 값이고, 지금은 F01~F09 아홉이 `field_unavailable` 이라 실측이 31 이다(31 + 7 = 38 ≥ 36 으로 화해). 36 을 넣으면 첫 빌드가 폐기되고 31 을 넣으면 격자가 붙어도 하한이 느슨하다 → 3단계 뒤 사람이 등재 | `baseline_seed_s20.json` |
| `blocked_reason` 형식 | 토큰 | **`<토큰>: <걸린 field_id 들>`** — 표만 보고 원인을 짚을 수 있어야 인계 문서를 대신한다. 어휘 폐쇄는 `split_part(…, ':', 1)` 로 본다 | `sql/factor_readiness.sql` |
| `first_usable_date` | ready 만 NOT NULL | 요구 필드 `coverage_from` 의 최댓값이되 **재료가 없거나(field_unavailable) 관측이 0 이면(no_observations) NULL**. `partial_support` 로 막힌 행은 재료가 실재하므로 날짜를 남긴다(조건이 풀리면 그날부터 쓸 수 있다) | `tests/test_equity_s20_readiness.py` |
| 컬럼 | DESIGN §4-8 의 10 | **+ `label`** — 인계 문서의 "팩터 × 컬럼 × 시작일 표" 가 이 테이블인데 `V01` 만으로는 사람이 못 읽는다 | DESIGN §4-8 |
| `owner` 의 뜻 | (미정의) | **막힌 것을 푸는 책임자**. ready 행은 계산이 남았으므로 `factor_layer`. 절단본 분포 equity 16 · factor_layer 37 · unavailable 1 | `rules_s20.FactorSpec` |
| R04(시장 베타) | 제안서 §1-6 "즉시" | **blocked(field_unavailable) · owner `unavailable`** — `index_daily.close_idx` 는 실재하지만 `benchmark.close` 는 FIELD_MAP §2 가 미지원으로 판정한 축이다(GAP-09: 지수가 security 축이 아니다). 데이터가 아니라 계약이 없다 | FIELD_MAP §2 |
| G04(EPS 성장률) | 제안서 §1-3 "즉시" | **blocked(partial_support)** — `fin_std.eps_basic` 이 주식분할 미조정 원장 값이라(FIELD_MAP §3, S12 실측) 시계열 비율이 분할 구간에서 가짜 점프를 낸다. 제안서는 S12 실측 전 판정이었다 | FIELD_MAP §3 |
| E05·E06 | 제안서 §1-7 "즉시" | **blocked(no_observations)** — `corp_event` 가 MVP 4유형만 적재해 `treasury_buy`·`rights`·`cb_issue` 행이 0 이다(DESIGN §4-2). 원천(`stg_event_tsstk_aq` 1,951 등)은 실재하므로 S05 후속으로 풀린다. **EG10 이 잡으라고 만든 바로 그 경우** | DESIGN §10 P39 |
| V05·V07·Q07·Q08 | 제안서 §1-1·§1-2 "조건부(GAP-02)" | **ready** — GAP-02 는 "3계정이 실재하는가" 였고 S12·S19 실측이 **실재한다**로 답했다(`borrowings` 17.5% · `depreciation`·`interest_expense` 15.0%). 대신 커버 시작이 늦어 `first_usable_date` 가 2023-11-14 다 — 조건이 판정에서 **시작일**로 옮겨간 것이다 | DESIGN §10 P38 |

**S21 축소 정정 — 워크벤치 어댑터 · contract `ADAPTERS` · MVP-B 백테스트 (2026-09-05, 절단본 실측 → `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/`)**

| 항목 | 초안(WORKFLOW §3-5·§4 DoD) | 정정 | 근거 |
|---|---|---|---|
| S21 축소 범위 | `RawObservationPort` 만 | 5포트 전부(필드는 3) — `build_container`·`BacktestRunService` 가 `EquityDataPort`·`FactorMetadataPort`·`FactorObservationPort`·`BacktestDataPort` 를 함께 요구한다. stub 아님(같은 패널 코어) | DESIGN §7 표 |
| contract `ADAPTERS` | `[mock]` | `[mock, equity_duckdb]` — 어댑터 id 를 매개변수화하고 `adapter` fixture(indirect)가 세운다. 각 어댑터는 `FIELDS` 중 `list_fields()` 가 선언한 부분집합으로 답한다(equity 는 `price.close`·`price.market_cap`); `sector_id` 단언은 `classification.sector` 제공 여부에 따른다. 손 픽스처 `backend/tests/equity_fixture.build_workbench_root`(카탈로그 매크로는 픽스처 안 `CREATE MACRO`, `equity.views` 템플릿 사본) | `backend/tests/contract/test_raw_observation_port.py` 27 pass |
| DoD "미지원 필드 `unavailable` 명시" | `list_fields()` 에 `unavailable` 표시 | `DatasetFieldProfile` 에 상태 컬럼이 없어 목록 밖 + 질의 시 `INVALID_QUERY`(detail `unavailable field_id … supported=[…]`) 로 명시. mock 폴백 없음 | 도메인 무수정 원칙 |
| DoD "같은 셀에 같은 값·공개일" | — | `test_raw_port_and_research_panel_agree_cell_by_cell` 이 equity 어댑터에서도 pass | 위 |
| DoD "`build_container(equity_adapter="duckdb")` 부팅" | — | `equity_root` 인자 추가(없으면 ValueError, 미지 어댑터는 unsupported). `test_container_boots_with_the_duckdb_adapter` | `bootstrap/_container.py` |
| EG-C ⑥~⑨ | S21 | 미실행 — 필드 3 축소라 재무·컨센서스 항이 없다. 본판(S19·S20 뒤)에서 | — |
| MVP-B 재현(`tape_hash` 동일) | S22 | 같은 snapshot·spec 에서 `tape_hash` 결정적(P25: adj `75aa2447…`, raw `a8df8452…`). 재현 2회는 S22 | DESIGN §10 P25 |
| 커널 정지 세션 | — | `TargetTapeStrategy.on_event` 가 bar 없는 목표를 보유 유지/건너뛰기(`no_bar=[…]` reason). 커널 정책이 생기면 되돌린다 | `backend/tests/test_target_tape_strategy.py` |
| 워밍업 부족 | — | start 앞 캘린더가 요청 워밍업보다 짧으면 잘라내고 `warnings` 로 알린다(NO_DATA 아님) — 첫 유효 스코어가 늦어질 뿐 값을 합성하지 않는다 | 어댑터 `_window` |

**S21 후속 — `price.adj_close` 전방 조정 (2026-09-05, 사용자 결정 → `views.py`·`catalog.py`·`equity_duckdb/_adapter.py`·`tests/equity_fixture.py`)**

| 항목 | 이전 | 정정 | 근거 |
|---|---|---|---|
| `price.adj_close` 축 | `v_adj_price(as_of := 창 end)` — base = end, 수준값 비 PIT·창마다 다름(DESIGN §11 ①) | **`v_adj_price_fwd(as_of)`** — 각 행 d 에 `apply_date ≤ d ∧ available_date ≤ d` 인 factor_ok 계수의 share_factor 누적곱(첫 관측 수준 고정). 005930 05-03 2,650,000 · 05-04 51,900 × 50 = 2,595,000. 접는 세션 `fold_date = greatest(apply_date, available_date)` 라 (ticker, date) 값이 as_of·창에 무관 | DESIGN §5·§11 ① |
| `available_date`(어댑터) | 원주가 공개일(date) | 뷰 출력 `greatest(date, 접힌 계수의 available_date)` — fold 규칙상 항상 date. available = apply 다음 세션인 계수는 apply 세션엔 접히지 않고(원주가 점프 노출) 다음 세션부터 접힌다 — 포트 계약 `available_date ≤ as_of` 가 어떤 랙에서도 성립. `DatasetFieldProfile.point_in_time=True` | contract `test_no_field_value_is_visible_before_its_available_date` |
| EG11·EG5c·`_asof/` 대상 뷰 | `v_cum_adj`·`v_adj_price` | + `v_adj_price_fwd`·`v_adj_volume_fwd`(`catalog.ASOF_VIEWS` 4, 매크로 6). 절단본 EG11 pass 4뷰 × 111,305행, snapshot `90115cc068146382` 불변 | `catalog.publish` |
| 부정 픽스처 | FX-N-006(거래량 나눗셈 → EG8) | + 전방 축 3건(`test_equity_s06_views.py`): (a) as_of 를 2018-05-03·05-04·2026-08-20 으로 바꿔도 공개된 사건 전후 셀(05-03 2,650,000 · 05-04 2,595,000) 불변 (b) `v_adj_price_fwd` 를 나눗셈으로 뒤집으면 분할일 \|조정수익률\| 0.9996(EG8-P02 상한 1.0 밖; 곱셈은 −2.08%) (c) 창 [start,end] 을 바꿔도 같은 셀 동일 — equity `test_adj_close_는_창을_바꿔도_같은_셀이_같다`(창 [04-02, 05-03] vs [04-02, 05-31])·backend `test_adj_close_is_raw_close_scaled_by_factors_applied_on_or_before_the_row`·contract `FIELDS` 에 `price.adj_close` 추가(`test_facts_do_not_depend_on_the_query_window` 가 equity 어댑터에서도 adj_close 를 본다) | S21 에이전트 지적 모순의 회귀 |
| EG8 | `v_adj_price` | 변경 없음 — base = as_of 축 위에서 그대로 잰다. 같은 as_of 에서 `adj_fwd / adj_bwd = Π(전 계수)` 가 종목별 상수(005930·005935 50, 247540 4)라 점프 판정 동일 | `test_전방조정과_as_of_조정은_종목별_상수배다` |
| MVP-B 재실행 | P25 | 4변형(top 20/3 × adj/raw) 수익률·`tape_hash`·`run_fingerprint` 전부 이전과 동일 — 순위·선택 불변이 채택 검증 기준 | DESIGN §10 P25 |

**S06-2 — `price_daily` 기준가 컬럼 · `adj_factor` v3 KRX 기준가 원천 `krx_base_price` (2026-09-05, DESIGN §4-2 v3 절 구현 → `rules_s04.py`·`sql/price_daily.sql`·`rules_s06.py`·`sql/adj_factor.sql`·`backend/…/equity_duckdb.py`, 규칙 판본 e1.3.0)**

절단본 실측(DESIGN §10 P27): `price_daily` 41,066행 그대로(+2컬럼), 기준가 = 직전 행 close 비율 0.99855(불일치 59 = ETF 069500 분배락 50 + 주식 9). `adj_factor` **10행** = corp_event MVP 8 + 기준가 신규 2 — (a) 교체 3(005930·005935 split · 247540 bonus, 전부 ok) · (b) unknown_krx 0 · (c) 재발견 2(101970 2014-08-21 · 900050 2016-07-29) · (d) unknown_price_only 2(247540 2022-05-09 · 900050 2011-02-16, ok=false) · 후보 밖 ETF 36 · 구간 첫날 2. EG8 max |조정수익률| 0.0898(247540: 135,900/124,700 − 1; 이전 0.0929 는 1/4 기준).

| 항목 | 설계(DESIGN v3 절) | 구현 | 근거 |
|---|---|---|---|
| `price_daily` 컬럼 | `change_krw`·`base_price_krw`(`shares_out` 다음) | 그대로. `base_price_krw` 는 DECIMAL 차(절단본 (10,0)), EG0-P04 는 컬럼명·순서만 대조. `input_columns` 에 두 stage 의 `change_krw` 추가. 기록형 `n_base_price_null`·`n_change_null`·`n_base_price_ne_close_minus_change`(항등 0)·`n_trade_rows_with_prev_row`·`n_base_price_eq/ne_prev_close_trade`·`base_price_match_rate_trade`·`n_base_price_ne_prev_close_reference` | `rules_s04.eg3_price_daily` |
| 픽스처 `price_daily.json` | 005930 05-04 기준가 53,000 · 247540 06-27 124,350 | 005930 05-04 `change_krw` −1,100·`base_price_krw` 53,000 · 05-03 참고가 행 base = close 2,650,000 · **247540 06-27 base = 124,700**(원자료: close 135,900 − change 11,200; 497,400/4 = 124,350 은 자기주식 신주 미배정 등 KRX 산식을 무시한 산술값 — 기준가가 정본) · 000030 무거래일 base = close · 069500 첫 거래일 base 22,575 | 절단본 원자료 |
| 기준가 후보 | 같은 티커 직전 행 close 대비 \|r − 1\| > `base_price_tol_rel` | + **ETF 제외**(`security.sec_type='etf'`) · **구간 첫날 제외**(`security_span.first_date`) — 두 입력 추가. ETF: 분배락이 기준가를 바꾸고 상장좌수가 설정·환매로 거의 매일 변해 (b) 의 주식수 변화가 증거가 아니다(절단본 069500 후보 36 중 14 가 곱 검사를 우연히 통과 → 설계대로면 가짜 ok `unknown_krx`, 나머지는 분배락마다 (d) 행). 구간 첫날: 재상장 첫 행의 직전 행은 옛 구간 종가(036220 2024-03-13 r 5.7 · 101970 2025-03-28 r 22.5) | DESIGN §4-2 v3 구현 결과 ①② |
| (a) 사건 교체 | apply/명목 ± `base_match_window_sessions`, 비율 `price_match_tol` 안, 성분은 곱 | 단위 = 개별 event_id 또는 combined (ticker, apply_date); 단위 ↔ 후보 1:1(dev·거리 최소, 양쪽 rank 1). 성분은 멤버 전부 같은 날 · 루트(min event_id) 잔여 r/Π(다른 pf) · share 는 S/Π(다른 ratio). + **반증 규칙**: ok 사건의 apply_date 에 후보가 있는데 비율이 안 맞으면 사건 `krx_base_inconsistent`(basis 유지), 후보는 (b)/(d) — 같은 날 두 원천이 다른 값을 낸 채 둘 다 ok 금지(이중 적용) | `sql/adj_factor.sql` pair·matched·replaced·conflict |
| share_factor 교체 | 같은 날 shares_out 비 S(\|S−1\| > `krx_share_change_tol`) 아니면 1/pf | 그대로. 결과: 247540 share_factor **3.9888**(1/0.2507) ≠ ratio 4.0 — EG3 `n_ok_share_factor_ne_ratio` 는 기준가 행 제외, `krx_share_factor_ratio_dev_max`(0.0028) 기록. 엔진 SPLIT ratio 도 3.9888(정수 아님) — 시총 불변 우선의 설계 귀결, ★ 서버 실측 뒤 ratio 정수 유지 옵션 판단 | `fixtures/adj_factor.json` bonus-247540-* |
| (b) unknown_krx | 같은 날 shares_out 변화 → 신규 행, ok = 곱 검사 | 그대로 + `corp_code = security.corp_code`. **무상증자는 여기 안 온다** — 권리락일과 신주 상장일이 2~3주 떨어져(247540 06-27 vs 07-15) 주식수 변화가 같은 날이 아니다 → DART 공백기 무상증자 권리락은 (d) ok=false. (b) 는 액면분할·감자(변경상장일 = 기준가 변경일) | 절단본 listing 원장 |
| (c)(d) | 재발견 행 없음 · `unknown_price_only` ok=false | 그대로. (d) 의 `factor_source='unknown_price_only'`(어휘 추가) · available = 다음 세션 · 월별 건수 `n_base_price_only_by_month` | `sql/adj_factor.sql` bp_new·out_new |
| EG1 | MVP + (b)(d) 독립 재계산 | 우변 = MVP + [후보(`_BASE_PRICE_CANDIDATES_CTE`, 입력만) − (c) − (a) 소비 날짜(산출 (ticker, apply_date) 를 읽는 유일한 항)]. §3 ⑩ 갱신 | `rules_s06.ADJ_FACTOR.eg1_rhs_sql` |
| EG3_adj_factor 추가 | apply_basis 어휘 + 곱 검사(base 원천 `factor_product_tol_base`) + (a)~(d) 건수 | 폐기형 6: `n_krx_new_row_invariant_bad`(event_id 패턴·effective = announce = apply·corp_event 없음) · `n_unknown_price_only_ok` · `n_krx_price_factor_mismatch`(ok 기준가 행의 (ticker, apply_date) 곱 = price_daily 기준가/직전 행 close 독립 재계산, 1e-12) · `n_unknown_krx_ok_share_factor_bad`(= 같은 날 주식수 비) · `n_krx_row_out_of_scope`(ETF·구간 첫날·후보 아님) · `n_unknown_krx_shared_apply_date`(ok unknown_krx 와 같은 날 다른 ok 행 0). 창 검사는 기준가 행에 ± base_win 여유(단위 = 같은 (ticker, apply_date, basis)). 기록형: 후보·ETF 제외·구간 첫날 제외·(a) 단위/행·(b) ok·(c)·(d)·월별·`n_ok_without_base_price_event`(폴백만으로 선 ok 행)·`krx_share_factor_ratio_dev_max` | `rules_s06.eg3_adj_factor` |
| 부정 픽스처 | (i) 비율 어긋남 (ii) 기준가만 (iii) 전일 무거래 (iv) no_price_match 회생 | `test_equity_s06_adj.py` 합성 5건 + 절단본 1건: (i) 분할 pf 0.5 + 같은 날 주식수 ×3 → `krx_base_inconsistent`; r 0.8 반증 → 사건 inconsistent + (d) 신규; 기준가 없으면 폴백 nominal 회귀 (ii) 기준가 ×0.9 → (d) 행·available 다음 세션·ETF 는 행 없음 (iii) 정지 뒤 ×0.7 → 행 없음·`n_base_price_rediscovery` 1·구간 첫날(×5 + 주식수 ×0.2)은 spans 축이 가른다 (iv) 감자 재개일 원수익률 ×12.5 → 2차 unmatched, 기준가 ×10 → (a) 회생(10, 0.1); 창 밖(+7)이면 (b) unknown_krx 가 대신 선다 · 성분(감자+병합 ×4) 기준가 곱 교체 · 산출 기준가 행 계수 ×2 변조 → `n_krx_price_factor_mismatch`·`n_unknown_krx_ok_share_factor_bad` | 절단본 분류 (a)3·(b)0·(c)2·(d)2 |
| 어댑터 | 변경 없음 | `EVENT_TYPE_MAP` 은 그대로 + `RATIO_DIRECTED_EVENT_TYPES = {unknown_krx}`: share_factor > 1 → SPLIT, < 1 → REVERSE_SPLIT, = 1 은 FORMAT_ERROR. `unknown_price_only` 는 ok=false 라 방출되지 않고 ok 로 오면 어휘 밖 FORMAT_ERROR(그대로) — EGC-04 가 `unknown_krx` ok 행을 비교 모집단에 넣으려면 매핑이 있어야 했다 | backend `test_unknown_krx_*` 3건 |
| 규칙 판본 | e1.3.0 | `model.RULES_VERSION` e1.3.0 — 첫 서버 빌드는 EG5a `skip(rules_changed)`, 재빌드 해시 동일로 확인 | DESIGN §2 |

---

**S11 `disclosure_version` 구현 정정 (2026-09-06, `rules_s11.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG7-P05 `no_label` 격리 | 기간 라벨 `(YYYY.MM)` 없는 행(서버 586)을 `_reject/no_label/` 로 | **폐기** — 행을 유지하고 `group_key` NULL + `group_key_basis='no_label'` 로 표시한다. 격리하면 그 접수가 모집단에서 사라져 뷰가 "정정 없음"으로 읽는다(§5-C6 의 `sec_type='other'` 와 똑같은, 게이트가 만드는 생존편향). 남은 격리 사유는 `rcept_dt_missing` 하나 | DESIGN v1.2 §4-4 "행 유지" · 부정 픽스처 `test_부정_기간_라벨이_없어도_행은_남는다` |
| §3-⑬ EG1 우변 | `_reg_vocab`(domain `periodic_report`·`excluded_report`) 테이블 | 레지스트리 테이블이 실재하지 않는다 → `.sql` 의 모집단 CTE(`periodic`)를 마커(`-- ==== eg1:`)까지 잘라 그대로 재사용한다(S05 `pool_sql` 규약). 어휘는 `rules_s11.PERIODIC_PREFIXES`(3) · `EXCLUDED_TOKENS`(4) 튜플이 정본이고 tests 가 `.sql` 리터럴과 대조 | `rules_s11.population_sql` · `test_모집단_어휘가_sql_리터럴과_같다` |
| §3-⑭ `correction_link` EG1 | 별도 테이블의 등식 | 16행에 흡수됐으므로 등식도 없다. 링크 성립 여부는 EG6-P05, 날짜 축은 EG6-P06, ZIP 부재는 `date_check='no_zip'` 건수(기록형)가 대신한다 | §9 C15 |
| EG6-P05·P06·EG8-P09 baseline 키 | `correction_link.*` | `disclosure_version.link_rate_min`·`date_exact_rate`·`reach_rate_min` (§1-12 갱신) | 흡수 |
| EG8-P09(E-G7) 분모 | `rm` 정정 플래그가 붙은 접수 전건 | **원본만**(`NOT is_correction`). 정정이 또 정정되면 DART 가 정정본에도 `rm` 을 붙이는데, 후보 술어가 정정본을 원본 자격에서 빼므로(DESIGN §4-4) 구조적으로 도달 대상이 아니다 — 정정 체인은 평평하게 접혀 그룹의 모든 정정이 같은 원본을 가리킨다. 절단본에서 분모에 넣으면 82/97 = 0.845, 빼면 82/82 = 1.0 이고 그 15건은 규칙이 의도한 결과다. 건수는 `n_rm_on_correction` 으로 기록 | `test_E_G7_도달률은_정정본을_분모에서_뺀다` |
| EG6-P07 그룹 오판율 | `misjudge_rate_max` 로 판정 | **코드가 판정하지 않는다** — 사람이 라벨한 정정 그룹 표본이 있어야 재는 값이라 SQL 로 못 쓴다(§5-A 유형). baseline 미등재로 두고 EG6 metrics 에 참조값만 싣는다 | §5-A |
| 모집단 사다리 5단 | baseline 등재 뒤 임계 판정 | 절대 건수는 **임계로 쓰지 않는다**(접수는 매일 늘어난다). `EG3_disclosure_version` 이 stage 뷰만으로 센 다섯 단과 산출에서 센 다섯 단의 **일치**를 폐기형으로 판정하고, 서버 참조값(181,106 / 20,579 / 24,285 / 17,600 / 15,225)은 `baseline_seed_s11.json` 의 `_measured._ladder_reference` 에 남긴다. 임계형은 E-G6a·E-G7 둘뿐이고 미등재면 `skip(no_baseline)` + 기록형 | WORKFLOW §3-4 |
| 사다리 L2 값 | DESIGN·WORKFLOW 20,579 | `STAGE_HANDOFF` §5 는 같은 축을 **20,759** 로 적었다(정기보고서 그룹 181,106 중 정정 있음 20,759). 180 건 어긋난다 — 첫 서버 빌드의 `ladder_stage.n_rm_corrected_later` 로 확정한다 | 두 문서 대조 |
| `date_check` 의 링크 실패 행 | 어휘 8종 그대로 | `candidate_status ∈ {none, multi_unresolved}` 인 정정은 대조할 원본 접수일이 없어 `mismatch` 로 떨어진다(어휘를 늘리지 않는다). 링크 성립률은 E-G6a 가 따로 재므로 두 축이 섞이지 않는다 | `.sql` date_check CASE |
| `corr_has_fin_item` 술어 | "items 에 재무 항목, 2,238" | 항목 목록이 명시되지 않은 인용이라 재현할 수 없다 → `rules_s11.FIN_ITEM_KEYWORDS` 6종(재무제표·재무상태표·손익계산서·현금흐름표·자본변동표·요약재무)을 선언하고 기록형으로 둔다. 절단본 39/84 · ★ 서버 재측정 | DESIGN §4-4 |
| 모집단 dedup (서버 1차 빌드 FAIL, 09-06) | 모집단 = 정기보고서 접수 **행** 전건 | **접수번호당 1행**으로 접는다. `stg_disclosure` 는 `write_mode='append_only'` · `key_unique=False`(rules_dart.py — 재수집 판본이 G6 축) 라 같은 `rcept_no` 가 여러 행으로 쌓인다: 서버 실측 203건(정기보고서 안 26건, 중복 쌍은 투영 컬럼이 전부 같다). grain 이 `rcept_no` 인데 안 접으면 산출이 같은 행을 두 번 내고 EG1 이 그만큼 어긋난다(**서버 1차 빌드 198,189 vs count(DISTINCT rcept_no) 198,163, delta −26** → 폐기). 모집단 CTE 에 `QUALIFY row_number() OVER (PARTITION BY rcept_no ORDER BY observed_date …) = 1`(first_write_wins, EG6-P04 규약; 동률은 투영 컬럼 전체로 깨 EG5a 를 지킨다). EG1 우변도 `count(DISTINCT rcept_no)` 로 축을 맞추고, 접힌 수는 `n_population_dup_rcept`(기록형)로 남긴다. `stg_doc_index` 도 같은 규약이라 조인 전에 접는다(`idx` CTE); `stg_doc_correction` 은 `key_unique=True` 라 접지 않는다. 사다리·E-G7 의 stage 축도 `count(DISTINCT rcept_no)`·`EXISTS` 로 바꿔 팬아웃을 막았다 | 서버 빌드 로그 · `test_부정_재수집_판본이_모집단에_둘이면_한_행만_낸다` |
| 상수 | 없음 | `deadline_days_annual` 90 · `deadline_days_interim` 45(법정) · `date_check_near_days` 7(어휘 경계). 뒤 하나는 SQL 숫자 리터럴 금지 규약(`test_sql파일에_상수_하드코딩_없음`)의 통로다 | `baseline_seed_s11.json` |

**S12 `fin_std` 구현 정정 (2026-09-06, `rules_s12.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| 접수일 원천 `stg_rcept_dt_map` | DESIGN §4-4 입력 | **stage 에 실재하지 않는다**(`data/stage` 절단본·STAGE_HANDOFF 표 어디에도 없다) → `stg_disclosure.rcept_dt` 로 대체. 절단본 `stg_fin` 의 226 접수번호 전건이 `stg_disclosure` 에 있다 | 절단본 DESCRIBE · `SELECT count(DISTINCT rcept_no) FROM stg_fin WHERE NOT EXISTS (…stg_disclosure…)` = 0 |
| EG6-P08 조인축 | `disclosure_version.bsns_year = f.bsns_year AND .reprt_code = f.report_code` | v1.2 `disclosure_version` 은 grain 이 `rcept_no` 라 그 두 컬럼이 없다 → **접수번호 조인**(`d.rcept_no = o.rcept_no`). baseline 키도 `fin_std.nonmatch_rate_gap_max` 로 옮겼다(측정이 `fin_std` 빌드에서 일어난다) | §1-12 갱신 · `eg6_fin_std` |
| EG7-P04 `rcept_lag_p99_days` | "상한은 baseline p99" | **값을 문자 그대로 p99 로 잡으면 안 된다** — 그러면 정의상 1% 가 매번 격리된다(§5-B 유형). 이 상한이 잡아야 하는 것은 `period_end` 오판(1년 어긋나면 lag 이 365 이상 튄다)과 음수 lag(결산 전 접수 = 불가능)이고, 늦은 정정 접수는 통과시켜야 한다. 이름은 등록부대로 두고 seed 는 1826(5년) — 절단본 p99 216 · max 445 | `baseline_seed_s12.json` |
| 격리 어휘 | EG7-P04 `rcept_lag_out_of_range` 만 | 4종: `non_krw`(`is_krw` 아님 — 원 단위 축 밖) · `period_unresolved`(문서도 없고 후보가 0 또는 2) · `rcept_lag_out_of_range` · `duplicate_vintage`(서로 다른 (bsns_year, reprt_code) 가 같은 grain 으로 접힘 — 어느 쪽이 옳은지 규칙이 못 고르므로 **둘 다** 격리한다) | 부정 픽스처 4건 |
| FX-4-008 `derived_n_rows` | 이름 하나 | 파생 블록이 둘(q4·CF 분기)이라 접두를 붙였다 — `q4_derived_n_rows`·`q4_derived_available_date` · `cf_q_n_rows`·`cf_q_available_date`. 계정별 `<col>_available_date` 는 두지 않는다: 한 블록의 구성 보고서가 같아 값이 전부 같다 | DESIGN §3 파생 규약 |
| 계정 수 | fin_map 21 + 3 + `gross_profit` | **24**. `gross_profit` 은 fin_map 21 에 이미 있으므로 새 계정이 아니라 FIELD_MAP §3 의 판정(미지원 → 지원)만 바뀐다. 추가 3 의 `account_id` 는 절단본에서 실재 확인: `DepreciationAndAmortisationExpense`(CIS 9) · `Borrowings`(BS 9) · `InterestExpense`(CIS 12). **합산하지 않는다** — 단기·장기·유동성장기 차입금은 겹쳐서 이중계상되고 `FinanceCosts`(137행)는 이자비용보다 넓다. 커버율이 낮은 것은 사실이고 `coverage_by_account` 가 기록한다 | `rules_s12.EXTRA_ACCOUNTS` |
| 계정 대응표 위치 | 미결 | `src/fin_map.py` 가 정본이고 `rules_s12.acct_rows()` 가 import 해서 유도한다(베끼지 않는다). `.sql` 의 `_acct` VALUES 블록은 `acct_values_sql()` 문자열을 그대로 담고 tests 가 `in` 으로 대조 | `test_sql_의_acct_블록은_생성기_문자열과_같다` |
| `.sql` 숫자 리터럴 | — | `test_sql파일에_상수_하드코딩_없음`(허용 {0,1,2,-1})에 걸려 넷을 바꿨다: ① tier 를 정렬 가능한 라벨(`a_concept`…`e_banking_gross`)로 — `min(tier)` 이 우선순위를 고른다 ② 우선순위를 `tier*1000+sj_rank` 대신 tier → sj_rank 두 단계로 ③ 보고서 코드 목록을 `_qcode`·`_rcode` CTE 로 빼서 "분기 셋" 을 `count(*)` 로 센다 ④ 개월 수 3·6·9 를 `_const` 로. `DECIMAL(38,4)` 캐스팅은 제거하고 원천 타입을 그대로 나른다(절단본 해시 불변) | `sql/fin_std.sql` |
| FX-4-002(3월 결산)·FX-4-006(은행) | 절단본 픽스처 | **절단본에서 낼 수 없다** — `stg_fin` 이 있는 법인 9개가 전부 12월 결산이고 은행·보험 재무가 없다(3월 결산 00694003 은 재무 행 0). 후보 규칙(`inferred`)·금융업 매출 대체는 손 트리 부정 픽스처로 덮고 실사례는 서버 빌드 뒤 `fixtures/fin_std.json` 에 추가한다. EG6-P08 도 같은 이유로 `skip(no_coverage)` | `test_equity_s12_fin.py` |
| FX-4-003(`v_fin_latest` asof) | S12 산출 | 뷰 `v_fin_latest` 는 이 슬라이스 범위 밖이다(EG5c·EG11·EG-C ⑥⑦ 과 함께 S12 후속). 4A 는 테이블 둘까지 | WORKFLOW §3-1 S12 행 |
| 재수집 판본 dedup (09-06) | — | S11 과 같은 함정이 둘 있다. ① `stg_fin` 도 append_only·key_unique=False 라 자연키 8열(corp_code·bsns_year·reprt_code·fs_div·sj_div·account_id·account_detail·ord)이 중복되면 `sum` 집계(lease_liab · 금융업 매출 대체)가 **이중계상**된다 → `fin` CTE 에서 QUALIFY 로 접는다. ② `stg_disclosure` 를 접수일 조회에 그대로 조인하면 grp 행이 판본 수만큼 늘어 grain 이 깨진다 → `dt` CTE 로 접수번호당 1행. `grp` 는 GROUP BY 라 안전하고 `stg_doc_meta` 는 `doc` 이 이미 접는다. 접힌 수는 `n_stg_fin_dup_natural_key`·`n_disclosure_dup_rcept`(기록형) | `test_부정_재수집_판본이_있어도_grain_과_합계가_흔들리지_않는다` |
| `corp.fiscal_month` 검산 | `period_end` 후보 규칙의 근거이자 검산 | `EG3_fin_std.n_fiscal_month_mismatch` 는 **기록형**이다(폐기형 아님) — `corp.fiscal_month` 는 현재값 스냅샷이라 결산월을 바꾼 법인의 과거 사업보고서는 정상적으로 어긋난다(DESIGN §11 "결산월 변경은 문서 `period_to` 로 해소"). 폐기형으로 두면 그런 법인 하나가 서버 빌드를 죽인다. 절단본 0 | DESIGN §11 |
| 기간 판정 재계산 축 | — | `.sql` 의 tie-break(rcept_no 당 main 이 여럿일 때 `ORDER BY period_to, period_from, doc_acode` 첫 행)를 게이트가 베끼면 항진명제가 된다 → **존재 명제**로 본다: "그 접수의 main 문서 중 어느 한 행이 이 `period_end`·`period_start` 를 주고 그 행의 개월 수가 이 `report_code` 를 준다". tie-break 가 실제로 작동했는지는 `n_doc_meta_multi_main`(절단본 0)이 기록한다 | §5-C 항진명제 규약 |
| 규칙 판본 | — | `model.RULES_VERSION` 은 **올리지 않았다**(4A 는 새 테이블 둘이라 기존 산출을 바꾸지 않는다). 병합 시 오케스트레이터가 판단 | DESIGN §2 |


**S15 지분·감사 3테이블 구현 정정 (2026-09-06, `rules_s15.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §2 매트릭스 19행 · §3-⑯ `ownership_snapshot` grain | (corp, bsns_year, reprt_code, `nm`) 비집계 | **`stock_knd` 를 grain 에 추가**. 초안 grain 은 유일하지 않다 — 한 주주가 보통주·우선주를 나눠 신고하면 같은 이름으로 2행이 온다(대신증권 00110893 2015 양홍석 보통주 6.92%·3,512,510주 / 우선주 0.00%·130주). 절단본 비집계 826행이 이름 축으로는 **733개**(충돌 93행)이고 `stock_knd` 를 넣으면 826 = 826. EG1 우변도 `SELECT DISTINCT corp_code, bsns_year, reprt_code, nm, stock_knd … WHERE row_kind IS DISTINCT FROM 'aggregate'` 로 바꿨다(`row_kind` NULL 안전을 위해 `<>` 대신 `IS DISTINCT FROM`). 접으면 종류주 지분율이 조용히 사라진다 | `stg_hyslr` DESCRIBE·`natural_key`(rules_dart.py) · `test_부정_ownership_집계행은_모집단_밖이고_종류주는_남는다` |
| §3-⑰ `audit_opinion` EG1 우변 | `count(*) FROM stg_audit`(= 93,037, 1:1) | **grain distinct** `SELECT count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, bsns_year_label FROM stg_audit)`. stage 행수 1:1 은 성립하지 않는다 — 절단본 291행이 grain 226개로 접힌다(충돌 52그룹 65행). 원인 둘: ① 응답이 같은 행을 그대로 두 번 준다(31그룹 75행이 투영 컬럼까지 동일) ② **연결/별도 감사보고서 2행**인데 그 구분이 구조 컬럼이 아니라 자유 텍스트(`emphs_matter`·`core_adt_matter` 안의 '(연결재무제표)'/'(별도재무제표)')에만 있다(21그룹). 키로 쓸 컬럼이 없으므로 grain 을 늘리지 않고 first_write_wins → `row_hash` 순으로 접는다. ★ 서버 재측정 — 93,037 대신 첫 서버 빌드의 EG1 rhs 로 확정 | 절단본 실측 · `baseline_seed_s15.json._measured._eg1_reference` |
| §3-⑮ `holder_daily` EG1 우변 | `count(*) elestock + count(*) majorstock` | **두 원천의 자연키 distinct 합**. `stg_holder_*` 둘 다 `key_unique=False`("한 접수에 보고자 행이 하나뿐인지 미실측", rules_dart.py) · `write_mode='append_only'` 라 재수집 판본이 쌓이면 행수 우변이 grain 축 좌변보다 커진다 — S11 서버 1차 빌드가 같은 함정으로 delta −26 을 냈다. 절단본에서는 (rcept_no, repror) 가 두 원천 모두 유일해 4,197 + 132 = 4,329 로 값이 같다 | `test_부정_holder_재수집_판본이_둘이면_한_행만_낸다` |
| 접힘의 가시성 | — | 세 테이블 모두 **`n_source_rows`**(그 grain 으로 접힌 stage 행 수, ≥1) 컬럼을 싣는다. 접힘을 행에 남기지 않으면 소비 측이 "1행 = 1신고" 로 읽는다. 게이트는 `n_grain_folded`(= stage 모집단 − 산출 − 격리)를 기록형으로 감시한다 — 절단본 holder 0 · ownership 0 · audit 65 | `EG3_*` metrics |
| §2 매트릭스 18·19·22행 EG3 | `●(P01[,P07])` | 프레임 EG3(P01 유일·격리 어휘)에 더해 테이블 특화 게이트 3종을 붙였다 — `EG3_holder_daily`(src 어휘·**원천 왕복**·원천 전용 컬럼 누수·PIT 항등·지분 전 파생) · `EG3_ownership_snapshot`(집계행 누수 **독립 재판정**·범위 격리 완결·접힘·원장 집계행 대조) · `EG3_audit_opinion`(등급 어휘·원문 **독립 재분류** 대조·접힘 충돌 3축). P07(`ticker` VARCHAR(6))은 세 테이블에 `ticker` 컬럼이 없어 공허하다(`corp` 1행과 같은 취급) | `rules_s15.py` |
| 항진명제 회피축 | — | 세 게이트의 핵심 검사는 전부 **산출이 만들지 않은 축**을 읽는다(§5-C 규약): `n_src_roundtrip_miss` 는 산출 행이 그 `src` 의 stage 테이블에 (rcept_no, repror) 로 실재하는지 · `n_aggregate_leaked` 는 stage `row_kind` 대신 `AGGREGATE_LABELS` 어휘로 이름을 다시 판정 · `n_class_recomputed_mismatch` 는 `adt_opinion` 원문에서 `OPINION_CLASS_ORDER` 로 다시 분류해 stage 등급과 대조 · `n_class_conflict_grain` 은 접기 **전** stage 축에서 의견이 갈리는지 | `test_부정_audit_부적정은_적정으로_뒤집히지_않는다` |
| 부적정 선매칭 | STAGE_SPEC §2-13 이 stage 쪽에만 명시 | equity 는 등급을 **다시 만들지 않고** stage `adt_opinion_class` 를 나른다(두 벌을 만들면 함정을 두 곳에서 지켜야 한다). 대신 게이트가 원문에서 부적정 → 의견거절 → 한정 → 적정 순으로 재분류해 대조한다(기록형, 절단본 mismatch 0) — 손 트리로 stage 등급이 뒤집힌 행을 넣으면 `n_class_recomputed_mismatch` 가 1 이 된다 | `rules_s15.OPINION_CLASS_ORDER` |
| FX-4B-005 | "`ownership_snapshot` 롤링 2년 창 첫날 종목 1 → `available_date`, `coverage_from`" | **재정의.** 롤링 2년 창은 `elestock`(= `holder_daily`) 축이고(DART_DESIGN P3e: 재수집 불가) `stg_hyslr` 은 2013~ 전 구간이다. `coverage_from` 은 `ownership_snapshot` 컬럼도 아니다(그 축은 S19 `dataset_profile`). FX-4B-005 는 **grain 에 `stock_knd` 가 필요함을 못 박는 종류주 2행 쌍**으로 바꾸고, 창 경계는 `EG3_holder_daily.rcept_dt_min`·`rcept_dt_max`(절단본 2024-08-26 ~ 2026-08-26)가 기록형으로 남긴다 | `fixtures/ownership_snapshot.json` · `fixtures/holder_daily.json` |
| EG7 격리 어휘 | 18·22행 `●`(사유 없음) · 19행 `●(P07)` | `rcept_dt_missing` 을 세 테이블 모두 선언한다 — 구조적으로 0 이지만(stage 가 `rcept_dt` 를 required 로 둔다) 선언하지 않으면 결측이 왔을 때 EG2-P01 이 빌드 전체를 죽인다. `ownership_snapshot` 만 EG7-P07 `pct_out_of_range` 를 추가로 갖고 **상한 100 은 포함**이다(우리금융지주 완전자회사 100.00% 가 정상값) | `baseline_seed_s15.json` · `test_부정_ownership_지분율이_범위_밖이면_격리된다` |
| 지분율 범위 판정 대상 | 초안은 19행만 | `holder_daily` 는 **격리하지 않는다** — 담보·대차 표기로 100%를 넘는 행이 원장에 있을 수 있고 폐기하면 사건 스트림에 구멍이 난다(§5-C6 생존편향). `n_rate_above_pct_max` 를 기록형으로만 둔다(절단본 0, 범위 0.00~100.00) | `baseline_seed_s15.json` holder_daily.pct_max note |
| 집계행 대조 판정형 | FX-4B-001 은 `treasury_stock` 에 "집계행 제외 후 합산이 원장 집계행 값과 일치" | `ownership_snapshot` 의 같은 대조는 **기록형**이다(`agg_reconcile_n`/`agg_reconcile_n_close`, 허용오차 `agg_rate_tol_pct` 0.5%p). 원장 지분율이 소수 2자리라 주주 수만큼 반올림이 쌓이고 집계행이 종류주를 섞어 적는 경우가 있다 — 폐기형으로 두면 정상 법인 하나가 서버 빌드를 죽인다. 절단본 76그룹 중 75가 오차 안 | GATES §0-1 기록형 규약 |
| 입력에 `corp` | WORKFLOW §3-1 S15 입력 "…·S01" | 세 테이블 모두 1단계 `corp` 를 입력으로 고정하되 **산출에는 쓰지 않는다** — 법인 대조 `n_corp_unmatched`(기록형, 절단본 0) 전용이자 S01 선행의 실물 표현이다. 티커 축은 두지 않는다: grain 이 전부 법인 축이고 법인→티커는 1:N(우선주·재상장)이라 전개하면 EG1 이 깨진다 | DESIGN §4-5 확정 문구 |
| 상수 | 없음 | `ownership_snapshot.pct_min` 0 · `pct_max` 100(`_const` → `.sql` 격리 술어, 숫자 리터럴 금지 규약의 통로) · `agg_rate_tol_pct` 0.5(게이트 기록형) · `holder_daily.pct_max` 100(게이트 기록형) · `threshold_EG7` holder 0.0 · ownership 0.001(프레임 기본값 유지) · audit 0.0 | `baseline_seed_s15.json` |
| 규칙 판본 | — | `model.RULES_VERSION` 은 **올리지 않았다**(4B 는 새 테이블 셋이라 기존 산출을 바꾸지 않는다). 병합 시 오케스트레이터가 판단 | DESIGN §2 |
**S16 `shares_outstanding`·`treasury_stock`·`dividend_event` 구현 정정 (2026-09-06, `rules_s16.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §3-⑱ 우변 | `… FROM stg_dividend WHERE row_kind IS DISTINCT FROM 'aggregate'` | **술어 삭제** — `stg_dividend` 에는 `row_kind` **열이 없어**(stage `rules_dart.py:136` 은 hyslr·shares·tesstk 3개만) 초안 SQL 은 Binder 오류로 실행 자체가 안 된다. 우변 = `count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, stock_knd FROM stg_dividend)` = 절단본 221 | 초안 §5-B5 가 지목한 것과 같은 사실. `DESCRIBE stg_dividend`(절단본 1,227행)에 `row_kind` 부재 |
| §2 매트릭스 20행 EG4 | `FX-4B-001` | **`FX-4B-006` 신설** — §4 카탈로그의 FX-4B-001 은 `treasury_stock`(집계행 대조) 용이고 `shares_outstanding` 에 걸린 픽스처가 없었다(FX-1-017 과 같은 매트릭스↔카탈로그 불일치) | §4 4-B단계 표 |
| §2 매트릭스 20·21·23행 EG8 | `—` | 유지하되 **KRX 대조를 `EG3_shares_outstanding` 기록형으로 편입**. DESIGN §4-2 가 "KRX `shares_out` 정본 · DART 주식수는 검산·보조" 라고 못 박았으므로 어긋남은 폐기 사유가 아니다 — 폐기형 EG8 을 새로 만들지 않고 metric(`n_krx_compared`·`n_krx_mismatch`·`n_krx_stale`·`n_krx_ambiguous`·`n_krx_no_price`·`krx_price_lag_days_max`)만 싣는다 | DESIGN §4-2 · GATES §0-1 기록형 · S04 선례 |
| `shares_outstanding` 입력 | WORKFLOW §3-2 는 `stg_shares`·S01 | **`("stg_shares", "corp_ticker", "price_daily")`** — 산출식은 `stg_shares` 만 읽고 뒤 둘은 위 KRX 대조를 재현 가능하게 하려고 **고정만** 한다(S11 이 `stg_doc_meta` 를 고정만 하는 규약). 그래서 S16 은 S01 뿐 아니라 S02·S04 뒤에 선다 | DESIGN §2 입력 고정 · §4-2 |
| KRX 대조 대상 | (초안 없음) | `corp_ticker` 는 시점축이 없는 현재 스냅샷(grain `ticker`)이라 폐지·**티커 재사용** 구간에서 결산일과 수년 떨어진 가격 행이 잡힌다(절단본 036220 마지막 가격 2016-05-04 vs 결산일 2023-12-31 · 101970 2015-03-16 vs 2024-12-31). **결산일과 같은 해의 가격 행만** 비교하고 나머지는 `n_krx_stale`. 종류별 상장 티커가 둘 이상인 법인(대신증권 003545·003547)은 합산 규칙을 새로 만들지 않고 `n_krx_ambiguous` 로만 센다 | 절단본 실측: 종류 대응 103 → 다중 11 · 가격 없음 2 · stale 16 → 비교 73 · 어긋남 6 |
| FX-4B-001 합산 축 | "집계행 제외 후 합산이 원장 집계행 값과 일치" | 술어를 명시 — 원장에는 **'소계'**(직접취득 · 신탁계약)도 `row_kind='detail'` 로 실려 있어 그대로 더하면 이중계상이다(stage `rules_dart.py:291` 의 `_row_kind("acqs_mth1")` 은 **1축만** 보므로 `acqs_mth3='소계'` 인 행은 detail 로 남는다). 합산 축은 `acqs_mth3 <> '소계'` 인 잎 행뿐이고, 총계 축은 `row_kind='aggregate'` ∧ `acqs_mth3='총계'` 다. 소계 행 자체는 모집단에 남긴다(빼면 EG1 우변과 어긋난다) | 절단본 977행 중 소계 246 · 총계 대조 125그룹 전부 일치(취득·기말 두 축) |
| 판본 중복 | (초안 없음) | 세 테이블 다 grain 에 `rcept_no` 가 없어 겹칠 수 있다 → S11 선례 `QUALIFY row_number() … = 1`(first_write_wins) + 산출 컬럼 `n_src_rows` + 기록형 `n_collapsed_src_rows`. `dividend_event` 는 축이 (grain, `se`) 이고 **값 있는 행을 먼저 집는다**(`ORDER BY thstrm IS NULL, observed_date, rcept_no, thstrm`) — 절단본 60그룹이 전부 `stock_knd='-'` 에 한쪽만 값이 있는 모양이라 first_write_wins 만으로는 값이 사라진다(SK하이닉스 2016 주당 현금배당금 600 / NULL). 값이 둘 다 있고 다른 경우는 기록형 `n_value_conflict`(절단본 0) | 절단본: shares 0 · tesstk 58그룹 · dividend 60그룹 |
| `dividend_event` 컬럼 | `dps_krw`·`cash_total_krw`·`yield_pct`·`payout_pct` | **`payout_basis` 동반 컬럼 추가**(consolidated·separate·individual·unlabeled) — 원장 라벨이 `(연결)`·`(별도)`·`(개별)`·무접두로 갈리므로 값만 주면 기준을 모른다. 폐기형 2: 어휘 폐쇄 · `payout_pct IS NULL ⇔ payout_basis IS NULL`. `cash_total_krw` 는 라벨이 백만원이라 `_const.cash_total_unit_krw`(1,000,000) 배율을 곱한다(`.sql` 숫자 리터럴 금지 규약) | DESIGN §4-5 확정 4 · `baseline_seed_s16.json` |
| 법인 축 값의 위치 | (초안 없음) | 총액·성향·순이익 라벨은 원장이 `stock_knd='-'` 에만 싣는다 → **'-' 행에만** 값이 실리고 보통주·우선주 행으로 퍼뜨리지 않는다(grain 을 넘는 값 복제는 DESIGN §1 밖). 종류 행에 실려 오는 경우는 원천 서식 신호이므로 기록형 `n_firm_value_on_class_row`(절단본 0) | DESIGN §1 · 절단본 `se × stock_knd` 교차표 |
| 배당 기준일·락일 | §4-2 "배당·락일은 S16" | **행을 만들지 않는다** — `dividend_event` 에 기준일·락일 열이 없고 `corp_event` 에 `cash_dividend` 행도 만들지 않는다(원천 부재). 절단본 `stg_disclosure` 현금·현물배당결정 공시 **0건**이라 결정공시 경유 보완도 서지 않는다. DESIGN §11 "TR 불가(배당락 원천 없음)" 유효 | DESIGN §4-5 확정 5 · §11 |
| baseline 상수 | — | S16 이 요구하는 `_const` 는 `dividend_event.cash_total_unit_krw` 하나(`baseline_seed_s16.json`). 임계는 세 테이블의 `threshold_EG7` = 0.0(절단본 격리 0) — **서버 재측정 필요**. KRX 대조·원장 총계는 상수를 등재하지 않는다(둘 다 기록형) | §1 EG7 · §7-3 |
| 규칙 판본 | — | `model.RULES_VERSION` 미변경 — S16 은 기존 테이블의 산출을 바꾸지 않고 새 테이블 3개만 더한다. 새 테이블의 첫 빌드는 EG5a `skip(no_previous_build)` 이고 재빌드에서 파티션 해시 동일을 확인했다 | DESIGN §2 · 로컬 P35 |
**S17 `consensus_daily` · 뷰 `v_consensus` 구현 정정 (2026-09-06, `rules_s17.py`·`sql/consensus_daily.sql`·`views.py`, 규칙 판본 e1.3.1 유지)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| EG6-P02 SQL | `available_date = coalesce(min_by(r.collected_date, r.date), min(r.date))` | **틀렸다.** duckdb 의 `min_by`/`arg_min` 은 인자가 NULL 인 행을 건너뛰므로, 최초 관측의 `collected_date` 가 NULL 이면 fallback(`date`) 대신 **그 뒤 처음으로 `collected_date` 가 있는 행**의 값을 돌려준다. 절단본 000660 2026-04: 초안 식 → **2026-04-10**, 옳은 답 → 2026-04-03(04-03·04-06·04-08 은 `collected_date` NULL). 구현은 `ORDER BY r.date LIMIT 1` 로 그 달 첫 관측 행을 고르고 stage 의 `available_date`·`available_basis`·`coverage_degraded` 를 통째로 대조한다 | 재현 test `test_equity_s17_consensus.py::test_GATES_초안의_min_by_식은_최초_관측을_못_고른다` · DESIGN §10 P36 |
| §3-⑲ 우변 v3 항 | `_reg_vocab`(domain='v3_metric') CROSS JOIN 으로 unpivot | equity 프레임에 `_reg_vocab` 이 없다. 우변은 `.sql` 의 모집단 CTE(`wise_obs`·`v3_long`·`v3_obs`)를 그대로 재사용한다(`rules_s17.population_sql`, S05·S11 규약) — 모집단 정의를 두 곳에 두지 않는다. 지표 어휘는 `rules_s17.V3_METRICS`(8) 이고 `.sql` 의 UNION ALL 가지와 순서까지 대조하는 테스트가 있다 | `rules_s17.EG1_RHS_SQL` · `test_v3_unpivot_대응표는_rules_선언과_SQL이_같다` |
| 판본 선택 입자 | "obs_month = 월 내 min(`date`)" (행 단위) | grain 이 `metric` 까지 내려가 있으므로 선택도 **지표 단위**다 — 그 달 첫날에 결측이던 지표는 그 지표가 처음 관측된 날 행을 고른다. 행 단위로 고르면 §3-⑲ 우변(지표별 non-null 조합)과 좌변이 어긋나고, 그 달 커버가 통째로 사라져 커버율이 조용히 줄어든다. 절단본에서는 결측이 티커 단위(003540 revenue 전건)라 두 정의가 같은 675행을 낸다 — ★ 서버에서 두 값이 갈리는지 확인 | DESIGN §4-6 · GATES §3-⑲ |
| `target_period` 어휘 | "그대로 노출" | **표기는 `YYYYMM` 으로 맞춘다.** wise 계열은 전부 6자리(`stg_consensus_monthly.pkey`·`stg_consensus_matrix.target_period`·`stg_consensus_annual.period`)이고 v3 만 `YYYY/MM` 라벨 표기다. 맞추지 않으면 같은 회계연도가 `202612`(wise)/`2026/12`(v3) 로 갈려 EG8-P07 이 **모집단 0 으로 공허하게 통과**한다. "그대로 노출" 은 12M forward 합성을 하지 않는다는 뜻이지 표기를 섞는다는 뜻이 아니다. 6자리가 아니면 격리 `target_period_invalid` | `test_target_period_어휘는_YYYYMM_으로_맞춘다` · DESIGN §4-6 |
| EG8-P07 축 | "v3 ⋈ WISE 겹침 구간 일치율" (구간만 명시) | 모집단은 **(ticker, `obs_date`, `target_period`, `metric`)** 이다. `obs_month` 로 맞추면 wise 는 월말·v3 는 월초 관측이라 서로 다른 날을 비교하게 된다(절단본 005930 2026-08: wise 08-31 7,397,268 vs v3 08-03 7,378,931). 왼쪽은 산출의 wise 행, 오른쪽은 v3 stage 값이라 두 원천이 실제로 만난다. 허용오차 상수 `consensus_daily.v3_wise_value_tol_rel` 신설(§1-12) | `rules_s17.eg8_overlap_sql` |
| `v3_wise_match_min` 성격 | 임계 하나 | 절단본 실측 **32/45 = 0.7111**(tol 0.001) — 어긋나는 쪽은 반올림이 아니다(최대 10.8%: 247540 2026-07-31 eps wise 312.34 vs v3 350). 두 WISE 엔드포인트(cF5001/cF5002 월간 vs v3 리비전)의 추정 모집단이 다른 것으로 보인다. seed 0.65 는 제안값이고 **★ 서버 실측 뒤 '왜 다른가' 를 먼저 판정하고 하한을 정한다** | `baseline_seed_s17.json._measured` |
| EG9-P06 (시총 분위별 커버율) | `consensus_daily` 매트릭스에 ● | S17 입력에 시총 축이 없다(WORKFLOW §3-2 S17 입력 = `stg_consensus_*`·`stg_v3_*`·S01). **대용**으로 `security_span` 을 입력에 넣어 그 달 상장 종목 대비 커버율을 기록형으로 남기고(절단본 0.583~0.636), 분위별 기록은 `dataset_profile.coverage_by_mktcap_quintile`(S19)이 맡는다 | DESIGN §4-7 · §11 「컨센서스 선택편향」 |
| EG2-P04 | 매트릭스 ●(P01–P04) | P04(`lag_known=false` stage 원천 → `dataset_profile` 행)는 `dataset_profile` 이 6단계 산출이라 5단계에서 실행할 대상이 없다. 두 원천 다 `lag_known=true`(wise `fetched_date` 측정 수집 시각 · v3 동결 사본 `collected_date`)라 P04 모집단 자체가 비어 있다 | `rules_wise.STG_CONSENSUS_MONTHLY.lag_known` |
| EG5c·EG11·`_asof/` | 뷰를 내는 슬라이스는 고정 표본으로 통과(WORKFLOW §3-2) | `v_consensus` 는 **`catalog.ASOF_VIEWS` 밖**이다. 표본 SQL 과 EG5c 차집합의 키가 (as_of, ticker, **date**)인데(`catalog.sample_sql`·`eg5c_asof_invariance`) 이 뷰에는 일별 date 축이 없고, `obs_month` 를 `date` 로 이름만 바꿔 실으면 DESIGN §4-6 이 금지한 날짜 축을 카탈로그 산출물에 굽는 셈이 된다. 대신 e2e 테스트가 같은 카탈로그를 read_only 연결 두 개로 열어 고정 as_of 2개의 해시가 같음을 직접 본다(EG11 규약) | `test_v_consensus_는_두_연결에서_같은_결과를_낸다` · DESIGN §5 |
| EG-C ⑨(EGC-09) | 5단계에서 선행 실행 | 판정 자체는 G05 팩터와 워크벤치 어댑터를 요구하므로 `contract.py` 등재는 7단계다. S17 은 그 **골자**(같은 as_of 에서 `obs_month` 축은 보이는데 `available_date` 축에서는 0)를 등식으로 고정한다 — 절단본 as_of 2026-06-30 에서 wise 행은 PIT 축 0, obs_month 축 >0. 상수 `obs_month_bias_min` 은 그래서 아직 등재하지 않는다 | `test_v_consensus_는_available_date_로만_자른다` · `baseline_seed_s17.json._seed` |
| FX-5-001 키 | "같은 (ticker, obs_month, target, metric) 에 3판본" | 절단본 wise 수집일은 2026-09-01·09-02 **둘뿐**이라 같은 키의 판본 수 최대 2다. 픽스처는 2판본이면서 **값이 실제로 바뀐** 키(000660 2026-08 202812 eps 458,131.68 → 450,128.71)로 잡았다 — 판본 수보다 '덮어쓰기가 최초 관측을 못 바꾼다' 를 고정하는 것이 목적이다. 서버에서 3판본 사례가 생기면 교체 | `fixtures/consensus_daily.json` |
| FX-5-002 기대값 | "est_mean 2행" | 두 관측점(2026-07-31·2026-08-31)의 **원본 값** 2행을 픽스처로 두고 리비전(= 48,338.64 / 47,928.74 − 1 = 0.00855228)은 테스트가 손계산한다. `stg_v3_revision_compare`·`stg_consensus_matrix.lookback` 유래 금지(EG4-P03)를 코드로 지킨 지점 | `test_두_관측점의_원본_값으로_리비전을_손계산한다` |
| FX-5-004 기대 컬럼 | `src` 2행 | `src` 를 기대값으로 두면 항진명제다(키에 `src` 가 들어간다). 두 행이 실제로 **다른 관측일**(wise 월말 2026-08-31 / v3 월초 2026-08-03)을 갖고 v3 만 `est_min`/`est_max` 가 NULL 인지로 본다 | `test_겹치는_달은_src_가_다른_2행이다` |
| `metric` 어휘 | v3 wide 8 | wise 쪽 `parse_failed`(stage `rules_wise` 주석 정본 "eps \| revenue \| parse_failed")를 포함해 **9**. 파서가 값 칸을 못 읽은 관측을 격리하면 '그 달 관측 없음' 이 되어 커버율이 조용히 부풀므로 값 NULL 로 남긴다(원칙 ④). 절단본에는 사례 0 | `rules_s17.METRIC_VOCAB` |
| `unit` 어휘 폐쇄 | 명시 없음 | v3 행만 폐기형(우리가 카탈로그 지식으로 부여한다: 억원·원·배·%), wise 행은 **기록형**(`n_wise_unit_outside_vocab`) — stage `unit` 은 데이터 값이고 stage 가 스케일 변환을 금지했다. 단위가 같은 계열임은 절단본 교차검증으로 확인(005930 2026-08-03 v3 revenue 7,378,931 = wise obs 2026-07-31 7,378,930.54 억원) | STAGE_HANDOFF §4 · DESIGN §10 P36 |
| EG7 격리 사유 | 없음 | `target_period_invalid` 하나. 절단본 0 이므로 `threshold_EG7` 은 코드 기본값(0.001)을 쓰고 baseline 등재는 2회차 이관 | `baseline_seed_s17.json._seed` |
| 규칙 판본 | — | **올리지 않는다**(e1.3.1 유지). `consensus_daily` 는 신규 테이블이라 직전 빌드가 없고(EG5a `skip(no_previous_build)`), 기존 테이블의 산출을 바꾸는 변경이 없다 — `views.py` 는 매크로 **추가**뿐이라 기존 6매크로의 본문이 그대로다 | DESIGN §2 규칙 판본 |

**S18 `opinion_daily`·`opinion_broker_daily` 구현 정정 (2026-09-06, `rules_s18.py`·`sql/opinion_daily.sql`·`sql/opinion_broker_daily.sql`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| v3 PIT basis | DESIGN §4-6 은 `opinion_daily` 의 PIT 축을 "수집 관측일 = `available_date`" 한 줄로만 적었고 stage 는 `stg_v3_analyst_opinions.available_basis='measured'`(`AvailableRule("column", column="date", basis="measured")`) 를 준다 | **v3 는 `default` + `coverage_degraded=true`** 로 내린다. 이 테이블에는 `stg_v3_revision_daily.collected_date` 에 해당하는 수집 시각 컬럼이 **아예 없어서** `date` 는 잰 수집일이 아니라 내용 라벨이다. 같은 상황을 §4-6 이 `consensus_daily` 에서 "v3 = `collected_date`(NULL → `date` default + `coverage_degraded`)" 로 이미 규정했으므로 그 규약을 전 행에 적용한다. wise 두 테이블은 `fetched_date` 가 실제 수집일이라 `measured` 유지 | `src/stage/rules_wise.py` `STG_V3_ANALYST_OPINIONS`(컬럼에 collected_date 없음) · 절단본 v3 809행 **전부** `observed_date ≠ obs_date`(DESIGN §10 P37 ②) |
| 판본 dedup 축 | "덮어쓰기 원천은 최초 관측만" | 그룹 = (`ticker`, `date`), 선택 = `observed_date` **최소** 1행(동률은 투영 컬럼 순서로 파기 — `corp.sql` 규약, EG5a). 접힌 수는 산출 컬럼 **`n_src_rows`** 로 싣는다(프레임 `build.py` 가 `_meta.n_dedup` 을 0 으로 고정하므로 `corp_event` 규약 계승, 위 S05 블록) | EG1 §3-⑳ 우변이 v3 를 `count(DISTINCT ticker, date)` 로 세는 것과 짝이 맞는다 |
| EG6 대상 술어 | 매트릭스 25행 `●(P01,P03)` — P01 은 `consensus_daily`(wise) 용 문구 | `opinion_daily` 판으로 옮겨 적는다. **P01** = 산출 wise·v3 행의 `observed_date` 가 그 stage 키 그룹의 `min(observed_date)` 와 같은가를 **stage 에서 독립 재계산**(+ v3 는 값 5축이 실제 그 원천 행에 있는지 역조인 `n_v3_payload_not_in_source`). **P03** = `is_latest*`·`*_current` 컬럼 부재(선언 컬럼 집합에서 판정). 부정 픽스처는 FX-N-005 대응으로 판본 선택을 `DESC` 로 뒤집는 변종 | `rules_s18.eg6_first_observation` · `tests/test_equity_s18_opinion.py::test_최신_관측을_고르면_EG6가_폐기한다` |
| EG8 술어·상수 | 매트릭스 25행 `skip(no_baseline)` (술어 미기재) | **v3 ⋈ wise 겹친 (`ticker`, `obs_date`) 의 값 5축(`opinion_score`·`target_price_krw`·`eps_krw`·`per`·`analyst_count`) 일치율 ≥ `opinion_daily.src_overlap_agree_min`**. 겹침 0 이면 `skip(no_cross_source)`, 상수 미등재면 `skip(no_baseline)` + 측정치(`agree_rate`·컬럼별 일치수) 기록. 절단본 실측 겹침 5키 · 일치율 **1.0** 이지만 표본이 5뿐이라 seed 에 넣지 않는다(1.0 을 굳히면 서버 첫 불일치에 폐기) | WORKFLOW §3-2 5단계 "v3⋈WISE 겹침 일치율 ≥ baseline" · `baseline_seed_s18.json._measured` |
| EG9-P06 (시총 분위별 커버율) | 매트릭스 25·26행 `●(P06)` | **분위 축은 S18 에서 낼 수 없다** — `mktcap_krw` 는 `universe_daily`(S03B)·`price_daily` 에 있는데 S18 의 선행은 S01 뿐이다(WORKFLOW §3-1). 대신 `security`(종목 축: 커버 종목수·`sec_type` 별 커버)·`security_span`(날짜 축: 관측일이 구간 안/뒤/앞)으로 커버 모집단을 **기록형** 으로 남기고 `coverage_by_mktcap_quintile` = `null` + 사유 문자열을 metrics 에 싣는다. 분위 축은 S19 `dataset_profile.coverage_by_mktcap_quintile` 이 낸다 | `rules_s18._coverage_metrics` · DESIGN §10 P37 ⑥ |
| S18 입력 | WORKFLOW §3-1 은 stage 4테이블만 적었다 | equity **`security`·`security_span` 2개를 입력에 더한다** — 위 EG9 커버 모집단이 읽는다. 산출 `.sql` 은 stage 만 읽고 두 equity 입력은 게이트 전용이지만 `BuildRecord.inputs` 에 고정돼야 커버율 기록이 재현된다(§2 입력 고정). 그래서 실행 순서는 `trading_calendar` → `security` → `security_span` → S18 이다 | `rules_s18.OPINION_DAILY.inputs` · `tests/test_equity_s18_opinion.py::_build_chain` |
| 등급 어휘 | DESIGN §4-6 에 어휘 명시 없음 | **stage 정본 계승**. `rules_wise._OPINION_CLASS` 가 원문 17종을 {`buy`, `hold`, `sell`, `other`} + 공란 NULL 로 접으므로 equity 는 **재계산 없이 폐쇄만 판정**한다(`n_opinion_class_outside_vocab`·`n_prev_opinion_class_outside_vocab`, 폐기형). 원문 `opinion`·`prev_opinion` 은 대소문자·한/영 표기 그대로 보존한다(절단본 8종). `opinion_daily.opinion_score` 는 WISE 가 이미 접은 점수라 임계로 등급을 굽지 않는다(§1 금지) | `tests/…::test_등급_어휘_밖이면_EG3가_폐기한다` |
| FX-5-007 | "임의 1행 · `analyst_count`·`available_date` · stage `stg_analyst_summary` · `= fetched_date`, basis `measured`" | (005930, 2026-09-01, `wise`) 를 축으로 23케이스로 펼쳤다 — wise 축 11(analyst_count 24 · available 2026-09-01 · basis measured · degraded false · base_date 2026-08-31 · eps 48339 · per 5.38 · n_src_rows 1 · observed 2026-09-01 · 다음 날 판본 22 / 487045) + v3 축 12(basis default · degraded true · observed **2026-09-02** ≠ obs_date · 겹친 키 값 일치 493958 · wise 전용 축 NULL · v3 전용 키 491875 · 빈 행 NULL 유지) | `src/equity/fixtures/opinion_daily.json` |
| FX-5-008 | "임의 1행 · `prev_opinion_date` · hand · 직전 `opinion_date`" | **절단본에는 실사례가 없다** — 3일치(09-01~03) 안에서 (ticker, broker) 84쌍 전부 `opinion_date` 가 하나뿐이라 249행 전부 NULL 이다. 픽스처는 그 NULL 과 동반 `prev_opinion_date_available_date` NULL 을 못박고(18케이스), 실제 되찾기는 합성 하네스(09-01·09-02 에 08-10 → 09-03 에 08-20)로 검증한다. 서버 9,717행에서 실사례가 나오면 등재한다(S05 FX-2-003 과 같은 처리) | `tests/…::test_직전_의견일은_우리_관측_이력에서만_되찾는다` |
| `prev_opinion_date` PIT 폐쇄 | 없음(신설) | 후보를 **`p.fetched_date <= r.fetched_date`** 로 묶는다 — 이 제약이 없으면 나중에 긁은 이력이 과거 행을 채우는 look-ahead 다. DESIGN §3 이 요구하는 동반 컬럼 `prev_opinion_date_available_date` 는 **구성 행에서 집계**해(자기 행 available 과 `greatest`) 싣고, EG3 이 같은 식을 stage 에서 다시 세어 대조한다(EG2-P05). 두 CTE 의 제약을 다 지우면 EG3 `n_prev_available_mismatch` 가 잡는다 | `tests/…::test_나중_관측으로_과거_행을_채우면_EG3가_폐기한다` |
| EG7 격리 사유 (신설) | §1 EG7 표에 S18 행 없음 | `opinion_daily`: `nonpositive_target_price`(목표가 ≤ 0, NULL 은 위반 아님) · `base_date_after_obs`(기준일 > 관측일). `opinion_broker_daily`: `nonpositive_target_price`(현재·직전 목표가) · `opinion_date_after_fetch`(의견일 > 수집일). **관측일 역전을 EG2 폐기가 아니라 격리로 다루는 이유**: 한 행 때문에 테이블 전체를 버리면 나머지 관측이 사라진다. 격리 뒤 EG2-P02(내용일 축 = `base_date` / `opinion_date`)는 살아남은 행에서 참인 불변식이 된다. 절단본 실측 4종 전부 0 | `tests/…::test_관측일이_역전되면_격리된다` · `test_관측일_역전을_격리하지_않으면_EG2가_폐기한다` |
| `stg_wise_coverage` 사용처 | WORKFLOW §3-1 S18 입력 목록에만 등장 | **팩트 컬럼으로 내리지 않는다** — stage `write_mode='upsert'`·`available=AVAILABLE_NONE` 인 현재값 라벨이라 팩트 행에 실으면 EG6-P03 이 금지하는 `_current` 컬럼이 된다. `opinion_daily` 의 EG9 커버 대장 대조(`wise_coverage_status`·`n_wise_covered_without_row`)에만 쓴다. `stg_calls_wise` 는 격자 테이블 `fill_kind` 의 근거 축이라 S18 에서는 쓰지 않는다 | `src/stage/rules_wise.py` `STG_WISE_COVERAGE` |
| baseline 상수 | — | S18 은 새 상수를 **등재하지 않는다**(`baseline_seed_s18.json` 이 이유와 로컬 실측을 기록). EG8 의 `src_overlap_agree_min` 만 사람 승인 대기이고, EG7 비율은 첫 빌드 코드 기본값 → 2회차에 `opinion_daily.thresholds.EG7`·`opinion_broker_daily.thresholds.EG7` 이관 | §1 EG7 · §1-12 등록부 |
| 규칙 판본 | — | `model.RULES_VERSION` 을 올리지 않는다 — S18 은 **새 테이블 2개** 이고 기존 테이블의 산출을 바꾸지 않는다. 첫 빌드는 `skip(no_previous_build)`, 재빌드 EG5a pass(해시 `819:614dcf63f13f829d`·`249:9a0c62ae8a8931c7` 동일, DESIGN §10 P37) | DESIGN §2 규칙 판본 |

**S08 — `flow_daily` 수급 격자 (2026-09-06, DESIGN §4-3 구현 → `rules_s08.py`·`sql/flow_daily.sql`·`fixtures/flow_daily.json`·`baseline_seed_s08.json`, 규칙 판본 e1.3.1 유지)**

절단본 실측(DESIGN §10 P31): 36,972행 = universe_daily 41,066 − ETF 4,094 · 격리 1,732(pre_calendar 18 + off_grid 1,714) · measured 18,581(커버 50.26%) · src_omitted 6,109 · not_collected 12,282 · 재빌드 해시 동일.

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §3-⑪ (a) 등식 | `count(T) − count(grid) = 0` (격리 항 없음) | 프레임 `gates.eg1_equation` 은 `expect = rhs − n_reject` 를 강제하므로 좌변 = `count(DISTINCT (date,ticker))`, 우변 = 격자 + **격자 밖 원장 행수**(둘 다 `universe_daily` 를 직접 다시 읽는다) → `expect = 격자`. 이 형태는 격자 밖 행이 격리되지 않고 산출로 **새는** 사고에는 눈이 먼다(격리가 준 만큼 좌변이 늘어 함께 움직인다). 그 축은 `EG3_flow_daily` 의 **`n_row_outside_grid`**(산출 → `universe_daily` 안티조인)와 **`n_grid_cell_missing`**(반대 방향) 두 폐기형이 각각 잡는다 — 부정 픽스처 `flow_grid_leak` 이 'EG1 pass · EG3 FAIL(1,714)' 을 고정한다. 우변에 `(SELECT count(*) FROM out_rej)` 를 넣으면 EG1 하나로 다 잡히지만 `python -m equity gate` 재판정 문맥에는 격리 뷰가 없어(`_cmd_gate` 가 `reject_view=None`) EG1 이 Binder 오류로 죽는다 | `rules_s08.FLOW_DAILY.eg1_rhs_sql` · `tests/test_equity_s08_flow.py::test_격자_밖_행이_새면_EG1이_잡는다`·`::test_gate_재판정은_격리_뷰_없이도_돈다` |
| §3-⑪ (b) 원장 보존 | `reason IN ('pre_calendar','off_grid')` 로 `_reject_counts` 합산 | `_reject_counts` 는 **사유 축뿐이라 원천을 못 가른다** — 두 원천의 격리가 섞이면 한쪽이 새도 합이 맞을 수 있다. 격리 뷰(`out_rej`)의 `src` 로 갈라 원천별 등식을 세운다: `count(stg_x) = measured(src) + reject(src)`. 별도 게이트 **`EG1_ledger`**(폐기형, EG3 뒤 실행). 격리 뷰가 없는 재판정 문맥에서는 합계 등식(`Σ원장 = Σmeasured + n_reject`)으로 낮춰 세우고 `reject_split_by_src: false` 를 남긴다 | `rules_s08.eg1_ledger` |
| §3-⑪ (c) 컬럼 계승 | `_reg_vocab` domain `kiwoom_investor_column` | 그 레지스트리 테이블이 없다 → **stage 선언에서 독립 산출**: `rules_kiwoom.STG_FLOW_DAILY_KIWOOM.columns` 중 `unit_scale == 1e6` 인 13개(`_flow_krw` 만 갖는다). 같은 튜플이 산출 컬럼 순서·`input_columns`·KIS 대응표 키의 정본이다 | `rules_s08.KIWOOM_INVESTOR_COLUMNS` |
| §3-⑪ (d) 격리 건수 회귀 | `n = bl_int(T, 'reject_' || reason)` | **미등재** — 절단본 격리(1,732)는 15티커 중 재상장 2종이 만든 값이라 서버(3,478 중 2)와 축이 다르다. 지금 등재하면 서버 첫 빌드가 잘못된 기준으로 폐기된다. `EG1_ledger.n_reject_by_reason` 기록형으로 남기고 서버 실측 뒤 승격 | GATES §7-3 |
| EG3-P06 `num_nulls` | `num_nulls(...) = 0` 으로 12컬럼 결측 행 제외 | duckdb 1.5.5 에 `num_nulls` 가 **없다**(Catalog Error). `c IS NOT NULL AND …` 12항으로 같은 술어를 쓴다. 제외 행수는 `n_investor_sum_excluded_null`(절단본 0) | `rules_s08.eg3_flow_daily` |
| EG3-P06 허용오차 | `flow_daily.investor_sum_tol_krw` 등재 단계 3, 값 미정 | **6,000,000** 등재. 절단본 실측 최댓값은 4,000,000 이지만 값은 **반올림 상한의 유도값**이다 — 키움이 12주체를 각각 백만원 단위로 반올림하므로 합의 오차는 12 × 0.5백만 = ±6백만을 넘을 수 없다. 실측 최댓값을 굳히면 서버(7.6M 행)에서 정당한 반올림이 폐기형 게이트를 깨뜨린다. 단위 오적용·매핑 누락은 오차가 1e8 이상이라 이 값으로도 전부 걸린다 | `baseline_seed_s08.json` `_measured` · P31 |
| EG7 임계 | 코드 기본값 0.001, 2회차부터 이관 | **첫 빌드부터 `flow_daily.threshold_EG7` = 0.05 등재.** 격자 테이블의 EG7 격리는 결함 행이 아니라 '원장이 격자보다 넓다' 는 사실(캘린더 하한 이전 + 폐지~재상장 티커 재사용)이라 절단본 비율이 0.0448 이다. 0.001 을 쓰면 첫 빌드가 반드시 폐기된다 | P31 · GATES §1 EG7 |
| EG3-P13 어휘 폐쇄 | 템플릿(컬럼×domain 은 `_reg_column` 이 선언) | `_reg_column` 이 없으므로 `EG3_flow_daily` 가 직접 센다: `src ∉ {kiwoom, kis}`(NULL 은 술어 밖 — `not_collected` 셀은 원천이 없다) · `fill_kind['kind'] ∉ FILL_KINDS` · `fill_kind['evidence'] ∉ FILL_EVIDENCE` · `fill_kind IS NULL` | `model.FILL_KINDS`·`FILL_EVIDENCE` |
| EG3 단위 방어 | 없음 | **추가(폐기형)** — measured 행을 원장에 **다시 조인**해 13컬럼(키움)·10컬럼+무대응 3 NULL(KIS) 전수 대조(`n_kiwoom_value_mismatch`·`n_kis_value_mismatch`). stage 가 이미 한 ×1e6 을 equity 가 또 하거나 주체를 뒤바꾸면 여기서만 잡힌다(부정 픽스처 `flow_unit_flip`·`flow_swap`). 양방향 정합 `n_measured_without_ledger`·`n_unmeasured_with_ledger`, 격자 재조회 `n_row_outside_grid` 도 폐기형 | `tests/test_equity_s08_flow.py` |
| FX-3-001~007 | 7건 전부 EG4 골든 픽스처 | **EG4 는 스칼라 컬럼만** 받는다(`CAST(col AS VARCHAR)` 비교) — `fill_kind` 는 STRUCT 라 기대값이 duckdb 의 구조체 렌더링 문자열이 되어 판본에 종속된다. 그래서 fill_kind 픽스처(FX-3-001·003·004)와 KIS 셀 픽스처(FX-3-002·005·007)는 **EG4 에 넣지 않고** `tests/test_equity_s08_flow.py` 가 셀 단위로 검사한다(FX-3-006 12주체 합은 성격상 EG3-P06). EG4 에는 **절단본·서버 양쪽에서 같은 값**인 것만 넣는다 — `fixtures/flow_daily.json` 7건: FX-3-010a/b/c(005930 2018-05-04 외국인·개인·내외국인 = 원장 값) · FX-3-011(2010-01-04 캘린더 하한 안쪽) · FX-3-012(0001A0 문자 티커) · FX-3-013(036220 2020-01-02 폐지 구간 행 부재 = off_grid) · FX-3-014(036220 2015-01-05 미측정 셀 NULL — 0 채움 금지). 절단본 KIS 원장이 0행이라 FX-3-002/005/007 은 **S08-2 로 이월** | 절단본 한계(P31) · P26′ 의 '모집단 의존 픽스처 제거' 선례 |
| EG8-P05(겹침 0) · EG9 | 매트릭스 12행에 ● | **S08 미구현.** EG8-P05 는 `EG3_flow_daily.n_src_overlap` 기록형(절단본 0)으로 대신하고, EG9 는 상수 5개(`coverage_probe_dates`·`coverage_min`·`evidence_rate_min`·`coverage_return_corr_max`·`corr_min_months`)를 **등재하지 않는다** — 절단본 커버율 50.26% 는 15티커 표본이라 서버(원장 7.6M / 격자 ≈ 9.2M)와 다른 축이고, 지금 굳히면 서버 첫 빌드가 잘못된 기준으로 폐기된다. `coverage_measured`·`evidence_rate`·`n_grid_cells_after_<src>_max` 를 기록형으로 내보내고 서버 실측 뒤 S08 후속에서 붙인다 | `baseline_seed_s08.json` `_seed` |
| EG9-P04 SQL `stg_units_kis.date` | `u.ticker = g.ticker AND u.status='ok' AND u.date = g.date` | `stg_units_kis` 에 **`date` 컬럼이 없다** — 축은 `(dataset, ticker, window_from, window_to)` 다. 판정은 `dataset='flow' ∧ date BETWEEN window_from AND window_to`. 키움 샤드도 같은 규약(`src_api='ka10060' ∧ date BETWEEN req_start AND req_end`) | 절단본 DESCRIBE |
| 외국인 3컬럼 | `flow_daily` 컬럼에 포함 | **S08-2 로 이월** — 원천이 `stg_foreign_daily`(ka10008)인데 절단본에 없어 검증축이 0이다. 선언만 하고 NULL 로 두면 FIELD_MAP `flow.foreign_ownership`·F08 이 '있는데 비어 있는' 컬럼을 본다. 선행 조건은 절단본에 `stg_foreign_daily` 를 잘라 넣는 것 | DESIGN §4-3 S08 확정 ① |
| 절단본 `stg_flow_split_daily` | 입력으로 읽는다 | 절단본은 **0행 · 파티션 0개**(README 의 '0행 테이블은 스키마 보존용 빈 파티션 1개' 규약 누락)라 `read_parquet` 이 IOException 을 던져 입력으로 열 수조차 없다. 테스트가 stage 선언(`rules_kis.STG_FLOW_SPLIT_DAILY.columns[].decimal_type`)에서 **스키마만** 복원한 0행 파티션을 갖는 사본 루트를 만든다(`_repaired_stage_root`, 다른 테이블은 심볼릭 링크). 값은 지어내지 않는다 — KIS 대응표는 별도 손 하네스(합성 3행)로 검사 | `tests/test_equity_s08_flow.py` |
**S09 — `short_daily` 공매도·대차 격자 구현 정정 (2026-09-06, DESIGN §4-3 → `rules_s09.py`·`sql/short_daily.sql`·`fixtures/short_daily.json`·`baseline_seed_s09.json`, 규칙 판본 상향 없음)**

절단본 실측(DESIGN §10 P32): 격자 **36,972**(= `universe_daily` 41,066 − ETF 4,094) · 격리 **1,042**(전건 `pre_calendar`, 키움 ka10014 2008-06-23~2009-12-30) · 원장 보존 소스별 delta 0(키움 18,265 = 17,223 + 1,042 · KIS 공매도 3,891 · KIS 대차 1,959, dedup 0) · 겹침 셀 0 · 픽스처 25 · 재빌드 EG5a pass.

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §2 매트릭스 13행 · grain | `flow_daily` 와 같이 `src` 를 PK 에 두는 결합 테이블로 읽음 | `short_daily` 는 grain **(date, ticker)** 이고 원천은 접미사 컬럼(`_kiwoom`·`_kis`)으로 나란히 산다(사용자 확정 09-06). ⑪ (a) 좌변은 `count(*)` — 「src 가 PK 에 들면 `count(DISTINCT (date,ticker))`」 단서는 이 테이블에 해당하지 않는다. `fill_kind` 도 원천마다 하나(`fill_kind_short_kiwoom`·`fill_kind_short_kis`·`fill_kind_loan_kis`) | DESIGN §4-3 구현 결과 ② · 절단본 겹침 0행 |
| ⑪ (a) 우변 | `count(*) FROM grid` 와 좌변의 차 | 프레임 EG1 은 `lhs = rhs − n_reject` 한 꼴만 받는다 → 우변 = 격자 재계산 + **격자 밖 원장 셀 수**(3원천 UNION 의 distinct (ticker,date) 중 격자에 없는 것). 결과적으로 등식은 `count(out) = 격자` 로 닫히면서 「우리가 격리한 수 = 독립 재계산한 격자 밖 셀 수」까지 함께 검사한다 | `rules_s09.SHORT_DAILY.eg1_rhs_sql` |
| ⑪ (b) 소스별 원장 보존 | `_reject_counts` 를 읽는 SQL 1개 | 프레임 EG1 이 등식 하나뿐이라 **테이블 특화 훅 `EG1_short_daily`** 로 뗀다(실행 순서상 EG3 뒤). 원천마다 `n_src − n_dedup − n_measured − n_offgrid = 0`, 그리고 `n_offgrid` 는 산출의 `_reject/` 가 아니라 stage ⋈ `universe_daily` 로 **독립 재계산**한다(§5-C 항진명제 금지). `n_dedup` 은 stage `key_unique=false` 인 KIS 두 테이블의 재수집 판본 접힘(= 원장 행수 − distinct (ticker,date))이며, 판본 선택은 PIT(`min(observed_date)`, 동률은 값 컬럼 전순서 — EG5a 재현성) | HANDOFF §2 · `rules_s09.eg1_short_daily` |
| ⑪ (d) 사유별 정확 건수 | `n = bl_int('${T}', 'reject_' || reason)` | **S09 미등재** — 절단본 수치(1,042)를 등재하면 서버에서 틀린다. 서버 첫 빌드 뒤 오케스트레이터가 `short_daily.reject_pre_calendar`·`reject_off_grid` 를 등재한다. 그때까지 회귀 감시는 EG7 비율 + `EG1_short_daily` 의 `n_reject_<reason>_<src>` 기록형 | `baseline_seed_s09.json._seed` |
| EG7-P06 임계 | 코드 기본값 0.001 | `pre_calendar` 는 「캘린더 하한 2010-01-04」 라는 **설계가 만드는 구조적 격리**라 0.001 로는 정상 빌드가 첫판부터 폐기된다(로컬 0.02741 · 서버 예상 58,211/1,040만 = 0.0056). `short_daily.thresholds.EG7` **0.05** 를 seed 에 제안값으로 등재(사람 승인 대기) | `baseline_seed_s09.json._measured` |
| EG3_short_daily 폐기 범위 | 테이블 특화 불변식 일반 | **위반형은 어휘·격자뿐**(사용자 확정 09-06): `fill_kind.kind`·`evidence` 어휘 폐쇄(3원천) · `*_basis` 어휘 폐쇄 · ticker 폭(EG3-P07) · 산출 ⊆ 격자 ∧ 격자 ⊆ 산출(양방향). 대차 음수 잔고·원천 간 비율·단위 미측정 축은 **기록형** — stage 가 keep 한 원장 사실이거나 서버 실측 뒤 baseline 승격을 기다리는 측정치라, 지금 임계를 박으면 사실을 게이트가 지운다 | GATES §0-1 기록형 |
| EG3_short_daily 기록형 | — | `unit_labels`(산출 컬럼 → stage 가 실제로 잰 단위) · `fill_kind_<src>` 분포(kind:evidence) · `n_lending_balance_negative`·`_krw_negative`·`lending_balance_min_shr` · `n_short_avg_price_kis_null_measured`(원장 '0' = ledger_zero) · `n_kis_acml_invalid` · `n_overlap_src_measured`·`corr_short_volume/value_kis_kiwoom`·`short_volume_ratio_quantiles`(p05/25/50/75/95) · `n_zero_without_log_evidence_<src>`(**EG9-P04 예비 측정**) · `n_measured_with_empty_evidence_<src>` · `n_grid_without_price_axis` · `n_kiwoom_shard_rows`/`_tickers` | `rules_s09.eg3_short_daily` |
| §4-3 「KRX 가격 행 존재일」 | `fill_kind` 판정 사다리의 조건 | 격자가 이미 보장한다(격자 ⊆ `security_span` ⊆ 상장 존재일; 절단본 위반 0) → 술어에서 빼고 `universe_daily.no_trade_run IS NULL` 로 세어 `n_grid_without_price_axis` 로 기록. 합성 stage 가 1건을 만들어 지표가 움직이는지 확인한다 | DESIGN §4-3 구현 결과 ③ |
| 로그 축 입력 | 「입력 = equity 2 + stage 3원천」 | 팩트 원천 3 + **로그 축 2**(`stg_shards_kiwoom`·`stg_units_kis`). 이 둘이 빠지면 `fill_kind` 가 `measured`/`not_collected` 두 값으로 무너지고 `evidence` 어휘(shard_done·unit_ok·shard_empty·unit_empty)가 죽어 FX-3-001·004 가 성립하지 않는다. 팬아웃 방어: 키움 샤드는 티커 단위로 접고(min req_start · max req_end · status 우선순위 max), KIS 유닛은 캘린더로 펼쳐 (dataset, ticker, date) 로 접는다 — 창이 겹치는 재수집이 격자를 불리지 못한다 | DESIGN §3 `fill_kind` · `sql/short_daily.sql` shard·unit_day |
| EG9 (매트릭스 13행 ●P01–P04) | S09 범위 | **S09 미구현** — 상수 5종(`coverage_probe_dates`·`coverage_min`·`evidence_rate_min`·`coverage_return_corr_max`·`corr_min_months`)이 전부 서버 실측이라 첫 빌드는 어차피 `skip(no_baseline)` 이다. 상수가 없는 EG9-P04(미수집→0)만 `EG3_short_daily` 가 기록형 `n_zero_without_log_evidence_<src>` 로 미리 잰다(부정 픽스처로 16,173 까지 움직이는 것을 확인). 서버 baseline 등재와 함께 승격 | §1 EG9 · `baseline_seed_s09.json._seed` |
| FX-3-009 대상 | `lending_balance_kiwoom_raw`(`stg_lending_daily.rmnd`) | 키움 대차 원장이 입력에 없어(DESIGN §4-3 구현 결과 ①) 같은 규약을 **키움 공매도 평균가**에 적용한다 — `shrts_avg_pric` 은 stage 가 단위를 못 잰 축이라 `short_avg_price_kiwoom_raw` + `short_avg_price_kiwoom_basis`('unknown', 값 없는 셀은 NULL)로 간다. 픽스처 `fx3_009_unit_unknown_raw_value`·`_basis` | STAGE_DESIGN §5 「단위를 모르면 접미사 금지」 |
| FX-3-001·002·003·004 키 | 서버 사례(2020-06-30 · ka10014 empty 60건 · 샤드 미커버 1,070 등) | 절단본 키로 재지정: FX-3-001(src_omitted/shard_done) = (`036220`, 2010-01-04) · FX-3-002 계열(measured/unit_ok, 폐지 종목) = (`000030`, 2014-11-20) · FX-3-003(로그 없는 셀 → not_collected/none) = (`005930`, 2026-08-20)의 KIS 축과 (`005935`, 2010-01-04)의 키움 축 · **FX-3-004(shard_empty)는 절단본에 사례가 없어** 합성 stage 로 옮긴다(`test_샤드_empty와_유닛_empty는_empty_response다`) | `src/equity/fixtures/short_daily.json` · 절단본 샤드 전건 `done` |
| 픽스처 비교축 | 스칼라 컬럼 | `fill_kind_*` 는 STRUCT 라 EG4 가 `CAST(col AS VARCHAR)` 로 비교한다 — 기대 문자열은 duckdb 표기 `{'kind': measured, 'evidence': shard_done}`. 값 셀의 짝(`…_value_null`)을 같이 두어 「미수집인데 0」 이 픽스처에서 폐기되게 한다(부정 픽스처 `short_zero_fill` 이 EG3 를 통과하고 EG4 에서 걸린다) | `tests/test_equity_s09_short.py` |
| 싣지 않는 축 | — | KIS 누적 6컬럼(`acml_*`)·키움 `ovr_shrts_qty_shr` 는 **수집 창의 산물**이라 팩트 테이블 밖(절단본 실측: `ovr_shrts_qty_shr` 는 샤드 요청창 시작부터의 `shrts_qty_shr` 누적합, 005930 2008-06-23~ 전건 일치 → 원값에 새 정보 없음). 두 원천의 종가·전일대비·거래량은 `price_daily` 정본과 중복 | DESIGN §4-3 구현 결과 ④⑤ |
| 부정 픽스처(§7-5) | — | 6건: 격자 행 제거 → EG1 delta −4,094 · 격리행을 채택으로 승격 → EG1 **통과**·EG1_short_daily delta −1,042(프레임 행수 등식이 못 보는 자리) · 격자 티커 치환 → EG1·(b) 통과·EG3_short_daily `n_grid_missing`=`n_grid_extra`=4,094 · evidence 어휘 파괴 → 36,972 · basis 어휘 파괴 → 36,972 · 미수집 0 채움 → EG3 통과·EG4 폐기. 합성 stage(3티커 × 6세션)가 절단본에 없는 축을 본다: `empty_response` 두 갈래 · 재수집 PIT 접힘 · `off_grid` 격리 · 겹침 상관 1.0·비율 분위수 1.1 · 다른 API 샤드/다른 dataset 유닛은 증거 아님 | `tests/test_equity_s09_short.py` 31건 |
**S10 `credit_daily` 구현 정정 (2026-09-06, `rules_s10.py`·`sql/credit_daily.sql`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| §3 ⑪ (a)(b) 배치 | 등식 둘 다 EG1 | 프레임 EG1 은 등식 **하나**만 받는다(`eg1_lhs_sql`/`eg1_rhs_sql`) → **(a) 는 프레임 EG1**(좌변 `count(out)`, 우변 `격자 + 격자 밖 원장 행`; 프레임이 `n_reject` 를 빼므로 격자 행수와 격리 건수가 한 등식에서 닫힌다), **(b) 원장 보존은 `EG1_credit_daily` 테이블 특화 훅**(GATES §7-1 순서상 EG3 뒤). 두 등식을 한 스칼라 등식에 합치면 `n_out` 초과와 `n_measured` 부족이 상쇄될 수 있어 나눴다. 부정 픽스처도 분리 — measured 1건을 not_collected 로 바꾸면 (a) 는 통과하고 (b) 만 폐기 | `rules_s10.eg1_credit_daily` · `test_measured를_미수집으로_적으면_EG1_credit_daily가_폐기한다` |
| ⑪ 격자 술어 | `u.sec_type <> 'etf'` | **`u.sec_type IS DISTINCT FROM 'etf'`** — `<>` 는 `sec_type` NULL 종목을 조용히 격자에서 뺀다(§5-C6 이 EG7-P03 에서 지적한 "게이트가 만드는 생존편향" 과 같은 부류). 절단본·서버 모두 `universe_daily.sec_type` NULL 은 0 이라 값은 같고 규칙만 안전한 쪽으로 닫힌다. 술어 문자열은 `rules_s10.grid_predicate(alias)` 한 곳에서 나와 `.sql`·EG1 우변·EG3 재계산이 같은 정의를 쓴다(`test_격자_술어는_rules_선언과_SQL_리터럴이_같다`) | `sql/credit_daily.sql` `grid` CTE |
| FX-3-001 "값 0" 의 적용 범위 | 3단계 격자 공통 규약처럼 읽힘 | **`credit_daily` 는 0 을 굽지 않는다**(DESIGN §9 결정 8). measured 셀만 값을 갖고 `src_omitted`·`empty_response`·`not_collected` 는 17축 전부 NULL. 0 채움은 원장 일괄 결측일에 시장 전체 잔고를 0 으로 굳힌다 — 절단본 2018-03-28(전 종목 credit 행 0, 앞뒤 거래일 정상)·2026-08-19·20(P16 백필 08-18 종료). 유닛 창은 '요청 구간' 이라 그 날짜가 응답에 들었는지 말해 주지 않는다. 0 의 뜻은 `fill_kind.kind` 가 나르고 엔진 `CellKind.SOURCE_OMITTED_ZERO` 가 소비 시점에 읽는다. FX-3-001 은 `short_daily` 키움 샤드 축의 사례로 그대로 두고, S09 착수 때 같은 축으로 재심사한다 | `EG3_credit_daily.ledger_gap_dates` · `test_원장_일괄_결측일은_0으로_채우지_않는다` |
| FX-3-008 | "임의 1행 · 값·단위 접미사 보존" | 키 (`005930`, 2020-01-02) 로 고정: `whol_loan_rmnd_stcn_shr` 2,435,031 · `whol_loan_rmnd_amt` 11,467,248 · `whol_stln_rmnd_stcn_shr` 29,538 · `whol_loan_rmnd_rate_pct` 0.03 · `stlm_date` 2020-01-06(T+2). `fixtures/credit_daily.json` 20 케이스 = FX-3-008 계열 9 + fill_kind 3종 6 + 격자 경계 5(구간 첫날·재상장 첫날·ETF·폐지일·캘린더 하한 이전은 **행 부재**를 NULL 기대로 검사) | `src/equity/fixtures/credit_daily.json` |
| `*_amt` 단위 | "단위 미상" 만 명시 | 원값 보존 + **`amt_basis` 상수 컬럼 `'unknown'`**(DESIGN §1 basis 어휘) 로 기계가 읽게 한다. `_krw` 접미사는 붙이지 않는다(FX-3-009 규약). 실측 근거는 기록형 `loan_amt_per_market_value` = 금액/(잔고주수 × 원주가) p50 **8.3e-05** — 원화라면 1 근처여야 한다 | `EG3_credit_daily` metrics |
| EG3_credit_daily 술어 | "음수·단위·잔고 ≤ 상장주식수" | 폐기형 16: 어휘 3(`fill_kind.kind`·`.evidence` 밖 · NULL) + `n_amt_basis_mismatch` + `n_ticker_bad_width` + 격자 2(`n_grid_missing`·`n_grid_extra`, `universe_daily` 재계산) + measured 3(`n_measured_without_src_row`·`n_src_row_without_measured`·`n_measured_value_mismatch` = `stlm_date` + 측정 16축 재조인) + `n_unmeasured_value_present` + `n_stlm_date_before_date` + 잔고 2(`n_balance_negative` · `n_balance_over_shares_out` = 같은 날 `price_daily.shares_out` 재조인). **신규·상환·증감율의 음수는 폐기형이 아니다** — 원천에 실재한다(절단본 6행: 003540 2024-07-02·03 · 036220 2013-11-15·2015-06-08 · 101970 2013-07-03 · 247540 2025-01-10, 병합·감자 정정 추정) → `n_negative_by_column` 기록형 | `rules_s10.eg3_credit_daily` |
| EG7 임계 | 첫 빌드는 코드 기본값(0.001) | **첫 빌드부터 `credit_daily.thresholds.EG7` = 0.005 를 seed 에 등재**한다. `_reject/pre_calendar` 는 KIS 신용잔고 시작일(2009-11)과 KRX 지수 캘린더 하한(2010-01-04)의 차이라 구조적이고, 절단본 비율 0.002536·서버 추정 0.0023(P11 credit 24,497 / 격자 10.9M)이 기본값을 넘는다. ★ 서버 1차 빌드 뒤 실측의 1.5~2배로 조인다 | `baseline_seed_s10.json` `_measured` |
| §2 매트릭스 14행 EG8·EG9 | EG8 `skip(no_cross_source)` · EG9 `●(P01,P03,P04)` · P02 `skip(no_log_axis)` | **S10 은 둘 다 붙이지 않는다**(S04 의 EG8-P01 과 같은 규약 — `_meta` 에 항목 자체가 없다). EG8: 신용잔고의 독립 교차 원천이 없다. EG9(레짐 커버리지): 로그 축 독립 재판정은 `stg_calls_kis` 를 함께 읽어야 하고 3단계 세 테이블(flow·short·credit)의 공통 술어로 짜는 편이 낫다 → S08·S09 와 함께 붙인다. 그때까지 커버리지 사실은 `EG3_credit_daily` 의 `fill_kind_evidence_counts`·`n_ledger_gap_dates` 기록형이 나른다 | 매트릭스 14행 |
| `credit.net_buy` 판정 | "미확인 → S10 에서 판정" | **부재**(FIELD_MAP §2·§3 갱신). 원장 39컬럼에 순매수 축이 없고 후보 `신규 − 상환` 은 잔고 증감과 맞지 않는다 — 융자 17,364/24,711(70.3%), 대주 24,672/24,711(99.8%), 신규·상환 음수 6행. 매 빌드 `EG3_credit_daily.net_buy_axis`·`loan_balance_step`·`stln_balance_step` 이 근거를 갱신하므로 서버 실측에서 뒤집히면 metric 이 먼저 움직인다 | `test_net_buy_축은_원장에_없다` |
| 규칙 판본 | — | `model.RULES_VERSION` 은 **건드리지 않는다**(e1.3.1). `credit_daily` 는 신설 테이블이라 직전 빌드가 없고 EG5a 가 `skip(no_previous_build)` 로 시작한다 — 기존 테이블 산출을 바꾸는 변경이 아니다 | DESIGN §2 |

**S10 2차 — 잔고 이상 격리 (2026-09-06, 서버 1차 빌드 폐기 대응)**

서버 1차 빌드가 `EG3_credit_daily.n_balance_over_shares_out` **4** 로 폐기됐다 — 격자 9,201,516 · 격리 24,948 · 다른 술어 전부 0 · 같은 판의 `flow_daily`·`short_daily` 는 통과. 잔고주수 > 상장주식수는 불가능한 사실이므로 게이트를 늦추지 않고 원장 행을 격리한다.

| 항목 | 1차 | 2차 정정 | 근거 |
|---|---|---|---|
| 격리 어휘 | `pre_calendar`·`off_grid` | **`balance_over_shares` 추가**(EG7-P06 격리형, `_reject/balance_over_shares/`, EG7 분모 포함). 술어 = 격자 안 원장 행 중 `whol_loan_rmnd_stcn_shr > price_daily.shares_out ∨ whol_stln_rmnd_stcn_shr > shares_out`. `shares_out` NULL 은 판정축이 없어 위반이 아니다. 정의는 `rules_s10.over_shares_predicate(alias, shares)` 한 곳에서 나와 `.sql` `over` CTE·EG1 우변·EG3 재계산이 같은 문자열을 쓴다 | `rules_s10.REJECT_REASONS` · `test_잔고_초과_술어…`(격자 술어 테스트에 편입) |
| 격리 단위 | — | **원장 행만 격리하고 격자 셀은 지우지 않는다.** 그 (date, ticker) 셀은 17축 전부 NULL 로 남아 ⑪(a) 행수 등식이 유지된다(셀째로 버리면 그날 그 종목이 유니버스에서 사라져 생존편향). SQL 은 `base` 에 `drop_value` 플래그를 세우고 원장 조인의 `drop_value` 등호 축 하나로 값을 비운다(아래 성능 행) | `sql/credit_daily.sql` `over`·`base`·`cell` |
| 셀의 `fill_kind.kind` | — | **`empty_response`**(= `rules_s10.REJECTED_CELL_KIND`, 엔진 `CellKind.MISSING`). `FILL_KINDS` 에 `rejected` 를 **더하지 않는다** — 어휘는 DESIGN §3 · 엔진 `CellKind` 계약이라 슬라이스가 늘릴 축이 아니고, 남은 넷 중 `empty_response` 만이 "원천에 물었고 쓸 값이 없다" 를 뜻한다(`measured` 는 값이 없고, `src_omitted` 는 0 으로 읽히며, `not_collected` 는 수집됐다는 사실과 어긋난다). 한계: 진짜 빈 응답과 값에서 구분되지 않는다 → 어느 셀이 이 경로였는지는 `_reject/balance_over_shares/` 가 키·원값째 보관하고 EG3 가 건수를 대조한다. 3단계 전체에 이 경로가 흔해지면 `rejected` kind + 엔진 `CellKind` 대응 신설이 후속 | DESIGN §9 결정 9 |
| `fill_kind.evidence` | kind 별 분기 | **로그 사실만으로 정한다** — 그 셀을 덮는 유닛 창이 ok → `unit_ok` / empty → `unit_empty` / 없음 → `none`. 기존 네 kind 에서 값이 전부 같고(절단본 분포 불변), 잔고 이상 셀까지 한 규칙으로 덮는다 | `sql/credit_daily.sql` 최종 SELECT |
| EG1 우변 | 격자 + 격자 밖 원장 행 | **+ 잔고 이상 원장 행**. 프레임이 `n_reject` 를 빼므로 세 격리 사유가 한 등식에서 닫힌다 — 격리를 빼먹으면 EG1 이 먼저 잡는다(부정 픽스처 delta −1) | `CREDIT_DAILY.eg1_rhs_sql` |
| ⑪(b) `EG1_credit_daily` | 두 사유 합 | `REJECT_REASONS` 를 그대로 돌므로 **새 사유를 자동으로 받는다**(`n_ledger_delta` = 원장 − measured − Σ격리). 합성 검증에서 measured 24,627 + 85 + 9 + 1 = 24,722 | `rules_s10.eg1_credit_daily` |
| `n_balance_over_shares_out` | 폐기형(서버에서 4로 실패) | **산출 검사로 그대로 유지**(0 이어야 한다) — 격리했으므로 산출에는 위반이 남지 않는다. 임계를 늦추거나 술어를 지우지 않았다. 폐기형 3 추가: `n_balance_over_shares_reject_delta`(입력에서 다시 센 위반 행수 = `_reject` 건수) · `n_balance_over_shares_cell_measured`(그 키의 셀이 아직 measured) · `n_balance_over_shares_cell_missing`(그 키의 셀이 사라졌다). `n_src_row_without_measured` 는 격리 키를 제외하도록 좁혔다(격리된 원장 행은 measured 셀이 없는 것이 정상) | `rules_s10.eg3_credit_daily` |
| 기록형 추가 | — | `n_balance_over_shares_rejected`(격리 건수) · `n_balance_over_shares_src`(입력 재계산) | `_meta.gates[EG3_credit_daily].metrics` |
| 합성 검증 | — | 절단본에는 위반 행이 **0** 이라 stage 사본에서 (161890, 2020-01-02) 융자 잔고 280,837 → 9,999,999,999(그날 상장주식수 22,881,180 초과)로 바꿔 검사한다: 격자 36,972 불변 · 격리 95 · measured 24,627 · 셀 17축 NULL · `fill_kind {empty_response, unit_ok}` · `_reject` 에 원값 보관 · 전 게이트 pass. 격리 경로를 지운 변종은 EG1 이, 우변까지 늦춘 변종은 EG3 가 잡는다 | `test_잔고가_상장주식수를_넘는_원장행은_격리되고_셀은_비어_남는다` · `test_잔고_이상_원장행을_격리하지_않으면_폐기한다` |
| 서버 4행의 정체 | — | **미확인 가설**: 분할·병합 적용일 전후의 **상장주식수 갱신 지연**. KIS 신용잔고는 사건 반영이 빠르고 KRX `stg_listing_daily.list_shrs`(→ `price_daily.shares_out`)는 변경상장일에 갱신되므로, 병합·감자로 주식수가 줄어드는 날 잔고가 며칠 먼저 새 기준으로 넘어오면 비가 뒤집힌다. 로컬 절단본에 재현 사례가 없어 확인하지 못했다 — 서버에서 `_reject/balance_over_shares/` 의 (ticker, date)를 `adj_factor.apply_date`·`corp_event` 와 대조하면 판별된다. 4행뿐이라 EG7 비율에는 영향이 없다(24,948 → 24,952) | 서버 1차 빌드 `_failed/` 리포트 |
| EG7 임계 | seed 0.005 | 변경 없음. 서버 격리가 24,948 → 24,952(+4)로 늘어도 격자 9.2M 대비 0.0027 수준이라 임계 안이다 | `baseline_seed_s10.json` |
| 규칙 판본 | e1.3.1 유지 | 여전히 유지 — `credit_daily` 는 아직 커밋된 빌드가 없으므로(1차는 폐기) EG5a 가 `skip(no_previous_build)` 로 시작한다 | DESIGN §2 |
| 값 비우기 조인 (성능, 09-06) | `LEFT JOIN stg_credit_daily s ON s.ticker = b.ticker AND s.date = b.date AND NOT b.drop_value` | **한쪽만 보는 술어를 조인 조건에 두지 않는다** — `NOT b.drop_value` 는 좌변(b)만 보므로 DuckDB 가 `JoinCondition` 으로 쓰지 못하고, LEFT OUTER 라 필터로 내리지도 못해 물리계획이 `HASH_JOIN(RIGHT)` → **`BLOCKWISE_NL_JOIN(RIGHT)`** 로 떨어진다(격자 9,226,468 × 원장 8,404,204). 고친 형태는 `LEFT JOIN (SELECT *, false AS drop_value FROM stg_credit_daily) s ON s.ticker = b.ticker AND s.date = b.date AND s.drop_value = b.drop_value` — 원장 쪽은 언제나 false 라 뜻이 같고(양쪽 NULL 없음), 조건이 좌·우 등호라 3키 해시 조인으로 돌아온다. 서버 실측(`.venv` duckdb 1.5.5, `memory_limit='8GB'`, `threads=3`): 빌드 SQL **70분+ (kill) → 6.1초**(375aeb4 판 8.2초), 게이트 SQL 은 원인이 아니었다(EG1 0.07초 · EG3 31.4초 · 프레임 EG1 우변 3.7초). 산출은 동일 — 격자 9,201,516 · 격리 `pre_calendar` 24,497 · `off_grid` 451 · `balance_over_shares` 4 · `n_balance_over_shares_out` 0, EG1·EG3 pass | `sql/credit_daily.sql` `cell` · 서버 `EXPLAIN (FORMAT json)` 물리계획 |
---

**S21 본판 — 어댑터 필드 3 → 24 · `v_fin_latest` 신설 (2026-09-06, `equity_duckdb/_specs.py`·`_adapter.py`·`views.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| 어댑터 필드 수 | FIELD_MAP §2 의 42 전부 | **24** = 표의 23 + 내부 스코프 `price.adj_close`. 빠진 19 의 사유는 셋(원천 부재 10 · S08~S10 미병합 7 · 굽지 않기로 한 2)이고 `_specs.UNSUPPORTED_FIELDS` 가 field_id 마다 문장으로 남긴다. **`flow_daily`·`short_daily`·`credit_daily` 는 이 브랜치에 없다** — S08~S10 은 `equity/s08-s10` 에 있고 `equity/s15-s18` 의 조상이 아니다 | `git merge-base --is-ancestor equity/s08-s10 equity/s15-s18` → false · `database/src/equity/rules_s0{8,9}.py`·`rules_s10.py` 부재 |
| `v_fin_latest` 접는 축 | DESIGN §5 한 줄("CFS 우선, ttm, has_correction") | **판본(`vintage`)·`fs_div` 만 접고 기간 축은 남긴다.** as_of 로 기간까지 접으면 소비자가 세션마다 매크로를 다시 불러야 한다 — `v_adj_price_fwd` 처럼 as_of 를 컷오프·행 절단으로만 쓰고 세션 절단은 어댑터의 ASOF 조회가 한다. TTM 은 4행 non-null ∧ `period_end` 폭 240~400일 ∧ 창 안 `available_date ≤ 이 행의 available_date` 를 다 만족할 때만 선다(부분합 금지) | `views.TEMPLATES['v_fin_latest']` · DESIGN §5 |
| `v_fin_latest` 게이트 대상 | (미기재) | **`ASOF_VIEWS` 밖**(`v_consensus` 와 같은 근거 — 표본 키가 (as_of, ticker, **date**)인데 이 뷰에는 일별 date 축이 없다). 카탈로그 매크로 수는 `consensus_daily`·`fin_std`·`disclosure_version` 이 있는 체인에서 **8**, S06 체인(7테이블)에서는 6 + `skip(not_built)` 2 | `catalog.ASOF_VIEWS` · `test_equity_s06_views.py::test_카탈로그는_매크로_6개이고_뷰_게이트를_통과한다` |
| LATEST 필드의 랙 의미 | "랙 n = n 세션 전 행"(가격 축 기준) | **원천마다 갈린다.** GRID(가격·조정가)는 정확히 n 세션 전 행이고 그 세션에 행이 없으면 셀 없음(재상장 첫날이 직전 구간 값을 물지 않는다). LATEST(재무·컨센서스·의견·배당·자사주·임원지분)는 컷오프 이하의 **마지막 관측**이고 `available_date` 는 그 관측의 공개일이라 as_of 보다 몇 달 앞일 수 있다 — 계약이 요구하는 것은 `available_date ≤ as_of` 뿐이다 | `_adapter._cell` · `test_adapters_equity_duckdb.py::test_latest_fields_never_show_a_filing_before_its_available_date` |
| grain 축소 규칙 | (미기재 — "equity 는 전개하지 않는다" 만) | 포트가 셀 하나를 요구하므로 어댑터가 줄인다. **선언표에 적어 코드에 흩뿌리지 않는다**: `v_consensus` FY1(관측 달 이후로 끝나는 `target_period` 중 최소) · `opinion_daily` 잰 판본 우선(`coverage_degraded=false`) · `dividend_event` 종류 축 접기(값 있는 행 → 최신 연도 → `stock_knd` 사전순) · `corp_event`·`holder_daily` 같은 공개일 합 | `_specs.SOURCE_SPECS` · FIELD_MAP §3 표 |
| `classification.sector` | FIELD_MAP §2 "부분/미지원" | **미지원으로 닫는다** — `corp.induty_code` 는 시점축 없는 현재값 라벨이라 과거 세션에 붙이면 look-ahead 다. DESIGN §7 이 이미 `sector_id=None` 을 규약으로 못박았고, 계약 테스트가 "sector 필드를 안 내면 `sector_id` 도 None" 을 강제한다 | `test_raw_observation_port.py::test_port_exposes_raw_facts_only` |
| `short.short_balance_ratio` | FIELD_MAP §2 "부분" | **미지원으로 내린다(어댑터 판단)** — 분모가 다른 테이블(`price_daily.shares_out`)이라 원천 하나의 컬럼식으로 굽지 못하고, 원장 값 자체가 잔고가 아니라 거래량이다(라벨 정정 필요). S09 병합 때 `short_daily` 에 비율 컬럼을 두거나 레지스트리를 고치는 것이 순서다 | `_specs.UNSUPPORTED_FIELDS` |
| 계약 테스트 `FIELDS` | 5(`price.close`·`market_cap`·`adj_close`·`financial.book_equity`·`classification.sector`) | **29** 로 넓혔다 — equity 어댑터가 내는 24 + mock 전용 5. `_fields(adapter)` 가 어댑터별로 걸러 주므로 mock 은 미선언 필드를 무시하고, 창 독립·PIT·순서·kind 일치·두 포트 셀 대조 절이 **equity 24필드 전부**에서 돈다 | `backend/tests/contract/test_raw_observation_port.py` |
| 손 픽스처 | `build_workbench_root` 7테이블 | **15테이블**(+ `corp_ticker`·`fin_std`·`disclosure_version`·`consensus_daily`·`opinion_daily`·`dividend_event`·`corp_event`·`holder_daily`), 카탈로그 매크로 3 → **5**(+ `v_consensus`·`v_fin_latest` 본문 사본). 픽스처가 심는 부정 케이스: 같은 grain 의 CFS/OFS 2행(OFS 값 9,999 — 골라지면 안 된다) · 같은 (ticker, obs_date) 의 v3/wise 2행 · 종류 3행 배당(보통/우선/'-') · 같은 공시일 자사주 2건 + 다른 event_type 1건 · elestock/majorstock 혼재 · 값 전 결측 보고 1건 · 005930·005935 가 같은 법인 | `backend/tests/equity_fixture.py` |
| 절단본 e2e | 9테이블 · 필드 3 | **16테이블 · 필드 24**, 필드별 1셀 손검산(가격 6 · 재무 3 + PIT 역전 1 · 배당 3 · 컨센서스 3 + 의견 3 · 임원지분 1 · 자사주 부재 1) + **PIT 전수**(두 창 × 24필드, `available_date ≤ as_of` 위반 0). MVP-B 백테스트는 `70e398c` 어댑터와 **바이트 동일**(§10 P40) | `database/tests/test_equity_s21_workbench.py` 13 pass |
| EG-C ⑥~⑨ | "본판(S19·S20 뒤)에서" | **여전히 미실행**. ⑥⑦(재무 as-of)·⑧(수급 격자)·⑨(컨센서스 obs_month look-ahead)는 `contract.py` 의 EGC 항이고 S21 본판은 어댑터 층만 넓혔다 — ⑧ 은 S08~S10 병합이 선행이다 | `src/equity/contract.py` |

**S21-3 — 격자 3테이블 연결, 어댑터 필드 24 → 30 (2026-09-06, `equity_duckdb/_specs.py`·`_adapter.py` + `EQUITY_FIELD_MAP.md` §2·§3)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| 어댑터 필드 수 | S21 본판 24(미병합 7 제외) | **30** = FIELD_MAP §2 의 **29** + 내부 스코프 `price.adj_close`. 더한 6 은 `flow.foreign_net_buy`·`flow.institution_net_buy`·`flow.retail_net_buy`·`short.short_sale_value`·`short.borrowed_quantity`·`credit.margin_balance` 이고 **선언표(`SOURCE_SPECS` +4 · `FIELD_SPECS` +6)에 행만 더했다** — 해석기에 field_id 분기는 여전히 없다. 남는 **13** 의 사유는 둘(원천·컬럼 부재 11 · 굽지 않기로 한 2)로 줄었다 | `_specs.UNSUPPORTED_FIELDS` · `tests/test_adapters_equity_duckdb.py::test_field_specs_cover_every_field_map_id_exactly_once` |
| FIELD_MAP §2 행 수 | 42 field_id | **47 로 부풀어 있었다** — `equity/s08-s10` 병합이 short 3행·credit 2행을 옛 판본과 새 판본 양쪽으로 남겼고 옛 행은 실재하지 않는 컬럼(`short_daily.short_value_krw`·`short_volume_shr`·`lending_balance_kiwoom_raw` 단독)을 가리켰다. **옛 5행을 지워 42 를 회복**했고(집합 불변 → `check_field_map.py` 판정 불변) §3 집계도 실제 판정으로 재계수했다(지원 14 · 부분 16 · 미지원 10 · 미확인 1 · 부분/미지원 1) | 표를 한 행씩 재계수 · `check_field_map.py` missing 0 |
| `short.*` 원천 선택 | "두 원천 중 어댑터가 고른다"(S09) | **키움 고정**(`short_value_kiwoom_krw` + `fill_kind_short_kiwoom`). KRX 정본이 없는 축이고 커버가 넓다(절단본 measured 키움 18,265 vs KIS 3,891). **폴백 병합 금지** — 키움이 없는 날 KIS 로 갈아타면 시계열이 원천을 섞는다. 대차(`short.borrowed_quantity`)는 KIS 축뿐이라 선택이 자동이고 `fill_kind_loan_kis` 를 읽는다. 원천마다 `fill_kind` 가 따로라 **읽는 자리(SourceSpec)도 원천마다 하나**다(`short_kiwoom`·`lending_kis` 둘이 같은 relation 을 본다) | DESIGN §4-3 「합치지 않는다」 · FIELD_MAP §2 |
| `flow_daily` 의 `src` | (미기재 — 빌더는 "조용히 하나를 고르지 않는다") | 포트 grain 은 (security, session, field) 하나뿐이라 어댑터는 고를 수밖에 없다. **GRID 모드에 `pick_order` 를 더해** (ticker, date) 당 1행을 결정적으로 고른다 — 키움 우선 · 동률 `src` 사전순. 값을 섞지 않으며(고른 한 행의 값이 그대로 나간다) 겹치는 셀은 절단본 0, 서버는 `EG3_flow_daily.n_src_overlap` 이 매 빌드 센다. 고르지 않으면 LEFT JOIN 이 격자 행을 불려 어댑터가 "duplicate (ticker, date)" 로 죽는다 | `sql/flow_daily.sql` 행 규칙 ① · DESIGN §11 ⑬ |
| `fill_kind` → `CellKind` | FIELD_MAP §1 「결측 어휘」 4대응 | **3대응만 성립한다** — `measured`→OBSERVED · `not_collected`→NOT_COLLECTED · `empty_response`→MISSING · **`src_omitted`→MISSING**(SOURCE_OMITTED_ZERO 아님). 워크벤치 도메인이 `SOURCE_OMITTED_ZERO` 셀에 값을 요구하는데(`RawFieldValue.__post_init__`) equity 는 그 자리를 NULL 로 둔다(결정 8) — 도메인 무수정을 택해 라벨을 접고 사유를 필드 프로필 `description` 에 문장으로 실었다. 값이 있는 셀은 `fill_kind` 와 무관하게 OBSERVED 다(계약: 관측 셀은 값을 갖는다) | DESIGN §11 ⑪ · FIELD_MAP §3 |
| `supported_cell_kinds` | `(OBSERVED, MISSING)` 고정 | **선언에서 나온다** — `SourceSpec.kind_expr` 이 있으면 `(OBSERVED, MISSING, NOT_COLLECTED)`, 없으면 `(OBSERVED, MISSING)`. `COVERAGE_GAP` 은 어느 원천도 내지 않는다(구간·백필 밖은 셀 자체가 없다) | `_adapter._cell_kinds` |
| 랙 | "S19 전에는 노출하지 않는다"(FIELD_MAP §3 S09 주석) | **랙 0 으로 노출하되 가정임을 카탈로그에 싣는다.** stage 6원천이 전부 `lag_known=false` 이고 **KIS 신용잔고는 실제 T+1 공표**라 랙 0 은 하루치 look-ahead 다 — 근거 문장을 프로필 `available_date_basis` 에 넣고 소비자가 `lag_overrides` 로 물리게 했다. 노출을 미루면 소비층이 필드가 왜 없는지도 못 본다. `recommended_lag_sessions` 정본은 여전히 S19 | DESIGN §11 ⑫ |
| 손 픽스처 | `build_workbench_root` 15테이블 | **18테이블**(+ `flow_daily`·`short_daily`·`credit_daily`). 한 창 안에서 셀 종류를 다 낸다 — 겹친 셀의 원천 선택(kiwoom vs kis 9,999) · 진짜 0(OBSERVED) · `src_omitted`(MISSING) · `not_collected`(NOT_COLLECTED) · `empty_response`(MISSING) · 음수 대차잔고 · 원장 행 없는 세션(셀 부재) | `backend/tests/equity_fixture.py` |
| 절단본 e2e | 16테이블 · 필드 24 | **19테이블 · 필드 30**. 격자 손검산 3건(005930 2018-05-04 — 외국인 순매수 −53,845,000,000원 · 공매도 거래대금 103,425,481,000원 · 신용융자 잔고 8,359,855주, 기대값 출처는 stage 원장) + `src_omitted`→MISSING 1건(2026-08-20 신용) + **PIT 전수**(두 창 × 30필드 위반 0) | `database/tests/test_equity_s21_workbench.py` |
| 계약 테스트 `FIELDS` | 29 | **33** — equity 30 + mock 전용 3(`short.short_balance_ratio`·`event.earnings_surprise`·`classification.sector`). 창 독립·PIT·두 포트 kind 일치 절이 격자 6필드 위에서도 돈다 | `backend/tests/contract/test_raw_observation_port.py` |
| EG-C ⑧(수급 격자) | "S08~S10 병합이 선행" | **여전히 미실행**. 선행 조건은 사라졌지만 `contract.py` 의 EGC 항 자체가 아직 없다 — S19·S20 뒤 몫이다 | `src/equity/contract.py` |

**S19-2 — 격자 3테이블 `field_profiles` 선언, `dataset_profile` 66 → 72행 (2026-09-06, `rules_s08/s09/s10.py`·`rules_s19.py`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| `dataset_profile` 입력 | 「전 equity 테이블 22」(`SOURCE_TABLES`) | **25** — `flow_daily`·`short_daily`·`credit_daily` 를 더했다. 22 는 격자 3테이블이 아직 없던 때의 수이고, 목록에서 빠져 있으면 `rules_s19.owned_fields()` 가 그 테이블의 선언을 아예 훑지 않는다(테이블이 커밋돼 있어도 프로파일 행이 안 생긴다) | `rules_s19.SOURCE_TABLES` · P39′ 서버 실측이 지목한 원인 |
| S08~S10 `field_profiles` | 선언 없음(빈 튜플) | **6 필드 선언** — `flow.foreign_net_buy`·`flow.institution_net_buy`·`flow.retail_net_buy`·`short.short_sale_value`·`short.borrowed_quantity`·`credit.margin_balance`. 대상은 어댑터 `_specs.FIELD_SPECS` 가 **실제로 내는** 6과 정확히 같다 | FIELD_MAP §2 · `equity_duckdb/_specs.py` |
| 선언하지 않는 격자 필드 | (미기재) | `flow.foreign_ownership`·`flow.foreign_limit_exhaustion`(원천 `stg_foreign_daily` 가 S08 입력에 없다 — S08-2) · `flow.pension_net_buy`(FIELD_MAP §2 어휘에 field_id 가 없다) · `short.short_balance_ratio`(공매도량 ÷ 상장주식수 — 비율 계산이 팩터층 몫) · `credit.net_buy`(원천에 축 부재, S10 판정) · KIS 공매도 축·`*_amt`·대주 잔고(field_id 없음/단위 미상). **선언하면 `dataset_profile` 에 '있는데 늘 빈' 행이 생겨 EG10 이 거짓으로 ready 를 낸다** | DESIGN §4-3 · `test_격자_3테이블은_어댑터가_내는_6필드만_선언한다` |
| 격자 필드 랙 | S21-3 이 어댑터 상수로 **0**(자기 `lag_basis` 가 "가정이고 확정은 `dataset_profile` 몫" 이라 적었다) | **1 세션**(`recommended_lag_days` 도 1). 세 슬라이스의 stage 원장 6종이 전부 `lag_known=false` 이고 신용잔고는 실제 T+1 공표라 0 은 하루치 look-ahead 다 | STAGE_HANDOFF §2 · FIELD_MAP §1 「나머지 전부 1 세션」 · `rules_s10` docstring |
| `supported_cell_kinds` 유도 | `FILL_KIND_COLUMN in owner.columns` (이름 정확 일치) | **접두 판별** — `short_daily` 는 원천마다 fill_kind 를 두어 컬럼 이름이 `fill_kind_short_kiwoom`·`fill_kind_short_kis`·`fill_kind_loan_kis` 다. 정확 일치만 보면 그 테이블의 두 필드가 격자가 아닌 것으로 잡혀 3종만 받고, 소비자가 `not_collected` 셀을 만나고도 어휘에서 못 찾는다. **격자 3테이블은 FIELD_MAP §1 결측 어휘 5종 전부**를 선언한다(어댑터가 `src_omitted`→MISSING 으로 좁히고 COVERAGE_GAP 을 내지 않는 것은 **포트 층의 좁힘**이고 위 S21-3 행이 기록한다 — equity 선언은 표가 나를 수 있는 어휘 전부다) | `rules_s19.supported_cell_kinds` · DESIGN §4-3 구현 결과 ② |
| EG2-P04 부정 하네스 | `stg_flow_daily_kiwoom` 을 「어떤 필드도 안 싣는 lag_known=false 원천」의 예로 썼다 | **`stg_foreign_daily` 로 옮겼다** — S19-2 가 `flow.*` 3필드를 랙 1 로 선언해 수급 원장이 덮이므로 옛 예는 이제 PASS 다(그 사실 자체를 같은 테스트가 단언한다). 술어가 항진이 되지 않게 실제로 안 덮이는 원천으로 바꾼다 | `test_어떤_필드도_싣지_않은_lag_known_false_원천은_EG2를_폐기한다` |
| 골든 픽스처 | `dataset_profile` 17 · `factor_readiness` 10 | `dataset_profile` **27**(+ FX-6-020~023: 격자 필드의 `column_scope`·랙·`supported_cell_kinds`·`coverage_basis`·`unit`·`requires_confirmation`) · `factor_readiness` 10(**FX-6-011 의 키를 F01 → F02 로**). F01 은 이제 ready 이고, `field_unavailable` 의 예로는 재료가 정말 없는 F02(`flow.foreign_ownership`)가 맞다. 모집단 의존 값은 여전히 픽스처에 넣지 않는다(P38′ 교훈) — F03 의 `partial_support` 는 커버율 > 0 이어야 성립하므로 절단본 손계산 테스트가 맡는다 | `src/equity/fixtures/*.json` |
| EG10 결과 | 31 ready / 23 blocked | **35 ready / 19 blocked** — F01·F06·F07·F09 가 열리고 F03 이 `field_unavailable` → `partial_support`(GAP-03) 로 옮겨 갔다. 사유 분포 `field_unavailable` 10 → **5** · `partial_support` 11 → **12** · `no_observations` 2(불변). **`ready_min` 은 여전히 미등재** — 서버 실측 뒤 사람이 정한다(§4-8 · P39′) | DESIGN §10 P41 |


**S23 — 전방 조정가 표 `price_adj_daily` 신설 (2026-09-06, `rules_s23.py`·`sql/price_adj_daily.sql`·`views._FWD_CTE`)**

| 항목 | 초안 | 정정 | 근거 |
|---|---|---|---|
| 조정가의 자리 | 카탈로그 매크로 `v_adj_price_fwd` 하나(S21 후속) | **표 `price_adj_daily`(28번째 테이블) + 매크로**. 매크로만 있으면 parquet 을 직접 읽는 소비자(커널 pyarrow 어댑터·노트북)가 못 보고, 카탈로그가 낡거나 없으면 `price.adj_close` 가 통째로 unavailable 이 된다. 전방 조정(결정 09-05)으로 값이 (ticker, date) 의 순수 함수가 된 뒤라야 저장할 수 있다 | DESIGN §4-2 · §11 ① |
| 누적 범위 | 티커 전체(`PARTITION BY ticker`) | **`(ticker, span_seq)` 안에서만**. 재상장 2종의 폐지 전 구간 계수가 새 구간 가격에 곱해지던 결함이다 — 전방 조정의 앵커는 그 구간의 첫 관측이다. 계수 쪽 구간 부여만 **엄격 부등호**(`fold_date > first_date`)라 구간 첫날 계수는 어떤 행에도 곱해지지 않고, 그래야 "구간 첫 행 누적 = 1" 이 정확히 선다. 매크로 `v_adj_price_fwd`·`v_adj_volume_fwd` 도 같은 규칙으로 고쳤다 | `views._FWD_CTE` · FX-2-017 |
| EG1 | (신규) | **항등식** `count(price_adj_daily) = count(price_daily)` — 격리 사유가 없다(EG7 항상 0). 등식이 약한 대신 값 축을 EG3 가 독립 재계산으로 전건 대조한다 | §3 ㉓ |
| EG3 독립 재계산 | (신규) | 산출은 ASOF JOIN + 창 누적곱, 게이트는 **범위 조인 + GROUP BY 집계**로 같은 값을 다시 만든다 — `sql/price_adj_daily.sql` 을 재사용하면 항진명제다. "계수의 구간 = 행의 구간" 과 "행의 앵커 < fold ≤ date" 가 같은 집합이라는 것이 두 형식을 잇는 등식이다 | `rules_s23.install_recalc` |
| `cum_price × cum_share = 1` 허용오차 | (신규) | 한 계수당 `adj_factor.factor_product_tol_base`(0.01, S06-2 기준가 원천의 KRX 산식 잔여)를 **접힌 수만큼 복리로 편** `(1+tol)^n − 1`. 접힌 계수가 0 이면 정확히 1 을 요구한다. 상수를 복제하지 않고 `adj_factor` 네임스페이스에서 읽는다 | `baseline_seed_s23.json` |
| 매크로 정합 | "게이트 또는 테스트로 증명" | **매 빌드 EG3 안에서** — `views.install_temp_macros` 로 두 fwd 매크로를 빌드 세션에 올려 as-of 표본(`asof_sample_dates` 5 × `asof_sample_tickers` 20)에서 행 집합·값을 대조한다(상대오차 1e-12). 서버 실측 **319,310행 비교, 차이 0, 최대 상대편차 0.0**(비트 동일) | `rules_s23._macro_mismatch` |
| 얇은 매크로로 합치기 | 권고 | **채택하지 않았다** — `lag_override` 가 PIT 계약의 일부이고(회귀 테스트가 "아직 공개 전인 계수를 접지 않는다" 를 단언한다) 표에는 랙 축이 없어, 매크로를 표 읽기로 바꾸면 `lag_override` 가 조용한 no-op 이 된다. 두 산출을 남기고 게이트로 묶는 쪽을 택했다 | DESIGN §5 |
| 커널 연결 | (미기재) | **금지**. 커널은 원주가 bar + `CorporateActionEvent` 로 수량을 조정하므로 조정가를 주면 이중 계산이다. `backtest_engine/adapters/equity_duckdb.py` 가 `price_adj_daily` 를 읽지 않는다는 것을 테스트가 회귀로 지킨다 | DESIGN §7 |
| 부정 픽스처 | (신규) | 절단본에는 "재상장 + 폐지 전 구간의 ok 계수" 조합이 없어(`n_rows_span_free_diff` = 0, 서버도 0) 구간 누출을 못 잡는다 → **합성 equity 트리**(2구간 재상장 1종)에서 돈다: ① 조정가를 나눗셈으로 뒤집기 → `n_recompute_mismatch`·`n_macro_mismatch` ② 구간 부여를 상수로 바꿔 누출 → `n_cross_span_factor`·`n_span_first_not_unit` ③ `n_unadjusted_events` 를 0 으로 지우기 → `n_unadjusted_mismatch` | `test_equity_s23_price_adj.py` |
| `dataset_profile` 소유 이동 | `price.adj_close` 를 `adj_factor` 가 뷰 필드(`view_name`)로 선언 | **`price_adj_daily` 가 표 컬럼으로 선언**한다(FX-6-006 `table_name` = `price_adj_daily` · `available_date_basis` = `derived`). 두 표가 같은 field_id 를 선언하면 grain 이 깨지므로 이동이지 추가가 아니다. `rules_s19.SOURCE_TABLES` 25 → **26**, 프로파일 행수 72 불변 | `rules_s19.owned_fields` |
| EG3 기록형 사건 축 | (신설) | 미조정 사건의 사유별 내역은 **`event_id` 축**으로 센다. (ticker, apply_date) 로 묶으면 같은 날 두 사건이 하나로 접혀 `n_unadjusted_events` 가 세는 축과 갈린다(서버 4,014 → 3,970 으로 44건 유실). `adj_factor.event_id`·`factor_source` 를 EG3 전용 입력 컬럼으로 선언한다(산출식은 읽지 않는다) | `rules_s23` `input_columns` |
| 조정 OHLC·거래량 노출 | (판단 대상) | **선언하지 않는다** — FIELD_MAP §2 어휘에도 FACTORS 정본 54 의 재료에도 없다(M02 는 원주가 `price.high` 를 쓴다). 표에는 컬럼으로 실려 있어 parquet 소비자는 읽을 수 있다 | FIELD_MAP §2 |
