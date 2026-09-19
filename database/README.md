# database — 국내증시 원장 · stage · equity 파이프라인

KRX·키움·KIS·DART·WISE 원장 수집기, stage 층 빌더, 문서층(L1) 파서, equity 층 빌더와 설계 문서를 둔다.
데이터는 카엘 서버(`~/quant-ledger`)가 정본이며 이 폴더에는 **코드와 문서만** 둔다
(`database/data/`·`logs/`·`scratchpad/` 는 `.gitignore`).

## 상태 (2026-09-09)

| 항목 | 값 |
|---|---|
| 원장 | **일일 체인 유지 중(09-10 첫 실행 통과)** — KRX·키움 09-09, KIS 신용잔고 09-07(조회일 기준 T-3 규칙), DART 09-09, WISE 09-10 |
| stage | 66테이블 커밋(09-05), 원장 정지 날짜까지 |
| equity | 29표(`coverage_daily` S24 추가)·규칙 **e1.15.0**(코드 반영 09-11, 서버 첫 빌드 대기 — 아래는 e1.14.0 서버 상태)·규칙 e1.14.0(09-09 전량 재빌드로 단일화)·팩터 준비도 54/54. catalog `2c38be1d58fb03be`·contract pass, baseline 락 바이트 동일(09-09 정렬) |
| 진행 중 | **일일 증분 플랜** `docs/plans/2026-09-09-daily-incremental.md` — R1~R10 승인(09-09), **P0·P1·P2 완료(키움 갭은 09-09 저녁 즉시 실행), P3 크론 가동(09-09), 관찰 1/5 통과(09-10)** — 06:00 수집·08:10 빌드 체인, 키움 단계 포함(사용자 결정: 공유 앱키로 콜). **첫 적재분 검수**(09-10): high 5·mid 9 → `docs/reviews/2026-09-10-intake-audit-summary.md`. 수집기 핫픽스 3건(WISE 커버 판정·키움 유예·DART 분기 창) 배포 `52d0f48`, 사용자 결정 3건 반영(결정 6: 키움 머지 신규 행만·KIS 최초 관측판(P5)·G3 개정). **사용자 행동 필요: 키움 앱키 추가 발급**(DECISIONS_PENDING R5 후속) |
| 크론 | **18:05 `daily_evening.sh`**(당일: 키움 투자자·공매도는 21:05 원장 직행 ∥ DART ∥ WISE 스냅샷) · 21:20 `build_evening.sh`(잠정 빌드) · 06:00 `daily_ledger.sh`(키움 마스터, 대차, KIS, DART 재스윕) · 08:10 `daily_build.sh`(KRX → 외국인 보유 → 머지 → 건전성 → **확정 빌드 포함**, 09-17 `--no-build` 제거) · 워치독 21:50/23:30/10:00 · 토 03:30 백업 · 일 04:30 gc. 전체는 아래 "운영 (P6) → 크론 전체표" |

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
| `scripts/` | 서버 크론·러너: `daily_evening.sh`(18:05)·`daily_ledger.sh`(06:00)·`daily_build.sh`(08:10)·`watchdog.sh`·`daily_wise.sh`(마스터만), `run_stage.sh`·`run_stage_all.sh`, `run_equity.sh`·`equity_rebuild_all.sh`·`equity_gate_all.sh`, `check_baseline_lock.py`, `fetch_equity_local.sh`(운영자 rsync 용), **`ledger_sync.ps1`·`.sh`·`register_daily_sync.ps1`**(협업자 SFTP 동기화 — `LEDGER_SYNC.md`), `run_survey*.sh` |
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
| `DECISIONS_PENDING.md` | 사람이 정해야 할 것 (결정 1~5) |
| `TECH_DEBT.md` | 기술 부채와 우선순위 |
| `BLOCKED_FACTORS.md` | 막힌 팩터와 원인 |
| `plans/2026-09-11-daily-incremental-v2.md` | **일일 증분 플랜 v2** — 당일 저녁 스코어링(18:05 저녁 슬롯·잠정/확정 빌드), 페이즈 A~D. 시간표·P4 이후의 정본 |
| `plans/2026-09-09-daily-incremental.md` | 일일 증분 플랜 v1 — P0~P3 결과·결정 R1~R10·결정 6·7 (유효), 시간표·P4~P6 은 v2 로 이관 |

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
cd database
uv run --no-project --python 3.11 --with pytest --with duckdb --with requests python -m pytest tests -q
```

- 서버 배포: `rsync -avz --exclude='.venv' --exclude='__pycache__' database/src/ kael-server:~/quant-ledger/src/` (`scripts/` 도 동일). 서버에만 있는 파일(`src/equity_s23/`, `rebuild_share.py`)은 플랜 P0 에서 저장소로 회수 예정.
- 키·토큰: 코드는 `QL_ENV` 또는 `~/kael-system-v3/.env` 에서만 읽는다. 레포에는 넣지 않는다.
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
| 06:00 | `0 21 * * *` | `daily_ledger.sh` — 캘린더 → 키움 마스터 → 대차(ka20068) → KIS 신용 → DART 재스윕 | 가동 |
| 07:10 | (08:10 체인 안) | 키움 외국인 보유 ka10008 — `daily_build.sh` 의 `--not-before 07:10` 하한 (결정 7) | 가동 |
| 08:10 | `10 23 * * *` | `daily_build.sh` — KRX → ka10008 → 머지 → `ledger_health` → `build_morning.sh`(**확정 빌드 포함**, 실측 종료 09:23~09:30) → `daily_report.py` | 가동 (09-17 00:45 `--no-build` 제거 — 결정 11 뒤 사용자 "전체 체인을 켜보자") |
| 10:00 | `0 1 * * *` | `watchdog.sh morning_build` — 직전 거래일 원장 건전성 + `latest_morning.json`(D+1 08:00 이후·health ok) 없음/실패면 crit. 매일(금요일 판은 토요일에 지어진다). 09:45 → 10:00(DEFECT-D03: 실측 종료 09:30 에 `krx_step` 재시도 1회 +10분까지 흡수) | 가동 |
| 18:05 | `5 9 * * 1-5` | `QL_KW_EVENING_HHMM=2105 daily_evening.sh` — DART ∥ WISE 즉시, 키움 ka10060·ka10014 는 **21:05 까지 기다렸다** 원장 직행(결정 11: KRX 애프터마켓 20:00 마감, 키움 집계 20:15 정착, kael-v3 20:05 앱키 공유 회피) | 가동 |
| 21:20 | `20 12 * * 1-5` | `build_evening.sh` — 키움·WISE 인계(≈21:20)를 기다렸다 잠정 빌드(stage → equity, `basis=evening`, 실측 종료 22:38~22:41), 한도 21:45 | 가동 (09-17) |
| 21:50 | `50 12 * * 1-5` | `watchdog.sh evening_ledger` — 저녁 원장 보고 없음/실패면 crit | 가동 |
| 23:30 | `30 14 * * 1-5` | `watchdog.sh evening_build` — `latest_evening.json` 이 오늘 것이 아니거나 health 실패면 crit. 23:00 → 23:30(DEFECT-D02: 한도 21:45 에 시작한 정상 판은 stage 43~66분 + equity 9~11분이라 23:06 에 끝난다) | 가동 |
| 22:30 | — | Kael-alpha 스코어 보고 목표(결정 11; 옛 19:00 목표는 애프터마켓으로 무효). 저녁 단축 빌드(B.1)로 ≈21:35 까지 당길 수 있다 | 예정 (페이즈 C) |
| 일요일 04:30 | `30 19 * * 6` | `gc.sh --apply` — 빌드 락을 잡고 캐시·`_failed` 정리, 끝에서 `rotate_logs.sh` 호출, 완료 info / 실패 warn | 가동 (09-11 18:50 등록) |
| ~~매시~~ | — | ~~키움 확정 시각 프로브~~ (09-16 제거 — 09-14 촘촘 프로브로 20:15 정착 확인, `data/evidence/after_market_20260914.db`) | 제거 |

crontab 복구용 원문 9줄(이 표와 같은 값이다. 서버가 초기화되면 이대로 넣는다 — 예전 표는 잠정 빌드를
18:15 로 적어 두어 그대로 복구하면 매 평일 crit 이 났다). 수집 체인 3개의 `>> logs/cron_*.log` 는
알림 전송 실패를 사후에 확인하기 위한 것이다(DEFECT-D04, `logs/notify_failed.log` 와 짝):

```cron
0 21 * * * /bin/bash /home/kael/quant-ledger/scripts/daily_ledger.sh >> /home/kael/quant-ledger/logs/cron_daily_ledger.log 2>&1
10 23 * * * /bin/bash /home/kael/quant-ledger/scripts/daily_build.sh >> /home/kael/quant-ledger/logs/cron_daily_build.log 2>&1
5 9 * * 1-5 QL_KW_EVENING_HHMM=2105 /bin/bash /home/kael/quant-ledger/scripts/daily_evening.sh >> /home/kael/quant-ledger/logs/cron_daily_evening.log 2>&1
50 12 * * 1-5 cd /home/kael/quant-ledger && /bin/bash scripts/watchdog.sh evening_ledger >> logs/watchdog.log 2>&1
0 1 * * * cd /home/kael/quant-ledger && /bin/bash scripts/watchdog.sh morning_build >> logs/watchdog.log 2>&1
20 12 * * 1-5 cd /home/kael/quant-ledger && /bin/bash scripts/build_evening.sh >> logs/build_evening.log 2>&1
30 14 * * 1-5 cd /home/kael/quant-ledger && /bin/bash scripts/watchdog.sh evening_build >> logs/watchdog.log 2>&1
30 18 * * 5 cd /home/kael/quant-ledger && /bin/bash scripts/backup_raw.sh >> logs/backup_raw.log 2>&1
30 19 * * 6 cd /home/kael/quant-ledger && /bin/bash scripts/gc.sh --apply >> logs/gc.log 2>&1
```

문서에 없던 환경변수: `QL_KW_EVENING_HHMM`(키움 저녁 수집 하한, 크론에 2105) · `QL_EVENING_BUILD_DEADLINE`
(잠정 빌드 시작 한도, 기본 21:45) · `QL_KW_FH_NOT_BEFORE` · `QL_BACKUP_TIMEOUT` · `QL_BACKUP_ROOT` ·
`QL_ENV` · `QL_EQUITY_CONTINUE` · `QL_EQUITY_KEEP` · `QL_HOME` · `QL_REMOTE`·`QL_REMOTE_ROOT`(deploy).

### 원장 백업 — `scripts/backup_raw.sh`

- 위치: `~/backups/quant-ledger/<YYYYMMDD>/{krx,kiwoom,kis,dart,wisereport,daily_run}.db` (`QL_BACKUP_ROOT` 로 변경). 한 세트 19 GB(09-19 실측. `data/raw` 전체는 46 GB 지만 `documents/` 27 GB 는 백업 대상이 아니다).
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
| crit (즉시) | 수집 실패(런 `failed`·저녁 원장 rc≠0) · 게이트 폐기(stage·빌드 health 실패) · `kael` 키 사용(건전성 halt) · 디스크 여유 < 50 GB | `notify.sh crit` (쿨다운 없음) |
| warn | 건전성 warn 항목 실패, 아직 `running` 인 런 | `notify.sh warn` |
| info | 그 밖의 일일 요약 | `notify.sh info` |

입력 파일이 없거나 날짜가 D 와 다르면 메시지 끝 "없음" 목록에만 적고 **등급을 올리지 않는다** —
보고 누락 판정은 워치독(`watchdog.sh`)의 몫이다(플랜 v2 §2-1).

### 배포 — `scripts/deploy.sh`

저장소 `database/{src,scripts}` + `backend/src/backtest_engine/` 을 서버로 민다. 기본은 dry-run 이고
`--apply` 만 실제로 전송한다(`rsync --delete`).

- `--apply` 전 검사: ① 작업 트리가 깨끗한가 ② HEAD 가 `origin/main` 을 포함하는가 — 미머지 브랜치는
  `--allow-branch <그 브랜치 이름>` 으로만 허용 ③ `uv run --project backend pytest database/tests -q` 통과
  (`--skip-tests` 로 생략 가능하며 생략 사실이 서버에 남는다).
- 전송 결과는 서버 `~/quant-ledger/DEPLOYED.json` 에 `{rev, branch, at_utc, by, tests}` 로 기록한다.
  **드리프트 조사는 여기서 시작한다** — 서버가 어느 리비전인지 알 수 없어 운영 시간표가 조용히
  되돌아간 사고가 있었다(DEFECT-D05).
- 두 모드 모두 첫 줄에 "내용이 바뀔 파일 n개" 를 체크섬 기준으로 출력한다(워크트리 체크아웃은 mtime 이
  전부 달라 크기·시각 비교로는 못 센다).
- `_engine/backtest_engine/` 은 `equity contract` 가 대조하는 커널 사본이다. 갱신 경로가 없어 2026-09-05
  판에서 멈춰 있었다(DEFECT-C05) — 이제 배포가 같이 민다.
- 서버에만 있어야 하는 것(제외): 토큰 캐시 2종, `sync_v3_wise.py`, `rebuild_share.py`(정본은 `backend/ops/`).

### 공유 소비자 완료 신호 — `data/{stage,equity}/_READY.json`

`/srv/quant-share` 에 바인드된 것은 `raw`·`stage`·`equity` 3개뿐이라 소비자는 `data/deliver` 를 볼 수 없다.
체인은 **stage·equity 건전성이 둘 다 ok 일 때만** 두 루트에 `_READY.json` 을 `os.replace` 로 원자 기록한다
(`{date, basis, generated_at_utc, builds}`). 실패한 판에서는 갱신하지 않으므로 마지막 성공 판 신호가 남는다.
빌드 중에 표별 `MANIFEST.json` 을 직접 읽으면 신·구 판이 섞인 상태를 보게 된다(DEFECT-B04) — 소비자는
`_READY.json` 의 `builds` 맵을 읽고 그 판만 쓴다.
