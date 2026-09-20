---
plan_version: 2
project: ai-assistant
project_status: IN_PROGRESS
current_phase: P0,A
current_pr: P0-01,A-01,A-02,A-03
active_prs: [P0-01, A-01, A-02, A-03]
parallel_window: [P0-01, A-01, A-02, A-03]
last_updated: 2026-09-20T23:20:10+09:00
planned_prs: 13
merged_prs: 0
approved_prs: 1
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
| Current/next PR | `P0-01,A-01,A-02,A-03` |
| Active PR | `P0-01, A-01, A-02, A-03` |
| Progress | `0 / 13 merged (0%)` |
| Approved | `1 / 13` |
| Aggregated at | `2026-09-20 23:20 KST` |
<!-- PLAN:SUMMARY:END -->

## 현재 결정

- 2026-09-20 제품 소유자 요청: 설정 화면에 Claude·Codex 연결, 그래프·YAML 어느 탭에서든 우측
  사이드바 AI 채팅. LLM은 들어온 데이터 + 인터넷 검색으로 시장을 조사해 전략을 제안한다. 확장
  여지를 위해 책임 분리를 우선한다.
- 참고 UI: `michelo.frontend` 설정 모달의 DB 프로파일 섹션(목록·활성 전환·추가·삭제·거부 사유
  toast). 그 저장소 main에는 Claude·Codex 연결 코드가 없어 패턴만 차용한다.
- 공급자 SDK는 outbound adapter에만. 도구는 application이 선언·실행. 검색은 v1에서 공급자 내장
  도구. 비밀은 backend 로컬 파일. 제안은 사용자의 "적용"으로만 문서에 들어간다(자동 적용 없음).
- P0-01 리뷰 반영(2026-09-20): 턴은 backtest_run 패턴(POST로 시작, GET `after_sequence` SSE, cancel은
  영속 상태 + 프로세스 내 신호, `AssistantTurnRunner`가 owner, 단일 워커 전제). 제안 적용은 업그레이드와
  같은 `replaceRange` 전체 교체 + stale 가드. 검색 `max_uses`·출력 토큰·벽시계 상한. base_url은 https만.
  `Failure.message`에 SDK 예외 문자열 금지. 출처 링크는 http/https만 + noopener. PR을 13개로 분할.
- Anthropic 기본 모델 `claude-opus-5`(adaptive thinking, effort high). OpenAI 기본 모델은 A-06
  구현 시 SDK 문서로 확정한다.
- OpenAPI와 frontend SDK는 A-04가 같은 PR에서 갱신한다(12절).
- reviewer 서브에이전트는 Opus로만. Phase 종료마다 SoT·책임분리 점검.

## 상태 값

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)의 상태 값과
tracker 규칙을 따른다(`PLANNED`·`READY`·`WAITING`·`IN_PROGRESS`·`SELF_CHECK`·`IN_REVIEW`·
`CHANGES_REQUESTED`·`APPROVED`·`MERGED`·`PAUSED`).

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package | 1 | 0 | `APPROVED` |
| A | Backend: ports, storage, HTTP, providers | 7 | 0 | `IN_PROGRESS` |
| B | Frontend: settings, entity, sidebar, e2e | 5 | 0 | `WAITING` |
| **Total** |  | **13** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-01` |
| Intent | 기획 패키지·spec을 main에 올린다 |
| Acceptance | WORKFLOW P0-01 |
| Non-goals | 코드 변경 |
| Branch/worktree | `docs/ai-assistant-plan` (PR #166) · A-01/A-02는 `wt-ai-a01` / `feat/ai-a-01-domain-ports` |
| Base SHA | `5f97f8c` (origin/main) |
| Head SHA | 4차 APPROVE 뒤 잔여 P2 반영 커밋 |
| Diff stat | 문서 8개 |
| Focused tests | `tools/update-plan-progress.ps1 -Check` |
| Full gate | CI(문서만) |

---

## P0 — 기획 패키지

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P0-01` | 기획 패키지·설계 spec·SoT 행 예약 | 없음 | `APPROVED` | [#166](https://github.com/Nochiski/Quant_study/pull/166) · `review_ai_p0_01` 4차 APPROVE(1~3차 REQUEST_CHANGES 전부 해소, 잔여 P2 1건 머지 전 반영) |

## A — backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `A-01` | `domain/assistant` 타입·도구 계약, `application/assistant_chat` 포트 5종·프로파일 서비스, SDK import 게이트, 경계 규칙 목록 | P0-01 | `IN_REVIEW` | [#169](https://github.com/Nochiski/Quant_study/pull/169) · `da08da7` · `review_ai_a_01` 1차 REQUEST_CHANGES(P1 1·P2 4·P3 5) 반영(P3 1건 사양) → 2차 재검토 중 |
| [ ] | `A-02` | 채팅 유스케이스·컨텍스트 빌더·프롬프트·턴 러너, 가짜 공급자 테스트 | A-01 | `IN_REVIEW` | [#170](https://github.com/Nochiski/Quant_study/pull/170) · `f01f69e`(A-01 위 rebase) · `review_ai_a_02` 1차 REQUEST_CHANGES(P1 1·P2 6) → 반영 중 |
| [ ] | `A-03` | `assistant_sqlite`·`secrets_local` adapter | A-02 | `IN_PROGRESS` | 구현자 `impl-ai-a03`, 워크트리 `wt-ai-a03`, 브랜치 `feat/ai-a-03-storage-adapters` |
| [ ] | `A-04` | `/api/v1/assistant/*` + 턴 시작·SSE·취소, bootstrap, OpenAPI·SDK | A-03 | `WAITING` | — |
| [ ] | `A-05` | `llm_anthropic` adapter | A-04 | `WAITING` | — |
| [ ] | `A-06` | `llm_openai` adapter | A-05 | `WAITING` | — |
| [ ] | `A-07` | 프롬프트 최종본, 컨텍스트 품질, 시나리오 fixture 3개 | A-06 | `WAITING` | — |

Phase exit:

- [ ] 가짜 공급자 SSE 시나리오 3개 green(재개 포함), live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 blocking 0.

## B — frontend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `B-01` | `/settings` 페이지, `configure-ai-providers` 섹션, `entities/assistant` 프로파일 query | A-07 | `WAITING` | — |
| [ ] | `B-02` | 세션·턴 query, 생성 SDK SSE 리더(재개·멱등), 이벤트 리듀서, property test | B-01 | `WAITING` | — |
| [ ] | `B-03` | `assist-strategy` 사이드바 feature(렌더 안전·취소 확인) | B-02 | `WAITING` | — |
| [ ] | `B-04` | IDE `assistant` 슬롯, 제안 적용(`replaceRange` + stale 가드)·미리보기·적용 후 백테스트 | B-03 | `WAITING` | — |
| [ ] | `B-05` | e2e(MSW 공급자, 재개·취소), 매뉴얼·README·SoT·features README | B-04 | `WAITING` | — |

Phase exit:

- [ ] 완료 정의 1~5 기록.
- [ ] SoT·책임분리 점검 blocking 0.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|
| P0-01 | `review_ai_p0_01` | 1 | REQUEST_CHANGES | P1 3(적용 경로·턴 owner·PR 분할), P2 10, P3 8 → 전부 반영 |
| P0-01 | `review_ai_p0_01` | 2 | REQUEST_CHANGES | 1차 전부 해소 확인. 신규 P1 2(EventSource 재개 회귀·stale 가드 진행 경로), P2 3, P3 5 → 반영 |
| P0-01 | `review_ai_p0_01` | 3 | REQUEST_CHANGES | 2차 전부 해소 확인. 신규 P1 1(토큰 예산 축소가 포트로 구현 불가), P2 2, P3 4 → 반영 |
| P0-01 | `review_ai_p0_01` | 4 | APPROVE | 3차 전부 해소. 잔여 P2 1(`max_tool_rounds` 집행 주체) 반영, P3 3(D9 분리 반영, OpenAI 통지 문구·기본값은 A-06·A-07) |
| A-01 | `review_ai_a_01` | 1 | REQUEST_CHANGES | P1 1(`ProbeResult.message` 스크럽 계약 없음), P2 4(활성 승계·create 쓰기 순서·base_url 정수/16진/`localhost.` 우회·`_models` 테스트/`DocumentRef` 불변식), P3 5 → 반영 |
| A-02 | `review_ai_a_02` | 1 | REQUEST_CHANGES | P1 1(취소 직후 두 번째 턴 시작), P2 6(`stream.close` 예외로 `_finish` 누락, 크래시 경로 Failure 없음, 취소 시 이벤트 유실, `logger.exception` 전문, `_DISCRIMINATOR` 손글씨, 타임아웃 유예) → 반영 |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| P0-01 | `update-plan-progress.ps1 -Check` | 통과 | 2026-09-20 |

## 변경 기록

- 2026-09-20 — A-01·A-02 1차 리뷰 REQUEST_CHANGES(각 P1 1건) → 같은 구현자가 반영 후 재검토.
- 2026-09-20 — A-01(#169)·A-02(#170) 스택 PR 생성, 리뷰 배정. A-03 착수.
- 2026-09-20 — P0-01 4차 APPROVE. 잔여 P2(`max_tool_rounds` 집행을 adapter로)·D9 값/집행 분리 반영. CI 통과 후 머지 대상.
- 2026-09-20 — P0-01 3차 리뷰 반영: 토큰 예산 집행을 adapter로(서비스는 Usage 기록만), Failure 우선순위(첫
  Failure만 턴 상태), 409는 backstop·프론트는 이력 복구·`sseMaxRetryAttempts`, OpenAI 검색 상한은 도구 목록 제거,
  기본값은 A-07 실측 후 확정, A-05 체크 항목화.
- 2026-09-20 — P0-01 2차 리뷰 반영: SSE 리더는 생성 SDK 클라이언트(`Last-Event-ID`), RUNNING 턴 없으면
  409·keepalive, 적용 전 확인(미리보기·덮어쓰기), 턴 토큰 예산·`OUTPUT_TRUNCATED`, `accepted_sequence` 정의.
- 2026-09-20 — P0-01 1차 리뷰 반영: spec D2·D3·D4·D5·D6·D7·D9 개정, WORKFLOW 13 PR로 분할(A 7, B 5),
  PLAN·README의 lang2 링크 제거(정본은 yaml-ui WORKFLOW 13.7절), SoT `paths:`·행 문구, 경계 규칙 목록.
- 2026-09-20 — P0-01 PR #166 생성, 리뷰 배정. A-01 구현 착수(P1·P2 스택과 독립이라 병렬).
- 2026-09-20 — 패키지 생성. 제품 소유자 요청(설정에서 Claude·Codex 연결, 우측 사이드바 AI 채팅,
  검색 기반 전략 제안, 책임 분리)을 spec과 Phase 0·A·B로 정리.

## 갱신 절차

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다.
집계는 `powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/ai-assistant/tools/update-plan-progress.ps1`
(`-Check`는 검증만).
