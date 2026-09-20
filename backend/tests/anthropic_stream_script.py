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

from anthropic.lib.streaming import ParsedContentBlockStopEvent, TextEvent
from anthropic.types import (
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
from anthropic.types.parsed_message import ParsedContentBlock, ParsedTextBlock
from anthropic.types.stop_reason import StopReason
from anthropic.types.web_search_tool_result_error_code import WebSearchToolResultErrorCode

from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (
    AnthropicMessagesClient,
)

__all__ = [
    "CallScript",
    "ScriptedMessagesClient",
    "StreamPayload",
    "RecordingClientFactory",
    "final_message",
    "search_error_stop",
    "search_result_stop",
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


def final_message(
    *,
    stop_reason: StopReason = "end_turn",
    content: list[ParsedContentBlock[None]] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
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
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@dataclass
class CallScript:
    """공급자 호출 한 번의 대본. `error`가 있으면 그 호출은 예외로 끝난다."""

    events: tuple[StreamStep, ...] = ()
    message: ParsedMessage[None] | None = None
    error: Exception | None = None


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
