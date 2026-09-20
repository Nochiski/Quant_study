"""AssistantTurnRunner 테스트 (A-02).

러너는 진행 중 턴의 owner다. 여기서 고정하는 것은 네 가지다: 이벤트가 나오는 대로 sequence를
달고 저장되는가, 한 세션에 턴이 하나인가, 취소가 실제로 스트림을 멈추는가, 상한을 넘긴 턴이
`Failure(TIMEOUT)`로 끝나는가.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    ChatEvent,
    ChatMessage,
    ChatRole,
    Done,
    Failure,
    FailureCode,
    ProviderKind,
    TextDelta,
    Turn,
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
    grace_seconds: float = 10.0,
    clock_step: float = 0.0,
    sessions: InMemoryChatSessionRepository | None = None,
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
    sessions = sessions if sessions is not None else InMemoryChatSessionRepository()
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
        grace_seconds=grace_seconds,
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
    # 취소 판정 시점에 손에 들고 있던 이벤트는 버리지 않는다(spec D3 텍스트 보존).
    assert [item.event for item in stored] == [
        TextDelta("안녕"),
        Failure(
            code=FailureCode.CANCELLED,
            message=f"사용자가 턴을 취소했습니다 — session_id={harness.session.session_id}",
        ),
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
        (FailureCode.INTERNAL, TurnStatus.FAILED),
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


# -- [P1] 취소한 턴도 스레드가 끝날 때까지 슬롯을 잡는다 ------------------------------------------


def test_a_turn_started_right_after_a_cancel_is_rejected() -> None:
    """취소는 신호일 뿐이다. 스레드가 아직 돌면 그 세션은 여전히 점유 중이다."""
    harness = _harness((TextDelta("안녕"), TextDelta("하세요"), Done("end_turn")))
    first = harness.runner.start(harness.session.session_id, "q1", CONTEXT)

    harness.runner.cancel(first.turn_id)

    with pytest.raises(TurnInProgressError, match=first.turn_id):
        harness.runner.start(harness.session.session_id, "q2", CONTEXT)


def test_the_slot_frees_once_the_cancelled_thread_finishes() -> None:
    harness = _harness((TextDelta("안녕"), Done("end_turn")))
    first = harness.runner.start(harness.session.session_id, "q1", CONTEXT)
    harness.runner.cancel(first.turn_id)

    ManualTurnThread.run_all()
    second = harness.runner.start(harness.session.session_id, "q2", CONTEXT)

    assert second.turn_id != first.turn_id
    # 역할 교대가 깨지면 공급자 요청 자체가 망가진다. user가 연속으로 오면 안 된다.
    roles = [message.role for message in harness.sessions.messages(harness.session.session_id)]
    assert roles == [ChatRole.USER, ChatRole.ASSISTANT, ChatRole.USER]


# -- [P2] 저장소가 흔들려도 세션이 잠기지 않는다 --------------------------------------------------


class _UpdateTurnFails(InMemoryChatSessionRepository):
    def update_turn(self, turn: Turn) -> Turn:
        raise RuntimeError("sqlite is locked")


class _AppendMessageFails(InMemoryChatSessionRepository):
    def append_message(self, session_id: str, message: ChatMessage) -> None:
        if message.role is ChatRole.ASSISTANT:
            raise RuntimeError("sqlite is locked")
        super().append_message(session_id, message)


class _AppendTextFails(InMemoryChatSessionRepository):
    """텍스트 이벤트 저장만 실패한다.

    크래시 경로가 Failure를 남기는지 보려면 나머지 append는 살아 있어야 한다.
    """

    def append_events(self, turn_id: str, events: Sequence[ChatEvent]) -> tuple[int, ...]:
        if any(isinstance(event, TextDelta) for event in events):
            raise RuntimeError("sqlite is locked")
        return super().append_events(turn_id, events)


def test_a_repository_failure_while_finishing_still_frees_the_session() -> None:
    harness = _harness((Done("end_turn"),), sessions=_UpdateTurnFails())
    first = harness.runner.start(harness.session.session_id, "q1", CONTEXT)

    ManualTurnThread.run_all()

    # 종료 기록에 실패해도 슬롯은 풀려야 한다. 잠기면 사용자는 아무 질문도 보낼 수 없다.
    second = harness.runner.start(harness.session.session_id, "q2", CONTEXT)
    assert second.turn_id != first.turn_id


def test_a_failure_while_closing_the_stream_still_finishes_the_turn() -> None:
    """유예가 지나 스트림을 닫을 때 부분 메시지 저장이 실패하는 경로."""
    harness = _harness(
        (TextDelta("한"), TextDelta("참"), TextDelta("뒤"), Done("end_turn")),
        timeout_seconds=5.0,
        grace_seconds=1.0,
        clock_step=4.0,
        sessions=_AppendMessageFails(),
    )
    first = harness.runner.start(harness.session.session_id, "q1", CONTEXT)

    ManualTurnThread.run_all()

    assert harness.runner.state(first.turn_id).status is TurnStatus.FAILED
    second = harness.runner.start(harness.session.session_id, "q2", CONTEXT)
    assert second.turn_id != first.turn_id


def test_a_crashed_turn_records_a_failure_event_and_fails() -> None:
    harness = _harness((TextDelta("안녕"), Done("end_turn")), sessions=_AppendTextFails())
    turn = harness.runner.start(harness.session.session_id, "q1", CONTEXT)

    ManualTurnThread.run_all()

    stored = [item.event for item in harness.runner.events(harness.session.session_id)]
    # 화면은 이벤트 열만 보고 턴의 끝을 안다. 종료 이벤트가 없으면 "멈춘 채 끝난" 턴으로 보인다.
    assert stored
    last = stored[-1]
    assert isinstance(last, Failure)
    # 저장소가 흔들린 것이지 공급자가 실패한 것이 아니다.
    assert last.code is FailureCode.INTERNAL
    assert "error_type=RuntimeError" in last.message
    assert "sqlite is locked" not in last.message
    assert harness.runner.state(turn.turn_id).status is TurnStatus.FAILED


# -- [P2] 유예는 턴 상한과 별개의 짧은 값이다 -----------------------------------------------------


def test_the_grace_window_is_shorter_than_the_turn_timeout() -> None:
    script: tuple[ScriptStep, ...] = tuple(TextDelta(str(index)) for index in range(10))
    # 이벤트마다 4초: 상한 5초에서 두 번째 이벤트가 넘기고, 유예 1초는 그 다음 이벤트에서 끝난다.
    harness = _harness(script, timeout_seconds=5.0, grace_seconds=1.0, clock_step=4.0)

    harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    stored = [item.event for item in harness.runner.events(harness.session.session_id)]
    # 유예가 상한과 같았다면 훨씬 더 많은 이벤트가 저장된다.
    assert len(stored) <= 4
    assert any(isinstance(event, Failure) and event.code is FailureCode.TIMEOUT for event in stored)


# -- [P3] 확정된 턴을 취소하면 확정된 상태가 보인다 ----------------------------------------------


def test_cancelling_a_turn_whose_reason_is_already_decided_reports_that_reason() -> None:
    """스레드가 아직 도는 동안 취소하면 레지스트리가 답한다 — 확정된 사유가 보여야 한다.

    `run_all()` 뒤에 취소하면 `_finish`가 이미 항목을 뺀 뒤라 저장소 폴백으로 빠지고, 레지스트리
    분기(`entry.decided` → `view()`)를 지나지 않는다.
    """
    harness = _harness(
        (TextDelta("한"), TextDelta("참"), TextDelta("뒤")),
        timeout_seconds=5.0,
        grace_seconds=1.0,
        clock_step=4.0,
    )
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    # 스레드를 돌리지 않은 채 타임아웃만 태워 `decided`를 FAILED로 만든다. 공개 API로는 이 창을
    # 동기적으로 만들 수 없다(`run_all()`은 소비와 종료를 한 번에 돌린다).
    harness.runner._expire(turn.turn_id)

    cancelled = harness.runner.cancel(turn.turn_id)

    assert cancelled.status is TurnStatus.FAILED
    assert cancelled.turn_id == turn.turn_id
    # 취소가 확정된 사유를 덮지 않고, 슬롯도 아직 잡혀 있다.
    assert harness.runner.state(turn.turn_id).status is TurnStatus.FAILED
    with pytest.raises(TurnInProgressError):
        harness.runner.start(harness.session.session_id, "다음", CONTEXT)

    ManualTurnThread.run_all()
    assert harness.runner.state(turn.turn_id).status is TurnStatus.FAILED


def test_cancelling_a_finished_turn_falls_back_to_the_repository() -> None:
    harness = _harness(
        (TextDelta("한"), TextDelta("참"), TextDelta("뒤")),
        timeout_seconds=5.0,
        grace_seconds=1.0,
        clock_step=4.0,
    )
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    ManualTurnThread.run_all()

    assert harness.runner.cancel(turn.turn_id).status is TurnStatus.FAILED


class _ObservingSessionRepository(InMemoryChatSessionRepository):
    """`update_turn`이 도는 **그 순간** 러너가 뭐라고 답하는지 적어 두는 저장소.

    `_finish`의 두 동작(종료 상태 저장, 슬롯 해제) 사이 창을 밖에서 들여다볼 방법이 이것뿐이다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.observe: Callable[[], tuple[Turn | None, bool]] | None = None
        self.observations: list[tuple[Turn | None, bool, Turn]] = []

    def update_turn(self, turn: Turn) -> Turn:
        if self.observe is not None:
            occupied, settled = self.observe()
            self.observations.append((occupied, settled, turn))
        return super().update_turn(turn)


def test_the_final_state_is_stored_before_the_session_slot_is_released() -> None:
    """슬롯을 먼저 풀면 "레지스트리에 없는데 저장 행은 아직 RUNNING"인 창이 생긴다.

    그 창에서 `POST /sessions/{id}/turns/{turn_id}/cancel`은 이미 끝난 턴을 RUNNING으로
    답하고, 이력도 같은 거짓을 말한다. 클라이언트는 그 말을 믿고 스트림을 열려다 409를 받는다
    (A-02 2차 리뷰가 A-04로 넘긴 확인 항목). 저장이 먼저면 창의 방향이 "아직 바쁘다" 쪽이라
    안전하다.
    """
    sessions = _ObservingSessionRepository()
    harness = _harness((TextDelta("안녕"), Done("end_turn")), sessions=sessions)
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)
    sessions.observe = lambda: (
        harness.runner.occupied_turn(harness.session.session_id),
        harness.runner.is_settled(turn.turn_id),
    )

    ManualTurnThread.run_all()

    occupied, settled, written = sessions.observations[-1]
    assert written.status is TurnStatus.COMPLETED
    # 저장이 도는 동안 슬롯은 아직 잡혀 있고, 조회는 이미 최종 상태를 말한다.
    assert occupied is not None
    assert occupied.turn_id == turn.turn_id
    assert occupied.status is TurnStatus.COMPLETED
    assert settled is False
    # 저장이 끝난 뒤에야 슬롯이 풀린다.
    assert harness.runner.occupied_turn(harness.session.session_id) is None
    assert harness.runner.is_settled(turn.turn_id) is True
    assert harness.runner.state(turn.turn_id).status is TurnStatus.COMPLETED


def test_the_slot_is_released_even_when_storing_the_final_state_fails() -> None:
    """저장 실패가 슬롯을 영원히 잡아 두면 그 세션은 재시작 전까지 새 턴을 못 연다."""

    class _RefusingRepository(InMemoryChatSessionRepository):
        def update_turn(self, turn: Turn) -> Turn:
            if turn.status is not TurnStatus.RUNNING:
                raise RuntimeError("storage is down")
            return super().update_turn(turn)

    sessions = _RefusingRepository()
    harness = _harness((TextDelta("안녕"), Done("end_turn")), sessions=sessions)
    turn = harness.runner.start(harness.session.session_id, "질문", CONTEXT)

    ManualTurnThread.run_all()

    assert harness.runner.occupied_turn(harness.session.session_id) is None
    assert harness.runner.is_settled(turn.turn_id) is True
