# v3 멀티팩터 파이프라인 → quant-ledger 통합 플랜 작성용 컨텍스트 (2026-09-24)

> **이 문서를 읽는 세션이 할 일**: 이 워크트리(`~/orca/workspaces/Quant_study/v3-merge`, 브랜치 `feat/v3-merge`, 기준 `feat/wics-ledger` 7423c2c = 서버 배포 rev)에서
> **"v3 모델 파이프라인을 quant-ledger(`database/`)와 합치는, 확장성까지 고려한 플랜"** 을 `database/docs/plans/2026-09-2x-v3-merge.md` 로 세운다.
> 사용자 규칙: **플랜 md 먼저 → 검증 게이트 정의 → 승인 뒤 구현**. 플랜은 `docs/plans/2026-09-20-wics-weekly.md`(상태 블록·게이트 표·태스크 단위) 형식을 따른다.
> 이 문서는 결정이 아니라 **재료**다. 아래 §6 "후보 구조" 는 제안이며 플랜에서 채택·기각을 밝힌다.

## 0. 사용자가 이미 결정한 것 (어기지 말 것)

| 날짜 | 결정 | 출처 |
|---|---|---|
| 09-11 | v3 는 "전부 옮기되 잡 하나씩, 각 잡은 병행 비교 통과 뒤 교체". 결과물(텔레그램·위키)은 유지하고 뒤에서 갈아 끼운다 | `docs/plans/2026-09-11-daily-incremental-v2.md` §6 (잡별 로드맵 표·D.2 DB 동결 규칙) |
| 09-24 | **"v3 모델을 죽이고 quant-ledger 를 원장으로 v3 모델에 들어가는 데이터를 여기서 가져다 쓴다"** — 죽이는 것은 v3 의 *수집*, 남기는 것은 *모델과 그 뒤 산출물* | 이 세션 |
| 09-24 | "워크트리 하나 파서 v3 모델 파이프라인을 quant-ledger 폴더와 합치는 **확장성까지 고려된** 플랜" | 이 세션 |
| 09-16~20 | 결정 11: KRX 애프터마켓 뒤 키움 저녁 수집 21:05, 잠정 빌드 21:20, 종가 대조는 기록만(키움 종가 = 장후 체결가), 워치독 10:00/21:50/23:30 | `docs/DECISIONS_PENDING.md` 결정 11 |
| 09-20 | WICS 백필 없음(09-18 부터 주 1회), 섹터 축은 그 뒤에만 | `docs/WICS_PROBE.md` §8 |
| 09-08 | WISE 직접 수집이 정본, v3 컨센서스 미러는 08-31 동결(재개 안 함) | `docs/START_HERE.md` |
| 상시 | 조용한 실패 금지(결정 V2-7) · v3 서버 파일은 읽기 전용 · 키 값은 어디에도 출력 금지 · 배포는 `scripts/deploy.sh --apply [--allow-branch]` 만 · 서버 데이터 정본은 kael-server | 메모리·`.claude/rules` |

## 1. quant-ledger 현재 상태 (2026-09-24, 배포 rev 7423c2c)

### 1-1. 층과 산출물 (서버 `~/quant-ledger`)
- **원장 6 DB** `data/raw/`: `krx.db`(시세·지수·마스터, KRX Open API, T+1 08:00) · `kiwoom.db`(ka10060 수급·ka10014 공매도·ka10008 외국인보유·ka20068 대차, REST) · `kis.db`(신용잔고 T+3) · `dart.db`(공시·정기보고서 7종·DS005 15종·문서 ZIP 17만) · `wisereport.db`(컨센서스·의견·재무요약 6화면, 09-01~) · `wiseindex.db`(WICS L1 10·L2 28 주간, 09-18~). 규약: 원문 보존·append-only·`collected_at`·판본 공존. 표별 상세는 `docs/STAGE_SPEC.md` §2, `docs/DATA_CATALOG.md`.
- **stage 67표** `data/stage/<table>/v=<build>/` (`src/stage/rules_*.py`, 게이트 G0~G9, 건전성 C1~C6 `src/stage/health.py`·`freshness.py`, baseline `data/stage/baseline.json`). 정의는 "출력 행은 원장 한 행의 함수".
- **equity 30표** `data/equity/<table>/v=<build>/` + 카탈로그 `data/equity/equity.duckdb` **매크로 9** (`v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap`·`v_adj_price_fwd`·`v_adj_volume_fwd`·`v_consensus`·`v_fin_latest`·`v_sector`; 전부 `as_of` 인자, PIT). 규칙 버전 e1.17.0 (`src/equity/rules_s*.py`, 게이트 `docs/EQUITY_GATES.md`, 필드 계약 `docs/EQUITY_FIELD_MAP.md`, `factor_readiness` 54 팩터 선언).
- **인계 신호** `data/deliver/latest_evening.json`·`latest_morning.json`(+`history/<D>_<basis>.json`): `{"date","basis","generated_at","stage_snapshot_id","stage_builds","equity_builds","health":{"stage","equity"},…}`. `data/deliver/ledger_evening.json` = 저녁 원장 완료 신호(kiwoom_rc·wise_rc·dart_rc). 스키마 정본은 Kael-alpha 인계 §2-5.
- **잠정판 vs 확정판**: 저녁 `basis='evening'` 은 T 행이 키움 종가(장후 마지막 체결가)·거래량뿐이고 시고저·주식수·시총 NULL, 유니버스·캘린더는 T-1 까지(`docs/EQUITY_DESIGN.md` §13-2). 아침 `basis='krx'` 가 KRX 공식값으로 자연 교체. 백테스트 어댑터는 `basis!='krx'` 를 버린다.

### 1-2. 시간표 (KST, 실측 09-17~24) — `README.md` 크론표가 정본
| 시각 | 잡 | 실측 |
|---|---|---|
| 06:00 | `daily_ledger.sh`: 캘린더 동기화 → 키움 마스터·대차 → KIS 신용 → DART 재스윕·상세·문서 → company gap | 07:10~07:15 종료 |
| 08:10 | `daily_build.sh`: KRX D 도착 대기 → ka10008 → 머지(거래량 전건 일치) → 원장 건전성 → `build_morning.sh`(stage 45~59분 + equity 11분) → 일일 리포트 | 확정판 09:20~09:35 |
| 18:05 | `daily_evening.sh`: DART ∥ WISE(18:10) ∥ 키움 21:05 대기 → 원장 직행(`--commit`, 커버리지 게이트) | 21:20 |
| 21:20 | `build_evening.sh` → `build_chain.sh evening`(한도 21:45) | 잠정판 22:25~22:41 |
| 워치독 | 10:00 morning_build · 21:50 evening_ledger · 23:30 evening_build · 토 11:30 wics_weekly | |
| 토 03:00/10:00 | `wics_weekly.sh` / `--retry` | 09-26 첫 자동 |
| 토 03:30 / 일 04:30 | 백업(주 1회 최신 1세트) / gc | |
- stage 전량이 계획(22.5분)보다 길다(45~63분). **B-22 저녁 단축 빌드**(키움·WISE 관련 표만)가 다음 과제 — 잠정판 ≈21:45 로 당긴다. v3 `adj_prices` 가 22:05~22:35 에 겹쳐 09-22 타임아웃 났다(4코어).

### 1-3. 운영 규약 (플랜이 그대로 써야 하는 것)
- 배포: 로컬에서 `database/scripts/deploy.sh --apply --allow-branch <branch>` (깨끗한 트리·`uv run --project backend pytest database/tests` 전량 통과 가드·`~/quant-ledger/DEPLOYED.json` 기록). 로컬 환경: `uv sync --project backend --all-extras` 뒤 그 명령(2026-09-24 기준 1,301 passed).
- 체인 스크립트 골격(`scripts/daily_ledger.sh`·`build_chain.sh`): raw/build 락(flock) · `step` 함수 · 런로그(`daily/runlog.py`, `data/raw/daily_run.db`) · 실패는 crit, 건너뜀·완료도 info(`scripts/notify.sh`, Telegram) · 워치독(`scripts/watchdog.sh`)이 산출물로 재판정.
- 원장 건전성 `src/daily/ledger_health.py`(소스별 REQUIRED/WARN/HALT), stage `health.py` C1~C6, equity 게이트 EG0~EG21, 카탈로그 EG5c(as-of 표본, 승인 기록 `data/equity/_asof/_approvals/`)·EG11.
- 키·토큰: 서버 `~/kael-system-v3/.env` 를 quant-ledger `src/api.py` 가 읽는다(DART 키 폴백, 키움 앱키 **v3 와 공유**, `JEV_API_KEY`(TypeSafe Jev) 포함). 토큰 캐시는 각자(`src/.kw_token.json` vs v3 `data/.kiwoom_token.json`).
- 문서 규약: 플랜 상태 블록은 작업 단위마다 갱신, 결함 보고는 4요소(상황·인풋·위치·위험성, `.claude/rules/pr-review.md`), 한글.

### 1-4. 알려진 미결 (플랜이 흡수할 것)
| ID | 내용 |
|---|---|
| B-22 | 저녁 단축 빌드(키움·WISE 관련 stage 표만) — 잠정판 22:40 → 21:45 |
| B-23 | equity `consensus_daily` wise 축에 **영업이익·순이익** 이 없다. stage `stg_consensus_annual`(op·ni, 일 ≈800종목)·`stg_consensus_matrix`(9계정 × current/1w/1m/3m/1y, 일 794종목) 에는 있다 — **v3 리비전 팩터의 재료가 바로 이 표** |
| B-24 | 저녁 T 행에 주식수·시총이 없다 → 순매수/시총·밸류는 "전일 KRX 주식수 × T 키움 종가" 규칙 필요(소비자 규약) |
| B-39 / B-1 | `dataset_profile` 이 `sector_snapshot`·`coverage_daily` 를 못 싣는다(S19 `SOURCE_TABLES` 고정 목록) |
| — | 재무 정의: 우리 `fin_std` 는 DART 원본 PIT(gross_profit·total_asset·cf_operating_ytd·capex_ytd·total_liab·borrowings·depreciation·…), v3 `financial_summary` 는 WISE 요약. stage `stg_fin_wise`(cF3002/4002) 가 있으나 equity 에 안 올라가 있다 |
| — | 배당수익률·EV/EBITDA 커버가 v3 보다 거칠다(DPS 는 사업보고서 접수일, 차입금·감가상각은 2016-03~) |
| — | 국면 입력 3개(`market_investor_flows`·`program_trading`·`macro_data`)는 v3 만 수집한다(하루 ≈6콜) — v3 수집을 죽이기 전에 이관 |
| TECH_DEBT | `docs/TECH_DEBT.md` B-1~B-39 전부 참고 |

## 2. kael-system-v3 현황 (서버 `~/kael-system-v3`, 로컬 `~/Desktop/kael-system-v3` — 읽기 전용)

### 2-1. 코드 규모 (backend/, 줄)
tests 18,433 · news 4,757 · db 3,325 · insight 2,685 · briefing 2,680 · clients 2,430(키움·KIS·naver·DART, 키움 rate limiter 5/s) · research_center 2,314 · **scoring 1,620**(`engine.py`, `factors/`, `v2_engine.py`·`v2_factors/`·`v2_data_loader.py`·`v2_repo.py`·`v2_export.py`·`v3_export.py`) · dart 1,207 · trade 1,124(정지) · research 887 · hypothesis 790 · pipeline 668 · scripts/ 7.1k(`job_runner.py` 체인 정의·`backfill.py`).

### 2-2. `daily_all` 체인 (`scripts/job_runner.py`, 크론 20:05 월~금, `flock /tmp/kael_v3_daily_all.lock`)
| 순서 | 잡 | (재시도, 타임아웃, critical) | 실측 |
|---|---|---|---|
| 1 | calendar_refresh · holiday_gate | | |
| 2 | **daily_pipeline** — 키움 종목마스터(20:05)·시세·수급, KIS, naver/WISE, 국면 입력 3개 수집 | (3, 3h, critical) | 20:05 → 21:52~22:07 |
| 3 | **adj_prices** — v3식 수정주가(`backfill.py --mode adj_prices`) | (1, 1,800s, non-critical) | 24~30분, 09-11·09-22 타임아웃(우리 저녁 빌드와 겹침) |
| 4 | scoring · scoring_v2 · export_scores | | 22:37~22:38 |
| 5 | insight_pipeline · wiki_ingest · wiki_lint | | 22:38~ |
다른 v3 크론(그대로 둘 것): research 20:30, broker_research 21:00(월~금), news ingest 30분, briefing 아침(07:00 KST)·점심·마감, events 04:30/07:40, dart 07:45/18:15, web_confirm, hypothesis shadow, backup 03:00, 주간 잡들. 크론 원문은 서버 `crontab -l`(quant-ledger 줄과 한 파일).

### 2-3. `data/quant.db` 표 (행수 09-24)
`daily_prices` 1,052,523(stock_code·trade_date·OHLCV·amount·**adj_close**, 수집시각 열 없음) · `investor_detail_flows` 1,015,414(13주체) · `financial_summary` 25,660(2,554종목; revenue·op·ni·eps·bps·per·pbr·roe·roa·debt_ratio·fcf·capex·op_margin·ni_margin·dividend_yield·shares·ev_ebitda·yoy·gross_profit·total_assets, period·period_type) · `consensus_revision_daily` 73,224 · `consensus_revision_compare` 800 · `consensus_annual` 17,984 · `analyst_opinions` 295,440 · `stocks` 2,557(sector = KRX 업종) · `score_history` 160,181 · `score_history_v2` 298,953 · `market_regime`·`market_inflection` 421 · `market_investor_flows` 814 · `program_trading` 211 · `macro_data` 1,063 · `sector_daily` 5,549 · `research_reports` 10,144 · `broker_reports` 1,434 · `major_shareholders` 651,706 · `market_indices` 2,844 · `pipeline_runs` 2,300. 다른 DB: `dart.db`·`news.db`·`hypotheses.db`·`trade.db`(정지)·`agenda/board/kg/market.db`(빈 것/실험). D.2 처리 표는 플랜 v2 §6.

### 2-4. 모델 정의 (`config.yaml` `scoring:`)
- 유니버스 KOSPI·KOSDAQ, `min_market_cap: 1000`.
- **momentum .30**: r1m .35 · r3m .25 · r6m .20 · r9m .12 · r12m .08 (`daily_prices.adj_close`).
- **revision .30**: metrics operating_profit .40 · net_income .60 × periods 1w .25 · 1m .45 · 3m .30 (`consensus_revision_daily/compare`).
- **flow .20**: inst_5d .25 · inst_20d .15 · for_5d .25 · for_20d .15 · pe_5d .10 · pe_20d .10 (`investor_detail_flows`, 시총 분모).
- **quality .10**: gpa .20 · roa .15 · fcf_assets .15 · debt_ratio .15 · gpa_change .15 · std_20d .20 (`financial_summary` + 가격).
- **valuation .10**: per .30 · pbr .30 · ev_ebitda .25 · dividend_yield .15.
- `scoring_v2:` normalization percentrank, 별도 팩터 세트(파일 뒤쪽 — 플랜 작성 시 원문 확인). 스코어링 코드가 읽는 표: `daily_prices`·`investor_detail_flows`·`consensus_annual`·`stocks`(+`financial_summary`), 출력 `score_history`·`score_history_v2`, 내보내기 `v2_export.py`·`v3_export.py`(엑셀·위키).
- 09-08 리서치(`~/orca/projects/Kael-alpha/handoff/2026-09-08-v3-factor-test/`): 가중치 역순(모멘텀 .30 이 음), 퀄리티·밸류가 신호 전부 — 메모리 `v3-scoring-improvement-2026-09`.

## 3. v3 팩터 ↔ quant-ledger equity 대응 (09-24 검증)
| v3 | 우리 | 상태 |
|---|---|---|
| momentum r1m~r12m ← adj_close | `price_adj_daily`·`v_adj_price_fwd(as_of)` (M01 ready, 2010~) | 즉시 |
| revision op/ni × 1w/1m/3m | stage `stg_consensus_matrix`(영업이익·순이익 등 9계정 × current/1w/1m/3m/1y, 794종목/일) — equity 미배선(B-23) | stage 직독 즉시 / equity 는 S17 수정 |
| flow inst/for/pe 5d·20d ÷ 시총 | `flow_daily`(13주체: orgn·frgnr_invsr·penfnd_etc…) + `price_daily.mktcap_krw` (F01·F03·F04) | 즉시 + B-24 |
| quality gpa·roa·fcf_assets·debt_ratio·gpa_change·std_20d | `fin_std`(gross_profit·total_asset·net_income·cf_operating_ytd·capex_ytd·total_liab·total_equity)·`v_fin_latest`·가격 (Q01~Q06) | 즉시, 정의 차이 있음 |
| valuation per·pbr·ev_ebitda·dividend_yield | V01·V02·V05(2016-03~)·V06 + `dividend_event` | 즉시, 커버 거칠음 |
| universe·min cap | `universe_daily`(sec_type≠etf)·`mktcap_krw` | 즉시 |
| (v3 없음) 섹터 | `sector_snapshot`·`v_sector(as_of)` WICS L1/L2 (09-18~), v3 `stocks.sector` 는 KRX 업종 | 체계 다름 |
| 이력 | 우리 2010~ / v3 365일 | 백테스트 가능 |

## 4. 확장성 요구 — 플랜이 답해야 할 질문
1. **모델 레지스트리**: v3 `scoring`·`scoring_v2`·향후 Kael-alpha 엔진이 같은 입력 계약 위에서 병행·버전 관리(모델 id·규칙 버전·입력 판 id·basis) 되는가. 점수 표는 판(`v=`)·MANIFEST·게이트를 갖는 equity 표 규약을 따르는가(예: `data/model/<model_id>/v=…`).
2. **입력 계약**: 엔진은 equity 매크로(`as_of` PIT)만 읽는가, parquet 직독인가. 저녁(basis evening, T 행 장후가·시총 NULL)·아침(krx) 두 판을 같은 코드가 도는가(B-24 규칙 위치).
3. **출력 계약·전달층**: `score_history` 호환 스키마(병행 비교용) + 판 추적 컬럼, Telegram·위키·엑셀 내보내기를 엔진과 분리(플랜 v2 §6 "전달층"·"리포트 템플릿 레지스트리").
4. **잡 러너 통합**: v3 `job_runner.py` 체인 개념을 우리 `build_chain.sh` 규약(락·step·런로그·notify·워치독·완료 가드·`_READY.json`)으로 흡수하는가, 파이썬 러너로 통일하는가. 트리거는 크론 시각이 아니라 인계 파일(`latest_<basis>.json`) 기준.
5. **병행 비교 도구**: 같은 날 v3 자체 수집 점수 vs 우리 데이터 점수를 종목·팩터별로 대조하는 스크립트와 통과 기준(5거래일).
6. **컷오버·동결**: v3 `daily_pipeline`·`adj_prices` 크론 제거 순서(백업), 국면 입력 3개 이관, D.2 DB 동결(`data/legacy/kael-v3/<날짜>/`), 키 `.env` 이관, 키움 앱키 단독 사용 뒤 저녁 수집 21:05 → 20:15 재조정.
7. **자원**: 4코어 서버에서 stage 60분·equity 11분·v3 잡들이 겹치지 않는 시간표(B-22 선행 여부).
8. **테스트·문서**: 절단본 픽스처(`tests/fixtures/stage_slice`)로 엔진 왕복 테스트, 골든 점수 픽스처, 문서(EQUITY_* 처럼 `MODEL_*.md`).

## 5. 참고 파일 (읽을 순서)
1. `database/docs/plans/2026-09-11-daily-incremental-v2.md` §2(하루 흐름)·§5(페이즈 C)·§6(컷오버 로드맵·D.2)·§8.
2. `~/orca/projects/Kael-alpha/handoff/2026-09-10-evening-scoring-pipeline/HANDOFF.md` §2-3~§2-5·§3 (인계 계약·소비 규약·결정 11 반영).
3. `database/docs/EQUITY_DESIGN.md` §4·§5·§7·§13, `EQUITY_GATES.md`, `EQUITY_FIELD_MAP.md`(§6 sector), `EQUITY_HANDOFF.md`.
4. `database/docs/plans/2026-09-20-wics-weekly.md`(플랜·게이트 형식 예), `2026-09-19-pipeline-audit-fix.md`(감사 결함·새 게이트 C6·EG21·롤백 규약).
5. `database/docs/DECISIONS_PENDING.md` 결정 8·9·10·11, `TECH_DEBT.md`, `START_HERE.md`, `DATA_CATALOG.md`(MS-08·MS-09).
6. v3(읽기 전용): `~/kael-system-v3/config.yaml`, `scripts/job_runner.py`, `backend/scoring/*`, `backend/pipeline/*`, `backend/db/`(스키마), `backend/clients/kiwoom/`.
7. 메모리(`~/.claude/projects/-Users-claudeoscarmonet-Desktop-Quant-study/memory/`): `daily-incremental-plan`, `evening-scoring-goal`, `kael-alpha-handoff`, `v3-scoring-improvement-2026-09`, `krx-factor-test-2020-2026`, `wics-api-probe`, `jev-typesafe-probe`.

## 6. 후보 구조 (제안 — 플랜에서 채택·기각)
- `database/src/model/`: `registry.py`(모델 id·버전·입력 계약·출력 스키마 선언), `inputs.py`(equity 카탈로그 매크로로 as_of·basis 별 입력 프레임 — B-24 규칙 여기), `engines/v3_compat/`(v3 `backend/scoring` 이식, 가중치는 `config/model/v3.yaml`), `engines/v3_v2/`, `export.py`(score_history 호환 + 판 추적), `compare.py`(병행 비교).
- `data/model/<model_id>/v=<build>/` + MANIFEST + 게이트(EG 스타일: 유니버스 등식·결측·순위 안정성·골든 픽스처), `data/deliver/latest_scores_<basis>.json`.
- 체인: `build_chain.sh` 끝(인계 뒤)에 `score_step`(엔진 전부, 실패는 crit 이나 판 인계는 유지) 또는 별도 `scripts/score_chain.sh` 가 `latest_<basis>.json` 을 트리거로.
- 과도기 호환 계층: 우리 equity → v3 `quant.db` 5표(`daily_prices`·`investor_detail_flows`·`consensus_*`·`financial_summary`·`stocks`)를 별도 SQLite 로 매일 생성해 v3 스코어링에 물린다 → 코드 수정 0 으로 병행 비교 → 통과 뒤 v3 수집 크론 제거 → 그다음 엔진 이식. (이 계층은 컷오버 뒤 삭제 대상 — 플랜에 수명을 적는다.)
- 전달층: Telegram(`scripts/notify.sh` 재사용)·위키·엑셀은 `deliver/` 모듈로 분리, 템플릿 레지스트리.

## 7. 게이트 후보 (플랜에서 확정)
G-M1 단위(전량 테스트·ruff·pyright) · G-M2 엔진 왕복(절단본에서 v3 점수 골든 재현, 허용 오차 명시) · G-M3 병행 5거래일(종목별 순위 상관·상위 N 겹침·차이 원인 분류 100%) · G-M4 시간표(잠정 점수 22:xx·확정 09:xx, v3 잡과 자원 비충돌) · G-M5 컷오버(v3 수집 크론 제거 뒤 5거래일 무사고, 국면 입력 이관 검증, DB 동결 체크섬) · 조용한 실패 점검표(§4b 형식).
