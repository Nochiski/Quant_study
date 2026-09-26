"""대본 공급자 adapter (테스트 전용, WORKFLOW B-05).

브라우저 e2e가 이 adapter 위에서 돈다. e2e가 깨졌을 때 "대본이 틀렸나 화면이 틀렸나"를 15분짜리
Playwright 실행 없이 가르려면, 대본 자체의 계약이 여기서 먼저 고정되어 있어야 한다.

여기서 보는 것은 세 가지다. 질문 → 시나리오 선택, 각 시나리오가 내는 이벤트 열, 그리고 취소
신호를 보면 멈춘다는 것. 제안이 실제로 compile을 통과하는지는 application이 소유한 사실이라
`tests/application`이 본다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from strategy_workbench.adapters.outbound.llm_scripted.facade.provider import (
    DEFAULT_SCRIPTED_MODEL,
    SCRIPTED_MARKER,
    SLOW_ANSWER_DELAY_SCALE,
    ScriptedLlmProvider,
    concept_answer,
    factor_window_proposal,
    idea_to_new_strategy,
    scenario_for,
    search_then_failure,
    simple_answer,
    slow_answer,
    tool_then_proposal,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    ChatRole,
    Done,
    ProviderKind,
    ProviderProfile,
    SearchActivity,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnRequest,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import (
    ASSISTANT_TOOLS,
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
)

CURRENT_SOURCE = 'title: "원래 제목"\nportfolio:\n  selection_count: 20\n'


def _profile() -> ProviderProfile:
    return ProviderProfile(
        profile_id="profile-1",
        kind=ProviderKind.ANTHROPIC,
        label="대본",
        model=DEFAULT_SCRIPTED_MODEL,
        base_url=None,
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
        active=True,
    )


def _request(text: str) -> TurnRequest:
    created_at = datetime(2026, 9, 21, tzinfo=UTC)
    return TurnRequest(
        system="system",
        messages=(
            ChatMessage(role=ChatRole.ASSISTANT, text="이전 답", created_at=created_at),
            ChatMessage(role=ChatRole.USER, text=text, created_at=created_at),
        ),
        tools=ASSISTANT_TOOLS,
        research=frozenset(),
        max_tool_rounds=12,
        max_search_uses=8,
        max_output_tokens_per_call=16_000,
        max_turn_output_tokens=64_000,
    )


class _ToolRecorder:
    """`execute_tool` 콜백 대역. `read_current_strategy`만 진짜처럼 답한다."""

    def __init__(self, *, source_text: str = CURRENT_SOURCE) -> None:
        self.calls: list[ToolCall] = []
        self._source_text = source_text

    def __call__(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if call.name == READ_CURRENT_STRATEGY:
            payload = {
                "source_text": self._source_text,
                "source_format": "yaml",
                "environment": None,
                "diagnostics": [],
            }
            return ToolResult(
                call_id=call.call_id, ok=True, content=json.dumps(payload, ensure_ascii=False)
            )
        return ToolResult(call_id=call.call_id, ok=False, content="검증 실패")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("모멘텀 전략이 뭐야?", simple_answer),
        ("KRX 모멘텀 전략 하나 제안해 줘", tool_then_proposal),
        ("요즘 시장을 검색해서 알려 줘", search_then_failure),
        ("천천히 설명해 줘", slow_answer),
        # 두 낱말이 같이 있으면 앞선 항목이 이긴다 — 선택이 질문 순서에 흔들리지 않게 고정한다.
        ("검색해서 제안해 줘", tool_then_proposal),
        ("모멘텀 창을 줄인 안을 제안해 줘", factor_window_proposal),
        # "새 전략"은 "제안"보다 앞이라 "새 전략으로 제안해 줘"도 전체 전략을 쓰는 대본을 고른다.
        ("많이 오른 대형주를 사는 새 전략을 만들어 줘", idea_to_new_strategy),
        ("새 전략으로 제안해 줘", idea_to_new_strategy),
        ("샤프 비율이 무슨 뜻이야?", concept_answer),
    ],
)
def test_the_question_picks_the_scenario(text: str, expected: object) -> None:
    assert scenario_for(text).run is expected


def test_the_simple_scenario_streams_text_and_ends() -> None:
    events = list(simple_answer(_ToolRecorder()))

    assert [type(event) for event in events] == [TextDelta, TextDelta, Usage, Done]


def test_the_slow_scenario_holds_the_turn_open_for_over_twenty_seconds() -> None:
    """재연결을 재현하려면 턴 길이가 머신 속도가 아니라 대본이 정해야 한다(리뷰 R1-003).

    브라우저가 새로고침하고 다시 붙는 데 드는 1~2초보다 한참 길어야, 빠른 장비에서 턴이 먼저
    끝나 재연결 경로가 조용히 "이미 끝난 턴의 이력 읽기"로 바뀌는 일이 없다.
    """
    plan = scenario_for("천천히 설명해 줘")
    events = list(plan.run(_ToolRecorder()))

    naps: list[float] = []
    provider = ScriptedLlmProvider(ProviderKind.ANTHROPIC, sleep=naps.append)
    list(
        provider.stream_turn(
            "sk-fake", _profile(), _request("천천히 설명해 줘"), _ToolRecorder(), lambda: False
        )
    )

    assert plan.delay_scale == SLOW_ANSWER_DELAY_SCALE
    assert len(naps) == len(events)
    assert sum(naps) > 20.0


def test_the_proposal_scenario_reads_the_document_and_changes_only_its_title() -> None:
    tools = _ToolRecorder()

    events = list(tool_then_proposal(tools))

    assert [call.name for call in tools.calls] == [READ_CURRENT_STRATEGY, PROPOSE_STRATEGY]
    proposed = tools.calls[1].arguments["source_text"]
    assert isinstance(proposed, str)
    assert proposed != CURRENT_SOURCE
    assert proposed.splitlines()[1:] == CURRENT_SOURCE.splitlines()[1:]
    assert proposed.startswith('title: "KRX 12-1 모멘텀"')
    # 도구 호출 뒤에는 언제나 이벤트가 하나 더 있어야 application이 큐(제안·도구 결과)를 비운다.
    assert _followed_by_another_event(events, PROPOSE_STRATEGY)


def test_the_proposal_scenario_submits_a_broken_document_when_the_tool_fails() -> None:
    """도구 경로가 끊기면 조용히 그럴듯한 원문으로 되돌아가지 않는다 — e2e가 그 사실을 봐야 한다."""

    def failing(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, ok=False, content="읽지 못함")

    calls: list[ToolCall] = []

    def record(call: ToolCall) -> ToolResult:
        calls.append(call)
        return failing(call)

    list(tool_then_proposal(record))

    assert calls[1].arguments["source_text"] == "title: 깨진 제안\n"


GRAPH_SOURCE = (
    'title: "원래 제목"\n'
    "factors:\n"
    "  - factor_id: momentum\n"
    "    graph:\n"
    "      nodes:\n"
    "        - kind: time_series\n"
    "          node_id: mom_252\n"
    "          window: 252\n"
    "        - kind: time_series\n"
    "          node_id: mom_60\n"
    "          window: 60\n"
)


def test_the_window_scenario_changes_the_title_and_the_first_window_only() -> None:
    """팩터 그래프를 바꿔야 frontend가 캐시에 없는 팩터 계획을 새로 조회한다(C-02 리뷰 P1-1)."""
    tools = _ToolRecorder(source_text=GRAPH_SOURCE)

    events = list(factor_window_proposal(tools))

    assert [call.name for call in tools.calls] == [READ_CURRENT_STRATEGY, PROPOSE_STRATEGY]
    proposed = tools.calls[1].arguments["source_text"]
    assert isinstance(proposed, str)
    changed = [
        (before, after)
        for before, after in zip(GRAPH_SOURCE.splitlines(), proposed.splitlines(), strict=True)
        if before != after
    ]
    assert changed == [
        ('title: "원래 제목"', 'title: "KRX 6개월 모멘텀"'),
        ("          window: 252", "          window: 126"),
    ]
    assert _followed_by_another_event(events, PROPOSE_STRATEGY)


def test_the_window_scenario_submits_a_broken_document_without_a_window() -> None:
    """창이 없는 문서에서 그래프가 그대로인 제안으로 되돌아가지 않는다.

    되돌아가면 e2e가 캐시된 팩터 계획 경로를 밟고도 통과한다.
    """
    tools = _ToolRecorder()

    list(factor_window_proposal(tools))

    assert tools.calls[1].arguments["source_text"] == "title: 깨진 제안\n"


def test_the_search_scenario_shows_sources_then_retries_the_proposal_three_times() -> None:
    tools = _ToolRecorder()

    events = list(search_then_failure(tools))

    search = next(event for event in events if isinstance(event, SearchActivity))
    assert [source.url for source in search.sources] == [
        "https://example.com/krx-momentum",
        "https://example.com/factor-review",
    ]
    assert [call.name for call in tools.calls] == [PROPOSE_STRATEGY] * 3
    # 세 번째 거절 뒤 application이 턴을 끊는다. 그 판정은 다음 이벤트 앞에서 일어나므로 대본은
    # 마지막 도구 호출 뒤에도 이벤트를 하나 더 낸다.
    assert isinstance(events[-1], Done)


def test_the_probe_succeeds_without_touching_a_network() -> None:
    provider = ScriptedLlmProvider(ProviderKind.OPENAI)

    result = provider.probe("sk-fake", model=DEFAULT_SCRIPTED_MODEL, base_url=None)

    assert result.ok is True
    assert provider.kind is ProviderKind.OPENAI
    assert provider.default_model() == DEFAULT_SCRIPTED_MODEL


def test_the_default_model_name_says_it_is_a_fake() -> None:
    """설정 화면의 모델 자리에 그대로 나가는 문자열이다(리뷰 R1-004)."""
    assert SCRIPTED_MARKER in DEFAULT_SCRIPTED_MODEL
    assert ScriptedLlmProvider(ProviderKind.ANTHROPIC).default_model() == DEFAULT_SCRIPTED_MODEL


def test_building_the_provider_warns_that_answers_come_from_a_script(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """켠 채로 배포하면 화면만 보고는 진짜와 구분되지 않는다 — 서버 로그가 정본 표식이다."""
    with caplog.at_level(logging.WARNING):
        ScriptedLlmProvider(ProviderKind.OPENAI)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "SCRIPTED FAKE" in message
    assert SCRIPTED_MARKER in message
    assert ProviderKind.OPENAI.value in message


def test_the_adapter_sleeps_between_chunks_so_the_stream_is_observable() -> None:
    naps: list[float] = []
    provider = ScriptedLlmProvider(
        ProviderKind.ANTHROPIC, step_delay_seconds=0.25, sleep=naps.append
    )

    events = list(
        provider.stream_turn(
            "sk-fake", _profile(), _request("모멘텀이 뭐야?"), _ToolRecorder(), lambda: False
        )
    )

    assert len(naps) == len(events)
    assert set(naps) == {0.25}


def test_a_cancelled_turn_stops_before_the_next_chunk() -> None:
    provider = ScriptedLlmProvider(ProviderKind.ANTHROPIC, step_delay_seconds=0.0)

    stream = provider.stream_turn(
        "sk-fake", _profile(), _request("천천히 설명해 줘"), _ToolRecorder(), lambda: True
    )

    assert list(stream) == []


def test_the_scenario_reads_the_last_user_message_not_the_assistant_reply() -> None:
    """이력에 assistant 답이 섞여 있어도 이번 질문으로 고른다."""
    provider = ScriptedLlmProvider(ProviderKind.ANTHROPIC, step_delay_seconds=0.0)
    tools = _ToolRecorder()

    list(
        provider.stream_turn(
            "sk-fake", _profile(), _request("전략 하나 제안해 줘"), tools, lambda: False
        )
    )

    assert [call.name for call in tools.calls] == [READ_CURRENT_STRATEGY, PROPOSE_STRATEGY]


def _followed_by_another_event(events: list[ChatEvent], tool_name: str) -> bool:
    for index, event in enumerate(events):
        if isinstance(event, ToolCall) and event.name == tool_name:
            return index + 1 < len(events)
    return False


NEW_STRATEGY_SOURCE = 'schema_version: "9.9"\ntitle: ""\n'


def test_the_idea_scenario_keeps_the_current_schema_line_and_writes_a_whole_strategy() -> None:
    """버전은 지어내지 않고 지금 원문에서 가져온다(US-DM-03). compile은 tests/application이 본다."""
    tools = _ToolRecorder(source_text=NEW_STRATEGY_SOURCE)

    events = list(idea_to_new_strategy(tools))

    assert [call.name for call in tools.calls] == [READ_CURRENT_STRATEGY, PROPOSE_STRATEGY]
    proposed = tools.calls[1].arguments["source_text"]
    assert isinstance(proposed, str)
    assert proposed.startswith('schema_version: "9.9"\ntitle: "KRX 대형 모멘텀"\n')
    assert "factors:\n" in proposed
    assert _followed_by_another_event(events, PROPOSE_STRATEGY)


def test_the_idea_scenario_submits_a_broken_document_without_a_schema_line() -> None:
    tools = _ToolRecorder(source_text='title: ""\n')

    list(idea_to_new_strategy(tools))

    assert tools.calls[1].arguments["source_text"] == "title: 깨진 제안\n"


def test_the_concept_scenario_explains_in_plain_text_without_tools() -> None:
    tools = _ToolRecorder()

    events = list(concept_answer(tools))

    assert tools.calls == []
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text.startswith("샤프 비율은 ")
    assert isinstance(events[-1], Done)
