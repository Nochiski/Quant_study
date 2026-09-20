"""채팅 턴 유스케이스: 도구 루프와 제안 검증 (설계 spec D3).

## 큐가 있는 이유

공급자 adapter는 `execute_tool` **콜백**으로 도구를 실행한다. 콜백은 제너레이터가 아니므로
이벤트를 yield할 수 없는데, 도구 실행 중에 생긴 사실(제안이 접수됐다, 도구 결과가 이랬다)은
화면에 나가야 한다. 그래서 콜백은 `deque`에 이벤트를 넣고, 서비스는 adapter가 흘리는 이벤트
사이사이에 큐를 비워 내보낸다. 결과적으로 `Proposal`은 그 도구의 `ToolResultSummary`보다 먼저
나간다.

## 무엇을 집행하고 무엇을 넘기기만 하는가

이 서비스가 직접 끊는 것은 **`propose_strategy` 재시도 3회**(`PROPOSAL_INVALID`)와 **취소**
(`CANCELLED`)뿐이다. 라운드 상한·검색 횟수·토큰 예산은 값만 `TurnRequest`에 실어 보내고 집행은
adapter가 한다(spec D3). 루프를 도는 주인이 adapter이고 `TurnRequest`는 루프 시작 전에 한 번
넘어가므로, 서비스가 도중에 호출당 상한을 낮추거나 라운드를 세어 끊을 통로가 없다. 타임아웃은
`AssistantTurnRunner`가 집행한다. `Usage`는 여기서 누적해 턴 끝에 기록만 한다.

## Failure 우선순위

한 턴에서 **턴 상태가 되는 Failure는 먼저 확정된 하나뿐이다**(spec D3). 뒤이어 들어오는 Failure는
이벤트로 저장되지만 상태를 바꾸지 않는다. 그 판정은 이벤트의 소유자인 `AssistantTurnRunner`가
하고, 여기서는 사유를 이벤트로 내보내기만 한다.

**`Failure.message`에 SDK 예외 문자열이나 응답 본문을 그대로 넣지 않는다**(spec D2). 공급자
예외 메시지에는 요청 URL·헤더·본문 일부가 섞여 들어올 수 있고, 그중에 API 키가 있을 수 있다.
`Failure(PROVIDER)`는 예외 **타입 이름**까지만 적고, 진단이 더 필요하면 로컬 로그에 남긴다.

## 이벤트 영속화는 여기가 아니다

`send`는 이벤트를 흘리기만 하고 저장하지 않는다. sequence를 붙여 저장소에 남기는 것은
`AssistantTurnRunner`의 일이다(턴이 이벤트의 소유자다). 여기서 저장하는 것은 사용자·assistant
메시지뿐이다.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from strategy_workbench.domain.assistant.facade.models import (
    DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL,
    DEFAULT_MAX_SEARCH_USES,
    DEFAULT_MAX_TOOL_ROUNDS,
    DEFAULT_MAX_TURN_OUTPUT_TOKENS,
    ChatEvent,
    ChatMessage,
    ChatRole,
    Failure,
    FailureCode,
    Proposal,
    ProviderKind,
    ProviderProfile,
    ResearchCapability,
    Source,
    StrategyProposal,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolResultSummary,
    TurnRequest,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import ASSISTANT_TOOLS, PROPOSE_STRATEGY

from ._context import AssistantContextBuilder
from ._models import ChatSession, DocumentRef, TurnContext, compile_payload
from ._profiles import ProviderNotInstalledError, ProviderProfileService
from .ports.outgoing.chat_sessions import ChatSessionRepository
from .ports.outgoing.llm_provider import LlmProviderPort
from .ports.outgoing.provider_secrets import ProviderSecretStore
from .ports.outgoing.strategy_compiler import StrategyCompilerPort

__all__ = ["AssistantChatService", "NoActiveProviderError"]

logger = logging.getLogger(__name__)

_SUMMARY_LIMIT = 200

# 다른 상한 기본값은 `domain/assistant/_models.py`가 소유한다(그 값들은 `TurnRequest`로 adapter에
# 나가는 계약이다). 제안 재시도는 adapter로 나가지 않고 이 서비스만 집행하는 규칙이라 여기가
# owner다.
DEFAULT_MAX_PROPOSAL_ATTEMPTS = 3


class NoActiveProviderError(RuntimeError):
    """활성 공급자 프로파일이 없어 턴을 시작할 수 없다."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"no active provider profile — {detail}; connect a provider in settings and activate it"
        )


def _never_cancelled() -> bool:
    return False


@dataclass
class _TurnState:
    """한 턴 동안만 사는 가변 상태. 콜백과 스트림 루프가 같이 읽고 쓴다."""

    proposal_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stop: FailureCode | None = None
    stop_message: str = ""
    queue: deque[ChatEvent] = field(default_factory=deque)

    def drain(self) -> Iterator[ChatEvent]:
        while self.queue:
            yield self.queue.popleft()


class AssistantChatService:
    def __init__(
        self,
        sessions: ChatSessionRepository,
        profiles: ProviderProfileService,
        secrets: ProviderSecretStore,
        providers: Mapping[ProviderKind, LlmProviderPort],
        compiler: StrategyCompilerPort,
        context_builder: AssistantContextBuilder,
        *,
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        max_proposal_attempts: int = DEFAULT_MAX_PROPOSAL_ATTEMPTS,
        max_search_uses: int = DEFAULT_MAX_SEARCH_USES,
        max_output_tokens_per_call: int = DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL,
        max_turn_output_tokens: int = DEFAULT_MAX_TURN_OUTPUT_TOKENS,
    ) -> None:
        self._sessions = sessions
        self._profiles = profiles
        self._secrets = secrets
        self._providers = providers
        self._compiler = compiler
        self._context_builder = context_builder
        self._now = now
        self._new_id = new_id
        self._max_tool_rounds = max_tool_rounds
        self._max_proposal_attempts = max_proposal_attempts
        self._max_search_uses = max_search_uses
        self._max_output_tokens_per_call = max_output_tokens_per_call
        self._max_turn_output_tokens = max_turn_output_tokens

    # -- 세션 ---------------------------------------------------------------------------------

    def create_session(self, document_ref: DocumentRef, *, title: str) -> ChatSession:
        profile = self._profiles.active()
        if profile is None:
            raise NoActiveProviderError(f"document_ref={document_ref!r}")
        return self._sessions.create(
            ChatSession(
                session_id=self._new_id(),
                document_ref=document_ref,
                provider_profile_id=profile.profile_id,
                created_at=self._now(),
                title=title,
            )
        )

    def get(self, session_id: str) -> ChatSession:
        return self._sessions.get(session_id)

    def list_for_document(self, document_ref: DocumentRef) -> tuple[ChatSession, ...]:
        return self._sessions.list_for_document(document_ref)

    # -- 턴 -----------------------------------------------------------------------------------

    def send(
        self,
        session_id: str,
        text: str,
        context: TurnContext,
        *,
        cancelled: Callable[[], bool] = _never_cancelled,
    ) -> Iterator[ChatEvent]:
        """사용자 메시지를 보내고 이벤트를 흘린다.

        세션·프로파일·비밀 확인과 사용자 메시지 저장은 호출 시점에 끝낸다(제너레이터 본문에
        두면 소비자가 순회를 시작할 때까지 오류가 숨는다).
        """
        session = self._sessions.get(session_id)
        profile = self._profiles.active()
        if profile is None:
            raise NoActiveProviderError(f"session_id={session_id!r}")
        provider = self._providers.get(profile.kind)
        if provider is None:
            raise ProviderNotInstalledError(profile.kind)
        secret = self._secrets.get(profile.profile_id)
        self._sessions.append_message(
            session.session_id,
            ChatMessage(role=ChatRole.USER, text=text, created_at=self._now()),
        )
        request = TurnRequest(
            system=self._context_builder.system_prompt(),
            messages=self._sessions.messages(session.session_id),
            tools=ASSISTANT_TOOLS,
            research=frozenset({ResearchCapability.WEB_SEARCH}),
            max_tool_rounds=self._max_tool_rounds,
            max_search_uses=self._max_search_uses,
            max_output_tokens_per_call=self._max_output_tokens_per_call,
            max_turn_output_tokens=self._max_turn_output_tokens,
        )
        return self._stream(session, profile, provider, secret, request, context, cancelled)

    def _stream(
        self,
        session: ChatSession,
        profile: ProviderProfile,
        provider: LlmProviderPort,
        secret: str,
        request: TurnRequest,
        context: TurnContext,
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        state = _TurnState()
        text_parts: list[str] = []

        def execute_tool(call: ToolCall) -> ToolResult:
            # 도구 실행은 우리 코드다. 여기서 난 예외가 바깥 `except`까지 올라가면 진단이
            # `Failure(PROVIDER)`로 잘못 찍히고 턴도 죽는다. 도구 오류로 흡수해 모델이 고치게 한다.
            try:
                result = self._run_tool(call, context, state, session.session_id, cancelled)
            except Exception as error:
                logger.warning(
                    "assistant tool failed — session_id=%s tool=%s error_type=%s",
                    session.session_id,
                    call.name,
                    type(error).__name__,
                )
                result = ToolResult(
                    call_id=call.call_id,
                    ok=False,
                    content=(
                        f"도구 실행이 실패했습니다 — name={call.name!r} "
                        f"error_type={type(error).__name__}"
                    ),
                )
            state.queue.append(
                ToolResultSummary(
                    call_id=call.call_id,
                    name=call.name,
                    ok=result.ok,
                    summary=_summarise(result),
                )
            )
            return result

        try:
            try:
                for event in provider.stream_turn(
                    secret, profile, request, execute_tool, cancelled
                ):
                    # 도구가 큐에 넣은 이벤트를 먼저 내보낸다.
                    yield from state.drain()
                    if isinstance(event, TextDelta):
                        text_parts.append(event.text)
                    if isinstance(event, Usage):
                        state.input_tokens += event.input_tokens
                        state.output_tokens += event.output_tokens
                    yield event
                    # 종료 판정은 이벤트를 처리한 **뒤**에 한다. 도구가 세운 사유든 취소든
                    # 마찬가지다. 공급자가 이미 만들어 낸 조각은 화면에도 assistant 메시지에도
                    # 남아야 한다(spec D3: 이미 스트리밍된 텍스트는 보존한다). 앞에서 보면 손에
                    # 든 이벤트 하나가 통째로 사라지고, 같은 규칙이 경로에 따라 달라진다.
                    if state.stop is not None:
                        break
                    if cancelled():
                        state.stop = FailureCode.CANCELLED
                        state.stop_message = (
                            f"사용자가 턴을 취소했습니다 — session_id={session.session_id}"
                        )
                        break
            except Exception as error:
                # 예외 전문에는 요청 헤더·본문 일부(키 접두사 포함)가 섞여 들어올 수 있다. 완료 정의
                # 3이 "키가 로그에도 평문으로 나오지 않는다"를 요구하므로 traceback도 남기지 않는다
                # (`logger.exception`은 예외의 str()을 그대로 기록한다).
                logger.warning(
                    "assistant provider call failed — kind=%s model=%s session_id=%s error_type=%s",
                    profile.kind.value,
                    profile.model,
                    session.session_id,
                    type(error).__name__,
                )
                state.stop = FailureCode.PROVIDER
                state.stop_message = (
                    "공급자 호출이 실패했습니다 — "
                    f"kind={profile.kind.value} model={profile.model} "
                    f"error_type={type(error).__name__}"
                )
            yield from state.drain()
            if state.stop is not None:
                yield Failure(code=state.stop, message=state.stop_message)
        finally:
            # 외부 호출의 사용량은 항상 남긴다(.claude/rules/error-messages.md 로깅 가이드).
            # 예산 집행은 adapter가 하므로 여기서는 집계만 한다.
            logger.info(
                "assistant turn finished — session_id=%s kind=%s model=%s "
                "input_tokens=%d output_tokens=%d stop=%s",
                session.session_id,
                profile.kind.value,
                profile.model,
                state.input_tokens,
                state.output_tokens,
                state.stop.value if state.stop is not None else "stream_end",
            )
            if text_parts:
                self._sessions.append_message(
                    session.session_id,
                    ChatMessage(
                        role=ChatRole.ASSISTANT,
                        text="".join(text_parts),
                        created_at=self._now(),
                    ),
                )

    # -- 도구 ---------------------------------------------------------------------------------

    def _run_tool(
        self,
        call: ToolCall,
        context: TurnContext,
        state: _TurnState,
        session_id: str,
        cancelled: Callable[[], bool],
    ) -> ToolResult:
        if cancelled():
            state.stop = FailureCode.CANCELLED
            state.stop_message = f"사용자가 턴을 취소했습니다 — session_id={session_id}"
            return ToolResult(call_id=call.call_id, ok=False, content=state.stop_message)
        # 라운드 수는 세지 않는다. 상한 집행은 루프의 주인인 adapter 몫이다(spec D3).
        if call.name == PROPOSE_STRATEGY:
            return self._propose(call, state, session_id)
        return self._context_builder.tool_result(call, context)

    def _propose(self, call: ToolCall, state: _TurnState, session_id: str) -> ToolResult:
        """제안을 다시 검증한다. 모델이 "검증했다"고 말해도 믿지 않는다(spec D8)."""
        source_text = call.arguments.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            return self._reject_proposal(
                call,
                state,
                session_id,
                "source_text 인자에 전략 문서 YAML 원문 전체를 넣어 다시 제출하세요 — "
                f"received={type(source_text).__name__}",
            )
        outcome = self._compiler.compile(source_text)
        if not outcome.ok:
            return self._reject_proposal(
                call,
                state,
                session_id,
                json.dumps(compile_payload(outcome), ensure_ascii=False),
            )
        proposal = StrategyProposal(
            title=_text_of(call.arguments.get("title")),
            summary=_text_of(call.arguments.get("summary")),
            rationale=_text_of(call.arguments.get("rationale")),
            sources=_sources_of(call.arguments.get("sources")),
            source_text=source_text,
            source_format="yaml",
            compile=outcome,
        )
        state.queue.append(Proposal(proposal=proposal))
        # spec D3은 "3회 **연속** 실패"다. 성공하면 연속이 끊긴다.
        state.proposal_attempts = 0
        return ToolResult(
            call_id=call.call_id,
            ok=True,
            content="제안이 접수되었습니다. 사용자가 문서에 적용할 수 있습니다.",
        )

    def _reject_proposal(
        self, call: ToolCall, state: _TurnState, session_id: str, content: str
    ) -> ToolResult:
        state.proposal_attempts += 1
        if state.proposal_attempts >= self._max_proposal_attempts:
            state.stop = FailureCode.PROPOSAL_INVALID
            state.stop_message = (
                f"제안이 {state.proposal_attempts}회 연속 검증에 실패해 턴을 종료했습니다 — "
                f"session_id={session_id} attempts={state.proposal_attempts} "
                f"max_attempts={self._max_proposal_attempts}"
            )
        return ToolResult(call_id=call.call_id, ok=False, content=content)


def _summarise(result: ToolResult) -> str:
    if len(result.content) <= _SUMMARY_LIMIT:
        return result.content
    return f"{result.content[:_SUMMARY_LIMIT]}… (총 {len(result.content)}자)"


def _text_of(value: object) -> str:
    return value if isinstance(value, str) else ""


def _sources_of(value: object) -> tuple[Source, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(
        Source(title=_text_of(entry.get("title")), url=_text_of(entry.get("url")))
        for entry in value
        if isinstance(entry, Mapping)
    )
