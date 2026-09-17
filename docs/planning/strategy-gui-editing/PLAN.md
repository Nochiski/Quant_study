---
plan_version: 2
project: strategy-gui-editing
project_status: IN_PROGRESS
current_phase: P1
current_pr: P1-01,P1-02,P1-03,P1-04,P1-05
active_prs: [P1-01, P1-02, P1-03, P1-04, P1-05]
parallel_window: [P1-01, P1-02, P1-03, P1-04, P1-05]
last_updated: 2026-09-18T01:35:23+09:00
planned_prs: 17
merged_prs: 0
approved_prs: 4
progress_percent: 0
---

# schema 1.1 · Form/Graph 편집 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P1` |
| Current/next PR | `P1-01,P1-02,P1-03,P1-04,P1-05` |
| Active PR | `P1-01, P1-02, P1-03, P1-04, P1-05` |
| Progress | `0 / 17 merged (0%)` |
| Approved | `4 / 17` |
| Aggregated at | `2026-09-18 01:35 KST` |
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
| P1 | Backend schema 1.1 | 5 | 0 | `IN_PROGRESS` |
| P2 | Frontend 1.1 and upgrade UI | 3 | 0 | `WAITING` |
| P3 | Source transactions | 2 | 0 | `WAITING` |
| P4 | Form editing | 4 | 0 | `WAITING` |
| P5 | Graph editing | 3 | 0 | `WAITING` |
| **Total** |  | **17** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P1-05` |
| Intent | `FIELD_APPLICABILITY` 조건표(domain), 명시된 필드가 현재 모드에서 읽히지 않으면 compile warning `strategy.field.inapplicable`, runtime schema·FieldContract에 `x-applicable-when` |
| Acceptance | WORKFLOW P1-05 |
| Non-goals | frontend 표시(P2-03), 노드 단위 pointer(demean의 quantile 등)는 후속 판단 |
| Branch/worktree | `feat/gui-p1-05-field-applicability` (base `feat/gui-p1-04-upgrade-endpoint` `294864c`) |
| Base SHA | `294864c` |
| Head SHA | — |
| Diff stat | — |
| Focused tests | — |
| Full gate | — |

---

## P1 — backend schema 1.1

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1-01` | 모델 1.1: `factors` 평탄화, 선택 보일러플레이트, `kind` 우선, fixture·hash golden | 없음 | `APPROVED` | [#108](https://github.com/Nochiski/Quant_study/pull/108) · `review_gui_p1_01` APPROVE (REQUEST_CHANGES P1 1/P2 7 해소) · `5d28996` |
| [ ] | `P1-02` | 미사용 필드 3개·enum 제거, unary alias 제거, `cross_sectional: demean` | P1-01 | `APPROVED` | [#111](https://github.com/Nochiski/Quant_study/pull/111) · `review_gui_p1_02` APPROVE (P0/P1 0, P2 3 후속 반영 후 유지) · `9a5cccb` |
| [ ] | `P1-03` | `_upgrade.py` dict 변환, 1.0 row 동결 읽기, saved-reference backtest 422 | P1-02 | `APPROVED` | [#112](https://github.com/Nochiski/Quant_study/pull/112) · `review_gui_p1_03` APPROVE (P0/P1 0, P2 5 후속 반영 후 유지) · `8bd0185` |
| [ ] | `P1-04` | `POST /strategy-documents/upgrade` (ruamel rt, drift fail-closed) | P1-03 | `APPROVED` | [#113](https://github.com/Nochiski/Quant_study/pull/113) · `review_gui_p1_04` APPROVE (4차; P1 4·P2 7 해소) · `f7049fa` |
| [ ] | `P1-05` | `FIELD_APPLICABILITY`, compile warning, `x-applicable-when` | P1-02 | `IN_PROGRESS` | — |

Phase exit:

- [ ] 1.1 fixture 4종 같은 hash, 1.0 fixture 거부, 업그레이드 golden 통과.
- [ ] 1.0 row 동결 읽기와 변조 fail-closed.
- [ ] SoT·책임분리 점검 서브에이전트 결과 기록, `strategy-workbench-sot.md` owner 행 추가.

## P2 — frontend 1.1

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | generated SDK 1.1, pointer helper·snippet·outline·plan·graph·debugger 적응 | P1-05 | `WAITING` | — |
| [ ] | `P2-02` | 1.0 문서 업그레이드 배너·동작, e2e fixture/spec 1.1 | P2-01, P1-04 | `WAITING` | — |
| [ ] | `P2-03` | Contract Inspector·Problems 적용 조건 표시 | P2-01, P1-05 | `WAITING` | — |

Phase exit:

- [ ] e2e "1.0 revision 열기 → 업그레이드 → 저장 → backtest" green.
- [ ] SoT·책임분리 점검 서브에이전트 결과 기록.

## P3 — source 트랜잭션

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P3-01` | `source-transactions.ts` 원시 연산 4종, preflight, property test | P2-01 | `WAITING` | — |
| [ ] | `P3-02` | `useSourceTransactions`, 스니펫 삽입 재구성 | P3-01 | `WAITING` | — |

Phase exit:

- [ ] property test가 임의 문서·연산에서 tree 동등·범위 밖 바이트 보존을 증명.
- [ ] SoT 점검(정본은 source 하나, frontend에 필드 목록 없음).

## P4 — Form 편집

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P4-01` | `form-projection.ts` (schema × tree × 진단) | P3-02, P2-03 | `WAITING` | — |
| [ ] | `P4-02` | 스칼라·enum·boolean·date·nullable·catalog·reference 컨트롤 | P4-01 | `WAITING` | — |
| [ ] | `P4-03` | 목록 섹션: eligibility rules, parameters, factors 헤더·preset, 참조 가드 | P4-02 | `WAITING` | — |
| [ ] | `P4-04` | IDE·page 연결, stale/JSON 잠금, i18n, e2e, SoT 규칙 개정 | P4-03 | `WAITING` | — |

Phase exit:

- [ ] e2e "Form 값 변경 → 주석 유지 → 저장 hash 동일" green.
- [ ] SoT·책임분리 점검 서브에이전트 결과 기록.

## P5 — Graph 편집

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | `graph-transactions.ts`: 노드 추가·필드·재연결·출력·삭제 가드·id 제안 | P4-03 | `WAITING` | — |
| [ ] | `P5-02` | Graph UI: 노드 추가 메뉴, property editor, 입력 슬롯, 키보드·ARIA | P5-01 | `WAITING` | — |
| [ ] | `P5-03` | 연결·e2e·문서·규칙 마감, read-only 문구 제거, 로드맵 M8 | P5-02, P4-04 | `WAITING` | — |

Phase exit:

- [ ] e2e "노드 추가 → 재연결 → plan 갱신 → 저장" green.
- [ ] ADR·SoT·README·manual·로드맵 갱신.
- [ ] SoT·책임분리 최종 점검 기록.

---

## Review 기록

수정 재검토에는 같은 reviewer를 사용한다.

| PR | Reviewer agent | Base SHA | Final HEAD SHA | Verdict | P0/P1 | Residual risk | Reviewed at |
|---|---|---|---|---|---:|---|---|
| P1-04 | `review_gui_p1_04` | `9ce5a7d` | `f7049fa` | APPROVE (1차 REQUEST_CHANGES P1-001 openapi 미재생성·P1-002 422 union 누락·P2 5 → 2차 P1-003 `#####` 500 회귀·P2-006/007 → 3차 P1-004 인접 빈 섹션 비결정 → 4차 APPROVE; 시드 8종×2 배치 결정성, 프로브 전량 재실행) | 4 (해소) | 비어 버린 섹션이 마지막 키면 아래 주석 소실(설계상), 옮겨진 주석의 들여쓰기 유지, `except Exception` 강등은 로그로 구분; 기존 codec `x: =` 500은 별도 이슈 후보 | 2026-09-18 |
| P1-03 | `review_gui_p1_03` | `9ea8854` | `8bd0185` | APPROVE (P0/P1 0, P2-001~005 후속 확인 후 유지; 적대적 프로브 6종, 동결 head 위 1.1 append·pagination·업그레이드→저장→실행 e2e, CST parity) | 0 | `requires_upgrade` 3개가 dataclass 기본값 탓에 SDK에서 optional로 생성됨(P1-04에서 required로 정리); P1-04 CST golden이 덮어야 할 주석 손실 3종(안쪽 `factors:` 줄끝 주석, 바깥 독립 주석 뒤 시퀀스 들여쓰기, 삭제 키의 주석) | 2026-09-18 |
| P1-02 | `review_gui_p1_02` | `5d28996` | `9a5cccb` | APPROVE (P0/P1 0, P2-001~003 후속 `fcc3c8c` 확인 후 유지; 적대적 hydrate 10건, demean parity 무작위 200 + 경계 4) | 0 | demean 노드도 winsorize 전용 quantile 파라미터를 받음(P1-05 적용 조건표 후보), enum 순서 미고정, minimal fixture의 `signal` 생략 미단언 | 2026-09-17 |
| P1-01 | `review_gui_p1_01` | `3aa95d0` | `5d28996` | APPROVE (1차 REQUEST_CHANGES P1-001 계약 의도 단언 부재·P2 7건 → `1d885f5`·`d545f53` 반영, 변이 검증 3종·적대적 hydrate 27종 통과) | 1 (해소) | `default-from` 가드는 생략 시에만 실행(지연 검사, schema builder 불변식은 두 번째 default-from 도입 시 검토); `contract_hash`가 FieldContract 행 모양을 덮지 않음(schema const 변경으로 이번엔 무효화됨); 1.0 row는 P1-03까지 읽기 불가(의도) | 2026-09-17 |

## 검증 기록

| PR | Focused test | Full gate | API generated clean | Manual UX | CI | Recorded at |
|---|---|---|---|---|---|---|
| P1-04 | 어댑터 17·서비스 9·HTTP 5·openapi 동기 1 | backend pytest 1,289·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json` 재생성 diff 0 + 추적 동기 테스트 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-18 |
| P1-03 | upgrade 8·frozen repository 10·frozen HTTP 5 | backend pytest 1,257·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-18 |
| P1-02 | hydrate unknown_key 3·invalid_enum 4·demean 문서 1, demean parity 1, 설명 문구 1 | backend pytest 1,232·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 | 해당 없음 | 원격 CI backend job 대상, browser-e2e 알려진 빨간불 | 2026-09-17 |
| P1-01 | contract fixtures 15·schema 3 신규·hydrate 가드 1·JSON API label 비대칭 1 | backend pytest 1,228·Ruff check·Pyright 0; reviewer 독립 재실행 동일 | `openapi.json`·`runtime-schema.json` 재생성 후 diff 0 (frontend SDK는 P2-01) | 해당 없음(backend) | 원격 CI: backend job 대상, browser-e2e는 알려진 빨간불(WORKFLOW 1절) | 2026-09-17 |

## 변경 기록

| 시각 | 작성자 | 변경 | 근거 |
|---|---|---|---|
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
