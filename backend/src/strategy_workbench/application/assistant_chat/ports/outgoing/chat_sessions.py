"""Outgoing port: 채팅 세션·턴·메시지·이벤트 저장소 (설계 spec D3/D5).

이벤트는 **세션 단위로 단조 증가하는 sequence**를 달고 저장된다. 클라이언트는 `after_sequence`
하나만 들고 스트림을 다시 열 수 있고, 저장소가 번호를 부여하므로 러너·HTTP·adapter가 각자 세는
일이 없다. 번호가 세션 단위인 이유는 사이드바가 한 세션의 이력을 이어서 읽기 때문이다.

세션 저장에는 키와 시스템 프롬프트 원문을 넣지 않는다.

**구현은 스레드 안전해야 한다.** `append_events`는 턴을 돌리는 워커 스레드에서, `last_sequence`·
`create_turn`·`get`·`messages`는 요청 스레드에서 불린다(`AssistantTurnRunner` 참고). 특히 sequence
부여는 원자적이어야 한다 — 두 호출이 같은 번호를 받으면 클라이언트의 `after_sequence` 재개가
이벤트를 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    SequencedEvent,
    Turn,
)

from ..._models import ChatSession, DocumentRef

__all__ = ["ChatSessionNotFoundError", "ChatSessionRepository", "TurnNotFoundError"]


class ChatSessionNotFoundError(LookupError):
    """요청한 세션이 저장소에 없다."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"unknown chat session — session_id={session_id!r}")
        self.session_id = session_id


class TurnNotFoundError(LookupError):
    """요청한 턴이 저장소에 없다."""

    def __init__(self, turn_id: str) -> None:
        super().__init__(f"unknown assistant turn — turn_id={turn_id!r}")
        self.turn_id = turn_id


class ChatSessionRepository(Protocol):
    def create(self, session: ChatSession) -> ChatSession: ...

    def get(self, session_id: str) -> ChatSession:
        """없으면 `ChatSessionNotFoundError`."""
        ...

    def list_for_document(self, document_ref: DocumentRef) -> tuple[ChatSession, ...]: ...

    def append_message(self, session_id: str, message: ChatMessage) -> None: ...

    def messages(self, session_id: str) -> tuple[ChatMessage, ...]:
        """대화 순서대로 전부. 다음 턴의 `TurnRequest.messages`가 여기서 나온다."""
        ...

    def create_turn(self, turn: Turn) -> Turn: ...

    def update_turn(self, turn: Turn) -> Turn:
        """상태·종료 시각을 갱신한다. 없으면 `TurnNotFoundError`."""
        ...

    def get_turn(self, turn_id: str) -> Turn:
        """없으면 `TurnNotFoundError`."""
        ...

    def append_events(self, turn_id: str, events: Sequence[ChatEvent]) -> tuple[int, ...]:
        """이벤트를 저장하고 부여한 sequence를 순서대로 돌려준다.

        번호는 그 턴이 속한 세션 안에서 단조 증가한다. 없는 턴이면 `TurnNotFoundError`.
        """
        ...

    def last_sequence(self, session_id: str) -> int:
        """세션에 마지막으로 부여된 sequence. 아직 없으면 -1.

        `Turn.accepted_sequence`를 채우려고 이력 전체를 읽는 일을 막는 조회다(sqlite adapter에서는
        `MAX(sequence)` 한 번).
        """
        ...

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        """`after_sequence`보다 큰 sequence의 이벤트를 번호 순으로. 기본값은 세션 전체."""
        ...
