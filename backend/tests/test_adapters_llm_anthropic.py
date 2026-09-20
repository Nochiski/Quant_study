"""Anthropic 공급자 adapter (`adapters/outbound/llm_anthropic`) — A-05.

네트워크를 쓰지 않는다. `tests/anthropic_stream_script.py`가 SDK가 흘렸을 이벤트 열과 최종
메시지를 대신 내주고, 여기서는 adapter가 **무엇을 보내고 무엇으로 옮기며 어떤 상한을 집행하는지**만
본다. 실제 공급자 호출은 A-07의 `RUN_LLM_LIVE=1` smoke가 맡는다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence

import anthropic
import httpx2
import pytest
from anthropic.types import MessageParam

from strategy_workbench.adapters.outbound.llm_anthropic._payload import (
    MAX_CACHE_BREAKPOINTS,
    build_messages,
    build_system,
    build_tools,
)
from strategy_workbench.adapters.outbound.llm_anthropic._turn import MAX_PAUSE_RESUMES
from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (
    DEFAULT_MODEL,
    AnthropicLlmAdapter,
)
from strategy_workbench.application.assistant_chat.facade.ports import LlmProviderPort
from strategy_workbench.domain.assistant.facade.models import (
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

from .anthropic_stream_script import (
    CallScript,
    RecordingClientFactory,
    ScriptedMessagesClient,
    final_message,
    search_error_stop,
    search_result_stop,
    server_tool_use_stop,
    text_event,
    thinking_stop,
    tool_use_block,
)

SECRET = "sk-ant-api03-TEST-SECRET"

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


def make_profile(*, model: str = "claude-opus-5", base_url: str | None = None) -> ProviderProfile:
    return ProviderProfile(
        profile_id="profile-1",
        kind=ProviderKind.ANTHROPIC,
        label="Claude",
        model=model,
        base_url=base_url,
        created_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
        active=True,
    )


def run_turn(
    client: ScriptedMessagesClient,
    *,
    request: TurnRequest | None = None,
    profile: ProviderProfile | None = None,
    execute_tool: Callable[[ToolCall], ToolResult] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[ChatEvent]:
    factory = RecordingClientFactory(client)
    adapter = AnthropicLlmAdapter(client_factory=factory)
    return list(
        adapter.stream_turn(
            SECRET,
            profile or make_profile(),
            request or make_request(),
            execute_tool or (lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}")),
            cancelled,
        )
    )


def message_blocks(messages: Iterable[MessageParam]) -> list[object]:
    """대화 메시지 안의 content 블록. 문자열 content는 breakpoint를 가질 수 없다."""
    blocks: list[object] = []
    for message in messages:
        content = message["content"]
        if not isinstance(content, str):
            blocks.extend(content)
    return blocks


def count_cache_breakpoints(*blocks: Iterable[object]) -> int:
    """요청이 쓰는 캐시 breakpoint 수. 상한(4)을 넘기면 공급자가 400을 낸다."""
    return sum(
        1
        for group in blocks
        for block in group
        if isinstance(block, Mapping) and block.get("cache_control") is not None
    )


def as_dicts(blocks: Iterable[object]) -> list[dict[str, object]]:
    """TypedDict union을 평범한 dict로 본다.

    SDK 요청 타입은 `NotRequired` 키를 가진 TypedDict union이라 `block["name"]` 같은 첨자를
    타입 검사기가 막는다. 테스트는 보낸 값만 확인하면 되므로 dict로 한 번 낮춘다. dict가 아닌
    블록(응답 객체를 그대로 되돌린 assistant 턴)은 조용히 건너뛰지 않고 실패시킨다.
    """
    lowered: list[dict[str, object]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            raise AssertionError(f"dict 블록이 아니다 — type={type(block).__name__}")
        lowered.append({str(key): value for key, value in block.items()})
    return lowered


def failures(events: Sequence[ChatEvent]) -> list[Failure]:
    return [event for event in events if isinstance(event, Failure)]


def status_error(
    error_class: type[anthropic.APIStatusError], status: int, message: str
) -> anthropic.APIStatusError:
    """SDK가 상태 코드별로 올리는 예외를 그대로 만든다.

    베이스 `APIStatusError`로 만들면 안 된다 — SDK는 상태 코드마다 전용 하위 타입을 올리고,
    adapter의 매핑은 그 하위 타입을 본다.
    """
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return error_class(message, response=httpx2.Response(status, request=request), body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


# -- 도구 변환 -------------------------------------------------------------------------------


def test_tool_specs_become_strict_anthropic_tools() -> None:
    tools = as_dicts(build_tools(make_request()))

    declared = [tool for tool in tools if "type" not in tool]
    assert [tool["name"] for tool in declared] == ["read_current_strategy", "propose_strategy"]
    assert all(tool.get("strict") is True for tool in declared)
    # 스키마는 domain 상수가 owner다. adapter가 손보면 계약의 주인이 둘이 된다.
    assert declared[0]["input_schema"] == _SCHEMA


def test_web_search_tool_carries_the_requested_use_limit() -> None:
    tools = as_dicts(build_tools(make_request(max_search_uses=3)))

    search = [tool for tool in tools if tool.get("type") == "web_search_20260209"]
    assert len(search) == 1
    assert search[0]["name"] == "web_search"
    assert search[0]["max_uses"] == 3


def test_no_web_search_tool_when_research_was_not_requested() -> None:
    tools = as_dicts(build_tools(make_request(research=frozenset())))

    assert [tool.get("type") for tool in tools] == [None, None]


# -- 프롬프트 캐싱 ---------------------------------------------------------------------------


def test_cache_breakpoints_sit_on_the_stable_blocks_only() -> None:
    request = make_request()
    tools = build_tools(request)
    system = build_system(request)
    messages = build_messages(request)

    # 안정 구간(도구 목록 끝, 시스템 블록)에만 찍는다.
    assert tools[-1].get("cache_control") == {"type": "ephemeral"}
    assert all(tool.get("cache_control") is None for tool in tools[:-1])
    assert system[-1].get("cache_control") == {"type": "ephemeral"}
    # 휘발 구간(대화 메시지)에는 없다. 질문이 바뀔 때마다 캐시가 깨지면 캐싱한 의미가 없다.
    assert count_cache_breakpoints(tools, system, message_blocks(messages)) == 2
    assert count_cache_breakpoints(tools, system, message_blocks(messages)) <= MAX_CACHE_BREAKPOINTS


def test_history_becomes_anthropic_messages_in_order() -> None:
    request = make_request()

    assert build_messages(request) == [{"role": "user", "content": "모멘텀 전략을 만들어 줘"}]


# -- 스트리밍 --------------------------------------------------------------------------------


def test_stream_turn_emits_text_thinking_usage_and_done() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                thinking_stop("모멘텀 팩터를 고른다"),
                text_event("모멘"),
                text_event("텀"),
            ),
            message=final_message(stop_reason="end_turn", input_tokens=120, output_tokens=34),
        )
    )

    events = run_turn(client)

    assert events == [
        ThinkingSummary(text="모멘텀 팩터를 고른다"),
        TextDelta(text="모멘"),
        TextDelta(text="텀"),
        Usage(input_tokens=120, output_tokens=34),
        Done(stop_reason="end_turn"),
    ]


def test_the_call_asks_for_summarized_adaptive_thinking_and_high_effort() -> None:
    client = ScriptedMessagesClient(CallScript(message=final_message()))

    run_turn(client, profile=make_profile(model="claude-opus-5"))

    payload = client.payloads[0]
    assert payload.thinking == {"type": "adaptive", "display": "summarized"}
    assert payload.output_config == {"effort": "high"}
    assert payload.model == "claude-opus-5"


def test_default_model_is_the_spec_model() -> None:
    assert AnthropicLlmAdapter().default_model() == DEFAULT_MODEL == "claude-opus-5"


def test_the_adapter_satisfies_the_outgoing_port() -> None:
    provider: LlmProviderPort = AnthropicLlmAdapter()

    assert provider.kind is ProviderKind.ANTHROPIC


def test_the_secret_and_base_url_reach_the_client_factory_per_call() -> None:
    client = ScriptedMessagesClient(CallScript(message=final_message()))
    factory = RecordingClientFactory(client)
    adapter = AnthropicLlmAdapter(client_factory=factory)

    list(
        adapter.stream_turn(
            SECRET,
            make_profile(base_url="https://gateway.example.test"),
            make_request(),
            lambda call: ToolResult(call_id=call.call_id, ok=True, content="{}"),
            lambda: False,
        )
    )

    assert factory.seen == [(SECRET, "https://gateway.example.test")]


# -- 서버 도구(검색) -------------------------------------------------------------------------


def test_search_success_becomes_search_activity_with_sources() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-1", "모멘텀 팩터"),
                search_result_stop(
                    "srvtoolu-1", (("모멘텀 소개", "https://example.test/momentum"),)
                ),
            ),
            message=final_message(),
        )
    )

    events = run_turn(client)

    assert events[0] == SearchActivity(
        query="모멘텀 팩터",
        sources=(Source(title="모멘텀 소개", url="https://example.test/momentum"),),
    )


def test_search_error_object_becomes_an_empty_search_activity_not_an_exception() -> None:
    """검색 결과 블록의 `content`는 성공이면 리스트, 실패면 단일 오류 객체다(spec D4).

    오류여도 턴은 계속된다 — 모델이 같은 오류를 보고 검색 없이 답을 잇는다.
    """
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-9", "존재하지 않는 질의"),
                search_error_stop("srvtoolu-9", "max_uses_exceeded"),
                text_event("검색 없이 답한다"),
            ),
            message=final_message(stop_reason="end_turn"),
        )
    )

    events = run_turn(client)

    assert events[0] == SearchActivity(query="존재하지 않는 질의", sources=())
    assert TextDelta(text="검색 없이 답한다") in events
    assert failures(events) == []


# -- 도구 루프 -------------------------------------------------------------------------------


def test_tool_use_runs_the_callback_and_feeds_the_result_back() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {"a": 1})],
            )
        ),
        CallScript(events=(text_event("완료"),), message=final_message(stop_reason="end_turn")),
    )
    executed: list[ToolCall] = []

    def execute(call: ToolCall) -> ToolResult:
        executed.append(call)
        return ToolResult(call_id=call.call_id, ok=True, content='{"source_text": "x"}')

    events = run_turn(client, execute_tool=execute)

    assert executed == [
        ToolCall(call_id="toolu-1", name="read_current_strategy", arguments={"a": 1})
    ]
    assert ToolCall(call_id="toolu-1", name="read_current_strategy", arguments={"a": 1}) in events
    assert isinstance(events[-1], Done)

    # 두 번째 호출에는 assistant 턴과 tool_result가 붙는다.
    second = client.payloads[1].messages
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    results = second[2]["content"]
    assert not isinstance(results, str)
    assert as_dicts(results) == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu-1",
            "content": '{"source_text": "x"}',
            "is_error": False,
        }
    ]


def test_a_failed_tool_result_is_marked_is_error() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "propose_strategy", {})],
            )
        ),
        CallScript(message=final_message(stop_reason="end_turn")),
    )

    run_turn(
        client,
        execute_tool=lambda call: ToolResult(call_id=call.call_id, ok=False, content="진단"),
    )

    results = client.payloads[1].messages[2]["content"]
    assert not isinstance(results, str)
    assert as_dicts(results)[0]["is_error"] is True


def test_tool_rounds_over_the_limit_end_the_turn() -> None:
    rounds = 2
    client = ScriptedMessagesClient(
        *[
            CallScript(
                message=final_message(
                    stop_reason="tool_use",
                    content=[tool_use_block(f"toolu-{index}", "read_current_strategy", {})],
                )
            )
            for index in range(rounds + 1)
        ]
    )

    events = run_turn(client, request=make_request(max_tool_rounds=rounds))

    assert failures(events)[0].code is FailureCode.TOOL_ROUNDS_EXCEEDED
    # 상한을 넘긴 라운드의 호출은 하지 않는다.
    assert len(client.payloads) == rounds + 1


def test_tool_use_without_any_tool_block_fails_instead_of_looping() -> None:
    client = ScriptedMessagesClient(
        CallScript(message=final_message(stop_reason="tool_use", content=[]))
    )

    events = run_turn(client)

    assert failures(events)[0].code is FailureCode.PROVIDER


# -- pause_turn ------------------------------------------------------------------------------


def test_pause_turn_resumes_by_replaying_the_paused_assistant_turn() -> None:
    client = ScriptedMessagesClient(
        CallScript(message=final_message(stop_reason="pause_turn")),
        CallScript(events=(text_event("이어서"),), message=final_message(stop_reason="end_turn")),
    )

    events = run_turn(client)

    assert isinstance(events[-1], Done)
    resumed = client.payloads[1].messages
    # 재개는 멈춘 assistant 턴만 붙인다. "계속하세요" 같은 사용자 메시지를 넣으면 서버가 재개
    # 신호를 읽지 못한다.
    assert [message["role"] for message in resumed] == ["user", "assistant"]


def test_endless_pause_turn_stops_at_the_resume_limit() -> None:
    client = ScriptedMessagesClient(
        *[
            CallScript(message=final_message(stop_reason="pause_turn"))
            for _ in range(MAX_PAUSE_RESUMES + 1)
        ]
    )

    events = run_turn(client)

    assert failures(events)[0].code is FailureCode.PROVIDER
    assert len(client.payloads) == MAX_PAUSE_RESUMES + 1


# -- 종료 사유 -------------------------------------------------------------------------------


def test_refusal_ends_the_turn_with_the_refusal_code() -> None:
    client = ScriptedMessagesClient(CallScript(message=final_message(stop_reason="refusal")))

    assert failures(run_turn(client))[0].code is FailureCode.REFUSAL


def test_max_tokens_stop_reason_is_reported_as_truncated_output() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            events=(text_event("길어진 답"),), message=final_message(stop_reason="max_tokens")
        )
    )

    events = run_turn(client)

    # 잘린 텍스트는 보존하되 정상 종료로 보이지 않는다(spec D3).
    assert TextDelta(text="길어진 답") in events
    assert failures(events)[0].code is FailureCode.OUTPUT_TRUNCATED
    assert not any(isinstance(event, Done) for event in events)


def test_context_window_overflow_is_a_provider_failure_not_a_normal_end() -> None:
    client = ScriptedMessagesClient(
        CallScript(message=final_message(stop_reason="model_context_window_exceeded"))
    )

    assert failures(run_turn(client))[0].code is FailureCode.PROVIDER


# -- 토큰 예산 -------------------------------------------------------------------------------


def test_the_next_call_is_capped_by_the_remaining_turn_budget() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {})],
                output_tokens=900,
            )
        ),
        CallScript(message=final_message(stop_reason="end_turn")),
    )

    run_turn(
        client,
        request=make_request(max_output_tokens_per_call=1_000, max_turn_output_tokens=1_500),
    )

    assert client.payloads[0].max_tokens == 1_000  # min(호출당 1000, 남은 1500)
    assert client.payloads[1].max_tokens == 600  # min(호출당 1000, 남은 1500-900)


def test_an_exhausted_turn_budget_stops_the_loop_before_the_next_call() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {})],
                output_tokens=1_000,
            )
        )
    )

    events = run_turn(
        client,
        request=make_request(max_output_tokens_per_call=1_000, max_turn_output_tokens=1_000),
    )

    assert failures(events)[0].code is FailureCode.TOKEN_BUDGET_EXCEEDED
    assert len(client.payloads) == 1


# -- 취소 ------------------------------------------------------------------------------------


def test_cancelling_before_the_first_call_never_reaches_the_provider() -> None:
    client = ScriptedMessagesClient()

    events = run_turn(client, cancelled=lambda: True)

    assert failures(events)[0].code is FailureCode.CANCELLED
    assert client.payloads == []


def test_cancelling_between_stream_events_keeps_what_was_already_streamed() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            events=(text_event("앞"), text_event("뒤")),
            message=final_message(stop_reason="end_turn"),
        )
    )
    seen = 0

    def cancelled() -> bool:
        nonlocal seen
        seen += 1
        # 첫 호출은 루프 맨 앞의 사전 확인이다. 두 번째(첫 이벤트 뒤)부터 참을 돌려준다.
        return seen >= 2

    events = run_turn(client, cancelled=cancelled)

    assert events[0] == TextDelta(text="앞")
    assert failures(events)[0].code is FailureCode.CANCELLED


def test_cancelling_between_tool_calls_stops_before_the_second_tool_runs() -> None:
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[
                    tool_use_block("toolu-1", "read_current_strategy", {}),
                    tool_use_block("toolu-2", "propose_strategy", {}),
                ],
            )
        )
    )
    executed: list[str] = []
    cancel = False

    def execute(call: ToolCall) -> ToolResult:
        nonlocal cancel
        executed.append(call.call_id)
        cancel = True
        return ToolResult(call_id=call.call_id, ok=True, content="{}")

    events = run_turn(client, execute_tool=execute, cancelled=lambda: cancel)

    assert executed == ["toolu-1"]
    assert failures(events)[0].code is FailureCode.CANCELLED


# -- 예외 매핑 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), FailureCode.AUTH),
        (status_error(anthropic.PermissionDeniedError, 403, "forbidden"), FailureCode.AUTH),
        (status_error(anthropic.RateLimitError, 429, "rate limited"), FailureCode.RATE_LIMIT),
        (connection_error(), FailureCode.NETWORK),
        (status_error(anthropic.InternalServerError, 500, "server error"), FailureCode.PROVIDER),
    ],
)
def test_sdk_errors_map_to_failure_codes(error: Exception, expected: FailureCode) -> None:
    client = ScriptedMessagesClient(CallScript(error=error))

    assert failures(run_turn(client))[0].code is expected


def test_an_unknown_exception_is_left_for_the_application_to_absorb() -> None:
    client = ScriptedMessagesClient(CallScript(error=ZeroDivisionError("bug")))

    with pytest.raises(ZeroDivisionError):
        run_turn(client)


def test_a_key_inside_the_sdk_error_never_reaches_the_failure_message() -> None:
    """`Failure.message`는 SSE를 타고 화면까지 가고 이력에도 저장된다(spec D2).

    공급자 인증 오류 본문은 키 조각을 그대로 담는 일이 흔하다. 한 번 새면 회수 경로가 없다.
    """
    leaked = f"Incorrect API key provided: {SECRET}"
    client = ScriptedMessagesClient(
        CallScript(error=status_error(anthropic.AuthenticationError, 401, leaked))
    )

    failure = failures(run_turn(client))[0]

    assert SECRET not in failure.message
    assert "Incorrect API key provided" not in failure.message
    assert "AuthenticationError" in failure.message


# -- probe -----------------------------------------------------------------------------------


def test_probe_reports_success_with_a_latency() -> None:
    client = ScriptedMessagesClient()
    ticks = iter([1.0, 1.25])
    adapter = AnthropicLlmAdapter(
        client_factory=RecordingClientFactory(client), monotonic=lambda: next(ticks)
    )

    result = adapter.probe(SECRET, model="claude-opus-5", base_url=None)

    assert result.ok is True
    assert result.latency_ms == 250
    assert client.create_payloads == [(16, "claude-opus-5")]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid key"), ProbeFailure.AUTH),
        (status_error(anthropic.PermissionDeniedError, 403, "forbidden"), ProbeFailure.AUTH),
        (
            status_error(anthropic.NotFoundError, 404, "model not found"),
            ProbeFailure.MODEL_NOT_FOUND,
        ),
        (status_error(anthropic.RateLimitError, 429, "slow down"), ProbeFailure.RATE_LIMIT),
        (connection_error(), ProbeFailure.NETWORK),
        (RuntimeError("something else"), ProbeFailure.UNKNOWN),
    ],
)
def test_probe_tells_failure_kinds_apart(error: Exception, expected: ProbeFailure) -> None:
    client = ScriptedMessagesClient(create_error=error)
    adapter = AnthropicLlmAdapter(client_factory=RecordingClientFactory(client))

    result = adapter.probe(SECRET, model="claude-opus-5", base_url=None)

    assert result.ok is False
    assert result.failure is expected


def test_probe_never_quotes_the_provider_error_text() -> None:
    client = ScriptedMessagesClient(
        create_error=status_error(
            anthropic.AuthenticationError, 401, f"Incorrect API key provided: {SECRET}"
        )
    )
    adapter = AnthropicLlmAdapter(client_factory=RecordingClientFactory(client))

    result = adapter.probe(SECRET, model="claude-opus-5", base_url=None)

    assert SECRET not in result.message
    assert result.message == "API 키가 거부되었습니다. 키를 다시 확인하세요."


# -- facade ----------------------------------------------------------------------------------


def test_the_facade_declares_only_the_nodes_it_uses() -> None:
    from strategy_workbench.adapters.outbound.llm_anthropic import facade

    assert facade.DEPENDS_ON == ("application.assistant_chat", "domain.assistant")
