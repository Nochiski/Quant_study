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
SDK · SQLite · React 19 · TanStack Query · `EventSource` · Vitest · MSW · Playwright

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

**Intent**: 공급자 없이도 성립하는 계약 층. 공급자 SDK는 이 PR에서 전혀 쓰지 않는다.

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
  `Failure(PROPOSAL_INVALID)`, `max_tool_rounds`, 취소, 공급자 예외 → `Failure(PROVIDER)`에 예외 타입
  이름만), `_turns.py` `AssistantTurnRunner`(스레드, 즉시 append, 중복 턴 거부, cancel, 타임아웃
  → `Failure(TIMEOUT)`), facade `chat.py`·`turns.py`.
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

- spec D6의 라우트 전부. 턴 시작 202, 이벤트 GET SSE(backtest 이벤트 스트림과 같은 프레이밍·재개
  규칙), cancel. 비밀은 요청 전용(`writeOnly`), 응답은 꼬리 4자리. 422·409 코드.
- bootstrap: `build_container`에 assistant 서비스·턴 러너·`StrategyCompilerPort` 구현(authoring compile
  감싸기)·가용 adapter 등록(설치된 SDK만).
- OpenAPI 재생성 + `npm run api:generate` + frontend typecheck green.
- 테스트: TestClient로 프로파일 CRUD·probe 실패 422·base_url 422·활성 전환·턴 시작·SSE 순서·재개·
  중복 턴 409·취소; 응답·로그에 비밀 문자열이 없다는 테스트; OpenAPI에 secret이 `writeOnly`.

### A-05 — Anthropic adapter

**Acceptance**

- `adapters/outbound/llm_anthropic`: spec D4 행 전부. 서버 도구 오류 객체 분기(성공 리스트 vs 오류
  객체) 단위 테스트, `max_uses = request.max_search_uses`, `max_tokens = request.max_output_tokens`,
  프롬프트 캐싱(안정 블록에만 `cache_control`, 휘발 값은 뒤), 예외 → `FailureCode` 매핑에서 message에
  예외 타입 이름만(키 문자열 미노출 테스트). `probe`.
- optional extra `llm`에 `anthropic>=1.0`. `RUN_LLM_LIVE=1` smoke 1건.

### A-06 — OpenAI(Codex) adapter

**Acceptance**: A-05와 같은 수준. 기본 모델은 SDK 문서로 확정해 PLAN 변경 기록에 근거. extra `llm`에
`openai>=2.0`.

### A-07 — 프롬프트 최종본·컨텍스트 품질·시나리오 fixture

**Acceptance**: 시스템 프롬프트 최종본(역할, 한국어, 도구 순서, 금지, 출처), 세션 `Usage` 집계, 가짜
공급자 시나리오 fixture 3개(제안 성공·검증 실패 후 수정·검색 후 제안)가 B-05 e2e의 MSW 응답이 된다.

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

- `EventSource` 기반 리더(`after_sequence` 재개, 종료 상태에서 닫힘, 취소), `SequencedEvent` → 화면
  상태 리듀서(스트리밍 텍스트 병합, 검색 활동, 제안, 실패, 턴 상태). property test(임의 청크·재개
  분할에 대해 같은 상태).
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
  단계) + stale 가드(제안 생성 시점 텍스트 ≠ 현재 텍스트면 적용 거부 안내) → compile. "미리보기"는
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
