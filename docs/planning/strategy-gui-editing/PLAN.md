---
plan_version: 2
project: strategy-gui-editing
project_status: COMPLETE
current_phase: complete
current_pr: none
active_prs: []
parallel_window: [P1-01, P1-02, P1-03, P1-04, P1-05, P2-01, P2-02, P1-06, P2-03, P3-01, P3-02, P4-01, P4-02, P4-03, P4-04, P4-05, P5-01, P5-02, P5-03]
last_updated: 2026-09-18T21:15:41+09:00
last_updated: 2026-09-18T21:57:30+09:00
planned_prs: 19
merged_prs: 19
approved_prs: 19
progress_percent: 100
---

# schema 1.1 · Form/Graph 편집 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `COMPLETE` |
| Current phase | `complete` |
| Current/next PR | `none` |
| Active PR | none |
| Progress | `19 / 19 merged (100%)` |
| Approved | `19 / 19` |
| Aggregated at | `2026-09-18 21:15 KST` |
| Aggregated at | `2026-09-18 21:57 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. frontmatter와 위 표, Phase 집계는
[update-plan-progress.ps1](./tools/update-plan-progress.ps1)이 생성하며 직접 수정하지 않는다.

## 현재 결정

- 2026-09-17 제품 소유자 결정: YAML 정리는 schema 1.1(hash 변경 포함)까지 전부, GUI는 Form과
  Graph 모두, stacked PR로 범위를 나눈다.
- 저장된 1.0 revision은 동결 이력이다. 재해시·DB 마이그레이션을 하지 않고 업그레이드 → 새 revision.
- Form/Graph 편집은 JSON Pointer 범위의 source 트랜잭션이다. 별도 편집 모델을 두지 않는다(ADR D5 개정).
- P1 backend PR은 `backend/openapi.json`만 재생성하고 frontend generated SDK는 P2-01이 갱신한다.
- reviewer 서브에이전트는 Opus로만 배정한다. Phase 종료마다 SoT·책임분리 점검 서브에이전트를 돌린다.
- 알려진 기존 결함(cleanup 후보): `_explanation.py`가 `selection_method: percentile`에서도 `selection_count`를 "N종목"으로
  요약해 compile의 적용 불가 경고와 모순된다(P1-05 리뷰 P2-005). 실행 결과에는 영향 없음.
- 알려진 기존 결함(이 initiative 범위 밖, 별도 이슈 후보): safe codec이 YAML 1.1 value 태그 스칼라 `=`를
  `parse()` 밖으로 `ConstructorError`로 던져 `/compile`·`/upgrade`가 500이 난다(P1-04 리뷰 관찰, base부터 재현).
- 알려진 간격(P1 merge ~ P5-03): `docs/manual/strategy-workbench/README.md`와 이전 initiative WORKFLOW 2.2의
  YAML 예시가 1.0 모양(`factors.factors`, `signal.method`)이라 그대로 따라 치면 unknown key다. 문서 개정은
  P5-03이 소유한다.
- 2026-09-17 절차 조정: 이 세션은 격리 worktree(`scad`)에서 돌고 로컬 `main`은 원본 checkout에 체크아웃되어
  있어 직접 merge하지 않는다. APPROVE된 PR은 `APPROVED`로 두고 다음 PR의 base 브랜치가 된다(선언된 스택,
  `parallel_window`에 전부 기록). `MERGED`는 GitHub에서 PR이 병합될 때 제품 소유자 확인 후 표기한다.

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

기본 active PR은 하나다. `parallel_window`에 PR ID를 먼저 기록할 때만 최대 두 개를 허용한다.

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P1 | Backend schema 1.1 | 6 | 6 | `MERGED` |
| P2 | Frontend 1.1 and upgrade UI | 3 | 3 | `MERGED` |
| P3 | Source transactions | 2 | 2 | `MERGED` |
| P4 | Form editing | 5 | 5 | `MERGED` |
| P5 | Graph editing | 3 | 3 | `MERGED` |
| **Total** |  | **19** | **19** | **100%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P5-03` |
| Intent | Phase 5·initiative 마감: Graph → Form 왕복("Form에서 열기"), Graph 편집 e2e(노드 추가 → field_id → 재연결 → 검증 통과 → plan 투영 → 저장), 트랜잭션 잠금을 parse 실패로 좁힘(디바운스 구간 잠금 해소, 리뷰 132-03), route 테스트 de-flake(timeout 15s·reveal 대기), ADR D5 개정·로드맵 M8 주석·매뉴얼 8절·README 2·SoT "전략 의미" 행 |
| Acceptance | WORKFLOW P5-03(+구현 결정) |
| Non-goals | DAG 카드 안 편집 컨트롤 병합, `node_id` 중복 방지·참조 갱신 rename, 항목 안 배열 편집, 표현식 DSL |
| Branch/worktree | `feat/gui-p5-03-wrap-up` (base `feat/gui-p5-02-graph-ui` `4bf6342`; 워크트리 `scad-p43`) |
| Base SHA | `4bf6342` |
| Head SHA | `c2f90c8` (review 후속 4 + CI 수정; diff freeze `e6a0074`) |
| Diff stat | ui 2(`factor-graph-editor`·`factor-graph-panel`)·model 1(`use-source-transactions`)·page 2·messages·e2e 1·테스트 2, 문서 6(ADR·로드맵·매뉴얼·README 2·SoT)·WORKFLOW |
| Focused tests | factor-graph-editor 7 · use-source-transactions 8 · document-routes 63 · e2e chromium-workflow 7/7 |
| Full gate | Vitest 604 passed(52 files) · typecheck · lint · build · property 1000 · Playwright chromium-workflow 7/7 |

---

## P1 — backend schema 1.1

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P1-01` | 모델 1.1: `factors` 평탄화, 선택 보일러플레이트, `kind` 우선, fixture·hash golden | 없음 | `MERGED` | [#108](https://github.com/Nochiski/Quant_study/pull/108) · `review_gui_p1_01` APPROVE (REQUEST_CHANGES P1 1/P2 7 해소) · `5d28996` · main 머지 [#108](https://github.com/Nochiski/Quant_study/pull/108) 2026-09-18 |
| [x] | `P1-02` | 미사용 필드 3개·enum 제거, unary alias 제거, `cross_sectional: demean` | P1-01 | `MERGED` | [#111](https://github.com/Nochiski/Quant_study/pull/111) · `review_gui_p1_02` APPROVE (P0/P1 0, P2 3 후속 반영 후 유지) · `9a5cccb` · main 머지 [#111](https://github.com/Nochiski/Quant_study/pull/111) 2026-09-18 |
| [x] | `P1-03` | `_upgrade.py` dict 변환, 1.0 row 동결 읽기, saved-reference backtest 422 | P1-02 | `MERGED` | [#112](https://github.com/Nochiski/Quant_study/pull/112) · `review_gui_p1_03` APPROVE (P0/P1 0, P2 5 후속 반영 후 유지) · `8bd0185` · main 머지 [#112](https://github.com/Nochiski/Quant_study/pull/112) 2026-09-18 |
| [x] | `P1-04` | `POST /strategy-documents/upgrade` (ruamel rt, drift fail-closed) | P1-03 | `MERGED` | [#113](https://github.com/Nochiski/Quant_study/pull/113) · `review_gui_p1_04` APPROVE (4차; P1 4·P2 7 해소) · `f7049fa` · main 머지 [#113](https://github.com/Nochiski/Quant_study/pull/113) 2026-09-18 |
| [x] | `P1-05` | `FIELD_APPLICABILITY`, compile warning, `x-applicable-when` | P1-02 | `MERGED` | [#114](https://github.com/Nochiski/Quant_study/pull/114) · `review_gui_p1_05` APPROVE (P1-001 철회, P2-002~004 해소) · `3c70001` · main 머지 [#114](https://github.com/Nochiski/Quant_study/pull/114) 2026-09-18 |
| [x] | `P1-06` | Phase 1 감사 후속: schema 버전 owner 단일화, 동결 술어 통일, 주석 재배치 일반화, 규칙·spec D4 갱신, P2-01 리뷰 후속 | P1-05, P2-02 | `MERGED` | [#117](https://github.com/Nochiski/Quant_study/pull/117) · `review_gui_p1_06` APPROVE (P0/P1 0, P2 3 → 후속 `e77f7ce` 확인 요청) · `e77f7ce` · main 머지 [#117](https://github.com/Nochiski/Quant_study/pull/117) 2026-09-18 |

Phase exit:

- [x] 1.1 fixture 4종 같은 hash, 1.0 fixture 거부, 업그레이드 golden 통과 (P1-01·P1-03·P1-04).
- [x] 1.0 row 동결 읽기와 변조 fail-closed (P1-03).
- [x] SoT·책임분리 점검 서브에이전트 결과 기록(`audit_gui_phase1`: blocking 0, DEFECT-P1X-001~004, 문서 액션 8), `strategy-workbench-sot.md` owner 행 4개 추가 → P1-06 #117. 미결: `collaboration-language.md` docstring 언어 정책은 소유자 결정.

## P2 — frontend 1.1

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P2-01` | generated SDK 1.1, pointer helper·snippet·outline·plan·graph·debugger 적응 | P1-05 | `MERGED` | [#115](https://github.com/Nochiski/Quant_study/pull/115) · `review_gui_p2_01` APPROVE (P0/P1 0, P2 6 → 후속은 P2-02 브랜치에서) · `802d6e2` · main 머지 [#115](https://github.com/Nochiski/Quant_study/pull/115) 2026-09-18 |
| [x] | `P2-02` | 1.0 문서 업그레이드 배너·동작, e2e fixture/spec 1.1 | P2-01, P1-04 | `MERGED` | [#116](https://github.com/Nochiski/Quant_study/pull/116) · `review_gui_p2_02` 3차 APPROVE (1차 P1 2·P2 6 → 2차 P1-001 잔존 → 3차 해소) · `6278c40` · main 머지 [#116](https://github.com/Nochiski/Quant_study/pull/116) 2026-09-18 |
| [x] | `P2-03` | Contract Inspector·Problems 적용 조건 표시 | P2-01, P1-05 | `MERGED` | [#118](https://github.com/Nochiski/Quant_study/pull/118) · `review_gui_p2_03` 2차 APPROVE (1차 P1 1·P2 8 해소; 8문서×8행 판정 대조 일치) · `abafcc7` · main 머지 [#118](https://github.com/Nochiski/Quant_study/pull/118) 2026-09-18 |

Phase exit:

- [x] e2e "1.0 revision 열기 → 업그레이드 → 저장 → backtest" green (P2-02: 전체 17 passed, workflow `--repeat-each 2` 10 passed).
- [x] `npm run api:generate` 후 `git diff --exit-code -- ../backend/openapi.json src/shared/api/generated` green (P2-01 커밋 후 재생성 diff 0; Phase 2 감사 재확인).
- [x] SoT·책임분리 점검 서브에이전트 결과 기록(`audit_gui_phase2`: blocking 0, DEFECT-P2X-001~005, 문서 액션 6). 조치: 001·002·003·004·005(b)와 문서 4.1·4.3·4.4는 P2-03 후속 `076e8d8`; 4.2(source 트랜잭션 owner 행)·4.5(P3-01 acceptance `not-found` 규칙)는 P3-01이 소유.

## P3 — source 트랜잭션

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P3-01` | `source-transactions.ts` 원시 연산 4종, preflight, property test | P2-01 | `MERGED` | [#119](https://github.com/Nochiski/Quant_study/pull/119) · `review_gui_p3_01` 2차 APPROVE(`51301cc`) · WORKFLOW 한 줄 `fb8a1b4` · main 머지 [#119](https://github.com/Nochiski/Quant_study/pull/119) 2026-09-18 |
| [x] | `P3-02` | `useSourceTransactions`, 스니펫 삽입 재구성 | P3-01 | `MERGED` | [#120](https://github.com/Nochiski/Quant_study/pull/120) · `review_gui_p3_02` 2차 APPROVE(`96d6b3b`) · main 머지 [#120](https://github.com/Nochiski/Quant_study/pull/120) 2026-09-18 |

Phase exit:

- [x] property test가 임의 문서·연산에서 tree 동등·범위 밖 바이트 보존을 증명 (P3-01: 줄 단위 범위 밖 비교·tree deep-equal; P3-02: 주석 소유자 규칙, 줄별 주석 생성기로 `- - x` 회귀 반례 실측).
- [x] SoT 점검(정본은 source 하나, frontend에 필드 목록 없음) — `audit_gui_phase3` PASS(blocking 0, DEFECT-P3X-001~004 non-blocking, 문서 액션 9 반영, Phase 4 위험 R1~R6 → P4-01/P4-02 착수 전 대응).

## P4 — Form 편집

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P4-01` | `form-projection.ts` (schema × tree × 진단) | P3-02, P2-03 | `MERGED` | [#121](https://github.com/Nochiski/Quant_study/pull/121) · `review_gui_p4_01` 2차 APPROVE(`cd9addf`) · main 머지 [#121](https://github.com/Nochiski/Quant_study/pull/121) 2026-09-18 |
| [x] | `P4-02` | Form 컨트롤·트랜잭션 연결 | P4-01, P3-02 | `MERGED` | [#122](https://github.com/Nochiski/Quant_study/pull/122) · `review_gui_p4_02` 2차 APPROVE(`f1a76dc`) · main 머지 [#122](https://github.com/Nochiski/Quant_study/pull/122) 2026-09-18 |
| [x] | `P4-03` | 목록 섹션: 루트 목록(parameters·factors) 헤더·preset, 참조 가드 | P4-02 | `MERGED` | [#125](https://github.com/Nochiski/Quant_study/pull/125) · `review_gui_p4_03` 2차 APPROVE(`24ad45a`) · main 머지 [#125](https://github.com/Nochiski/Quant_study/pull/125) 2026-09-18 |
| [x] | `P4-04` | IDE·page 연결, stale/JSON 잠금, i18n, e2e, SoT 규칙 개정 | P4-03 | `MERGED` | [#126](https://github.com/Nochiski/Quant_study/pull/126) · `review_gui_p4_04` 2차 APPROVE(`8c5ade0`) · main 머지 [#126](https://github.com/Nochiski/Quant_study/pull/126) 2026-09-18 |
| [x] | `P4-05` | 중첩 목록(`eligibility.rules`) 편집, Phase 4 감사 후속(discriminator `schemaFacts`·`x-catalog` 집합 테스트·README·문서 액션) | P4-04 | `MERGED` | [#130](https://github.com/Nochiski/Quant_study/pull/130) · `review_gui_p4_05` 1차 APPROVE(`b394dbe`) · main 머지 [#130](https://github.com/Nochiski/Quant_study/pull/130) 2026-09-18 |

Phase exit:

- [x] e2e "Form 값 변경 → 주석 유지 → 저장 hash 동일" green — P4-04 `chromium-workflow` Form 시나리오(YAML 바이트 동일·`spec_hash`·`source_hash` backend 대조), 6/6.
- [x] SoT·책임분리 점검 서브에이전트 결과 기록 — `audit_gui_phase4` PASS(blocking 0; P1 2: DEFECT-P4X-001 `eligibility.rules` 편집이 acceptance에만 있고 구현 경로 없음 → P4-05, DEFECT-P4X-002 `findReferences`가 node 스코프를 무시해 같은 `node_id`를 쓰는 다른 팩터의 정의·출력을 참조로 오탐 → P5-01 착수 조건; P2 6: discriminator 직접 읽기(→ P4-05), 카탈로그 이름 사본(→ 테스트, P4-05), 숫자 canonical 표기(기록), 스니펫 훅 버려지는 인스턴스(기록), README read-only 서술(→ P4-05), CodeMirror CRLF→LF 정규화로 편집 없이 저장해도 `source_hash` 변동(기록); 이월 15; Phase 5 위험 R1~R6 → P5-01/02 acceptance에 결정; 문서 액션 11 반영). 왕복 불변식: golden 4케이스 바이트·`spec_hash` 동일.

## P5 — Graph 편집

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `P5-01` | `graph-transactions.ts`: 노드 추가·필드·재연결·출력·삭제 가드·id 제안, `findReferences` 스코프, owner별 feedback, `planRemove` 선행 주석 | P4-05 | `MERGED` | [#131](https://github.com/Nochiski/Quant_study/pull/131) · `review_gui_p5_01` 2차 APPROVE(`226231d`) · main 머지 [#131](https://github.com/Nochiski/Quant_study/pull/131) 2026-09-18 |
| [x] | `P5-02` | Graph UI: 노드 추가 메뉴, property editor, 입력 슬롯, 키보드·ARIA | P5-01 | `MERGED` | [#132](https://github.com/Nochiski/Quant_study/pull/132) · `review_gui_p5_02` 2차 APPROVE(`45f01c9`) · main 머지 [#132](https://github.com/Nochiski/Quant_study/pull/132) 2026-09-18 |
| [x] | `P5-03` | 연결·e2e·문서·규칙 마감, read-only 문구 제거, 로드맵 M8 | P5-02, P4-04 | `MERGED` | [#133](https://github.com/Nochiski/Quant_study/pull/133) · `review_gui_p5_03` 5차 APPROVE(`5de4464`, 문서 `ca46b56`) · main 머지 [#133](https://github.com/Nochiski/Quant_study/pull/133) 2026-09-18 |

Phase exit:

- [x] e2e "노드 추가 → 재연결 → plan 갱신 → 저장" green — P5-03 `chromium-workflow` Graph 시나리오(저장 revision hash = backend compile), 7/7.
- [x] ADR·SoT·README·manual·로드맵 갱신 — ADR D5 개정·5절 취소선, SoT 전략 의미 행, README 2, 매뉴얼 8절, 로드맵 M8 주석, yaml-ui WORKFLOW 2.1/2.2/2.3(P5-03).
- [x] SoT·책임분리 최종 점검 기록 — `audit_gui_phase5` PASS(blocking 0; exit 질문 (a)~(f) 전부 통과, 왕복 불변식 바이트·`spec_hash`·undo 개수 실측, Phase 4 위험 R1~R6 해소; P1 1(DEFECT-P5X-001 확정 직후 같은 필드 재편집 경합)·P2 9는 머지 뒤 backlog). initiative COMPLETE(2026-09-18).

---

## Review 기록

수정 재검토에는 같은 reviewer를 사용한다.

| PR | Reviewer agent | Base SHA | Final HEAD SHA | Verdict | P0/P1 | Residual risk | Reviewed at |
|---|---|---|---|---|---:|---|---|
| P5-03 | `review_gui_p5_03` | `4bf6342` | `5de4464` (diff freeze `e6a0074`; 후속 4회 + CI 수정) | APPROVE (1차 P1 2: 잠금 완화가 디바운스 구간 위치 연산을 열어 삭제 연타 데이터 손실·yaml-ui WORKFLOW 잔존 + P2 11 → `406976a`; 2차 P1 2: 구조 변경 직후 스칼라도 밀림·flake 케이스 SLOW 누락 → `3ffeba3`; 3차 P1 2: 재계산 투영이 blocked에서 소멸·케이스 단위 timeout 실패 → `2efc44c`; 4차 P1 1: 편집 직후 사유가 `stale` → `f283390`; CI backend `FactorStep` → `c2f90c8`. 5차: 실제 reducer 40ms 샘플링으로 DAG·배지 연속, 4라운드 프로브 전부 회귀 없음, 스크립트가 도메인 계약과 일치, 원격 CI 3 job pass) | 7 (전부 해소) | 매뉴얼 8절 스크린샷 없음, `lastStructural` ref가 문서 경계에 안 묶임(한 번 과잉 보류, 오기록 없음), compile error 뒤 `pending` 분기가 두 편집 전 DAG를 보일 수 있음, 모듈 미사용 export·`node_id` 중복 방지 부재, 이 PR이 안 돌린 Playwright project 4개(최종 보고 전 전체 실행), backend self-check의 `database/tests` 공백(WORKFLOW gate에 추가 `ca46b56`) | 2026-09-18 |
| P5-02 | `review_gui_p5_02` | `a0e533d`(P5-01 후속) | `45f01c9` (diff freeze `0097e51`) | APPROVE (1차 REQUEST_CHANGES P1 DEFECT-132-01 노드 삭제가 표시 이름 기반 `nodePointerOf` 첫 일치로 다른 노드를 지움(GUI 도달 가능 silent data loss) + P2 7 → 후속 `45f01c9`: `removeNodeAt`(pointer 정본), 중복 표시 이름 순번, `graph.removeMissing`, kind 미해소 노드 kind select, 후보 `Set`, CSS·aria, stale route 테스트 `findByText`; 2차: 1차 프로브 A2/B2/B 전부 뒤집힘, 접근성 이름 단수 쿼리 구분, P5-01 후속과 맞물림(binary 두 슬롯 빈 select), 132-02 복구까지) | 1 (해소) | OBS-132-05 재컴파일 구간 DAG 목록 소멸·"재계산 중" 미표시(→ P5-03 후속에서 직전 ready 투영 유지 + 배지), 디바운스 잠금(132-03 → P5-03), `node_id` 중복 방지·rename 범위 밖, ready 분기 단위 테스트는 P5-03 e2e | 2026-09-18 |
| P5-01 | `review_gui_p5_01` | `76842ab` | `226231d` (diff freeze `1e09b6e`) | APPROVE (1차 REQUEST_CHANGES P1-1 `addNode`가 `x-reference`를 `schemaFacts` 없이 직접 읽음(SoT owner 위반) + P2 8 → 후속 `226231d`: `nodeReferenceKeys`(`schemaFacts(...).reference`), 단일 참조 분기만 마지막 노드로 채움, `rewireInput(schema?)` 키 검증, 머리 주석(offset 0 블록) 보존, `parentLine < to` 가드(base의 `- - "#tag"` 실패 + 같은 모양 4건 살아남), memo 의존성; 2차: `x-reference` 직접 읽기 0건, 12분기 실측, base·1차·후속 3자 비교, property 2000×8 전부 통과) | 1 (해소) | 빈 줄로 시작하는 문서의 머리 주석은 여전히 삭제(가드가 offset 0만), `rewireInput`의 `schema`가 선택 인자(P5-02 UI는 reference select라 미호출), 루트가 시퀀스인 문서·flow 컨테이너의 `remove` 계획 실패(base 기존 결함, backlog) | 2026-09-18 |
| P4-05 | `review_gui_p4_05` | `ef19805` | `b394dbe` | APPROVE (1차; P2 4: `kindOfBranch`가 분기 안 `properties.kind`의 `$ref`를 안 풀어 DEFECT-P4X-003 부분 해소, 중첩 목록이 스키마 순서와 무관하게 스칼라 뒤에 그려짐, `appendOperation` 루트 fallback이 부모 pointer 한 세그먼트 가정, `projectForm`의 `array === null` 죽은 분기; 감사 항목 001/003(부분)/004/007·문서 액션 11 대조, 중첩 목록 실제 planner 경로(`rules: []`·부모 없음·마지막 항목 삭제 → `[]`), 진단 소유권 누락 0·중복 0, 회귀 없음) | 0 | P2-1·P2-4는 P5-02에서 처리, P2-2(표시 순서)·P2-3(단일 세그먼트 부모 가정, Form object 섹션만 해당) 기록 | 2026-09-18 |
| P4-04 | `review_gui_p4_04` | `81aa14e`(코드 `24ad45a`) | `8c5ade0` (diff freeze `13fa8d3`) | APPROVE (1차 APPROVE P1 0·P2 10: STALE 오탐(디바운스 구간), 없는 접기 테스트, 스니펫 결과가 Form 반영됨을 지움, PR 본문 Graph 배선 오기, 죽은 `inapplicablePointers`, 고아 키 7·CSS 8, `view` prop, SoT 행이 Graph 편집을 선행 서술, e2e 팩터 검증 약함, status 2개 → 권고 후속 `8c5ade0`: stale을 같은 버전 parse 실패로 좁힘, 접기 테스트, 고아 제거, role 제거, SoT 단서, 본문 정정; 2차: 확정 직후 STALE false·구문 오류 시 true·복구 즉시, 문서 경계 누수 없음, status 1개, grep 0, rebase가 리뷰 코드 불변) | 0 | DEFECT-P404-011 이미 구문 오류인 문서에서 source를 더 치면 디바운스 150ms 동안 stale=false로 옛 값을 표시 없이 보여줌(잠금·저장 차단은 유지; "갱신 중" 상태 도입 검토), 공유 feedback 슬롯 하나(스니펫 결과가 Form 반영됨을 지움 → P5-01), e2e 팩터 추가 검증은 Graph 탭 텍스트 포함, 확정 직후 150ms 컨트롤 잠금은 base 훅 `disabled: syntax` | 2026-09-18 |
| P4-03 | `review_gui_p4_03` | `4b3b456` | `24ad45a` (diff freeze `6460971`) | APPROVE (1차 REQUEST_CHANGES P1 DEFECT-125-01 목록 키가 없는 문서(새 전략 starter·생략형)에서 항목 추가·preset이 `insert-item` → `not-found`로 언제나 실패(테스트가 스텁이라 통과) + P2 6 → 후속 `24ad45a`: `ListSection.written`·미작성이면 `insert-key`로 키 열며 첫 항목(실제 planner 테스트), identity는 `x-authoring-identity` 우선·카탈로그 필드 제외, 거부 안내는 tree 바뀌면 해제, 가드 docstring; 2차: CRLF·머리꼬리 주석 문서에서 단일 범위 삽입·undo 1, starter preset → written → 둘째는 insert-item, factors/parameters/EligibilityRule identity 판정 정확) | 1 (해소) | `insert-key`로 여는 블록 내부 들여쓰기가 문서 폭과 무관하게 2칸(source-transactions, 스니펫·object 섹션과 같은 기존 동작), 부모 키까지 없는 중첩 목록은 `not-found`(중첩 목록 편집 전 확인), `factors: {}` 타입 불일치 문서에서 추가 실패(backend가 막음), 항목 삭제 시 선행 독립 주석 고아(`planRemove` → P5-01), `saved_factor.factor_id` 오탐은 안전 방향 | 2026-09-18 |
| P4-02 | `review_gui_p4_02` | `cd9addf` | `f1a76dc` (diff freeze `5946bf9`) | APPROVE (1차 REQUEST_CHANGES P1 DEFECT-P402-001 list-link 행 "기본값으로"가 배열 전체 삭제 + P2 8 → 후속 `f1a76dc`: 링크·const 행 버튼·컨트롤 제거, 실패 재확정, 무효 안내 해제, aria-labelledby, 토큰·죽은 코드·WORKFLOW·문구 3종, Form 확정 시 focusEditor false; 2차: 링크 행 button 0·textbox 0, focus() 0회·replaceRange 1회, A→B→A 3회 반영, Vitest 539 전체 3회 초록) | 1 (해소) | 009 실패 필드 재확정이 다른 필드 성공 뒤 다시 막히고 오류 문구도 덮임·011 실패 상태 blur마다 재계획·012 `canRetry`가 label로 필드 식별(P4-03 항목은 같은 label) → P4-03에서 필드 로컬 실패 플래그로 처리; 010 passive 행 라벨 CSS·별표·단위 누락 → P4-03; 013 x-default-from placeholder 분기 미도달·패널 미검증 → P4-03 테스트; feedback 단일 슬롯(마지막 결과만 남음) | 2026-09-18 |
| P4-01 | `review_gui_p4_01` | `bef91cc` | `cd9addf` (diff freeze `d9160ef`) | APPROVE (1차 REQUEST_CHANGES P1 DEFECT-121-01 목록 요약이 const `kind`에 걸림·121-02 섹션·항목 pointer 진단 유실(프로브 13 중 8) + P2 6(example 분기 가드, applicable null 중첩, schema_version 자유 입력, x-default-from 미반영, SoT 행이 스니펫 직접 읽기 미포함, 주석) → 후속 `cd9addf`: summary const 건너뛰기, 섹션·항목 `diagnostics` 슬롯(list-link 하위 흡수), const 컨트롤, defaultFrom, 가드 복원; 2차: 166 pointer 배치 누락 0·중복 0, 골든 412 pointer 차이 0) | 2 (해소) | `unabsorbedDiagnostics`의 `includeRoot` 죽은 파라미터, 진단 집계 `as FormField` 캐스트 4곳, summarize const 판정이 `$ref` 미해소(현재 fixture 인라인) → P4-03에서 처리 | 2026-09-18 |
| P3-02 | `review_gui_p3_02` | `97409f1` | `96d6b3b` (diff freeze `d13830c`) | APPROVE (1차 REQUEST_CHANGES P1-1 스니펫 위치 회귀·P1-2 property 회귀 방지 주장 반증 + P2 4 → 후속: `options.anchor`로 커서 줄 자리, 삽입 앵커 통일, 생성기 줄별 주석(회귀 되돌리면 279회째 반례 실측), 부모 줄 끝 주석 보존, applyToTree before 거부, nit 3; 2차: 스니펫 20종 프로그램 검증 불일치 0, `#` 휴리스틱 오탐 경로 4종 fail-closed 확인) | 2 (해소) | 확정 시점 호출 규칙은 호출자 규율(P4-02 컨트롤 커밋 규칙으로 처리), `onEditorReady` setState(인라인 람다 호출자 주의), 주석 고아 보존, property 미생성 영역(줄 끝 주석·따옴표 키·flow 내부) | 2026-09-18 |
| P3-01 | `review_gui_p3_01` | `610a17c` | `fb8a1b4` (리뷰 대상 `51301cc`) | APPROVE (1차 REQUEST_CHANGES P1-1 block scalar range 경계·P1-2 시퀀스/dash 줄 삭제의 주석 삭제 + P2 9 → 후속 `51301cc`: leaf 범위 줄바꿈 trim, `removeLines`·dash 줄 첫 키 규칙, 생성기 커버리지(주석·빈 컨테이너·block scalar·`- - `·dash 줄 키 각 1000건+/4000), 관용 절 제거, 줄 단위 범위 밖 비교; 2차: P1 전부 해소, 회귀 점검 통과, `FC_NUM_RUNS=5000` 통과) | 2 (해소) | P2-R1 `- - x` 안쪽 첫 항목 삭제가 사이 주석을 지움(WORKFLOW 제한 명기, 1.1 문서 경로 밖), P2-R2 property `remove` 관용 절이 pointer 줄 범위보다 넓고 개수 비교(→ P3-02), `const escape` 별칭(→ P3-02), 성능 parse 2회/연산(→ P3-02 훅 설계) | 2026-09-18 |
| P2-03 | `review_gui_p2_03` | `e77f7ce` | `abafcc7` | APPROVE (1차 REQUEST_CHANGES DEFECT-118-01 Form 배지가 backend가 침묵시킨 기본값 필드에 경고 + P2 8 → 후속: 배지 = compile 경고 pointer, 발행 기본값 resolver, 글리프, signal 섹션, 문구 키 커버리지, `valueAtPointer` 공유, 중립 문구; 2차 실측 Form 배지 = backend 경고 pointer 8문서 일치, Inspector 판정 = `applies_to` 64건 일치) | 1 (해소) | nit 4: `contract.applicable.unknown` 문구가 옛 동작 서술(현재 도달 불가), `fromDefault` 미표시, i18n 커버리지 테스트가 한 단계만 탐색(재귀와 결과 동일), `STARTER` 별칭 중복 → P3-02 cleanup 커밋 후보 | 2026-09-18 |
| P2-02 | `review_gui_p2_02` | `2a16ebe` | `6278c40` | APPROVE (1차 REQUEST_CHANGES P1-001 e2e seeding 비멱등·P1-002 undo 격리·P2 6 → 2차 P1-001 잔존(재시도 시 `/revisions/1` 저장 409) → 3차 시도별 고유 전략 id로 해소; seeding CLI 4케이스·`--repeat-each 2` 10 passed 실측) | 2 (해소) | suffix가 ms 타임스탬프(충돌 시 fail-closed), 정규식에 id 보간(base36·하이픈만), 동결 전략이 시도마다 누적(목록 페이지 20) | 2026-09-18 |
| P1-06 | `review_gui_p1_06` | `af816a3` | `e77f7ce` | APPROVE (P0/P1 0, P2 3: 시퀀스 항목 첫 키 삭제 시 docstring과 반대로 아래 주석 소실 → 다음 키 앞 주석 슬롯으로 이동 구현, 버전 리터럴 테스트 가드, WORKFLOW 잔재 2곳; relocation probe 4종·0.9/1.2/2.0 row fail-closed 실측) | 0 | 1.1→1.2 도입 시 변환 step 미존재(의도된 제한, 테스트가 상수를 읽어 실패로 드러남); 중첩 mapping이 비는 경우 `_finish_emptied_sections` 미적용(현행 step으로 도달 불가) | 2026-09-18 |
| P2-01 | `review_gui_p2_01` | `66e1002` | `802d6e2` | APPROVE (P0/P1 0, P2 6: 영문 주석 4, 409 mock의 계약 외 `requires_upgrade`, 문서 mock의 `requires_upgrade` 누락, 팩터 컬렉션 탐색 첫 매치 의존(fail-open), WORKFLOW P2-01 `x-defines: factor` 문구 stale, synthetic 스키마의 `method` 잔재; 스니펫 매트릭스 6케이스·pointer 정규식 8입력·SDK 재생성 diff 0 실측) | 0 | 프로덕션 build·런타임 e2e는 reviewer가 직접 확인 못함(P2-02에서 e2e green 실측); P2-003은 P2-02가 `document()` mock에 `requires_upgrade: false`를 넣어 해소, 나머지 P2는 P2-02 브랜치 후속 커밋 | 2026-09-18 |
| P1-05 | `review_gui_p1_05` | `294864c` | `3c70001` | APPROVE (1차 REQUEST_CHANGES P1-001 생성 SDK 미동기는 WORKFLOW 1절 스택 정책 확인 후 reviewer 철회; P2-002 AND 조건·P2-003/004 동일 모양·owned_by_error 해소 실측) | 0 | `_explanation.py`가 percentile 모드에서도 `selection_count`를 "N종목"으로 단언(기존 결함, cleanup 후보); i18n `strategy.contract.applicable.*` 키는 P2-03; spec D4 문구·표(7행→8행, validator 발행)는 P5-03 개정 | 2026-09-18 |
| P1-04 | `review_gui_p1_04` | `9ce5a7d` | `f7049fa` | APPROVE (1차 REQUEST_CHANGES P1-001 openapi 미재생성·P1-002 422 union 누락·P2 5 → 2차 P1-003 `#####` 500 회귀·P2-006/007 → 3차 P1-004 인접 빈 섹션 비결정 → 4차 APPROVE; 시드 8종×2 배치 결정성, 프로브 전량 재실행) | 4 (해소) | 비어 버린 섹션이 마지막 키면 아래 주석 소실(설계상), 옮겨진 주석의 들여쓰기 유지, `except Exception` 강등은 로그로 구분; 기존 codec `x: =` 500은 별도 이슈 후보 | 2026-09-18 |
| P1-03 | `review_gui_p1_03` | `9ea8854` | `8bd0185` | APPROVE (P0/P1 0, P2-001~005 후속 확인 후 유지; 적대적 프로브 6종, 동결 head 위 1.1 append·pagination·업그레이드→저장→실행 e2e, CST parity) | 0 | `requires_upgrade` 3개가 dataclass 기본값 탓에 SDK에서 optional로 생성됨(P1-04에서 required로 정리); P1-04 CST golden이 덮어야 할 주석 손실 3종(안쪽 `factors:` 줄끝 주석, 바깥 독립 주석 뒤 시퀀스 들여쓰기, 삭제 키의 주석) | 2026-09-18 |
| P1-02 | `review_gui_p1_02` | `5d28996` | `9a5cccb` | APPROVE (P0/P1 0, P2-001~003 후속 `fcc3c8c` 확인 후 유지; 적대적 hydrate 10건, demean parity 무작위 200 + 경계 4) | 0 | demean 노드도 winsorize 전용 quantile 파라미터를 받음(P1-05 적용 조건표 후보), enum 순서 미고정, minimal fixture의 `signal` 생략 미단언 | 2026-09-17 |
| P1-01 | `review_gui_p1_01` | `3aa95d0` | `5d28996` | APPROVE (1차 REQUEST_CHANGES P1-001 계약 의도 단언 부재·P2 7건 → `1d885f5`·`d545f53` 반영, 변이 검증 3종·적대적 hydrate 27종 통과) | 1 (해소) | `default-from` 가드는 생략 시에만 실행(지연 검사, schema builder 불변식은 두 번째 default-from 도입 시 검토); `contract_hash`가 FieldContract 행 모양을 덮지 않음(schema const 변경으로 이번엔 무효화됨); 1.0 row는 P1-03까지 읽기 불가(의도) | 2026-09-17 |

## 검증 기록

| PR | Focused test | Full gate | API generated clean | Manual UX | CI | Recorded at |
|---|---|---|---|---|---|---|
| P4-02 | form-transactions 4·strategy-form-panel 11·use-source-transactions 7·snippet-insertion 3·document-routes 62 | Vitest 539 passed(48 files)·typecheck·lint·build | 해당 없음(SDK 무변경) | page 미연결(P4-04에서 e2e) | 원격 CI frontend job 대상 | 2026-09-18 |
| P4-01 | form-projection 10·contract-inspector 22·schema-navigator·schema-assist·canonical-snippets 16 | Vitest 522 passed(46 files)·typecheck·lint·build | 해당 없음(SDK 무변경) | 해당 없음(순수 projection, UI 무변경) | 원격 CI frontend job 대상 | 2026-09-18 |
| P3-02 | use-source-transactions 5·source-transactions 22·canonical-snippets 16·snippet-insertion 8·property 1(`FC_NUM_RUNS=3000`)·contract-inspector 22 (감사 `vitest --reporter=json` 실측) | Vitest 512 passed(45 files)·typecheck·lint·build·e2e chromium-workflow 5 passed; backend pytest 1,316 passed·ruff·pyright 0 | 해당 없음(SDK·openapi 무변경) | 해당 없음(공개 UI 동작 동일; 기본값 판정 표시만 추가) | 원격 CI frontend·backend job 대상 | 2026-09-18 |
| P3-01 | source-transactions 10·property 1(`FC_NUM_RUNS=3000` 통과) | Vitest 490 passed(44 files)·typecheck·lint·build | 해당 없음(SDK 무변경) | 해당 없음(순수 함수) | 원격 CI frontend job 대상 | 2026-09-18 |
| P2-03 | field-applicability 5·contract projection/UI 3·hover 1·Form 배지 1 | Vitest 478 passed(41 files)·typecheck·lint·build | SDK 무변경(`ApplicableWhen` 타입 재수출만) | 해당 없음 | 원격 CI frontend job 대상 | 2026-09-18 |
| P1-06 | upgrade-source `periods` 주석 1·frozen 술어 1·syntax 메시지 1·팩터 컬렉션 모호성 1 | backend pytest 1,315·Ruff·Pyright 0; frontend Vitest 468·typecheck·lint·build | `openapi.json`·`runtime-schema.json` 재생성 diff 0, SDK 무변경 | 해당 없음 | 원격 CI backend·frontend job 대상 | 2026-09-18 |
| P2-02 | document-upgrade 3·upgrade-banner 4(MSW 성공+undo/422 drift/네트워크/legacy 동결)·router 배지 1·document-routes backtest 422 1 | Vitest 464 passed(40 files)·typecheck·lint·build·Playwright tsconfig typecheck·backend 25 passed·Ruff·Pyright | SDK 무변경(P2-01 상태 유지) | e2e chromium-workflow 5 passed: 1.0 row seeding → 배너 → 업그레이드 200 → 검증 통과 → v2 저장(1.1 golden과 같은 spec hash) → saved_revision backtest completed → legacy 동결 배너 → 목록 배지 | 원격 CI frontend·browser-e2e green 기대(P1-01부터 알려진 빨간불 해소) | 2026-09-18 |
| P2-01 | canonical-snippets 9·execution-plan 7·schema-navigator 13·schema-assist 17·cursor 10·backtest-error-contract 1 | Vitest 455 passed(38 files)·typecheck·lint 0 warnings·build·Playwright tsconfig typecheck | 커밋 후 `npm run api:generate` 재실행, `src/shared/api/generated`·`backend/openapi.json` diff 0 | 해당 없음(타입·pointer·스니펫 적응) | 원격 CI frontend job green 기대(P1-01부터 알려진 빨간불 해소), browser-e2e는 P2-02까지 빨간불 | 2026-09-18 |
| P1-05 | applicability 25·document HTTP 13·constraints·schema·application | backend pytest 1,313·Ruff check·Pyright 0; reviewer 독립 재실행 동일 + frontend Vitest 31 실패가 전부 기존(1.1 fixture) 실패임을 확인 | `openapi.json`·`runtime-schema.json` 재생성 diff 0 | 해당 없음 | 원격 CI backend job 대상; frontend·browser-e2e 알려진 빨간불(P2-01/02 exit) | 2026-09-18 |
| P1-04 | 어댑터 17·서비스 9·HTTP 5·openapi 동기 1 | backend pytest 1,289·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json` 재생성 diff 0 + 추적 동기 테스트 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-18 |
| P1-03 | upgrade 8·frozen repository 10·frozen HTTP 5 | backend pytest 1,257·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-18 |
| P1-02 | hydrate unknown_key 3·invalid_enum 4·demean 문서 1, demean parity 1, 설명 문구 1 | backend pytest 1,232·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-17 |
| P1-01 | contract fixtures 15·schema 3 신규·hydrate 가드 1·JSON API label 비대칭 1 | backend pytest 1,228·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 (frontend SDK는 P2-01) | 해당 없음(backend) | 원격 CI: backend job 대상, browser-e2e는 알려진 빨간불(WORKFLOW 1절) | 2026-09-17 |

## 머지 뒤 backlog (audit_gui_phase5 §11, 우선순위)

| 우선 | 항목 | 근거 | 크기 |
|---:|---|---|---|
| 1 | ~~DEFECT-P5X-001 — `TextualControl` 되돌리기에 "사용자가 draft를 고치지 않았다" 조건(확정 직후 150ms 안에 같은 필드를 비우고 다시 치면 parse 도착이 옛 값을 되살려 `window: 100101`처럼 이어 붙음, "반영됨" 오보고; undo 1회 복구)~~ → [#140](https://github.com/Nochiski/Quant_study/pull/140) | `strategy-form-panel.tsx` `TextualControl` seen/committed 조정 — `pristine`(손대지 않은) draft일 때만 되돌림 | 한 줄 + 테스트 1 |
| 2 | ~~DEFECT-P5X-002 — 사용자 매뉴얼 1절 샘플이 schema 1.0~~ → [#143](https://github.com/Nochiski/Quant_study/pull/143) | 첫 실습이 업그레이드 오류로 시작 | 문서 |
| 3 | ~~DEFECT-P5X-003 — `x-reference` 네임스페이스 리터럴(`NAMESPACES`)을 runtime schema fixture와 묶는 테스트(`CATALOGS`와 대칭)~~ → [#140](https://github.com/Nochiski/Quant_study/pull/140) | 새 네임스페이스가 조용히 text 컨트롤로 떨어짐 | 테스트 1 |
| 4 | ~~P5X-009 — `node_id` 중복 방지 판정 + 참조 id rename(참조 갱신)~~ → [#145](https://github.com/Nochiski/Quant_study/pull/145) | 중복이 backend compile error로만 드러남 | 기능 |
| 5 | ~~P5X-005 — CodeMirror `lineSeparator`로 CRLF 보존 또는 "LF 정규화" 문서화~~ → [#143](https://github.com/Nochiski/Quant_study/pull/143) | 편집 없이 저장해도 `source_hash` 변동 | 설정 1 또는 문서 |
| 6 | ~~DEFECT-P5X-004 — `removalBlockers`가 SoT 행이 요구한 스코프 없이 `findReferences` 호출(팩터·파라미터는 전역이라 현재 무해)~~ → [#144](https://github.com/Nochiski/Quant_study/pull/144) | 계약 명시 | 테스트 또는 인자 |
| 7 | ~~P5X-006 — `lastStructural`·`lastReady`를 `documentEpoch`에 결속~~ → [#144](https://github.com/Nochiski/Quant_study/pull/144) | 다른 stale 캐시는 전부 epoch에 묶여 있음 | 한 줄 |
| 8 | P5X-007 — 매뉴얼 스크린샷 전량 재촬영(8절 신규 + 기존 12장 — 마지막 촬영 `5a7b0b3` 2026-09-06이 1.1 전환 `9fab26b` 이전이라 전부 1.0 화면; #143 리뷰 P2-2) | 두 편집 표면을 글로만 설명, 1·2절 그림이 1.0 구조 | 문서 |
| 9 | ~~P5X-008 — `graph-transactions.ts` UI 미호출 export 5종 배선 또는 정리~~ → [#144](https://github.com/Nochiski/Quant_study/pull/144) | P5-01 R4-4 규약이 공허 | 정리 |
| 10 | ~~Phase 1 이월 — `trace.strategy.requires_upgrade` i18n 키, 세 라우트 422 typed화~~ → [#147](https://github.com/Nochiski/Quant_study/pull/147) | P5-03 소유로 지정됐으나 미이행 | 소 |
| 11 | ~~Phase 2 이월 — DEFECT-P2X-004(사전 누락 시 침묵), -005(적용 조건 문장 이중 소유)~~ → [#147](https://github.com/Nochiski/Quant_study/pull/147) | 진단 wire 계약 변경 | 중 |
| 12 | ~~`database/` ruff 설정 부재 → `--config backend/pyproject.toml` 명시를 WORKFLOW에~~ → [#143](https://github.com/Nochiski/Quant_study/pull/143) | P5-03 부수 권고 | 문서 |
| 13 | ~~빈 팩터의 `output_node_id` 자동 지정 비대칭~~ → [#144](https://github.com/Nochiski/Quant_study/pull/144) | 동작은 맞고 compile이 안내 | 소 |
| 14 | flow mapping(`{ … }` 한 줄)으로 쓴 팩터·그래프는 `insert-item`부터 계획이 실패하고(base 한계, PLAN 기록), 다중 연산 트랜잭션(`planSourceOperations`)의 all-or-nothing이 그 한계를 노드 추가 같은 주 동작으로 전파한다(#144 3차 리뷰 관찰) | flow 컨테이너를 block으로 여는 연산 또는 안내 문구 | 중 |
| 15 | route 테스트(`document-routes.test.tsx`)가 전체 실행·CI에서 부하 flake(#143 CI 재실행, #144 리뷰 1회차, 로컬 반복) — 파일 timeout 15s로도 남는 한 틱 지연 | 케이스별 `findBy*`/`waitFor` 정리 또는 워커 격리 | 소 |

## 변경 기록

| 시각 | 작성자 | 변경 | 근거 |
|---|---|---|---|
| 2026-09-18 KST | Claude | backlog 10·11 처리 PR [#147](https://github.com/Nochiski/Quant_study/pull/147) `fix/gui-backlog-b5`(main 기반): create·revise 422를 `StrategyDocumentSave422Response`로 선언(openapi.json·SDK 재생성), `traceErrorMessage`가 `trace.error.<code>` 번역(위험 5b), 매뉴얼 1절 샘플 compile 게이트(#143 리뷰 권고), P2X-004는 P2-03 테스트 118-07로 이미 종결 확인, P2X-005는 "문장은 소비자별 소유"로 SoT 종결. backend pytest 1330·ruff·pyright·frontend 게이트 초록(route 테스트 부하 flake 1건 단독 재통과). 리뷰 `review_gui_backlog_147` | 머지 뒤 backlog |
| 2026-09-18 KST | Claude | backlog 4 처리 PR [#145](https://github.com/Nochiski/Quant_study/pull/145) `feat/gui-backlog-b3`(#144 위 스택): `renameNode`(중복·빈 값 거부, 정의 + 같은 그래프 참조 연산 목록) + `FormFieldsEditor.planCommit`(필드 확정 가로채기, `CommitInvalidReason` union으로 i18n 키 타입 검사). Vitest 614/52·eslint·tsc·Playwright 19/19. 리뷰 `review_gui_backlog_145` 1차 APPROVE(P2 3: 거부 뒤 blur가 안내를 지움, Escape가 안내를 남김, 매뉴얼 문장 끊김) → 후속 `a209f5a`(`onValid`를 재확정 가드 뒤로, Escape에 `onValid`, 문장 복원, 편집기 단언 4줄) → 재검토 APPROVE(리뷰어가 Playwright 19/19 재실행) | 머지 뒤 backlog |
| 2026-09-18 KST | Claude | backlog 6·7·9·13 처리 PR [#144](https://github.com/Nochiski/Quant_study/pull/144) `fix/gui-backlog-b2`(main 기반): `removalBlockers` `REFERENCE_SCOPES`(node → 팩터 graph), `lastStructural`을 `documentEpoch`에 결속(useEffect), `graph-transactions.ts` 미호출 export 4종·`nodeBranchAt` 제거(`nodePointerOf` 유지), `planSourceOperations`(순차 계획 + 공통 접두·접미 diff로 편집 한 번) + 훅 `apply`가 연산 배열 수용, `addNode`가 `nodes: []`·빈 출력이면 두 연산. Vitest 611/52·eslint·tsc·Playwright 19/19(worktree `scad`의 stale `backtest_core`가 백테스트 시나리오를 한 번 failed로 만들어 maturin 재빌드). 리뷰 `review_gui_backlog_144` 1차 APPROVE(P2 5: JSDoc 끊김·WORKFLOW 잔존 언급·주석·epoch effect 의존·nodes 없는 경로) → 후속 `349ddee` → 2차 REQUEST_CHANGES(`null` 출력을 미지정으로 보아 `output_node_id:` 표기에서 노드 추가 전체가 막힘) → 후속 `0de9d3d`(null 제외 + 회귀 테스트) → 3차 APPROVE(문서 모양 8종 실측; P2 1: `addNode` JSDoc 순서 — 머지 전 정리). backlog 14·15 추가 | 머지 뒤 backlog |
| 2026-09-18 KST | Claude | backlog 2·5·12 처리 PR [#143](https://github.com/Nochiski/Quant_study/pull/143) `docs/gui-backlog-b1`(main 기반, 문서만): 매뉴얼 1절 샘플을 backend `upgrade_yaml_source` 출력의 1.1로(빈 `signal: {}` 제거), 4절 줄 끝 LF 정규화 안내(리뷰 P2-3으로 8절에서 이동), SoT 편집기 행에 "CRLF → LF 정규화·`source_hash` 변동 가능·`spec_hash` 불변" 결정, WORKFLOW 게이트 행에 `database/` ruff 실측(설정 없음·CI는 pytest만·backend 규칙 663건). 리뷰 `review_gui_backlog_143` | 머지 뒤 backlog |
| 2026-09-18 KST | Claude | backlog 1·3 처리 PR [#140](https://github.com/Nochiski/Quant_study/pull/140) `fix/gui-backlog-p5x-001`(main 기반): `TextualControl` 되돌리기 조건을 "손대지 않은 draft"(`pristine` 플래그: 입력이 바뀌거나 확정이 실패하면 꺼지고 확정 성공·Escape로 켜짐)로 좁힘 + 재편집 경합 회귀 테스트(수정 전 red 확인), `NAMESPACES` fixture 결속 테스트. Vitest 606/52·eslint·tsc 초록. 리뷰 `review_gui_backlog_140` 1차 APPROVE(P2 3) → 후속 `9dc1921`(pristine 플래그·손대지 않은 입력 경로 테스트) → 재검토 APPROVE. main 위로 rebase 뒤 본 PLAN 기록 | 머지 뒤 backlog |
| 2026-09-18 KST | Claude | **19 PR 머지 완료** — 사용자 지시(전체 E2E 백테스트까지 → CI 통과 → 머지)대로 전체 Playwright 19/19, #133 CI 3 job 초록 확인 후 #106(기획) → #108…#133 순서로 `--merge`. 절차 기록: #106을 `--delete-branch`로 머지하자 GitHub가 base 브랜치 삭제로 #108을 CLOSED 처리(retarget 아님) → base 브랜치를 같은 SHA로 재생성해 reopen·base main으로 편집·재삭제, 이후는 각 PR base를 main으로 먼저 옮긴 뒤 머지하고 브랜치는 끝에 일괄 삭제. 머지 전 스택 체인 검증에서 P4-01에만 있던 PLAN 문서 커밋 `0b4a0bd`가 P4-02 이후에 없어 체인이 끊겨 P4-01 head를 코드 `cd9addf`로 되돌림(리뷰 승인 코드 동일, 변경 기록 한 줄 손실). 머지 뒤 `origin/main` 트리 `2de69fc…` = 사전 `merge-tree` 예측과 일치. 중간 18 PR의 CI는 설계상 빨강(P1: frontend SDK는 P2-01, 전 구간: `FactorStep` ImportError는 P5-03 `c2f90c8`에서 수정) — 게이트는 최상위 #133 CI(머지 ref) | 13.7 머지 |
| 2026-09-18 KST | Claude | `audit_gui_phase5` 수신: PASS(blocking 0). 게이트 9종 중 8 초록(루트 `database/tests`는 로컬 Windows 환경 사유 72건, 원격 CI 초록). SoT 여섯 질문 통과, 규칙 표 1행 뒤처짐(전략 의미 행의 `findReferences` 스코프 "→ P5-01" → 구현 완료로 정정). 결함 P1 1(DEFECT-P5X-001)·P2 9(매뉴얼 1절 1.0 샘플, `x-reference` 네임스페이스 fixture 결속 없음, `removalBlockers` 스코프, …), 이월 정리 표, 머지 뒤 backlog 13 → 아래 "머지 뒤 backlog" 절 | 13.6 Phase 감사 |
| 2026-09-18 KST | Claude | `review_gui_p5_03` 5차 APPROVE(`5de4464`) → P5-03 APPROVED. 19 PR 전부 APPROVED. Phase 5 exit 2항 체크. WORKFLOW backend gate에 `database/tests` 추가(`ca46b56`). 다음: 전체 Playwright(백테스트 포함) → `audit_gui_phase5` → PLAN COMPLETE → 사용자 지시대로 #133 CI 초록 확인 후 #106부터 순차 머지 | 13.5 판정 |
| 2026-09-18 KST | Claude | 머지 준비 중 원격 CI 확인: 스택 중간 PR의 backend job이 `database/scripts/run_mvp_backtest.py`의 `FactorStep` ImportError(P1-01 `factors` 평탄화로 사라진 이름; 로컬 backend pytest는 `database/tests`를 안 돌려 미검출)로 P1-01부터 전부 빨강 → P5-03에 `c2f90c8` 커밋(`factors=(FactorSignal(...),)`), equity 테스트 `test_MVP_B_…` 로컬 통과. 사용자 지시(2026-09-18): 최종 보고 시 전체 E2E(백테스트 포함) → CI 통과 → 스택 머지. P1 PR들은 설계상 frontend CI가 빨강(SDK는 P2-01)이라 게이트는 최상위 #133 CI, 머지는 #106부터 순서대로 `--merge --delete-branch` | 13.3 CI |
| 2026-09-18 KST | Claude | `review_gui_p5_03` 4차 중간 보고(3차 P1 2·P2 닫힘; 남은 P1: 실제 reducer 경로에서 편집 직후 plan 상태가 `blocked(stale)`(이전 compile spec 잔존, stale 판정이 pending보다 먼저)라 재계산 유지 조건(`pending`만)에 안 걸려 compile 응답 전까지 DAG가 사라짐, 테스트가 실제 경로를 안 탐) → 후속 `f283390`: 유지 조건에 `stale` 추가, 테스트를 stale → pending → loading 경로로, WORKFLOW 문구. Vitest 604/52·build·e2e 7/7 → 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p5_03` 3차 REQUEST_CHANGES(2차 P1 2·P2 해소 확인; 신규 P1 재계산 투영을 blocked에서 버려 편집 경로(blocked pending → loading)에서 기능 소멸·단위 테스트가 ready → loading 직행만 검증, P1 케이스 단위 `SLOW`로는 부하 배율 8배를 못 따라잡아 다른 케이스 red(리뷰어가 1차 권고 철회); P2 Form 목록 잠금이 항목 필드까지 넓음) → 후속 `2efc44c`: `FactorGraphEditing.documentKey`(= `documentEpoch`)로 직전 투영을 문서 경계에만 묶고 blocked(pending)·loading 구간에 유지(테스트: ready → blocked pending → loading 유지, 문서 키 변경 시 폐기), Form은 추가·preset·삭제 버튼만 `settling` 비활성, route 파일 전체 `testTimeout: 15_000` 복구, 매뉴얼·WORKFLOW 문구. 전체 Vitest 2회 초록(604/52), e2e 7/7 → 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p5_03` 2차 REQUEST_CHANGES(1차 P1 2·P2 11 전부 해소 확인; 신규 P1-1 구조 변경 직후 150ms 안의 스칼라 확정도 형제 pointer가 밀려 다른 항목에 써짐, P1-2 실제 flake 케이스(514행)에 `SLOW` 누락; P2 재계산 투영이 문서 경계에 안 묶임, Form 목록 버튼 미비활성) → 후속 `3ffeba3`: 훅이 직전 적용 연산의 구조 변경 여부를 ref로 기억해 그때는 `settling` 동안 스칼라도 `pending`(테스트), Form 목록 섹션 fieldset `settling` 잠금, `lastReady`는 loading 외 상태에서 폐기, 514행 `SLOW`, 매뉴얼·WORKFLOW 문구 → 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p5_02` 2차 APPROVE(`45f01c9`) → P5-02 APPROVED. `review_gui_p5_03` 1차 REQUEST_CHANGES(P1-1 잠금 완화가 디바운스 구간의 위치 연산을 열어 stale pointer 삭제 연타로 팩터·노드 소실(회귀); P1-2 yaml-ui `WORKFLOW.md` 2.1/2.2/2.3이 read-only·1.0 서술로 잔존; P2 11) → 후속 `406976a`: `apply`가 `settling`(parse 대기) 동안 위치 연산을 `pending`으로 보류(스칼라는 열림) + Graph 추가·삭제 fieldset 비활성 + 훅 테스트, Form `selectedPointer`로 URL path 항목 `aria-current`(왕복 대칭), 재계산 중 배지(OBS-132-05), e2e hash 동치·줄 단위 단언, route timeout 느린 2케이스만 `SLOW`(P4-07은 이미 15s), yaml-ui WORKFLOW 2.1 개정 주석·2.2 골든 예시·2.3, `backend/FACTORS.md`·매뉴얼 STALE 문구·revision page 한글 주석 → 재검토 요청. 기록: 매뉴얼 8절 스크린샷 후속, 모듈 미사용 함수는 계약 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p5_01` 2차 APPROVE(`226231d`) → P5-01 APPROVED(기록은 스택 상위 P5-03 PLAN). P5-03 구현·self-check(Graph→Form 왕복, Graph e2e, 잠금을 parse 실패로 좁힘, route de-flake, 문서 6·규칙·WORKFLOW), P5-02 후속 `4bf6342` 위로 `--onto` rebase, diff freeze `e6a0074`, stacked PR #133(base P5-02), `review_gui_p5_03`(opus) 배정 → IN_REVIEW(활성 리뷰 P5-02 2차·P5-03 = 2). 결정: `projection.readOnly`(JSON)는 유지, DAG 카드·편집 목록 분리 유지, 디바운스 대기는 잠그지 않음(live 텍스트 preflight) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p5_02` 1차 REQUEST_CHANGES(P1 DEFECT-132-01: 노드 삭제가 표시 이름으로 `nodePointerOf` 첫 일치 노드를 찾아 `node_id` 중복·누락 문서에서 다른 노드를 지움 — GUI만으로 도달 가능한 silent data loss; P2 7: kind 미해소 노드 선택 시 안내 모순, 확정 직후 디바운스 잠금, 중복 후보 option key, CSS 폴백, aria-label 중복, ready 분기 단위 테스트 없음, SoT 행 이월) → 후속 `45f01c9`: `removeNodeAt(tree, factorPointer, nodePointer)`(pointer 정본)로 삭제, 중복 표시 이름은 문서 순번(`spare (4) · 삭제`), 못 찾은 노드 `graph.removeMissing` 안내, kind 미해소 노드는 kind select(`setNodeField(kind)`), reference 후보 `Set` 중복 제거, CSS·aria 정리, stale route 테스트 `findByText`(디바운스 대기 — 전체 실행 flake 원인), P5-01 후속 위로 rebase → 재검토 요청. 잔여: 디바운스 잠금(132-03)은 P5-03에서 훅 `disabled` 판정을 parse 실패로 좁혀 처리, `node_id` 중복 방지·rename은 범위 밖 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p4_05` 1차 APPROVE(`b394dbe`, P2 4) → P4-05 APPROVED(기록은 스택 상위 P5-02 브랜치 PLAN). P5-02 구현·self-check(편집 표면은 parse tree, `projectObjectSection`, `FormFieldsEditor` owner, plan 없는 상태에서도 편집 — 감사 R4·R6), P4-05 P2-1·P2-4 처리, diff freeze `0097e51`, stacked PR #132(base P5-01 `a0e533d`), `review_gui_p5_02`(opus) 배정 → IN_REVIEW(활성 리뷰 P5-01·P5-02 = 2). 결정: 투영(plan 순서)과 편집 목록(문서 순서)은 별개 표면, 입력 재연결은 reference select(그래프 스코프·자기 제외), 그래프 설정은 `graph`가 있을 때만 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p5_01` 1차 REQUEST_CHANGES(P1-1 `addNode`가 `x-reference`를 `schemaFacts` 없이 직접 읽음(SoT owner 위반, P4-05 DEFECT-P4X-003 재발); P2 8: `- - "#tag"` 안쪽 유일 항목 삭제 계획 실패(base, property 간헐 red), 문서 머리 주석 삭제, 다중 참조 분기 즉시 valid 불성립, `rewireInput` 키 미검증, P5-02 소비자용 `nodeReferenceKeys` 부재·`nodePointerById` 오기·kind 라벨, `within`·`definingPointer` 짝, 스니펫 memo 죽음, scope 왕복 슬롯 부활(기록)) → 후속 `226231d`: `nodeReferenceKeys`(`schemaFacts(...).reference`) + `addNode`·`rewireInput(schema?)`이 사용, 참조 슬롯 하나뿐인 분기만 마지막 노드로 채움, 머리 주석(offset 0 블록) 보존, `parentLine < to` 가드, `within` 주석, memo 의존성 슬롯 값, WORKFLOW 정정 → 재검토 요청. P5-02(#132, 리뷰 중)·P5-03 브랜치는 리뷰 뒤 rebase | 13.5 재검토 |
| 2026-09-18 KST | Claude | P5-01 구현·self-check(`graph-transactions.ts` 8함수 + 감사 착수 조건 R1 `findReferences` `within`·R2 `feedbackFor`·R3 `planRemove` 선행 주석), diff freeze `1e09b6e`, stacked PR #131(base P4-05 `76842ab`), `review_gui_p5_01`(opus) 배정 → IN_REVIEW(활성 리뷰 P4-05·P5-01 = 2). 결정: 스코프는 호출자가 옵션으로 넘긴다(모듈은 스키마를 모른다), `feedback`은 유지하고 owner 슬롯을 더한다, 선행 주석은 삭제 대상의 것(빈 줄에서 멈춤), `setOutput`·`rewireInput`에 `not-found` 추가(fail-closed) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | P4-05 구현·self-check(중첩 목록 섹션 `lists`·`parentPointer/parentWritten`·`listOwners` 진단 소유권, `appendOperation` 부모까지 열기, 패널 재사용, discriminator `schemaFacts`, `x-catalog` 집합 테스트, README·SoT·WORKFLOW·문구), diff freeze `b394dbe`, stacked PR #130(base P4-04 `ef19805`), `review_gui_p4_05`(opus) 배정 → IN_REVIEW. e2e 첫 실행 실패는 감사 에이전트의 backend 환경 재동기화로 `backtest_core` 확장이 빠진 환경 문제(재빌드 후 6/6). 결정: 중첩 배열은 link 필드가 아니라 목록 섹션(루트 목록과 같은 뷰·연산), 항목 안 배열은 `list-link` 유지, DEFECT-P4X-002 스코프는 P5-01 착수 조건 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `audit_gui_phase4` 수신: PASS(blocking 0). exit 질문 통과(YAML 텍스트 생성은 `planSourceOperation` 하나, `schemaFacts`·`referenceCandidates`·`materializeSchemaValue` 단일 owner, `schema-assist`·`canonical-snippets`의 마커·enum·default 직접 읽기 0 → Phase 3 R5 해소, 왕복 불변식 바이트·spec_hash 동일). P1: DEFECT-P4X-001 중첩 목록 범위 미확정 → **WORKFLOW 자체 수정: P4-05 추가(19 PR)**, DEFECT-P4X-002 `findReferences` node 스코프 오탐 → P5-01 acceptance에 스코프 인자 결정. Phase 5 위험: R1 스코프, R2 feedback owner별 슬롯(P5-01 결정), R3 `planRemove` 선행 주석(P5-01 결정), R4 plan 없는 Graph 편집 표면(P5-02 제약), R5 빈 `nodes: []` insert-item 가능, R6 노드 pointer 규약(P5-02). 문서 액션 11 반영(WORKFLOW·SoT 행·PLAN·README 2·`form.field.listLink` 문구). Phase 4 exit 2항 체크 | 13.6 Phase 감사 |
| 2026-09-18 KST | Claude | `review_gui_p4_03` 2차 APPROVE(`24ad45a`) → P4-03 APPROVED, `review_gui_p4_04` 2차 APPROVE(`8c5ade0`, 신규 P2 DEFECT-P404-011 기록) → P4-04 APPROVED. Phase 4 PR 4/4 승인(승인 기록은 스택 상위 P4-04 브랜치 PLAN에 합류). 다음: Phase 4 exit 감사(`audit_gui_phase4`, opus) → P5-01 | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p4_04` 1차 APPROVE(P1 0·P2 10; 실측: hidden 편집기 유지·replaceRange 1회·문서 경계 누수 없음·undo 2회·view 전환 identity 유지·JSON/stale 잠금). 권고 P2를 후속 `8c5ade0`로 처리: (001) stale을 "같은 버전 parse 실패"로 좁혀 디바운스 구간 STALE 오탐 제거, (003) 섹션 접기 테스트, (005/006/007) `inapplicablePointers`·`view` prop·고아 키 7·고아 CSS 8 삭제, (008) SoT 행 Graph 읽기 전용 단서, (010) 안내 문단 role 제거, (004) PR 본문 정정. 기록: (002) 공유 슬롯 하나라 스니펫 결과가 Form 반영됨을 지움 → P5-01, (009) e2e 팩터 추가 검증 약함, R6 근거는 e2e가 아니라 잠금·route 테스트. P4-03 후속 `24ad45a` 위로 rebase, 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P4-04 구현·self-check → P4-03 head `b089e05` 위로 rebase(P4-03 패널 CSS 중괄호 결함은 이 브랜치의 e2e 백테스트 결과 배경 검사가 잡아 P4-03에서 수정), Playwright chromium-workflow 6/6, diff freeze `13fa8d3`, stacked PR #126(base P4-03), `review_gui_p4_04`(opus) 배정 → IN_REVIEW(활성 리뷰 P4-03·P4-04 = 2). 결정: 트랜잭션 인스턴스는 page 하나·`editorActive`는 "handle이 살아 있는가"(스니펫만 source view 게이트), stale은 마지막 유효 parse로 그리되 훅 `disabled: syntax`로 잠금, JSON 문서는 문구 안내(포맷 변환 명령 없음), 섹션 접기는 `aria-expanded` + `hidden`(DOM 유지), 이중 확정 결함은 P4-02 후속으로 이관 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p4_03` 1차 REQUEST_CHANGES(P1 DEFECT-125-01: 목록 키가 문서에 없으면(새 전략 starter·생략형 문서) "항목 추가"·preset이 `insert-item` → `not-found`로 언제나 실패, 테스트가 스텁이라 통과; P2 6: identity 추출이 카탈로그 필드 오인, 삭제 거부 안내가 pointer에 묶여 재색인 뒤 잔존, 삭제 시 선행 주석 고아(`planRemove` 소관), 괄호 가드 개수 균형만, saved_factor 오탐 기록, kind option 원문) → 후속 `24ad45a`: `ListSection.written` + 미작성이면 `insert-key`로 키 열며 첫 항목(실제 planner 테스트), `FormListItem.identityKey`(x-authoring-identity) 우선·카탈로그 필드 제외, 거부 안내는 tree 바뀌면 해제, 가드 docstring, WORKFLOW 기록 → 재검토 요청. 잔여: 주석 고아는 P5-01 노드 삭제와 함께 `planRemove`에서 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P4-03 구현·self-check → P4-02 승인 head `4b3b456` 위로 rebase, P4-01·P4-02 2차 리뷰 잔여 P2 8건을 둘째 커밋 `6460971`으로 처리(apply/run boolean 반환, 재확정 판정 컨트롤 로컬(Enter 재시도·blur 무시), passive 라벨 CSS, placeholder 패널 테스트, `includeRoot` 제거, 캐스트 0, summarize `$ref`), P4-04 e2e에서 실측한 패널 CSS 닫는 중괄호 누락(번들 뒤쪽 규칙 전부 중첩 → 앱 스타일 소실)을 3커밋으로 수정 + 스타일시트 괄호 균형 테스트(30), diff freeze, stacked PR #125(base P4-02), `review_gui_p4_03`(opus) 배정 → IN_REVIEW. 결정: 참조 탐색은 스키마를 모르는 `<ns>_id`/`*_<ns>_id` 키 규칙(오탐은 삭제 거부 방향), kind 선택은 헤더 select, 빈 factor `graph`는 materialize 결과(최소 노드는 P5-01), `eligibility.rules` 중첩 목록은 후속 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p4_02` 2차 APPROVE(`f1a76dc`) → P4-02 APPROVED. Phase 4 PR 2/4 승인. 검증 수치 정정(Vitest 539·panel 11). 잔여 P2 5건(009/011/012 재확정 식별, 010 passive 라벨 CSS, 013 placeholder 패널 검증)은 P4-03에서 처리. 다음: P4-03·P4-04 PR 개설(활성 2) | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p4_01` 2차 APPROVE(`cd9addf`) → P4-01 APPROVED. 검증 수치 정정(Vitest 522·46 files). 잔여 P2 3건(`includeRoot` 죽은 파라미터, `as FormField` 캐스트, summarize `$ref` const)은 P4-03에서 처리 | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p4_02` 1차 REQUEST_CHANGES(P1: DEFECT-P402-001 링크 행의 "기본값으로"가 배열 전체 삭제; P2: 실패 후 재확정 불가, 무효 안내 잔존, 링크 행 label 연결, `--surface` 토큰, 죽은 코드, WORKFLOW export 목록, PlanFailure 문구 3종) → 후속 `f1a76dc`(P4-01 후속 `cd9addf` 위로 rebase): 링크·const 행 버튼·컨트롤 제거 + `aria-labelledby`, 재확정·무효 해제, 토큰·죽은 코드·문구, const 읽기 전용·x-default-from placeholder, P4-04에서 실측한 이중 확정 결함(적용 뒤 `focus()`가 blur 유발)을 `focusEditor:false`·`(draft, committed)` 가드로 이관, 테스트 4건 → 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P4-02 구현·self-check(별도 worktree) + Phase 3 감사 대응(R1·R3: 훅 `disabled` 사유 owner, `editor-inactive`; R2: feedback `owner`, 스니펫 훅 주입; R4: reset 주석 삭제 테스트·PR 명기; DEFECT-003 계약 테스트; DEFECT-004 scope 값 비교) → diff freeze `5946bf9`, stacked PR #122(base P4-01), `review_gui_p4_02`(opus) 배정 → IN_REVIEW(P4-01·P4-02 동시 리뷰, parallel_window 2). 사용자 대면 변화: projection view의 스니펫 삽입 안내가 `editor-inactive` 문구로 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | P4-01 구현·self-check → P3-02 감사 후속 `bef91cc` 위로 rebase, 감사 R5로 SoT 행을 "필드 표시 사실" 범위로 축소, diff freeze `d9160ef`, stacked PR #121(base P3-02), `review_gui_p4_01`(opus) 배정 → IN_REVIEW. 결정: `schemaFacts`/`referenceCandidates`를 navigator로, `applicable`(스키마 판정)과 배지(backend 진단) 분리(Phase 2 감사 이월 결정 확정), 루트 스칼라 `key: ""` 섹션, `properties` object 필드 → graph-link·배열 필드 → list-link, summary 규칙 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `audit_gui_phase3` 수신: PASS(blocking 0). DEFECT-P3X-001 flow 컬렉션 제한 문구 과대(replace-scalar는 동작)·002 `yes` 따옴표 예시 오류·003 `run`이 `enabled`를 강제하지 않는 계약 미단언·004 feedback scope가 memo identity → 문서 액션 9건 반영(WORKFLOW acceptance 블록 2·제한 문구 2·SoT 행 2·PLAN 2). Phase 2 이월 5: 3 해소, 업그레이드 `replaceRange` 별도 경로는 설계상 잔존(SoT 행에 명기), boolean equals 비교 변동 없음. Phase 4 위험 R1(Form view 활성 시 `editorActive=false`로 전부 거부)·R2(스니펫 훅 자체 인스턴스 → 공유 시 feedback/label 교차)·R3(`formDisabledReason` 중복 판정)·R4(reset이 섹션 마지막 필드에서 주석 삭제)·R5(P4-01 SoT 행이 코드보다 강함)·R6(연타 시 parse 2회 누적) → 결정: 훅이 `disabled` 사유·owner별 feedback을 내고 `editorActive`는 "hidden 편집기가 살아 있는가"(R1·R3, P4-02), 스니펫 훅은 인스턴스 주입 + owner `snippet`(R2, P4-02), R4는 P4-02 테스트·PR 본문 명기, R5는 P4-01 SoT 행을 실제 범위로 축소, R6는 P4-04 e2e 시나리오, 004는 값 비교로 수정(P4-02). Phase 3 exit 2항 체크 | 13.6 Phase 감사 |
| 2026-09-18 KST | Claude | `review_gui_p3_02` 2차 APPROVE → P3-02 APPROVED. Phase 3 PR 2/2 승인. 게이트 수치 정정(Vitest 512·source-transactions 19·canonical-snippets 16). 다음: Phase 3 exit 감사(`audit_gui_phase3`) → P4-01(#PR 대기, 브랜치 `feat/gui-p4-01-form-projection` 구현 완료)·P4-02(브랜치 `feat/gui-p4-02-form-panel` 구현 완료) PR 개설 | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p3_02` 1차 REQUEST_CHANGES(P1-1 스니펫이 커서 줄이 아니라 앞 형제 뒤에 들어가 선행 주석·빈 줄 위로 이동, P1-2 설계 결정 5의 "property가 P2-R1 회귀를 잡는다"가 생성기 밀도 탓에 거짓(6000 샘플 0건); P2-1 insert-key/insert-item의 대상 위 주석 규칙 상반 + 코드 주석 반대, P2-2 부모 접힘 시 `key: # 메모` 줄 끝 주석 소실, P2-3 `applyToTree` 값 없는 부모 + `before` 비대칭, P2-4 nit 4) → 후속 `96d6b3b`: `options.anchor`(형제 구간 안의 커서 줄 자리)로 스니펫 위치 복원 + 테스트 5, 삽입 앵커를 키·항목 공통 "앞 형제 내용 줄 끝 뒤"로 통일, 생성기 줄별 1/3 주석(버그 되돌리면 58회째 반례 실측), 부모 줄 끝 주석 보존(`{}`/`[]` 다음 줄), `applyToTree` before 거부, `run` docstring·타입 가드·barrel 축소, WORKFLOW·PR 본문 정정 → 재검토 요청. 잔여 위험 기록: 확정 시점 호출 규칙은 호출자 규율(P4-02는 텍스트류 blur/Enter·선택류 변경 즉시로 설계, keystroke 호출 없음), `onEditorReady`가 setState(인라인 람다 호출자 주의), 주석 고아 보존, property는 줄 끝 주석·따옴표 키·flow 내부 미생성 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P3-02 구현·self-check(훅 5·단위 15·property 3000회·Vitest 503·e2e 5) → diff freeze `d13830c`, stacked PR #120(base P3-01), `review_gui_p3_02`(opus) 배정 → IN_REVIEW. 결정: 훅은 `apply(op)` 외 `run(planner)`(스니펫은 커서 줄 부분 키 제거+삽입을 undo 한 번으로), 스니펫 중복 판정은 원문 전체로 먼저, `insert-key.before`는 앞 형제 내용 줄 끝 뒤(주석 소유 유지), 값 없는 `key:`·빈 문서는 삽입 부모로 허용(P2-6 해소), property `remove` 관용은 주석 소유자 규칙(P2-R2)로 대체하고 그 규칙으로 `- - x` 갈래를 코드 수정(P2-R1 대안 2). cleanup 커밋 2건(P2-03 nit 118-10/11/13, P1-06 nit 13/14) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p3_01` 2차 APPROVE(P1-1·P1-2 해소, P2 9 전부 해소, 회귀 점검·`FC_NUM_RUNS=5000` 통과) → P3-01 APPROVED. 머지 전 요청 1건(WORKFLOW 알려진 제한에 P2-R1 예외 한 줄) `fb8a1b4` 반영. P3-02로 이월: P2-R2 property `remove` 관용 절 좁히기(pointer 자기 줄 범위)·multiset 비교, `const escape` 별칭 제거, 빈 `key:` 부모 확장(P2-6; 스니펫 재구성이 필요로 함), 훅의 parse 2회 비용은 확정 시점 호출로 한정 | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p3_01` 1차 REQUEST_CHANGES(P1-1 block scalar range가 줄바꿈 포함 → 경계 한 줄 초과·silent 주석 삭제, P1-2 시퀀스 항목·dash 줄 첫 키 삭제가 다음 주석 삭제; P2 9: property 관용 절·항등식 단언·생성기 커버리지(주석·빈 컨테이너·block scalar 0건), escape 중복, flow 컬렉션 미지원 사유, 빈 `key:` 부모, `- []` 후행 공백, EOL 혼재) → 후속 `51301cc`(전부 조치, P2-5·6은 WORKFLOW 제한 명기) → 재검토 요청. 잔여 위험 기록: 연산 1회 = parse 2회(67KB 문서 ~720ms) → P3-02 훅에서 parse 결과 재사용/디바운스 설계; 스니펫(커서 줄)·트랜잭션(문서) 들여쓰기 규칙 이원화는 P3-02 스니펫 재구성으로 해소, Phase 3 exit 점검 항목 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p2_03` 2차 APPROVE → P2-03 APPROVED. Phase 2 PR 3/3 승인. nit 4건(118-10~13)은 P3-02 cleanup 커밋 후보로 기록 | 13.5 판정 |
| 2026-09-18 KST | Claude | P3-01 구현·self-check(단위 10·property 3000회·Vitest 490) → diff freeze `2fba821`, stacked PR #119(base P2-03), `review_gui_p3_01`(opus) 배정 → IN_REVIEW. 결정: 삽입·삭제 경계는 노드 range가 아니라 leaf·키 범위 최댓값(노드 range가 뒤 주석 줄 포함), 시퀀스 항목은 자기 `-`에 앵커(`- - x`·`- key:` 첫 키), 성공 판정은 preflight parse + `applyToTree` deep-equal, fast-check는 WORKFLOW가 계획한 devDependency. 감사 4.2 SoT 행·4.5 WORKFLOW 규칙 반영 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p2_03` 1차 REQUEST_CHANGES(P1 DEFECT-118-01: Form 배지가 backend가 침묵시킨 기본값 필드에 경고; P2 8) + `audit_gui_phase2` 수신(blocking 0; DEFECT-P2X-001 STARTER 버전 리터럴, 002 `LEGACY_SCHEMA_VERSION` 리터럴이 다음 은퇴 시 배너 소멸, 003 Form에 signal 섹션 없음, 004 description_key 미번역 silent, 005 진단 문장 이중 owner; Phase 3 위험 5: `planSnippetEdit` 규칙 절반이 P3-01과 겹침, `locateRange` 조상 fallback을 remove에 쓰면 문서 파괴, `replaceRange` 격리 소비자 단일화, Form/Inspector 판정 입력 차이 기록, boolean 조건 비교 방식 차이) → 후속 `076e8d8`: Form 배지 = compile 경고 pointer, Inspector는 발행 기본값으로 판정, 글리프, signal 섹션, 문구 키 커버리지 테스트, `valueAtPointer` 공유, 중립 문구, 버전 리터럴 판정 제거, STARTER 단언 테스트, 규칙 문서 3개 → 재검토 요청. Phase 2 exit 3항 체크. 결정: 진단 message는 backend 한글 문장 통과(감사 5(b)), Form/Inspector 판정 입력 차이는 P4-01 착수 시 결정 | 13.5 · 8절 |
| 2026-09-18 KST | Claude | `review_gui_p2_02` 3차 APPROVE → P2-02 APPROVED. `review_gui_p1_06` 후속 `e77f7ce` 확인(P2-001 해소, P2-002 부분: 1.1 row를 1.2에서 읽는 시나리오 테스트 없음 → 1.2 도입 PR 몫으로 기록, P2-003 해소; nit: `_prepend_before_key`를 `_finish_emptied_sections`에서도 재사용, 시퀀스 항목 `-` 한 글자 줄 렌더 모양 단언 → cleanup 후보). Phase 2 exit 감사 `audit_gui_phase2`(opus) 착수 | 13.5 판정 · 8절 |
| 2026-09-18 KST | Claude | P2-02 2차 REQUEST_CHANGES(P1-001 잔존: 재시도 시 `/revisions/1` 저장이 409) → 3차 후속 `6278c40`(시도마다 고유 전략 id seeding, `--repeat-each 2` 10 passed) 재검토 요청. `review_gui_p1_06` APPROVE(P2 3) → 후속 `e77f7ce`(시퀀스 항목 첫 키 아래 주석 보존, 버전 리터럴 가드, WORKFLOW 잔재) → P1-06 APPROVED. 스택 rebase: P1-06 → P2-02 `6278c40`, P2-03 → P1-06 `e77f7ce`. P2-03 구현 → diff freeze `6ec933f`, stacked PR #118(base P1-06), `review_gui_p2_03`(opus) 배정 → IN_REVIEW. 결정: Inspector·hover는 parse tree로 판정(조건 필드 없으면 판정 불가, 기본값 미복제), Form은 컴파일된 spec으로 판정 | 13.3·13.5 |
| 2026-09-18 KST | Claude | 계획 외 PR `P1-06`(감사 후속) 추가: DEFECT-P1X-001~004 수정, 규칙 문서·spec D4 갱신, P2-01 리뷰 P2-001~006 후속 → diff freeze `11576cc`, stacked PR #117(base P2-02), `review_gui_p1_06`(opus) 배정 → IN_REVIEW. Phase 1 exit 3항 체크. 소유자 확인 필요: PR 추가(17→18), `collaboration-language.md` docstring 정책 제안 보류 | 8절 Phase 종료 gate · 13.3 |
| 2026-09-18 KST | Claude | `review_gui_p2_02` 1차 REQUEST_CHANGES(P1-001 e2e seeding 비멱등, P1-002 undo 격리 누락; P2-001~006) → 후속 `8f4c090`(CLI "있으면 그대로", 다음 revision 번호 API 조회, env 경로, `replaceRange` 격리, `upgradeable` 우선, saved 회귀 테스트, 422 번역, Pick 파생) + 기준선 갱신 `6d95226`(strict e2e 17 passed) → 같은 reviewer 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p2_01` APPROVE → P2-01 APPROVED. `audit_gui_phase1` 결과 수신: blocking 0, DEFECT-P1X-001(schema 버전 상수 이중 owner)·002(`periods` 삭제 시 아래 주석 소실)·003(동결 술어 어댑터/port 불일치)·004(`DocumentUpgradeSyntaxError` 메시지), 문서 액션 8(SoT 4행+금지 1, boundary 2, frontend-testing 1, collaboration-language docstring 정책 1[규칙 변경이라 소유자 확인 필요], spec D4). 조치는 P1-06(감사 후속, backend+docs) PR로 묶는다 | 13.5 판정 · 8절 Phase 종료 gate |
| 2026-09-18 KST | Claude | P2-02 구현·self-check(Vitest 464, e2e workflow 5 passed) → diff freeze `b7f8de1`, stacked PR #116(base P2-01), `review_gui_p2_02`(opus) 배정 → IN_REVIEW. 결정: 배너 판정은 backend 진단 + parse tree `schema_version`(지원 버전은 backend 소유), 적용은 `setText` 한 번(reducer에 교체 action 추가 안 함), e2e 1.0 row는 backend 테스트 헬퍼 CLI로 seeding, 배너 상태는 `sourceVersion·savedVersion`에 묶음(첫 e2e에서 저장 후 안내가 남는 결함 발견·수정) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | P2-01 구현·self-check(Vitest 455, typecheck·lint·build, SDK 재생성 diff 0) → diff freeze `802d6e2`, stacked PR #115(base P1-05), `review_gui_p2_01`(opus) 배정 → IN_REVIEW. 결정: 팩터 컬렉션은 루트 배열 + `x-authoring-identity`로 탐색(키 이름 미가정), 루트에 `factors`가 이미 있으면 스니펫 `duplicate`, optional이 된 단계는 빈 객체 렌더(기본값 복제 없음) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p1_05` 재검토 APPROVE(P1-001 철회) → P1-05 APPROVED. Phase 1 PR 5/5 승인, Phase exit SoT·책임분리 점검 서브에이전트 착수 | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p1_05` REQUEST_CHANGES(P1-001 생성 SDK 미동기 — 스택 결정으로 P2-01에서 해소, WORKFLOW 1절에 `api:generate` 게이트까지 명시; P2-002~004) → 후속 `3c70001`(AND 조건, selection_count 행, 동일 모양, owned_by_error 노출), 1,313 passed, 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P1-05 구현·self-check(1,310 passed) → diff freeze `b94d4ce`, stacked PR(base P1-04), `review_gui_p1_05`(opus) 배정 → IN_REVIEW. 결정: 기존 error 규칙이 소유한 3행은 warning 없이 스키마 노출만(`owned_by_error`), 기본값과 같은 명시값은 경고 없음(canonical 문서가 조용하도록) | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p1_04` 4차 APPROVE → P1-04 APPROVED. P1-05 IN_PROGRESS, 브랜치 `feat/gui-p1-05-field-applicability`(base P1-04 `294864c`) | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p1_04` 3차 REQUEST_CHANGES(P1-004 인접 두 빈 섹션에서 set 순회 순서·슬롯 pop 탓에 출력 비결정) → 후속 `f7049fa`(문서 순서 고정, 줄끝 슬롯만 비움, 결정성 테스트, drift 강등 시 warning 로그), 1,289 passed, 4차 검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p1_04` 재검토 REQUEST_CHANGES(P1-003 `#####` 줄끝 주석 섹션이 비면 IndexError 500 회귀, P2-006 빈 섹션 경로 아래 주석 소실, P2-007 주석 텍스트 변형) → 후속 `d529401`(원본 토큰 보존, 꼬리를 다음 최상위 키로, 어댑터 예외 전부 drift 422), 1,287 passed, 재검토 요청 | 13.5 재검토 |
| 2026-09-18 KST | Claude | `review_gui_p1_04` REQUEST_CHANGES(P1-001 openapi 미재생성, P1-002 422 union 누락; P2 5) → P1 후속 `5bd97fe`(union에 invalid 추가, 재생성, 추적 openapi 동기 테스트), P2 후속 `92800f7`(주석 재배치·비어 버린 섹션만·CRLF 통일·예외 분리·재parse 제거), 1,279 passed, 같은 reviewer 재검토 요청. 스코프 밖: 기존 codec `x: =` 500 결함은 별도 이슈 후보로 기록 | 13.5 재검토 |
| 2026-09-18 KST | Claude | P1-04 구현·self-check(1,276 passed) → diff freeze `0a70484`, stacked PR(base P1-03), `review_gui_p1_04`(opus) 배정 → IN_REVIEW. 결정: 422 코드 `strategy_document.not_upgradeable`·`strategy_document.upgrade_drift`(기존 `strategy_document.invalid` namespace), 비어 버린 섹션의 주석 잔해는 어댑터가 제거 | 13.3 diff freeze |
| 2026-09-18 KST | Claude | `review_gui_p1_03` 후속 확인 APPROVE 유지 → P1-03 APPROVED. P1-04 IN_PROGRESS, 브랜치 `feat/gui-p1-04-upgrade-endpoint`(base P1-03 `9ce5a7d`) | 13.5 판정 |
| 2026-09-18 KST | Claude | `review_gui_p1_03` APPROVE(P0/P1 0, P2 5) → 후속 `8bd0185`(requires_upgrade를 JSON API·목록·history에, schema_version 검증 422, 메시지 진단, hash owner 단일화, 재바인딩 계약 테스트, WORKFLOW 문구·P2-02 분기 기록), 1,257 passed, 같은 reviewer 확인 요청 | 13.5 |
| 2026-09-18 KST | Claude | P1-03 구현·self-check(1,253 passed) → diff freeze `7312c1a`, stacked PR(base P1-02), `review_gui_p1_03`(opus) 배정 → IN_REVIEW. 결정: 422 코드는 기존 namespace를 따라 `backtest.strategy.requires_upgrade`·`trace.strategy.requires_upgrade`(WORKFLOW의 `strategy_revision_requires_upgrade` 표기 대체) | 13.3 diff freeze |
| 2026-09-17 KST | Claude | `review_gui_p1_02` 후속 확인 APPROVE 유지 → P1-02 APPROVED. P1-03 IN_PROGRESS, 브랜치 `feat/gui-p1-03-upgrade-and-frozen-1-0`(base P1-02 `9ea8854`) | 13.5 판정 |
| 2026-09-17 KST | Claude | `review_gui_p1_02` APPROVE(P0/P1 0, P2 3) → P2-001~003 후속 `9a5cccb`(unary fallthrough 제거, 설명 문구 모델 사실만, demean 문서 hydrate 테스트), 1,232 passed, 같은 reviewer 확인 요청 | 13.5 |
| 2026-09-17 KST | Claude | P1-02 구현·self-check(1,231 passed) → diff freeze `aae4c23`, stacked PR(base P1-01), `review_gui_p1_02`(opus) 배정 → IN_REVIEW | 13.3 diff freeze |
| 2026-09-17 KST | Claude | `review_gui_p1_01` 재검토 APPROVE(`5d28996`) → P1-01 APPROVED. 로컬 main merge 대신 선언된 스택으로 진행(절차 조정 기록). P1-02 IN_PROGRESS, 브랜치 `feat/gui-p1-02-dead-fields-unary-alias`(base P1-01) | 13.5 판정 |
| 2026-09-17 KST | Claude | 리뷰 잔여 P2-003~008 반영 `d545f53`(canonical_payload_json 인코더, FieldContract.default_from, 죽은 헬퍼·docstring·치환 복원, label 비대칭 pin). backend 1,228 passed. reviewer 스코프 밖 관찰(P1 PR에서 browser-e2e 빨간불)을 WORKFLOW 1절에 알려진 상태로 기록 | 13.5 재검토 |
| 2026-09-17 KST | Claude | `review_gui_p1_01` REQUEST_CHANGES(P1-001 계약 의도 단언 부재, P2-002 default-from KeyError) → 수정 `1d885f5`(schema 단언 3, hydrate 모델 가드 + 테스트), backend 1,227 passed, 같은 reviewer 재검토 요청 | 13.5 재검토 |
| 2026-09-17 KST | Claude | P1-01 diff freeze `9fab26b`, PR #108(base #106), `review_gui_p1_01`(opus) 배정 → IN_REVIEW | 13.3 diff freeze |
| 2026-09-17 KST | Claude | P1-01 구현 완료 → SELF_CHECK. 결정: 노드 dataclass는 `kw_only`로 바꾸지 않고 schema builder가 `kind`를 첫 property로 정렬(positional 생성 호출 다수), `factors`에 `x-defines`를 두지 않음(defines↔references 불변식). Rust core는 worktree venv에 `maturin develop`로 빌드 | 13.2 self-check |
| 2026-09-17 KST | Claude | 설계 spec, 기획 패키지(README/WORKFLOW/PLAN/tools) 생성. 17 PR tracker. 스택 바닥 PR [#106](https://github.com/Nochiski/Quant_study/pull/106) | 제품 소유자 결정 |

## 갱신 절차

1. 대상 PR row의 상태와 Review 값을 수정한다.
2. merge 시 상태를 `MERGED`, checkbox를 `[x]`로 함께 바꾼다.
3. 현재 작업 Packet과 Review/검증/변경 기록을 갱신한다.
4. 다음 명령으로 frontmatter와 집계를 계산한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/strategy-gui-editing/tools/update-plan-progress.ps1
```

5. drift 검증은 `-Check`를 붙인다.

Reviewer는 이 파일을 수정하지 않는다. 구현 책임자가 reviewer verdict와 gate 결과를 반영한다.
CI 게이트는 origin push 전까지 로컬 동일 범위 gate로 대체하고, push 시점에 원격 결과를 `CI` 열에
소급 기록한다.
