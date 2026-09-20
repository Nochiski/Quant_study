---
plan_version: 2
project: ai-assistant
project_status: SELF_CHECK
current_phase: P0,A,B
current_pr: P0-01,A-01,A-02,A-03,A-04,A-05,A-06,A-07,B-01,B-02,B-03,B-04
active_prs: [P0-01, A-01, A-02, A-03, A-04, A-05, A-06, A-07, B-01, B-02, B-03, B-04]
parallel_window: [P0-01, A-01, A-02, A-03, A-04, A-05, A-06, A-07, B-01, B-02, B-03, B-04]
last_updated: 2026-09-21T03:40:23+09:00
planned_prs: 13
merged_prs: 0
approved_prs: 7
progress_percent: 0
---

# AI 어시스턴트 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `SELF_CHECK` |
| Current phase | `P0,A,B` |
| Current/next PR | `P0-01,A-01,A-02,A-03,A-04,A-05,A-06,A-07,B-01,B-02,B-03,B-04` |
| Active PR | `P0-01, A-01, A-02, A-03, A-04, A-05, A-06, A-07, B-01, B-02, B-03, B-04` |
| Progress | `0 / 13 merged (0%)` |
| Approved | `7 / 13` |
| Aggregated at | `2026-09-21 03:40 KST` |
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
| A | Backend: ports, storage, HTTP, providers | 7 | 0 | `SELF_CHECK` |
| B | Frontend: settings, entity, sidebar, e2e | 5 | 0 | `IN_REVIEW` |
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
| [ ] | `A-01` | `domain/assistant` 타입·도구 계약, `application/assistant_chat` 포트 5종·프로파일 서비스, SDK import 게이트, 경계 규칙 목록 | P0-01 | `APPROVED` | [#169](https://github.com/Nochiski/Quant_study/pull/169) · `e8c6895` · `review_ai_a_01` 3차 APPROVE(비차단 P3 2건은 A-02 브랜치에 적재) · CI 대기 |
| [ ] | `A-02` | 채팅 유스케이스·컨텍스트 빌더·프롬프트·턴 러너, 가짜 공급자 테스트 | A-01 | `APPROVED` | [#170](https://github.com/Nochiski/Quant_study/pull/170) · `8324ebc`(84570df + 후속 2: A-01 P3·A-02 P3·CI flake 수정) · `review_ai_a_02` 2차 APPROVE + 후속 확인 APPROVE · CI 대기 |
| [ ] | `A-03` | `assistant_sqlite`·`secrets_local` adapter | A-02 | `APPROVED` | [#171](https://github.com/Nochiski/Quant_study/pull/171) · `d52436b7`(A-02 `8324ebc` 위 rebase, 내용 동일) · `review_ai_a_03` 2차 APPROVE · POSIX 모드 비트는 Linux CI로 확인 |
| [ ] | `A-04` | `/api/v1/assistant/*` + 턴 시작·SSE·취소, bootstrap, OpenAPI·SDK | A-03 | `APPROVED` | [#174](https://github.com/Nochiski/Quant_study/pull/174) · `9f39faec`(A-03 `d52436b7` 위, 2차 P3 4건 반영) · `review_ai_a_04` 2차 APPROVE · CI 대기 |
| [ ] | `A-05` | `llm_anthropic` adapter | A-04 | `APPROVED` | [#175](https://github.com/Nochiski/Quant_study/pull/175) · `c0219894`(A-04 최종 `9f39faec` 위 15커밋) · `review_ai_a_05` 3차 APPROVE(세 라운드 24건 전부 닫힘, 비인증 헤더 통과는 docstring 한 문장 P3 — A-06 rebase 뒤 A-05에 fast-forward) · live smoke 최우선: 선언되지 않은 서버 도구 결과 블록 history 수용 여부 |
| [ ] | `A-06` | `llm_openai` adapter | A-05 | `IN_REVIEW` | [#178](https://github.com/Nochiski/Quant_study/pull/178) · `6c3859e7`(A-05 `fad379e7` 위 12커밋, 리뷰 반영 + Usage 분리형 정규화; A-05 최종 `c0219894` 위 replay 예정) · `review_ai_a_06` 1차 REQUEST_CHANGES(P1 2: `OPENAI_CUSTOM_HEADERS`가 Authorization 덮어씀·예산 잔량 API 최소 미만 호출, P2 3: `store=true`·통지 경로 spec 불일치·도구 제거 호출 조합 미테스트, P3 7) 반영 → 2차 재검토 중 · 게이트: pytest 1895(extras)/1718(없음)·ruff·pyright 0 · 기본 모델 `gpt-6-astra` · Usage 분리형 정규화(OpenAI 원시 내역 뺄셈) rebase 때 반영 |
| [ ] | `A-07` | 프롬프트 최종본, 컨텍스트 품질, 시나리오 fixture 3개 | A-06 | `SELF_CHECK` | 구현 완료(로컬 `9250699f`, A-05 위 9커밋, `total_input_tokens` wire 필드·분리형 docstring: 골든 fixture·live smoke·기본값 근거·세션 Usage 집계(`aggregate_usage` 순수 함수, `SessionHistoryView.usage`)·시나리오 fixture 3개(실제 HTTP 응답에서 받아 적음), pytest 1821·ruff·pyright 0) → A-06 tip 위 rebase·캐시 필드 반영 뒤 push·PR · live smoke 미실행(키 없음, 사용자 실행 필요) |

Phase exit:

- [ ] 가짜 공급자 SSE 시나리오 3개 green(재개 포함), live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 blocking 0.

## B — frontend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `B-01` | `/settings` 페이지, `configure-ai-providers` 섹션, `entities/assistant` 프로파일 query | A-07 | `IN_REVIEW` | 구현 완료(로컬 `9f536bb4`, A-04 `645efcb1` 위, 18 파일 +1542/−10, 게이트·e2e 19/19) · `review_ai_b_01` 1차 REQUEST_CHANGES(P1 1·P2 2) 반영 완료(로컬 `7fcc584e`: 키 실은 요청은 plain async, 접힘 시 base_url 미전송, 삭제 확인 포커스·live region, P3 4건, R2-1 `probingIds` Set·R2-2 제목 위계) · 2차 APPROVE_WITH_COMMENTS · R2-3·R2-4는 B-05 · cascade 뒤 A-07 위 rebase·push·PR |
| [ ] | `B-02` | 세션·턴 query, 생성 SDK SSE 리더(재개·멱등), 이벤트 리듀서, property test | B-01 | `APPROVED` | 구현 완료(로컬 `cb852032`, B-01 최종 `7fcc584e` 위 12커밋) · `review_ai_b_02` 3차 APPROVE(StrictMode probe 포함, e2e 19/19) · 어휘 단일 입구는 entity · cascade 뒤 push·PR |
| [ ] | `B-03` | `assist-strategy` 사이드바 feature(렌더 안전·취소 확인) | B-02 | `IN_REVIEW` | 구현 완료(로컬 `df7b25f4`, B-02 최종 `cb852032` 위 7커밋, vitest 724, 훅 API 적응: exhausted 재연결·rejected 정착·droppedFrames 경고, vitest 714·e2e 19/19) · `review_ai_b_03` 1차 REQUEST_CHANGES(P1 1: 이력보다 202가 먼저 오면 turns 순서 축이 갈라져 질문↔답 짝 밀림 — 리듀서 정렬을 B-03에서 수정, P2 4, P3 6) 반영(턴 배열 서버 생성 순서 불변, 409 질문 복원, 출처 호스트, aria-live 범위, 초점 복귀) → 2차 재검토 중 · 후속 backlog: `ChatMessageView.turn_id` |
| [ ] | `B-04` | IDE `assistant` 슬롯, 제안 적용(`replaceRange` + stale 가드)·미리보기·적용 후 백테스트 | B-03 | `IN_REVIEW` | 구현 완료(로컬 `e2a49935`, B-03 `64c5d6f2` 위 7커밋: 슬롯·적용 훅·페이지 배선·적용 후 백테스트·오버레이·spec 정정·사이드바 실장착 + App 통합 테스트 2건, vitest 753·e2e 19/19·기준선 4장 재생성) · `review_ai_b_04` 로컬 ref 검토 중(e2e 제외) · B-03 `df7b25f4` 위 replay 예정 · landmark 중복은 B-03 후속(section) |
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
| A-01 | `review_ai_a_01` | 2 | REQUEST_CHANGES | 1차 10건 전부 해소. 새 P2 1(insecure 플래그가 기본 정책의 상위집합이 아님), 비차단 2(거절 문구, 승계 테스트) → 반영 |
| A-03 | `review_ai_a_03` | 1 | REQUEST_CHANGES | P1 2(`ChatEvent` 확장 가드 부재, 비밀 파일 경로가 예외 메시지에), 권고 1(`update_turn`이 `started_at`·`accepted_sequence` 덮음), P2 1(부모 디렉터리 0700), P3 6 → 반영 |
| A-01 | `review_ai_a_01` | 3 | APPROVE | 2차 3건 전부 해소(39 URL 매트릭스 실측). 새 P3 2(스킴 누락 거절 문구, `FailureCode`·`ChatEvent` 집합 대조 테스트) → A-02 브랜치에 적재. 잔여 위험: `Failure.message` 자유 문자열(adapter PR에서 `str(exc)` 유입 게이트) |
| A-02 | `review_ai_a_02` | 2 | APPROVE | 1차 P1 1·P2 6 전부 해소(원인 수정 + 회귀 테스트). `equity_workspace` 화살표는 포트 소비 확인. 새 P3 7(도구 종료 경로 이벤트 보존 등) → 5 반영, 2 사양(판별자 첫 매치, `_finish` 창 `state()`는 A-04 확인) |
| A-03 | `review_ai_a_03` | 2 | APPROVE | 1차 P1 2·권고·P2 전부 해소(`assert_never` 양방향 실증). 새 P3 2(`__cause__` 경로, 종료 상태 역전 미차단) → 후속 커밋 |
| A-02 | `review_ai_a_02` | 3 | APPROVE | 후속 커밋 2개 확인. `Done` 뒤 `Failure` 가능 계약은 spec 문장 추가(A-04)·B-03 리듀서 확인 항목 |
| A-04 | `review_ai_a_04` | 1 | APPROVE WITH CHANGES | P1 1(`assistant.document_ref_invalid`가 OpenAPI·SDK에 없음), P2 3(이력 읽기 순서, SSE payload `unknown`, 레지스트리 즉시 호출·미설치 표현), P3 7 → 반영. `_finish` 순서 변경은 A-02 불변 4개 유지 확인 |
| A-05 | `review_ai_a_05` | 1 | REQUEST_CHANGES | P0 1(`output_format=None`이 SDK 센티널 아님 → live에서 모든 텍스트 블록 `ValidationError`, 대본이 `_client.py`를 안 지나 미검출), P1 1(검색 상한 호출당 집행), P2 3(`llm` extra 없이 수집 깨짐, 로그 키 테스트 부재, 미사용 `DEPENDS_ON`), P3 6 → 반영. SDK 표면은 설치본 1.7.0과 전부 일치 |
| B-01 | `review_ai_b_01` | 1 | REQUEST_CHANGES | P1 1(제출한 API 키가 react-query mutation cache `variables.secret`에 잔류 → 키 실은 요청은 mutation 미사용), P2 2(고급 접힘 시 base_url 전송, 삭제 확인 포커스·announce). FSD·i18n·서버 문구 미노출·스크린샷 OK |
| A-04 | `review_ai_a_04` | 2 | APPROVE | 1차 11건 중 10 해소·1 근거 보류. `PROVIDER_SDK_MODULES` 표·라이브 SSE 테스트가 권고보다 나음. 새 P3 4(하위 모듈 오분류, pyright ignore, 실패 시 스레드 정리, 공개 setter) → 후속 커밋 |
| B-02 | `review_ai_b_02` | 1 | REQUEST_CHANGES | P1 2(재시도 상한 소진 뒤 진행 중 턴 스트림 영구 중단 — `streamKey`·`status`·`retry()`로 재연결 owner를 화면으로; 마지막 시도 본문 끊김이 `ended`로 분류 — `onSseError`로 계수), P2 2(이력 병합이 watermark 아래 앞선 턴 이벤트 폐기 → 적용 sequence 집합; `frontend-api-state.md` 갱신), P3 3 |
| B-01 | `review_ai_b_01` | 2 | APPROVE_WITH_COMMENTS | P1·P2·P3 전부 닫힘(캐시 단언 실효성 되돌리기 실측). 새 P2 1(`probingId` 단일 슬롯 — 동시 probe에서 버튼 조기 해제, 주석 오기), P3 1(font-size 토큰화로 h2>h1 위계) → 후속. R2-3 삭제 후 포커스·R2-4 배지 연결은 B-05 |
| A-05 | `review_ai_a_05` | 2 | APPROVE WITH CHANGES | 1차 12건 전부 해소(P0 red 확인, extras 없는 환경 재현, env 폴백은 `ANTHROPIC_AUTH_TOKEN`까지 차단). 블로킹 1(A-04 하위 모듈 fix 되돌림)은 base `439f9411` 불일치 산물 → replay 보존. P2 2(예산 소진 호출의 history 서버 도구 블록 미테스트 → live smoke 최우선, 캐시 접두 파기 비용 미기재), P3 4 |
| B-02 | `review_ai_b_02` | 2 | APPROVE_WITH_NITS | 1차 P1 2·P2 2 해소(재현 probe 재실행). 이탈 2건(5값 status, 내부 attempt+retry) 타당. 새 P2 1(`streamKey` 입력 파생 → 세션 이탈·복귀 시 옛 close 사유가 status로), P3 6 → 후속 |
| A-06 | `review_ai_a_06` | 1 | REQUEST_CHANGES | P1 2(`OPENAI_CUSTOM_HEADERS`→`default_headers`가 Authorization 우선 — A-05도 `ANTHROPIC_CUSTOM_HEADERS` 동일; 턴 예산 잔량<16 호출 400→PROVIDER), P2 3(`store` 기본 true, 통지 경로 spec 문장, 도구 제거 호출에 이전 `web_search_call` 동승 미테스트), P3 7. A-05 결함 부류 15 중 9 부재 확인 |
| B-02 | `review_ai_b_02` | 3 | APPROVE | 2차 잔여 7건 전부 닫힘(단조 `run`·렌더 중 조정, StrictMode 이중 호출 probe 통과, 값 형태 setState 멱등). 메모: `queryClient` 인스턴스 교체는 실제 경로 없음 |
| B-03 | `review_ai_b_03` | 1 | REQUEST_CHANGES | P1 1(`reduceHistory`가 messages는 교체·turns는 append → `asked[index]` 짝 밀림; 리듀서 턴 배열을 accepted_sequence 순 유지), P2 4(409 질문 유실, 출처 호스트 미표시, aria-live 델타 재낭독, 대화상자 초점 복귀), P3 6. 렌더 안전·비밀·FSD·i18n OK |
| A-05 | `review_ai_a_05` | 3 | APPROVE | 5항목 전부 통과(A-04 보존, 양쪽 환경 green, P2·P3 반영, Usage 분리형 계약+파생, `ANTHROPIC_CUSTOM_HEADERS` 독립 재현·차단 확인 — 위험은 키 유출이 아니라 요청이 남의 계정으로 나가는 것). P3 1(비인증 헤더 통과를 docstring에 명시) |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| P0-01 | `update-plan-progress.ps1 -Check` | 통과 | 2026-09-20 |

## 변경 기록

- 2026-09-21 — 보안 결함(A-06 발견, A-05·A-06 수정): 프로파일에 base_url이 없으면 SDK가 `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` 환경 변수를 읽어 spec D6 검사를 지나지 않은 호스트로 키가 나감 → 기본 base_url·api_key를 항상 명시해 SDK 환경 변수 폴백 차단(spec D6 한 줄). `Usage` 캐시 필드는 SDK 값 그대로 매핑.
- 2026-09-21 — A-06(#178) PR 생성·리뷰 배정. `Usage` 캐시 의미 결정(번복 후 확정): domain은 **분리형**(`input_tokens` = 캐시 읽기·쓰기 제외, 세 칸 겹치지 않음) 유지, 총입력은 파생 `total_input_tokens`(A-05), OpenAI adapter가 원시 내역을 빼서 정규화(A-06), A-07 집계는 성분 합산 + wire `total_input_tokens`. 근거: 성분별 단가·저장 이력 의미 보존(A-05 리뷰어).
- 2026-09-21 — B-03 구현 완료. A 후속 backlog: `ChatMessageView.turn_id` 추가(질문↔턴 짝짓기가 순서 가정에 의존).
- 2026-09-21 — A-07: 실행 설정 문장은 schema 1.1 기준(lang2 P2-03 머지 뒤 프롬프트·골든 갱신 후속), env `STRATEGY_WORKBENCH_LIVE_SMOKE`로 통일, 기본값 5종 유지(근거 표), 타임아웃 300s 여유 얇음(live smoke 후 bootstrap 주입으로 조정).
- 2026-09-21 — A-02 후속(CI flake 수정)이 스택 위에 없어 A-04·A-05 CI 실패 → A-03부터 cascade rebase(A-03 `d52436b7`, 이어 A-04·A-05·A-06·A-07·B-01).
- 2026-09-21 — A-04(#174) PR 생성·리뷰 배정, A-05 rebase·팩토리 등록 지시, B-01 착수(A-04 위, frontend만).
- 2026-09-21 — A-01(3차)·A-02(2차)·A-03(2차) APPROVE. A-04 구현 완료·rebase 중, A-05 착수.
- 2026-09-20 — A-03(#171) PR 생성·리뷰 배정, A-04 착수(A-02 rewrite 뒤 A-03·A-04 rebase 예정).
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
