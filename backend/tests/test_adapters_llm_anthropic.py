"""Anthropic 공급자 adapter (`adapters/outbound/llm_anthropic`) — A-05.

네트워크를 쓰지 않는다. `tests/anthropic_stream_script.py`가 SDK가 흘렸을 이벤트 열과 최종
메시지를 대신 내주고, 여기서는 adapter가 **무엇을 보내고 무엇으로 옮기며 어떤 상한을 집행하는지**만
본다. 실제 공급자 호출은 A-07의 `scripts/assistant_live_smoke.py`가 맡는다.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence

import pytest

pytest.importorskip(
    "anthropic",
    reason="공급자 SDK는 optional extra `llm`이다. 미설치 환경에서는 이 모듈을 건너뛴다.",
)

import anthropic  # noqa: E402  # reason: 위 importorskip 뒤에야 import할 수 있다
import httpx2  # noqa: E402  # reason: 위와 같음
from anthropic.types import MessageParam  # noqa: E402  # reason: 위와 같음
from anthropic.types.web_search_tool_result_error_code import (  # noqa: E402  # reason: 위와 같음
    WebSearchToolResultErrorCode,
)

from strategy_workbench.adapters.outbound.llm_anthropic._adapter import (  # noqa: E402  # reason: 위와 같음
    PROBE_MAX_TOKENS,
)
from strategy_workbench.adapters.outbound.llm_anthropic._payload import (  # noqa: E402  # reason: 위와 같음
    MAX_CACHE_BREAKPOINTS,
    build_messages,
    build_system,
    build_tools,
)
from strategy_workbench.adapters.outbound.llm_anthropic._turn import (  # noqa: E402  # reason: 위와 같음
    MAX_PAUSE_RESUMES,
    MIN_CALL_OUTPUT_TOKENS,
)
from strategy_workbench.adapters.outbound.llm_anthropic.facade.provider import (  # noqa: E402  # reason: 위와 같음
    DEFAULT_MODEL,
    AnthropicLlmAdapter,
)
from strategy_workbench.application.assistant_chat.facade.ports import (  # noqa: E402  # reason: 위와 같음
    LlmProviderPort,
)
from strategy_workbench.domain.assistant.facade.models import (  # noqa: E402  # reason: 위와 같음
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

from .anthropic_stream_script import (  # noqa: E402  # reason: 위와 같음
    CallScript,
    RecordingClientFactory,
    ScriptedMessagesClient,
    final_message,
    message_with_future_stop_reason,
    search_error_stop,
    search_result_content,
    search_result_stop,
    server_tool_use_content,
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
    tools = as_dicts(build_tools(make_request(), remaining_search_uses=8))

    declared = [tool for tool in tools if "type" not in tool]
    assert [tool["name"] for tool in declared] == ["read_current_strategy", "propose_strategy"]
    assert all(tool.get("strict") is True for tool in declared)
    # 스키마는 domain 상수가 owner다. adapter가 손보면 계약의 주인이 둘이 된다.
    assert declared[0]["input_schema"] == _SCHEMA


def test_web_search_tool_carries_the_requested_use_limit() -> None:
    tools = as_dicts(build_tools(make_request(max_search_uses=3), remaining_search_uses=3))

    search = [tool for tool in tools if tool.get("type") == "web_search_20260209"]
    assert len(search) == 1
    assert search[0]["name"] == "web_search"
    assert search[0]["max_uses"] == 3


def test_a_spent_search_budget_drops_the_tool_entirely() -> None:
    """SDK의 `max_uses`는 호출당 한도라 0을 보낼 수 없다. 도구를 빼는 것이 유일한 표현이다."""
    tools = as_dicts(build_tools(make_request(), remaining_search_uses=0))

    assert [tool.get("type") for tool in tools] == [None, None]


def test_no_web_search_tool_when_research_was_not_requested() -> None:
    tools = as_dicts(build_tools(make_request(research=frozenset()), remaining_search_uses=8))

    assert [tool.get("type") for tool in tools] == [None, None]


# -- 프롬프트 캐싱 ---------------------------------------------------------------------------


def test_cache_breakpoints_sit_on_the_stable_blocks_only() -> None:
    request = make_request()
    tools = build_tools(request, remaining_search_uses=request.max_search_uses)
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


def test_cache_tokens_travel_into_the_domain_usage_event() -> None:
    """SDK의 `input_tokens`는 캐시 읽기·쓰기를 **뺀** 값이다.

    그것만 옮기면 세션 집계가 실제 청구 입력 토큰을 과소 보고한다. 프롬프트 캐싱을 켠 adapter가
    그 사실을 숨기면 안 된다. 단가가 달라 합칠 수도 없으므로 칸을 따로 둔다.
    """
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                input_tokens=120,
                output_tokens=34,
                cache_read_input_tokens=8_000,
                cache_creation_input_tokens=450,
            )
        )
    )

    events = run_turn(client)

    assert (
        Usage(
            input_tokens=120,
            output_tokens=34,
            cache_read_tokens=8_000,
            cache_write_tokens=450,
        )
        in events
    )
    # Anthropic SDK가 이미 분리형으로 보고하므로 adapter는 빼지도 더하지도 않는다. 총입력이
    # 필요한 쪽은 파생 property로 더한다(domain 계약: 세 칸은 서로 겹치지 않는다).
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.input_tokens == 120
    assert usage.total_input_tokens == 120 + 8_000 + 450


def test_a_provider_without_cache_tokens_reports_zeros_not_none() -> None:
    """SDK는 캐시를 안 쓴 호출에 `None`을 준다. domain은 정수만 안다."""
    client = ScriptedMessagesClient(CallScript(message=final_message()))

    events = run_turn(client)

    usage = next(event for event in events if isinstance(event, Usage))
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (0, 0)


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


@pytest.mark.parametrize(
    "error_code",
    [
        "invalid_tool_input",
        "unavailable",
        "max_uses_exceeded",
        "too_many_requests",
        "query_too_long",
        "request_too_large",
    ],
)
def test_search_error_object_becomes_an_empty_search_activity_not_an_exception(
    error_code: WebSearchToolResultErrorCode,
) -> None:
    """검색 결과 블록의 `content`는 성공이면 리스트, 실패면 단일 오류 객체다(spec D4).

    오류여도 턴은 계속된다 — 모델이 같은 오류를 보고 검색 없이 답을 잇는다. 6종 전부 같은
    처리를 받는다(분기가 코드가 아니라 타입을 본다). 나중에 코드별 분기가 생기면 여기가 자리다.
    """
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-9", "존재하지 않는 질의"),
                search_error_stop("srvtoolu-9", error_code),
                text_event("검색 없이 답한다"),
            ),
            message=final_message(stop_reason="end_turn"),
        )
    )

    events = run_turn(client)

    assert events[0] == SearchActivity(query="존재하지 않는 질의", sources=())
    assert TextDelta(text="검색 없이 답한다") in events
    assert failures(events) == []


def test_the_search_budget_is_spent_across_calls_not_reset_every_call() -> None:
    """spec D9: 검색 횟수 집행은 adapter다.

    SDK의 `max_uses`는 **호출당** 한도라 한 번 계산해 모든 호출에 같은 값을 보내면 라운드가
    12번 도는 턴이 예산의 12배를 쓴다. 검색은 과금 대상이다.
    """
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-1", "첫 질의"),
                search_result_stop("srvtoolu-1", (("A", "https://a.test"),)),
            ),
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {})],
            ),
        ),
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-2", "둘째 질의"),
                search_result_stop("srvtoolu-2", (("B", "https://b.test"),)),
            ),
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-2", "read_current_strategy", {})],
            ),
        ),
        CallScript(message=final_message(stop_reason="end_turn")),
    )

    run_turn(client, request=make_request(max_search_uses=2))

    def search_tools(index: int) -> list[dict[str, object]]:
        return [
            tool
            for tool in as_dicts(client.payloads[index].tools)
            if tool.get("type") == "web_search_20260209"
        ]

    assert search_tools(0)[0]["max_uses"] == 2  # 아직 안 썼다
    assert search_tools(1)[0]["max_uses"] == 1  # 한 번 썼다
    assert search_tools(2) == []  # 예산 소진 — 도구를 뺀다


def test_a_call_without_the_search_tool_still_carries_the_earlier_search_blocks() -> None:
    """예산이 소진돼 `web_search`를 뺀 호출의 history에 이전 검색 블록이 그대로 실린다.

    두 규칙이 만나 생기는 조합이다. 하나는 "assistant 턴을 한 글자도 바꾸지 않고 되돌린다"
    (thinking signature를 지키려고), 다른 하나는 "예산이 소진되면 도구를 뺀다". 그래서 **선언
    되지 않은 서버 도구의 `server_tool_use`·`web_search_tool_result` 블록이 든 history**를
    도구 없이 보내게 된다.

    여기서 고정하는 것은 우리 쪽 동작뿐이다. 공급자가 그런 history를 받아 주는지는 문서화된
    계약이 아니다 — **A-07 live smoke 최우선 항목**이다. 400이면 대안은 도구를 빼는 대신
    `max_uses=1`로 남겨 1회 초과를 허용하는 것이다.
    """
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-1", "첫 질의"),
                search_result_stop("srvtoolu-1", (("A", "https://a.test"),)),
            ),
            message=final_message(
                stop_reason="tool_use",
                content=[
                    server_tool_use_content("srvtoolu-1", "첫 질의"),
                    search_result_content("srvtoolu-1", (("A", "https://a.test"),)),
                    tool_use_block("toolu-1", "read_current_strategy", {}),
                ],
            ),
        ),
        CallScript(message=final_message(stop_reason="end_turn")),
    )

    run_turn(client, request=make_request(max_search_uses=1))

    second = client.payloads[1]
    assert [tool.get("type") for tool in as_dicts(second.tools)] == [None, None]
    assistant_turn = next(message for message in second.messages if message["role"] == "assistant")
    content = assistant_turn["content"]
    assert not isinstance(content, str)
    carried = [getattr(block, "type", None) for block in content]
    assert carried == ["server_tool_use", "web_search_tool_result", "tool_use"]


def test_the_tool_list_is_byte_identical_until_a_search_actually_happens() -> None:
    """검색 전에는 남은 횟수가 곧 전체 예산이라 캐시 접두가 깨지지 않는다."""
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {})],
            )
        ),
        CallScript(message=final_message(stop_reason="end_turn")),
    )

    run_turn(client)

    assert as_dicts(client.payloads[0].tools) == as_dicts(client.payloads[1].tools)


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


def test_endless_pause_turn_with_no_progress_stops_at_the_resume_limit() -> None:
    """아무것도 하지 않고 멈추기만 하는 재개는 상한에서 끊는다."""
    client = ScriptedMessagesClient(
        *[
            CallScript(message=final_message(stop_reason="pause_turn"))
            for _ in range(MAX_PAUSE_RESUMES + 1)
        ]
    )

    events = run_turn(client)

    failure = failures(events)[0]
    assert failure.code is FailureCode.PROVIDER
    # 사유가 메시지에 드러나야 한다. "서버 도구가 멈춘다"만으로는 무엇이 한도였는지 모른다.
    assert "reason=pause_turn_no_progress" in failure.message
    assert len(client.payloads) == MAX_PAUSE_RESUMES + 1


def test_a_pause_after_every_search_does_not_eat_the_search_budget() -> None:
    """검색마다 턴이 멈추는 동작에서도 허락한 검색 예산을 다 쓸 수 있어야 한다.

    재개를 무조건 세면 `DEFAULT_MAX_SEARCH_USES`(8)가 `MAX_PAUSE_RESUMES`(5)보다 커서, 예산의
    62%를 쓴 시점에 `Failure(PROVIDER)`로 끝나고 사용자는 원인을 알 수 없다. 진전 있는 재개는
    세지 않으므로 여기서는 8회 검색 + 8회 재개가 전부 지나간다.
    """
    searches = 8
    client = ScriptedMessagesClient(
        *[
            CallScript(
                events=(
                    server_tool_use_stop(f"srvtoolu-{index}", f"질의 {index}"),
                    search_result_stop(f"srvtoolu-{index}", (("제목", "https://a.test"),)),
                ),
                message=final_message(stop_reason="pause_turn"),
            )
            for index in range(searches)
        ],
        CallScript(events=(text_event("정리"),), message=final_message(stop_reason="end_turn")),
    )

    events = run_turn(client, request=make_request(max_search_uses=searches))

    assert failures(events) == []
    assert events[-1] == Done(stop_reason="end_turn")
    assert len(client.payloads) == searches + 1
    # 마지막 호출에는 예산이 소진돼 검색 도구가 없다.
    assert [tool.get("type") for tool in as_dicts(client.payloads[-1].tools)] == [None, None]


def test_a_no_progress_streak_after_real_searches_still_stops() -> None:
    """진전이 카운터를 되돌려도, 그 뒤 진전 없는 연속 재개는 여전히 끊는다."""
    client = ScriptedMessagesClient(
        CallScript(
            events=(
                server_tool_use_stop("srvtoolu-1", "질의"),
                search_result_stop("srvtoolu-1", (("제목", "https://a.test"),)),
            ),
            message=final_message(stop_reason="pause_turn"),
        ),
        *[
            CallScript(message=final_message(stop_reason="pause_turn"))
            for _ in range(MAX_PAUSE_RESUMES + 1)
        ],
    )

    events = run_turn(client, request=make_request(max_search_uses=8))

    failure = failures(events)[0]
    assert failure.code is FailureCode.PROVIDER
    assert "reason=pause_turn_no_progress" in failure.message
    # 검색 1회 + 진전 없는 재개 6회 = 7번 호출하고 멈춘다.
    assert len(client.payloads) == MAX_PAUSE_RESUMES + 2


def test_the_total_resume_cap_names_its_reason() -> None:
    """진전이 계속 있어도 재개 총량에는 하드캡이 있다.

    검색 예산이 1이면 총량 상한은 1 + 5 = 6이다. 매 재개가 새 검색을 동반해 연속 카운터가
    계속 0으로 돌아가더라도 7번째 재개에서 멈춘다.
    """
    budget = 1
    cap = budget + MAX_PAUSE_RESUMES
    client = ScriptedMessagesClient(
        *[
            CallScript(
                events=(
                    server_tool_use_stop(f"srvtoolu-{index}", f"질의 {index}"),
                    search_result_stop(f"srvtoolu-{index}", ()),
                ),
                message=final_message(stop_reason="pause_turn"),
            )
            for index in range(cap + 1)
        ]
    )

    events = run_turn(client, request=make_request(max_search_uses=budget))

    failure = failures(events)[0]
    assert failure.code is FailureCode.PROVIDER
    assert "reason=pause_turn_total_exceeded" in failure.message
    assert len(client.payloads) == cap + 1


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


def test_an_unknown_stop_reason_is_a_provider_failure_not_a_silent_done() -> None:
    """SDK가 종류를 늘렸을 때 새 **실패성** 사유가 화면에 "정상 종료"로 보이면 안 된다."""
    client = ScriptedMessagesClient(
        CallScript(message=message_with_future_stop_reason("some_future_reason"))
    )

    events = run_turn(client)

    assert failures(events)[0].code is FailureCode.PROVIDER
    assert not any(isinstance(event, Done) for event in events)


def test_stop_sequence_is_still_a_normal_end() -> None:
    client = ScriptedMessagesClient(CallScript(message=final_message(stop_reason="stop_sequence")))

    assert run_turn(client)[-1] == Done(stop_reason="stop_sequence")


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


def test_a_budget_remainder_too_small_to_call_is_reported_as_budget_not_truncation() -> None:
    """잔량이 1이면 `max_tokens=1` 호출이 거의 확실히 잘려 진짜 사유를 가린다.

    그래서 최소 호출 크기보다 적게 남으면 호출하지 않는다. 두 사유가 같은 상황을 가리키면
    이력에서 무엇이 턴을 끊었는지 구분할 수 없다.
    """
    spent = 1_000 - (MIN_CALL_OUTPUT_TOKENS - 1)
    client = ScriptedMessagesClient(
        CallScript(
            message=final_message(
                stop_reason="tool_use",
                content=[tool_use_block("toolu-1", "read_current_strategy", {})],
                output_tokens=spent,
            )
        )
    )

    events = run_turn(
        client,
        request=make_request(max_output_tokens_per_call=1_000, max_turn_output_tokens=1_000),
    )

    assert failures(events)[0].code is FailureCode.TOKEN_BUDGET_EXCEEDED
    assert len(client.payloads) == 1


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


def test_a_cancelled_call_still_reports_the_tokens_it_already_spent() -> None:
    """취소해도 그때까지의 출력 토큰은 과금된다. 집계에서 빠지면 세션 `Usage`가 0으로 보인다."""
    client = ScriptedMessagesClient(
        CallScript(
            events=(text_event("앞"), text_event("뒤")),
            message=final_message(stop_reason="end_turn"),
            snapshot=final_message(input_tokens=70, output_tokens=12),
        )
    )
    seen = 0

    def cancelled() -> bool:
        nonlocal seen
        seen += 1
        return seen >= 2

    events = run_turn(client, cancelled=cancelled)

    assert events == [
        TextDelta(text="앞"),
        Usage(input_tokens=70, output_tokens=12),
        Failure(code=FailureCode.CANCELLED, message=events[-1].message),  # pyright: ignore[reportAttributeAccessIssue]  # reason: 문구가 아니라 순서를 본다
    ]


def test_a_cancellation_before_any_snapshot_still_ends_the_turn() -> None:
    """`message_start`를 보기 전에 취소되면 사용량을 읽을 수 없다. 취소 자체는 성립해야 한다."""
    client = ScriptedMessagesClient(
        CallScript(events=(text_event("앞"),), message=final_message(), snapshot=None)
    )
    seen = 0

    def cancelled() -> bool:
        nonlocal seen
        seen += 1
        return seen >= 2

    events = run_turn(client, cancelled=cancelled)

    assert not any(isinstance(event, Usage) for event in events)
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


def test_the_turn_log_carries_the_exception_type_but_never_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """완료 정의 3: 키는 로그에도 평문으로 남지 않는다.

    다음 사람이 진단을 늘리려고 `logger.warning("... %s", error)`나 `logger.exception(...)`으로
    바꾸면 401 응답 본문이 그대로 로그에 남는다. 키 누설은 회수 경로가 없으므로 여기서 고정한다.
    """
    leaked = f"Incorrect API key provided: {SECRET}"
    client = ScriptedMessagesClient(
        CallScript(error=status_error(anthropic.AuthenticationError, 401, leaked))
    )

    with caplog.at_level(logging.DEBUG):
        run_turn(client)

    assert SECRET not in caplog.text
    assert "Incorrect API key provided" not in caplog.text
    assert "AuthenticationError" in caplog.text
    # `logger.exception`은 예외의 `str()`을 `exc_text`로 기록한다. 쓰지 않는다는 뜻이다.
    assert all(record.exc_text is None for record in caplog.records)


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
    assert client.create_payloads == [(PROBE_MAX_TOKENS, "claude-opus-5")]


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


def test_probe_never_quotes_the_provider_error_text(caplog: pytest.LogCaptureFixture) -> None:
    client = ScriptedMessagesClient(
        create_error=status_error(
            anthropic.AuthenticationError, 401, f"Incorrect API key provided: {SECRET}"
        )
    )
    adapter = AnthropicLlmAdapter(client_factory=RecordingClientFactory(client))

    with caplog.at_level(logging.DEBUG):
        result = adapter.probe(SECRET, model="claude-opus-5", base_url=None)

    assert SECRET not in result.message
    assert result.message == "API 키가 거부되었습니다. 키를 다시 확인하세요."
    assert SECRET not in caplog.text
    assert "Incorrect API key provided" not in caplog.text
    assert all(record.exc_text is None for record in caplog.records)


# -- facade ----------------------------------------------------------------------------------


def test_the_facade_declares_only_the_nodes_it_uses() -> None:
    """선언은 실제 import와 같아야 한다.

    이 노드는 `application.assistant_chat`을 import하지 않는다 — `LlmProviderPort`는 Protocol
    이라 구조만 맞으면 되고, 쓰는 타입은 전부 `domain.assistant` 것이다. 경계 게이트는
    "선언 없는 import"만 잡고 "import 없는 선언"은 잡지 못하므로 여기서 고정한다.
    """
    from strategy_workbench.adapters.outbound.llm_anthropic import facade

    assert facade.DEPENDS_ON == ("domain.assistant",)
