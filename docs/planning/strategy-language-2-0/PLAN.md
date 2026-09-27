---
plan_version: 2
project: strategy-language-2-0
project_status: IN_REVIEW
current_phase: P2
current_pr: P2-09,P2-10
active_prs: [P2-09, P2-10]
parallel_window: [P2-09, P2-10]
last_updated: 2026-09-27T12:30:40+09:00
planned_prs: 30
merged_prs: 9
integrated_prs: 6
approved_prs: 15
progress_percent: 50
---

# schema 1.2 · 그래프 표현 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_REVIEW` |
| Current phase | `P2` |
| Current/next PR | `P2-09,P2-10` |
| Active PR | `P2-09, P2-10` |
| Progress | `15 / 30 done (50%), main 9, integration 6` |
| Approved | `15 / 30` |
| Aggregated at | `2026-09-27 12:30 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. `[x]` 는 대상 브랜치에 머지된 PR 이다 — main 머지
(`MERGED`)와 lang2 통합 브랜치 머지(`INTEGRATED`, 아래 상태 값)를 따로 센다. frontmatter와 위 표, Phase 집계는
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
  **실행 방식(Phase 2 감사 DEFECT-P2X-001 기록)**: 묶음은 통합 브랜치 `lang2/integration` 에 모은다(main
  기반 `a4ccfd7a`). P2-03(#183)·P2-04(#184)·P2-05(#187)·P2-06(#200)은 APPROVE 상태로 통합 머지 커밋
  `c72f6257` 에 들어갔고, 원 PR 은 CLOSED 다 — GitHub 가 커밋 차이 없는 base 변경을 거부해 각 PR 에 사유
  댓글을 남기고 닫았다. P2-07(#201)부터는 PR 을 통합 브랜치 base 로 열어 머지한다(#201 `013da821`, #205
  `48eff6d8`). 통합 브랜치의 main 머지는 추적 draft PR #202 가 맡는다. 이 PR 들의 tracker 상태는 `INTEGRATED` 다.
- 2026-09-27 P2-07 주의(리뷰 DEFECT-P3-3): P2-07 부터 hydrate 가 boolean 팩터 출력에 승격 노드를
  붙인다. 그래서 P2-03~P2-06 판(통합 브랜치 포함)으로 저장한 1.2 revision 중 출력이 비교 노드인 팩터가
  있는 row 는 저장소 무결성 검사(canonical JSON 재생성 비교)에서 읽히지 않는다. **통합 브랜치로 로컬
  SQLite 를 쓴 적이 있으면 그 DB 를 초기화한다.** 위 "실 DB 1.2 저장 금지"를 지켰다면 해당 row 는 없다.
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
| `INTEGRATED` | 리뷰 APPROVE 뒤 `lang2/integration` 통합 브랜치에 머지됨. main 은 묶음 머지(draft #202) 대기. `[x]` 로 센다 |
| `MERGED` | 로컬 gate 후 main merge 완료 |
| `PAUSED` | 제품·계약 결정이 필요해 일시 정지 |

active PR 수와 병행 규칙은 [yaml-ui WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 그대로
따른다(재서술하지 않는다). 집계 도구는 PR row 상태에서 active를 뽑아 `parallel_window`와 일치하는지 검사한다.

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Main | Integrated | Status |
|---|---|---:|---:|---:|---|
| P0 | Planning package and contract docs | 1 | 1 | 0 | `MERGED` |
| P1 | In-screen friction removal on 1.1 | 6 | 6 | 0 | `MERGED` |
| P2 | Backend schema 1.2 (environment split, 10 PRs) | 10 | 2 | 6 | `IN_REVIEW` |
| P3 | Frontend 1.2 adaptation | 3 | 0 | 0 | `WAITING` |
| P4 | Graph level 1: pipeline | 4 | 0 | 0 | `WAITING` |
| P5 | Graph level 2: recipe | 3 | 0 | 0 | `WAITING` |
| P6 | Graph level 3: node canvas | 3 | 0 | 0 | `WAITING` |
| **Total** |  | **30** | **9** | **6** | **50%** |
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

| 항목 | 값 |
|---|---|
| PR | `P2-06` |
| Intent | 변동성 역가중(아이디어 5)을 언어가 표현하게 하고, 실행이 늘 거부하던 `saved_*` 노드를 문법에서 뺀다 |
| Acceptance | WORKFLOW P2-06 |
| Non-goals | 화면에서 `risk_factor_id` 를 고르는 흐름·추적 표시(P3-01), 아이디어 5 fixture(P2-08), 업그레이더의 `saved_*` 422(P2-09), `saved_*` 제거로 도달 불가가 된 frontend 잔재 정리(BACKLOG-012, P3-01) |
| Branch/worktree | `feat/lang2-p2-06-risk-factor` / `wt-lang2-p2-06` |
| Base SHA | `4262796f` (`feat/lang2-p2-05-eligibility` tip) |
| Head SHA | PR [#200](https://github.com/Nochiski/Quant_study/pull/200) 본문 참조(게이트 실측 SHA 와 같이 적는다) |
| Diff stat | 생성 산출물(OpenAPI·SDK·runtime schema·seed·어시스턴트 프롬프트)·기준선 PNG·문서를 뺀 handwritten **49파일 · +610 / −331**. 비테스트(backend src·frontend src·CSS·i18n·`FACTORS.md`) 24파일 +165/−193(그중 `saved_*` 제거 커밋이 +5/−158), test 25파일 +445/−138(신규 `test_risk_factor.py` 339줄, 골든 `spec_hash` 리터럴 7파일 각 1줄) |
| Focused tests | `uv run pytest tests/domain/test_risk_factor.py tests/domain/test_strategy_applicability.py tests/domain/test_factor_research.py tests/domain/test_strategy_schema.py tests/domain/test_strategy_diagnostic_messages.py tests/integration/test_truthful_pipeline.py -q` |
| 제약사항 | **12절 상한 초과(600줄·10파일 → 941줄·49파일), 분할하지 않는다.** WORKFLOW 가 두 변경(`risk_factor_id`·`saved_*` 제거)을 한 PR 로 묶었고, `saved_*` 제거가 노드 union·평가·계획·추적·검증·실행 거부·팩터 연구 요청까지 걸친 배관 삭제라 파일 수가 늘었다(src 14파일 −158줄). frontend 16파일은 생성 타입이 바뀌어 컴파일·테스트가 깨지는 곳만 고쳤고, 골든 `spec_hash` 리터럴 7파일은 필드 추가가 강제한 1줄씩이다. 커밋을 논리 단위 7개로 나눠 리뷰 단위를 대신한다. e2e 는 P2-03~P3-02 묶음 머지 전략의 두 원인으로만 red 다(PR 본문 대조표) |
| Full gate | PR 본문 "테스트 계획" 참조 — push tip 에서 backend 전체·ruff·pyright·계약 산출물 diff 0·frontend typecheck·typecheck:e2e·lint·test·build·`api:generate` diff 0·하네스 검사·충돌 표식·PLAN `-Check`·`npm run test:e2e` |

| 항목 | 값 |
|---|---|
| PR | `P2-07` |
| Intent | "검증 통과 = 실행 가능"(spec D5). 실행 직전에야 나던 판정(없는 필드·비수치 출력·그룹 연산 capability)을 compile 로 앞당기고, 정규화 없는 단위 혼합을 경고한다 |
| Acceptance | WORKFLOW P2-07 |
| Non-goals | 화면에서 승격 노드를 숨기거나 사람 말로 보이기(BACKLOG-014, P3-01), duckdb 그룹 필드 제공과 `group.rank` 단위(P2-08, BACKLOG-015), 단위 경고·출력 타입 문장의 frontend i18n(진단 문장은 backend 가 완성해 보낸다 — SoT 진단 코드 행), 횡단면 eligibility 가 모집단을 0 으로 만드는 쪽의 진단(P2-05 DEFECT-P3-1, 아래 변경 기록의 판단) |
| Branch/worktree | `feat/lang2-p2-07-compile-gate` / `wt-lang2-p2-07` |
| Base SHA | `lang2/integration` (`c72f6257`, P2-03~P2-06 통합 머지, main `a4ccfd7a` 기반). 착수는 P2-06 tip `36060527` 에서 했고, P2-06 문서 커밋 `dd1fbebd` 와 통합 브랜치를 차례로 merge 로 따라갔다 |
| Head SHA | PR [#201](https://github.com/Nochiski/Quant_study/pull/201) 본문 참조(게이트 실측 SHA 와 같이 적는다) |
| Diff stat | 생성 산출물(OpenAPI·SDK·연산자 카탈로그 fixture)·문서를 뺀 handwritten **24파일 · +1823 / −35**. 비테스트 backend src 15파일 +492/−31(신규 `_promotion.py` 87줄·`field_catalog.py` 21줄, `_validation.py` +225), test 9파일 +1331/−4(신규 4파일: compile 게이트 411·property 332·출력 타입 312·단위 경고 162줄, 나머지는 BACKLOG-003 76줄·duckdb 16줄·e2e 19줄과 기대값 3줄) |
| Focused tests | `uv run pytest tests/domain/test_factor_operators.py tests/domain/test_factor_output_gate.py tests/domain/test_signal_unit_mismatch.py tests/application/test_strategy_compile_gate.py tests/integration/test_compile_gate_property.py tests/integration/test_truthful_pipeline.py tests/test_adapters_equity_duckdb.py -q` |
| 제약사항 | **12절 상한 초과(600줄·10파일 → 1823줄·24파일), 분할하지 않는다.** WORKFLOW 가 compile 게이트 네 가지(필드 계약·출력 타입·단위 경고·capability)와 BACKLOG-003 을 한 PR 로 묶었고, 넷이 `validate_strategy` 의 같은 인자(`fields`)와 compile 서비스의 같은 port 를 공유해 따로 떼면 중간 PR 이 인자만 있고 쓰는 곳이 없다. 비테스트는 523줄로 상한 안이고 초과분은 신규 테스트 4파일(1217줄)이다 — 12절이 "test 는 구현과 같은 PR"이라 분리할 수 없다. 커밋을 논리 단위 11개로 나눠 리뷰 단위를 대신한다. e2e 는 P2-03~P3-02 묶음 머지 전략의 두 원인으로만 red 다(PR 본문 대조표) |
| Full gate | PR 본문 "테스트 계획" 참조 — push tip 에서 backend 전체·ruff·pyright·계약 산출물 diff·frontend typecheck·typecheck:e2e·lint·test·build·`api:generate` diff 0·하네스 검사·충돌 표식·PLAN `-Check`·`npm run test:e2e` |

| 항목 | 값 |
|---|---|
| PR | `P2-08` |
| Intent | 완료 정의 아이디어 5개를 backend 수준에서 닫고(hydrate·검증·미리보기), 실데이터 어댑터가 그룹 필드(`group_series`)를 줄 수 있는지 확인한다 |
| Acceptance | WORKFLOW P2-08 |
| Non-goals | 그래프 탭에서 아이디어를 만드는 e2e(P5-03), 빌더 쪽 node_id 생성 테스트(P5-01, 아래 결정 3), duckdb 그룹 필드 제공(원천 없음, 결정 1), 실데이터 섹터 상한 결함(이슈 #203, main 별도 수정), mock·duckdb 필드 계약 불일치(이슈 #207) |
| Branch/worktree | `feat/lang2-p2-08-ideas` / `wt-lang2-p2-08` |
| Base SHA | P2-07 tip `945fd081`. 착수는 `6e397a3b` 에서 했고 P2-07 의 통합 브랜치 merge 와 리뷰 반영을 merge 로 따라갔다 |
| Head SHA | PR [#205](https://github.com/Nochiski/Quant_study/pull/205) 본문 참조(게이트 실측 SHA 와 같이 적는다) |
| Diff stat | 생성 연산자 카탈로그 fixture 와 계획 문서(PLAN·WORKFLOW)를 뺀 handwritten **16파일 · +610 / −15**. backend 13파일 +597/−8(src 4파일 +73/−2 중 mock 필드 59줄, 신규 `test_idea_fixtures.py` 273줄, 아이디어 fixture 5파일), spec D2 1파일, 스토리 2파일 |
| Focused tests | `uv run pytest tests/integration/test_idea_fixtures.py tests/domain/test_factor_operators.py tests/integration/test_strategy_http_api.py tests/contract/test_raw_observation_port.py -q` |
| 제약사항 | **파일 수가 12절 상한(10)을 넘는다(16), 분할하지 않는다.** 아이디어 fixture 5개는 acceptance 가 한 묶음으로 요구하는 데이터 파일이고, 리드 결정(빌더 node_id 정본)이 fixture 와 spec 예시를 같이 고치라고 해서 문서가 늘었다. backend 는 +597 줄이다. e2e 는 P2-03~P3-02 묶음 머지 전략의 두 원인으로만 red 다(PR 본문 대조표) |
| Full gate | PR 본문 "테스트 계획" 참조 — push tip 에서 backend 전체·ruff·pyright·계약 산출물 diff·frontend typecheck·typecheck:e2e·lint·test·build·`api:generate` diff 0·하네스 검사·충돌 표식·PLAN `-Check`·`npm run test:e2e` |

| 항목 | 값 |
|---|---|
| PR | `P2-09` |
| Intent | 은퇴 버전(1.0·1.1) 문서가 현재 버전으로 오르는 경로를 연다. 버전 디스패치 업그레이더, 업그레이드 응답의 `environment`·`warnings`, 동결 row 를 같은 체인으로 읽기 |
| Acceptance | WORKFLOW P2-09 |
| Non-goals | 배너 문구·1.1 문서 배너·`environment` 로 실행 설정 채우기·warning 표시와 그 i18n(P3-02, WORKFLOW P3-02 에 예약), 실행 결과 캐시와 `run_fingerprint` 표기 버전(캐시를 처음 도입하는 PR), 실 DB 마이그레이션 |
| Branch/worktree | `feat/lang2-p2-09-upgrader` / `wt-lang2-p2-09` |
| Base SHA | `lang2/integration` (`48eff6d8`, P2-07·P2-08 머지). 착수는 P2-08 tip `dffc1d7b` 에서 했고 P2-08 리뷰 반영 `d99aa024` 와 통합 브랜치를 차례로 merge 로 따라갔다(두 번째 merge 는 트리 변화 없음) |
| Head SHA | [#217](https://github.com/Nochiski/Quant_study/pull/217) 본문 참조(게이트 실측 SHA 와 같이 적는다) |
| Diff stat | 생성 산출물(OpenAPI·SDK)·계획 문서를 뺀 handwritten **28파일 · +1911 / −526**. backend src 14파일 +913/−279(`_upgrade.py` 재작성 +435/−180, 신규 `domain/backtest/_retired.py` 227줄), test 10파일 +958/−226(신규 3: 실행 설정 변환 134·1.1 의미 보존 180·v1_2 golden 41줄), e2e·스토리 3파일 +36/−17, SoT 규칙 1파일 +4/−4 |
| Focused tests | `uv run pytest tests/domain/test_strategy_upgrade.py tests/domain/test_retired_run_environment.py tests/contract/test_document_upgrade_source.py tests/application/test_strategy_authoring_upgrade.py tests/integration/test_strategy_document_upgrade_http_api.py tests/integration/test_upgrade_preserves_1_1_meaning.py tests/contract/test_strategy_repository_retired_1_1.py tests/contract/test_strategy_repository_frozen_1_0.py -q` |
| 제약사항 | **12절 상한 초과(600줄·10파일 → 1911줄·28파일), 분할하지 않는다.** acceptance 가 "심볼 교체와 호출자 갱신을 같은 PR"로 묶었다 — facade 공개 심볼(`upgrade_document_1_0`·`is_legacy_document`·`RETIRED_SCHEMA_VERSIONS` 등)을 지우는 순간 source 경로 어댑터·repository codec·authoring 서비스와 그 테스트가 같이 움직여야 컴파일된다. 비테스트 src 는 913줄이고 그중 `_upgrade.py` 는 파일 재작성이라 diff 가 부풀었다(단계 맵·검증·결과 타입·실행 설정 읽기). 커밋을 논리 단위 8개로 나눠 리뷰 단위를 대신한다. e2e 는 P2-03~P3-02 묶음 머지 전략의 원인 (1)로만 red 이고 원인 (2)는 이 PR 이 해소한다(PR 본문 대조표) |
| Full gate | PR 본문 "테스트 계획" 참조 — push tip 에서 backend 전체·ruff·pyright·계약 산출물 diff·`database/tests`·frontend typecheck·typecheck:e2e·lint·test·build·`api:generate` diff 0·하네스 검사·충돌 표식·PLAN `-Check`·`npm run test:e2e` |

P2-09 결정 11건(WORKFLOW 원문이 비워 둔 곳과 원문 밖으로 나간 곳):

1. **단계 맵의 값은 이름 붙은 step 튜플이고, 목표 버전은 디스패처가 찍는다.** WORKFLOW 는
   `UPGRADE_STEPS: Mapping[str, tuple[UpgradeStep, ...]]` 라고 적지만 기존처럼 `(이름, step)` 쌍으로 둔다.
   순서 고정 테스트와 단계 실패 메시지가 이름을 읽는다. `schema_version` 을 찍던 step 은 단계 밖으로
   빼서 `apply_upgrade_steps` 가 단계마다 **그 단계의 목표 버전**(체인의 다음 키)을 찍는다. 단계의 검증
   조건은 같은 키의 `_STAGE_LEFTOVERS`(1.0: `legacy_shape_hints`, 1.1: 실행 설정 세 자리)이고, 남으면
   `NotUpgradeableDocumentError` 다(세 겹 `factors` 같은 입력이 1.1 로 찍힌 채 넘어가지 않는다).
2. **공개 API 는 `upgrade_document(tree, *, until=None) -> UpgradeOutcome(tree, source_version,
   environment, warnings)` 와 제자리 판 `apply_upgrade_steps` 둘이다.** 제자리 판은 source 경로(ruamel
   CST)가 쓴다. `until` 은 체인 중간에서 멈춰 중간 golden(`.v1_1.commented.yaml`)을 바이트로 고정한다.
   코덱 클래스의 `upgrade_source(..., until=None)` 도 같은 인자를 받지만 port 계약 밖의 선택 인자다.
3. **체인은 문서가 선언한 은퇴 버전에서 시작한다(버전 상한 BACKLOG-010, Phase 2 감사 NB-1 반영).**
   선언된 버전보다 앞선 단계는 타지 않는다. 버전 줄이 현재 판인데 1.0 모양이 섞인 문서는 업그레이드가
   아니라 제자리에서 고칠 구조 오류다 — 처음 구현은 이 문서를 1.0 단계부터 태웠고, 그러면 1.1 → 1.2
   단계가 `normalization: none` 을 조용히 넣어 1.2 기본값 `rank` 의 뜻을 바꾸고 문서에 없던 `/data/*` 를
   짚는 warning 3건을 냈다(감사 탐침 실측). 그래서 `structure.legacy_shape` 문장은 업그레이드를 시키지 않고
   frontend 배너도 이 코드에 반응하지 않는다(US-DM-06 수용 기준 개정). 선언된 은퇴 버전보다 앞선 모양이
   섞였거나(1.1 선언 + 1.0 키) 버전 줄이 없으면 거절한다. 모르는 버전은 닫힌 집합으로 거절한다 — 숫자
   비교(`"1.3" > "1.2"`)가 아니라서 `"0.9"`·`"draft"` 같은 손상 값도 거절한다. 따옴표 없는 `1.0`(YAML
   float)은 문자열로 맞춘다. `is_upgradeable_document` 와 디스패처가 같은 `_chain_start` 를 읽는다.
4. **`require_retired_schema_version`·`UnknownSchemaVersionError` 는 합치지 않고 지웠다.** repository
   codec 이 `upgrade_document` 를 부르면 체인이 모르는 버전을 `NotUpgradeableDocumentError` 로 거절해
   같은 fail-closed 가 된다. 메시지는 "neither current nor a known retired version" 을 유지해
   `test_strategy_repository_retired_1_1.py` 의 미지 버전 테스트가 **그대로** 통과한다.
5. **실행 설정 변환의 owner 는 `domain/backtest` 다.** 업그레이더는 떼어 낸 원문 값을
   `RetiredExecutionSettings` 로 돌려주기만 한다 — `domain.strategy → domain.backtest` 화살표는 기존 반대
   방향과 순환이 된다(P2-01 브리지와 같은 배치). `environment_from_retired_settings` 는 Result 값을 돌려주고
   (`.claude/rules/python.md`), 하나라도 읽히지 않으면 `environment` 전체를 비운다 — 일부만 채운 실행
   설정은 사용자가 지정한 적 없는 값을 사실처럼 보인다. 원문 필드 이름은 `data_section`·`execution_section`
   이다(`settings.data.x` 는 `test_run_environment_ownership.py` 의 옛 섹션 읽기 가드에 걸린다).
6. **팩터별 결측 정책 충돌은 spec 대로 첫 값 + warning 이다.** P2-02 의 런타임 브리지는 같은 충돌을
   거부했지만(결정 2), 업그레이드는 사용자가 결과를 보고 실행 설정에서 고칠 수 있는 단계라 막을 이유가
   없다. warning 이 쓴 값·출처와 결측 처리가 바뀌는 팩터(`factor_id: 옛값->새값`)를 짚는다. 판정은
   **실효 값**으로 한다(P2-09 리뷰 DEFECT-P1-1): 결측 정책을 생략한 팩터는 1.1 기본값 `drop` 으로 센다.
   명시한 팩터만 세면 "a 생략 + b `zero`" 가 warning 없이 `zero` 가 되어 a 의 결측 처리가 조용히 바뀐다.
   1.1 기본값은 `_upgrade.py` 의 과거 사실 상수다(`DEFAULT_MISSING_POLICY` import 는 순환이다).
7. **`weighting: factor_score` 문서에는 warning 을 낸다**(`strategy_document.upgrade_weighting_rule_changed`).
   P2-04 결정 5 로 비중 규칙이 바뀌어 선정은 같아도 목표 비중이 1.1 과 다를 수 있다. 저장된 1.1 전략의
   동작 변화를 사용자에게 알리는 통로가 업그레이드 응답 warning 이다(P3-02 가 배너에 표시). "1.1 결과
   보존" 통합 테스트는 `equal` 비중으로 합성 점수를 1.1 공식과 대조한다.
8. **warning 코드는 닫힌 `Literal` 이다.** OpenAPI·생성 SDK 에 enum 으로 나가 화면이 코드별 문장을 붙일
   수 있고, 런타임 게이트(`UPGRADE_WARNING_CODES`)도 둔다. 실행 설정을 옮기지 못했다는 코드는
   application 이 내지만 어휘 owner 는 `_upgrade.py` 다.
9. **없던 `signal` 섹션은 모델 필드 순서 자리에 넣고, 이미 적힌 `normalization` 은 덮지 않는다.** 1.1
   템플릿에는 `signal` 이 없다. 자리는 `StrategySpec` 필드 순서에서 읽는다(`factors` 뒤). 1.1 문서에
   `normalization` 이 있으면 1.1 에서도 구조 오류였던 문서라 작성자 값을 둔다.
10. **source 경로 주석 규칙을 두 경우로 넓혔다.** (a) 섹션을 통째로 지울 때(`data`·`execution`) 섹션 끝
    아래 주석(다음 키 설명)은 섹션 안 **가장 깊은 마지막 키** 슬롯에 있으므로 거기서 읽고, 앞 키가 컨테이너면
    그 가장 깊은 마지막 키 뒤로 옮긴다. (b) step 이 새 키를 넣으면 앞 키의 꼬리 주석을 새 키 뒤로 옮긴다 —
    안 옮기면 `portfolio` 를 설명하던 주석이 새 `signal:` 위에 붙는다. 두 규칙은 돌연변이(로직 무력화)에서
    각각 2·4건 red 로 확인했다. `_finish_emptied_sections` 는 체인 끝에서 `signal` 이 다시 채워져 중간
    단계(`until`)에서만 닿지만, 규칙이 일반적이고 다음 단계가 섹션을 비울 수 있어 남긴다.
11. **`saved_*` 노드가 있는 저장 row 는 무결성 오류로 멈춘다.** 업그레이드 API 는 422
    `strategy_document.upgrade_unsupported_node`(pointer 포함)이고, repository codec 은 같은 예외가
    `StrategyRepositoryStorageError` 가 된다 — 조용히 지운 spec 을 보이면 저장한 적 없는 그래프가 그
    revision 의 사실로 뜬다. P2-06 이후에도 같은 row 는 hydrate 실패로 읽히지 않았으니 새 회귀는 아니다.

| 항목 | 값 |
|---|---|
| PR | `P2-10` |
| Intent | Phase 2 감사(2026-09-27)의 BLOCKING 2건(DEFECT-P2X-001 PLAN 기록, DEFECT-P2X-002 수정주가 전환 담당)을 닫고, 감사가 넘긴 P3 계약 누락·비차단 항목·BACKLOG-018 을 담당 PR acceptance 에 예약한다 |
| Acceptance | WORKFLOW P2-10 |
| Non-goals | 코드 변경(감사 NB-1 은 P2-09 `17c68261`, 리뷰 DEFECT-P1-1 은 `a32ed9d7`). 감사 비차단 항목과 BACKLOG-018 은 담당만 정하고 실행하지 않는다 |
| Branch/worktree | `docs/lang2-p2-10-phase2-records` / `wt-lang2-p2-10` |
| Base SHA | P2-09 `17c68261`(착수), 리뷰 DEFECT-P1-1 반영 `a32ed9d7` 을 merge 로 따라갔다 |
| 변경 파일 | `docs/planning/strategy-language-2-0/PLAN.md`, `WORKFLOW.md`, `tools/update-plan-progress.ps1` |
| Focused tests | `tools/update-plan-progress.ps1 -Check`, `uv run --locked python -m quant_study_dev.user_story_trace`, `uv run --locked python -m quant_study_dev.conflict_markers` |
| Full gate | 문서와 집계 도구만 바뀐다. 코드 게이트는 base `17c68261` 의 결과(P2-09 PR 본문)와 같다 |

P2-10 결정 2건:

1. **통합 브랜치 머지는 새 상태 `INTEGRATED` 로 센다.** `[x]` 는 대상 브랜치(main 또는 `lang2/integration`)에
   머지된 PR 이고, 집계는 main 머지와 통합 머지를 따로 낸다(frontmatter `merged_prs`·`integrated_prs`, Phase 표
   `Main`·`Integrated` 열). `APPROVED` 에 비고만 다는 안은 `-Check` 가 머지 수를 여전히 0 으로 내서 감사가
   짚은 증상이 남는다. `MERGED` 로 세는 안은 main 에 없는 변경을 main 머지로 읽게 한다. 프로젝트 `COMPLETE` 는
   main 머지로만 판정한다. READY 판정은 의존 PR 이 `MERGED` 또는 `INTEGRATED` 면 충족이다(이후 lang2 PR 은
   통합 브랜치를 base 로 연다). 도구 파일에 한글 주석을 넣으며 UTF-8 BOM 을 붙였다 — Windows PowerShell 5.1 은
   BOM 없는 스크립트를 ANSI 로 읽는다. 출력 문자열은 ASCII 로 둔다.
2. **승격 노드 식별 수단은 backend wire 표식이다**(P3-01 예약, 감사 8절 #13). frontend 가 `__promote_` 접두사를
   손으로 적으면 `PROMOTION_NODE_PREFIX`(backend `_promotion.py`)의 이중 owner 가 된다. 리드 결정이다.

P2-08 결정 6건(WORKFLOW 원문이 비워 둔 곳과 원문 밖으로 나간 곳):

1. **`GROUP_SERIES` 는 `unsupported` 로 남긴다.** 원장(`~/quant-ledger/data/equity`, 스냅샷
   `b5f7f8c286fe6d45`)을 조사했다. `dataset_profile` 의 `classification.sector` 는 `corp.induty_code`
   (KSIC 현재값, `point_in_time=false`, evidence "과거 시점 업종 시계열이 없어(WICS 보류)")다. WICS
   `sector_snapshot` 테이블은 있지만 행이 2026-09-18 스냅샷 하나(2460 티커, `available_date` 같은 날)뿐이라
   과거 세션에서는 셀이 없다. 어댑터 패널 코어는 값을 `float` 로만 나른다(`_Observed.value`). 그룹 필드를
   내려면 문자열 값 경로와 두 포트 계약 테스트, equity 쪽 FIELD_MAP·게이트 판정(`classification.sector`
   "굽지 않기로 한 2")을 함께 바꿔야 하는데, 그래도 과거 구간은 전부 결측이라 그룹 연산이 "가용"이 되는
   순간 모든 실데이터 백테스트에서 빈 값이 된다. 선행 조건은 월별 WICS 백필(`database/docs/WICS_PROBE.md`
   7-5절, 라이선스 go/no-go 는 사용자 결정)이다. P2-07 의 capability 분기(`strategy.operator.unsupported`,
   카탈로그 `availability: unsupported`)가 그대로 남는다.
2. **mock 어댑터에 `financial.net_income`·`price.trading_value` 를 더했다(WORKFLOW 밖).** 아이디어 2·4 가
   읽는 필드다. P2-07 부터 compile 이 연결된 어댑터의 필드 계약을 읽으므로 mock 에 없으면 저장 전
   `strategy.expression.field_missing`·`strategy.field.missing` 이고, P5-03 e2e(mock backend)가 백테스트에
   닿지 못한다. 단위·값 타입은 duckdb 선언과 같게 두었고 `test_idea_fixtures.py` 가 아이디어가 쓰는 필드마다
   두 어댑터 계약을 대조한다. 기존 필드 값은 그대로라 mock `snapshot_id` 는 올리지 않았다 — 캐시 키에는
   spec hash(필드 id 포함)가 들어가 새 필드 질의가 옛 결과와 섞일 수 없다. 대안(ROE 를 mock 에 있는 필드로
   바꾸기)은 아이디어의 의미를 바꾸므로 버렸다.
3. **node_id 는 빌더 규칙이 정본이다(리드 결정, 2026-09-27).** spec D2 본문(빌더가 `<operator>_<n>` 으로
   짓는다)과 아이디어 3 예시(`ma20`·`breakout`)가 어긋났다. 사용자가 빌더로 만든 문서가 실제 산출물이고
   node_id 는 `spec_hash` 에 들어가므로 빌더 규칙을 정본으로 하고 fixture·spec 예시를 고쳤다. 규칙은
   기존 `suggestNodeId` 와 같다: 바탕 이름을 그대로 쓰고 같은 그래프 안에서 겹치면 `_2`·`_3` … 을 붙인다.
   바탕 이름은 단계가 연산자(`mean`·`gt`·`momentum`·`std`·`divide`), 잎이 필드 id 끝 조각(`close`·`close_2`)
   이다. 팩터 그래프마다 이름 공간이 따로라 변동성 팩터의 수익률 단계도 `momentum` 이다.
   `test_idea_fixtures.py::test_node_ids_follow_the_recipe_builder_naming` 이 fixture 쪽을, P5-01
   acceptance 에 예약한 테스트가 빌더 쪽을 고정한다. 잎 바탕 이름(필드 id 끝 조각)은 spec D2 가 적지
   않던 부분이라 같은 커밋에서 spec D2 에 적었다.
4. **아이디어 4·5 의 빈칸을 채웠다.** spec 5절은 이름만 적는다. 아이디어 4 는 `price.trading_value`
   `top_percent 0.2` 유니버스 조건 + 60 세션 모멘텀, 아이디어 5 는 12-1 모멘텀(알파) + 일간 수익률
   (`momentum window 2`)의 60 세션 표준편차(변동성, `direction: low`) 두 벌이고 `weighting: risk`,
   `max_name_weight: 0.1` 이다. 변동성 팩터 하나만 두면 `strategy.signal.no_alpha_factor` 다.
5. **BACKLOG-015: `group.rank` 를 무차원으로 바꿨다.** 그룹 안 순위는 `cross_sectional_rank` 와 같은 백분위
   공식이라 입력 단위가 남지 않는다. 카탈로그 `unit_rule` 과 검증기 추론을 같이 바꾸고,
   `group.neutralize` 는 입력 단위를 지키는 대조 테스트를 두었다. 실데이터 그룹 필드는 없지만 mock
   (`classification.sector`)과 도메인 검증으로 재현된다. OpenAPI diff 0, 연산자 카탈로그 fixture 만 바뀐다.
6. **체인 형태 검사는 fixture 가드다.** `test_idea_fixtures.py` 가 spec D2 체인 규칙으로 fixture 를
   확인하지만 판정의 owner 는 frontend `recipe-projection.ts`(P5-01)다. backend 제품 코드에는 체인 판정을
   두지 않았다. 미리보기는 CI 에서 mock(9 세션이라 리밸런싱 프레임 0)으로 경로 전체를 태우고, 실데이터는
   수동으로 한 번 확인했다(변경 기록).

P2-07 결정 11건(WORKFLOW 원문이 비워 둔 곳과 원문 밖으로 나간 곳):

1. **승격은 새 노드 종류 없이 조건 노드 하나와 상수 둘이다.** hydrate(`domain/strategy/_promotion.py`)가
   boolean 출력 그래프 끝에 `__promote_<factor>_one`(1.0)·`_zero`(0.0) 상수와 조건 노드
   `__promote_<factor>`(predicate = 원래 출력)를 붙이고 출력을 그 조건 노드로 옮긴다. WORKFLOW 는
   "승격 노드"만 적었다. 새 kind 를 만들면 문법(스키마 union)에 들어가 사용자가 쓸 수 있는 노드가 되고,
   평가·계획·추적·연산자 카탈로그를 다 고쳐야 한다. 결측은 조건 노드 규칙대로 None 이다.
2. **조건 노드 추론을 고쳤다(WORKFLOW 밖).** 가지가 둘 다 scalar 이고 조건이 boolean 시계열이면 출력은
   숫자 시계열이다. 이전 추론은 가지 타입을 물려줘 승격 노드가 scalar 가 되었고, 사용자가 직접 쓴
   `조건 ? 1 : 0` 팩터도 실행 경계에서 scalar 출력으로 거부됐다. 조건이 boolean 이 아니면(오류 문서)
   올리지 않는다.
3. **예약 id 가 이미 쓰였으면 승격하지 않는다.** 중복 노드를 만들면 원인과 무관한 `duplicate_node` 가
   문서에 없는 자리를 가리킨다. 대신 출력이 boolean 으로 남고 `strategy.factor.output_type` 문장이 예약
   id 를 말한다. 승격 뒤 출력은 숫자라 canonical payload 를 다시 hydrate 해도 또 붙지 않는다(저장소
   무결성 검사가 canonical JSON 을 다시 만들어 비교한다).
4. **출력 타입 코드는 하나다.** 실행 경계의 `_reject_non_numeric_factor_outputs` 도
   `strategy.factor.output_type` 을 내고 `strategy.expression.output_type` 을 레지스트리에서 뺐다. 한
   사실에 두 코드가 있으면 JSON spec 경로(scalar 는 validator, group 은 방어 검사)가 같은 문제를 두
   코드로 답한다. scalar 는 어댑터 없이도 error 이고, group 은 필드 계약이 있어야 보인다.
5. **필드 계약 port 는 `strategy_authoring` 이 소유한다**(`FieldCatalogPort.factor_field_catalog`).
   `factor_research` 의 `FactorMetadataPort` 에 메서드를 더하면 그 port 를 구현한 테스트 가짜 5개가
   깨지고 `strategy_authoring → factor_research` 화살표가 새로 생긴다. 어댑터는 새 메서드에서
   `resolve_factor_fields` 를 그대로 불러 실행 경로와 같은 변환을 거친다. 계약은 매 compile 마다 읽는다.
6. **capability 는 어댑터 필드 계약의 값 타입 집합이다.** kind → 요구 필드 타입 표
   (`_REQUIRED_FIELD_VALUE_TYPES`, 그룹 연산 → `group_series`)와 판정 함수 `operator_availability` 를
   연산자 레지스트리가 소유하고 compile 진단과 카탈로그 응답이 같이 읽는다. mock 어댑터는
   `classification.sector` 를 그룹 필드로 주므로 mock 위에서는 그룹 연산이 **available** 로 바뀐다
   (duckdb 는 unsupported). 어댑터가 없으면 아무 필드도 없는 것으로 보아 정의 시점 값(unsupported)이다.
   카탈로그 해시가 가용성을 덮어 어댑터가 바뀌면 ETag 가 바뀐다. 진단 분류는 `capability` 이고, 같은
   노드의 `group_field_missing`·`group_field_type` 은 같은 원인이라 내지 않는다.
7. **저장소 무결성 검사는 어댑터 없는 compile 로 돌린다.** 그 검사가 필드 계약을 읽으면 어댑터를
   바꾸거나(mock ↔ duckdb) 필드가 빠진 순간 저장된 revision 을 읽지 못해 목록·이력이 500 이 된다.
   bootstrap 이 무결성 resolver 에 어댑터 없는 authoring 서비스를 따로 준다(회귀 테스트 있음).
8. **단위 경고는 합성에 들어가는 팩터만 본다.** `composite_factors`(역가중 리스크 팩터 제외)의 출력
   단위 중 모르는 값(`unknown`, 필드 계약 없음)을 뺀 것이 둘 이상이면 `signal.normalization` 을
   가리키는 warning 이다. 필드 계약을 받으면 그래프 안 더하기·빼기의 단위 오류(`unit_mismatch`)도
   compile 에서 난다 — 실행 플랜이 원래 같은 계약으로 내던 오류다.
9. **BACKLOG-003 은 횡단면 `rank`·`zscore` 만 바꿨다.** `UnitRule.DIMENSIONLESS`(`"1"`)를 더하고 추론도
   같게 했다. 그룹 안 순위 `group.rank` 도 같은 성격이지만 acceptance 밖이고 그룹 필드를 실제로 주는
   P2-08 이 확인할 수 있어 BACKLOG-015 로 넘겼다.
10. **새 `strategy.*` 코드 3개**(`strategy.factor.output_type`·`strategy.operator.unsupported`·
   `strategy.signal.unit_mismatch`)는 `SEMANTIC_ONLY_CODES` 에 등록했다. 진단 코드 문자열은 OpenAPI 에
   열거되지 않는다. OpenAPI diff 는 `UnitRule` 의 새 값과 설명 두 줄이다.
11. **그래프 밖 필드 참조도 compile 이 판정한다(리뷰 조기 알림 P1).** WORKFLOW 는 `field_missing`
   만 적어 그래프 노드만 덮었다. eligibility 규칙·유동성·레짐·리스크 필드 네 자리는 검사를 받지 않아
   오타가 진단 0건으로 통과하고 미리보기 422 `portfolio.data.unavailable` 이 됐다. 대상은 모델 metadata
   `catalog: equity-field`(runtime schema `x-catalog` 의 원천)에서 걸어 찾고(목록을 따로 적지 않는다),
   없는 필드는 `strategy.field.missing`, 숫자 시계열이 아닌 필드는 `strategy.field.value_type` 이다(네
   자리 모두 값을 숫자로 읽는다). 적용 조건과 무관하게 검사한다 — 참조 무결성 규칙(P2-06 결정 2)과
   같다. 새 코드 둘도 `SEMANTIC_ONLY_CODES` 에 등록했다.

P2-06 결정 8건(WORKFLOW 원문이 비워 둔 곳과 원문 밖으로 나간 곳):

1. **제외 판정은 `domain/strategy/_models.py` 의 `inverse_risk_factor_id`·`composite_factors` 두 함수가
   소유한다.** 컴파일러(가중 합·분모·정규화 모집단·역가중 값), 검증(제외 warning·알파 0개 error),
   설명(신호 단계 팩터 수)이 같은 함수를 읽는다. 컴파일러 안에 판정을 두고 검증이 같은 조건을 다시
   적으면 "모드별로 읽히는 필드" 조건이 두 곳에서 갈릴 수 있다. SoT 합성 공식 행에 적었다.
2. **`strategy.risk.risk_factor_missing` error 를 더했다(WORKFLOW 밖).** `risk_factor_id` 가 문서에 없는
   팩터를 가리키면, 막지 않을 때 합성에는 아무 영향이 없고 선정 종목 전부가 역가중 값을 못 읽어
   `MISSING_RISK` 로 빠진다 — 경고 없이 빈 포트폴리오가 된다. 노드·파라미터 참조가 모두 `*_missing`
   error 인 것과 같은 규칙이다. `weighting` 과 무관하게 검사한다(문서 참조 무결성).
3. **`risk_source_conflict` 도 `weighting` 과 무관한 error 다.** 원천이 둘인 문서는 `risk` 로 바꾸는
   순간 어느 쪽을 읽을지 정할 수 없다. 적용 조건 warning 은 `FIELD_APPLICABILITY` 행이 따로 낸다
   (`owned_by_error` 없음, WORKFLOW 그대로).
4. **`strategy.risk.risk_field` 는 "필드나 팩터 중 하나"로 넓혔다.** 새 코드를 만들지 않은 이유는
   관계가 같기 때문이다(`weighting: risk` 에 역가중 원천이 없다).
5. **역가중 팩터 값의 결측·공개일 초과도 `MISSING_RISK` 다.** WORKFLOW 는 `<= 0` 만 적는다. 결측을
   알파 결측(`MISSING_FACTOR`)으로 두면 역가중 팩터가 선정을 흔들어 "선정은 같고 비중만 다르다"는
   수치 계약이 깨진다. 추적의 팩터 기여 목록에도 역가중 팩터는 나오지 않는다(기여 합 = 합성 점수).
6. **`saved_*` 배관을 와이어까지 지웠다.** 노드·스키마 외에 팩터 연구 요청의 `factor_ids`·
   `subgraph_ids`, 실행 계획의 `referenced_factor_ids`·`referenced_subgraph_ids`, 추적 상태
   `reference_missing`, 평가 입력 `FactorObservation.references` 가 두 노드만을 위해 있었다. 남기면
   아무것도 검사하지 않는 입력과 늘 비어 있는 응답이 계약에 남는다. 연산자 카탈로그에는 처음부터
   두 노드의 행이 없어(연산자 없는 kind) 바뀐 것이 없다.
7. **BACKLOG-001 은 파생 대신 단언이다.** 카탈로그 50개 중 43개는 그래프가 없는 `catalog_only` 라 시드
   `history` 가 유일한 출처다. 구현 팩터만 그래프에서 파생하면 한 표 안에 두 규칙이 섞인다. 시드를
   273 으로 고치고, 구현 팩터 7개 전부의 카탈로그 값이 그래프 검증 결과와 같은지 테스트가 대조한다.
8. **`factors` 에 `x-defines: factor` 를 달았다(WORKFLOW 는 `x-reference: factor` 만 적는다).**
   참조 후보는 가장 가까운 `x-defines` 배열에서 읽으므로(`referenceCandidates`) 정의 배열이 없으면
   화면이 고를 후보가 없다. frontend 는 Form 네임스페이스 목록에 `factor` 를 더했고(runtime schema 값
   집합과 같아야 한다는 테스트), 팩터 삭제 가드는 원래 `*_factor_id` 를 참조로 본다.

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
| [x] | `P1-06` | Phase 1 감사 후속(문서): PLAN 진행 기록 정정, SoT 하한·e2e 잠금 행, 이월 항목 담당 지정 | P1-05 | `MERGED` | [#191](https://github.com/Nochiski/Quant_study/pull/191) · `review_lang2_p1_06` 2차 APPROVE(`909ae273`, 1차 REQUEST_CHANGES P2 2·P3 5 반영) · 추가분(main 병합 cascade, US-DM-06 스토리 e2e, 병합 리뷰 P3 반영)은 병합 cascade 리뷰 APPROVE · main 머지 `0cfb4959`(#191, 2026-09-26) |

Phase exit:

- [x] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과. (Phase 1 감사 4절 (a) PASS — CI #188 `browser-e2e` 25/25)
- [x] 노드 property·kind·연산자 설명 커버리지 100%. (감사 4절 (b) PASS — 발행은 생성기 구조로, 소비는 `screen-vocabulary.test.ts`로 고정)
- [x] SoT·책임분리 점검 서브에이전트 blocking 0. (감사 2026-09-21 BLOCKING 2 — DEFECT-P1X-001은 `ac3a3d0e`+cascade rebase, DEFECT-P1X-002는 P1-06이 해소. `ac3a3d0e`는 5차 APPROVE, P0-01~P1-06 전부 main 머지 — P1-06 `0cfb4959`)

## P2 — backend schema 1.2

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P2-01` | `RunEnvironment` 모델·브리지(`domain/backtest`), 실행 요청 optional `environment`, manifest·캐시 키, `/run-environments/schema` | P0-01 | `MERGED` | [#172](https://github.com/Nochiski/Quant_study/pull/172) · `review_lang2_p2_01` 1차 REQUEST_CHANGES(P0 1·P1 1·P2 4·P3 3) → 2차 APPROVE · main 병합 cascade 리뷰 2회 APPROVE(`review_lang2_p2_merge_0102`·`_r2`) · main 머지 `2d08157a`(#172, 2026-09-26) |
| [x] | `P2-02` | `graph.missing_policy` 제거 → `environment.missing`(plan 인자, `plan_hash` 유지) | P2-01 | `MERGED` | [#176](https://github.com/Nochiski/Quant_study/pull/176) · `review_lang2_p2_02` 1차 REQUEST_CHANGES(P1 1·P2 3·P3 3) → 2차 APPROVE · main 병합 cascade 리뷰 2회 APPROVE · main 머지 `1fb099f7`(#176, 2026-09-26) |
| [x] | `P2-03` | `data`·`execution` 제거, `CURRENT_SCHEMA_VERSION` 1.2, 필수 키 2개, fixture·hash golden | P2-02 | `INTEGRATED` | [#183](https://github.com/Nochiski/Quant_study/pull/183) · `review_lang2_p2_03` 1차 REQUEST_CHANGES(P2 2·P3 8) → 2차 APPROVE · 통합 머지 `c72f6257`(`lang2/integration`, 2026-09-27). 원 PR 은 CLOSED — GitHub 가 커밋 차이 없는 base 변경을 거부해 사유 댓글과 함께 닫았다(현재 결정 묶음 머지 항목) |
| [x] | `P2-04` | `signal.normalization`과 결합 전 정규화 | P2-03 | `INTEGRATED` | [#184](https://github.com/Nochiski/Quant_study/pull/184) · 1~4차 REQUEST_CHANGES → 5차 APPROVE → 6차 APPROVE(R5 단조성 반영 `71d6655d` 확인) · 통합 머지 `c72f6257`(`lang2/integration`, 2026-09-27). 원 PR 은 CLOSED — GitHub 가 커밋 차이 없는 base 변경을 거부해 사유 댓글과 함께 닫았다(현재 결정 묶음 머지 항목) |
| [x] | `P2-05` | 횡단면 eligibility(전용 `EligibilityOperator`, exhaustive `_compare`, 2-pass) | P2-04 | `INTEGRATED` | [#187](https://github.com/Nochiski/Quant_study/pull/187) · 1차 REQUEST_CHANGES(P2 2·P3 2) → 2~6차 APPROVE · 통합 머지 `c72f6257`(`lang2/integration`, 2026-09-27). 원 PR 은 CLOSED — GitHub 가 커밋 차이 없는 base 변경을 거부해 사유 댓글과 함께 닫았다(현재 결정 묶음 머지 항목) |
| [x] | `P2-06` | `risk.risk_factor_id`(합성 제외·원시값 역가중), `saved_*` 제거 | P2-05, P1-03 | `INTEGRATED` | [#200](https://github.com/Nochiski/Quant_study/pull/200) · `review_lang2_p2_06` 1차 APPROVE(blocking 0·P2 1·P3 4, 전부 반영 `77bd713c`·`7b707660`) · 통합 머지 `c72f6257`(`lang2/integration`, 2026-09-27). 원 PR 은 CLOSED — GitHub 가 커밋 차이 없는 base 변경을 거부해 사유 댓글과 함께 닫았다(현재 결정 묶음 머지 항목) |
| [x] | `P2-07` | compile 단일 게이트: `field_missing`, boolean 승격, 단위 경고, 연산자 unsupported | P2-06, P1-03 | `INTEGRATED` | [#201](https://github.com/Nochiski/Quant_study/pull/201) · `review_lang2_p2_07` 1차 REQUEST_CHANGES(P1 1: 그래프 밖 필드 참조가 필드 계약 검사를 빠져나감·P3 3) → 2차 APPROVE(tip `945fd081`) · 통합 머지 `013da821`(#201, base `lang2/integration`, 2026-09-27) |
| [x] | `P2-08` | duckdb `GROUP_SERIES` 스파이크, `ideas/*.yaml` 5개(레시피 산출 형태) | P2-07 | `INTEGRATED` | [#205](https://github.com/Nochiski/Quant_study/pull/205) · 1차 REQUEST_CHANGES(P1 1 DEFECT-P208-001·P2 1·P3 3) → 2차 APPROVE(`d99aa024`, 새 관찰 P2-NEW-1 원주가 오염 → 이슈 #214 → BACKLOG-017) · 통합 머지 `48eff6d8`(#205, 2026-09-27) |
| [ ] | `P2-09` | 1.1 → 1.2 업그레이더(버전 디스패치), upgrade 응답 `environment`, 동결 읽기, OpenAPI | P2-08 | `IN_REVIEW` | [#217](https://github.com/Nochiski/Quant_study/pull/217) · 워크트리 `wt-lang2-p2-09`, 브랜치 `feat/lang2-p2-09-upgrader` · base `lang2/integration`(처음 연 #216 은 P2-08 브랜치 삭제로 닫혀 #217 로 다시 열었다) · `review_lang2_p2_09` 1차 REQUEST_CHANGES(P1 1: 결측 정책을 생략한 팩터를 충돌 판정에서 뺀다) → 반영 `a32ed9d7`, 재확인 대기 · Phase 2 감사 NB-1(현재 판 문서 업그레이드) 수정 `17c68261` |

| [ ] | `P2-10` | Phase 2 감사 후속(문서): PLAN 머지·통합·리뷰 기록 정정, `INTEGRATED` 상태, BACKLOG-017, P3 계약 누락 예약 | P2-09 | `IN_REVIEW` | [#222](https://github.com/Nochiski/Quant_study/pull/222) · 워크트리 `wt-lang2-p2-10`, 브랜치 `docs/lang2-p2-10-phase2-records` · base P2-09(`17c68261` 에서 착수, `a32ed9d7` 을 merge 로 따라감) |

Phase exit:

- [x] 1.2 fixture 같은 hash, 1.1 fixture 전부 업그레이드 통과. (P2-09 tip 실측: 1.0·1.1 golden 을 올린 두 결과의 `spec_hash` 가 같고 golden 에 `normalization: none` 을 명시한 1.2 문서와 같다 — `test_a_1_1_document_upgrades_to_the_same_meaning_as_the_1_0_golden`, e2e US-SM-07. 1.0 원문·1.0 commented·1.1 원문·1.1 commented(중간 golden)·canonical 1.0 JSON 이 전부 현재 버전으로 오른다)
- [x] compile 통과 문서가 preview에서 422 없음(property). (P2-07 `test_compile_gate_property.py`, P2-09 tip backend 전체에서 통과)
- [x] `plan_hash`가 결측 정책으로 계속 갈린다(P2-02 회귀). (`test_factor_missing_policy.py`, P2-09 tip 통과)
- [x] `ideas/*.yaml` 5개 backend 통과. (P2-08 `test_idea_fixtures.py`, P2-09 tip 통과)
- [ ] SoT·책임분리 점검 blocking 0, SoT "실행 설정" 행 채움·업그레이드 행 예약 해제. (SoT 두 행은 P2-09 가 고쳤다 — 실행 설정 행에 업그레이드 응답 `environment` 경로, 업그레이드 행 예약 표기 해제, 금지 절 "1.0 → 1.1 → 1.2". Phase 2 감사(2026-09-27, `e064d2af`)는 FAIL — BLOCKING 2건 DEFECT-P2X-001·002 는 둘 다 기록 결함이고 P2-10 이 닫는다. 코드·계약은 통과. 이 PR 의 재감사 통과 뒤 체크한다)

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
| `P2-01`·`P2-02` main 병합 | `review_lang2_p1_06` | 병합 cascade 1 | `APPROVE` | `review_lang2_p2_merge_0102.md`. #172 merge `87de0fec`(P0-01 main 반영판)·#176 merge `3f4dce21`. blocking·P3 0. #193 진행 보고·취소 checkpoint 보존, P2-02 `missing` 이관 의미 불변, own-diff 되돌림 0 |
| `P2-01`·`P2-02` main 병합 | `review_lang2_p1_06` | 병합 cascade 2 | `APPROVE` | `review_lang2_p2_merge_0102_r2.md`. #172 merge `0913f49b`(main `0cfb4959` = P0·P1 전체)·#176 merge `2ad24f3e`. blocking 0. P1·P2 상호 되돌림 없음, `DEPENDS_ON` 합집합 비순환(돌연변이로 두 원소 필요 확인) |
| `P2-03` | `review_lang2_p2_03` | 1 | `REQUEST_CHANGES` | P2 2·P3 8. P2 둘 다 `_record_codec.py` 의 은퇴 row 읽기 5줄: 1.1 row 테스트 0건(그 가지를 `raise` 로 바꿔도 초록), 미지 `schema_version` 이 fail-closed 에서 silent 현재 버전 해석으로 바뀜 |
| `P2-03` | `review_lang2_p2_03` | 2 | `APPROVE` | 돌연변이 재실행 2 failed 확인, P3-07 이탈 타당 |
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
| `P2-04` | `review_lang2_p2_0405` | 6 | `APPROVE` | `review_lang2_p2_0405_r6.md`, tip `35089901`. R5-P204-001(P3) 닫힘 — `_margin_strengths` 의 `below` 조건 `<` → `<=` 한 줄. 단조성 퍼즈 20,000회 위반 0 |
| `P2-05` | `review_lang2_p2_0405` | 6 | `APPROVE` | 같은 파일, tip `803e49c2`. P2-04 R5 반영 위 재배치, 코드 변경분 동일 |
| `P2-06` | `review_lang2_p2_06` | 1 | `APPROVE` | blocking 0 · P2 1 · P3 4. acceptance 전 항목 충족, 돌연변이 11종 중 10종 red(M6 정규화 모집단 변형은 동치). **P2-1**: `_risk_value` 의 공개일 조건을 지워도 테스트가 전부 초록 — 역가중 팩터가 합성 루프 밖이라 그 한 줄이 유일한 look-ahead 가드다. 반영: 공개일 초과 → `MISSING_RISK`·선정 불변 테스트, 그 조건만 지우면 1건 red 확인(`77bd713c`). **P3**: form-list 삭제 가드 fixture 의 `saved_factor` → `risk.risk_factor_id` 참조, `risk_factor_id` 설명에 적용 조건, `strategy.risk.risk_field` 메시지에 값(`77bd713c`·`7b707660`), PR 본문 e2e 합계에 "9 did not run" |
| `P2-07` | `review_lang2_p2_07` | 1 | `REQUEST_CHANGES` | P1 1: 그래프 밖 필드 참조(eligibility·유동성·레짐·리스크)가 compile 필드 계약 검사를 빠져나간다. P3 3 |
| `P2-07` | `review_lang2_p2_07` | 2 | `APPROVE` | 같은 파일 10절, tip `945fd081`(`bf07bc11`·`97193304`·`f8b052cc`). P1 1·P3 3 닫힘, 새 결함 없음 |
| `P2-08` | `review_lang2_p1_06` | 1 | `REQUEST_CHANGES` | P1 1 · P2 1 · P3 3 · 관찰 1. **DEFECT-P208-001(P1)**: 아이디어 2 에 자본총계 ≤ 0 조건이 없어 자본잠식 적자 기업이 PBR·ROE 두 팩터 모두 최상위(원장 실측 28개) → `financial.book_equity gt 0` 규칙 + mock 자본잠식 종목 재현 테스트(수정 전 red). **DEFECT-P208-002(P2)**: 0/1 이진 팩터 선정이 `security_id` 순서 → 리드 결정으로 BACKLOG-016(P5-03). P3: spec D2 옛 문장 정리, P5-01 슬롯·삽입 순서 예약, 아이디어 4 당일 거래대금 한계 주석. 관찰(assistant DB 격리 누락)은 AI 계획 몫이라 이 PR 밖 |
| `P2-08` | `review_lang2_p1_06` | 2 | `APPROVE` | `review_lang2_p2_08_r2.md`, tip `d99aa024`. DEFECT-P208-001 red→green (아이디어 2 eligibility). 새 관찰 **P2-NEW-1**: 가격 변화 아이디어 1·3·4·5 가 원주가 `price.close` 를 쓴다 → 이슈 #214 → BACKLOG-017 |
| `P2-09` | `review_lang2_p2_09` | 1 | `REQUEST_CHANGES` | tip `e064d2af`. P1 1: 결측 정책을 생략한 팩터를 충돌 판정에서 빼서 1.1 의 팩터별 결측 처리가 경고 없이 바뀐다. 비차단 관찰 2. 반영 `a32ed9d7`(실효 값 판정, 관찰 1 float 1.1 테스트). 같은 시점 Phase 2 감사 NB-1 은 `17c68261` 이 고쳤다 |
## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| `P1-03` | cascade tip `748bc62f`: backend `pytest -q`·`ruff check src tests examples scripts`·`pyright`, 루트 tools unittest·ruff·pyright, 충돌 표식 검사, frontend `api:generate` 후 생성물 diff·`typecheck`·`typecheck:e2e`·`lint`·`npm test` | pytest 1579 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 674/674 | 2026-09-26 |
| `P1-04` | cascade tip `e6fb10b0`, 위와 같은 게이트 | pytest 1611 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 704/704 | 2026-09-26 |
| `P1-05` | cascade tip `45f1c4a3`, 위와 같은 게이트 + `npm run test:e2e`(머신 잠금 아래) | pytest 1695 passed · ruff·pyright 0 · tools 13 OK · 표식 0 · SDK diff 0 · Vitest 733/733 · e2e 25/25 | 2026-09-26 |
| `P1-06` | `tools/update-plan-progress.ps1 -Check`, `uv run --locked python -m quant_study_dev.conflict_markers` | `PLAN.md is consistent: 0/29 merged, 6 approved`(P1-02 5차 APPROVE 반영 뒤. 1차 리뷰 반영 시점에는 P1-02가 `ac3a3d0e` 리뷰 대기라 5) · 충돌 표식 0 | 2026-09-26 |
| `P2-10` | `update-plan-progress.ps1 -Check`, `user_story_trace`, `conflict_markers` | PR 본문 참조 | 2026-09-27 |
| `P2-09` | push tip 게이트 전체 — PR [#217](https://github.com/Nochiski/Quant_study/pull/217) 본문 "테스트 계획" | 결과·실패 원인 대조는 PR 본문(US-SM-07 은 업그레이드·저장·hash 단언을 모두 지나 원인 (2) 해소, 남은 실패는 원인 (1) 실행 요청 environment 미배선 하나) | 2026-09-27 |
| `P2-01` | `uv run pytest -q` (backend) | 1480 passed, 13 skipped | 2026-09-20 |
| `P2-01` | `uv run pytest -q` (backend, 리뷰 반영 후) | 1493 passed, 13 skipped | 2026-09-21 |
| `P2-01` | `uv run ruff check src tests` · `ruff format --check`(변경 30파일) | 통과 | 2026-09-20 |
| `P2-01` | `uv run pyright` | 4 errors — 전부 `duckdb` 미설치(기존), 신규 파일 0 | 2026-09-20 |
| `P2-01` | `npm run typecheck` · `lint` · `test` · `build` (frontend) | 통과, Vitest 639(57 파일) | 2026-09-20 |
## 변경 기록

- 2026-09-27 — P2-10 구현(`IN_REVIEW`, [#222](https://github.com/Nochiski/Quant_study/pull/222)). Phase 2 감사(`e064d2af`, FAIL — BLOCKING 2건 모두 기록)
  후속. (1) DEFECT-P2X-001: P1-06·P2-01·P2-02 를 main 머지(`0cfb4959`·`2d08157a`·`1fb099f7`)로, P2-03~P2-08
  을 통합 브랜치 머지(`c72f6257`·`013da821`·`48eff6d8`)로 고치고 새 상태 `INTEGRATED` 를 집계 도구와 상태 값
  절에 더했다. Review 기록에 빠진 10행을 넣고 표 중간 빈 줄 두 곳을 지웠다. 현재 결정의 묶음 머지 항목에
  통합 브랜치 실행 방식을 적었다. Phase 1 exit (c)를 체크했다(P1-06 머지). (2) DEFECT-P2X-002: BACKLOG-017
  (수정주가 전환, #212 링크)을 등록하고 P3-01 에 예약했다. (3) 감사 8절 P3 계약 17행을 acceptance 와 대조해
  누락 6건을 예약했다 — #3 sandbox `missing`(P3-02), #7 탈락 사유 i18n(P3-01, P4-03 규칙/순위 구분), #11 새
  진단 코드 화면 매핑(P3-01), #13 승격 노드 표식(P3-01, backend wire 표식으로 결정), #15 은퇴 버전 문구 6키
  (P3-02), #16 run 상세 `environment` 표시(P3-02). NB-7(P3-01 착수 전 main cascade)도 P3-01 에 적었다.
  감사 비차단 담당(리드 지시로 지정, WORKFLOW 각 acceptance 에 예약): NB-2(a) eligibility SoT 행 → P3-01,
  NB-2(b) `run_environment.*` 진단 코드 SoT 행 → P3-02, NB-3 spec D6·D7 구현 결과 단락 → P3-03, NB-4(a) 매니페스트
  평면 비용 필드 유지·제거 → P3-02, NB-6 단위 규칙 SoT 문장 → P3-03. **NB-4(b) `run_fingerprint` 표기 버전은
  lang2 범위 밖(리드 결정)**: 담당은 캐시 lookup 을 도입하는 PR(미계획)이고, 그 PR 의 acceptance 에서 표기
  버전을 정한다. 이 필드는 캐시 lookup 이 있어야 의미가 있는데 lang2 계획(P3~P6)에는 그 PR 이 없다.
  NB-2(c)는 P2-09 `17c68261` 이 테스트로 닫았다. BACKLOG-018(원주가 시계열 변화 경고, 리드 결정)을 P3-01 에
  예약했다.
- 2026-09-27 — P2-09 Phase 2 감사 NB-1 반영(`17c68261`). 업그레이드 체인은 문서가 선언한 은퇴 버전에서만
  시작한다. 현재 판 문서의 1.0 모양은 제자리에서 고칠 구조 오류이고, frontend 배너가 이 경우에 뜨지 않는다
  (US-DM-06 수용 기준 개정). 자세한 것은 P2-09 결정 3.

- 2026-09-27 — P2-09 구현(`IN_REVIEW`, [#217](https://github.com/Nochiski/Quant_study/pull/217)). 업그레이더를 버전 디스패치로 다시 쓰고 1.1 → 1.2
  단계(`data`·`execution`·`graph.missing_policy` 떼기, `signal.normalization: none` 명시, `saved_*` 거절)를
  붙였다. 업그레이드 응답에 `environment`(옛 문서의 실행 설정)·`warnings` 를 싣고, 저장된 1.0·1.1 row 를
  같은 체인으로 읽는다. 저장된 1.1 row 의 설명 문장이 `rank` 로 나오던 P2-04 1차 리뷰 P3 관찰도 이로써
  닫힌다(복원 spec 이 `none`). `quality_momentum.v1_2.commented.yaml` 을 1.0 문서의 최종 기대 출력으로
  두고 세 단언(application·contract·HTTP)을 옮겼다. BACKLOG-010·011 처리. 결정 11건은 P2-09 패킷.
  e2e: 결과·실패 원인 대조는 PR 본문(US-SM-07 은 업그레이드·저장·hash 단언을 모두 지나 원인 (2) 해소, 남은 실패는 원인 (1) 실행 요청 environment 미배선 하나)
- 2026-09-27 — P2-01 이 P2-09 로 넘긴 `run_fingerprint` 표기 버전 결정은 다시 넘긴다. P2-09 는 실행 결과
  캐시를 도입하지 않는다(WORKFLOW P2-09 acceptance 에 없다). 캐시 lookup 이 없으면 옛 지문과의 잘못된
  히트가 불가능하므로, 지문 표기 버전은 캐시 lookup 을 처음 도입하는 PR 이 정한다.
- 2026-09-27 — WORKFLOW P3-02 에 P2-09 가 남긴 배너 소비 항목 4개를 예약했다: 사실과 달라진 배너 문구
  ("1.1로 업그레이드" 등), warning 코드 3종 i18n, 새 422 `strategy_document.upgrade_unsupported_node` 문장,
  `environment: null` 이면 패널을 채우지 않기.
- 2026-09-27 — P2-08 1차 리뷰(REQUEST_CHANGES) 반영. **DEFECT-P208-001(P1)**: 아이디어 2 에 유니버스 조건
  `financial.book_equity gt 0` 을 넣었다. 자본잠식이면 PBR 이 음수라 `low` 1위가 되고, 적자까지 겹치면 ROE 가
  음수/음수 = 양수라 두 팩터 모두 최상위다(리뷰 실측: 미리보기 기간 보통주 28개). mock 합성 구간의 세 번째
  종목(`sec-035420-1`)을 자본잠식 적자 기업으로 만들고, 규칙 없이 그 종목이 합성 1위로 선정됨(red)을 확인한 뒤
  규칙으로 빠지는지 단언하는 테스트를 넣었다. 나머지 아이디어 점검: 1·4(모멘텀)는 가격 비율이라 분모가 양수이고
  `momentum` 은 분모 0 을 결측으로 둔다. 3 은 나눗셈이 없다. 5 의 변동성은 표준편차라 음수가 없고 0 이하는
  `MISSING_RISK` 로 빠진다. 부호 함정은 아이디어 2 뿐이다. 관련 한계로 원장 당기순이익은 최신 공시가 분기면
  3개월, 사업보고서면 12개월 값이라 ROE 가 기간을 섞는다(fixture 주석). **DEFECT-P208-002(P2)** 는 리드 결정으로
  BACKLOG-016(담당 P5-03)에 기록만 했다. **P3**: spec D2 의 옛 `<operator>_<n>` 문장을 정리하고 "접미사 규칙만
  `suggestNodeId` 와 같다"로 좁혔다. 빌더의 부가 잎 삽입 위치·꼬리 슬롯 규칙을 P5-01 acceptance 에 예약했다.
  아이디어 4 가 당일 거래대금 한 값으로 자르는 한계를 fixture 주석에 적었다.
- 2026-09-27 — P2-08 리드 결정 반영. (1) node_id 는 spec D2 빌더 규칙이 정본이다. `ideas/*.yaml` 5개의
  단계 node_id 를 빌더 이름(`mean`·`gt`·`momentum`·`std`·`divide`)으로 바꾸고 spec D2 아이디어 3 예시와
  WORKFLOW P2-08 문장을 고쳤다. P5-01 acceptance 에 "빌더 node_id 생성 규칙이 ideas fixture 와 일치
  (테스트로 고정)"을 예약했다(결정 3). (2) 실데이터 미리보기 기간 2021-01-04 ~ 2022-12-29 는 홀드아웃
  기준(측정은 2020-03-19 이후, 규칙 정본은 미머지 PR #100) 안이다. 비중 합 0.3 은 이슈 #203 때문이다.
  (3) P2-07 의 그래프 밖 필드 판정(`bf07bc11`)을 merge 로 따라간 뒤, mock 두 필드를 뺀 상태로
  `test_idea_fixtures.py` 를 돌려 compile 이 `strategy.expression.field_missing`(`financial.net_income`,
  그래프 필드)과 `strategy.field.missing`(`price.trading_value`, eligibility 규칙)을 내는 것을 확인했다.
- 2026-09-27 — P2-08 구현(`IN_REVIEW`, [#205](https://github.com/Nochiski/Quant_study/pull/205)).
  `backend/tests/fixtures/strategy_documents/ideas/*.yaml` 5개(12-1 모멘텀·저PBR + 고ROE·20일 이평 돌파·거래대금
  상위 20%·변동성 역가중)를 레시피 빌더 산출 형태로 두고, mock 필드 계약으로 compile(진단 코드 집합까지)·
  실행 설정을 실은 미리보기·체인 형태·실데이터 어댑터 필드 계약 일치를 `test_idea_fixtures.py` 가 본다.
  mock 에 `financial.net_income`·`price.trading_value` 를 더했다. BACKLOG-015 처리. 실데이터 확인: duckdb
  어댑터, `krx.common-stock`, 2021-01-04 ~ 2022-12-29 로 다섯 아이디어 모두 compile 통과(변동성 역가중만 의도한
  warning `strategy.risk.risk_factor_excluded`)·미리보기 23 프레임·프레임당 목표 20종목·엔진 호환이었다.
- 2026-09-27 — P2-08 `GROUP_SERIES` 스파이크 결론: **duckdb 어댑터는 그룹 필드를 주지 않고 그룹 연산은
  `unsupported` 로 남긴다**(P2-07 분기 유지). 사유는 원장에 PIT 섹터 시계열이 없어서다. `classification.sector`
  는 KSIC 현재값 라벨(`dataset_profile.point_in_time=false`)이고 WICS `sector_snapshot` 은 2026-09-18 스냅샷
  하나뿐이다. 선행 조건은 월별 WICS 백필(WICS_PROBE 7-5절, 라이선스 결정 대기)이다. 자세한 근거는 P2-08 결정 1.
- 2026-09-27 — P2-08 관찰 두 건(이 PR 범위 밖, 담당 미정). (1) **실데이터에서 섹터 상한이 포트폴리오 전체를
  줄인다.** duckdb 어댑터는 `sector_id=None` 이라 `domain/portfolio/_compiler.py` 의 `_apply_sector_constraints`
  가 선정 종목 전부를 `"__unknown__"` 한 섹터로 묶고 기본 `max_sector_weight 0.3` 으로 스케일한다. 위 실데이터
  미리보기에서 23 프레임 모두 목표 비중 합이 0.3 이었다(종목당 0.05 × 0.3). 경고 없이 현금 70% 로 도는 silent
  wrong result 다. mock 은 종목마다 섹터가 달라 테스트가 못 잡는다. 리드가 이슈
  [#203](https://github.com/Nochiski/Quant_study/issues/203)으로 등록했고 1안(섹터를 모르는 종목은 섹터
  상한에서 빼고 경고)으로 main 에서 별도 수정한다. P2-08 은 고치지 않는다. (2) mock 과 duckdb 의 필드 계약이 두 필드에서 다르다 — `consensus.forward_eps` 단위(mock `KRW/share`,
  duckdb `KRW`), `credit.margin_balance` 단위·값 타입(mock `KRW`·amount, duckdb `shares`·count). mock 에서
  통과한 단위 판정이 실데이터에서 달라질 수 있다. 아이디어 fixture 는 이 두 필드를 쓰지 않는다.
  이슈 [#207](https://github.com/Nochiski/Quant_study/issues/207)로 등록됐다.

- 2026-09-27 — P2-07 구현(`IN_REVIEW`, [#201](https://github.com/Nochiski/Quant_study/pull/201)). compile 이
  연결된 어댑터의 필드 계약(`FieldCatalogPort`)으로 `field_missing`·그룹 연산 capability
  (`strategy.operator.unsupported`, 카탈로그 `availability` 같은 판정)를 내고, boolean 팩터 출력을
  hydrate 에서 0/1 로 승격하며, scalar·group 출력은 `strategy.factor.output_type`, 정규화 없는 단위
  혼합은 `strategy.signal.unit_mismatch` warning 이다. BACKLOG-003(횡단면 `rank`·`zscore` 무차원) 처리.
  compile 통과 문서가 미리보기 사전 검사에서 거부되지 않는다는 property 테스트를 두었다. US-CS-02 e2e 가
  분모 필드 오타를 compile 오류로 먼저 확인한다. 리뷰 조기 알림 P1(그래프 밖 필드 참조 네 자리의 오타가
  compile 을 통과)을 같은 PR 에서 닫았다(결정 11). 리뷰 P3 세 건 중 단위 경고 `unknown` 제외 테스트는
  더했고, semantic diff 표시는 BACKLOG-014 범위에 넣었으며(담당 P3-01, 수정 위치가 backend `_diff.py`
  여도 같은 PR), 옛 1.2 row 무결성은 현재 결정에 로컬 DB 초기화 주의로 적었다. 관찰 두 건을 BACKLOG-014(승격 노드 화면 표시, P3-01)와
  BACKLOG-015(`group.rank` 단위, P2-08)로 예약했다.
- 2026-09-27 — P2-07 판단: **P2-05 DEFECT-P3-1(횡단면 eligibility 가 모집단을 0 으로 자르는 쪽)은 이
  PR 에서 다루지 않는다.** compile 은 모집단 크기를 모른다 — 모집단은 실행 설정(유니버스·기간)과 데이터가
  정하므로 문서만으로는 `top_percent: 0.001` 이 0 이 되는지 판정할 수 없다. 프레임 warning 은 컴파일러의
  리밸런싱 경로를 바꾸는 실행 측 변경이라 compile 게이트 PR 범위 밖이고, 결과가 빈 포트폴리오라 조용히
  틀린 값을 내지 않는다(P3 그대로). 추적 화면이 규칙·순위 탈락을 구분해 세는 P4-03 미리보기 패널에서 빈
  결과의 이유가 보인다.
- 2026-09-27 — P2-06 1차 리뷰 APPROVE(blocking 0). 권고 P2-1(역가중 팩터 공개일 가드 회귀 테스트)과
  P3 4건을 반영해 `APPROVED`. 삭제 가드 fixture 를 `risk.risk_factor_id` 참조로 바꿔 BACKLOG-012 에 넣을
  항목은 생기지 않았다.

- 2026-09-27 — P2-01·P2-02 main 병합 리뷰 P3 반영(P2-06 PR 문서): 실행 설정 스키마 설명 키 7개의
  frontend 문장·커버리지 테스트 부재를 BACKLOG-013 으로 P3-02 acceptance 에 예약하고, US-SM-10 비고에
  "P3-02 가 설명 문장을 붙이면 `예정` 전환 검토"를 적었다.

- 2026-09-27 — P2-06 구현(`IN_REVIEW`, [#200](https://github.com/Nochiski/Quant_study/pull/200)). `risk.risk_factor_id`(`x-reference: factor`,
  `factors` 는 `x-defines: factor`)와 적용 조건 행, 검증 코드 4개(`risk_source_conflict`·
  `risk_factor_missing`·`risk_factor_excluded`·`no_alpha_factor`), 합성 제외와 원시값 역가중을 넣고
  `saved_*` 노드와 그 배관을 와이어까지 지웠다. BACKLOG-001 처리(시드 273 + 동치 테스트). P2 구현자
  관찰 두 건을 BACKLOG-010·011 로 P2-09 에, `saved_*` 제거로 남은 frontend 잔재를 BACKLOG-012 로 P3-01 에
  예약했다. 필드 추가로 `spec_hash` 가 바뀌어 골든 리터럴 7곳과 시각 기준선 4장을 갱신했다.

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
- **처리(P2-06)**: 시드를 273 으로 고치고 `tests/domain/test_factor_research.py::test_catalog_history_of_every_implemented_factor_is_its_graph_minimum` 이 구현 팩터 7개의 카탈로그 값과
  그래프 최소 이력을 대조한다(고치기 전 `{'price.momentum_12_1': (252, 273)}` 로 red). `FACTORS.md` 표도 273.

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

### BACKLOG-010: `is_upgradeable_document` docstring 이 이미 지난 조건을 들어 상한 추가를 미룬다

- **상황**: P1-05 가 업그레이드 판정을 "은퇴 버전이거나 본문이 옛 판 모양"으로 넓혔고, 상한이 없는
  이유를 docstring 에 적었다. P2-03 이 `CURRENT_SCHEMA_VERSION` 을 1.2 로 올린 뒤에도 문장이 남았다.
  P2 구현자(P2-06)가 관찰했다.
- **인풋**: `backend/src/strategy_workbench/domain/strategy/_upgrade.py` 의 `is_upgradeable_document`
  docstring 을 읽는다.
- **에러 위치**: `_upgrade.py:198-200` — "지금은 아는 버전이 1.0·1.1 둘뿐이라 … schema 1.2 가 들어오면".
- **위험성**: 1.2 는 이미 현재 버전이라 "미래 버전 + 옛 키 하나"가 1.1 로 강등되는 경로가 지금 열려
  있다(판정 자체의 상한은 WORKFLOW P2-09 기존 항목). docstring 이 조건을 미래형으로 적어 두면 읽는
  사람이 아직 안전하다고 오해한다(문서 부채, 동작 결함은 위 항목이 소유).
- **담당**: `P2-09`(버전 디스패치와 상한을 넣는 PR). WORKFLOW P2-09 에 같은 번호로 예약했다.
- **처리**: P2-09(`1bb76712`). 판정에 버전 상한(아는 버전 집합)을 두고 docstring 을 현재 조건으로 고쳤다.
  `test_an_unknown_version_line_is_never_downgraded_even_with_a_1_0_body` 가 `"1.3"`·`"2.0"`·`2.0`·`"0.9"`·
  `"draft"` 를 고정한다(P2-09 결정 3).

### BACKLOG-011: `structure.invalid_date` 는 schema 1.2 문서로 닿을 수 없는 코드다

- **상황**: P2-03 이 `data.start`·`end` 를 실행 설정으로 옮겨 `StrategySpec` 에 날짜 필드가 없다.
  `STRUCTURE_CODES` 와 hydrate 분기, golden 테스트(날짜 필드 하나짜리 가짜 모델)는 남아 있다. P2 구현자가
  관찰했다.
- **인풋**: 1.2 문서 어디에 어떤 값을 적어도 hydrate 가 `date` 타입 필드를 만나지 않는다.
- **에러 위치**: `backend/src/strategy_workbench/domain/strategy/_hydrate.py:49`(코드 목록)·`:496-511`
  (`tp is date` 분기), `backend/tests/domain/test_strategy_diagnostic_messages.py:412-441`(가짜 모델 golden).
- **위험성**: 동작 결함은 아니다(도달 불가). 다만 "코드마다 문장 golden 이 있다"는 게이트가 도달 불가
  코드를 위해 가짜 모델을 유지하게 만들어, 코드 목록이 실제 진단 집합보다 넓어 보인다(cleanup).
  업그레이더가 1.0·1.1 원문의 날짜를 이 분기로 읽는지 확인한 뒤 함께 지운다.
- **담당**: `P2-09`(은퇴 버전 원문을 다루는 업그레이더 PR). WORKFLOW P2-09 에 예약했다.
- **처리**: P2-09(`5cf065a7`). 업그레이더는 은퇴 문서의 날짜를 hydrate 가 아니라 `domain/backtest` 의
  `environment_from_retired_settings` 로 읽으므로 코드·분기·golden 을 함께 지웠다. 날짜 필드가 모델에
  돌아오면 hydrate 가 `unsupported hydrate type` 으로 멈추는지 테스트로 고정했다.

### BACKLOG-012: `saved_*` 제거로 도달할 수 없게 된 frontend 잔재

- **상황**: P2-06 이 `saved_factor`·`saved_subgraph` 를 노드 union 에서 빼면서 runtime schema 가 발행하던
  `x-catalog: factor`·`x-catalog: subgraph` 가 사라졌다. frontend 는 컴파일·테스트가 깨지는 곳만 고쳤다.
- **인풋**: 1.2 runtime schema 로 편집기를 띄운다. 어떤 필드도 `factor`·`subgraph` 카탈로그를 가리키지
  않는다.
- **에러 위치**: `frontend/src/features/edit-strategy/model/contract-inspector.ts:461-518`(팩터 카탈로그
  join)와 그 자원 로딩, `form-projection.ts:45`·`:144`(`CATALOGS` 의 `factor`·`subgraph`),
  `schema-assist.ts:129`·`:164`, `ui/strategy-form-panel.tsx:1065-1073`, `shared/config/messages.ts` 의
  `strategy.node.saved_*`·`strategy.field.node.factor_id`·`strategy.field.node.subgraph_id`·
  `assist.catalog.subgraph`(한국어·영어).
- **위험성**: 동작 결함은 아니다(도달 불가 분기·안 쓰는 번역). Contract Inspector 는 팩터 카탈로그를
  계속 불러오므로 쓰지 않는 요청이 하나 남는다(cleanup).
- **담당**: `P3-01`(frontend 1.2 적응, 소비자 배선 owner). WORKFLOW P3-01 에 예약했다.

### BACKLOG-013: 실행 설정 스키마의 설명 키 7개에 frontend 문장과 커버리지 테스트가 없다

- **상황**: P2-01 이 `GET /api/v1/run-environments/schema` 를 만들고, 스키마 빌더
  (`domain/strategy/_schema.py` 의 `dataclass_json_schema`)가 필드마다 `x-description-key` 를 붙인다.
  P2-01·P2-02 main 병합 리뷰가 P3 로 관찰했다(2026-09-27).
- **인풋**: 실행 설정 스키마를 받아 발행된 설명 키를 모은다 —
  `strategy.field.run_environment.{start,end,market,frequency,universe_id,timing,missing}` 7개와
  `run_environment.contract.{fee_bps,slippage_bps,participation_rate}` 3개.
- **에러 위치**: `frontend/src/shared/config/messages.ts` 에 뒤의 3개(제약 행 설명)만 있고 앞의 7개는
  한국어·영어 모두 없다. frontend 커버리지 테스트(`features/edit-strategy/__tests__/screen-vocabulary.test.ts`)는
  전략 runtime schema fixture 만 순회해 실행 설정 스키마의 키를 보지 않는다.
- **위험성**: 지금은 패널이 설명 키를 읽지 않아 화면 결함은 없다. P3-02 가 패널을 넓히며 키를 읽기
  시작하면 번역 없는 키 문자열이 화면에 그대로 찍히고, 게이트가 없어 조용히 통과한다(누락 번역).
- **스토리**: US-SM-10(실행 설정 용어 도움말, `미계획`) 비고에 이 예약을 적었다.
- **담당**: `P3-02`(실행 설정 패널 확장). WORKFLOW P3-02 에 같은 번호로 예약했다.

### BACKLOG-014: boolean 출력 승격 노드가 실행 계획·디버거에 문서 밖 노드로 보인다

- **상황**: P2-07 이 boolean 팩터 출력을 hydrate 에서 0/1 로 승격한다. 승격 노드(`__promote_<factor>`
  조건 노드와 상수 `_one`·`_zero`)는 compile 된 spec 에만 있고 사용자 문서에는 없다.
- **인풋**: 출력이 비교 노드인 팩터(아이디어 3, `gt(close_2, mean)`)를 편집기에서 compile 하고
  실행 계획 탭·디버거 노드 목록을 연다.
- **에러 위치**: `frontend/src/features/edit-strategy/model/use-execution-plans.ts:151-160`(compile spec 의
  `factor.graph` 로 설명 요청)과 `nodePointerById`(`:181-190`) — 승격 노드의 index 가 문서 노드 목록
  밖이라 pointer 가 문서에 없는 자리를 가리키고, `factor-graph-projection.ts:187` 이 kind 를 `unknown`
  으로 보인다. semantic diff 도 같다(P2-07 리뷰 DEFECT-P3-1): `backend/src/strategy_workbench/
  domain/strategy/_diff.py:42` `diff_strategy_specs` 가 canonical payload 를 위치로 비교해, 비교 출력
  팩터 끝에 사용자 노드 하나를 더하면 `/factors/N/graph/nodes/K..` 의 `__promote_*` 노드 kind·node_id·
  value 가 바뀐 것으로 나온다. frontend `semantic-diff-table.tsx`·`conflict-banner.tsx` 가 그 목록을
  그대로 보인다.
- **위험성**: 동작 결함은 아니다(계산·추적 값은 맞다). 사용자가 쓰지 않은 노드 셋이 `unknown` 으로 보여
  "내 그래프가 바뀌었나"로 읽히고, "소스 열기"가 문서에 없는 줄을 짚는다(표시 drift). diff 표와 충돌
  배너에는 사용자가 쓰지 않은 노드의 변경 여러 줄이 "의미 변경"으로 보인다. diff 자체는 해시가 보는
  것을 정확히 말하므로 계산 결함은 아니다.
- **담당**: `P3-01`(execution plan·debugger 를 1.2 에 맞추는 PR). WORKFLOW P3-01 에 예약했다.

### BACKLOG-015: 그룹 안 순위 `group.rank` 가 입력 단위를 물려준다

- **상황**: P2-07 이 BACKLOG-003 으로 횡단면 `rank`·`zscore` 를 무차원으로 바꿨다. `group.rank` 는
  acceptance 밖이라 그대로 `UnitRule.SAME_AS_INPUT` 이다.
- **인풋**: 그룹 필드를 주는 어댑터(mock `classification.sector`)에서 단위가 다른 두 필드에 각각
  `group.rank` 를 붙이고 `binary.add` 로 더한다.
- **에러 위치**: `backend/src/strategy_workbench/domain/factor/_operators.py` 의 `GroupOperator.RANK`
  정의(`unit_rule=UnitRule.SAME_AS_INPUT`)와 `domain/factor/_validation.py` 의 단위 추론(무차원 집합
  `_DIMENSIONLESS_SECTIONS` 는 횡단면 연산만 본다).
- **위험성**: 섹터 안 백분위 두 개의 합이 `factor.graph.unit_mismatch` 로 거부된다(false rejection,
  BACKLOG-003 과 같은 모양). 실데이터 어댑터가 그룹 필드를 주기 전에는 도달하지 않는다.
- **담당**: `P2-08`(duckdb `GROUP_SERIES` 스파이크). WORKFLOW P2-08 에 예약했다.
- **처리**: P2-08(`4b4ccfea`). `group.rank` 의 `unit_rule` 을 `DIMENSIONLESS` 로, 검증기 추론을 무차원으로
  바꿨다. 재현 테스트 `test_factor_operators.py::test_sector_ranks_of_fields_with_different_units_can_be_added`
  (P2-07 tip 에서 `factor.graph.unit_mismatch` 로 실패 확인)와 대조 `test_sector_neutralization_keeps_the_input_unit`.

### BACKLOG-016: 0/1 이진 팩터의 선정이 동점 해소 순서(`security_id`)로 정해진다 (P2-08 리뷰 DEFECT-P208-002)

- **상황**: 아이디어 3(20일 이평 돌파, `ideas/ma20_breakout.yaml`)은 WORKFLOW P2-08 이 정한 대로 비교 노드
  `gt(close_2, mean)` 의 참/거짓을 compile 이 0/1 로 승격한 점수다. `selection_count: 20`, 결합 전 정규화
  `rank`.
- **인풋**: 이평 위 종목이 20개를 넘는 평범한 기준일(실데이터에서 보통 수백 개)의 미리보기·백테스트.
- **에러 위치**: `backend/src/strategy_workbench/domain/factor/_statistics.py:8-19` `cross_sectional_rank` 가
  동점에 평균 순위를 주므로 1 인 종목이 전부 같은 점수다. `domain/portfolio/_compiler.py:422-425` 가
  `(-composite_score, security_id)` 로 정렬해 상위 20 을 자른다.
- **위험성**: "20일 이평 돌파" 전략의 실제 선정은 "이평 위 종목 중 종목코드가 가장 작은 20개"다. 결정적이라
  테스트는 통과하지만, 종목코드 순서(대체로 상장 연차)라는 의도하지 않은 요인이 수익률을 만들고 사용자는
  결과를 돌파 효과로 읽는다(silent 오해석). 아이디어 3 만이 아니라 0/1 이진 팩터 전반의 체계적 편향이다.
- **해결 후보**: (a) 보조 팩터로 동점을 해소한다(예: 이평 괴리율 같은 연속 점수를 2순위 키로). (b) 동점
  종목 전원을 균등 비중으로 담는다(`selection_count` 를 넘으면 규칙 필요).
- **담당**: `P5-03`(아이디어 e2e 를 확정할 때 해결 방식을 정한다). WORKFLOW P5-03 에 예약했다.

### BACKLOG-017: 가격 변화 아이디어 fixture 가 원주가 `price.close` 를 쓴다 (Phase 2 감사 DEFECT-P2X-002, 이슈 #214)

- **상황**: P2-08 2차 리뷰가 찾은 관찰 P2-NEW-1 이다. 리드가 이슈 #214 로 올려 결정했다 — 가격 변화를 재는
  계산(수익률·모멘텀·이평·변동성·돌파)은 수정주가 `price.adj_close` 를 쓰고, `price.close` 는 절대 가격이
  필요한 곳에 남긴다. main 쪽 레지스트리·mock 은 PR #218 이 옮겼다(main 머지 `28d13b69`, 2026-09-27). #214
  결정문은 "lang2 아이디어 fixture 는 통합 브랜치에서 따라간다(P5-03 hash 전에)"라고 넘겼고, #218 본문도
  그 일을 범위 밖으로 적었다. lang2 쪽 담당이 없었다.
- **인풋**: `backend/tests/fixtures/strategy_documents/ideas/` 의 `momentum_12_1`·`ma20_breakout`·
  `top_trading_value`·`inverse_volatility` 를 실데이터(duckdb 어댑터)로 미리보기·백테스트한다.
- **에러 위치**: 위 네 fixture 의 가격 잎 `field_id: price.close`(`grep -n field_id ideas/*.yaml`), 어댑터 필드
  계약 대조 `backend/tests/integration/test_idea_fixtures.py`.
- **위험성**: silent wrong result. 원장 실측으로 2020~2022년 가격계수가 5% 넘게 바뀐 사건이 380건이다. 50:1
  병합 종목은 12-1 모멘텀이 +4900% 로 1위가 되고, 무상증자·분할은 가짜 급락·가짜 변동성·이평 돌파 소거를
  만든다. 아이디어 5개는 initiative 완료 정의의 측정 대상이라 P5-01(빌더 산출물 = fixture node_id)·
  P5-03(e2e 산출물 hash)이 원주가 형태로 굳으면 대표 전략이 경고 없이 틀린 결과로 닫힌다.
- **관련**: 아이디어 2(저PBR + 고ROE)의 `financial.net_income` 은 분기 3개월·연간 12개월 값이 섞인다(이슈 #212,
  main 담당, OPEN). 이 BACKLOG 는 fixture 주석에 #212 링크만 단다. mock·duckdb 필드 계약 불일치 #207 도 main
  담당이다(Phase 2 감사 NB-5).
- **선행 조건**: #218 이 main 에 머지됐다(완료). 통합 브랜치가 main 을 따라가야 한다(WORKFLOW P3-01 착수 전
  cascade).
- **담당**: `P3-01`. WORKFLOW P3-01 acceptance 에 같은 번호로 예약했다. 늦어도 P5-01 착수 전이다.

### BACKLOG-018: 원주가 가격 필드로 시계열 변화를 재는 문서에 경고가 없다 (#218 후속, 리드 결정)

- **상황**: #218 이 기본 팩터 레지스트리를 `price.adj_close` 로 옮겼다(main `28d13b69`). 하지만 사용자가 이미
  쓴 전략 문서와 매뉴얼 샘플은 원주가 `price.close` 로 기간 수익률·이동평균·변동성·낙폭 같은 시계열 변화를
  계산한다. compile 은 이 조합을 알리지 않는다.
- **인풋**: `field: price.close` 잎을 `time_series` 의 `momentum`·`mean`·`std` 등 변화 연산자에 넣은 문서를
  compile·저장·실행한다(예: 1.2 golden `quality_momentum.yaml` 의 `mom_252`).
- **에러 위치**: compile 단일 게이트 `domain/strategy/_validation.py`(P2-07 이 필드 계약·출력 타입·단위 경고를 둔
  곳)에 해당 진단이 없다.
- **위험성**: silent wrong result. 실데이터의 분할·증자·병합 사건(2020~2022년 380건, #214)이 가짜 수익률·가짜
  변동성·이평 소거를 만든다. 레지스트리를 쓰지 않고 직접 쓴 문서는 #218 의 혜택을 받지 못하고, 사용자는 경고
  없이 오염된 결과를 전략 효과로 읽는다.
- **결정(리드)**: compile 에 warning 을 추가한다. 시계열 변화 연산자의 입력이 원주가 가격 필드이면 "분할·증자에
  오염될 수 있습니다. `price.adj_close` 를 쓰세요" 를 backend 한글 완성 문장으로 낸다.
- **담당**: `P3-01`(backend 진단 추가와 테스트). WORKFLOW P3-01 acceptance 에 같은 번호로 예약했다.

## 갱신 절차

1. PR row의 상태·Review 열과 `현재 작업 Packet`을 고친다.
2. `변경 기록`에 한 줄 남긴다.
3. `pwsh docs/planning/strategy-language-2-0/tools/update-plan-progress.ps1`을 실행한다(`-Check`는 검증만).
