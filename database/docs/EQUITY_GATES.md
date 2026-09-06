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
| `declaration_table` | 행수 등식이 정의되지 않는 선언표(`dataset_profile`·`universe_policy`) |
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
- **적용** 24 테이블 (`dataset_profile`·`universe_policy` 는 `skip(declaration_table)`)
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
| EG2-P04 | `lag_known=false` 인 stage 원천 → `dataset_profile` 행 ∧ `recommended_lag_days ≥ 1` | `n = 0` |
| EG2-P05 | 파생 컬럼 `available` = 구성 행 `available` 의 max | `n = 0` |
| EG2-P06 | `basis='default'` 인 profile 행에 `evidence` 필수 | `n = 0` |
| EG2-P07 | `coverage_from` 전수 (profile 전 행 NOT NULL) | `n = 0` |

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
      AND p.recommended_lag_days >= 1);

-- EG2-P05 : 파생 컬럼 동반 available. `<col>_available_date` 규약 (§5-C4 참조)
SELECT count(*) AS n FROM fin_std f
WHERE f.derived_n_rows IS NOT NULL
  AND f.q4_derived_available_date IS DISTINCT FROM (
    SELECT max(g.available_date) FROM fin_std g
    WHERE g.corp_code = f.corp_code AND g.fs_div = f.fs_div
      AND g.period_end IN (f.period_end, f.period_end - INTERVAL 3 MONTH,
                           f.period_end - INTERVAL 6 MONTH, f.period_end - INTERVAL 9 MONTH));

-- EG2-P06 / EG2-P07
SELECT count(*) FILTER (WHERE basis = 'default' AND coalesce(trim(evidence), '') = '')
     + count(*) FILTER (WHERE coverage_from IS NULL) AS n
FROM dataset_profile;
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

-- EG6-P02
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
| `fin_std.rcept_lag_p99_days` | EG7-P04 | 4 | `rcept_dt − period_end` p99 |
| `fin_std.period_end_lag_max_days` | `period_end` 후보 선택 | 4 | 후보 판정 상한 |
| `fin_std.asof_value_change_max` | EG5c-P02 | 4 | 과거값 변경 허용 건수 |
| `v_fin_latest.asof_sample_dates`·`asof_sample_tickers` | EG5c-P01 | 4 | as-of 표본 |
| `disclosure_version.misjudge_rate_max` | EG6-P07 | 4 | 그룹 오판율 상한 |
| `disclosure_version.nonmatch_rate_gap_max` | EG6-P08 | 4 | 비대칭 상한 |
| `correction_link.link_rate_min` | EG6-P05 (E-G6a) | 4 | 링크 성립률 |
| `correction_link.date_exact_rate` | EG6-P06 (E-G6b) | 4 | 기록형 |
| `correction_link.reach_rate_min` | EG8-P09 (E-G7) | 4 | 도달률 |
| `consensus_daily.v3_wise_match_min` | EG8-P07 | 5 | 겹침 일치율 |
| `consensus_daily.cover_ratio_drop_max`·`cover_ratio_rise_max` | EG9-P05 | 5 | 급락·급증 |
| `consensus_daily.obs_month_bias_min` | EG-C ⑨ | 5 | 편의 하한 |
| `<factor>.hand_calc_tol` | EG-C ⑥ | 7 | 손계산 허용오차 |
| `<table>.material_coverage_min` | EG10 (§6) | 6 | 팩터 재료 커버율 |
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
| 15 | `fin_std` | 4 | ● | ●(§3-⑫) | ●(P01–P05) | ●(P01,P13) | ●(FX-4-001…008) | ●(a,c) | ●(P07,P08) | ●(P04) | skip(no_baseline)→D9 승격 시 ● | skip(not_grid) | ●⑥⑦ |
| 16 | `disclosure_version` | 4 | ● | ●(§3-⑬) | ●(P01–P04) | ●(P01) | ●(FX-4-004,005) | ●(a) | ●(P07,P08) | ●(P05) | — | skip(not_grid) | ●⑦ |
| 17 | `correction_link` (문서층 §8.1) | 4 | ● | ●(§3-⑭) | ●(P01–P03) | ●(P01,P13) | ●(FX-4-009,010) | ●(a) | ●(P05,P06) | ● | ●(P09) | skip(not_grid) | — |
| 18 | `holder_daily` | 4B | ● | ●(§3-⑮) | ●(P01–P04) | ●(P01,P07) | ●(FX-4B-003) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 19 | `ownership_snapshot` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-005) | ●(a) | skip(no_multi_version) | ●(P07) | — | skip(not_grid) | — |
| 20 | `shares_outstanding` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-001) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 21 | `treasury_stock` | 4B | ● | ●(§3-⑯) | ●(P01–P04) | ●(P01) | ●(FX-4B-001) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 22 | `audit_opinion` | 4B | ● | ●(§3-⑰) | ●(P01–P04) | ●(P01,P13) | ●(FX-4B-002) | ●(a) | skip(no_multi_version) | ● | — | skip(not_grid) | — |
| 23 | `dividend_event` | 4B | ● | ●(§3-⑱) | ●(P01–P04) | ●(P01) | ●(FX-4B-004) | ●(a) | skip(no_multi_version) | ●(P07) | — | skip(not_grid) | — |
| 24 | `consensus_daily` | 5 | ● | ●(§3-⑲) | ●(P01–P04) | ●(P01,P07) | ●(FX-5-001…006) | ●(a,c) | ●(P01,P02,P03) | ●(P07) | ●(P07) | ●(P05,P06) | ●⑥⑨ |
| 25 | `opinion_daily` | 5 | ● | ●(§3-⑳) | ●(P01–P04) | ●(P01,P07) | ●(FX-5-007) | ●(a) | ●(P01,P03) | ● | skip(no_baseline) | ●(P06) | — |
| 26 | `opinion_broker_daily` | 5 | ● | ●(§3-㉑) | ●(P01–P04) | ●(P01,P07) | ●(FX-5-008) | ●(a) | skip(no_multi_version) | ● | — | ●(P06) | — |
| 27 | `dataset_profile` | 6 | ● | skip(declaration_table) | ●(P04,P06,P07) | ●(P01) | ●(FX-6-001,002) | ●(a) | skip(no_multi_version) | ● | — | ●(P06 기록형) | ●⑥⑧ |

**뷰 7종**(`v_universe`·`v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_fin_latest`·`v_consensus`·`v_firm_mktcap`)은 테이블이 아니므로 EG0~EG9 매트릭스에 행이 없고, **EG5c·EG-C·EG11(§6)** 이 담당한다. 이것이 현재 명세의 가장 큰 공백이다(§5-A6).

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
           FROM stg_dividend WHERE row_kind IS DISTINCT FROM 'aggregate'))
        - eg1_tail('dividend_event')) AS delta;
--   주의: stg_dividend 에는 row_kind 가 없다(rules_dart.py:136 은 hyslr·shares·tesstk 3개만).
--   IS DISTINCT FROM 은 NULL 을 통과시키므로 안전하나, WORKFLOW §2-1 의 "비집계" 표현은
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

### ㉒ `dataset_profile` · `universe_policy`
```sql
-- 행수 등식 없음 → skip(declaration_table). EG2-P04/P06/P07 이 커버 조건으로 대신한다
```

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

| ID | 테이블 | 키 | 기대 컬럼 | 기대값 출처 | 검증 술어 요지 |
|---|---|---|---|---|---|
| FX-6-001 | `dataset_profile` | (`price_daily`, `ohlcv`) | `basis`, `recommended_lag_days` | doc:DESIGN §4-7 | `convention`, 0 |
| FX-6-002 | `dataset_profile` | (`flow_daily`, `kiwoom`) | `recommended_lag_days`, `coverage_from` | doc:DESIGN §4-7 | ≥ 1 · 격자 하한 |

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

### EG10 — 팩터 재료 커버율
- **분류** 폐기형(선언 컬럼 실재) + 기록형(커버율) → baseline 승인 후 폐기형
- **적용** `_reg_factor_material` 이 가리키는 전 테이블. 6단계
- **왜** FACTORS 54개 중 하나의 재료 컬럼이 조용히 NULL 로 채워져도 현재 어떤 게이트도 실패하지 않는다. V05·V07·Q07·Q08 은 계정 3개(`depreciation`·`borrowings`·`interest_expense`)의 실재 여부에 매달려 있다(DESIGN §6)
- **상수** `<factor_id>.material_coverage_min`

```sql
-- (a) 선언 재료 컬럼 실재 — 폐기형
SELECT count(*) AS n FROM _reg_factor_material m
WHERE NOT EXISTS (SELECT 1 FROM duckdb_columns() c
                  WHERE c.table_name = m."table" AND c.column_name = m."column");
-- (b) 커버율 — coverage_from 이후 유니버스 대비 NOT NULL 비율
SELECT m.factor_id, m."table", m."column",
       (SELECT count(*) FILTER (WHERE t.${COL} IS NOT NULL)::DOUBLE / nullif(count(*), 0)
        FROM query_table(m."table") t
        WHERE t.available_date >= m.coverage_from) AS cover
FROM _reg_factor_material m;
-- 통과: cover >= bl(m.factor_id, 'material_coverage_min')
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
| EG10 | 팩터 재료 커버율 | 폐기형+기록형 | 6 | `<factor>.material_coverage_min` | (신규) 재료 무성 소실 |
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
(7단계 전역) EG10 재료 커버율 · EG11 뷰 결정성 · EG19 as-of 단조성 · EG5c · EG-C ①~⑩
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
