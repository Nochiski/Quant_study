---
plan_version: 2
project: yaml-strategy-workbench-ui
project_status: IN_REVIEW
current_phase: P0
current_pr: P0-02,P0-03
active_prs: [P0-02, P0-03]
parallel_window: [P0-02, P0-03]
last_updated: 2026-09-04T13:33:53+09:00
planned_prs: 45
merged_prs: 1
approved_prs: 1
progress_percent: 2
---

# YAML Strategy Workbench 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 구현 기준 화면은 [verbose YAML v3 시안](./assets/strategy-workbench-yaml-ui-concept-v3.png)을 따른다. v2와 최초 시안은 변경 기록용이다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_REVIEW` |
| Current phase | `P0` |
| Current/next PR | `P0-02,P0-03` |
| Active PR | `P0-02, P0-03` |
| Progress | `1 / 45 merged (2%)` |
| Approved | `1 / 45` |
| Aggregated at | `2026-09-04 13:33 KST` |
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
| P0 | Contract, product direction, tool choices | 4 | 1 | `IN_REVIEW` |
| P1 | Backend Authoring Contract | 9 | 0 | `READY` |
| P1.5 | Backtest Correctness Gate | 4 | 0 | `READY` |
| P2 | App Shell and visual foundation | 4 | 0 | `READY` |
| P3 | YAML Editor MVP | 7 | 0 | `WAITING` |
| P4 | Outline, Contract, Projections | 8 | 0 | `WAITING` |
| P5 | Truthful Trace UI | 3 | 0 | `WAITING` |
| P6 | Professional release and migration | 6 | 0 | `WAITING` |
| **Total** |  | **45** | **1** | **2%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-02` + `P0-03` (병렬 window, P0-03은 worktree `Quant_study-p0-03`) |
| Intent | P0-02: editor ADR(CodeMirror 6). P0-03: ruamel.yaml + YAML 1.2 cross-runtime manifest(12 accepted/16 rejected)와 양쪽 테스트 |
| Acceptance | 번들 크기·IME·schema completion 기준 비교표, 최종 선택과 rollback 방법, 이후 PR dependency 기록 |
| Non-goals | editor 의존성 설치·UI 코드 변경 (P3-02), parser (P0-03), router (P0-04) |
| Branch/worktree | `feat/p0-02-editor-spike` / `feat/p0-03-yaml-parser` |
| Base SHA | `38a2304` |
| Head SHA | P0-02 `9093b1a` / P0-03 `4a59d17` |
| Diff stat | P0-02 11 files +165 (ADR + spike 스크립트) / P0-03 35 files +578 (fixture 28개 포함) |
| Focused tests | P0-02 링크 검증 / P0-03 backend 29 passed, frontend 28 passed |
| Full gate | P0-03 worktree: ruff·pyright clean, frontend typecheck·lint·65 tests·build OK. backend pytest는 worktree Rust 미빌드로 core_parity 3건 환경 실패 → merge 후 main 트리에서 재실행 |

---

## P0 — 계약·제품 전환·기술 선택

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P0-01` | Verbose source ADR, YAML-first 전환, roadmap/rules/tracker 정합화 | 없음 | `MERGED` | `review_p0_01` APPROVE |
| [ ] | `P0-02` | Monaco/CodeMirror frontend editor spike | P0-01 | `IN_REVIEW` | `review_p0_02` |
| [ ] | `P0-03` | Backend parser ADR와 YAML 1.2 cross-runtime fixture | P0-01 | `IN_REVIEW` | `review_p0_03` |
| [ ] | `P0-04` | Frontend router ADR와 direct-entry spike | P0-01 | `READY` | — |

Phase exit:

- [ ] DSL·단위 sugar가 v1 non-goal로 명시되었다.
- [ ] WORKFLOW 2.2의 YAML 예시가 완전한 golden compile fixture로 등록되었다.
- [ ] YAML-first 전환과 no-code 범위가 roadmap/rules에 반영되었다.
- [ ] Frontend editor와 backend YAML 1.2 parser가 결정되었다.
- [ ] Frontend router와 route composition이 결정되었다.

## P1 — Backend Authoring Contract

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1-01` | Typed canonical hydrate와 numeric/date/enum hash fixture | P0-01 | `READY` | — |
| [ ] | `P1-02` | 안전한 YAML/JSON codec과 source map | P0-03, P1-01 | `WAITING` | — |
| [ ] | `P1-03` | Compile API와 통합 diagnostic, generated SDK | P1-02 | `WAITING` | — |
| [ ] | `P1-04` | Constraint catalog와 adapter-owned discriminator, domain Pydantic 금지 | P1-01 | `WAITING` | — |
| [ ] | `P1-05` | Discriminator가 포함된 runtime schema/contract API | P1-04 | `WAITING` | — |
| [ ] | `P1-06` | Revision source envelope, list/history repository port와 contract test | P1-01, P1-02 | `WAITING` | — |
| [ ] | `P1-07` | Document save/get/history API, generated SDK | P1-03, P1-06 | `WAITING` | — |
| [ ] | `P1-08` | Canonical semantic revision diff API | P1-07 | `WAITING` | — |
| [ ] | `P1-09` | Saved revision reference와 reproducible Backtest Run Manifest | P1-07 | `WAITING` | — |

Phase exit:

- [ ] Source compile → save → get → recompile 후 source/spec hash가 보존된다.
- [ ] Validation과 schema metadata가 같은 constraint declaration에서 파생된다.
- [ ] Backtest result가 resolved strategy revision/hash 또는 inline provenance를 기록한다.
- [ ] OpenAPI generated tree가 clean하다.

## P1.5 — Backtest Correctness Gate

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1.5-01` | Adapter-owned snapshot provenance와 cache key | P0-01 | `READY` | — |
| [ ] | `P1.5-02` | Bounded Factor evaluation projection과 parity test | P1.5-01 | `WAITING` | — |
| [ ] | `P1.5-03` | Raw PIT observation port | P1.5-01 | `WAITING` | — |
| [ ] | `P1.5-04` | 실제 FactorGraph 기반 preview/backtest TargetTape pipeline | P1.5-02, P1.5-03 | `WAITING` | — |

Phase exit:

- [ ] Factor ID hash 기반 synthetic factor path가 제거되었다.
- [ ] FactorGraph output, CandidateDecision, TargetTape와 Backtest 입력이 일치한다.
- [ ] PIT 미래 데이터가 차단된다.

## P2 — App Shell과 시각 기반

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | 시안 기준 light theme token과 공통 UI primitive | P0-01 | `READY` | — |
| [ ] | `P2-02` | Router, research/operations namespace, App Shell | P0-04 | `WAITING` | — |
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
| P0-01 | `review_p0_01` | `c174452` | `b988984` | APPROVE (1차 REQUEST_CHANGES → 재검토) | 1 (해소) | unknown-key fail-closed는 P1-01 구현 전까지 계약만 고정; ParameterValue union hash 분기 P1-01; YAML 1.1/1.2 차이는 P0-03까지 미검출 | 2026-09-04 |

## 검증 기록

| PR | Focused test | Full gate | API generated clean | Manual UX | CI | Recorded at |
|---|---|---|---|---|---|---|
| P0-01 | contract 9 passed + 1 xfailed | pytest 608 + ruff + pyright | 해당 없음 (API 미변경) | 해당 없음 | 로컬 게이트 동일 범위 (원격 push 전) | 2026-09-04 |

## 변경 기록

| 시각 | 변경자 | 변경 내용 | 근거 |
|---|---|---|---|
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
