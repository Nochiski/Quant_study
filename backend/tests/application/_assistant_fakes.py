"""어시스턴트 유스케이스 테스트용 가짜 구현.

공급자 SDK 없이도 계약이 성립하는지 확인하는 것이 Phase A 앞단의 목표이므로, 여기 가짜들이
유일한 "바깥"이다. `ScriptedProvider`는 미리 적어 둔 이벤트 열을 흘리고 `ToolStep`을 만나면
application이 넘긴 `execute_tool` 콜백을 실제로 호출한다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from strategy_workbench.application.assistant_chat.facade.chat import (
    ChatSession,
    DocumentRef,
)
from strategy_workbench.application.assistant_chat.facade.ports import (
    ChatSessionNotFoundError,
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
    TurnNotFoundError,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    ProbeResult,
    ProposalCompileResult,
    ProposalDiagnostic,
    ProviderKind,
    ProviderProfile,
    SequencedEvent,
    ToolCall,
    ToolResult,
    Turn,
    TurnRequest,
)


class InMemoryProviderProfileRepository:
    """`ProviderProfileRepository` 인메모리 구현. 활성 프로파일은 언제나 최대 한 개다."""

    def __init__(self) -> None:
        self._profiles: list[ProviderProfile] = []

    def list(self) -> tuple[ProviderProfile, ...]:
        return tuple(self._profiles)

    def get(self, profile_id: str) -> ProviderProfile:
        for profile in self._profiles:
            if profile.profile_id == profile_id:
                return profile
        raise ProviderProfileNotFoundError(profile_id)

    def add(self, profile: ProviderProfile) -> None:
        self._profiles.append(profile)

    def delete(self, profile_id: str) -> None:
        self.get(profile_id)
        self._profiles = [item for item in self._profiles if item.profile_id != profile_id]

    def set_active(self, profile_id: str) -> None:
        self.get(profile_id)
        self._profiles = [
            dataclasses.replace(item, active=item.profile_id == profile_id)
            for item in self._profiles
        ]


class InMemoryProviderSecretStore:
    """`ProviderSecretStore` 인메모리 구현. `delete`는 멱등이다."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get(self, profile_id: str) -> str:
        try:
            return self.secrets[profile_id]
        except KeyError as error:
            raise ProviderSecretMissingError(profile_id) from error

    def put(self, profile_id: str, secret: str) -> None:
        self.secrets[profile_id] = secret

    def delete(self, profile_id: str) -> None:
        self.secrets.pop(profile_id, None)


@dataclass(frozen=True)
class ToolStep:
    """공급자가 도구를 부르는 지점. `execute_tool` 콜백을 실제로 호출한다."""

    call: ToolCall


@dataclass(frozen=True)
class RaiseStep:
    """공급자 쪽 예외(SDK 오류·네트워크 끊김)를 흉내 내는 지점."""

    error: Exception


ScriptStep = ChatEvent | ToolStep | RaiseStep


class ScriptedProvider:
    """`LlmProviderPort` 가짜 구현. 스크립트를 순서대로 흘린다.

    `probe_result`로 연결 테스트 결과를 주입한다. `ProbeResult`는 사유만 받고 문장은 스스로
    고르므로, 이 가짜가 SDK 오류 본문을 흉내 내려 해도 넣을 자리가 없다(spec D2 스크럽 계약).
    """

    def __init__(
        self,
        *,
        script: Sequence[ScriptStep] = (),
        probe_result: ProbeResult | None = None,
        kind: ProviderKind = ProviderKind.ANTHROPIC,
        model: str = "fake-model-1",
    ) -> None:
        self.kind = kind
        self._script = tuple(script)
        self._probe_result = probe_result or ProbeResult(ok=True, latency_ms=7)
        self._model = model
        self.tool_results: list[ToolResult] = []
        self.requests: list[TurnRequest] = []
        self.probe_calls: list[tuple[str, str, str | None]] = []

    def default_model(self) -> str:
        return self._model

    def probe(self, secret: str, *, model: str, base_url: str | None) -> ProbeResult:
        self.probe_calls.append((secret, model, base_url))
        return self._probe_result

    def stream_turn(
        self,
        secret: str,
        profile: ProviderProfile,
        request: TurnRequest,
        execute_tool: Callable[[ToolCall], ToolResult],
        cancelled: Callable[[], bool],
    ) -> Iterator[ChatEvent]:
        self.requests.append(request)
        for step in self._script:
            if isinstance(step, RaiseStep):
                raise step.error
            if isinstance(step, ToolStep):
                yield step.call
                self.tool_results.append(execute_tool(step.call))
                continue
            yield step


def sequential_ids(prefix: str) -> Callable[[], str]:
    """결정적인 id 생성기. 테스트가 id를 미리 알 수 있게 한다."""
    counter = iter(range(1, 1000))
    return lambda: f"{prefix}-{next(counter)}"


class InMemoryChatSessionRepository:
    """`ChatSessionRepository` 인메모리 구현. sequence는 세션 단위로 단조 증가한다.

    `append_batches`는 러너가 이벤트를 모아서 넣는지 나오는 대로 넣는지 보려고 남긴다.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._events: dict[str, list[SequencedEvent]] = {}
        self._turns: dict[str, Turn] = {}
        self.append_batches: list[int] = []

    # -- 세션 ---------------------------------------------------------------------------------

    def create(self, session: ChatSession) -> ChatSession:
        self._sessions[session.session_id] = session
        self._messages.setdefault(session.session_id, [])
        self._events.setdefault(session.session_id, [])
        return session

    def get(self, session_id: str) -> ChatSession:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise ChatSessionNotFoundError(session_id) from error

    def list_for_document(self, document_ref: DocumentRef) -> tuple[ChatSession, ...]:
        return tuple(
            session for session in self._sessions.values() if session.document_ref == document_ref
        )

    def append_message(self, session_id: str, message: ChatMessage) -> None:
        self.get(session_id)
        self._messages[session_id].append(message)

    def messages(self, session_id: str) -> tuple[ChatMessage, ...]:
        self.get(session_id)
        return tuple(self._messages[session_id])

    # -- 턴 -----------------------------------------------------------------------------------

    def create_turn(self, turn: Turn) -> Turn:
        self.get(turn.session_id)
        self._turns[turn.turn_id] = turn
        return turn

    def update_turn(self, turn: Turn) -> Turn:
        if turn.turn_id not in self._turns:
            raise TurnNotFoundError(turn.turn_id)
        self._turns[turn.turn_id] = turn
        return turn

    def get_turn(self, turn_id: str) -> Turn:
        try:
            return self._turns[turn_id]
        except KeyError as error:
            raise TurnNotFoundError(turn_id) from error

    def turns(self, session_id: str) -> tuple[Turn, ...]:
        self.get(session_id)
        return tuple(turn for turn in self._turns.values() if turn.session_id == session_id)

    # -- 이벤트 -------------------------------------------------------------------------------

    def append_events(self, turn_id: str, events: Sequence[ChatEvent]) -> tuple[int, ...]:
        turn = self.get_turn(turn_id)
        self.append_batches.append(len(events))
        stored = self._events[turn.session_id]
        sequences: list[int] = []
        for event in events:
            sequence = len(stored)
            stored.append(SequencedEvent(sequence=sequence, turn_id=turn_id, event=event))
            sequences.append(sequence)
        return tuple(sequences)

    def last_sequence(self, session_id: str) -> int:
        self.get(session_id)
        stored = self._events[session_id]
        return stored[-1].sequence if stored else -1

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        self.get(session_id)
        return tuple(item for item in self._events[session_id] if item.sequence > after_sequence)


class FakeStrategyCompiler:
    """`StrategyCompilerPort` 가짜 구현. 정해 둔 원문만 통과시킨다."""

    def __init__(self, *, valid_sources: Sequence[str] = ()) -> None:
        self._valid = frozenset(valid_sources)
        self.compiled: list[str] = []

    def compile(self, source_text: str) -> ProposalCompileResult:
        self.compiled.append(source_text)
        if source_text in self._valid:
            return ProposalCompileResult(ok=True, spec_hash="spec-hash-1", diagnostics=())
        return ProposalCompileResult(
            ok=False,
            spec_hash=None,
            diagnostics=(
                ProposalDiagnostic(
                    code="strategy.factor.unknown",
                    pointer="/factors/0/factor_id",
                    message="알 수 없는 팩터 식별자",
                    severity="error",
                ),
            ),
        )


class ManualTurnThread:
    """`start()`가 아무것도 하지 않는 스레드. 테스트가 `run_now()`로 직접 돌린다.

    턴이 RUNNING으로 남아 있는 구간(중복 턴 거부, 취소)을 결정적으로 재현하려면 시작과 실행을
    떼어 놓아야 한다.
    """

    started: list[ManualTurnThread] = []

    def __init__(self, *, target: Callable[[], None], name: str, daemon: bool) -> None:
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self) -> None:
        ManualTurnThread.started.append(self)

    def run_now(self) -> None:
        self._target()

    @classmethod
    def reset(cls) -> None:
        cls.started = []

    @classmethod
    def run_all(cls) -> None:
        pending, cls.started = cls.started, []
        for thread in pending:
            thread.run_now()


class SteppedClock:
    """호출할 때마다 `step`만큼 흐르는 단조 시계. 타임아웃 경로를 결정적으로 만든다."""

    def __init__(self, *, step: float = 0.0, start: float = 0.0) -> None:
        self._value = start
        self._step = step

    def __call__(self) -> float:
        current = self._value
        self._value += self._step
        return current
