"""실제 스레드로 도는 턴 러너 통합 테스트 (A-02).

다른 러너 테스트는 `ManualTurnThread`로 스레드를 동기 실행한다. 덕분에 취소·중복 거부 구간이
결정적이 되지만, 그 대가로 워커 스레드에서 불리는 `append_events`의 sequence 단조성과 `start()`의
check-then-act 경합이 한 번도 실행되지 않는다. 여기서는 진짜 `threading.Thread`로 그 경로를 한 번
밟는다.

결정성은 sleep이 아니라 `threading.Event`로 만든다. 공급자가 "시작했다"를 알리고 테스트가 "계속
해도 된다"를 알리는 두 신호만 쓰므로, 느린 기계에서도 순서가 뒤집히지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.assistant_chat.facade.chat import (
    AssistantChatService,
    DocumentRef,
    TurnContext,
)
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.profiles import ProviderProfileService
from strategy_workbench.application.assistant_chat.facade.turns import (
    AssistantTurnRunner,
    TurnInProgressError,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatRole,
    Done,
    ProbeResult,
    ProviderKind,
    ProviderProfile,
    TextDelta,
    ToolCall,
    ToolResult,
    Turn,
    TurnRequest,
    TurnStatus,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

from ._assistant_fakes import (
    FakeStrategyCompiler,
    InMemoryChatSessionRepository,
    InMemoryProviderProfileRepository,
    InMemoryProviderSecretStore,
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
JOIN_TIMEOUT_SECONDS = 10.0


class BlockingProvider:
    """첫 조각을 낸 뒤 테스트가 풀어 줄 때까지 멈춰 있는 가짜 공급자."""

    kind = ProviderKind.ANTHROPIC

    def __init__(self) -> None:
        self.streaming = threading.Event()
        self.release = threading.Event()

    def default_model(self) -> str:
        return "fake-model-1"

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        return ProbeResult(ok=True, latency_ms=1)

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        yield TextDelta("첫")
        self.streaming.set()
        self.release.wait(timeout=JOIN_TIMEOUT_SECONDS)
        for index in range(50):
            if cancelled():
                return
            yield TextDelta(str(index))
        yield Done("end_turn")


def _build() -> tuple[AssistantTurnRunner, InMemoryChatSessionRepository, str, BlockingProvider]:
    provider = BlockingProvider()
    providers = {ProviderKind.ANTHROPIC: provider}
    secrets = InMemoryProviderSecretStore()
    profiles = ProviderProfileService(
        InMemoryProviderProfileRepository(),
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
    session = chat.create_session(DOCUMENT, title="스레드 테스트")
    runner = AssistantTurnRunner(
        chat,
        sessions,
        now=lambda: FIXED_NOW,
        new_id=sequential_ids("turn"),
    )
    return runner, sessions, session.session_id, provider


def _await_finished(runner: AssistantTurnRunner, turn_id: str) -> Turn:
    """워커 스레드가 `_finish`에 닿을 때까지 기다린다.

    status로는 알 수 없다. 취소는 스레드가 돌고 있는 중에도 status를 CANCELLED로 바꾸기 때문이다.
    `finished_at`이 채워지는 시점이 곧 슬롯이 풀리는 시점이다.
    """
    deadline = time.monotonic() + JOIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        turn = runner.state(turn_id)
        if turn.finished_at is not None:
            return turn
        time.sleep(0.005)
    raise AssertionError(
        f"turn never finished — turn_id={turn_id!r} status={runner.state(turn_id).status.value!r}"
    )


def test_a_real_worker_thread_holds_the_session_slot_until_it_finishes() -> None:
    runner, sessions, session_id, provider = _build()

    turn = runner.start(session_id, "q1", CONTEXT)
    assert provider.streaming.wait(timeout=JOIN_TIMEOUT_SECONDS), "worker thread never started"

    # 워커가 도는 동안 같은 세션의 두 번째 턴은 거부된다.
    with pytest.raises(TurnInProgressError, match=turn.turn_id):
        runner.start(session_id, "q2", CONTEXT)

    # 취소는 신호일 뿐이라 슬롯은 아직 잡혀 있다.
    assert runner.cancel(turn.turn_id).status is TurnStatus.CANCELLED
    with pytest.raises(TurnInProgressError):
        runner.start(session_id, "q3", CONTEXT)

    provider.release.set()
    assert _await_finished(runner, turn.turn_id).status is TurnStatus.CANCELLED

    # 워커가 끝난 뒤에는 다음 턴이 들어간다.
    provider.streaming.clear()
    provider.release.clear()
    second = runner.start(session_id, "q4", CONTEXT)
    assert second.turn_id != turn.turn_id
    assert provider.streaming.wait(timeout=JOIN_TIMEOUT_SECONDS)
    runner.cancel(second.turn_id)
    provider.release.set()
    assert _await_finished(runner, second.turn_id).status is TurnStatus.CANCELLED

    stored = runner.events(session_id)
    sequences = [item.sequence for item in stored]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert second.accepted_sequence == max(
        item.sequence for item in stored if item.turn_id == turn.turn_id
    )
    roles = [message.role for message in sessions.messages(session_id)]
    assert roles == [ChatRole.USER, ChatRole.ASSISTANT, ChatRole.USER, ChatRole.ASSISTANT]


def test_two_threads_racing_to_start_leave_exactly_one_winner() -> None:
    """`start()`의 check-then-act가 한 RLock 구간인지 실제 경합으로 확인한다."""
    runner, _, session_id, provider = _build()
    provider.release.set()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait(timeout=JOIN_TIMEOUT_SECONDS)
        try:
            turn = runner.start(session_id, "q", CONTEXT)
        except TurnInProgressError:
            with lock:
                outcomes.append("rejected")
            return
        with lock:
            outcomes.append(turn.turn_id)

    racers = [threading.Thread(target=attempt, name=f"racer-{index}") for index in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=JOIN_TIMEOUT_SECONDS)
        assert not racer.is_alive()

    assert len(outcomes) == 2
    assert outcomes.count("rejected") == 1
    winners = [outcome for outcome in outcomes if outcome != "rejected"]
    assert len(winners) == 1
