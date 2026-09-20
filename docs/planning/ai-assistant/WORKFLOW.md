# AI 어시스턴트 구현 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans`로 PR 단위로 실행한다. PR마다 [YAML Strategy Workbench WORKFLOW
> 13절](../strategy-workbench-yaml-ui/WORKFLOW.md) 절차를 지킨다. 진행 상태는 [PLAN.md](./PLAN.md)만
> 갱신한다.

**Goal:** 설정에서 Claude·Codex를 연결하고, 전략 화면 우측 사이드바에서 LLM이 현재 전략·카탈로그·
인터넷 검색으로 시장을 조사해 전략을 제안하며, 사용자가 제안을 문서에 적용해 백테스트한다.

**Architecture:** `domain/assistant`(타입·도구 계약) ← `application/assistant_chat`(유스케이스·턴 러너·
포트·도구 실행·제안 검증) ← `adapters/outbound/{llm_anthropic, llm_openai, assistant_sqlite,
secrets_local}`, `adapters/inbound/http_api`(`/api/v1/assistant/*`, SSE). frontend는 `entities/assistant`,
`features/configure-ai-providers`, `features/assist-strategy`, `pages/settings`, `widgets/strategy-ide`
슬롯. 계약은 [설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)이 소유한다.

**Tech Stack:** Python 3.11 · FastAPI(SSE `StreamingResponse`) · `anthropic` 공식 SDK · `openai` 공식
SDK · SQLite · React 19 · TanStack Query · 생성 SDK SSE 클라이언트 · Vitest · MSW · Playwright

---

## 1. 실행 순서와 스택

```text
main
 └─ P0-01  docs/ai-assistant-plan
     └─ A-01 ─ A-02 ─ A-03 ─ A-04 ─ A-05 ─ A-06 ─ A-07      backend
         └─ B-01 ─ B-02 ─ B-03 ─ B-04 ─ B-05                  frontend
```

- 브랜치 `feat/ai-<pr-id 소문자>-<slug>`.
- OpenAPI와 frontend generated SDK는 **A-04가 같은 PR에서** 갱신한다(12절 "API 변경은 같은 PR에서
  OpenAPI와 generated SDK 갱신"). 생성 타입 추가는 기존 frontend 코드를 깨지 않으므로 frontend CI가
  A-04부터 green이어야 한다.
- 실제 공급자 호출은 `RUN_LLM_LIVE=1` smoke 테스트에서만. CI는 가짜 공급자.
- `strategy-language-2-0`(schema 1.2)이 먼저 머지되면 A-07 fixture와 B-05 e2e를 1.2 문서로 갱신한다.

## 2. 공통 gate

`docs/planning/strategy-workbench-yaml-ui/WORKFLOW.md` 13.2절과 같다. 추가: 모든 PR에서
`backend/tests/architecture`의 SDK import 게이트와 비밀 평문 검사 테스트가 green. PR 크기는 12절
(150~450줄, logical file 8개, 600줄 초과 시 분할)을 따르고 초과 시 PR 본문 `제약사항`에 이유를 적는다.

---

## 3. Phase 0

### P0-01 — 기획 패키지·spec

**Acceptance**: 이 패키지 3개 문서와 spec, `tools/update-plan-progress.ps1 -Check` 통과. SoT 규칙에
"AI 어시스턴트" owner 행 예약과 `paths:` 등록.

**제약사항(12절 예외 기록)**: `tools/update-plan-progress.ps1`은 `strategy-workbench-yaml-ui`판의 복사본이다
(`$prIdPattern`·`$phaseGoals` 두 곳만 다름). 공유 스크립트화는 별도 정리 PR 후보로 남긴다.

---

## 4. Phase A — backend

### A-01 — 도메인 타입·도구 계약·포트·프로파일 서비스

**Intent**: 공급자 없이도 성립하는 계약 층. 공급자 SDK는 이 PR에서 전혀 쓰지 않는다. 12절 권장치를
넘으면 PR 본문 `제약사항`에 사유를 적거나 프로파일 서비스를 A-01b로 뗀다.

**Acceptance**

- `domain/assistant`(facade `DEPENDS_ON = ()`): spec D2의 타입 전부(`ProviderKind`, `ProviderProfile`,
  `ProbeResult`·`ProbeFailure`, `ToolSpec`, `ResearchCapability`, `Source`, `ChatRole`·`ChatMessage`,
  `TurnRequest`, `ChatEvent` union 9종, `ToolResult`, `StrategyProposal`·`ProposalCompileResult`·
  `ProposalDiagnostic`, `TurnStatus`·`Turn`·`SequencedEvent`, `FailureCode`). 도구 이름·JSON Schema
  상수 5종(`additionalProperties: false` + `required`), `ASSISTANT_TOOLS`. facade `models.py`, `tools.py`.
- `application/assistant_chat`(facade `DEPENDS_ON = ("domain.assistant", "domain.strategy",
  "domain.factor", "application.equity_workspace")`): `ports/outgoing/{llm_provider, strategy_compiler,
  provider_profiles, provider_secrets, chat_sessions}.py`(Protocol + 예외), `_models.py`(`DocumentRef`,
  `ChatSession`, `TurnContext`), `_profiles.py` `ProviderProfileService`(probe 필수, base_url 규칙 spec D6,
  활성 전환, 삭제 시 비밀 삭제, `available_kinds`), facade `ports.py`·`profiles.py`.
- `.claude/rules/backend-package-boundary.md`의 application 간 화살표 목록에
  `assistant_chat → equity_workspace`(EquityDataPort 소비)를 추가한다.
- 테스트: 프로파일 서비스(probe 실패 시 저장 안 함, 성공 시 secret 저장·첫 프로파일 active, delete가
  secret 삭제, 미설치 kind 거부, base_url 규칙 6케이스), 도구 스키마 상수(strict 조건). architecture
  테스트: `anthropic`·`openai` import는 `adapters/outbound/llm_anthropic`, `llm_openai`에서만 허용.

**Non-goal**: 채팅 서비스, HTTP, 실제 SDK, 저장.

### A-02 — 채팅 유스케이스·컨텍스트 빌더·프롬프트·턴 러너

**Acceptance**

- `_context.py` `AssistantContextBuilder`(시스템 프롬프트 = `_prompt.py` 템플릿 + runtime schema에서
  생성한 언어 요약; 도구 4종 실행), `_prompt.py`, `_chat.py` `AssistantChatService.send`(도구 루프,
  `propose_strategy`는 `StrategyCompilerPort`로 검증 후 `Proposal` 이벤트, 3회 실패 →
  `Failure(PROPOSAL_INVALID)`, 취소, 공급자 예외 → `Failure(PROVIDER)`에 예외 타입
  이름만, `Usage` 기록), `_turns.py` `AssistantTurnRunner`(스레드, 즉시 append, `accepted_sequence`, 중복
  턴 거부, cancel, 타임아웃 → `Failure(TIMEOUT)`을 먼저 확정한 뒤 cancel 신호, Failure 우선순위: 첫 Failure만
  턴 상태), facade `chat.py`·`turns.py`. 라운드·검색·토큰 상한의 집행은 adapter(A-05·A-06) 책임이라 여기
  없다(서비스는 값만 `TurnRequest`에 싣는다).
- 테스트: 가짜 `LlmProviderPort`·in-memory 저장소·가짜 compiler로 spec D3의 종료 조건 전부, 턴 러너
  sequence·중복·취소·타임아웃(주입 clock), 시스템 프롬프트가 runtime schema의 최상위 키·노드 kind를
  포함하고 손으로 적은 필드명이 없음.

### A-03 — 저장 adapter: `assistant_sqlite`·`secrets_local`

**Acceptance**

- `adapters/outbound/assistant_sqlite`: 프로파일·세션·턴·메시지·이벤트(세션 단위 단조 sequence)
  테이블, 별도 DB 파일(`STRATEGY_WORKBENCH_ASSISTANT_DB_PATH`, 기본 `.local/assistant.sqlite3`),
  테스트는 in-memory.
- `adapters/outbound/secrets_local`: 사용자 설정 디렉터리 `secrets.json`, POSIX 0600 / Windows 사용자
  전용 ACL(`icacls`), env 경로 변경, 저장소 경로 거부. 권한 테스트 플랫폼 분기.
- DB 덤프·파일 목록에 비밀이 없다는 테스트.

### A-04 — HTTP API·SSE·bootstrap·OpenAPI·SDK

**Acceptance**

- spec D6의 라우트 전부. 턴 시작 202(`accepted_sequence`), 이벤트 GET SSE(backtest와 같은 프레이밍,
  `after_sequence` 또는 `Last-Event-ID` 재개, RUNNING 턴 없으면 409, 15초 keepalive, 턴 종료 시 닫힘),
  cancel. 비밀은 요청 전용(`writeOnly`), 응답은 꼬리 4자리. 422·409 코드.
- bootstrap: `build_container`에 assistant 서비스·턴 러너·`StrategyCompilerPort` 구현(authoring compile
  감싸기)·가용 adapter 등록(설치된 SDK만).
- OpenAPI 재생성 + `npm run api:generate` + frontend typecheck green.
- 테스트: TestClient로 프로파일 CRUD·probe 실패 422·base_url 422·활성 전환·턴 시작·SSE 순서·재개·
  중복 턴 409·취소; 응답·로그에 비밀 문자열이 없다는 테스트; OpenAPI에 secret이 `writeOnly`.

### A-05 — Anthropic adapter

**Acceptance**

- `adapters/outbound/llm_anthropic`: spec D4 행 전부. 체크 항목:
  - [ ] `ToolSpec` → `tools[]`(`strict: true`), `web_search_20260209`(`max_uses = request.max_search_uses`)
  - [ ] 수동 도구 루프(`tool_use` → `execute_tool` → `tool_result`, `pause_turn` 재개, `refusal` → `Failure(REFUSAL)`,
    라운드가 `request.max_tool_rounds`를 넘으면 `Failure(TOOL_ROUNDS_EXCEEDED)`)
  - [ ] 스트리밍 → `TextDelta`·`ThinkingSummary`(`display: summarized`)·`SearchActivity`·`Usage`
  - [ ] 서버 도구 오류 객체 분기(성공 리스트 vs 오류 객체) 단위 테스트
  - [ ] 토큰 예산: `max_tokens = min(request.max_output_tokens_per_call, 남은 턴 예산)`, 소진 시
    `Failure(TOKEN_BUDGET_EXCEEDED)`; `stop_reason == "max_tokens"` → `Failure(OUTPUT_TRUNCATED)`
  - [ ] 프롬프트 캐싱(안정 블록에만 `cache_control`, 휘발 값은 뒤, breakpoint ≤ 4)
  - [ ] 예외 → `FailureCode` 매핑, message는 예외 타입 이름만(키 문자열 미노출 테스트)
  - [ ] `probe`(실패 종류 구분)
- optional extra `llm`에 `anthropic>=1.0`. `RUN_LLM_LIVE=1` smoke 1건.

### A-06 — OpenAI(Codex) adapter

**Acceptance**: A-05와 같은 체크 항목(검색 횟수 상한은 spec D4 OpenAI 행의 규칙 — 누적 상한 도달 시 이후
호출의 도구 목록에서 `web_search` 제거, 잘림 → `OUTPUT_TRUNCATED`, 토큰 예산 집행은 adapter).
기본 모델은 SDK 문서로 확정해 PLAN 변경 기록에 근거. extra `llm`에 `openai>=2.0`.

### A-07 — 프롬프트 최종본·컨텍스트 품질·시나리오 fixture

**Acceptance**: 시스템 프롬프트 최종본(역할, 한국어, 도구 순서, 금지, 출처), 세션 `Usage` 집계, 가짜
공급자 시나리오 fixture 3개(제안 성공·검증 실패 후 수정·검색 후 제안)가 B-05 e2e의 MSW 응답이 된다.

**A-05가 넘긴 항목** (2차 리뷰, 각각 근거가 코드·spec에 있다):

- [ ] **live smoke 최우선** — 검색 예산이 소진돼 `web_search`를 뺀 호출에, 이전 호출의
  `server_tool_use`·`web_search_tool_result` 블록이 든 history를 공급자가 **받아 주는지**.
  A-05가 우리 쪽 동작은 고정했지만(`test_a_call_without_the_search_tool_still_carries_the_earlier_search_blocks`)
  공급자 계약은 문서화돼 있지 않다. 400이면 대안은 도구를 빼는 대신 `max_uses=1`로 남겨 1회
  초과를 허용하는 것이다. 400은 `Failure(PROVIDER)`로 흡수돼 화면에 사유가 안 보이므로
  로컬 로그의 `error_type`을 직접 봐야 한다.
- [ ] **캐시 비용 실측** — 검색이 한 번 일어나면 이후 호출은 시스템 프롬프트를 캐시 읽기가
  아니라 **쓰기**로 치른다(`_payload.py` 모듈 docstring). `Usage.cache_read_tokens`·
  `cache_write_tokens`로 실제 값을 재고, 검색 초과를 막는 이득과 견줘 "`max_uses` 고정 +
  소진 시에만 도구 제거"로 바꿀지 결정한다.
- [ ] **`UsageView`에 캐시 두 칸을 싣는다** — domain `Usage`와 sqlite codec은 4칸인데 wire는
  2칸이라 SSE·이력 응답에서 조용히 절삭된다. 세션 집계가 캐시를 뺀 입력 토큰으로 과소
  보고하지 않으려면 여기서 채워야 한다(OpenAPI·생성 SDK 갱신 동반).
- [ ] **상한 기본값 확정** — 라운드 12·호출당 16000·턴 64000·검색 8·`MIN_CALL_OUTPUT_TOKENS`
  256(spec D3이 A-07 실측에 맡긴 값).
- [ ] **SDK 표면 확인 4건** — thinking 블록 signature 왕복, probe `max_tokens=64`,
  `display: "summarized"`가 실제로 텍스트를 채우는지, 검색 결과의 `title`이 비는 경우.

**A-06이 넘긴 항목** (리뷰, 각각 근거가 코드·SDK 소스에 있다):

- [ ] **live smoke 최우선** — 검색 예산이 소진돼 `web_search`를 뺀 호출에, 이전 호출의
  `web_search_call` 항목이 든 `input`을 공급자가 **받아 주는지**. A-06이 우리 쪽 동작은
  고정했지만(`test_the_call_without_the_search_tool_still_carries_the_earlier_search_items`)
  공급자 계약은 문서화돼 있지 않다. 400이면 대안은 "도구를 빼지 말고 통지만 보낸다"이다 —
  OpenAI `WebSearchToolParam`에는 `max_uses`가 없어 Anthropic식 절충을 쓸 수 없다.
- [ ] **추론 항목 재전송 수용** — `previous_response_id` 없이 `reasoning` 항목 +
  `encrypted_content`를 `input`에 실어 보내는 경로. `store=False`와의 조합도 같이 본다
  (SDK 문서가 그 맥락으로 설명한다).
- [ ] **`Reasoning.context` 기본 동작** — SDK가 "`gpt-5.6` 계열은 `all_turns`, 이전 모델은
  `current_turn`이 기본"이라고 적는다. `gpt-6-astra`가 어느 쪽인지에 따라 우리가 `input`에 실은
  추론 항목이 실제로 쓰이는지가 갈린다.
- [ ] **`reasoning.summary: "auto"`가 요약을 실제로 채우는지** — 비면 `ThinkingSummary`가 영영
  나오지 않는다(빈 문자열은 adapter가 거른다).
- [ ] **probe `max_output_tokens=64`가 `effort: high`에서 400을 내지 않는지**, 그리고 추론
  미지원 모델을 골랐을 때 probe만 통과하고 턴이 매번 실패하는 경로. 후자면 probe에 `reasoning`을
  같이 싣는다.
- [ ] **`MIN_CALL_OUTPUT_TOKENS` 실측** — `llm_anthropic`과 같은 256이며 값의 확정은 실측이다.
- [ ] **`gpt-6-astra` 실존과 `developer` 통지에 대한 모델 반응**, 캐시 적중 실측
  (`Usage.cache_read_tokens`).
- [ ] **`openai>=2.0` 하한 확인** — 실제로 확인한 표면은 3.16.2 하나다.
  `cache_write_tokens`·`Reasoning.context`·`ActionSearch.sources`는 최근 필드일 수 있어 2.x에서
  깨질 수 있다. 하한을 올리거나 2.0을 세워 확인한다.
- [ ] **`UsageView` 확장** — 전송 계약이 아직 `input_tokens`·`output_tokens` 두 칸이라 캐시 두
  칸이 화면까지 가지 않는다. 확장 시 `test_assistant_http_openai.py`의 `usage` 단언도 같이 고친다.

**Phase A exit**

- [ ] 가짜 공급자로 HTTP SSE 시나리오 3개 green(재개 포함). live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

---

## 5. Phase B — frontend

### B-01 — 설정 페이지와 AI 연결 섹션

**Acceptance**

- `/settings` 라우트·페이지, app-shell 내비 하단 "설정".
- `features/configure-ai-providers`: 공급자 카드 목록(Claude·Codex, 라벨, 모델, 꼬리 4자리, 활성 배지,
  설치 안 됨 상태), 활성 전환, 삭제 확인, 추가 폼(공급자 → 라벨 → 키 → 모델·base_url), "연결 테스트"
  인라인 결과·지연 시간, 서버 거부 사유 표시. 키 입력 `autocomplete="off"`, 상태에 키를 남기지 않음.
- `entities/assistant` 프로파일 query·mutation(A-04가 생성한 SDK 사용).
- MSW 테스트: 목록·생성(probe 실패 422, base_url 422)·활성·삭제·설치 안 됨.

### B-02 — 어시스턴트 entity: 세션·턴·SSE 리더·이벤트 리듀서

**Acceptance**

- 생성 SDK SSE 클라이언트(`serverSentEvents.gen.ts`) 기반 리더(턴 시작 응답의 `accepted_sequence`로
  열고 재시도는 `Last-Event-ID`·`sseMaxRetryAttempts` 명시(기본 5), 종료 상태에서 닫힘, 진행 중 턴이 없으면
  열지 않음, 409 `no_running_turn`은 재시도 없이 `GET /sessions/{id}` 이력으로 복구(테스트), 취소),
  `SequencedEvent` → 화면 상태 리듀서(스트리밍 텍스트 병합, 검색 활동, 제안, 실패, 턴 상태; 이미 반영한
  sequence 이하는 무시). property test(임의 청크·재개 분할·중복 재전송에 대해 같은 상태).
- 세션 생성·목록·이력·턴 시작·취소 mutation.

### B-03 — 사이드바 채팅 feature

**Acceptance**

- `features/assist-strategy`: 메시지 목록, 입력, 스트리밍, 검색 활동 칩(출처는 http/https만 링크,
  `rel="noopener noreferrer"`), 도구 활동 접힘, 제안 카드(제목·한 문장·근거·출처, "미리보기"·"문서에
  적용"·"적용 후 백테스트" 버튼은 콜백), 취소, 세션 전환, 활성 공급자 없음 → 설정 링크, 닫을 때 진행
  중 턴 취소 확인. 모델 텍스트는 평문 렌더.
- 테스트: 스트리밍·제안 카드·렌더 안전(javascript: URL은 링크가 아님)·취소 확인.

### B-04 — IDE 슬롯과 제안 적용

**Acceptance**

- `widgets/strategy-ide` 우측 레일 탭 "계약 · AI", `assistant` 슬롯, 폭·펼침 `use-panel-layout` 확장.
  1280px 미만은 기존 drawer 규칙.
- `pages/research-strategy-*`: `onApplyProposal` → `replaceRange(0, length, source)`(history 격리, undo 한
  단계). 제안의 기준 텍스트 ≠ 현재 텍스트면 "문서가 바뀌었습니다" + "미리보기"·"그래도 덮어쓰기"
  확인(덮어쓰기도 같은 경로, undo 한 번으로 복원되는 테스트) → compile. "미리보기"는
  diff 투영. "적용 후 백테스트"는 적용 후 기존 실행 흐름. 턴 시작 시 현재 텍스트·진단·실행 설정을
  실어 보낸다.
- 키보드·ARIA(`frontend-ui-quality.md`), i18n 전부 `messages.ts`.

### B-05 — e2e·문서

**Acceptance**

- e2e(MSW 공급자 고정): 설정 등록 → 전략 화면 사이드바 질문 → 검색 활동 → 제안 카드 → 적용 →
  "검증 통과" → 백테스트 페이지. 새로고침 재개. 취소.
- 매뉴얼 절 "AI 어시스턴트 연결과 사용", README, SoT 규칙 행 채움, `frontend/src/features/README.md`에
  slice 설명 추가.

**Phase B exit**

- [ ] 완료 정의 1~5 전부 PLAN에 기록.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.
