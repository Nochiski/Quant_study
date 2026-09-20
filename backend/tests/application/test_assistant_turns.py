"""AssistantTurnRunner 테스트 (A-02).

러너는 진행 중 턴의 owner다. 여기서 고정하는 것은 네 가지다: 이벤트가 나오는 대로 sequence를
달고 저장되는가, 한 세션에 턴이 하나인가, 취소가 실제로 스트림을 멈추는가, 상한을 넘긴 턴이
`Failure(TIMEOUT)`로 끝나는가.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    TurnContext,
)
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.ports import (
    ChatSessionNotFoundError,
    TurnNotFoundError,
)
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.turns import (
    AssistantTurnRunner,
    TurnInProgressError,
)
from strategy_workbench.domain.assistant.facade.models import (
    Done,
    Failure,
    FailureCode,
    ProviderKind,
    TextDelta,
    TurnStatus,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

from ._assistant_fakes import (
    FakeStrategyCompiler,
    InMemoryChatSessionRepository,
    InMemoryProviderProfileRepository,
    InMemoryProviderSecretStore,
    ManualTurnThread,
    ScriptedProvider,
    ScriptStep,
    SteppedClock,
    sequential_ids,
)

FIXED_NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
DOCUMENT = DocumentRef(strategy_id="strategy-1", revision=3, draft_id=None)
CONTEXT = TurnContext(
    source_text="schema_version: '1.1'\ntitle: 현재 문서\n",
    source_format="yaml",
    environment=None,
    diagnostics=(),
)


@dataclass(frozen=True)
class Harness:
    runner: AssistantTurnRunner
    sessions: InMemoryChatSessionRepository
    session: ChatSession


def _harness(
    script: Sequence[ScriptStep],
    *,
    timeout_seconds: float = 300.0,
    clock_step: float = 0.0,
) -> Harness:
    ManualTurnThread.reset()
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
    profiles.create(kind=ProviderKind.ANTHROPIC, label="Claude", secret="sk-fake-0001")
    compiler = FakeStrategyCompiler()
    sessions = InMemoryChatSessionRepository()
    chat = AssistantChatService(
        sessions,
        profiles,
        secrets,
        providers,
        compiler,
        AssistantContextBuilder(
            equity_data=MockEquityDataAdapter.demo(),
            factor_registry=build_default_factor_registry(),
            compiler=compiler,
            today=lambda: date(2026, 9, 20),
        ),
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("session"),
    )
    session = chat.create_session(DOCUMENT, title="테스트 세션")
    runner = AssistantTurnRunner(
        chat,
        sessions,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("turn"),
        thread_factory=ManualTurnThread,
        timeout_seconds=timeout_seconds,
        monotonic=SteppedClock(step=clock_step),
    )
    return Harness(runner=runner, sessions=sessions, session=session)


def test_start_records_a_running_turn_before_the_thread_does_anything() -> None:
    harness = _harness((TextDelta("안녕"), Done("end_turn")))

    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)

    assert turn.status is TurnStatus.RUNNING
    assert turn.session_id == harness.session.session_id
    assert turn.accepted_sequence == -1
    assert turn.finished_at is None
    assert harness.runner.state(turn.turn_id) == turn


def test_events_are_appended_one_at_a_time_with_session_sequences() -> None:
    harness = _harness((TextDelta("안"), TextDelta("녕"), Done("end_turn")))

    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    stored = harness.runner.events(harness.session.session_id)
    assert [item.sequence for item in stored] == [0, 1, 2]
    assert [item.turn_id for item in stored] == [turn.turn_id] * 3
    assert [item.event for item in stored] == [
        TextDelta("안"),
        TextDelta("녕"),
        Done("end_turn"),
    ]
    # 배치로 모으면 화면이 턴이 끝날 때까지 비어 있다.
    assert harness.sessions.append_batches == [1, 1, 1]
    assert harness.runner.state(turn.turn_id).status is TurnStatus.COMPLETED


def test_after_sequence_returns_only_newer_events() -> None:
    harness = _harness((TextDelta("안"), TextDelta("녕"), Done("end_turn")))
    harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    tail = harness.runner.events(harness.session.session_id, after_sequence=0)

    assert [item.sequence for item in tail] == [1, 2]


def test_a_second_turn_accepts_only_after_the_first_one_finishes() -> None:
    harness = _harness((Done("end_turn"),))
    first = harness.runner.start(harness.session.session_id, "첫 질문", CONTEXT)

    with pytest.raises(TurnInProgressError, match=first.turn_id):
        harness.runner.start(harness.session.session_id, "둘째 질문", CONTEXT)

    ManualTurnThread.run_all()
    second = harness.runner.start(harness.session.session_id, "둘째 질문", CONTEXT)
    assert second.turn_id != first.turn_id


def test_the_second_turn_starts_after_the_events_the_first_one_produced() -> None:
    harness = _harness((TextDelta("안녕"), Done("end_turn")))
    harness.runner.start(harness.session.session_id, "첫 질문", CONTEXT)
    ManualTurnThread.run_all()

    second = harness.runner.start(harness.session.session_id, "둘째 질문", CONTEXT)

    assert second.accepted_sequence == 1
    assert (
        harness.runner.events(harness.session.session_id, after_sequence=second.accepted_sequence)
        == ()
    )


def test_cancel_stops_the_stream_and_marks_the_turn_cancelled() -> None:
    harness = _harness((TextDelta("안녕"), TextDelta("하세요"), Done("end_turn")))
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)

    cancelled = harness.runner.cancel(turn.turn_id)
    ManualTurnThread.run_all()

    assert cancelled.status is TurnStatus.CANCELLED
    stored = harness.runner.events(harness.session.session_id)
    assert [item.event for item in stored] == [
        Failure(
            code=FailureCode.CANCELLED,
            message=f"사용자가 턴을 취소했습니다 — session_id={harness.session.session_id}",
        )
    ]
    # 스레드가 뒤늦게 끝나도 사용자가 확정한 CANCELLED를 COMPLETED로 덮지 않는다.
    final = harness.runner.state(turn.turn_id)
    assert final.status is TurnStatus.CANCELLED
    assert final.finished_at == FIXED_NOW


def test_cancelling_a_finished_turn_returns_it_unchanged() -> None:
    harness = _harness((Done("end_turn"),))
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    assert harness.runner.cancel(turn.turn_id).status is TurnStatus.COMPLETED


def test_a_turn_past_the_deadline_fails_with_a_timeout_event() -> None:
    script: tuple[ScriptStep, ...] = (
        TextDelta("한"),
        TextDelta("참"),
        TextDelta("뒤"),
        Done("end_turn"),
    )
    # 시계가 이벤트마다 4초씩 흐르고 상한이 5초이므로 두 번째 이벤트에서 상한을 넘는다.
    harness = _harness(script, timeout_seconds=5.0, clock_step=4.0)

    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    stored = [item.event for item in harness.runner.events(harness.session.session_id)]
    assert stored[:2] == [TextDelta("한"), TextDelta("참")]
    failures = [event for event in stored if isinstance(event, Failure)]
    assert failures[0].code is FailureCode.TIMEOUT
    assert "timeout_seconds=5.0" in failures[0].message
    # 러너가 TIMEOUT을 먼저 확정한 뒤 취소 신호를 보내므로, 뒤따르는 CANCELLED는 이벤트로만 남는다.
    assert [failure.code for failure in failures] == [
        FailureCode.TIMEOUT,
        FailureCode.CANCELLED,
    ]
    assert harness.runner.state(turn.turn_id).status is TurnStatus.FAILED


def test_only_the_first_failure_becomes_the_turn_status() -> None:
    """한 턴에 Failure가 여럿이면 먼저 확정된 하나만 상태가 된다(spec D3)."""
    script: tuple[ScriptStep, ...] = (
        Failure(code=FailureCode.TOOL_ROUNDS_EXCEEDED, message="라운드 상한"),
        Failure(code=FailureCode.CANCELLED, message="뒤늦은 취소"),
    )
    harness = _harness(script)

    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    stored = [item.event for item in harness.runner.events(harness.session.session_id)]
    assert [event.code for event in stored if isinstance(event, Failure)] == [
        FailureCode.TOOL_ROUNDS_EXCEEDED,
        FailureCode.CANCELLED,
    ]
    assert harness.runner.state(turn.turn_id).status is TurnStatus.FAILED


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (FailureCode.TOOL_ROUNDS_EXCEEDED, TurnStatus.FAILED),
        (FailureCode.TOKEN_BUDGET_EXCEEDED, TurnStatus.FAILED),
        (FailureCode.OUTPUT_TRUNCATED, TurnStatus.FAILED),
        (FailureCode.PROVIDER, TurnStatus.FAILED),
        (FailureCode.CANCELLED, TurnStatus.CANCELLED),
    ],
)
def test_a_provider_failure_decides_the_turn_status(
    code: FailureCode, expected: TurnStatus
) -> None:
    """상한 집행은 adapter가 하므로 그 Failure가 그대로 턴 상태가 되어야 한다."""
    harness = _harness((Failure(code=code, message="공급자 종료"),))

    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    assert harness.runner.state(turn.turn_id).status is expected


def test_events_for_an_unknown_session_fail_instead_of_looking_empty() -> None:
    harness = _harness((Done("end_turn"),))

    with pytest.raises(ChatSessionNotFoundError, match="session-404"):
        harness.runner.events("session-404")


def test_state_for_an_unknown_turn_fails() -> None:
    harness = _harness((Done("end_turn"),))

    with pytest.raises(TurnNotFoundError, match="turn-404"):
        harness.runner.state("turn-404")
