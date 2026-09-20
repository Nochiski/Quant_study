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
| 턴 토큰 예산 | `max_tokens = min(호출당, 남은 예산)`, 잔량 < 최소 호출 | `TOKEN_BUDGET_EXCEEDED` |
| 검색 횟수 | 턴 누적을 세어 호출마다 `max_uses`를 줄이고, 0이면 도구를 뺀다 | (도구 없음) |

**검색은 턴 누적이다.** SDK의 `max_uses`는 호출당 한도라, 한 번 계산해 모든 호출에 같은 값을
보내면 라운드가 12번 도는 턴이 예산의 12배를 쓴다. 검색은 과금 대상이고 spec D9는 집행을
adapter에 맡겼으므로, 여기서 `server_tool_use` 블록을 세어 남은 횟수를 호출마다 다시 계산한다.
같은 `TurnRequest` 값이 Anthropic과 OpenAI에서 다른 상한을 뜻하면 안 된다.

`stop_reason == "max_tokens"`는 예산 때문에 줄였든 호출당 상한 때문이든 `OUTPUT_TRUNCATED`다.
예산 소진은 "다음 호출을 할 토큰이 남지 않았다"일 때만 쓴다 — 두 사유가 같은 상황을 가리키면
이력에서 무엇이 턴을 끊었는지 구분할 수 없다. 경계에서 이 구분이 뒤집히지 않게 최소 호출
크기(`MIN_CALL_OUTPUT_TOKENS`)를 둔다. 남은 예산이 그보다 작으면 호출하지 않는다 — 1토큰짜리
호출은 거의 확실히 `max_tokens`로 끝나 진짜 사유(예산 소진)를 `OUTPUT_TRUNCATED`로 가린다.

## 취소

`cancelled()`는 **호출 사이·이벤트 사이·도구 실행 사이** 세 곳에서 본다. 확인되면 그 자리에서
`Failure(CANCELLED)`를 내고 스트림을 끝낸다. 이미 흘려보낸 텍스트는 되돌리지 않는다
(spec D3: 이미 스트리밍된 텍스트는 assistant 메시지로 보존한다).

취소로 끊은 호출도 그때까지의 `Usage`를 먼저 내보낸다. 취소해도 그 시점까지의 출력 토큰은
과금되고, 내보내지 않으면 세션 집계가 취소 턴을 0으로 본다. 이때 `get_final_message()`를 부르면
**안 된다** — 그건 스트림을 끝까지 읽으므로 멈추려던 응답을 오히려 전부 받아 온다.
`current_message_snapshot`은 남은 이벤트를 읽지 않는다.

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
    ResearchCapability,
    SearchActivity,
    Source,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResult,
    TurnRequest,
    Usage,
)

from ._client import (
    AnthropicMessagesClient,
    AnthropicMessageStream,
    StreamEvent,
    TurnMessage,
)
from ._failures import failure_for
from ._payload import (
    OUTPUT_CONFIG,
    THINKING,
    WEB_SEARCH_TOOL_NAME,
    build_messages,
    build_system,
    build_tools,
)

__all__ = ["MAX_PAUSE_RESUMES", "MIN_CALL_OUTPUT_TOKENS", "stream_turn"]

logger = logging.getLogger(__name__)

# `pause_turn` 재개 횟수 상한. 서버 도구가 반복 한도에 닿을 때마다 재개하므로 상한이 없으면
# 루프가 끝나지 않을 수 있다. 라운드 상한과 따로 세는 이유는 재개가 우리 도구를 부른 것이
# 아니어서 "도구 라운드"로 세면 사용자가 허락한 라운드를 서버 도구가 먹어 버리기 때문이다.
MAX_PAUSE_RESUMES = 5

# 의미 있는 호출 하나의 최소 출력 토큰. 남은 예산이 이보다 작으면 호출하지 않고 예산 소진으로
# 끝낸다. 값 자체는 A-07 실측으로 확정한다(spec D3: 기본값은 실측 뒤 확정).
MIN_CALL_OUTPUT_TOKENS = 256

# `Done`으로 흘려보내도 되는 정상 종료 사유. SDK가 종류를 늘렸을 때 새 **실패성** 사유가
# 화면에 "정상 종료"로 보이지 않게 화이트리스트로 둔다.
_NORMAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})

_CANCELLED_MESSAGE = "턴이 취소되어 공급자 호출을 중단했습니다"


@dataclass(frozen=True)
class _CallOutcome:
    """한 번의 공급자 호출 결과. 취소로 끊겼으면 최종 메시지가 없다."""

    message: TurnMessage | None


class _SearchTrace:
    """턴 하나의 검색 기록. 질의를 결과 블록에 이어 붙이고 사용 횟수를 센다.

    `server_tool_use` 블록에 질의가, 뒤따르는 `web_search_tool_result` 블록에 결과가 실려 온다.
    둘은 별개의 content block이라 질의를 기억해 두지 않으면 `SearchActivity`에 넣을 질의가 없다.

    **호출 하나가 아니라 턴 하나를 산다.** 검색 예산은 턴 단위이고, 호출마다 새로 만들면 셀
    대상이 사라진다.
    """

    def __init__(self) -> None:
        self._queries: dict[str, str] = {}

    @property
    def uses(self) -> int:
        """이 턴에서 모델이 검색을 부른 횟수."""
        return len(self._queries)

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
    system = build_system(request)
    messages = build_messages(request)
    search = _SearchTrace()
    spent_output_tokens = 0
    tool_rounds = 0
    pause_resumes = 0

    while True:
        if cancelled():
            yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
            return

        remaining = request.max_turn_output_tokens - spent_output_tokens
        if remaining < MIN_CALL_OUTPUT_TOKENS:
            yield Failure(
                code=FailureCode.TOKEN_BUDGET_EXCEEDED,
                message=(
                    "턴 출력 토큰 예산이 다음 호출에 모자랍니다 — "
                    f"spent_output_tokens={spent_output_tokens} "
                    f"budget={request.max_turn_output_tokens} remaining={remaining} "
                    f"min_call={MIN_CALL_OUTPUT_TOKENS} tool_rounds={tool_rounds}"
                ),
            )
            return
        max_tokens = min(request.max_output_tokens_per_call, remaining)
        # 검색 예산은 턴 누적이다. 남은 횟수를 호출마다 다시 계산한다(모듈 docstring 표).
        remaining_search_uses = max(0, request.max_search_uses - search.uses)
        if remaining_search_uses == 0 and ResearchCapability.WEB_SEARCH in request.research:
            logger.info(
                "anthropic web search budget spent — model=%s max_search_uses=%d "
                "tool_rounds=%d (dropping the tool for the rest of the turn)",
                model,
                request.max_search_uses,
                tool_rounds,
            )
        tools = build_tools(request, remaining_search_uses=remaining_search_uses)

        try:
            outcome = yield from _stream_once(
                client,
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools,
                cancelled=cancelled,
                search=search,
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
            if stop_reason is not None and stop_reason not in _NORMAL_STOP_REASONS:
                # SDK가 종류를 늘렸다. 모르는 사유를 `Done`으로 흘리면 실패가 정상 종료로 보인다.
                yield Failure(
                    code=FailureCode.PROVIDER,
                    message=(
                        "공급자가 알 수 없는 종료 사유를 보냈습니다 — "
                        f"kind=anthropic model={model} stop_reason={stop_reason} "
                        f"tool_rounds={tool_rounds}"
                    ),
                )
                return
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
    search: _SearchTrace,
) -> Generator[ChatEvent, None, _CallOutcome]:
    """공급자를 한 번 부르고 스트림을 흘린다. 반환값은 최종 메시지(또는 취소)다."""
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
            yield from _events_from(event, search)
            # 취소 확인은 이벤트를 **내보낸 뒤**에 한다. 앞에서 보면 공급자가 이미 만들어 낸
            # 조각 하나가 통째로 사라진다.
            if cancelled():
                yield from _usage_so_far(stream)
                yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
                return _CallOutcome(message=None)
        return _CallOutcome(message=stream.get_final_message())


def _usage_so_far(stream: AnthropicMessageStream) -> Iterator[Usage]:
    """취소 시점까지의 사용량. 읽지 못하면 아무것도 내지 않는다.

    취소해도 그때까지의 출력 토큰은 과금되므로 집계에서 빠지면 안 된다. 다만 이건 부가
    정보이고, 여기서 터져 취소 자체가 실패하면 그게 더 나쁘다. `message_start`를 보기 전에
    취소되면 스냅샷 자체가 없다.
    """
    try:
        snapshot = stream.current_message_snapshot
    except Exception as error:  # reason: 스냅샷 부재를 SDK가 assert로 알려 타입으로 잡히지 않는다
        logger.info(
            "anthropic usage snapshot unavailable at cancellation — error_type=%s",
            type(error).__name__,
        )
        return
    yield Usage(
        input_tokens=snapshot.usage.input_tokens,
        output_tokens=snapshot.usage.output_tokens,
    )


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
