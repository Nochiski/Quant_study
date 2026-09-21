---
plan_version: 2
project: strategy-language-2-0
project_status: IN_PROGRESS
current_phase: P0,P1,P2
current_pr: P0-01,P1-01,P1-02,P2-01
active_prs: [P0-01, P1-01, P1-02, P2-01]
parallel_window: [P0-01, P1-01, P1-02, P2-01]
last_updated: 2026-09-21T07:28:39+09:00
planned_prs: 28
merged_prs: 0
approved_prs: 2
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
| Current/next PR | `P0-01,P1-01,P1-02,P2-01` |
| Active PR | `P0-01, P1-01, P1-02, P2-01` |
| Progress | `0 / 28 merged (0%)` |
| Approved | `2 / 28` |
| Aggregated at | `2026-09-21 07:28 KST` |
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
- P2 backend PR은 `backend/openapi.json`만 재생성하고 frontend generated SDK는 P3-01이 갱신한다.
  P2 스택은 backend gate만 merge gate로 삼는다.
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
| P1 | In-screen friction removal on 1.1 | 5 | 0 | `IN_REVIEW` |
| P2 | Backend schema 1.2 (environment split, 9 PRs) | 9 | 0 | `IN_PROGRESS` |
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
| [ ] | `P1-01` | 문제 목록·검증 배지를 탭과 무관하게 렌더 | P0-01 | `IN_REVIEW` | [#168](https://github.com/Nochiski/Quant_study/pull/168) · `review_lang2_p1_01` 1·2차 APPROVE(blocking 0), 후속 8건 반영 · 게이트: typecheck·lint·Vitest 658·build·e2e 19/19 |
| [ ] | `P1-02` | 되돌리기·다시 실행 버튼, 전역 단축키 | P1-01 | `APPROVED` | [#173](https://github.com/Nochiski/Quant_study/pull/173) · `review_lang2_p1_02` 4차 APPROVE (1차·3차 REQUEST_CHANGES 반영, 2차 APPROVE·새 P2) |
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
| [ ] | `P2-01` | `RunEnvironment` 모델·브리지(`domain/backtest`), 실행 요청 optional `environment`, manifest·캐시 키, `/run-environments/schema` | P0-01 | `IN_PROGRESS` | 구현자 `impl-lang2-p2-01`, 워크트리 `wt-lang2-p2-01`, 브랜치 `feat/lang2-p2-01-run-environment` |
| [ ] | `P2-02` | `graph.missing_policy` 제거 → `environment.missing`(plan 인자, `plan_hash` 유지) | P2-01 | `WAITING` | — |
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
| `P1-02` | `review_lang2_p1_02` | 1차 | `REQUEST_CHANGES` | blocking 1 + P3 7. **P1**: 같은 문서 안의 전체 교체(초안 복구 `use-autosave.ts`, 서버 초안 적용 `use-server-draft.ts`)가 `SourceEditor`의 같은-epoch 분기에서 격리 없는 `setText`로 가, CodeMirror `newGroupDelay`(500ms) 안에 친 글자와 한 undo 단계로 합쳐졌다 — 되돌리기 한 번에 복구한 초안이 통째로 사라진다. → 같은-epoch 분기를 `replaceRange(0, length)`로 통일하고 호출자가 없어진 `setText`를 핸들에서 제거, SoT 행 문구 정정, 회귀 테스트 추가. **P3**: 480px 이하 탭·버튼 겹침, 탭 밑줄이 버튼 아래에서 끊김, `aria-disabled` 스타일이 공용 `:disabled`와 불일치, 날짜 입력 포커스에서 Ctrl+Z 무반응, 핸들 대역 들여쓰기, PR 본문 spec D8→D9, 12절 초과 사유에 줄 수 누락. 코드 6건 반영, 본문 2건은 리드가 정정. **오기**: "업그레이드 적용 undo 잠금 테스트 없음"(P2)은 사실과 다르다 — `upgrade-banner.test.tsx`에 undo 단언 2건(직후 타이핑 케이스 포함)이 이미 있다 |
| `P1-02` | `review_lang2_p1_02` | 2차 | `APPROVE`(코드) | 1차 blocking 해소를 확인. 구현이 권장(격리 주석 덧붙이기)보다 나은 방향 — 비격리 전체 교체 API 자체를 없앴고 새 회귀 테스트가 수정 전 실제로 실패함을 리뷰어가 재현. 1차 P2는 리뷰어가 철회(오기). P3 7건 중 5건 해소·1건 부분·1건 미해소(남은 둘은 PR 본문). **새 P2**: 탭 스트립의 `overflow-x: auto`가 `overflow-y`를 `auto`로 만들어 스크롤 컨테이너를 세우고, 탭 포커스 링(바깥 4px)이 위아래로 잘린다. 권장은 선언을 480px 미만으로 한정 |
| `P1-02` | `review_lang2_p1_02` | 3차 | `REQUEST_CHANGES` | 링 클립은 해소됐으나(도장 픽셀·기하 양쪽 확인) 해법의 부작용 1건. **P2**: 스크롤포트를 위아래 8px 넓힌 `padding-block`+음수 `margin-block`이, 툴바 액션 줄과 탭 줄 사이 2px 간격을 넘어 검증 버튼 하단 6px을 덮어 그 영역 클릭을 가로챈다(28px 버튼의 21%가 무표시 사각지대). → padding·음수 margin을 걷고 링을 `outline-offset: -2px`로 탭 안쪽에 그려 해소, 검증 버튼 하단 actionability e2e 단언 추가(직전 해법에서 실패 확인). 리뷰어는 2차의 "겹침은 480px 이하에서만" 판단을 실제 앱 계측으로 철회(1440px에서 이미 넘침) — `overflow-x` 유지 결정 확정 |
| `P1-02` | `review_lang2_p1_02` | 4차 | `APPROVE` | 코드 결함 0. 3차 P2(검증 버튼 하단 6px 클릭 가로채기)가 inset outline 교체로 해소되고, 회귀를 막는 e2e actionability 단언이 시각 project 4개에서 돈다. `overflow-x: auto` 유지, 탭 줄 한 줄·밑줄·`aria-disabled` 스타일·날짜 입력 단축키 모두 그대로. PR 본문 항목(spec D9, SoT와 어긋난 문장, 줄 수 근거, 테스트 수치)은 리드가 정정 |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|

## 변경 기록

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
  (`tools/quant_study_dev/conflict_markers.py`, 단위 테스트 8건, CI backend job step)을 추가했다.
- 2026-09-21 — P1-02 구현·리뷰 4회: PR #173. 되돌리기·다시 실행을 탭 밖 버튼과 IDE 전역
  단축키 두 경로로 내고, 편집 이력의 단일 정본을 편집기(CodeMirror history)로 못 박았다
  (SoT 행 추가). 1차 REQUEST_CHANGES: 같은 문서 전체 교체가 비격리 `setText`로 가 직후
  타이핑과 한 undo 단계로 합쳐지던 것을 `replaceRange` 격리 경로로 통일하고 `setText`를
  핸들에서 제거했다. 3차 REQUEST_CHANGES: 포커스 링 클립을 고치려 넓힌 스크롤포트가 검증
  버튼 하단 클릭을 가로채 `outline-offset: -2px`(inset)로 교체하고 actionability e2e 단언을
  추가했다. 4차 APPROVE(코드 결함 0).
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

## 갱신 절차

1. PR row의 상태·Review 열과 `현재 작업 Packet`을 고친다.
2. `변경 기록`에 한 줄 남긴다.
3. `pwsh docs/planning/strategy-language-2-0/tools/update-plan-progress.ps1`을 실행한다(`-Check`는 검증만).
