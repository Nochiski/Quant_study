# database — 국내증시 원장 · stage · equity 파이프라인

KRX·키움·KIS·DART·WISE 원장 수집기, stage 층 빌더, 문서층(L1) 파서, equity 층 빌더와 설계 문서를 둔다.
데이터는 카엘 서버(`~/quant-ledger`)가 정본이며 이 폴더에는 **코드와 문서만** 둔다
(`database/data/`·`logs/`·`scratchpad/` 는 `.gitignore`).

## 상태 (2026-10-10, Q-4 병합)

| 항목 | 값 |
|---|---|
| 원장 | **일일 체인 가동 중** — 2026-09-17 전체 체인 가동(저녁 슬롯·아침 슬롯). 수집 시각은 아래 "운영 (P6) → 크론 전체표" |
| stage | 66테이블. 저녁 잠정판(`basis=evening`)·아침 확정판(`basis=morning`)을 매일 빌드한다(플랜 v2 페이즈 B) |
| equity | 30표·코드 규칙 **e1.27.0**(`src/equity/model.py` `RULES_VERSION` — Q-4 main 병합판. 브랜치 e1.17~e1.26 · main e1.17~e1.19 두 계보의 이력은 그 파일 주석). 판본을 올리는 규칙은 `EQUITY_HANDOFF.md` §6 |
| 진행 중 | **일일 증분 플랜 v2** `docs/plans/2026-09-11-daily-incremental-v2.md`(상태 블록이 정본) — 페이즈 A 저녁 원장 슬롯·페이즈 B 잠정/확정 빌드 **가동**(09-17 전체 체인), 페이즈 C Kael-alpha 인계 완료, 페이즈 D 범위 확정. 09-19 전수 감사 수정은 `docs/plans/2026-09-19-pipeline-audit-fix.md`. v1 `docs/plans/2026-09-09-daily-incremental.md` 는 P0~P3 기록. 사용자 행동 대기: 키움 앱키 추가 발급(`DECISIONS_PENDING.md` 결정 5 R5 후속) |
| 크론 | **18:05 `daily_evening.sh`**(당일: 키움 투자자·공매도는 21:05 원장 직행 ∥ DART ∥ WISE 스냅샷) · 21:20 `build_evening.sh`(잠정 빌드) · 06:00 `daily_ledger.sh`(키움 마스터, 월요일 DART 번호표, 대차, KIS, 저녁 키움 보강 판정, DART 재스윕) · 08:10 `daily_build.sh`(KRX → 외국인 보유 → 머지 → 건전성 → **확정 빌드 포함**, 09-17 `--no-build` 제거) · 워치독 21:50/23:55/10:30(F-11, 10-06·10-07) · 토 03:30 백업 · 일 04:30 gc. **장 마감 체인 15:41 `postclose_chain.sh close` + 16:30 워치독은 제안(컷오버 PR-8 — 그림자 시작 때 등록, 그때 21:20 잠정 빌드·23:55 워치독은 뺀다)**. 전체는 아래 "운영 (P6) → 크론 전체표" |

## 층 구조

```
원장(raw)   SQLite 5개  data/raw/{krx,kiwoom,kis,dart,wisereport}.db   ← src/backfill_*.py + 서버 크론
    ↓ src/stage  (duckdb → parquet, 게이트 G0~G9, 스냅샷 재현성)
stage       parquet 66테이블  data/stage/                              ← STAGE_SPEC · STAGE_DESIGN · STAGE_HANDOFF
    ↓ src/equity (구조 정책: 조인·격자·PIT·조정계수·유니버스·카탈로그, 게이트 EG0~EG20)
equity      parquet 29표 + equity.duckdb   data/equity/                ← EQUITY_DESIGN · EQUITY_GATES · EQUITY_WORKFLOW
    ↓ 어댑터(워크벤치 5포트 + 커널 3포트, backend/)
```

문서층(L1)은 DART 보고서 ZIP 을 stage 의 여섯 번째 원장 소스로 다룬다 (`DOC_DESIGN`).

## 디렉터리

| 경로 | 내용 |
|---|---|
| `src/` | 수집기(`backfill_*.py`, `api.py`, `dart_universe.py`, `sweep_disclosure.py`, `master_daily.py`), stage 패키지(`src/stage/`, `python -m stage --table <t>`), equity 패키지(`src/equity/`, `python -m equity build|gate|catalog|contract`), 동기화 패키지(`src/ledger_sync/`, `python -m ledger_sync` — 협업자 로컬이 서버 equity 층을 SFTP 로 받아 증분 유지, 동사 목록은 `docs/LEDGER_SYNC.md`), 파일럿 통합층(`build_*.py`·`finalize.py`·`fin_map.py` — STAGE_DESIGN §8 이 파일럿 보존·로직 재사용으로 명시) |
| `scripts/` | 서버 크론·러너: `daily_evening.sh`(18:05)·`daily_ledger.sh`(06:00)·`daily_build.sh`(08:10)·`postclose_chain.sh`(15:41 장 마감 체인 — 아래 "장 마감 체인")·`watchdog.sh`·`daily_wise.sh`(마스터만), 빌드 체인 `build_chain.sh`·`build_evening.sh`·`build_morning.sh`, 운영 `backup_raw.sh`·`gc.sh`·`rotate_logs.sh`·`daily_report.py`·`notify.sh`·`deploy.sh`(아래 "운영 (P6)"), `doc_prepass_daily.sh`·`sync_calendar.sh`, `run_stage.sh`·`run_stage_all.sh`, `run_equity.sh`·`equity_rebuild_all.sh`·`equity_gate_all.sh`, `replay.sh`·`replay_tool.py`(격리 재생 — 운영 루트에 쓰지 않고 출력 루트에 equity→catalog→contract→fi→model 을 다시 지어 표별 해시를 남기고 `--compare` 로 대조, `--stage-at D` 는 그날 인계 이력의 stage 판), `check_baseline_lock.py`, `fetch_equity_local.sh`(운영자 rsync 용), **`ledger_sync.ps1`·`.sh`·`register_daily_sync.ps1`**(협업자 SFTP 동기화 — `LEDGER_SYNC.md`), `run_survey*.sh` |
| `src/daily/` | 일일 증분 러너(`kw_daily.py`·`kis_daily.py`·`dart_daily.py`)와 공용 모듈(거래일 `calendar.py`, 요청 유니버스 `universe.py`, 실행 기록 `runlog.py`, 원장 건전성 `ledger_health.py`) |
| `tests/` | stage·equity 테스트 |
| `survey/`, `survey_out/v2/` | 원장 전 테이블·컬럼 어휘 전수 측정과 결과. stage (p,s)·부호·결측 규칙의 실측 근거. 재생성은 서버에서 `scripts/run_survey_v2.sh` |
| `eval/table_schema/` | 자유 서식 표 스키마 추론 골든셋 100표 (라벨링 대기) |
| `docs/` | 현행 설계·계약 문서 (아래 색인) |
| `docs/plans/` | 구현 플랜 (상태 블록·체크박스로 진행 추적) |
| `docs/reviews/` | 제안서·실측 조사 기록 (플랜의 근거) |
| `docs/archive/` | 2026-08-21~26 조사·판정 기록. 결정의 근거로만 참조하고 갱신하지 않는다 |
| `docs/outlines/` | DART 보고서 내용 구조도 샘플 7건 (`DOC_DESIGN` 참고 산출) |

## 문서 색인

### 입구·상태

| 문서 | 내용 |
|---|---|
| `START_HERE.md` | equity 층 입구 — 목적·지금 상태·다음 할 일 |
| `DECISIONS_PENDING.md` | 사람이 정해야 할 것 (결정 1~11) |
| `TECH_DEBT.md` | 기술 부채와 우선순위 |
| `BLOCKED_FACTORS.md` | 막힌 팩터와 원인 |
| `plans/2026-09-11-daily-incremental-v2.md` | **일일 증분 플랜 v2** — 당일 저녁 스코어링(18:05 저녁 슬롯·잠정/확정 빌드), 페이즈 A~D. 시간표·P4 이후의 정본 |
| `plans/2026-09-09-daily-incremental.md` | 일일 증분 플랜 v1 — P0~P3 결과·결정 R1~R10·결정 6·7 (유효), 시간표·P4~P6 은 v2 로 이관 |
| `plans/2026-09-19-pipeline-audit-fix.md` | 09-19 일일 파이프라인 전수 감사 결함 수정 플랜 — 갈래 6개 수정·배포·실전 게이트 기록 |

### 원장 수집

| 문서 | 내용 |
|---|---|
| `DATA_CATALOG.md` | 수집 대상 데이터 전수 목록, 통합 판정본 (08-21) |
| `FACTORS.md` | 팩터 정본 목록. 수집 우선순위의 근거 (08-25) |
| `COLLECT_PLAN.md` | 백필·일일 증분 실행 계획 (08-21). §4 의 19:00 안은 무효 — 정본은 일일 증분 플랜 |
| `DART_CENSUS.md` | DART OpenAPI 전수조사 종합, 수집 범위 확정 (08-24) |
| `DART_DESIGN.md` | DART 수집기 확정 설계 (08-24) |
| `WICS_PROBE.md` | WICS API 프로브 원문 |

### stage · 문서층

| 문서 | 내용 |
|---|---|
| `STAGE_SPEC.md` | stage 층 계약·실측 사실의 정본 (08-28) |
| `STAGE_DESIGN.md` | stage 구현 설계 v2.2 (09-02) |
| `STAGE_HANDOFF.md` | stage → equity 인계: 읽기 계약·테이블별 메모 (09-03) |
| `DOC_LAYER_KICKOFF.md` | 문서층(L1) 착수 노트 (09-03) |
| `DOC_DESIGN.md` | 문서층(L1) 설계 v1.1 (09-04) |
| `plans/2026-09-04-doc-stage-p1.md` | 문서층 P1 구현 플랜 (PR #54 병합) |

### equity

| 문서 | 내용 |
|---|---|
| `EQUITY_KICKOFF.md` | equity 층 착수 노트 (09-03) |
| `EQUITY_WORKFLOW.md` | 슬라이스 순서·DoD (v1.2) |
| `EQUITY_DESIGN.md` | 테이블·뷰·소비자 계약 |
| `EQUITY_GATES.md` | 게이트 술어 EG0~EG20 |
| `EQUITY_FIELD_MAP.md` | 팩터 field_id ↔ equity 컬럼 대응 |
| `EQUITY_HANDOFF.md` | 빌드·게이트 실패·baseline·서버 반영 기록(§8) |
| `LEDGER_SYNC.md` | 협업자 로컬 ← 서버 equity 층 SFTP 동기화·검증·일일 증분 운영 절차 (09-19) |
| `RATIO_RECOVERY.md` | 무상증자 비율 유도식 검증 기록 |

### 실측 조사 (docs/reviews/)

| 문서 | 내용 |
|---|---|
| `2026-09-05-equity-*.md` (4건) | equity v1.1 → v1.2 제안서 |
| `2026-09-09-daily-findings-A~E-*.md` (5건) | 일일 증분 플랜 근거: 원장 A(KRX·키움·KIS)·B(DART·문서·WISE), stage, equity, 운영·타이밍 |
| `2026-09-09-kis-credit-balance-scope.md` | KIS 신용잔고(`daily-credit-balance`) 정체 판정 — 웹 공표치·내부 정합성 대조 |
| `2026-09-09-p0-task0{6,8,9}-*.md` (3건) | 일일 증분 플랜 P0 조사: baseline 락 불일치(0.6), 키움·KIS 앱키 실사용량(0.8), 서버 전용 파일 처분(0.9) |
| `2026-09-10-intake-audit-summary.md` + `-{A-krx,B-kiwoom,C-kis,D-dart-wise}.md` | 일일 증분 첫 적재분(D=09-09) 검수 — 종합 판정·조치 제안 + 소스별 검사표·재현 SQL |

읽는 순서 — equity 작업: `START_HERE` → `EQUITY_HANDOFF` §0 → `EQUITY_WORKFLOW` §0~§1.
문서층 작업: `DOC_LAYER_KICKOFF` → `STAGE_HANDOFF` §4 → `STAGE_DESIGN` §0·§7·§10 → `DOC_DESIGN`.
일일 증분 작업: `plans/2026-09-11-daily-incremental-v2.md` 상태 블록 → §1 결정 → 현재 페이즈 (v1 은 P0~P3 기록).

### 기록 (docs/archive/)

| 문서 | 내용 |
|---|---|
| `RESEARCH_VERDICT.md` | 5축 웹 리서치 × 설계 대조 판정. 원장 SQLite + 분석층 Parquet 확정 (08-21) |
| `FINAL_SUMMARY.md` | 6축 통합 판정 (08-21) |
| `A1_krx_endpoints.md` | KRX OPEN API 엔드포인트 전수 조사 (08-21) |
| `A2_kiwoom_targets.md` | 키움 대량 백필 대상 TR 정리 (08-21) |
| `FINAL_PLAN.md` | 최종 실행 계획, 5축 검토 (08-23) |
| `dart_census_DS001.md` ~ `DS006.md` | DART 카테고리별 전수조사 (08-24) |
| `E2E_VERDICT.md` | 파이프라인 관통 테스트 판정 |
| `BACKFILL_REVIEW.md` | DART 백필 착수 전 최종 검토 (08-26) |
| `SOURCE_AUDIT.md` | KRX·키움 원장 정규화 감사, 폐지종목 수집 계획 (08-26) |

## 실행

```bash
# 저장소 루트에서 (CI 와 같은 명령). backend 환경에 duckdb(`equity` extra)가 있어야 한다
uv sync --project backend --extra parquet --extra equity --extra llm
uv run --project backend pytest database/tests -q
```

- 테스트: backend 환경 없이(`--no-project` 등) 돌리면 워크벤치 연동 테스트(`test_equity_s21_workbench.py`)가 조용히 skip 되므로 위 명령을 쓴다.
- 서버 배포: `database/scripts/deploy.sh`(기본 dry-run, `--apply` 만 전송 — 아래 "운영 (P6) → 배포"). `rebuild_share.py` 의 정본은 `backend/ops/` 다.
- 키·토큰: 코드는 `QL_ENV` 가 가리키는 파일에서만 읽는다(비었거나 파일이 없으면 실패 — 다른 파일로 대신하지 않는다). 운영은 서버 `$HOME/quant-ledger/.env` 이고 체인 스크립트가 시작부에서 고정한다 — 아래 "운영 (P6) → 비밀 파일". 레포에는 넣지 않는다.
- 협업자 로컬 동기화(서버 equity 층 → `~/quant-ledger/data/equity`): `database\scripts\ledger_sync.ps1 sync`, 검증 `verify --offline`, 일일 등록 `register_daily_sync.ps1`. 절차·판단 기준은 `docs/LEDGER_SYNC.md`.

## 작업 규칙

- 작업 단위(조사·플랜·태스크·페이즈)가 끝날 때마다 **같은 커밋에서 상태 문서를 갱신**한다: 플랜의 상태 블록·체크박스, 이 README 의 상태 표, `START_HERE.md` §4, 새 결정은 `DECISIONS_PENDING.md`, 새 부채는 `TECH_DEBT.md`.

## 운영 (P6)

일일 파이프라인의 크론·백업·로그·리포트 규칙. 스크립트는 전부 서버 `~/quant-ledger` 에서 돌고,
서버 TZ 는 UTC 라 **crontab 시각 = KST − 9** 이다. 크론 등록·배포는 오케스트레이터만 한다.

### 크론 전체표

| KST | crontab (UTC) | 스크립트 | 상태 |
|---|---|---|---|
| 토 03:30 | `30 18 * * 5` | `backup_raw.sh` — 원장 6 DB 온라인 백업, 금요일 장마감분. 성공 시 최신 1세트만 보관(결정 9, 09-14) | 가동 |
| 06:00 | `0 21 * * *` | `daily_ledger.sh` — v3 휴장 사본 동기화(`data/calendar/v3/`, 병행 대조용) → 휴장 달력 직접 갱신 `python -m daily.calendar_refresh`(KIS chk-holiday 하루 1콜, 판정 연도 파일 `data/calendar/kis_holidays_<YYYY>.json`·원장 `kis.db.kis_holiday`·보고서 `logs/calendar/<날짜>.json`, K1-9 — 플랜 `docs/plans/2026-10-10-holiday-calendar-direct.md`. 달력을 못 읽으면 D 산출에서 중단) → 키움 마스터 → (월요일 KST, 또는 마지막 성공이 7일을 넘었거나 기록이 없으면) DART 번호표 갱신 `dart_universe.py`(A-01 — 'D 이미 수집' 건너뜀 검사보다 앞. 성공은 런 로그 `source=dart_universe`, 실패는 warn 한 줄이고 체인 rc 는 그대로 — 배포 묶음 5-2) → 대차(ka20068) → KIS 신용 → 저녁 키움 보강 판정 `kw_daily --cover --tr ka10060,ka10014`(T-13 · H1-5 — 전날 21:05 저녁 직행의 D 커버리지를 원장에서 재고, 저녁 직행 게이트와 같은 술어(ka10060 요청 종목 대비 ≥ 0.98 · ka10014 자기 최근 20세션 평균 대비 ≥ 0.80)에 미달인 TR 만 `--fetch --commit` 으로 한 번 다시 받은 뒤 다시 잰다. 그래도 미달이면 소스 단계 실패 → crit · rc 2. 판정은 런 로그 `source=kiwoom_cover`) → DART 재스윕 → 회사 정보 공백 | 가동 |
| 07:10 | (08:10 체인 안) | 키움 외국인 보유 ka10008 — `daily_build.sh` 의 `--not-before 07:10` 하한 (결정 7) | 가동 |
| 08:10 | `10 23 * * *` | `daily_build.sh` — KRX → ka10008 → 머지 → `ledger_health` → `build_morning.sh`(**확정 빌드 포함**, 실측 종료 09:23~09:51) → `model_daily.sh`(확정판 rc 0·1 일 때만 fi → 모델 → 일간 엑셀 발송, 발송 장부로 D 당 1건 — N-25 Q0 임시, 실패는 notify.log crit 만·체인 rc 불변. 원천 전환 뒤(PR-9 — `config/postclose_chain.env` 의 `POSTCLOSE_ENABLED=1` 그리고 `POSTCLOSE_SEND=1`, 판정 `scripts/postclose_conf.sh`)엔 짓기만 하고, 장 마감 발송 장부 `data/model_db/deliver/sent_model_daily.jsonl` 에 그 D 의 basis=evening 줄이 없을 때만 대체 발송 + warn. 줄이 없는데 런 로그의 그 D 장 마감 엑셀(`postclose_excel`) 런 중 보냈을 수 있는 런(rc 3 — 발송 뒤 장부 기록 실패일 수 있다, B-58 · rc 0 발송 on · rc 를 모름)이 하나라도 있거나 장부·런 로그를 못 읽으면 보내지 않고 crit · rc 5. 상세 `docs/MODEL_DELIVER.md` §2) → `daily_report.py`. 06:00 체인이 아직 원장 락을 쥐고 있으면 끝날 때까지 기다렸다가 이어서 돈다(P9, 시간 한도 없음 — 대기 시작은 notify.log 에 info). 06:00 체인이 일찍 끝나도 08:10 전엔 시작하지 않는다 — KRX 확정 데이터(전날 애프터마켓까지 반영) 공개·T-1 정정(07시 전후)을 기다리는 시각이다. 모델 단계 뒤 장 마감 판 아침 잇기(`postclose_chain.sh morning` — v3 아침 KRX 재반영·두 판 대조, 컷오버 PR-8 ⑧. 확정판 rc 0·1 일 때, 모델 단계 성패와 무관). 그 뒤 체인 맨 끝에 조용한 손실 검사(`python -m daily.silent_loss check`, 컷오버 K1-4a — 모델 폐포 표를 직전 거래일 아침 확정판과 대조, 기록형이라 체인 rc 불변, 아래 '조용한 손실 검사') | 가동 (09-17 00:45 `--no-build` 제거 — 결정 11 뒤 사용자 "전체 체인을 켜보자") · 아침 잇기는 PR-8 배포 때 · 아침판 짓기만은 **예정**(원천 전환 10-19, PR-9 설정) |
| 10:30 | `30 1 * * *` | `watchdog.sh morning_build` — 직전 거래일 원장 건전성 + `latest_morning.json`(D+1 08:00 이후·health ok) 없음/실패면 crit. 확정판이 정상이면 그 D 의 엑셀 발송 장부 줄(`data/deliver/sent_model_daily.jsonl`, basis=morning)까지 보고, 없거나 장부를 못 읽으면 crit(B-57, 배포 묶음 5-1 — 손 발송은 사용자 승인 뒤 `scripts/model_daily.sh --date D`). 원천 전환 뒤(PR-9)엔 장 마감 장부(`data/model_db/deliver/sent_model_daily.jsonl`, basis=evening) 줄 또는 아침 장부 줄(대체 발송)을 발송 기록으로 보고, 둘 다 없으면 crit, 어느 장부든 못 읽으면 crit. 매일(금요일 판은 토요일에 지어진다). 09:45 → 10:00(DEFECT-D03: 실측 종료 09:30 에 `krx_step` 재시도 1회 +10분까지 흡수) → 10:30(F-11, 10-06: 빌드가 거래일마다 약 2분씩 길어져 10-03 종료 09:49). 08:10 체인이 원장 락을 기다리는 날엔 이 crit 은 예상된 것이다 — 아래 'DART 완료 판정 실패 · 놓친 확정판' 2번 | 가동 |
| 15:41 | `41 6 * * 1-5` | `postclose_chain.sh close` — 장 마감 수집(키움 ka10060 KRX, 15:41~16:00) → stage 단독 빌드 → fi → 모델 → 엑셀(그림자 미발송 — 원천 전환 10-19 부터 발송, D 의 엑셀은 이것이 먼저 간다) → v3 그림자 반영. 아래 "장 마감 체인" | **제안**(컷오버 PR-8, 그림자 시작 10-14 에 등록) |
| 16:30 | `30 7 * * 1-5` | `watchdog.sh postclose_board` — 오늘 장 마감 체인 런 로그(수집 + 단계 5개)가 없거나 실패·미완이면 crit. 세션 예외일·휴장은 정상 | **제안**(컷오버 PR-8) |
| 18:05 | `5 9 * * 1-5` | `QL_KW_EVENING_HHMM=2105 daily_evening.sh` — DART ∥ WISE 즉시, 키움 ka10060·ka10014 는 **21:05 까지 기다렸다** 원장 직행(결정 11: KRX 애프터마켓 20:00 마감, 키움 집계 20:15 정착, kael-v3 20:05 앱키 공유 회피). 체인 끝(키움 rc 0)에 원장 락을 놓고 장 마감 재반영 훅(`postclose_chain.sh refill` — 점수 없는 7표, 컷오버 PR-8 ⑦ · T-38) | 가동 · 재반영 훅은 PR-8 배포 때 |
| 21:20 | `20 12 * * 1-5` | `build_evening.sh` — 키움·WISE 인계(≈21:20)를 기다렸다 잠정 빌드(stage → equity, `basis=evening`, 실측 종료 22:38~22:41), 한도 21:45 | 가동 (09-17) — **중단 예정 10-14**(그림자 시작 때 컨트롤러가 줄 앞에 `#` — N-42 Q4, 장 마감 판이 대신한다) |
| 21:50 | `50 12 * * 1-5` | `watchdog.sh evening_ledger` — 저녁 원장 보고 없음/실패면 crit | 가동 |
| 23:30 | `30 14 * * *` | `daily.cutover_watch` — 컷오버 감시(QL-L): 그날 v3 무거운 수집 0 · 점수 쓰기 한 곳 · v3 크론(V3-A·B·D). 위반·판정 불가면 crit. 명령 `cd ~/quant-ledger && crontab -l \| PYTHONPATH=src .venv/bin/python -m daily.cutover_watch --v3-db ~/kael-system-v3/data/quant.db --v3-log ~/logs/kael-v3/pipeline.log --crontab - >> logs/cutover_watch.log 2>&1`(아래 "컷오버 감시") | **예정**(컷오버 날 10-19, V3-A~E 적용 뒤 컨트롤러가 등록 — 되돌리면 주석, `docs/CUTOVER_ROLLBACK.md` 4-3) |
| 23:55 | `55 14 * * 1-5` | `watchdog.sh evening_build` — `latest_evening.json` 이 오늘 것이 아니거나 health 실패면 crit. 23:00 → 23:30(DEFECT-D02: 한도 21:45 에 시작한 정상 판은 stage 43~66분 + equity 9~11분이라 23:06 에 끝난다) → 23:55(F-11, 10-06 에 00:00 으로 옮겼다가 10-07 수정 — 자정을 넘기면 날짜 판정이 다음 날이 되어 거짓 crit·금요일 무감시. 10-02 종료 23:02) | 가동 — **중단 예정 10-14**(21:20 잠정 빌드와 함께 — 잠정판이 없어 매일 crit 이 난다) |
| 22:30 | — | Kael-alpha 스코어 보고 목표(결정 11; 옛 19:00 목표는 애프터마켓으로 무효). 저녁 단축 빌드(B.1)로 ≈21:35 까지 당길 수 있다 | 예정 (페이즈 C) |
| 토 03:00 | `0 18 * * 5` | `wics_weekly.sh` — WICS 섹터 구성 주간 스냅샷(dt=금요일, L2 28 + L1 10 = 38콜, 멱등) → `data/raw/wiseindex.db`. 전부 빈 응답이면 rc 4 + warn | 가동 (09-20 10:15 등록) |
| 토 10:00 | `0 1 * * 6` | `wics_weekly.sh --retry` — 빈 코드만 다시 콜(행>0 판본은 skip). 그래도 비면 crit | 가동 (09-20) |
| 토 11:30 | `30 2 * * 6` | `watchdog.sh wics_weekly` — 원장에 금요일 dt 스냅샷 38코드(행>0)가 없으면 crit(잡 크론이 안 돈 경우까지 잡는다) | 가동 (09-20) |
| 일요일 04:30 | `30 19 * * 6` | `gc.sh --apply` — 빌드 락을 잡고 캐시·`_failed` 정리, 끝에서 `rotate_logs.sh` 호출, 완료 info / 실패 warn | 가동 (09-11 18:50 등록) |
| ~~매시~~ | — | ~~키움 확정 시각 프로브~~ (09-16 제거 — 09-14 촘촘 프로브로 20:15 정착 확인, `data/evidence/after_market_20260914.db`) | 제거 |

crontab 복구용 원문 12줄(이 표와 같은 값이다. 경로는 `~/` 로 적었다 — 크론은 /bin/sh 로 돌고 HOME 이 있어 그대로 넣어도 된다(서버의 실제 줄은 펼친 경로라 글자 대조는 다르게 나온다, F-13 10-06). WICS 3줄은 10-06 에 빠진 것을 채웠다. 서버가 초기화되면 이대로 넣는다 — 예전 표는 잠정 빌드를
18:15 로 적어 두어 그대로 복구하면 매 평일 crit 이 났다). 수집 체인 3개의 `>> logs/cron_*.log` 는
알림 전송 실패를 사후에 확인하기 위한 것이다(DEFECT-D04, `logs/notify_failed.log` 와 짝):

```cron
0 21 * * * /bin/bash ~/quant-ledger/scripts/daily_ledger.sh >> ~/quant-ledger/logs/cron_daily_ledger.log 2>&1
10 23 * * * /bin/bash ~/quant-ledger/scripts/daily_build.sh >> ~/quant-ledger/logs/cron_daily_build.log 2>&1
5 9 * * 1-5 QL_KW_EVENING_HHMM=2105 /bin/bash ~/quant-ledger/scripts/daily_evening.sh >> ~/quant-ledger/logs/cron_daily_evening.log 2>&1
50 12 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/watchdog.sh evening_ledger >> logs/watchdog.log 2>&1
30 1 * * * cd ~/quant-ledger && /bin/bash scripts/watchdog.sh morning_build >> logs/watchdog.log 2>&1
20 12 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/build_evening.sh >> logs/build_evening.log 2>&1
55 14 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/watchdog.sh evening_build >> logs/watchdog.log 2>&1
30 18 * * 5 cd ~/quant-ledger && /bin/bash scripts/backup_raw.sh >> logs/backup_raw.log 2>&1
30 19 * * 6 cd ~/quant-ledger && /bin/bash scripts/gc.sh --apply >> logs/gc.log 2>&1
0 18 * * 5 cd ~/quant-ledger && /bin/bash scripts/wics_weekly.sh >> logs/cron_wics_weekly.log 2>&1
0 1 * * 6 cd ~/quant-ledger && /bin/bash scripts/wics_weekly.sh --retry >> logs/cron_wics_weekly.log 2>&1
30 2 * * 6 cd ~/quant-ledger && /bin/bash scripts/watchdog.sh wics_weekly >> logs/watchdog.log 2>&1
```

장 마감 체인 크론 **제안**(컷오버 PR-8 — 아직 등록하지 않았다. 등록·삭제는 컨트롤러가 그림자 시작(10-14) 때 한다. 같은 날 `config/postclose_chain.env` 의 `POSTCLOSE_ENABLED=1` 을 배포한다 — 꺼져 있으면 체인은 info 한 줄로 끝나고 16:30 워치독이 crit 을 낸다. 등록하면 위 복구용 원문에 옮긴다):

```cron
# 15:41 KST 장 마감 체인 — 수급 확정 15:40(N-35 ②, 외부 공개 시각) 뒤. 수집기도 15:41 전이면 rc 3 으로 받지 않는다
41 6 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/postclose_chain.sh close >> logs/cron_postclose_chain.log 2>&1
# 16:30 KST 장 마감 판 워치독 — 수집 ≈3.4분(N-35 ③) + 단독 빌드·fi·모델·엑셀·v3 그림자 반영이 16:1x 에 끝나는 것을 본다
30 7 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/watchdog.sh postclose_board >> logs/watchdog.log 2>&1
# 21:20 연구 잠정 빌드와 그 워치독은 그림자 시작 때 끈다(N-42 Q4) — 줄 앞에 # 를 붙여 남긴다(되돌리기 = # 삭제)
#20 12 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/build_evening.sh >> logs/build_evening.log 2>&1
#55 14 * * 1-5 cd ~/quant-ledger && /bin/bash scripts/watchdog.sh evening_build >> logs/watchdog.log 2>&1
```

문서에 없던 환경변수: `QL_KW_EVENING_HHMM`(키움 저녁 수집 하한, 크론에 2105) · `QL_EVENING_BUILD_DEADLINE`
(잠정 빌드 시작 한도, 기본 21:45) · `QL_KW_FH_NOT_BEFORE` · `QL_BACKUP_TIMEOUT` · `QL_BACKUP_ROOT` ·
`QL_ENV`(비밀 파일 경로 — 체인 진입점이 `$HOME/quant-ledger/.env` 로 고정, 아래 "비밀 파일") · `QL_EQUITY_CONTINUE` · `QL_EQUITY_KEEP` · `QL_HOME` · `QL_REMOTE`·`QL_REMOTE_ROOT`(deploy) · `QL_WEEKDAY`(테스트 전용 — `daily_ledger.sh` 의 KST 요일 판정을 덮어쓴다, 운영 크론에는 넣지 않는다) · `QL_RAW_LOCK_FILE`(테스트 전용 — 원장 락 파일 경로, `scripts/raw_lock.sh` 를 쓰는 다섯 스크립트 공통, 운영 크론·대화형 셸에 남기지 않는다) · `QL_RAW_LOCK_WAKE_DATE`(테스트 전용 — 원장 락을 잡은 직후의 KST 날짜 YYYY-MM-DD 를 덮어쓴다, 운영에선 비운다) · `QL_V3_DB`(v3 quant.db 경로 — `v3_post.sh`·`postclose_chain.sh`, 체인 기본 `$HOME/kael-system-v3/data/quant.db`) · `QL_BUILD_LOCK_FILE`·`QL_POSTCLOSE_CHAIN_LOCK_FILE`(테스트 전용 — 빌드 락·장 마감 체인 락 파일 경로, 운영 크론·대화형 셸에 남기지 않는다).

### 장 마감 체인 — `scripts/postclose_chain.sh` (컷오버 PR-8)

15:41 장 마감 수집에서 v3 반영까지를 **완료 감지로** 잇는다(P9 — 대기 한도·재시도 시각 없음, 고정 시각은 외부 공개
시각인 수급 확정 15:40 뒤 15:41 하나). 정본은 `docs/plans/2026-10-10-cutover-track.md` §3 PR-8, 단계별 상세·rc 는
스크립트 머리 주석. 산출은 `data/model_db/` 아래만이고 stage·equity 연구 판은 읽기만 한다(T-3 · T-29).

| 모드 | 언제 | 단계(앞이 실패하면 뒤는 안 돈다) | 런 로그 source |
|---|---|---|---|
| `close` | 15:41 크론(T = 오늘 KST) | ⓪ 휴장이면 건너뜀 → ① `python -m daily.postclose`(수집기 자체 락, 원장 락 안 기다림 — T-4. rc 3 + 세션 예외일이면 그날 체인 전체 건너뜀 — T-26) → ①' 고정 판 확인(`data/deliver/history/<D'>_morning.json` 이 그 날짜·health ok — 아니면 빌드 락을 잡지 않고 crit, T-7 대체 발송 경로) + 조용한 손실 관문(K1-4a — 스위치는 셸이 파이썬 없이 읽는다. 꺼져 있으면(지금) 관문을 부르지 않고 통과해 어떤 상태에서도 막지 않고, 켜져 있을 때만 `python -m daily.silent_loss gate --date D'` 가 D' 검사의 미설명·판정 불가·결과 없음·관문 실패에서 같은 자리 crit) → [빌드 락] ② stage 단독 빌드 `stg_flow_postclose_kiwoom` + 그 표만 건전성(`stage.health --tables`, 리포트 `logs/health/postclose_stage_<T>.json`) + 장 마감 스냅샷 GC(기록형) → ③ fi 장 마감 판(`--builds-from <D'>_morning.json`, T-2 — 판 manifest 게이트의 `metrics.warn`(FG5, T-37)은 런 로그 `warn:FG5` + warn 한 줄) → ④ 모델 → ⑤ 엑셀(`data/model_db/deliver`) [빌드 락 놓음] → ⑥ `v3_post.sh --basis evening --model-root data/model_db/model --builds-from <D'>_morning.json` (점수 두 표는 여기서만 쓴다) | `kiwoom_postclose`(수집기) · `postclose_stage`·`_fi`·`_model`·`_excel`·`_v3` |
| `refill` | `daily_evening.sh` 끝(런 로그·최종 인계 파일 뒤, 키움 rc 0 일 때 — 원장 락을 먼저 놓는다) | ⑥ 결과와 상관없이 늘 점수 없는 7표 `v3_post.sh --basis evening --no-scores --builds-from <D'>_morning.json`(QL-F2 · T-38, compat 만). 16:00 컷오프 종목을 21:05 값으로 채우고, 장 마감 판이 없는 날(판 실패·세션 예외일)엔 그날 v3 T 행의 유일한 경로다. 부르기 전에 고정 판의 날짜·health 를 본다 — 쓸 수 없으면 v3_post 를 부르지 않고 crit(compat 은 health 를 보지 않는다, P1) | `postclose_v3_refill` |
| `morning` | `daily_build.sh` 가 확정판(rc 0·1)·모델 단계 뒤 | ⓐ `v3_post.sh --date D --basis morning --builds-from <D>_morning.json`(compat 만, 반영 표는 compat 이 고른다 — T-34. 고정 판 health 가 ok 가 아니면 부르지 않고 crit) ⓑ 그날 장 마감 모델 판이 있으면 두 판 대조 `python -m daily.board_compare`(PR-7). 둘은 서로 막지 않는다. 대조 rc 1 은 이번 실행이 쓴 `data/model_db/compare/<D>.json` 이 불일치 판정(verdict fail)일 때만 `mismatch`(warn), 그 밖의 rc 1 은 실패. 그날 수집기 런(`kiwoom_postclose`)이 아예 없으면 15:41 크론 누락이라 끝 알림이 info 대신 warn | `postclose_v3_morning` · `postclose_compare` |

- v3_post 세 호출은 `docs/COMPAT_LAYER.md` §8 호출 표가 정본이다. 셋 다 인계 이력으로 판을 고정한다(`--builds-from`) —
  이력이 없으면 compat 이 멈추고 v3_post·체인이 crit 을 낸다.
- **스위치**(`config/postclose_chain.env`): `POSTCLOSE_ENABLED=1` 이어야 세 모드가 돈다 — 저장소 값은 **꺼짐**이라 머지·배포만으로는
  아무것도 돌지 않는다(꺼져 있으면 info 한 줄·rc 0. 크론을 등록한 뒤에도 꺼져 있으면 16:30 워치독이 crit). 그림자 시작 때 켠다.
  `POSTCLOSE_SEND=1` 이면 엑셀 발송, `POSTCLOSE_V3=in-place` 면 v3 제자리 반영(+ `POSTCLOSE_V3_POST_CMD` 가 있으면 ⑥ 에만
  `--v3-post-cmd`) — PR-9 가 켠다. 켜는 쪽만 정확한 값을 요구하고, in-place 인데 발송이 꺼져 있으면 rc 5 로 거부한다(보내지 않은
  점수가 v3 에 들어가는 조합).
- **원천 전환(PR-9 · T-7)**: `POSTCLOSE_ENABLED=1` 그리고 `POSTCLOSE_SEND=1` 이면 '전환 뒤'다 — 새 변수 없이 이 두 값이 정한다
  (장 마감 발송이 켜졌는데 아침도 보내면 중복, 꺼졌는데 아침이 안 보내면 무발송이라 따로 놀 수 없다). 설정 읽기·판정은
  `scripts/postclose_conf.sh` 한 곳이고 이 체인·`model_daily.sh`·`watchdog.sh morning_build` 가 같이 쓴다. 전환 뒤 08:10 아침판은
  짓기만 하고 장 마감 발송 장부에 그 D 줄이 없을 때만 대체 발송하며, 10:30 워치독은 두 장부 중 하나를 본다(위 크론 표 08:10·10:30).
  ⑤ 엑셀 ok·⑥ v3 실패인 날은 대체 발송이 없다 — 그 D 의 v3 점수는 아침 재반영(T-34)이 채운다. 되돌리기는 `POSTCLOSE_SEND=0`
  하나(`docs/CUTOVER_ROLLBACK.md` 4-1).
- **락**: 장 마감 체인 락 `/tmp/quant_ledger_postclose_chain.lock` — `close` 는 비대기(이미 돌면 warn·rc 3), `refill` 은
  돌고 있는 `close` 가 끝날 때까지 기다린다(같은 v3 반영을 겹치지 않게), `morning` 은 안 잡는다. 빌드 락은 ②~⑤ 동안만 쥐고 쥐여
  있으면 기다린다(`model_daily.sh` 와 같은 모양). ①·①' 은 빌드 락 밖이다 — 16:00 창을 빌드·배포에 묶지 않는다.
- **연구 21:20 잠정 빌드 중단**(N-42 Q4): 그림자 시작 때 `build_evening.sh`·`watchdog.sh evening_build` 두 줄을 크론에서
  뺀다(위 '크론 제안'). 장 마감 판이 그 자리를 대신하고, 체인끼리 빌드 락이 겹치지 않는다.
- **감시**: 16:30 `watchdog.sh postclose_board` 가 오늘 단계마다 마지막 런을 본다 — 수집은 ok·cutoff·late, 나머지는 ok 만
  정상, 없음·실패·running 은 crit. 세션 예외일·휴장은 정상. 다음 날 08:10 일일 리포트도 이 source 들을 '마지막 런'
  규칙으로 센다(손 재실행이 ok 면 회복). `daily_evening.sh`·`daily_build.sh` 는 훅 rc 를 요약에 싣고 0·1 이 아니면 warn 1건.
- **배포 창**: 15:40~16:30(장 마감 체인)·21:00~21:30(21:05 키움 저녁 수집 → 재반영)에는 배포하지 않는다 — 배포가 빌드 락을
  쥐면 장 마감 체인이 기다리고, 스크립트가 도중에 바뀐다.
- **손 재실행**: 같은 T 를 다시 돌리면 ① 은 이미 받은 종목을 빼고(16:00 뒤면 콜 0 — `late`) 나머지 단계를 다시 짓는다 —
  `bash scripts/postclose_chain.sh close --date T`. 계획만 보려면 `--dry-run`(락·단계·런 로그·알림 없음, 꺼져 있어도 계획은 보인다).
  원천 전환 뒤(PR-9)에는 손으로 다시 돌리기 전에 두 장부(장 마감 `data/model_db/deliver/sent_model_daily.jsonl`·아침
  `data/deliver/sent_model_daily.jsonl`)와 텔레그램을 먼저 확인한다. ⑤ 는 아침 장부를 보지 않아 이미 대체 발송된 D 를 다시 보낸다.
- 알림은 `notify.sh`(기록만): 준비 info · 단계 실패 crit · 휴장·꺼짐 info · 세션 예외일 warn · 락 경합 warn · 게이트 경고 warn ·
  설정 오류 crit.

### 원장 락 — `scripts/raw_lock.sh` (배포 묶음 5-3 · TECH_DEBT B-52 해결)

원장(`data/raw/*.db`)을 쓰는 체인 스크립트 다섯 — `daily_ledger.sh`(06:00) · `daily_build.sh`(08:10) · `daily_evening.sh`(18:05) · `daily_wise.sh`(06:00 체인 안, 손 실행) · `wics_weekly.sh`(토 03:00·10:00) — 은 원장 락 `/tmp/quant_ledger_raw.lock` 을 이 공용 조각 하나로 잡는다.

- 비었으면 바로 잡는다. 다른 원장 작업이 쥐고 있으면 **건너뛰지 않고 끝날 때까지 기다렸다 이어서 돈다** — 시간 한도 없음(P9). 늦어짐은 워치독이 알린다. 예전엔 08:10 만 기다리고 나머지는 `flock -n` 실패 시 그 회차를 건너뛰었다(rc 3).
- 대기 시작은 실시간 출력(크론 `logs/cron_*.log`)과 `logs/notify.log` 의 info '<이름> 원장 락 대기' 한 줄, 대기 시간은 체인 로그에 남는다. dry-run 은 알림을 남기지 않는다.
- **스크립트마다 대기자는 하나**: 같은 스크립트가 이미 기다리는 중이면 두 번째 실행은 기다리지 않고 info '<이름> 이미 대기 중인 실행 있음 — 이번 실행 건너뜀' 후 rc 3. 대기자 표시는 `/tmp/quant_ledger_raw.lock.<이름>.wait`(빈 파일, 지우지 않는다). `<이름>` 은 `daily_wise.sh` 만 `daily_master`(그 스크립트의 다른 알림과 같은 이름)이고 나머지는 파일 이름이다.
- 대기형 flock 이 실패하면 warn '<이름> 락 실패' 후 rc 3 — 락 없이 원장을 쓰지 않는다. 부모가 이미 쥐었으면 `QL_RAW_LOCK_HELD=1` 로 물려받아 락을 열지 않는다(06:00 체인 → `daily_wise.sh`).
- 다섯 스크립트를 `flock <원장 락> …` 래퍼로 감싸 실행하지 않는다 — 스크립트가 같은 락을 새로 열어 자기 자신을 한도 없이 기다리고, 그 뒤의 다른 원장 체인도 전부 멈춘다. 감쌀 일이 있으면 `QL_RAW_LOCK_HELD=1` 로 넘긴다.
- 지금 누가 기다리는지: `grep -E "원장 락 대기|이미 대기 중" ~/quant-ledger/logs/notify.log | tail; pgrep -af "daily_(ledger|build|evening|wise)\.sh|wics_weekly\.sh"`.
- 기다리는 동안 KST 날짜가 바뀌면(자정을 넘는 점유) 락을 잡아도 본 작업을 하지 않는다 — crit '<이름> 원장 락 대기 중 날짜가 바뀜(시작 … → 지금 …) — 이번 실행 중단' 후 락을 놓고 rc 3(dry-run 은 알림 없이 같은 rc). 수집 대상일(D·오늘)을 대기 뒤에 정하므로 그날 값을 그대로 받을 수 없다(TECH_DEBT B-51). 다시 돌릴지는 사람이 정한다 — 원래 날의 슬롯을 `--date` 로 다시 돌리는 것이 맞는지(저녁 WISE·키움은 그날 안에만 받을 수 있다) 먼저 본다.
- 서로 다른 스크립트가 함께 기다리다 풀리면 누가 먼저 잡을지는 정해져 있지 않다(TECH_DEBT B-62 — 08:10 이 06:00 보다 먼저 잡으면 그날 확정판이 원장 게이트에서 멈춘다).
- 손으로 돌리는 수집기는 이 조각을 쓰지 않고 `flock -w <초>` 로 대기 상한을 둔다(아래 절차서 — 사람이 정한 마감이 있어서).
- 예외: 장 마감 수집기 `python -m daily.postclose`(15:41, 컷오버 PR-1)는 별도 원장 `data/raw/postclose.db` 에만 쓰고 원장 락을 잡지도 기다리지도 않는다 — 자체 락 `/tmp/quant_ledger_postclose.lock` 을 비대기로 잡고 쥐고 있으면 rc 3(16:00 창, 컷오버 정본 T-4).
  세션 시각이 바뀌는 날(수능일 등)은 rc 3 으로 건너뛴다 — 표는 저장소 기본 `config/calendar/session_exceptions.json`(배포로 나감) ∪ 운영 `data/calendar/session_exceptions.json`(급할 때 더하고 같은 항목을 PR 로 기본 표에도), 읽기는 `daily.calendar.load_session_exceptions` 한 곳.

### WISE 같은 날 재실행 — `src/backfill_wise.py` (A-03, 10-07)

18:05 저녁 체인의 WISE 갈래가 끊겼거나(체인 crit), 호출 일부가 실패해 다음 날 08:10 `wise.*` 건전성이 FAIL 할 날이면 그날 안에 수집기만 다시 돌린다. 건전성은 그날 런을 모두 합쳐 본다. 실패한 호출을 같은 날 뒤 런이 ok 로 다시 받았으면 회복으로 친다(`backfill_wise.last_call_status`).

저녁 warn "WISE 수집 일부 실패 N콜"(`logs/notify.log`)을 보면 이 절차다 — 수집기는 일부 콜이 실패해도 rc 0 이라 체인 crit 이 없다. 저녁 체인이 이번 런의 런 로그(`ws_run_log` n_req − n_ok: 본문 검증 실패 + 전송 실패)를 세어 1콜이라도 있으면 warn 1건을 남기고, 인계 파일 `ledger_evening.json` 의 `wise_n_bad`·`wise_bad_summary` 와 다음 날 일일 리포트 경고에도 싣는다(N-27 ③ · N-30 ③). 실패 수를 못 읽으면(수집기 rc 0 인데 런 로그 0행) "WISE 실패 콜 수 확인 불가" warn 이다. warn 은 저녁 체인이 끝날 때(키움 21:05 대기 뒤, 약 21:15) 나간다 — 21:20~22:45 잠정 빌드 중엔 재실행 금지(아래 4번)라 실제 재실행 창은 잠정 빌드 뒤 ~ 자정 전이다. 다음 날 리포트는 원장 `wise.run` 이 pass 면(같은 날 재실행으로 회복) 경고로 올리지 않고 '회복'으로만 적는다.

1. **같은 KST 날 24:00 전에만** 돌린다. 수집기의 스냅샷 날짜는 시작 시각 기준이라, 자정 뒤 재실행은 D+1 스냅샷이 된다.
2. **수집기를 직접, 원장 락 아래에서** 돌린다. 수집기가 rc 0 으로 끝난 날은 저녁 체인 status 가 ok 라서, `daily_evening.sh` 를 다시 돌려도 "이미 완료"로 아무것도 하지 않는다(`scripts/daily_evening.sh:119-123`).
   ```bash
   cd ~/quant-ledger && export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
   flock -w 600 /tmp/quant_ledger_raw.lock .venv/bin/python src/backfill_wise.py --mode full
   ```
   재실행은 아직 안 받은 종목과 오늘 실패가 남은 종목만 부른다. 대상이 0 이어도 런 로그를 1줄 남긴다. `-w 600` 은 손으로 돌리는 작업의 대기 상한이다(사람이 정한 마감이 있어서 — 체인끼리의 연결 P9 와 다르다).
3. **`--limit` 을 쓰지 않는다.** `--limit` 은 시험용이라 런 로그를 남기지 않는다. 그날 런 로그 1행 = '`--limit` 없는 런이 끝까지 돌았다'가 완료의 증거다. `daily_evening.sh --dry-run` 도 원장에 3종목을 실제로 쓴다(런 로그는 없음).
4. 21:20~22:45 잠정 빌드 중에는 돌리지 않는다.
5. 미리 보기: `.venv/bin/python -m daily.ledger_health --date D --out "$(mktemp -d)"`. 운영 리포트(`logs/health/`)는 건드리지 않는다.
6. 재실행 뒤에도 `wise.run` 값의 `unrecovered` 가 0 이 아니면, 다음 날 08:10 체인이 원장 게이트에서 멈춘다(TECH_DEBT B-46 — 수집 도중 커버 판정이 뒤집힌 종목). 그때는 사유를 결정 장부에 남기고, WISE 만 빼고 판정한 뒤 확정 빌드를 손으로 돌린다(B-25 의 09-14·15 처리와 같은 방식):
   ```bash
   .venv/bin/python -m daily.ledger_health --date D --skip wise && bash scripts/build_morning.sh --date D
   ```

### WISE 재무 2표 — 연속 판 접기 · 아침 재사용 (배포 묶음 7, stage 2.7.0)

`stg_fin_wise`·`stg_fin_wise_q` 는 같은 (종목, ep, pkey) 의 바로 앞 원장 판과 원문(sha256)이 같은 판을 싣지 않는다(`src/stage/fold.py`, STAGE_DESIGN §1 예외 f). 원장은 그대로다. 두 표의 `fetched_date` 는 '그 원문을 처음 본 날'이다 — 날짜 D 의 값은 (종목, ep[, pkey]) 마다 `fetched_date ≤ D` 최신 판으로 읽는다(STAGE_HANDOFF §4).

**아침 재사용**(`src/stage/reuse.py`): 08:10 확정 빌드에서 두 표는 먼저 저녁 판(e_) 재사용을 시도한다. 원장 내용 지문(키·sha256·fetched_at)·규칙 판본·코드 rev(코드 루트 `~/quant-ledger/DEPLOYED.json` — 저녁 판에도 같은 rev 가 기록돼 있어야 한다)·`data/stage/baseline.json` 그 표 항목·골든 픽스처·연도·CLI 게이트 임계 override(`STAGE_EXTRA='--g2 …'`)가 저녁 판과 모두 같으면, 다시 짓지 않고 저녁 판 parquet 를 하드링크한 새 `m_` 판으로 커밋한다.
- 로그(`logs/stage_all/<표>.log`) 결과 줄 끝에 `reused_from=e_… <초>s` 가 붙는다. `summary.tsv` 해석은 그대로다.
- 하나라도 다르거나 도중에 실패하면 `reuse_declined reason=…` 한 줄을 남기고 일반 빌드로 간다 — 손댈 일은 없다. 저녁 판 뒤 WISE 를 같은 날 다시 돌렸으면(위 절) 지문이 달라 일반 빌드가 된다(정상).
- **끄기**: `touch ~/quant-ledger/data/stage/REUSE_OFF` → 다음 아침부터 일반 빌드(코드·크론 변경 없음). 되살리기는 파일 삭제. 접기 자체를 되돌리는 절차는 플랜 `docs/plans/2026-10-09-batch7-wise-dedup.md` '7-4'.
- 재사용 정합의 독립 확인은 하드링크한 parquet 의 content_hash 재계산(판정 ⑦)이다. 재사용 판은 m_ 판이라 건전성 C1 이 그대로 통과하고, C4 는 계수·해시를 저녁 판에서 옮겨 적으므로 구조상 통과한다(따로 확인하는 것이 아니다).

### DART 완료 판정 실패 · 놓친 확정판 (배포 묶음 3, 10-07)

DART 완료 판정은 plan 의 전 유닛(정기 7종·주요사항 15종·지분 2종)이 '받았는지'(수집 기록이 ok·자료 없음이고, 계기 공시를 처음 본 뒤) 보고, 공시 목록 건수(filings)·문서·미해석 공시도 본다. 같은 D 의 두 번째 이후 런(06:00·주말 재실행·아래 수동 실행)은 못 받은 유닛과 재무 재확인(저장 재무 행이 계기 공시보다 옛것이거나 재무 자료 없음)만 다시 부른다(`plans/2026-10-07-batch3-dart-deadline.md`).

손으로 돌리는 작업은 **18:05 저녁 체인 전에 끝나야 한다** — 원장 락을 쥔 채 18:05 를 넘기면 `daily_evening.sh` 가 그 끝을 기다리느라 그날 저녁 슬롯 전체(키움 소멸성 수급·WISE·DART 첫 런)가 늦어지고, 자정을 넘기면 crit 후 그 회차를 멈춘다(위 '원장 락' — 배포 묶음 5-3 전에는 아예 건너뛰었다). 그래서 체인끼리의 연결(P9, 한도 없음)과 달리 수동 실행은 `flock -w <초>` 로 대기 상한을 두고, 끝낼 수 없으면 그날 저녁 체인이 끝난 뒤(≈22:45 이후)부터 자정 사이에 한다. 저녁 슬롯이 건너뛰어졌으면(대기 실패 · 체인 crit) 자정 전에 `bash scripts/daily_evening.sh` 를 다시 돌린다.

1. **DART 런이 실패했다**(체인 crit · 일일 리포트) — 먼저 그 D(`YYYYMMDD`)의 **마지막** dart 런 결과와 실패 줄을 본다(앞 런 실패가 뒤 런에서 회복됐으면 할 일 없음):
   ```bash
   cd ~/quant-ledger && sqlite3 "file:data/raw/daily_run.db?mode=ro" "SELECT started, status, substr(detail,1,300) FROM run WHERE source='dart' AND date='D' ORDER BY started"
   ```
   | 실패 줄 | 조치 |
   |---|---|
   | `periodic_followed`·`major_followed`·`holder_followed` 의 missing(수신) | 아래 명령으로 못 받은 유닛만 다시 부른다 |
   | `filings`(공시 목록 미달) | 스윕 문제다 — `--skip-sweep` 없이 다시 돌린다 |
   | `periodic_followed` 의 unresolved(미해석 공시) · `documents` | 다시 돌려도 안 풀린다 — 라벨 매핑·문서 원인을 조사한다 |
   | TOOL_FAILED · BUDGET_EXCEEDED · KAEL_KEY_USED | 다시 돌리지 말고 원인부터(kael 키 사용은 멈추고 조사) |

   못 받은 유닛 다시 부르기 — 소요는 실패 줄의 `units − received` × 약 0.8초로 가늠하고, 18:05 전에 끝날 때만:
   ```bash
   cd ~/quant-ledger && export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src" QL_ENV="$HOME/quant-ledger/.env"
   flock -w 600 /tmp/quant_ledger_raw.lock .venv/bin/python -m daily.dart_daily --date D --skip-sweep
   ```
   06:00 체인 crit 직후(08:10 전)에 돌리면 08:10 체인이 그 끝을 기다렸다가 회복된 DART 로 확정판을 짓는다. 그날 일일 리포트에서 dart 는 '회복'으로 보이지만 `ledger_chain` 실패는 crit 으로 남는다(`dart company gap` 을 실제로 건너뛰었으므로 — 아래처럼 따로 돌린다). 08:10 뒤에 고쳤다면 그 D 확정판에는 빠져 있다 — 다시 지을지는 3번. DART 가 실패하면 06:00 체인의 `dart company gap` 도 건너뛰므로 회복 뒤 `bash scripts/dart_company_gap.sh` 를 돌린다(이 스크립트는 `QL_ENV` 를 스스로 고정한다). '재무 반영 지연 N건'은 판정을 막지 않는다 — 다음 런이 다시 확인한다. 다음 거래일 저녁 런 뒤에 돌리면 다른 D 의 늦은 공시가 섞여 판정에 들어온다(TECH_DEBT B-54).
2. **10:30 워치독이 확정판 없음으로 crit** — 08:10 체인이 기다리는 중인지, 기다리는 상대가 살아 있는지 먼저 본다:
   ```bash
   grep "원장 락 대기" ~/quant-ledger/logs/notify.log | tail -1; pgrep -af "daily_build.sh|daily_ledger.sh"
   sqlite3 "file:$HOME/quant-ledger/data/raw/dart.db?mode=ro" "SELECT MAX(ts), COUNT(*) FROM dart_call_log WHERE ts > strftime('%Y-%m-%dT%H:%M:%S','now','-10 minutes')"
   ```
   기다리는 중이고 최근 10분 DART 호출이 있으면(진행 중) 그대로 둔다. 호출이 멈췄으면 06:00 체인 로그(`logs/cron_daily_ledger.log`)로 원인을 본다. 기다리는 체인이 없고 원장 락도 비었으면 `bash scripts/daily_build.sh --date D` 로 다시 짓는다 — 75~101분이 걸리므로 **16:30 뒤엔 시작하지 않는다**(18:05 저녁 체인과 겹친다). 원장 락 래퍼(`flock …`) 안에서 부르지 않는다(자기 락을 기다리며 멈춘다. 부모가 락을 쥐었으면 `QL_RAW_LOCK_HELD=1`).
3. **더 새 D 판이 이미 있으면 옛 D 를 다시 짓지 않는다.** 지어도 `latest_<basis>.json`·`_READY.json` 은 더 새 D 를 유지하고 그 판은 `history/` 에만 남지만, stage·equity MANIFEST 의 현재 판과 fi·model 의 `latest_*` 는 옛 D 판으로 바뀐다(TECH_DEBT B-50). 옛 D 판이 꼭 필요하면 사용자와 정한다.
4. 아직 오지 않은 D(오늘 KST 보다 뒤)는 `daily_build.sh --date` 가 원장 락 전에, `build_chain.sh` 가 시작부에서 rc 2 로 거부한다(`--date` 오타).

### 원장 백업 — `scripts/backup_raw.sh`

- 위치: `~/backups/quant-ledger/<YYYYMMDD>/{krx,kiwoom,kis,dart,wisereport,daily_run,postclose}.db` (`QL_BACKUP_ROOT` 로 변경). 한 세트 19 GB(09-19 실측. `data/raw` 전체는 46 GB 지만 `documents/` 27 GB 는 백업 대상이 아니다).
- 방식: `sqlite3 .backup` **온라인 백업만**(DB 당 `timeout 25m`, 외부 쓰기가 계속되면 재시작만 반복하므로). 원장이 18 GB 라 `cp`·`rsync`·하드링크는 금지고, 원장 락도 잡지
  않는다(18 GB 를 뜨는 동안 수집 체인이 막힌다). 03:30 은 어느 체인과도 겹치지 않는다.
- 판정: DB 별로 격리해 하나가 실패해도 나머지를 끝까지 뜨고, 실패 목록을 모아 crit 한 번. 사본마다
  `PRAGMA integrity_check` 가 `ok` 여야 하며 실패한 사본은 지운다. 성공한 사본은 `journal_mode=DELETE` 로
  바꿔 WAL 잔재(`-wal`·`-shm`)를 남기지 않는다.
- 보관: **주 1회(토요일 03:30), 최신 1세트만**(사용자 결정 9, 09-14 — `backup_raw.sh:19 KEEP_SETS=1`). 이번 백업이 성공하면 이전 세트를 지운다(다음 주가 덮어쓰는 셈). 실패한 주는 이전 세트를 남긴다. 순간 최대 2세트 ≈38 GB. 09-19 실측 소요 8.1분, 보관 1세트 19 GB.
- 중단 조건: 백업 대상 파일시스템 여유 < 60 GB 면 뜨기 전에 crit 후 중단.

### 로그 — `scripts/gc.sh` · `scripts/rotate_logs.sh`

- 체인 로그는 `logs/<체인>_<YYYYMMDD>.log`, 저녁 슬롯의 병렬 갈래는 `logs/evening_{kiwoom,dart,wise}_<D>.log`.
- `gc.sh`(일요일, 기본 dry-run / `--apply`): `/tmp/quant_ledger_build.lock` 을 잡고(수동 빌드·프리패스와 겹치면
  warn 후 rc 0 으로 물러난다 — DEFECT-D12) `data/stage/_tmp/doc` 프리패스 캐시를 **현재 stage 판이 선 스냅샷
  + 가장 최근 1개만 남기고** 삭제, `_failed` 30일 초과 삭제. 전삭제가 아닌 이유는 캐시 재생성이 3~4.5시간이고
  다음 증분 프리패스가 최근 캐시를 base 로 쓰기 때문이다. 보존 목록을 계산하지 못하면 아무것도 지우지 않는다.
  로그는 직접 건드리지 않고 끝에서 같은 모드로 `rotate_logs.sh` 를 호출한다(2026-09-11 자체 7일 gzip 줄 제거).
- `rotate_logs.sh`: `*.log` 14일 초과 gzip, `*.log.gz` 90일 초과 삭제. `logs/health/**` 는 **절대 건드리지
  않는다** — 워치독과 일일 리포트가 `logs/health/<D>.json`·`stage_<D>_<basis>.json` 을 읽고, 건전성 판정이
  전날 리포트를 기준선(KIS 중복쌍 증가분)으로 쓴다. 로그 gzip 규칙은 이 파일 한 곳뿐이다.

### 통합 일일 리포트 — `scripts/daily_report.py`

`daily_build.sh` 끝(≈08:55 KST)에서 `--date D` 로 한 번 돈다. 입력은 전부 읽기 전용이다 —
`logs/health/<D>.json`(원장) · `logs/health/stage_<D>_<basis>.json` · `data/deliver/latest_{evening,morning}.json` ·
`data/deliver/ledger_evening.json` · `data/raw/daily_run.db`(`mode=ro`) · 디스크 여유 · `/tmp/quant_ledger_*.lock`.
`--dry-run` 은 발송 없이 메시지만 출력한다.

| 등급 | 조건 | 행동 |
|---|---|---|
| crit (즉시) | 수집 실패(런 `failed`·저녁 원장 rc≠0) · 게이트 폐기(stage·빌드 health 실패) · `kael` 키 사용(건전성 halt) · 디스크 여유 < 50 GB. 단 같은 D 의 dart 는 **마지막 런**으로 판정한다 — 앞 런 실패가 뒤 런 ok 로 회복됐으면 '회복'으로 표시하고 crit 이 아니다(kael 키 런이 있으면 회복 아님). DART 하나로만 실패한 `evening_chain`(그 런 detail 의 다른 `*_rc` 가 모두 0)과 저녁 원장 dart_rc 도 같다(N-24 3.15). 장 마감 수집(`kiwoom_postclose`)·장 마감 체인 단계(`postclose_*`, PR-8)도 마지막 런으로 판정한다 | `notify.sh crit` (쿨다운 없음) |
| warn | 건전성 warn 항목 실패, 아직 `running` 인 런, 러너가 정한 주의 상태(`daily.runlog.WARN_STATUSES` — 장 마감 수집 cutoff·late·session_exception · 두 판 대조 mismatch), 저녁 WISE 부분 실패(`ledger_evening.json` 의 `wise_n_bad` > 0 — 같은 리포트의 원장 `wise.run` 이 pass 면 '회복'으로만 적고 올리지 않는다) · 그 실패 수 확인 불가(`wise_n_bad` null · `wise_rc` 0) | `notify.sh warn` |
| info | 그 밖의 일일 요약 | `notify.sh info` |

입력 파일이 없거나 날짜가 D 와 다르면 메시지 끝 "없음" 목록에만 적고 **등급을 올리지 않는다** —
보고 누락 판정은 워치독(`watchdog.sh`)의 몫이다(플랜 v2 §2-1).

### 조용한 손실 검사 — `python -m daily.silent_loss` (컷오버 K1-4a)

08:10 체인(`daily_build.sh`)의 맨 끝 — 확정판·모델 단계·장 마감 판 아침 잇기가 끝난 뒤 이어서(시간 한도 없음, P9) — 모델 폐포
표를 직전 거래일 아침 확정판과 대조해 '설명 안 되는 값→NULL·행 삭제·available_date 변경'을 센다(로드맵 K1-4 · N-42 Q4).
확정판이 선 날(build_morning rc 0·1)에만 돈다. 비교 엔진은 `scripts/equity_diff.py` 의 `diff_core` 하나다 — 갈래 정의·비용 실측은
`docs/EQUITY_GATES.md` §12-6, 규칙은 모듈 머리 주석이 정본이다.

- **대상(모델 폐포)**: fi 직접 원천(`factor_inputs.queries.TABLE_SOURCES`)에서 equity 선언 입력(`RULES[t].inputs`)을 따라 닫은
  stage·equity 표(원장 제외). 코드로 만든다 — 10-10 기준 53표(equity 18 · stage 35).
- **판**: `data/deliver/history/<D>_morning.json` 대 직전 거래일(판정 달력) `<D'>_morning.json` 의 판. 판이 GC 돼 없으면 그 표는
  판정 불가(P1).
- **갈래**: 새 행·NULL→값은 정상 · 재수집 창(D 와 그 앞 10세션, `ledger_health.KRX_RECHECK_SESSIONS`) 안 행의 값 변경·값→NULL 은
  '재수집 창' · 두 판의 규칙 판본이 다른 표는 '규칙 변경' · 그 밖의 값→NULL·행 삭제·available_date 변경은 **미설명**. 앞의 둘(창·
  규칙)은 설명됨으로 보되 수를 남긴다.
- **결과**: `logs/silent_loss/<D>.json`(표별 갈래 수·미설명 표본 상위 5) · 런 로그 source `silent_loss`(상태 ok · unexplained ·
  undecidable · blocked — unexplained·undecidable 은 일일 리포트 '주의 런', blocked 는 crit) · 미설명 > 0 또는 판정 불가면 notify
  warn 한 줄 '조용한 손실 기록 D=…'. 모듈이 rc 0~3 밖으로 죽으면 `daily_build.sh` 가 warn 1건. 체인 rc·요약 등급은 그대로다(요약에
  '조용한 손실 검사 종료 rc=N' 줄).
- **차단 전환**: 설정 한 줄 `config/silent_loss.env` 의 `SILENT_LOSS_BLOCK=1`(저장소 값 0 — 기록형). 켜면 미설명 > 0 또는 판정
  불가 표(N-42 Q4 '필수 검사 SKIP = 실패')가 crit 한 줄 '조용한 손실 차단 D=…'(X-2 세는 목록)이 되고, 그 D 의 아침 확정판을 고정해
  읽는 15:41 장 마감 체인 close 가 고정 판 확인 뒤·빌드 락 전에 멈춘다(`gate` — 결과 파일이 없거나 못 읽거나 관문이 죽어도 막는다).
  꺼져 있으면 체인이 스위치를 셸에서 읽고(`scripts/postclose_conf.sh` `silent_loss_block_on`) 관문을 아예 부르지 않는다. 언제 켜나:
  **그림자 시작 10-14 부터 2주 기록한 뒤**(정본 K1-4a · N-42 Q4). 켜는 것은 이 한 줄을 바꿔 배포하는 일이다.
- **비용**(로컬 실측 — §12-6): equity 는 파티션 해시가 같은 파티션을 건너뛰어 수 초, stage 는 판 기록에 파티션 해시가 없어 표
  전체를 조인한다(stg_fin 모양 1,540만 행 20.5초·RSS 5.05GB, `--memory-limit 6GB`). 서버 어림 2~4분.

```bash
# 손으로 한 번(결과 파일·런 로그·알림 없이, 결과를 다른 곳에) — 과거 D 도 그날 인계 이력의 판이 GC 전이면 된다
PYTHONPATH=src .venv/bin/python -m daily.silent_loss check --date 20261008 --dry-run --out /tmp/silent_loss_20261008.json
# 관문만 — 차단형일 때 그 D 결과로 rc 1
PYTHONPATH=src .venv/bin/python -m daily.silent_loss gate --date 20261008
```

### 연속 창 판정 — `python -m daily.window_judge` (컷오버 X-2)

그림자·실운영 3거래일 창과 되돌리기 5거래일 창을 판정 달력(`daily.calendar`)으로 센다. 규칙 정본은
`docs/plans/2026-10-10-cutover-track.md` §4 · T-39 이고 모듈 머리 주석에 옮겨 두었다. 거래일 T 통과 = 장 마감 체인 단계
런 전부 ok · 그날(KST) crit 0 · 수동 개입 0 · 다음 날 두 판 대조 `data/model_db/compare/<T>.json` pass(실운영
결과·등록 하한, 그날 대조 런 전부 ok). 실패 1건이면 다음 거래일부터 다시 세고, 휴장·세션 예외일은 건너뛴다
— 그날(주말 포함)의 crit·수동 개입은 직전 거래일에 귀속한다(T-39, 기준일 뒤 주말도 본다).
crit 은 제목으로 가른다(N-43): 점수에 영향을 주는 경로(장 마감 체인·연구 아침 빌드·v3 반영·그 워치독,
`COUNTED_CRIT_PREFIXES`)는 실패, 제외 목록(수집 단계·21:20 잠정판과 그 워치독·일일 리포트·백업·수동 도구,
`EXCLUDED_CRIT_PREFIXES`)은 '판정 밖 crit' 으로 표시만, **두 목록 어디에도 없으면 '분류 안 된 crit' 으로 실패**다.
스크립트에 새 crit 제목을 만들면 `test_window_judge.py::test_스크립트_crit_제목은_빠짐없이_분류된다` 가 깨진다 — 둘 중
한 곳에 넣는다. 입력은 읽기만 하고 `data/cutover/window.json` 에 결과를 쓴다(`--dry-run` 이면 안 씀).
rc 0 통과 · 1 아직·실패 · 2 입력 오류(장부 없음·notify.log 가 범위 시작 뒤에 시작·ISO 시각으로 시작하는 형식 밖 줄 포함).

```bash
# 그림자 시작일(10-14)에 한 번 — 빈 수동 개입 장부(없으면 판정 불가)
PYTHONPATH=src .venv/bin/python -m daily.window_judge record --init
# 손으로 개입한 날
PYTHONPATH=src .venv/bin/python -m daily.window_judge record --date 20261015 --what "v3 반영 손 재실행" --by controller
# PR-9 스위치 판정 — 그림자 3거래일(10-14~16). 셋째 날 대조는 토요일 08:10 체인(D=금요일)이 낸다
PYTHONPATH=src .venv/bin/python -m daily.window_judge judge --start 20261014 --as-of 20261016
# 컷오버 뒤 — 실운영 창 + 되돌리기 창
PYTHONPATH=src .venv/bin/python -m daily.window_judge judge --start 20261019 --cutover 20261019
```

손으로 개입한 날(재실행·데이터 손수정·스위치 조작)은 `record` 로 장부 `data/cutover/manual_interventions.jsonl` 에
남긴다 — 그날은 무사고가 아니다.

### 컷오버 감시 — `python -m daily.cutover_watch` (컷오버 QL-L)

컷오버 뒤 하루(KST D, 휴장·주말 포함)마다 v3 를 읽기만 하고 셋을 본다. 규칙·근거 줄은 모듈 머리 주석이 정본이다.
① **v3 무거운 수집 0**(N-42 Q2) — v3 quant.db `pipeline_runs` 의 그날 job 과 v3 체인 로그(`pipeline.log`)의 `job=` 줄을
분류표(`JOBS`)로 가른다. 허용 = 브리핑·daily_post·daily_insight(holiday_gate·export_scores·insight·위키)·증권사 리포트·
뉴스·관세청, 금지 = `chain:daily_all`·`daily_pipeline`(하위 단계 holiday_check·stock_master·daily_prices·
investor_flows·consensus)·`adj_prices`·`calendar_refresh`. **분류표에 없는 job 도 위반**(fail-closed). 로그 표지는
v3 가 실제로 남기는 메시지 모양에만 맞춘다(키움 `ka<5자리> ohlcv|failed for` · `KIS rate limit exceeded` ·
`koreainvestment.com` · `wisereport retry|failed|save failed|worker` · `WiseReport page load failed` — 위키 LLM 산문은 안
잡힌다). 허용 TR(T-27 insight 시장 단위 ka20006·ka20001·ka10051·ka90010·ka20003)은 허용, 허용 job(위키·insight 등)에 붙은
표지는 경고만, 그 밖의 job·job 없는 줄의 표지는 위반.
② **점수 쓰기 한 곳**(T-16) — v3 `scoring`·`scoring_v2` 가 돌았거나, `score_history`·`_v2` 의 그날 행 수가 그날
`_compat_meta` ok 기록(그 표를 쓴 마지막 기록, 판 id 포함)의 넣은 행 수와 다르거나 기록이 없으면 위반.
③ **v3 크론**(V3-A·B·D) — `crontab -l` 출력에 `--chain daily_all` 줄이 있거나 `--chain daily_insight` 줄이 없으면 위반.
`--chain daily_post` 줄도 위반이다 — daily_post 는 v3_post.sh 가 부르므로(COMPAT_LAYER §8-1) 크론에도 있으면 v3 엑셀이
두 번 나가고 반영 전 옛 점수가 갈 수 있다(그날 `chain:daily_post` 실행 수는 보이기만). v3 휴장 쓰기
(`refresh_year_holidays`·`monthly_holiday_review`) 줄과, job_runner 를 거치지 않아 `pipeline_runs` 에 안 남는 직접 진입점
(`scripts/backfill.py`·`backend.pipeline`·`backend.scoring.`·`scoring_then_ingest`) 줄도 위반.
결과는 화면 요약 + `data/cutover/watch_<D>.json`. rc 0 정상 · 1 위반 · 2 입력 오류(파일·표 없음, 빈 crontab).
위반이면 `notify.sh crit "컷오버 감시 위반 …"`, 입력 오류면 `crit "컷오버 감시 판정 불가 …"` 한 줄 — 둘 다 X-2 세는
목록이다(v3 반영 경로). `--baseline`(컷오버 전 그림자 기간)은 같은 분석을 기록만 하고 rc 0·알림 없음 — 지금 v3 의
실제 job 목록으로 허용 목록을 확인하는 용도다. `--dry-run` 은 결과 파일·알림 없이 요약만.

```bash
# 그림자 기간 — 지금 v3 기준선(읽기 전용, 날짜마다 한 번)
crontab -l | PYTHONPATH=src .venv/bin/python -m daily.cutover_watch --baseline --date 20261014 \
  --v3-db ~/kael-system-v3/data/quant.db --v3-log ~/logs/kael-v3/pipeline.log --crontab -
```

크론 제안(**아직 넣지 않는다** — 컨트롤러가 컷오버 날 V3-A~E 적용 뒤 넣는다. 매일 23:30 KST, D = 그날 KST. `--v3-log` 는
daily_insight 크론 줄의 리다이렉트 대상과 같게 둔다. 연구 리포트 로그는 넣지 않는다 — 리포트 수집의 KIS 휴장 1콜이
표지로 잡힌다). 되돌리기(V3-B 줄 복원) 때는 이 줄도 주석으로 돌린다 — 아니면 매일 위반 crit 이다. 23:30 에 그날을
보므로 23:30~24:00 은 어느 날 실행에도 보이지 않는다 — 지금 그 시간대에 `pipeline_runs` 를 쓰는 v3 job 은 없다(23:30·
24:00 뉴스 수집·23:50 위키 git push 는 이 표에 쓰지 않는다).

```cron
30 14 * * * cd ~/quant-ledger && crontab -l | PYTHONPATH=src .venv/bin/python -m daily.cutover_watch --v3-db ~/kael-system-v3/data/quant.db --v3-log ~/logs/kael-v3/pipeline.log --crontab - >> logs/cutover_watch.log 2>&1
```

### 배포 — `scripts/deploy.sh`

저장소 `database/{src,scripts}` + `backend/src/backtest_engine/` 을 서버로 민다. 기본은 dry-run 이고
`--apply` 만 실제로 전송한다(`rsync --delete`).

- `--apply` 전 검사: ① 작업 트리가 깨끗한가 ② HEAD 가 `origin/main` 을 포함하는가 — 미머지 브랜치는
  `--allow-branch <그 브랜치 이름>` 으로만 허용 ③ `uv run --project backend pytest database/tests -q` 통과
  (`--skip-tests` 로 생략 가능하며 생략 사실이 서버에 남는다).
- 그다음 서버 쪽 검사(K1-1e): ⑤ 서버 빌드 락(`/tmp/quant_ledger_build.lock`)을 비차단으로 잡아 rsync 와
  DEPLOYED.json 기록을 마칠 때까지 쥔다 — 못 잡으면 "다른 빌드·배포가 실행 중" 으로 거부하고 기다리지 않는다(P9).
  ⑦ 서버 `DEPLOYED.json` 의 rev 가 HEAD 의 조상인가 — main 을 역병합한 브랜치는 ② 를 늘 통과하므로
  다른 브랜치에서 먼저 민 핫픽스는 이 검사가 지킨다. 알고 되돌릴 때만 `--allow-rollback <서버 rev>`
  (서버 rev 와 정확히 같아야 한다). dry-run 은 ⑤·⑦ 판정만 출력한다(락은 잡았다가 바로 놓는다).
- ⑧ 비밀 파일(RG-C7-4): rsync 전에 서버 `~/quant-ledger/.env` 가 일반 파일(심볼릭 링크 아님)·권한 600·배포 계정 소유인지
  ssh 로 확인한다(`stat` 으로 존재·권한만 — 내용은 읽지 않는다). 아니면 `--apply` 는 거부(rc 2), dry-run 은 결과만 출력한다.
  **처음 이관 때는 아래 "비밀 파일" 절차로 파일을 먼저 만든 뒤 배포한다** — 체인 스크립트가 그 경로로 고정돼 있어 파일 없이 밀면 다음 체인이 실패한다.
- 빌드 크론 시각(06:00·08:10·18:05·21:20, 장 마감 15:40~16:30 · 21:05 재반영 21:00~21:30) 근처에는 배포하지 않는다 — 락을 쥔 동안 시작한
  체인은 그 회차를 건너뛴다.
- 전송 결과는 서버 `~/quant-ledger/DEPLOYED.json` 에 `{rev, branch, at_utc, by, tests}` 로 기록한다
  (`--allow-rollback` 으로 덮었으면 `rollback_from` 도).
  **드리프트 조사는 여기서 시작한다** — 서버가 어느 리비전인지 알 수 없어 운영 시간표가 조용히
  되돌아간 사고가 있었다(DEFECT-D05).
- 두 모드 모두 첫 줄에 "내용이 바뀔 파일 n개" 를 체크섬 기준으로 출력한다(워크트리 체크아웃은 mtime 이
  전부 달라 크기·시각 비교로는 못 센다).
- `_engine/strategy_workbench/` 는 `equity contract` 가 부르는 워크벤치 사본이다(#372 — 전에는 커널
  사본 `_engine/backtest_engine/` 이었고 그 디렉터리는 이제 아무도 읽지 않는다). 갱신 경로가 없어
  2026-09-05 판에서 멈춰 있던 것(DEFECT-C05)을 이제 배포가 같이 민다.
- 서버에만 있어야 하는 것(제외): 토큰 캐시 2종, `sync_v3_wise.py`, `rebuild_share.py`(정본은 `backend/ops/`).

### 비밀 파일 — `$HOME/quant-ledger/.env` (RG-C7-4)

quant-ledger 는 API 키·텔레그램 토큰을 **자기 파일 하나**에서만 읽는다. 옛 시스템(kael-system-v3) 폴더의 비밀 파일로 넘어가는 경로는 없다.

- 경로 = 환경변수 `QL_ENV`. 크론 진입점(`daily_ledger`·`daily_build`·`daily_evening`·`build_evening`·`watchdog`·`backup_raw`·`gc`·`wics_weekly`·`postclose_chain`)과
  손으로 단독 실행하는 `model_daily.sh`·`daily_wise.sh`·`dart_company_gap.sh` 가 `cd` 직후 `export QL_ENV="$HOME/quant-ledger/.env"` 로 **고정**한다(바깥 값을 물려받지 않는다). 크론 줄은 그대로다.
  그 밖의 손 명령(`python -m daily.dart_daily`·`python -m deliver`·`daily_dart.sh` 등)은 앞에 `QL_ENV="$HOME/quant-ledger/.env"` 를 붙인다.
- 읽는 곳: `src/api.py`(import 시점) · `src/deliver/telegram.py`(`--env-file` > `QL_ENV`) · `scripts/notify.sh`(`QL_NOTIFY_TELEGRAM=1` 분기만 — 기본 로그 분기는 파일 없이 돈다).
  경로가 비었거나 가리킨 파일이 없으면 셋 다 실패한다 — `import api`·`telegram.env_path` 는 FileNotFoundError, `python -m deliver` 는 설정 오류 rc 2, notify 텔레그램 분기는 rc 2.
- 배포 순서: 이 파일을 **먼저** 만든 뒤 배포한다 — `deploy.sh` 가 rsync 전에 원격 파일의 존재·권한 600·소유자를 확인하고, 아니면 멈춘다(아래 "배포").
- 위치: 운영 루트 바로 아래 — `deploy.sh` 의 `rsync --delete` 대상(`src/`·`scripts/`·`config/`·`_engine/`) 밖이라 배포가 지우지 않는다. `config/` 아래에 두지 않는다.
- 만들기(서버에서 한 번, 값은 셸 기록·화면에 남기지 않는다 — 편집기로 넣는다):

  ```bash
  ( umask 077 && touch "$HOME/quant-ledger/.env" ) && chmod 600 "$HOME/quant-ledger/.env"
  # 편집기로 KEY=값 줄을 넣은 뒤 — 키 이름만 확인(값 출력 금지)·권한 600 확인
  cut -d= -f1 "$HOME/quant-ledger/.env" | sort
  stat -c '%a %U' "$HOME/quant-ledger/.env"
  ```

  | 키 | 읽는 곳 | 넣는가 |
  |---|---|---|
  | `KRX_API_KEY` | `api.py` import 시점(없으면 import 가 KeyError) | 필수 |
  | `KIS_APP_KEY` · `KIS_APP_SECRET` | `api.py` KIS 토큰 · `backfill_kis.py` | 필수 |
  | `KIWOOM_APP_KEY` · `KIWOOM_SECRET_KEY` | `api.py` 키움 토큰 | 필수 |
  | `DART_API_KEY_2` ~ `DART_API_KEY_5` | `api.dart_keys()` — 번호순이 소진 순서 | 지금 쓰는 번호 그대로(하나 이상) |
  | `BOT_TOKEN` · `CHAT_ID_AIPLAYGROUND` | `deliver/telegram.py` — 모델 엑셀 발송 | 필수 |
  | `KRX_ID` · `KRX_PW` | `api.py` 가 환경변수로 내보내기만 한다(저장소 코드에 소비처 없음) | 선택 |
  | `CHAT_ID_LOG` | `notify.sh` 텔레그램 분기만(`QL_NOTIFY_TELEGRAM=1` — 로그 알림 텔레그램 금지, 10-01) | 넣지 않아도 된다 |
  | `DART_API_KEY` | v3 프로덕션 키 — `api.dart_keys()` 가 쓰지 않는다 | **넣지 않는다** |

  `DART_API_KEY_2`~`_5` 의 **값**도 v3 프로덕션 `DART_API_KEY` 값과 달라야 한다 — 이름만 바꾼 같은 키 금지(그 키를 쓰면 v3 한도를 같이 태운다).

### 공유 소비자 완료 신호 — `data/{stage,equity}/_READY.json`

`/srv/quant-share` 에 바인드된 것은 `raw`·`stage`·`equity` 3개뿐이라 소비자는 `data/deliver` 를 볼 수 없다.
체인은 **stage·equity 건전성이 둘 다 ok 일 때만** 두 루트에 `_READY.json` 을 `os.replace` 로 원자 기록한다
(`{date, basis, generated_at_utc, builds}`). 실패한 판에서는 갱신하지 않으므로 마지막 성공 판 신호가 남는다.
빌드 중에 표별 `MANIFEST.json` 을 직접 읽으면 신·구 판이 섞인 상태를 보게 된다(DEFECT-B04) — 소비자는
`_READY.json` 의 `builds` 맵을 읽고 그 판만 쓴다.
