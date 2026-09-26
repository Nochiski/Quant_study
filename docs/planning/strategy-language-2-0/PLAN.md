---
plan_version: 2
project: strategy-language-2-0
project_status: IN_PROGRESS
current_phase: P0,P1,P2
current_pr: P0-01,P1-01,P1-02,P2-01,P2-02,P2-03,P2-04
active_prs: [P0-01, P1-01, P1-02, P2-01, P2-02, P2-03, P2-04]
parallel_window: [P0-01, P1-01, P1-02, P2-01, P2-02, P2-03, P2-04]
last_updated: 2026-09-26T13:44:58+09:00
planned_prs: 28
merged_prs: 0
approved_prs: 3
progress_percent: 0
---

# schema 1.2 · 그래프 표현 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P0,P1,P2` |
| Current/next PR | `P0-01,P1-01,P1-02,P2-01,P2-02,P2-03,P2-04` |
| Active PR | `P0-01, P1-01, P1-02, P2-01, P2-02, P2-03, P2-04` |
| Progress | `0 / 28 merged (0%)` |
| Approved | `3 / 28` |
| Aggregated at | `2026-09-26 13:44 KST` |
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
  생성 산출물만 넣고 소비자 배선은 P3-01 그대로다. `browser-e2e` job은 계속 P3-03의 exit 조건이다.
- P1 스택과 P2 스택은 독립이라 병렬 진행할 수 있다. P3-01은 P1-05·P2-09 둘 다 merge 뒤 시작한다.
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
| P0 | Planning package and contract docs | 1 | 0 | `APPROVED` |
| P1 | In-screen friction removal on 1.1 | 5 | 0 | `IN_PROGRESS` |
| P2 | Backend schema 1.2 (environment split, 9 PRs) | 9 | 0 | `IN_REVIEW` |
| P3 | Frontend 1.2 adaptation | 3 | 0 | `WAITING` |
| P4 | Graph level 1: pipeline | 4 | 0 | `WAITING` |
| P5 | Graph level 2: recipe | 3 | 0 | `WAITING` |
| P6 | Graph level 3: node canvas | 3 | 0 | `WAITING` |
| **Total** |  | **28** | **0** | **0%** |
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
| Diff stat | 커밋 8개(공식 SoT 정리 1 + 기능 1 + 계약 산출물 1 + frontend 기대값 1 + 문서 1 + 1차 리뷰 반영 `28003cf1` 1 + 시각 기준선 `80a5ddf5` 1 + PLAN 상태 갱신 1). `0dd740e8..80a5ddf5` 실측 36파일 +945/−43, 문서·PNG 제외 31파일 +833/−34 |
| Focused tests | `uv run pytest tests/domain/test_portfolio_pipeline.py tests/domain/test_strategy_hydrate.py tests/domain/test_strategy_diff.py tests/domain/test_strategy_spec.py -q` |
| 제약사항 | 12절 상한 중 **파일 수(10)를 넘긴다** — 29파일(리뷰 반영 뒤 문서·PNG 제외 31파일). 넘긴 몫은 (a) 골든 `spec_hash` 리터럴을 한 줄씩 고치는 테스트 6파일, (b) 새 테스트 4파일이다. src 변경은 6파일 99줄이고 handwritten diff 는 약 510줄로 줄 수 상한(600) 안쪽이다. 골든 hash 리터럴이 파일 6곳에 복사돼 있는 것 자체가 부채지만 한 곳으로 모으는 정리는 이 PR 범위 밖이라 backlog 로 남긴다. 중간 상태 base `5653c14e` 에서 작업하던 동안에는 P2-03 잔재(import 붕괴·`typecheck:e2e`·테스트 3건) 우회 커밋 두 개를 앞에 뒀고, P2-03 최종 tip `7ec8f337` 이 같은 수정을 담아 replay 에서 버렸다 |
| Full gate | backend `uv sync --all-extras` · `maturin develop --release` · `uv run pytest -q`(1571 passed, 0 failed) · `ruff check src tests` · `ruff format --check`(이 PR 변경 파일 clean) · `pyright`(0) · `export_openapi.py` · `export_runtime_schema.py`(둘 다 재생성 후 diff 0) / frontend `npm ci`·`api:generate`(diff 0)·`typecheck`·`typecheck:e2e`·`lint`·`test`(639, 57파일)·`build` / e2e: 시각 기준선 4장(`strategy-workbench.png`, 1440/1920 × light/dark)을 계약 해시 변경으로 재생성(`80a5ddf5`). 이 수치들은 옛 tip 기준이고, replay 뒤 게이트 전체와 `npm run test:e2e` 는 이 PLAN 커밋을 포함한 push tip 에서 다시 돌려 PR #184 댓글에 기록한다 |

P2-04 결정 7건(WORKFLOW 원문과 다르게 갔거나 원문이 비워 둔 곳 4 + 1차 리뷰 반영 3):

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

5. **`weighting: factor_score` × `normalization: zscore` 는 compile error 로 막는다**
   (`strategy.portfolio.weighting_normalization_incompatible`, 1차 리뷰 P2-1 반영).
   - **상황**: schema 1.2 문서, `portfolio.weighting: factor_score`, `signal.normalization:
     zscore`, 팩터 1개(`direction: high`), `risk.max_name_weight` 가 충분히 큼.
   - **인풋**: 같은 기준일 5종목의 팩터 원시값 `1,2,3,4,5` 로 `compile_target_tape` 실행.
   - **에러 위치**: `domain/portfolio/_compiler.py` 의 `_weight_scores` —
     `score = max(abs(candidate.composite_score or 0.0), 1e-12)`.
   - **위험성**: `zscore` 는 합성 점수를 평균 0 중심으로 옮긴다. 그 위의 `abs()` 는 "신호가
     세다" 가 아니라 "평균에서 멀다" 를 비중으로 번역해서, 횡단면 **최악** 종목(0.333)이
     **최고** 종목(0.333)과 같은 최대 비중을 받고 중앙값 종목은 0 이 된다. 예외도 경고도 없어
     백테스트 지표만으로는 알 수 없는 silent corrupt 다.
   - **선택**: 경고가 아니라 **error** 다. 경고로 두면 방향이 뒤집힌 포트폴리오가 그대로
     실행된다. 부호 있는 합성 점수를 비중으로 옮기는 규칙 자체는 P2-07 compile 게이트가
     설계한다 — 이 PR 은 그때까지 조합을 막기만 한다.

6. **`factor_score` 에서 0점 종목은 비중 0 으로 tape 에서 뺀다**(1차 리뷰 P2-1 반영).
   `rank` 의 횡단면 최하위가 **항상 정확히 0.0** 이라, 기존 `max(..., 1e-12)` 바닥값이 그
   종목에 `4e-13` 짜리 dust 비중을 주고 `target_weight != 0` 필터를 통과해 tape 에 남았다.
   기본값이 `rank` 라 이 dust 가 기본 동작이었다. 기존 `MISSING_RISK` 와 같은 경로(사유를
   남기고 `continue`)를 재사용하고 사유는 `ExclusionReason.SCORE_THRESHOLD` 다 — "합성 점수가
   이 종목을 빼놨다" 라는 같은 뜻이고, `MINIMUM_TRADE` 를 쓰면 이전 보유가 있을 때 `selected`
   로 되살아나는 별도 분기(`_finalize_decision`)에 잘못 걸린다.

7. **정규화 값 조회는 catch-all default 대신 엄격 조회다**(1차 리뷰 P2-2 반영).
   `normalized_signals.get(key, value.value)` 는 조회가 빗나가면 **원시값**으로 떨어져서,
   정규화된 값과 원시값이 같은 가중 합에 섞인다 — 이 PR 이 없애려던 단위 지배가 진단도 예외도
   없이 되살아난다. 결정 6(분기 exhaustive)과 같은 실패 모양이라 같은 정책을 쓴다:
   `_signal_value` 가 `none` 분기에서만 원시값을 쓰고, 그 밖에는 `[...]` 로 조회해 `KeyError`
   를 진단 컨텍스트(`as_of`·`security_id`·`factor_id`·`normalization`·모집단 크기)가 붙은
   `ValueError` 로 올린다. 지금은 도달 불가이지만 P2-05 가 모집단 전제를 건드린다.

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
| [ ] | `P0-01` | 기획 패키지·설계 spec·ADR/로드맵/SoT 개정 | 없음 | `APPROVED` | [#167](https://github.com/Nochiski/Quant_study/pull/167) · `review_lang2_p0_01` 5차 APPROVE(1~4차 REQUEST_CHANGES 전부 해소) |

Phase exit:

- [ ] `update-plan-progress.ps1 -Check` 통과, 상위 문서 링크 확인.

## P1 — 화면 안에서 끝나는 마찰 제거

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1-01` | 문제 목록·검증 배지를 탭과 무관하게 렌더 | P0-01 | `IN_REVIEW` | [#168](https://github.com/Nochiski/Quant_study/pull/168) · `6ffb6ba` · `review_lang2_p1_01` 진행 중 · 게이트: typecheck·lint·Vitest 652·build·e2e 19/19 |
| [ ] | `P1-02` | 되돌리기·다시 실행 버튼, 전역 단축키 | P1-01 | `IN_PROGRESS` | 구현자 `impl-lang2-p1-02`, 워크트리 `wt-lang2-p1-02`, 브랜치 `feat/lang2-p1-02-undo-redo` |
| [ ] | `P1-03` | 연산자 카탈로그(backend)·노드/필드 한글 이름·설명 | P1-02 | `WAITING` | — |
| [ ] | `P1-04` | 연산자 먼저 고르기(kind 자동), 조용한 실패 피드백, 오류 본문 인라인 | P1-03 | `WAITING` | — |
| [ ] | `P1-05` | 구조 오류 한글화, 진단 코드 네임스페이스, 순환·중복 진단에 node_id | P1-04 | `WAITING` | — |

Phase exit:

- [ ] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과.
- [ ] 노드 property·kind·연산자 설명 커버리지 100%.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

## P2 — backend schema 1.2

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | `RunEnvironment` 모델·브리지(`domain/backtest`), 실행 요청 optional `environment`, manifest·캐시 키, `/run-environments/schema` | P0-01 | `IN_REVIEW` | [#172](https://github.com/Nochiski/Quant_study/pull/172) · 2차 APPROVE 대상 `1e0b095` + P3 후속 커밋 1개 · 구현자 `impl-lang2-p2-01`, 워크트리 `wt-lang2-p2-01`, 브랜치 `feat/lang2-p2-01-run-environment` · `review_lang2_p2_01` 1차 REQUEST_CHANGES(P0 1·P1 1·P2 4·P3 3) → 반영, 2차 APPROVE(P3 6 → 코드 2 반영, 문서 3 이관, 본문 1 리드). 커밋 7개(backend 3 + 생성 SDK 1 + 리뷰 반영 3). 31파일은 12절 상한(8파일)을 넘어 논리 단위로 쪼갰다 — 모델·브리지 / 세 요청 배선 / 스키마 엔드포인트, 그리고 CI `api:generate` 게이트가 요구하는 생성 SDK. 게이트: pytest 1494·ruff·pyright(duckdb 4건 기존) · frontend typecheck·lint·Vitest 639·build |
| [ ] | `P2-02` | `graph.missing_policy` 제거 → `environment.missing`(plan 인자, `plan_hash` 유지) | P2-01 | `APPROVED` | [#176](https://github.com/Nochiski/Quant_study/pull/176) · 구현자 `impl-lang2-p2-02`, 워크트리 `wt-lang2-p2-02`, 브랜치 `feat/lang2-p2-02-missing-policy` · `review_lang2_p2_02` 1차 REQUEST_CHANGES(P1 1·P2 3·P3 3) → 반영, 2차 APPROVE(P3 4건 후속 커밋). 커밋 12개(1차 5 + 1차 리뷰 반영 6 + 2차 리뷰 반영 1, history 재작성 없음). 게이트: pytest·ruff·pyright 0 · frontend api:generate diff 0·typecheck·lint·Vitest 639·build. `database/tests` 는 base `fff33fd` 와 같은 41 failed/1268 passed/33 errors(기존 실패, 이 PR 무관) |
| [ ] | `P2-03` | `data`·`execution` 제거, `CURRENT_SCHEMA_VERSION` 1.2, 필수 키 2개, fixture·hash golden | P2-02 | `APPROVED` | [#183](https://github.com/Nochiski/Quant_study/pull/183) · 워크트리 `wt-lang2-p2-03`, 브랜치 `feat/lang2-p2-03-schema-1-2` · `review_lang2_p2_03` 1차 REQUEST_CHANGES(P2 2·P3 8) → 반영, 2차 **APPROVE**(돌연변이 재실행 2 failed 확인, P3-07 이탈 타당). P2 둘 다 `_record_codec.py`의 은퇴 row 읽기 5줄이다: 1.1 row 테스트 0건(그 가지를 `raise`로 바꿔도 초록), 미지 `schema_version`이 fail-closed에서 silent 현재 버전 해석으로 바뀜. 게이트는 아래 Full gate |
| [ ] | `P2-04` | `signal.normalization`과 결합 전 정규화 | P2-03 | `IN_REVIEW` | [#184](https://github.com/Nochiski/Quant_study/pull/184) · 워크트리 `wt-lang2-p2-04`, 브랜치 `feat/lang2-p2-04-normalization` · `review_lang2_p2_04` 1차 REQUEST_CHANGES(P2 3·P3 5) → 반영 `28003cf1`(P2 3건 전부: `factor_score`×`zscore` compile error·0점 dust 제외, 정규화 엄격 조회, rank 백분위 값 고정 테스트 2건), 시각 기준선 재생성 `80a5ddf5`. P2-03 APPROVED tip `0dd740e8` 위로 replay, 2차 리뷰 대기. 게이트·e2e 는 push tip 에서 재실행(PR 댓글) |
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
| [ ] | `P3-01` | SDK 1.2, pointer 헬퍼·outline·snippet·Form projection·plan·debugger 적응, 새 필드 i18n | P2-09, P1-05 | `WAITING` | — |
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
| `P2-01` | `review_lang2_p2_01` | 1 | `REQUEST_CHANGES` | P0 1 · P1 1 · P2 4 · P3 3. **P0**: 명시 `environment`가 run의 tape 파이프라인(`backtest_run/_service.py`의 `preflight`·`run_pipeline`)에 미전달 — 데이터셋·엔진·매니페스트는 명시값을, 관측 조회·tape는 문서 브리지 값을 써서 preview가 422로 거절하는 유니버스를 run이 completed로 기록. **P1**: 명시 `environment`가 제약 검증 우회 — 범위 밖 값이 202 접수 뒤 `backtest.run.internal`로 늦게 실패, 런타임 스키마에도 범위 없음. **P2 4**: 지문 표기 변경 미기록, `environment_hash`의 int/float 비대칭, 매니페스트 비용 3필드가 `environment`와 중복·미대조(fixture가 이미 불일치), `engine_portfolio` PARTIAL_FILL 과소 선언 방향 미기록. **P3 3**: trace 메시지 미갱신, 해시 분리 테스트에 `start` 누락, 범용 빌더 owner. 반영: 파이프라인 전달 + architecture 테스트를 AST 전달 검사로 확장, `RunEnvironment.__post_init__` 검증(범위는 `_constraints.py` `/execution/*` 행 재사용)·float 정규화, 매니페스트 비용 축 대조, 문서 검증 → 브리지 순서 정정(부수 회귀 6건), P2-03에 방향·결정 항목 |
| `P2-01` | `review_lang2_p2_01` | 2 | `APPROVE` | 1차 10건 전부 해소 확인(재현 2개 재실행, 되돌리기 실험이 정확히 2건 실패). 새 findings 6건은 전부 P3·비차단. 코드 2건 반영 — 새 HTTP 테스트의 필드 단언이 사실상 항상 참(422 `input`이 요청 객체를 되돌려줘 모든 필드명이 본문에 있음) → `detail[0]["msg"]`·`loc` 기준으로 좁힘, `RUN_ENVIRONMENT_CONSTRAINTS`의 import 시점 bare `KeyError` → 누락 포인터·카탈로그를 실은 명시 `LookupError`. 문서 3건 이관 — P2-03에 "`preflight`가 `environment`를 받지만 읽지 않음"(두 호출부 값 동일성 가드 강화 또는 시그니처 정리), P3-02에 명시 `environment` 422의 필드 단위 표면 결정과 "범위 SoT는 `/run-environments/schema`, OpenAPI `RunEnvironment`에는 범위 없음". 나머지 1건(PR 본문의 수치·동작 변화 문장)은 리드가 처리 |
| `P2-02` | `review_lang2_p2_02` | 1 | `REQUEST_CHANGES` | P1 1 · P2 3 · P3 3. 핵심 invariant 4개(기본값 경로 `plan_hash` 동일, 정책별 분기, 충돌 거부, 소비자 0건 가드)는 base 코드 대조와 가드 되돌리기 실험으로 실증 확인. **P1**: 팩터 sandbox(`/factors/explain`·`/factors/preview`)가 요청 `missing` 기본값 `DROP`에 고정돼 문서의 `graph.missing_policy`를 무시 — 편집 화면 실행 플랜 패널이 실제 실행과 다른 결측 정책·`plan_hash`를 표시(이 PR이 만든 회귀). **P2 3**: `backtest_run` 의 `LegacyMissingPolicyConflictError` catch가 preflight 뒤라 도달 불가(죽은 코드), `preflight` docstring·`start` 주석이 새 동작과 정반대, WORKFLOW P2-02 미갱신으로 물리 삭제가 어느 PR에도 미할당. **P3 3**: trace만 진단을 문자열로 떨어뜨림, `x-deprecated` 소비자 부재와 해석 시점 순서 미명시, AST 가드가 `missing_policy` 이름 전체를 잡아 `plan.missing_policy`까지 막음. 반영: 요청 `missing`을 `MissingPolicy | None`으로 바꾸고 `None`이면 브리지 `resolve_graph_missing_policy`가 그래프 값으로 해소(+ explain 회귀 테스트 2개, frontend mock을 `body.missing` 기준으로 정정), 죽은 catch 제거 + run 경로 422 shape 테스트, 세 주석 정정, WORKFLOW P2-02 결정 3건·P2-03 물리 삭제 항목 |
| `P2-02` | `review_lang2_p2_02` | 2 | `APPROVE` | 1차 findings 6건 전부 해소 확인. P1 은 세 각도로 실증 재현 — 문서 값 반영(세 정책이 각자 값과 서로 다른 `plan_hash`), 경로 간 일치(`run_pipeline` 의 plan 과 `explain` 의 plan 이 `missing_policy`·`plan_hash` 모두 동일), 가드 되돌리기(application 에서 `request.graph.missing_policy` 를 읽으면 AST 테스트 실패). 새 findings 3건 + 미해소 1건은 전부 P3 비차단이며 후속 커밋 하나로 반영: trace 의 충돌 진단을 `_resolve_environment_or_reject` 로 통일해 preview·run 과 같은 `portfolio.strategy.invalid` + `validation.issues` 구조로, `/factors/preview` fallback 회귀 테스트(mock 관측에 결측 셀이 없어 이중체 포트로 값 수준 고정 — plan 과 값이 같은 정책을 읽는지), `resolve_graph_missing_policy` domain 단위 테스트를 owner 옆에, 모듈 내부 전용 헬퍼를 `_missing_from_legacy_graphs` 로 개명(WORKFLOW P2-03 삭제 목록도 함께). 리뷰어 권고 1건(`x-deprecated` 해석 시점을 P3-01 acceptance 에 명시)은 P3-01 소관으로 남긴다 |
| `P2-04` | `review_lang2_p2_04` | 1 | `REQUEST_CHANGES` | P2 3 · P3 5. 기능(look-ahead 없음·수치 정확·`spec_hash` 변화가 새 키 하나로 설명됨)은 맞음. **P2-1**: `weighting: factor_score` × `normalization: zscore` 에서 `abs()` 때문에 최악 종목이 최대 비중, `rank` 최하위 0점이 `1e-12` 바닥값으로 dust target → `strategy.portfolio.weighting_normalization_incompatible` error + 0점 종목 `SCORE_THRESHOLD` 제외. **P2-2**: 정규화 조회 default 가 원시값 → `_signal_value` 엄격 조회·진단 `ValueError`. **P2-3**: 추출한 rank 공식 값이 기존 테스트로 고정 안 됨(돌연변이 통과) → `CrossSectionalOperator.RANK`·`GroupOperator.RANK` 백분위 값 테스트. P3 3건 코드 반영, 2건 문서 기록. 반영 커밋 `28003cf1` |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| `P2-01` | `uv run pytest -q` (backend) | 1480 passed, 13 skipped | 2026-09-20 |
| `P2-01` | `uv run pytest -q` (backend, 리뷰 반영 후) | 1493 passed, 13 skipped | 2026-09-21 |
| `P2-01` | `uv run ruff check src tests` · `ruff format --check`(변경 30파일) | 통과 | 2026-09-20 |
| `P2-01` | `uv run pyright` | 4 errors — 전부 `duckdb` 미설치(기존), 신규 파일 0 | 2026-09-20 |
| `P2-01` | `npm run typecheck` · `lint` · `test` · `build` (frontend) | 통과, Vitest 639(57 파일) | 2026-09-20 |

## 변경 기록

- 2026-09-26 — 리뷰 기록 표의 P2-04 행이 P2-02 1·2차 행 사이에 끼어 있어 P2-02 행 뒤로 옮겼다(문서만).
- 2026-09-26 — P2-04 를 P2-03 APPROVED tip `0dd740e8` 위로 replay 하고(코드 4커밋 range-diff 동일) 1차
  리뷰 반영 `28003cf1`·시각 기준선 `80a5ddf5` 를 얹어 상태를 `IN_REVIEW` 로 갱신했다. 옛 PR head
  `dc8030d3` 는 옛 base 위라 PR #184 가 CONFLICTING 이었다.
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

## 갱신 절차

1. PR row의 상태·Review 열과 `현재 작업 Packet`을 고친다.
2. `변경 기록`에 한 줄 남긴다.
3. `pwsh docs/planning/strategy-language-2-0/tools/update-plan-progress.ps1`을 실행한다(`-Check`는 검증만).
