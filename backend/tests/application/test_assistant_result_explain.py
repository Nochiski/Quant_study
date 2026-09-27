"""결과 설명 세션의 모드 분기 테스트 (결과 설명 spec R3·R5).

결과 세션은 실행 하나에 붙고, 모드는 요청이 아니라 세션이 정한다. 모델이 받는 도구·프롬프트·검색이
전략 세션과 다르고, 준 도구 밖의 호출은 도구 오류로 돌아간다. 특히 `propose_strategy`가 결과
세션에서 제안을 만들지 않는 것이 이 모드 경계의 핵심이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.assistant_chat.facade.chat import (
    AssistantChatService,
    BacktestResultUnavailableError,
    ChatSession,
    DocumentRef,
    TurnContext,
    TurnContextMismatchError,
)
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.result_context import (
    summarize_backtest_result,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    Proposal,
    ProviderKind,
    TextDelta,
    ToolCall,
)
from strategy_workbench.domain.assistant.facade.tools import (
    ASSISTANT_TOOLS,
    PROPOSE_STRATEGY,
    READ_BACKTEST_RESULT,
    READ_CURRENT_STRATEGY,
    RESULT_EXPLAIN_TOOLS,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

from ..assistant_result_samples import SAMPLE_RUN_ID, sample_backtest_result
from ._assistant_fakes import (
    FakeBacktestResults,
    FakeStrategyCompiler,
    InMemoryChatSessionRepository,
    InMemoryProviderProfileRepository,
    InMemoryProviderSecretStore,
    ScriptedProvider,
    ScriptStep,
    ToolStep,
    sequential_ids,
)

FIXED_NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
TODAY = date(2026, 9, 27)
RUN_REF = DocumentRef(strategy_id=None, revision=None, draft_id=None, run_id=SAMPLE_RUN_ID)
STRATEGY_REF = DocumentRef(strategy_id="strategy-1", revision=3, draft_id=None)
VALID_YAML = "schema_version: '1.1'\ntitle: 모멘텀\n"
STRATEGY_CONTEXT = TurnContext(
    source_text=VALID_YAML, source_format="yaml", environment=None, diagnostics=()
)


@dataclass(frozen=True)
class Harness:
    service: AssistantChatService
    provider: ScriptedProvider
    sessions: InMemoryChatSessionRepository
    results: FakeBacktestResults


def _harness(script: tuple[ScriptStep, ...] = (Done("end_turn"),)) -> Harness:
    profiles_repository = InMemoryProviderProfileRepository()
    secrets = InMemoryProviderSecretStore()
    provider = ScriptedProvider(script=script)
    providers = {ProviderKind.ANTHROPIC: provider}
    profiles = ProviderProfileService(
        profiles_repository,
        secrets,
        providers,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("profile"),
    )
    profiles.create(kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001")
    compiler = FakeStrategyCompiler(valid_sources=(VALID_YAML,))
    results = FakeBacktestResults({SAMPLE_RUN_ID: sample_backtest_result()})
    sessions = InMemoryChatSessionRepository()
    service = AssistantChatService(
        sessions,
        profiles,
        secrets,
        providers,
        compiler,
        AssistantContextBuilder(
            equity_data=MockEquityDataAdapter.demo(),
            factor_registry=build_default_factor_registry(),
            compiler=compiler,
            backtest_results=results,
            today=lambda: TODAY,
        ),
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("session"),
    )
    return Harness(service=service, provider=provider, sessions=sessions, results=results)


def _session(harness: Harness, reference: DocumentRef = RUN_REF) -> ChatSession:
    return harness.service.create_session(reference, title="결과 설명")


def _send(
    harness: Harness,
    session: ChatSession,
    text: str = "이 결과 좋은 거야?",
    context: TurnContext | None = None,
) -> list[ChatEvent]:
    return list(harness.service.send(session.session_id, text, context, turn_id="turn-1"))


# -- 세션 --------------------------------------------------------------------------------------


def test_a_result_session_is_created_for_a_completed_run() -> None:
    harness = _harness()

    session = _session(harness)

    assert session.document_ref == RUN_REF
    assert harness.service.list_for_document(RUN_REF) == (session,)


def test_a_result_session_for_an_unknown_run_is_refused() -> None:
    harness = _harness()
    missing = DocumentRef(strategy_id=None, revision=None, draft_id=None, run_id="run-gone")

    with pytest.raises(BacktestResultUnavailableError, match="run_id='run-gone'"):
        _session(harness, missing)

    assert harness.service.list_for_document(missing) == ()


# -- 모드 ---------------------------------------------------------------------------------------


def test_a_result_turn_offers_only_the_result_tool_without_web_search() -> None:
    harness = _harness()
    session = _session(harness)

    _send(harness, session)

    request = harness.provider.requests[0]
    assert request.tools == RESULT_EXPLAIN_TOOLS
    assert request.research == frozenset()
    assert READ_BACKTEST_RESULT in request.system
    assert TODAY.isoformat() in request.system
    # 전략 세션의 작업 순서(문서 읽기·제안)가 섞여 들어가면 모델이 없는 도구를 찾는다.
    assert PROPOSE_STRATEGY not in request.system
    assert READ_CURRENT_STRATEGY not in request.system


def test_a_strategy_turn_still_gets_the_strategy_tools() -> None:
    harness = _harness()
    session = _session(harness, STRATEGY_REF)

    _send(harness, session, "모멘텀 전략 만들어 줘", STRATEGY_CONTEXT)

    assert harness.provider.requests[0].tools == ASSISTANT_TOOLS
    assert READ_BACKTEST_RESULT not in harness.provider.requests[0].system


def test_the_result_tool_returns_the_server_summary_of_the_run() -> None:
    call = ToolCall(call_id="call-1", name=READ_BACKTEST_RESULT, arguments={})
    harness = _harness((ToolStep(call), TextDelta("좋은 편입니다."), Done("end_turn")))
    session = _session(harness)

    _send(harness, session)

    result = harness.provider.tool_results[0]
    assert result.ok
    assert result.content == summarize_backtest_result(sample_backtest_result())
    assert json.loads(result.content)["run"]["run_id"] == SAMPLE_RUN_ID


def test_a_proposal_in_a_result_session_is_refused_and_never_reaches_the_screen() -> None:
    """프롬프트가 막아도 모델이 부를 수 있다. 제안은 서비스가 도구 목록으로 막는다(spec R5)."""
    call = ToolCall(
        call_id="call-propose",
        name=PROPOSE_STRATEGY,
        arguments={
            "title": "몰래 온 제안",
            "summary": "",
            "rationale": "",
            "sources": [],
            "source_text": VALID_YAML,
        },
    )
    harness = _harness((ToolStep(call), Done("end_turn")))
    session = _session(harness)

    events = _send(harness, session)

    assert not any(isinstance(event, Proposal) for event in events)
    refused = harness.provider.tool_results[0]
    assert not refused.ok
    assert PROPOSE_STRATEGY in refused.content


@pytest.mark.parametrize("name", [READ_CURRENT_STRATEGY, "list_equity_fields"])
def test_strategy_tools_are_unsupported_in_a_result_session(name: str) -> None:
    call = ToolCall(call_id="call-1", name=name, arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))
    session = _session(harness)

    _send(harness, session)

    assert not harness.provider.tool_results[0].ok


def test_the_result_tool_is_unsupported_in_a_strategy_session() -> None:
    call = ToolCall(call_id="call-1", name=READ_BACKTEST_RESULT, arguments={})
    harness = _harness((ToolStep(call), Done("end_turn")))
    session = _session(harness, STRATEGY_REF)

    _send(harness, session, "결과 알려 줘", STRATEGY_CONTEXT)

    assert not harness.provider.tool_results[0].ok


# -- 턴 거절 -----------------------------------------------------------------------------------


def test_a_result_turn_with_document_context_is_refused_before_anything_is_stored() -> None:
    harness = _harness()
    session = _session(harness)

    with pytest.raises(TurnContextMismatchError, match="result session") as raised:
        _send(harness, session, context=STRATEGY_CONTEXT)

    assert session.session_id in str(raised.value)
    assert harness.sessions.messages(session.session_id) == ()
    assert harness.provider.requests == []


def test_a_strategy_turn_without_document_context_is_refused() -> None:
    harness = _harness()
    session = _session(harness, STRATEGY_REF)

    with pytest.raises(TurnContextMismatchError, match="strategy session"):
        _send(harness, session, "모멘텀 전략 만들어 줘")

    assert harness.sessions.messages(session.session_id) == ()


def test_a_result_that_disappeared_after_the_session_was_made_refuses_the_turn() -> None:
    """실행 레지스트리는 프로세스 안에만 있다. 재시작 뒤 결과 세션 이력은 남지만 새 턴은 못 돈다."""
    harness = _harness()
    session = _session(harness)
    harness.results.results.clear()

    with pytest.raises(BacktestResultUnavailableError, match=SAMPLE_RUN_ID):
        _send(harness, session)

    assert harness.sessions.messages(session.session_id) == ()
