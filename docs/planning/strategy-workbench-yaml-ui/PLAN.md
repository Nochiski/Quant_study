---
plan_version: 2
project: yaml-strategy-workbench-ui
project_status: IN_REVIEW
current_phase: P1
current_pr: P1-02
active_prs: [P1-02]
parallel_window: [P1-02, P1-04]
last_updated: 2026-09-04T15:10:18+09:00
planned_prs: 45
merged_prs: 7
approved_prs: 7
progress_percent: 16
---

# YAML Strategy Workbench 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 구현 기준 화면은 [verbose YAML v3 시안](./assets/strategy-workbench-yaml-ui-concept-v3.png)을 따른다. v2와 최초 시안은 변경 기록용이다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_REVIEW` |
| Current phase | `P1` |
| Current/next PR | `P1-02` |
| Active PR | `P1-02` |
| Progress | `7 / 45 merged (16%)` |
| Approved | `7 / 45` |
| Aggregated at | `2026-09-04 15:10 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. frontmatter와 위 표, Phase 집계는 [update-plan-progress.ps1](./tools/update-plan-progress.ps1)이 생성하며 직접 수정하지 않는다.

## 현재 결정

- v1 authoring은 canonical field name과 raw value를 쓰는 verbose YAML/JSON이다.
- 표현식 문자열 DSL과 `%`·`bps` literal은 v1 범위가 아니다.
- 전략 정의는 전문 사용자를 위한 YAML-first로 전환한다. Parameter Search는 source를 직접 편집하지 않고 UI에서 수행할 수 있게 유지한다.
- roadmap은 product milestone SoT, 이 파일은 본 initiative의 PR delivery SoT다.
- Quick/Advanced 삭제는 P0-01의 roadmap/rule 변경과 migration acceptance가 끝난 뒤에만 가능하다.
- 현재 synthetic factor 경로는 UI 디버거가 아니라 backtest correctness 결함으로 분류하여 Phase 1.5에서 먼저 수정한다.
- Domain은 Pydantic을 import하지 않고 inbound schema adapter가 기존 domain union에 discriminator annotation을 제공한다.
- source의 unknown key는 모든 depth에서 structural blocking error로 fail-closed한다 (P1-01 구현).
- i18n 문구 갱신은 P0-01이 아니라 P3-05 cutover에서 한다.
- Frontend editor는 CodeMirror 6, frontend marker는 syntax만(advisory), structural/semantic marker는 backend compile diagnostic (editor ADR D2).
- Backend YAML parser는 ruamel.yaml(1.2, pure safe). frontend `yaml` 2.9.0. 양쪽 accept 집합은 yaml12 manifest가 고정하고 판정이 갈리는 문법은 거부한다 (parser ADR D2).
- Router는 TanStack Router. validateSearch는 잘못된 값을 제거하고 기본값을 URL에 쓰지 않으며 멱등이다. dirty blocker는 pathname 변경만 차단한다. `/?step=`·`/?run`은 query를 유지해 `/legacy/builder`로 redirect한다 (router ADR D1~D3).
- Dirty가 아니고 base revision/hash가 일치할 때만 saved revision backtest를 사용하며, 나머지 valid/current 문서는 inline draft provenance를 사용한다.

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
| `MERGED` | latest main 반영과 CI 후 merge 완료 |
| `PAUSED` | 제품·계약 결정이 필요해 일시 정지 |

기본 active PR은 하나다. `parallel_window`에 PR ID를 먼저 기록하고 별도 git worktree를 사용할 때만 최대 두 개를 허용한다. Domain contract, OpenAPI/generated SDK, CSS, editor state, fixture를 공유하면 병렬화하지 않는다.

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Contract, product direction, tool choices | 4 | 4 | `MERGED` |
| P1 | Backend Authoring Contract | 9 | 2 | `IN_REVIEW` |
| P1.5 | Backtest Correctness Gate | 4 | 1 | `READY` |
| P2 | App Shell and visual foundation | 4 | 0 | `READY` |
| P3 | YAML Editor MVP | 7 | 0 | `WAITING` |
| P4 | Outline, Contract, Projections | 8 | 0 | `WAITING` |
| P5 | Truthful Trace UI | 3 | 0 | `WAITING` |
| P6 | Professional release and migration | 6 | 0 | `WAITING` |
| **Total** |  | **45** | **7** | **16%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P1-02`→…→`P1-09` stack (main 트리 + worktrees `Quant_study-p1-05`, `Quant_study-p1-06`; P1-02 4차 확인 대기, P1-03 APPROVED, P1-05~09 IN_REVIEW) + `P2-01`→`P2-02` (worktree `Quant_study-p2-01`, IN_REVIEW) |
| Intent | P1-02: DocumentCodecPort + ruamel codec(source map, 정책 거부, 제한), manifest를 codec으로 검증. P1-04: scalar constraint catalog 단일 owner, validator가 catalog 소비, EXPRESSION_NODE_KINDS |
| Acceptance | 번들 크기·IME·schema completion 기준 비교표, 최종 선택과 rollback 방법, 이후 PR dependency 기록 |
| Non-goals | editor 의존성 설치·UI 코드 변경 (P3-02), parser (P0-03), router (P0-04) |
| Branch/worktree | `feat/p1-02-yaml-codec` (main 트리, P1-01 위 stack) / `feat/p1-04-constraint-catalog` (worktree, P1-01 위 stack) |
| Base SHA | `38a2304` |
| Head SHA | P1-02 `4090661` (base b1096c6) / P1-04 `3ae1efc` (base c997e52) |
| Diff stat | P1-02 12 files +716/−114 / P1-04 6 files +506/−100 |
| Focused tests | P1-02 codec 25 + manifest 44 + contract·domain·arch 146 / P1-04 constraints 35 + domain·http 24 |
| Full gate | P1-02: main 트리 pytest 698 passed(Rust 포함), ruff·pyright clean. P1-04: worktree 505 passed(Rust 의존 2 파일 deselect), ruff·pyright clean. merge 후 main(worktree) contract·domain·arch·http 117 passed |

---

## P0 — 계약·제품 전환·기술 선택

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P0-01` | Verbose source ADR, YAML-first 전환, roadmap/rules/tracker 정합화 | 없음 | `MERGED` | `review_p0_01` APPROVE |
| [x] | `P0-02` | Monaco/CodeMirror frontend editor spike | P0-01 | `MERGED` | `review_p0_02` APPROVE |
| [x] | `P0-03` | Backend parser ADR와 YAML 1.2 cross-runtime fixture | P0-01 | `MERGED` | `review_p0_03` APPROVE |
| [x] | `P0-04` | Frontend router ADR와 direct-entry spike | P0-01 | `MERGED` | `review_p0_04` APPROVE |

Phase exit:

- [x] DSL·단위 sugar가 v1 non-goal로 명시되었다.
- [x] WORKFLOW 2.2의 YAML 예시가 완전한 golden compile fixture로 등록되었다.
- [x] YAML-first 전환과 no-code 범위가 roadmap/rules에 반영되었다.
- [x] Frontend editor와 backend YAML 1.2 parser가 결정되었다.
- [x] Frontend router와 route composition이 결정되었다.
- [x] Phase 종료 SoT·책임분리 점검 서브에이전트 결과 기록 (사용자 지시, 2026-09-04) — 2026-09-04 audit: 결함 0, 문서 액션은 `.claude/rules/strategy-workbench-sot.md` 4행 추가, WORKFLOW 2.6/P1-01/P1-02, roadmap 5·7.1·9.1, `frontend-testing.md` manifest 규칙으로 반영

## P1 — Backend Authoring Contract

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P1-01` | Typed canonical hydrate와 numeric/date/enum hash fixture | P0-01 | `MERGED` | `review_p1_01` APPROVE |
| [ ] | `P1-02` | 안전한 YAML/JSON codec과 source map | P0-03, P1-01 | `IN_REVIEW` | `review_p1_02` |
| [ ] | `P1-03` | Compile API와 통합 diagnostic, generated SDK | P1-02 | `APPROVED` | `review_p1_03` APPROVE (P1-02 stack 대기) |
| [x] | `P1-04` | Constraint catalog와 adapter-owned discriminator, domain Pydantic 금지 | P1-01 | `MERGED` | `review_p1_04` APPROVE |
| [ ] | `P1-05` | Discriminator가 포함된 runtime schema/contract API | P1-04 | `IN_REVIEW` | `review_p1_05` |
| [ ] | `P1-06` | Revision source envelope, list/history repository port와 contract test | P1-01, P1-02 | `IN_REVIEW` | `review_p1_06` |
| [ ] | `P1-07` | Document save/get/history API, generated SDK | P1-03, P1-06 | `IN_REVIEW` | `review_p1_07` |
| [ ] | `P1-08` | Canonical semantic revision diff API | P1-07 | `IN_REVIEW` | `review_p1_08` |
| [ ] | `P1-09` | Saved revision reference와 reproducible Backtest Run Manifest | P1-07 | `IN_REVIEW` | `review_p1_09` |

Phase exit:

- [ ] Source compile → save → get → recompile 후 source/spec hash가 보존된다.
- [ ] Validation과 schema metadata가 같은 constraint declaration에서 파생된다.
- [ ] Backtest result가 resolved strategy revision/hash 또는 inline provenance를 기록한다.
- [ ] OpenAPI generated tree가 clean하다.

## P1.5 — Backtest Correctness Gate

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P1.5-01` | Adapter-owned snapshot provenance와 cache key | P0-01 | `MERGED` | `review_p15_01` APPROVE |
| [x] | `P1.5-02` | Bounded Factor evaluation projection과 parity test | P1.5-01 | `MERGED` | `review_p15_02` APPROVE |
| [x] | `P1.5-03` | Raw PIT observation port | P1.5-01 | `MERGED` | `review_p15_03` APPROVE |
| [x] | `P1.5-04` | 실제 FactorGraph 기반 preview/backtest TargetTape pipeline | P1.5-02, P1.5-03 | `MERGED` | `review_p15_04` APPROVE |

Phase exit:

- [x] Factor ID hash 기반 synthetic factor path가 제거되었다. (P1.5-04: `_portfolio_factor_value`·`load_portfolio_observations` 삭제, 소스 grep 0건)
- [x] FactorGraph output, CandidateDecision, TargetTape와 Backtest 입력이 일치한다. (`tests/integration/test_truthful_pipeline.py`: composite parity, run manifest tape hash == preview tape hash)
- [x] PIT 미래 데이터가 차단된다. (raw port 계약 `available_date <= as_of` + application `LookAheadViolationError`, contract/pipeline 회귀 테스트)

## P2 — App Shell과 시각 기반

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | 시안 기준 light theme token과 공통 UI primitive | P0-01 | `IN_REVIEW` | `review_p2_01` |
| [ ] | `P2-02` | Router, research/operations namespace, App Shell | P0-04 | `IN_REVIEW` | `review_p2_02` |
| [ ] | `P2-03` | Stepper를 제거한 resizable Strategy IDE layout | P2-01, P2-02 | `WAITING` | — |
| [ ] | `P2-04` | Revision-aware loader와 draft base 상태 | P1-07, P2-02 | `WAITING` | — |

Phase exit:

- [ ] New/saved strategy와 backtest direct route가 동작한다.
- [ ] Legacy editor가 migration 기간 별도 route에서 유지된다.
- [ ] 1280/1440/1920 light layout과 focus-visible을 확인했다.

## P3 — YAML Editor MVP

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P3-01` | YAML 1.2 document state machine과 CST path index | P0-03, P1-03, P1-05 | `WAITING` | — |
| [ ] | `P3-02` | Lazy code editor adapter와 worker lifecycle | P0-02, P2-03 | `WAITING` | — |
| [ ] | `P3-03` | Runtime schema 구조 검증·completion·hover | P3-01, P3-02, P1-05 | `WAITING` | — |
| [ ] | `P3-04` | Backend semantic diagnostic marker와 stale response 차단 | P3-03, P1-03 | `WAITING` | — |
| [ ] | `P3-05` | Dirty/base hash에 따른 saved reference 또는 inline draft Backtest | P3-04, P1-07, P1-09, P1.5-04, P2-04 | `WAITING` | — |
| [ ] | `P3-06` | Local autosave와 recovery 비교 | P3-05 | `WAITING` | — |
| [ ] | `P3-07` | 409 revision conflict에서 source 보존 | P3-05, P1-08 | `WAITING` | — |

Phase exit:

- [ ] New/edit/validate/save/reload/backtest가 verbose source editor에서 동작한다.
- [ ] Invalid 또는 stale source를 실행할 수 없다.
- [ ] Saved revision run과 inline draft run의 provenance가 표시된다.

## P4 — Outline·Contract·Projection

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P4-01` | Parameters를 포함한 Strategy Outline과 cursor 연동 | P3-03 | `WAITING` | — |
| [ ] | `P4-02` | Backend metadata 기반 Contract Inspector | P3-03, P1-05 | `WAITING` | — |
| [ ] | `P4-03` | Problems panel, filter, editor jump | P3-04 | `WAITING` | — |
| [ ] | `P4-04` | Backend Execution Plan과 source/graph 연동 | P3-05 | `WAITING` | — |
| [ ] | `P4-05` | Canonical node snippet insertion | P3-02, P1-05 | `WAITING` | — |
| [ ] | `P4-06` | Read-only canonical JSON과 Form projection | P3-05 | `WAITING` | — |
| [ ] | `P4-07` | Read-only FactorGraph DAG projection | P4-01, P3-05 | `WAITING` | — |
| [ ] | `P4-08` | Source/semantic/revision Diff와 conflict resolution | P1-08, P3-07 | `WAITING` | — |

Phase exit:

- [ ] 상단 중복 stepper 없이 left/center/right IDE 영역이 완성되었다.
- [ ] Outline에 parameters가 포함되었다.
- [ ] YAML/JSON/Form/Graph/Diff가 같은 StrategySpec을 표현한다.

## P5 — 실제 계산 Trace UI

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | Correctness pipeline을 조회하는 scoped trace API | P1.5-04, P1-03 | `WAITING` | — |
| [ ] | `P5-02` | Date/security/node 선택 Debugger shell | P3-05, P5-01, P2-03 | `WAITING` | — |
| [ ] | `P5-03` | Raw→Target trace, risk before/after, order delta estimate | P4-01, P4-07, P5-02 | `WAITING` | — |

Phase exit:

- [ ] UI trace가 실제 FactorGraph, TargetTape, Backtest 입력과 일치한다.
- [ ] `spec_hash/snapshot_id/registry_version/plan_hash`로 재현할 수 있다.
- [ ] `/risk/max_name_weight` JSON Pointer와 zero/missing/coverage 상태가 정확하다.

## P6 — 전문 사용자 마감과 전환

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P6-01` | SQLite persistent strategy revision repository | P1-07 | `WAITING` | — |
| [ ] | `P6-02` | Server draft, strategy/revision/backtest history UI | P6-01, P3-06, P4-08 | `WAITING` | — |
| [ ] | `P6-03` | Keyboard workflow와 Command Palette | P4-01, P4-02, P4-03, P4-04, P4-05, P4-06, P4-07, P4-08, P5-03 | `WAITING` | — |
| [ ] | `P6-04` | Large spec 성능·접근성·i18n과 soft dark theme | P3-05, P4-01, P4-02, P4-03, P4-04, P4-05, P4-06, P4-07, P4-08, P5-03 | `WAITING` | — |
| [ ] | `P6-05` | Playwright/visual regression infrastructure와 CI | P2-03, P3-05, P6-04 | `WAITING` | — |
| [ ] | `P6-06` | 전체 E2E, migration gate, 조건부 legacy cleanup | P6-01, P6-02, P6-03, P6-04, P6-05 | `WAITING` | — |

Phase exit:

- [ ] Latest main에서 backend/frontend/browser E2E CI가 성공한다.
- [ ] P0-01의 migration 조건 충족 여부에 따라 legacy 유지/제거가 결정되었다.
- [ ] 모든 PR에 reviewer `APPROVE` 기록이 있다.

---

## Review 기록

수정 재검토에는 같은 reviewer를 사용한다.

| PR | Reviewer agent | Base SHA | Final HEAD SHA | Verdict | P0/P1 | Residual risk | Reviewed at |
|---|---|---|---|---|---:|---|---|
| P1.5-04 | `review_p15_04` | `17d13df` | `b843d29` | APPROVE (1차 REQUEST_CHANGES: 빈 frame parity·look-ahead guard 제거 → 2차 APPROVE, P2 backtests route 422 매핑은 b843d29에서 수정) | 2 (해소) | hand-computed golden 없음(self-consistency만), lag는 adapter 계약 위임, cross-sectional op가 비회원 포함(P5 SoT 결정), per-factor full-panel 평가 비용(P6-04), reference_unsupported 코드 owner 서술(P3) | 2026-09-04 |
| P1.5-03 | `review_p15_03` | `0cc578d` | `69516ba` | APPROVE (1차 REQUEST_CHANGES: universe 식별자 부재·실패 채널 없음/미지 field 합성 → 2차 APPROVE) | 2 (해소) | fixture↔synthetic 경계 불연속(mock 전용, docstring 명시), fixture 휴장일 시 빈 세션, CellKind 5종이 None으로 병합(계약 명시) | 2026-09-04 |
| P1.5-02 | `review_p15_02` | `cae73fb` | `3c846f2` | APPROVE (1차 APPROVE w/ P2: cap 경계 빈 node·중복 row·도달 불가 node → 재검토 APPROVE) | 0 | ValueError는 P5-01에서 사용자 입력이 되면 Result/diagnostic으로 wrap, reference-present-but-None은 P5-03에서 세분화 | 2026-09-04 |
| P1-03 | `review_p1_03` | `bb138d0` | `13db6bd` | APPROVE (1차 REQUEST_CHANGES: depth-2000 500·node_id·severity optional·테스트 공백 → 2차 APPROVE) | 1 (해소) | semantic warning 경로는 monkeypatch 단위 테스트만, `_service.py` docstring P2-02 오기(P3, P1-02 merge 시 수정) | 2026-09-04 |
| P1-04 | `review_p1_04` | `c997e52` | `93d5625` | APPROVE (1차 APPROVE w/ P2: cost path 분할·NaN·EXPRESSION_NODE_KINDS 파생·pydantic 게이트 → 재검토 APPROVE) | 0 | `_kind_of` helper 2개(P1-05에서 통합), description_key 4-segment 네이밍 결정(P1-05 전), PR body에 wire path/NaN/PyYAML 게이트 근거 기재 | 2026-09-04 |
| P1.5-01 | `review_p15_01` | `1e28d73` | `cae73fb` | APPROVE | 0 | 409 시 frontend 복구 UX 없음(후속), 검사 순서 단위 테스트 없음, backtest adapter snapshot 메시지 cleanup | 2026-09-04 |
| P1-01 | `review_p1_01` | `8a824ab` | `881c38e` | APPROVE (1차 REQUEST_CHANGES: -0.0 tuple 정규화 → 재검토) | 1 (해소) | NaN/inf는 hash 시점 ValueError(P1-03 compile 경계에서 처리), YAML 1e-2는 P1-02 codec 소관, choice integral float fold | 2026-09-04 |
| P0-03 | `review_p0_03` | `38a2304` | `bc7e8a6` | APPROVE (1차 P0 gitignore·P1 merge key·P1 non-core number, 2차 P1 `.5e3` → 3차) | 4 (해소) | tab/`\0`/CR/`%TAG !!` backend-narrower(fail-closed), 복수 위반 reason 순서는 P1-02 | 2026-09-04 |
| P0-04 | `review_p0_04` | `c814758` | `70dae8d` | APPROVE (1차 REQUEST_CHANGES: blocker 술어·legacy redirect·정규화 경로, 2차 P2 run 기본값 → 3차) | 1 (해소) | blocker/errorComponent/ensureQueryData 동작은 P2-02/P2-04 렌더 테스트 필수 | 2026-09-04 |
| P0-02 | `review_p0_02` | `38a2304` | `fc8023d` | APPROVE (1차 REQUEST_CHANGES: IME 서술·D2 경계 → 재검토) | 2 (해소) | 200 KB chunk 예산은 P3-02 측정; main-thread parse는 P3-01 debounce·P6-04 측정; monaco-yaml이 0.56+ 지원 시 IME 논거 약화 | 2026-09-04 |
| P0-01 | `review_p0_01` | `c174452` | `b988984` | APPROVE (1차 REQUEST_CHANGES → 재검토) | 1 (해소) | unknown-key fail-closed는 P1-01 구현 전까지 계약만 고정; ParameterValue union hash 분기 P1-01; YAML 1.1/1.2 차이는 P0-03까지 미검출 | 2026-09-04 |

## 검증 기록

| PR | Focused test | Full gate | API generated clean | Manual UX | CI | Recorded at |
|---|---|---|---|---|---|---|
| P1.5-02/03/04 | trace 13 + raw port contract 16 + pipeline 13 passed | worktree pytest 559 passed(Rust core 제외), main merge 후 559 passed, ruff, pyright | 해당 없음 (응답 스키마 미변경, 422 코드 additive) | 해당 없음 | 로컬 (worktree) | 2026-09-04 |
| P1-03 | compile HTTP 12 + authoring service 1 passed | pytest 717 passed, ruff, pyright, frontend typecheck·lint | SDK 재생성 clean (severity required, node_id 추가) | 해당 없음 | 로컬 | 2026-09-04 |
| P1-04 | constraints 35 + domain·architecture·http 110 passed | worktree pytest 170(reviewer) / main merge 후 525 passed(Rust core 제외), ruff, pyright | 해당 없음 (API 미변경; issues[].path 분할은 기존 스키마 내) | 해당 없음 | 로컬 (worktree) | 2026-09-04 |
| P1.5-01 | backend factor 13 passed, frontend 80 passed | ruff·pyright·typecheck·lint·build·SDK clean | SDK 재생성 clean | 해당 없음 | 로컬 (worktree) | 2026-09-04 |
| P1-01 | hydrate 19 + contract 10 passed | pytest 630 passed, ruff, pyright | 해당 없음 | 해당 없음 | 로컬 | 2026-09-04 |
| P0-03 | backend 44 passed, frontend 43 passed | ruff·pyright·typecheck·lint·build | 해당 없음 | 해당 없음 | 로컬 (worktree) | 2026-09-04 |
| P0-04 | 스파이크 node --test 8 passed | 링크 검증 | 해당 없음 | 해당 없음 | 로컬 | 2026-09-04 |
| P0-02 | 문서 링크 검증 | 스파이크 3종 빌드 재현(reviewer) | 해당 없음 | 해당 없음 | 로컬 | 2026-09-04 |
| P0-01 | contract 9 passed + 1 xfailed | pytest 608 + ruff + pyright | 해당 없음 (API 미변경) | 해당 없음 | 로컬 게이트 동일 범위 (원격 push 전) | 2026-09-04 |

## 변경 기록

| 시각 | 변경자 | 변경 내용 | 근거 |
|---|---|---|---|
| 2026-09-04 KST | Claude | P2-02 착수·diff freeze 9cab283 (branch `feat/p2-02-app-shell`, P2-01 위; 29 files +1118/−16; typecheck·lint·vitest 94·build clean; @tanstack/react-router 1.170.32 추가), review_p2_02(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P2-01 착수·diff freeze b6fc778 (worktree `Quant_study-p2-01`, main 기반; 16 files +2613/−1765; typecheck·lint·vitest 87·build clean), review_p2_01(opus) 배정 → IN_REVIEW. 병렬 window: P1 stack + P2 line | 13.3 |
| 2026-09-04 KST | Claude | P1-09 착수·diff freeze 3fb99d3 (branch `feat/p1-09-run-manifest`, P1-08 stack 위; 16 files +607/−30; backend 599 passed, ruff, pyright, SDK·typecheck·lint·vitest 80·build clean), review_p1_09(opus) 배정 → IN_REVIEW. Phase 1 PR 9/9 구현 완료, 리뷰·merge 대기 | 13.3 |
| 2026-09-04 KST | Claude | P1-08 착수·diff freeze 5d05646 (branch `feat/p1-08-semantic-diff`, P1-07 stack 위; 11 files +583; backend 596 passed, ruff, pyright, SDK·typecheck·lint clean), review_p1_08(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P1-07 착수·diff freeze 844d932 (branch `feat/p1-07-document-api`, P1-06 stack 위; 12 files +1185/−7; backend 591 passed, ruff, pyright, SDK 재생성·typecheck·lint clean), review_p1_07(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P1-06 착수·diff freeze c85f786 (worktree `Quant_study-p1-06`, P1-05 stack 위; 8 files +495/−69; backend 588 passed, ruff, pyright, SDK 변경 없음), review_p1_06(opus) 배정 → IN_REVIEW. P1-02 4차 수정(b4a34f6) 확인 대기 | 13.3 |
| 2026-09-04 KST | Claude | P1.5-02/03/04 APPROVE → main merge(a2e4996), MERGED, Phase 1.5 종료 기준 3항 체크, Phase 1.5 SoT·책임분리 감사 서브에이전트 착수. P1-03 APPROVE(13db6bd) → APPROVED(P1-02 3차 수정 대기). P1-02 3차 REQUEST_CHANGES(%YAML 1.3 AssertionError) 수정 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P1-05 diff freeze aef2a53 (base 6490fb7, 13 files +1316/−5; backend 579 passed, ruff, pyright, SDK clean), review_p1_05(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P1-05 IN_PROGRESS (worktree `Quant_study-p1-05`, P1-03 stack + main). P1.5-02 APPROVE(3c846f2, stack 대기), P1.5-03/04 2차 리뷰(69516ba) 요청, P1-02 2차 수정(54d734c) 요청, P1-03 2차 리뷰 대기 | 13.3 |
| 2026-09-04 KST | Claude | P1-04 APPROVE(93d5625) → main merge(b7a3ce8), MERGED. P1-05 READY. P1-02 2차 REQUEST_CHANGES(P2 2건: YAML escape surrogate, JSON duplicate key 위치) 수정 중; P1-03 재검토 대기; P1.5-03/04 REQUEST_CHANGES(P1 각 2건) 수정 착수 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P1-01(881c38e)·P1.5-01(cae73fb) APPROVE → main merge, MERGED. P1-02(4090661)·P1-04(3ae1efc) IN_REVIEW 병렬 window. Phase 0 SoT 감사 보고 대기 중 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P0-03 APPROVE(bc7e8a6) → main merge, MERGED. Phase 0 PR 4/4 merge, 종료 점검 서브에이전트 착수. P1-01(c3c4158) IN_REVIEW, P1.5-01 병렬 window | 13.6 merge gate |
| 2026-09-04 KST | Claude | P0-04 APPROVE(70dae8d) → main merge, MERGED. P1-01 IN_PROGRESS로 병렬 window 교체 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P0-04(36d61b7) diff freeze, review_p0_04 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P0-02 APPROVE(fc8023d) → main merge, MERGED. P0-04 IN_PROGRESS로 병렬 window 교체 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P0-02(9093b1a)·P0-03(4a59d17) diff freeze, 병렬 window 선언, reviewer 각 1명 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P0-01 APPROVE → 로컬 main merge, MERGED. P0-02/03/04, P1-01, P1.5-01, P2-01 READY | 13.6 merge gate |
| 2026-09-04 KST | Claude | review_p0_01 REQUEST_CHANGES(P1: unknown key 정책) 반영 → b988984, 같은 reviewer 재검토 요청 | 13.5 재검토 |
| 2026-09-04 KST | Claude | P0-01 SELF_CHECK 통과, diff freeze(7123f0a), review_p0_01 배정 → IN_REVIEW | 13.3 diff freeze |
| 2026-09-04 KST | Claude | P0-01 IN_PROGRESS 전환, 브랜치 생성, scope packet 작성 | 착수 |
| 2026-09-04 KST | Codex | 완전한 YAML 예시, adapter-owned discriminator, dirty Backtest 규칙, router ADR, P6 visual dependency, dependency 검증 및 pointer 통일 반영 | 2차 계획 리뷰 |
| 2026-09-04 KST | Codex | v1 verbose YAML 확정, YAML-first 전환과 roadmap/rules 정합화 PR 추가, parser spike 분리, constraint catalog owner 및 run manifest 추가, correctness를 Phase 1.5로 승격, P5를 trace UI로 축소, light-only 초기 theme, v2 시안과 자동 집계 도입 | 계획 리뷰 반영 |
| 2026-09-04 KST | Codex | 최초 기획 폴더, UI 시안, workflow, 41개 PR tracker 생성 | 사용자 요청 |

## 갱신 절차

1. 대상 PR row의 상태와 Review 값을 수정한다.
2. merge 시 상태를 `MERGED`, checkbox를 `[x]`로 함께 바꾼다.
3. 현재 작업 Packet과 Review/검증/변경 기록을 갱신한다.
4. 다음 명령으로 frontmatter와 집계를 계산한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/strategy-workbench-yaml-ui/tools/update-plan-progress.ps1
```

5. CI에서는 아래 check mode로 drift를 검증한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/strategy-workbench-yaml-ui/tools/update-plan-progress.ps1 -Check
```

Reviewer는 이 파일을 수정하지 않는다. 구현 책임자가 reviewer verdict와 CI 결과를 반영한다.

CI 게이트(WORKFLOW 12절 9번)는 origin push 전까지 로컬에서 같은 범위의 게이트(backend pytest/ruff/pyright,
frontend typecheck/lint/vitest/build, generated diff clean)로 대체한다. push 시점에 원격 CI 결과를
`CI` 열에 소급 기록한다. (2026-09-04 결정)
