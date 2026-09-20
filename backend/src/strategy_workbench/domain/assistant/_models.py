"""AI 어시스턴트 순수 타입 (설계 spec D2).

공급자 SDK·HTTP·저장소를 모르는 값 타입만 둔다. 공급자 adapter는 여기 타입을 자기 형식으로
옮기고, application은 여기 타입으로 도구 루프와 제안 검증을 돌린다.

실패는 예외가 아니라 값으로 나른다(`.claude/rules/python.md` errors-as-values). `ProbeResult`는
연결 테스트 결과를, `Failure`는 턴이 끝난 이유를, `ProposalCompileResult`는 제안 YAML의 검증
결과를 각각 사유 enum과 함께 담는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, TypeAlias

__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL",
    "DEFAULT_MAX_SEARCH_USES",
    "DEFAULT_MAX_TOOL_ROUNDS",
    "DEFAULT_MAX_TURN_OUTPUT_TOKENS",
    "DEFAULT_TURN_TIMEOUT_SECONDS",
    "ChatEvent",
    "ChatMessage",
    "ChatRole",
    "Done",
    "Failure",
    "FailureCode",
    "ProbeFailure",
    "ProbeResult",
    "Proposal",
    "ProposalCompileResult",
    "ProposalDiagnostic",
    "ProviderKind",
    "ProviderProfile",
    "ResearchCapability",
    "SearchActivity",
    "SequencedEvent",
    "Source",
    "StrategyProposal",
    "TextDelta",
    "ThinkingSummary",
    "ToolCall",
    "ToolResult",
    "ToolResultSummary",
    "ToolSpec",
    "Turn",
    "TurnRequest",
    "TurnStatus",
    "Usage",
]

# 턴의 상한 기본값 (설계 spec D3). 이 파일이 숫자의 유일한 소유자다. 서비스·러너·adapter가 각자
# 리터럴을 들면 한쪽만 바뀌었을 때 조용히 어긋난다.
#
# 값을 정하는 쪽과 집행하는 쪽은 다르다. 라운드·검색·토큰 상한은 application이 값을 정해
# `TurnRequest`에 싣고, 실제로 세고 끊는 것은 호출 루프의 주인인 공급자 adapter다. 타임아웃만
# `AssistantTurnRunner`가 집행한다(spec D3 "턴의 상한과 종료").
DEFAULT_MAX_TOOL_ROUNDS = 12
DEFAULT_MAX_SEARCH_USES = 8
DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL = 16_000
DEFAULT_MAX_TURN_OUTPUT_TOKENS = 64_000
DEFAULT_TURN_TIMEOUT_SECONDS = 300.0


class ProviderKind(StrEnum):
    """지원 공급자. 값은 저장·전송 계약이므로 화면 이름(Claude/Codex)과 분리한다."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class ProbeFailure(StrEnum):
    """연결 테스트가 실패한 사유.

    화면이 "키를 확인하세요"와 "잠시 후 다시"를 구분하려면 필요하다.
    """

    AUTH = "auth"
    MODEL_NOT_FOUND = "model_not_found"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


# 사유별 고정 문구. 연결 테스트 결과로 사람에게 보여 줄 문장은 이 표가 전부다.
_PROBE_MESSAGES: Mapping[ProbeFailure, str] = MappingProxyType(
    {
        ProbeFailure.AUTH: "API 키가 거부되었습니다. 키를 다시 확인하세요.",
        ProbeFailure.MODEL_NOT_FOUND: "모델을 찾을 수 없습니다. 모델 이름을 확인하세요.",
        ProbeFailure.NETWORK: "공급자에 연결하지 못했습니다. 네트워크를 확인하세요.",
        ProbeFailure.RATE_LIMIT: "요금 한도에 걸렸습니다. 잠시 후 다시 시도하세요.",
        ProbeFailure.UNKNOWN: "연결 테스트가 알 수 없는 이유로 실패했습니다.",
    }
)
_PROBE_OK_MESSAGE = "연결을 확인했습니다."


@dataclass(frozen=True)
class ProviderProfile:
    """사용자가 등록한 공급자 연결 하나. 비밀은 여기 없고 `ProviderSecretStore`가 가진다."""

    profile_id: str
    kind: ProviderKind
    label: str
    model: str
    base_url: str | None
    created_at: datetime
    active: bool


@dataclass(frozen=True)
class ProbeResult:
    """연결 테스트 결과. `ok`가 거짓이면 `failure`가 사유를 말한다.

    **`message`는 필드가 아니라 `failure`에서 유도된다.** `Failure.message`와 같은 이유로 SDK 예외
    문자열·응답 본문이 여기 들어올 자리를 아예 없앴다(spec D2). 공급자 인증 오류 본문은
    `Incorrect API key provided: sk-proj-…`처럼 키 조각을 그대로 담는 일이 흔하고, 이 값은
    설정 화면의 "연결 테스트" 결과로 HTTP 응답 본문까지 그대로 나간다. 한 번 새면 회수 경로가
    없으므로 adapter는 사유(`ProbeFailure`)만 고르고 문장은 고르지 못한다.

    진단이 더 필요한 adapter는 자기 로컬 로그에 남긴다.
    """

    ok: bool
    latency_ms: int | None = None
    failure: ProbeFailure | None = None

    def __post_init__(self) -> None:
        if self.ok and self.failure is not None:
            raise ValueError(
                "a successful probe must not carry a failure reason — "
                f"ok={self.ok} failure={self.failure.value!r}"
            )
        if not self.ok and self.failure is None:
            raise ValueError(
                f"a failed probe must name its reason — ok={self.ok} failure=None; "
                f"expected one of {[member.value for member in ProbeFailure]}"
            )

    @property
    def message(self) -> str:
        """사유별 고정 문구. 호출자가 문장을 정하지 않는다."""
        if self.failure is None:
            return _PROBE_OK_MESSAGE
        return _PROBE_MESSAGES[self.failure]


@dataclass(frozen=True)
class ToolSpec:
    """application이 선언하고 adapter가 공급자 형식으로 옮기는 도구 정의 하나."""

    name: str
    description: str
    input_schema: Mapping[str, object]  # reason: JSON Schema는 DTO가 아니라 열린 문서다


class ResearchCapability(StrEnum):
    """턴에 켜 달라고 요청하는 리서치 능력. v1은 공급자 내장 웹 검색 하나뿐이다."""

    WEB_SEARCH = "web_search"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    role: ChatRole
    text: str
    created_at: datetime


@dataclass(frozen=True)
class TurnRequest:
    """한 턴에 공급자로 넘기는 전부. adapter는 이 값 말고 다른 컨텍스트를 만들지 않는다.

    상한 네 개는 값일 뿐이고 집행은 adapter가 한다. 루프를 도는 주인이 adapter이고 이 값은 루프가
    시작하기 전에 한 번 넘어가므로, 서비스가 도중에 호출당 상한을 낮출 통로가 없다. adapter는 자기
    루프 안에서 라운드·검색·출력 토큰을 세어 다음 호출의 `max_tokens`를 줄이거나
    `Failure(TOOL_ROUNDS_EXCEEDED)`·`Failure(TOKEN_BUDGET_EXCEEDED)`로 루프를 멈춘다.
    """

    system: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]
    research: frozenset[ResearchCapability]
    max_tool_rounds: int
    max_search_uses: int
    max_output_tokens_per_call: int
    max_turn_output_tokens: int


@dataclass(frozen=True)
class Source:
    """근거로 인용된 출처 하나."""

    title: str
    url: str


@dataclass(frozen=True)
class ProposalDiagnostic:
    """제안 YAML 검증에서 나온 진단 하나. 모델에게 돌려줘 스스로 고치게 하는 입력이다."""

    code: str
    pointer: str
    message: str
    severity: str


@dataclass(frozen=True)
class ProposalCompileResult:
    """제안 YAML을 parse·hydrate·validate한 결과. `ok`가 참일 때만 `spec_hash`가 있다."""

    ok: bool
    spec_hash: str | None
    diagnostics: tuple[ProposalDiagnostic, ...]


@dataclass(frozen=True)
class StrategyProposal:
    """모델이 제출하고 application이 검증한 전략 제안. 문서 적용은 사용자가 한다."""

    title: str
    summary: str
    rationale: str
    sources: tuple[Source, ...]
    source_text: str
    source_format: Literal["yaml"]
    compile: ProposalCompileResult


@dataclass(frozen=True)
class ToolResult:
    """도구 실행 결과. `content`는 모델에게 그대로 돌려줄 텍스트(대개 JSON 문자열)다."""

    call_id: str
    ok: bool
    content: str


class FailureCode(StrEnum):
    """턴이 정상 종료하지 못한 사유.

    `Failure.message`는 이 코드별 고정 문장에 진단 컨텍스트만 붙인다. SDK 예외 문자열·응답 본문을
    그대로 넣지 않는다(spec D2: 비밀 스크럽). 예외는 코드로만 매핑한다.
    """

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    REFUSAL = "refusal"
    PROVIDER = "provider"
    # 러너·서비스 내부 예외. 공급자 탓이 아닌 실패를 `PROVIDER`로 찍으면 이력과 화면 모두 원인을
    # 잘못 가리킨다. `message`는 다른 코드와 같이 예외 타입 이름까지만 담는다.
    INTERNAL = "internal"
    TOOL_ROUNDS_EXCEEDED = "tool_rounds_exceeded"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PROPOSAL_INVALID = "proposal_invalid"
    OUTPUT_TRUNCATED = "output_truncated"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingSummary:
    text: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]  # reason: 모델이 보낸 열린 JSON 객체다


@dataclass(frozen=True)
class ToolResultSummary:
    call_id: str
    name: str
    ok: bool
    summary: str


@dataclass(frozen=True)
class SearchActivity:
    query: str
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Proposal:
    proposal: StrategyProposal


@dataclass(frozen=True)
class Usage:
    """한 번의 공급자 호출이 쓴 토큰.

    캐시 두 칸을 따로 두는 이유는 `input_tokens`가 **캐시 읽기·쓰기를 뺀** 값이기 때문이다.
    그것만 더하면 세션 집계가 실제 청구 입력 토큰을 과소 보고한다. 값이 다른 단가로 과금되므로
    합쳐서도 안 된다 — 캐시 읽기는 싸고 쓰기는 오히려 비싸다.

    기본값 0은 호환을 위해서다. 채우지 않는 공급자 adapter와 이 필드가 생기기 전에 저장된
    이력이 그대로 성립한다.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Done:
    stop_reason: str


@dataclass(frozen=True)
class Failure:
    code: FailureCode
    message: str


# 공급자 → application → SSE로 그대로 흘러가는 이벤트. 공통 상위 타입을 두지 않는 이유는
# 소비자가 `match`/`isinstance`로 전부를 다루게 강제하기 위해서다(새 이벤트를 추가하면 소비자가
# 컴파일 단계에서 드러난다).
ChatEvent: TypeAlias = (
    TextDelta
    | ThinkingSummary
    | ToolCall
    | ToolResultSummary
    | SearchActivity
    | Proposal
    | Usage
    | Done
    | Failure
)


class TurnStatus(StrEnum):
    """진행 중 턴의 영속 상태. 종료 상태 세 개는 다시 바뀌지 않는다."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Turn:
    """한 번의 `send` 실행. 이벤트 스트림의 소유자이며 취소의 단위다.

    `accepted_sequence`는 턴이 시작되기 직전 세션의 마지막 sequence다. 클라이언트는 이 값을
    `after_sequence`로 그대로 써서, 턴 시작과 스트림 연결 사이에 생긴 이벤트를 놓치지도 않고
    이전 턴의 이벤트를 다시 받지도 않는다.
    """

    turn_id: str
    session_id: str
    status: TurnStatus
    accepted_sequence: int
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class SequencedEvent:
    """저장된 이벤트 하나. `sequence`는 세션 안에서 단조 증가하며 SSE의 `id`가 된다."""

    sequence: int
    turn_id: str
    event: ChatEvent
