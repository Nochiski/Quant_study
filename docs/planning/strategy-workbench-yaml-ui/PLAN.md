---
plan_version: 2
project: yaml-strategy-workbench-ui
project_status: IN_PROGRESS
current_phase: P5
current_pr: P5-01
active_prs: [P5-01]
parallel_window: []
last_updated: 2026-09-05T10:15:03+09:00
planned_prs: 50
merged_prs: 41
approved_prs: 41
progress_percent: 82
---

# YAML Strategy Workbench 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 구현 기준 화면은 [verbose YAML v3 시안](./assets/strategy-workbench-yaml-ui-concept-v3.png)을 따른다. v2와 최초 시안은 변경 기록용이다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P5` |
| Current/next PR | `P5-01` |
| Active PR | `P5-01` |
| Progress | `41 / 50 merged (82%)` |
| Approved | `41 / 50` |
| Aggregated at | `2026-09-05 10:15 KST` |
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
| P1 | Backend Authoring Contract | 10 | 10 | `MERGED` |
| P1.5 | Backtest Correctness Gate | 5 | 5 | `MERGED` |
| P2 | App Shell and visual foundation | 4 | 4 | `MERGED` |
| P3 | YAML Editor MVP | 7 | 7 | `MERGED` |
| P4 | Outline, Contract, Projections | 10 | 10 | `MERGED` |
| P5 | Truthful Trace UI | 3 | 0 | `IN_PROGRESS` |
| P6 | Professional release and migration | 7 | 1 | `WAITING` |
| **Total** |  | **50** | **41** | **82%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P5-01` Correctness pipeline을 조회하는 scoped trace API IN_PROGRESS |
| Intent | 기존 truthful portfolio pipeline과 factor evaluator를 단일 계산 owner로 재사용해 전문 사용자가 제한된 date/security/factor/node trace를 provenance와 함께 조회하게 한다 |
| Acceptance | `POST /api/v1/strategies/debug/trace`; inline draft 또는 saved revision; as-of/security/factor/node/raw/starting holdings scope; deterministic ordering; row/page cap; cancellation; 계산 전 invalid/capability diagnostic; `spec_hash/snapshot_id/registry_version/plan_hash` provenance |
| Non-goals | 별도 factor 계산기, frontend Debugger shell(P5-02), full linked trace/risk/order projection(P5-03), persistent strategy repository(P6-01) |
| Branch/worktree | `feat/p5-01-scoped-trace-api` (`Quant_study-p5-01`) |
| Base SHA | `5a242ec` (P4-08 merge main) |
| Head SHA | 구현 전 |
| Diff stat | 구현 후 기록 |
| Focused tests | 구현 후 기록 |
| Full gate | 구현 후 기록 |

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
| [x] | `P1-02` | 안전한 YAML/JSON codec과 source map | P0-03, P1-01 | `MERGED` | `review_p1_02` APPROVE |
| [x] | `P1-03` | Compile API와 통합 diagnostic, generated SDK | P1-02 | `MERGED` | `review_p1_03` APPROVE |
| [x] | `P1-04` | Constraint catalog와 domain schema builder discriminator, domain Pydantic 금지 | P1-01 | `MERGED` | `review_p1_04` APPROVE |
| [x] | `P1-05` | Discriminator가 포함된 runtime schema/contract API | P1-04 | `MERGED` | `review_p1_05` |
| [x] | `P1-06` | Revision source envelope, list/history repository port와 contract test | P1-01, P1-02 | `MERGED` | `review_p1_06` |
| [x] | `P1-07` | Document save/get/history API, generated SDK | P1-03, P1-06 | `MERGED` | `review_p1_07` |
| [x] | `P1-08` | Canonical semantic revision diff API | P1-07 | `MERGED` | `review_p1_08` |
| [x] | `P1-09` | Saved revision reference와 reproducible Backtest Run Manifest | P1-07 | `MERGED` | `review_p1_09` |
| [x] | `P1-10` | Phase 1 종료 감사 후속(상대 import 게이트, 코덱 코드 접두어, manifest hash 불변식, 문서) | P1-09 | `MERGED` | `review_p1_10` APPROVE · [#50](https://github.com/Nochiski/Quant_study/pull/50) |

Phase exit:

- [x] Source compile → save → get → recompile 후 source/spec hash가 보존된다. (`test_strategy_document_save_http_api.py`: YAML→JSON revise까지 같은 `spec_hash`, CRLF 바이트 보존)
- [x] Validation과 schema metadata가 같은 constraint declaration에서 파생된다. (scalar는 `_constraints.py` 카탈로그 한 곳. cross-field·graph 코드는 감사 시점 DEFECT-102를 #49의 `EXPRESSION_CODES` + `semantic_issue()` owner gate로 해소했다)
- [x] Backtest result가 resolved strategy revision/hash 또는 inline provenance를 기록한다. (`RunManifest.strategy_provenance`, P1-10에서 `strategy_hash`와의 일치 불변식 추가)
- [x] OpenAPI generated tree가 clean하다. (`npm run api:generate` 후 `git diff --ignore-cr-at-eol` 빈 diffstat)
- [x] Phase 종료 SoT·책임분리 점검 서브에이전트 결과 기록 (사용자 지시, 2026-09-04) — 2026-09-04 audit: PASS_WITH_ACTIONS, High 2(DEFECT-101/102) Medium 1(DEFECT-103) Low 2(DEFECT-104/105). DEFECT-101/103/105와 문서 액션 5건은 `P1-10`, DEFECT-102는 `feat/p1.5-05-audit-fixes`, DEFECT-104는 `P4-02` 소관

## P1.5 — Backtest Correctness Gate

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P1.5-01` | Adapter-owned snapshot provenance와 cache key | P0-01 | `MERGED` | `review_p15_01` APPROVE |
| [x] | `P1.5-02` | Bounded Factor evaluation projection과 parity test | P1.5-01 | `MERGED` | `review_p15_02` APPROVE |
| [x] | `P1.5-03` | Raw PIT observation port | P1.5-01 | `MERGED` | `review_p15_03` APPROVE |
| [x] | `P1.5-04` | 실제 FactorGraph 기반 preview/backtest TargetTape pipeline | P1.5-02, P1.5-03 | `MERGED` | `review_p15_04` APPROVE |
| [x] | `P1.5-05` | Phase 1.5 SoT 감사 후속(D-001~D-007, 문서 6건) | P1.5-04 | `MERGED` | `review_p15_05` APPROVE · `review_p15_05_merge` APPROVE · [#49](https://github.com/Nochiski/Quant_study/pull/49) |

Phase exit:

- [x] Factor ID hash 기반 synthetic factor path가 제거되었다. (P1.5-04: `_portfolio_factor_value`·`load_portfolio_observations` 삭제, 소스 grep 0건)
- [x] FactorGraph output, CandidateDecision, TargetTape와 Backtest 입력이 일치한다. (`tests/integration/test_truthful_pipeline.py`: composite parity, run manifest tape hash == preview tape hash)
- [x] raw **필드**의 미래 공개 데이터가 차단된다. (raw port 계약 `available_date <= as_of` +
  application `LookAheadViolationError`, contract/pipeline 회귀 테스트) `sector_id`와
  `universe_member`는 공개일이 없어 이 가드가 닿지 않으며 as_of vintage는 어댑터 책임이다
  (D-006).

## P2 — App Shell과 시각 기반

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P2-01` | 시안 기준 light theme token과 공통 UI primitive | P0-01 | `MERGED` | `review_p2_01` + `review_p2_01_fix` APPROVE · [#38](https://github.com/Nochiski/Quant_study/pull/38) |
| [x] | `P2-02` | Router, research/operations namespace, App Shell | P0-04 | `MERGED` | `review_p2_02` + `review_p2_02_final` APPROVE · [#39](https://github.com/Nochiski/Quant_study/pull/39) |
| [x] | `P2-03` | Stepper를 제거한 resizable Strategy IDE layout | P2-01, P2-02 | `MERGED` | `review_p2_03` + `review_p2_03_final` APPROVE · [#40](https://github.com/Nochiski/Quant_study/pull/40) |
| [x] | `P2-04` | Revision-aware loader와 draft base 상태 | P1-07, P2-02 | `MERGED` | `review_p2_04` + `review_p2_04_latest` APPROVE · [#43](https://github.com/Nochiski/Quant_study/pull/43) |

Phase exit:

- [ ] New/saved strategy와 backtest direct route가 동작한다.
- [ ] Legacy editor가 migration 기간 별도 route에서 유지된다.
- [ ] 1280/1440/1920 light layout과 focus-visible을 확인했다.

## P3 — YAML Editor MVP

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P3-01` | YAML 1.2 document state machine과 CST path index | P0-03, P1-03, P1-05 | `MERGED` | `review_p3_01` + `review_p3_01_final` APPROVE · [#41](https://github.com/Nochiski/Quant_study/pull/41) |
| [x] | `P3-02` | Lazy code editor adapter와 worker lifecycle | P0-02, P2-03 | `MERGED` | `review_p3_02` + `review_p3_02_final` APPROVE · [#42](https://github.com/Nochiski/Quant_study/pull/42) |
| [x] | `P3-03` | Runtime schema 구조 검증·completion·hover | P3-01, P3-02, P1-05 | `MERGED` | `review_p3_03` + `review_p3_03_latest` APPROVE · [#44](https://github.com/Nochiski/Quant_study/pull/44) |
| [x] | `P3-04` | Backend semantic diagnostic marker와 stale response 차단 | P3-03, P1-03 | `MERGED` | `review_p3_04` + `review_p3_04_latest` APPROVE · [#45](https://github.com/Nochiski/Quant_study/pull/45) |
| [x] | `P3-05` | Dirty/base hash에 따른 saved reference 또는 inline draft Backtest | P3-04, P1-07, P1-09, P1.5-04, P2-04 | `MERGED` | `review_p3_05` + `review_p3_05_latest` APPROVE · [#46](https://github.com/Nochiski/Quant_study/pull/46) |
| [x] | `P3-06` | Local autosave와 recovery 비교 | P3-05 | `MERGED` | `review_p3_06` + `review_p3_06_latest` APPROVE · [#47](https://github.com/Nochiski/Quant_study/pull/47) |
| [x] | `P3-07` | 409 revision conflict에서 source 보존 | P3-05, P1-08 | `MERGED` | `review_p3_07` + `review_p3_07_latest` APPROVE · [#48](https://github.com/Nochiski/Quant_study/pull/48) |

Phase exit:

- [x] New/edit/validate/save/reload/backtest가 verbose source editor에서 동작한다.
- [x] Invalid 또는 stale source를 실행할 수 없다.
- [x] Saved revision run과 inline draft run의 provenance가 표시된다.

## P4 — Outline·Contract·Projection

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P4-01` | Parameters를 포함한 Strategy Outline과 cursor 연동 | P3-03 | `MERGED` | [#52](https://github.com/Nochiski/Quant_study/pull/52) · `review_p4_01` APPROVE |
| [x] | `P4-02` | Backend metadata 기반 Contract Inspector | P3-03, P1-05 | `MERGED` | [#53](https://github.com/Nochiski/Quant_study/pull/53) · `review_p4_02` APPROVE |
| [x] | `P4-03` | Problems panel, filter, editor jump | P3-04 | `MERGED` | [#55](https://github.com/Nochiski/Quant_study/pull/55) · `review_p4_03` APPROVE |
| [x] | `P4-04` | Backend Execution Plan query·version gate·source mapping model | P3-05, P4-02 | `MERGED` | [#57](https://github.com/Nochiski/Quant_study/pull/57) · `review_p4_04` APPROVE · `25d8b45` |
| [x] | `P4-05` | Canonical node snippet insertion | P3-02, P1-05 | `MERGED` | [#59](https://github.com/Nochiski/Quant_study/pull/59) · `review_p4_05` APPROVE · `191b902` |
| [x] | `P4-06` | Read-only canonical JSON과 Form projection | P3-05 | `MERGED` | [#61](https://github.com/Nochiski/Quant_study/pull/61) · `review_p4_06` APPROVE · `f9a0e35` |
| [x] | `P4-07` | Read-only FactorGraph DAG projection | P4-01, P3-05 | `MERGED` | [#62](https://github.com/Nochiski/Quant_study/pull/62) · `review_p4_07` APPROVE · `bdee3f7` |
| [x] | `P4-08` | Source/semantic/revision Diff와 conflict resolution | P1-08, P3-07 | `MERGED` | [#63](https://github.com/Nochiski/Quant_study/pull/63) · `review_p4_08` APPROVE (P1 2/P2 3 해소) · `5a242ec` |
| [x] | `P4-09` | Execution Plan 표시와 YAML/graph selection 연동 | P4-04, P4-01 | `MERGED` | [#58](https://github.com/Nochiski/Quant_study/pull/58) · `review_p4_09` APPROVE · `8a2ebfc` |
| [x] | `P4-10` | Five-area snippet catalog UI와 new/revision page wiring | P4-05 | `MERGED` | [#60](https://github.com/Nochiski/Quant_study/pull/60) · `review_p4_10` APPROVE · `20491b8` |

Phase exit:

- [x] 상단 중복 stepper 없이 left/center/right IDE 영역이 완성되었다.
- [x] Outline에 parameters가 포함되었다.
- [x] YAML/JSON/Form/Graph/Diff가 같은 StrategySpec을 표현한다.

## P5 — 실제 계산 Trace UI

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | Correctness pipeline을 조회하는 scoped trace API | P1.5-04, P1-03 | `IN_PROGRESS` | branch `feat/p5-01-scoped-trace-api` · base `5a242ec` |
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
| [ ] | `P6-03` | Keyboard workflow와 Command Palette | P4-01, P4-02, P4-03, P4-04, P4-05, P4-06, P4-07, P4-08, P4-09, P4-10, P5-03 | `WAITING` | — |
| [ ] | `P6-04` | Large/hostile spec 성능·접근성·i18n과 soft dark theme (`$ref`-only cycle fail-closed 포함) | P3-05, P4-01, P4-02, P4-03, P4-04, P4-05, P4-06, P4-07, P4-08, P4-09, P4-10, P5-03 | `WAITING` | — |
| [ ] | `P6-05` | Playwright/visual regression infrastructure와 CI | P2-03, P3-05, P6-04 | `WAITING` | — |
| [ ] | `P6-06` | 전체 browser E2E·실구동 시나리오 검증, migration gate, 조건부 legacy cleanup | P6-01, P6-02, P6-03, P6-04, P6-05 | `WAITING` | — |
| [x] | `P6-07` | Root frontend/backend development entrypoints | P0-02, P0-03 | `MERGED` | [#56](https://github.com/Nochiski/Quant_study/pull/56) · `review_p6_07` APPROVE |

Phase exit:

- [ ] Latest main에서 backend/frontend/browser E2E CI가 성공한다.
- [ ] backend/frontend를 실제로 동시에 실행해 전략 생성·오류 수정·저장/복구·백테스트·trace/risk·history/diff 전체 시나리오를 수행하고 실행 보고서를 남긴다.
- [ ] P0-01의 migration 조건 충족 여부에 따라 legacy 유지/제거가 결정되었다.
- [ ] 모든 PR에 reviewer `APPROVE` 기록이 있다.

---

## Review 기록

수정 재검토에는 같은 reviewer를 사용한다.

| PR | Reviewer agent | Base SHA | Final HEAD SHA | Verdict | P0/P1 | Residual risk | Reviewed at |
|---|---|---|---|---|---:|---|---|
| P4-08 | `review_p4_08` | `bdee3f7` | `684dc69` | APPROVE (최초 P1 2/P2 3과 new-draft badge를 동일 reviewer 재검토에서 모두 해소) | 2 (해소) | 대형 Diff 전체 행 virtualization은 P6-04, 서로 다른 history page 간 선택은 P6-02 범위 | 2026-09-05 |
| P4-07 | `review_p4_07` | `f9a0e35` | `380d583` | APPROVE (REQUEST_CHANGES P1 1/P2 1 해소 후 동일 reviewer 재승인) | 0 | saved JSON Graph↔source browser 통합과 대형 DAG 시각·키보드 UX는 P6 E2E/성능·접근성에서 확인 | 2026-09-05 |
| P4-09 | `review_p4_09` | `25d8b45` | `160fad4` | APPROVE | 0 | factor 보유 실제 route→URL→editor reveal/focus 통합 테스트와 plan-null registry/data provenance 표시를 후속 보강 | 2026-09-05 |
| P6-07 | `review_p6_07` | `ebc16c2` | `2e53089` | APPROVE (REQUEST_CHANGES P1 1건 해소, 비차단 P2 보강 후 재승인) | 1 (해소) | blocking risk 없음 | 2026-09-05 |
| P4-03 | `review_p4_03` | `3284fe0` | `752ed60` | APPROVE (initial P2 2건 선제 보강 후 동일 reviewer 재승인) | 0 | blocking code risk 없음 | 2026-09-05 |
| P4-02 | `review_p4_02` | `3d7b996` | `850bd91` | APPROVE (REQUEST_CHANGES 3회, P1 누적 5건 수정 후 동일 reviewer 4차 승인) | 5 (해소) | catalog 첫 100건 선로딩은 P6 후속; union schema key order 차이는 보수적 branch-dependent로 fail-closed | 2026-09-05 |
| P4-01 | `review_p4_01` | `30baf41` | `be8c183` | APPROVE (REQUEST_CHANGES 2회, P1 누적 6건 수정 후 동일 reviewer 3차 승인) | 6 (해소) | route identity 전환 1-frame·대형 spec 성능·초기 invalid 빈 outline은 비차단 후속 위험 | 2026-09-05 |
| P3-07 | `review_p3_07` + `review_p3_07_latest` | `8b7b1a4` | `c1951b6` | APPROVE (stale conflict identity와 latest revision 이중 추론 P1 2건을 structured contract 단방향으로 수정 후 승인) | 2 (해소) | malformed/null latest revision은 fail-closed, 대형 diff 렌더링은 P4-08에서 보강 | 2026-09-04 |
| P3-06 | `review_p3_06` + `review_p3_06_latest` | `4a3dc14` | `201479c` | APPROVE (저장 중 후속 편집의 pre-save key, 복구 identity fail-closed, diff baseline owner P1 3건 수정 후 승인) | 3 (해소) | 수동 원복 시 기존 autosave 잔존, 다중 탭 `new` key 조정, main bundle 경고는 후속 | 2026-09-04 |
| P3-05 | `review_p3_05` + `review_p3_05_latest` | `0167fb9` | `b1c5c51` | APPROVE (saved/inline provenance와 invalid/stale 차단 재검토, dirty orphan run·stale route overwrite P1 2건 수정 후 승인) | 2 (해소) | 폐기된 응답의 서버 run은 향후 history에서 회수, disabled tooltip·transport 문구·main bundle은 후속 | 2026-09-04 |
| P3-04 | `review_p3_04` + `review_p3_04_latest` | `20c1447` | `b5c37a6` | APPROVE (stale/error marker·selection clamp·IME·capability 상태 재검토, unknown-key가 value를 가리키던 P1 수정 후 승인) | 1 (해소) | backend code-point fallback의 UTF-16 변환, 전체 state effect의 abort/redebounce, main bundle 경고는 후속 | 2026-09-04 |
| P3-03 | `review_p3_03` + `review_p3_03_latest` | `03600de` | `a76f3c4` | APPROVE (runtime schema SoT·x-defines, cursor→pointer, kind union, completion/hover와 IME/stale 처리 재검토) | 0 | catalog 100+ pagination, 배포 경계 schema/contract/catalog version 검증, flow/multiline YAML은 후속 | 2026-09-04 |
| P2-04 | `review_p2_04` + `review_p2_04_latest` | `0b89cfd` | `0527b6f` | APPROVE (저장 snapshot·epoch/version, create 중 편집 2단 저장, pathname dirty guard, dialog 접근성 재검토) | 0 | POP/beforeunload 브라우저 E2E, 닫힌 dialog 포커스 복원, stale save tone, entry chunk 경고는 후속 | 2026-09-04 |
| P3-02 | `review_p3_02` + `review_p3_02_final` | `d52885b` | `21ba0fa` | APPROVE (latest main 기준 lazy CodeMirror·설정 compartment·history·IME·diagnostic clamp·번들 예산 재검토) | 0 | 실제 브라우저 IME/Android EditContext E2E, entry chunk 500 kB 경고, selection clamp는 후속 추적 | 2026-09-04 |
| P3-01 | `review_p3_01` + `review_p3_01_final` | `f5c4309` | `8b49212` | APPROVE (backend와 diagnostic code owner 정합화 후 문서 epoch/version·IME·stale 응답 재검토) | 0 | 큰 문서 동기 parse 비용, reason 집합의 cross-runtime drift 방지 강화는 P6-04 후속 | 2026-09-04 |
| P2-03 | `review_p2_03` + `review_p2_03_final` | `2d7bcb5` | `7aa7e10` | APPROVE (좁은 화면 nav 시각 상태·ARIA 정합, outline pending 중립색 재검토) | 0 | 실제 브라우저 viewport·시각 회귀와 panel preference 영속성은 P6-03~P6-05 | 2026-09-04 |
| P2-02 | `review_p2_02` + `review_p2_02_final` | `08726e3` | `49495fd` | APPROVE (latest main 기준 router·App Shell·접근성 재검토) | 0 | hard-reload fallback은 배포 E2E, revision safe-integer 상한 후속 가능 | 2026-09-04 |
| P2-01 | `review_p2_01` + `review_p2_01_fix` | `3df3980` | `1c2d5e5` | APPROVE (direction badge 대비 9.08:1 보강, latest main 통합 후 재검증) | 0 | 다크 테마는 P6-04 범위 | 2026-09-04 |
| P1-10 | `review_p1_10` | `7efe811` | `d7fff5e` | APPROVE (상대 import initializer 우회 수정 및 #49/#51 main 통합 재검토) | 0 | 전체 backend는 Rust extension 빌드 후 검증해야 함(CI와 로컬 883개 통과) | 2026-09-04 |
| P1.5-05 | `review_p15_05` + `review_p15_05_merge` | `0db8e31` | `060c918` | APPROVE (D-001~D-007 감사 후속 및 latest main 통합 재검토) | 0 | saved provenance+raw warning 결합 전용 테스트 없음(각 경로와 조립 코드로 검증), carried target은 executed book이 아님 | 2026-09-04 |
| P1-09 | `review_p1_09` | `5d05646` | `54eb349` | APPROVE (1차 REQUEST_CHANGES: 지문에 provenance 포함 → 2차 APPROVE, 422 형태 통일·InlineDraft source_hash 계약 명시) | 1 (해소) | inline source_hash는 서버 검증 불가(계약 명시, 배포 정책이 신뢰 금지), 응답 strategy 영구 nullable, union discriminator 미적용(FastAPI dataclass 제약) | 2026-09-04 |
| P1-08 | `review_p1_08` | `844d932` | `54eb349` | APPROVE (테스트 보강 반영) | 0 | diff 404가 OpenAPI에 미기재, 대용량 diff 상한 없음(P6) | 2026-09-04 |
| P1-07 | `review_p1_07` | `c85f786` | `54eb349` | APPROVE (P2 반영) | 0 | list_strategies 라우트 미노출(P2-04에서 추가), 문서 전략 legacy revise가 stale과 같은 409 코드(전용 코드 후속) | 2026-09-04 |
| P1-06 | `review_p1_06` | `4722fae` | `54eb349` | APPROVE (1차 REQUEST_CHANGES: contract test 검출력 부족 B1·B2 → 2차 APPROVE, 뮤테이션 7종 중 7종 검출) | 2 (해소) | source↔spec 짝짓기는 docstring 규율(문서 use case만 생성), timezone 정규화는 adapter 책임(P6-01) | 2026-09-04 |
| P1-05 | `review_p1_05` | `6490fb7` | `54eb349` | APPROVE (P2 4건 반영: contract_hash, branch, 304 `*`, 상수) | 0 | schema는 dataclass 힌트 파생이라 pydantic 검증과의 drift는 architecture test로만 감시 | 2026-09-04 |
| P1-02 | `review_p1_02` | `b1096c6` | `b4a34f6` | APPROVE (1차 REQUEST_CHANGES P1 RecursionError 누수·compose 후 node 제한 → 2차 P2 surrogate/JSON dup 위치 → 3차 P1 %YAML 1.3 AssertionError → 4차 APPROVE) | 2 (해소) | pure Python ruamel 처리량(15.5k줄 2.4 s)은 P1-03 latency 예산, offset 단위 변환은 P3-04, compose가 정책 위반 문서까지 도는 구조는 except Exception 안전망으로 닫음 | 2026-09-04 |
| P1.5-04 | `review_p15_04` | `17d13df` | `b843d29` | APPROVE (1차 REQUEST_CHANGES: 빈 frame parity·look-ahead guard 제거 → 2차 APPROVE, P2 backtests route 422 매핑은 b843d29에서 수정) | 2 (해소) | hand-computed golden 없음(self-consistency만), lag는 adapter 계약 위임, cross-sectional op가 비회원 포함(P1.5-05에서 해소, D-001), per-factor full-panel 평가 비용(P6-04), reference_unsupported 코드 owner 서술(P1.5-05에서 해소, D-007) | 2026-09-04 |
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
| P4-08 | author focused 75/66 + reviewer focused 5 files 75 passed | frontend typecheck·lint·vitest 368·build; reviewer 독립 전체 368; real-backend PIT E2E 포함; backend Ruff | OpenAPI/SDK 재생성 deterministic·clean | save source/hash/canonical 결합과 fail-closed 재compile, edit/명시 검증 baseline retry, EOF·대형 duplicate/reorder non-zero, v51 현재 revision page, Diff view 409 recovery, new draft badge까지 검증 | [#63](https://github.com/Nochiski/Quant_study/pull/63) `review_p4_08` APPROVE P0/P1/P2 0, review HEAD `684dc69` CI 4/4 pass | 2026-09-05 |
| P4-07 | FactorGraph model/UI·disconnected node·plan-null·same-pointer source 복귀·execution gate·new/revision route 53 passed | frontend typecheck·lint·vitest 350·build; real-backend PIT 포함 | generated API 변경 없음 | 실행 노드는 backend plan 순서/contract, disconnected authored node는 별도 미실행 영역·validation contract·exact pointer; hidden editor focus 금지와 명시적 projection→source reveal, Form/JSON selection·undo 보존 검증 | [#62](https://github.com/Nochiski/Quant_study/pull/62) `review_p4_07` APPROVE P0/P1/P2 0, latest approval-doc HEAD CI 4/4 pass, MERGED (`bdee3f7`) | 2026-09-05 |
| P4-10 | schema coherence·snippet hook/UI·new/revision route 47 passed | frontend typecheck·lint·vitest 317·build; real-backend PIT 포함; editor gzip 136.63 KB | OpenAPI/SDK 재생성 deterministic·semantic diff clean | metadata query 오류는 unavailable, schema/contract hash·registry 세대 불일치는 incompatible로 fail-closed; JSON projection YAML-only, feedback epoch/status 왕복, 실제 factor graph 삽입·중복·syntax 무변경·dirty compile·focus·단일 undo 검증 | [#60](https://github.com/Nochiski/Quant_study/pull/60) `review_p4_10` APPROVE P0/P1/P2 0, latest HEAD CI 4/4 pass, MERGED (`20491b8`) | 2026-09-05 |
| P4-05 | backend schema 14, canonical snippet·CodeEditor transaction·outline 22 passed | backend 904·Ruff·Pyright; frontend typecheck·lint·vitest 302·build; editor gzip 136.63 KB | OpenAPI/SDK 재생성 deterministic·clean, runtime schema fixture 갱신 | backend `x-authoring-*` mapping만으로 factor preset을 투영하고 loading/catalog-only/null graph/incomplete metadata를 fail-closed; 동일 factor 무변경, CRLF 중간·EOF·빈 줄, selection bounds, recursive/oversized schema, isolated undo를 reviewer가 재현 | [#59](https://github.com/Nochiski/Quant_study/pull/59) `review_p4_05` APPROVE, latest HEAD CI 4/4 pass, MERGED (`191b902`) | 2026-09-05 |
| P4-09 | execution query·panel projection 21 passed; reviewer focused 46 passed | frontend typecheck·lint·vitest 291·build; real backend PIT E2E 포함 | generated API 변경 없음 | backend step 순서·input/output type/unit·history·registry/dataset·fingerprint 표시, 모든 blocked/loading/error/incompatible/invalid 상태, factor/node/input pointer와 route selection 검증 | [#58](https://github.com/Nochiski/Quant_study/pull/58) `review_p4_09` APPROVE, latest CI 4/4 pass, MERGED (`8a2ebfc`) | 2026-09-05 |
| P4-04 | backend factor HTTP·truthful pipeline·raw port·equity HTTP 47, frontend orchestration 7 passed | backend 903·Ruff·Pyright; frontend 277·typecheck·lint·build | OpenAPI/SDK 재생성 deterministic·clean | explain↔portfolio plan 전체 동등성, 공개 category catalog↔raw loader, 숫자 group 동일 코드 거부, sector group completed/tape hash, metadata/raw snapshot mismatch, category·boolean·scalar output 실행 차단까지 검증 | [#57](https://github.com/Nochiski/Quant_study/pull/57) 동일 reviewer APPROVE, latest CI 4/4 pass, MERGED (`25d8b45`) | 2026-09-05 |
| P6-07 | root delegate 4 + 실제 server HTTP smoke 1 + backend entrypoint/architecture 8 passed | backend pytest 896·ruff·pyright; frontend typecheck·lint·vitest 270·build; root test 5·ruff·pyright·locks | generated API 변경 없음 | root `npm run dev` HTTP 200, root `uv run server --port 42810` health 200·reload·Ctrl+C, backend/root help 동일 | [#56](https://github.com/Nochiski/Quant_study/pull/56) latest backend/frontend 중복 CI 4 pass, reviewer 최종 APPROVE, MERGED (`84d7c88`) | 2026-09-05 |
| P4-03 | problem projection·panel·compile navigation 18 passed | frontend typecheck·lint·vitest 270·build | generated API 변경 없음 | 4종 toggle filter·error/warning section·정확한 dedupe·jump·빈 root pointer·pointer/node ID copy·stale/copy race/failure 접근성 검증 | [#55](https://github.com/Nochiski/Quant_study/pull/55) latest backend/frontend 중복 CI 4개 pass, MERGED (`ebc16c2`) | 2026-09-05 |
| P4-02 | Contract Inspector·schema assist·schema navigator·outline·IDE 56 passed | frontend typecheck·lint·vitest 262·build | generated API 변경 없음 | missing/unknown FactorNode·Parameter union·상이 requiredness와 이전 P1 4건 회귀를 독립 reviewer 재확인 | [#53](https://github.com/Nochiski/Quant_study/pull/53) latest backend/frontend 중복 CI 4개 pass, MERGED (`3284fe0`) | 2026-09-05 |
| P4-01 | editor selection·outline projection/navigation·router·document route 38 passed | frontend typecheck·lint·vitest 232·build | 해당 없음 | runtime schema/source map/URL owner, exact sourceVersion·route selection 귀속, RFC 6901 검증, visible roving tabindex·ARIA ownership | [#52](https://github.com/Nochiski/Quant_study/pull/52) latest duplicate backend/frontend 4 pass, MERGED | 2026-09-05 |
| P3-07 | backend conflict contract 16, frontend document routes 14 passed | backend ruff·pyright; frontend typecheck·lint·vitest 221·build | OpenAPI/generated SDK deterministic | source 보존, delayed 409 폐기, 서버본 실제 이동, copy/diff 실패, kind i18n 검토 | [#48](https://github.com/Nochiski/Quant_study/pull/48) 수정 후 backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-06 | autosave recovery·text diff 12 passed | frontend typecheck·lint·vitest 217·build | 해당 없음 | savedVersion 기반 old/new key 전환, identity 불명·불일치 raw-only, owner baseline·quota·키보드 스크롤 검토 | [#47](https://github.com/Nochiski/Quant_study/pull/47) 수정 후 backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-05 | backtest source·document route 13 passed | frontend typecheck·lint·vitest 205·build | 해당 없음 | dirty inline run 접수/머무르기/재열기, 중복 제출 차단, stale document 응답 폐기 검토 | [#46](https://github.com/Nochiski/Quant_study/pull/46) 수정 후 backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-04 | compile diagnostics 10 passed | frontend typecheck·lint·vitest 199·build | 해당 없음 | stale marker 비활성·범위 clamp·root anchor·unknown-key의 key range 이동 검토 | [#45](https://github.com/Nochiski/Quant_study/pull/45) 수정 후 backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-03 | backend schema 13, frontend schema/navigator/cursor 22 passed | backend ruff·pyright; frontend typecheck·lint·vitest 189·build | OpenAPI 및 runtime schema fixture 재생성 exact | output node·parameter reference scope, zero-indent sequence, IME/stale hover 검토 | [#44](https://github.com/Nochiski/Quant_study/pull/44) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P2-04 | revision/router 16 passed | frontend typecheck·lint·vitest 167·build | 해당 없음 | 저장 중 추가 편집 보존·재저장 후 URL 전환, same-path tab 허용, dialog focus/Escape 검토 | [#43](https://github.com/Nochiski/Quant_study/pull/43) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-02 | CodeMirror adapter 4 passed | frontend typecheck·lint·vitest 160·build | 해당 없음 | lazy load·undo history·IME cleanup·Escape/Tab/ARIA 검토, editor gzip 136.37 kB | [#42](https://github.com/Nochiski/Quant_study/pull/42) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P3-01 | YAML parse·cross-runtime·document-state 91 passed, backend codec parity 101 passed | frontend typecheck·lint·vitest 156·build | 해당 없음 | IME·stale parse/compile/save 상태 전이 검토 | [#41](https://github.com/Nochiski/Quant_study/pull/41) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P2-03 | responsive App Shell·Strategy IDE 18 passed | frontend typecheck·lint·vitest 108·build | 해당 없음 | 1279px 이하 초기 축소·토글 복원, pending 중립색 확인 | [#40](https://github.com/Nochiski/Quant_study/pull/40) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P2-02 | router 8 passed | frontend typecheck·lint·vitest 98·build | 해당 없음 | direct entry·legacy redirect·operations flag 확인 | [#39](https://github.com/Nochiski/Quant_study/pull/39) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P2-01 | shared UI primitive·theme 회귀 | frontend typecheck·lint·vitest 90·build | 해당 없음 | direction badge 대비 9.08:1 | [#38](https://github.com/Nochiski/Quant_study/pull/38) backend/frontend 중복 실행 4개 pass, MERGED | 2026-09-04 |
| P1-10 | audit·architecture·owner gate 228 passed | backend 883 passed, ruff, pyright, Rust 13·fmt·clippy; frontend typecheck·lint·vitest 80·build | 재생성 후 clean | 해당 없음 | [#50](https://github.com/Nochiski/Quant_study/pull/50) backend/frontend pass, MERGED | 2026-09-04 |
| P1.5-05 | truthful pipeline·backtest HTTP 24 passed, CRLF/LF tracker 47 rows | backend 865 passed, ruff, pyright; frontend typecheck·lint·vitest 80·build | 재생성 후 clean | 해당 없음 | [#49](https://github.com/Nochiski/Quant_study/pull/49) backend/frontend pass, MERGED | 2026-09-04 |
| P1-05~09 | schema/contract·repository 계약(뮤테이션)·document save/history/diff·fingerprint·reference HTTP | worktree pytest 649 passed(Rust core 제외), ruff, pyright; main merge(7efe811) 후 649 passed, vitest 80 | SDK 재생성 clean (c1f6b52; main에서는 LF/CRLF 차이만) | 해당 없음 | 로컬 (worktree) | 2026-09-04 |
| P1-02 | codec 31 + manifest 43 + contract·domain·arch 144 passed | pytest 709 passed, ruff, pyright | 해당 없음 | 해당 없음 | 로컬 | 2026-09-04 |
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
| 2026-09-05 KST | Codex | P4-08 PR #63 최신 approval-doc HEAD `983b5f4` CI 4/4 통과 후 merge commit `5a242ec`로 순차 머지하고 main fast-forward. Phase 4 exit을 닫고 메인 기준 `feat/p5-01-scoped-trace-api` 전용 worktree에서 P5-01 IN_PROGRESS 전환 | 13.5 merge gate·단일 active PR·truthful pipeline SoT |
| 2026-09-05 KST | Codex | 동일 reviewer `review_p4_08`이 review HEAD `684dc69`에서 최초 P1 2/P2 3과 new-draft badge 해소를 재현하고 APPROVE(P0/P1/P2 0). reviewer focused 75·frontend 전체 368·typecheck·lint·build, PLAN consistency, 원격 CI 4/4 통과를 확인해 APPROVED 전환 | 13.5 approval gate·same-reviewer 재승인·backend canonical/hash SoT·latest CI |
| 2026-09-05 KST | Codex | P4-08 review-fix code freeze `9b29220`, 최신 검증과 28 files +2,129/-177 size exception을 PR #63 본문에 반영하고 최초 검토자 `review_p4_08`에게 P1 2/P2 3 동일 reviewer 재검토를 요청하기 위해 IN_REVIEW 전환 | 13.4 same-reviewer fix loop·최신 evidence·PR body merge gate |
| 2026-09-05 KST | Codex | P4-08 review fix `9b29220`: Save snapshot의 source/spec_hash/canonical을 한 쌍으로 검증하고 불일치 시 current 또는 saved baseline을 backend 재compile할 때까지 Save/Run/Diff를 fail-closed. baseline 실패는 다음 edit와 명시 검증으로 재시도한다. exact EOF marker와 대형 duplicate/reorder 상한 diff, current revision을 포함하는 50개 history page, 모든 projection 위의 409 recovery notice, new draft 중립 badge를 회귀로 고정. reviewer 회귀 75·compile/document/route 66·frontend 전체 368·typecheck·lint·build·backend Ruff·generated clean 후 SELF_CHECK 전환 | backend canonical/hash SoT·document/query/diff/shell 책임분리·P1 2/P2 3 전부 회귀 고정·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | `review_p4_08`이 P0 0/P1 2/P2 3으로 REQUEST_CHANGES. save 응답 hash와 이전 compile canonical 결합, EOF/대형 중복 source diff의 false zero를 blocking으로 확인했고 baseline transient retry, 51+ revision 현재 target, 비-YAML view의 409 recovery 노출도 같은 reviewer loop에서 함께 수정하기 위해 CHANGES_REQUESTED 전환 | backend canonical/hash 짝 SoT·exact source truth·retry/revision/view lifecycle 책임·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-08 [#63](https://github.com/Nochiski/Quant_study/pull/63)을 공개하고 implementation freeze `7543cc9`, review transition HEAD `ce1813c`, 22 files +1,576/-131 size exception과 frontend 361 전체 gate를 본문에 고정해 fresh review-only agent `review_p4_08` 검토로 IN_REVIEW 전환 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate·12절 size exception |
| 2026-09-05 KST | Codex | P4-08을 `7543cc9`에 freeze: exact source changed-line Diff와 backend canonical payload 전용 semantic projection, revision-to-revision source/API semantic 비교, invalid text-only, comment-only zero semantic, 409 server 이동·copy·현재 전체 문서 새 revision 작성을 new/revision route에 연결. 편집이 초기 compile을 추월해도 별도 base-compiled action이 현재 verdict를 바꾸지 않고 exact saved canonical만 채운다. focused 79, frontend 361·typecheck·lint·build 통과. 총 1,707 churn/22 files 중 테스트 505이며 shared model/table·document lifecycle·route conflict가 단일 acceptance라 12절 size exception 기록 | source text/backend canonical SoT 분리·document/query/projection/UI 책임분리·P4-08 acceptance·12절 size exception |
| 2026-09-05 KST | Codex | #62 P4-07을 동일 reviewer APPROVE(P0/P1/P2 0), 최신 approval-doc HEAD CI 4/4 후 main에 병합(`bdee3f7`), 40/50(80%). 최신 main에서 P4-08 전용 worktree를 만들고 source text/backend canonical semantic/revision diff와 안전한 conflict resolution 구현을 시작 | 13.6 merge gate·text/canonical SoT 분리·revision conflict 책임 |
| 2026-09-05 KST | Codex | 동일 reviewer `review_p4_07`이 최신 HEAD `380d583`에서 최초 P1/P2 해소와 신규 P0/P1/P2 0을 확인해 APPROVE. focused 53·frontend 350·typecheck·lint·build 및 latest CI 4/4 통과를 기록하고 APPROVED 전환 | 13.5 approval gate·same-reviewer 재승인·latest CI |
| 2026-09-05 KST | Codex | P4-07 수정 freeze `b8a29be`와 최신 검증·size exception을 PR #62 본문에 반영하고 최초 검토자 `review_p4_07`에게 P1/P2 동일 reviewer 재검토를 요청해 IN_REVIEW 전환 | 13.4 same-reviewer fix loop·최신 evidence·PR body merge gate |
| 2026-09-05 KST | Codex | P4-07 reviewer P1/P2를 `b8a29be`에서 수정. backend plan node는 plan 순서·contract를 유지하고 disconnected authored node는 validation contract만 쓰는 별도 미실행 영역에 보존했으며, projection이 명시적으로 source를 열 때만 same-pointer reveal을 요청해 일반 Form 왕복의 selection/history 보존과 hidden editor focus 금지를 함께 유지. focused 53, frontend 350·typecheck·lint·build 통과 후 SELF_CHECK 전환 | StrategySpec 완전성·backend SoT·명시적 source reveal 책임·회귀 검증 |
| 2026-09-05 KST | Codex | `review_p4_07`이 P0 0/P1 1/P2 1로 REQUEST_CHANGES. backend plan이 output-reachable node만 포함하므로 valid graph의 disconnected authored node가 Graph에서 조용히 사라지는 StrategySpec 동등성 결함과, 동일 pointer의 YAML→Graph→source 복귀 시 route key가 남아 editor focus가 회복되지 않는 전이를 확인해 CHANGES_REQUESTED로 전환 | authored graph/plan 투영 구분·exact pointer completeness·projection/source focus lifecycle·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-07 [#62](https://github.com/Nochiski/Quant_study/pull/62)를 공개하고 implementation freeze `8f65c4f`·review HEAD `f09d94d`·1,711줄 size exception을 본문에 고정해 fresh review-only agent `review_p4_07` 검토로 IN_REVIEW 전환 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate·12절 size exception |
| 2026-09-05 KST | Codex | P4-07을 `8f65c4f`에 freeze: backend Execution Plan 순서·type/unit/history와 validation/compile diagnostic만 읽는 FactorGraph DAG, conditional의 predicate/true/false·saved subgraph·OUTPUT 표시, Graph↔source JSON Pointer 선택을 new/revision 양쪽에 연결. metadata-aware factor plan이 invalid/loading/incompatible이면 Backtest도 fail-closed. focused 61, frontend 348·typecheck·lint·build 통과 후 SELF_CHECK 전환. 1,711줄 중 테스트 645·독립 CSS 337이며 projection model/UI/route/run gate가 하나의 end-to-end acceptance라 12절 size exception 기록 | backend graph/plan/diagnostic SoT·projection/navigation/run 책임분리·P4-07 acceptance·12절 size exception |
| 2026-09-05 KST | Codex | #61 P4-06을 동일 reviewer APPROVE(P0/P1/P2 0), 최신 approval-doc HEAD CI 4/4 후 main에 병합(`f9a0e35`), 39/50(78%). 최신 main에서 P4-07 전용 worktree를 만들고 backend FactorGraph·Execution Plan·diagnostic만 읽는 DAG projection 구현을 시작 | 13.6 merge gate·graph/type/unit backend SoT·projection/edit 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_06` 최종 재검토가 completeness 단일 owner, 네 필드 누락 fail-closed, warning-only, exact backend canonical, draft identity 제거, document epoch/out-of-order, YAML/JSON source·view lifecycle과 실제 HEAD `63a2130` size breakdown을 재현하고 APPROVE(P0/P1/P2 0). 최신 원격 CI 4/4 통과로 APPROVED 전환 | 동일 reviewer 최종 승인·SoT/책임분리·latest CI/size gate |
| 2026-09-05 KST | Codex | P4-06 P1 3/P2 1 수정 `2dac1c2`와 frontend 339 전체 gate를 동일 `review_p4_06` 재검토로 보내기 위해 IN_REVIEW 전환. PLAN의 Head는 implementation freeze, 실제 review HEAD·최종 diff는 push 뒤 PR #61 본문/체크에서 고정 | blocking finding은 같은 reviewer만 재검토·diff/size/CI gate |
| 2026-09-05 KST | Codex | P4-06 review fix `2dac1c2`: `isCompleteCompileOutcome/currentCompile`을 completeness 단일 owner로 두어 spec·canonical·hash·schema 누락 시 phase·Save·Backtest·Execution Plan·projection을 전부 차단. JSON은 backend bytes를 무변형 표시하고 saved seed 직렬화를 제거했으며 Form에서 draft/0 identity를 숨김. 필드별 누락·warning-only·actual canonical shape·route mock 회귀 포함 focused 84, frontend 339·typecheck·lint·build·generated clean 후 SELF_CHECK 전환 | P1 3/P2 1 전부 회귀 고정·canonical SoT·document/projection/route 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_06`이 P0 0/P1 3/P2 1로 REQUEST_CHANGES. canonical 누락 compile을 projection만 막고 Save·Backtest·Execution Plan은 허용하는 completeness 판정 drift, backend canonical의 parse/stringify 및 saved seed 재생성, review HEAD/size 기록 불일치와 revision Form의 draft/0 identity 노출을 확인해 CHANGES_REQUESTED 전환 | compile completeness 단일 SoT·canonical bytes 무변형·revision provenance·정확한 freeze gate |
| 2026-09-05 KST | Codex | P4-06 PR [#61](https://github.com/Nochiski/Quant_study/pull/61)을 공개하고 code freeze `84dadf0`, 전체 frontend 329 tests와 20 files +889/-78 size breakdown을 본문에 고정해 fresh review-only agent `review_p4_06` 검토로 IN_REVIEW 전환 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate·12절 size exception |
| 2026-09-05 KST | Codex | P4-06 구현을 `84dadf0`에 freeze: backend compile의 canonical JSON·StrategySpec만 읽는 JSON/Form projection, same-document last-valid stale 표시, 저장 revision seed, YAML/JSON source view와 CodeMirror의 source·selection·undo 보존을 new/revision 양쪽에 연결. focused 54·frontend 329·typecheck·lint·build·generated clean 후 SELF_CHECK 전환. 총 860줄은 production/i18n 약 500줄과 state·route·접근성 회귀 약 360줄이며 projection·editor lifecycle을 분리하면 acceptance가 검증되지 않아 12절 size exception 기록 | canonical/hash backend SoT·projection/edit/view 책임분리·P4-06 acceptance·12절 size exception |
| 2026-09-05 KST | Codex | #60 P4-10을 동일 reviewer APPROVE(P0/P1/P2 0), 최신 승인 문서 HEAD CI 4/4 후 main에 병합(`20491b8`), 38/50(76%). 최신 main에서 P4-06 전용 worktree를 만들고 backend compile 결과만 읽는 canonical JSON·Form projection 구현을 시작 | 13.6 merge gate·backend semantic SoT·projection/edit 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_10` 최종 재검토가 metadata coherence, query fail-closed, JSON 경계, feedback capability 왕복, 실제 route factor/parse/dirty/focus/undo와 PR body size gate를 모두 재현하고 APPROVE(P0/P1/P2 0). reviewed HEAD CI 4/4 통과로 APPROVED 전환 | 동일 reviewer 최종 승인·review/size/CI gate |
| 2026-09-05 KST | Codex | P4-10 PR #60 본문을 317 tests와 최신 diff breakdown·`제약사항` size exception으로 갱신하고 capability 왕복 fix `bb66833`을 동일 `review_p4_10` 최종 재검토로 보내기 위해 IN_REVIEW 전환 | WORKFLOW 12절 PR body gate·동일 reviewer final gate |
| 2026-09-05 KST | Codex | P4-10 추가 P2를 `bb66833`에서 수정: document epoch와 source capability 전환마다 새 scope token을 발급해 ready→incompatible/loading→ready 복귀에도 과거 feedback이 재출현하지 않게 하고 왕복 회귀를 추가. focused 8, 전체 frontend 317·typecheck·lint·build 통과 후 SELF_CHECK 전환 | query lifecycle과 feedback 책임분리·동일 reviewer finding 회귀 |
| 2026-09-05 KST | Codex | P4-10 재검토 중 capability가 ready→incompatible→ready로 왕복하면 status 문자열 scope만으로 과거 feedback이 되살아나는 P2와 PR 본문이 최초 309 tests·597줄 수치로 남아 WORKFLOW size exception을 충족하지 못한 문서 P1을 확인해 CHANGES_REQUESTED 전환 | transient query lifecycle 정확성·PR body merge gate·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-10 P1 1/P2 3 수정과 reviewer 요구 route 회귀를 `e50e8f4`에 고정하고 전체 frontend 317·generated clean을 확인해 동일 `review_p4_10` 재검토로 IN_REVIEW 전환 | blocking finding은 같은 reviewer만 재검토·diff freeze |
| 2026-09-05 KST | Codex | P4-10 리뷰 수정 `e50e8f4`: schema-assist가 factor catalog coherence를 단일 판정하고 query 오류와 schema/contract hash·registry 세대 불일치를 각각 unavailable/incompatible로 fail-closed. snippet feedback을 document epoch/source capability에 귀속하고 JSON projection은 YAML-only로 명시. 실제 route factor 삽입·중복·syntax 무변경·dirty compile·focus·isolated undo 포함 47 focused, frontend 317 전체·typecheck·lint·build·generated clean 후 SELF_CHECK 전환. 리뷰 요구 회귀 8건으로 총 964줄이나 production/i18n 509줄이고 나머지는 tests 423·PLAN 32라 size exception 기록 | schema/contract/catalog coherence SoT·query/projection/hook/page 책임분리·review finding 회귀 고정 |
| 2026-09-05 KST | Codex | `review_p4_10`이 P0 0/P1 1/P2 3으로 REQUEST_CHANGES. schema만 성공한 metadata query 오류·schema/contract hash 또는 registry 세대 불일치를 ready empty로 오인하는 상태 계약을 blocking으로 확인. JSON projection의 실패 이유, feedback document epoch, route factor/dirty/undo 경계도 함께 수정하기 위해 CHANGES_REQUESTED 전환 | coherent metadata 상태·전문 사용자 오류 투명성·view/document lifecycle 책임 |
| 2026-09-05 KST | Codex | P4-10 #60을 공개하고 code freeze `0004e49`를 fresh review-only agent `review_p4_10`에 전달하기 위해 IN_REVIEW 전환. blocking finding은 같은 reviewer에게만 재검토 요청하며 구현 diff를 고정 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate |
| 2026-09-05 KST | Codex | P4-10 UI를 `0004e49`에 freeze: P4-05의 status-gated catalog/edit plan을 hook이 CodeEditor transaction에 연결하고 5영역 목록·상태·접근 가능한 feedback을 new/revision 양쪽에 합성. 중복 kind 라벨을 제거해 시각 소음을 줄였으며 focused 24·frontend 309·typecheck·lint·build 통과 후 SELF_CHECK 전환 | P4-05 SoT 무복제·query/hook/UI/page 책임분리·600줄 gate·전문 사용자 keyboard/IME UX |
| 2026-09-05 KST | Codex | #59 P4-05를 동일 reviewer 최종 승인과 승인 문서 포함 latest HEAD CI 4/4 통과 후 main에 병합(`191b902`), 37/50(74%). 최신 main에서 P4-10 전용 worktree를 만들고 stash의 UI/page 파일만 선별 복원해 새 P4-05 core를 보존한 채 구현 시작 | 13.6 merge gate·core/UI 책임분리·안전한 stash 복원 |
| 2026-09-05 KST | Codex | `review_p4_05` 2차 검토가 이전 P1 4/P2 2 해소를 실제 runtime fixture·CodeMirror undo·wide schema로 재현하고 APPROVE(P0 0/P1 0/P2 1). latest CI 4/4도 통과해 APPROVED 전환. backend가 생성하지 않는 순수 `$ref`-only cycle P2는 P6-04 hostile schema gate에 명시 | 동일 reviewer 최종 승인·backend schema SoT·latest CI·비차단 residual 추적 |
| 2026-09-05 KST | Codex | P4-05 수정 diff와 전체 gate를 고정하고 기존 단일 reviewer `review_p4_05`의 2차 검토를 위해 IN_REVIEW로 전환. 새 reviewer를 추가하지 않으며 latest PLAN-only HEAD CI도 병합 전 다시 확인 | 13.4 동일 reviewer 재검토·13.6 latest CI merge gate |
| 2026-09-05 KST | Codex | P4-05 리뷰 수정 `e3f38e0`: FactorSignal 필드의 `x-authoring-source/default/identity`를 backend model/schema 단일 owner로 만들고 frontend는 완전한 mapping만 일반 투영하도록 변경. catalog 상태·동일 identity·CRLF·selection bounds·recursive schema를 fail-closed하고 snippet transaction을 독립 undo group으로 고정. backend 904·frontend 302 전체 gate와 OpenAPI/SDK clean 확인 후 SELF_CHECK 전환 | P1 4/P2 2 전부 회귀 고정·SoT·projection/editor 책임분리·12절 size exception |
| 2026-09-05 KST | Codex | `review_p4_05`가 P0 0/P1 4/P2 2로 REQUEST_CHANGES. schema/catalog 밖 `weight: 1`·generic factor 생성, 동일 catalog factor 재삽입, CRLF 혼합 EOL, 직전 타이핑과 합쳐지는 undo group을 blocking으로 확인했고 범위 밖 selection·recursive schema도 방어하기 위해 CHANGES_REQUESTED로 전환 | backend authoring SoT·fail-closed catalog·exact source 보존·editor transaction 책임·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-05 #59를 공개하고 code freeze `d3068fc`를 fresh review-only agent `review_p4_05`에 전달하기 위해 IN_REVIEW로 전환. 리뷰 중 core 구현 diff를 고정하며 blocking finding은 같은 reviewer에게 재검토 요청 예정 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate |
| 2026-09-05 KST | Codex | P4-05 core를 `d3068fc`에 freeze. runtime schema의 property/default/enum과 caller가 제공한 coherent factor catalog graph만 투영하고, root partial key·기존 factor array의 cursor indentation을 보존한 전체 YAML 1.2 preflight edit을 editor-agnostic 단일 transaction으로 적용. focused 16, frontend 296, typecheck·lint·build 통과 후 SELF_CHECK 전환 | schema/factor SoT·projection/edit/editor 책임분리·600줄 gate·P4-05 acceptance |
| 2026-09-05 KST | Codex | P4-05 초기 diff가 UI/page까지 포함해 1,250줄이 된 것을 self-check에서 확인. P4-05를 schema/catalog projection·cursor/indent edit plan·YAML preflight·editor transaction core 584줄로 고정하고, 사용자 표시·page composition·i18n은 P4-10으로 분리해 총 50 PR로 조정 | PR 600줄 원칙·snippet 의미와 표시/페이지 책임분리·독립 review 검출력 |
| 2026-09-05 KST | Codex | #58 P4-09를 독립 reviewer 승인과 latest PLAN-only HEAD backend/frontend CI 통과 후 main에 병합(`8a2ebfc`), 36/49(73%). 최신 main에서 P4-05 전용 worktree를 만들고 backend runtime schema/catalog를 SoT로 하는 canonical snippet transaction 구현을 시작 | 13.6 merge gate·schema/catalog SoT·editor transaction 책임분리 |
| 2026-09-05 KST | Codex | P4-09 독립 리뷰가 P0 0/P1 0으로 APPROVE. backend plan의 무추론 투영, P4-04 query와 P4-09 projection/page 책임 경계, stale/invalid/error/plan-null 차단, exact pointer navigation과 키보드 접근성을 재확인했고 latest 원격 CI 4/4 통과. route→editor 실통합 테스트와 plan-null provenance 표시는 비차단 후속으로 추적 | PR별 독립 review gate·SoT·책임분리·latest CI |
| 2026-09-05 KST | Codex | P4-09 #58을 공개하고 code freeze `160fad4`를 fresh review-only agent `review_p4_09`에 전달하기 위해 IN_REVIEW로 전환. 리뷰 중 구현 diff를 고정하고 blocking finding은 같은 reviewer에게 재검토 요청 예정 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate |
| 2026-09-05 KST | Codex | P4-09 UI를 `160fad4`에 고정: P4-04 query의 plan step만 사용해 topological order·input/output type/unit·history·registry/dataset·graph/plan hash를 표시하고 factor/node/input 선택을 exact pointer로 연결. stale/invalid/pending/loading/error/incompatible/plan-null 상태에서 이전 plan을 숨기며 new/revision page에 동일 projection을 합성. focused 21·frontend 291·typecheck·lint·build 통과 후 SELF_CHECK 전환. 806줄은 상태·접근성 회귀 14개와 component/page composition이 하나의 독립 acceptance라 600줄 예외 기록 | backend plan SoT·표시/query/page 책임분리·P4-09 acceptance·12절 size exception |
| 2026-09-05 KST | Codex | #57 P4-04를 동일 reviewer 최종 승인과 latest HEAD CI 4/4 통과 후 main에 병합(`25d8b45`), 35/49(71%). 최신 main에서 P4-09 전용 worktree를 만들고 보관한 Execution Plan UI projection을 query SoT에 연결하는 구현을 시작 | 13.6 merge gate·backend plan 표시 SoT·query/UI 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_04` 4차 재검토가 이전 P1 전부 해소와 FactorSignal numeric output gate의 SoT/책임분리를 확인해 APPROVE(P0 0/P1 0). 비차단 P2 validate provenance는 실행 권한을 주지 않는 view-only stale window로 후속 범위에 남기고 P4-04를 APPROVED·latest CI merge gate로 전환 | 동일 reviewer 최종 승인·실행 경로 fail-closed·13.6 merge gate |
| 2026-09-05 KST | Codex | P4-04 추가 P1을 `70aeba8`에서 수정: generic explain은 group/boolean/scalar typed plan을 유지하되 executable FactorSignal 경계는 종목별 점수인 numeric_series만 허용하고 owner registry의 `strategy.expression.output_type`으로 portfolio/backtest를 동일 차단. 세 비수치 타입 422와 유효 sector group backtest COMPLETED·preview tape hash 일치를 고정해 backend 903 전체 gate 후 동일 reviewer 4차 재검토로 전환 | 범용 DAG와 실행 가능 signal 책임분리·semantic code 단일 owner·silent empty backtest 차단 |
| 2026-09-05 KST | Codex | `review_p4_04` 3차 검토에서 이전 P1은 모두 해소됐으나 category/boolean graph output을 FactorSignal 최종 출력으로 사용하면 evaluator가 값을 `None`으로 바꾼 뒤 portfolio/backtest가 성공하는 P1을 재현. 범용 explain의 typed DAG 허용은 유지하고 실행 경계에서 numeric output만 owner 등록 semantic code로 차단하도록 CHANGES_REQUESTED 전환 | generic graph 설명과 executable FactorSignal 책임분리·의미 없는 성공 backtest fail-closed |
| 2026-09-05 KST | Codex | P4-04 잔여 P1을 `a2fa034`에서 수정: PortfolioDesignService가 FactorResearch와 동일한 FactorMetadataPort를 명시적 dependency로 받아 모든 graph plan을 같은 snapshot/field contract로 compile하고 raw snapshot 불일치를 fail-closed. `classification.sector`를 공개 equity catalog·raw loader·factor metadata가 공유하는 category 선언으로 통합했으며 explain/portfolio plan exact parity, 숫자 group의 동일 오류 코드, sector group preview/backtest 성공, snapshot drift의 portfolio/backtest 차단을 회귀 테스트로 고정. backend 900·frontend 277 전체 gate와 generated deterministic 확인 후 동일 reviewer 3차 재검토로 전환 | metadata/catalog/loader 단일 SoT·application facade dependency 명시·실행 경로 책임분리·동일 reviewer re-review |
| 2026-09-05 KST | Codex | `review_p4_04` 2차 검토에서 기존 P1 3건 중 2건 해소를 확인했으나 explain과 실제 portfolio/backtest가 서로 다른 field metadata로 plan을 compile해 unit/hash·group 승인 결과가 갈리는 P1 1건을 재현. CHANGES_REQUESTED로 되돌리고 동일 FactorMetadataSnapshot을 실행 compiler·raw loader까지 연결 예정 | Execution Plan과 실제 실행 path parity·truthful pipeline SoT |
| 2026-09-05 KST | Codex | P4-04 P1 3건을 `f970dc6`에서 수정: backend FactorMetadataPort가 field/group contract와 snapshot을 원자적으로 resolve하고 group key를 plan required fields의 domain SoT에 포함; explain plan-null에도 registry/snapshot provenance 제공; frontend compiled/runtime schema 및 모든 response provenance를 차단. backend 897·frontend 277 전체 gate와 generated clean 후 같은 `review_p4_04` 재검토로 전환 | frontend 타입 추론 금지·metadata/provenance backend SoT·동일 reviewer re-review |
| 2026-09-05 KST | Codex | `review_p4_04`이 P1 3건(group metadata를 equity catalog로 표현 불가, plan-null 응답 registry provenance 부재, compiled spec/runtime schema 세대 미결합)을 확인해 CHANGES_REQUESTED로 전환. frontend 추론을 만들지 않고 backend-owned metadata·response contract와 schema gate를 수정한 뒤 같은 reviewer에게 재검토 예정 | SoT·모든 canonical graph 지원·response provenance·동일 reviewer gate |
| 2026-09-05 KST | Codex | P4-04 #57을 공개하고 code freeze `24f7c22`를 fresh review-only agent `review_p4_04`에 전달하기 위해 IN_REVIEW로 전환. 리뷰 중 구현 diff는 고정하며 수정이 필요하면 같은 reviewer에게 재검토를 요청 | PR별 독립 reviewer 정확히 1명·13.3~13.4 review gate |
| 2026-09-05 KST | Codex | P4-04 code를 `24f7c22`에 고정. current valid spec만 전체 factor explain query로 만들고 schema/contract/dataset/registry coherence, catalog completeness, response registry drift, AbortSignal, exact pointer mapping을 fail-closed로 검증. focused 5·frontend 전체 275·typecheck·lint·build를 통과해 SELF_CHECK로 전환 | backend plan/type/unit/hash SoT·invalid/stale 실행 금지·query cancellation·13.2 self-check |
| 2026-09-05 KST | Codex | P4-04 구현을 query orchestration/version gate/source mapping model(P4-04)과 실제 Execution Plan panel/page wiring(P4-09)으로 분할. 단일 PR 예상 diff가 600줄을 넘고 query correctness와 표시 책임을 독립 검토할 수 있어 총 49 PR로 조정 | 12절 PR 크기 규칙·query/UI 책임분리·독립 review 검출력 |
| 2026-09-05 KST | Codex | #56 P6-07을 동일 reviewer 최종 승인과 latest CI 4/4 통과 후 main에 병합(`84d7c88`), 34/48(71%). 최신 main에서 P4-04 전용 worktree를 만들고 backend factor compiler를 SoT로 하는 Execution Plan·source node selection projection 구현을 시작 | 13.6 merge gate·plan/type/unit 재계산 금지·책임분리 |
| 2026-09-05 KST | Codex | `review_p6_07` 최종 재확인이 P1 해소와 strictPort·앱 HTML marker 보강을 확인해 APPROVE(P0 0/P1 0)를 유지했고 최신 PLAN-only HEAD 원격 CI 4/4도 통과. P6-07을 APPROVED로 전환해 merge gate에 진입 | 동일 reviewer 최종 승인·latest CI·13.6 merge gate |
| 2026-09-05 KST | Codex | 사용자 요청에 따라 P6-06/Phase 6 완료 조건에 backend+frontend 실제 동시 구동, 전략 생성·오류 유도/수정·저장/복구·백테스트·trace/risk·history/diff 실사용 시나리오와 실행 보고서를 추가. P6-07 reviewer APPROVE 후 비차단 P2도 `2e53089`에서 Vite `--strictPort`와 앱 root HTML marker로 보강해 false-positive를 차단하고 최종 재확인으로 전환 | 사용자 최종 E2E 요구·실행 증거·smoke identity |
| 2026-09-05 KST | Codex | `review_p6_07`이 root lock·delegate test가 CI에서 수집되지 않고 실제 HTTP smoke가 자동화되지 않은 P1 1건을 확인. `8689075`에서 Linux CI에 root lock·unit·ruff·pyright와 실제 `uv run server` health 200 process smoke를 연결하고 root `npm run dev` Vite HTTP smoke도 함께 고정해 동일 reviewer 재검토로 전환 | 실제 명령 계약·cross-platform packaging 회귀 차단·동일 reviewer gate |
| 2026-09-05 KST | Codex | P6-07 code diff를 `1648051`로 freeze하고 #56 공개. PLAN-only 상태 커밋 외 변경을 막고 `review_p6_07`에 root/backend entrypoint 계약, cross-platform process forwarding, packaging·lock·SoT/책임분리 독립 검토를 배정 | 13.2~13.4·PR별 독립 reviewer gate |
| 2026-09-05 KST | Codex | P6-07 구현을 `1648051`에 고정: root npm은 frontend Vite에만 위임하고 root uv wrapper는 backend의 단일 server entrypoint로 args/stdin/out/exit를 전달. Uvicorn target·host·port·reload는 backend bootstrap만 소유. root/backend 명령 help, 실제 Vite HTTP 200, Uvicorn health 200·reload·Ctrl+C와 backend 896·frontend 270 전체 회귀를 확인해 SELF_CHECK로 전환 | Vite/Uvicorn 설정 SoT·root 경로 위임 책임·실제 process smoke |
| 2026-09-05 KST | Codex | #55 P4-03을 독립 reviewer 최종 승인과 latest backend/frontend CI 4개 통과 후 main에 병합(`ebc16c2`). 사용자 요청의 root `npm run dev`·`uv run server`를 SoT 중복 없이 제공하기 위해 P6-07을 추가하고 전용 worktree에서 시작 | 13.6 merge gate·사용자 추가 범위·개발 진입점 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_03` 재검토가 root pointer 원본/표시 분리와 clipboard request ordering, 이전 acceptance 회귀를 확인해 최종 APPROVE(P0 0/P1 0). P4-03을 APPROVED로 전환하고 최신 HEAD CI merge gate 확인 단계로 이동 | 동일 reviewer 승인·SoT/책임분리·13.6 merge gate |
| 2026-09-05 KST | Codex | `review_p4_03`이 P0 0/P1 0으로 APPROVE. 비차단 P2도 `752ed60`에서 선제 보강하여 빈 root JSON Pointer를 `"" (문서 루트)`로 정확히 표시·복사하고 request sequence로 늦은 clipboard 응답의 최신 상태 덮어쓰기를 차단. focused 18·전체 270·typecheck·lint·build 통과 후 동일 reviewer 재검토 요청 | 정확한 pointer 의미·async UI 최신 요청 owner·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-03 code diff를 `454282b`로 freeze하고 #55 공개. PLAN-only 상태 커밋 외 변경을 막고 `review_p4_03`에 SoT·책임분리·dedupe·stale·clipboard·접근성·P3-04 회귀 독립 검토를 배정 | 13.2~13.4·PR별 독립 reviewer gate |
| 2026-09-05 KST | Codex | P4-03 구현을 `454282b`에 고정: backend/parser diagnostic을 재분류하지 않는 exact-dedupe projection, 4종 filter, error/warning section, editor jump, JSON Pointer/node ID copy와 stale·clipboard failure 접근성을 구현. focused 16·전체 268·typecheck·lint·build·diff-check 통과 후 SELF_CHECK로 전환 | diagnostic SoT·projection/navigation 책임분리·P4-03 acceptance |
| 2026-09-05 KST | Codex | #53 P4-02를 독립 reviewer 승인과 latest backend/frontend CI 4개 통과 후 main에 병합(`3284fe0`). 최신 main에서 P4-03 전용 worktree를 만들고 Problems panel 구현을 시작 | 13.6 merge gate·진단 SoT/표시 projection 책임분리 |
| 2026-09-05 KST | Codex | `review_p4_02` 4차 재검토에서 shared requiredness와 이전 P1 4건을 모두 실제 backend fixture·synthetic mismatch·Inspector/hover/completion 경로로 재확인해 APPROVE(P0 0/P1 0). focused 56·전체 262·typecheck·lint·build·PLAN·diff-check와 code HEAD 원격 CI 4개 통과를 확인하고 P4-02를 APPROVED로 전환 | 독립 reviewer 승인·SoT/책임분리·13.6 merge gate |
| 2026-09-05 KST | Codex | 3차 리뷰의 shared requiredness P1을 `850bd91`에서 수정: navigator가 property schema와 별도로 모든 applicable branch의 required 합의를 `propertyRequired`에 보존하고, Contract projection은 branch-specific row가 없을 때 이 backend schema 값을 사용. FactorNode missing/unknown kind의 `/node_id`·`/kind`, Parameter union 공통 required 필드, branch별 required 불일치 synthetic schema와 hover를 회귀 테스트로 고정해 focused 56·전체 262·typecheck·lint·build 통과 후 동일 reviewer 4차 검토로 전환 | backend schema required SoT·공통/상이 required 분리·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | `review_p4_02` 3차 리뷰가 branch-dependent first-branch 차단을 확인했으나, 모든 FactorNode branch가 동일하게 required로 선언한 shared `/node_id`가 navigator traversal에서 requiredness를 잃어 Inspector·hover에 `required:null`로 표시되는 P1 1건을 재현하여 REQUEST_CHANGES. schema가 branch 간 합의한 requiredness를 resolution까지 전달하고 `/node_id`·`/kind`·Parameter union·상이 requiredness를 회귀 테스트로 고정하는 수정으로 전환 | backend schema required SoT·shared property projection·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | 2차 리뷰의 남은 P1을 `89ae776`에서 수정: unresolved union property의 branch별 가용성·required·schema를 navigator `propertyVariants`가 보존하고 동일하지 않으면 first-branch traversal을 금지. Inspector는 raw 값과 관련 branch만 표시하며 enum/default/range/catalog 등을 차단하고, hover는 kind 선택 필요를 설명하며 value completion은 비활성화했다가 valid kind에서 복구. missing/unknown kind `operator`·`field_id`, 상이 enum, falsy example 회귀를 추가해 focused 52·전체 258·typecheck·lint·build 통과 후 동일 reviewer 3차 검토로 전환 | union schema SoT·branch selection 책임·fail-closed·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | `review_p4_02` 2차 리뷰가 이전 P1 중 null example과 schema-contract coherence 해소를 확인했으나, missing/unknown kind에서 동명 `operator`의 첫 matching branch enum을 Inspector·hover·completion이 오표현하는 P1 1건을 재현하여 REQUEST_CHANGES. schema navigator가 branch-dependent property 자체를 명시하고 branch 선택 전 constraint/catalog projection을 차단하는 수정으로 전환 | union/discriminator SoT·first-branch 추론 금지·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | P4-02 리뷰 P1 3건을 `99acad3`에서 수정: wire `example: null`은 부재로 해석하고, union kind는 backend variants에 속할 때만 branch를 선택하며 미선택/unknown은 variants만 표시, schema-contract hash/version compatibility를 단일 helper로 만들고 assist metadata도 이를 공유하여 drift 동안 schema-only로 fail-closed. 양방향 arrival race와 Inspector·hover 회귀를 추가해 focused 33·전체 frontend 245·typecheck·lint·build 통과 후 동일 reviewer 2차 검토로 전환 | backend wire 의미 SoT·discriminator branch 책임·coherence 단일 owner·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | `review_p4_02` 독립 리뷰가 P0 0/P1 3으로 REQUEST_CHANGES: 실제 wire의 `example: null`을 예시로 오인, 미선택 discriminator를 첫 union branch const로 오표현, schema-contract 비동기 drift 중 Inspector와 assist의 coherence gate 불일치를 재현. focused 27·전체 239·typecheck·lint·build·PLAN·원격 CI 4개는 통과했으며 세 계약 결함 수정 후 동일 reviewer 재검토로 전환 | backend wire 의미 SoT·union 선택 책임·schema/contract coherence 단일 owner |
| 2026-09-05 KST | Codex | P4-02 code diff를 `b24b7b6`으로 freeze하고 #53 공개: URL path를 runtime schema·FieldContract·버전 고정 catalog의 단일 projection으로 연결하고 raw/display·range·description·stage·union·PIT·registry provenance 및 실패 상태를 구현. frontend 239·typecheck·lint·build 통과 후 `review_p4_02` 독립 리뷰 배정 | 13.2~13.4·SoT/책임분리 |
| 2026-09-05 KST | Codex | #52 P4-01을 독립 reviewer 승인과 중복 backend/frontend CI 4개 통과 후 main에 병합(`3d7b996`). 최신 main에서 P4-02 전용 worktree를 만들고 Contract Inspector 구현을 시작 | 13.6 merge gate·SoT/책임분리 |
| 2026-09-05 KST | Codex | `review_p4_01` 3차 재검토에서 pending cursor의 documentEpoch·routePointer·targetSourceVersion exact match와 이전 P1 5건을 모두 재확인하여 APPROVE, P0/P1 0 판정. P4-01을 APPROVED로 전환하고 최신 원격 CI merge gate 확인 차수로 진입 | 독립 reviewer 승인·13.6 merge gate |
| 2026-09-05 KST | Codex | P4-01 2차 리뷰의 남은 P1 1건을 `be8c183`에서 수정: pending edit cursor를 캡처 당시 URL path와 정확한 target sourceVersion에 귀속하고, direct URL/back-forward가 같은 commit에 도착하거나 parser debounce가 version을 건너뛰면 오래된 offset을 폐기. 재현 2경로를 hook test로 고정하고 focused 38·전체 frontend 232·typecheck·lint·build 통과 후 동일 reviewer 3차 검토로 전환 | URL selection SoT·document identity·동일 reviewer 재검토 |
| 2026-09-05 KST | Codex | `review_p4_01`이 P1 5건을 발견: collection/root selection 재발행, edit cursor stale path, collapse focus·roving tabindex·ARIA ownership, frontend `_id` identity 추론, malformed JSON Pointer 보존. `75570fb`에서 programmatic transaction 1회 소비·fresh sourceVersion parse 후 cursor 재매핑·collapse origin 분리·visible tree roving·treeitem/group 포함 관계·`x-defines` 전용 identity·shared RFC 6901 validator로 모두 수정. focused 37, 전체 frontend 231, typecheck·lint·build 통과 후 동일 reviewer 재검토 차수로 전환 | 13.3·SoT/책임분리·동일 reviewer 재검토 |
| 2026-09-04 KST | Codex | P4-01 구현을 `bc84510`으로 freeze하고 #52 공개: backend runtime schema와 parser source map을 정본으로 하는 Strategy Outline, URL-owned JSON Pointer↔CodeMirror 양방향 연결, same-epoch 오류 보존, schema `x-defines` 기반 index/semantic ID 분리, ARIA tree keyboard 구현. frontend 227·build 통과 후 `review_p4_01` 독립 리뷰 배정 | 13.3·SoT/책임분리 |
| 2026-09-04 KST | Codex | #48 P3-07 독립 리뷰에서 stale 409 document 오염과 human message/`history.total` 최신 revision 이중 추론 P1 2건을 발견. repository-owned `latest_revision`을 exception→HTTP model→OpenAPI/SDK→UI로 단방향 연결하고 save status를 document epoch에 귀속(c1951b6). 동일 리뷰어 blocker 0 재승인, frontend 221·backend 게이트 및 원격 CI 4개 통과 후 main 병합(30baf41), Phase 3 종료 | 인수 후 merge gate·SoT/책임분리 |
| 2026-09-04 KST | Codex | #47 P3-06 독립 리뷰에서 저장 중 추가 편집 old key, 복구 identity fail-open, diff baseline 이중 owner P1 3건을 발견. accepted save의 `savedVersion`을 정본으로 old key를 정리하고, schema/format/base/hash 완전 일치 restore 및 autosave-owned baseline으로 수정(201479c). 동일 리뷰어 blocker 0 재승인, frontend 217·build 및 원격 CI 4개 통과 후 main 병합(8b7b1a4) | 인수 후 merge gate·SoT/책임분리 |
| 2026-09-04 KST | Codex | #46 P3-05 독립 리뷰에서 dirty inline orphan run과 stale 응답 route overwrite P1 2건을 발견. run 상태를 document epoch/version/pathname에 귀속하고 accepted run ID 재개·fresh save event 단회 소비로 수정(b1c5c51), 테스트 async budget도 shared owner로 통합(c063a4b). 동일 리뷰어 blocker 0 재승인, frontend 205·build 및 원격 CI 4개 통과 후 main 병합(4a3dc14) | 인수 후 merge gate·SoT/책임분리 |
| 2026-09-04 KST | Codex | #45 P3-04 독립 리뷰에서 unknown-key가 값 범위를 가리키는 P1을 발견해 key range 우선과 회귀 테스트로 수정(b5c37a6). 동일 리뷰어 blocker 0 재승인, frontend 199·build 및 원격 CI 4개 통과 후 main 병합(0167fb9) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #44 P3-03을 latest main 기준 독립 재검토하여 blocker 0, runtime schema/OpenAPI 정합과 backend 13·frontend 189 및 원격 CI 4개 통과 후 main 병합(20c1447) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #43 P2-04를 latest main 기준 독립 재검토하여 blocker 0, 저장 중 추가 편집·dirty guard·dialog 접근성을 확인하고 frontend 167·build 및 원격 CI 4개 통과 후 main 병합(03600de) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #42 P3-02를 latest main 기준 독립 재검토하여 blocker 0, frontend 160·build와 원격 CI 4개, editor gzip 136.37 kB 예산 통과 후 main 병합(0b89cfd) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #41 P3-01의 frontend parser diagnostic code를 backend wire owner(`document.*`/`yaml.*`/format syntax)와 정합화. 동일 리뷰어 재검토 blocker 0, frontend 156·backend codec parity 101·원격 CI 4개 통과 후 main 병합(d52885b) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #40 P2-03의 responsive nav 상태·ARIA 불일치와 가짜 성공색을 수정하고 동일 리뷰어 재검토 blocker 0, frontend 108·build와 원격 CI 4개 통과 후 main 병합(f5c4309) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #39 P2-02를 main 대상으로 전환해 독립 재리뷰 blocker 0, frontend 98·build와 원격 CI 4개 통과 후 main 병합(2d7bcb5) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #38 P2-01을 latest main에 통합하고 direction badge 대비를 9.08:1로 보강. 독립 재리뷰 blocker 0, frontend 90·build와 원격 CI 4개 통과 후 main 병합(08726e3) | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #50 P1-10의 재리뷰·전체 로컬 게이트·원격 backend/frontend CI 통과 후 main 병합(3df3980). Phase 1을 10/10 완료로 확정 | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #49 CI·최종 재리뷰 통과 후 main 병합(1ffc0ff). P1-10을 latest main에 통합(d7fff5e), initializer 상대 import 우회 회귀와 owner gate를 재검토하여 `review_p1_10` APPROVE, backend 883·frontend 80 및 전체 게이트 통과 | 인수 후 merge gate |
| 2026-09-04 KST | Codex | #49 P1.5-05를 latest main에 통합하며 saved/inline provenance와 raw observation warning을 함께 보존하도록 충돌 해소, Linux file URI를 손상시키던 artifact 존재 assertion을 wire 계약 검증으로 교체, PLAN tracker CRLF 지원. backend 865·frontend 80 및 전체 게이트 통과, `review_p15_05_merge` APPROVE | 인수 후 merge gate |
| 2026-09-04 KST | Claude | 사용자 지시로 origin/main fast-forward push(c174452→73d812e) 후 리뷰 중 브랜치 12개를 Stacked PR로 공개: #38 P2-01 → #39 P2-02 → #40 P2-03 → #41 P3-01 → #42 P3-02 → #43 P2-04 → #44 P3-03 → #45 P3-04 → #46 P3-05 → #47 P3-06 → #48 P3-07(스택 끝, 리뷰 후속 수정 1eecbb2·8933bcc·9581811 포함), #49 P1.5-05(main 기준). P2-04·P3-01~04 REQUEST_CHANGES 수정 완료, 같은 리뷰어 재검토 요청. 게이트: typecheck·lint·vitest 187·build, backend 652 | 사용자 지시 |
| 2026-09-04 KST | Claude | P3-07 착수·diff freeze 8c3d074 (branch `feat/p3-07-conflict-safety`, P3-06 위; 409 시 문서 보존, ConflictBanner(서버 최신/현재 기준 리비전, 서버본 열기 링크, 현재 문서 복사, Diff 열기→P1-08 semantic diff 표), strategyDiffQuery·diffStrategyRevisions 래퍼; vitest 176·typecheck·lint·build clean), review_p3_07(opus) 배정 → IN_REVIEW. Phase 3 PR 7/7 구현 완료, 리뷰·merge 대기 | 13.3 |
| 2026-09-04 KST | Claude | P3-06 착수·diff freeze 7dcb3bf (branch `feat/p3-06-autosave-recovery`, P3-05 위; draft-store(localStorage, base key `strategyId@rev`/new, 손상·quota 내성), useAutosave(dirty 800ms 후 기록, 저장 성공 시 정리, 로드 시 원본과 다른 복구본 제안), RecoveryBanner(줄 diff 요약, 복구/삭제, schema 불일치 시 원문 다운로드), lineDiffSummary; vitest 175·typecheck·lint·build clean), review_p3_06(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P3-05 착수·diff freeze 0fb303b (branch `feat/p3-05-toolbar-cutover`, P3-04 위; 15 files +719/−33; DocumentToolbar(schema/source hash/spec hash·dirty·Validate/Save/Backtest), canSave는 현재 compile 성공 시만, decideBacktestSource(saved_revision ↔ inline_draft ↔ blocked), useRunBacktest→/research/backtests/$runId, IDE meta 확장, builder 문구 YAML-first; vitest 168·typecheck·lint·build clean), review_p3_05(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P3-04 착수·diff freeze 0a6b28c (branch `feat/p3-04-semantic-markers`, P3-03 위; compile debounce 300ms·AbortController·버전 불일치 응답 폐기, pointer→range(프론트 parse map→backend range→조상), 문제 목록(오류/경고 분리, 클릭 시 selection 이동), 전송 실패는 capability 진단; vitest 163 통과, 브라우저에서 compile 진단·completion 실동작 확인(backend CORS 5173)), review_p3_04(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P3-03 착수·diff freeze 380511a (branch `feat/p3-03-schema-assist`, P2-04 위; backend: id 필드 dataclass metadata → 스키마 `x-catalog`/`x-reference`, FieldContract.catalog/reference, runtime-schema.json 골든 fixture + export 도구, pytest 652; frontend: cursor→pointer, schema navigator(kind union), completion/hover 소스, hoverTooltip, 카탈로그 쿼리; typecheck·lint·vitest 156·build clean), review_p3_03(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P2-04 착수·diff freeze 09ceacf (branch `feat/p2-04-revision-loader`, P3-02 위 + main 7efe811 병합; 23 files +1029/−157; DocumentSource new/revision, document API wrapper·query, create/revise 저장(expected_revision, 409/422 상태), URL 전환, DirtyLeaveGuard(useBlocker), MSW 라우트 테스트 5건; typecheck·lint·vitest 136·build clean), review_p2_04(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | active PR 상한 규칙 보완: `parallel_window`에 전부 나열된 stack은 한 line으로 보고 2개 제한 초과 허용(WORKFLOW 13절, update-plan-progress.ps1). P2/P3 stack 5개를 window로 선언 | 규칙 갱신 |
| 2026-09-04 KST | Claude | P1-06(2차)·P1-09(2차) APPROVE 수신, 잔여 소항목 반영(54eb349) → P1-05~09 스택 main merge(7efe811, container 충돌 해소), MERGED. Phase 1 9/9 완료, Phase 1 종료 감사 착수. P2-03 시안(v3) 정합 반영(1814817·259b682) 후 재리뷰 요청, P3-01(0925d1c)·P3-02(429615b)에 전진 병합. Phase 1.5 감사 후속(D-001~007) 구현 서브에이전트 착수 | 13.6 merge gate |
| 2026-09-04 KST | Claude | P1-02 APPROVE(4차 b4a34f6) → main merge(df32c09), P1-03 → main merge(90a14bc, container 충돌 해소), MERGED. main 607 passed(Rust core 제외), SDK clean. P1-05~09·P2-01~03·P3-01/02 리뷰 결과 수신 중(요약 재전송 요청) | 13.6 merge gate |
| 2026-09-04 KST | Claude | P3-02 착수·diff freeze 579bce8 (branch `feat/p3-02-code-editor`, P3-01 위; CodeMirror 6 pin, editor chunk gzip 136 KB ≤ 200 KB 예산; typecheck·lint·vitest 124·build clean), review_p3_02(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P3-01 착수·diff freeze eebf6e3 (branch `feat/p3-01-document-state`, P2-03 위; 8 files +1075/−137; typecheck·lint·vitest 120·build clean), review_p3_01(opus) 배정 → IN_REVIEW | 13.3 |
| 2026-09-04 KST | Claude | P2-03 착수·diff freeze (branch `feat/p2-03-ide-layout`, P2-02 위; typecheck·lint·vitest 98·build clean), review_p2_03(opus) 배정 → IN_REVIEW | 13.3 |
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
