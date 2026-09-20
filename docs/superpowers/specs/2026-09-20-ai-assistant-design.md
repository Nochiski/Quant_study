# 설계: AI 어시스턴트 — LLM 연결 설정과 전략 사이드바 채팅

> 작성: 2026-09-20
>
> 상태: Accepted (제품 소유자 요청 2026-09-20: "설정 화면에 클로드·코덱스 연결, 그래프든 YAML이든
> 우측 사이드바에 AI 채팅창. LLM은 들어온 데이터 + 인터넷 검색으로 시장을 서치해 적절한 전략을
> 준다. 추후 확장 여지가 있으니 코드 책임 분리를 잘 해 둔다")
>
> 관련: [schema 1.2·그래프 표현 설계](./2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)
> (어시스턴트는 그 언어와 실행 설정을 읽고 쓴다), 규칙 `backend-package-boundary.md`,
> `frontend-fsd.md`, `strategy-workbench-sot.md`
>
> PR 진행은 [docs/planning/ai-assistant/PLAN.md](../../planning/ai-assistant/PLAN.md)

## 1. 맥락

Strategy Workbench는 전략을 YAML 정본과 그래프 표현으로 편집하고 backend compile로 검증한다.
사용자는 여기에 두 가지를 원한다.

1. 설정 화면에서 LLM 공급자(Claude, Codex)를 연결한다. 참고 UI는 `michelo.frontend`의 설정 모달
   "DB 프로파일" 섹션이다: 프로파일 목록, 활성 전환, 추가, 삭제, 서버 거부 사유 toast.
2. 전략 화면 우측 사이드바에 AI 채팅창. LLM은 현재 전략·실행 설정·데이터 카탈로그와 인터넷
   검색으로 시장을 조사해 전략을 제안한다. 사용자는 제안을 문서에 적용하고 백테스트한다.

확장 여지(다른 공급자, 자체 검색, 백테스트 실행 도구, 자율 리서치)를 남기려면 **공급자 SDK와
도구 실행과 화면이 서로를 모르는 구조**여야 한다.

## 2. 결정

### D1. 책임 경계

```text
frontend                       backend
────────────────────────────   ───────────────────────────────────────────────────────
pages/settings                 adapters/inbound/http_api      /api/v1/assistant/*
features/configure-ai-providers  │
features/chat-assistant          ▼
entities/assistant             application/assistant_chat    유스케이스 + ports/outgoing
widgets/strategy-ide (슬롯)      │  ├─ LlmProviderPort      ← adapters/outbound/llm_anthropic
                                 │  ├─ ProviderProfileRepo   ← adapters/outbound/llm_openai
                                 │  ├─ ProviderSecretStore   ← adapters/outbound/assistant_sqlite
                                 │  └─ ChatSessionRepo       ← adapters/outbound/secrets_local
                                 ▼
                               domain/assistant              순수 타입·도구 계약·제안 검증 규칙
```

- **공급자 SDK는 outbound adapter에만 있다.** `anthropic`·`openai` import는
  `adapters/outbound/llm_anthropic`, `llm_openai` 밖에서 금지(architecture 테스트로 강제).
- **도구는 application이 소유하고 실행한다.** 공급자 adapter는 "도구 정의 목록을 모델에 주고,
  모델이 부른 도구를 콜백으로 실행해 결과를 돌려주는 루프"만 안다. 어떤 도구가 있고 무엇을
  하는지는 `application/assistant_chat`이 정한다. 새 도구(백테스트 실행, 자체 검색)는 application에
  추가하면 모든 공급자에서 동작한다.
- **인터넷 검색은 v1에서 공급자 내장 도구다**(Anthropic `web_search_20260209`, OpenAI Responses
  `web_search`). 포트에는 `ResearchCapability.WEB_SEARCH`로 선언만 하고, adapter가 자기 방식으로
  켠다. 자체 검색 adapter는 같은 capability를 application 도구로 구현하는 후속 범위다.
- **비밀은 backend를 떠나지 않는다.** frontend는 마스킹된 꼬리 4자리와 연결 상태만 본다. 키는
  로컬 사용자 디렉터리 파일(0600)에 저장하고 저장소·DB 덤프·로그·OpenAPI에 나오지 않는다.
- **제안은 문서 트랜잭션으로만 적용된다.** 어시스턴트가 문서를 직접 쓰지 않는다. 제안(YAML)을
  사용자가 "적용"하면 edit-strategy의 전체 범위 교체(업그레이드 적용과 같은 경로)로 들어가고,
  compile이 검증한다. 자동 적용·자동 실행은 없다.

### D2. 도메인 모델 (`domain/assistant`)

```python
class ProviderKind(StrEnum):
    ANTHROPIC = "anthropic"   # 화면 이름 "Claude"
    OPENAI = "openai"         # 화면 이름 "Codex"

@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    kind: ProviderKind
    label: str
    model: str                 # 공급자별 기본값은 adapter가 제안, 사용자가 바꿀 수 있다
    base_url: str | None
    created_at: datetime
    active: bool

@dataclass(frozen=True)
class ToolSpec:                # application이 선언, adapter가 공급자 형식으로 변환
    name: str
    description: str
    input_schema: Mapping[str, object]   # JSON Schema, strict

class ResearchCapability(StrEnum):
    WEB_SEARCH = "web_search"

@dataclass(frozen=True)
class TurnRequest:
    system: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]
    research: frozenset[ResearchCapability]
    max_tool_rounds: int

# 공급자 → application 이벤트 (SSE로 그대로 나간다)
ChatEvent = TextDelta | ThinkingSummary | ToolCall | ToolResultSummary | SearchActivity
          | Proposal | Usage | Done | Failure

@dataclass(frozen=True)
class StrategyProposal:
    title: str
    summary: str               # 한 문장
    rationale: str             # 근거, 출처 인용 포함
    sources: tuple[Source, ...)
    source_text: str           # 전략 YAML(현재 schema 버전)
    environment: RunEnvironmentDraft | None   # 기간·유니버스 제안(1.2 이후)
    compile: ProposalCompileResult            # application이 채운다: ok / diagnostics
```

- 도구 이름과 스키마는 domain 상수다. 공급자 adapter는 `ToolSpec`을 자기 형식으로 변환할 뿐
  이름을 알지 않는다.
- `StrategyProposal.source_text`는 반드시 현재 `CURRENT_SCHEMA_VERSION` 문서다. 검증은
  `domain/strategy`의 hydrate·validate로 application이 한다.

### D3. 유스케이스 (`application/assistant_chat`)

| 서비스 | 책임 |
|---|---|
| `ProviderProfileService` | 프로파일 목록·생성(키 포함)·삭제·활성 전환·연결 테스트(`probe`). 키는 `ProviderSecretStore`에, 나머지는 `ProviderProfileRepository`에 |
| `AssistantChatService` | 세션 생성·조회, `send(session_id, user_text, context) -> Iterator[ChatEvent]`. 컨텍스트 조립, 도구 루프 실행, 제안 검증, 세션 저장 |
| `AssistantContextBuilder` | 시스템 프롬프트와 도구 결과에 넣을 사실을 모은다: runtime schema 요약(필드·연산자·enum은 스키마에서 생성, 손으로 적지 않는다), 데이터 필드 카탈로그(`EquityDataPort`), 팩터 카탈로그(domain registry), 현재 문서 원문·compile 진단, 실행 설정, 오늘 날짜 |

application이 선언하는 도구(v1):

| 도구 | 하는 일 | 실행 |
|---|---|---|
| `read_current_strategy` | 현재 문서 원문·진단·실행 설정 | 요청에 실린 컨텍스트에서 |
| `list_equity_fields` | 데이터 필드 id·이름·단위·설명 | `EquityDataPort` 카탈로그 |
| `list_factor_catalog` | 팩터 정의·방향·필요 필드·구현 여부 | domain factor registry |
| `validate_strategy_yaml` | YAML을 hydrate·validate해 진단 반환 | `domain/strategy` |
| `propose_strategy` | 최종 제안 제출(제목·요약·근거·출처·YAML) | application이 검증 후 `Proposal` 이벤트. 실패하면 도구 오류로 돌려줘 모델이 고친다(최대 3회) |

- 웹 검색은 `research={WEB_SEARCH}`로 요청하고, adapter가 공급자 내장 도구를 켠다. 검색 활동은
  `SearchActivity(query, sources)` 이벤트로 화면에 보인다.
- 도구 루프 상한 `max_tool_rounds`(기본 12), 취소 토큰, 공급자 예외 → `Failure(code, message)`
  (키 오류·요금 한도·네트워크·거부 구분).
- 세션은 전략 문서 단위(`strategy_id` 또는 초안 id)로 묶이고, 메시지·이벤트를 저장한다.

**의존 선언**: `application.assistant_chat` `DEPENDS_ON = ("domain.assistant", "domain.strategy",
"domain.factor", "application.equity_workspace")`. `equity_workspace`는 outgoing port(`EquityDataPort`)
소비다(경계 규칙의 허용 사유). 다른 유스케이스 로직을 빌리지 않는다.

**포트** (`application/assistant_chat/ports/outgoing/`):

```python
class LlmProviderPort(Protocol):
    kind: ProviderKind
    def default_model(self) -> str: ...
    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult: ...
    def stream_turn(self, secret: str, profile: ProviderProfile, request: TurnRequest,
                    execute_tool: Callable[[ToolCall], ToolResult],
                    cancelled: Callable[[], bool]) -> Iterator[ChatEvent]: ...

class ProviderSecretStore(Protocol): get / put / delete (profile_id)
class ProviderProfileRepository(Protocol): list / get / add / delete / set_active
class ChatSessionRepository(Protocol): create / get / list_for_document / append(message) / events
```

### D4. 공급자 adapter

| adapter | SDK | 기본 모델 | 검색 | 스트리밍 |
|---|---|---|---|---|
| `llm_anthropic` | `anthropic` (Python 공식) | `claude-opus-5`, adaptive thinking, `output_config.effort: high` | `web_search_20260209` 서버 도구 | `client.messages.stream`, 도구 루프는 adapter가 수동 루프로(`pause_turn` 재개 포함) |
| `llm_openai` | `openai` (Python 공식) | Responses API 최신 GPT 모델(구현 시 SDK 문서로 확정) | Responses `web_search` 도구 | Responses 스트리밍 |

- `probe`는 최소 토큰 요청 한 번으로 키·모델·네트워크를 확인하고 `ProbeResult(ok, message,
  latency_ms)`를 돌려준다. 실패 종류를 구분한다(인증·모델 없음·네트워크·요금 한도).
- 도구 정의 변환: `ToolSpec` → Anthropic `tools[]`(`strict: true`, `input_schema`) / OpenAI
  function tools. 이름·스키마는 그대로.
- 의존성은 optional extra `llm = ["anthropic>=1.0", "openai>=2.0"]`. 설치되지 않은 공급자는 설정
  화면에서 "설치 필요"로 표시되고 프로파일을 만들 수 없다(bootstrap이 가용 adapter만 등록).

### D5. 저장

- 프로파일·세션·메시지는 `adapters/outbound/assistant_sqlite`가 별도 파일
  (`.local/assistant.sqlite3`, env `STRATEGY_WORKBENCH_ASSISTANT_DB_PATH`)에 저장한다. 전략
  revision DB의 immutable trigger 스키마를 건드리지 않는다.
- 비밀은 `adapters/outbound/secrets_local`이 사용자 설정 디렉터리(Windows `%APPDATA%/quant-workbench/
  secrets.json`, POSIX `~/.config/quant-workbench/secrets.json`, 0600)에 저장한다. env
  `STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH`로 바꿀 수 있다. 저장소 안 경로는 금지. OS 키체인
  adapter는 후속.
- 세션 저장에는 키·시스템 프롬프트 원문을 넣지 않는다(카탈로그 스냅샷 해시만).

### D6. HTTP API (`/api/v1/assistant`)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/providers` | 가용 공급자 종류(설치 여부·기본 모델)와 프로파일 목록(키 꼬리 4자리) |
| POST | `/providers` | 프로파일 생성 `{kind, label, model?, base_url?, secret}` → 저장 전 `probe` 필수 통과 |
| POST | `/providers/{id}/test` | 연결 테스트 |
| POST | `/providers/{id}/activate` | 활성 프로파일 전환 |
| DELETE | `/providers/{id}` | 삭제(키 포함) |
| POST | `/sessions` | 세션 생성 `{document_ref}` |
| GET | `/sessions?document_ref=` | 문서의 세션 목록 |
| GET | `/sessions/{id}` | 메시지·이벤트 이력 |
| POST | `/sessions/{id}/messages` | `{text, context: {source_text, source_format, environment?, diagnostics?}}` → **SSE** `ChatEvent` 스트림(`id`·`event`·`data`, 기존 backtest 이벤트 스트림과 같은 모양) |
| POST | `/sessions/{id}/cancel` | 진행 중 턴 취소 |

- 422 코드: `assistant.provider_not_installed`, `assistant.no_active_provider`,
  `assistant.probe_failed`, `assistant.turn_in_progress`. 공급자 오류는 `Failure` 이벤트로.
- OpenAPI에 반영하고 frontend SDK를 재생성한다. 비밀 필드는 요청 전용(`writeOnly`).

### D7. Frontend

| slice | 책임 |
|---|---|
| `entities/assistant` | 생성 SDK 타입, 프로파일·세션 query, SSE 리더(`EventSource`가 POST를 못 하므로 `fetch` + `ReadableStream` 파서), `ChatEvent` 리듀서 |
| `features/configure-ai-providers` | 설정 화면 "AI 연결" 섹션. michelo DB 프로파일 섹션 패턴: 카드 목록(공급자 아이콘·라벨·모델·꼬리 4자리·활성 배지), 활성 전환, 삭제 확인, 추가 폼(공급자 선택 → 라벨·키·모델), "연결 테스트" 결과 인라인. 설치 안 된 공급자는 비활성 + 이유 |
| `features/chat-assistant` | 우측 사이드바 채팅: 메시지 목록, 스트리밍 텍스트, 검색 활동 칩(질의·출처 링크), 도구 활동 접힘, 제안 카드(제목·한 문장·근거·출처·"미리보기"·"문서에 적용"·"적용 후 백테스트"), 취소, 세션 전환. 적용은 `onApplyProposal(sourceText)` 콜백으로 밖에 넘긴다(feature가 feature를 import하지 않는다) |
| `pages/settings` | `/settings` 라우트. 섹션: AI 연결(위 feature), 실행 설정 기본값(후속) |
| `widgets/app-shell` | 내비 하단 "설정" 링크 |
| `widgets/strategy-ide` | 우측 레일에 `assistant` 슬롯. 계약 인스펙터와 탭으로 공존("계약 · AI"), 폭·펼침은 기존 `use-panel-layout` |
| `pages/research-strategy-*` | 조합: chat-assistant의 `onApplyProposal` → edit-strategy의 전체 범위 교체(`use-upgrade-document`와 같은 `setText` 한 번, undo 한 단계) → compile → 진단. "적용 후 백테스트"는 적용 뒤 기존 run-backtest 실행 |

- 사이드바는 그래프·YAML 어느 탭에서도 같은 세션이다. 컨텍스트는 보낼 때마다 현재 편집기
  텍스트·진단·실행 설정을 실어 보낸다(서버가 문서를 따로 들지 않는다).
- 제안 미리보기는 기존 diff 투영(`diff-projection.ts`)으로 현재 문서와의 차이를 보인다.
- 키 입력 필드는 `autocomplete="off"`, 붙여넣기 후 마스킹. 프론트 상태·localStorage에 키를 두지
  않는다.

### D8. 프롬프트와 컨텍스트 원칙

- 시스템 프롬프트는 backend `application/assistant_chat/_prompt.py`가 소유한다. 전략 언어 요약은
  runtime schema에서 생성한다(필드 이름·enum을 손으로 적지 않는다, SoT 규칙). 한국어로 답하고,
  제안은 반드시 `propose_strategy` 도구로 제출하며, 근거에는 검색 출처 URL을 붙이고, 실행 불가한
  아이디어(지원 안 되는 연산자·필드)는 제안하지 않도록 지시한다.
- 모델은 먼저 `validate_strategy_yaml`로 자기 제안을 검증하고 통과한 것만 제출하도록 지시한다.
  application은 제출 시 다시 검증한다(모델을 믿지 않는다).
- 프롬프트 캐싱: 시스템 프롬프트 + 카탈로그(안정 부분)에 `cache_control`, 문서·질문은 뒤에.

### D9. 상태 소유권

| 상태 | 소유자 |
|---|---|
| 공급자 프로파일·활성 여부 | `assistant_sqlite` (application port 뒤) |
| 비밀 | `secrets_local` |
| 세션·메시지·이벤트 이력 | `assistant_sqlite` |
| 도구 정의·프롬프트·검증 규칙 | `application/assistant_chat` |
| 공급자별 요청 형식·스트리밍·검색 도구 켜기 | 각 `llm_*` adapter |
| 사이드바 열림·폭·현재 세션 id | frontend local UI state |
| 제안 적용 결과(문서 텍스트) | edit-strategy source 트랜잭션(정본 YAML) |

## 3. Non-goals

- 자동 적용·자동 백테스트·자동 매매. 모든 문서 변경은 사용자의 "적용" 클릭.
- Claude Agent SDK·Managed Agents. 이 기능은 Messages/Responses API 위의 자체 도구 루프다.
- 자체 웹 검색 adapter(후속), 백테스트 실행 도구(후속, application 도구로 추가).
- 다중 사용자·권한. 로컬 단일 사용자 도구다.
- 채팅으로 문서를 부분 편집(레시피 단계 수정 등). v1은 전체 제안 적용만.

## 4. Phase와 PR

| Phase | 목표 | PR |
|---|---|---|
| A | backend: 도메인·포트·서비스(가짜 공급자로 테스트), 저장 adapter, HTTP API, Anthropic·OpenAI adapter | A-01 ~ A-05 |
| B | frontend: 설정 화면·공급자 연결, 어시스턴트 entity·SSE, 사이드바 채팅·제안 적용, e2e·문서 | B-01 ~ B-04 |

## 5. 완료 정의

1. 설정에서 Claude 프로파일을 키로 등록하면 연결 테스트가 통과하고 활성으로 표시된다. Codex도 같다.
2. 전략 화면 우측 사이드바에서 "요즘 KRX에서 통할 만한 모멘텀 전략 하나 만들어 줘"를 보내면
   검색 활동이 보이고, 제안 카드가 뜨며, "문서에 적용"이 YAML을 바꾸고 compile "검증 통과"가 뜬다.
   "적용 후 백테스트"가 실행 페이지로 간다.
3. 키가 응답·로그·DB·OpenAPI 어디에도 평문으로 나오지 않는다(테스트로 고정).
4. `anthropic`·`openai` import가 두 adapter 밖에 없다(architecture 테스트).
5. 공급자 adapter 없이(가짜 공급자) application·HTTP·frontend 테스트가 전부 돈다.

## 6. 테스트

- backend: 가짜 `LlmProviderPort`로 도구 루프·제안 검증·재시도·취소·실패 이벤트; 프로파일 서비스
  probe 필수; secrets_local 권한·경로; sqlite 세션 저장; HTTP SSE 계약(이벤트 순서·id); architecture
  import 게이트; 실제 SDK adapter는 SDK의 응답 객체를 흉내 낸 단위 테스트 + `RUN_LLM_LIVE=1`일
  때만 도는 실연결 smoke.
- frontend: SSE 파서·리듀서 property test; 설정 섹션 MSW(생성·테스트·활성·삭제·거부 사유);
  사이드바 스트리밍·제안 카드·적용 콜백; e2e(MSW로 공급자 응답 고정): 설정 등록 → 사이드바 제안 →
  적용 → 검증 통과 → 백테스트 페이지.

## 7. 롤백

Phase A는 새 노드·새 라우트 추가라 PR 단위 revert 가능. Phase B는 feature 모듈 추가·슬롯 연결이라
사이드바를 숨기면 기존 화면으로 돌아간다.
