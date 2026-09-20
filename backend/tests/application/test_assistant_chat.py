"""AssistantChatService 도구 루프·제안 검증 테스트 (A-02).

공급자 SDK 없이 도는 것이 A-01의 완료 조건이므로 여기서는 `ScriptedProvider`만 쓴다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.assistant_chat.facade.chat import (
    AssistantChatService,
    ChatSession,
    DocumentRef,
    NoActiveProviderError,
    TurnContext,
)
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.domain.assistant.facade.models import (
    DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL,
    DEFAULT_MAX_SEARCH_USES,
    DEFAULT_MAX_TURN_OUTPUT_TOKENS,
    ChatEvent,
    ChatRole,
    Done,
    Failure,
    FailureCode,
    Proposal,
    ProviderKind,
    ResearchCapability,
    Source,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolResultSummary,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import (
    ASSISTANT_TOOLS,
    LIST_EQUITY_FIELDS,
    LIST_FACTOR_CATALOG,
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
    VALIDATE_STRATEGY_YAML,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

from ._assistant_fakes import (
    FakeStrategyCompiler,
    InMemoryChatSessionRepository,
    InMemoryProviderProfileRepository,
    InMemoryProviderSecretStore,
    RaiseStep,
    ScriptedProvider,
    ScriptStep,
    ToolStep,
    sequential_ids,
)

FIXED_NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
TODAY = date(2026, 9, 20)
VALID_YAML = "schema_version: '1.1'\ntitle: 모멘텀\n"
INVALID_YAML = "schema_version: '1.1'\ntitle: 깨진 문서\n"
DOCUMENT = DocumentRef(strategy_id="strategy-1", revision=3, draft_id=None)
CONTEXT = TurnContext(
    source_text="schema_version: '1.1'\ntitle: 현재 문서\n",
    source_format="yaml",
    environment={"start": "2020-01-02", "end": "2026-09-01"},
    diagnostics=("strategy.factor.unknown /factors/0/factor_id",),
)


@dataclass(frozen=True)
class Harness:
    service: AssistantChatService
    provider: ScriptedProvider
    sessions: InMemoryChatSessionRepository
    compiler: FakeStrategyCompiler
    session: ChatSession


def _harness(
    script: Sequence[ScriptStep],
    *,
    valid_sources: Sequence[str] = (),
    max_tool_rounds: int = 12,
    max_proposal_attempts: int = 3,
    with_profile: bool = True,
    context_builder: AssistantContextBuilder | None = None,
) -> Harness:
    repository = InMemoryProviderProfileRepository()
    secrets = InMemoryProviderSecretStore()
    provider = ScriptedProvider(script=script)
    providers = {ProviderKind.ANTHROPIC: provider}
    profiles = ProviderProfileService(
        repository,
        secrets,
        providers,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("profile"),
    )
    if with_profile:
        profiles.create(kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001")
    compiler = FakeStrategyCompiler(valid_sources=valid_sources)
    context_builder = context_builder or AssistantContextBuilder(
        equity_data=MockEquityDataAdapter.demo(),
        factor_registry=build_default_factor_registry(),
        compiler=compiler,
        today=lambda: TODAY,
    )
    sessions = InMemoryChatSessionRepository()
    service = AssistantChatService(
        sessions,
        profiles,
        secrets,
        providers,
        compiler,
        context_builder,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("session"),
        max_tool_rounds=max_tool_rounds,
        max_proposal_attempts=max_proposal_attempts,
    )
    session = (
        service.create_session(DOCUMENT, title="테스트 세션")
        if with_profile
        else ChatSession(
            session_id="unused",
            document_ref=DOCUMENT,
            provider_profile_id="none",
            created_at=FIXED_NOW,
            title="테스트 세션",
        )
    )
    return Harness(
        service=service,
        provider=provider,
        sessions=sessions,
        compiler=compiler,
        session=session,
    )


def _send(
    harness: Harness,
    text: str = "요즘 KRX에서 통할 만한 모멘텀 전략 하나 만들어 줘",
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[ChatEvent]:
    return list(
        harness.service.send(harness.session.session_id, text, CONTEXT, cancelled=cancelled)
    )


def _tool_payload(harness: Harness, index: int = 0) -> dict[str, object]:
    payload = json.loads(harness.provider.tool_results[index].content)
    assert isinstance(payload, dict)
    return payload


def _proposal_call(source_text: str, call_id: str = "call-propose") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name=PROPOSE_STRATEGY,
        arguments={
            "title": "KRX 12-1 모멘텀",
            "summary": "12개월 모멘텀에서 최근 1개월을 뺀 순위로 상위 30종목을 담는다.",
            "rationale": "모멘텀 프리미엄은 KRX에서도 관측된다 (https://example.com/krx-momentum).",
            "sources": [{"title": "KRX 모멘텀 리뷰", "url": "https://example.com/krx-momentum"}],
            "source_text": source_text,
        },
    )


# -- (a) 텍스트만 -------------------------------------------------------------------------------


def test_text_only_turn_streams_events_and_stores_the_assistant_message() -> None:
    harness = _harness((TextDelta("안녕"), TextDelta("하세요"), Done("end_turn")))

    events = _send(harness, "안녕?")

    assert events == [TextDelta("안녕"), TextDelta("하세요"), Done("end_turn")]
    messages = harness.sessions.messages(harness.session.session_id)
    assert [message.role for message in messages] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert messages[0].text == "안녕?"
    assert messages[1].text == "안녕하세요"
    # 이벤트 영속화는 `AssistantTurnRunner`의 일이다. 서비스는 메시지만 남긴다.
    assert harness.sessions.events(harness.session.session_id) == ()


def test_turn_request_carries_the_tool_catalog_history_and_web_search() -> None:
    harness = _harness((Done("end_turn"),))

    _send(harness, "첫 질문")

    request = harness.provider.requests[0]
    assert request.tools == ASSISTANT_TOOLS
    assert request.research == frozenset({ResearchCapability.WEB_SEARCH})
    assert [message.text for message in request.messages] == ["첫 질문"]
    assert request.max_tool_rounds == 12
    assert request.max_search_uses == DEFAULT_MAX_SEARCH_USES
    assert request.max_output_tokens_per_call == DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL
    assert request.max_turn_output_tokens == DEFAULT_MAX_TURN_OUTPUT_TOKENS
    assert "KRX" in request.system


def test_history_accumulates_across_turns() -> None:
    harness = _harness((TextDelta("첫 답"), Done("end_turn")))

    _send(harness, "첫 질문")
    _send(harness, "둘째 질문")

    request = harness.provider.requests[1]
    assert [message.text for message in request.messages] == ["첫 질문", "첫 답", "둘째 질문"]


# -- (b) 카탈로그 도구 --------------------------------------------------------------------------


def test_list_equity_fields_returns_the_port_catalog() -> None:
    call = ToolCall(call_id="call-1", name=LIST_EQUITY_FIELDS, arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))

    events = _send(harness)

    payload = _tool_payload(harness)
    fields = payload["fields"]
    assert isinstance(fields, list)
    returned = {str(row["id"]) for row in fields if isinstance(row, dict)}
    expected = {profile.field_id for profile in MockEquityDataAdapter.demo().list_fields()}
    assert returned == expected
    summaries = [event for event in events if isinstance(event, ToolResultSummary)]
    assert [(item.call_id, item.name, item.ok) for item in summaries] == [
        ("call-1", LIST_EQUITY_FIELDS, True)
    ]


def test_list_factor_catalog_reports_direction_and_availability() -> None:
    call = ToolCall(call_id="call-1", name=LIST_FACTOR_CATALOG, arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))

    _send(harness)

    payload = _tool_payload(harness)
    factors = payload["factors"]
    assert isinstance(factors, list)
    registry = build_default_factor_registry()
    first = factors[0]
    assert isinstance(first, dict)
    assert set(first) == {"id", "label", "direction", "availability", "required_field_ids"}
    assert len(factors) == len(registry.all())


def test_read_current_strategy_returns_the_turn_context() -> None:
    call = ToolCall(call_id="call-1", name=READ_CURRENT_STRATEGY, arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))

    _send(harness)

    payload = _tool_payload(harness)
    assert payload["source_text"] == CONTEXT.source_text
    assert payload["source_format"] == "yaml"
    assert payload["diagnostics"] == list(CONTEXT.diagnostics)
    assert payload["environment"] == CONTEXT.environment


def test_validate_strategy_yaml_runs_the_compiler_port() -> None:
    call = ToolCall(
        call_id="call-1",
        name=VALIDATE_STRATEGY_YAML,
        arguments={"source_text": INVALID_YAML},
    )
    harness = _harness((ToolStep(call), Done("end_turn")))

    _send(harness)

    assert harness.compiler.compiled == [INVALID_YAML]
    payload = _tool_payload(harness)
    assert payload["ok"] is False
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0] == {
        "code": "strategy.factor.unknown",
        "pointer": "/factors/0/factor_id",
        "message": "알 수 없는 팩터 식별자",
        "severity": "error",
    }
    assert harness.provider.tool_results[0].ok is False


def test_an_unknown_tool_name_comes_back_as_a_tool_error_not_a_crash() -> None:
    call = ToolCall(call_id="call-1", name="run_backtest", arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))

    _send(harness)

    result = harness.provider.tool_results[0]
    assert result.ok is False
    assert "run_backtest" in result.content


# -- (c) 제안 성공 ------------------------------------------------------------------------------


def test_a_valid_proposal_is_emitted_before_the_tool_result_and_stored() -> None:
    harness = _harness(
        (ToolStep(_proposal_call(VALID_YAML)), Done("end_turn")),
        valid_sources=(VALID_YAML,),
    )

    events = _send(harness)

    kinds = [type(event).__name__ for event in events]
    assert kinds == ["ToolCall", "Proposal", "ToolResultSummary", "Done"]
    proposal_event = events[1]
    assert isinstance(proposal_event, Proposal)
    proposal = proposal_event.proposal
    assert proposal.title == "KRX 12-1 모멘텀"
    assert proposal.source_text == VALID_YAML
    assert proposal.source_format == "yaml"
    assert proposal.compile.ok is True
    assert proposal.compile.spec_hash == "spec-hash-1"
    assert proposal.sources == (
        Source(title="KRX 모멘텀 리뷰", url="https://example.com/krx-momentum"),
    )
    assert harness.provider.tool_results[0].ok is True


def test_an_invalid_proposal_comes_back_to_the_model_with_diagnostics() -> None:
    harness = _harness(
        (ToolStep(_proposal_call(INVALID_YAML)), Done("end_turn")),
        valid_sources=(VALID_YAML,),
    )

    events = _send(harness)

    assert not any(isinstance(event, Proposal) for event in events)
    result = harness.provider.tool_results[0]
    assert result.ok is False
    payload = json.loads(result.content)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["pointer"] == "/factors/0/factor_id"


def test_a_proposal_without_source_text_is_rejected_without_compiling() -> None:
    call = ToolCall(call_id="call-1", name=PROPOSE_STRATEGY, arguments={"title": "제목만"})
    harness = _harness((ToolStep(call), Done("end_turn")), valid_sources=(VALID_YAML,))

    _send(harness)

    assert harness.compiler.compiled == []
    assert harness.provider.tool_results[0].ok is False
    assert "source_text" in harness.provider.tool_results[0].content


# -- (d) 제안 재시도 상한 -----------------------------------------------------------------------


def test_three_invalid_proposals_end_the_turn_with_proposal_invalid() -> None:
    script: tuple[ScriptStep, ...] = (
        ToolStep(_proposal_call(INVALID_YAML, "call-1")),
        ToolStep(_proposal_call(INVALID_YAML, "call-2")),
        ToolStep(_proposal_call(INVALID_YAML, "call-3")),
        Done("end_turn"),
    )
    harness = _harness(script, valid_sources=(VALID_YAML,), max_proposal_attempts=3)

    events = _send(harness)

    assert events[-1] == Failure(
        code=FailureCode.PROPOSAL_INVALID,
        message=(
            "제안이 3회 연속 검증에 실패해 턴을 종료했습니다 — "
            f"session_id={harness.session.session_id} attempts=3 max_attempts=3"
        ),
    )
    # 손에 든 이벤트는 종료 사유가 세워져 있어도 그대로 내보낸다(취소 경로와 같은 규칙).
    assert isinstance(events[-2], Done)
    assert len(harness.compiler.compiled) == 3


def test_text_streamed_after_the_last_failed_proposal_is_kept() -> None:
    """모델이 도구 결과 뒤에 한 문장을 더 흘리는 흔한 경로. 그 문장이 사라지면 안 된다."""
    script: tuple[ScriptStep, ...] = (
        ToolStep(_proposal_call(INVALID_YAML, "call-1")),
        ToolStep(_proposal_call(INVALID_YAML, "call-2")),
        ToolStep(_proposal_call(INVALID_YAML, "call-3")),
        TextDelta("죄송합니다. 유효한 전략을 만들지 못했습니다."),
        Done("end_turn"),
    )
    harness = _harness(script, valid_sources=(VALID_YAML,), max_proposal_attempts=3)

    events = _send(harness)

    assert events[-2] == TextDelta("죄송합니다. 유효한 전략을 만들지 못했습니다.")
    failure = events[-1]
    assert isinstance(failure, Failure)
    assert failure.code is FailureCode.PROPOSAL_INVALID
    messages = harness.sessions.messages(harness.session.session_id)
    assert messages[1].text == "죄송합니다. 유효한 전략을 만들지 못했습니다."


# -- 공급자가 집행하는 상한 -----------------------------------------------------------------------
#
# 라운드·검색·토큰 상한의 집행은 루프의 주인인 adapter 몫이다(spec D3). 서비스는 값만 싣고,
# 공급자가 낸 Failure를 그대로 흘려보낸다. 그 Failure가 턴 상태가 되는지는 러너 테스트가 본다.


def test_limits_are_passed_to_the_provider_but_not_enforced_here() -> None:
    call = ToolCall(call_id="call-1", name=LIST_EQUITY_FIELDS, arguments={})
    script: tuple[ScriptStep, ...] = (ToolStep(call), ToolStep(call), ToolStep(call), Done("x"))
    harness = _harness(script, max_tool_rounds=2)

    events = _send(harness)

    assert harness.provider.requests[0].max_tool_rounds == 2
    # 상한이 2여도 세 번의 도구 호출이 전부 실행된다. 끊는 것은 공급자다.
    assert [result.ok for result in harness.provider.tool_results] == [True, True, True]
    assert not any(isinstance(event, Failure) for event in events)


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.TOOL_ROUNDS_EXCEEDED,
        FailureCode.TOKEN_BUDGET_EXCEEDED,
        FailureCode.OUTPUT_TRUNCATED,
    ],
)
def test_a_provider_failure_passes_through_untouched(code: FailureCode) -> None:
    failure = Failure(code=code, message="공급자가 상한에서 멈췄습니다")
    harness = _harness((TextDelta("앞부분"), failure))

    events = _send(harness)

    assert events == [TextDelta("앞부분"), failure]
    assert harness.sessions.messages(harness.session.session_id)[1].text == "앞부분"


def test_usage_is_recorded_and_streamed_without_enforcement() -> None:
    script: tuple[ScriptStep, ...] = (
        Usage(input_tokens=100, output_tokens=90_000),
        Done("end_turn"),
    )
    harness = _harness(script)

    events = _send(harness)

    # 턴 기본 예산(64000)을 넘겼지만 서비스는 끊지 않는다.
    assert events == [Usage(input_tokens=100, output_tokens=90_000), Done("end_turn")]


# -- (f) 취소 -----------------------------------------------------------------------------------


def test_a_cancelled_turn_keeps_the_event_it_was_already_holding() -> None:
    """spec D3: 취소는 `Failure(CANCELLED)`, 이미 스트리밍된 텍스트는 보존한다."""
    harness = _harness((TextDelta("안녕"), Done("end_turn")))

    events = _send(harness, cancelled=lambda: True)

    assert [type(event).__name__ for event in events] == ["TextDelta", "Failure"]
    assert events[0] == TextDelta("안녕")
    failure = events[1]
    assert isinstance(failure, Failure)
    assert failure.code is FailureCode.CANCELLED
    assert harness.sessions.messages(harness.session.session_id)[1].text == "안녕"


def test_text_streamed_before_the_cancel_survives_as_one_message() -> None:
    """두 번째 이벤트에서 취소가 켜져도 그때까지의 조각은 전부 남는다."""
    harness = _harness((TextDelta("안"), TextDelta("녕"), TextDelta("하세요"), Done("end")))
    seen = 0

    def cancelled() -> bool:
        nonlocal seen
        seen += 1
        return seen >= 2

    events = _send(harness, cancelled=cancelled)

    assert [type(event).__name__ for event in events] == ["TextDelta", "TextDelta", "Failure"]
    assert [event for event in events if isinstance(event, TextDelta)] == [
        TextDelta("안"),
        TextDelta("녕"),
    ]
    messages = harness.sessions.messages(harness.session.session_id)
    assert messages[1].text == "안녕"


# -- (g) 공급자 예외 ----------------------------------------------------------------------------


def test_a_provider_exception_becomes_a_provider_failure_event() -> None:
    harness = _harness((TextDelta("안"), RaiseStep(RuntimeError("upstream 503"))))

    events = _send(harness)

    assert events[0] == TextDelta("안")
    failure = events[-1]
    assert isinstance(failure, Failure)
    assert failure.code is FailureCode.PROVIDER
    assert "error_type=RuntimeError" in failure.message
    assert "anthropic" in failure.message
    # 예외 본문에는 요청 URL·헤더·키가 섞여 들어올 수 있으므로 밖으로 내보내지 않는다(spec D2).
    assert "upstream 503" not in failure.message
    assert "sk-fake-0001" not in failure.message


# -- (h) 활성 프로파일 없음 ---------------------------------------------------------------------


def test_creating_a_session_without_an_active_provider_is_rejected() -> None:
    harness = _harness((), with_profile=False)

    with pytest.raises(NoActiveProviderError):
        harness.service.create_session(DOCUMENT, title="세션")


def test_sessions_are_listed_per_document() -> None:
    harness = _harness((Done("end_turn"),))
    other = DocumentRef(strategy_id=None, revision=None, draft_id="draft-9")
    harness.service.create_session(other, title="초안 세션")

    assert [session.title for session in harness.service.list_for_document(DOCUMENT)] == [
        "테스트 세션"
    ]
    assert [session.title for session in harness.service.list_for_document(other)] == ["초안 세션"]
    assert harness.service.get(harness.session.session_id) == harness.session


def test_a_document_ref_needs_at_least_one_identifier() -> None:
    with pytest.raises(ValueError, match="document_ref"):
        DocumentRef(strategy_id=None, revision=None, draft_id=None)


# -- [P2] 비밀은 로그에도 남지 않는다 -----------------------------------------------------------


def test_a_provider_exception_leaves_no_secret_in_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """완료 정의 3: 키가 응답·로그·DB 어디에도 평문으로 나오지 않는다."""
    leaked = "invalid x-api-key: sk-ant-SECRET123"
    harness = _harness((TextDelta("안"), RaiseStep(RuntimeError(leaked))))

    with caplog.at_level(logging.DEBUG):
        events = _send(harness)

    rendered = "; ".join(
        [record.getMessage() for record in caplog.records]
        + [record.exc_text or "" for record in caplog.records]
    )
    assert "sk-ant-SECRET123" not in rendered
    assert leaked not in rendered
    assert "error_type=RuntimeError" in rendered
    # 사유는 여전히 남는다 — 진단을 지우자는 규칙이 아니다.
    failure = events[-1]
    assert isinstance(failure, Failure)
    assert failure.code is FailureCode.PROVIDER


# -- [P3] 도구 실행 중 우리 코드의 예외는 공급자 탓이 아니다 --------------------------------------


class _ExplodingContextBuilder(AssistantContextBuilder):
    """`tool_result`가 터지는 컨텍스트 빌더(카탈로그 포트 오류·직렬화 오류를 흉내 낸다)."""

    def tool_result(self, call: ToolCall, context: TurnContext) -> ToolResult:
        raise RuntimeError("catalog port exploded")


def test_a_tool_failure_comes_back_as_a_tool_error_not_a_provider_failure() -> None:
    call = ToolCall(call_id="call-1", name=LIST_EQUITY_FIELDS, arguments={})
    harness = _harness(
        (ToolStep(call), Done("end_turn")),
        context_builder=_ExplodingContextBuilder(
            equity_data=MockEquityDataAdapter.demo(),
            factor_registry=build_default_factor_registry(),
            compiler=FakeStrategyCompiler(),
            today=lambda: TODAY,
        ),
    )

    events = _send(harness)

    assert not any(isinstance(event, Failure) for event in events)
    result = harness.provider.tool_results[0]
    assert result.ok is False
    assert "error_type=RuntimeError" in result.content
    assert "catalog port exploded" not in result.content


# -- [P3] 제안 실패는 "연속" 3회다 ---------------------------------------------------------------


def test_a_successful_proposal_resets_the_retry_streak() -> None:
    script: tuple[ScriptStep, ...] = (
        ToolStep(_proposal_call(INVALID_YAML, "call-1")),
        ToolStep(_proposal_call(VALID_YAML, "call-2")),
        ToolStep(_proposal_call(INVALID_YAML, "call-3")),
        ToolStep(_proposal_call(INVALID_YAML, "call-4")),
        Done("end_turn"),
    )
    harness = _harness(script, valid_sources=(VALID_YAML,), max_proposal_attempts=3)

    events = _send(harness)

    # 누적으로 세면 3·4번째에서 끊긴다. 연속으로 세면 끊기지 않는다(spec D3).
    assert not any(
        isinstance(event, Failure) and event.code is FailureCode.PROPOSAL_INVALID
        for event in events
    )
    assert any(isinstance(event, Proposal) for event in events)


# -- (k) 시스템 프롬프트 ------------------------------------------------------------------------


def _prompt_builder() -> AssistantContextBuilder:
    return AssistantContextBuilder(
        equity_data=MockEquityDataAdapter.demo(),
        factor_registry=build_default_factor_registry(),
        compiler=FakeStrategyCompiler(),
        today=lambda: TODAY,
    )


def _discriminator_name(schema: Mapping[str, object]) -> str:
    """스키마가 스스로 붙인 판별자 속성 이름. 테스트도 `"kind"`를 손으로 적지 않는다."""
    found = _find_marker(schema)
    assert found is not None, "runtime schema has no discriminator marker"
    return found


def _find_marker(node: object) -> str | None:
    if isinstance(node, Mapping):
        marker = node.get("discriminator")
        if isinstance(marker, Mapping) and isinstance(marker.get("propertyName"), str):
            return str(marker["propertyName"])
        for value in node.values():
            found = _find_marker(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_marker(item)
            if found is not None:
                return found
    return None


def _schema_vocabulary() -> tuple[tuple[str, ...], tuple[str, ...]]:
    schema = strategy_document_schema()
    sections = tuple(name for name in schema["properties"] if name != "schema_version")
    discriminator = _discriminator_name(schema)
    defs = schema["$defs"]
    kinds = tuple(
        str(definition["properties"][discriminator]["const"])
        for definition in defs.values()
        if discriminator in definition["properties"]
    )
    return sections, kinds


def test_system_prompt_lists_the_runtime_schema_sections_and_node_kinds() -> None:
    sections, kinds = _schema_vocabulary()

    prompt = _prompt_builder().system_prompt()

    # 어휘가 비어 있으면 아래 두 단언이 공허하게 통과한다. 그 통과가 이 가드의 유일한 실패 모드다.
    assert sections
    assert kinds
    assert [section for section in sections if section not in prompt] == []
    assert [kind for kind in kinds if kind not in prompt] == []
    assert TODAY.isoformat() in prompt


def test_the_summary_follows_the_schema_when_the_discriminator_is_renamed() -> None:
    """판별자 키가 바뀌어도 요약의 종류 줄이 조용히 사라지면 안 된다."""
    renamed = {
        "properties": {"signal": {"$ref": "#/$defs/Node"}},
        "required": ["signal"],
        "$defs": {
            "FieldNode": {
                "type": "object",
                "properties": {"node_type": {"type": "string", "const": "field"}},
            },
            "BinaryNode": {
                "type": "object",
                "properties": {"node_type": {"type": "string", "const": "binary"}},
            },
            "Node": {
                "oneOf": [{"$ref": "#/$defs/FieldNode"}],
                "discriminator": {"propertyName": "node_type"},
            },
        },
    }
    builder = AssistantContextBuilder(
        equity_data=MockEquityDataAdapter.demo(),
        factor_registry=build_default_factor_registry(),
        compiler=FakeStrategyCompiler(),
        today=lambda: TODAY,
        schema=lambda: renamed,
    )

    prompt = builder.system_prompt()

    assert "사용할 수 있는 node_type 값" in prompt
    assert "field, binary" in prompt


def test_the_prompt_template_never_hand_writes_schema_vocabulary() -> None:
    """DEFECT 방지: 필드 이름을 템플릿에 손으로 적으면 스키마가 바뀔 때 조용히 stale해진다."""
    from strategy_workbench.application.assistant_chat._prompt import SYSTEM_PROMPT_TEMPLATE

    sections, kinds = _schema_vocabulary()

    hand_written = [
        word
        for word in (*sections, *kinds)
        if re.search(rf"(?<![A-Za-z_]){re.escape(word)}(?![A-Za-z_])", SYSTEM_PROMPT_TEMPLATE)
    ]
    assert hand_written == []
