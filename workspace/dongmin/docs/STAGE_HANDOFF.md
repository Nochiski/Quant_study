# STAGE → equity 인계 (v1, 2026-09-03)

> stage 층은 원장 5 DB·61테이블을 Parquet 로 옮기는 층이다. 이 문서는 equity 층이 stage 를 **어떻게 읽고, 무엇을 믿고, 무엇을 판단해야 하는지**만 적는다. 규칙의 근거는 `STAGE_DESIGN.md`(v2.2), 실측은 §10.

## 1. 읽기 계약
- 위치: 서버 `~/quant-ledger/data/stage/<table>/`. **`MANIFEST.json` 경유 필수** — `current_build` 의 `partitions[].path` 만 읽는다. 맨 glob 금지(구버전 `v=…` 디렉토리가 keep=3 으로 공존한다).
- 파티션: `date_axis`·`receipt_axis` 는 `v=<build>/year=YYYY/*.parquet`, `whole` 은 `v=<build>/part0.parquet`. `read_parquet(..., hive_partitioning=true)` 로 읽으면 `v`·`year` 하이브 컬럼이 붙는다(파일 안에는 없음).
- 판정: `_meta.json`(파티션당) 의 `gates`·`n_reject`·`rcept_map_miss`·`coverage_from`·`version_loss_upstream`·`observed_date_exempt`. reject 행은 `v=<build>/_reject/part.parquet`(원문 TEXT + `reject_reason`).
- 회귀 기준: `data/stage/baseline.json` — `{table: {metric: value, thresholds}}` + `_measured[]`(SQL·measured_at·growing).

## 2. 공통 골격 (모든 테이블)
| 컬럼 | 의미 | equity 가 할 일 |
|---|---|---|
| `ticker`·`date` | 정규 키(6문자 TEXT·내용일). 없는 테이블: stg_fin·DS005·로그류 | 티커는 절대 정수 캐스팅 금지(`0001A0` 실재) |
| `available_date` / `available_basis` | 공개일 **사실만**: measured(원장 공개일·수집일) / derived(rcept 참조표) / default(내용일 대용) / unknown(참조표 미스, NULL) | **랙은 엔진 설정** — `WHERE available_date <= asof - lag`. `default` 는 "당일 가용" 이 아니다(가격류 제외) |
| `observed_date` / `observed_n` | 원장 시각의 KST 날짜 · payload 접힌 행수 | 재수집 판본 선택 축: PIT = 같은 키의 `min(observed_date)`, latest = max. `is_latest` 없음 |
| `_src` / `_src_flag` / `_cast_fail_cols` | UNION 출처 · ok/partial · 캐스팅 실패 컬럼 list | partial 행은 값 NULL 의 원인을 `miss_kind` 로 본다 |
| `miss_kind` STRUCT | 셀 결측 원인: ledger_null/blank/dash/zero/cast_failed/out_of_range | 결측 3분류(`fill_kind`)는 equity 가 `stg_units_*`·`stg_shards_kiwoom`·캘린더로 만든다 |

`_meta.json` 의 `lag_known=false` 테이블(수급·외인·대차·공매도·마스터·DART·v3 revision)은 lag 0 을 적용하면 안 된다 — 공표 시점은 `dataset_profile`(equity 트랙) 몫.

## 3. 테이블별 인계 메모 (§4 요약 — 숫자는 §10 1차 풀 빌드)
| 소스 | 테이블 | stage 행 | 원장 행 | 접힘 | 메모 |
|---|---|---:|---:|---:|---|
| KRX | `stg_price_daily` | 9,201,516 | 9,201,516 | 0 | 키 (ticker,date) · O/H/L '0' → NULL(ledger_zero) · G9 KRX⋈키움 종가 100% |
| KRX | `stg_etf_price_daily` | 1,688,735 | 1,688,735 | 0 | ETF 고유 NAV·순자산·기초지수 · 정지행 2(baseline) |
| KRX | `stg_index_daily` | 347,821 | 347,821 | 0 | 키 (index_class,index_name,date) — 업종지수명 양시장 중복 |
| KRX | `stg_listing_daily` | 9,201,516 | 9,201,516 | 0 | 당일 상태 스냅샷 · `isin` 12자 · `par_value_krw`+`par_value_kind`(비수치 73,615 → cast_failed, G2 1%) |
| KRX | `stg_ingest_krx` | 30,373 | 30,373 | 0 | 수집 로그 · available 비부여 |
| 키움 | `stg_flow_daily_kiwoom` | 7,621,338 | 7,621,338 | 0 | 투자자 13컬럼 ×1e6 `_krw`(픽스처 13) · `close_krw` abs + `_dir` · `volume_shr`(오표기 정정) |
| 키움 | `stg_short_daily_kiwoom` | 3,971,630 | 3,971,630 | 0 | `shrts_trde_prica_krw` ×1e3 · `ovr_shrts_qty_shr` 원문(_valid 없음 — equity) |
| 키움 | `stg_foreign_daily` | 7,682,844 | 7,682,844 | 0 | `wght_pct` strip_plus · `poss_stkcnt` 음수 3 keep |
| 키움 | `stg_lending_daily` | 6,988,296 | 6,988,296 | 0 | `remn_amt_krw` ×1e6 · 항등식 불변식 |
| 키움 | `stg_master_daily` | 8,614 | 8,614 | 0 | first_write_wins · `coverage_from=2026-09-01` · state 파이프 분해 3불린 |
| 키움 | `stg_shards_kiwoom` | 10,420 | 10,420 | 0 | 원구조 · collected_at 08-23~24 2일뿐 |
| KIS | `stg_flow_split_daily` | 949,023 | 1,001,370 | 52,347 | 중복 52,347 접힘 · `*_ntby_tr_pbmn_krw` ×1e6(픽스처 13) · 수정주가 플래그 payload 보존 |
| KIS | `stg_short_daily_kis` | 939,610 | 939,610 | 0 | `acml_valid`(창 첫 행) · `avrg_prc` '0' 결측 |
| KIS | `stg_loan_daily_kis` | 493,445 | 493,445 | 0 | `rmnd_stcn_shr` 음수 2,691 keep · `rmnd_amt_krw` ×1e6 |
| KIS | `stg_credit_daily` | 8,404,204 | 8,970,999 | 566,795 | 중복 566,795 접힘(req_d2 만 상이) · `stlm_date` 보존 · `price_basis_close/ohl` 컬럼 라벨 · `*_amt` 6 단위 미상 |
| KIS | `stg_delisted_master` | 652 | 652 | 0 | 652행 현재 상태 전부 `_current` · available 비부여 · '00000000' → ledger_zero |
| KIS | `stg_calls_kis` | 355,027 | 357,682 | 2,655 | unversioned 로그 · d1·d2 비키 |
| KIS | `stg_units_kis` | 355,027 | 355,027 | 0 | unversioned 로그 |
| DART | `stg_rcept_dt_map` | 3,443,898 | 3,444,518 | 620 | 참조표 rcept_no→rcept_dt · 페이지 중복 620 접힘 · key_unique |
| DART | `stg_fin` | 15,375,024 | 15,375,024 | 0 | 8컬럼 요청축 키 · wide 6금액 Decimal(38,4) · available=참조표(미스 0) · `is_krw`·`account_std` |
| DART | `stg_dividend` | 384,232 | 384,232 | 0 | 키 = row_hash + 요청축 + se·stock_knd · `se` 라벨이 단위 → 스케일 없음 |
| DART | `stg_shares` | 97,194 | 97,194 | 0 | 키 = row_hash + … · `row_kind` · 비숫자 다수(G2 12%) |
| DART | `stg_capital` | 283,479 | 283,479 | 0 | `isu_dcrs_de` 점표기 + `_raw` · 연도 오타 셀 격리 15 |
| DART | `stg_tesstk` | 328,704 | 328,704 | 0 | `row_kind`(총계·소계) · 취득방법 3단 |
| DART | `stg_hyslr` | 228,226 | 228,226 | 0 | 지분율 `_pct` · `row_kind`(계) |
| DART | `stg_audit` | 93,037 | 93,037 | 0 | `adt_opinion` 원문 + `adt_opinion_class` |
| DART | `stg_holder_elestock` | 32,668 | 65,015 | 32,347 | elestock ∪ v1 (동일 행 32,347 접힘) · rcept_dt measured |
| DART | `stg_holder_majorstock` | 22,209 | 44,208 | 21,999 | majorstock ∪ v1 (21,999 접힘) · rcept_dt measured |
| DART | `stg_disclosure` | 3,444,101 | 3,444,518 | 417 | rm 8플래그 · `is_correction`(577,072) · `has_ticker` · 페이지 중복 417 접힘 |
| DART | `stg_company` | 3,478 | 3,478 | 0 | `_current` 전부 · `est_dt` TEXT(1956 이전 설립 실재) · available 비부여 |
| DART | `stg_corp_map` | 3,478 | 3,478 | 0 | 참조표 · observed_date 면제 |
| DART | `stg_doc_index` | 174,309 | 174,309 | 0 | ZIP 메타 · 수집 종료(09-03 07:34 KST) · zip_ok=false 3,130 |
| DART | `stg_calls_dart` | 555,294 | 555,294 | 0 | unversioned 로그 · 요청축 빈값 비키 |
| DART | `stg_units_dart` | 346,342 | 346,342 | 0 | unversioned 로그 |
| DS005 | `stg_event_bnk_mngt_pcbg` | 15 | 15 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_cmp_dv` | 414 | 414 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_cmp_dvmg` | 13 | 13 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_cmp_mg` | 1,395 | 1,395 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_cr` | 720 | 720 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_ctrcvs_bgrq` | 168 | 168 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_cvbd_is` | 5,386 | 5,386 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_df_ocr` | 47 | 47 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_ds_rs_ocr` | 134 | 134 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_fric` | 758 | 758 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_pifric` | 106 | 106 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_piic` | 5,538 | 5,538 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_stk_extr` | 112 | 112 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_tsstk_aq` | 1,951 | 1,951 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| DS005 | `stg_event_tsstk_dp` | 4,069 | 4,069 | 0 | DS005 · 키 rcept_no · 한글 날짜 · available=참조표 |
| WISE | `stg_consensus_monthly` | 222,499 | 222,499 | 0 | cF5001+cF5002 outer join · 키 (…,metric,obs_label) · 단위는 데이터 값 |
| WISE | `stg_consensus_annual` | 11,270 | 11,270 | 0 | T2Y 7행/blob · 키 period_label 원문 · 억원/원 표시 단위(스케일 없음) |
| WISE | `stg_consensus_quarterly` | 11,284 | 11,284 | 0 | T2Q · 동일 |
| WISE | `stg_consensus_matrix` | 214,650 | 214,650 | 0 | T4 계정 9 × lookback 5(current·1w·1m·3m·1y 실측 대조) |
| WISE | `stg_analyst_summary` | 1,612 | 1,612 | 0 | cTB15 요약행 · 무의견 346 · `N/A` blank |
| WISE | `stg_fin_wise` | 445,294 | 445,294 | 0 | 키 (…,ep,seq) wide · `period_label_1~6` 병기 · `val_q*` 슬롯 라벨 없음 · Decimal(38,6) |
| WISE | `stg_v3_revision_daily` | 63,175 | 63,175 | 0 | 동결 사본 · available=collected_date, NULL 은 base_date/default + `coverage_degraded` |
| WISE | `stg_v3_analyst_opinions` | 254,925 | 254,925 | 0 | 동결 사본 · snapshot_date measured |
| WISE | `stg_v3_consensus_annual` | 18,086 | 18,086 | 0 | sync_date 09-01·09-02 두 판본 |
| WISE | `stg_v3_revision_compare` | 978 | 978 | 0 | `opinion_*` 전행 NULL |
| WISE | `stg_wise_coverage` | 2,566 | 2,566 | 0 | 종목당 1행 `status_current` · 이력 아님 |
| WISE | `stg_calls_wise` | 31,442 | 31,442 | 0 | unversioned 로그 · `pkey=''` 키 인정 |

합계 61테이블 · stage 84,364,371행 / 원장 85,041,551행(접힘 677,180 — doc_index 재빌드 반영) · reject 0 · 1차 패스 23분 · 같은 스냅샷 재빌드 content_hash 60/60 동일(G5 Δ=0). 게이트 판정은 각 테이블 `MANIFEST.json` 의 `builds[].gates`.


## 4. equity 가 판단해야 하는 것 (stage 는 안 한다)
- 가격 정본·조정계수: `stg_price_daily`(KRX 원주가) + `stg_credit_daily`(`stck_prpr` 수정종가, OHL 원주가 — 컬럼 단위 `price_basis_*`) + `stg_listing_daily`(PARVAL·상장주식수). `price_matches_krx` 딱지는 equity.
- corp_code↔ticker: `stg_corp_map`(현재 시점 값 — `corp_cls` 로 과거 유니버스 거르면 폐지종목 26% 소실) + `stg_company`(`_current`). 우선주 1:N.
- 유니버스: `universe_asof` = KRX 스냅샷(pit) · 현재=`stg_master_daily`(coverage_from 2026-09-01) · 폐지일=`stg_delisted_master`(전 상태 `_current` — 과거 필터 금지). 2026-08-21~백필일 구간 `coverage_gap`.
- 집계행·라벨: DART 보조원장 `row_kind='aggregate'` 제외 후 합산. `stg_fin` 은 wide 6금액·`account_std=false` 행은 표준계정 조인 금지·기간 라벨은 텍스트.
- WISE: `stg_consensus_*` 는 fetched_date 판본 축 — 리비전 팩터는 `(ticker, obs)` 의 `min(fetched_date)` 행이 PIT. `stg_fin_wise` 의 `val_q*` 슬롯 라벨·`lookback` 해석은 equity. 단위는 데이터 값(`unit`, `acc_nm`).
- 단위 미측정 컬럼(접미사 없음): 키움 `shrts_avg_pric`·`last_price`·ka20068 `dbrt_trde_*`·`rmnd`·ka10008 `frgnr_limit*`, KIS `frgn_reg/nreg_ntby_pbmn`·credit `*_amt` 6, DART `df_amt`, WISE T2Y/T2Q 값(억원·원은 카탈로그 지식).

## 5. 알려진 한계 (§0·§8)
- 문서층 L1(ZIP 본문)·정정 체인 복원·공개시점 대장·일일 증분은 범위 밖. `ws_run_log` 미편입.
- KRX 2026-08-21~ 상장·폐지 재구성 불가. 관리종목·거래정지 과거 시계열 부재(09-01 부터 `stg_master_daily`).
- `stg_wise_coverage`·`stg_delisted_master`·`stg_company`·`stg_corp_map` 은 현재 상태(`_current`). 재조회가 덮는 소스(KRX·키움 upsert)는 판본이 1~3개로 퇴화.
- 후속 후보: c1010001 `cTB24` 제공처별 목표가 표 · stg_fin 스필 최적화 · daily_wise flock · 통합 종목마스터 DB(사용자 보류).
