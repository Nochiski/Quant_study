---
plan_version: 2
project: strategy-language-2-0
project_status: IN_REVIEW
current_phase: P1,P2
current_pr: P1-06,P2-01,P2-02
active_prs: [P1-06, P2-01, P2-02]
parallel_window: [P1-06, P2-01, P2-02]
last_updated: 2026-09-27T03:43:28+09:00
planned_prs: 29
merged_prs: 6
approved_prs: 7
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
| Current/next PR | `P1-06,P2-01,P2-02` |
| Active PR | `P1-06, P2-01, P2-02` |
| Progress | `6 / 29 merged (21%)` |
| Approved | `7 / 29` |
| Aggregated at | `2026-09-27 03:43 KST` |
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
| [ ] | `P2-03` | `data`·`execution` 제거, `CURRENT_SCHEMA_VERSION` 1.2, 필수 키 2개, fixture·hash golden | P2-02 | `WAITING` | — |
| [ ] | `P2-04` | `signal.normalization`과 결합 전 정규화 | P2-03 | `WAITING` | — |
| [ ] | `P2-05` | 횡단면 eligibility(전용 `EligibilityOperator`, exhaustive `_compare`, 2-pass) | P2-04 | `WAITING` | — |
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
