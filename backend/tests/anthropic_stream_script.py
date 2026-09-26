"""Anthropic adapter 테스트용 스트림 스크립트 (A-05).

실제 호출은 하지 않는다. 대신 `client.messages`가 흘렸을 이벤트 열과 최종 메시지를 미리 적어
두고, adapter가 그것을 어떻게 옮기고 어떤 상한을 집행하는지만 본다.

픽스처는 **SDK 실제 타입**(`ParsedMessage`, `TextEvent`, `WebSearchToolResultBlock` …)으로 만든다.
손으로 흉내 낸 객체를 쓰면 SDK 모양이 바뀌었을 때 테스트만 초록으로 남는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Literal

import pytest

pytest.importorskip(
    "anthropic",
    reason="공급자 SDK는 optional extra `llm`이다. 미설치 환경에서는 이 모듈을 건너뛴다.",
)

from anthropic.lib.streaming import (  # noqa: E402  # reason: 위 importorskip 뒤에야 import할 수 있다
    ParsedContentBlockStopEvent,
    TextEvent,
)
from anthropic.types import (  # noqa: E402  # reason: 위와 같음
    Message,
    MessageParam,
    OutputConfigParam,
    ParsedMessage,
    ServerToolUseBlock,
    TextBlockParam,
    ThinkingBlock,
    ThinkingConfigParam,
    ToolUnionParam,
    ToolUseBlock,
    Usage,
    WebSearchResultBlock,
    WebSearchToolResultBlock,
    WebSearchToolResultError,
)
from anthropic.types.parsed_message import (  # noqa: E402  # reason: 위와 같음
    ParsedContentBlock,
    ParsedTextBlock,
)
from anthropic.types.stop_reason import StopReason  # noqa: E402  # reason: 위와 같음
from anthropic.types.web_search_tool_result_error_code import (  # noqa: E402  # reason: 위와 같음
    WebSearchToolResultErrorCode,
)

from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (  # noqa: E402  # reason: 위와 같음
    AnthropicMessagesClient,
)

__all__ = [
    "CallScript",
    "ScriptedMessagesClient",
    "StreamPayload",
    "RecordingClientFactory",
    "final_message",
    "message_with_future_stop_reason",
    "search_error_stop",
    "search_result_content",
    "search_result_stop",
    "server_tool_use_content",
    "server_tool_use_stop",
    "text_event",
    "thinking_stop",
    "tool_use_block",
]

StreamStep = TextEvent | ParsedContentBlockStopEvent[None]


def text_event(text: str, snapshot: str | None = None) -> TextEvent:
    """SDK가 흘리는 누적 텍스트 이벤트 하나."""
    return TextEvent(type="text", text=text, snapshot=snapshot if snapshot is not None else text)


def thinking_stop(thinking: str, *, index: int = 0) -> ParsedContentBlockStopEvent[None]:
    """사고 블록이 끝나는 이벤트. `display: "summarized"`면 요약 텍스트가 실려 온다."""
    return ParsedContentBlockStopEvent(
        type="content_block_stop",
        index=index,
        content_block=ThinkingBlock(type="thinking", thinking=thinking, signature="sig"),
    )


def server_tool_use_stop(
    tool_use_id: str,
    query: str,
    *,
    name: Literal["web_search", "web_fetch"] = "web_search",
    index: int = 0,
) -> ParsedContentBlockStopEvent[None]:
    """서버 도구 호출 블록이 끝나는 이벤트. 검색 질의가 여기 실린다."""
    return ParsedContentBlockStopEvent(
        type="content_block_stop",
        index=index,
        content_block=ServerToolUseBlock(
            type="server_tool_use",
            id=tool_use_id,
            name=name,
            input={"query": query},
        ),
    )


def search_result_stop(
    tool_use_id: str, sources: tuple[tuple[str, str], ...], *, index: int = 1
) -> ParsedContentBlockStopEvent[None]:
    """검색 성공 블록. `content`가 **리스트**인 쪽이다."""
    return ParsedContentBlockStopEvent(
        type="content_block_stop",
        index=index,
        content_block=WebSearchToolResultBlock(
            type="web_search_tool_result",
            tool_use_id=tool_use_id,
            content=[
                WebSearchResultBlock(
                    type="web_search_result",
                    title=title,
                    url=url,
                    encrypted_content="enc",
                    page_age=None,
                )
                for title, url in sources
            ],
        ),
    )


def search_error_stop(
    tool_use_id: str,
    error_code: WebSearchToolResultErrorCode = "max_uses_exceeded",
    *,
    index: int = 1,
) -> ParsedContentBlockStopEvent[None]:
    """검색 실패 블록. `content`가 **단일 오류 객체**인 쪽이다(spec D4 분기)."""
    return ParsedContentBlockStopEvent(
        type="content_block_stop",
        index=index,
        content_block=WebSearchToolResultBlock(
            type="web_search_tool_result",
            tool_use_id=tool_use_id,
            content=WebSearchToolResultError(
                type="web_search_tool_result_error",
                error_code=error_code,
            ),
        ),
    )


def tool_use_block(call_id: str, name: str, arguments: dict[str, object]) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=call_id, name=name, input=arguments)


def server_tool_use_content(tool_use_id: str, query: str) -> ServerToolUseBlock:
    """최종 메시지 content에 들어가는 서버 도구 호출 블록.

    스트림 이벤트(`server_tool_use_stop`)와 **다른 자리**다. 실제 `get_final_message()`는 검색을
    한 턴의 content에 이 블록과 결과 블록을 담아 돌려주고, adapter는 그것을 그대로 다음 요청의
    assistant 턴으로 되돌린다. 대본이 이 블록을 담지 않으면 실제와 다른 history가 만들어져
    "도구를 뺀 호출에 이전 검색 블록이 실린다"는 조합을 볼 수 없다.
    """
    return ServerToolUseBlock(
        type="server_tool_use", id=tool_use_id, name="web_search", input={"query": query}
    )


def search_result_content(
    tool_use_id: str, sources: tuple[tuple[str, str], ...] = ()
) -> WebSearchToolResultBlock:
    """최종 메시지 content에 들어가는 검색 결과 블록."""
    return WebSearchToolResultBlock(
        type="web_search_tool_result",
        tool_use_id=tool_use_id,
        content=[
            WebSearchResultBlock(
                type="web_search_result",
                title=title,
                url=url,
                encrypted_content="enc",
                page_age=None,
            )
            for title, url in sources
        ],
    )


def final_message(
    *,
    stop_reason: StopReason = "end_turn",
    content: list[ParsedContentBlock[None]] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    model: str = "claude-opus-5",
) -> ParsedMessage[None]:
    """한 번의 호출이 끝나며 오는 최종 메시지."""
    default_content: list[ParsedContentBlock[None]] = [ParsedTextBlock(type="text", text="답변")]
    return ParsedMessage(
        id="msg-1",
        type="message",
        role="assistant",
        model=model,
        content=content if content is not None else default_content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        ),
    )


def message_with_future_stop_reason(reason: str) -> ParsedMessage[None]:
    """SDK가 아직 모르는 종료 사유를 흉내 낸다.

    `StopReason`이 Literal이라 생성자가 막지만, 실제로 이런 값이 오는 시점은 **서버가 SDK보다
    먼저** 새 사유를 내보낼 때다. 생성 뒤 대입은 pydantic이 검증하지 않으므로(모델에
    `validate_assignment`가 없다) 그 상황을 그대로 만들 수 있다.
    """
    message = final_message()
    message.stop_reason = reason  # pyright: ignore[reportAttributeAccessIssue]  # reason: 위 docstring
    return message


@dataclass
class CallScript:
    """공급자 호출 한 번의 대본. `error`가 있으면 그 호출은 예외로 끝난다."""

    events: tuple[StreamStep, ...] = ()
    message: ParsedMessage[None] | None = None
    error: Exception | None = None
    # 취소 시점에 SDK가 들고 있을 누적 스냅샷. `None`이면 `message_start`를 보기 전에
    # 취소된 상황을 흉내 낸다(실제 SDK는 그 경우 assert로 막는다).
    snapshot: ParsedMessage[None] | None = None


@dataclass
class StreamPayload:
    """adapter가 보낸 인자 한 벌. 테스트가 캐시 breakpoint·도구·상한을 여기서 본다."""

    max_tokens: int
    model: str
    messages: list[MessageParam]
    system: list[TextBlockParam]
    tools: list[ToolUnionParam]
    thinking: ThinkingConfigParam
    output_config: OutputConfigParam


class _ScriptedStream:
    def __init__(self, script: CallScript) -> None:
        self._script = script

    def __iter__(self) -> Iterator[StreamStep]:
        yield from self._script.events

    def get_final_message(self) -> ParsedMessage[None]:
        message = self._script.message
        if message is None:
            raise AssertionError("대본에 최종 메시지가 없다 — CallScript.message를 채워라")
        return message

    @property
    def current_message_snapshot(self) -> ParsedMessage[None]:
        """SDK와 같이, 스냅샷이 없으면 `AssertionError`를 낸다.

        취소 경로가 이 예외를 삼키고 `Usage` 없이 넘어가는지 테스트가 볼 수 있어야 한다.
        """
        snapshot = self._script.snapshot
        if snapshot is None:
            raise AssertionError("아직 message_start를 보지 못했다")
        return snapshot


class _ScriptedStreamManager:
    def __init__(self, script: CallScript) -> None:
        self._script = script

    def __enter__(self) -> _ScriptedStream:
        # SDK는 `__enter__`에서 HTTP 요청을 보낸다. 네트워크·인증 오류도 여기서 난다.
        if self._script.error is not None:
            raise self._script.error
        return _ScriptedStream(self._script)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


class ScriptedMessagesClient:
    """`AnthropicMessagesClient` 가짜 구현. 대본을 순서대로 내준다."""

    def __init__(
        self,
        *calls: CallScript,
        create_result: Message | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self._calls = list(calls)
        self._create_result = create_result
        self._create_error = create_error
        self.payloads: list[StreamPayload] = []
        self.create_payloads: list[tuple[int, str]] = []

    def stream(
        self,
        *,
        max_tokens: int,
        messages: Iterable[MessageParam],
        model: str,
        output_config: OutputConfigParam,
        system: Iterable[TextBlockParam],
        thinking: ThinkingConfigParam,
        tools: Iterable[ToolUnionParam],
    ) -> _ScriptedStreamManager:
        self.payloads.append(
            StreamPayload(
                max_tokens=max_tokens,
                model=model,
                # 루프가 같은 리스트에 이어 붙이므로 호출 시점 스냅샷을 떠 둔다.
                messages=list(messages),
                system=list(system),
                tools=list(tools),
                thinking=thinking,
                output_config=output_config,
            )
        )
        if not self._calls:
            raise AssertionError(f"대본보다 호출이 많다 — calls={len(self.payloads)} model={model}")
        return _ScriptedStreamManager(self._calls.pop(0))

    def create(self, *, max_tokens: int, messages: Iterable[MessageParam], model: str) -> Message:
        self.create_payloads.append((max_tokens, model))
        list(messages)
        if self._create_error is not None:
            raise self._create_error
        if self._create_result is not None:
            return self._create_result
        return final_message(model=model)


class RecordingClientFactory:
    """`AnthropicClientFactory` 자리에 꽂는 팩토리. 받은 비밀·base_url을 기록한다."""

    def __init__(self, client: ScriptedMessagesClient) -> None:
        self._client = client
        self.seen: list[tuple[str, str | None]] = []

    def __call__(self, secret: str, base_url: str | None) -> AnthropicMessagesClient:
        self.seen.append((secret, base_url))
        return self._client
