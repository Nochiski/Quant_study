# Equity 층 설계 v1.2 (2026-09-05 — 문서층 P1 통합 · 소비자 계약 재기술)

> stage(사실) 위에 equity(구조 정책)를 쌓는 층의 **테이블·뷰·규칙 명세**. 워크플로우는 `EQUITY_WORKFLOW.md` v1.2, 게이트 사양은 `EQUITY_GATES.md` v1.0, 소비자 필드 계약은 `EQUITY_FIELD_MAP.md` v1.0, 인계 사실은 `STAGE_HANDOFF.md`·`DOC_DESIGN.md` v1.1(§8 equity 인계), 실측 근거는 §10(P1~P15).
> 규칙 한 줄마다 근거를 병기한다. **stage 컬럼명은 `src/stage/rules_*.py` 의 `ColumnRule.name` 이 정본**이다. v1.1 → v1.2 변경은 §12.

---

## 0. 범위와 한 문장

**"날짜 D에, 그 시점에 알 수 있던 정보만으로, 그날 실존한 전 종목의 상태를 줘."**

- 입력: 서버 `~/quant-ledger/data/stage/` **66테이블**(62 + 문서층 P1 4, 읽기 전용, MANIFEST 경유). 원장 SQLite 는 읽지 않는다.
- 산출: `~/quant-ledger/data/equity/` parquet 테이블 **27**(26 + `factor_readiness`) + `equity.duckdb` 뷰 카탈로그 + 어댑터(워크벤치 5포트·커널 3포트).
- 소비자: 워크벤치 `strategy_workbench`(팩터 그래프 평가·백테스트 파이프라인)와 엔진 커널. 필드 계약은 `EQUITY_FIELD_MAP.md`.
- 범위 밖: 팩터 값·통계 정규화·정책 **적용**·일일 증분·문서층 P2~P4 자체·WICS 수집(보류)·거래비용 규칙표.

---

## 1. 층 계약

허용 변환: stage 위에서 **조인 · 격자 · 판본 선택 · 파생(계수·정정 링크·정지 상태·무거래 연속일·기간축)** 을 한다. 하지 않는 것: 임계값·판정 결과를 팩트 행에 굽기, 통계 정규화, 최신 판본 선택, `_current` 테이블로 과거 필터, 게이트 술어를 산출식으로 재계산.

원칙 6(ERD v0.1): ① 전 팩트 행 PIT 게이트 ② 원주가 불변 + 계수 분리 ③ corp/ticker 이축 ④ 결측은 결측 ⑤ 지식은 카탈로그 ⑥ 읽기 전용 SQL 계약(PIT 뷰 4 + 보조 3).

**정본 우선순위(사용자 확정 09-03)**: KRX 가 주는 사실은 KRX 정본. 폐지일도 KRX(listing 소멸) 우선, KIS 는 대조축. basis 어휘: stage 4종 `measured`·`derived`·`default`·`unknown` + equity 신설 `convention`.

---

## 2. 저장 · 판본 · 빌드 `[결정 1·2]`

| 항목 | 규칙 | 근거 |
|---|---|---|
| 포맷 | parquet, `data/equity/<table>/v=<build>/…` + `MANIFEST.json`(keep=3) + 파티션 `_meta.json` + `_reject/reject_reason=<r>/`(duckdb `PARTITION_BY`) | stage §2 계승 |
| 파티션 클래스 | `date_axis` = `year(date)` / `receipt_axis` = `substr(rcept_no,1,4)`(receipt 테이블은 `rcept_no` 필수) / `whole`. `consensus_daily` 는 `date_axis` 이되 파티션 키 식은 `year(obs_month)`(PIT 축 아님) | STAGE_DESIGN §4 |
| 입력 고정 | `BuildRecord.inputs = {stg_x: build_id}` · 고정 빌드는 `_pinned/<stg_x>/v=<build>/` 하드링크 + `_pinned/<stg_x>/MANIFEST.json` 에 BuildRecord 복사(맨 glob 금지). 입력 이름이 `stg_` 로 시작하지 않으면 앞서 커밋된 **equity 테이블**(`<equity_root>/<table>/MANIFEST.json` current_build)을 같은 규약으로 고정한다 — S03 부터 `security_span`·`trading_calendar` 가 이렇게 들어온다. 자기 참조는 선언 단계에서 거부. **문서층 4테이블도 고정**. `_pinned/` 에 `manifest.commit()` 호출 금지 | manifest.py GC · 같은 파일시스템(P9) |
| 재현성 | 같은 `inputs` → 파티션 `content_hash` 동일(EG5a, 해시는 **tmp 경로에서** 계산 — `v=` 하이브 컬럼 함정) · `_asof/<view>/<build>/` 에 고정 표본(baseline `asof_sample` 날짜 5 × 종목 20) 결과 보관, keep=3 (EG5c·EG11·EG19) | code proposal §2-5 |
| 뷰 카탈로그 | `equity.duckdb` 는 매크로만, 절대경로, 임시 파일 → `os.replace`. **빌드·GC 뒤 반드시 재생성**(굽힌 `v=` 가 rmtree 되면 깨진다) · `snapshot_id` = 전 테이블 build_id 정렬 해시 | P1a~d · code proposal |
| 빌드 실행 | 서버 테이블 직렬(`flock`), `memory_limit` 6GB·threads 3·`temp_directory=data/equity/_tmp/spill`. 큰 테이블은 연도 파티션 루프, 집계는 스트리밍(09-05 문서층 스왑 사고) | RAM 15GB |
| 커밋 입자 | 테이블. 섀도 → 게이트(GATES §7-1 순서) → `MANIFEST.json` `os.replace`. 실패 `_failed/<build>.json` | stage §2 |
| 상수 | `baseline.json` `{table}.{metric}` · 본문·코드 하드코딩 금지 · 첫 빌드 `skip(no_baseline)` + metrics 기록 | GATES §0-3 |
| SQL | 테이블당 `src/equity/sql/<table>.sql`, 상수는 `_const` 임시 테이블로 주입 | code proposal §2 |

---

## 3. 공통 골격 컬럼

| 컬럼 | 규칙 |
|---|---|
| `ticker` TEXT(6) · `date` DATE · `corp_code` TEXT(8) · `rcept_no` TEXT(14) | 정규 키. 티커 정수 캐스팅 금지 |
| `available_date` · `available_basis` | 전 팩트 행 필수(EG2). **파생 컬럼은 `<col>_available_date` 동반**(구성 행 max) |
| `observed_date` | stage 계승 |
| `src` | 상보 결합 테이블(`kiwoom`/`kis`/`v3`/`wise`), PK 포함 |
| `fill_kind` STRUCT(kind, evidence) | 격자 테이블만. kind ∈ {measured, src_omitted, empty_response, not_collected} · evidence ∈ {shard_done, unit_ok, shard_empty, unit_empty, none}. 엔진 `CellKind` 대응은 FIELD_MAP §1 |
| `factor_ok` | `adj_factor` 만 |
| `security_id`(뷰·어댑터) | `{ticker}:{span_seq}` — 테이블에는 두지 않고 어댑터가 조립 |

---

## 4. 테이블 카탈로그 (27)

표기: grain · 원천(rules 실명) · available · 파티션 · EG1(등식 ID 는 GATES §3) · 규칙. 슬라이스 ID 는 WORKFLOW §3-1.

### 4-1. 1단계 — S01 식별 · S02 캘린더·구간·지수 · S03/S03B 유니버스

**`corp`** (S01) — grain `corp_code` · whole — 원천 `stg_corp_map`(`corp_code`·`ticker`·`corp_name_current`) · `stg_company`(`acc_mt`·`induty_code_current`). 컬럼 `corp_name`·`fiscal_month`·`fiscal_month_basis='current_snapshot'`(P10: observed_date 1개)·`induty_code`·`induty_class`(64/65/66 → financial). `corp_cls` 없음. EG1 = 3,478 · `stg_fin.corp_code ⊆ corp_map`(P10).

**`security`** (S01) — grain `ticker` · whole — 원천 `stg_listing_daily`(`isin`·`name`·`list_date`·`market`·`secugrp`·`stkcert_tp`·`sect_tp`) · `stg_etf_price_daily` · `stg_delisted_master`(`lstg_abol_dt`). `sec_type`(P11 전 이력 어휘): common 3,341 · preferred(구형 120·신형 60·종류 14) · reit 34 · ship_fund 49 · fund(투자회사 12·SOC 2) · foreign 24 · dr(예탁증권 14·예탁증서 6) · spac(sect_tp 'SPAC(소속부없음)' ∨ name '기업인수목적', KOSPI 4·KOSDAQ 372) · etf 1,416 · **other = 행 유지 + 플래그(격리 아님, 생존편향)**. `delist_date_krx` = span 마지막 존재일 + 1거래일(마지막 = `backfill_end` 면 NULL/unknown) · `delist_date_kis` = `lstg_abol_dt` · `delist_conflict` · `delist_date` = krx → kis. EG1 = 3,672 + 1,416.

**`security_span`** (S02) — grain (`ticker`, `span_seq`) · whole — (listing ∪ etf) 존재일 × 캘린더 최대 run. `end_reason ∈ {delisted, coverage_gap, data_gap}`. 재상장 2(036220·101970). 실측 listing 완결성 4,094/4,094(P11) → `data_gap` 0. EG1 비중첩 ∧ Σ n_days = count(ticker,date) ∧ distinct date = 캘린더 거래일 수.

**`corp_ticker`** (S01) — grain `ticker` · whole — isin8 KR7 만 그룹(3,438 그룹 전부 보통주 1, P11) · 비KR7 40티커 단독 · `link_basis ∈ {isin8, corp_map, none}`. **`is_common` = 주식종류 `stkcert_tp='보통주'`**(종목 유형 `sec_type` 과 별개 — 스팩·리츠·펀드도 주식종류는 보통주; sec_type 기준이면 474 그룹이 보통주 0, P18). EG1 = `security` 행수.

**`trading_calendar`** (S02) — grain `date` · whole — `stg_index_daily` distinct date(4,094, 2010-01-04~2026-08-20). **gap 축 없음** — 09-05 실측(P16): 08-20 이후 date 를 가진 stage 팩트는 `stg_master_daily` 스냅샷(09-01·09-02)뿐이고 flow_split·credit·short_kis·loan_kis 는 08-14~08-18 에서 끝난다. `backfill_end` = 2026-08-20(baseline). 컬럼 `date`·`prev_td`·`next_td`. 어댑터의 `sessions` 축. EG1 = 4,094 = `stg_index_daily` distinct date.

**`index_daily`** (S02) — grain (`index_class`, `index_name`, `date`) · date_axis — `stg_index_daily` 1:1(컬럼 실명 `close_idx` 등). 어댑터는 `idx:코스피`·`idx:코스닥`·`idx:코스피 200`·`idx:코스닥 150` 예약 `security_id` 로 `benchmark.close` 를 낸다. EG1 = 347,821.

**`universe_daily`** — grain (`date`, `ticker`) · date_axis — **두 슬라이스**:
- **S03 존재·상태**: 원천 `security_span` × 캘린더 · `stg_listing_daily`(`market`·`sect_tp`) · `stg_master_daily`(`is_admin_issue`·`is_trade_halt`·`is_liquidation`, 2026-09-01~) · `stg_disclosure` 신호. 컬럼 `status`(`listed`·`suspended`·`delisted`)·`market`·`sec_type`·`halt_state`·`admin_state`·`admin_state_basis`·`liquidation_window`·`signal_halt`·`signal_halt_release`·`signal_admin`·`signal_liquidation`·`signal_delist`·`admin_flag`·`available_date`·`available_basis`. **coverage_gap 행은 만들지 않는다**(만들고 소비를 금지하는 데이터, GAP-21) — gap 은 `security_span.end_reason` 과 profile `coverage_to` 로 표현.
- **S03B 시장 파생**(2단계 `price_daily` 정본 의존): `mktcap_krw`·`adv20_krw`([D−19, D] 그대로, 랙은 뷰)·`listing_age_days`·`no_trade_run`·`suspended` 판정 완성(`halt_state` ∨ (`price_kind='reference'` ∧ `no_trade_run ≥ baseline universe_daily.no_trade_run_k`)).
- 상태 규칙(PIT, P6·P12): `halt_state` = 지정 공시 rcept_dt 부터 [해제 공시 ∨ 다음 `volume>0` 거래일) — 지정 8,415·해제 2,287·동일일 1,214·첫 거래 중앙값 4일·p90 562일·재거래 없음 930. `admin_state`: KOSDAQ = `sect_tp ∈ {관리종목(소속부없음), 투자주의환기종목(소속부없음)}` 일별 / KOSPI = 지정 신호 365거래일 창(`admin_state_basis='derived_kospi_window'`, 해제 공시 3건) / 09-01~ `is_admin_issue`. `liquidation_window` = 정리매매 개시 공시(349) 부터 `delist_date`. ETF 행 포함.
- EG1 = Σ_span n_days(≤ backfill_end) · EG3 `halt_state` 열린 구간 0.

**공시 축 신호 표** (report_nm 정규화는 stage 완료 → 접두어 `^\[[^\]]*\]` 제거만): `signal_halt` `%매매거래정지%` ∧ NOT `%해제%`(8,415) · `signal_halt_release`(2,287 + 동일일 1,214) · `signal_liquidation` `…정리매매 개시%`(349) · `signal_admin` `%관리종목%` ∧ NOT 해제(1,218) · `signal_delist`(3,952) · 락일·배당결정 → `corp_event`.

**`universe_policy`** (S03) — grain (`policy`, `rule_seq`) · whole — 스키마 + `all` 만(임계 등재는 S03B 실측 후). equity 보관, 팩터층 적용. 어댑터 `universe_id` 대응: `krx.all`·`krx.common-stock`(sec_type=common)·`krx.investable`·`krx.liquid`.

### 4-2. 2단계 — S04 가격 · S05 기업행위 · S06 계수

**`price_daily`** (S04) — grain (`ticker`, `date`) · date_axis — `stg_price_daily` ∪ `stg_etf_price_daily` · `stg_listing_daily`(`par_value_krw`·`list_shrs`). 컬럼 `open`·`high`·`low`·`close`(원주가)·`volume_shr`·`value_krw`·`mktcap_krw`·`shares_out`·`par_value_krw`·`price_kind`·`available_date=date`(default). **`krx_kis_close_ratio` 컬럼 없음** — KIS 수정종가 대조는 EG8 metric 으로만(이른 최적화 제거). EG1 = 10,890,251. `open IS NULL ∧ volume>0` 건수는 `_meta` 기록(GAP-14).

**`corp_event`** (S05) — grain `event_id` · receipt_axis — 원천 DS005 15종 · `stg_capital`(비집계) · `stg_disclosure` 락일·배당결정 · KRX `list_shrs`·`par_value_krw` 변화. 컬럼 `event_type`(split·reverse_split·bonus·rights·capred·spinoff·merger·stock_dividend·cash_dividend·cb_issue·treasury_buy·treasury_sell·other)·`announce_date`·`effective_date`·`effective_basis`(disclosure_body·krx_shares_change·krx_notice·unconfirmed)·`ratio`·`amount_krw`·`rcept_no`·`available_date=announce_date`. EG1 = Σ 원천 − dedup(ticker·type·effective_date, 건수 기록). MVP 는 split·bonus·capred 만.

**`adj_factor`** (S06) — grain (`ticker`, `effective_date`, `event_id`) · date_axis — `price_factor`·`share_factor`·`factor_source`·`factor_ok`·`available_date = min(공시 rcept_dt, effective_date + 1거래일)`(EG2 예외: ≥ announce_date 축). 정규화: `cum_*(d, asof)` = d 이후 asof 까지 계수의 곱, base=asof, **가격·거래량 둘 다 곱셈**. 배당 계수 없음. EG3 시총 불변 이벤트 `price×share=1` · EG8 가격·거래량 점프 ≤ baseline · 탐지 recall ≥ baseline(분모 재계산).

### 4-3. 3단계 — S08 수급 · S09 공매도·대차 · S10 신용 (격자)

격자 = 캘린더 × `universe_daily`(`status ∈ {listed, suspended}` ∧ `sec_type ∉ {etf}`) · date_axis · `_reject/pre_calendar`(P11: short 58,211 · flow 7,609 · foreign 67,994 · credit 24,497 · lending 0) · `_reject/off_grid`. `fill_kind` 판정(P3·P12 어휘): 키움 샤드 `status='done'` ∧ 요청창 ∧ KRX 가격 행 존재일 → `src_omitted`(shard_done) / `empty` → `empty_response` / KIS 유닛 `status='ok'` ∧ 창 → `src_omitted`(unit_ok) / `empty` → `empty_response` / 없음 → `not_collected`. 유닛 `dataset ∈ {credit 3,175·flow 652·loan 287·master 652·short 652}`. EG9 의 미수집→0 검사는 **로그 축 독립 재판정**(항진명제 금지).

**`flow_daily`** (S08) — 키움 13주체 `_krw` 전부 + `foreign_wght_pct`·`limit_exh_rt_pct`·`foreign_poss_shr` + `src`. KIS 대응표(명세 기반, 검증축 없음): ind_invsr↔prsn · frgnr_invsr↔frgn · orgn↔orgn(합계, 항등식 제외) · fnnc_invt↔scrt · insrnc↔insu · invtrt↔ivtr · bank↔bank · penfnd_etc↔fund · samo_fund↔pe_fund · etc_corp↔etc_corp · etc_fnnc·natn·natfor 없음(NULL) · mrbn·etc_orgt·etc 버림. EG3 12주체 합 = 0 ± baseline(kiwoom, orgn 제외). PK (date, ticker, src) → EG1 좌변 `count(DISTINCT (date,ticker))`.
**`short_daily`** (S09) — 공매도(키움·KIS) + 대차(`lending_balance_kis_shr`·`lending_balance_kiwoom_raw` 단위 미측정, 겹침 구간 비율 분포 기록).
**`credit_daily`** (S10) — `stg_credit_daily` 단독(융자 `whol_loan_rmnd_stcn_shr`·대주 `whol_stln_rmnd_stcn_shr`·`stlm_date`). `credit.net_buy` 축 존재는 S10 에서 판정.

### 4-4. 4A — S11 공시 판본 · S12 재무 PIT (문서층 P1 기반, 1단계 비의존)

**`disclosure_version`** (S11) — **grain `rcept_no`** · receipt_axis — 모집단 = `stg_disclosure` 정기보고서 3종(사업·반기·분기; 연장신고·유동화전문회사·회계법인·해외신고 제외) 전건. 컬럼: `corp_code`·`kind`·`period_label`·`group_key`(라벨 없는 586건은 NULL + `group_key_basis='no_label'`, 행 유지)·`is_correction`·`corr_prefix`·**링크**(DOC §8.1): `orig_rcept_no`·`candidate_status ∈ {unique, none, multi_resolved, multi_unresolved, n/a}`·`date_check ∈ {exact, off_1d, off_2_7d, mismatch, unparsed, no_page, no_zip, n/a}`(`no_page`/`no_zip` 은 `stg_doc_index.zip_ok` 로)·`prior_corr_count`·`corr_page_found`·`filed_date`(`stg_doc_correction`)·`reason_raw`·`corr_has_fin_item`(items 에 재무 항목, 2,238)·**원본 측 팩트** `first_correction_dt`·`n_corrections`(판정은 뷰가 asof 로 — `has_correction` 정적 컬럼 폐기, DEFECT-E01)·`legal_deadline`·`delay_days`·`link_basis='parsed'`·`available_date=rcept_dt`(derived). 후보 술어: 같은 `corp_code`·`kind`·`period_label` · `corr_prefix ∉ {기재정정, 첨부정정}`(`[첨부추가]` 는 원본 라벨) · `rcept_no <`. `kind` 는 정정 문서 자신의 `report_nm` 에서(`target_raw` 미사용). **모집단 5단 사다리**(정기보고서 접수 → 정정 접수 20,579 → is_correction 24,285 → ZIP 있음 17,600 → filed parsed 15,225)를 baseline 에 등재한 뒤 E-G6a(≥ 임계)·E-G6b(exact+1d 기록)·E-G7(`rm` 플래그 도달 ≥ 임계) 판정. EG1 = `count(DISTINCT rcept_no)`(항등) + 사다리 건수 회귀.

**`fin_std`** (S12) — grain (`corp_code`, `period_end`, `report_code`, `fs_div`, **`vintage_kind`**) · receipt_axis — 원천 `stg_fin`(8키, `account_std`·`is_krw`·`rcept_no`) · `stg_rcept_dt_map` · **`stg_doc_meta`(`period_from`·`period_to`·`doc_acode`, main·파싱 ok 168,938 전건 채움 = 정본)** · `corp.fiscal_month`(검산). `period_end` = `period_to`; `report_code` = `doc_acode`(11011/11012) 이며 1Q/3Q 는 `period_from→period_to` 개월 수(3 vs 9)로 판정; 문서가 없으면 후보 규칙(bsns_year·bsns_year+1 × acc_mt 말일, 0~200일)으로 `period_end_basis='inferred'`. 손익 = `thstrm_amount`(3개월, SPEC §2-7)·`*_q4_derived`(연간 − Σ3분기, `q4_available_date`)·CF `_ytd` + `*_q`(`_available_date` 동반)·부분합 금지. 계정 = `fin_map.py` 21 + 추가 3(depreciation·borrowings·interest_expense, `account_id` 실재 확인 후) + `gross_profit`(FIELD_MAP §3). `revenue_basis ∈ {standard, banking_gross, insurance_gross, consensus, unavailable}`. `vintage_kind='api_restated'`(4A) → 4C 에서 `original`·`corrected` 추가. `restated_unknown=true`(4A). `available_date=rcept_dt`(derived) · `rcept_no`. EG1 = distinct(corp, bsns_year, reprt_code, fs_div) with ≥1 account_std − reject · EG7 `rcept_dt − period_end` ∉ [0, baseline] 격리.

**4C = S14(대기, 문서층 P2)**: `stg_fin_asreported`(XBRL 격자 long, `scope`·`period_slot`) → `vintage_kind ∈ {original, corrected}` 적재 · `v_fin_latest(vintage := 'pit'|'restated')` · D9(정정 없는 보고서 API=원본 100%) 교차 · `restated_unknown=false` · EG5c 재기준. stage 요청: `scope_basis`·`period_slot_basis`·`IS1/IS2/IS3` 의미·D9/D13 baseline 등재.

### 4-5. 4B — S15 지분·감사 · S16 주식수·자사주·배당 (+ 4B′ S16B 대기)

전부 receipt_axis · `available_date=rcept_dt`(derived) · grain = stage 자연키 유도 · `row_kind='aggregate'` 제외는 `stg_shares`·`stg_tesstk`·`stg_hyslr` 3테이블만.

| 테이블 | grain | 원천 | 컬럼 | EG1 |
|---|---|---|---|---|
| `holder_daily` | (`rcept_no`, `repror`, `src`) | elestock 32,668 ∪ majorstock 22,209 | 보고자·구분·지분 전/후 | 합(1:1) |
| `ownership_snapshot` | (corp, bsns_year, reprt_code, `nm`) 비집계 | `stg_hyslr` | 최대주주·특수관계인 지분율 | 비집계 행수 |
| `shares_outstanding` | (corp, bsns_year, reprt_code, `se`) 비집계 | `stg_shares` | 발행·자기·유통 | 비집계 행수 |
| `treasury_stock` | (corp, bsns_year, reprt_code, `acqs_mth1~3`, `stock_knd`) 비집계 | `stg_tesstk` | 취득·처분·소각 | 비집계 행수 |
| `audit_opinion` | (corp, bsns_year, reprt_code, `bsns_year_label`) | `stg_audit` | `adt_opinion`·class | 93,037 |
| `dividend_event` | (corp, bsns_year, reprt_code, `stock_knd`) | `stg_dividend`(`se` wide) | `dps_krw`·`cash_total_krw`·`yield_pct`·`payout_pct` — 기준일·락일 없음 | distinct |

**4B′ = S16B(대기, 문서층 P3)**: `stg_doc_form_cell` 서식표(2011-03 서식 13종 일괄 도입)로 6테이블에 `src='form'` 축 추가 → E01~E04·I03~I05·V06 시작연도 2015 → 2011.

### 4-6. 5단계 — S17 컨센서스 · S18 의견

**`consensus_daily`** — grain (`ticker`, `obs_month`, `target_period`, `metric`, `src`) · 파티션 `year(obs_month)`(PIT 축 아님). wise = min(fetched_date, ≥ 2026-09-01) / v3 = `collected_date`(NULL → `date` default + `coverage_degraded`), v3 wide 8 → metric unpivot(revenue·op·ni·eps·per·bps·pbr·roe_pct), v3 min/max NULL, obs_month = 월 내 min(`date`). `n_analyst` 없음. **`obs_month` 날짜 축 금지**. `target_period` 그대로 노출(12M forward 합성은 팩터층, FIELD_MAP). EG1 distinct(…, src) · EG6 최초 관측 독립 재계산 · EG8 v3⋈wise 겹침(09-01~02).
**`opinion_daily`** — (`ticker`, `obs_date`, `src`) · summary(810종목·2일) ∪ v3 opinions. **`opinion_broker_daily`** — (`ticker`, `fetched_date`, `broker`, `opinion_date`) · 9,717(1:1). WISE 커버 804.

### 4-7. 6단계 — S19 `dataset_profile` · whole

grain (`field_id`) — 어댑터 `list_fields()` 의 원천. 컬럼 `field_id`(FIELD_MAP 어휘 + equity 내부 스코프)·`table_name`·`column_scope`·`source_stage_tables` TEXT[]·`label`·`unit`·`value_type`·`frequency`·`available_date_basis`·`recommended_lag_sessions`(**세션**)·`recommended_lag_days`·`disclosure_basis`·`evidence`·`point_in_time` BOOL·`requires_confirmation` BOOL·`supported_cell_kinds` TEXT[]·`coverage_from`·`coverage_to`·`coverage_basis`·`estimated_coverage_pct`·`coverage_by_mktcap_quintile` DOUBLE[5]. EG2(SQL): lag_known=false stage 테이블 T 중 `source_stage_tables ∋ T ∧ recommended_lag_sessions ≥ 1` 행이 없는 T = 0. 초기 행은 v1.1 §4-7 표를 세션 단위로 옮기고 `universe_daily·core`·`mktcap,adv20` 행을 추가한다(EQD-07).

### 4-8. 6단계 — S20 `factor_readiness` · whole (신설)

grain `factor_id`(FACTORS 54 + 레지스트리 50 대응 컬럼 `registry_factor_id`) — 컬럼 `required_columns`(equity table.column / view)·`required_field_ids`(FIELD_MAP)·`status ∈ {ready, blocked}`·`blocked_reason`·`owner ∈ {equity, factor_layer, unavailable}`·`first_usable_date`·`caveat`·`evidence`. **EG10**: 54행 전부 존재 ∧ blocked 는 reason·owner 필수 ∧ ready 는 first_usable_date NOT NULL ∧ ready 수 ≥ baseline(초기 36 = 워크플로우 제안 §1-8). 인계 문서의 "팩터 × 컬럼 × 시작일 표" 는 이 테이블을 가리킨다.

---

## 5. 뷰 · 매크로 (`equity.duckdb`, 기본값 `:=`, 랙은 본문 서브쿼리)

```sql
v_universe(d, policy := 'all', lag_override := NULL)         -- 정책 미적용 기본, 컬럼군별 세션 랙
v_cum_adj(asof, lag_override := NULL)                         -- effective_date ≤ asof ∧ available_date ≤ asof − lag, base = asof
v_adj_price(asof, lag_override := NULL)                       -- adj_close = close × cum_price_factor  → FIELD_MAP `price.adj_close`
v_adj_volume(asof, lag_override := NULL)                      -- adj_volume = volume_shr × cum_share_factor
v_fin_latest(asof, lag_override := NULL, vintage := 'restated')  -- CFS 우선(fs_div_used), ttm 은 4분기 전부 보일 때만, has_correction = (first_correction_dt ≤ asof − lag)
v_consensus(asof, lag_override := NULL)                       -- 관측점별 최초 관측, target_period 노출
v_firm_mktcap(d)                                              -- Σ 종류주 시총 (독립 재계산 게이트 EG3-P05)
```

패널 질의(`[start, end]` × 종목 × 필드, 세션별 컷오프)는 어댑터가 `trading_calendar` 를 돌며 위 뷰를 호출하거나 세션 조인 SQL 로 구현한다(E-1). 카탈로그 `snapshot_id` = 전 테이블 build_id 정렬 해시.

---

## 6. 팩터 ID × 계정 매트릭스 — v1.1 §6 유지 + `gross_profit`(fin_map 실재) 추가. 판정의 정본은 `factor_readiness`(§4-8).

---

## 7. 소비자 계약 `[결정 5 재기술 · 6 · 7]`

| 소비자 | 계약 | equity 어댑터 |
|---|---|---|
| 워크벤치 `EquityDataPort` | `snapshot()`·`list_fields()`(`DatasetFieldProfile`)·`load_universe(UniverseHistoryQuery)`·`load_panel(ResearchPanelQuery: start·end·security_ids·field_ids·lag_overrides)` | `adapters/outbound/equity_duckdb`(S21) — `dataset_profile` → `DatasetFieldProfile`, 세션별 컷오프 = `trading_calendar` 역산 |
| `RawObservationPort` | (as_of, security_id) 별 `RawFieldValue(field_id, value, available_date)`·`universe_member`·`sector_id`·워밍업 세션 · 값 합성 금지 · `available_date ≤ as_of` 위반은 어댑터 버그 | 같은 셀에 `EquityDataPort` 와 같은 값·공개일(계약 테스트) · `previous_weight=0.0` |
| `FactorObservationPort`·`FactorMetadataPort`·`BacktestDataPort` | 팩터 그래프 평가·메타·백테스트 데이터셋 | S21 |
| 커널 `BarSource`·`UniverseSource`·`CorporateActionSource` | 원주가 Bar · `Membership` · `CorporateActionEvent(ts=effective_date, ratio=share_factor)` | `backtest_engine/adapters/equity_duckdb.py`(S07, pyarrow) · span 단위 질의(사전 제외 금지) · reference/volume=0 행 미방출 · `available_date > asof − lag` 계수 미방출 · `venue='XKRX'` |

어휘(FIELD_MAP §1): `security_id = ticker:span_seq` · `universe_id` `krx.all`/`krx.common-stock`/… · `market='KRX'` · 벤치마크 `idx:` 접두 · 결측 `fill_kind` → `CellKind` · `snapshot_id`. **`price.close` = 원주가, `price.adj_close` = `v_adj_price`**(결정 6). 미지원 필드 9 는 `list_fields()` 에서 `unavailable`(mock fallback 금지).

엔진 측 한계: `HistoryStore` 원주가만 · SHARE_COUNT_CHANGE 알림 전용 · rights/spinoff 불연속 실현손익 · `MonthEndSession` 미구현 · 레지스트리 수익률 팩터가 `price.close` 를 요구(결정 6 이슈).

---

## 8. 게이트 — `EQUITY_GATES.md` v1.0 이 사양(테이블별 매트릭스 §2 · EG1 등식 §3 · 픽스처 FX §4 · 실행 순서 §7).

---

## 9. 결정 기록

| # | 선택 | 대안 | 이유 · 근거 | 상태 |
|---|---|---|---|---|
| 1 | parquet 정본 + `equity.duckdb` 매크로 카탈로그 | duckdb 단일 파일 | P1a~d · GC 뒤 재생성 | 확정 |
| 2 | stage 골격 + `inputs`·`_pinned/`·`_asof/` | — | manifest GC · EG5c | 확정 |
| 3 | FACTORS 정본 54 + 레지스트리 50 대응(`factor_readiness`) | 평면 F## | — | 확정 |
| 4 | 유니버스 사실+상태 + `universe_policy` 보관 | 파일럿 플래그 | P6·P12 | 확정 |
| 5 | **커널 3포트 + 워크벤치 5포트, 단일 어댑터** | v1.1 "팩터층 주입" | `application/*/ports/outgoing/*.py` 실재, `build_container(equity_adapter="mock")`, contract `ADAPTERS` | 확정(재기술) |
| 6 | `price.close` 원주가 · `price.adj_close` 조정가 두 필드 | 원주가만 | 분할 구간 모멘텀 오류가 게이트를 통과 | 확정(09-05) |
| 7 | backend optional-dependency `equity=["duckdb>=1.5"]`(워크벤치 어댑터, S21 반영) | pyarrow 패널(성능 미확인) | 카탈로그 매크로 호출 | 확정(09-05) |

---

## 10. 실측 기록 — v1.1 P1~P12 유지(`git show 8bf6ac3:workspace/dongmin/docs/EQUITY_DESIGN.md` §10) + 09-05 추가

| # | 항목 | 결과 |
|---|---|---|
| P13 | 문서 원본 재무표 | ZIP 본문에 재무제표 표 값 존재(부방 2024.12 원본·정정) |
| P14 | 문서층 P1 테이블 | `stg_doc_meta` 242,196(main 170,762: 분기 77,516·사업 51,063·반기 40,332; `period_from` 채움 168,938 = 파싱 ok 전건; XBRL 그룹 ≥1 153,970, 연도별 86.7~97.5%) · `stg_doc_section` 7,929,624(`fin` 252,238 = '4. 재무제표' 126,124 + '2. 연결재무제표' 126,112, XBRL 그룹 844,438) · `stg_doc_correction` 17,600(filed parsed 15,225 / unparsed 2,375; 재무 항목 포함 2,238; (corp, kind, filed_date) 조인 유일 14,026 / 0 1,199) · `stg_doc_parse_log` 242,196 · 정정 정기보고서 접수 24,285 |
| P16 | 08-20 이후 date | `stg_master_daily` max 2026-09-02(스냅샷 2일) · `stg_flow_split_daily` 08-14 · `stg_credit_daily` 08-18 · `stg_short_daily_kis`·`stg_loan_daily_kis` 08-14 · `stg_price_daily` 08-20 → 캘린더 gap 축 폐기 |
| P17 | **S02 서버 실측(09-05, T0 코드)** | 입력 고정 `stg_index_daily` b_20260902T162449_297822Z · `stg_listing_daily` b_20260902T162451_832365Z · `stg_etf_price_daily` b_20260902T162435_439598Z. `trading_calendar` 4,094행(0.2s·RSS 76MB, EG0·EG7·EG1·EG3·EG17·EG4 pass, EG2 skip(dimension)) · `index_daily` 347,821행·17파티션(1.1s·RSS 295MB, EG2 pass) · `security_span` **5,090행**(= 3,672 + 1,416 + 재상장 2; Σ n_days 10,890,251 = listing ∪ etf 행수; EG3x 비중첩·캘린더 완결 4,094/4,094 · EG16a respan 2; 4.4s·RSS 694MB). **재현성 EG5a pass 3/3**(같은 inputs 재빌드 해시 동일). baseline 초기값: `calendar_start` 2010-01-04 · `backfill_end` 2026-08-20 · `respan_count` 2 · `asof_sample_dates` 5 · `asof_sample_tickers` 20 |
| P18 | **S01 서버 실측(09-05)** | 입력 고정 `stg_corp_map` b_20260902T164506_359427Z · `stg_company` b_20260902T164505_780832Z · `stg_delisted_master` b_20260902T163312_154524Z(+ S02 와 같은 listing·etf·index). `corp` **3,478행**(= corp_map DISTINCT corp_code; EG3_corp `n_financial` 470, 0.1s) · `security` **5,088행**(= listing ∪ etf 티커; `n_delisted` 1,164 · `n_delist_conflict` 13 · `n_corp_code_null` 1,610, 1.7s) · `corp_ticker` **5,088행**(KR7 3,632 전부 corp 링크 `map_rate` 1.0 · `n_corp_code_null` 1,416, 0.4s). 첫 `corp_ticker` 빌드는 EG3 FAIL(`n_kr7_group_common_not_one` 474) — `is_common` 을 `sec_type='common'` 으로 정의해 스팩·리츠·펀드 그룹의 보통주가 0 이었다 → **`is_common = stkcert_tp='보통주'`(주식종류)** 로 재정의, 비KR7 은 `common_ticker` NULL 고정 후 pass. EG4 픽스처 corp 9 · security 26 · corp_ticker 22 전부 일치. **재현성 EG5a pass 3/3**(같은 inputs 재빌드 파티션 해시 동일). |
| P15 | 소비자 계약 | 워크벤치 포트 `EquityDataPort`·`RawObservationPort`·`FactorObservationPort`·`FactorMetadataPort`·`BacktestDataPort` · `build_container(equity_adapter="mock")` 외 거절 · contract `ADAPTERS=[mock]`, `UNIVERSE="krx.common-stock"`, `MARKET="KRX"` · 레지스트리 50 팩터 · field_id 42 · mock 프로필 `recommended_lag_sessions` |

---

## 11. 미결 · 한계

- TR 불가(배당락 원천 없음, PR 고지) · 재무 원본 판본은 4C(P2) 전까지 `api_restated` 만(PIT = 비랜덤 결측, 교차표 인계) · KOSPI 관리종목 신호 창(비대칭) · 정리매매 개시 공시 349 ≈ 32% · 키움 샤드 로그 2일·1,070 티커 미커버 · KIS 주체 대응 검증축 없음 · 결산월 변경은 문서 `period_to` 로 해소(4A) · pre-2010 격리 · 컨센서스 선택편향(804/787) · 업종 PIT 없음(`classification.sector` 는 현재값 라벨, `point_in_time=false`) · 미지원 필드 9(대량매매·반대매매·담보·잠정실적·지수구성·감성·차입금 계열) · 엔진 저장소 이슈(레지스트리 `adj_close`·`MonthEndSession`·enum 확장·거래비용 규칙표).
- stage 요청(문서층): 모집단 술어 화해(최우선) · `scope_basis`·`period_slot_basis`·`IS1/2/3` 의미·`form_cell.section_code`·`n_period_tu`·D9/D13 baseline 등재·`corr_item_hdr.json`.

---

## 12. 검수 기록

**v1 → v1.1 (09-03)**: 3관점 30건 — v1.1 §12 참조(`git show 8bf6ac3:…EQUITY_DESIGN.md`).

**v1.1 → v1.2 (09-05)**: 제안서 4건(`reviews/2026-09-05-equity-*.md`) 반영. 채택: `disclosure_version` grain `rcept_no`·링크 흡수·`has_correction` 팩트화 · `period_end` 문서 정본 · 4A/4C/4B′ · `universe_daily` S03/S03B · coverage_gap 행 폐기 · `krx_kis_close_ratio` 삭제 · `dataset_profile` 세션 랙·field_id·cell kinds · `factor_readiness`+EG10 · 소비자 계약 재기술(워크벤치 5포트) · `_asof/`·`<col>_available_date` · `sec_type='other'` 격리 폐기 · 어휘(security_id·universe_id·benchmark). 기각·조정: 엔진 별도 저장소(같은 모노레포) · consensus 4테이블 부재(실재) · 정정 링크 92% 를 위험으로 본 것(같은 축 실측, C340 94.8% 와 정합).
