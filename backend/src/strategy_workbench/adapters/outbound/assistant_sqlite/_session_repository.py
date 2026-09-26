"""`ChatSessionRepository`의 SQLite 구현 (설계 spec D5, D9).

이 어댑터가 이력의 owner다. 세션 단위 단조 `sequence`도 여기서 **한 트랜잭션 안에** 부여한다.
러너·HTTP·공급자 adapter가 각자 세면 스트림이 끊겼다 이어질 때 번호가 겹치거나 비어, 클라이언트의
`after_sequence` 재개가 이벤트를 두 번 반영하거나 빠뜨린다.

세션 저장에는 키와 시스템 프롬프트 원문을 넣지 않는다(spec D5). 저장하는 텍스트는 사용자·모델의
대화 메시지와 이벤트 JSON뿐이다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from strategy_workbench.application.assistant_chat.facade.chat import ChatSession, DocumentRef
from strategy_workbench.application.assistant_chat.facade.ports import (
    ChatSessionNotFoundError,
    TurnNotFoundError,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    ChatMessage,
    ChatRole,
    SequencedEvent,
    Turn,
    TurnStatus,
)

from ._database import (
    AssistantDatabase,
    datetime_text,
    datetime_value,
    int_value,
    optional_int_value,
    optional_text_value,
    text_value,
)
from ._errors import AssistantStorageError
from ._event_codec import decode_event, encode_event

_SESSION_COLUMNS = """
session_id, ordinal, strategy_id, revision, draft_id, provider_profile_id, created_at, title
"""
_TURN_COLUMNS = "turn_id, session_id, status, accepted_sequence, started_at, finished_at"

# 종료 상태. `TurnStatus` docstring이 "다시 바뀌지 않는다"고 선언하고 여기서 집행한다.
_TERMINAL_STATUSES = frozenset({TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED})


class SQLiteChatSessionRepository:
    """세션·메시지·턴·이벤트를 한 DB 파일에 담는 저장소."""

    def __init__(self, database: AssistantDatabase) -> None:
        self._database = database

    # -- 세션 ---------------------------------------------------------------------------------

    def create(self, session: ChatSession) -> ChatSession:
        with self._database.transaction(write=True) as connection:
            ordinal = _next_ordinal(connection, "chat_sessions", "ordinal", where=None)
            try:
                connection.execute(
                    f"INSERT INTO chat_sessions ({_SESSION_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        ordinal,
                        session.document_ref.strategy_id,
                        session.document_ref.revision,
                        session.document_ref.draft_id,
                        session.provider_profile_id,
                        datetime_text(session.created_at, field="chat session created_at"),
                        session.title,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise AssistantStorageError(
                    "could not store the chat session — "
                    f"session_id={session.session_id!r} ordinal={ordinal} ({error})"
                ) from error
        return session

    def get(self, session_id: str) -> ChatSession:
        with self._database.transaction(write=False) as connection:
            row = _session_row(connection, session_id)
        return _decode_session(row)

    def list_for_document(self, document_ref: DocumentRef) -> tuple[ChatSession, ...]:
        # `IS`는 NULL도 같은 값으로 비교한다. `=`로 쓰면 draft 세션은 어떤 조회에도 걸리지 않는다.
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS} FROM chat_sessions
                WHERE strategy_id IS ? AND revision IS ? AND draft_id IS ?
                ORDER BY ordinal ASC
                """,
                (document_ref.strategy_id, document_ref.revision, document_ref.draft_id),
            ).fetchall()
        return tuple(_decode_session(row) for row in rows)

    # -- 메시지 -------------------------------------------------------------------------------

    def append_message(self, session_id: str, message: ChatMessage) -> None:
        with self._database.transaction(write=True) as connection:
            _session_row(connection, session_id)
            ordinal = _next_ordinal(
                connection, "chat_messages", "ordinal", where=("session_id", session_id)
            )
            connection.execute(
                """
                INSERT INTO chat_messages (session_id, ordinal, role, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    ordinal,
                    message.role.value,
                    message.text,
                    datetime_text(message.created_at, field="chat message created_at"),
                ),
            )

    def messages(self, session_id: str) -> tuple[ChatMessage, ...]:
        with self._database.transaction(write=False) as connection:
            _session_row(connection, session_id)
            rows = connection.execute(
                """
                SELECT role, text, created_at FROM chat_messages
                WHERE session_id = ? ORDER BY ordinal ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(_decode_message(row) for row in rows)

    # -- 턴 -----------------------------------------------------------------------------------

    def create_turn(self, turn: Turn) -> Turn:
        with self._database.transaction(write=True) as connection:
            _session_row(connection, turn.session_id)
            try:
                connection.execute(
                    f"INSERT INTO chat_turns ({_TURN_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                    _encode_turn(turn),
                )
            except sqlite3.IntegrityError as error:
                raise AssistantStorageError(
                    f"could not store the assistant turn — turn_id={turn.turn_id!r} "
                    f"session_id={turn.session_id!r} ({error})"
                ) from error
        return turn

    def update_turn(self, turn: Turn) -> Turn:
        """상태·종료 시각만 갱신하고, **나머지는 저장된 값을 지킨다.**

        `accepted_sequence`는 SSE 재개의 기준점이고 `started_at`은 사이드바 턴 정렬의 기준이다
        (`chat_turns_by_session`). 둘 다 턴이 시작될 때 한 번 정해지는 정체이지 갱신 대상이 아니다.
        `-1`로 덮이면 재접속 클라이언트가 세션을 처음부터 다시 받고, 더 큰 값으로 덮이면 그 사이
        이벤트를 영영 못 본다. 지금은 러너가 두 값을 보존해 주지만, 저장소가 지켜야 할 불변식을
        호출자의 예의에 맡기지 않는다 — A-04가 HTTP 요청에서 `Turn`을 재구성하면 바로 깨진다.

        그래서 돌려주는 값도 인자가 아니라 **저장된 행**이다. 인자를 그대로 돌려주면 보존한
        사실을 호출자에게 거짓으로 말하게 된다.

        상태 전이는 한쪽 방향만 연다. RUNNING에서 종료 상태로는 갈 수 있고(크래시 복구가 남은
        RUNNING 턴을 FAILED로 정리해야 한다), 종료된 턴이 다른 상태로 바뀌는 것은 거부한다
        (`TurnStatus` docstring: "종료 상태 세 개는 다시 바뀌지 않는다"). 같은 종료 상태로의
        갱신은 남겨 둔다 — 러너의 `cancel`이 CANCELLED를 먼저 쓰고 스레드가 끝날 때 같은 상태에
        `finished_at`만 채우기 때문이다. 종료된 턴이 RUNNING으로 되살아나면 "세션에 RUNNING 턴이
        있으면 409"(spec D6) 판정이 뒤집혀 그 세션에서 새 턴을 영영 시작할 수 없다.
        """
        with self._database.transaction(write=True) as connection:
            stored = _decode_turn(_turn_row(connection, turn.turn_id))
            if stored.session_id != turn.session_id:
                raise AssistantStorageError(
                    "an assistant turn cannot move between sessions — "
                    f"turn_id={turn.turn_id!r} stored={stored.session_id!r} "
                    f"given={turn.session_id!r}"
                )
            if stored.status in _TERMINAL_STATUSES and turn.status is not stored.status:
                raise AssistantStorageError(
                    "a finished assistant turn cannot change status — "
                    f"turn_id={turn.turn_id!r} stored={stored.status.value} "
                    f"given={turn.status.value}"
                )
            connection.execute(
                "UPDATE chat_turns SET status = ?, finished_at = ? WHERE turn_id = ?",
                (
                    turn.status.value,
                    None
                    if turn.finished_at is None
                    else datetime_text(turn.finished_at, field="assistant turn finished_at"),
                    turn.turn_id,
                ),
            )
            return _decode_turn(_turn_row(connection, turn.turn_id))

    def get_turn(self, turn_id: str) -> Turn:
        with self._database.transaction(write=False) as connection:
            row = _turn_row(connection, turn_id)
        return _decode_turn(row)

    def turns(self, session_id: str) -> tuple[Turn, ...]:
        """세션의 턴 전부를 시작 순서대로. `chat_turns_by_session` 인덱스를 그대로 탄다."""
        with self._database.transaction(write=False) as connection:
            _session_row(connection, session_id)
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM chat_turns WHERE session_id = ?"
                # `WITHOUT ROWID` 테이블이라 삽입 순서를 물어볼 수 없다. 시작 시각이 같은 턴은
                # 직전 sequence로, 그것도 같으면 id로 갈라 순서를 결정적으로 만든다.
                " ORDER BY started_at ASC, accepted_sequence ASC, turn_id ASC",
                (session_id,),
            ).fetchall()
        return tuple(_decode_turn(row) for row in rows)

    # -- 이벤트 -------------------------------------------------------------------------------

    def append_events(self, turn_id: str, events: Sequence[ChatEvent]) -> tuple[int, ...]:
        """한 트랜잭션에서 세션의 다음 번호부터 차례로 부여하고 그 번호를 돌려준다."""
        encoded = [encode_event(event) for event in events]
        with self._database.transaction(write=True) as connection:
            session_id = text_value(_turn_row(connection, turn_id), "session_id")
            start = _max_sequence(connection, session_id) + 1
            sequences = tuple(range(start, start + len(encoded)))
            connection.executemany(
                """
                INSERT INTO chat_events (session_id, sequence, turn_id, event_type, event_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (session_id, sequence, turn_id, tag, payload)
                    for sequence, (tag, payload) in zip(sequences, encoded, strict=True)
                ],
            )
        return sequences

    def last_sequence(self, session_id: str) -> int:
        with self._database.transaction(write=False) as connection:
            _session_row(connection, session_id)
            return _max_sequence(connection, session_id)

    def events(self, session_id: str, *, after_sequence: int = -1) -> tuple[SequencedEvent, ...]:
        with self._database.transaction(write=False) as connection:
            _session_row(connection, session_id)
            rows = connection.execute(
                """
                SELECT sequence, turn_id, event_type, event_json FROM chat_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (session_id, after_sequence),
            ).fetchall()
        return tuple(
            SequencedEvent(
                sequence=int_value(row, "sequence"),
                turn_id=text_value(row, "turn_id"),
                event=decode_event(text_value(row, "event_type"), text_value(row, "event_json")),
            )
            for row in rows
        )


# -- 행 조회 ----------------------------------------------------------------------------------


def _session_row(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT {_SESSION_COLUMNS} FROM chat_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ChatSessionNotFoundError(session_id)
    return row


def _turn_row(connection: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT {_TURN_COLUMNS} FROM chat_turns WHERE turn_id = ?", (turn_id,)
    ).fetchone()
    if row is None:
        raise TurnNotFoundError(turn_id)
    return row


def _next_ordinal(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    *,
    where: tuple[str, str] | None,
) -> int:
    clause = f" WHERE {where[0]} = ?" if where is not None else ""
    parameters = (where[1],) if where is not None else ()
    row = connection.execute(f"SELECT MAX({column}) FROM {table}{clause}", parameters).fetchone()
    return _after(row, f"{table}.{column}")


def _max_sequence(connection: sqlite3.Connection, session_id: str) -> int:
    """세션의 마지막 sequence. 하나도 없으면 -1 (포트가 약속한 값)."""
    row = connection.execute(
        "SELECT MAX(sequence) FROM chat_events WHERE session_id = ?", (session_id,)
    ).fetchone()
    return _after(row, "chat_events.sequence") - 1


def _after(row: sqlite3.Row | None, label: str) -> int:
    current = row[0] if row is not None else None
    if current is None:
        return 0
    if not isinstance(current, int) or isinstance(current, bool):
        raise AssistantStorageError(
            f"stored {label} is not an integer — type={type(current).__name__}"
        )
    return current + 1


# -- 행 ↔ 값 타입 -----------------------------------------------------------------------------


def _decode_session(row: sqlite3.Row) -> ChatSession:
    return ChatSession(
        session_id=text_value(row, "session_id"),
        document_ref=DocumentRef(
            strategy_id=optional_text_value(row, "strategy_id"),
            revision=optional_int_value(row, "revision"),
            draft_id=optional_text_value(row, "draft_id"),
        ),
        provider_profile_id=text_value(row, "provider_profile_id"),
        created_at=datetime_value(row["created_at"], field="chat session created_at"),
        title=text_value(row, "title"),
    )


def _decode_message(row: sqlite3.Row) -> ChatMessage:
    raw_role = text_value(row, "role")
    try:
        role = ChatRole(raw_role)
    except ValueError as error:
        raise AssistantStorageError(
            f"stored chat message has an unknown role — role={raw_role!r}"
        ) from error
    return ChatMessage(
        role=role,
        text=text_value(row, "text"),
        created_at=datetime_value(row["created_at"], field="chat message created_at"),
    )


def _encode_turn(turn: Turn) -> tuple[object, ...]:
    return (
        turn.turn_id,
        turn.session_id,
        turn.status.value,
        turn.accepted_sequence,
        datetime_text(turn.started_at, field="assistant turn started_at"),
        None
        if turn.finished_at is None
        else datetime_text(turn.finished_at, field="assistant turn finished_at"),
    )


def _decode_turn(row: sqlite3.Row) -> Turn:
    raw_status = text_value(row, "status")
    try:
        status = TurnStatus(raw_status)
    except ValueError as error:
        raise AssistantStorageError(
            f"stored assistant turn has an unknown status — "
            f"turn_id={text_value(row, 'turn_id')!r} status={raw_status!r}"
        ) from error
    finished_at = optional_text_value(row, "finished_at")
    return Turn(
        turn_id=text_value(row, "turn_id"),
        session_id=text_value(row, "session_id"),
        status=status,
        accepted_sequence=int_value(row, "accepted_sequence"),
        started_at=datetime_value(row["started_at"], field="assistant turn started_at"),
        finished_at=None
        if finished_at is None
        else datetime_value(finished_at, field="assistant turn finished_at"),
    )
