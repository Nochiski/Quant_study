---
plan_version: 2
project: ai-assistant
project_status: IN_PROGRESS
current_phase: P0,A
current_pr: P0-01,A-01
active_prs: [P0-01, A-01]
parallel_window: [P0-01, A-01]
last_updated: 2026-09-20T22:23:41+09:00
planned_prs: 10
merged_prs: 0
approved_prs: 0
progress_percent: 0
---

# AI 어시스턴트 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P0,A` |
| Current/next PR | `P0-01,A-01` |
| Active PR | `P0-01, A-01` |
| Progress | `0 / 10 merged (0%)` |
| Approved | `0 / 10` |
| Aggregated at | `2026-09-20 22:23 KST` |
<!-- PLAN:SUMMARY:END -->

## 현재 결정

- 2026-09-20 제품 소유자 요청: 설정 화면에 Claude·Codex 연결, 그래프·YAML 어느 탭에서든 우측
  사이드바 AI 채팅. LLM은 들어온 데이터 + 인터넷 검색으로 시장을 조사해 전략을 제안한다. 확장
  여지를 위해 책임 분리를 우선한다.
- 참고 UI: `michelo.frontend` 설정 모달의 DB 프로파일 섹션(목록·활성 전환·추가·삭제·거부 사유
  toast). 그 저장소 main에는 Claude·Codex 연결 코드가 없어 패턴만 차용한다.
- 공급자 SDK는 outbound adapter에만. 도구는 application이 선언·실행. 검색은 v1에서 공급자 내장
  도구. 비밀은 backend 로컬 파일. 제안은 사용자의 "적용"으로만 문서에 들어간다(자동 적용 없음).
- Anthropic 기본 모델 `claude-opus-5`(adaptive thinking, effort high). OpenAI 기본 모델은 A-04
  구현 시 SDK 문서로 확정한다.
- reviewer 서브에이전트는 Opus로만. Phase 종료마다 SoT·책임분리 점검.

## 상태 값

`docs/planning/strategy-language-2-0/PLAN.md`의 상태 값과 같다.

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package | 1 | 0 | `IN_REVIEW` |
| A | Backend: ports, storage, HTTP, providers | 5 | 0 | `IN_PROGRESS` |
| B | Frontend: settings, entity, sidebar, e2e | 4 | 0 | `WAITING` |
| **Total** |  | **10** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-01` |
| Intent | 기획 패키지·spec을 main에 올린다 |
| Acceptance | WORKFLOW P0-01 |
| Non-goals | 코드 변경 |
| Branch/worktree | `docs/ai-assistant-plan` (PR #166) · A-01은 `wt-ai-a01` / `feat/ai-a-01-domain-ports` |
| Base SHA | `5f97f8c` (origin/main) |
| Head SHA | 리뷰 중 |
| Diff stat | — |
| Focused tests | `tools/update-plan-progress.ps1 -Check` |
| Full gate | — |

---

## P0 — 기획 패키지

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P0-01` | 기획 패키지·설계 spec·SoT 행 예약 | 없음 | `IN_REVIEW` | [#166](https://github.com/Nochiski/Quant_study/pull/166) · `review_ai_p0_01` 진행 중 |

## A — backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `A-01` | `domain/assistant`, `application/assistant_chat` 포트·서비스·도구 루프·제안 검증, 가짜 공급자 테스트, SDK import 게이트 | P0-01 | `IN_PROGRESS` | 구현자 `impl-ai-a01`, 워크트리 `wt-ai-a01`, 브랜치 `feat/ai-a-01-domain-ports` |
| [ ] | `A-02` | `assistant_sqlite`·`secrets_local` adapter, `/api/v1/assistant/*` + SSE, bootstrap, OpenAPI | A-01 | `WAITING` | — |
| [ ] | `A-03` | `llm_anthropic` adapter (claude-opus-5, web_search 서버 도구, 스트리밍, probe) | A-02 | `WAITING` | — |
| [ ] | `A-04` | `llm_openai` adapter (Responses API, web_search, 스트리밍, probe) | A-03 | `WAITING` | — |
| [ ] | `A-05` | 프롬프트 최종본, 컨텍스트 품질, 시나리오 fixture 3개 | A-04 | `WAITING` | — |

Phase exit:

- [ ] 가짜 공급자 SSE 시나리오 3개 green, live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 blocking 0.

## B — frontend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `B-01` | `/settings` 페이지, `configure-ai-providers` 섹션, `entities/assistant` 프로파일 query, SDK 재생성 | A-05 | `WAITING` | — |
| [ ] | `B-02` | 세션 query, SSE 리더, `ChatEvent` 리듀서, property test | B-01 | `WAITING` | — |
| [ ] | `B-03` | `chat-assistant` 사이드바, IDE `assistant` 슬롯, 제안 적용·미리보기·적용 후 백테스트 | B-02 | `WAITING` | — |
| [ ] | `B-04` | e2e(MSW 공급자), 매뉴얼·README·SoT·FSD 규칙 | B-03 | `WAITING` | — |

Phase exit:

- [ ] 완료 정의 1~5 기록.
- [ ] SoT·책임분리 점검 blocking 0.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|

## 변경 기록

- 2026-09-20 — P0-01 PR #166 생성, 리뷰 배정. A-01 구현 착수(P1·P2 스택과 독립이라 병렬).
- 2026-09-20 — 패키지 생성. 제품 소유자 요청(설정에서 Claude·Codex 연결, 우측 사이드바 AI 채팅,
  검색 기반 전략 제안, 책임 분리)을 spec D1~D9와 Phase 0·A·B, 10 PR로 정리.

## 갱신 절차

`docs/planning/strategy-language-2-0/PLAN.md`의 갱신 절차와 같다(도구 경로만 이 패키지).
