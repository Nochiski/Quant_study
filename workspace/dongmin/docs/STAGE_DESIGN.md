# Stage 레이어 구현 설계 v2.2 (최종 검수 반영)

> 2026-09-02. v2 를 Opus 5기 **2차 병렬 적대 리뷰**(전건 서버 실측 기반 — 기계검증·PIT재검·
> 회귀검증·사실실측·구현시뮬레이션)로 재개정. 층 계약·실측 사실의 정본은 `STAGE_SPEC.md`.
> v1 의 실패 = 실측을 뒤집음. v2 의 실패 = 실측을 안 함(유령 컬럼 2·유령 클래스 1·미정의 3).
> **v2.1 의 원칙: 규칙 한 줄마다 원장 실측 근거를 병기한다. 근거 없는 규칙은 쓰지 않는다.**
>
> v2 → v2.1 핵심 변경 6:
> ① available_date 재정의 — **사실 날짜만 기록, 랙·보수 버퍼 금지 (판단은 엔진)** [결정 ⑥]
> ② 골격 3컬럼 추가 — `observed_date` · `miss_kind` · 테이블 선언 `write_mode`
> ③ `is_latest`·`observed_seq` 제거 — 판본 선택은 equity 가 (available_date, observed_date) 로
> ④ 중복 접기 = **payload 투영 동일** (바이트 동일 정의는 실측상 공집합)
> ⑤ 카탈로그 실측 정정 — 지수 `IDX_NM`/`IDX_CLSS` · WISE ep 지도 · stg_fin 자연키 8컬럼
> ⑥ 게이트 9→10종 (G9 교차 소스 회귀) + G5 Δ등식 + G7 축 분리
>
> v2.1 → v2.2 (2026-09-02 최종 검수 — 4축 병렬 적대 리뷰 + 지적 전건 서버 재측정. 치명 5·중요 10·경미 14·이관 3):
> ⑦ `observed_date` 원천 확정 — **원장 시각은 전부 UTC 무표기**, 테이블별 `observed_src` 선언, 시각 컬럼 0 테이블은 면제 (§3)
> ⑧ G7 범위 게이트 = **행 격리형**. 테이블 폐기 금지 — 원장에 dart_capital 2120~2923 7행·tsstk_dp 2106 1행 실재 (§9)
> ⑨ survey v2 = **전수 스캔 + 어휘 측정, 캐스트 금지** — 표본 유래 범위 반증(whol_loan_gvrt −292~+333 → 전수 −594.76~+1120.92) (§5·§10)
> ⑩ 파티션 61테이블 전수 선언 (§4-파티션) · 회귀 `baseline.json`·★ 목록 통합·게이트 상수 술어 병기 (§9)
> ⑪ 카탈로그 보강 — WISE `target_price`·`min_max`·단위값, SPEC 규칙 4건 이식(천원·통화·감사의견·점표기), stg_fin 골격 키 예외, stg_credit available_date=deal_date, `ovr_shrts_qty_valid` equity 이관, `rcept_no[:8]` 폴백 폐기
> ⑫ 산출물 스키마 3종·실패 처리·게이트 skip 판정 (§2·§9) · SPEC §7·§8 과의 관계 명시 (§10) · 이관 3건 인계 문장 (§0)

## 0. 범위

**포함**: 정형 원장 5 DB(krx·kiwoom·kis·dart·wisereport) 실물 61테이블의 stage 변환.
**제외 (사유 명기)**:
- 문서층 L1 (ZIP 본문 파싱) — 별도 트랙. 구양식 연대별 표본(2010·2013·2016·2020·2024) 검토 선행
- equity.db(조인·정본 선정·조정계수·유니버스·BCNF 분해)·팩터층 — stage 완료 후.
  ERD 초안은 별도 아티팩트(2026-09-01)에 예습으로 존재
- **공개시점 대장 + `dataset_profile`** (소스별 공표 시점 지식·권장 랙) — **일일 증분·equity
  트랙 산출물.** stage 는 사실 날짜만 실으므로 이 지식을 필요로 하지 않는다
- 일일 증분 상세(더티 파티션 판정·워터마크) — 별도 설계. 단 §2 에 실측 제약을 기록해 둔다
- `ws_run_log` — 운영 요약 로그. 61테이블 중 유일한 stage 미편입 (§4 전수 대조표)
- **정정 체인 복원** — `[기재정정]` 공시의 원본 링크는 원장에 없다(ZIP 본문에만 존재,
  실측: disclosure 컬럼 전수 확인). 문서층 L1 의 몫
- **KRX 2026-08-21~ 공백** — 상장·폐지가 이 구간에서 어떤 원장으로도 재구성 불가(실측:
  KIS 폐지 max 08-18 · DART 상장공시 0건). 백필은 일일 증분 때 소급(09-02 사용자 결정).
  그전까지 `universe_asof` 는 이 구간에서 `coverage_gap` 예외를 던진다 (§8)
- **격자 채우기·결측 3분류(`src_omitted → 0 + flag`)** — equity. 캘린더×유니버스 조인이라 stage 가
  할 수 없다. SPEC §5-3 의 레짐 편향 제거 장치는 equity 가 stage 의 `miss_kind`·수집 로그
  (`stg_units_*`·`stg_shards_kiwoom`)를 재료로 구현한다. SPEC D3 의 `miss_kind` 는 이 3분류를 뜻했고
  §3 의 `miss_kind` 는 셀 결측 원인이다 — 이름이 같고 개념이 다르므로 3분류 컬럼은 equity 에서 `fill_kind`
- **공표 랙 판단** — 가격 외 소스(수급·외인·대차·공매도)의 available_date=내용일은 "공표 시점 미상"이지
  "당일 공표 실증"이 아니다(§6 주의). 실제 공표 시점(관행 D+1)과 랙은 공개시점 대장·dataset_profile 이 정한다
- **수집 크론의 배타 제어** — `daily_wise.sh`(06:00 KST) 크론에 `flock` 을 붙이는 것은 수집 운영 몫.
  stage 는 스냅샷·WAL 전환의 실행 창과 락 파일만 정한다(§2)

## 1. 층 계약과 경계 원리

허용 변환 4종(rename·cast·단위 스케일·categorize), 조인·집계·파생·정책 금지 — SPEC §1.

**경계 판정 원리**: **"출력 행은 원장 한 행의 함수다 (1:1)."** 예외는 다섯뿐, 각각 전용 게이트:

| 예외 | 내용 | 게이트 |
|---|---|---|
| (a) 수직결합 | 동일 스키마 UNION (stk+ksq 등) + `_src` 부여. 실측: stk/ksq bydd 17컬럼 완전 동일 | G1 팬아웃 항 |
| (b) 결정론적 접기 | **payload 투영 동일 행만** 접는다 (§5-중복) | G1 dedup 항 · G6 |
| (c) blob 언네스트 | ws_raw JSON·c1010001 HTML → N행. **zlib 해제 → json.loads ×2 (이중 인코딩)** 포함 | G8 파싱 등식 |
| (d) 불변 참조표 룩업 | `corp_code↔ticker`(dart_corp_map — 시각 컬럼 0, 전량 재생성) · **`stg_rcept_dt_map`(rcept_no→rcept_dt, disclosure 유래 — 키당 값 불변)** | G0 선언 대조 |
| (e) 동일 좌표 다중 blob 결합 | cF5001+cF5002 **outer join** (실측: 겹침 32,867 좌표 전수에서 select_item ≡ avg, 불일치 0). UNION 금지 — 같은 키 2행이 됨 | G8 좌표 합집합 등식 |

`price_matches_krx` 컬럼(교차 정본 딱지)은 equity 몫. `price_basis_*` 는 stage — 단 **컬럼 단위**(§4-KIS).
KRX 가격(stg_price_daily)의 기준가/체결가 구분은 별도 컬럼 없이 **`volume=0` 로 판독**한다(SPEC §2-9 규칙의 v2.2 구체화 — 무거래일 종가는 보존).

## 2. 저장·커밋·빌드

- **Parquet** (결정 ①). 원장 SQLite 유지. `.venv` 에 pyarrow·duckdb 설치가 선행 조건(승인 기수령 — 09-02 서버 실측: duckdb 1.5.5 만 있고 **pyarrow 미설치**. sqlite_scanner 확장은 `~/.duckdb/extensions` 로컬 캐시 있음)
- **빌드 입력 동결**: 빌드 직전 `VACUUM INTO` 로 원장 스냅샷 사본을 뜨고 그 위에서만 빌드·게이트.
  근거(실측): 시각 술어(`W_now`)는 스냅샷이 못 된다 — 키움은 런 시작 시각을 전 행에 박아
  늦게 쓴 행이 이른 시각표를 달고, KRX 는 11.3M 행 전부가 3시간 창 안. wisereport.db 는
  WAL 전환(현재 저널 `delete` 실측 — 06:00 데일리 크론과 읽기 충돌 방지). **스냅샷·WAL 전환의 실행 창 = 06:30~익일 05:30 KST**(daily_wise 무동작), 빌드와 크론이 같은 락 파일 `/tmp/quant_ledger_raw.lock` 을 쓴다(크론 쪽 `flock` 부여는 수집 운영 — §0)
- **버전 디렉토리 + manifest 포인터**: `stg_x/v=<빌드ID>/year=YYYY/part.parquet`.
  리더는 `MANIFEST.json`(최상위 인덱스) 경유 필수 — **맨 glob 금지 계약**
- **커밋 입자 = 테이블**: 섀도 버전에 전 파티션 생성 → 게이트 전량 통과 → `MANIFEST.json`
  을 `os.replace` 원자 교체(포인터 1회 — **파일 교체에만 원자적이므로 디렉토리가 아니라 `MANIFEST.json` 을 바꾼다**). 게이트 실패 = 새 버전 폐기, 구 버전 무손.
  tmp 는 `data/stage/_tmp/<빌드ID>/`(테이블 밖) · 구버전 GC keep=3 · 동시 실행 금지 `flock -n`
- **실패 처리**: 게이트 실패 시 `_tmp/<빌드ID>/` 즉시 삭제, 판정은 `data/stage/_failed/<빌드ID>.json`
  (게이트별 pass/fail/skip + reject 키 샘플 경로)에 남기고 stdout 요약. 알림 채널은 운영 몫. 실패 파일은 GC 대상 아님
- **산출물 스키마 3종 + baseline** (v2.2):
  - `MANIFEST.json`(테이블당 1) = {`table`, `current_build`, `keep`:3, `builds`:[{`build_id`, `snapshot_id`,
    `rules_version`, `built_at_utc`, `partitions`:[{`path`, `n_rows`, `bytes`}], `gates`:{G0..G9: `pass`|`fail`|`skip(사유)`}}]}
  - `_meta.json`(파티션당 1) = {`n_rows`, `n_src`, `fanout`, `n_dedup`, `n_reject`, `n_out_of_range`, `src_bytes`,
    `src_mtime`, `snapshot_id`, `rules_version`, `coverage_from`, `version_loss_upstream`, `observed_date_exempt`,
    `rcept_map_miss`, `gates`}
  - reject parquet = `stg_x/v=<빌드ID>/_reject/part.parquet` — 원장 원문 전 컬럼(TEXT 그대로) + `reject_gate` + `reject_reason` + `_src`
  - `data/stage/baseline.json` = 회귀 기준값 (§9). 게이트 상수는 전부 여기서 읽는다
- **`partition_expr` 테이블별 필수 선언 — 클래스 3종** (실측: 관측일 축이 없는 테이블 실재):
  `date_axis`(내용일 연도 — 가격·수급 등) / `receipt_axis`(rcept 연도 — DART. `bsns_year`
  금지: 접수지연 max 2,875일) / `whole`(단일 파티션 — 참조표·소형·관리). **61테이블 전수 선언은 §4-파티션 표**(v2.2 — v2.1 은 KRX 표에만 열이 있었다)
- **일일 증분은 별도 설계**로 이관하되 실측 제약을 여기 기록한다: ⓐ 워터마크 해상도 —
  kiwoom `collected_at` distinct 2(런 스탬프 — 09-02 재측정, v2.1 의 1 은 구수치)·krx 전량 3시간 창 → 시각 워터마크 증분 불성립,
  수집 원장(ingest_log/shard) 기반 더티 판정으로 설계할 것. ⓑ 수집기 2줄 수정
  (kiwoom 행별 stamp·krx tz)은 증분 착수 전 필수 — **풀 빌드에는 스냅샷 동결로 충분**
- **★(성장 중이었던) 테이블 — 통합 목록** (v2.2: SPEC ★=보조원장 6종, v2.1 ★=doc_index·fin_raw 로 교집합 0 이었다):
  `doc_index`(문서 수집 ~09-04) · `dart_fin_raw`(SPEC 의 "08-27 고정"은 실측 반증 — collected_at max 08-30, 15,375,024행) ·
  보조원장 6종(`adt_opinion` 변형 373→434 성장 실측) · `dart_disclosure`(09-02 02:23 스윕이 중복 그룹 129 추가) · DS005 15종(09-01 수집).
  **09-02 daily_dart 제거로 doc_index 외엔 성장이 멈췄다** → 빌드 직전 1회 재측정으로 `baseline.json` 에 고정(§9)

## 3. 공통 골격

| 컬럼 | 내용 |
|---|---|
| `ticker` / `date` | 정규 키. date = 내용일(그 행이 어느 날 이야기인가), ISO DATE |
| **`available_date`** | **사실 날짜만** [결정 ⑥]: 원장에 공개일 사실 컬럼이 실재하면 그것(게시일·결제일·수집일), 없으면 내용일. **랙·보수 버퍼를 더하지 않는다 — 판단은 엔진 설정** (`WHERE available_date <= :asof - :lag`). §6 |
| `available_basis` | `measured`(원장 공개일 실재) / `derived`(참조표 유도) / `default`(내용일 대용 — 가격류는 당일 관측 실증, **그 외는 공표 시점 미상**) / `unknown`(참조표 미스 — available_date NULL, v2.2) |
| **`observed_date`** | 원장 시각의 KST 날짜 — **전 테이블 필수, 원천은 테이블별 `observed_src` 선언**(아래 표). **원장 시각은 전부 UTC 무표기**(서버 TZ=UTC. 수집기가 `time.strftime`·`datetime.now()`·`utcnow()` 를 혼용하지만 전부 UTC) → **+9h 후 DATE**. 실측: KRX `collected_at` 08-23T15:23~18:41 = KST 08-24 — 앞 10자를 자르면 하루 이르다. 재수집 판본의 PIT 선택 축 (equity 가 (available_date, observed_date) 로 판본 선택. 실측: 이것 없이는 재수집 두 판본이 동일 좌표가 되어 선택 불능). upsert 소스(KRX·키움)는 값이 1~3개로 퇴화하지만 컬럼은 유지 |
| `_src` | 원장 출처 (UNION 구분: stk/ksq, v1 등). **시장이전 22종목 실측 — (티커,일자) 단위 속성이지 티커 속성이 아님** (DISTINCT 매핑 조인 시 팬아웃 주의) |
| `_src_flag` | `ok` / `partial` / `parse_failed` — `partial` 은 **캐스팅을 시도했다 실패한 행만** |
| `_cast_fail_cols` | `list<string>` — cast_failed 컬럼명. 전 행 non-null(정상은 빈 리스트 — NULL 혼용 금지) |
| **`miss_kind`** | 컬럼 단위 아님 — 값 없는 셀의 원인 분류를 캐스팅 로직이 기록: `ledger_blank`(원장 `''`) / `ledger_dash`(`'-'`) / `ledger_zero`(테이블별 재판정된 `'0'`) / `ledger_null` / `cast_failed` / **`out_of_range`**(G7 격리 — v2.2). 근거(실측): `bfefrmtrm_amount` 는 원장 NULL 9,880,808+빈값 485,922 = 67.4%가 정상 결측(v2.1 의 64.3% 재측정) — 이걸 실패로 세면 G2 임계가 무의미해짐 |
| `observed_n` | payload 접기로 합쳐진 원장 행수(접기 없음 = 1). 대표 `observed_date` 는 그룹 **min** (§5-중복) |

- **`observed_src` — 원장 테이블별 시각 컬럼** (실측 09-02: `collected_at` 부재 14/61. rules 미선언 = G0 실패):

  | 원천 | 테이블 |
  |---|---|
  | `collected_at` | KRX 8(ingest_log 포함) · 키움 6 · KIS 5 · DART 28 |
  | `fetched_at` | ws_raw · doc_store |
  | `checked_at` | ws_coverage |
  | `copied_at` | v3_* 4종 (미러 시각. 내용 시각은 available_date 가 담당) |
  | `ts` | kis_call_log · kis_ingest_log · dart_call_log · dart ingest_log · ws_call_log |
  | **면제** | dart_corp_map — 시각 컬럼 0. `observed_date` NULL + `_meta.json` `observed_date_exempt=true` |

- **`write_mode` — 원장 테이블별 필수 선언**: `append_only`(KIS·DART — row_hash PK) /
  `upsert`(KRX·키움 — `INSERT OR REPLACE`, 재수집이 과거를 덮음) / `first_write_wins`
  (master_daily — `INSERT OR IGNORE`; v3 미러는 09-02 동결 후 쓰기 없음). 전 판본 보존 게이트(G6)는 append_only
  소스에만 유효 — 나머지는 `_meta.json` 에 `version_loss_upstream=true` 기록
- **temporality 강제 — 2갈래** (실측 교정): ⓐ 재조회로 덮어써지는 단일 상태 = `_current`
  접미사 강제(`corp_cls_current`, kis_stock_info 의 상태 컬럼 전부, ws_coverage) —
  실측: corp_cls='E' 의 26%가 과거 상장사. ⓑ `snap_date`/`fetched_date` 축으로 누적되는
  스냅샷 = `_current` **금지**, `coverage_from` 필수(ka10099 — 하루만 지나면 이름이 거짓이 됨. rules 의
  `TableRule.coverage_from` → `_meta.json`, 09-03)
- 시각류는 KST 날짜로 변환해 DATE 로만 (결정 ③′ — 시각 해상도 폐기)

## 4. 테이블 카탈로그 — 실물 61 전수 대조 (2차 실측 정정 반영)

> **원장 61 = stage 편입 60 + 제외 1(ws_run_log).** 절별: KRX 8→5 · 키움 6→6 · KIS 7→7 ·
> DART 32→30(+참조표 1) · WISE 8→12. G0 가 이 표와 실물의 diff 를 매 빌드 검사하며,
> **rules 의 모든 컬럼 참조는 `pragma_table_info` 선검증 + `expected_len` 검증** (큰따옴표
> 폴백·실재-오답 컬럼 함정 방어 — §11 도구 교훈).

### KRX (실물 8 → stage 5)
| stage | 원장 | 키 | 파티션 | 비고 |
|---|---|---|---|---|
| `stg_price_daily` | stk_bydd + ksq_bydd (UNION — 17컬럼 완전 동일 실측) | ISU_CD,BAS_DD | year | 티커=`ISU_CD`(len 6 전수 — 6"문자", `0001A0` 실재: zfill/int 금지) · `market`=MKT_NM 승격(_src 와 동치 — G3 불변식) · **O/H/L=0→NULL 술어: 원문 문자열 `='0'`, 3컬럼 독립, 거래량 무결합**(실측: 패턴 000/111 뿐 — 동시성을 G3 불변식으로) · OHL=0∧거래량>0 = **125행**(stk 96+ksq 29, 회귀 고정. ETF 2 는 별도 — SPEC 의 127 은 ETF 포함 합계) · 기준가/체결가는 `volume=0` 로 판독(별도 price_basis 컬럼 없음 — §1) · `SECT_TP_NM`: **stk 100% 빈값 / ksq 만 유효**(관리종목 160,632 실측) → 싣되 `sect_available` 불린 병기, 관리종목 필터 재료로 단독 사용 금지 |
| `stg_etf_price_daily` | etf_bydd | ISU_CD,BAS_DD | year | 불변식 상이 분리. OHL=0∧거래량>0 = 2행 |
| `stg_index_daily` | kospi_dd + kosdaq_dd (UNION) | **IDX_CLSS,IDX_NM**,BAS_DD | year | **컬럼 실명 `IDX_NM`(`index_name` 은 유령 — v2 오기, 미조사 테이블에서 발생)** · `IDX_CLSS` 키 필수 — 업종지수명 20개가 양시장 중복((IDX_NM,BAS_DD) 충돌쌍 실측 71,158 — 81,880 은 20×4,094 이론 상한) · 거래일 4,094 정확 일치 · 일당 KOSPI 47.8 + KOSDAQ 37.1 |
| `stg_listing_daily` | stk_isu + ksq_isu (UNION) | ISU_SRT_CD,bas_dd_req | year | 티커=`ISU_SRT_CD`(len 6). **`ISU_CD` 는 12자리 ISIN — 실재하는 오답 컬럼, 티커 사용 절대 금지**(expected_len 게이트) · `PARVAL`→`par_value_krw`+`par_value_kind`(비수치 **73,615** = survey v2 전수 stk 31,487+ksq 42,128 — 09-03 정정, 97,996 은 재현 안 됨. G2 예상 0.80%) · 스냅샷은 **당일 상태**(실측: 삼성 20180504 이 이미 분할 후 — v2 의 "전일 확정치" 반증). 공표 시점 지식(T+1 08:00)은 카탈로그로 · 재상장 실증 2종 → universe_asof 복수 구간 |
| `stg_ingest_krx` | krx.ingest_log | — | whole | 관리 — v2 의 고아 참조("아래") 해소, 정식 편입 |

### 키움 (실물 6 → stage 6)
| stage | 원장 | 키 | 특이 |
|---|---|---|---|
| `stg_flow_daily_kiwoom` | ka10060 | ticker,dt / year | `cur_prc` **abs** · 투자자 13컬럼 keep+×1e6 · `acc_trde_prica`→`volume_shr`(오표기 — 실측 KRX 거래량과 99.9864% 일치) |
| `stg_short_daily_kiwoom` | ka10014 | ticker,dt / year | `close_pric` **abs** · `shrts_trde_prica` **천원 → ×1,000 `_krw`**(SPEC §2-4, v2.1 누락) · `ovr_shrts_qty` 원문 keep — **`ovr_shrts_qty_valid` 는 stage 에서 제거**(77.7% 리셋 판정은 LAG 또는 shard 조인이 필요해 §1 1:1 원리 위반. equity 파생, 재료는 `stg_shards_kiwoom`) · 공매도 0인 날은 원장이 행을 생략한다 — 격자 채우기는 equity(§0) |
| `stg_foreign_daily` | ka10008 | ticker,dt / year | `close_pric` **abs** · `poss_stkcnt` keep(음수 3 회귀) · `wght` **strip_plus**(SPEC §2-3 — v2 의 keep 은 인용 오류. 전수 실측: 비영 7,297,410행 — survey 표본치 [0,0] 은 오류, §11-N5) |
| `stg_lending_daily` | ka20068 | ticker,dt / year | |
| `stg_master_daily` | ka10099 | snap_date,code | **snap_date 축 누적 스냅샷 — `_current` 금지, `coverage_from=2026-09-01`** (§3-temporality ⓑ). auditInfo(관리·정지 상태의 유일한 시계열 재료)·state 분해 |
| `stg_shards_kiwoom` | ingest_shard | 원구조 | shard 단위. **커버리지 한계: collected_at 08-23~24 2일뿐** — 3분류 재료로 쓸 때 명시 |

### KIS (실물 7 → stage 7)
| stage | 원장 | 자연키 (실측) | 특이 |
|---|---|---|---|
| `stg_flow_split_daily` | kis_investor_flow | req_ticker,stck_bsop_date (중복 52,347 — 전건 2행) | 수정주가 포함 — 재수집 시 값 변동, observed_date 가 판본 축 |
| `stg_short_daily_kis` | kis_short_sale | req_ticker,stck_bsop_date (중복 0) | `acml_*` 6컬럼 `acml_valid`(창 첫 행 리셋) |
| `stg_loan_daily_kis` | kis_loan_trans | req_ticker,bsop_date (중복 0) | `rmnd_stcn` 음수 2,691 keep(회귀) |
| `stg_credit_daily` | kis_credit_balance | req_ticker,deal_date (중복 566,795) | date=deal_date · **available_date=deal_date(default) — `stlm_date` 는 컬럼 보존, 공개일이 아니라 결제일(내용 속성. v2.1 의 measured 는 오인, §6)** · `*_amt` **6컬럼**(09-03 정정) unit=unknown — `_krw` 금지 · **가격축 컬럼 단위 실측**: `stck_prpr`=수정종가(÷50 검증) / `stck_oprc·hgpr·lwpr`=**원주가(KRX 와 완전 일치, 5일 전수)** → `price_basis_close='adjusted_asof_collect'` / `price_basis_ohl='raw'` — **행 단위 라벨 금지**(v2 정정). OHL 은 교차검증·조정계수 재료로 사용 가능. 크로스 63.7% 는 유일한 수정주가 컬럼(prpr)만 대조한 결과였음 · 중복 566,795 = **req_d2 만 상이한 순수 재수집**(payload 접기 실증, §5) · `stck_prpr='0'` 561행 |
| `stg_delisted_master` | kis_stock_info | req_ticker | pdno 12→6자리 · **상태 컬럼 전부 `_current` 강제**(admn_item_yn·tr_stop_yn·kospi200_item_yn 등 — 652행 전건 08-26 단일 조회 실측: 폐지 종목은 동결값·생존 종목은 현재값 혼재. kospi200 현재값의 과거 필터 사용 = look-ahead+생존편향) |
| `stg_calls_kis` / `stg_units_kis` | kis_call_log / kis_ingest_log | — | 3분류 재료. 시계 컬럼 실명 `ts` |

### DART (실물 32 → stage 30 + 참조표 1)
| stage | 원장 | 비고 |
|---|---|---|
| **`stg_rcept_dt_map`** | dart_disclosure 유래 (예외 d) | **(rcept_no → rcept_dt) 참조표.** 근거: `rcept_dt` 는 32테이블 중 **5개에만 실재**(disclosure·elestock±v1·majorstock±v1) — 나머지 21개 내용 테이블은 rcept_no 뿐. 접수번호 앞 8자리는 gap 실측 −716~+359일(양수 = look-ahead 방향 3.28%)이라 대용 불가 → **미스 시 available_date=NULL, basis=`unknown`**(v2.1 의 rcept_no[:8] 폴백 폐기 — 같은 줄에서 불가라 하고 폴백으로 쓰던 자기모순). 실측 09-02: dart_fin_raw distinct rcept_no 95,199 전건 disclosure 에 존재(미스 0). 미스율은 `_meta.json` `rcept_map_miss` 에 기록, 0 초과 시 G0 경고. 키당 값 불변(append 만 됨) |
| `stg_fin` | dart_fin_raw | **골격 키 예외: `ticker`·`date` 없음**(corp_code→ticker 는 우선주 1:N — 결합은 equity 의 corp_map 조인. 내용 축은 요청축 (req_bsns_year, req_reprt_code), available_date 는 rcept_dt_map) · `currency` 원문 + `is_krw` 불린(6통화, non-KRW 127,903행·35사 — 환산 금지, SPEC §2-16) · **자연키 8컬럼 실측 확정: (req_corp_code, req_bsns_year, req_reprt_code, req_fs_div, sj_div, account_id, account_detail, ord)** — 15,375,024행 위반 0. account_detail 제외 시 878,156 위반·ord 제외 시 418,902(자본변동표 465만 행 오접힘 위험) · **키는 요청축**(응답축 bsns_year 불일치 120행 — `bsns_year_mismatch` 불린) · `account_detail` 원문 보존(키 구성원 — 변형 금지)+`account_detail_path` list 병기 · 금액 6컬럼 **wide 유지**(1:1 원리 — long 은 1→6 팬아웃) · 기간 라벨 4컬럼(`thstrm_nm` 등 841종)은 §5 문자열 정규화만, 날짜 파싱 금지(비12월 105사) · `account_id='-표준계정코드 미사용-'` 2,741,192행(17.8%) → `account_std=false` 불린, 표준계정 조인 금지 · Decimal(38,4)(**survey v2 전수 확정 09-02**: 금액 6컬럼 정수 max 18(bfefrmtrm 17)·소수 max 2, 비숫자 0) · 파티션=rcept 연도(12파티션 합=행수 검증) · 정기 6종 `stlm_dt`=결산기준일 — **가용일 유도 금지**(KIS `stlm_date`=결제일과 이름 충돌 경고) |
| `stg_dividend`~`stg_audit` 6종 | 정기 보조원장 6종 | se long 유지 · **날짜 포맷 테이블별 파서 선언**: `dart_capital.isu_dcrs_de` = `YYYY.MM.DD` 점표기(180,511행) + `'-'` 결측(8,274행 재측정) — 단일 파서 금지(SPEC §2-14) · 연도 오타 실재(2120·2121·2202·2923, 7행) → G7 행 격리 · `stg_audit.adt_opinion` 원문 보존 + `adt_opinion_class` categorize(§5 정규화 후 **`부적정` 선매칭 → `의견거절`/`한정` → `적정`**, 그 외 `other` — 변형 434종 실측 09-02, SPEC §2-13) · 집계행 `row_kind='aggregate'`(§5) · **자연키 = 요청축 + 구분 컬럼 + `row_hash`(원장 PK)** — 1차 풀 빌드(09-03) G6 실측: 같은 접수번호 안에 구분 컬럼이 같은 복수 행(capital 27·hyslr 13·audit 4·tesstk 4·dividend 2·shares 2). DART 응답 행에는 위치 식별자가 없어 내용 해시가 유일한 행 식별자다 |
| `stg_holder_elestock` / `_majorstock` | elestock(+v1 UNION) / majorstock(+v1) | v1 전용 194행 구제 |
| `stg_disclosure` | dart_disclosure | rm 분해 · `corp_cls_current`·`corp_name_current` · rcept_no 중복 그룹 **618 / 초과 행 620**(09-02 02:46 기준. 489 그룹은 스윕 전 = v2.1 의 491, 129 그룹은 09-02 스윕 추가분. **중복 지표 정의 = 그룹 수**) = 페이지 경계, payload 접기 대상 · `is_correction` 카테고라이즈(술어 `report_nm LIKE '[%정정]%'` — 577,072건=16.75%, 술어 병기) · rcept_dt 포맷 8자리(elestock/majorstock 은 10자리 — 파서 테이블별 선언) |
| `stg_company` | dart_company | `acc_mt`(결산월) · `_current` 계열 · **rcept_no·rcept_dt 없음 — available_date 비부여** |
| `stg_corp_map` | dart_corp_map | 예외 (d). 시각 컬럼 0 — 증분 제외·전량 재생성 |
| `stg_event_*` 15종 | DS005 15종 | 한글 날짜(`YYYY년 MM월 DD일`) **118컬럼 + YYYYMMDD 4컬럼**(piic·pifric 의 `ssl_bgd`·`ssl_edd` 387행 — 09-03 정정: 단일 한글 파서면 두 테이블이 G2 폐기) . 예외: `bnk_mngt_pcbg.mngt_pd` 는 기간표기 `'… ~ …'` 15행 — 텍스트 보존) · `'-'` 결측 마커 실재(cvbd pymd 229행) · **만기일 2053 실재 + 오타 2106(`tsstk_dp_decsn.dpprpd_bgd`, rcept_no 20160108000502) 실재 — G7 은 행 격리(§9)** · 키 rcept_no(유일성 전수 ✓) |
| `stg_doc_index` | doc_store | 메타 인덱스(원장에 blob 없음 — ZIP 은 파일시스템). rcept_no 유일 · ★수집 중 |
| `stg_calls_dart` / `stg_units_dart` | dart_call_log / ingest_log | 시계 컬럼 실명 `ts` — 3분류 재료 |

### WISE (실물 8 → stage 12)
> **ep 실물 어휘 6종 전부**: `c1010001`·`c1050001_data`·`cF3002`·`cF4002`·`cF5001`·`cF5002`.
> **T2Y/T2Q/T4 는 ep 가 아니라 `c1050001_data` 의 `pkey` 값**이다 (v2 는 유령 ep 로 오기 —
> 그대로 구현하면 컨센서스 3테이블의 원장이 존재하지 않아 G0 즉사).

| stage | 원장 (ws_raw) | 키 · 파싱 규칙 |
|---|---|---|
| `stg_consensus_monthly` | ep=cF5001 + cF5002 (예외 e — outer join) | (ticker,fetched_date,target_period,**metric**,obs_month) — metric∈{eps,revenue} 없으면 chart1/chart2 가 같은 키 충돌(실측) · **날짜 라벨로 join — 인덱스 zip 금지**(축 길이 불일치 실측 09-02: chart1 기준 1,402/2,430=57.7%, chart2 기준 2,415/2,430 — v2.1 의 1,740 은 분자 정의 부재. EPS·매출이 한 달 어긋남) · 말미 관측점 중복 71% → 값 동일 검증 후 dedup+카운터 · `obs_label` 원문 보존(비월말 라벨 실재) · 무커버 프로브(cF5001 52.95%)는 **행 생성**(avg=NULL, close_price 실값 보존) — cF5002 빈 배열은 0행. G8 계상 규칙 명문 · zlib(`789C`)+이중 JSON — **양쪽 loads 에 parse_float=Decimal** · 내부 키명 ep 별 상이(select_item vs avg) — 파서 분기 · **v2.1 누락 필드 적재(v2.2)**: cF5001 `target_price`(8,360 blob 중 3,993 실값)·cF5002 `min_max` → `consensus_min`·`consensus_max` · **단위는 데이터 값**: cF5001 `select_item_unit`·cF5002 `item_unit`(EPS '원'·매출 '억원' 실측) → `unit` 컬럼 보존, 스케일 변환 금지 · `metric` 은 `select_item_name`/`item_name` 파싱('EPS'→eps, '매출액'→revenue, 그 외 `parse_failed`) — 실측 키셋: cF5001 {categories, close_price, select_item, select_item_name, select_item_unit, target_price} / cF5002 {avg, categories, item_name, item_unit, min_max} |
| `stg_consensus_annual` / `_quarterly` | ep=c1050001_data, pkey='T2Y' / 'T2Q' | (ticker,fetched_date,**period_label**) — 라벨 원문 `'2022.12(A)'` 이 키, `period`(YYYYMM)·`period_kind`(A/E)는 파서 유도. **실측 09-02**: JsonData 7행/blob(5·6행 10 blob), 라벨 어휘 `9999.99(A|E)` 2종만, MAIN∈{IFRS연결·IFRS별도·GAAP개별}, 빈 blob 2. 값은 WISE 표시 단위(매출·영업이익·순이익 억원, EPS·BPS 원 — T4 `ACC_NM` '매출액(억원)' 실측) 그대로 — 데이터에 단위 컬럼이 없어 접미사·스케일 없음 |
| `stg_consensus_matrix` | ep=c1050001_data, pkey GLOB 'T4:*' | ticker,fetched_date,target_period,acc_cd,lookback · `target_period` YYYYMM 문자열 보존(비12월 202605·202903 실재 — 연도 절삭 금지) · cmp_cd 비숫자 19종(`0004Y0` 등) — TEXT 유지 · **실측 09-02**: JsonData = 계정 9(610100 투자의견·121000 매출·121500 영업이익·122710 순이익·312000 EPS·382000 PER·314000 BPS·382400 PBR·211500 ROE) × `VAL1~5` → 45행/blob. 키 마지막 요소는 **`lookback_idx`**('1'~'5' 원문 인덱스) + `lookback` 라벨(current·1w·1m·3m·1y — v3 revision_compare 의 1w/1m/3m/1y 가 VAL2~5 와 대각 일치 667·674·699·709/1,844쌍, VAL1=현재) · `DT`→`base_date`(WISE 기준일, fetched_date 와 다름) · (cmp,fd)당 T4 blob 정확히 3 · 빈 JsonData 72 blob = 0행 |
| (pkey='') | c1050001_data 목록 호출 | 파싱 입력으로 소비 — 별도 테이블 없음 |
| `stg_analyst_summary` | c1010001 HTML | ticker,fetched_date — `id="cTB15"` 표 마지막 행(투자의견·목표주가(원)·EPS(원)·PER·추정기관수) + `[기준:YYYY.MM.DD]`→`base_date`(date_dot). **실측 09-02 모양 3종**(1,612 blob): 5셀 숫자 / 5셀 중 빈칸(`&nbsp;`·'' → blank) / 단일 셀 '최근N개월 이내에 제시된 의견이 없습니다'(346 → 값 NULL + `no_opinion_note`) · `<script>alert(…)` 리다이렉트 본문 1 blob = 데이터 없음(`n_no_data`, 실패 아님). `PER='N/A'`(EPS 음수 — 98 blob 실측) 는 blank 로 계상(`n_na_cells`). G8 필수. 같은 HTML 의 `cTB24`(제공처별 목표가·투자의견·최종일자 목록)는 카탈로그 밖 — 후속 후보로 등록 |
| `stg_fin_wise` | cF3002/4002 (pkey='Y') | **키 (ticker,fetched_date,ep,seq)** — seq = DATA 배열 위치. 실측 09-02: cF4002 는 같은 ACCODE 가 여러 P_ACCODE 아래 반복(1,614/1,614 blob)이라 ACCODE 는 키가 못 된다(cF3002 는 유일). 1행 = DATA 원소(wide): `val_1~6` ↔ 기간 라벨 `YYMM[0..5]`(`period_label_1~6` 행마다 병기 — 라벨 8 = 기간 6 + '전년대비(YoY)' 2), `val_q1·q2·q4·q5·q6`(라벨이 blob 에 없다 — QOQ/YOY 코멘트가 상대 위치만 말함 → 슬롯명 보존, 해석은 equity), 증감률 6·코멘트 4·POINT_CNT·FIN·FRQ. Decimal(38,6) — 값에 float 잔재(소수 10자리 `2589354.9400000004`) 실재, duckdb 캐스트가 6자리로 반올림 · DATA 행수 cF3002 244(1,552 blob)·cF4002 36, 빈 DATA 2 · 082640(무효 종목)은 cF3002 `DATA: null`·cF4002 `YYMM: []` — 빈 blob(`n_empty`) |
| `stg_v3_revision_daily` 등 v3 4종 | v3_* | 정식 stage(결정 ⑤, **09-02 개정: 2026-04-03~09-02 동결 사본, 증분 없음**). 규칙 분해는 §6 · revision_daily 키 (ticker, date=base_date, target_period), available=`collected_date`(measured) → NULL 1,390행은 `AvailableRule.fallback_column=date`(default) + `coverage_degraded` · opinions 키 (ticker, date=snapshot_date) · annual 키 (sync_date, ticker, period, period_type, data_type) · compare 키 (sync_date, ticker, target_period), `opinion_*` 4컬럼 전행 NULL 실측 |
| `stg_wise_coverage` | ws_coverage | **이력 아님 — 종목당 1행 현재 상태**(실측 2,566행=2,566종목) → `status_current`·`checked_date_current`. 3분류 재료로 쓰려면 수집기를 append 이력으로 바꿔야 — stage 밖 이슈로 등록 · `checked_date_current` = checked_at 의 KST 날짜(observed_date 와 같은 환산) |
| `stg_calls_wise` | ws_call_log | 시계 컬럼 실명 `ts` · 키 (ts, ticker, ep, pkey) — `pkey=''`(목록 호출 6,844행)는 값이라 `blank_is_value` 로 key_missing 에서 제외 |

**v3 편입 계약**(결정 ⑤, 09-02 개정): **동결 사본만.** sync_v3 는 2026-09-02 중단 —
직접 수집 6종이 같은 값을 커버한다(analyst_count 는 c1010001 원문, opinion_score 는 flag=4
매트릭스). v3_* 는 우리 수집 시작 전 구간(2026-04-03~09-02)의 과거분 공급원이며 이후 행이
늘지 않는다(write_mode=first_write_wins 선언은 유지 — 상류 정정은 원장 단계에서 유실됨을
명기). `base_date` 는 내용 기준일 라벨(실측 09-02: 비NULL 61,785행 전수에서 collected=base+1영업일
100%). **annual·compare 는 sync_date 09-01·09-02 두 판본으로 끝.** WISE 자체 수집 6종은
fetched_date 09-01 부터 매일 적립 — 그 이전 구간의 컨센서스는 v3 과거분뿐이다.

### 파티션 선언 — 61테이블 전수 (v2.2)

rules 의 `partition_class`·`partition_expr` 는 이 표와 일치해야 한다(G0). 표현식은 원장 컬럼명 기준 TEXT 앞 4자.

| 클래스 | 표현식 | 테이블 |
|---|---|---|
| `date_axis` | `substr(BAS_DD,1,4)` | stg_price_daily · stg_etf_price_daily · stg_index_daily |
| `date_axis` | `substr(bas_dd_req,1,4)` | stg_listing_daily |
| `date_axis` | `substr(dt,1,4)` | stg_flow_daily_kiwoom · stg_short_daily_kiwoom · stg_foreign_daily · stg_lending_daily |
| `date_axis` | `substr(snap_date,1,4)` | stg_master_daily |
| `date_axis` | `substr(stck_bsop_date,1,4)` / `substr(bsop_date,1,4)` / `substr(deal_date,1,4)` | stg_flow_split_daily · stg_short_daily_kis / stg_loan_daily_kis / stg_credit_daily |
| `date_axis` | `substr(fetched_date,1,4)` | stg_consensus_monthly · _annual · _quarterly · _matrix · stg_analyst_summary · stg_fin_wise |
| `date_axis` | `substr(base_date,1,4)` / `substr(snapshot_date,1,4)` / `substr(sync_date,1,4)` | stg_v3_revision_daily / stg_v3_analyst_opinions / stg_v3_consensus_annual · stg_v3_revision_compare |
| `receipt_axis` | **`substr(rcept_no,1,4)`** (`rcept_dt` 아님 — 136행 상이. 접수번호가 불변 키. stg_fin 실측 12파티션 2015~2026) | stg_fin · 보조원장 6종 · stg_holder_elestock · _majorstock · stg_disclosure · stg_event_* 15종 · stg_doc_index |
| `whole` | — | stg_rcept_dt_map · stg_corp_map · stg_company · stg_delisted_master · stg_wise_coverage · stg_shards_kiwoom · stg_ingest_krx · stg_calls_kis · stg_units_kis · stg_calls_dart · stg_units_dart · stg_calls_wise |

합계 5 + 6 + 7 + 31 + 12 = 61(참조표 stg_rcept_dt_map 포함).

## 5. 값 규칙

- **식별자**: 정규 `ticker` 6문자(숫자 캐스팅·zfill 금지). bydd=`ISU_CD`(len 6),
  isu_base=`ISU_SRT_CD`(len 6, **`ISU_CD` 는 12자 ISIN 함정**), KIS pdno 뒤 6자.
  rules 에 `expected_len` 필수 — G0 이 길이 분포 검증
- **중복 (결정 ④′ — 리뷰 5기 수렴으로 투영 재정의)**: 접기 판정 = **payload 투영 동일**.
  payload = 원장 컬럼 − {`req_*`, `row_hash`, `dup_seq`, `collected_at`} (테이블별 rules 선언.
  단 응답 의미를 바꾸는 요청 파라미터 — 수정주가 플래그류 — 는 payload 에 포함).
  근거(실측): kis_credit 중복 566,795 전형 표본이 전 데이터 컬럼 동일·req_d2 만 상이 —
  "바이트 동일" 기준으로는 접힘 0건이라 ④ 의 목적 자체가 무효였음. payload 상이 =
  전 행 보존, 판본 좌표는 (available_date, observed_date) — **`is_latest`·`observed_seq`
  는 stage 에 두지 않는다**(윈도우 연산 = §1 원리 위반 + 정렬 재료 부재로 비결정 실측).
  **접힌 그룹의 대표 `observed_date` = min**(처음 관측된 날) + `observed_n`(접힌 행수) — v2.2.
  max 를 쓰면 "08-25 에 알던 값"이 08-30 뒤로 밀려 PIT 결측을 만들고, 미지정이면 빌드마다 비결정.
  실측 09-02: kis_credit 중복 566,795 = payload 26컬럼 동일 그룹 517,648/517,648, 접기 후 잔여 0
- **문자열 정규화 — 텍스트 컬럼만**: NFKC → 개행·탭 제거 → 연속공백 축약 → strip. **태그 제거는 컬럼별 옵트인(`strip_tags`)** —
  WISE 라벨의 `<br />` 류만. DART 서술 컬럼의 `<주1>` 은 각주(내용)라 무차별 제거 = silent 손실(09-03 5단계 리뷰 정정).
  **식별자·조인 키(account_id·corp_code·ticker·ISU_*)와 숫자 컬럼에는 적용 금지** —
  실측: 숫자 컬럼 1,607만 행에서 전각·유니코드 마이너스 0건(방어는 게이트로 —
  비ASCII 검출 시 실패), 반면 account_id 는 17.8%가 한글 센티널이라 무차별 NFKC 는 키 변형 위험
- **숫자**: 콤마 제거 → 부호 정책 → **Decimal**. 컬럼별 (p,s) 필수 — **survey v2 가 유도
  필드(max_int_digits·max_scale·has_comma·has_sign)를 제공하는 것이 선행 조건. survey v2 는
  전수 스캔 + 정규식 어휘 측정만, 캐스트 금지**(v2.2). 근거: 현 survey 는 rowid stride 12만 행 표본
  (fin_raw 의 0.73%) + `float()` 캐스트 — (p,s)는 max 통계라 표본 유도는 반드시 과소.
  실증 `whol_loan_gvrt` 표본 범위 −292~+333 vs 전수 −594.76~+1120.92, 범위 밖 259행. float 경유는
  2^53 초과 366~380행의 자릿수 손실. 어휘 측정이면 G2 임계 ← survey ← 캐스팅 규칙의 순환이 끊긴다.
  오버플로 = 게이트 즉사. JSON 은 `parse_float=Decimal, parse_int=Decimal` — **이중 인코딩
  블록은 안쪽 loads 에도** (§4-WISE)
- **부호 정책 3값** {abs, strip_plus, keep} — (table,column) 키. abs 실측 3컬럼(§4 키움).
  abs 컬럼은 원부호 `*_dir` 보존. 음수 회귀 고정: poss_stkcnt 3 · rmnd_stcn 2,691 ·
  whol_loan_gvrt 539 · **whol_stln_gvrt 1 (신규 고정)**
- **비율**: `_pct` / `_ratio` 접미사. 실측 범위 주의: whol_loan_gvrt **−594.76~+1120.92**(전수 09-02 — v2.1 의 −292~+333 은 survey 표본치. 0~100 가정 금지)
- **결측**: 값 NULL + `miss_kind` (§3). 마커 실측: `''`(DART 5.8%) · `'-'`(tesstk 80.8%,
  DS005 날짜 컬럼에도 실재) · `'0'`(KIS 만 결측 표현 — 테이블별 재판정. **리터럴은 `'0'`·`'0.00'`(loan stck_prpr 281행)·
  `'00000000'`(stock_info 날짜 168셀) 전부** — 09-03 5단계 리뷰 K1) · 집계행
  (`'합계'`·`'계'`·`'총계'`·`'소계'`(09-03 tesstk 실측 추가))은 `row_kind='aggregate'` 카테고라이즈(실패 아님)
- **신뢰 불가 값**: "싣는다 + 기계 판독 플래그" 단일 처방 (각주·미적재 금지)

## 6. available_date — 사실 날짜만 [결정 ⑥·⑦, 2026-09-02 사용자 확정]

> v2 의 클래스표("+1거래일"·"T+2" 등)는 **판단을 사실인 척 저장**하는 설계였고, 입자
> 오류(동일 종가가 테이블 따라 D/D+1/D+2 — 실측)까지 낳았다. v2.1: **stage 는 사실만.**
> 공개 시점 지식(수급 익일 공표 등)은 공개시점 대장→`dataset_profile`(equity)로,
> 랙 판단은 엔진 설정으로. 이로써 "+N거래일" 계산이 소멸 — 캘린더 순환 의존도 함께 해소.

| 원장의 공개일 사실 | available_date | basis | 해당 |
|---|---|---|---|
| 없음 → 내용일 대용 | = `date` (내용일 그대로. 가격은 당일 실시간 관측 실증 — 교차 100%) | default (가격류는 measured 급 실증이나 라벨 통일) | 가격·지수·ETF·마스터·수급·외인·대차·공매도(거래 데이터 — **v2 의 "잔고 T+2" 클래스는 삭제: 양 테이블 전 컬럼 실측 결과 잔고 컬럼 0개**, 진짜 잔고는 SPEC §4 취득 불가) |
| 결제일만 실재(공개일 아님) | = `date`(=deal_date). `stlm_date`(매매일+2~12일, 위반 0)는 컬럼 보존 | default | stg_credit — v2.1 의 "stlm_date, measured" 는 결제일을 공개 사실로 오인(SPEC §2-8 도 "유도"라 했지 등치가 아님). 같은 행의 OHL 은 KRX 원주가 완전 일치라 deal_date 에 가용 — 행 단위 라벨이 컬럼 사실을 덮었다 |
| 게시일 — 참조표 유도 | = `stg_rcept_dt_map[rcept_no]` (그대로 — max() 보정 등 판단 금지). **미스 시 NULL**(rcept_no[:8] 폴백 폐기 — gap 양수 3.28%, 최대 +359일 look-ahead 방향. 실측 09-02 미스 0) | derived / unknown | DART 내용 21테이블 + rcept_dt 보유 5테이블(직접, measured) |
| 수집일 실재 | = `fetched_date` (WISE — 06:00 수집이라는 지식은 카탈로그로) / `collected_date`(v3 revision — NULL 1,390행은 base_date, basis=default + `coverage_degraded` 불린. 실측 collected=base+1영업일 100% 이므로 default 는 "사실 없음"이지 "당일 가용"이 아니다 — +1영업일은 dataset_profile 이 적용: **실측 시리즈 최초 4거래일 100% 집중, 04-06·07 은 커버 15%·11% 붕괴**) / `snapshot_date`(v3 opinions) / `sync_date`(v3 annual·compare) | measured | WISE 6종 · v3 4종(4행 분해 — v2 의 "4종 일괄" 은 3종에 collected_date 부재로 불성립 실측) |
| 비부여 | — | — | calls·units·shards·doc_index·coverage·corp_map·company·**delisted_master**(현재 상태 스냅샷, 내용일 없음 — 09-03) — 컬럼은 생성하되 NULL, basis NULL (parquet 스키마 통일) |

> **주의 (v2.2 이관 3 의 인계)**: 위 표의 `default` 는 두 부류다. 가격·지수·ETF 는 "당일 실시간 관측 실증"이고,
> 수급·외인·대차·공매도·마스터는 **"공표 시점 미상"**이다. 실제 공표(관행 D+1)와 랙은 dataset_profile 의
> 몫이며 stage 는 여기서 판단하지 않는다(결정 ⑥). rules 는 테이블별 `lag_known: true|false` 를 선언해
> `_meta.json` 에 실어 equity 가 lag 0 을 무심코 적용하지 못하게 한다 — 이것이 stage 에 남는 최소 장치.

## 7. 정정·판본

- stage 는 **전 행 보존**, 판본 좌표 = (available_date, observed_date). 선택(pit/latest)은
  equity — `is_latest` 없음 (§5)
- `vintage_kind`: `original` / `restated_unknown`(fnlttSinglAcntAll 전량 — **재무의 원본
  판본은 원리적으로 부재**: 다중 rcept_no 유닛 0 실측 + 표본 3/3 이 [기재정정]. §7 은
  재무에서 무동작임을 명기) / `v3_mirror`
- **소급 재작성 소스(WISE 13개월 창)의 PIT 판본 = 같은 (ticker,obs) 점의 `min(fetched_date)`
  행** — equity 규칙으로 지식 이관. 최신 행 선택은 재작성 컨센서스의 과거 주입 = 리비전
  팩터 무효화(방향이 곧 알파라서)
- 정정 존재의 기계 판독: `stg_disclosure.is_correction` (§4). 체인 복원은 범위 밖 (§0)

## 8. 마스터 사용 규칙

과거=KRX 스냅샷(pit) · 현재=키움 ka10099 · 폐지일=KIS(정본). `universe_asof(date)` 는
재적 "복수 구간"(재상장 실증 2종) + **2026-08-21 ≤ date < 백필일 구간은 `coverage_gap`
예외**(조용한 08-20 연장 금지 — §0). **확정 한계 2건**(SPEC §4 등록): ⓐ 관리종목·거래정지
과거 시계열 전 카탈로그 부재(SECT_TP_NM stk 전건 빈값 실측 — §11 미결 해소. 09-01부터
ka10099 로 적립) ⓑ 08-21~31 상장·폐지 재구성 불가.

## 9. 게이트 — 10종. 테이블 폐기형과 행 격리형을 구분한다 (v2.2)

**폐기형**(실패 = 새 버전 폐기): G0·G1·G3·G4·G5·G6·G8·G9. **행 격리형**(범위 밖 행을 NULL+`miss_kind`+reject 로 격리, 비율 임계 초과 시에만 테이블 실패): G2·G7.
**실행 불가 판정**: 조건이 안 되는 게이트는 `skip(사유)` 로 MANIFEST 에 기록하고 폐기하지 않는다 — G5 첫 빌드 `skip(no_baseline)` · G6 는 upsert/first_write_wins 소스 `skip(write_mode)` **및 판본 개념이 없는 로그 테이블(콜·유닛 — rules `versioned=False`) `skip(unversioned)`(09-03)** · G8 은 blob 테이블만 · G9 는 원장 직접 대조라 항상 실행.

| # | 게이트 | 내용 |
|---|---|---|
| G0 | 선언 대조 | 원장 실물 vs rules diff=0 + **pragma 컬럼 실재 + expected_len 길이 분포** |
| G1 | 행수 등식 | `stage = Σ(원장ᵢ×fanoutᵢ) − dedup(payload동일) − reject`. 각 항 독립 산출, 실패 시 reject parquet+키 샘플. **fanout 은 rules 필수 필드**(1:1·UNION 테이블 = 1. blob 언네스트 테이블은 fanout 미정의 — **G8 이 G1 을 대체**) |
| G2 | 손실 임계 | **`miss_kind='cast_failed'` 만** 카운트 (원장 정상 결측 제외 — 67% 오염 방지). 임계 = survey v2 실측(어휘 측정 기반 — §5). 행 격리형 |
| G3 | 불변식 | stage 산출물에 duckdb DECIMAL 재작성 실행 (원장 문법·REAL 금지). **`key_unique` 는 upsert·first_write_wins·참조표(rcept_dt_map·corp_map)에만** — append_only 내용 테이블은 재수집 판본이 같은 키로 공존하므로(§7) 유일성은 (키, observed_date) 축의 G6 이 본다(09-03 5단계 리뷰 DEFECT-E1) |
| G4 | 골든 픽스처 | 불변형/시변형 분리. 정정 반영: 회귀 **125**(price)+**2**(etf) · 정지행 125(OHL=0∧거래량>0 — DEFECT-001 계열) · 원주가 2,650,000 · abs 금지 3행 · **unit≠1 선언 전 컬럼에 픽스처 강제**(×1e6 오적용 = SPEC 최대 결함 클래스의 유일 방어). **픽스처 파일** = `data/stage/fixtures/<table>.json` [{`key`, `column`, `expect`, `measured_sql`, `measured_at`}] — 강제 주체는 `gates.py`(unit≠1 컬럼에 픽스처 없음 = G4 실패). WISE 처럼 단위가 데이터 값인 테이블은 `unit` 컬럼 분포 픽스처 |
| G5 | 회귀 Δ등식 | `Δstage = Δn_src×fanout − Δdedup − Δreject` — 설명 안 되는 증감 실패 (v2 의 "증가 허용"은 payload 접기 하에서 탐지력 상실 — 정정). 기준 = MANIFEST `current_build` 의 `_meta.json`, **첫 빌드는 `skip(no_baseline)`** · 스냅샷 콘텐츠 해시(`src_bytes`·`src_mtime`)도 비교해 upsert 소스의 값 변경(n_src 불변)을 잡는다 |
| G6 | 판본 보존 | (자연키, observed_date) 유일성 위반 0 + **자연키 중복 중 payload 동일 비율 기록**(임계 초과 = 재수집 잡음 과다) — **append_only 소스에만** (§3 write_mode) |
| G7 | 범위 — **행 격리형** | 범위 밖 값은 NULL + `miss_kind='out_of_range'` + reject 계상(원문 보존). 테이블 실패는 격리 비율 > 임계(기본 0.1%, survey v2 후 확정)일 때만. 축: **관측일**(BAS_DD·dt·deal_date·rcept) 연도 **[1999(DART 전자공시 최초 연도 — 접수번호 `19990403000009` 실재), 현재+1]**(09-03 정정: 하한 2000 은 1999 공시를 행째 reject 했다) / **내용일**(만기·상환·증감자일·상장일 — pymd·*_edd·isu_dcrs_de·LIST_DD) 연도 **[1956(KRX 개장), 현재+40]**(09-03 정정: 하한 1990 은 삼성전자 상장일 19750611 을 격리했다) · 부호 교차 · 비율 범위. 실측: CB 만기 2053 정상 · **오타 2106(tsstk_dp)·2120~2923(dart_capital 7행) 실재** — v2.1 의 테이블 폐기형은 두 테이블을 영구 빌드 불가로 만들었다(v2 가 축 분리로 잡았다던 사고 클래스의 상한값 재발) |
| G8 | 파싱 등식 | blob=parse_log ∧ Σn_rows=stage — **ep 별 분리 산출** + 조인 테이블은 좌표 합집합 등식 (예외 e) · coverage 급락 감지(v3 일별 행수 < 직전 중앙값 50% 플래그) |
| G9 | **교차 소스 회귀** | **원장 직접 대조**(stage 간 아님 — S1 단독 실행 가능): (stk_bydd ∪ ksq_bydd) ⋈ ka10060 ON (ISU_CD=ticker, BAS_DD=dt), `CAST(TDD_CLSPRC AS INT) = abs(CAST(cur_prc AS INT))` **7,537,984행 100.0000%**(v2.1 의 2,864,871 은 survey_cross 조인 수 — 09-02 재측정) · 거래량 `ACC_TRDVOL = acc_trde_prica` 99.9864% · `MKTCAP = TDD_CLSPRC × LIST_SHRS` 위반 0 — 기준값은 baseline.json, 저하 = 실패 |

**회귀 baseline** (`data/stage/baseline.json`, v2.2): [{`table`, `metric`, `sql`, `value`, `measured_at`, `growing`(★)}].
게이트 상수는 전부 여기서 읽는다 — **코드 하드코딩 금지**. 상수마다 조인 키·술어를 `sql` 에 병기해야 재현 가능하다
(v2.1 의 상수 3개 — G9 2,864,871행, disclosure 중복 491, whol_loan_gvrt 범위 — 가 술어 없이 적혀 지금 원장과 달랐다).
★ 목록은 §2 통합 목록. 갱신은 diff 를 커밋 메시지에 남기고 사람이 승인한다. "게이트가 깨졌다"와 "원장이 자랐다"는
`baseline.json` 의 `measured_at` 과 `_meta.json` 의 `src_mtime` 으로 가른다.

## 10. 빌더

**SPEC §7·§7-1·§8 과의 관계 (v2.2 명시)**: SPEC 의 구현 순서(krx→kis→kiwoom→dart)·기존 코드 판정·착수 전 필수
수정 6건은 **sqlite `build_stage.py` 경로 기준**이다. v2.2 의 duckdb→parquet 빌더가 그 경로를 대체하므로 §8-1~4 는
소멸하고, **§8-5(손실 카운터)·§8-6(폐기 행 계상)의 원칙만 G1·G2 로 승계**한다. `build_stage.py`·`build_panel.py`·
`build_views.py` 는 파일럿으로 남기고 수정하지 않는다. `fin_map.py`(매핑 사전)·`build_bridge.py`(corp_ticker,
isin8 추가)·`finalize.py` 의 `asof(year, acc_mt)` 로직만 재사용한다. 슬라이스 순서는 아래 하나다.

```
stage/rules.py    선언 — 테이블별 {소스, 자연키, fanout, partition_class, partition_expr, observed_src,
                  dedup=payload_cols, write_mode, lag_known, 컬럼맵[원명→정규명, expected_len, (p,s),
                  sign_policy, unit|unit_src, temporality, miss 마커, date_format]}
stage/build.py    duckdb 주엔진: ATTACH(READ_ONLY, 스냅샷 사본) → CAST DECIMAL → COPY TO parquet
                  memory_limit 6GB · threads 3 · temp_directory data/stage/_tmp/spill
                  파이썬 보조 단계(듀얼 불가 변환): 한글날짜 · 파이프분해 · zlib+이중 JSON ·
                  **NFKC = pyarrow pc.utf8_normalize 벡터화** (duckdb 에 NFKC 부재 실측 —
                  v2 "인엔진 고정" 문구 폐기. 벤치: 벡터화 48초 vs 파이썬 루프 ~10분)
stage/gates.py    G0~G9 · stg_parse_log · 원장 인덱스 부재(PK 오토인덱스뿐 실측) → 전량
                  스캔 전제로 벤치 산정
```
착수 전 벤치 = S1 풀 테이블(**다중 파티션 COPY 경로 포함** — 1개 연도만 재면 정작 위험한 경로를 안 잰다). 경과·최대 RSS 가
추정과 1.3배 이상 벌어지면 재검토(v2.1 의 "2배"는 행수 추정 자체의 +26% 오차를 통과시켰다).
자원 실측 09-02: 원장 전 테이블 **80,639,641행**(SPEC 의 64M 은 isu_base 9.2M·지수·WISE 누락) · 17.2GiB · 서버 CPU 4 ·
RAM 15GB(available 13) · 디스크 여유 334GB. 벤치: KRX 3.77M행 TRY_CAST DECIMAL(38,4) 실패 0 · 123초 · RSS 70MB /
fin_raw 15.4M행 8키 GROUP BY 9초 · RSS 3.6GB → 6GB·3threads 성립. 풀 빌드 추정은 행수 비례 ≈ 2.6시간, S1 벤치 후 확정.
슬라이스: S1 `stg_price_daily` → **S1b `stg_rcept_dt_map`**(S2 의 available_date 가 의존) → S2 `stg_fin` → S3 `stg_consensus_monthly` → 잔여 확장.

**착수 선행 조건 체크리스트**:
1. ~~`.venv` pyarrow 설치~~ → **S1 실측으로 불필요해짐**: NFKC 는 텍스트 컬럼의 distinct 값만 파이썬
   `unicodedata` 로 정규화해 임시 매핑표 조인(KRX 3컬럼 수천 값, ms 단위). parquet 읽기·쓰기는 duckdb 네이티브.
   pyarrow 는 arrow/pandas 산출이 필요해질 때 설치
2. ✅ **survey v2 완료 (09-02 16:28 KST, 1,617초, 61테이블·1,427컬럼, 커버리지 0/0 미조사)** — `survey/survey_v2.py`,
   산출 `survey_out/v2/`(테이블별 JSON·`ps_table.json`·`coverage.json`). 알려진 실측 13/13 재현(음수 회귀 4건·PARVAL 73,615·
   fin_raw 15,375,024행 등). 재무 금액 6컬럼 전수 (p,s) = **정수 18(bfefrmtrm 17)·소수 2 → Decimal(38,4) 확정**.
   kind 분포: numeric 707 · text 501 · date_korean 118 · date_yyyymmdd 57 · date_iso 16 · date_dot 1(isu_dcrs_de) · empty 27.
   dart_capital `isu_dcrs_de` 점표기 275,205 + `'-'` 8,274 = 283,479 전행(SPEC 180,511 은 08-28 백필 중 측정치).
   원 요구: 전수 스캔·정규식 어휘 측정·캐스트 금지(§5)로 (p,s) 유도 필드 추가 + **미조사 16테이블 커버**
   (krx ingest_log·kospi_dd·kosdaq_dd / kiwoom ingest_shard / kis call_log·ingest_log / dart call_log·corp_map·
   elestock_v1·majorstock_v1·doc_store·ingest_log / wise v3_revision_compare·ws_call_log·**ws_raw**·ws_run_log).
   **ws_raw 최우선** — WISE stage 12테이블의 유일 입력인데 조사 0. 체크포인트 JSON 45개 삭제 후 재실행.
   `targets.py` 를 `sqlite_master` 와 diff 해 "미조사 0개"를 기계 판정
3. `VACUUM INTO` 스냅샷 절차 + wisereport.db WAL 전환 — 실행 창 06:30~익일 05:30 KST, 락 파일 공유(§2)
4. ★테이블 재측정 → `baseline.json` 생성(§9). 게이트 상수마다 조인 키·술어 병기
5. (일일 증분 전까지만) 수집기 2줄 수정 유예 가능 — 풀 빌드는 스냅샷으로 충분

**S1 완료 (09-02 17:56 KST) — `src/stage/` 빌더 뼈대 + `stg_price_daily` 서버 실측**:
- 스냅샷 `VACUUM INTO` krx 4.01GB + kiwoom 3.26GB = 61초. 빌드 **9,201,516행 59초**(게이트 포함, 재현성 재빌드 58.8초),
  최대 RSS **6.35GB**(memory_limit 6GB + 파이썬), threads 3. 산출 17 연도 파티션(2010~2026) **239MB**/빌드.
- 게이트: G0·G1·G2·G3·G4(픽스처 4)·G7 pass, G9 pass — **KRX⋈키움 ka10060 7,537,984행 종가 100.0000% · 거래량 99.98642%**,
  G5 는 첫 빌드 skip(no_baseline) → 두 번째 빌드 Δ=0 pass, G6·G8 skip(사유 기록). 첫 시도는 G4 만 실패했다 — 픽스처의
  NULL 기대값을 문자열 'None' 으로 적은 코드 결함이라 테스트 추가 후 수정(JSON null = SQL NULL).
- **재현성**: 같은 스냅샷 재빌드 content_hash `9201516:a111951402930d93` 동일. MANIFEST `current_build` 교체·keep=3 GC 동작.
- 실측 확인: 삼성전자 2018-05-03 은 O/H/L 이 '0'(분할 전 정지일) → NULL + `miss_kind.open_krw='ledger_zero'`,
  종가 2,650,000 보존. dedup 0·reject 0·cast_failed 0 — KRX 는 survey v2 예측대로 캐스팅 손실이 없다.
- 읽는 쪽 주의: `read_parquet(..., hive_partitioning=true)` 로 `v=<build_id>/year=YYYY/` 를 읽으면 `v`·`year`
  하이브 컬럼이 붙는다(파일 안에는 없음). 리더는 MANIFEST 의 `partitions[].path` 를 경유한다.
- 구현 메모: duckdb 식별자는 대소문자 무시라 원장 `LIST_SHRS` 와 stage `list_shrs` 가 충돌 — 원장 컬럼은 `raw__` 접두로
  분리. 골든 픽스처는 `src/stage/fixtures/<table>.json` 으로 코드와 함께 산다(data/ 아님). 임계 기본값 G2 0 · G7 0.1% · G9 종가 1.0.

**S1b·S2 완료 (09-02 21:03 KST) — `stg_rcept_dt_map` + `stg_fin` 서버 실측** (스냅샷 dart.db 7.07GB VACUUM INTO ≈ 80초):
- `stg_rcept_dt_map`(whole, `v=<id>/part0.parquet` 31MB): disclosure 3,444,518행 → **3,443,898행, dedup 620** — 페이지 경계 중복
  620행(=그룹 618)이 (rcept_no, rcept_dt) payload 투영에서 정확히 접혔다. `key_uniqueness_violations` 0(rcept_no 당 rcept_dt 충돌 0 실측 재확인),
  G6 pass(append_only), 빌드 8초, 재현성 해시 동일.
- `stg_fin`(receipt_axis 12파티션 2015~2026, 295MB/빌드): **15,375,024행 677초**(1차) / 908초(재빌드, 스필 경합), RSS 6.86GB,
  **duckdb 스필 15~18GB**. dedup 0·reject 0·cast_failed 0. `rcept_map_miss` **0** → available_date 전건 `derived`(참조표 폴백 미발동).
  실측 재현: bsns_year 불일치 **120** · 표준계정 미사용 **2,741,192** · 비KRW **132,355**(survey v2 와 일치, SPEC 127,903 은 08-27 치) ·
  thstrm 빈값 867,749. G4 픽스처 2/2(삼성전자 FY2024 연결 매출 300,870,903,000,000.0000 · available_date 2025-03-11).
  두 번째 빌드 G5 Δ=0 pass, 재현성 해시 `15375024:692f16acca50779a` 동일.
- **성능 후속(§10 미결 추가)**: stg_fin 은 `stage_all` 임시 테이블이 원장 28컬럼(`raw__`)을 윈도우 함수까지 끌고 가 스필이 크다.
  개선안 — raw 컬럼은 payload_hash 계산 후 reject 출력용 키만 남기거나, rn 계산을 (키, payload_hash) 투영으로 분리해 조인.
  잔여 DART 28테이블은 소형이라 영향 없고, 풀 빌드 추정에는 fin 15분을 반영한다.
- 빌더 일반화: `partition_class=whole`(단일 parquet + `_meta.json` 1개), `AvailableRule`(column/lookup/none — lookup 은 참조 stage
  테이블의 MANIFEST current_build 를 읽고 미빌드면 즉시 예외), `ExtraColumn`(같은 행 categorize), `required`(비키 NULL = reject),
  `payload_columns` 명시 투영, `key_unique`(G3 집계). reject 사유 어휘: `key_cast_failed` · `key_missing` · `required_null` · `out_of_range`.

**S3 완료 (09-02 23:10 KST) — `stg_consensus_monthly` 서버 실측** (첫 blob 테이블, §1 예외 c·e):
- ws_raw cF5001 8,360 + cF5002 4,842 blob → **222,499행 5.9초**, RSS 665MB, 재현성 해시 동일. 파티션 `year=2026` 1개(수집 09-01·09-02).
- G8 파싱 등식 pass: 셀 5001 216,978 + 5002 74,030 → 좌표 합집합 222,499 · **말미 라벨 중복 접기 1,732**(값 전부 동일) ·
  **5001≡5002 불일치 0** · parse_failed 0 · 항목명 미상 1,856(전부 cF5002 빈 chart — 행 0, 설계 §4 실측치와 일치).
- 좌표 분포: 5001만 148,469(무커버 프로브 + 5002 축 밖 라벨) · 양쪽 68,509 · 5002만 5,521. metric eps 111,384 / revenue 111,115,
  단위 '원'/'억원' 데이터 값 그대로. 컨센서스 NULL 148,081(무커버) · 목표주가 NULL 140,051.
- 리비전 실증: 삼성전자 202612 EPS 2026/08/31 관측점이 수집일 09-01 48,338.64 → 09-02 48,139.28, 목표주가 493,958 → 491,875 —
  (ticker, fetched_date, target_period, metric, obs_label) 키가 일별 리비전 축을 그대로 보존한다.
- **구현 교훈(§11 도구 교훈 ⑥)**: 파서 출력 34만 행을 duckdb `executemany` 로 넣으면 행마다 statement 를 돌려 5분+ 무응답 —
  JSON Lines 파일 → `read_json(columns=VARCHAR…)` 한 번으로 6초. JSON null = SQL NULL 이라 ''/NULL 구분도 보존.
  blob 소스 계약: `BlobSource(db, table, eps, parser)` + `parsers.PARSERS`; 원장 실물 계약(G0)은 파서 입력 컬럼 6개.
- 카탈로그 정정(§4 WISE): `stg_consensus_monthly` 키의 마지막 요소는 `obs_month` 가 아니라 **`obs_label`(원문 라벨)** 이고
  `obs_date`(DATE) 는 파생 비키 컬럼. 컬럼 종류 `date_iso`(fetched_date)·`date_slash`(라벨)·`bool` 추가, available_basis `measured`.

**S4 구현 (09-03 로컬 TDD, 서버 실측은 5단계 풀 빌드에서) — WISE 잔여 11테이블** (`rules_wise.py` 12테이블 완성):
- blob 구조 실측(09-02, 읽기 전용 스크립트): c1050001_data 는 zlib+JSON 한 겹 `{JsonData:[…]}`(T2Y/T2Q 7행, T4 계정 9×VAL5, pkey='' 목록 4행) · cF3002/4002 `{YYMM[8], DATA[244|36], FIN, FRQ}` · c1010001 zlib+HTML 85KB(`cTB15` 요약표 + `cTB24` 제공처별 표). 파서 4종 추가(`parse_consensus_annual/_quarterly/_matrix`, `parse_fin_wise`, `parse_analyst_summary`) — 같은 ep 의 다른 pkey 는 `n_skipped_pkey`, 빈 배열은 `n_empty`, alert 리다이렉트는 `n_no_data` 로 세고 실패로 치지 않는다(G8 parse_failed=0 유지).
- 빌더 확장 3: `AvailableRule.fallback_column`(§6 v3 revision) · `ColumnRule.blank_is_value`(키 '' 인정) · 캐스팅 대상 컬럼 0개 테이블(ws_coverage)의 `miss_kind` = 자리표시 필드 하나의 NULL STRUCT(빈 `struct_pack()` 은 duckdb 오류).
- 테스트 18(파서 4·선언 2·빌드 12) 추가, 전체 75 passed. 골든 픽스처는 unit_scale 컬럼이 없어 강제 대상 없음 — 서버 빌드 때 삼성전자 EPS·목표주가·추정기관수 22 를 회귀 고정값으로 추가 예정.

**5단계 완료 (09-03 로컬) — 61테이블 전수 선언, 서버 실측은 6단계**:
- 소스별 병렬: KRX 4+키움 6(PR #17) · KIS 7(#19) · DART 본체 14(#21) · DS005 이벤트 15(#16, `rules_dart_events.py`) · WISE 11(#18, 직접). 레지스트리 `rules.py` 6모듈 = 61.
  테스트 187(파서·선언·손계산 빌드), ruff·pyright 0. 리뷰 감사 스크립트로 §3 observed_src·§4 파티션 표·write_mode·키·expected_len·§6 available 을 기계 대조(불일치 0).
- 리뷰가 잡은 빌더 결함 → PR #20·#22·#23: 정규화의 `<주1>` 각주 삭제(→ `strip_tags` 옵트인) · 내용일 하한 1990 이 상장일 격리(→ 1956) · 관측일 하한 2000 이 1999 공시 reject(→ 1999) ·
  `'0'` 마커가 `'0.00'`·`'00000000'` 미인식(→ 정규식) · 로그 테이블의 G6(→ `versioned=False`) · `coverage_from` 미기록 · 정규화 매핑 executemany · 전 TEXT 테이블의 빈 struct_pack.
- 선언 결정: append_only 내용 테이블은 `key_unique=False`(§9 G3 갱신) · 콜/유닛 로그의 요청축(d1·d2·corp_code)은 빈값이 실재라 비키·expected_len 없음 · `dart_company.est_dt` 는 1956 이전 설립일이 실재해 TEXT 보존 ·
  단위 미측정으로 접미사 보류: 키움 `shrts_avg_pric`·`lastPrice`·ka20068 `dbrt_trde_*`·`rmnd`·ka10008 `frgnr_limit*`, KIS `frgn_reg/nreg_ntby_pbmn`, DART `df_amt`.
- **6단계 착수 전 서버 측정 목록**: ① unit_scale 29컬럼 골든 픽스처(키움 flow 13·short 1·lending 1, KIS flow_split 13·loan 1) ② 키 유일성 — ETF·지수·상장(KRX 원장 PK 가 다른 축), DS005 15종 rcept_no, delisted_master req_ticker
  ③ `ka20068` `irds = cntrcnt − rpy` 위반 수 ④ G2 임계 후보: listing 0.80% · shares **11.44%**(etc 3,316 등 비숫자) · dividend 0.037% · hyslr `'#######'` 9행 · credit `prdy_ctrt` 465행 · delisted_master(K1 후 재측정) ⑤ G7: delisted_master `mfnd_end_dt` 비-(19|20) 5행.
- 후속 후보(카탈로그 밖): c1010001 `cTB24` 제공처별 목표가 표 · G4 로 못 세는 카운트 회귀(ETF 정지행 2)는 baseline.json · v3 `induty_code` 등 코드 컬럼 정규화 해제.

**6단계 풀 빌드 (09-03 KST 00:46~) — 61테이블 서버 실측** (`scripts/run_stage_all.sh`, 스냅샷 `snap_20260902T154207Z` = 5 DB 17GB, VACUUM INTO 약 6분):
- **1차 패스**: 61테이블 1,368초(23분), 산출 3.0GB. **53 ok / 8 gate_failed** → 원인 실측·수정(PR #28·#29) 후 재빌드 전부 ok.
  ① 보조원장 6종 G6 — 같은 접수번호 안에 구분 컬럼이 같은 복수 행(capital 27·hyslr 13·audit 4·tesstk 4·dividend 2·shares 2). DART 응답 행에 위치 식별자가 없어 원장 PK `row_hash` 를 키에 넣었다.
  부작용 실측: 원장 `row_hash` 는 내용 해시가 아니라 행마다 다르므로 동일 payload 행도 접히지 않는다(dividend 접힘 36,930 → 0, tesstk 31,928 → 0, audit 11,488 → 0) — 원장이 이미 행을 구분한 것이라 stage 는 그대로 싣는다(1:1).
  ② `stg_analyst_summary` G2 — WISE 가 EPS 음수면 PER 을 `N/A` 로 표기(98 blob) → blank 계상. ③ `stg_fin_wise` G8 — 무효 종목 082640 의 `DATA: null` → 빈 blob. ④ 빌더 — 전 행 reject 시 read_parquet 크래시 → 빈 빌드.
  ⑤ dividend·shares·hyslr 의 G2 는 baseline 임계 없이는 통과 불가(survey 예측대로 비숫자 실재) — baseline.json 이 먼저 있어야 한다.
- **시간**: fin 657초(RSS 6.2GB) · credit 211초 · flow_kiwoom 107초 · price 63초 · listing 54초 · flow_split 42초 · fin_wise 27초 · disclosure 21초 · 나머지 52테이블 합 90초.
- **접힘(payload 동일)**: rcept_dt_map 620 · disclosure 417(전 컬럼 투영 — 참조표의 620 과 다름) · credit 566,795 · flow_split 52,347 · holder_elestock 32,347·majorstock 21,999(v1∪v2 동일 행) · calls_kis 2,655 · capital 2,053(row_hash 키 이전). reject 는 61테이블 전부 0.
- **G7 격리 셀**: capital 15 · delisted_master 5 · tsstk_dp 1 — 전부 임계 내, 행 reject 0.
- **G8 blob 계상**: monthly 13,202 blob → 222,499행(라벨 접힘 1,732·불일치 0·항목명 미상 1,856) · annual/quarterly 1,614 blob(빈 2) → 11,270 / 11,284 · matrix 4,842 blob(빈 72) → 214,650 · analyst 1,613 blob(no_data 1·무의견 346·N/A 98) → 1,612 · fin_wise 3,228 blob(빈 2) → 445,294.
- **G4 골든 픽스처 38개 전부 일치**: price 4 · fin 2 · monthly 3 · 키움 15 · KIS 14(unit_scale ×1e6·×1e3 검증).
- **baseline.json 28지표**(`python -m stage.baseline`, 09-03): 가격 교차 close 1.0 · volume 0.99986(조인 7,537,984) · 정지행 125 / ETF 2 · PARVAL 비수치 73,615 · 음수 poss_stkcnt 3 · rmnd_stcn 2,691 · whol_loan_gvrt 539(−594.76~1120.92) · whol_stln_gvrt 1 · credit 중복 그룹 517,648 · disclosure 3,444,518행·중복 618·정정 577,072 · fin 15,375,024·비KRW 132,355·bsns_year 불일치 120·표준계정 미사용 2,741,192 · rcept 참조표 미스 0 · audit 의견 434종 · capital 연도 오타 7 · tsstk_dp 2106 1 · ★ doc_index 106,065(99,172 → 수집 진행. **09-03 07:34 KST 수집 종료**: 대상 174,309 = 사업보고서 52,329 + 반기·분기 120,577, ZIP 저장 171,179 · 26.56GB · 2,499분, `http_status=014` 3,130건은 zip_ok=false 로 보존 → `stg_doc_index` 새 스냅샷 `snap_20260902T230100Z` 로 재빌드 174,309행, 해시 `174309:21fc3c3fa818f1c`) · ★ monthly blob 13,202 · ★ analyst blob 1,613. 임계 7(listing G2 1%·shares 12%·dividend 0.1%·hyslr 0.01%·credit 0.01%·delisted_master G2 5%·G7 1%).
- **2차 패스(같은 스냅샷 재빌드)**: 61/61 ok, 1,348초. content_hash 1차(수정 후 재빌드 포함) 대비 **60/60 동일**, G5 Δ=0 61테이블 전부 pass, G9 baseline(close 1.0·volume 0.99986) pass. MANIFEST keep=3 GC 동작(fin·price 3판, 재빌드 테이블 2판). 산출 4.4GB(3판본 누적), 디스크 여유 297GB. **stage 층 구현 완료 — equity 인계는 `docs/STAGE_HANDOFF.md`.**
- 후속(코드 밖): baseline `stg_shares.non_numeric_cells` 술어가 `'-'` 를 셌다(655,481) → `'-'` 제외로 정정(PR 이후 재측정) · doc_index 는 수집 종료 후 재고정 · 매 빌드 전 `python -m stage.baseline` 로 ★ 재측정 후 사람이 승인.

## 11. 결정 기록

| # | 결정 | 일자 |
|---|---|---|
| ① | Parquet + pyarrow·duckdb | 09-01 승인 |
| ②′ | 결측 = NULL + `_src_flag` + `_cast_fail_cols` + **`miss_kind`** (v2.1 확장) | 09-01 / 09-02 |
| ③′ | available_at → available_date (날짜 단위) | 09-01 사용자 확정 |
| ④′ | 중복 = **payload 투영 동일만 접기** · 값 상이 전 행 보존 · **is_latest 는 stage 서 제거** (④ 의 목적 — 소급 주입 방지·전행 보존 — 을 지키는 투영 재정의. 리뷰 5기 수렴) | 09-01 승인 / 09-02 개정 |
| ⑤ | v3 미러 = 정식 stage (팩터·백테스트 입력) — 4종 규칙 분해로 이행. **09-02 개정: 일일 sync 중단, 2026-04-03~09-02 동결 사본만 편입** | 09-01 사용자 확정 / **09-02 개정** |
| **⑥** | **available_date = 사실 날짜만. 랙·보수 버퍼 금지 — 판단은 엔진 설정, 공개시점 지식은 카탈로그(dataset_profile — equity·증분 트랙)** | **09-02 사용자 확정** |
| **⑦** | **가격 = 내용일 당일** ("당일치 바로 나오니까" — 실시간 관측 + 교차 100% 실증) | **09-02 사용자 확정** |
| **⑧** | KRX 08-21~ 백필 보류 — 일일 증분 때 소급, 그전엔 coverage_gap 예외 | **09-02 사용자 결정** |
| **⑨** | 운영 크론 정리 — `daily_dart.sh` 제거(stage 2·4 는 0콜, stage 3 은 013 재확인 39,055콜/일에 적재 0행, 스윕 미완 1창은 +3행 판정 문제) · `sync_v3` 중단 · **시계열 일일 증분은 미설계가 정상**(매일 도는 건 소멸성 소스 적립뿐) | **09-02 사용자 결정** |
| **⑩** | **v2.2 최종 검수 반영** — `observed_src`·UTC 명시, G7 행 격리, survey v2 전수·어휘, 파티션 61 전수 선언, `baseline.json`, 카탈로그 보강(WISE target_price·min_max·단위값, SPEC 규칙 4건, stg_fin 키 예외, stg_credit=deal_date, ovr_shrts_qty_valid·격자·랙·크론 이관, rcept_no[:8] 폴백 폐기), 산출물 스키마·실패 처리·게이트 skip 판정, SPEC §7·§8 관계 | **09-02 검수** |
| — | stage 1회 풀 빌드 → 증분은 별도 설계 · equity 는 stage 후 | 09-01 |

**미결**: 컬럼별 (p,s) 최종값과 G2·G7 임계(survey v2 완료 — rules 반영은 테이블별 착수 시) · stg_fin 빌드 스필 최적화(§10 S2) · 수급·공매도의 실제 공표 시점(관행 D+1 —
프로브 실측은 일일 증분 트랙, 공개시점 대장에서). cF5003 어닝서프라이즈는 09-02 사용자 판단으로 제외.

**크로스 대조 최종 실측 (09-02 재측정)**: 종가 KRX=키움 **7,537,984행** 100.0%(09-01 의 2,864,871 은 survey_cross 조인 수) · 거래량 99.9864% ·
공매도 키움∩KIS 겹침 0(상보 유니버스) · 상장주식수 97.45%(차이 24건 전부 8/20~9/1 기업행위).

**도구 교훈 (3건 — rules·gates 는 실행 전 컬럼 존재+길이 검증 필수)**:
① SQLite 큰따옴표 폴백 — 없는 컬럼명이 문자열 상수가 됨 (ISU_SRT_CD·close_pric 2회)
② **실재하는 오답 컬럼** — isu_base 의 `ISU_CD` 는 12자 ISIN: 존재 검증을 통과하는 더 나쁜 변종
③ **미조사 지대의 유령** — `index_name` 은 전수조사가 안 덮은 테이블에서 났다. 조사 커버리지 = 설계 신뢰의 상한
④ **표본 유래 수치는 실측이 아니다** — survey 의 stride 표본 범위(−292~+333)를 전수 실측처럼 인용했다. 게이트 상수는 전수 술어가 병기된 것만 (v2.2)
⑤ **게이트 상수와 문서 숫자는 정의를 함께 적는다** — "중복 491"은 그룹 수인지 초과 행수인지, "2,864,871행"은 어떤 조인인지 없어 재현이 안 됐다 (v2.2)
⑥ **duckdb executemany 금지** — 파서 출력은 JSON Lines 로 쓰고 `read_json` 한 번에 읽는다 (S3: 5분+ → 6초)
