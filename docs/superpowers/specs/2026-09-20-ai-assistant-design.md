# 설계: AI 어시스턴트 — LLM 연결 설정과 전략 사이드바 채팅

> 작성: 2026-09-20 (P0-01 리뷰 1·2차 반영 개정: 제안 적용 경로, 턴·취소 owner, PR 분할, 검색·토큰
> 상한, base_url·비밀 스크럽·렌더 안전, SSE 재개 규칙, 적용 전 확인, 예산 집행 위치·Failure 우선순위)
>
> 상태: Accepted (제품 소유자 요청 2026-09-20: "설정 화면에 클로드·코덱스 연결, 그래프든 YAML이든
> 우측 사이드바에 AI 채팅창. LLM은 들어온 데이터 + 인터넷 검색으로 시장을 서치해 적절한 전략을
> 준다. 추후 확장 여지가 있으니 코드 책임 분리를 잘 해 둔다")
>
> 관련 규칙: `backend-package-boundary.md`, `frontend-fsd.md`, `strategy-workbench-sot.md`. 병행
> initiative: schema 1.2·그래프 표현(PR #167). 어시스턴트는 전략 언어를 runtime schema에서 읽으므로
> 스키마 버전과 무관하게 동작하지만, 그 initiative가 머지되면 시나리오 fixture(A-07)와 e2e(B-05)를
> 1.2 문서로 갱신해야 한다.
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

전제: 이 앱은 로컬 단일 사용자 도구이고 backend는 uvicorn 단일 워커로 돈다. 진행 중 턴 레지스트리와
취소는 이 전제 위에 있다(D3). 워커를 늘리려면 레지스트리를 프로세스 밖으로 옮겨야 한다.

## 2. 결정

### D1. 책임 경계

```text
frontend                          backend
───────────────────────────────   ─────────────────────────────────────────────────────────
pages/settings                    adapters/inbound/http_api        /api/v1/assistant/*
features/configure-ai-providers     │
features/assist-strategy            ▼
entities/assistant                application/assistant_chat       유스케이스 + 턴 러너 + ports/outgoing
widgets/strategy-ide (슬롯)         │
                                    ├─ LlmProviderPort ──────────── adapters/outbound/llm_anthropic
                                    │                               adapters/outbound/llm_openai
                                    ├─ StrategyCompilerPort ──────── bootstrap이 StrategyAuthoringService.compile을 감싼다
                                    ├─ ProviderProfileRepository ── adapters/outbound/assistant_sqlite
                                    ├─ ChatSessionRepository ────── adapters/outbound/assistant_sqlite
                                    └─ ProviderSecretStore ──────── adapters/outbound/secrets_local
                                    ▼
                                  domain/assistant                 순수 타입·도구 계약(이름·JSON Schema)
```

- **공급자 SDK는 outbound adapter에만 있다.** `anthropic`·`openai` import는
  `adapters/outbound/llm_anthropic`, `llm_openai` 밖에서 금지(architecture 테스트로 강제).
- **도구는 application이 소유하고 실행한다.** 공급자 adapter는 "도구 정의 목록을 모델에 주고,
  모델이 부른 도구를 콜백으로 실행해 결과를 돌려주는 루프"만 안다. 새 도구(백테스트 실행, 자체
  검색)는 application에 추가하면 모든 공급자에서 동작한다.
- **인터넷 검색은 v1에서 공급자 내장 도구다**(Anthropic `web_search_20260209`, OpenAI Responses
  `web_search`). 포트에는 `ResearchCapability.WEB_SEARCH`로 선언만 하고 adapter가 자기 방식으로
  켠다. 자체 검색 adapter는 같은 capability를 application 도구로 구현하는 후속 범위다.
- **비밀은 backend를 떠나지 않는다.** frontend는 마스킹된 꼬리 4자리와 연결 상태만 본다. 키는
  사용자 설정 디렉터리 파일에 저장하고 저장소·DB·로그·응답·OpenAPI에 나오지 않는다(D5).
- **제안은 사용자의 "적용"으로만 문서에 들어간다.** 어시스턴트가 문서를 직접 쓰지 않는다. 적용
  경로는 D7이 정한다. 자동 적용·자동 실행은 없다.

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
    base_url: str | None       # D6의 base_url 규칙을 통과한 값만
    created_at: datetime
    active: bool

@dataclass(frozen=True)
class ToolSpec:                # application이 선언, adapter가 공급자 형식으로 변환
    name: str
    description: str
    input_schema: Mapping[str, object]   # JSON Schema. strict 변환을 위해 최상위에
                                         # additionalProperties: false 와 required 를 반드시 둔다

class ResearchCapability(StrEnum):
    WEB_SEARCH = "web_search"

@dataclass(frozen=True)
class Source:
    title: str
    url: str

@dataclass(frozen=True)
class TurnRequest:
    system: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]
    research: frozenset[ResearchCapability]
    max_tool_rounds: int
    max_search_uses: int              # 공급자 내장 검색의 max_uses (D4)
    max_output_tokens_per_call: int   # 공급자 호출 한 번의 max_tokens
    max_turn_output_tokens: int       # 턴 누적 출력 토큰 예산. 집행은 adapter(루프 주인)가 한다

# 공급자·application → 화면 이벤트 (세션 저장소에 sequence 번호와 함께 남는다)
ChatEvent = TextDelta | ThinkingSummary | ToolCall | ToolResultSummary | SearchActivity
          | Proposal | Usage | Done | Failure

class TurnStatus(StrEnum):
    RUNNING = "running"; COMPLETED = "completed"; FAILED = "failed"; CANCELLED = "cancelled"

@dataclass(frozen=True)
class Turn:
    turn_id: str
    session_id: str
    status: TurnStatus
    accepted_sequence: int     # 턴 시작 직전 세션의 마지막 sequence. 클라이언트가 after_sequence로 쓰고,
                               # 저장 레코드에도 남겨 이력 화면이 턴의 시작 위치를 안다
    started_at: datetime
    finished_at: datetime | None

@dataclass(frozen=True)
class SequencedEvent:
    sequence: int              # 세션 안에서 단조 증가
    turn_id: str
    event: ChatEvent

class FailureCode(StrEnum):
    AUTH, RATE_LIMIT, NETWORK, REFUSAL, PROVIDER, INTERNAL, TOOL_ROUNDS_EXCEEDED, TIMEOUT,
    CANCELLED, PROPOSAL_INVALID, OUTPUT_TRUNCATED, TOKEN_BUDGET_EXCEEDED
    # INTERNAL = 러너·서비스 내부 예외(공급자 탓이 아니다), message는 예외 타입 이름만

@dataclass(frozen=True)
class StrategyProposal:
    title: str
    summary: str               # 한 문장
    rationale: str             # 근거, 출처 인용 포함
    sources: tuple[Source, ...]
    source_text: str           # 전략 YAML(현재 CURRENT_SCHEMA_VERSION 문서)
    compile: ProposalCompileResult   # application이 채운다: ok / spec_hash / diagnostics
```

- 도구 이름과 JSON Schema는 domain 상수다. 공급자 adapter는 `ToolSpec`을 자기 형식으로 변환할 뿐
  이름을 알지 않는다.
- `Failure.message`는 자유 문자열이지만 **SDK 예외 문자열·응답 본문을 그대로 넣지 않는다.** 예외는
  `FailureCode`로만 매핑하고 message는 코드별 고정 문장 + 예외 타입 이름까지만 허용한다(비밀
  스크럽, 완료 정의 3).
- **`ProbeResult.message`도 같은 급으로 스크럽한다.** adapter는 `ProbeFailure` 사유만 고르고 문장은
  고르지 못한다(`message`는 사유에서 유도되는 고정 문구다). 공급자 인증 오류 본문은
  `Incorrect API key provided: sk-proj-…`처럼 키 조각을 담고, 이 값은 설정 화면 "연결 테스트"
  결과로 HTTP 응답 본문까지 그대로 나간다. `assistant.probe_failed` 422 본문은 `failure` 코드를
  보고 자기 문장을 고른다.

### D3. 유스케이스 (`application/assistant_chat`)

| 서비스 | 책임 |
|---|---|
| `ProviderProfileService` | 프로파일 목록·생성(키 포함)·삭제·활성 전환·연결 테스트(`probe`). 키는 `ProviderSecretStore`에, 나머지는 `ProviderProfileRepository`에. 생성은 `probe` 통과가 필수 |
| `AssistantContextBuilder` | 시스템 프롬프트와 도구 결과의 사실: runtime schema 요약(필드·연산자·enum은 스키마에서 생성, 손으로 적지 않는다), 데이터 필드 카탈로그(`EquityDataPort.list_fields`), 팩터 카탈로그(domain `FactorRegistry`), 현재 문서 원문·compile 진단·실행 설정, 오늘 날짜 |
| `AssistantChatService` | 세션 생성·조회, `send(session_id, text, context, *, cancelled) -> Iterator[ChatEvent]`: 컨텍스트 조립, 공급자 `stream_turn` 호출, 도구 실행 콜백, 제안 검증, 메시지 저장 |
| `AssistantTurnRunner` | **진행 중 턴의 owner.** `start(session_id, text, context) -> Turn`은 세션에 RUNNING 턴이 있으면 `TurnInProgressError`, 아니면 스레드에서 `send`를 돌리며 이벤트를 나오는 대로 `ChatSessionRepository.append_events`로 영속화한다. `cancel(turn_id)`는 프로세스 내 `threading.Event`를 set(이것이 `send`의 `cancelled` 콜백)하고 턴을 CANCELLED로 기록한다. `events(session_id, after_sequence)`·`state(turn_id)`는 저장소를 읽는다. backtest_run의 `BacktestRunService`와 같은 구조(스레드 + sequence + 폴링 스트림). 레지스트리는 프로세스 내(dict + RLock), 단일 워커 전제 |

application이 선언하는 도구(v1). 모든 스키마는 `additionalProperties: false`와 `required`를 갖는다.

| 도구 | 하는 일 | 실행 |
|---|---|---|
| `read_current_strategy` | 현재 문서 원문·진단·실행 설정 | 요청에 실린 컨텍스트에서 |
| `list_equity_fields` | 데이터 필드 id·이름·단위·설명 | `EquityDataPort` 카탈로그 |
| `list_factor_catalog` | 팩터 정의·방향·필요 필드·구현 여부 | domain factor registry |
| `validate_strategy_yaml` | YAML을 parse·hydrate·validate해 진단 반환 | `StrategyCompilerPort` |
| `propose_strategy` | 최종 제안 제출(제목·요약·근거·출처·YAML) | application이 `StrategyCompilerPort`로 검증 후 `Proposal` 이벤트. 실패하면 진단을 담은 도구 오류로 돌려줘 모델이 고친다 |

턴의 상한과 종료:

- `max_tool_rounds`(기본 12)를 넘으면 `Failure(TOOL_ROUNDS_EXCEEDED)`로 턴을 끝낸다. 값은 application이
  정하고 **집행은 토큰 예산과 같이 adapter(루프 주인)가 한다.** 서비스는 라운드를 세지 않는다.
- `propose_strategy`가 3회 연속 검증에 실패하면 `Failure(PROPOSAL_INVALID)`로 끝낸다. 제안 없이 루프가
  자연 종료되면 `Done("end_turn")`이고 화면은 "제안 없이 답변만"으로 보인다.
- **`Done`은 공급자 스트림이 끝났다는 표시일 뿐 턴 종료 판정이 아니다.** 턴이 끝났는지는 저장된 턴 상태
  (`COMPLETED`/`FAILED`/`CANCELLED`)가 말하며, 도구가 세운 종료 사유는 손에 든 이벤트를 내보낸 뒤에
  적용되므로 `Done` 뒤에 `Failure`가 올 수 있다. 소비자(SSE 스트림, frontend 리듀서)는 `Done`을 받았다고
  스트림을 닫거나 턴을 완료로 표시하지 않는다.
- 턴당 벽시계 타임아웃(기본 300초) 초과는 `Failure(TIMEOUT)`. 검색 `max_search_uses`(기본 8)와
  호출당 `max_output_tokens_per_call`(기본 16000)은 `TurnRequest`로 adapter에 전달된다.
- 토큰 예산은 턴 단위이며 **집행은 adapter가 한다**(루프의 주인이 adapter이고 `TurnRequest`는 루프 시작 전에
  넘어가므로 서비스는 호출당 상한을 바꿀 수 없다). adapter는 자기 루프 안에서 출력 토큰을 누적해 남은
  예산이 호출당 상한보다 작으면 다음 호출의 `max_tokens`를 그 값으로 줄이고, 소진되면
  `Failure(TOKEN_BUDGET_EXCEEDED)`를 내고 루프를 멈춘다. 서비스는 `Usage`를 기록만 한다. 기본값(호출당
  16000, 턴 64000, 라운드 12)은 A-07의 fixture·live smoke 실측 뒤 확정하고 근거를 PLAN에 남긴다.
- **Failure 우선순위**: 한 턴에서 턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다. 뒤이어 들어오는
  Failure(예: 러너 타임아웃이 cancel 신호를 보낸 뒤 adapter가 내는 `CANCELLED`)는 이벤트로 저장되지만
  상태를 바꾸지 않는다. 러너의 `TIMEOUT`은 cancel 신호를 보내기 전에 확정한다.
- 공급자 응답이 `max_tokens`로 잘리면 `Failure(OUTPUT_TRUNCATED)`(화면: "답변이 길어 잘렸습니다").
  잘린 텍스트는 보존하되 정상 종료로 보이지 않는다.
- 취소는 `Failure(CANCELLED)`. 이미 스트리밍된 텍스트는 assistant 메시지로 보존한다.
- 클라이언트가 이벤트 스트림 연결을 끊어도 턴은 위 상한 안에서 계속 돌고 이벤트는 저장된다
  (backtest와 같은 의미). 멈추려면 취소를 부른다. 프론트는 사이드바를 닫을 때 진행 중 턴이 있으면
  취소를 묻는다.

**포트** (`application/assistant_chat/ports/outgoing/`):

```python
class LlmProviderPort(Protocol):
    kind: ProviderKind
    def default_model(self) -> str: ...
    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult: ...
    def stream_turn(self, secret: str, profile: ProviderProfile, request: TurnRequest,
                    execute_tool: Callable[[ToolCall], ToolResult],
                    cancelled: Callable[[], bool]) -> Iterator[ChatEvent]: ...

class StrategyCompilerPort(Protocol):
    def compile(self, source_text: str) -> ProposalCompileResult: ...   # bootstrap이 authoring compile을 감싼다

class ProviderSecretStore(Protocol): get / put / delete (profile_id)
class ProviderProfileRepository(Protocol): list / get / add / delete / set_active
class ChatSessionRepository(Protocol):
    create / get / list_for_document / append_message
    messages(session_id) -> tuple[ChatMessage, ...]      # 다음 턴의 TurnRequest.messages 출처
    create_turn / update_turn / get_turn / append_events(turn_id, events) -> sequences
    last_sequence(session_id) -> int                     # accepted_sequence용, 이력 전체를 읽지 않는다
    events(session_id, *, after_sequence=-1) -> tuple[SequencedEvent, ...]
```

**의존 선언**: `application.assistant_chat` `DEPENDS_ON = ("domain.assistant", "domain.strategy",
"domain.factor", "application.equity_workspace")`. `equity_workspace`는 outgoing port(`EquityDataPort`)
소비다. 이 6번째 application 간 화살표를 `backend-package-boundary.md`의 선언 목록에 추가한다(A-01).
compile은 `StrategyCompilerPort`로 받으므로 `strategy_authoring`에 의존하지 않는다.

### D4. 공급자 adapter

| adapter | SDK | 기본 모델 | 검색 | 스트리밍 |
|---|---|---|---|---|
| `llm_anthropic` | `anthropic` (Python 공식) | `claude-opus-5`, `thinking: {type: "adaptive", display: "summarized"}`, `output_config.effort: high`, `max_tokens = request.max_output_tokens_per_call` | `web_search_20260209` 서버 도구, 도메인 제한 없음. SDK의 `max_uses`는 **호출당** 한도라 adapter가 턴 누적 검색 횟수를 세어 호출마다 `max_uses = max(0, request.max_search_uses − 누적)`을 다시 계산하고, 0이면 그 호출의 도구 목록에서 `web_search`를 뺀다(D9 · OpenAI 행과 같은 집행) | `client.messages.stream`. 도구 루프는 adapter의 수동 루프(`stop_reason == "tool_use"` → `execute_tool` → `tool_result`; `pause_turn` 재개; `refusal` → `Failure(REFUSAL)`) |
| `llm_openai` | `openai` (Python 공식) | Responses API 최신 GPT 모델(A-06 구현 시 SDK 문서로 확정, PLAN 변경 기록에 근거), `max_output_tokens = request.max_output_tokens_per_call` | Responses `web_search` 도구(서버 측이라 개별 호출을 거부할 수 없다). adapter가 검색 호출 이벤트를 세어 누적이 `max_search_uses`에 닿으면 이후 공급자 호출의 도구 목록에서 `web_search`를 빼고 화면(`SearchActivity`)에 알린다. 모델에게 알리는 문장은 adapter가 저술하지 않고 application 프롬프트 owner가 준 고정 문구(`TurnRequest`에 실어 보내는 도구 결과 문구)만 쓴다(A-06에서 확정). 한 호출 안의 초과는 사후 관측만 가능하다. 실제 SDK 표면은 A-06에서 확정 | Responses 스트리밍 |

- `probe`는 최소 토큰 요청 한 번으로 키·모델·네트워크를 확인하고 `ProbeResult(ok, message,
  latency_ms, failure)`를 돌려준다. 실패 종류를 구분한다(인증·모델 없음·네트워크·요금 한도).
- 도구 정의 변환: `ToolSpec` → Anthropic `tools[]`(`strict: true`, `input_schema`) / OpenAI function
  tools. 이름·스키마는 그대로. `strict`가 요구하는 `additionalProperties: false`·`required`는 domain
  상수가 이미 갖는다.
- **서버 도구 오류 블록**: 검색 결과 블록의 `content`는 성공이면 리스트, 오류면 단일 오류 객체다.
  adapter는 분기해서 오류를 `SearchActivity(query, sources=())` + 이어지는 텍스트로 넘기거나, 턴을
  이어갈 수 없으면 `Failure(PROVIDER)`로 매핑한다. 예외를 던지지 않는다.
- 예외 → `Failure`: SDK 예외 종류를 `FailureCode`로 매핑하고 message에는 예외 타입 이름만 쓴다.
  단위 테스트가 "예외 메시지에 키 문자열이 섞여도 `Failure.message`에 나오지 않는다"를 고정한다.
  `stop_reason == "max_tokens"`(OpenAI는 `incomplete`·`max_output_tokens`)는 `Failure(OUTPUT_TRUNCATED)`.
- 프롬프트 캐싱: 시스템 프롬프트와 카탈로그(안정 부분)에 `cache_control`, 오늘 날짜·현재 문서·질문은
  마지막 breakpoint 뒤에 둔다. breakpoint는 최대 4개.
- 의존성은 optional extra `llm = ["anthropic>=1.0", "openai>=2.0"]`. 설치되지 않은 공급자는 설정
  화면에서 "설치 필요"로 표시되고 프로파일을 만들 수 없다(bootstrap이 가용 adapter만 등록).

### D5. 저장

- 프로파일·세션·턴·메시지·이벤트는 `adapters/outbound/assistant_sqlite`가 별도 파일
  (`.local/assistant.sqlite3`, env `STRATEGY_WORKBENCH_ASSISTANT_DB_PATH`)에 저장한다. 전략
  revision DB의 immutable trigger 스키마를 건드리지 않는다.
- 비밀은 `adapters/outbound/secrets_local`이 사용자 설정 디렉터리(Windows `%APPDATA%/quant-workbench/
  secrets.json`, POSIX `~/.config/quant-workbench/secrets.json`)에 저장한다. 파일 권한은 **POSIX는
  0600, Windows는 현재 사용자만 접근 가능한 ACL**(`icacls`로 상속 제거 후 사용자 F만). 권한 테스트는
  플랫폼별로 분기한다. env `STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH`로 경로를 바꿀 수 있고 저장소 안
  경로는 거부한다. OS 키체인 adapter는 후속.
- 세션 저장에는 키·시스템 프롬프트 원문을 넣지 않는다(카탈로그 스냅샷 해시만).

### D6. HTTP API (`/api/v1/assistant`)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/providers` | 가용 공급자 종류(설치 여부·기본 모델)와 프로파일 목록(키 꼬리 4자리) |
| POST | `/providers` | 프로파일 생성 `{kind, label, model?, base_url?, secret}` → base_url 규칙 검사 → `probe` 통과 후 저장 |
| POST | `/providers/{id}/test` | 연결 테스트 |
| POST | `/providers/{id}/activate` | 활성 프로파일 전환 |
| DELETE | `/providers/{id}` | 삭제(키 포함) |
| POST | `/sessions` | 세션 생성 `{document_ref}` |
| GET | `/sessions?document_ref=` | 문서의 세션 목록 |
| GET | `/sessions/{id}` | 메시지·턴 이력 |
| POST | `/sessions/{id}/turns` | 턴 시작 `{text, context: {source_text, source_format, environment?, diagnostics?}}` → 202 `{turn_id, accepted_sequence}`. `accepted_sequence`는 턴 시작 직전 세션의 마지막 sequence이며 클라이언트가 그대로 `after_sequence`로 쓴다. RUNNING 턴이 있으면 409 `assistant.turn_in_progress` |
| GET | `/sessions/{id}/events?after_sequence=` | **SSE** `SequencedEvent` 스트림. backtest 이벤트 스트림(`/api/v1/backtests/{run_id}/events`)과 같은 프레이밍(`id`=sequence). 재개 위치는 `after_sequence` 쿼리 또는 `Last-Event-ID` 헤더(헤더가 있으면 우선). 진행 중(RUNNING) 턴이 없으면 409 `assistant.no_running_turn`으로 열지 않는다(프론트 규칙 D7의 backstop이며, 프론트는 409에 재시도하지 않고 이력으로 복구한다). 열린 동안 15초마다 `: keepalive` 주석을 보내고, 그 턴이 종료 상태가 되면 닫는다 |
| POST | `/sessions/{id}/turns/{turn_id}/cancel` | 취소(영속 상태 CANCELLED + 프로세스 내 신호) |

- 422 코드: `assistant.provider_not_installed`, `assistant.no_active_provider`,
  `assistant.probe_failed`, `assistant.base_url_rejected`. 409: `assistant.turn_in_progress`,
  `assistant.no_running_turn`. 공급자 오류는 `Failure` 이벤트로.
- **base_url 규칙**: 없으면 공급자 기본. 있으면 `https` 스킴만, 호스트는 루프백·사설 대역·IP 리터럴
  금지. 로컬 프록시 개발용 예외는 env `STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL=1`일
  때만 `http`·루프백 허용. 검사는 application(`ProviderProfileService`)이 하고 위반은
  `assistant.base_url_rejected`.
- **adapter는 SDK의 환경 변수 폴백(base_url·api_key)을 차단한다.** 둘 다 인자를 비우면 공급자
  SDK가 `ANTHROPIC_BASE_URL`·`ANTHROPIC_API_KEY`(OpenAI도 같은 방식)를 읽어, 위 검사를 한 번도
  지나지 않은 호스트로 프로파일의 키가 나간다. 화면은 정상으로 보인다. base_url이 없으면
  adapter가 공급자 기본 호스트를 **명시**하고, api_key는 언제나 프로파일 비밀만 쓴다.
- OpenAPI와 frontend generated SDK를 **같은 PR(A-04)에서** 갱신한다(12절). 비밀 필드는 요청 전용
  (`writeOnly`), 응답 스키마에 없다.

### D7. Frontend

| slice | 책임 |
|---|---|
| `entities/assistant` | 생성 SDK 타입, 프로파일·세션 query, SSE 리더는 **생성 SDK의 SSE 클라이언트**(`shared/api/generated/core/serverSentEvents.gen.ts`: fetch 기반, `id:` 파싱, 재시도 시 `Last-Event-ID` 부착)를 그대로 쓴다. `EventSource`를 쓰지 않는다(재연결 시 같은 URL을 반복해 중복 수신). 리듀서는 이미 반영한 sequence 이하를 무시한다(멱등). 진행 중 턴이 있을 때만 스트림을 열고 종료 상태에서 닫는다. `sseMaxRetryAttempts`를 명시(기본 5)하고, 409 `no_running_turn`은 재시도하지 않고 `GET /sessions/{id}` 이력으로 그 턴의 이벤트를 복구한다(턴 시작 직후 실패해 스트림을 열기 전에 끝난 경우) |
| `features/configure-ai-providers` | 설정 화면 "AI 연결" 섹션. michelo DB 프로파일 섹션 패턴: 카드 목록(공급자 이름·라벨·모델·꼬리 4자리·활성 배지), 활성 전환, 삭제 확인, 추가 폼(공급자 선택 → 라벨·키·모델·base_url), "연결 테스트" 결과 인라인. 설치 안 된 공급자는 비활성 + 이유 |
| `features/assist-strategy` | 우측 사이드바 채팅: 메시지 목록, 스트리밍 텍스트, 검색 활동 칩(질의·출처 링크), 도구 활동 접힘, 제안 카드(제목·한 문장·근거·출처·"미리보기"·"문서에 적용"·"적용 후 백테스트"), 취소, 세션 전환, 닫을 때 진행 중 턴 취소 확인. 적용은 `onApplyProposal(proposal)` 콜백으로 밖에 넘긴다(feature가 feature를 import하지 않는다) |
| `pages/settings` | `/settings` 라우트. 섹션: AI 연결(위 feature) |
| `widgets/app-shell` | 내비 하단 "설정" 링크 |
| `widgets/strategy-ide` | 우측 레일에 `assistant` 슬롯. 계약 인스펙터와 탭으로 공존("계약 · AI"), 폭·펼침은 기존 `use-panel-layout` |
| `pages/research-strategy-*` | 조합: `onApplyProposal` → **업그레이드 적용과 같은 전체 범위 교체 경로**(`CodeEditorHandle.replaceRange(0, length, source)`; `setText`가 아니라 `replaceRange`인 이유는 history 격리(`isolateHistory`)로 직후 타이핑과 undo가 섞이지 않게 하기 위함, `use-upgrade-document.ts:93-99`와 동일) → compile → 진단. **적용 전 확인**: 제안 카드의 기준 텍스트(턴 시작 시점)와 적용 시점 텍스트가 다르면 바로 덮어쓰지 않고 "문서가 바뀌었습니다" 안내와 함께 "미리보기"·"그래도 덮어쓰기" 두 버튼을 보인다. 덮어쓰기도 같은 `replaceRange` 한 번·undo 한 단계이므로 데이터 손실이 아니다(턴은 수십~수백 초라 기다리며 편집하는 것이 흔하다). "적용 후 백테스트"는 적용 뒤 기존 run-backtest 실행 |

- 사이드바는 그래프·YAML 어느 탭에서도 같은 세션이다. 턴을 시작할 때마다 현재 편집기 텍스트·진단·
  실행 설정을 실어 보낸다(서버가 문서를 따로 들지 않는다).
- 제안 미리보기는 기존 diff 투영(`diff-projection.ts`)으로 현재 문서와의 차이를 보인다.
- **렌더 안전**: 모델 텍스트·제안 본문은 평문(줄바꿈만)으로 렌더한다. 출처 URL은 `http`·`https`만
  링크로 만들고 나머지는 텍스트로 둔다. 링크는 `target="_blank" rel="noopener noreferrer"`.
- 키 입력 필드는 `autocomplete="off"`, 붙여넣기 후 마스킹. 프론트 상태·localStorage에 키를 두지 않는다.

### D8. 프롬프트와 컨텍스트 원칙

- 시스템 프롬프트는 backend `application/assistant_chat/_prompt.py`가 소유한다. 전략 언어 요약은
  runtime schema에서 생성한다(필드 이름·enum을 손으로 적지 않는다, SoT 규칙). 한국어로 답하고,
  제안은 반드시 `propose_strategy` 도구로 제출하며, 근거에는 검색 출처 URL을 붙이고, 실행 불가한
  아이디어(지원 안 되는 연산자·필드)는 제안하지 않도록 지시한다.
- 모델은 먼저 `validate_strategy_yaml`로 자기 제안을 검증하고 통과한 것만 제출하도록 지시한다.
  application은 제출 시 다시 검증한다(모델을 믿지 않는다).
- 검색 결과는 이 기능의 유일한 비신뢰 입력이다. 도구는 전부 읽기 전용이고 문서 변경은 사용자
  클릭 + compile 검증을 거치므로 인젝션의 영향 범위는 제안 내용에 한정된다. 화면 표면은 D7 렌더
  안전 규칙이 막는다.

### D9. 상태 소유권

| 상태 | 소유자 |
|---|---|
| 공급자 프로파일·활성 여부 | `assistant_sqlite` (application port 뒤) |
| 비밀 | `secrets_local` |
| 세션·턴·메시지·이벤트 이력(sequence) | `assistant_sqlite` |
| 진행 중 턴 레지스트리·취소 신호 | `application/assistant_chat`의 `AssistantTurnRunner`(프로세스 내, 단일 워커 전제) |
| 도구 정의·프롬프트·검증 규칙, 턴 상한의 **값**(라운드·검색·토큰·타임아웃) | `application/assistant_chat`(도구 이름·스키마 상수는 `domain/assistant`) |
| 턴 상한의 **집행**(라운드·검색 횟수·토큰 예산·잘림) | 각 `llm_*` adapter(루프 주인). 타임아웃만 `AssistantTurnRunner` |
| 공급자별 요청 형식·스트리밍·검색 도구 켜기·예외→코드 매핑 | 각 `llm_*` adapter |
| 사이드바 열림·폭·현재 세션 id·제안 카드의 기준 텍스트·마지막 반영 sequence | frontend local UI state |
| 제안 적용 결과(문서 텍스트) | edit-strategy 편집기(전체 범위 교체, 업그레이드 적용과 같은 예외 경로) |

## 3. Non-goals

- 자동 적용·자동 백테스트·자동 매매. 모든 문서 변경은 사용자의 "적용" 클릭.
- Claude Agent SDK·Managed Agents. 이 기능은 Messages/Responses API 위의 자체 도구 루프다.
- 자체 웹 검색 adapter(후속), 백테스트 실행 도구(후속, application 도구로 추가).
- 다중 사용자·권한·다중 워커. 로컬 단일 사용자 도구다(전제는 1절·D3·D9가 같은 문장을 가리킨다).
- 채팅으로 문서를 부분 편집(레시피 단계 수정 등). v1은 전체 제안 적용만.
- 마크다운·HTML 렌더. 모델 텍스트는 평문이다.

## 4. Phase와 PR

| Phase | 목표 | PR |
|---|---|---|
| A | backend: 도메인·포트·프로파일(A-01), 채팅·턴 러너(A-02), 저장 adapter(A-03), HTTP·SSE·bootstrap·OpenAPI·SDK(A-04), Anthropic(A-05), OpenAI(A-06), 프롬프트·fixture(A-07) | A-01 ~ A-07 |
| B | frontend: 설정 화면(B-01), entity·SSE(B-02), 사이드바 feature(B-03), IDE 슬롯·제안 적용(B-04), e2e·문서(B-05) | B-01 ~ B-05 |

## 5. 완료 정의

1. 설정에서 Claude 프로파일을 키로 등록하면 연결 테스트가 통과하고 활성으로 표시된다. Codex도 같다.
2. 전략 화면 우측 사이드바에서 "요즘 KRX에서 통할 만한 모멘텀 전략 하나 만들어 줘"를 보내면
   검색 활동이 보이고, 제안 카드가 뜨며, "문서에 적용"이 YAML을 바꾸고 compile "검증 통과"가 뜬다.
   "적용 후 백테스트"가 실행 페이지로 간다. 새로고침해도 진행 중 턴의 이벤트를 이어 받는다.
3. 키가 응답·로그·DB·OpenAPI·`Failure.message` 어디에도 평문으로 나오지 않는다(테스트로 고정).
4. `anthropic`·`openai` import가 두 adapter 밖에 없다(architecture 테스트).
5. 공급자 adapter 없이(가짜 공급자) application·HTTP·frontend 테스트가 전부 돈다.

## 6. 테스트

- backend: 가짜 `LlmProviderPort`로 도구 루프·제안 검증·재시도·취소·타임아웃·상한·실패 이벤트; 턴
  러너 스레드·sequence·중복 턴 거부; 프로파일 서비스 probe 필수·base_url 규칙; secrets_local 권한(플랫폼
  분기)·경로 거부; sqlite 세션·턴·이벤트 저장; HTTP SSE 계약(이벤트 순서·id·재개); architecture import
  게이트; 비밀 평문 검사(응답·로그·DB 덤프·`Failure.message`); 실제 SDK adapter는 SDK 응답 객체를 흉내
  낸 단위 테스트(서버 도구 오류 객체 분기 포함) + `RUN_LLM_LIVE=1`일 때만 도는 실연결 smoke.
- frontend: SSE 리더 재개(재연결 후 중복 sequence가 두 번 반영되지 않음)·리듀서 property test; 설정
  섹션 MSW(생성·테스트·활성·삭제·거부 사유·설치 안 됨); 사이드바 스트리밍·제안 카드·적용 전 확인
  (변경된 문서에서 덮어쓰면 적용되고 undo 한 번으로 복원)·렌더 안전(javascript: URL은 링크가 아님);
  e2e(MSW로 공급자 응답 고정): 설정 등록 → 사이드바 제안 → 적용 → 검증 통과 → 백테스트 페이지,
  새로고침 재개, 취소.

## 7. 롤백

Phase A는 새 노드·새 라우트 추가라 PR 단위 revert 가능. Phase B는 feature 모듈 추가·슬롯 연결이라
사이드바를 숨기면 기존 화면으로 돌아간다.
