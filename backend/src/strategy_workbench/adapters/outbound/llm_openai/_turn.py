"""OpenAI Responses 수동 도구 루프와 스트림 번역 (설계 spec D3/D4).

## 왜 수동 루프인가

라운드·검색·토큰 예산의 **집행자가 adapter**다(spec D3). 루프 주인이 우리여야 다음 호출의
`max_output_tokens`를 줄이거나, 라운드를 세어 끊거나, 검색 상한에 닿았을 때 도구 목록을 바꿀 수
있다. SDK가 대신 돌려 주는 루프에는 그 통로가 없다.

## 대화 상태를 서버에 맡기지 않는다

Responses API는 `previous_response_id`로 이전 응답을 이어받을 수 있다. 쓰지 않고 `input`에
누적하는 이유는 세 가지다.

- `previous_response_id`는 응답이 서버에 저장된 경우에만 유효하다. 저장을 끄거나(ZDR 조직) 저장이
  만료되면 같은 코드가 조용히 다른 동작을 한다.
- 검색 상한에 닿아 도구 목록을 바꾸는 것이 이 adapter의 핵심 규칙인데, 그러려면 매 호출이
  자기 요청을 온전히 들고 있어야 한다.
- Anthropic adapter(`llm_anthropic`)와 루프 모양이 같아진다. 두 adapter가 대칭이면 한쪽에서 찾은
  버그를 다른 쪽에서도 같은 자리에서 찾는다.

대신 추론 항목을 그대로 되돌려 보내야 모델이 앞 라운드의 사고를 잃지 않으므로 `include`에
`reasoning.encrypted_content`를 넣는다(`_payload.py`).

## 이 루프가 집행하는 상한

| 상한 | 규칙 | 실패 |
|---|---|---|
| 도구 라운드 | `max_tool_rounds` 초과 | `TOOL_ROUNDS_EXCEEDED` |
| 턴 토큰 예산 | `min(호출당 상한, 남은 예산)`, 남은 예산 0 | `TOKEN_BUDGET_EXCEEDED` |
| 검색 횟수 | 누적이 `max_search_uses`에 닿으면 도구 목록에서 제거 | (실패 아님) |

`incomplete_details.reason == "max_output_tokens"`는 예산 때문에 줄였든 호출당 상한 때문이든
`OUTPUT_TRUNCATED`다. 예산 소진은 "다음 호출을 할 토큰이 남지 않았다"일 때만 쓴다 — 두 사유가 같은
상황을 가리키면 이력에서 무엇이 턴을 끊었는지 구분할 수 없다.

## 검색 횟수 집행이 왜 여기 있는가

Anthropic은 `web_search` 도구 정의에 `max_uses`를 실어 서버가 세고, 초과를 도구 오류로 모델에게
알려 준다. OpenAI Responses의 `web_search`에는 그런 인자가 없다(SDK `WebSearchToolParam`에 필드
자체가 없다). 서버 도구라 개별 호출을 거부할 통로도 없다. 그래서 adapter가 `web_search_call`
항목을 세고, 누적이 상한에 닿으면 **다음** 호출의 도구 목록에서 `web_search`를 뺀다.

여기서 나오는 두 가지 한계는 설계상 받아들인 것이다(spec D4).

- **한 호출 안의 초과는 사후 관측만 가능하다.** 모델이 한 번의 호출에서 검색을 여러 번 하면 그
  호출이 끝난 뒤에야 개수를 안다. 상한은 "호출당"이 아니라 "다음 호출을 열 때의 누적"이다.
- **도구가 사라지는 이유를 모델에게 말해 줘야 한다.** 말없이 빼면 모델이 같은 시도를 반복한다.
  그 문장은 adapter가 쓰지 않고 프롬프트 owner인 application의 상수
  (`SEARCH_BUDGET_EXHAUSTED_NOTICE`)를 그대로 실어 나른다. 같은 문장을 `SearchActivity`로도 내
  화면이 왜 검색이 멈췄는지 보이게 한다.

## 취소

`cancelled()`는 **호출 사이·이벤트 사이·도구 실행 사이** 세 곳에서 본다. 확인되면 그 자리에서
`Failure(CANCELLED)`를 내고 스트림을 끝낸다. 이미 흘려보낸 텍스트는 되돌리지 않는다
(spec D3: 이미 스트리밍된 텍스트는 assistant 메시지로 보존한다).

## 검색 오류

`web_search_call` 항목의 `status`가 `"failed"`면 예외를 던지지 않고
`SearchActivity(query, sources=())`로 넘긴다. 모델은 같은 실패를 자기 컨텍스트에서 보고 검색 없이
답을 잇거나 다시 시도한다 — 여기서 턴을 죽이면 사용자는 답변 자체를 잃는다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import cast

from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseFunctionWebSearch,
    ResponseInputItemParam,
    ResponseOutputItem,
    ResponseStreamEvent,
    ToolParam,
)
from openai.types.responses.response_input_item_param import FunctionCallOutput

from strategy_workbench.application.assistant_chat.facade.prompt import (
    SEARCH_BUDGET_EXHAUSTED_NOTICE,
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

from ._client import OpenAiResponsesClient
from ._failures import failure_for
from ._payload import INCLUDE, REASONING, build_input, build_tools, notice_item

__all__ = ["stream_turn"]

logger = logging.getLogger(__name__)

_CANCELLED_MESSAGE = "턴이 취소되어 공급자 호출을 중단했습니다"


@dataclass(frozen=True)
class _CallOutcome:
    """한 번의 공급자 호출 결과.

    `response`가 `None`이면 이 호출로 턴이 끝났고(취소 또는 스트림 오류) 끝낸 쪽이 이미 `Failure`를
    내보냈다. 호출자는 더 볼 것 없이 돌아가면 된다.
    """

    response: Response | None
    search_uses: int


def stream_turn(
    client: OpenAiResponsesClient,
    request: TurnRequest,
    execute_tool: Callable[[ToolCall], ToolResult],
    cancelled: Callable[[], bool],
    *,
    model: str,
) -> Iterator[ChatEvent]:
    """한 턴을 흘린다. 상한 집행과 도구 루프가 전부 여기 있다."""
    input_items = build_input(request)
    wants_search = ResearchCapability.WEB_SEARCH in request.research
    spent_output_tokens = 0
    tool_rounds = 0
    search_uses = 0
    search_notice_sent = False

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
        max_output_tokens = min(request.max_output_tokens_per_call, remaining)

        search_allowed = wants_search and search_uses < request.max_search_uses
        if wants_search and not search_allowed and not search_notice_sent:
            # 도구를 빼기 **전에** 알린다. 모델은 다음 요청의 입력에서, 사용자는 지금 화면에서
            # 같은 문장을 본다. 문장의 owner는 application이다(spec D4).
            logger.info(
                "openai web search budget spent — model=%s search_uses=%d max_search_uses=%d",
                model,
                search_uses,
                request.max_search_uses,
            )
            input_items.append(notice_item(SEARCH_BUDGET_EXHAUSTED_NOTICE))
            yield SearchActivity(query=SEARCH_BUDGET_EXHAUSTED_NOTICE, sources=())
            search_notice_sent = True

        tools = build_tools(request, web_search=search_allowed)

        try:
            outcome = yield from _stream_once(
                client,
                model=model,
                max_output_tokens=max_output_tokens,
                input_items=input_items,
                instructions=request.system,
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
                "openai turn failed — model=%s tool_rounds=%d spent_output_tokens=%d error_type=%s",
                model,
                tool_rounds,
                spent_output_tokens,
                type(error).__name__,
            )
            yield failure
            return

        search_uses += outcome.search_uses
        response = outcome.response
        if response is None:
            return

        usage = response.usage
        if usage is not None:
            spent_output_tokens += usage.output_tokens
            yield Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)

        if _has_refusal(response):
            yield Failure(
                code=FailureCode.REFUSAL,
                message=f"공급자가 요청을 거절했습니다 — kind=openai model={model}",
            )
            return

        status = response.status
        if status == "incomplete":
            details = response.incomplete_details
            reason = details.reason if details is not None else None
            if reason == "max_output_tokens":
                yield Failure(
                    code=FailureCode.OUTPUT_TRUNCATED,
                    message=(
                        "답변이 출력 토큰 상한에 걸려 잘렸습니다 — "
                        f"max_output_tokens={max_output_tokens} "
                        f"per_call_limit={request.max_output_tokens_per_call} "
                        f"remaining_budget={remaining}"
                    ),
                )
                return
            yield Failure(
                code=FailureCode.PROVIDER,
                message=(
                    "공급자가 응답을 끝내지 못했습니다 — "
                    f"kind=openai model={model} incomplete_reason={reason} "
                    f"tool_rounds={tool_rounds}"
                ),
            )
            return
        if status == "failed":
            error_detail = response.error
            yield Failure(
                code=FailureCode.PROVIDER,
                message=(
                    "공급자가 응답 생성에 실패했습니다 — "
                    f"kind=openai model={model} "
                    f"error_code={error_detail.code if error_detail is not None else None} "
                    f"tool_rounds={tool_rounds}"
                ),
            )
            return

        calls = _tool_calls(response)
        if not calls:
            yield Done(stop_reason=status or "completed")
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

        input_items.extend(_as_input_items(response.output))
        for call in calls:
            if cancelled():
                yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
                return
            yield call
            result = execute_tool(call)
            input_items.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=result.call_id,
                    output=result.content,
                )
            )


def _stream_once(
    client: OpenAiResponsesClient,
    *,
    model: str,
    max_output_tokens: int,
    input_items: list[ResponseInputItemParam],
    instructions: str,
    tools: list[ToolParam],
    cancelled: Callable[[], bool],
) -> Generator[ChatEvent, None, _CallOutcome]:
    """공급자를 한 번 부르고 스트림을 흘린다. 반환값은 최종 응답(또는 이미 끝난 호출)이다."""
    final: Response | None = None
    search_uses = 0
    with client.stream_response(
        include=INCLUDE,
        input=input_items,
        instructions=instructions,
        max_output_tokens=max_output_tokens,
        model=model,
        reasoning=REASONING,
        tools=tools,
    ) as stream:
        for event in stream:
            if event.type == "error":
                # 스트림 도중의 오류 이벤트. `message`는 자유 문자열이라 코드만 인용한다.
                logger.warning(
                    "openai stream error event — model=%s error_code=%s", model, event.code
                )
                yield Failure(
                    code=FailureCode.PROVIDER,
                    message=(
                        "공급자가 스트림 도중 오류를 보냈습니다 — "
                        f"kind=openai model={model} error_code={event.code}"
                    ),
                )
                return _CallOutcome(response=None, search_uses=search_uses)

            events, searches = _events_from(event)
            yield from events
            search_uses += searches
            # 셋을 `in frozenset(...)`으로 묶지 않는다. 타입 검사기가 리터럴 비교로만 union을
            # 좁혀 주고, 좁혀지지 않으면 `event.response`가 없는 이벤트까지 같은 가지에 들어온다.
            if (
                event.type == "response.completed"
                or event.type == "response.incomplete"
                or event.type == "response.failed"
            ):
                final = event.response
            # 취소 확인은 이벤트를 **내보낸 뒤**에 한다. 앞에서 보면 공급자가 이미 만들어 낸
            # 조각 하나가 통째로 사라진다.
            if cancelled():
                yield Failure(code=FailureCode.CANCELLED, message=_CANCELLED_MESSAGE)
                return _CallOutcome(response=None, search_uses=search_uses)

    if final is None:
        # 종료 이벤트 없이 스트림이 끝나면 상한을 집행할 `usage`도 `status`도 없다. 한 번 더
        # 돌아도 같은 자리에서 끝날 뿐이라 턴을 끝낸다.
        yield Failure(
            code=FailureCode.PROVIDER,
            message=(
                "공급자 스트림이 종료 이벤트 없이 끊겼습니다 — "
                f"kind=openai model={model} max_output_tokens={max_output_tokens}"
            ),
        )
        return _CallOutcome(response=None, search_uses=search_uses)
    return _CallOutcome(response=final, search_uses=search_uses)


def _events_from(event: ResponseStreamEvent) -> tuple[list[ChatEvent], int]:
    """스트림 이벤트 하나를 화면 이벤트로 옮긴다. 두 번째 값은 이 이벤트가 쓴 검색 횟수다.

    텍스트는 델타로, 사고 요약은 블록이 끝날 때(`...summary_text.done`) 읽는다. 요약을 델타로
    흘리면 사용자에게 문장 조각이 쏟아지고, `llm_anthropic`이 사고 블록을 통째로 내보내는 것과
    모양이 달라진다.
    """
    if event.type == "response.output_text.delta":
        return [TextDelta(text=event.delta)], 0
    if event.type == "response.reasoning_summary_text.done":
        return ([ThinkingSummary(text=event.text)] if event.text else []), 0
    if event.type == "response.output_item.done" and event.item.type == "web_search_call":
        return _search_events(event.item)
    return [], 0


def _search_events(item: ResponseFunctionWebSearch) -> tuple[list[ChatEvent], int]:
    """`web_search_call` 항목 하나 → `SearchActivity`와 소비한 검색 횟수.

    `search` 행동만 검색 1회로 센다. `open_page`·`find_in_page`는 모델이 이미 찾은 결과를 읽는
    후속 행동이라 질의가 없고, 이것까지 세면 사용자가 허락한 검색 횟수를 페이지 열람이 먹는다.
    """
    action = item.action
    if action.type != "search":
        return [], 0
    query = action.query or (action.queries[0] if action.queries else "")
    if item.status == "failed":
        logger.warning("openai web search failed — call_id=%s query_length=%d", item.id, len(query))
        return [SearchActivity(query=query, sources=())], 1
    sources = tuple(
        # 검색 결과 출처는 URL만 온다(`ActionSearchSource`에 제목 필드가 없다). 화면이 빈 제목을
        # 그리지 않도록 URL을 제목 자리에도 둔다.
        Source(title=source.url, url=source.url)
        for source in (action.sources or ())
    )
    return [SearchActivity(query=query, sources=sources)], 1


def _has_refusal(response: Response) -> bool:
    """응답에 거절 블록이 있는지. 있으면 턴은 `Failure(REFUSAL)`로 끝난다."""
    for item in response.output:
        if item.type != "message":
            continue
        if any(block.type == "refusal" for block in item.content):
            return True
    return False


def _tool_calls(response: Response) -> list[ToolCall]:
    """응답에서 우리 도구 호출만 뽑는다(서버 도구는 공급자가 이미 실행했다)."""
    return [
        ToolCall(call_id=item.call_id, name=item.name, arguments=_arguments_of(item))
        for item in response.output
        if item.type == "function_call"
    ]


def _arguments_of(item: ResponseFunctionToolCall) -> Mapping[str, object]:
    """도구 인자 JSON 문자열을 dict로. 깨진 JSON은 빈 인자로 넘긴다.

    `strict: True`를 걸어도 깨진 JSON이 올 가능성이 0은 아니다(잘린 응답 등). 여기서 예외를 올리면
    사용자는 답변 자체를 잃지만, 빈 인자로 넘기면 application의 도구가 스키마 위반을 진단으로
    돌려주고 모델이 같은 라운드 안에서 고친다.
    """
    try:
        parsed = json.loads(item.arguments)
    except json.JSONDecodeError:
        logger.warning(
            "openai tool arguments were not valid JSON — name=%s call_id=%s length=%d",
            item.name,
            item.call_id,
            len(item.arguments),
        )
        return {}
    if not isinstance(parsed, Mapping):
        logger.warning(
            "openai tool arguments were not a JSON object — name=%s call_id=%s type=%s",
            item.name,
            item.call_id,
            type(parsed).__name__,
        )
        return {}
    # reason: 모델이 보낸 열린 JSON 객체다. domain `ToolCall`이 같은 타입을 쓴다.
    return cast(Mapping[str, object], parsed)


def _as_input_items(output: Iterable[ResponseOutputItem]) -> list[ResponseInputItemParam]:
    """응답 항목을 다음 요청의 입력 항목으로 되돌린다.

    항목을 dict로 옮기지 않고 SDK 객체 그대로 싣는다. 추론 항목의 `encrypted_content`처럼 우리가
    의미를 모르는 필드를 한 글자도 바꾸지 않아야 모델이 이어서 돌 수 있다. SDK 직렬화는 응답 항목
    객체를 그대로 받는다(공식 수동 루프 예제의 `input_list += response.output`과 같은 형태).
    """
    # reason: 응답 항목(`ResponseOutputItem`)과 입력 항목(`ResponseInputItemParam`)은 SDK에서 별개
    # 타입이지만 직렬화 경로는 같다. 그대로 되돌려 보내는 것이 수동 루프의 계약이라 여기서만 좁힌다.
    return cast(list[ResponseInputItemParam], list(output))
