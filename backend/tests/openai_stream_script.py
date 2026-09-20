"""OpenAI adapter 테스트용 스트림 스크립트 (A-06).

실제 호출은 하지 않는다. 대신 `client.responses`가 흘렸을 이벤트 열을 미리 적어 두고, adapter가
그것을 어떻게 옮기고 어떤 상한을 집행하는지만 본다.

Responses API는 최종 응답을 종료 이벤트(`response.completed`/`incomplete`/`failed`)에 실어 보내므로
Anthropic 픽스처의 `get_final_message()` 자리에 `final_event(...)`가 온다 — 대본의 마지막 이벤트다.

픽스처는 **SDK 실제 타입**(`Response`, `ResponseTextDeltaEvent`, `ResponseFunctionWebSearch` …)으로
만든다. 손으로 흉내 낸 객체를 쓰면 SDK 모양이 바뀌었을 때 테스트만 초록으로 남는다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Literal

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseError,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseFunctionWebSearch,
    ResponseIncludable,
    ResponseIncompleteEvent,
    ResponseInputItemParam,
    ResponseOutputItem,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDoneEvent,
    ResponseStatus,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
    ResponseUsage,
    ToolParam,
)
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_function_web_search import (
    ActionOpenPage,
    ActionSearch,
    ActionSearchSource,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from openai.types.shared_params import Reasoning

from strategy_workbench.adapters.outbound.llm_openai.facade.provider import OpenAiResponsesClient

__all__ = [
    "CallScript",
    "RecordingClientFactory",
    "ResponsePayload",
    "ScriptedResponsesClient",
    "error_event",
    "final_event",
    "function_call",
    "message_item",
    "open_page_done",
    "reasoning_item",
    "reasoning_summary",
    "refusal_item",
    "response_of",
    "search_done",
    "text_delta",
    "web_search_item",
]


def text_delta(text: str) -> ResponseTextDeltaEvent:
    """본문 텍스트 조각 하나."""
    return ResponseTextDeltaEvent(
        type="response.output_text.delta",
        content_index=0,
        delta=text,
        item_id="msg-1",
        logprobs=[],
        output_index=0,
        sequence_number=0,
    )


def reasoning_summary(text: str) -> ResponseReasoningSummaryTextDoneEvent:
    """사고 요약 블록이 끝나는 이벤트. `reasoning.summary`를 켜야 온다."""
    return ResponseReasoningSummaryTextDoneEvent(
        type="response.reasoning_summary_text.done",
        item_id="rs-1",
        output_index=0,
        sequence_number=0,
        summary_index=0,
        text=text,
    )


def search_done(
    call_id: str,
    query: str,
    sources: Sequence[str] = (),
    *,
    status: Literal["completed", "failed"] = "completed",
) -> ResponseOutputItemDoneEvent:
    """`web_search_call` 항목이 끝나는 이벤트. `status="failed"`가 검색 오류 분기다."""
    return ResponseOutputItemDoneEvent(
        type="response.output_item.done",
        output_index=0,
        sequence_number=0,
        item=ResponseFunctionWebSearch(
            type="web_search_call",
            id=call_id,
            status=status,
            action=ActionSearch(
                type="search",
                query=query,
                sources=[ActionSearchSource(type="url", url=url) for url in sources],
            ),
        ),
    )


def open_page_done(call_id: str, url: str) -> ResponseOutputItemDoneEvent:
    """검색이 아니라 이미 찾은 페이지를 여는 후속 행동. 검색 횟수로 세지 않는다."""
    return ResponseOutputItemDoneEvent(
        type="response.output_item.done",
        output_index=0,
        sequence_number=0,
        item=ResponseFunctionWebSearch(
            type="web_search_call",
            id=call_id,
            status="completed",
            action=ActionOpenPage(type="open_page", url=url),
        ),
    )


def web_search_item(
    call_id: str, query: str, sources: Sequence[str] = ()
) -> ResponseFunctionWebSearch:
    """응답 `output`에 실리는 `web_search_call` 항목.

    실제 응답은 같은 항목을 스트림 이벤트와 `output` 양쪽에 담는다. `_as_input_items`가 그것을
    다음 요청의 `input`으로 되돌리므로, 도구를 뺀 호출에 이 항목이 실리는 조합이 생긴다.
    """
    return ResponseFunctionWebSearch(
        type="web_search_call",
        id=call_id,
        status="completed",
        action=ActionSearch(
            type="search",
            query=query,
            sources=[ActionSearchSource(type="url", url=url) for url in sources],
        ),
    )


def error_event(code: str, message: str) -> ResponseErrorEvent:
    """스트림 도중의 오류 이벤트."""
    return ResponseErrorEvent(type="error", code=code, message=message, sequence_number=0)


def function_call(call_id: str, name: str, arguments: str) -> ResponseFunctionToolCall:
    """모델이 우리 도구를 부른 출력 항목. `arguments`는 JSON **문자열**이다."""
    return ResponseFunctionToolCall(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
        id=f"fc-{call_id}",
        status="completed",
    )


def message_item(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        type="message",
        id="msg-1",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def refusal_item(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        type="message",
        id="msg-1",
        role="assistant",
        status="completed",
        content=[ResponseOutputRefusal(type="refusal", refusal=text)],
    )


def reasoning_item(
    item_id: str = "rs-1", *, encrypted_content: str = "enc"
) -> ResponseReasoningItem:
    """추론 항목. 다음 라운드에 그대로 되돌려 보내야 사고가 이어진다."""
    return ResponseReasoningItem(
        type="reasoning",
        id=item_id,
        summary=[],
        encrypted_content=encrypted_content,
        status="completed",
    )


def response_of(
    *,
    status: ResponseStatus = "completed",
    output: Sequence[ResponseOutputItem] = (),
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    incomplete_reason: Literal["max_output_tokens", "max_messages", "content_filter", "steered"]
    | None = None,
    error_code: Literal["server_error", "rate_limit_exceeded"] | None = None,
    model: str = "gpt-6-astra",
) -> Response:
    """한 번의 호출이 끝나며 오는 최종 응답."""
    return Response(
        id="resp-1",
        object="response",
        created_at=0.0,
        model=model,
        status=status,
        output=list(output) if output else [message_item("답변")],
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        incomplete_details=(
            IncompleteDetails(reason=incomplete_reason) if incomplete_reason is not None else None
        ),
        error=(
            ResponseError(code=error_code, message="provider said so")
            if error_code is not None
            else None
        ),
        usage=ResponseUsage(
            input_tokens=input_tokens,
            input_tokens_details=InputTokensDetails(
                cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens
            ),
            output_tokens=output_tokens,
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            total_tokens=input_tokens + output_tokens,
        ),
    )


def final_event(response: Response) -> ResponseStreamEvent:
    """응답 상태에 맞는 종료 이벤트. 대본의 마지막에 온다."""
    if response.status == "incomplete":
        return ResponseIncompleteEvent(
            type="response.incomplete", response=response, sequence_number=0
        )
    if response.status == "failed":
        return ResponseFailedEvent(type="response.failed", response=response, sequence_number=0)
    return ResponseCompletedEvent(type="response.completed", response=response, sequence_number=0)


@dataclass
class CallScript:
    """공급자 호출 한 번의 대본. `error`가 있으면 그 호출은 예외로 끝난다."""

    events: tuple[ResponseStreamEvent, ...] = ()
    error: Exception | None = None


@dataclass
class ResponsePayload:
    """adapter가 보낸 인자 한 벌. 테스트가 도구 목록·상한·입력 누적을 여기서 본다."""

    include: list[ResponseIncludable]
    input: list[ResponseInputItemParam]
    instructions: str
    max_output_tokens: int
    model: str
    reasoning: Reasoning
    store: bool
    tools: list[ToolParam]


class _ScriptedStream:
    def __init__(self, script: CallScript) -> None:
        self._script = script

    def __iter__(self) -> Iterator[ResponseStreamEvent]:
        yield from self._script.events

    def __enter__(self) -> _ScriptedStream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


class ScriptedResponsesClient:
    """`OpenAiResponsesClient` 가짜 구현. 대본을 순서대로 내준다."""

    def __init__(
        self,
        *calls: CallScript,
        create_result: Response | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self._calls = list(calls)
        self._create_result = create_result
        self._create_error = create_error
        self.payloads: list[ResponsePayload] = []
        self.create_payloads: list[tuple[int, str]] = []
        self.create_store_flags: list[bool] = []

    def stream_response(
        self,
        *,
        include: list[ResponseIncludable],
        input: list[ResponseInputItemParam],
        instructions: str,
        max_output_tokens: int,
        model: str,
        reasoning: Reasoning,
        store: bool,
        tools: list[ToolParam],
    ) -> _ScriptedStream:
        self.payloads.append(
            ResponsePayload(
                include=list(include),
                # 루프가 같은 리스트에 이어 붙이므로 호출 시점 스냅샷을 떠 둔다.
                input=list(input),
                instructions=instructions,
                max_output_tokens=max_output_tokens,
                model=model,
                reasoning=reasoning,
                store=store,
                tools=list(tools),
            )
        )
        if not self._calls:
            raise AssertionError(f"대본보다 호출이 많다 — calls={len(self.payloads)} model={model}")
        script = self._calls.pop(0)
        # SDK는 요청을 `create` 호출에서 보낸다. 네트워크·인증 오류도 스트림을 돌기 전에 난다.
        if script.error is not None:
            raise script.error
        return _ScriptedStream(script)

    def create(self, *, input: str, max_output_tokens: int, model: str, store: bool) -> Response:
        self.create_payloads.append((max_output_tokens, model))
        self.create_store_flags.append(store)
        if self._create_error is not None:
            raise self._create_error
        if self._create_result is not None:
            return self._create_result
        return response_of(model=model)


class RecordingClientFactory:
    """`OpenAiClientFactory` 자리에 꽂는 팩토리. 받은 비밀·base_url을 기록한다."""

    def __init__(self, client: ScriptedResponsesClient) -> None:
        self._client = client
        self.seen: list[tuple[str, str | None]] = []

    def __call__(self, secret: str, base_url: str | None) -> OpenAiResponsesClient:
        self.seen.append((secret, base_url))
        return self._client
