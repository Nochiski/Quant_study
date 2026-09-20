# AI 어시스턴트 구현 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans`로 PR 단위로 실행한다. PR마다 [YAML Strategy Workbench WORKFLOW
> 13절](../strategy-workbench-yaml-ui/WORKFLOW.md) 절차를 지킨다. 진행 상태는 [PLAN.md](./PLAN.md)만
> 갱신한다.

**Goal:** 설정에서 Claude·Codex를 연결하고, 전략 화면 우측 사이드바에서 LLM이 현재 전략·카탈로그·
인터넷 검색으로 시장을 조사해 전략을 제안하며, 사용자가 제안을 문서에 적용해 백테스트한다.

**Architecture:** `domain/assistant`(타입·도구 계약) ← `application/assistant_chat`(유스케이스·포트·
도구 실행·제안 검증) ← `adapters/outbound/{llm_anthropic, llm_openai, assistant_sqlite, secrets_local}`,
`adapters/inbound/http_api`(`/api/v1/assistant/*`, SSE). frontend는 `entities/assistant`,
`features/configure-ai-providers`, `features/chat-assistant`, `pages/settings`, `widgets/strategy-ide`
슬롯. 계약은 [설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)이 소유한다.

**Tech Stack:** Python 3.11 · FastAPI(SSE `StreamingResponse`) · `anthropic` 공식 SDK · `openai` 공식
SDK · SQLite · React 19 · TanStack Query · `fetch` + `ReadableStream` SSE 파서 · Vitest · MSW · Playwright

---

## 1. 실행 순서와 스택

```text
main
 └─ P0-01  docs/ai-assistant-plan
     └─ A-01 ─ A-02 ─ A-03 ─ A-04 ─ A-05      backend
         └─ B-01 ─ B-02 ─ B-03 ─ B-04          frontend
```

- 브랜치 `feat/ai-<pr-id 소문자>-<slug>`.
- A PR은 `backend/openapi.json`만 재생성하고 frontend SDK는 B-02가 한 PR에서 갱신한다(기존 규칙).
- 실제 공급자 호출은 `RUN_LLM_LIVE=1` smoke 테스트에서만. CI는 가짜 공급자.

## 2. 공통 gate

`docs/planning/strategy-language-2-0/WORKFLOW.md` 2절과 같다. 추가: 모든 PR에서
`backend/tests/architecture`의 SDK import 게이트와 비밀 평문 검사 테스트가 green.

---

## 3. Phase 0

### P0-01 — 기획 패키지·spec

**Acceptance**: 이 패키지 3개 문서와 spec, `tools/update-plan-progress.ps1 -Check` 통과. SoT 규칙에
"AI 어시스턴트" owner 행 예약(프로파일·비밀·세션·도구 정의·프롬프트).

---

## 4. Phase A — backend

### A-01 — 도메인·포트·유스케이스(가짜 공급자)

**Intent**: 공급자 없이도 도구 루프와 제안 검증이 도는 application 골격.

**Acceptance**

- `domain/assistant`: `ProviderKind`, `ProviderProfile`, `ToolSpec`, `ResearchCapability`,
  `TurnRequest`, `ChatMessage`, `ChatEvent` union(`TextDelta`·`ThinkingSummary`·`ToolCall`·
  `ToolResultSummary`·`SearchActivity`·`Proposal`·`Usage`·`Done`·`Failure`), `StrategyProposal`,
  `ProbeResult`. 도구 이름·JSON Schema 상수 5종(spec D3). facade `DEPENDS_ON = ()`.
- `application/assistant_chat`: `ports/outgoing/{llm_provider, provider_profiles, provider_secrets,
  chat_sessions}.py`(Protocol), `ProviderProfileService`(probe 필수·활성 전환·삭제 시 비밀 삭제),
  `AssistantContextBuilder`(runtime schema 요약은 `domain/strategy` 스키마 빌더에서 생성, 필드
  카탈로그는 `EquityDataPort`, 팩터 카탈로그는 domain registry), `AssistantChatService.send`
  (도구 루프: adapter가 부른 `execute_tool` 콜백을 application이 처리, `propose_strategy`는
  hydrate·validate 후 `Proposal` 이벤트, 실패 시 도구 오류 반환 최대 3회, `max_tool_rounds`,
  취소, `Failure` 매핑). `_prompt.py`가 시스템 프롬프트 소유.
- 테스트: 가짜 `LlmProviderPort`(스크립트된 이벤트·도구 호출)로 루프·검증·재시도·취소·상한.
  in-memory repository 3종. `facade/__init__.py` `DEPENDS_ON`과 architecture 테스트 통과.
- architecture 테스트 확장: `anthropic`·`openai` import는 `adapters/outbound/llm_anthropic`,
  `llm_openai`에서만 허용.

**Non-goal**: HTTP, 실제 SDK, 저장.

### A-02 — 저장 adapter와 HTTP API(프로파일·세션·SSE)

**Acceptance**

- `adapters/outbound/assistant_sqlite`: 프로파일·세션·메시지·이벤트 테이블, 별도 DB 파일
  (`STRATEGY_WORKBENCH_ASSISTANT_DB_PATH`, 기본 `.local/assistant.sqlite3`), 테스트는 in-memory.
- `adapters/outbound/secrets_local`: 사용자 설정 디렉터리 `secrets.json`(0600, Windows는 사용자
  ACL), env로 경로 변경, 파일에 profile_id → secret. 저장소 경로 거부.
- HTTP: spec D6의 라우트 전부. SSE는 backtest 이벤트 스트림과 같은 프레이밍. 비밀은 요청 전용
  (`writeOnly`), 응답은 꼬리 4자리. 422 코드 4종. OpenAPI 재생성.
- 테스트: TestClient로 프로파일 CRUD·probe 실패 422·활성 전환·SSE 이벤트 순서·취소; 응답·로그·
  DB 덤프에 비밀 문자열이 없다는 테스트; OpenAPI에 secret이 `writeOnly`.
- bootstrap: `build_container`에 assistant 서비스 추가, 가용 adapter 등록(설치된 SDK만).

### A-03 — Anthropic adapter

**Acceptance**

- `adapters/outbound/llm_anthropic`: `anthropic` 공식 SDK, `claude-opus-5` 기본, adaptive thinking
  (`display: summarized` → `ThinkingSummary` 이벤트), `output_config.effort: high`, `web_search_20260209`
  서버 도구(`research`에 WEB_SEARCH가 있을 때), `ToolSpec` → `tools[]`(`strict: true`), 수동 도구
  루프(`stop_reason == "tool_use"` → `execute_tool` → `tool_result`; `pause_turn` 재개; `refusal`
  → `Failure`), `client.messages.stream`으로 `TextDelta`, 검색 결과 블록 → `SearchActivity`, usage.
  프롬프트 캐싱(`cache_control`을 시스템·카탈로그 블록에). `probe`는 `max_tokens` 작은 요청 1회,
  예외 종류별 `ProbeResult`.
- optional extra `llm`에 `anthropic>=1.0`. 미설치 시 bootstrap이 등록하지 않는다.
- 테스트: SDK 응답 객체를 흉내 낸 단위 테스트(스트림 이벤트 → ChatEvent 매핑, 도구 루프, 오류
  매핑). `RUN_LLM_LIVE=1` smoke 1건.

### A-04 — OpenAI(Codex) adapter

**Acceptance**

- `adapters/outbound/llm_openai`: `openai` 공식 SDK Responses API, 기본 모델은 구현 시 SDK 문서로
  확정(PLAN 변경 기록에 근거), `web_search` 도구, function tools 변환, 스트리밍 이벤트 → ChatEvent,
  `probe`. extra `llm`에 `openai>=2.0`.
- 테스트: A-03과 같은 수준.

### A-05 — 컨텍스트 품질·프롬프트·평가 fixture

**Acceptance**

- 시스템 프롬프트 최종본: 역할, 한국어, 도구 사용 순서(카탈로그 → 검색 → 검증 → 제안), 지원
  안 되는 연산자·필드 금지, 출처 인용. 전략 언어 요약은 runtime schema에서 생성(손으로 적은
  필드명 0개 테스트).
- 세션 이력을 `Usage`로 집계. 취소 시 부분 텍스트 보존.
- 가짜 공급자 시나리오 fixture 3개(제안 성공·검증 실패 후 수정·검색 후 제안)가 e2e(B-04)의 MSW
  응답이 된다.

**Phase A exit**

- [ ] 가짜 공급자로 HTTP SSE 시나리오 3개 green. live smoke 2건(Anthropic·OpenAI) 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

---

## 5. Phase B — frontend

### B-01 — 설정 페이지와 AI 연결 섹션

**Acceptance**

- `/settings` 라우트·페이지, app-shell 내비 하단 "설정".
- `features/configure-ai-providers`: 공급자 카드 목록(Claude·Codex 아이콘 텍스트, 라벨, 모델, 꼬리
  4자리, 활성 배지, 설치 안 됨 상태), 활성 전환, 삭제 확인 다이얼로그, 추가 폼(공급자 → 라벨 → 키
  → 모델 기본값 채움), "연결 테스트" 인라인 결과·지연 시간, 서버 거부 사유 표시. 키 입력
  `autocomplete="off"`, 상태에 키를 남기지 않음.
- `entities/assistant` 프로파일 query·mutation. SDK는 B-02에서 재생성하므로 이 PR은 A-02 OpenAPI로
  먼저 `npm run api:generate`(B-01이 SDK를 갱신한다).
- MSW 테스트: 목록·생성(probe 실패 422)·활성·삭제·설치 안 됨.

### B-02 — 어시스턴트 entity: 세션·SSE 리더·이벤트 리듀서

**Acceptance**

- `fetch` + `ReadableStream` SSE 파서(`id`·`event`·`data`, 재연결 없음·취소 지원), `ChatEvent` →
  화면 상태 리듀서(스트리밍 텍스트 병합, 검색 활동, 제안, 실패). property test(임의 청크 분할에
  대해 같은 이벤트 열).
- 세션 생성·목록·이력 query. 문서 참조 키는 `strategy_id`/`revision` 또는 초안 id.

### B-03 — 사이드바 채팅과 제안 적용

**Acceptance**

- `features/chat-assistant`: 메시지 목록, 입력, 스트리밍, 검색 활동 칩(출처 링크 새 창), 도구 활동
  접힘, 제안 카드(제목·한 문장·근거·출처, "미리보기"(diff 투영), "문서에 적용", "적용 후
  백테스트"), 취소, 세션 전환, 활성 공급자 없음 → 설정 링크.
- `widgets/strategy-ide`: 우측 레일 탭 "계약 · AI", `assistant` 슬롯, 폭·펼침 `use-panel-layout`
  확장. 1280px 미만은 기존 drawer 규칙.
- `pages/research-strategy-*`: `onApplyProposal` → 전체 범위 교체(`setText` 한 번, undo 한 단계) →
  compile. "적용 후 백테스트"는 적용 후 기존 실행 흐름. 컨텍스트는 보낼 때 현재 텍스트·진단·실행
  설정을 실어 보낸다.
- 키보드·ARIA(`frontend-ui-quality.md`), i18n 전부 `messages.ts`.

### B-04 — e2e·문서·규칙

**Acceptance**

- e2e(MSW 공급자 고정): 설정 등록 → 전략 화면 사이드바 질문 → 검색 활동 → 제안 카드 → 적용 →
  "검증 통과" → 백테스트 페이지. 취소 시나리오.
- 매뉴얼 절 "AI 어시스턴트 연결과 사용", README, SoT 규칙 행 채움, `frontend-fsd.md`에 slice 추가.

**Phase B exit**

- [ ] 완료 정의 1~5 전부 PLAN에 기록.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.
