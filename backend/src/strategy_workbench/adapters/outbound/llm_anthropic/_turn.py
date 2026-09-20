"""Anthropic 수동 도구 루프와 스트림 번역 (설계 spec D3/D4).

## 왜 수동 루프인가

SDK에는 도구 루프를 대신 돌려 주는 `tool_runner`가 있지만 쓰지 않는다. 두 가지가 맞지 않는다.

- `tool_runner`는 `pause_turn`을 스스로 재개하지 못한다. 서버 도구가 내부 반복 한도에 닿으면
  턴이 조용히 잘린 채 "정상 종료"처럼 끝난다.
- 라운드·토큰 예산의 **집행자가 adapter**다(spec D3). 루프 주인이 우리여야 다음 호출의
  `max_tokens`를 줄이거나 라운드를 세어 끊을 수 있다.

## 이 루프가 집행하는 상한

| 상한 | 규칙 | 실패 |
|---|---|---|
| 도구 라운드 | `request.max_tool_rounds` 초과 | `TOOL_ROUNDS_EXCEEDED` |
| 턴 토큰 예산 | `max_tokens = min(호출당 상한, 남은 예산)`, 남은 예산 0 | `TOKEN_BUDGET_EXCEEDED` |
| 검색 횟수 | `web_search` 도구의 `max_uses`로 서버가 집행 | (서버 도구 오류 객체) |

`stop_reason == "max_tokens"`는 예산 때문에 줄였든 호출당 상한 때문이든 `OUTPUT_TRUNCATED`다.
예산 소진은 "다음 호출을 할 토큰이 남지 않았다"일 때만 쓴다 — 두 사유가 같은 상황을 가리키면
이력에서 무엇이 턴을 끊었는지 구분할 수 없다.

## 취소

`cancelled()`는 **호출 사이·이벤트 사이·도구 실행 사이** 세 곳에서 본다. 확인되면 그 자리에서
`Failure(CANCELLED)`를 내고 스트림을 끝낸다. 이미 흘려보낸 텍스트는 되돌리지 않는다
(spec D3: 이미 스트리밍된 텍스트는 assistant 메시지로 보존한다).

## 서버 도구 오류

검색 결과 블록의 `content`는 성공이면 결과 리스트, 실패면 단일 오류 객체
(`web_search_tool_result_error`)다. 오류여도 예외를 던지지 않고 `SearchActivity(query, sources=())`
로 넘긴다. 모델은 같은 오류를 자기 컨텍스트에서 보고 검색 없이 답을 잇거나 다시 시도한다 —
여기서 턴을 죽이면 사용자는 답변 자체를 잃는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import cast

from anthropic.types import (
    ContentBlockParam,
    MessageParam,
    TextBlockParam,
    ToolResultBlockParam,
    ToolUnionParam,
    WebSearchToolResultBlock,
    WebSearchToolResultError,
)

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    Failure,
    FailureCode,
    SearchActivity,
    Source,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResult,
    TurnRequest,
    Usage,
)

from ._client import AnthropicMessagesClient, StreamEvent, TurnMessage
from ._failures import failure_for
from ._payload import (
    OUTPUT_CONFIG,
    THINKING,
    WEB_SEARCH_TOOL_NAME,
    build_messages,
    build_system,
    build_tools,
)

__all__ = ["MAX_PAUSE_RESUMES", "stream_turn"]

logger = logging.getLogger(__name__)

# `pause_turn` 재개 횟수 상한. 서버 도구가 반복 한도에 닿을 때마다 재개하므로 상한이 없으면
# 루프가 끝나지 않을 수 있다. 라운드 상한과 따로 세는 이유는 재개가 우리 도구를 부른 것이
# 아니어서 "도구 라운드"로 세면 사용자가 허락한 라운드를 서버 도구가 먹어 버리기 때문이다.
MAX_PAUSE_RESUMES = 5

_CANCELLED_MESSAGE = "턴이 취소되어 공급자 호출을 중단했습니다"


@dataclass(frozen=True)
class _CallOutcome:
    """한 번의 공급자 호출 결과. 취소로 끊겼으면 최종 메시지가 없다."""

    message: TurnMessage | None


class _SearchTrace:
    """검색 질의와 결과 블록을 이어 붙이는 자리.

    `server_tool_use` 블록에 질의가, 뒤따르는 `web_search_tool_result` 블록에 결과가 실려 온다.
    둘은 별개의 content block이라 질의를 기억해 두지 않으면 `SearchActivity`에 넣을 질의가 없다.
    """

    def __init__(self) -> None:
        self._queries: dict[str, str] = {}

    def remember(self, tool_use_id: str, query: str) -> None:
        self._queries[tool_use_id] = query

    def query_for(self, tool_use_id: str) -> str:
        return self._queries.get(tool_use_id, "")


def stream_turn(
    client: AnthropicMessagesClient,
    request: TurnRequest,
    execute_tool: Callable[[ToolCall], ToolResult],
    cancelled: Callable[[], bool],
    *,
    model: str,
) -> Iterator[ChatEvent]:
    """한 턴을 흘린다. 상한 집행과 도구 루프가 전부 여기 있다."""
    tools = build_tools(request)
    system = build_system(request)
    messages = build_messages(request)
    spent_output_tokens = 0
    tool_rounds = 0
    pause_resumes = 0

    while True:
        if cancelled():
            yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
            return

        remaining = request.max_turn_output_tokens - spent_output_tokens
        if remaining <= 0:
            yield Failure(
                code=FailureCode.TOKEN_BUDGET_EXCEEDED,
                message=(
                    "턴 출력 토큰 예산을 모두 썼습니다 — "
                    f"spent_output_tokens={spent_output_tokens} "
                    f"budget={request.max_turn_output_tokens} tool_rounds={tool_rounds}"
                ),
            )
            return
        max_tokens = min(request.max_output_tokens_per_call, remaining)

        try:
            outcome = yield from _stream_once(
                client,
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools,
                cancelled=cancelled,
            )
        except Exception as error:
            failure = failure_for(error, model=model)
            if failure is None:
                # 우리가 분류하지 못하는 예외는 그대로 올린다. application이 `PROVIDER`로 흡수하고,
                # 여기서 억지로 코드를 고르면 원인을 잘못 가리킨다.
                raise
            logger.warning(
                "anthropic turn failed — model=%s tool_rounds=%d spent_output_tokens=%d "
                "error_type=%s",
                model,
                tool_rounds,
                spent_output_tokens,
                type(error).__name__,
            )
            yield failure
            return

        message = outcome.message
        if message is None:
            return

        spent_output_tokens += message.usage.output_tokens
        yield Usage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

        stop_reason = message.stop_reason
        if stop_reason == "refusal":
            yield Failure(
                code=FailureCode.REFUSAL,
                message=f"공급자가 요청을 거절했습니다 — kind=anthropic model={model}",
            )
            return
        if stop_reason == "max_tokens":
            yield Failure(
                code=FailureCode.OUTPUT_TRUNCATED,
                message=(
                    "답변이 출력 토큰 상한에 걸려 잘렸습니다 — "
                    f"max_tokens={max_tokens} "
                    f"per_call_limit={request.max_output_tokens_per_call} "
                    f"remaining_budget={remaining}"
                ),
            )
            return
        if stop_reason == "model_context_window_exceeded":
            yield Failure(
                code=FailureCode.PROVIDER,
                message=(
                    "대화가 모델 컨텍스트 창을 넘었습니다 — "
                    f"kind=anthropic model={model} tool_rounds={tool_rounds}"
                ),
            )
            return

        if stop_reason == "pause_turn":
            pause_resumes += 1
            if pause_resumes > MAX_PAUSE_RESUMES:
                yield Failure(
                    code=FailureCode.PROVIDER,
                    message=(
                        "서버 도구가 계속 턴을 멈춰 재개 상한에 닿았습니다 — "
                        f"kind=anthropic model={model} pause_resumes={pause_resumes} "
                        f"max_pause_resumes={MAX_PAUSE_RESUMES}"
                    ),
                )
                return
            # 멈춘 assistant 턴을 그대로 붙여 다시 보내면 서버가 이어서 돈다. "계속하세요" 같은
            # 사용자 메시지를 덧붙이면 안 된다(서버가 재개 신호를 못 읽는다).
            messages.append(_assistant_turn(message))
            continue

        if stop_reason != "tool_use":
            yield Done(stop_reason=stop_reason or "end_turn")
            return

        tool_rounds += 1
        if tool_rounds > request.max_tool_rounds:
            yield Failure(
                code=FailureCode.TOOL_ROUNDS_EXCEEDED,
                message=(
                    "도구 호출 라운드 상한을 넘겨 턴을 끝냈습니다 — "
                    f"tool_rounds={tool_rounds} max_tool_rounds={request.max_tool_rounds}"
                ),
            )
            return

        calls = _tool_calls(message)
        if not calls:
            # `stop_reason`이 `tool_use`인데 호출 블록이 없으면 붙일 `tool_result`가 없어 다음
            # 요청이 400이 된다. 한 번 더 돌아도 같은 응답이 올 뿐이다.
            yield Failure(
                code=FailureCode.PROVIDER,
                message=(
                    "공급자가 도구 호출을 예고하고 호출 블록을 보내지 않았습니다 — "
                    f"kind=anthropic model={model} stop_reason={stop_reason} "
                    f"content_blocks={len(message.content)}"
                ),
            )
            return

        messages.append(_assistant_turn(message))
        results: list[ContentBlockParam] = []
        for call in calls:
            if cancelled():
                yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
                return
            yield call
            result = execute_tool(call)
            results.append(
                ToolResultBlockParam(
                    type="tool_result",
                    tool_use_id=result.call_id,
                    content=result.content,
                    is_error=not result.ok,
                )
            )
        messages.append(MessageParam(role="user", content=results))


def _stream_once(
    client: AnthropicMessagesClient,
    *,
    model: str,
    max_tokens: int,
    messages: list[MessageParam],
    system: list[TextBlockParam],
    tools: list[ToolUnionParam],
    cancelled: Callable[[], bool],
) -> Generator[ChatEvent, None, _CallOutcome]:
    """공급자를 한 번 부르고 스트림을 흘린다. 반환값은 최종 메시지(또는 취소)다."""
    trace = _SearchTrace()
    with client.stream(
        max_tokens=max_tokens,
        messages=messages,
        model=model,
        output_config=OUTPUT_CONFIG,
        system=system,
        thinking=THINKING,
        tools=tools,
    ) as stream:
        for event in stream:
            yield from _events_from(event, trace)
            # 취소 확인은 이벤트를 **내보낸 뒤**에 한다. 앞에서 보면 공급자가 이미 만들어 낸
            # 조각 하나가 통째로 사라진다.
            if cancelled():
                yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
                return _CallOutcome(message=None)
        return _CallOutcome(message=stream.get_final_message())


def _assistant_turn(message: TurnMessage) -> MessageParam:
    """응답 블록을 다음 요청의 assistant 턴으로 되돌린다.

    블록을 dict로 옮기지 않고 SDK 객체 그대로 싣는다. thinking 블록의 `signature`처럼 우리가
    의미를 모르는 필드를 한 글자도 바꾸지 않아야 공급자가 이어서 돌 수 있다. SDK 직렬화는 응답
    블록 객체를 그대로 받는다(공식 수동 루프 예제와 같은 형태).
    """
    # reason: 응답 블록(`ContentBlock`)과 요청 블록(`ContentBlockParam`)은 SDK에서 별개 타입이지만
    # 직렬화 경로는 같다. 그대로 되돌려 보내는 것이 수동 루프의 계약이라 여기서만 좁힌다.
    content = cast(Iterable[ContentBlockParam], message.content)
    return MessageParam(role="assistant", content=content)


def _tool_calls(message: TurnMessage) -> list[ToolCall]:
    """최종 메시지에서 우리 도구 호출만 뽑는다(서버 도구는 공급자가 이미 실행했다)."""
    calls: list[ToolCall] = []
    for block in message.content:
        if block.type != "tool_use":
            continue
        arguments = block.input if isinstance(block.input, Mapping) else {}
        calls.append(
            ToolCall(
                call_id=block.id,
                name=block.name,
                # reason: 모델이 보낸 열린 JSON 객체다. domain `ToolCall`이 같은 타입을 쓴다.
                arguments=cast(Mapping[str, object], arguments),
            )
        )
    return calls


def _events_from(event: StreamEvent, trace: _SearchTrace) -> list[ChatEvent]:
    """스트림 이벤트 하나를 화면 이벤트로 옮긴다. 옮길 것이 없으면 빈 리스트.

    텍스트는 누적 이벤트(`text`)로, 사고 요약과 검색 활동은 블록이 끝날 때(`content_block_stop`)
    읽는다. 원시 이벤트(`content_block_delta`)를 같이 읽으면 같은 텍스트가 두 번 나간다 — SDK는
    원시 이벤트와 누적 이벤트를 **둘 다** 흘린다.
    """
    if event.type == "text":
        return [TextDelta(text=event.text)]
    if event.type != "content_block_stop":
        return []

    block = event.content_block
    if block.type == "thinking":
        # `display: "summarized"`가 아니면 여기 텍스트가 비어 온다. 빈 요약은 내보내지 않는다.
        return [ThinkingSummary(text=block.thinking)] if block.thinking else []
    if block.type == "server_tool_use":
        if block.name == WEB_SEARCH_TOOL_NAME:
            trace.remember(block.id, _query_of(block.input))
        return []
    if block.type == "web_search_tool_result":
        return [
            SearchActivity(
                query=trace.query_for(block.tool_use_id),
                sources=_sources_of(block),
            )
        ]
    return []


def _query_of(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    query = payload.get("query")
    return query if isinstance(query, str) else ""


def _sources_of(block: WebSearchToolResultBlock) -> tuple[Source, ...]:
    """검색 결과 블록의 출처. 오류 객체면 빈 튜플이다(예외를 던지지 않는다)."""
    content = block.content
    if isinstance(content, WebSearchToolResultError):
        logger.warning(
            "anthropic web search failed — error_code=%s tool_use_id=%s",
            content.error_code,
            block.tool_use_id,
        )
        return ()
    return tuple(Source(title=result.title, url=result.url) for result in content)
