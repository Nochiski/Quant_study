# findings C — stage 층 일일 증분 설계용 사실 수집

조사일 2026-09-09 · 워크트리 `/Users/claudeoscarmonet/orca/workspaces/Quant_study/데이터베이스` (main, `b9f2aaf`) · 서버 `kael-server:~/quant-ledger` 읽기 전용.
표기: **실측** = 서버 파일/DB/로그에서 직접 잰 값, **추정** = 코드·문서에서 유추. 저장소·서버 파일 무변경.

---

## 0. 한 줄 결론 (먼저)

stage 는 **증분 경로가 코드에 전혀 없다**. 빌드 입자는 "테이블 전체"이고, 파티션은 *출력* 디렉터리 레이아웃일 뿐 *입력* 필터가 아니다. 다만 **매일 실제로 자라는 원장은 2개(`kiwoom.db` 의 마스터 1테이블 · `wisereport.db` 전부)뿐**이라, 영향 테이블은 66 중 **10개**이고 그 10개의 빌드 합계는 **실측 43.6초**다. "일일 전체 재빌드"(1,348초 = 22.5분)를 살 것인지, "영향 테이블만"(44초)을 살 것인지가 최초 분기다.

---

## 1. 스냅샷 모델

### 1.1 어떻게 고정하는가 — 복사(VACUUM INTO)

`database/src/stage/snapshot.py:41-45` 가 전부다.

```python
con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
con.execute(f"VACUUM INTO '{...dst...}'")
```

- 원장은 **읽기 전용 URI** 로만 열고 `VACUUM INTO` 로 **물리 사본**을 뜬다 (attach 읽기가 아니다). 원장 무변경.
- 대상 DB 는 "그 테이블 규칙이 필요로 하는 것만": `database/src/stage/__main__.py:34-37` — `{s.db for s in rule.sources} | {rule.cross_check.db}`. 그래서 `stg_price_daily` 한 장을 빌드하면 krx+kiwoom 2개만 뜬다(교차 게이트 G9 가 kiwoom 을 요구).
- snapshot-id 형식: `snap_%Y%m%dT%H%M%SZ` (UTC) — `snapshot.py:33`. 디렉터리는 `mkdir(exist_ok=False)` 라 같은 초에 두 번 뜨면 예외.
- 산출: `data/snapshots/<sid>/<db alias>.db` + `snapshot.json`(files 별 `path`·`bytes`·`src_mtime`). alias 는 `rules.LEDGER_FILES` 키 — **wisereport.db → `wise.db` 로 이름이 바뀐다**(`rules.py:20-21`).
- 빌드는 이 사본을 `ATTACH ... (TYPE sqlite, READ_ONLY)` 로 붙인다 (`build.py:323`).

### 1.2 비용 · 용량 (실측)

| 항목 | 값 | 출처 |
|---|---|---|
| 5 DB 1세트 용량 | **17GB** (`snap_20260902T154207Z`) | 서버 `du -sh` 실측 |
| ├ dart.db | 7.07GB | 〃 |
| ├ krx.db | 4.01GB | 〃 |
| ├ kis.db | 3.26GB | 〃 |
| ├ kiwoom.db | 3.26GB | 〃 |
| └ wise.db | 0.10GB | 〃 |
| 5 DB 1세트 생성 시간 | **약 3.8분** (snapshot_id 15:42:07 → `snapshot.json` mtime 15:46) | 서버 mtime 실측 |
| krx + kiwoom (7.27GB) | **61초** | STAGE_DESIGN.md:380 실측 |
| dart 단독 (7.07GB) | **약 80초** | STAGE_DESIGN.md:394 실측 |
| kiwoom + wise 만 (일일 후보, 3.95GB) | **약 35초** | 위 두 실측의 GB 비례 — **추정** |
| `data/snapshots/` 현재 총량 | **37GB / 7세트** | 서버 `du` 실측 |

현재 남아 있는 7세트(실측):

| snapshot_id | 담은 DB | 크기 |
|---|---|---|
| snap_20260902T084316Z | kiwoom·krx | 6.8G |
| snap_20260902T112544Z | dart | 6.6G |
| snap_20260902T140317Z | wise | 100M |
| snap_20260902T141029Z | wise | 100M |
| snap_20260902T154207Z | **5 DB 전부** (풀 빌드 정본) | 17G |
| snap_20260902T230100Z | dart (문서층·DART 재빌드용) | 6.6G |
| snap_20260903T015712Z | wise (analyst_broker 용) | 134M |

### 1.3 보관·정리 정책 — **없다** (결함)

- `snapshot.py` 에 GC/retention 코드 없음. `run_stage.sh`·`run_stage_all.sh` 에도 스냅샷 삭제 없음 (grep 확인).
- 설계 문서의 GC 는 전부 **stage 출력** 쪽 얘기다(`MANIFEST keep=3`, STAGE_DESIGN.md:77).
- 결과: 7세트 37GB 가 09-02~03 이후 그대로 남아 있다. 디스크 여유는 **271GB** (실측 `df -h`) 라 아직 문제는 아니지만, **일일 풀 스냅샷을 돌리면 하루 17GB × N일** 이 그대로 쌓인다 → 16일이면 271GB 소진. 일일 플랜에 스냅샷 보관 규칙이 반드시 들어가야 한다.

### 1.4 실행 창·락

- 설계(STAGE_DESIGN.md:72): "스냅샷·WAL 전환의 실행 창 = **06:30~익일 05:30 KST**(daily_wise 무동작), 빌드와 크론이 같은 락 파일 `/tmp/quant_ledger_raw.lock`".
- 설계(STAGE_DESIGN.md:77): "동시 실행 금지 `flock -n`".
- **코드 현실**: `run_stage.sh`·`run_stage_all.sh` 어디에도 `flock` 이 없다. `flock` 을 실제로 쓰는 건 `scripts/run_equity.sh:10-11`(`/tmp/quant_ledger_equity.lock`)뿐. `/tmp/quant_ledger_raw.lock` 은 코드 어디에도 없다(grep 0건). **일일 크론화 시 신설 필요.**
- 실제 크론(서버 `crontab -l` 실측): quant-ledger 항목은 **`0 21 * * * daily_wise.sh` 단 하나**(= KST 06:00). stage 크론 없음. `daily_dart.sh` 는 09-02 결정으로 제거됨(STAGE_DESIGN.md:467 ⑨).

---

## 2. 빌드 단위 — 항상 전체 재생성

### 2.1 입력에 증분 필터가 없다

`build.py` 흐름(`build_table`, `build.py:420-575`):

1. `_union_sql` (`build.py:138-152`) — 소스 테이블을 통째로 `SELECT ... FROM db.table` UNION ALL. **WHERE 절 없음**. 날짜/워터마크 인자 자체가 함수 시그니처에 없다.
2. `CREATE TEMP TABLE stage_all AS <_stage_sql(...)>` (`build.py:463-465`) — 전 행 캐스팅·중복 판정(`row_number() OVER (PARTITION BY nk, payload_hash, reject_reason)`, `build.py:271-273`). **dedup·G1 등식이 전역 윈도**라 부분 입력으로는 성립하지 않는다.
3. 쓰기 (`build.py:487-497`):
   ```python
   COPY (SELECT * FROM stage_ok ORDER BY {nk}) TO '{tmp_table}'
        (FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part')
   ```
   — 매번 **빈 tmp 디렉터리**(`_tmp/<build_id>/<table>`, `build.py:428-432`)에 전 연도를 새로 쓴다.
4. `shutil.move(tmp_table, table_root/"v=<build_id>")` (`build.py:568`) → `manifest.commit` (`build.py:570-573`) → keep=3 밖 구버전 `rmtree` (`manifest.py:64-70`).

즉 **파티션은 출력 레이아웃일 뿐** 입력 축이 아니다. `year=YYYY` 디렉터리에 하루치를 append 하는 코드 경로는 존재하지 않는다.

### 2.2 파티션 축과 write_mode (66테이블 전수, 코드에서 추출)

| partition_class | 테이블 수 | 예 |
|---|---:|---|
| `date_axis` | 24 | price·etf·index·listing / kiwoom 5 / kis 4 / wise blob 7 · v3 4 |
| `receipt_axis` | 30 | stg_fin · 보조원장 6 · disclosure · doc_index · event 15 · doc_* 4 |
| `whole` | 12 | rcept_dt_map · corp_map · company · delisted_master · wise_coverage · shards · ingest_krx · calls/units ×5 |

`write_mode` (G6 게이트 실행 여부를 가른다): `append_only` 49 · `upsert` 11 · `first_write_wins` 6.

### 2.3 "매일 하루치가 추가됐을 때" 최소 비용 경로 — 코드에 없다. 무엇을 추가해야 하나

| 안 | 무엇을 하나 | 하루 비용(실측 기반) | 정합성 위험 |
|---|---|---|---|
| **A. 전체 재빌드** | 5 DB 스냅샷 + `run_stage_all.sh` 66테이블 | 스냅샷 3.8분 + 빌드 **22.5분**(=1,348초) + 디스크 스냅샷 17GB·산출 3.0GB | 없음(현행 검증 그대로). 단 문서층 4테이블은 프리패스 3~4.5h 가 앞에 붙는다(§5) |
| **B. 영향 테이블만 재빌드** ← 권장 | 자란 원장만 스냅샷(kiwoom+wise) + 그 원장을 소스로 하는 stage 테이블만 | 스냅샷 ~35초(추정) + 빌드 **43.6초**(실측 합) | 게이트 전부 그대로 돈다. **스냅샷 혼재**가 생김(§2.4) |
| **C. 파티션 append 증분 빌더 신설** | 새 코드 | 이론상 초 단위 | G1(전역 행수 등식)·dedup 윈도·G5(Δ등식)·content_hash 재현성이 전부 전역 전제라 **게이트 체계를 다시 설계해야 한다**. 설계문서도 "일일 증분은 별도 설계"로 미뤄 둔 상태 |

**B 의 대상 10테이블 (일일 자라는 원장 = kiwoom.ka10099_stock_master + wise 전부)**:

| 테이블 | 소스 | 09-02 풀빌드 elapsed | 09-02 stage 행 | 현재 원장 행(실측 09-09) |
|---|---|---:|---:|---|
| stg_master_daily | kiwoom.ka10099_stock_master | 0.3s | 8,614 | **38,771** (snap_date 20260901~20260909, 9일) |
| stg_consensus_monthly | wise.ws_raw (cF5001·cF5002) | 6.2s | 222,499 | cF5001 37,562 + cF5002 21,720 |
| stg_consensus_annual | wise.ws_raw (c1050001_data) | 0.8s | 11,270 | c1050001_data 59,282 |
| stg_consensus_quarterly | 〃 | 0.8s | 11,284 | 〃 |
| stg_consensus_matrix | 〃 | 4.4s | 214,650 | 〃 |
| stg_analyst_summary | wise.ws_raw (c1010001) | 0.8s | 1,612 | c1010001 7,238 |
| stg_analyst_broker | wise.ws_raw (c1010001) | (풀빌드 미포함) | 9,717 | 〃 |
| stg_fin_wise | wise.ws_raw (cF3002·cF4002) | 27.1s | 445,294 | 7,240 + 7,240 |
| stg_wise_coverage | wise.ws_coverage | 0.1s | 2,566 | 2,566 |
| stg_calls_wise | wise.ws_call_log | 0.3s | 31,442 | **140,480** |
| **합** | | **43.6s** (broker 제외) | | |

주의: WISE 수집은 09-01 개시라 원장이 **9일치**뿐이다(ws_raw min fetched_date `2026-09-01`, 실측). 일일 증가분은 ws_raw **약 15.6k행/일**, kiwoom 마스터 **약 4.3k행/일**(실측 나눗셈). 즉 3개월 뒤에도 ws_raw 는 140만 행 수준 → **B 안의 43.6초는 앞으로도 분 단위를 넘지 않는다(추정)**.

### 2.4 B 안이 만드는 문제 — 스냅샷 혼재는 이미 실재한다

서버 66 MANIFEST 를 훑은 실측: **현재 stage 는 한 스냅샷이 아니다.**

| snapshot_id | 테이블 수 | 어떤 것 |
|---|---:|---|
| snap_20260902T154207Z | 54 | 풀 빌드 본체 |
| snap_20260902T230100Z | 11 | doc_* 4 + 09-05 재빌드한 DART 6(audit·capital·dividend·hyslr·shares·tesstk) + doc_index |
| snap_20260903T015712Z | 1 | stg_analyst_broker |

특히 `stg_dividend`·`stg_shares` 등은 `available` 을 **`stg_rcept_dt_map` 룩업**으로 채우는데(`build.py:352-360`, `_current_build_glob`), 그 참조표는 `snap_...154207Z` 판이다. 참조표는 "키당 값 불변"이라 설계상 무해하다고 선언돼 있지만(STAGE_DESIGN.md §1 예외 (d)), **일일 운영에서는 "어느 스냅샷을 정본으로 볼 것인가"가 매일 갈라진다.** 일일 플랜은 `MANIFEST.snapshot_id` 혼재를 허용할지 명시해야 한다.

---

## 3. 의존 순서와 소요 시간

### 3.1 ORDER 구성 (`scripts/run_stage_all.sh:15-32`)

ORDER 배열은 62테이블, 여기에 프리패스 캐시가 있으면 doc 4테이블이 뒤에 붙어 **66**. `RULES` 레지스트리도 66 (코드에서 확인). 서버 `data/stage/` 에도 66 테이블 디렉터리가 있고 **전부 `current_build` 를 갖고 있다**(실측).

순서의 **유일한 진짜 제약**은 `stg_rcept_dt_map` 이 맨 앞이라는 것이다 — DART 22테이블이 `AvailableRule(kind="lookup", table="stg_rcept_dt_map")` 로 그 테이블의 **커밋된 parquet** 를 읽는다(`build.py:355`, 없으면 `FileNotFoundError`). 나머지 61테이블은 서로 독립이라 **병렬화 가능**(추정 — 단 duckdb memory_limit 6GB × N 과 spill 경합 주의. `stg_fin` 단독으로 spill 15~18GB 실측, STAGE_DESIGN.md:397).

### 3.2 소스별 묶음 + 소요 (실측, `logs/stage_all/summary.tsv` 09-02 16:23→16:46)

| 그룹 | 테이블 수 | elapsed 합(s) | 행 합 |
|---|---:|---:|---:|
| 참조표 (stg_rcept_dt_map) | 1 | 8.4 | 3,443,898 |
| KRX | 5 | 130.5 | 20,470,961 |
| 키움 | 6 | 208.3 | 26,274,142 |
| KIS | 7 | 238.5 | 11,547,190 |
| DART 재무·보조원장 | 9 | 687.3 | 16,822,453 |
| DART 공시·회사·인덱스·로그 | 6 | 25.2 | 7,795,588 |
| DART 이벤트 15종 | 15 | 6.4 | 20,822 |
| WISE (v3 4종 포함) | 12 | 43.3 | 1,257,563 |
| **합계** | **61** | **1,347.9 (22.5분)** | **84,296,127** |

벽시계는 22분 37초(로그 mtime 16:23:26→16:46:03, 실측) — 즉 오버헤드는 사실상 없다.
`stg_analyst_broker` 는 이 summary 에 없다(09-03 별도 빌드). 문서층 4테이블도 없다(§5).

### 3.3 상위 10 (실측)

| # | 테이블 | elapsed(s) | 행 | 비중 |
|---:|---|---:|---:|---:|
| 1 | stg_fin | **666.4** | 15,375,024 | 49.4% |
| 2 | stg_credit_daily | 181.2 | 8,404,204 | 13.4% |
| 3 | stg_flow_daily_kiwoom | 106.0 | 7,621,338 | 7.9% |
| 4 | stg_price_daily | 59.9 | 9,201,516 | 4.4% |
| 5 | stg_listing_daily | 54.2 | 9,201,516 | 4.0% |
| 6 | stg_flow_split_daily | 41.9 | 949,023 | 3.1% |
| 7 | stg_lending_daily | 36.3 | 6,988,296 | 2.7% |
| 8 | stg_foreign_daily | 36.1 | 7,682,844 | 2.7% |
| 9 | stg_short_daily_kiwoom | 29.4 | 3,971,630 | 2.2% |
| 10 | stg_fin_wise | 27.1 | 445,294 | 2.0% |
| | 상위 10 소계 | 1,238.5 | | **91.9%** |

→ **`stg_fin` 하나가 전체의 절반**이다. 그런데 `stg_fin` 의 원장 `dart.dart_fin_raw` 는 지금 안 자란다(§3.5). 전체 재빌드를 매일 도는 건 "안 변한 15,375,024행을 매일 11분간 다시 쓰는 것"이다.

### 3.4 원장 → stage 의존 지도 (rules_*.py 의 `sources` 에서 추출, 66테이블 전수)

| 원장 DB | 원장 테이블 | 재빌드해야 하는 stage 테이블 |
|---|---|---|
| **krx** | krx_stk_bydd_trd, krx_ksq_bydd_trd | stg_price_daily |
| | krx_etf_bydd_trd | stg_etf_price_daily |
| | krx_kospi_dd_trd, krx_kosdaq_dd_trd | stg_index_daily |
| | krx_stk_isu_base_info, krx_ksq_isu_base_info | stg_listing_daily |
| | ingest_log | stg_ingest_krx |
| **kiwoom** | ka10060_investor_flows | stg_flow_daily_kiwoom **+ stg_price_daily 의 G9 교차대조 입력** |
| | ka10014_short_selling | stg_short_daily_kiwoom |
| | ka10008_foreign_holdings | stg_foreign_daily |
| | ka20068_lending_balance | stg_lending_daily |
| | **ka10099_stock_master** ← 매일 자람 | **stg_master_daily** |
| | ingest_shard | stg_shards_kiwoom |
| **kis** | kis_investor_flow / kis_short_sale / kis_loan_trans / kis_credit_balance / kis_stock_info / kis_call_log / kis_ingest_log | stg_flow_split_daily / stg_short_daily_kis / stg_loan_daily_kis / stg_credit_daily / stg_delisted_master / stg_calls_kis / stg_units_kis |
| **dart** | dart_disclosure | **stg_rcept_dt_map**(참조표) · stg_disclosure |
| | dart_fin_raw | stg_fin |
| | dart_dividend/shares/capital/tesstk/hyslr/audit | stg_dividend·shares·capital·tesstk·hyslr·audit |
| | dart_elestock(+_v1) / dart_majorstock(+_v1) | stg_holder_elestock / stg_holder_majorstock |
| | dart_company / dart_corp_map | stg_company / stg_corp_map |
| | dart_*_decsn 15종 | stg_event_* 15종 |
| | doc_store | stg_doc_index · **stg_doc_meta·section·correction·parse_log**(ZIP 프리패스 경유) |
| | dart_call_log / ingest_log | stg_calls_dart / stg_units_dart |
| **wise** | **ws_raw** ← 매일 자람 | **stg_consensus_monthly·annual·quarterly·matrix · stg_analyst_summary·broker · stg_fin_wise (7)** |
| | **ws_coverage / ws_call_log** ← 매일 | **stg_wise_coverage / stg_calls_wise** |
| | v3_* 4종 (동결) | stg_v3_revision_daily·analyst_opinions·consensus_annual·revision_compare |

**stage 내부 의존은 단 하나**: `stg_rcept_dt_map` → DART 22테이블(fin·보조원장 6·event 15·doc_meta/section/correction/parse_log). `stg_disclosure` 가 바뀌면 `stg_rcept_dt_map` 도 바뀌고, 그러면 그 22개를 전부 다시 지어야 값이 정합한다.
**교차 게이트 의존 하나**: `stg_price_daily` 의 G9 가 `kiwoom.ka10060` 원장을 직접 읽는다 → **kiwoom 스냅샷 없이는 stg_price_daily 를 빌드할 수 없다**(`__main__.py:34`, `build.py:316-317`).

### 3.5 지금 원장이 실제로 어디까지 차 있나 (실측 2026-09-09)

| 원장 | 파일 mtime | 데이터 최신 | 상태 |
|---|---|---|---|
| krx.db | 08-23 18:41 | `max(BAS_DD)=20260820` | **정지** |
| kis.db | 08-27 20:55 | `max(deal_date)=20260818` | **정지** |
| dart.db | 09-02 22:34 | `max(rcept_no)=20260901900796`, doc_store 174,309 | **정지** |
| kiwoom.db | **09-08 21:00** | `ka10099 max(snap_date)=20260909`, 38,771행/9일 | **매일 자람**(daily_wise ①) |
| wisereport.db | **09-08 21:04** | `ws_raw max(fetched_date)=2026-09-09`, 140,282행 | **매일 자람**(daily_wise ②) |

즉 설계문서 ⑨ 의 판정 그대로다 — "**시계열 일일 증분은 미설계가 정상**, 매일 도는 건 소멸성 소스 적립뿐"(STAGE_DESIGN.md:467). KRX/KIS/DART 를 다시 매일 수집하기 시작하면 §3.4 의 지도가 그대로 재빌드 목록이 된다.

---

## 4. 게이트와 baseline

### 4.1 게이트 목록 — G0~G9 (`database/src/stage/gates.py`)

폐기형(FAIL = 새 버전 폐기, 구 버전 무손): G0·G1·G3·G4·G5·G6·G8·G9. 행 격리형(비율 임계 초과 시에만 FAIL): G2·G7.

| 게이트 | 술어 (코드) | SKIP 조건 | 인용 |
|---|---|---|---|
| **G0** 선언 대조 | 소스마다 `duckdb_columns()` 에 규칙 컬럼 전부 실재(`missing == []`) **AND** `expected_len` 선언 컬럼의 `length(col) <> N` 행수 == 0 | 없음(항상 실행) | gates.py:75-101 |
| **G1** 행수 등식 | `n_stage == n_src × fanout − n_dedup − n_reject` | 없음 | gates.py:104-110 |
| **G2** 캐스팅 손실 | `count(_src_flag='partial') / n_stage <= thresholds["G2"]` (기본 0.0) | 없음 | gates.py:113-121 |
| **G3** 불변식 | 규칙별 `Invariant.violation_sql` 위반 행 == 0, `key_unique=True` 면 자연키 중복 그룹 == 0 | 없음 | gates.py:124-137 |
| **G4** 골든 픽스처 | `unit_scale` 선언 컬럼에 픽스처 없으면 즉시 FAIL. 그 외엔 픽스처 기대값 문자열 비교 전건 일치 | 픽스처 파일 없으면 `skip(no_fixtures)` | gates.py:140-172 |
| **G5** 회귀 Δ등식 | `Δn_stage == Δn_src × fanout − Δn_dedup − Δn_reject` (직전 빌드 G1 metrics 대비) | 직전 빌드 없으면 `skip(no_baseline)` | gates.py:175-188 |
| **G6** 판본 보존 | `(natural_key, observed_date)` 중복 그룹 == 0 | `write_mode != append_only` → skip · `versioned=False` → `skip(unversioned)` | gates.py:191-200 |
| **G7** 범위 | (관측일 축 밖 reject 행 + 내용일 축 밖 NULL 셀) / n_src <= `thresholds["G7"]` (기본 0.001). 축 = 관측일 `[1999, 올해+1]`, 내용일 `[1900, 올해+40]` | 없음 | gates.py:203-223, 20-22 |
| **G8** 파싱 등식 | blob/file 소스: `n_parse_failed==0` ∧ `n_value_mismatch==0` ∧ `n_rows_emitted == n_src` | blob/file 아니면 `skip(not_blob)` | gates.py:226-241 |
| **G9** 교차 소스 | 원장 직접 조인. `close_match_ratio >= thresholds["G9_close"]`(=1.0) ∧ joined>0 ∧ **baseline 의 `close_match_ratio`·`volume_match_ratio` 이상** | `cross_check` 없으면 skip · 해당 원장 미부착이면 `skip(ledger_unavailable:<db>)` | gates.py:244-272 |

기본 임계 (`gates.py:15-19`): `G2 0.0` · `G7 0.001` · `G9_close 1.0`.
우선순위 (`build.py:440`): `DEFAULT_THRESHOLDS` ← `baseline.json[table].thresholds` ← CLI `--g2/--g7`.

### 4.2 baseline.json — 무엇이 들어 있고 누가 게이트로 쓰는가

- 위치 `data/stage/baseline.json` (서버 실측 12,994B, mtime **2026-09-02 16:48**).
- 생성: `PYTHONPATH=src python -m stage.baseline --snapshot-id <id>` (`baseline.py:173-190`). **스냅샷 원장 위에서 duckdb 로 28지표를 SQL 그대로 재측정**하고, 항목마다 `sql` 을 같이 적어 재현 가능하게 둔다.
- 구조: `{table: {metric: value, thresholds: {...}}, _measured: [{table, metric, db, sql, value, measured_at, growing}], snapshot_id, measured_at}`.
- **`growing: True` 표식** (`baseline.py:32`, "수집 중(성장) 여부 — 빌드마다 값이 달라져도 게이트 실패가 아니다") 이 붙은 지표: `stg_doc_index.n_rows` · `stg_consensus_monthly.n_blobs` · `stg_analyst_summary.n_blobs` (`baseline.py:104,113,116`).

**중요 — 게이트가 실제로 읽는 baseline 값은 두 종류뿐이다:**

1. `thresholds` — 6테이블에만 선언 (`baseline.py:120-128`): `stg_listing_daily G2=0.01` · `stg_shares G2=0.12` · `stg_dividend G2=0.001` · `stg_hyslr G2=0.0001` · `stg_credit_daily G2=0.0001` · `stg_delisted_master G2=0.05, G7=0.01`.
2. `close_match_ratio` / `volume_match_ratio` — **G9 만** 읽는다 (`gates.py:265-269`).

나머지 24지표는 **기록용**이다 — 어떤 게이트도 읽지 않는다. 즉 "행수가 매일 늘어나는 것"은 baseline 을 통해서는 절대 게이트를 깨지 않는다.

### 4.3 "행수가 매일 늘어나는 것"을 게이트가 어떻게 취급하나 — 항목별 판정

| 게이트 | 일일 성장에 대한 거동 | 판정 |
|---|---|---|
| G1 | 절대 등식. 늘어난 만큼 `n_src` 도 늘어나므로 자동 성립 | 안전 |
| **G2** | **비율**(`partial/n_stage`)이라 증가/감소 임계가 아니다. 새 데이터의 캐스팅 실패율이 기존 평균보다 나쁘면 비율이 올라간다. `stg_shares`(임계 12%) 처럼 여유가 큰 곳은 안전, **기본값 0.0 인 60테이블은 새 데이터 1행만 cast_failed 여도 즉시 FAIL** | **위험** |
| G3 | 절대. 새 행이 불변식/키 유일성을 깨면 FAIL | 정상 동작 |
| G4 | 픽스처는 과거 고정 좌표라 성장 무관 | 안전 |
| **G5** | Δ등식이라 **성장을 허용한다** — 늘어난 `Δn_src` 만큼 `Δn_stage` 가 늘면 pass. 설계 v2 의 "증가 허용"을 v2.2 가 등식으로 바꾼 것 | 안전 · 의도대로 |
| G6 | `(키, observed_date)` 유일성. WISE blob 은 키에 `fetched_date` 가 들어가고 kiwoom master 는 `first_write_wins`(G6 skip) | 안전 |
| G7 | 비율(`n_out_of_range / n_src`). 새 날짜가 `[1999, 올해+1]` 안이면 무영향 | 안전 |
| G8 | `emitted == n_src` 등식 | 안전 |
| **G9** | `close_match_ratio >= 1.0` **(100.0000%)** 이면서 baseline(1.0) 이상. 새로 들어온 하루치 KRX×키움 종가에 **단 1행이라도 불일치가 있으면 stg_price_daily 빌드가 폐기**된다 | **가장 위험** |

**G5 의 미구현 항목** — 설계(STAGE_DESIGN.md §9 G5 행)는 "스냅샷 콘텐츠 해시(`src_bytes`·`src_mtime`)도 비교해 **upsert 소스의 값 변경(n_src 불변)을 잡는다**"고 적었으나, `gates.g5_regression_delta`(gates.py:175-188)는 행수 Δ만 본다. `src_bytes`·`src_mtime` 은 `_meta.json` 에 **기록만** 된다(`build.py:550-551`). 일일 운영에서 KRX/키움 upsert 소스의 값 정정이 조용히 통과할 수 있다 — **설계-코드 갭**.
동일하게 G6 의 "자연키 중복 중 payload 동일 비율 기록"도 코드에 없다(중복 수만 센다).

### 4.4 baseline 갱신 규칙과 `check_baseline_lock.py`

- 갱신 주체: **사람**. `baseline.py:7` "갱신은 사람이 승인한다", STAGE_DESIGN.md §9 말미 "★ 갱신은 diff 를 커밋 메시지에 남기고 사람이 승인한다. '게이트가 깨졌다'와 '원장이 자랐다'는 `baseline.json` 의 `measured_at` 과 `_meta.json` 의 `src_mtime` 으로 가른다."
- **자동 갱신 코드 없음.** `run_stage_all.sh:12` 도 "게이트 임계는 baseline.json 에서 읽는다 — **여기 하드코딩 금지**"만 적고 갱신은 하지 않는다.
- **`database/scripts/check_baseline_lock.py` 는 stage 가 아니라 equity 전용이다** (`check_baseline_lock.py:2-17`): 서버 `~/quant-ledger/data/equity/baseline.json` 이 레포 `database/src/equity/baseline_locked.json` 과 **바이트 동일**한지 sha256 으로 대조하고, 다르면 `thresholds.<G>` 를 `threshold_<G>` 로 펴서 상수 diff 를 찍는다. 종료코드 0=동일/1=다름/2=인자오류.
  → **stage baseline 에는 이런 lock 파일도, 대조 스크립트도 없다.** 서버 `data/stage/baseline.json`(09-02 16:48)이 유일본이고 레포에 사본이 없다. 일일 크론화 시 stage 쪽에도 같은 장치가 필요한지가 결정 사항.

### 4.5 content_hash 재현성 검증 (`run_stage.sh`)

`database/scripts/run_stage.sh:11-21`:
1. `python -m stage --table T` (스냅샷 새로 뜸) → 출력에서 `snapshot=snap_...` 추출.
2. **같은 스냅샷 id 로 한 번 더** 빌드.
3. `hash=<n>:<hex>` 를 두 출력에서 뽑아 `H1 = H2` 를 비교, "재현성 OK/FAIL" 을 로그(`logs/stage_<table>.log`)에 남긴다.

`content_hash` 자체는 `build.py:288-294`:
```sql
SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM read_parquet('<glob>', hive_partitioning=true) t
```
→ `"{행수}:{16진}"`. **행 순서 무관**(bit_xor) 이므로 파티션 파일 분할이 달라져도 같은 값이 나온다.

실측(STAGE_DESIGN.md:451): 2차 패스 61/61 ok, 1,348초, **content_hash 60/60 동일**, G5 Δ=0 전 테이블 pass.

**주의 — `run_stage_all.sh` 는 2회 빌드를 하지 않는다**(테이블당 1회, `run_stage_all.sh:39`). 재현성 대조는 "같은 스냅샷으로 전체를 두 번 돌려 summary.tsv 를 비교"하는 수동 절차였다. 일일 크론이 재현성을 매일 확인하려면 **비용이 2배(45분)** 가 된다.
알려진 재현성 결함 1건: `stg_doc_parse_log` 의 `t_*_ms` 가 payload 에 들어 있어 **content_hash 가 실행마다 달라진다** (DOC_DESIGN.md:420 미결 — `payload_exclude` 로 뺄 것).

---

## 5. 문서층 `stg_doc_*`

### 5.1 캐시 구조

- 프리패스 `python -m stage.doc_prepass --snapshot-id S` 가 스냅샷 `dart.doc_store WHERE zip_ok=1` 의 ZIP 을 **한 번만** 파싱해 JSON Lines 로 떨군다:
  `data/stage/_tmp/doc/<snapshot_id>/<table>/year=YYYY_qN.jsonl` + `summary.json` (doc_prepass.py:3, 191, 408).
- 샤드 = **접수 연도×분기**(`_shard_of`, doc_prepass.py:76-80) — 연도 단위면 큰 연도가 끝에 홀로 남아 워커가 논다는 이유. 실측 **68 샤드**.
- 빌더는 이 캐시를 `FileSource` 로 읽는다 (`build.py:393-417`): `summary.json` 이 없으면 "프리패스를 먼저 돌려라" 예외, `status != "ok"` 면 RuntimeError.
- `run_stage_all.sh:26-32` 가 같은 조건을 셸에서 선체크해서, 캐시가 없으면 doc 4테이블을 **조용히 건너뛰고** 안내를 찍는다.
- 서버 실측: 캐시 `data/stage/_tmp/doc/snap_20260902T230100Z` **3.7GB**, 이 한 스냅샷 것만 존재. DOC_DESIGN.md:280 은 "캐시는 빌드 완료 후 삭제(keep 0)" 라고 했지만 **실제로는 09-04 부터 남아 있다**(정리 코드 없음).

### 5.2 증분 경로 — `--scan` / `--repair` 는 "파서 수정" 용이지 "새 문서 추가" 용이 아니다

| 명령 | 하는 일 | 비용(실측) |
|---|---|---|
| `--scan REGEX --out LIST` | ZIP 을 **디코딩만** 하고 정규식에 걸리는 접수번호를 뽑는다(파싱 없음). `scan()`/`_scan_shard`, doc_prepass.py:295-327 | "분 단위"(docstring). 전량 디코딩이라 수 분~수십 분 **추정** |
| `--repair LIST` | 목록 문서만 재파싱 → `_replace_rows` 로 샤드 JSONL 안의 해당 `rcept_no` 행을 원자 교체 → `summarize_from_cache` 로 summary 재집계. `input_hash`·D0 missing 은 이전 summary 에서 **그대로 이어받는다**. doc_prepass.py:330-387 | 정정 1건 수리 **22초**(DOC_DESIGN.md:408 실측). 정정 문서 전체(17,603) 재파싱 ≈ 15분(DOC_DESIGN.md:180 추정) |

**`repair()` 의 두 가드가 "신규 문서 추가" 를 막는다:**
- `doc_prepass.py:353-354` — `cache/summary.json` 이 이미 있어야 한다. 캐시 디렉터리는 **snapshot_id 키**라, 새 스냅샷을 뜨면 캐시가 없어 repair 불가.
- `doc_prepass.py:358-360` — `found != rcept_list` 면 ValueError.
- `run()`(전량 경로)은 진입 즉시 `_clear_cache()` 로 그 스냅샷 캐시의 `year=*.jsonl` 을 **전부 지운다**(doc_prepass.py:178-184, 192). `--years`·`--rcept-list` 부분 실행도 마찬가지라, 부분 실행은 "표본·픽스처용"(argparse help)이지 증분용이 아니다.

→ **"doc_store 에 신규 ZIP 만 파싱" 하는 코드 경로는 없다.** DOC_DESIGN.md:17 은 "접수번호 키·ZIP 불변이라 `doc_store` 신규 행만 파싱하면 되는 **구조**" 라고 가능성만 적어 두고 "일일 증분 — stage 와 동일하게 1회 풀 빌드 후 별도 설계" 로 미뤘다.
가장 값싼 신설안(추정): ① 이전 스냅샷의 캐시 디렉터리를 새 snapshot_id 로 복사(하드링크) → ② 신규 접수번호만 `--repair` 로 넣고 `input_hash` 를 새로 계산하도록 `repair()` 를 확장. `_replace_rows` 는 이미 "없던 rcept_no 를 add" 할 수 있으므로 변경은 `input_hash` 계산과 가드 두 줄 수준.

### 5.3 전체 재파싱 비용 (실측 · DOC_DESIGN.md:397,404)

- 전량 프리패스 3회: **2h44m · 3h16m · 4h34m** (마지막이 4워커, RSS 0.59GB, 파서 p1.4).
- 대상 171,179 문서 / 멤버 242,196 / ZIP 결함 0 / 파싱 실패 0.
- 단일 코어 산식(DOC_DESIGN.md:180): 문서당 48ms → 파싱만 2.3h, ZIP 해제·산출 포함 ×1.5 ≈ 3.5h, 3워커 ≈ 1.2h.
- 프리패스 후 duckdb 빌드는 싸다 (실측): `stg_doc_meta` 242,196행 **6.7s** · `stg_doc_parse_log` 242,196행 **2.6s** · `stg_doc_correction` 17,600행 **1.2s** · `stg_doc_section` **7,929,624행 74s** (RSS 6.7GB). 합 ≈ **85초**.
- 서버 현 상태(실측): 4테이블 전부 커밋됨, `stg_doc_section` 7,929,624행 232MB, doc_checks.json `status: "ok"`.

→ **문서층의 일일 비용은 사실상 "프리패스 3~4.5시간" 이 전부**다. doc_store 가 지금 안 자라므로(174,309 고정, 09-03 07:34 수집 종료) **당장은 매일 돌 이유가 없다.**

---

## 6. 매니페스트 · 완료 판정 · equity 참조

### 6.1 `MANIFEST.json` 이 남기는 것 (`database/src/stage/manifest.py`)

테이블당 1개, `data/stage/<table>/MANIFEST.json`:

```
{ table, current_build, keep(=3), builds: [ BuildRecord ] }
BuildRecord = { build_id, snapshot_id, rules_version, built_at_utc,
                n_rows, content_hash, partitions:[{path, n_rows}], gates:[{name,status,detail,metrics}],
                inputs:{}  ← equity 전용, stage 는 항상 빈 dict }
```

- 커밋은 **`os.replace` 로 MANIFEST.json 파일 하나만** 원자 교체 (`manifest.py:50-53`). 디렉터리가 아니라 파일을 바꾸는 이유가 주석에 명시돼 있다.
- keep=3 밖 구버전 `v=<build_id>` 는 `shutil.rmtree` (`manifest.py:64-70`).
- build_id 형식 `b_%Y%m%dT%H%M%S_%fZ` (`build.py:426`).
- 파티션마다 `_meta.json` 도 쓴다 (`build.py:546-559`): `n_rows`·`n_src`·`fanout`·`n_dedup`·`n_reject`·`n_out_of_range`·**`src_bytes`·`src_mtime`**·`snapshot_id`·`rules_version`·`coverage_from`·`version_loss_upstream`·`observed_date_exempt`·`rcept_map_miss`·`lag_known`·`content_hash`·`doc_input_hash`·`gates`.
- 실패 시: tmp 즉시 `rmtree`, 판정은 `data/stage/_failed/<build_id>.json` (`build.py:526-534`). **`_failed` 는 GC 대상이 아니다** — 서버에 15건 잔존(실측).

### 6.2 equity 는 stage 를 어떻게 보나 — **pin 을 옮겨야 한다**

- 읽기 계약(STAGE_HANDOFF.md §1): `MANIFEST.json` 의 `current_build` → 그 BuildRecord 의 `partitions[].path` 만. **맨 glob 금지**(구버전이 keep=3 으로 공존).
- 고정: `equity/inputs.py:110-134` `pin()` 이 `current_build` 의 파티션 파일을 `data/equity/_pinned/<table>/v=<build>/` 로 **하드링크**하고, 그 옆에 BuildRecord 만 담은 MANIFEST 를 원자 기록한다. `manifest.commit()` 은 **일부러 안 부른다**(keep 밖을 rmtree 해서 고정한 하드링크를 지우므로 — inputs.py:10,144).
- **핀 시점 = equity 테이블을 빌드하는 순간**: `equity/build.py:151` `pinned = {t: inputs.pin(stage_root, equity_root, t) for t in rule.inputs}`.

→ **답: 자동으로 새 것을 보지 않는다.** stage 를 새로 빌드해도, equity 산출물은 자기 `BuildRecord.inputs` 에 적힌 **옛 stage build_id** 를 계속 가리킨다. 새 stage 를 반영하려면 **그 equity 테이블을 다시 빌드해야** 하고, 그때 `pin()` 이 새 `current_build` 를 잡는다.
- 이때 `EG5a`(재현성 게이트)는 `inputs_changed` 로 **SKIP** 된다 (`equity/gates.py:333`, `equity_rebuild_all.sh:6`). 즉 stage 가 바뀐 날의 equity 빌드는 재현성 검증이 자동으로 꺼진다 — 일일 플랜이 알아야 할 사실.
- 서버 실측: `data/equity/_pinned/` 에 stage 테이블 **41개** + equity 테이블 27개가 고정돼 있다. equity ORDER 는 28표(`equity_rebuild_all.sh:17`).

---

## 7. "stage 하루 갱신 완료" 판정 — 제안 (기대치는 실측)

빌드 후 아래 5개를 순서대로 통과해야 "오늘치 stage 갱신 완료"로 본다. 전부 서버 로컬 파일만 읽는다.

### C1. 커밋 도달 — 대상 테이블 전부가 오늘 스냅샷을 가리키는가

```python
# data/stage/<t>/MANIFEST.json 의 current_build 레코드
assert rec["snapshot_id"] == TODAY_SNAPSHOT_ID
assert rec["built_at_utc"] >= TODAY_START_UTC
```
기대치(실측): 오늘의 대상 테이블 수. B 안이면 **10**, A 안이면 **66**.
현재 상태(실측 09-09)는 3개 스냅샷 혼재 — 54 / 11 / 1.

### C2. 게이트 무FAIL

```python
assert all(g["status"] != "fail" for g in rec["gates"])
```
기대치: `fail` 0건. 참고로 `skip` 은 정상이다 — G6 은 upsert/first_write_wins 17테이블과 `versioned=False` 로그류에서, G8 은 blob/file 11테이블 외 전부에서, G9 는 `stg_price_daily` 외 전부에서 skip 이다.
보강: `data/stage/_failed/` 에 오늘 mtime 파일이 **0건**이어야 한다.

### C3. 성장 방향성 — append_only 테이블의 행수는 줄지 않는다

```python
prev, cur = builds[-2], builds[-1]
if rule.write_mode == "append_only":
    assert cur["n_rows"] >= prev["n_rows"]
```
기대치(실측 근거): ws_raw 약 **+15.6k행/일**, kiwoom ka10099 약 **+4.3k행/일**. 대략 `stg_master_daily +4,300` · WISE 7테이블 합 +수만 행.
※ G5 가 이미 Δ등식을 보지만, G5 는 "설명되는 감소"도 통과시킨다. 이 체크는 방향성 전용.

### C4. 안 변한 원장은 해시가 그대로여야 한다 (공짜 재현성 검사)

KRX·KIS·DART 원장이 정지 상태인 동안, 그 소스의 stage 테이블을 다시 지으면 `content_hash` 가 **직전 빌드와 완전히 같아야** 한다.

```python
if ledger_src_mtime_unchanged:      # _meta.json 의 src_mtime 비교
    assert cur["content_hash"] == prev["content_hash"]
```
기대치(실측 선례): 2차 패스에서 **60/60 동일**(STAGE_DESIGN.md:451), `stg_price_daily` `9201516:a111951402930d93`(STAGE_DESIGN.md:385).
알려진 예외 1건: `stg_doc_parse_log` 는 `t_*_ms` 때문에 매번 달라진다(DOC_DESIGN.md:420) — 이 체크에서 제외하거나 먼저 고칠 것.

### C5. 소요 시간 예산

`logs/stage_all/summary.tsv` 의 오늘 블록 `elapsed_s` 합을 예산과 대조.
기대치(실측): A 안 **1,348초 ± 20%** (벽시계 22.5분) / B 안 **43.6초 ± 20%**. `stg_fin` 단독 666초가 절반이므로 A 안의 예산 초과는 거의 항상 `stg_fin` 이다.

### 참고 — 판정에 쓸 수 있는 최소 명령 (읽기 전용)

```bash
# 오늘 커밋된 테이블·스냅샷·게이트 요약
cd ~/quant-ledger && .venv/bin/python - <<'PY'
import json,glob
for mp in sorted(glob.glob("data/stage/*/MANIFEST.json")):
    m=json.load(open(mp)); cb=m["current_build"]
    r=next(b for b in m["builds"] if b["build_id"]==cb)
    bad=[g["name"] for g in r["gates"] if g["status"]=="fail"]
    print(m["table"], r["snapshot_id"], r["built_at_utc"], f'{r["n_rows"]:,}', r["content_hash"], bad or "")
PY
# 실패 리포트
ls -la data/stage/_failed/
# 이번 런 요약
tail -n 70 logs/stage_all/summary.tsv
```

---

## 부록 A. 지금 서버 stage 상태 요약 (실측 2026-09-09)

- 테이블 **66/66 커밋**. 총 **8.5GB**(keep=2~3 판본 누적), 최대 `stg_fin` 925MB · `stg_credit_daily` 778MB · `stg_price_daily` 749MB.
- 스냅샷 3종 혼재(§2.4). 마지막 빌드 **09-05 10:59**(DART 6테이블 재빌드).
- `data/stage/baseline.json` 09-02 16:48 (28지표 + 임계 6테이블).
- `data/stage/_tmp/doc/snap_20260902T230100Z` 3.7GB 잔존.
- `data/snapshots/` 37GB 잔존, 디스크 여유 **271GB**.
- 크론에 stage 항목 **없음**. quant-ledger 크론은 `daily_wise.sh` 하나(KST 06:00, 실측 09-09 06:00:01→06:04:47, 4.7분, 요청 15,617).

## 부록 B. 코드-설계 갭 (일일 플랜이 정하고 갈 것)

| # | 갭 | 위치 |
|---|---|---|
| 1 | 스냅샷 GC/보관 정책이 코드·문서 어디에도 없다. 일일 풀 스냅샷은 17GB/일 | snapshot.py 전체 |
| 2 | `flock` 이 stage 러너에 없다. 설계는 `/tmp/quant_ledger_raw.lock` 을 지정 | run_stage.sh · run_stage_all.sh vs STAGE_DESIGN.md:72,77 |
| 3 | G5 의 `src_bytes`·`src_mtime` 비교 미구현 → upsert 소스의 **값 변경**(행수 불변)이 무검출 | gates.py:175-188 vs STAGE_DESIGN.md §9 G5 |
| 4 | G6 의 "payload 동일 비율 기록" 미구현 | gates.py:191-200 |
| 5 | `stg_doc_parse_log` content_hash 비결정(`t_*_ms` payload) → 재현성 검사 불가 | DOC_DESIGN.md:420 |
| 6 | stage baseline 에는 equity 의 `baseline_locked.json` + `check_baseline_lock.py` 같은 잠금·대조 장치가 없다 | scripts/check_baseline_lock.py (equity 전용) |
| 7 | 문서 프리패스 캐시가 "빌드 후 삭제(keep 0)" 라고 문서에만 있고 삭제 코드가 없다(3.7GB 잔존) | DOC_DESIGN.md:280 |
| 8 | `_failed/` GC 없음(설계상 의도, 다만 일일 크론이면 누적) | STAGE_DESIGN.md:79 |

---

## 플랜 작성자에게 — 결정이 필요한 쟁점

**D1. 매일 무엇을 다시 짓는가 — 전체(A) vs 영향 테이블만(B)**
비용은 A **22.5분 + 스냅샷 3.8분 + 17GB**, B **44초 + 스냅샷 ~35초 + ~4GB**(실측/추정). 지금 자라는 원장이 kiwoom 마스터·wise 둘뿐이므로 B 가 압도적으로 싸다. 반대로 A 는 "매일 66테이블이 한 스냅샷으로 정렬된다"는 단순함을 산다. **다만 KRX/KIS/DART 수집이 재개되는 순간(§3.5) B 의 대상은 §3.4 지도 전체로 커진다** — B 를 고르더라도 "원장별 mtime/max(date) 를 보고 대상을 자동 판정" 하는 트리거를 함께 설계해야 한다. 판정 근거는 이미 `_meta.json` 의 `src_mtime` 에 있다.

**D2. 스냅샷 보관 규칙 (미결정 · 반드시 정할 것)**
현재 정책이 **없다**. 후보: (a) `keep=N` 일치기, (b) 빌드 성공 후 즉시 삭제하고 `snapshot.json` 만 남김, (c) 주 1회 풀 스냅샷만 보존. B 안이면 kiwoom+wise 4GB/일이라 keep=7 도 28GB 로 견딜 만하지만, A 안이면 17GB/일 → keep=3 이 상한(51GB, 여유 271GB 대비).

**D3. 증분 빌더 신설 여부 — 지금은 "필요 없다" 가 답으로 보인다**
파티션 append 증분(C안)은 G1 전역 행수 등식·dedup 윈도·content_hash 재현성을 전부 다시 설계해야 한다. 반면 B 안의 실측 비용이 44초다. **증분 빌더가 필요해지는 조건은 하나뿐 — `stg_fin`(666초) 처럼 큰 테이블의 원장이 매일 자라기 시작할 때.** 그 시점이 오면 `receipt_axis`(연도 파티션)를 입력 필터로 승격하는 안을 검토하되, 그 전에 G1/G5 를 "파티션 단위 등식"으로 재정의하는 설계가 선행돼야 한다.

**D4. G9 를 매일 통과시킬 수 있는가 (가장 큰 운영 리스크)**
`stg_price_daily` 의 G9 는 종가 일치율 **100.0000%** 를 요구한다(기본 임계 1.0 + baseline 1.0). KRX 수집이 재개되면 하루치 신규 데이터에 단 1행의 KRX↔키움 종가 불일치만 있어도 그날 가격 stage 가 폐기된다. 결정 필요: (a) 그대로 두고 실패 시 사람이 본다(fail-closed 유지), (b) baseline 을 "직전 값 이상" 이 아니라 "직전 값 − ε" 로 완화, (c) G9 를 신규 파티션에만 적용. 참고로 거래량 쪽은 이미 99.98642% 라 baseline 비교만 걸려 있다.

**D5. baseline 자동 갱신 정책**
현재 규칙은 "**사람이 승인**"(baseline.py:7, STAGE_DESIGN §9). 게이트가 실제로 읽는 값은 `thresholds` 6테이블 + G9 비율 2개뿐이므로, **자동 갱신을 하지 않아도 일일 성장은 게이트를 깨지 않는다.** 다만 (a) `growing` 지표 3개(doc_index·monthly·analyst n_blobs)를 매일 재측정해 기록만 남길지, (b) G9 비율을 자동 재측정하면 **저하를 스스로 승인해 버리는** 위험이 있으니 금지할지, (c) stage 에도 equity 처럼 `baseline_locked.json` + 대조 스크립트를 둘지 — 셋 다 결정 필요. 권고(조사자 의견): **(b)는 금지**, (a)만 자동, (c)는 stage 파일이 서버에만 있어 유실 위험이 있으니 도입.

**D6. 문서층은 일일에서 뺄 것인가**
doc_store 는 174,309 로 고정(09-03 수집 종료, 실측)이고 전량 프리패스는 3~4.5시간이다. **현행 권고: 일일 대상에서 제외**, DART 문서 수집이 재개될 때만 §5.2 의 증분 프리패스(캐시 하드링크 복사 + `repair` 확장)를 신설. 그 전에 `stg_doc_parse_log` 의 content_hash 비결정(부록 B-5)을 고쳐야 재현성 판정이 성립한다.

**D7. equity 를 매일 따라 돌릴 것인가**
stage 를 다시 지어도 equity 는 자동으로 새 것을 보지 않는다(§6.2 — `pin` 이 equity 빌드 시점에 잡힌다). 그리고 stage 가 바뀐 날의 equity 빌드는 `EG5a` 가 `inputs_changed` 로 skip 된다. 결정: (a) stage 만 매일 돌리고 equity 는 주기적으로/수동, (b) stage→equity 28표를 매일 체인. (b) 를 고르면 equity 재현성 검증을 어떻게 대체할지가 따라온다.

**D8. 크론 배타 제어와 실행 창**
설계는 `06:30~익일 05:30 KST` 창과 `/tmp/quant_ledger_raw.lock` 을 지정했지만 **코드에 없다**(부록 B-2). `daily_wise.sh` 는 06:00~06:05 KST 에 kiwoom·wise 원장을 쓴다(실측). 일일 stage 는 06:30 이후로 잡고 `flock -n /tmp/quant_ledger_raw.lock` 을 `run_stage_all.sh` 와 `daily_wise.sh` 양쪽에 붙일지 결정.

**D9. 재현성 2회 빌드를 매일 할 것인가**
`run_stage.sh` 는 테이블 1개를 2회 빌드해 해시를 대조하지만 `run_stage_all.sh` 는 1회다. 매일 재현성을 확인하려면 비용이 2배(A안 45분/B안 88초). 대안으로 **C4(안 변한 원장 = 같은 해시)** 를 쓰면 추가 비용 0 으로 같은 성질을 검사할 수 있다 — 권고.
