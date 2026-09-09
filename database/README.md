# database — 국내증시 원장 · stage · equity 파이프라인

KRX·키움·KIS·DART·WISE 원장 수집기, stage 층 빌더, 문서층(L1) 파서, equity 층 빌더와 설계 문서를 둔다.
데이터는 카엘 서버(`~/quant-ledger`)가 정본이며 이 폴더에는 **코드와 문서만** 둔다
(`database/data/`·`logs/`·`scratchpad/` 는 `.gitignore`).

## 상태 (2026-09-09)

| 항목 | 값 |
|---|---|
| 원장 | **KRX·키움 시계열 08-20, KIS 08-18, DART 09-01 에 정지.** WISE·키움 마스터만 매일(`daily_wise.sh` 06:00 KST) |
| stage | 66테이블 커밋(09-05), 원장 정지 날짜까지 |
| equity | 28표·규칙 e1.14.0(09-09 전량 재빌드로 단일화)·팩터 준비도 54/54. catalog `2c38be1d58fb03be`·contract pass, baseline 락 바이트 동일(09-09 정렬) |
| 진행 중 | **일일 증분 플랜** `docs/plans/2026-09-09-daily-incremental.md` — R1~R10 승인(09-09), **P0·P1·P2 완료(키움 갭은 09-09 저녁 즉시 실행), P3 크론 가동(09-09)** — 06:00 수집·08:10 빌드 체인, 키움 단계 포함(사용자 결정: 공유 앱키로 콜). **사용자 행동 필요: 키움 앱키 추가 발급**(DECISIONS_PENDING R5 후속) |
| 크론 | 06:00 `daily_ledger.sh`(daily_wise 포함, 키움 시계열·KIS·DART) · 08:10 `daily_build.sh --no-build`(KRX·키움 대조·건전성) · 매시 키움 프로브(임시, 09-15 제거). `daily_dart.sh` 폐기 |

## 층 구조

```
원장(raw)   SQLite 5개  data/raw/{krx,kiwoom,kis,dart,wisereport}.db   ← src/backfill_*.py + 서버 크론
    ↓ src/stage  (duckdb → parquet, 게이트 G0~G9, 스냅샷 재현성)
stage       parquet 66테이블  data/stage/                              ← STAGE_SPEC · STAGE_DESIGN · STAGE_HANDOFF
    ↓ src/equity (구조 정책: 조인·격자·PIT·조정계수·유니버스·카탈로그, 게이트 EG0~EG20)
equity      parquet 28표 + equity.duckdb   data/equity/                ← EQUITY_DESIGN · EQUITY_GATES · EQUITY_WORKFLOW
    ↓ 어댑터(워크벤치 5포트 + 커널 3포트, backend/)
```

문서층(L1)은 DART 보고서 ZIP 을 stage 의 여섯 번째 원장 소스로 다룬다 (`DOC_DESIGN`).

## 디렉터리

| 경로 | 내용 |
|---|---|
| `src/` | 수집기(`backfill_*.py`, `api.py`, `dart_universe.py`, `sweep_disclosure.py`, `master_daily.py`), stage 패키지(`src/stage/`, `python -m stage --table <t>`), equity 패키지(`src/equity/`, `python -m equity build|gate|catalog|contract`), 파일럿 통합층(`build_*.py`·`finalize.py`·`fin_map.py` — STAGE_DESIGN §8 이 파일럿 보존·로직 재사용으로 명시) |
| `scripts/` | 서버 크론·러너: `daily_wise.sh`(06:00 KST, 유일 크론), `daily_dart.sh`(미등록), `run_stage.sh`·`run_stage_all.sh`, `run_equity.sh`·`equity_rebuild_all.sh`·`equity_gate_all.sh`, `check_baseline_lock.py`, `fetch_equity_local.sh`, `run_survey*.sh` |
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
| `plans/2026-09-09-daily-incremental.md` | **일일 증분 플랜** — 6페이즈·게이트, 결정 R1~R10 |

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
| `RATIO_RECOVERY.md` | 무상증자 비율 유도식 검증 기록 |

### 실측 조사 (docs/reviews/)

| 문서 | 내용 |
|---|---|
| `2026-09-05-equity-*.md` (4건) | equity v1.1 → v1.2 제안서 |
| `2026-09-09-daily-findings-A~E-*.md` (5건) | 일일 증분 플랜 근거: 원장 A(KRX·키움·KIS)·B(DART·문서·WISE), stage, equity, 운영·타이밍 |

읽는 순서 — equity 작업: `START_HERE` → `EQUITY_HANDOFF` §0 → `EQUITY_WORKFLOW` §0~§1.
문서층 작업: `DOC_LAYER_KICKOFF` → `STAGE_HANDOFF` §4 → `STAGE_DESIGN` §0·§7·§10 → `DOC_DESIGN`.
일일 증분 작업: `plans/2026-09-09-daily-incremental.md` 상태 블록 → §1 결정 → 현재 페이즈.

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

## 작업 규칙

- 작업 단위(조사·플랜·태스크·페이즈)가 끝날 때마다 **같은 커밋에서 상태 문서를 갱신**한다: 플랜의 상태 블록·체크박스, 이 README 의 상태 표, `START_HERE.md` §4, 새 결정은 `DECISIONS_PENDING.md`, 새 부채는 `TECH_DEBT.md`.
