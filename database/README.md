# database — 국내증시 원장 · stage · 문서층 파이프라인

KRX·키움·KIS·DART·WISE 원장 수집기, stage 층 빌더, 문서층(L1) 파서와 설계 문서를 둔다.
데이터는 카엘 서버(`~/quant-ledger`)가 정본이며 이 폴더에는 **코드와 문서만** 둔다
(`database/data/`·`logs/`·`scratchpad/` 는 `.gitignore`).

## 층 구조

```
원장(raw)   SQLite 5개  data/raw/{krx,kiwoom,kis,dart,wisereport}.db   ← src/backfill_*.py + 서버 크론
    ↓ src/stage  (duckdb → parquet, 게이트 검증, 스냅샷 재현성)
stage       parquet 61테이블  data/stage/                              ← STAGE_SPEC · STAGE_DESIGN · STAGE_HANDOFF
    ↓ (착수 단계)
equity      통합층 equity.db                                           ← EQUITY_KICKOFF
```

문서층(L1)은 DART 보고서 ZIP 을 stage 의 여섯 번째 원장 소스로 다룬다 (`DOC_DESIGN`).

## 디렉터리

| 경로 | 내용 |
|---|---|
| `src/` | 수집기(`backfill_*.py`, `api.py`, `dart_universe.py`, `sweep_disclosure.py`, `master_daily.py`), stage 패키지(`src/stage/`, `python -m stage --table <t>`), 파일럿 통합층(`build_*.py`·`finalize.py`·`fin_map.py` — STAGE_DESIGN §8 이 파일럿 보존·로직 재사용으로 명시) |
| `scripts/` | 서버 크론·러너: `daily_dart.sh`(00:01 KST), `daily_wise.sh`(06:00 KST), `run_stage.sh`, `run_stage_all.sh`, `run_survey*.sh` |
| `tests/` | stage 테스트 |
| `survey/`, `survey_out/v2/` | 원장 전 테이블·컬럼 어휘 전수 측정과 결과. stage (p,s)·부호·결측 규칙의 실측 근거. 재생성은 서버에서 `scripts/run_survey_v2.sh` |
| `eval/table_schema/` | 자유 서식 표 스키마 추론 골든셋 100표 (라벨링 대기) |
| `docs/` | 현행 설계·계약 문서 (아래 색인) |
| `docs/archive/` | 2026-08-21~26 조사·판정 기록. 결정의 근거로만 참조하고 갱신하지 않는다 |
| `docs/plans/` | 구현 플랜 |
| `docs/outlines/` | DART 보고서 내용 구조도 샘플 7건 (`DOC_DESIGN` 참고 산출) |

## 문서 색인

### 현행 (docs/)

| 문서 | 내용 |
|---|---|
| `DATA_CATALOG.md` | 수집 대상 데이터 전수 목록, 통합 판정본 (08-21) |
| `FACTORS.md` | 팩터 정본 목록. 수집 우선순위의 근거 (08-25) |
| `COLLECT_PLAN.md` | 백필·일일 증분 실행 계획 (08-21) |
| `DART_CENSUS.md` | DART OpenAPI 전수조사 종합, 수집 범위 확정 (08-24) |
| `DART_DESIGN.md` | DART 수집기 확정 설계 (08-24) |
| `STAGE_SPEC.md` | stage 층 계약·실측 사실의 정본 (08-28) |
| `STAGE_DESIGN.md` | stage 구현 설계 v2.2 (09-02) |
| `STAGE_HANDOFF.md` | stage → equity 인계: 읽기 계약·테이블별 메모 (09-03) |
| `EQUITY_KICKOFF.md` | equity 층 착수 노트 (09-03) |
| `DOC_LAYER_KICKOFF.md` | 문서층(L1) 착수 노트 (09-03) |
| `DOC_DESIGN.md` | 문서층(L1) 설계 v1.1 (09-04) |
| `plans/2026-09-04-doc-stage-p1.md` | 문서층 P1 구현 플랜 (PR #54 병합) |

읽는 순서 — equity 작업: `EQUITY_KICKOFF` → `STAGE_HANDOFF` → `STAGE_SPEC` §3·§4·§5.
문서층 작업: `DOC_LAYER_KICKOFF` → `STAGE_HANDOFF` §4 → `STAGE_DESIGN` §0·§7·§10 → `DOC_DESIGN`.

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

- 서버 배포: `rsync -avz --exclude='.venv' --exclude='__pycache__' database/src/ kael-server:~/quant-ledger/src/` (`scripts/` 도 동일).
- 키·토큰: 코드는 `QL_ENV` 또는 `~/kael-system-v3/.env` 에서만 읽는다. 레포에는 넣지 않는다.
