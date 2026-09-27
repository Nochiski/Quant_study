"""어시스턴트 DB 스키마 v1 → v2 제자리 업그레이드 (C-03).

v2는 두 가지를 바꾼다.

1. `chat_messages`에 `turn_id` 칼럼을 더한다. v1 행은 같은 세션에서 **메시지 작성 시각 이전에 시작한
   마지막 턴**으로 채운다. 러너는 턴 시작 시각을 먼저 찍고 사용자 메시지를 쓰며, 답 메시지는 슬롯이
   풀리기 전에 쓰므로 다음 턴의 시작보다 앞선다. 그 앞에 시작한 턴이 없는 메시지는 NULL로 둔다.
2. v1이 검색 상한 **통지**를 `search_activity`(검색어 = 통지 문장, 출처 없음)로 저장한 행을
   `search_budget_exhausted`로 다시 쓴다. v2 이후 codec과 집계는 문구를 비교하지 않는다.

v1 파일은 이미 사용자 디스크에 있다. 그래서 v1 DDL은 고정된 사실이고, 아래 해시 테스트가 그 선언이
조용히 바뀌지 않게 막는다.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.assistant_sqlite._schema import (
    SCHEMA_VERSION,
    V1_SCHEMA_OBJECTS,
)
from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    AssistantStorageError,
    SQLiteChatSessionRepository,
)
from strategy_workbench.domain.assistant.facade.models import (
    ChatRole,
    SearchActivity,
    SearchBudgetExhausted,
    Source,
)

# v1 코드(A-06~C-02)가 통지로 저장한 문장. v1 행을 알아보는 근거라 여기에도 그대로 적는다.
V1_NOTICE = (
    "이 턴에 허용된 웹 검색 횟수를 모두 썼습니다. 더 이상 검색하지 않고 지금까지 확인한 자료로만 "
    "답합니다. 확인하지 못한 사실은 모른다고 말합니다."
)

# v1 DDL 전체의 sha256. v1 파일은 이미 사용자에게 있으므로 이 선언은 바뀌면 안 된다.
V1_SCHEMA_SHA256 = "18897f5972c14160161488bdd8f8342b994496c8bf35ccc0aedfd8ef0097779b"


def _v1_schema_digest() -> str:
    joined = "\n".join(
        f"{object_type}:{name}:{statement.strip()}"
        for object_type, name, statement in V1_SCHEMA_OBJECTS
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _create_v1_file(path: Path) -> sqlite3.Connection:
    """v1 코드가 만든 것과 같은 빈 파일을 연다. 호출자가 행을 채우고 닫는다."""
    connection = sqlite3.connect(path)
    for _object_type, _name, statement in V1_SCHEMA_OBJECTS:
        connection.execute(statement)
    connection.execute("PRAGMA application_id = 0x53574149")
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        """
        INSERT INTO chat_sessions (session_id, ordinal, strategy_id, revision, draft_id,
            provider_profile_id, created_at, title)
        VALUES ('session-1', 0, NULL, NULL, 'draft-1', 'profile-1',
            '2026-09-20T09:00:00.000000+00:00', '상담')
        """
    )
    return connection


def _add_turn(connection: sqlite3.Connection, turn_id: str, started_at: str) -> None:
    connection.execute(
        """
        INSERT INTO chat_turns (turn_id, session_id, status, accepted_sequence, started_at,
            finished_at)
        VALUES (?, 'session-1', 'completed', -1, ?, NULL)
        """,
        (turn_id, started_at),
    )


def _add_message(
    connection: sqlite3.Connection, ordinal: int, role: str, text: str, created_at: str
) -> None:
    connection.execute(
        """
        INSERT INTO chat_messages (session_id, ordinal, role, text, created_at)
        VALUES ('session-1', ?, ?, ?, ?)
        """,
        (ordinal, role, text, created_at),
    )


def _add_event(
    connection: sqlite3.Connection, sequence: int, turn_id: str, event_type: str, payload: str
) -> None:
    connection.execute(
        """
        INSERT INTO chat_events (session_id, sequence, turn_id, event_type, event_json)
        VALUES ('session-1', ?, ?, ?, ?)
        """,
        (sequence, turn_id, event_type, payload),
    )


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
    return int(version)


def test_the_version_1_schema_declaration_is_frozen() -> None:
    """v1 선언을 고치면 사용자 디스크의 v1 파일이 manifest 검사에서 거부된다."""
    assert _v1_schema_digest() == V1_SCHEMA_SHA256


def test_the_current_schema_version_is_two() -> None:
    assert SCHEMA_VERSION == 2


def test_a_version_1_file_is_upgraded_in_place_and_stamps_messages_with_their_turn(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assistant.sqlite3"
    connection = _create_v1_file(path)
    with connection:
        _add_turn(connection, "turn-a", "2026-09-20T09:01:00.000000+00:00")
        _add_turn(connection, "turn-b", "2026-09-20T09:05:00.000000+00:00")
        # 사용자 메시지는 턴 시작과 같은 순간일 수 있다(시계 해상도). 그래도 그 턴이다.
        _add_message(connection, 0, "user", "첫 질문", "2026-09-20T09:01:00.000000+00:00")
        _add_message(connection, 1, "assistant", "첫 답", "2026-09-20T09:02:00.000000+00:00")
        _add_message(connection, 2, "user", "둘째 질문", "2026-09-20T09:05:00.000100+00:00")
        _add_message(connection, 3, "assistant", "둘째 답", "2026-09-20T09:06:00.000000+00:00")
    connection.close()

    with AssistantDatabase(path) as database:
        messages = SQLiteChatSessionRepository(database).messages("session-1")

    assert [(message.role, message.text, message.turn_id) for message in messages] == [
        (ChatRole.USER, "첫 질문", "turn-a"),
        (ChatRole.ASSISTANT, "첫 답", "turn-a"),
        (ChatRole.USER, "둘째 질문", "turn-b"),
        (ChatRole.ASSISTANT, "둘째 답", "turn-b"),
    ]
    assert _user_version(path) == 2
    # 올린 파일을 다시 열어도 v2 manifest와 글자 단위로 같다.
    with AssistantDatabase(path) as reopened:
        assert len(SQLiteChatSessionRepository(reopened).messages("session-1")) == 4


def test_a_message_older_than_every_turn_keeps_no_turn(tmp_path: Path) -> None:
    """어느 턴 뒤에도 오지 않는 메시지는 추정해 붙이지 않는다. 화면은 그 질문을 어느 턴에도
    달지 않는다.

    v1에서 세션의 첫 턴 행 저장이 실패한 경우(사용자 메시지만 남음)가 이렇다. 순서로 짝지으면
    그 질문이 다음 턴의 질문이 되고, 뒤 질문이 모두 한 칸씩 밀린다.
    """
    path = tmp_path / "assistant.sqlite3"
    connection = _create_v1_file(path)
    with connection:
        _add_message(connection, 0, "user", "고아 질문", "2026-09-20T08:59:00.000000+00:00")
        _add_turn(connection, "turn-a", "2026-09-20T09:01:00.000000+00:00")
        _add_message(connection, 1, "user", "질문", "2026-09-20T09:01:00.000100+00:00")
    connection.close()

    with AssistantDatabase(path) as database:
        messages = SQLiteChatSessionRepository(database).messages("session-1")

    assert [(message.text, message.turn_id) for message in messages] == [
        ("고아 질문", None),
        ("질문", "turn-a"),
    ]


def test_the_v1_search_budget_notice_rows_become_the_dedicated_event(tmp_path: Path) -> None:
    """v1 통지 행만 다시 쓴다. 출처 없이 끝난 진짜 검색(검색 실패)은 그대로 검색이다."""
    path = tmp_path / "assistant.sqlite3"
    connection = _create_v1_file(path)
    notice_payload = f'{{"query":"{V1_NOTICE}","sources":[]}}'
    with connection:
        _add_turn(connection, "turn-a", "2026-09-20T09:01:00.000000+00:00")
        _add_event(
            connection, 0, "turn-a", "search_activity", '{"query":"실패한 검색","sources":[]}'
        )
        _add_event(connection, 1, "turn-a", "search_activity", notice_payload)
        _add_event(
            connection,
            2,
            "turn-a",
            "search_activity",
            f'{{"query":"{V1_NOTICE}","sources":[{{"title":"t","url":"https://a.test"}}]}}',
        )
    connection.close()

    with AssistantDatabase(path) as database:
        stored_events = SQLiteChatSessionRepository(database).events("session-1")
    events = [stored.event for stored in stored_events]

    assert events == [
        SearchActivity(query="실패한 검색", sources=()),
        SearchBudgetExhausted(),
        SearchActivity(query=V1_NOTICE, sources=(Source(title="t", url="https://a.test"),)),
    ]


def test_a_version_1_file_whose_schema_was_edited_by_hand_is_refused(tmp_path: Path) -> None:
    """v1 manifest가 맞지 않으면 올리지 않는다.

    손으로 완화한 파일의 불변식을 v2로 끌고 가지 않는다. 파일은 v1 그대로 남는다.
    """
    path = tmp_path / "assistant.sqlite3"
    connection = _create_v1_file(path)
    with connection:
        connection.execute("CREATE INDEX extra_index ON chat_messages (role)")
    connection.close()

    with pytest.raises(AssistantStorageError, match="version 1"):
        AssistantDatabase(path)
    assert _user_version(path) == 1
