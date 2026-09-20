"""OpenAI 공급자 adapter (`adapters/outbound/llm_openai`) — A-06.

네트워크를 쓰지 않는다. `tests/openai_stream_script.py`가 SDK가 흘렸을 이벤트 열을 대신 내주고,
여기서는 adapter가 **무엇을 보내고 무엇으로 옮기며 어떤 상한을 집행하는지**만 본다. 실제 공급자
호출은 A-07의 `RUN_LLM_LIVE=1` smoke가 맡는다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence

import httpx2
import pytest

pytest.importorskip("openai", reason="backend optional extra `llm` (uv sync --extra llm)")

import openai  # noqa: E402  # reason: importorskip 이후 import

from strategy_workbench.adapters.outbound.llm_openai._adapter import (  # noqa: E402  # reason: importorskip 이후 import
    PROBE_MAX_OUTPUT_TOKENS,
)
from strategy_workbench.adapters.outbound.llm_openai._client import (  # noqa: E402  # reason: importorskip 이후 import
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    SdkResponsesClient,
)
from strategy_workbench.adapters.outbound.llm_openai._payload import (  # noqa: E402  # reason: importorskip 이후 import
    INCLUDE,
    REASONING,
    build_input,
    build_tools,
)
from strategy_workbench.adapters.outbound.llm_openai.facade.provider import (  # noqa: E402  # reason: importorskip 이후 import
    DEFAULT_MODEL,
    OpenAiLlmAdapter,
    sdk_client_factory,
)
from strategy_workbench.application.assistant_chat.facade.ports import (  # noqa: E402  # reason: importorskip 이후 import
    LlmProviderPort,
)
from strategy_workbench.application.assistant_chat.facade.prompt import (  # noqa: E402  # reason: importorskip 이후 import
    SEARCH_BUDGET_EXHAUSTED_NOTICE,
)
from strategy_workbench.domain.assistant.facade.models import (  # noqa: E402  # reason: importorskip 이후 import
    ChatEvent,
    ChatMessage,
    ChatRole,
    Done,
    Failure,
    FailureCode,
    ProbeFailure,
    ProviderKind,
    ProviderProfile,
    ResearchCapability,
    SearchActivity,
    Source,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnRequest,
    Usage,
)

from .openai_stream_script import (  # noqa: E402  # reason: importorskip 이후 import
    CallScript,
    RecordingClientFactory,
    ScriptedResponsesClient,
    error_event,
    final_event,
    function_call,
    open_page_done,
    reasoning_item,
    reasoning_summary,
    refusal_item,
    response_of,
    search_done,
    text_delta,
)

SECRET = "sk-proj-TEST-SECRET"

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"source_text": {"type": "string"}},
    "required": ["source_text"],
    "additionalProperties": False,
}

TOOLS = (
    ToolSpec(name="read_current_strategy", description="현재 문서", input_schema=_SCHEMA),
    ToolSpec(name="propose_strategy", description="제안 제출", input_schema=_SCHEMA),
)


def make_request(
    *,
    research: frozenset[ResearchCapability] = frozenset({ResearchCapability.WEB_SEARCH}),
    max_tool_rounds: int = 12,
    max_search_uses: int = 8,
    max_output_tokens_per_call: int = 16_000,
    max_turn_output_tokens: int = 64_000,
) -> TurnRequest:
    return TurnRequest(
        system="너는 전략 어시스턴트다.",
        messages=(
            ChatMessage(
                role=ChatRole.USER,
                text="모멘텀 전략을 만들어 줘",
                created_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
            ),
        ),
        tools=TOOLS,
        research=research,
        max_tool_rounds=max_tool_rounds,
        max_search_uses=max_search_uses,
        max_output_tokens_per_call=max_output_tokens_per_call,
        max_turn_output_tokens=max_turn_output_tokens,
    )


def make_profile(*, model: str = "gpt-6-astra", base_url: str | None = None) -> ProviderProfile:
    return ProviderProfile(
        profile_id="profile-1",
        kind=ProviderKind.OPENAI,
        label="Codex",
        model=model,
        base_url=base_url,
        created_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
        active=True,
    )


def run_turn(
    client: ScriptedResponsesClient,
    *,
    request: TurnRequest | None = None,
    profile: ProviderProfile | None = None,
    execute_tool: Callable[[ToolCall], ToolResult] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[ChatEvent]:
    factory = RecordingClientFactory(client)
    adapter = OpenAiLlmAdapter(client_factory=factory)
    return list(
        adapter.stream_turn(
            SECRET,
            profile or make_profile(),
            request or make_request(),
            execute_tool or (lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}")),
            cancelled,
        )
    )


def as_dicts(items: Iterable[object]) -> list[dict[str, object]]:
    """TypedDict union을 평범한 dict로 본다.

    SDK 요청 타입은 `NotRequired` 키를 가진 TypedDict union이라 `item["name"]` 같은 첨자를 타입
    검사기가 막는다. 테스트는 보낸 값만 확인하면 되므로 dict로 한 번 낮춘다.
    """
    lowered: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise AssertionError(f"dict 항목이 아니다 — type={type(item).__name__}")
        lowered.append({str(key): value for key, value in item.items()})
    return lowered


def dict_items(items: Iterable[object]) -> list[dict[str, object]]:
    """입력 항목 중 우리가 만든 dict만. 응답에서 그대로 되돌린 SDK 객체는 건너뛴다."""
    return [
        {str(key): value for key, value in item.items()}
        for item in items
        if isinstance(item, Mapping)
    ]


def tool_names(tools: Iterable[object]) -> list[object]:
    """도구 목록의 식별자. 함수 도구는 `name`, 서버 도구는 `type`으로 구분된다."""
    return [tool.get("name", tool.get("type")) for tool in as_dicts(tools)]


def failures(events: Sequence[ChatEvent]) -> list[Failure]:
    return [event for event in events if isinstance(event, Failure)]


def status_error(
    error_class: type[openai.APIStatusError], status: int, message: str
) -> openai.APIStatusError:
    """SDK가 상태 코드별로 올리는 예외를 그대로 만든다.

    베이스 `APIStatusError`로 만들면 안 된다 — SDK는 상태 코드마다 전용 하위 타입을 올리고,
    adapter의 매핑은 그 하위 타입을 본다.
    """
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return error_class(message, response=httpx2.Response(status, request=request), body=None)


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses")
    )


def one_call(*events: object) -> ScriptedResponsesClient:
    """이벤트 열 하나짜리 대본. 마지막 이벤트는 종료 이벤트여야 한다."""
    return ScriptedResponsesClient(CallScript(events=tuple(events)))  # pyright: ignore[reportArgumentType]  # reason: 픽스처 헬퍼가 SDK 이벤트 union을 그대로 받는다


# -- 도구 변환 -------------------------------------------------------------------------------


def test_tool_specs_become_strict_openai_function_tools() -> None:
    tools = as_dicts(build_tools(make_request(), web_search=False))

    assert [tool["name"] for tool in tools] == ["read_current_strategy", "propose_strategy"]
    assert {tool["type"] for tool in tools} == {"function"}
    assert {tool["strict"] for tool in tools} == {True}
    # 스키마는 domain 상수가 소유한다. adapter가 손보면 계약의 owner가 둘이 된다.
    assert tools[0]["parameters"] == _SCHEMA


def test_web_search_is_a_server_tool_without_a_use_limit_argument() -> None:
    """Anthropic의 `max_uses`에 해당하는 인자가 없다는 것이 A-06 루프 설계의 출발점이다."""
    tools = as_dicts(build_tools(make_request(max_search_uses=3), web_search=True))

    assert tools[-1] == {"type": "web_search"}


def test_no_web_search_tool_when_research_was_not_requested() -> None:
    tools = build_tools(make_request(research=frozenset()), web_search=True)

    assert "web_search" not in tool_names(tools)


def test_history_becomes_responses_input_items_in_order() -> None:
    request = make_request()
    older = ChatMessage(
        role=ChatRole.ASSISTANT,
        text="어떤 시장을 보시나요?",
        created_at=dt.datetime(2026, 9, 19, tzinfo=dt.UTC),
    )
    request = TurnRequest(
        system=request.system,
        messages=(older, *request.messages),
        tools=request.tools,
        research=request.research,
        max_tool_rounds=request.max_tool_rounds,
        max_search_uses=request.max_search_uses,
        max_output_tokens_per_call=request.max_output_tokens_per_call,
        max_turn_output_tokens=request.max_turn_output_tokens,
    )

    items = as_dicts(build_input(request))

    assert [(item["role"], item["content"]) for item in items] == [
        ("assistant", "어떤 시장을 보시나요?"),
        ("user", "모멘텀 전략을 만들어 줘"),
    ]


# -- 스트림 번역 -----------------------------------------------------------------------------


def test_stream_turn_emits_text_thinking_usage_and_done() -> None:
    client = one_call(
        reasoning_summary("모멘텀 팩터를 찾는다"),
        text_delta("모멘텀"),
        text_delta(" 전략"),
        final_event(response_of(input_tokens=120, output_tokens=42)),
    )

    events = run_turn(client)

    assert events == [
        ThinkingSummary(text="모멘텀 팩터를 찾는다"),
        TextDelta(text="모멘텀"),
        TextDelta(text=" 전략"),
        Usage(input_tokens=120, output_tokens=42),
        Done(stop_reason="completed"),
    ]


def test_an_empty_reasoning_summary_is_not_emitted() -> None:
    client = one_call(reasoning_summary(""), final_event(response_of()))

    assert not [event for event in run_turn(client) if isinstance(event, ThinkingSummary)]


def test_the_call_asks_for_high_effort_reasoning_with_a_summary() -> None:
    """`summary`를 주지 않으면 요약 이벤트가 아예 오지 않아 `ThinkingSummary`가 영원히 빈다."""
    client = one_call(final_event(response_of()))

    run_turn(client)

    assert client.payloads[0].reasoning == REASONING
    assert REASONING == {"effort": "high", "summary": "auto"}


def test_the_call_includes_search_sources_and_encrypted_reasoning() -> None:
    client = one_call(final_event(response_of()))

    run_turn(client)

    assert client.payloads[0].include == INCLUDE
    assert set(INCLUDE) == {"web_search_call.action.sources", "reasoning.encrypted_content"}


def test_the_system_prompt_travels_as_instructions_not_as_a_message() -> None:
    client = one_call(final_event(response_of()))

    run_turn(client)

    payload = client.payloads[0]
    assert payload.instructions == "너는 전략 어시스턴트다."
    assert [item["role"] for item in as_dicts(payload.input)] == ["user"]


def test_default_model_is_the_model_fixed_from_the_sdk() -> None:
    assert DEFAULT_MODEL == "gpt-6-astra"
    assert OpenAiLlmAdapter().default_model() == "gpt-6-astra"


def test_the_adapter_satisfies_the_outgoing_port() -> None:
    adapter: LlmProviderPort = OpenAiLlmAdapter()

    assert adapter.kind is ProviderKind.OPENAI


def test_the_secret_and_base_url_reach_the_client_factory_per_call() -> None:
    client = one_call(final_event(response_of()))
    factory = RecordingClientFactory(client)
    adapter = OpenAiLlmAdapter(client_factory=factory)

    list(
        adapter.stream_turn(
            SECRET,
            make_profile(base_url="https://proxy.example.com/v1"),
            make_request(),
            lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}"),
            lambda: False,
        )
    )

    assert factory.seen == [(SECRET, "https://proxy.example.com/v1")]


# -- 검색 -----------------------------------------------------------------------------------


def test_search_success_becomes_search_activity_with_sources() -> None:
    client = one_call(
        search_done("ws-1", "KRX 모멘텀", ("https://example.com/a", "https://example.com/b")),
        final_event(response_of()),
    )

    activities = [event for event in run_turn(client) if isinstance(event, SearchActivity)]

    assert activities == [
        SearchActivity(
            query="KRX 모멘텀",
            sources=(
                Source(title="https://example.com/a", url="https://example.com/a"),
                Source(title="https://example.com/b", url="https://example.com/b"),
            ),
        )
    ]


def test_a_failed_search_becomes_an_empty_search_activity_not_an_exception() -> None:
    """검색 실패로 턴을 죽이면 사용자는 답변 자체를 잃는다(spec D4 서버 도구 오류 분기)."""
    client = one_call(
        search_done("ws-1", "KRX 모멘텀", ("https://example.com/a",), status="failed"),
        text_delta("검색 없이 답한다"),
        final_event(response_of()),
    )

    events = run_turn(client)

    assert SearchActivity(query="KRX 모멘텀", sources=()) in events
    assert TextDelta(text="검색 없이 답한다") in events
    assert failures(events) == []


def test_opening_a_page_is_not_counted_as_a_search() -> None:
    request = make_request(max_search_uses=1)
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                open_page_done("ws-1", "https://example.com/a"),
                final_event(
                    response_of(output=[function_call("call-1", "read_current_strategy", "{}")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    run_turn(client, request=request)

    # 페이지 열람은 검색이 아니므로 두 번째 호출에도 `web_search`가 남아 있다.
    assert "web_search" in tool_names(client.payloads[1].tools)


def test_the_search_budget_drops_the_tool_and_tells_the_model_once() -> None:
    """spec D4 OpenAI 행: 누적이 상한에 닿으면 이후 호출의 도구 목록에서 `web_search`를 뺀다."""
    request = make_request(max_search_uses=1)
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                search_done("ws-1", "첫 검색"),
                final_event(
                    response_of(output=[function_call("call-1", "read_current_strategy", "{}")])
                ),
            )
        ),
        CallScript(
            events=(
                final_event(
                    response_of(output=[function_call("call-2", "read_current_strategy", "{}")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    events = run_turn(client, request=request)

    assert "web_search" in tool_names(client.payloads[0].tools)
    assert "web_search" not in tool_names(client.payloads[1].tools)
    assert "web_search" not in tool_names(client.payloads[2].tools)
    # 통지는 한 번뿐이다. 매 호출마다 붙이면 같은 문장이 대화에 쌓인다.
    notices = [
        item for item in dict_items(client.payloads[2].input) if item.get("role") == "developer"
    ]
    assert len(notices) == 1
    assert [event for event in events if isinstance(event, SearchActivity)][-1] == SearchActivity(
        query=SEARCH_BUDGET_EXHAUSTED_NOTICE, sources=()
    )


def test_the_search_notice_text_comes_from_the_application_prompt_owner() -> None:
    """adapter는 모델에게 가는 문장을 저술하지 않는다(spec D4·D8: 프롬프트 owner는 application)."""
    request = make_request(max_search_uses=1)
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                search_done("ws-1", "첫 검색"),
                final_event(
                    response_of(output=[function_call("call-1", "read_current_strategy", "{}")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    run_turn(client, request=request)

    notice = dict_items(client.payloads[1].input)[-1]
    assert notice["role"] == "developer"
    assert notice["content"] == [{"type": "input_text", "text": SEARCH_BUDGET_EXHAUSTED_NOTICE}]


def test_no_search_notice_when_research_was_never_requested() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(output=[function_call("call-1", "read_current_strategy", "{}")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    events = run_turn(client, request=make_request(research=frozenset(), max_search_uses=0))

    assert [event for event in events if isinstance(event, SearchActivity)] == []
    assert [
        item for item in dict_items(client.payloads[1].input) if item.get("role") == "developer"
    ] == []


# -- 도구 루프 -------------------------------------------------------------------------------


def test_tool_call_runs_the_callback_and_feeds_the_result_back() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(
                        output=[
                            reasoning_item("rs-1"),
                            function_call(
                                "call-1", "read_current_strategy", '{"source_text": "x"}'
                            ),
                        ]
                    )
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )
    seen: list[ToolCall] = []

    def execute(call: ToolCall) -> ToolResult:
        seen.append(call)
        return ToolResult(call_id=call.call_id, ok=True, content='{"ok": true}')

    events = run_turn(client, execute_tool=execute)

    assert seen == [
        ToolCall(call_id="call-1", name="read_current_strategy", arguments={"source_text": "x"})
    ]
    assert (
        ToolCall(call_id="call-1", name="read_current_strategy", arguments={"source_text": "x"})
        in events
    )

    # 두 번째 호출의 입력: 원래 질문 + 응답 항목 그대로 + 도구 결과.
    second = client.payloads[1].input
    assert [getattr(item, "type", None) or as_dicts([item])[0].get("type") for item in second] == [
        "message",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert as_dicts([second[-1]])[0] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"ok": true}',
    }


def test_the_reasoning_item_is_replayed_verbatim_so_the_model_keeps_its_chain() -> None:
    replayed = reasoning_item("rs-1", encrypted_content="enc-abc")
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(
                        output=[replayed, function_call("call-1", "read_current_strategy", "{}")]
                    )
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    run_turn(client)

    assert client.payloads[1].input[1] is replayed


def test_a_failed_tool_result_still_reaches_the_model_as_output_text() -> None:
    """Responses의 `function_call_output`에는 `is_error`가 없다. 오류는 본문 문자열로 간다."""
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(output=[function_call("call-1", "propose_strategy", "{}")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    run_turn(
        client,
        execute_tool=lambda call: ToolResult(
            call_id=call.call_id, ok=False, content="검증 실패: unknown field"
        ),
    )

    assert as_dicts([client.payloads[1].input[-1]])[0]["output"] == "검증 실패: unknown field"


def test_broken_tool_arguments_become_empty_arguments_instead_of_an_exception() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(output=[function_call("call-1", "propose_strategy", "not json")])
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )
    seen: list[ToolCall] = []

    run_turn(
        client,
        execute_tool=lambda call: (
            seen.append(call) or ToolResult(call_id=call.call_id, ok=False, content="스키마 위반")
        ),
    )

    assert seen[0].arguments == {}


def test_tool_rounds_over_the_limit_end_the_turn() -> None:
    calls = [
        CallScript(
            events=(
                final_event(
                    response_of(output=[function_call(f"call-{index}", "propose_strategy", "{}")])
                ),
            )
        )
        for index in range(3)
    ]
    client = ScriptedResponsesClient(*calls)

    events = run_turn(client, request=make_request(max_tool_rounds=2))

    assert failures(events)[0].code is FailureCode.TOOL_ROUNDS_EXCEEDED
    assert len(client.payloads) == 3


# -- 종료 사유 -------------------------------------------------------------------------------


def test_refusal_ends_the_turn_with_the_refusal_code() -> None:
    client = one_call(final_event(response_of(output=[refusal_item("그건 도울 수 없습니다")])))

    assert failures(run_turn(client))[0].code is FailureCode.REFUSAL


def test_incomplete_for_max_output_tokens_is_reported_as_truncated_output() -> None:
    client = one_call(
        text_delta("절반만 쓴 답"),
        final_event(response_of(status="incomplete", incomplete_reason="max_output_tokens")),
    )

    events = run_turn(client)

    # 잘린 텍스트는 보존하되 정상 종료로 보이지 않는다(spec D3).
    assert TextDelta(text="절반만 쓴 답") in events
    assert failures(events)[0].code is FailureCode.OUTPUT_TRUNCATED
    assert not [event for event in events if isinstance(event, Done)]


def test_other_incomplete_reasons_are_provider_failures_not_truncation() -> None:
    client = one_call(
        final_event(response_of(status="incomplete", incomplete_reason="content_filter"))
    )

    failure = failures(run_turn(client))[0]

    assert failure.code is FailureCode.PROVIDER
    assert "content_filter" in failure.message


def test_a_failed_response_is_a_provider_failure_carrying_only_the_error_code() -> None:
    client = one_call(final_event(response_of(status="failed", error_code="server_error")))

    failure = failures(run_turn(client))[0]

    assert failure.code is FailureCode.PROVIDER
    assert "server_error" in failure.message
    # `ResponseError.message`는 자유 문자열이라 인용하지 않는다.
    assert "provider said so" not in failure.message


def test_a_stream_error_event_ends_the_turn_without_an_exception() -> None:
    client = one_call(text_delta("시작"), error_event("server_error", "boom"))

    events = run_turn(client)

    assert TextDelta(text="시작") in events
    assert failures(events)[0].code is FailureCode.PROVIDER
    assert "boom" not in failures(events)[0].message


def test_a_stream_without_a_terminal_event_is_a_provider_failure() -> None:
    client = one_call(text_delta("끊긴 답"))

    failure = failures(run_turn(client))[0]

    assert failure.code is FailureCode.PROVIDER
    assert "종료 이벤트" in failure.message


# -- 토큰 예산 -------------------------------------------------------------------------------


def test_the_next_call_is_capped_by_the_remaining_turn_budget() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(
                        output=[function_call("call-1", "propose_strategy", "{}")],
                        output_tokens=900,
                    )
                ),
            )
        ),
        CallScript(events=(final_event(response_of()),)),
    )

    run_turn(
        client,
        request=make_request(max_output_tokens_per_call=600, max_turn_output_tokens=1000),
    )

    assert client.payloads[0].max_output_tokens == 600
    assert client.payloads[1].max_output_tokens == 100


def test_an_exhausted_turn_budget_stops_the_loop_before_the_next_call() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(
                        output=[function_call("call-1", "propose_strategy", "{}")],
                        output_tokens=1000,
                    )
                ),
            )
        )
    )

    events = run_turn(
        client,
        request=make_request(max_output_tokens_per_call=600, max_turn_output_tokens=1000),
    )

    assert failures(events)[0].code is FailureCode.TOKEN_BUDGET_EXCEEDED
    assert len(client.payloads) == 1


# -- 취소 -----------------------------------------------------------------------------------


def test_cancelling_before_the_first_call_never_reaches_the_provider() -> None:
    client = ScriptedResponsesClient()

    events = run_turn(client, cancelled=lambda: True)

    assert failures(events)[0].code is FailureCode.CANCELLED
    assert client.payloads == []


def test_cancelling_between_stream_events_keeps_what_was_already_streamed() -> None:
    client = one_call(text_delta("이미 흘린 답"), text_delta("두 번째"), final_event(response_of()))
    ticks = iter([False, True])

    events = run_turn(client, cancelled=lambda: next(ticks, True))

    assert events == [
        TextDelta(text="이미 흘린 답"),
        Failure(code=FailureCode.CANCELLED, message="턴이 취소되어 공급자 호출을 중단했습니다"),
    ]


def test_cancelling_between_tool_calls_stops_before_the_second_tool_runs() -> None:
    client = ScriptedResponsesClient(
        CallScript(
            events=(
                final_event(
                    response_of(
                        output=[
                            function_call("call-1", "read_current_strategy", "{}"),
                            function_call("call-2", "propose_strategy", "{}"),
                        ]
                    )
                ),
            )
        )
    )
    ran: list[str] = []
    # 루프가 `cancelled()`를 보는 자리: 호출 전 1회, 스트림 이벤트 1개 뒤 1회, 도구 호출마다 1회.
    answers = iter([False, False, False, True])

    events = run_turn(
        client,
        execute_tool=lambda call: (
            ran.append(call.call_id) or ToolResult(call_id=call.call_id, ok=True, content="{}")
        ),
        cancelled=lambda: next(answers, True),
    )

    assert ran == ["call-1"]
    assert failures(events)[-1].code is FailureCode.CANCELLED


# -- 예외 매핑 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(openai.AuthenticationError, 401, "invalid api key"), FailureCode.AUTH),
        (status_error(openai.PermissionDeniedError, 403, "forbidden"), FailureCode.AUTH),
        (status_error(openai.RateLimitError, 429, "rate limited"), FailureCode.RATE_LIMIT),
        (connection_error(), FailureCode.NETWORK),
        (status_error(openai.InternalServerError, 500, "server error"), FailureCode.PROVIDER),
        # 컨텍스트 창 초과는 Responses에서 400으로 온다. Anthropic의
        # `stop_reason == "model_context_window_exceeded"`에 대응하는 자리다.
        (
            status_error(openai.BadRequestError, 400, "context_length_exceeded"),
            FailureCode.PROVIDER,
        ),
    ],
)
def test_sdk_errors_map_to_failure_codes(error: Exception, expected: FailureCode) -> None:
    client = ScriptedResponsesClient(CallScript(error=error))

    assert failures(run_turn(client))[0].code is expected


def test_an_unknown_exception_is_left_for_the_application_to_absorb() -> None:
    client = ScriptedResponsesClient(CallScript(error=ZeroDivisionError("bug")))

    with pytest.raises(ZeroDivisionError):
        run_turn(client)


def test_a_key_inside_the_sdk_error_never_reaches_the_failure_message() -> None:
    """`Failure.message`는 SSE를 타고 화면까지 가고 이력에도 저장된다(spec D2).

    공급자 인증 오류 본문은 키 조각을 그대로 담는 일이 흔하다. 한 번 새면 회수 경로가 없다.
    """
    leaked = f"Incorrect API key provided: {SECRET}"
    client = ScriptedResponsesClient(
        CallScript(error=status_error(openai.AuthenticationError, 401, leaked))
    )

    failure = failures(run_turn(client))[0]

    assert SECRET not in failure.message
    assert "Incorrect API key provided" not in failure.message
    assert "AuthenticationError" in failure.message


def test_the_turn_log_never_quotes_the_provider_error_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """로그도 같은 규칙이다. `logger.exception`은 예외의 `str()`을 그대로 기록하므로 쓰지 않는다."""
    leaked = f"Incorrect API key provided: {SECRET}"
    client = ScriptedResponsesClient(
        CallScript(error=status_error(openai.AuthenticationError, 401, leaked))
    )

    with caplog.at_level(logging.DEBUG):
        run_turn(client)

    assert SECRET not in caplog.text
    assert "AuthenticationError" in caplog.text
    # `caplog.text`는 포매터를 거친 문자열이다. 예외 본문은 `exc_text`에도 따로 실리므로 둘 다 본다
    # — `logger.exception`으로 바꾸는 순간 그쪽에 응답 본문이 통째로 들어온다.
    assert all(SECRET not in (record.exc_text or "") for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


def test_the_probe_log_never_quotes_the_provider_error_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaked = f"Incorrect API key provided: {SECRET}"
    client = ScriptedResponsesClient(
        create_error=status_error(openai.AuthenticationError, 401, leaked)
    )
    adapter = OpenAiLlmAdapter(client_factory=RecordingClientFactory(client))

    with caplog.at_level(logging.DEBUG):
        adapter.probe(SECRET, model="gpt-6-astra", base_url=None)

    assert SECRET not in caplog.text
    assert all(SECRET not in (record.exc_text or "") for record in caplog.records)
    assert "AuthenticationError" in caplog.text


# -- probe -----------------------------------------------------------------------------------


def test_probe_reports_success_with_a_latency() -> None:
    client = ScriptedResponsesClient()
    ticks = iter([1.0, 1.25])
    adapter = OpenAiLlmAdapter(
        client_factory=RecordingClientFactory(client), monotonic=lambda: next(ticks)
    )

    result = adapter.probe(SECRET, model="gpt-6-astra", base_url=None)

    assert result.ok is True
    assert result.latency_ms == 250
    assert client.create_payloads == [(PROBE_MAX_OUTPUT_TOKENS, "gpt-6-astra")]


def test_probe_succeeds_even_when_the_minimal_response_is_incomplete() -> None:
    """추론 모델은 상한을 사고에 다 쓰고 본문 없이 끝난다. 그래도 연결은 살아 있다."""
    client = ScriptedResponsesClient(
        create_result=response_of(status="incomplete", incomplete_reason="max_output_tokens")
    )
    adapter = OpenAiLlmAdapter(client_factory=RecordingClientFactory(client))

    assert adapter.probe(SECRET, model="gpt-6-astra", base_url=None).ok is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(openai.AuthenticationError, 401, "invalid key"), ProbeFailure.AUTH),
        (status_error(openai.PermissionDeniedError, 403, "forbidden"), ProbeFailure.AUTH),
        (status_error(openai.NotFoundError, 404, "model not found"), ProbeFailure.MODEL_NOT_FOUND),
        (status_error(openai.RateLimitError, 429, "slow down"), ProbeFailure.RATE_LIMIT),
        (connection_error(), ProbeFailure.NETWORK),
        (RuntimeError("something else"), ProbeFailure.UNKNOWN),
    ],
)
def test_probe_tells_failure_kinds_apart(error: Exception, expected: ProbeFailure) -> None:
    client = ScriptedResponsesClient(create_error=error)
    adapter = OpenAiLlmAdapter(client_factory=RecordingClientFactory(client))

    result = adapter.probe(SECRET, model="gpt-6-astra", base_url=None)

    assert result.ok is False
    assert result.failure is expected


def test_probe_never_quotes_the_provider_error_text() -> None:
    client = ScriptedResponsesClient(
        create_error=status_error(
            openai.AuthenticationError, 401, f"Incorrect API key provided: {SECRET}"
        )
    )
    adapter = OpenAiLlmAdapter(client_factory=RecordingClientFactory(client))

    result = adapter.probe(SECRET, model="gpt-6-astra", base_url=None)

    assert SECRET not in result.message
    assert result.message == "API 키가 거부되었습니다. 키를 다시 확인하세요."


# -- SDK 래퍼 (`_client.py`) -------------------------------------------------------------------
#
# 위 테스트들은 대본을 `OpenAiResponsesClient` Protocol 자리에 통째로 꽂으므로 `_client.py`가 한
# 줄도 실행되지 않는다. 래퍼가 인자를 빠뜨리거나 잘못된 오버로드를 부르면 단위 테스트는 전부
# 초록인 채로 프로덕션만 깨진다. 여기서는 SDK를 실제로 만들고 HTTP 전송만 가짜로 바꿔, 우리가
# 조립한 인자가 그대로 요청에 실리고 SDK가 돌려준 스트림이 그대로 해석되는지 본다.


def _sse_body(*events: object) -> bytes:
    """SDK가 네트워크에서 읽을 SSE 본문.

    손으로 JSON을 적지 않고 SDK 이벤트 객체를 직렬화한다. 스키마를 흉내 내면 SDK가 모양을 바꿔도
    이 테스트만 초록으로 남는다.
    """
    frames: list[str] = []
    for event in events:
        payload = event.model_dump_json()  # pyright: ignore[reportAttributeAccessIssue]  # reason: SDK 이벤트는 전부 pydantic 모델이다
        frames.append(f"data: {payload}\n\n")
    return "".join(frames).encode("utf-8")


def _sdk_with_transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> SdkResponsesClient:
    """진짜 `openai.OpenAI`를 만들고 HTTP 전송만 가짜로 바꾼 래퍼."""
    return SdkResponsesClient(
        openai.OpenAI(
            api_key=SECRET,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )
    )


def test_the_sdk_wrapper_sends_our_arguments_and_decodes_the_real_stream() -> None:
    sent: list[dict[str, object]] = []
    body = _sse_body(text_delta("모멘텀"), final_event(response_of(output_tokens=7)))

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    request = make_request()
    tools = build_tools(request, web_search=True)
    with _sdk_with_transport(handler).stream_response(
        include=INCLUDE,
        input=build_input(request),
        instructions=request.system,
        max_output_tokens=1234,
        model="gpt-6-astra",
        reasoning=REASONING,
        tools=tools,
    ) as stream:
        events = list(stream)

    assert sent[0]["stream"] is True
    assert sent[0]["model"] == "gpt-6-astra"
    assert sent[0]["max_output_tokens"] == 1234
    assert sent[0]["instructions"] == request.system
    assert sent[0]["include"] == INCLUDE
    assert sent[0]["reasoning"] == REASONING
    assert tool_names(sent[0]["tools"]) == [  # pyright: ignore[reportArgumentType]  # reason: 요청 본문은 열린 JSON이다
        "read_current_strategy",
        "propose_strategy",
        "web_search",
    ]
    # 첨자 대신 풀어서 받는다. 리스트 원소는 이벤트 union이라 `events[1].response`를 타입
    # 검사기가 좁히지 못하고, `type` 비교로 좁히려면 지역 이름이 필요하다.
    delta, completed = events
    assert delta.type == "response.output_text.delta"
    assert delta.delta == "모멘텀"
    assert completed.type == "response.completed"
    assert completed.response.usage is not None
    assert completed.response.usage.output_tokens == 7


def test_the_sdk_wrapper_decodes_a_plain_probe_response() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=json.loads(response_of().model_dump_json()))

    result = _sdk_with_transport(handler).create(
        input="ping", max_output_tokens=PROBE_MAX_OUTPUT_TOKENS, model="gpt-6-astra"
    )

    assert result.status == "completed"
    # probe는 스트리밍이 아니다. `stream`을 실어 보내면 반환 타입이 달라져 `probe`가 깨진다.
    assert "stream" not in sent[0]
    assert sent[0] == {
        "input": "ping",
        "max_output_tokens": PROBE_MAX_OUTPUT_TOKENS,
        "model": "gpt-6-astra",
    }


def test_the_sdk_client_factory_carries_the_base_url_timeout_and_retry_defaults() -> None:
    """프로덕션 배선이 쓰는 팩토리. 타임아웃이 빠지면 멈춘 소켓을 아무도 깨우지 못한다."""
    client = sdk_client_factory()(SECRET, "https://proxy.example.com/v1")

    assert isinstance(client, SdkResponsesClient)
    sdk = client._client  # pyright: ignore[reportPrivateUsage]  # reason: 배선 확인용이라 공개 접근자를 만들 이유가 없다
    assert str(sdk.base_url).startswith("https://proxy.example.com")
    assert sdk.timeout == DEFAULT_TIMEOUT_SECONDS
    assert sdk.max_retries == DEFAULT_MAX_RETRIES


# -- facade ----------------------------------------------------------------------------------


def test_the_facade_declares_only_the_nodes_it_uses() -> None:
    from strategy_workbench.adapters.outbound.llm_openai import facade

    assert facade.DEPENDS_ON == ("application.assistant_chat", "domain.assistant")
