---
plan_version: 2
project: strategy-language-2-0
project_status: IN_REVIEW
current_phase: P1,P2
current_pr: P1-06,P2-01,P2-02,P2-03,P2-04,P2-05
active_prs: [P1-06, P2-01, P2-02, P2-03, P2-04, P2-05]
parallel_window: [P1-06, P2-01, P2-02, P2-03, P2-04, P2-05]
last_updated: 2026-09-27T04:57:05+09:00
planned_prs: 29
merged_prs: 6
approved_prs: 10
progress_percent: 21
---

# schema 1.2 · 그래프 표현 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_REVIEW` |
| Current phase | `P1,P2` |
| Current/next PR | `P1-06,P2-01,P2-02,P2-03,P2-04,P2-05` |
| Active PR | `P1-06, P2-01, P2-02, P2-03, P2-04, P2-05` |
| Progress | `6 / 29 merged (21%)` |
| Approved | `10 / 29` |
| Aggregated at | `2026-09-27 04:57 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. frontmatter와 위 표, Phase 집계는
[update-plan-progress.ps1](./tools/update-plan-progress.ps1)이 생성하며 직접 수정하지 않는다.

## 현재 결정

- 2026-09-20 제품 소유자 결정: 템플릿·프리셋은 주 진입 경로가 아니다(튜토리얼 전용). 목표는 범용
  전략 구성이며, 언어 밖에서 정할 수 있는 것(시장·기간·유니버스·수수료·체결)은 실행 설정으로 뺀다.
- 2026-09-20 제품 소유자 결정(개정): **기존 YAML은 남긴다.** 표현은 YAML과 그래프 둘뿐이다. 새
  팩터 표기(`steps`)와 섹션 재구성은 하지 않는다. schema 1.2는 `data`·`execution`·`missing_policy`
  제거 + 추가 필드 3개(`signal.normalization`, 횡단면 eligibility, `risk.risk_factor_id`)다. 배관은
  그래프 UI가 숨긴다. Form·JSON 탭은 은퇴한다. 1.0·1.1 revision은 동결 이력, 업그레이더 한 벌.
- 한글 어휘의 소유: 키(`x-description-key`, 연산자 카탈로그)는 backend, 문장은 frontend i18n. 적용
  조건 문장과 같은 규칙.
- 결합 정규화는 `signal.normalization`(기본 `rank`)이 소유한다. 1.1에서 올라온 문서는 `none`을
  명시해 실행 의미를 보존한다.
- 그래프 라이브러리 도입은 P6-01 ADR이 결정한다. 레시피 빌더(P5)가 먼저 비전공자 경로를 닫는다.
- 2026-09-20 개정(P2-01): OpenAPI를 바꾸는 backend PR은 `frontend/src/shared/api/generated`도 같은
  PR에서 재생성해 별도 커밋으로 넣는다. CI `frontend` job의 `api:generate` diff 게이트 때문이다.
  생성 산출물만 넣고 소비자 배선은 P3-01 그대로다. `browser-e2e` job은 계속 P3-03의 exit 조건이다. P2 스택은 backend gate만 merge gate로 삼는다.
- 2026-09-27 리드 결정(머지 전략): P2-01·P2-02 는 main 반영 merge 커밋 리뷰 뒤 개별로 main 에
  머지한다. **P2-03 ~ P3-02 구간은 main 에 개별 머지하지 않는다.** schema 1.2(P2-03)는 실행 요청에
  `environment` 를 요구하는데, 브라우저가 그 값을 싣는 배선이 P3-02 의 실행 설정 패널이라, 그 사이
  tip 을 main 에 넣으면 화면에서 백테스트·추적이 422 `backtest.run.environment_required` 로 막히는
  사용자 회귀가 된다. 그래서 P3-02 가 승인되고 그 tip 에서 e2e 가 전부 green 이 되면 이 구간을
  한 묶음으로 머지한다. 구간 안에서도 main 반영 cascade 는 계속한다. 이 구간 PR 의 CI
  `browser-e2e` 는 red 일 수 있고, 각 PR 본문 제약사항에 실패 목록과 원인을 적는다. P2-03 main 반영
  시점 실측 원인은 둘이고 둘 다 이 묶음 안에서 해소된다. (1) 실행 요청 `environment` 미배선(P3-02):
  브라우저의 백테스트 시작이 422 `backtest.run.environment_required`, 추적이 422
  `portfolio.strategy.invalid`(이슈 `run_environment.required`)다. (2) 1.1 → 1.2 업그레이더(P2-09):
  US-SM-07 의 "1.0 동결 revision 업그레이드" 시나리오에서 업그레이드 응답이 1.1 원문이라 1.2 편집기가
  구조 오류로 본다. 스토리 태그 e2e 는 잠그거나 태그를 떼지 않는다.
  backend·frontend 단위 게이트와 유저 스토리 하네스 정적 검사는 green 이어야 한다. WORKFLOW 1절의
  "P2 PR 은 backend gate 만 merge gate"와 "P2-09 전 실 DB 1.2 저장 금지"에 맞춘 결정이다.
- P1 스택과 P2 스택은 독립이라 병렬 진행할 수 있다. P3-01은 P1 스택 끝(P1-06)·P2-09 둘 다 merge 뒤 시작한다.
- reviewer 서브에이전트는 Opus로만 배정한다. Phase 종료마다 SoT·책임분리 점검 서브에이전트를 돌린다.
- 완료 정의는 spec 5절의 6항이다. 특히 퀀트 아이디어 5개(12-1 모멘텀, 저PBR+고ROE, 20일 이평 돌파,
  거래대금 상위 20%, 변동성 역가중)가 그래프 탭만으로 백테스트에 도달해야 한다.

## 상태 값

| 상태 | 의미 |
|---|---|
| `PLANNED` | 범위만 정의됨 |
| `READY` | dependency가 충족되어 시작 가능 |
| `WAITING` | 선행 PR을 기다림 |
| `IN_PROGRESS` | 구현 중 |
| `SELF_CHECK` | 구현 완료, 작성자 검증 중 |
| `IN_REVIEW` | diff 고정, review sub-agent 검토 중 |
| `CHANGES_REQUESTED` | blocking finding 수정 중 |
| `APPROVED` | reviewer 승인, merge gate 확인 중 |
| `MERGED` | 로컬 gate 후 main merge 완료 |
| `PAUSED` | 제품·계약 결정이 필요해 일시 정지 |

active PR 수와 병행 규칙은 [yaml-ui WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 그대로
따른다(재서술하지 않는다). 집계 도구는 PR row 상태에서 active를 뽑아 `parallel_window`와 일치하는지 검사한다.

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package and contract docs | 1 | 1 | `MERGED` |
| P1 | In-screen friction removal on 1.1 | 6 | 5 | `IN_REVIEW` |
| P2 | Backend schema 1.2 (environment split, 9 PRs) | 9 | 0 | `IN_REVIEW` |
| P3 | Frontend 1.2 adaptation | 3 | 0 | `WAITING` |
| P4 | Graph level 1: pipeline | 4 | 0 | `WAITING` |
| P5 | Graph level 2: recipe | 3 | 0 | `WAITING` |
| P6 | Graph level 3: node canvas | 3 | 0 | `WAITING` |
| **Total** |  | **29** | **6** | **21%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-01` |
| Intent | 기획 패키지·spec을 main에 올리고 ADR·로드맵·SoT가 이 initiative를 가리키게 한다 |
| Acceptance | WORKFLOW P0-01 |
| Non-goals | 코드 변경 |
| Branch/worktree | `docs/strategy-language-2-0-plan` |
| Base SHA | `5f97f8c` (main) |
| Head SHA | 재검토 중 |
| Diff stat | 문서만. 리뷰 반영 커밋 포함, PR #167 diff 참조 |
| Focused tests | `tools/update-plan-progress.ps1 -Check` |
| Full gate | 코드 변경 없음 — 문서 링크 존재 확인 |

### P1-05

| 항목 | 값 |
|---|---|
| PR | `P1-05` |
| Intent | 초보자가 가장 자주 만나는 구조 오류 6종이 전부 영문이던 것을 한글 문장으로 바꾸고, 그래프 진단이 전략 문서 네임스페이스로만 나가게 한다 |
| Acceptance | WORKFLOW P1-05 |
| Non-goals | 의미 오류 문구 손질(이미 한글), 그래프 새 화면(P4·P5), 실행 설정 UI(P2·P3) |
| Branch/worktree | `feat/lang2-p1-05-structure-errors-ko` / `wt-lang2-p1-05` |
| Base SHA | `e6fb10b0` (P1-04 tip, PR #181. 2026-09-26 P1-02 표식 수정 `ac3a3d0e` 위로 cascade rebase — 직전 base `0ce311bb`) |
| 문장 소유 | backend. `.claude/rules/strategy-workbench-sot.md` authoring 진단 코드 행("compile 진단의 `message`는 backend가 한글 문장으로 완성해 보내고 Problems panel은 그대로 보여준다")을 따른다. `messages.ts`에 진단 문장 템플릿을 두지 않는다 — P1-04와 겹치는 파일이 없다 |
| 변경 파일 | backend 문장: `domain/strategy/_hydrate.py`(`structure.*` 전부 + 오타 제안 + `LEGACY_SHAPE_CODE`), `adapters/outbound/document_codec/_codec.py`(`document.*`·`yaml.*`·`<format>.syntax`) |
| | backend 1.0 힌트: `domain/strategy/_upgrade.py`(`legacy_shape_hints` — 판정은 `UPGRADE_STEPS`와 같은 조건), `application/strategy_authoring/_service.py`(키 범위 코드 집합), `domain/strategy/facade/document.py` |
| | backend 네임스페이스: `domain/factor/_validation.py`(`FACTOR_GRAPH_CODES` 게이트, 순환·중복에 `node_id`), `domain/factor/facade/validation.py`, `domain/strategy/_constraints.py`(`EXPRESSION_CODES` 20개 확장·`expression_code()`), `domain/strategy/facade/constraints.py`, `domain/strategy/_validation.py`(`semantic_issue`가 `factor.` 접두사 거절), `application/portfolio_design/_service.py` |
| | backend 테스트: `tests/domain/test_strategy_diagnostic_messages.py`(신규, 74건), `tests/domain/test_strategy_constraints.py`, `tests/domain/test_strategy_hydrate.py`, `tests/contract/test_strategy_authoring_fixtures.py`, `tests/integration/test_truthful_pipeline.py` |
| | frontend: `features/edit-strategy/model/document-upgrade.ts`(배너가 `structure.legacy_shape`에도 반응), `model/use-compile-document.ts`(같은 코드는 키 범위), 테스트 2·e2e 1 |
| | backend CORS: `bootstrap/_http.py`(`STRATEGY_WORKBENCH_ALLOWED_ORIGINS`)·`bootstrap/facade/http.py`·`tests/test_server_entrypoint.py`·`backend/README.md` |
| | 1차 리뷰 반영: `domain/strategy/_upgrade.py`(`is_upgradeable_document`)·`application/strategy_authoring/_service.py`·`domain/factor/_validation.py`(순환을 SCC로)·`tests/application/test_strategy_authoring_upgrade.py`·`tests/domain/test_strategy_upgrade.py`·`tests/integration/test_strategy_document_upgrade_http_api.py` |
| | 그 외 frontend: `eslint.config.js`(Playwright 산출물 무시), `package.json`(빌드를 npm 스크립트에서 러너로 옮김 — 러너는 빌드를 잠금 **밖**에서 돌린다, `--update-snapshots=changed`), `e2e/ports.d.mts`, `e2e/free-port.mjs`(IPv6), `scripts/capture-manual-screenshots.mjs`(포트 상수), `e2e/workbench.workflow.spec.ts`, 시각 기준선 4장 |
| | 문서: `docs/manual/strategy-workbench/README.md`(오류 문장 예시·`—` 읽는 법) |
| | e2e 인프라(저장소 전체 결함, 리드 지시로 이 PR에서): `frontend/e2e/{lock,free-port,ports,port-owner,build-command}.mjs`(신규)·`lock.test.mjs`(27건, 두 프로세스 경합 1건 포함)·`run-playwright.mjs`·`playwright.config.ts`·`vite.config.ts`·`workbench-helpers.ts`·`workbench.infrastructure.spec.ts`·`e2e/README.md`. 머신 단위 잠금으로 워크트리 간 e2e를 직렬화하고, 포트를 `PW_BACKEND_PORT`·`PW_PREVIEW_PORT`로 연다 |
| Focused tests | `uv run pytest tests/domain/test_strategy_diagnostic_messages.py tests/domain/test_strategy_constraints.py tests/domain/test_strategy_hydrate.py`, `npx vitest run src/features/edit-strategy/__tests__/document-upgrade.test.ts src/features/edit-strategy/__tests__/factor-graph-panel.test.tsx` |
| Head SHA | `45f1c4a3` (cascade rebase 뒤). 리뷰가 본 옛 SHA 대응: `5e8e0af0`→`91363442`, `a55e7a67`→`f9ae9d2a`, `3755b186`→`7f7eb69c`, `a263276b`→`45f1c4a3` |
| Diff stat | base `e6fb10b0`(= 옛 `0ce311bb`) 대비 52 파일 `+2920 −167` (시각 기준선 4장 포함, 3차 반영·추가분까지) |
| Full gate | backend `pytest -q` 1694 passed · `ruff check src tests examples scripts` clean · `pyright` 0 · frontend `typecheck`·`lint`·`build` clean · `npm test` · e2e 20 passed(잠금 래퍼 아래). OpenAPI·runtime schema 재생성 diff 0 → 생성 SDK 변경 없음. cascade tip `45f1c4a3` 재실행은 `검증 기록` |

### P1-06

| 항목 | 값 |
|---|---|
| PR | `P1-06` |
| Intent | Phase 1 감사(2026-09-21)의 BLOCKING 중 남은 PLAN 기록 결함(DEFECT-P1X-002)을 닫고, SoT 대장 비차단 N8·N9를 정정하며, 담당이 없던 이월 항목에 담당 PR을 붙인다 |
| Acceptance | WORKFLOW P1-06 |
| Non-goals | 코드 변경. 매뉴얼 재촬영(N1)은 담당만 정하고 실행하지 않는다 |
| Branch/worktree | `docs/lang2-p1-06-phase1-records` / `wt-lang2-p1-06` |
| Base SHA | `45f1c4a3` (P1-05 tip, cascade rebase 뒤) |
| 변경 파일 | `docs/planning/strategy-language-2-0/PLAN.md`, `WORKFLOW.md`, `.claude/rules/strategy-workbench-sot.md` |
| Focused tests | `tools/update-plan-progress.ps1 -Check`, `uv run --locked python -m quant_study_dev.conflict_markers` |
| Full gate | 문서만 바뀐다. 코드 게이트는 base `45f1c4a3`의 결과(`검증 기록`)와 같다 |

---

### P2 스택

| 항목 | 값 |
|---|---|
| PR | `P2-01` |
| Intent | 실행 설정을 전략 문서 밖에서 받을 자리를 만든다. 1.1과 완전 호환(브리지) |
| Acceptance | WORKFLOW P2-01 |
| Non-goals | StrategySpec 변경, `missing_policy`(P2-02), enum 물리 이동(P2-03), frontend 소비자 배선(P3-01) |
| Branch/worktree | `feat/lang2-p2-01-run-environment` / `wt-lang2-p2-01` |
| Base SHA | `15f8ca8` (`docs/strategy-language-2-0-plan` tip) |
| Head SHA | 2차 리뷰 대상 `1e0b095` + P3 후속 커밋 1개(이 PLAN 갱신과 같은 커밋) |
| Diff stat | 커밋 4개 · 31파일(신규 7). `92b304e` 모델·canonical hash·브리지·런타임 스키마(10파일) → `6b451ef` preview·trace·run 배선(16파일) → `a7bf368` 스키마 엔드포인트·OpenAPI(6파일) → `54af48c` 생성 SDK(3파일) |
| Focused tests | `uv run pytest tests/domain/test_run_environment.py tests/application/test_run_environment_wiring.py tests/architecture tests/integration/test_run_environment_schema_http_api.py -q` |
| 제약사항 | `run_fingerprint` 표기가 바뀐다 — `run_spec`에 `environment` 키가 늘어 같은 입력의 지문이 P2-01 이전과 다르다(실측 `258b4637…` → `7074f1aa…`). `run_fingerprint`를 읽는 캐시 lookup은 코드베이스에 없어 잘못된 캐시 히트는 불가능하고, 영향은 감사 축이다: P2-01 이전에 저장된 매니페스트의 지문은 재계산 대상이 아니다. 지문에 표기 버전을 넣을지는 캐시 도입 시점(P2-09)에 정한다. 매니페스트의 `fee_bps`·`slippage_bps`·`participation_rate` 평면 필드는 `environment`와 중복이지만 호환을 위해 남긴다(대조로 불일치를 막고, 제거는 P2-03) |
| Full gate | backend `uv run pytest -q`(1480 passed, 13 skipped) · `ruff check src tests` · `ruff format --check`(변경 30파일) · `pyright`(duckdb 미설치 4건만, 기존) / frontend `npm run typecheck`·`lint`·`test`(639, 57파일)·`build` |

작업 파일(신규 7 + 수정 24):

- 신규 backend src — `domain/backtest/_bridge.py`, `domain/backtest/_schema.py`,
  `domain/backtest/facade/environment.py`
- 신규 backend tests — `tests/domain/test_run_environment.py`,
  `tests/application/test_run_environment_wiring.py`,
  `tests/architecture/test_run_environment_ownership.py`,
  `tests/integration/test_run_environment_schema_http_api.py`
- domain — `backtest/_models.py`, `backtest/_canonical.py`, `backtest/facade/__init__.py`,
  `strategy/_schema.py`, `strategy/facade/schema.py`, `strategy/facade/specification.py`
- application — `portfolio_design/_models.py`·`_service.py`·`_trace_models.py`·`_trace_service.py`·
  `facade/__init__.py`, `backtest_run/_service.py`, `strategy_authoring/_service.py`·
  `facade/__init__.py`·`facade/authoring.py`
- adapters — `inbound/http_api/_app.py`, `outbound/backtest_engine/_adapter.py`
- 계약 산출물 — `backend/openapi.json`, `frontend/src/shared/api/generated/{index.ts,sdk.gen.ts,types.gen.ts}`
- 기존 테스트 갱신 — `tests/domain/test_backtest_fingerprint.py`,
  `tests/test_backtest_artifact_store.py`, `tests/test_target_tape_strategy.py`,
  `tests/integration/test_truthful_pipeline.py`·`test_backtest_http_api.py`·
  `test_backtest_strategy_reference_http_api.py`

| 항목 | 값 |
|---|---|
| PR | `P2-02` |
| Intent | 결측 정책을 팩터 그래프에서 실행 설정으로 옮기되 plan의 일부로 남긴다 |
| Acceptance | WORKFLOW P2-02 |
| Non-goals | `data`·`execution` 제거·`schema_version` 1.2(P2-03), 업그레이더(P2-09), frontend 실행 설정 배선(P3-01) |
| Branch/worktree | `feat/lang2-p2-02-missing-policy` / `wt-lang2-p2-02` |
| Base SHA | `fff33fd` (`feat/lang2-p2-01-run-environment`) |
| Head SHA | `c5bec5a` (코드 마지막 커밋. 브랜치 head는 이 PLAN 갱신 커밋) |
| Diff stat | 커밋 10개 · 36파일(신규 1) · 776/120줄. 1차: `e3dd414` domain·application 배선과 계약 산출물(23파일) → `13fc203` 생성 SDK → `78b9c5c` frontend 카탈로그 소비자 → `6692d9d` 포매팅 부채 정리(동작 불변) → `e702302` 문서. 리뷰 반영: `7776a98` sandbox 결측 정책 fallback → `81e274f` 생성 SDK → `d1965bd` frontend mock → `c5bec5a` 죽은 catch·주석 정정 → `925b2a2` 문서 |
| Focused tests | `uv run pytest tests/domain/test_factor_missing_policy.py tests/domain/test_run_environment.py tests/domain/test_factor_trace.py tests/architecture tests/application/test_run_environment_wiring.py -q` |
| Full gate | backend `uv run pytest -q`(리뷰 반영 후 1551 passed) · `ruff check src tests` · `ruff format --check`(이 PR 변경 파일 21개 clean, 저장소의 기존 부채 21파일은 그대로) · `pyright`(0 errors, `uv sync --all-extras` 후) / frontend `npm run api:generate`(diff 0)·`typecheck`·`lint`·`test`(639, 57파일)·`build` / `uv run --project backend pytest database/tests/test_equity_s21_workbench.py -q`(14 passed) |

| 항목 | 값 |
|---|---|
| PR | `P2-03` |
| Intent | 전략 문서에서 실행 설정을 빼고 `CURRENT_SCHEMA_VERSION`을 1.2로 올린다 |
| Acceptance | WORKFLOW P2-03 |
| Non-goals | 업그레이더 버전 디스패치·upgrade 응답 `environment`(P2-09), `signal.normalization`(P2-04), 횡단면 eligibility(P2-05), frontend 실행 설정 UI(P3-01·P3-02), 실 DB 마이그레이션 |
| Branch/worktree | `feat/lang2-p2-03-schema-1-2` / `wt-lang2-p2-03` |
| Base SHA | `d1836ed7` (`feat/lang2-p2-02-missing-policy` tip) |
| Head SHA | 1차 리뷰 대상 `7ec8f337` + 리뷰 반영 커밋(아래 Review 열) |
| Diff stat | 1차 리뷰 대상 커밋 14개 · 121파일 +1732/-1610. 그중 손으로 쓴 코드는 `backend/src`·`backend/scripts`·`database/scripts` 33파일 +417/-416이고 나머지는 생성 산출물(OpenAPI·SDK·runtime schema)·fixture·기준선 PNG와 그에 맞춘 테스트다 |
| Focused tests | `uv run pytest tests/domain/test_strategy_hydrate.py tests/domain/test_run_environment.py tests/architecture tests/application/test_run_environment_wiring.py tests/contract/test_strategy_repository_frozen_1_0.py tests/contract/test_strategy_repository_retired_1_1.py -q` |
| 제약사항 | **P2-09 전까지 은퇴 버전 문서의 업그레이드 결과는 저장·실행할 수 없다.** `POST /api/v1/strategy-documents/upgrade`가 아직 1.1까지만 올리므로(1.1 → 1.2 step 등록과 응답 `environment`는 P2-09 acceptance) 돌려준 원문의 compile 진단에 `structure.unsupported_schema_version`이 실린다. 저장된 은퇴 버전 row는 repository codec이 `strip_retired_execution_settings`까지 태워 현재 버전으로 읽으므로 목록·이력·문서 조회는 그대로 동작한다. **P2-03~P3-02 구간 브라우저 e2e는 시나리오 4건이 `test.fixme`다.** 상황: 프론트가 실행 요청에 `environment`를 싣지 않는다(그 배선은 P3-02 실행 설정 패널). 인풋: 편집기에서 백테스트 버튼 → `POST /api/v1/backtests`에 `environment` 없음. 에러 위치: `application/backtest_run/_service.py`의 `start()`가 `require_environment`로 422 `backtest.run.environment_required`를 낸다. 위험성: 브라우저에서 시작한 run이 전부 거절되어 e2e가 실제 회귀를 더는 못 잡는다. 전략 디버거 trace 요청도 같은 배선이 없어 `run_environment.required`로 거절되고 "추적 재현 정보" 패널이 뜨지 않는다(같은 fixme 시나리오 안이다) — 그 구간의 백테스트 경로는 명시 `environment`를 싣는 backend 통합 테스트가 검증한다. 잠근 시나리오: `workbench.workflow.spec.ts`의 `creates, recovers, validates, versions, traces and backtests`·`upgrades a frozen 1.0 revision …`, `workbench.real-equity.spec.ts`의 `edits the graph on real data …`, `workbench.infrastructure.spec.ts`의 `keeps a real debugger trace legible and inside the viewport`(trace 요청이 거절되어 "추적 재현 정보" 패널이 뜨지 않는다 — 픽셀 차이가 아니다). 되살리는 지점: P3-02(패널로 `environment` 배선·fixme 해제), P3-03(e2e fixture 1.2로 최종 시나리오 재작성). 크기: 이 PR은 12절 상한(600줄·10파일)을 크게 넘는다 — 최상위 모델 필드 두 개를 지우는 변경이라 hydrate·schema·validation·explanation·compile·adapter·fixture·테스트가 한 커밋 단위로 같이 움직여야 컴파일되고, enum 이동만 떼어내도 상한 안에 들어오지 않는다 |
| Full gate | backend `uv run pytest -q`(1544 passed) · `ruff check src tests` · `ruff format --check`(이 PR 변경 파일 clean) · `pyright`(0 errors) / frontend `npm run api:generate`·`typecheck`·`lint`·`test`(639, 57파일)·`build` / `uv run --project backend pytest database/tests -q`(base `1dee07a` 와 같은 41 failed/1268 passed/33 errors — Windows symlink 권한(`WinError 1314`)으로 나는 기존 실패다) |

| 항목 | 값 |
|---|---|
| PR | `P2-04` |
| Intent | `signal.normalization`(none·rank·zscore)을 1.2 언어에 넣고 가중 합 **전에** 횡단면 정규화를 적용한다 |
| Acceptance | WORKFLOW P2-04 |
| Non-goals | 연산자 `availability` 판정(WORKFLOW 상 P2-07), 단위 경고 `strategy.signal.unit_mismatch`(P2-07), 횡단면 eligibility(P2-05), `risk.risk_factor_id`(P2-06), 업그레이더의 `normalization: none` 명시(P2-09), frontend i18n·소비자 배선(P3-01) |
| Branch/worktree | `feat/lang2-p2-04-normalization` / `wt-lang2-p2-04` |
| Base SHA | `0dd740e8` (`feat/lang2-p2-03-schema-1-2` APPROVED tip) — 옛 base 위 `dc8030d3` 계열 5커밋을 replay 했다. `git range-diff` 결과 코드 4커밋은 동일(`=`), PLAN 커밋만 base 쪽 PLAN 변경을 흡수해 달라졌다 |
| Head SHA | 커밋 SHA는 PR 본문 참조 |
| Diff stat | 커밋 11개(공식 SoT 정리 1 + 기능 1 + 계약 산출물 1 + frontend 기대값 1 + 문서 1 + 1차 리뷰 반영 `28003cf1` 1 + 시각 기준선 `80a5ddf5` 1 + PLAN 상태 갱신 2 + 2차 리뷰 반영 `2a10798d` 1 + 이 PLAN 커밋 1). `0dd740e8..80a5ddf5` 실측 36파일 +945/−43, 문서·PNG 제외 31파일 +833/−34 |
| Focused tests | `uv run pytest tests/domain/test_portfolio_pipeline.py tests/domain/test_strategy_hydrate.py tests/domain/test_strategy_diff.py tests/domain/test_strategy_spec.py -q` |
| 제약사항 | 12절 상한 중 **파일 수(10)를 넘긴다** — 29파일(리뷰 반영 뒤 문서·PNG 제외 31파일). 넘긴 몫은 (a) 골든 `spec_hash` 리터럴을 한 줄씩 고치는 테스트 6파일, (b) 새 테스트 4파일이다. src 변경은 6파일 99줄이고 handwritten diff 는 약 510줄로 줄 수 상한(600) 안쪽이다. 골든 hash 리터럴이 파일 6곳에 복사돼 있는 것 자체가 부채지만 한 곳으로 모으는 정리는 이 PR 범위 밖이라 backlog 로 남긴다. 중간 상태 base `5653c14e` 에서 작업하던 동안에는 P2-03 잔재(import 붕괴·`typecheck:e2e`·테스트 3건) 우회 커밋 두 개를 앞에 뒀고, P2-03 최종 tip `7ec8f337` 이 같은 수정을 담아 replay 에서 버렸다 |
| Full gate | backend `uv sync --all-extras` · `maturin develop --release` · `uv run pytest -q`(1571 passed, 0 failed) · `ruff check src tests` · `ruff format --check`(이 PR 변경 파일 clean) · `pyright`(0) · `export_openapi.py` · `export_runtime_schema.py`(둘 다 재생성 후 diff 0) / frontend `npm ci`·`api:generate`(diff 0)·`typecheck`·`typecheck:e2e`·`lint`·`test`(639, 57파일)·`build` / e2e: 시각 기준선 4장(`strategy-workbench.png`, 1440/1920 × light/dark)을 계약 해시 변경으로 재생성(`80a5ddf5`). 이 수치들은 옛 tip 기준이다. 2차 리뷰 반영 뒤 게이트 전체와 `npm run test:e2e` 는 이 PLAN 커밋을 포함한 push tip 에서 다시 돌려 PR #184 댓글에 기록한다 |

| 항목 | 값 |
|---|---|
| PR | `P2-05` |
| Intent | 유니버스 필터에 횡단면 규칙(`top_percent`·`top_count`)을 넣고 프레임 컴파일을 2-pass 로 나눈다 |
| Acceptance | WORKFLOW P2-05 |
| Non-goals | `availability`·`unit_mismatch`(P2-07), `risk.risk_factor_id`(P2-06), 업그레이더(P2-09), 프론트 실행 설정 UI(P3). **탈락 사유 i18n 표도 범위 밖이다** — trace 화면은 기존 12종을 포함해 사유를 번역 없이 원문 코드로 찍으므로 새 값도 빈칸 없이 렌더된다. 표를 만들면 기존 12종 표시 문구까지 바꾸는 변경이라 소비자 배선을 소유한 P3-01 몫이다(리드 승인, 2026-09-21) |
| Branch/worktree | `feat/lang2-p2-05-eligibility` / `wt-lang2-p2-05` |
| Base SHA | `35089901` (`feat/lang2-p2-04-normalization` tip, P2-03 APPROVED `0dd740e8` 위 replay + 1~5차 리뷰 반영). `git rebase --onto a823ebc8 dc8030d3` 로 옮긴 뒤 P2-04 의 문서 커밋을 따라 `git rebase --onto 41c92616 a823ebc8`, P2-04 2차 리뷰 반영 뒤 `git rebase --onto a9f4ed93 41c92616`, 3차 리뷰 반영 뒤 `git rebase --onto 350e8a40 a9f4ed93`, 4차 리뷰 반영 뒤 `git rebase --onto 34b20ef5 350e8a40`, 5차 리뷰 반영 뒤 `git rebase --onto 35089901 34b20ef5` 로 옮겼다. 뒤의 다섯 번은 PLAN 만 충돌했고 코드 변경분은 바이트 단위로 같다 — 코드 커밋 중 첫 커밋만 `_compiler.py` 에서 충돌했다(P2-04 의 `_signal_value` 와 이 PR 의 `_apply_cross_sectional_eligibility` 가 같은 자리에 추가된 최상위 함수라 둘 다 남김). PLAN 커밋 3개는 새 base 구조 위에 P2-05 패킷·행·리뷰 기록만 얹었다. 그 이전 이력: 최초 구현 `bb8f3843` 위 → `dc8030d3` 위 |
| Head SHA | `a35c1e40` 모델·연산자·2-pass·탈락 사유 → `dc4f14fc` `top_*` 값 검증 → `bcba9353` 계약 산출물 재생성 → `22705f6b` 비유한 cut 크기 거절 → `e1c0adda` 프론트 enum 단언 → `3ca17587` 이 패킷 → `765b3815` 주석 → `377aac3e` validator exhaustive 리팩터 → `1358fd4c` 패킷 갱신 → `641c6f3b` 1차 리뷰 반영(동점 역순 단언·필드 조회 통일) → `efd6a768` 리뷰 기록 → `57731080` 시각 기준선 재생성(계약 해시 변경, `strategy-workbench.png` 4장) → `8587d413` P2-04 3차 리뷰 재현 테스트(`top_count` × `factor_score`) → 이 PLAN 상태 갱신 커밋 |
| Diff stat | 실측(`git diff --numstat`, 생성 산출물 4파일·PLAN 제외) **13파일 · +810 / −35** — src 6파일 +250/−23, test 7파일(backend 6 + frontend 1) +560/−12. 1차 리뷰 때 690 은 반영 커밋의 +46줄이 빠진 값이었고(2차 리뷰 R2-P205-001), 2차 APPROVE 때 736 에 P2-04 3차 리뷰 재현 테스트 `8587d413`(+71)와 첫 커밋의 enum 개명 한 줄(+3/−1)이 더해졌다. **12절 상한(600줄·10파일)을 넘는다**(사유는 아래 제약사항) |
| Focused tests | `uv run pytest tests/domain/test_eligibility_cross_section.py tests/domain/test_strategy_constraints.py tests/domain/test_portfolio_pipeline.py tests/domain/test_strategy_schema.py tests/integration/test_openapi_document_is_current.py -q` |
| 제약사항 | **12절 상한 초과(600줄·10파일 → 810줄·13파일), 분할하지 않는다.** 13파일 중 5개(`test_portfolio_pipeline`·`test_strategy_diff`·`test_strategy_trace_preflight`·`test_truthful_pipeline`·`facade/specification.py`)는 enum 개명이 강제한 1~2줄 import 수정이라 떼어 낼 단위가 없고, 신규 테스트 373줄은 12절이 "test 는 구현과 같은 PR"이라 분리할 수 없다. 나머지 src 변경(모델·컴파일러·validator)은 한 커밋 단위로 같이 움직여야 컴파일된다. e2e 는 기준선 재생성과 함께 잠금 러너로 돌렸다(`57731080`). 옛 base `bb8f3843` 에서 관측했던 backend 3 failed 와 `typecheck:e2e` 3건 실패, 1.1 스냅샷 기준선 불일치는 **전부 옛 base 산물이고 P2-03 최종 tip `7ec8f337`(#183)이 해소했다** — 새 base `dc8030d3` 위에서는 backend 1587 passed / 0 failed, `typecheck:e2e` 통과다 |
| Full gate | base `dc8030d3` 시절 실측 — backend `uv run pytest -q`(1587 passed / 0 failed) · `ruff check src tests` · `ruff format --check`(변경 12파일 clean) · `pyright` 0 errors · `export_openapi.py`·`export_runtime_schema.py` 재실행 diff 0 / frontend `npm ci`·`npm run api:generate` diff 0·`typecheck`·`typecheck:e2e`·`lint`·`test`(639, 57파일)·`build`. base `35089901` 재배치 뒤 게이트 전체와 `npm run test:e2e` 는 이 PLAN 커밋을 포함한 push tip 에서 다시 돌려 PR #187 댓글에 기록한다 |

P2-05 결정 4건(WORKFLOW 원문이 비워 둔 곳과 원문 밖으로 나간 곳):

1. **`ComparisonOperator` 를 남기지 않고 삭제했다.** WORKFLOW 는 "공유 `ComparisonOperator` 에
   얹지 않는다"만 적지만, `EligibilityRule` 이 그 enum 의 **유일한** 소비자라 분리하는 순간
   소비자 0개가 된다(팩터 그래프의 `comparison` 노드는 `domain/factor` 의 별개
   `FactorComparisonOperator` 를 쓴다). 남기면 facade `__all__` 과 OpenAPI 에 아무도 안 쓰는
   타입이 계속 발행되어, 다음 사람이 "공유 enum 이 있으니 여기 얹자"로 되돌아간다. 이름
   변경이 아니라 타입 분리이므로 같은 커밋에 뒀다.
2. **비율 cut 은 이진 부동소수가 아니라 문서가 쓴 10진 표기로 곱한다**(`Decimal(str(value))`).
   `math.floor(100 * 0.29)` 는 28 이다 — "상위 29%" 문서가 진단도 예외도 없이 한 종목을 더
   떨군다. 1~300 × 1~99% 격자에서 두 방식이 갈리는 조합이 16개 있다(0.29·0.57·0.58·0.7·
   0.82·0.35 …). WORKFLOW 는 "경계(소수점 절사)" 테스트만 요구하고 어느 산술인지는 비워 뒀다.
3. **`top_*` 의 `value` 범위를 compile 진단으로 막는다**(`strategy.eligibility.rule_value`,
   WORKFLOW acceptance 밖). 이 PR 이 `value` 의 의미를 연산자별로 갈라 놓았기 때문에 생긴
   구멍이라 같은 PR 이 닫는다 — "상위 20%"를 `20` 으로 적으면 `100 × 20 = 2000 → min(…, 100)`
   으로 **모집단 전체가 통과**해, 필터가 있는데 아무것도 거르지 않는 채 백테스트가 완주한다.
   포인터가 배열 항목(`eligibility.rules.N.value`)이라 평면 포인터 전용인 `_constraints.py`
   스칼라 카탈로그가 담을 수 없어 validator 가 소유한다.
4. **탈락 사유 i18n 표를 프론트에 새로 만들지 않았다.** WORKFLOW 는 "프론트가 모르는 enum
   값을 받아 trace 화면이 빈칸을 낸다"를 재생성 사유로 들지만, 실제 화면은 사유를 번역 없이
   원문 코드로 찍는다(`strategy-debugger.tsx:429`·`:597` 의 `<code>{…join(", ")}</code>`).
   기존 12종이 전부 그렇고 새 값도 같은 경로로 빈칸 없이 렌더된다. 표를 만들면 기존 12종의
   표시 문구까지 바꾸는 변경이라 소비자 배선을 소유한 P3-01 범위다(리드 승인, 2026-09-21).

WORKFLOW acceptance 중 **범위 밖으로 남긴 것 1건**:

- **`application/portfolio_design/_trace_service.py` 는 무변경이다.** WORKFLOW 는 "trace 투영과
  `_trace_service.py` 가 새 사유를 그대로 전달한다"를 요구하는데, 그 경로는 `CandidateDecision`
  객체를 그대로 실어 나르고 사유를 열거하지 않는다(`_trace_service.py:336-348`). 2-pass 가
  `_compile_frame` 안에서 `_candidate_trace` 보다 먼저 사유를 병합하므로 코드 변경 없이
  전달되며, 그 사실은 `test_the_rank_cut_reason_reaches_the_construction_trace` 가 고정한다.

P2-04 결정 7건(WORKFLOW 원문과 다르게 갔거나 원문이 비워 둔 곳 4 + 리뷰 반영 3):

1. **연산자 `availability` 판정은 이 PR 범위가 아니다.** WORKFLOW P2-04 acceptance 에 그 항목이
   없고, `strategy.operator.unsupported` 와 카탈로그 `availability` 는 P2-07 acceptance 가 통째로
   갖는다. WORKFLOW 1절 교차 제약도 "P2-06·P2-07 은 P1-03(연산자 카탈로그) merge 뒤 착수"라고
   적어, P1-03 레지스트리가 없는 이 스택에서 먼저 만들면 owner 가 둘이 된다.
2. **`plan_hash` 에는 `normalization` 을 넣지 않는다.** `plan_hash`(`domain/factor/_planning.py`)는
   팩터 그래프 하나의 실행 계획 지문이고, 정규화는 팩터를 **합칠 때** 쓰는 signal 단계 사실이라
   같은 팩터의 값·캐시가 정규화에 따라 달라지지 않는다. 넣으면 팩터 행렬 캐시가 근거 없이 쪼개진다.
   전략 단위 지문(`spec_hash` → `strategy_hash` → `tape_hash`)에는 canonical payload 를 통해
   자동으로 들어가며, 그 사실을 테스트로 고정했다.
3. **정규화 모집단은 `domain/factor` 의 횡단면 동료 집단 규칙을 그대로 쓴다.** 한 리밸런싱
   프레임은 기준일 하나이므로 남는 구분자는 `universe_member` 다(`_cross_section_indices` 와 같은
   키). 유니버스 밖 행이 유니버스 안 종목의 순위를 움직이지 않고, 유니버스 밖 행끼리는 자기들끼리
   한 집단이 된다. 결측·공개일 초과·비유한값은 모집단에서 빼며, 이는 `_score_candidate` 가 같은
   값을 점수에서 버리는 사유와 1:1 로 같다.
4. **순위·표준화 공식은 `domain/factor/_statistics.py` 가 소유한다.** `_evaluation.py` 가 같은
   두 공식을 세 자리에 펼쳐 쓰고 있어서, 복사하면 `CrossSectionalOperator.RANK` 와
   `signal.normalization: rank` 가 갈릴 수 있었다. `cross_sectional_rank`·`cross_sectional_zscore`
   로 뽑고 `domain/factor/facade/cross_section.py` 로 공개해 `domain.portfolio` 가 읽는다
   (`DEPENDS_ON` 에 `domain.factor` 추가, `domain.factor` 의 `DEPENDS_ON` 은 비어 있어 순환 아님).
   동작 변경이 아니므로 별도 `refactor` 커밋이다.

5. **점수 비례 가중(`weighting: factor_score`) 규칙: 선정이 2종목 이상이면 선정 종목 강도 = 합성 점수 − 기준점, 기준점 = `min(선정 최저 점수 이하인 eligible 비선정 종목 중 최고 점수, 선정 최저 − (선정 최고 − 선정 최저) / (선정 수 − 1))`(컷 아래 종목이 없으면 뒤 항), 선정 1종목이거나 강도가 모두 0 이면 균등 배분, 숏은 합성 점수 부호를 뒤집어 같은 규칙.**
   1차(P2-1)·2차(R2-P204-001)·3차(R3-P204-001)·4차(R4-P204-001·002)·5차(R5-P204-001) 리뷰를 거쳐
   리드가 정한 요건 7개와 4·5차 결정을 만족하는 규칙이다. 1차의 `zscore` 조합 차단 error, 2차의 `min(0, eligible
   최저)` 바닥, 3차의 "컷 아래 최고만" 기준점은 모두 지웠다.
   - **상황**: `weighting: factor_score`, 한 프레임의 eligible 후보와 선정 결과가 정해진 뒤.
   - **인풋**: 선정 종목(롱 `long_ids`, 숏 `short_ids`)과 eligible 후보의 합성 점수. 합성 점수는
     방향을 이미 반영해 **클수록 매수 선호**다(spec D4).
   - **에러 위치(이전 규칙)**: `domain/portfolio/_compiler.py` 의 `_weight_scores`·
     `_margin_strengths`. 1.1·1차는 `abs(합성 점수)` 라 `direction: low`·공매도 쪽에서 순서가
     뒤집혔다. 2차는 eligible 최저 종목이 항상 강도 0 이라 1종목·동점 프레임이 비었다. 3차는
     기준점이 컷 아래 최고뿐이라, 원시값 3, 2, 1+2⁻⁵², 1 에서 3종목을 고르면 `c` 가
     `7.4e-17` 비중으로 tape 에 남았다.
   - **위험성(이전 규칙)**: valid 문서가 예외도 경고도 없이 뒤집힌 비중, 빈 프레임, dust target 을
     낸다(silent corrupt).
   - **이 규칙이 지키는 요건**: (1) 리드 4차 결정대로 "선정 종목은 산술적으로 0 이 아닐 뿐 아니라
     최소 평균 간격만큼의 강도를 가진다"로 해석한다. 기준점이 `선정 최저 − 평균 간격` 이하라서
     그렇다. 그래서 최고/최저 강도 비는 선정 수를 넘지 않고 dust 가 생기지 않는다. (2) 선정
     1종목이거나 전원 동점이고 아래 종목이 없으면 균등 배분한다. 전원 동점이고 아래 종목이 있으면
     강도가 모두 같아 역시 균등이다. (3) 두 항 모두 점수의 평행 이동·양의 배율에 공변이라 비중이
     불변이다. x 에 `low`, −x 에 `high` 를 준 두 문서는 `none`·`zscore` 에서 합성 점수가 같고
     `rank` 에서 상수 1 차이(`−r` 대 `1 − r`)여도 보유 종목·비중이 같다. (4) 숏은 부호를 뒤집어
     같은 규칙이다. (5) 선정 종목의 자기 점수가 오르면 자기 비중은 줄지 않는다(5차 리뷰
     R5-P204-001). 선정 최저와 동점인 비선정 종목도 기준점 후보에 넣어(`<=`) 동점이 풀리고 묶일
     때 기준점이 튀지 않게 했다. 평균 간격 하한이 있어 동점 후보가 들어와도 선정 종목의 강도는
     0 이 되지 않는다(간격이 0 이면 강도가 모두 0 이라 균등 배분).
   - **대안**:
     | 규칙 | 어기는 요건 | 버린 이유 |
     |---|---|---|
     | `abs(합성 점수)` 비례(1.1·1차) | 3, 4 | `direction: low`·공매도 쪽 순서 반전 |
     | 롱 바닥 `min(0, eligible 최저)`(2차) | 1, 2, `rank` 의 3 | eligible 최저 종목이 항상 0, 1종목·동점 프레임이 빈다 |
     | 기준점 = 컷 아래 최고, 없으면 평균 간격(3차) | 1 의 확정 해석 | 근접 동점 선정 종목이 dust 비중(`7.4e-17`)을 받는다 |
     | 4차 규칙에서 기준점 후보를 선정 최저보다 **엄격히** 낮은 종목으로 한정 | 5(단조성) | 선호 점수 1.0, 1.0, 1.02, −4 에서 2종목 선정 시 `a` 를 1.0 → 1.01 로 올리면 비중이 .499 → .333 으로 준다 |
     | 기준점 = 선정 최저 − 평균 간격만 | 없음 | 최고/최저 강도 비가 **항상** 선정 수라, 컷 아래 종목이 멀리 떨어져 있어도 그 정보를 버린다 |
     | 선정 순위 비례(N, N−1, …, 1) | 없음 | 점수 크기를 전혀 쓰지 않아 `weighting: rank` 와 같다 |
   - **채택 규칙에도 해당하는 기각 사유(4차 리뷰 R4-P204-003)**:
     - 기본값 `rank` 정규화에 동점이 없으면 선정 종목의 순위 간격이 균일해, 기준점이 늘
       `선정 최저 − 평균 간격` 이 되고 강도가 N : … : 1 이다. 즉 **`weighting: rank` 와 같은 비중**이다
       (100종목 상위 5 → 1:2:3:4:5). 순위 비례 대안을 버린 사유가 기본 설정에서는 채택 규칙에도
       그대로 해당한다. `factor_score` 가 `weighting: rank` 와 달라지는 것은 `none`·`zscore` 이거나
       `rank` 에 동점이 있을 때다.
     - 컷 아래 eligible 종목이 없으면(`top_count: N` + 선정 N 처럼 흔한 경우) 채택 규칙은 "선정 최저 −
       평균 간격만" 대안과 같다. 선정 2종목이면 점수와 무관하게 항상 2 : 1 이다. 그 대안을 버린
       사유("점수 크기를 버린다")가 이 경우에는 채택 규칙에도 해당한다.
     - 차이는 컷 아래 종목이 `선정 최저 − 평균 간격` 보다 더 아래 있을 때 그 거리를 쓴다는 점뿐이다.
   - **알려진 성질(컷 아래가 먼 경우)**: 컷 아래 종목이 선정 최저에서 멀면 그 점수가 기준점이
     되어 비중이 균등 쪽으로 평평해진다. 원시값 3, 2, 1, −100 에서 3종목 선정은 기준점 −100, 강도
     103 : 102 : 101, 비중 .337 · .333 · .330 이다. 평균 간격 하한은 기준점이 선정 최저에 **너무
     가까운** 쪽만 막는다. 그래서 컷 아래 종목이 `선정 최저 − 평균 간격` 과 선정 최저 사이에서
     움직이는 동안에는 비중이 흔들리지 않지만, 그보다 아래에서 움직이면 비중이 따라 움직인다.
     리드는 (a)안이 이 예의 흔들림도 줄인다고 봤으나 이 예의 비중은 3차 규칙과 같다. 테스트가 이
     값을 고정한다. 먼 쪽도 막으려면 기준점에 아래쪽 한계(예: `선정 최저 − k × 평균 간격`)를 더하는
     별도 결정이 필요하다.
   - **1.1 과 달라지는 곳(정확한 불변 조건, 4차 규칙으로 재실측)**: 선정·보유 종목은 1.1 과 같다(1.1
     도 선정 종목을 모두 보유했다). 비중은 롱이고 **기준점이 정확히 0** 일 때만 1.1 과 같다(예: 원시값
     1~N 등간격을 전부 선정). `none`·`high` 실측:
     | 입력 | 1.1 | 새 규칙 |
     |---|---|---|
     | 합성 점수 2.5, 1.0 전부 선정 | 5/7 · 2/7 | 2/3 · 1/3 |
     | 원시값 10, 20, 40 전부 선정 | .143 · .286 · .571 | .176 · .294 · .529 |
     | 원시값 1~5 상위 2 | .444 · .556 | 1/3 · 2/3 |
     | 원시값 −2, −1, 1, 2, 3 상위 2 | .4 · .6 | 1/3 · 2/3 |
     | `long_short` 2/2, 원시값 −3, −1, 1, 2, 5 의 숏 `a`·`b` | −.375 · −.125 | −1/3 · −1/6 |
     | 같은 입력의 롱 `d`·`e` | .143 · .357 | 1/6 · 1/3 |
     `direction: low` 와 공매도 쪽의 반전도 사라진다. 저장된 1.1 리비전은 `requires_upgrade` 로
     실행이 막혀 있어서 이 차이는 P2-09 업그레이드(`normalization: none` 명시) 뒤에 드러난다. 과거
     실행 결과 아티팩트는 바뀌지 않는다. WORKFLOW P2-09 acceptance 에 한 줄을 남겼다.
   - **테스트**: 1~4차 경계를 값으로 고정했다. 3차에 넣은 선정 5 보유 5, `low`, 숏, eligible 1종목,
     0 이하 1종목, 전원 동점, 전부 음수, 모집단 10·eligible 5, 거울 대칭 5경우에 4차 경계를 더했다.
     4차 경계는 근접 동점(3차 tip 에서 red), 불균등 간격 5·4·2·1, 컷 동점 5·4·4·1, 컷 아래가 먼
     3·2·1·−100, `rank` 무동점 = N…1 이다. 5차에 비교를 `<=` 로 바꾸며 컷 동점 5·4·4·1 기대값을
     2/3·1/3 으로 고치고, 리뷰어 반례와 자기 점수 단조성 퍼즈(고정 시드, 선정 2~3, 0.25 격자로 컷
     동점 유도, 150건)를 더했다. 5차 전 tip `34b20ef5` 에서 이 넷이 red 였다. `<` 로 되돌리면 컷
     동점·반례·퍼즈가, `if below:` 를 지우면 불균등·먼 경우 테스트가 red 다.

6. **(폐기) 강도 0 종목 제외.** 1차 리뷰 P2-1 에서 `rank` 모집단 최하위(백분위 0)가 `1e-12` 바닥값
   때문에 dust 비중으로 tape 에 남던 문제를 "강도 0 이면 `SCORE_THRESHOLD` 로 뺀다"로 막았다.
   사용자가 설정한 규칙이 아니라 dust 를 없애려던 장치였다. 3차에서 지웠을 때는 "dust 도 제외도
   생기지 않는다"고 적었지만, 3차 규칙에서는 근접 동점 종목이 dust 비중을 받았다(4차 리뷰
   R4-P204-001). 4차 규칙은 선정 종목의 강도가 최소 평균 간격이라 dust 가 생기지 않고, 강도가 모두
   0 인 프레임은 균등 배분하므로 제외도 없다. `signal.score_threshold` 는 그대로 사용자 필터다.

7. **정규화 값 조회는 catch-all default 대신 엄격 조회다**(1차 리뷰 P2-2 반영).
   `normalized_signals.get(key, value.value)` 는 조회가 빗나가면 **원시값**으로 떨어져서,
   정규화된 값과 원시값이 같은 가중 합에 섞인다 — 이 PR 이 없애려던 단위 지배가 진단도 예외도
   없이 되살아난다. 결정 6(분기 exhaustive)과 같은 실패 모양이라 같은 정책을 쓴다:
   `_signal_value` 가 `none` 분기에서만 원시값을 쓰고, 그 밖에는 `[...]` 로 조회해 `KeyError`
   를 진단 컨텍스트(`as_of`·`security_id`·`factor_id`·`normalization`·모집단 크기)가 붙은
   `ValueError` 로 올린다. 지금은 도달 불가이지만 P2-05 가 모집단 전제를 건드린다. 정규화 맵에서
   한 종목을 빼 이 분기를 강제로 타는 테스트가 동작을 고정한다(2차 리뷰 R2-P204-002).

WORKFLOW acceptance 중 **하지 않은 것 2건**:

- **백테스트 골든은 바뀌지 않았다.** `rank` 기본값으로 갈아탄 뒤에도 기존 골든
  (`test_backtest_http_api.py` 의 결과·지표 parity, `test_truthful_pipeline.py` 의 실행 경로)이
  그대로 통과한다. 그 문서들이 팩터 하나짜리라 순위 변환이 순서를 보존해 선정·비중이 같기 때문이다.
  `composite_score` 값 자체는 바뀌지만 골든이 그 값을 고정하지 않는다. WORKFLOW 는 "결과가 바뀌면
  갱신"이라 조건이 성립하지 않았고, 대신 "`none` 이 1.1 과 같다"를 도메인·통합 두 층에서 테스트로
  고정했다.

- **은퇴한 1.1 리비전의 설명 문장이 그 리비전의 의미와 다르다**(1차 리뷰 P3, 기록만).
  저장된 1.1 row 를 `adapters/outbound/strategy_sqlite/_record_codec.py` 가 `normalization`
  없이 hydrate 하므로 기본값 `RANK` 가 붙고, `explain_strategy` 가 "횡단면 순위로 맞춘 뒤 …
  결합" 이라고 말한다. 그 리비전의 실제 의미는 원시값 가중합이다. 실행은 `requires_upgrade`
  가 막아(`backtest_run/_service.py`) 수치 손실은 없고, 문장을 바로잡는 owner 는 업그레이더가
  `normalization: none` 을 명시하는 **P2-09** 다. 표시 문구만 남는 노출이라 이 PR 에서는
  고치지 않고 기록한다.

- **(정보) 이 PR 은 "P2-09 전까지 실 SQLite 에 1.2 revision 을 저장하지 않는다"(WORKFLOW 1절)에
  의존한다.** 이 PR 이후 `normalization` 없이 저장된 1.2 `spec_json` 은 `_record_codec` 의
  canonical 재직렬화 비교에서 탈락한다. 그 규칙이 이미 막고 있어 결함은 아니다.

- **`quality_momentum.yaml` 에 `signal:` 블록을 넣지 않았다.** 이 verbose fixture 는 frontend 27개
  테스트가 포인터·줄 위치로 읽고 있어(`readBackendFixture`), 섹션 하나를 끼우면 그 테스트들이
  깨진다. 소비자 배선은 P3-01 범위라 fixture 를 그대로 두고, 명시 값 커버리지는
  `quality_momentum.legacy.json` 과 hydrate 테스트가 맡는다. P3-01 이 frontend 투영을 만질 때
  verbose fixture 에 같이 넣는 것을 권한다.

P2-03 결정 9건(WORKFLOW 결정 항목 4 + 새 결정 5):

1. **`dataclass_json_schema`의 owner는 `domain/strategy/facade/schema.py`에 그대로 둔다.**
   `domain.backtest → domain.strategy` 화살표가 "표기법을 빌린다" 사유로 남지만 규칙 위반이
   아니다(`DEPENDS_ON` 선언됨, facade가 책임 이름을 가짐). 옮기려면 두 노드 모두가 읽는 새 노드를
   만들어야 하는데 그 노드의 유일한 내용이 이 빌더 하나라 owner 없는 공용 모듈이 된다.
2. **실행 설정 수치 범위 행의 owner를 `domain/backtest/_models.py`로 옮겼다.** 1.2 문서에
   `execution` 섹션이 없어 `/execution/*` 포인터가 가리킬 곳을 잃었기 때문이다. 포인터는 실행 설정
   문서 기준(`/fee_bps` …)이고 진단 코드는 `strategy.*` validator 레지스트리 밖의
   `run_environment.*`다 — 실행 설정 값은 문서 검증이 아니라 요청 검증에서 걸린다.
3. **실행 설정 스키마 엔드포인트는 `application/strategy_authoring`에 그대로 둔다.** 옮기는 것은
   P3-02와 함께 한다(WORKFLOW P2-03 결정 항목의 선택지 그대로).
4. **`preflight`의 `environment`는 시그니처에 남기고 읽는다.** 참여율(엔진 능력)과 결측 정책(플랜)이
   실행 설정 값이라 문서만으로는 같은 판정을 낼 수 없다. 해소 단계가 사라져(문서 브리지 없음)
   `start()`와 `_run`이 서로 다른 값을 넘길 여지 자체가 없으므로 P2-01 P0의 재발 경로가 닫힌다.
5. **`environment` 미지정은 필수 필드 오류가 아니라 코드화된 진단이다.** 요청 모델의 타입은
   `RunEnvironment | None`으로 두고 `require_environment`가 `run_environment.required`(run 경로는
   422 `backtest.run.environment_required`)로 거절한다. 타입을 필수로 바꾸면 pydantic의 영문
   "Field required"가 나가 프론트가 번역할 코드를 잃는다.

6. **팩터 sandbox 의 `missing` 기본값을 실행 설정과 같은 상수로 묶었다.** P2-02 후속이 넣은
   "요청이 `missing` 을 생략하면 문서의 `graph.missing_policy` 로 떨어진다"는 1.2 에서 떨어질
   문서 값이 없어 성립하지 않는다. 대신 `domain/backtest` 가 `DEFAULT_MISSING_POLICY`(= 1.1
   까지의 기본값 `drop`)를 소유하고 `RunEnvironment.missing` 과 sandbox 요청이 같은 상수를
   읽는다 — 두 기본값이 갈리면 편집 화면의 실행 플랜 패널이 실제 실행과 다른 `plan_hash` 를
   보인다. P3-01 이 실행 설정의 `missing` 을 sandbox 요청에 실으면 `None` 경로가 사라진다.
   `resolve_graph_missing_policy`·`_missing_from_legacy_graphs`·`LegacyMissingPolicyConflictError`
   는 입력이 사라져 함께 삭제했다.
7. **`domain/backtest/_bridge.py` 를 `_requirement.py` 로 개명했다.** 브리지가 사라진 뒤에도
   파일 이름이 "브리지"로 남으면 다음 사람이 없는 폴백을 찾는다.
8. **`x-deprecated` 표기를 은퇴시켰다**(WORKFLOW P2-03 잔재 삭제 항목). `DEPRECATED_FIELD`
   마커, 런타임 스키마의 `x-deprecated` 발행, `FieldContract.deprecated` 를 지웠다 — 유일한
   사용자였던 `graph.missing_policy` 가 사라졌다. 다시 필요해지면 그때 되살린다.
9. **아키텍처 가드를 `*.graph.missing_policy` 모양으로 좁혔다**(P2-02 2차 리뷰 P3). 이름만 보고
   전부 잡으면 `FactorExecutionPlan.missing_policy`(실행 설정에서 인자로 받아 `plan_hash` 에
   남는 정당한 필드)까지 걸려, 가드가 옳은 코드를 막고 결국 지워진다.

WORKFLOW 원문과 다르게 간 곳 2건:

1. **`template()`은 시작 팩터 하나를 유지한다.** WORKFLOW는 "P4-04 시작 문서(`schema_version`·
   `title`만)와 같아야 한다"고 적지만, 이 템플릿은 `create()`로 바로 들어가는 서버 초안이라 팩터를
   비우면 `strategy.factor.required`로 모든 새 전략 저장이 막힌다. 편집 화면이 여는 빈 시작 문서는
   저장 전 원문이라 규칙이 다르고, 그쪽은 `NEW_STRATEGY_STARTER`가 이미 두 키만 갖는다.
2. **422 코드 철자는 `backtest.run.environment_required`다.** WORKFLOW는
   `backtest_run.environment_required`로 적지만 기존 시작 요청 422 어휘가 전부 `backtest.*`이고
   프론트가 `backtest.error.<code>`로 번역한다.

작업 파일(신규 1 + 수정 28):

- 신규 backend tests — `tests/domain/test_factor_missing_policy.py`
- domain/factor — `_nodes.py`(`missing_policy`에 `x-deprecated` 마커), `_planning.py`·`_evaluation.py`·
  `_trace.py`(`missing` 키워드 인자), `_registry.py`(`FactorDefinition.missing_policy` 제거)
- domain/strategy — `_schema.py`(`x-deprecated` 마커와 `FieldContract.deprecated`)
- domain/backtest — `_bridge.py`(`_missing_from_legacy_factors`,
  `LegacyMissingPolicyConflictError`), `facade/environment.py`
- application — `portfolio_design/_service.py`(`_prepare`가 검증 뒤 실행 설정을 해소해
  `_PreparedPipeline.environment`로 돌려줌)·`_trace_service.py`, `backtest_run/_service.py`,
  `factor_research/_models.py`·`_service.py`(연구 요청이 `missing`을 직접 가짐)
- 계약 산출물 — `backend/openapi.json`, `tests/fixtures/strategy_documents/runtime-schema.json`,
  `frontend/src/shared/api/generated/types.gen.ts`
- frontend 소비자 — `contract-inspector.tsx`(팩터 카탈로그 결측 정책 행 제거), `messages.ts`,
  테스트 fixture 3개
- 기존 테스트 갱신 — `tests/domain/test_factor_research.py`·`test_factor_trace.py`·
  `test_run_environment.py`·`test_strategy_schema.py`,
  `tests/architecture/test_run_environment_ownership.py`,
  `tests/application/test_run_environment_wiring.py`,
  `tests/integration/test_truthful_pipeline.py`

P2-02 결정 3건(WORKFLOW 원문과 다르게 간 곳):

1. **`FactorGraph.missing_policy`를 물리 삭제하지 않고 `x-deprecated`로 표시했다.** WORKFLOW
   P2-02는 "`FactorGraph`에서 `missing_policy` 제거"라고 적지만, `CURRENT_SCHEMA_VERSION`이 아직
   `"1.1"`이라 필드를 지우면 1.1 문서가 `structure.unknown_field`로 깨지고 1.0 → 1.1 업그레이드
   출력(`quality_momentum.v1_1.commented.yaml`, WORKFLOW가 "건드리지 않는다"고 못 박은 fixture)이
   곧바로 compile 실패가 된다(`tests/application/test_strategy_authoring_upgrade.py`가
   `not upgraded.compiled.diagnostics`를 단언). spec D3 S3의 "unknown key"는 1.2 문법 변경표의
   항목이므로 물리 제거는 `CURRENT_SCHEMA_VERSION` 1.2와 업그레이더가 함께 오는 P2-03·P2-09에서
   한다. 이 PR이 고정하는 invariant(소비자 0건·`plan_hash` 분기)는 그대로 달성된다.
2. **팩터별 값 충돌은 warning이 아니라 거부다.** spec D7의 업그레이더 규칙은 "첫 팩터 값 + warning"
   이지만, 런타임 브리지가 같은 규칙을 쓰면 팩터 일부가 조용히 다른 결측 처리로 계산된다. 명시
   `environment`를 주면 통과하는 경로가 있으므로 거부해도 막다른 길이 아니다. P2-09 업그레이더는
   spec대로 warning을 낸다.
3. **`FactorGraphRequest`·`FactorPreviewRequest`가 `missing`을 갖는다.** 팩터 연구는 전략 실행
   설정 밖에서 도는 sandbox라 `RunEnvironment`가 없다. WORKFLOW의 소비자 목록에는 없지만
   `compile_factor_plan` 호출자라 어디선가는 정책을 말해야 한다.

---

## P0 — 기획 패키지와 계약 문서

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P0-01` | 기획 패키지·설계 spec·ADR/로드맵/SoT 개정 | 없음 | `MERGED` | [#167](https://github.com/Nochiski/Quant_study/pull/167) · `review_lang2_p0_01` 5차 APPROVE(1~4차 REQUEST_CHANGES 전부 해소) · main 머지 `b438e58d`(#167, 2026-09-27) |

Phase exit:

- [x] `update-plan-progress.ps1 -Check` 통과, 상위 문서 링크 확인. (#167 머지, 2026-09-27)

## P1 — 화면 안에서 끝나는 마찰 제거

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P1-01` | 문제 목록·검증 배지를 탭과 무관하게 렌더 | P0-01 | `MERGED` | [#168](https://github.com/Nochiski/Quant_study/pull/168) · `review_lang2_p1_01` 3차 APPROVE(1·2·3차 전부 APPROVE, blocking 0. 1차 P2 2·P3 6, 2차 새 P2 1·P3 5, 3차 P3 3 전부 반영 — 3차분 `ea7e2fa4`) · 게이트: typecheck·lint·Vitest 658·build·e2e 19/19 · main 머지 `3d6f2b42`(#168, 2026-09-27) |
| [x] | `P1-02` | 되돌리기·다시 실행 버튼, 전역 단축키 | P1-01 | `MERGED` | [#173](https://github.com/Nochiski/Quant_study/pull/173) · `review_lang2_p1_02` 4차 APPROVE(`db3bc079`까지, 1차·3차 REQUEST_CHANGES 반영, 2차 APPROVE·새 P2 2) + 5차 APPROVE(`ac3a3d0e` — SoT 표식 해소·`conflict_markers.py`·테스트 7건·CI backend step, blocking 0·P3 4, 코드 개선은 BACKLOG-008) · main 머지 `bee2a4fd`(#173, 2026-09-27) |
| [x] | `P1-03` | 연산자 카탈로그(backend)·노드/필드 한글 이름·설명 | P1-02 | `MERGED` | [#177](https://github.com/Nochiski/Quant_study/pull/177) · `review_lang2_p1_03` 2차 APPROVE(1차 REQUEST_CHANGES 반영, 돌연변이 7건 실패 확인), 2차 P3 4건 후속 · main 머지 `9cef7f3d`(#177, 2026-09-27) |
| [x] | `P1-04` | 연산자 먼저 고르기(kind 자동), 조용한 실패 피드백, 오류 본문 인라인 | P1-03 | `MERGED` | [#181](https://github.com/Nochiski/Quant_study/pull/181) · `review_lang2_p1_04` 4차 APPROVE (1·2·3차 REQUEST_CHANGES 차단 2·1·1, P3 7·3·2 전부 반영) · main 머지 `3c1f1ab7`(#181, 2026-09-27) |
| [x] | `P1-05` | 구조 오류 한글화, 진단 코드 네임스페이스, 순환·중복 진단에 node_id | P1-04 | `MERGED` | [#188](https://github.com/Nochiski/Quant_study/pull/188) · `review_lang2_p1_05` 3차 APPROVE(1차 REQUEST_CHANGES P1 1·P2 2·P3 13 → 2차 REQUEST_CHANGES 새 P1 1(POSIX 잠금 덮어쓰기)·P3 8, 수정 `5e8e0af0` → 3차 APPROVE P2 1·P3 2, 추가분 `a55e7a67` 확인 P3 3, 3차 반영 `3755b186`·`a263276b`. SHA는 rebase 전 값, 현 tip `45f1c4a3`) · 게이트: pytest 1695·Vitest 733·e2e 25/25 · main 머지 `7d525949`(#188, 2026-09-27) |
| [ ] | `P1-06` | Phase 1 감사 후속(문서): PLAN 진행 기록 정정, SoT 하한·e2e 잠금 행, 이월 항목 담당 지정 | P1-05 | `IN_REVIEW` | [#191](https://github.com/Nochiski/Quant_study/pull/191) · `review_lang2_p1_06` 2차 APPROVE(`909ae273`, 1차 REQUEST_CHANGES P2 2·P3 5 반영). 이후 추가분 — main 병합 cascade, US-DM-06 스토리 e2e, 병합 리뷰 P3 문서 반영 — 은 리뷰 대기 |

Phase exit:

- [x] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과. (Phase 1 감사 4절 (a) PASS — CI #188 `browser-e2e` 25/25)
- [x] 노드 property·kind·연산자 설명 커버리지 100%. (감사 4절 (b) PASS — 발행은 생성기 구조로, 소비는 `screen-vocabulary.test.ts`로 고정)
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0. (감사 2026-09-21 BLOCKING 2 — DEFECT-P1X-001은 `ac3a3d0e`+cascade rebase, DEFECT-P1X-002는 P1-06이 해소. `ac3a3d0e`는 5차 APPROVE, P0-01~P1-05는 main 머지. P1-06 머지 뒤 체크)

## P2 — backend schema 1.2

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | `RunEnvironment` 모델·브리지(`domain/backtest`), 실행 요청 optional `environment`, manifest·캐시 키, `/run-environments/schema` | P0-01 | `IN_REVIEW` | [#172](https://github.com/Nochiski/Quant_study/pull/172) · 2차 APPROVE 대상 `1e0b095` + P3 후속 커밋 1개 · 구현자 `impl-lang2-p2-01`, 워크트리 `wt-lang2-p2-01`, 브랜치 `feat/lang2-p2-01-run-environment` · `review_lang2_p2_01` 1차 REQUEST_CHANGES(P0 1·P1 1·P2 4·P3 3) → 반영, 2차 APPROVE(P3 6 → 코드 2 반영, 문서 3 이관, 본문 1 리드). 커밋 7개(backend 3 + 생성 SDK 1 + 리뷰 반영 3). 31파일은 12절 상한(8파일)을 넘어 논리 단위로 쪼갰다 — 모델·브리지 / 세 요청 배선 / 스키마 엔드포인트, 그리고 CI `api:generate` 게이트가 요구하는 생성 SDK. 게이트: pytest 1494·ruff·pyright(duckdb 4건 기존) · frontend typecheck·lint·Vitest 639·build |
| [ ] | `P2-02` | `graph.missing_policy` 제거 → `environment.missing`(plan 인자, `plan_hash` 유지) | P2-01 | `APPROVED` | [#176](https://github.com/Nochiski/Quant_study/pull/176) · 구현자 `impl-lang2-p2-02`, 워크트리 `wt-lang2-p2-02`, 브랜치 `feat/lang2-p2-02-missing-policy` · `review_lang2_p2_02` 1차 REQUEST_CHANGES(P1 1·P2 3·P3 3) → 반영, 2차 APPROVE(P3 4건 후속 커밋). 커밋 12개(1차 5 + 1차 리뷰 반영 6 + 2차 리뷰 반영 1, history 재작성 없음). 게이트: pytest·ruff·pyright 0 · frontend api:generate diff 0·typecheck·lint·Vitest 639·build. `database/tests` 는 base `fff33fd` 와 같은 41 failed/1268 passed/33 errors(기존 실패, 이 PR 무관) |
| [ ] | `P2-03` | `data`·`execution` 제거, `CURRENT_SCHEMA_VERSION` 1.2, 필수 키 2개, fixture·hash golden | P2-02 | `APPROVED` | [#183](https://github.com/Nochiski/Quant_study/pull/183) · 워크트리 `wt-lang2-p2-03`, 브랜치 `feat/lang2-p2-03-schema-1-2` · `review_lang2_p2_03` 1차 REQUEST_CHANGES(P2 2·P3 8) → 반영, 2차 **APPROVE**(돌연변이 재실행 2 failed 확인, P3-07 이탈 타당). P2 둘 다 `_record_codec.py`의 은퇴 row 읽기 5줄이다: 1.1 row 테스트 0건(그 가지를 `raise`로 바꿔도 초록), 미지 `schema_version`이 fail-closed에서 silent 현재 버전 해석으로 바뀜. 게이트는 아래 Full gate |
| [ ] | `P2-04` | `signal.normalization`과 결합 전 정규화 | P2-03 | `APPROVED` | [#184](https://github.com/Nochiski/Quant_study/pull/184) · 워크트리 `wt-lang2-p2-04`, 브랜치 `feat/lang2-p2-04-normalization` · 1차 → `28003cf1` · 2차 → `2a10798d` · 3차 → `70cfdffa` · 4차 → `22636fc3` · 5차 **APPROVE**(P3 R5-P204-001 단조성: 기준점 후보 비교를 `<=` 로, 리드 지시로 즉시 반영). 게이트는 push tip 에서 재실행(PR 댓글) |
| [ ] | `P2-05` | 횡단면 eligibility(전용 `EligibilityOperator`, exhaustive `_compare`, 2-pass) | P2-04 | `APPROVED` | [#187](https://github.com/Nochiski/Quant_study/pull/187) · 워크트리 `wt-lang2-p2-05`, 브랜치 `feat/lang2-p2-05-eligibility` · 1차 REQUEST_CHANGES(P2 2·P3 2) → `641c6f3b`·`efd6a768` · 2~5차 **APPROVE**. 3차 재배치 때 첫 커밋 `a35c1e40` 에 P2-04 새 테스트 한 줄의 enum 개명을 넣었고, P2-04 3차 재현 테스트 `8587d413` 를 더했다. P2-04 5차 반영 tip `35089901` 위로 rebase(코드 변경분 동일). 게이트는 push tip 에서 재실행(PR 댓글) |
| [ ] | `P2-06` | `risk.risk_factor_id`(합성 제외·원시값 역가중), `saved_*` 제거 | P2-05, P1-03 | `WAITING` | — |
| [ ] | `P2-07` | compile 단일 게이트: `field_missing`, boolean 승격, 단위 경고, 연산자 unsupported | P2-06, P1-03 | `WAITING` | — |
| [ ] | `P2-08` | duckdb `GROUP_SERIES` 스파이크, `ideas/*.yaml` 5개(레시피 산출 형태) | P2-07 | `WAITING` | — |
| [ ] | `P2-09` | 1.1 → 1.2 업그레이더(버전 디스패치), upgrade 응답 `environment`, 동결 읽기, OpenAPI | P2-08 | `WAITING` | — |

Phase exit:

- [ ] 1.2 fixture 같은 hash, 1.1 fixture 전부 업그레이드 통과.
- [ ] compile 통과 문서가 preview에서 422 없음(property).
- [ ] `plan_hash`가 결측 정책으로 계속 갈린다(P2-02 회귀).
- [ ] `ideas/*.yaml` 5개 backend 통과.
- [ ] SoT·책임분리 점검 blocking 0, SoT "실행 설정" 행 채움·업그레이드 행 예약 해제.

## P3 — frontend 1.2 적응

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P3-01` | SDK 1.2, pointer 헬퍼·outline·snippet·Form projection·plan·debugger 적응, 새 필드 i18n | P2-09, P1-06 | `WAITING` | — |
| [ ] | `P3-02` | 실행 설정 패널 확장, 1.1 업그레이드 배너(`environment` prefill), 실행 설정 띠 | P3-01 | `WAITING` | — |
| [ ] | `P3-03` | e2e fixture 1.2, 매뉴얼·README·FACTORS 1.2, CI green | P3-02 | `WAITING` | — |

Phase exit:

- [ ] CI 전체 green.
- [ ] 같은 전략·다른 기간 → 같은 spec_hash e2e.
- [ ] SoT·책임분리 점검 blocking 0.

## P4 — 그래프 1수준: 파이프라인

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P4-01` | `pipeline-projection.ts` (4단계 모델, `x-stage`, 팩터 요약 문장) | P3-03 | `WAITING` | — |
| [ ] | `P4-02` | 단계 카드 UI(거른다·합쳐서 고른다·비중을 준다), 실행 설정 띠 | P4-01 | `WAITING` | — |
| [ ] | `P4-03` | 팩터 카드, 빈 팩터 추가, 기준일 미리보기 패널 | P4-02 | `WAITING` | — |
| [ ] | `P4-04` | 탭을 그래프·YAML 둘로, 기본 탭 그래프, Form·JSON 은퇴, 빈 화면 e2e, 식별자 0개 단언 | P4-03 | `WAITING` | — |

Phase exit:

- [ ] 빈 화면 e2e green, 식별자 0개 단언 green.
- [ ] SoT·책임분리 점검 blocking 0.

## P5 — 그래프 2수준: 레시피

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | `recipe-projection.ts`(체인 판정)·`recipe-transactions.ts`(추가·삭제·이동·파라미터·재배선), property test | P4-04 | `WAITING` | — |
| [ ] | `P5-02` | 팔레트(연산자 카탈로그), 단계 카드 UI, 설명, 인라인 진단, 식별자 접힘 영역 | P5-01 | `WAITING` | — |
| [ ] | `P5-03` | 팩터 결과 미리보기·결측 표시, 아이디어 5개 e2e, 매뉴얼 그래프 절 | P5-02 | `WAITING` | — |

Phase exit:

- [ ] 아이디어 5개 e2e green (완료 정의 1·2).
- [ ] SoT·책임분리 점검 blocking 0.

## P6 — 그래프 3수준: 고급 노드 캔버스

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P6-01` | 그래프 라이브러리 ADR·스파이크 | P5-03 | `WAITING` | — |
| [ ] | `P6-02` | 노드 캔버스: 드래그 배선, 좌표 local state, 자동 정렬, 키보드 | P6-01 | `WAITING` | — |
| [ ] | `P6-03` | 노드 위 진단, 옛 목록형 편집기 제거, 마감 문서(SoT·로드맵 M8·매뉴얼) | P6-02 | `WAITING` | — |

Phase exit:

- [ ] 드래그 배선 → YAML 반영 → 되돌리기 e2e.
- [ ] SoT·책임분리 점검 blocking 0. 완료 정의 6항 전부 기록.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|
| `P0-01` | `review_lang2_p0_01` | 1 | `REQUEST_CHANGES` | P1 6 · P2 7 · P3 5. 전부 문서 결정과 실제 코드의 불일치. 반영: 필수 키 2개, 업그레이더 버전 디스패치, 브리지를 `domain/backtest`로, `plan_hash`에 결측 정책 유지, 전용 `EligibilityOperator`·2-pass, 다중 입력 잎 노드 규칙, `risk_factor_id` 합성 제외, 연산자 23=(kind, operator), `/run-environments/schema`, SoT 업그레이드 행·`paths`·캐시 키·`x-stage`, P2 5→9 분할 |
| `P0-01` | `review_lang2_p0_01` | 2 | `REQUEST_CHANGES` | P1 2 · P2 3 · P3 4. 1차 18건은 전부 종결 확인. 새 회귀: P2-01 enum 이동 + re-export가 `domain.strategy ↔ domain.backtest` 순환, 합성 제외 후 팩터 0개면 `security_id` 사전순 선정. 반영: enum 이동을 P2-03으로(re-export 없음), `strategy.signal.no_alpha_factor` error, 제외를 `weighting: risk`로 한정 + `FIELD_APPLICABILITY` 행, P2-06 테스트 기준선 정정, `ExclusionReason`·trace 갱신, 아이디어 5 fixture 두 벌 |
| `P0-01` | `review_lang2_p0_01` | 3 | `REQUEST_CHANGES` | P1 1 · 비차단 3. 2차 9건 종결 확인. P2-03 enum 소비자 목록에 재수출 지점(`specification.py`)이 들어가 순환이 되살아난 것을 고쳤다(삭제 대상, 실제 리다이렉트는 `engine_portfolio` 하나). `/risk/risk_factor_id` 행은 `owned_by_error` 없이 적용 조건만, 배타는 별개 validator error. P2-04·P2-05·P2-06에 OpenAPI 재생성 항목. P2-03에 `template()` 갱신과 12절 재점검 문장 |
| `P0-01` | `review_lang2_p0_01` | 4 | `REQUEST_CHANGES` | P1 1 · 비차단 3. 3차 4건 종결 확인. P2-06·P2-07의 P1-03(연산자 카탈로그) 교차 의존이 미선언 → WORKFLOW 1절 교차 제약·Dependency 열에 P1-03. OpenAPI 재생성 사유에서 진단 코드 문자열 제외, active PR 문장 정정, P2-03 분할 시 PLAN 절차 참조 |
| `P0-01` | `review_lang2_p0_01` | 5 | `APPROVE` | 4차 4건 종결. 잔여 문구 2건(active PR 문장을 13.7절 인용으로, 집계 도구 설명 방향) 반영 |
| `P1-01` | `review_lang2_p1_01` | 1차 | `APPROVE` | blocking 0 · P2 2 · P3 6. **P2**: 문제 목록이 편집 패널 바닥에 붙어 편집기와 사이에 약 137px 빈 영역이 생김, 목적지가 `current-view`일 때 화면상 아무 일도 없을 수 있음(Form·Graph에 reveal 훅 없음, runtime schema 도착 전 창). **P3**: route 테스트가 outline reveal과 진단 reveal을 구분 못 함, Graph 유지 테스트가 URL만 단언, JSON·Diff wire 테스트 없음, 배지 간격, PLAN 갱신 절차 누락, 12절 크기 초과 사유. 관찰: reveal 순서가 훅 호출 순서에만 고정 — P1-02가 같은 page에 훅을 끼우면 조용한 회귀(교차 PR 경고, BACKLOG-006) |
| `P1-01` | `review_lang2_p1_01` | 2차 | `APPROVE` | blocking 0 · 새 P2 1 · P3 5. 1차 P2-1과 P3 5건 해소, P2-2·JSON wire는 부분. **R2-1(P2)**: Graph 탭에서 중첩된 `useRevealSelection` 둘이 서로 다른 요소를 끌고 바깥(plan DAG) 것이 이긴다 → `querySelectorAll`의 마지막 매치로 수렴. P3: 같은 행 재클릭 reveal 없음, `form !== null`을 schema 도착 대리값으로 씀, `scrollIntoView` 수신 요소 미단언, 훅 위치·타입 매개변수 이름, "PNG 8장 제외" 표기 오류 |
| `P1-01` | `review_lang2_p1_01` | 3차 | `APPROVE` | blocking 0 · P3 3. 2차 R2-1·R2-2·R2-4·R2-5·R2-6 해소, R2-3 부분(선택사항). P3: `schemaLoaded` 단위 테스트가 `form: null`을 함께 넘겨 옛 조건과 구분되지 않음, 주석 오타 3곳, props 빈 줄 — 전부 `ea7e2fa4`로 반영(옛 조건으로 되돌리면 단언이 깨지는 것 확인). JSON 탭 wire 테스트는 끝내 없음(순수 판정 테스트가 덮어 수용) |
| `P1-02` | `review_lang2_p1_02` | 1차 | `REQUEST_CHANGES` | P1 1 · P2 1 · P3 7. **P1**: 같은 문서 안의 전체 교체(초안 복구 `use-autosave.ts`, 서버 초안 적용 `use-server-draft.ts`)가 `SourceEditor`의 같은-epoch 분기에서 격리 없는 `setText`로 가, CodeMirror `newGroupDelay`(500ms) 안에 친 글자와 한 undo 단계로 합쳐졌다 — 되돌리기 한 번에 복구한 초안이 통째로 사라진다. → 같은-epoch 분기를 `replaceRange(0, length)`로 통일하고 호출자가 없어진 `setText`를 핸들에서 제거, SoT 행 문구 정정, 회귀 테스트 추가. **P3**: 480px 이하 탭·버튼 겹침, 탭 밑줄이 버튼 아래에서 끊김, `aria-disabled` 스타일이 공용 `:disabled`와 불일치, 날짜 입력 포커스에서 Ctrl+Z 무반응, 핸들 대역 들여쓰기, PR 본문 spec D8→D9, 12절 초과 사유에 줄 수 누락. 반영 수치: 코드 6건(P1 1 + P3 5 — 탭 겹침·밑줄·`aria-disabled`·대역 들여쓰기·날짜 입력 Ctrl+Z). PR 본문 2건(P3-4 spec 번호, P3-6 줄 수 근거)은 2차 시점에 각각 부분·미해소였고 4차 전에 리드가 본문을 정정했다 — 2차 행의 "5건 해소·1건 부분·1건 미해소"와 같은 사실을 세는 방식만 다르다. **오기**: "업그레이드 적용 undo 잠금 테스트 없음"(P2)은 사실과 다르다 — `upgrade-banner.test.tsx`에 undo 단언 2건(직후 타이핑 케이스 포함)이 이미 있다 |
| `P1-02` | `review_lang2_p1_02` | 2차 | `APPROVE`(코드) | 새 P2 2 · P3 3. 1차 blocking 해소를 확인. 구현이 권장(격리 주석 덧붙이기)보다 나은 방향 — 비격리 전체 교체 API 자체를 없앴고 새 회귀 테스트가 수정 전 실제로 실패함을 리뷰어가 재현. 1차 P2는 리뷰어가 철회(오기). P3 7건 중 코드 5건 해소, PR 본문 2건은 부분 1(P3-4)·미해소 1(P3-6). **새 P2(1)**: 탭 스트립의 `overflow-x: auto`가 `overflow-y`를 `auto`로 만들어 스크롤 컨테이너를 세우고, 탭 포커스 링(바깥 4px)이 위아래로 잘린다. 권장은 선언을 480px 미만으로 한정. **새 P2(2)**: PR 본문이 머지된 SoT 규칙과 반대되는 문장을 담았다(4차 전 리드 정정). **P3**: 12절 초과 사유가 여전히 파일 수만 다룸(1차 P3-6 재지적), 활성 버튼 `title`이 접근 가능한 이름을 중복 낭독, visually-hidden 레시피가 두 곳(= 감사 N6) |
| `P1-02` | `review_lang2_p1_02` | 3차 | `REQUEST_CHANGES` | 링 클립은 해소됐으나(도장 픽셀·기하 양쪽 확인) 해법의 부작용 1건. **P2**: 스크롤포트를 위아래 8px 넓힌 `padding-block`+음수 `margin-block`이, 툴바 액션 줄과 탭 줄 사이 2px 간격을 넘어 검증 버튼 하단 6px을 덮어 그 영역 클릭을 가로챈다(28px 버튼의 21%가 무표시 사각지대). → padding·음수 margin을 걷고 링을 `outline-offset: -2px`로 탭 안쪽에 그려 해소, 검증 버튼 하단 actionability e2e 단언 추가(직전 해법에서 실패 확인). 리뷰어는 2차의 "겹침은 480px 이하에서만" 판단을 실제 앱 계측으로 철회(1440px에서 이미 넘침) — `overflow-x` 유지 결정 확정 |
| `P1-02` | `review_lang2_p1_02` | 4차 | `APPROVE` | 코드 결함 0. 3차 P2(검증 버튼 하단 6px 클릭 가로채기)가 inset outline 교체로 해소되고, 회귀를 막는 e2e actionability 단언이 시각 project 4개에서 돈다. `overflow-x: auto` 유지, 탭 줄 한 줄·밑줄·`aria-disabled` 스타일·날짜 입력 단축키 모두 그대로. PR 본문 항목(spec D9, SoT와 어긋난 문장, 줄 수 근거, 테스트 수치)은 리드가 정정 |
| `P1-02` | `review_lang2_p1_06` | 5차(`ac3a3d0e`) | `APPROVE` | blocking 0 · P3 4. 4차 APPROVE(`db3bc079`) 뒤 Phase 1 감사 BLOCKING(DEFECT-P1X-001)을 닫으려고 올라간 코드 커밋이다. 범위: SoT 대장 표식 해소(4행은 P0-01 판, 편집 이력 1행은 `86ce099c` 판)와 N9 행, `conflict_markers.py`(139줄)·`test_conflict_markers.py`(7건), CI backend job step. **근거**: (1) 사고 당시 트리 `db3bc079`에서 SoT `:27/:33/:39` 3건을 찍고 exit 1, 수정 트리 `ac3a3d0e`는 exit 0. 임시 저장소의 실제 `git merge` 충돌도 기본 방식·diff3(zdiff3 포함) 모두 exit 1로 잡는다 — diff3의 `|||||||` 줄 자체는 목록에 없지만 나머지 세 표식으로 검출된다. (2) 오탐 규칙이 git의 `git diff --check`와 같다(정확히 7자 `=======` 단독 줄은 git도 `leftover conflict marker`, 8자 이상·들여쓴 줄·표 구분선·doctest는 둘 다 통과). 현재 추적 파일에 해당 줄 0개. (3) CI step이 실제로 돌았다 — run 36220139969(#191 push) backend job의 "No committed merge conflict markers" success, 약 1초. **P3**: ① 주석·커밋 문장 "setext 밑줄은 오검출하지 않는다"가 과하다(7자가 아닌 밑줄만 해당), ② 파일 열거를 `rglob`+디렉터리 이름 제외 대신 `git ls-files`로(추적 소스 안 `build/`·`dist/` 누락, 비추적 파일 오탐, 불필요한 순회), ③ `.lock` 확장자 전체를 건너뛰어 아무도 파싱하지 않는 reference `uv.lock`의 표식이 조용히 통과, 비 UTF-8 텍스트(cp949·UTF-16)도 건너뜀, ④ 테스트 수는 8건이 아니라 7건(PLAN 정정 완료). ①~③은 BACKLOG-008 |
| `P1-03` | `review_lang2_p1_03` | 1차 | `REQUEST_CHANGES` | 차단 2 · P2 2 · P3 7. **차단1**: `messages.ts`의 `en` 블록이 한글 계산식 6개를 담아 영어 화면에 한글이 떴다 — `satisfies Record<MessageKey, string>`는 키 존재만 보고 커버리지 테스트는 ko만 조회해서 타입·테스트·lint 어디도 잡지 않았다. **차단2**: 시간축 연산자 6개의 계산식·설명이 `lag`를 빠뜨려 엔진의 창(`x[t-lag-window+1 … t-lag]`, `_evaluation.py:388-407`)과 어긋났다 — 12-1 모멘텀을 화면대로 만들면 11-0이 되는데 백테스트는 통과한다. **P2**: `output_type_rule` 대조 테스트가 입력이 항상 숫자 시계열이라 세 규칙이 한 값으로 접혀 공회전(mutation 2건 미검출), 선언 순서 테스트가 레지스트리에서 파생한 값끼리 비교하는 동어반복. 전부 반영 |
| `P1-03` | `review_lang2_p1_03` | 2차 | `APPROVE` | 차단 0. 1차 findings 전부 해소 확인, 돌연변이 7건이 모두 실패하는 것을 실증. 새 P3 4건(en `momentum`·`delta` 문장 자족성, ko 산문의 식별자 호칭, e2e의 산문 고정, WORKFLOW 줄바꿈)은 후속 커밋에서 반영. 스코프 밖 관찰(`_registry.py` 12-1 모멘텀 시드 `history=252` ↔ 그래프 최소 이력 273)은 BACKLOG-001로 기록 |
| `P1-04` | `review_lang2_p1_04` | 1차 | `REQUEST_CHANGES` | 차단 2 · P3 7. **차단1**: 팔레트로 만든 `기간 집계` 노드가 `window: 0`이라 곧바로 거부됐다 — runtime schema가 하한을 발행하지 않아 화면이 0을 채웠다. 하한을 노드 dataclass 옆에 한 번 선언하고(`_nodes.minimum`) 검증기·스키마가 함께 읽게 고쳤다. **차단2**: Graph 탭 인라인 본문 테스트가 backend가 내지 않는 pointer로만 단언해, 실제 노드 객체 pointer에서는 본문이 어디에도 안 붙는 것을 못 잡았다. P3: `availability` 판정 반전, 팔레트 계산식 접근성, 진단 본문 `role="alert"` 제거, `referenceLabel` fallback, 공개 API 8→1, `filterPalette` 참조 동일성. Form 목록 pointer 표기는 의도적 제외로 근거 명시 |
| `P1-04` | `review_lang2_p1_04` | 2차 | `REQUEST_CHANGES` | 차단 1 · 잔여 3. 1차 차단 2건은 실측으로 해소 확인(카탈로그 23개 씨앗 전수, 게이트 민감도 probe 2건). **차단**: 노드 카드에 진단 본문 마크업만 더하고 CSS가 따라오지 않아 긴 한글 문장이 버튼 옆 같은 줄로 갔다 — e2e boundingBox로 재현하고(`flex-wrap` 없음) 카드 아래 줄 전체 폭으로 고쳤다. 잔여: 같은 문장 3중 렌더·중복 DOM id → 선택 노드 패널 사본 제거·`useId`; `부호 뒤집기`에 쓰이지 않는 `periods: 1` → 씨앗을 `addNode`로 옮겨 카탈로그 `params`에만; TS·Python 씨앗 규칙 2중 구현 → `parameter-seeds.json` golden이 양쪽을 묶음 |
| `P1-04` | `review_lang2_p1_04` | 3차 | `REQUEST_CHANGES` | 차단 1 · P3 2. 2차 차단·잔여 3건 해소를 리뷰어가 실측 확인(CSS 되돌리면 e2e 두 단언이 숫자로 실패, golden 돌연변이 2건이 양쪽을 동시에 깸, 팔레트 29개 씨앗 전수). **차단**: 본문이 자기 행과 8px·다음 노드 행과 4px라 근접성이 뒤집혀, 마지막이 아닌 노드의 오류가 아래 노드 것으로 읽혔다(진단 문장에 node_id 없음). 카드 사이 간격을 12px로 넓히고 카드 안 행 간격을 4px로 좁혔다. e2e에 오류 노드 뒤 노드를 하나 더 두고 거리 비교를 단언 — 되돌려 9.5 > 4로 실패 확인. P3: `ChosenOperator` 공개 API export, 기준선이 backend 소유 문장을 픽셀로 고정하던 것을 `mask`로 분리 |
| `P1-04` | `review_lang2_p1_04` | 4차 | `APPROVE` | 새 결함 0. 3차 차단(본문 근접성)과 P3 2건이 해소된 것을 확인했다. 관측 기록: `document-routes.test.tsx`의 P6-03 키보드 테스트가 1회 flake(재실행 통과) — P1-04 변경과 무관한 자리다(BACKLOG-007) |
| `P1-05` | `review_lang2_p1_05` | 1차 | `REQUEST_CHANGES` | P1 1 · P2 2 · P3 13. **DEFECT-P105-001(P1)**: `structure.legacy_shape` 배너가 누르면 반드시 422 — 진단의 업그레이드 판정과 endpoint 판정이 달랐다 → `is_upgradeable_document` 하나로 일원화. **DEFECT-P105-002(P2)**: e2e 머신 잠금이 두 경합에서 둘 다 주인이 됨(실측) → 원자적 rename 획득. **DEFECT-P105-003(P2)**: `PW_BACKEND_PORT`가 vitest·dev 설정까지 새어 단위 게이트를 깸 → 빌드 자식에만 넘김. P3 13건(순환 진단 SCC, IPv6 포트 확인, 문서·기록 정정 등) 반영. 수정 `fb1588d9`·`6bedd7d8`(rebase 전 SHA) |
| `P1-05` | `review_lang2_p1_05` | 2차 | `REQUEST_CHANGES` | 새 P1 1 · P3 8. 1차 blocking 3건 전부 해소 확인. **DEFECT-P105R2-001(P1)**: 1차 수정이 `tryAcquireLock`에서 `publishLock`을 유예 검사보다 먼저 불러, POSIX `rename(2)`가 빈 디렉터리를 덮어쓰는 성질 때문에 mkdir 방식 구현이 pid를 쓰기 전 창의 잠금을 빼앗는다 — 둘 다 주인이 되고 ubuntu CI 단위 테스트가 적색. Windows는 같은 rename이 EPERM이라 로컬 게이트로 보이지 않았다 → 자리가 비었을 때만 publish, 플랫폼 무관 단언 추가. 수정 `5e8e0af0`(rebase 뒤 `91363442`). POSIX 실행 확인은 ubuntu CI가 했다 |
| `P1-05` | `review_lang2_p1_05` | 3차 | `APPROVE` | P1 0 · P2 1 · P3 2(추가분 P3 3). 2차 P1 해소를 ubuntu CI가 확정, P3 8건 반영 확인. **DEFECT-P105R3-001(P2)**: 두 프로세스 경합 테스트가 고정 900ms 보유에 기대 부하 중 간헐 실패 → 승자가 부모 IPC 신호로 잠금을 놓게. 추가분 `a55e7a67`(포트를 쥔 고아 서버 진단, 자동 kill 없음)을 확인했고 이 tip에서 CI 3 job이 처음 전부 초록. 3차 반영 `3755b186`(경합 신호·JSDoc·빌드 env 단언·조회 실패 degrade·오류 문구 순서)·`a263276b`(PLAN `package.json` 서술). rebase 뒤 각각 `f9ae9d2a`·`7f7eb69c`·`45f1c4a3` |
| `P1-06` | `review_lang2_p1_06` | 1차 | `REQUEST_CHANGES` | P2 2 · P3 5. cascade rebase는 range-diff로 코드 드리프트 0, SoT 해소 규칙 일치, N3 독립 재현(`rank` 포함). **P2-1**: P1-02 행이 4차 APPROVE를 근거로 `APPROVED`인데 #173 head는 그 뒤의 코드 커밋 `ac3a3d0e`(새 도구·CI step)이고 리뷰 기록이 없다 → `IN_REVIEW`·"`ac3a3d0e` 리뷰 대기"로, 테스트 수 8 → 7 정정. **P2-2**: BACKLOG-002~007이 담당 PR의 WORKFLOW acceptance에 없다 → P2-07·P3-03·P4-04·P5-03·P6-03 acceptance에 한 줄씩 예약, BACKLOG-004는 소비자가 정해지지 않아 조건부 담당으로. P3: WORKFLOW:44 착수 조건을 P1-06으로, P1-05 Packet Diff stat·e2e 파일, P1-02 2차 행 수치(새 P2 2·P3 3), BACKLOG-003·004 줄 번호. 관찰(표식 게이트 경계)은 P1-02 5차 행에 |
| `P1-06` | `review_lang2_p1_06` | 2차 | `APPROVE` | `909ae273` 판정. 1차 P2 2건(P1-02 `ac3a3d0e` 리뷰 대기 기록, BACKLOG의 WORKFLOW acceptance 예약)과 P3 5건 반영 확인. 그 뒤 추가분(병합 cascade·US-DM-06 스토리 e2e·병합 리뷰 P3 문서)은 별도 확인 |
| `P0-01`~`P1-06` 병합 | `review_lang2_p1_06` | 병합 cascade | `APPROVE` | blocking 0 · P3 3. main(AI 스택 15개·#193·#195)을 7개 PR에 올린 병합 커밋과 정정 커밋만 봤다. 병합이 PR 고유 변경을 하나도 되돌리지 않았고(층마다 옛·새 범위 추가·삭제 줄 대조), AI 제안 적용은 P1-02 편집 이력 계약의 격리된 전체 교체 한 단계로 들어가며, 단축키 분기는 겹치지 않고, SoT 3-way 결과에 이중 owner·누락이 없다. 의미 충돌 수정 4건 중 테스트를 약화한 것은 없고(`9815b40b`는 단언 강화), 기준선 변화는 전부 P1 기능으로 설명된다. P3: SoT e2e 행의 "B-05가 hunk를 복사" 문장, AI 적용 → 되돌리기 버튼 브라우저 e2e 부재(BACKLOG-009), `currentSource` 우회를 BACKLOG-007에 잇기 — 전부 P1-06 `229eed5f`에서 반영 |

| `P2-01` | `review_lang2_p2_01` | 1 | `REQUEST_CHANGES` | P0 1 · P1 1 · P2 4 · P3 3. **P0**: 명시 `environment`가 run의 tape 파이프라인(`backtest_run/_service.py`의 `preflight`·`run_pipeline`)에 미전달 — 데이터셋·엔진·매니페스트는 명시값을, 관측 조회·tape는 문서 브리지 값을 써서 preview가 422로 거절하는 유니버스를 run이 completed로 기록. **P1**: 명시 `environment`가 제약 검증 우회 — 범위 밖 값이 202 접수 뒤 `backtest.run.internal`로 늦게 실패, 런타임 스키마에도 범위 없음. **P2 4**: 지문 표기 변경 미기록, `environment_hash`의 int/float 비대칭, 매니페스트 비용 3필드가 `environment`와 중복·미대조(fixture가 이미 불일치), `engine_portfolio` PARTIAL_FILL 과소 선언 방향 미기록. **P3 3**: trace 메시지 미갱신, 해시 분리 테스트에 `start` 누락, 범용 빌더 owner. 반영: 파이프라인 전달 + architecture 테스트를 AST 전달 검사로 확장, `RunEnvironment.__post_init__` 검증(범위는 `_constraints.py` `/execution/*` 행 재사용)·float 정규화, 매니페스트 비용 축 대조, 문서 검증 → 브리지 순서 정정(부수 회귀 6건), P2-03에 방향·결정 항목 |
| `P2-01` | `review_lang2_p2_01` | 2 | `APPROVE` | 1차 10건 전부 해소 확인(재현 2개 재실행, 되돌리기 실험이 정확히 2건 실패). 새 findings 6건은 전부 P3·비차단. 코드 2건 반영 — 새 HTTP 테스트의 필드 단언이 사실상 항상 참(422 `input`이 요청 객체를 되돌려줘 모든 필드명이 본문에 있음) → `detail[0]["msg"]`·`loc` 기준으로 좁힘, `RUN_ENVIRONMENT_CONSTRAINTS`의 import 시점 bare `KeyError` → 누락 포인터·카탈로그를 실은 명시 `LookupError`. 문서 3건 이관 — P2-03에 "`preflight`가 `environment`를 받지만 읽지 않음"(두 호출부 값 동일성 가드 강화 또는 시그니처 정리), P3-02에 명시 `environment` 422의 필드 단위 표면 결정과 "범위 SoT는 `/run-environments/schema`, OpenAPI `RunEnvironment`에는 범위 없음". 나머지 1건(PR 본문의 수치·동작 변화 문장)은 리드가 처리 |
| `P2-02` | `review_lang2_p2_02` | 1 | `REQUEST_CHANGES` | P1 1 · P2 3 · P3 3. 핵심 invariant 4개(기본값 경로 `plan_hash` 동일, 정책별 분기, 충돌 거부, 소비자 0건 가드)는 base 코드 대조와 가드 되돌리기 실험으로 실증 확인. **P1**: 팩터 sandbox(`/factors/explain`·`/factors/preview`)가 요청 `missing` 기본값 `DROP`에 고정돼 문서의 `graph.missing_policy`를 무시 — 편집 화면 실행 플랜 패널이 실제 실행과 다른 결측 정책·`plan_hash`를 표시(이 PR이 만든 회귀). **P2 3**: `backtest_run` 의 `LegacyMissingPolicyConflictError` catch가 preflight 뒤라 도달 불가(죽은 코드), `preflight` docstring·`start` 주석이 새 동작과 정반대, WORKFLOW P2-02 미갱신으로 물리 삭제가 어느 PR에도 미할당. **P3 3**: trace만 진단을 문자열로 떨어뜨림, `x-deprecated` 소비자 부재와 해석 시점 순서 미명시, AST 가드가 `missing_policy` 이름 전체를 잡아 `plan.missing_policy`까지 막음. 반영: 요청 `missing`을 `MissingPolicy | None`으로 바꾸고 `None`이면 브리지 `resolve_graph_missing_policy`가 그래프 값으로 해소(+ explain 회귀 테스트 2개, frontend mock을 `body.missing` 기준으로 정정), 죽은 catch 제거 + run 경로 422 shape 테스트, 세 주석 정정, WORKFLOW P2-02 결정 3건·P2-03 물리 삭제 항목 |
| `P2-02` | `review_lang2_p2_02` | 2 | `APPROVE` | 1차 findings 6건 전부 해소 확인. P1 은 세 각도로 실증 재현 — 문서 값 반영(세 정책이 각자 값과 서로 다른 `plan_hash`), 경로 간 일치(`run_pipeline` 의 plan 과 `explain` 의 plan 이 `missing_policy`·`plan_hash` 모두 동일), 가드 되돌리기(application 에서 `request.graph.missing_policy` 를 읽으면 AST 테스트 실패). 새 findings 3건 + 미해소 1건은 전부 P3 비차단이며 후속 커밋 하나로 반영: trace 의 충돌 진단을 `_resolve_environment_or_reject` 로 통일해 preview·run 과 같은 `portfolio.strategy.invalid` + `validation.issues` 구조로, `/factors/preview` fallback 회귀 테스트(mock 관측에 결측 셀이 없어 이중체 포트로 값 수준 고정 — plan 과 값이 같은 정책을 읽는지), `resolve_graph_missing_policy` domain 단위 테스트를 owner 옆에, 모듈 내부 전용 헬퍼를 `_missing_from_legacy_graphs` 로 개명(WORKFLOW P2-03 삭제 목록도 함께). 리뷰어 권고 1건(`x-deprecated` 해석 시점을 P3-01 acceptance 에 명시)은 P3-01 소관으로 남긴다 |
| `P2-04` | `review_lang2_p2_04` | 1 | `REQUEST_CHANGES` | P2 3 · P3 5. 기능(look-ahead 없음·수치 정확·`spec_hash` 변화가 새 키 하나로 설명됨)은 맞음. **P2-1**: `weighting: factor_score` × `normalization: zscore` 에서 `abs()` 때문에 최악 종목이 최대 비중, `rank` 최하위 0점이 `1e-12` 바닥값으로 dust target → `strategy.portfolio.weighting_normalization_incompatible` error + 0점 종목 `SCORE_THRESHOLD` 제외. **P2-2**: 정규화 조회 default 가 원시값 → `_signal_value` 엄격 조회·진단 `ValueError`. **P2-3**: 추출한 rank 공식 값이 기존 테스트로 고정 안 됨(돌연변이 통과) → `CrossSectionalOperator.RANK`·`GroupOperator.RANK` 백분위 값 테스트. P3 3건 코드 반영, 2건 문서 기록. 반영 커밋 `28003cf1` |
| `P2-04` | `review_lang2_p2_0405_r2` | 2 | `REQUEST_CHANGES` | P2 1 · P3 1. 1차 blocking 은 전부 되돌리기 실험으로 닫힘 확인, replay 드리프트 없음. **R2-P204-001(P2)**: 1차 P2-1 이 증상만 막혔다 — `_weight_scores` 의 `abs(composite_score)` 때문에 기본값 `rank` 에서 `direction: low` 는 최선 종목이 빠지고 최악이 최대 비중, `long_short` 공매도 쪽은 가장 강한 숏이 빠진다. 1차 error 의 `allowed=` 가 `rank` 를 대안으로 안내했다 → 원인 수정(결정 5), error 제거. **R2-P204-002(P3)**: 엄격 조회를 되돌려도 초록 → 강제 누락 테스트. 반영 커밋 `2a10798d` |
| `P2-04` | `review_lang2_p2_0405` | 3 | `REQUEST_CHANGES` | P2 1 · P3 1. 방향 반전(R2)은 원인 수준에서 닫힘 확인, 되돌리기 실험 4건 red. **R3-P204-001(P2)**: 롱 바닥 `min(0, eligible 최저)` 때문에 eligible 최저 종목의 강도가 항상 0 이라 `SCORE_THRESHOLD` 로 빠진다 — eligible 1종목·전원 동점 프레임이 비고, `top_count: 1` + `low` 는 매 프레임 보유 0, 5종목 선정은 4종목 보유, `rank` 에서 `high`/`low` 비대칭. **R3-P204-002(P3)**: "1.1 과 달라지는 곳" 이 실제보다 좁다. 리드가 비중 규칙 요건 7개를 정했고 결정 5 를 그 요건을 만족하는 규칙으로 다시 썼다 |
| `P2-04` | `review_lang2_p2_0405` | 4 | `REQUEST_CHANGES` | P2 2 · P3 2. 3차 결함은 닫힘. **R4-P204-001(P2)**: 기준점이 컷 아래 최고뿐이라 원시값 3, 2, 1+2⁻⁵², 1 에서 선정 `c` 가 `7.4e-17` dust 비중, 폐기된 결정 6 의 "dust 없음" 문장과 모순. **R4-P204-002(P2)**: `if below:` 제거·`<=` 돌연변이가 351건 전부 통과(등간격 입력만 있고 컷 동점 없음). R4-P204-003(P3): `rank` 무동점에서 채택 규칙이 `weighting: rank` 와 같고, 컷 아래가 없으면 선정 2 는 항상 2:1. R4-P204-004(P3): ±1e308 overflow 는 `_finite` 로 요란하게 실패(기록만). 리드 결정 (a)안 반영 |
| `P2-04` | `review_lang2_p2_0405` | 5 | `APPROVE` | 4차 blocking 2건 닫힘. P3 R5-P204-001: 컷 동점에서 비중이 불연속이라 자기 점수가 올라 자기 비중이 줄 수 있다(1.0, 1.0, 1.02, −4 선정 2 에서 `a` .499 → .333). 평균 간격 하한이 생겨 엄격 비교가 더는 필요 없으므로 `<=` 로 바꿨다(리드 지시) |
| `P2-05` | `review_lang2_p2_05` | 1 | `REQUEST_CHANGES` | P2 2 · P3 2. 의미 계약·look-ahead·SDK 잔재·trace 렌더 경로는 전부 확인됨. **P2-1**: 동점 결정성 테스트가 입력 순서=기대 순서라 파이썬 안정 정렬이 타이브레이커를 대신해, 정렬 2차 키를 지운 돌연변이가 15 passed 로 통과했다(미충족 acceptance). **P2-2**: PR 크기 기록이 실측과 달랐다(350줄·8파일로 적었으나 실측 690줄·13파일로 12절 상한 초과) — 분할은 요구하지 않고 기록·사유만. **P3 2**: `top_percent` 가 모집단을 0으로 만드는 쪽에 진단 없음(P2-07 backlog), 1-pass dict / 2-pass 선형 탐색 조회 불일치. 반영: 같은 픽스처를 `reversed()` 로 한 번 더 컴파일해 동일 결과 단언(돌연변이 재현으로 검증), 2-pass 조회를 관측당 dict 로 통일 + 중복 `field_id` 회귀 테스트, PLAN·PR 본문 크기 기록 정정, WORKFLOW P2-07 에 backlog 한 줄 |
| `P2-05` | `review_lang2_p2_0405_r2` | 2 | `APPROVE` | 1차 P2 2건·P3-2 가 되돌리기 실험으로 닫힘 확인. rebase 뒤 코드 변경분이 바이트 단위로 같고 기준선 해시(`41290101…`/`e637846e…`)가 새 enum 값 두 개로 설명된다. P3 3건: R2-P205-001 크기 실측 690 → 736(반영), R2-P205-002 기준선 커밋 메시지가 `eligibility_rank_cut` 을 runtime schema 변화 원인으로 적음(그 값은 OpenAPI 에만 있다, 커밋 메시지라 기록만), R2-P205-003 테스트 주석의 문턱 값 `> 3` 이 코드 `2.0` 과 다름(판별력 무관, 기록만) |
| `P2-05` | `review_lang2_p2_0405` | 3 | `APPROVE` | P2-04 2차 반영 tip 위 재배치 확인. 코드 커밋 range-diff 는 문맥 줄 두 개만 다르고 `backend/src`·`backend/tests`·`frontend/src` 변경분이 바이트 동일, 기준선 해시 불변. P2-04 R3-P204-001 이 이 PR 의 `top_count: 1` 로 재현돼 재현 테스트 `8587d413` 을 더했다 |
| `P2-05` | `review_lang2_p2_0405` | 4 | `APPROVE` | src 는 이전 APPROVE본과 바이트 동일, 재현 테스트 `8587d413` 는 균등 대체를 지우면 6건 red 로 판별력 있음. 첫 커밋의 enum 개명 한 줄은 P2-05 가 enum 을 바꾸므로 필요한 변경으로 확인. 해시 불변 |
| `P2-05` | `review_lang2_p2_0405` | 5 | `APPROVE` | 4차본과 src 바이트 동일, 해시 불변. P2-04 R5 반영(`<=`) 뒤 재배치 |
## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| `P1-03` | cascade tip `748bc62f`: backend `pytest -q`·`ruff check src tests examples scripts`·`pyright`, 루트 tools unittest·ruff·pyright, 충돌 표식 검사, frontend `api:generate` 후 생성물 diff·`typecheck`·`typecheck:e2e`·`lint`·`npm test` | pytest 1579 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 674/674 | 2026-09-26 |
| `P1-04` | cascade tip `e6fb10b0`, 위와 같은 게이트 | pytest 1611 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 704/704 | 2026-09-26 |
| `P1-05` | cascade tip `45f1c4a3`, 위와 같은 게이트 + `npm run test:e2e`(머신 잠금 아래) | pytest 1695 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 733/733 · e2e 25/25 | 2026-09-26 |
| `P1-06` | `tools/update-plan-progress.ps1 -Check`, `uv run --locked python -m quant_study_dev.conflict_markers` | `PLAN.md is consistent: 0/29 merged, 6 approved`(P1-02 5차 APPROVE 반영 뒤. 1차 리뷰 반영 시점에는 P1-02가 `ac3a3d0e` 리뷰 대기라 5) · 충돌 표식 0 | 2026-09-26 |

| `P2-01` | `uv run pytest -q` (backend) | 1480 passed, 13 skipped | 2026-09-20 |
| `P2-01` | `uv run pytest -q` (backend, 리뷰 반영 후) | 1493 passed, 13 skipped | 2026-09-21 |
| `P2-01` | `uv run ruff check src tests` · `ruff format --check`(변경 30파일) | 통과 | 2026-09-20 |
| `P2-01` | `uv run pyright` | 4 errors — 전부 `duckdb` 미설치(기존), 신규 파일 0 | 2026-09-20 |
| `P2-01` | `npm run typecheck` · `lint` · `test` · `build` (frontend) | 통과, Vitest 639(57 파일) | 2026-09-20 |
## 변경 기록

- 2026-09-27 — P0-01·P1-01~P1-05 main 머지. 머지 커밋 #167 `b438e58d`, #168 `3d6f2b42`, #173
  `bee2a4fd`, #177 `9cef7f3d`, #181 `3c1f1ab7`, #188 `7d525949`. 그 전에 AI 어시스턴트 스택 15개·#193·
  #195(유저 스토리 하네스)를 merge cascade(리뷰된 SHA 보존)로 P0-01부터 P1-06까지 올렸고, 병합 커밋 리뷰는
  APPROVE(blocking 0·P3 3)였다. 병합이 드러낸
  의미 충돌 네 건은 각 브랜치에서 고쳤다: AI 테스트의 편집기 대역(P1-02 핸들 계약), e2e 원문 읽기의
  reveal 경합(`currentSource` 안정 읽기), 계약 패널 스토리 e2e(P1-03 화면 어휘), 어시스턴트 대본 골든
  글자 수(P1-05 한글 진단). 인프라 기준선은 층마다 다시 찍었다.
- 2026-09-27 — 병합 리뷰 P3 반영(P1-06): SoT e2e 잠금 행의 "B-05가 hunk를 복사" 문장을 병합 뒤 사실
  (한 벌)로, `currentSource` 우회를 BACKLOG-007에, AI 적용 뒤 버튼 되돌리기 e2e 부재를 BACKLOG-009(담당
  P3-03, 스토리 US-DM-09 `예정`)로, BACKLOG-008 줄 번호를 `:16-17`로. 하네스 문서도 P0-01 머지 뒤 사실로
  맞췄다: US-CS-01 수용 기준을 P1-04 연산자 팔레트·P1-02 툴바 되돌리기로(e2e는 이미 그 흐름),
  traceability 머리말·README 한계 절의 "lang2 WORKFLOW가 아직 main에 없다" 문장.
- 2026-09-27 — P1-06 추가분(유저 스토리 하네스): P1-03~P1-05가 구현한 US-DM-06(화면의 말과 오류 문장을
  쉬운 한글로)을 `구현됨-e2e`로 올렸다. 스토리 e2e `frontend/e2e/stories/dm.readable-korean.spec.ts` 두
  건(`@story`·`@US-DM-06`)이 노드 종류·연산자·필드의 한글 이름과 설명, 노드 이름으로 말하는 삭제 거부,
  오타 필드의 한글 문장과 제안, 1.0 키의 업그레이드 안내 문장과 배너를 본다. 수용 기준 네 항목을 P1
  기능이 모두 덮어 스토리를 쪼개지 않았고, 배너가 저장된 리비전 화면에만 뜬다는 사실만 기준과 비고에
  적었다. traceability는 `user_story_trace --write`로 다시 만들었다(검사·Playwright 목록 대조 통과).
- 2026-09-26 — P1-02 5차 리뷰(`ac3a3d0e`) APPROVE, blocking 0·P3 4. 게이트가 사고 당시 트리
  `db3bc079`와 실제 git 충돌(기본 방식·diff3)을 exit 1로 잡고, 오탐 규칙이 `git diff --check`와
  같으며, CI step이 run 36220139969에서 실제로 돌았다. P1-02를 `APPROVED`로 되돌렸다. 앞서 5차
  대기 행에 "diff3 표식은 검출하지 않는다"고 적은 것은 틀렸다 — `|||||||` 줄 자체는 목록에 없지만
  diff3 충돌도 나머지 세 표식으로 검출된다. 도구 개선 P3(문장 정정·`git ls-files` 전환·`.lock`과
  비 UTF-8 건너뜀)는 BACKLOG-008로 P3-03 acceptance에 예약했다.
- 2026-09-26 — P1-06 1차 리뷰(REQUEST_CHANGES, P2 2·P3 5) 반영. P1-02를 `APPROVED`에서
  `IN_REVIEW`로 내렸다 — 4차 APPROVE는 `db3bc079`까지이고 #173 head `ac3a3d0e`(표식 해소·검출 도구·
  CI step)는 리뷰 기록이 없다. Review 기록에 5차 대기 행을 두고 결과가 오면 채운다. 도구 테스트
  수를 7건으로 바로잡았다. BACKLOG-002~007을 담당 PR의 WORKFLOW acceptance에 한 줄씩 예약했다
  (P2-07 ← 003, P3-03 ← 002, P4-04 ← 005·006·007, P5-03 ← 004 조건부, P6-03 ← 006 후속).
  P3-01 착수 조건을 P1 스택 끝(P1-06)으로, P1-05 Packet의 Diff stat(52 파일 `+2920 −167`)·e2e
  파일·테스트 수(27), P1-02 2차 행 수치(새 P2 2·P3 3), BACKLOG-003·004 줄 번호를 고쳤다.
- 2026-09-26 — P1-06(Phase 1 감사 후속, 문서). Phase 1 감사(2026-09-21, 판정 기준 `cf56b5b4`,
  추가 확인 `a55e7a67`)는 exit (a)·(b)를 충족, (c)를 **BLOCKING 2건**으로 미충족 판정했다.
  DEFECT-P1X-001(SoT 대장 충돌 표식, 정본 5행 이중 owner)은 P1-02 `ac3a3d0e`가 해소하고 검출
  게이트를 세웠으며, P1-03~P1-05를 그 위로 cascade rebase해 스택 전체에서 표식이 0이 됐다.
  DEFECT-P1X-002(PLAN이 P1-05 리뷰·blocking·PR 미기록)는 이 PR이 닫는다: P1-01·P1-05 상태를
  `APPROVED`로, PR 링크(#181·#188), Review 기록에 P1-01 1~3차·P1-05 1~3차 행, P1-02 1·2차 행의
  수치 표현 정정, rebase가 남긴 표 중간 빈 줄 제거, backlog 절에 잘못 들어간 변경 기록 8줄을 이
  절로 옮겼다(같은 사실이 이미 있는 3줄은 기존 항목으로 흡수, 매뉴얼 1줄은 BACKLOG-002로).
  비차단: SoT에서 N8(하한 행 — 스키마 빌더는 metadata를 optional로 읽는다)·N9(e2e 잠금·포트·
  빌드 주소 행을 실제 owner로, `build-command.mjs` 포함)·연산자 가용성 담당(P2-04 → P2-07)을
  정정하고, N1·N2·N3·N4·N5·N10과 P6-03 키보드 flake에 담당 PR을 붙였다(BACKLOG-002~007).
  N6·N7·N11은 cleanup·관찰이라 담당 없이 둔다. 계획 PR 수 28 → 29.
- 2026-09-26 — P1-03~P1-05 cascade rebase. P1-02 tip `ac3a3d0e` 위로 `rebase --onto`했다.
  P1-03(옛 base `db3bc079`)은 충돌 없음 → `748bc62f`. P1-04는 3차 반영 커밋에서 PLAN `변경 기록`
  충돌 1건(P1-02 수정 항목 ↔ P1-04 항목, 둘 다 추가라 양쪽을 남김) → `e6fb10b0`. P1-05는 충돌
  없음 → `45f1c4a3`. 세 tip 모두 옛 tip과의 차이가 `ac3a3d0e` 내용뿐이고(코드 패치 동일), 표식
  0·게이트 통과(`검증 기록`). 단계마다 `git diff --name-only --diff-filter=U`로 미해소 파일을
  확인했다 — DEFECT-P1X-001을 만든 절차 누락의 재발 방지다.
- 2026-09-26 — P2-05 5차 리뷰 APPROVE. P2-04 5차 반영(`<=`) tip `35089901` 위로 다시 옮겼다.
  PLAN 만 충돌했고 코드 커밋 13개는 range-diff 가 모두 `=` 다.
- 2026-09-26 — P2-05 4차 리뷰 APPROVE. P2-04 4차 리뷰 반영 tip `34b20ef5` 위로 다시 옮겼다.
  PLAN 만 충돌했고 코드 커밋 13개는 range-diff 가 모두 `=` 다.
- 2026-09-26 — P2-05 3차 리뷰 APPROVE. P2-04 3차 리뷰 반영 tip `350e8a40` 위로 다시 옮겼다.
  PLAN 만 충돌했다. 코드는 한 줄이 다르다 — P2-04 3차 테스트가 쓰는
  `ComparisonOperator.GREATER_THAN` 을 이 PR 의 첫 커밋(`a35c1e40`, enum 개명)이 다른 소비자와
  함께 `EligibilityOperator` 로 바꾼다. P2-04 R3-P204-001 이 `top_count: 1` +
  `low` 로 재현되므로 그 문서를 고정하는 테스트 `8587d413` 을 더했다(P2-04 2차 규칙에서 6건 red).
  크기 실측은 810줄·13파일이다(첫 커밋의 개명 한 줄 포함).
- 2026-09-26 — P2-05 2차 리뷰 APPROVE. P2-04 2차 리뷰 반영 tip `a9f4ed93` 위로 다시 옮겼다.
  코드 변경분은 바이트 단위로 같고 PLAN 만 충돌했다. 크기 실측을 736줄·13파일로 정정했다
  (R2-P205-001). 처음 `a823ebc8` 위로 옮길 때의 코드 충돌은 `_compiler.py` 한 곳이었고
  P2-04 `_signal_value` 와 P2-05 `_apply_cross_sectional_eligibility` 를 둘 다 남겼다. 새 계약
  해시로 워크벤치 시각 기준선 4장을 재생성했다(`57731080`).
- 2026-09-26 — P2-04 5차 리뷰 APPROVE. 비차단 P3 R5-P204-001 을 리드 지시로 반영했다. 기준점 후보
  비교를 `<=` 로 바꿔 선정 최저와 동점인 비선정 종목도 후보에 넣었다. 컷 동점 기대값을 2/3·1/3 으로
  고치고 리뷰어 반례와 자기 점수 단조성 퍼즈를 더했다. 결정 5·SoT 의 규칙 문장을 "이하인"으로 고쳤다.
- 2026-09-26 — P2-04 4차 리뷰(REQUEST_CHANGES, P2 2·P3 2) 반영. 리드 결정 (a)안대로 기준점을
  `min(컷 아래 최고, 선정 최저 − 평균 간격)` 으로 바꿔 근접 동점 dust 를 없앴다. 요건 1 은 "최소 평균
  간격만큼의 강도"로 확정했다. 근접 동점·불균등 간격·컷 동점·컷 아래가 먼 경우·`rank` 무동점을
  값으로 고정하고 엄격 비교·`if below:` 돌연변이가 red 인지 확인했다. 결정 5 에 채택 규칙에도
  해당하는 기각 사유(R4-P204-003)와 컷 아래가 먼 경우의 성질을 적고, 1.1 차이 수치를 재실측했다.
- 2026-09-26 — P2-04 3차 리뷰(REQUEST_CHANGES, P2 1·P3 1) 반영. 리드가 정한 `factor_score` 비중
  요건 7개(선정 종목 비중 0 금지, 퇴화 프레임 균등, 거울 대칭, 숏 대칭, 규칙 문서화, 1.1 차이
  정정, 경계 테스트)를 만족하는 규칙으로 결정 5 를 다시 썼다. 강도는 선정 컷 바로 아래 eligible
  비선정 종목과의 점수 차(없으면 선정 최저 − 평균 간격)다. 결정 6(강도 0 제외)은 폐기했다.
  1.1 과 비중이 같은 경우가 "기준점이 정확히 0" 뿐이라는 사실과 P2-09 영향을 적었다.
- 2026-09-26 — P2-04 2차 리뷰(REQUEST_CHANGES, P2 1·P3 1) 반영. 점수 비례 가중이 합성 점수의
  절댓값 대신 선호 방향 거리(롱은 `min(0, 최저 점수)` 바닥, 숏은 `max(0, 최고 점수)` 천장)를
  쓰게 고쳐 `rank`·`zscore`·`none` 모두에서 `direction: low` 와 공매도 쪽의 비중 반전을 없앴다.
  원인이 사라져 1차의 `zscore` 조합 차단 error 를 지우고 결정 5 를 다시 썼다. 엄격 조회 동작을
  고정하는 테스트를 넣었다.
- 2026-09-26 — 리뷰 기록 표의 P2-04 행이 P2-02 1·2차 행 사이에 끼어 있어 P2-02 행 뒤로 옮겼다(문서만).
- 2026-09-26 — P2-04 를 P2-03 APPROVED tip `0dd740e8` 위로 replay 하고(코드 4커밋 range-diff 동일) 1차
  리뷰 반영 `28003cf1`·시각 기준선 `80a5ddf5` 를 얹어 상태를 `IN_REVIEW` 로 갱신했다. 옛 PR head
  `dc8030d3` 는 옛 base 위라 PR #184 가 CONFLICTING 이었다.
- 2026-09-21 — P1-05 3차 APPROVE와 반영. 2차 blocking 해소를 ubuntu CI가 확정했다. 새 P2
  DEFECT-P105R3-001(경합 테스트가 고정 900ms 보유에 기대 부하 중 간헐 실패)은 승자가 부모의
  IPC 신호로 잠금을 놓게 고쳤다(`3755b186`). 추가분 `a55e7a67`(포트를 쥔 고아 서버의 pid·시작
  시각·커맨드를 오류에 담는다, 판정에 쓰지 않고 자동 kill 없음)도 APPROVE 유지, 이 tip에서 CI
  세 job이 처음 전부 초록이었다. PLAN `package.json` 서술 정정 `a263276b`.
- 2026-09-21 — P1-05 2차 REQUEST_CHANGES(DEFECT-P105R2-001) 수정.
  **상황**: 1차 DEFECT-P105-002 수정이 잠금 획득을 원자적 rename 하나로 바꿨다.
  **인풋**: 잠금 자리에 pid 파일이 아직 없는 빈 디렉터리가 있을 때(mkdir 방식 구현이 pid를 쓰기
  전 창, 또는 `lock.test.mjs`의 "갓 만들어진 pid 없는 잠금" 단언) `tryAcquireLock`을 부른다.
  **에러 위치**: `frontend/e2e/lock.mjs`의 `tryAcquireLock`이 `publishLock`을 유예 검사보다 먼저
  불렀다. POSIX `rename(2)`는 대상이 빈 디렉터리면 성공하므로 유예 검사에 닿지 못한다.
  **위험성**: POSIX에서 둘 다 주인이 되어 잠금이 막으려던 "옆 워크트리 backend를 테스트하고도
  통과"(silent 오탐)가 되살아나고 ubuntu CI 단위 테스트가 적색이 된다. Windows는 같은 rename이
  EPERM이라 로컬 게이트로는 보이지 않았다.
  **수정**: `5e8e0af0` — 자리가 비었을 때만 publish한다. 잠금 코드를 다시 만질 때 1차 수정만
  근거로 삼지 않는다(SoT e2e 잠금 행에 순서의 이유를 적었다).
- 2026-09-21 — P1-05 1차 리뷰(REQUEST_CHANGES) 반영: 업그레이드 가능 판정을
  `is_upgradeable_document` 하나로 모아 `structure.legacy_shape` 배너가 실제로 동작하게 했고
  (전에는 눌러도 반드시 422), e2e 잠금 획득을 원자적 rename 하나로 바꿔 두 프로세스가 동시에
  주인이 되던 경합 2종을 닫았으며, `PW_BACKEND_PORT`가 vitest·dev 설정까지 새어 단위 게이트를
  깨던 경로를 막았다. 순환 진단은 SCC로 바꿔 고리에 묶인 노드를 하나도 빠뜨리지 않는다. 이 잠금 수정은
  POSIX에서 반대 방향 경합을 새로 만들었고 2차 리뷰가 잡아 다시 고쳤다(위 2차 항목).
- 2026-09-21 — P1-05에서 저장소 전체 e2e 결함을 고쳤다(리드 지시): Playwright `webServer`의
  `reuseExistingServer: false`가 시작 시점 포트만 봐서, 워크트리 둘이 겹쳐 돌면 브라우저가 옆
  체크아웃의 backend를 테스트하고도 통과할 수 있었다. 머신 단위 잠금으로 직렬화하고, 포트를
  환경 변수로 열고, 포트가 막혀 있으면 조용히 재사용하지 않고 즉시 실패하게 했다.
- 2026-09-21 — P1-05 구현: 구조·codec 진단이 backend에서 한글 문장으로 완성돼 나가고
  (`.claude/rules/strategy-workbench-sot.md` authoring 진단 코드 행), `structure.legacy_shape`·
  `STRUCTURE_CODES`·`FACTOR_GRAPH_CODES`·`expression_code()` 세 레지스트리 게이트가 생겼다.
  `factor.*`는 더 이상 전략 문서 진단으로 나가지 않는다. 문장 golden이 레지스트리와 1:1이다.
  리드 브리핑의 "진단 문장을 frontend i18n 템플릿으로" 방향은 SoT·WORKFLOW 원문과 충돌해 철회됐다.
- 2026-09-21 — P2-09 acceptance 추가: `is_upgradeable_document`에 버전 상한을 두는 항목을
  업그레이더 PR에 적었다. 판정을 넓힌 것은 P1-05가 이미 했고(진단이 "업그레이드하세요"라고 시킨
  문서를 endpoint가 거절하던 모순 제거), 1.2가 들어오면 "미래 버전 + 옛 키 하나"가 1.1로
  강등되는 경로가 되므로 상한은 버전 디스패치를 넣는 PR 몫이다. 옛 24 PR 계획 기준으로 이 줄을
  P2-05에 적었던 것을 28 PR 계획에 맞춰 옮겼다.
- 2026-09-21 — P1-02 결함 수정(Phase 1 감사 BLOCKING): SoT 대장에 커밋된 병합 충돌 표식.
  **상황**: P1-02를 28 PR 계획 tip(`ea7e2fa4`) 위로 `rebase --onto`할 때, 충돌 해소를
  `git add -A` + `rebase --continue` 반복 스크립트로 돌리면서 각 단계의 남은 표식을
  확인하지 않았다. 출력도 `tail -3`으로 잘라 봐 SoT 파일의 CONFLICT 줄을 놓쳤다.
  **인풋**: 3번째 커밋 `a9121667`(원본 `86ce099c`) replay — PLAN.md와 함께
  `.claude/rules/strategy-workbench-sot.md`도 충돌했는데 PLAN만 해소하고 staging했다.
  **에러 위치**: `.claude/rules/strategy-workbench-sot.md:27-39` — 정본 대장 안에
  `<<<<<<< HEAD`/`=======`/`>>>>>>> 86ce099c` 표식과 함께 5행(실행 설정·그래프 표현 투영·
  편집 이력·authoring schema 버전·업그레이드 변환)이 두 벌로 남았다. `a9121667`부터
  P1-02 tip `db3bc079`, 이어받은 P1-03~P1-05 tip `a55e7a67`까지 전파됐다.
  **위험성**: 표식이 있어도 Markdown은 정상 렌더되고 게이트도 전부 통과해 조용하다.
  되살아난 24 PR 시절 행이 P2-01 브리지를 `application/backtest_run/_environment.py`에
  두라고 해 WORKFLOW P2-01의 금지 배치와 정면으로 모순되고, 1.2 업그레이드 step과
  `FROZEN_SCHEMA_VERSIONS` 예약이 사라져 후속 PR이 어느 행을 정본으로 읽느냐에 따라
  서로 다른 구현을 낳는다(정본 분열).
  **수정**: 4행(실행 설정·그래프 투영·schema 버전·업그레이드 변환)은 P0-01 개정본,
  편집 이력 1행은 `86ce099c` 판으로 해소. 재발 방지로 저장소 전체 충돌 표식 검출
  (`tools/quant_study_dev/conflict_markers.py`, 단위 테스트 7건 — 커밋 메시지의 "8건"은 오기, CI
  backend job step)을 추가했다. 이 커밋은 4차 APPROVE 뒤에 올라가 5차 리뷰가 따로 APPROVE했다
  (P1-02 Review 5차 행, 개선 P3는 BACKLOG-008).
- 2026-09-21 — P1-04 구현·리뷰 3회: 연산자 팔레트(카탈로그·스키마 주도, 손으로 적은 목록 0), 추가·
  삭제 실패의 사유 표시, 진단 본문 인라인. 구현 중 발견한 `window: 0` 결함은 하한을 노드 dataclass
  옆에 한 번 선언하고 검증기·runtime schema가 함께 읽게 해 같은 PR에서 고쳤다(SoT 행 추가).
  1차 REQUEST_CHANGES: `지연`의 `periods: null`과 노드 pointer 진단이 붙을 자리가 없던 것.
  2차: 노드 카드 본문 CSS 누락(flex 한 줄)과 중복 렌더·중복 id·씨앗 범위·TS/Python 2중 구현.
  3차: 본문 근접성이 뒤집혀 아래 노드 것으로 읽히던 것. 레이아웃 계약은 e2e boundingBox가 잠그고
  기준선은 문장을 `mask`로 가려 backend 문구와 분리했다.
- 2026-09-21 — P1-02 구현·리뷰 4회: PR #173. 되돌리기·다시 실행을 탭 밖 버튼과 IDE 전역
  단축키 두 경로로 내고, 편집 이력의 단일 정본을 편집기(CodeMirror history)로 못 박았다
  (SoT 행 추가). 1차 REQUEST_CHANGES: 같은 문서 전체 교체가 비격리 `setText`로 가 직후
  타이핑과 한 undo 단계로 합쳐지던 것을 `replaceRange` 격리 경로로 통일하고 `setText`를
  핸들에서 제거했다. 3차 REQUEST_CHANGES: 포커스 링 클립을 고치려 넓힌 스크롤포트가 검증
  버튼 하단 클릭을 가로채 `outline-offset: -2px`(inset)로 교체하고 actionability e2e 단언을
  추가했다. 4차 APPROVE(코드 결함 0).
- 2026-09-21 — P1-03 구현·1차 리뷰 반영·2차 APPROVE: PR #177. 연산자 정의 레지스트리
  (`domain/factor/_operators.py`, 키 `(kind, operator)` 23개)와 runtime schema의 `x-description-key`·
  `x-operator`, `GET /api/v1/strategy-documents/operators`를 backend가 소유하고 화면 어휘 문장은
  frontend i18n이 렌더한다. Form·Graph 라벨이 키 대신 이름을 보인다. 1차 리뷰가 잡은 차단 2건
  (en 로케일 한글 계산식, 시간축 계산식의 `lag` 누락)과 공회전하던 `output_type_rule` 대조 테스트를
  고치고 2차 APPROVE. 스코프 밖 관찰은 BACKLOG-001.
- 2026-09-21 — P2-05 1차 리뷰(REQUEST_CHANGES, P2 2·P3 2) 반영. **P2-1** 동점 결정성 테스트가
  타이브레이커를 고정하지 못했다 — 관측을 `s000`~`s004` 오름차순으로만 넣어 파이썬 안정 정렬이
  2차 키를 대신해 줬고, `population.sort` 에서 `item[0]` 을 지운 돌연변이가 그대로 통과했다.
  같은 픽스처를 `reversed()` 로 한 번 더 컴파일해 결과가 같음을 단언하고, 돌연변이를 다시 넣어
  이번에는 실패하는 것을 확인했다. **P2-2** 크기 기록을 실측(690줄·13파일)으로 고치고 12절 상한
  초과 사유를 패킷과 PR 본문에 적었다 — 13파일 중 5개가 enum 개명이 강제한 1~2줄 import 수정이라
  분할할 단위가 없다. **P3-2** 2-pass 의 필드 조회를 1-pass 와 같은 관측당 dict 로 통일했다
  (중복 `field_id` 에서 두 패스가 다른 값을 읽던 차이, 포트 계약이 막지만 공개 도메인 facade 는
  임의 관측으로 불린다). **P3-1**(`top_percent` 가 모집단을 0으로 만드는 쪽에 진단 없음)은
  WORKFLOW P2-07 acceptance 에 backlog 한 줄로 옮겼다 — 결과가 빈 포트폴리오라 조용하지 않고,
  compile 단일 게이트가 frame warning 을 다루는 자리다.
- 2026-09-21 — P2-05 을 P2-04 새 tip `dc8030d3`(P2-03 최종 `7ec8f337` 위 replay 판) 으로
  재배치했다. 코드 8커밋은 충돌 없이 replay 됐고 PLAN 커밋만 P2-04 행·frontmatter 에서 충돌해
  새 base 의 P2-04 행을 취했다. 옛 base `bb8f3843` 에서 관측했던 backend 3 failed·
  `typecheck:e2e` 3건 실패·1.1 스냅샷 기준선 불일치는 전부 그 base 산물이었고 `7ec8f337` 이
  이미 해소했다 — 새 base 에서는 backend 1587 passed / 0 failed, `typecheck:e2e` 통과다.
  그래서 한때 여기 적었던 e2e base 결함 기록은 지웠다. e2e 자체는 잠금·포트를 AI 스택이 먼저
  쓰므로 리드 신호 뒤 1회 실행한다.
- 2026-09-21 — P2-05 구현 완료(SELF_CHECK). `EligibilityRule.operator` 를 전용
  `EligibilityOperator` 로 떼고(`ComparisonOperator` 는 소비자가 사라져 삭제), `_compare` 의
  catch-all `return value == threshold` 를 없애 미지 연산자를 raise 하게 했다. 프레임 컴파일은
  2-pass 다 — 1-pass 가 절대 규칙으로 후보를 거르고, 2-pass 가 규칙마다 따로 센 모집단의 순위로
  `top_*` 를 적용해 AND 로 묶는다. 모집단은 유니버스 멤버 ∩ 절대 규칙 전부 통과 ∩ 해당
  `field_id` 값이 기준일까지 공개된 종목이고, 결측·공개일 초과는 탈락이면서 분모에서도 빠진다.
  동점은 값 내림차순 → `security_id` 오름차순 cut 이라 같은 입력이 같은 컷을 낸다. 순위 탈락은
  `ExclusionReason.ELIGIBILITY_RANK_CUT` 로 규칙 위반과 구분되며, 멤버 추가가 API 계약 변경이라
  OpenAPI·runtime schema·생성 SDK 를 같은 PR 에서 재생성했다. 비율 cut 을 이진 부동소수로
  곱하면 `100 × 0.29` 가 28 을 내서(격자 1~300 × 1~99% 에서 16조합) `Decimal(str(value))` 로
  문서 표기를 그대로 곱한다. `top_*` 의 `value` 범위는 새 진단
  `strategy.eligibility.rule_value` 가 막는다 — 이 PR 이 `value` 의 의미를 연산자별로 갈라
  놓아 생긴 구멍이라 같은 PR 이 닫는다.
- 2026-09-21 — P2-04 구현 완료(SELF_CHECK). `signal.normalization`(기본 `rank`)을 모델·runtime
  schema·OpenAPI·생성 SDK 에 넣고, `domain/portfolio/_compiler.py` 가 가중 합 **전에** 프레임
  횡단면 정규화를 적용한다. 순위·표준화 공식은 `domain/factor/_statistics.py` 하나가 소유하게
  정리해 팩터 그래프의 횡단면 연산자와 같은 정의를 쓴다. 정규화 모집단은 `(기준일,
  universe_member)` 동료 집단이고 결측·공개일 초과·비유한값을 뺀다 — 결측 처리가 항상 정규화
  앞이라는 순서를 테스트로 고정했다. `availability` 판정은 WORKFLOW 상 P2-07 이라 범위 밖으로
  두었다. base 인 P2-03 tip 이 import 단계에서 깨져 있어 같은 결정(`missing` 생략 → `drop`)의
  우회 커밋 두 개를 앞에 뒀고, P2-03 origin tip 위 rebase 에서 버릴 수 있게 분리해 두었다.
- 2026-09-21 — P2-02 2차 리뷰 APPROVE. 1차 6건 해소 확인, 남은 P3 4건을 후속 커밋 하나로
  반영했다. trace 만 충돌 사유를 `InvalidStrategyTraceRequestError(str(error))` 로 납작하게
  만들어 프론트가 코드로 분기하려면 메시지를 파싱해야 했다 — preview·run 과 같은
  `_resolve_environment_or_reject` 를 쓰게 해 세 경로가 같은 코드·details 를 낸다.
  `/factors/preview` 는 plan 컴파일과 평가가 각각 정책을 해소하므로 한쪽만 되돌아가면 plan 과
  값이 어긋난 응답이 나간다. mock 관측에는 결측 셀이 없어 HTTP 로는 안 보이므로 결측 셀을 담은
  이중체 포트로 값 수준에서 고정했다. `resolve_graph_missing_policy` 단위 테스트를
  `resolve_environment` 계약 옆에 두고, 모듈 내부 전용 헬퍼는 `_missing_from_legacy_graphs` 로
  개명했다(public 이름은 deep import 를 유인한다).
- 2026-09-21 — P2-02 1차 리뷰(REQUEST_CHANGES, P1 1·P2 3·P3 3) 반영. **P1**: 팩터 sandbox가
  문서의 결측 정책을 무시했다. `FactorGraphRequest`·`FactorPreviewRequest`의 `missing`을
  `MissingPolicy | None = None`으로 바꾸고, `None`이면 브리지의 `resolve_graph_missing_policy`가
  그래프의 1.1 값으로 해소한다 — application이 `graph.missing_policy`를 직접 읽지 않아야 AST
  가드가 살아 있으므로 판정은 `domain/backtest`가 갖고 `application.factor_research`의
  `DEPENDS_ON`에 `domain.backtest`를 더했다. frontend mock은 옛 backend 동작(그래프만 읽기)을
  흉내 내 이 회귀를 구조적으로 못 잡았으므로 `body.missing` 기준으로 고쳤다. **P2**: preflight
  뒤라 도달할 수 없던 `backtest_run`의 충돌 catch를 지우고 run 경로의 422 shape
  (`portfolio.strategy.invalid` + `validation.issues`의 코드)를 테스트로 고정했다. `preflight`
  docstring의 "`request.environment`를 읽지 않는다"와 P2-03 결정 항목 문구는 이 PR이 바로 그
  시점이므로 현재 동작으로 바꿨다. WORKFLOW P2-02 절에 결정 3건을, P2-03 acceptance에 1.1 호환
  잔재의 물리 삭제 항목을 적었다.
- 2026-09-21 — P2-01 2차 리뷰 APPROVE. 새 findings 6건은 전부 P3다. 코드 2건을 반영했다 —
  새 HTTP 테스트의 `assert field_name in response.text`는 422 본문의 `input`이 요청한
  environment 객체를 통째로 되돌려주므로 **틀린 필드가 보고돼도 통과**했고, `detail[0]["msg"]`와
  `loc` 기준으로 좁혔다. `RUN_ENVIRONMENT_CONSTRAINTS`가 모듈 수준에서
  `scalar_constraint_index()[pointer]`를 불러 포인터 rename 시 부팅이 맥락 없는 `KeyError`로 죽던
  것을, 누락 포인터와 현재 카탈로그를 실은 `LookupError`로 바꾸고 테스트를 붙였다. 문서 3건은
  WORKFLOW P2-03·P3-02 결정 항목으로 이관했고, PR 본문 갱신 1건은 리드가 맡는다.
- 2026-09-21 — P2-01 1차 리뷰(REQUEST_CHANGES, P0 1·P1 1·P2 4·P3 3) 반영. **P0**: 명시
  `environment`가 run의 tape 파이프라인(`backtest_run/_service.py`의 `preflight`·`run_pipeline`)에
  전달되지 않아, 데이터셋·엔진·매니페스트는 명시값을 쓰는데 관측 조회·tape만 문서 브리지 값으로
  되돌아갔다. preview가 422로 거절하는 유니버스를 run이 completed로 기록했다. architecture 테스트를
  "`PortfolioPreviewRequest`를 `environment` 없이 만들지 않는다"(AST)로 확장해 전달 누락 자체를
  고정했다 — 기존 검사는 `spec.data.*` 읽기만 봐서 이 계열에 무력했다. **P1**: `RunEnvironment`에
  `__post_init__` 검증을 넣어 범위를 벗어난 명시 설정이 접수 단계 422가 된다(이전에는 202 접수 뒤
  `backtest.run.internal`). 수치 범위는 `_constraints.py`의 `/execution/*` 행을 필드 이름으로 다시
  걸어 검증과 런타임 스키마가 같은 행을 읽는다. **P2**: 수치 float 정규화(`fee_bps=15`와 `15.0`의
  hash 동일), 매니페스트 비용 축 대조. **부수**: `__post_init__` 검증이 브리지를 통해 문서 오류를
  코드화된 진단보다 먼저 터뜨려 6건이 회귀했고, 세 호출부 모두 "문서 검증 → 브리지" 순서로 고쳤다.
- 2026-09-21 — P2-01 기록: `run_fingerprint` 표기가 바뀌어 P2-01 이전 매니페스트의 지문은 재계산
  대상이 아니다(`run_spec`에 `environment` 키가 늘었다). 캐시 lookup이 없어 잘못된 히트는 불가능하고
  영향은 감사 대조뿐이다. 지문 표기 버전 도입 여부는 캐시가 생기는 P2-09에서 정한다. WORKFLOW
  P2-03에 잔여 이관 항목의 **과소 선언 방향**(문서 1.0 + 환경 0.1이면 `PARTIAL_FILL`이 빠진다)과
  결정 항목 3개(범용 스키마 빌더 owner, 실행 설정 수치 범위 행 owner, 실행 설정 스키마의 서빙
  유스케이스)를 추가했다.
- 2026-09-21 — P2-03 1차 리뷰(`review_lang2_p2_03`, REQUEST_CHANGES, P2 2·P3 8) 반영. **P2 둘 다
  `_record_codec.py` 의 은퇴 row 읽기 같은 5줄이다.** (1) 저장된 **1.1** row 를 읽는 가지에 테스트가
  0건이었다 — 동결 fixture 가 전부 1.0 이라 그 가지를 통째로 `raise` 로 바꿔도 1544 passed 였다.
  실 DB 에 남은 은퇴 row 는 사실상 전부 1.1 이므로, 이 PR 이 지키겠다고 선언한 바로 그 버전이 회귀
  그물 밖이었다. `seed_retired_1_1_row` 와 `tests/contract/test_strategy_repository_retired_1_1.py`
  (복원·목록·이력·문서 조회)를 더해 같은 돌연변이가 이제 2건을 실패시킨다. (2) 미지
  `schema_version`(`"1.3"`·`"9.9"`) row 가 base 의 fail-closed 에서 **silent 현재 버전 해석**으로
  바뀌어 있었다 — `is_frozen_schema_version` 은 "현재가 아닌 모든 것"이라 집합이 열려 있고,
  `spec_hash` 검증은 변환 **전에** 끝나 키를 지우거나 의미를 바꾼 버전을 못 잡는다. domain 이
  `RETIRED_SCHEMA_VERSIONS`(닫힌 집합)와 `require_retired_schema_version` 을 소유하고 codec 이 그
  밖을 `UnknownSchemaVersionError` 로 거절하게 했다(P2-09 의 `FROZEN_SCHEMA_VERSIONS` 를 그만큼
  앞당긴 셈). P3 8건도 전부 닫았다: 422 코드 철자 주석, `run_environment.contract.*` i18n 이관(옛
  `strategy.contract.execution.*` dead key 삭제), 매뉴얼 고아 표 행, `form-projection.ts` 고아 주석,
  fixme 해제 지점을 P3-02 로 통일, 디버거 `start`/`end` non-null 복구를 P3-02 acceptance 로,
  업그레이드 진단 테스트를 중간 상태 정확한 모양으로 강화(P2-09 가 깨뜨리며 원복), 이 패킷 메타.
- 2026-09-21 — 브라우저 백테스트 fixme(당시 3건, 뒤에 디버거 기준선 시나리오가 더해져 4건) 의 되살리는 지점을 계약에 고정했다. 리드 판단: 요청에 `environment`만 앞당겨 싣는 shim은 기간·유니버스 값의 출처가 없어(스키마 기본값이 있을 수 없다) 제품 동작을 속이는 것이라 하지 않는다. WORKFLOW P3-02 acceptance에 "P2-03이 잠근 `test.fixme` 해제"를 넣고, 이 PR 제약사항에 4요소로 적었다.
- 2026-09-21 — P2-03 rebase(base `1dee07a`) 후속. P2-02 리뷰 후속이 넣은 sandbox 문서 폴백이
  1.2 에서 성립하지 않아 `DEFAULT_MISSING_POLICY` 한 상수로 정리했다(결정 6). `x-deprecated`
  표기 은퇴(결정 8)와 아키텍처 가드 범위 축소(결정 9)로 P2-02 미반영 P3 두 건도 닫았다.
  **브라우저 백테스트 경로가 P3-02 까지 죽는다**: 실행 기간·유니버스를 요청의 `environment` 로
  옮겼는데 그 값을 싣는 프론트 배선이 P3-02 실행 설정 패널이므로, UI 에서 시작한 run 은 422
  `backtest.run.environment_required` 로 거절된다. e2e 시나리오 3개(뒤에 디버거 기준선까지 4개: `creates, recovers, …
  backtests`, `edits the graph on real data …`, `upgrades a frozen 1.0 revision …`)를
  `test.fixme` 로 잠그고 되살릴 지점을 주석에 적었다. P2-09 머지 전까지 1.0·1.1 문서의 실행
  경로도 없다 — 스택을 한꺼번에 머지하면 main 에는 이 상태가 남지 않는다.
- 2026-09-21 — P2-03 구현. WORKFLOW 결정 항목 4건에 결론을 내고(위 Packet 결정 1~4), 실행 설정
  미지정을 코드화된 진단으로 거절하기로 정했다(결정 5). WORKFLOW 원문과 다르게 간 곳 2건(template()의
  시작 팩터, 422 코드 철자)도 같은 표에 적었다. 크기 분할은 하지 않았다 — WORKFLOW가 제시한 분할선
  (enum 이동을 뒤 PR로)을 적용해도 `StrategySpec`에서 필드 두 개를 지우는 순간 hydrate·schema·
  validation·explanation·compile·adapter·fixture·테스트가 같이 움직여야 컴파일되므로 상한 안에
  들어오지 않는다. 대신 커밋을 논리 단위로 쪼갰다. 1.1 → 1.2 내용 변환
  (`strip_retired_execution_settings`)을 domain에 두고 저장 row를 읽는 repository codec만 쓰게
  했다 — 없으면 은퇴 버전 row 조회가 P2-09까지 500이 된다. source 경로는 1.1에서 멈추므로
  `quality_momentum.v1_1.commented.yaml` 골든과 dict/source drift 검사는 그대로다.
- 2026-09-20 — P1-01 2차 리뷰 APPROVE(blocking 0). 중첩된 reveal 훅 둘이 서로 다른 요소를
  끌던 R2-1을 "마지막 매치"로 고치고, 같은 문제 행 재클릭 reveal(R2-2), 명시적
  `schemaLoaded`(R2-4), `scrollIntoView` 수신 요소 단언(R2-5)까지 반영했다.
- 2026-09-20 — P1-01 구현·1차 리뷰: PR #168. 문서 상태 배지와 `DiagnosticsPanel`을
  `SourceEditor` 밖 슬롯으로 올려 다섯 탭 모두에서 보이게 하고, 문제 행 클릭 목적지를
  `resolveDiagnosticDestination`이 판정한다. Graph 판정은 backend plan이 아니라 parse tree로
  한다(편집 표면은 plan 없이도 문서의 팩터를 그린다). `review_lang2_p1_01` APPROVE(blocking 0),
  후속으로 편집기 높이 충전·선택 카드 `scrollIntoView`·route 테스트 강화 5건을 반영했다.
- 2026-09-20 — P0-01 4차 리뷰 반영: P2-06·P2-07의 P1-03 교차 의존 선언(WORKFLOW 1절·Dependency 열), P2-06·
  P2-07 OpenAPI 재생성 사유 정정, active PR 문장을 도구 동작과 일치, P2-03 분할 시 PLAN 절차 참조.
- 2026-09-20 — 패키지 생성. 디자인보드 원인 분석(코드 감사 2건, 아이디어 5개 실험)과 제품 소유자
  결정(템플릿 배제, 실행 설정 분리, 언어 축소)을 spec과 Phase 0~6, 27 PR로 정리.
- 2026-09-20 — 개정: "기존 YAML은 남긴다, 표현은 YAML과 그래프 둘". `steps` 표기·섹션 재구성·
  expand-steps 엔드포인트 제거, schema 2.0 → 1.2(실행 설정 분리 + 추가 필드 3개). P2 7→5, P5 4→3,
  총 24 PR. Form·JSON 탭 은퇴를 P4-04에.
- 2026-09-20 — P0-01 상위 문서 개정: ADR 2026-09-04 머리말·D8에 개정 표기, 1.1 spec 머리말에 후속
  링크, 로드맵 M8 완료 게이트를 원문("코드를 몰라도 …")으로 복귀, SoT 정본 대장에 실행 설정·연산자
  정의·그래프 표현 투영 행 3개와 "표현은 YAML과 그래프 둘" 문장 추가.
- 2026-09-20 — P0-01 리뷰(`review_lang2_p0_01`, REQUEST_CHANGES) 반영: spec D2·D3·D4·D6·D7·D8·D9·D11과
  WORKFLOW·SoT를 실제 코드에 맞게 고쳤다. 최상위 필수 키 3→2개, 업그레이더를 버전 디스패치로,
  브리지를 `domain/backtest`로(application 순환 회피), 결측 정책을 `plan_hash`에 유지, 횡단면
  eligibility에 전용 enum·모집단·동점·2-pass 정의, 다중 입력 연산자의 잎 노드 규칙과 아이디어 3
  정본 형태, `risk_factor_id` 합성 제외·원시값 역가중, 연산자 18→23(`(kind, operator)` 키),
  `GET /api/v1/run-environments/schema` 신설, golden 파일 역할 분리. **P2를 5 → 9 PR로 재분할**(총
  24 → 28 PR), P3-01 base는 P2-09.
- 2026-09-20 — P0-01 2차 리뷰(REQUEST_CHANGES, P1 2·P2 3·P3 4) 반영. enum(`Market`·`DataFrequency`·
  `ExecutionTiming`) 이동을 P2-01에서 빼고 P2-03으로 미뤘다(호환 re-export가 domain 순환 + 경계
  규칙 위반). 합성 제외 후 알파 팩터가 0개면 `composite_score`가 `None → 0.0`으로 폴백돼
  `security_id` 사전순 상위 N이 조용히 선정되므로 compile error `strategy.signal.no_alpha_factor`를
  추가했다. 합성 제외를 `weighting: risk`로 한정하고 `FIELD_APPLICABILITY`에 `/risk/risk_factor_id`
  행을 넣었다. P2-06 수치 테스트의 기준선, P2-05의 `ExclusionReason`·trace 갱신, 아이디어 5 fixture
  두 벌, spec 6절·D2의 "잎 4개" 표현, 금지 절 업그레이드 문장의 갱신 주체(P2-09), P2-01
  `DEPENDS_ON` 중복 항목을 함께 고쳤다. PR 수는 그대로 28.
- 2026-09-20 — P0-01 3차 리뷰(REQUEST_CHANGES, P1 1·비차단 3) 반영. P2-03 enum 이동 항목에서
  `domain/strategy/facade/specification.py`를 소비자 목록에서 빼고 "import·`__all__` 삭제"로 바꿨다
  (재수출 지점을 `domain.backtest`로 돌리면 2차에서 막은 순환이 되살아난다).
  `application/strategy_design/_service.py`는 `template()`의 `DataStep` 생성과 함께 import가 사라져
  리다이렉트 대상이 아니고, 실제 대상은 `engine_portfolio/_adapter.py` 하나다(그 facade
  `DEPENDS_ON`에 `domain.backtest` 추가). `/risk/risk_factor_id`의 `FIELD_APPLICABILITY` 행은
  `owned_by_error` 없이 적용 조건만 갖고 배타는 별개 validator error라고 spec S6·P2-06에 못 박았다.
  P2-04·P2-05·P2-06에 OpenAPI 재생성 항목을 넣고, 같은 분류 착오가 걸리는 P2-02(`missing_policy`
  제거)·P2-07(카탈로그 `availability`)에도 같은 줄을 넣었다. P2-03 경로 목록에 `template()`과 12절
  재점검 문장을 추가했다. PR 수는 그대로 28.
- 2026-09-20 — P2-01 구현 중 규칙 개정: **OpenAPI를 바꾸는 backend PR은 생성 SDK 재생성 커밋을
  포함한다.** CI `frontend` job이 `npm run api:generate` 뒤
  `git diff --exit-code -- ../backend/openapi.json src/shared/api/generated`를 돌려서, 생성 파일을
  빼면 그 PR이 곧바로 빨간불이 된다. 기존 규칙("P2는 `openapi.json`만, SDK는 P3-01")을 WORKFLOW
  1절·P2-01~P2-07 acceptance·이 문서 현재 결정에서 교체했다. 커밋에는 생성 산출물만 넣고 소비자
  배선(실행 설정 패널·요청 본문 연결)은 P3-01 그대로다.
- 2026-09-20 — P2-01 잔여 2건을 P2-03으로 이관 기록. `adapters/outbound/engine_portfolio/
  _adapter.py:42`의 참여율 `PARTIAL_FILL` 판정과 `domain/portfolio/_compiler.py:249,259`의
  `execution_timing`이 아직 `spec.execution`을 읽는다. 둘 다 시그니처 변경(`assess`/`requirements`,
  `compile_target_tape`)과 facade `DEPENDS_ON`에 `domain.backtest` 추가가 필요해 P2-01 범위
  (WORKFLOW가 지정한 유스케이스 서비스 3파일) 밖이다. `execution` 제거가 강제하는 P2-03 acceptance에
  항목으로 넣었다. 그때까지는 명시 `environment`로 참여율·체결 시점을 바꿔도 엔진 호환성 판정과
  tape hash는 문서 값을 읽는다(`ExecutionTiming` 값이 하나뿐이라 tape hash 실효 차이는 없다).

## 관찰 backlog (담당 PR 예약)

리뷰어가 자기 스코프 밖에서 관찰한 결함. 관찰한 PR에서 고치지 않고 담당 PR에 붙인다. 담당 PR의
WORKFLOW acceptance에도 같은 BACKLOG 번호로 한 줄을 예약한다(착수자는 acceptance를 완료 조건으로
읽는다).

### BACKLOG-001: 12-1 모멘텀 시드의 `history`가 그래프가 요구하는 이력보다 21 세션 짧다

- **상황**: `domain/factor/_registry.py`의 팩터 카탈로그 시드와 같은 파일의 구현 그래프.
  `price.momentum_12_1` 시드는 `history=252`를 선언하는데, 그 팩터의 그래프는
  `TimeSeriesNode(window=252, lag=21)`이다. P1-03 2차 리뷰가 관찰했다(수정 범위 밖).
- **인풋**:
  1. `_implemented_graphs()["price.momentum_12_1"]`를 꺼낸다.
  2. `validate_factor_graph(graph, fields=(price.close numeric_series,))`를 부른다.
  3. `minimum_history_sessions`가 `273`으로 나온다. 시드가 선언한 값은 `252`다.
- **에러 위치**: `backend/src/strategy_workbench/domain/factor/_registry.py:154-162`(시드
  `history=252`) ↔ `:103-117`(그래프 `window=252`, `lag=21`). 계산 주체는
  `domain/factor/_validation.py`의 `history += node.window - 1 + node.lag`다.
- **위험성**: `history`는 필드 이력이 팩터 최소 이력보다 짧은지 판정하는 기준
  (`factor.graph.insufficient_history`)이자 카탈로그 표시 값이다. 21 세션 모자란 값을 선언하면
  워밍업이 짧은 데이터에서 그 진단이 뜨지 않은 채 앞 구간이 조용히 결측이 되거나 창이 덜 찬
  첫 값이 나간다. 백테스트는 통과하고 화면에도 경고가 없다 — silent 결측/잘못된 첫 값이다.
  12-1 모멘텀은 spec D1의 완료 정의 "퀀트 아이디어 5개"의 첫 번째라 노출이 크다.
- **재현 test**: 없음(관찰만). 담당 PR이 시드 ↔ 그래프 최소 이력 동치 테스트를 함께 둔다.
- **담당**: `P2-06`(`risk.risk_factor_id`·`saved_*` 제거로 레지스트리를 건드리는 PR).

### BACKLOG-002: 매뉴얼이 옛 영문 화면이고 초안 복구 뒤 되돌리기를 안내하지 않는다 (감사 N1·N2)

- **상황**: P1-03(화면 어휘)·P1-05(오류 문장)가 화면을 한글로 바꿨는데 매뉴얼 스크린샷은 P1
  범위에서 한 장도 다시 찍지 않았다. P1-02 2차 리뷰가 "초안 복구 직후 되돌리기는 복구 이전
  텍스트로 돌아간다"는 안내를 P1 스택 끝으로 넘겼는데 본문에 없다.
- **인풋**: `npm run docs:capture`를 돌리지 않은 채 매뉴얼을 따라 한다.
- **에러 위치**: `docs/manual/strategy-workbench/assets/*.png`(14장, 예 `04-structure-error.png`),
  `docs/manual/strategy-workbench/README.md`(8절에 되돌리기 한 줄뿐, 초안 복구 안내 없음).
- **위험성**: 매뉴얼이 실제와 다른 영문 오류·키 라벨을 보여 초보자가 따라 하다 막힌다. 동작
  결함이 아니라 문서 drift다.
- **담당**: `P3-03`(매뉴얼·README를 1.2로 고치는 PR — 1.2 화면으로 어차피 다시 찍어야 해 한 번에
  한다). 되돌리기 본문 문장도 같은 PR에서.

### BACKLOG-003: 횡단면 `zscore`·`rank`가 입력 단위를 물려줘 표준화한 두 팩터의 합이 거부된다 (감사 N3)

- **상황**: P1-03 연산자 레지스트리. `cross_sectional.zscore`·`rank`의 계산식 설명은 무차원 값을
  말하는데 `unit_rule`은 `SAME_AS_INPUT`이다. P1-03 1차 리뷰가 "별도 이슈"로 넘겼다.
- **인풋**:
  1. 필드 두 개 — `price.close`(단위 `KRW`), `valuation.pbr`(단위 `ratio`).
  2. 각각 `cross_sectional.zscore` 노드를 붙이고 `binary.add`로 더한다.
  3. `validate_factor_graph(graph, fields=(...))` → `valid=False`,
     `factor.graph.unit_mismatch 더하기/빼기 단위가 다릅니다: left='KRW' right='ratio'`
     (2026-09-26 probe, P1-05 tip `45f1c4a3`).
- **에러 위치**: `backend/src/strategy_workbench/domain/factor/_operators.py:260-262`(`RANK`)·
  `:269-271`(`ZSCORE`)의 `unit_rule=UnitRule.SAME_AS_INPUT` ↔ `domain/factor/_validation.py:456-463`의
  더하기/빼기 단위 비교(`factor.graph.unit_mismatch`, `:460`). P1-06 리뷰가 `rank`도 같다는 것과
  단위가 같으면 통과하는 대조군을 독립 재현했다.
- **위험성**: 표준화한 두 팩터를 한 그래프 안에서 합치는 교과서적 합성이 blocking error로
  거부된다(false rejection). 화면 설명과 판정이 어긋나고, 그래프 탭만으로 전략을 만드는 완료
  정의 경로에서 막힌다. `demean`·`winsorize`는 단위 보존이 맞아 대상이 아니다.
- **재현 test**: 없음(위 probe). 담당 PR이 `test_factor_operators.py`의 `unit_rule` 대조에 넣는다.
- **담당**: `P2-07`(단위 경고를 다루는 compile 단일 게이트 PR).

### BACKLOG-004: `/factors/preview` 422가 `factor.graph.*` 코드를 그대로 낸다 (감사 N4)

- **상황**: P1-05가 전략 문서 경로의 그래프 진단을 `strategy.expression.*`로 옮겼다. 팩터 연구
  preview 경로는 그 범위 밖이었다(P1-05 1차 리뷰가 backlog로 넘김).
- **인풋**: `POST /api/v1/factors/preview`에 검증을 통과하지 못하는 그래프(예: 순환).
- **에러 위치**: `backend/src/strategy_workbench/adapters/inbound/http_api/_app.py:644-662`의
  `preview_factor_graph`(422 본문 `:661`) — `InvalidFactorRequestError`를 `"code": "factor.graph.invalid"`와 이슈
  코드 `factor.graph.*`가 든 `validation`으로 그대로 422에 싣는다.
- **위험성**: 같은 결함이 경로에 따라 두 네임스페이스로 나가 frontend가 코드 → 마커 매핑을 두 벌
  가져야 하고, 사용자에게 내부 코드 문자열이 보인다. 지금은 frontend가 이 endpoint를 쓰지 않아
  노출은 없다.
- **담당**: `P5-03`(팩터 결과 미리보기), 조건부. WORKFLOW·spec 어디에도 이 endpoint의 소비자가
  정해져 있지 않다(P4-03 기준일 미리보기는 기존 trace API). 미리보기가 `/factors/preview`를 쓰면
  P5-03이 422 코드를 `strategy.expression.*`로 정리하고, 쓰지 않으면 P5-03이 이 항목의 담당을 다시
  정한다(WORKFLOW P5-03 acceptance에 예약).

### BACKLOG-005: 폭 640px 이하에서 IDE 뷰 탭이 사라진다 (감사 N5)

- **상황**: Strategy Workbench IDE. 설계 하한은 1280px이고 그 아래는 `@media (max-width: 1279px)`
  하나가 다룬다.
- **인풋**: 창 폭을 640px 이하로 줄인다.
- **에러 위치**: `frontend/src/widgets/strategy-ide/ui/strategy-ide.css` — 탭 줄 폭이 0이 되어 뷰
  탭이 보이지 않는다.
- **위험성**: 마우스로 뷰를 바꿀 수 없다(키보드 단축키로만). 설계 하한 밖이라 결함인지는 "좁은 폭
  지원 범위" 제품 결정에 달렸다.
- **담당**: `P4-04`(탭을 그래프·YAML 둘로 다시 짜는 PR). 지원 하한을 정하고 그 폭에서 탭이 보이는
  단언을 둔다.

### BACKLOG-006: 진단 reveal 순서가 훅 호출 순서와 "마지막 매치"에 기대고 있다 (감사 N10)

- **상황**: P1-01 1차 리뷰 P3가 "reveal 순서가 훅 호출 순서에만 고정 — 다음 PR이 같은 page에 훅을
  끼우거나 재배치하면 조용한 회귀"를 교차 PR 경고로 남겼고, 2차 R2-1이 중첩된 두 훅이 다른 요소를
  끄는 결함을 실제로 잡았다. 지금은 `use-reveal-selection.ts`가 가장 깊은 매치를 고르고
  `document-routes.test.tsx`의 "scrolls to the editor row, not the plan node" 테스트가 잠근다.
- **인풋**: Form 탭 은퇴·목록형 편집기 제거처럼 reveal 훅을 가진 컴포넌트를 없애거나 옮긴다.
- **에러 위치**: `frontend/src/features/edit-strategy/model/use-reveal-selection.ts`와 그 소비자
  (`StrategyFormPanel`·`FactorGraphPanel`·`FactorGraphEditor`).
- **위험성**: 문제 행을 눌렀을 때 plan 노드처럼 편집할 수 없는 요소로 스크롤이 가고, 테스트가
  함께 지워지면 아무 게이트도 잡지 않는다.
- **담당**: `P4-04`(Form 탭 은퇴로 훅 하나가 사라지는 첫 PR). 이후 `P6-03`(목록형 편집기 제거)도
  같은 테스트를 새 캔버스 기준으로 유지한다.

### BACKLOG-007: `document-routes.test.tsx`의 P6-03 키보드 테스트가 부하 중 간헐 실패한다

- **상황**: 이름의 "P6-03"은 이전 yaml-ui initiative 번호다. describe "professional keyboard
  workflow (P6-03)"의 "finds a JSON Pointer from a read-only view, returns to YAML and reveals its
  source". P1-04 4차 리뷰와 Phase 1 감사가 전체 실행 중 각 1회 관측했고 단독 실행은 통과한다.
- **인풋**: 머신 부하가 큰 상태에서 `npm test` 전체.
- **에러 위치**: `frontend/src/app/__tests__/document-routes.test.tsx` 해당 테스트의 선택 영역
  `waitFor` — 주석이 "reveal은 route 전환 뒤 비동기로 선택을 옮긴다, 부하 중에는 한 틱 늦는다"고
  이미 적는다.
- **위험성**: merge gate가 간헐 적색이 되어 재실행이 습관이 되면 진짜 회귀를 흘려보낸다.
- **담당**: `P4-04`(JSON 탭 은퇴로 "read-only view에서 YAML로 돌아가는" 이 경로를 다시 쓴다).
- **관련 우회**: 같은 현상(탭 전환 뒤 reveal이 한 틱 늦게 와 선택을 옮김)이 main 병합 중 브라우저 e2e에서도
  나왔다. `frontend/e2e/workbench-helpers.ts`의 `currentSource`가 전체 선택·복사를 두 번 연속 같은 값이
  나올 때까지 되풀이하게 해 막았다(`9a3e1fd0`). P4-04가 reveal 경로를 다시 쓸 때 이 우회를 걷을 수
  있는지 함께 본다. P4-04가 늦은 reveal을 고치면 이 우회(두 번 연속 같은 값까지 되풀이)도 함께 걷어내고
  한 번 읽기로 되돌린다.

### BACKLOG-008: 충돌 표식 게이트가 일부 추적 텍스트를 건너뛰고, 주석이 검출 범위를 과장한다

- **상황**: P1-02 `ac3a3d0e`의 `tools/quant_study_dev/conflict_markers.py`(CI backend step "No committed
  merge conflict markers"). 5차 리뷰가 APPROVE하면서 P3 4건을 남겼고, 테스트 수 정정을 뺀 3건이다.
- **인풋**:
  1. `backend/reference/sangmok/implementation/uv.lock`처럼 다른 CI step이 파싱하지 않는 `.lock` 파일에
     충돌 표식을 커밋한다.
  2. 추적 소스 안 `build/`·`dist/`·`target/`·`coverage/` 이름 디렉터리 아래 파일, 또는 cp949·UTF-16
     텍스트에 표식을 커밋한다.
  3. 로컬에 추적하지 않는 `.orig`·`.rej`가 있는 상태에서 게이트를 돌린다.
  4. 주석을 믿고 정확히 7자인 setext 밑줄(`=======`)을 쓴다.
- **에러 위치**: `tools/quant_study_dev/conflict_markers.py:16-17`(`MARKER` 위 주석), `SKIP_DIRECTORIES`·
  `iter_candidate_files`(`root.rglob("*")` 열거), `BINARY_SUFFIXES`의 `.lock`, UTF-8 디코딩 실패 시 건너뜀.
- **위험성**: 1·2는 표식이 조용히 통과한다(false negative). 지금 추적 파일 중 해당 경로·비 UTF-8은
  0개이고 `.lock` 4개 중 3개는 다른 step이 파싱해 깨지므로 실손은 reference `uv.lock` 하나다. 3은
  로컬만 적색(비추적 파일 오탐), 4는 CI에서 막히고 원인을 헤맨다(동작은 `git diff --check`와 같아
  안전). 로컬 Windows 순회가 약 9.6초로 느린 것도 `rglob` 때문이다.
- **재현 test**: 없음(리뷰 probe `cm_probe.py`). 담당 PR이 FN 5건(cp949·UTF-16·`.lock`·`build/`·
  `dist/`)을 단위 테스트로 넣는다.
- **담당**: `P3-03`(CI 전체 green을 exit로 가진 PR). `git ls-files -z`로 추적 파일만 열거하고 디렉터리
  제외를 걷으며, `.lock`을 제외에서 빼고, `errors="replace"`로 읽으며, 주석을 "7자가 아닌 밑줄은
  잡지 않는다. 정확히 7자인 단독 줄은 git도 충돌 표식으로 본다"로 좁힌다.

### BACKLOG-009: AI 제안을 적용한 뒤 툴바 "실행 취소"로 되돌리는 브라우저 e2e가 없다

- **상황**: AI B-04의 제안 적용과 P1-02의 탭 밖 되돌리기 버튼이 main 병합으로 한 화면에 모였다. SoT 편집
  이력 행은 제안 적용을 격리된 `replaceRange` 한 단계로 정하고, 단위 테스트
  (`document-routes.test.tsx`의 "AI 어시스턴트 제안 적용 (B-04)")가 편집기 `undo`로 그 사실을 본다.
  병합 리뷰가 P3로 남겼다.
- **인풋**: 브라우저에서 사이드바 제안 카드의 "문서에 적용"을 누른 뒤 툴바 "실행 취소" 버튼을 누른다.
- **에러 위치**: `frontend/e2e/assistant.workflow.spec.ts`·`frontend/e2e/stories/dm.ai-new-strategy.spec.ts`
  — 적용까지만 보고 버튼 되돌리기를 누르지 않는다.
- **위험성**: 버튼·단축키 배선(`useDocumentHistory` ↔ 제안 적용 훅)이 page에서 끊겨도 단위 테스트는
  편집기를 직접 부르므로 초록이다. 사용자는 적용을 한 번에 되돌리지 못한다(silent 회귀).
- **스토리**: US-DM-09(`예정`, e2e 담당 P3-03)에 수용 기준으로 잇는다.
- **담당**: `P3-03`(e2e fixture를 1.2로 바꾸며 어시스턴트 e2e도 다시 도는 PR). 스토리 e2e를 더하고
  US-DM-09를 `구현됨-e2e`로 올린다.

## 갱신 절차

1. PR row의 상태·Review 열과 `현재 작업 Packet`을 고친다.
2. `변경 기록`에 한 줄 남긴다.
3. `pwsh docs/planning/strategy-language-2-0/tools/update-plan-progress.ps1`을 실행한다(`-Check`는 검증만).
