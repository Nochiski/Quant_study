"""AI 어시스턴트 라우트의 wire 계약 (설계 spec D6).

application·domain 값 타입에는 **판별자(discriminator)가 없다.** `ChatEvent`는 공통 상위 타입
없이 9개 dataclass의 합집합이고, 그 설계는 파이썬 소비자가 `isinstance`로 전부를 다루도록
강제하려는 것이다(spec D2). 그런데 JSON 한 줄을 받은 브라우저는 `isinstance`를 쓸 수 없으므로,
타입 이름을 값으로 실어 주는 것은 **전송 계층의 일**이다. 그래서 `type` 리터럴과 그 합집합
메타데이터를 이 파일이 소유한다(`_execution_error_contract.py`와 같은 관례).

비밀은 **요청 전용**이다. `CreateProviderProfileRequest.secret`만 키를 받고, 응답 모델 어디에도
평문이 들어갈 필드가 없다. 목록·상세는 `secret_tail`(꼬리 4자리)만 보여 주며 그 값은
`ProviderProfileService`가 만든다. OpenAPI에서도 `writeOnly: true`로 표시해, 생성 SDK가 응답
타입에 키 필드를 만들지 않게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field

from strategy_workbench.application.assistant_chat.facade.profiles import (
    ProviderAvailability,
    ProviderProfileSummary,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    ChatRole,
    Done,
    Failure,
    FailureCode,
    ProbeFailure,
    ProbeResult,
    Proposal,
    ProposalCompileResult,
    ProviderKind,
    SearchActivity,
    SequencedEvent,
    Source,
    StrategyProposal,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResultSummary,
    Turn,
    TurnStatus,
    Usage,
)

from ._execution_error_contract import RequestValidationResponse

__all__ = [
    "ASSISTANT_EVENT_NAME",
    "Assistant409Response",
    "Assistant422Response",
    "AssistantBaseUrlRejectedDetail",
    "AssistantDocumentRefInvalidDetail",
    "AssistantEventEnvelopeView",
    "AssistantEventView",
    "AssistantNoActiveProviderDetail",
    "AssistantNoRunningTurnDetail",
    "AssistantNotFoundDetail",
    "AssistantNotFoundResponse",
    "AssistantProbeFailedDetail",
    "AssistantProviderNotInstalledDetail",
    "AssistantSecretMissingDetail",
    "AssistantTurnInProgressDetail",
    "ChatMessageView",
    "CreateProviderProfileRequest",
    "CreateSessionRequest",
    "DocumentRefView",
    "ProbeResultView",
    "ProviderKindView",
    "ProviderProfileView",
    "ProvidersView",
    "SessionHistoryView",
    "SessionView",
    "StartTurnRequest",
    "TurnAcceptedView",
    "TurnContextPayload",
    "TurnView",
    "event_envelope_view",
    "message_view",
    "probe_result_view",
    "profile_view",
    "providers_view",
    "turn_view",
]

# SSE 프레임의 `event:` 이름. 백테스트 스트림이 `progress` 하나를 쓰는 것과 같은 프레이밍이다
# (spec D6). 이벤트 종류는 `event:` 줄이 아니라 payload의 `type`이 말한다 — 이름을 9개로 쪼개면
# 클라이언트가 리스너를 9번 달아야 하고, 새 이벤트를 추가할 때마다 프론트가 깨진다.
ASSISTANT_EVENT_NAME = "assistant"


# -- 공급자 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderKindView:
    """설정 화면이 "설치 필요"를 그리는 데 필요한 사실."""

    kind: ProviderKind
    installed: bool
    default_model: str | None


@dataclass(frozen=True)
class ProviderProfileView:
    """저장된 프로파일 하나. 키 자리에는 꼬리 4자리뿐이고, 짧은 키는 그마저 `null`이다."""

    profile_id: str
    kind: ProviderKind
    label: str
    model: str
    base_url: str | None
    created_at: datetime
    active: bool
    secret_tail: str | None


@dataclass(frozen=True)
class ProvidersView:
    kinds: tuple[ProviderKindView, ...]
    profiles: tuple[ProviderProfileView, ...]


@dataclass(frozen=True)
class CreateProviderProfileRequest:
    """프로파일 생성 요청. `secret`은 이 방향으로만 흐른다."""

    kind: ProviderKind
    label: str
    secret: Annotated[
        str,
        Field(
            min_length=1,
            json_schema_extra={"writeOnly": True},
            description="공급자 API 키. 요청 전용이며 어떤 응답에도 실리지 않는다.",
        ),
    ]
    model: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class ProbeResultView:
    """연결 테스트 결과. `message`는 `failure`에서 유도된 고정 문구다(spec D2)."""

    ok: bool
    message: str
    latency_ms: int | None
    failure: ProbeFailure | None


# -- 세션·턴 --------------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentRefView:
    """세션이 붙은 문서. 저장된 전략과 초안 중 정확히 하나다(application이 검증한다)."""

    strategy_id: str | None = None
    revision: int | None = None
    draft_id: str | None = None


@dataclass(frozen=True)
class CreateSessionRequest:
    document_ref: DocumentRefView
    title: str = ""


@dataclass(frozen=True)
class SessionView:
    session_id: str
    document_ref: DocumentRefView
    provider_profile_id: str
    created_at: datetime
    title: str


@dataclass(frozen=True)
class ChatMessageView:
    role: ChatRole
    text: str
    created_at: datetime


@dataclass(frozen=True)
class TurnView:
    turn_id: str
    session_id: str
    status: TurnStatus
    accepted_sequence: int
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class TurnAcceptedView:
    """202 응답. `accepted_sequence`를 그대로 `after_sequence`로 써서 스트림을 연다."""

    turn_id: str
    session_id: str
    status: TurnStatus
    accepted_sequence: int
    started_at: datetime


@dataclass(frozen=True)
class TurnContextPayload:
    """한 턴이 보는 문서 상태. 서버가 문서를 들지 않으므로 요청마다 실려 온다(spec D7)."""

    source_text: str
    source_format: str = "yaml"
    # reason: 실행 설정(시장·기간·유니버스·수수료)은 schema 1.2에서 타입이 정해진다. 그때까지
    # 어시스턴트는 이 값을 읽어 프롬프트에 싣기만 하고 해석하지 않는다.
    environment: dict[str, Any] | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class StartTurnRequest:
    text: Annotated[str, Field(min_length=1)]
    context: TurnContextPayload


# -- 이벤트 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceView:
    title: str
    url: str


@dataclass(frozen=True)
class ProposalDiagnosticView:
    code: str
    pointer: str
    message: str
    severity: str


@dataclass(frozen=True)
class ProposalCompileView:
    ok: bool
    spec_hash: str | None
    diagnostics: tuple[ProposalDiagnosticView, ...]


@dataclass(frozen=True)
class StrategyProposalView:
    title: str
    summary: str
    rationale: str
    sources: tuple[SourceView, ...]
    source_text: str
    source_format: Literal["yaml"]
    compile: ProposalCompileView


@dataclass(frozen=True)
class TextDeltaView:
    type: Literal["text_delta"]
    text: str


@dataclass(frozen=True)
class ThinkingSummaryView:
    type: Literal["thinking_summary"]
    text: str


@dataclass(frozen=True)
class ToolCallView:
    type: Literal["tool_call"]
    call_id: str
    name: str
    # reason: 모델이 보낸 열린 JSON 객체다. 도구마다 스키마가 달라 DTO로 좁힐 수 없다.
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultSummaryView:
    type: Literal["tool_result"]
    call_id: str
    name: str
    ok: bool
    summary: str


@dataclass(frozen=True)
class SearchActivityView:
    type: Literal["search_activity"]
    query: str
    sources: tuple[SourceView, ...]


@dataclass(frozen=True)
class ProposalView:
    type: Literal["proposal"]
    proposal: StrategyProposalView


@dataclass(frozen=True)
class UsageView:
    type: Literal["usage"]
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class DoneView:
    type: Literal["done"]
    stop_reason: str


@dataclass(frozen=True)
class FailureView:
    type: Literal["failure"]
    code: FailureCode
    message: str


AssistantEventView: TypeAlias = Annotated[
    TextDeltaView
    | ThinkingSummaryView
    | ToolCallView
    | ToolResultSummaryView
    | SearchActivityView
    | ProposalView
    | UsageView
    | DoneView
    | FailureView,
    Field(discriminator="type"),
]


@dataclass(frozen=True)
class AssistantEventEnvelopeView:
    """SSE 프레임 하나의 payload. `sequence`는 `id:` 줄과 같은 값이다."""

    sequence: int
    turn_id: str
    event: AssistantEventView


@dataclass(frozen=True)
class SessionHistoryView:
    """사이드바가 새로 열릴 때 한 번에 복구하는 이력.

    이벤트를 함께 싣는 이유는 spec D7의 복구 규칙 때문이다. 스트림을 열기도 전에 끝난 턴은
    `GET /sessions/{id}/events`가 409로 거절하므로, 그 턴의 이벤트를 볼 통로가 여기뿐이다.
    """

    session: SessionView
    messages: tuple[ChatMessageView, ...]
    turns: tuple[TurnView, ...]
    events: tuple[AssistantEventEnvelopeView, ...]


# -- 오류 ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AssistantProviderNotInstalledDetail:
    code: Literal["assistant.provider_not_installed"]
    message: str


@dataclass(frozen=True)
class AssistantNoActiveProviderDetail:
    code: Literal["assistant.no_active_provider"]
    message: str


@dataclass(frozen=True)
class AssistantProbeFailedDetail:
    """연결 테스트 실패. `message`는 `failure` 열거값이 정하는 고정 문구뿐이다.

    SDK 예외 문자열을 넣지 않는 것이 핵심이다. 공급자 인증 오류 본문은 키 조각을 그대로 담는
    일이 흔하고, 이 응답은 설정 화면에 그대로 뿌려진다(spec D2).
    """

    code: Literal["assistant.probe_failed"]
    message: str
    failure: ProbeFailure


@dataclass(frozen=True)
class AssistantBaseUrlRejectedDetail:
    code: Literal["assistant.base_url_rejected"]
    message: str


@dataclass(frozen=True)
class AssistantSecretMissingDetail:
    """프로파일은 있는데 키 파일에 그 키가 없다.

    spec D6이 적어 둔 네 코드 밖이지만, 이 상태는 사용자가 키 파일을 지우거나 다른 기기에서
    DB만 복사해 오면 실제로 생긴다. 500으로 떨어뜨리면 화면이 "알 수 없는 오류"만 보여 주고,
    다른 코드로 뭉개면 "키를 다시 넣으세요"라는 조치를 안내할 수 없다.
    """

    code: Literal["assistant.provider_secret_missing"]
    message: str


@dataclass(frozen=True)
class AssistantDocumentRefInvalidDetail:
    """`document_ref`가 "저장된 전략과 초안 중 정확히 하나" 규칙을 어겼다.

    spec D6이 적어 둔 네 코드 밖이지만, 세션 목록 조회는 사이드바가 열릴 때마다 타는 경로라
    비거나 둘 다 채워진 참조가 실전에서 들어온다. 규칙을 판정하는 곳은 application의
    `DocumentRef`이고 여기서는 그 거절을 옮기기만 한다.
    """

    code: Literal["assistant.document_ref_invalid"]
    message: str


AssistantUnprocessableDetail: TypeAlias = Annotated[
    AssistantProviderNotInstalledDetail
    | AssistantNoActiveProviderDetail
    | AssistantProbeFailedDetail
    | AssistantBaseUrlRejectedDetail
    | AssistantSecretMissingDetail
    | AssistantDocumentRefInvalidDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class AssistantUnprocessableResponse:
    detail: AssistantUnprocessableDetail


Assistant422Response: TypeAlias = AssistantUnprocessableResponse | RequestValidationResponse


@dataclass(frozen=True)
class AssistantTurnInProgressDetail:
    """세션에 이미 도는 턴이 있다.

    `turn_id`는 **조회 시점에 이미 종료 상태일 수 있다.** 러너는 종료 상태를 저장한 뒤에
    세션 슬롯을 풀기 때문에, 그 짧은 창에 도착한 시작 요청이 방금 끝난 턴의 id를 받는다.
    오차 방향을 "아직 바쁘다" 쪽으로 고정한 결과이므로, 이 409는 영구 거절이 아니라 잠깐
    뒤 다시 시도하면 되는 충돌이다.
    """

    code: Literal["assistant.turn_in_progress"]
    message: str
    turn_id: Annotated[
        str,
        Field(description=("충돌한 턴. 조회 시점에 이미 종료 상태일 수 있으므로 짧게 재시도한다.")),
    ]


@dataclass(frozen=True)
class AssistantNoRunningTurnDetail:
    code: Literal["assistant.no_running_turn"]
    message: str


AssistantConflictDetail: TypeAlias = Annotated[
    AssistantTurnInProgressDetail | AssistantNoRunningTurnDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class Assistant409Response:
    detail: AssistantConflictDetail


@dataclass(frozen=True)
class AssistantNotFoundDetail:
    code: Literal[
        "assistant.session.not_found",
        "assistant.turn.not_found",
        "assistant.provider.not_found",
    ]
    message: str


@dataclass(frozen=True)
class AssistantNotFoundResponse:
    detail: AssistantNotFoundDetail


# -- 값 타입 → wire 변환 ----------------------------------------------------------------------


def providers_view(
    kinds: tuple[ProviderAvailability, ...],
    profiles: tuple[ProviderProfileSummary, ...],
) -> ProvidersView:
    return ProvidersView(
        kinds=tuple(
            ProviderKindView(
                kind=item.kind, installed=item.installed, default_model=item.default_model
            )
            for item in kinds
        ),
        profiles=tuple(profile_view(item) for item in profiles),
    )


def profile_view(summary: ProviderProfileSummary) -> ProviderProfileView:
    profile = summary.profile
    return ProviderProfileView(
        profile_id=profile.profile_id,
        kind=profile.kind,
        label=profile.label,
        model=profile.model,
        base_url=profile.base_url,
        created_at=profile.created_at,
        active=profile.active,
        secret_tail=summary.secret_tail,
    )


def probe_result_view(result: ProbeResult) -> ProbeResultView:
    return ProbeResultView(
        ok=result.ok,
        message=result.message,
        latency_ms=result.latency_ms,
        failure=result.failure,
    )


def message_view(message: ChatMessage) -> ChatMessageView:
    return ChatMessageView(role=message.role, text=message.text, created_at=message.created_at)


def turn_view(turn: Turn) -> TurnView:
    return TurnView(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        status=turn.status,
        accepted_sequence=turn.accepted_sequence,
        started_at=turn.started_at,
        finished_at=turn.finished_at,
    )


def event_envelope_view(stored: SequencedEvent) -> AssistantEventEnvelopeView:
    return AssistantEventEnvelopeView(
        sequence=stored.sequence,
        turn_id=stored.turn_id,
        event=_event_view(stored.event),
    )


def _event_view(event: ChatEvent) -> AssistantEventView:
    """도메인 이벤트 하나에 전송용 `type` 태그를 붙인다.

    `match`가 모든 갈래를 덮는지 타입 체커가 검사하도록 마지막 `case _`를 두지 않는다. 새
    `ChatEvent`가 생기면 여기서 pyright가 먼저 막는다.
    """
    match event:
        case TextDelta():
            return TextDeltaView(type="text_delta", text=event.text)
        case ThinkingSummary():
            return ThinkingSummaryView(type="thinking_summary", text=event.text)
        case ToolCall():
            return ToolCallView(
                type="tool_call",
                call_id=event.call_id,
                name=event.name,
                arguments=dict(event.arguments),
            )
        case ToolResultSummary():
            return ToolResultSummaryView(
                type="tool_result",
                call_id=event.call_id,
                name=event.name,
                ok=event.ok,
                summary=event.summary,
            )
        case SearchActivity():
            return SearchActivityView(
                type="search_activity",
                query=event.query,
                sources=_sources_view(event.sources),
            )
        case Proposal():
            return ProposalView(type="proposal", proposal=_proposal_view(event.proposal))
        case Usage():
            return UsageView(
                type="usage",
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
            )
        case Done():
            return DoneView(type="done", stop_reason=event.stop_reason)
        case Failure():
            return FailureView(type="failure", code=event.code, message=event.message)


def _proposal_view(proposal: StrategyProposal) -> StrategyProposalView:
    return StrategyProposalView(
        title=proposal.title,
        summary=proposal.summary,
        rationale=proposal.rationale,
        sources=_sources_view(proposal.sources),
        source_text=proposal.source_text,
        source_format=proposal.source_format,
        compile=_compile_view(proposal.compile),
    )


def _compile_view(result: ProposalCompileResult) -> ProposalCompileView:
    return ProposalCompileView(
        ok=result.ok,
        spec_hash=result.spec_hash,
        diagnostics=tuple(
            ProposalDiagnosticView(
                code=item.code,
                pointer=item.pointer,
                message=item.message,
                severity=item.severity,
            )
            for item in result.diagnostics
        ),
    )


def _sources_view(sources: tuple[Source, ...]) -> tuple[SourceView, ...]:
    return tuple(SourceView(title=item.title, url=item.url) for item in sources)
