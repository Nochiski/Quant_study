"""어시스턴트 DB의 스키마와 마이그레이션 (설계 spec D5).

전략 revision DB와 **다른 파일**이다. 저 쪽은 revision이 immutable이라 UPDATE/DELETE를 트리거로
막지만, 여기 턴은 RUNNING → 종료로 바뀌고 이벤트는 계속 붙는다. 두 수명을 한 파일에 섞으면
한쪽 트리거가 다른 쪽을 막는다.

`application_id`를 따로 두어 남의 SQLite 파일을 이 스키마로 덮어쓰지 않는다. 빈 파일만 claim하고,
다른 application id를 만나면 거부한다(`strategy_sqlite/_schema.py`와 같은 방어).

## 버전

| 버전 | 바뀐 것 |
|---|---|
| 1 | 최초(A-03) |
| 2 | `chat_messages.turn_id` 추가, v1 검색 상한 통지 행을 전용 이벤트로(C-03) |
| 3 | `chat_sessions.run_id` 추가, CHECK를 "전략·초안·실행 중 정확히 하나"로(결과 설명 D-02) |

v1 파일은 열 때 제자리에서 v2, v3으로 차례로 올린다. v1·v2 선언(`V1_SCHEMA_OBJECTS`·
`V2_SCHEMA_OBJECTS`)은 이미 사용자 디스크에 쓰인 사실이라 고치지 않는다 — manifest 검사가 그 선언과
글자 단위로 비교하므로, 고치면 옛 파일이 올리기 전에 거부된다. 올리기는 그 버전의 manifest를 먼저
확인하고 한 트랜잭션에서 끝낸다. 중간에 실패하면 파일은 원래 버전 그대로 남는다.

## v3: 실행에 붙은 세션 (결과 설명 spec R3)

v3은 `chat_sessions`의 CHECK를 바꾼다. SQLite에는 CHECK를 바꾸는 `ALTER`가 없어 v2와 같이 옛
표를 비켜 두고 원래 이름으로 새로 만든다. 다만 `chat_sessions`는 다른 세 표가 외래 키로 참조하는
**부모**다.

- 외래 키를 끈 채로 돈다. 켜 두면 옛 표를 지울 때 자식 행 때문에 RESTRICT가 막는다.
  `PRAGMA foreign_keys`는 트랜잭션 안에서 바뀌지 않으므로 `migrate_schema`가 BEGIN 전에 끄고 끝나면
  되돌린다. 커밋 전에 `foreign_key_check`로 고아 행이 없는지 본다.
- 옛 표 이름을 바꿀 때 `legacy_alter_table`을 켠다. 켜지 않으면 SQLite 3.26+가 자식 표의
  `REFERENCES chat_sessions`까지 새 이름으로 고쳐 써서 자식 DDL이 선언과 글자 단위로 달라진다.
"""

from __future__ import annotations

import sqlite3

from ._errors import AssistantStorageError
from ._upgrade_v2 import copy_messages_with_turn_ids, rewrite_v1_search_budget_notices

SCHEMA_VERSION = 3

# ASCII-ish "SWAI". 전략 revision DB의 0x5357524B("SWRK")와 달라야 두 파일을 서로 열지 않는다.
_APPLICATION_ID = 0x53574149

V1_SCHEMA_OBJECTS: tuple[tuple[str, str, str], ...] = (
    (
        "table",
        "provider_profiles",
        """
        CREATE TABLE provider_profiles (
            profile_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY CHECK (
                length(trim(profile_id)) >= 1
            ),
            ordinal INTEGER NOT NULL CHECK (
                typeof(ordinal) = 'integer' AND ordinal >= 0
            ),
            kind TEXT NOT NULL CHECK (kind IN ('anthropic', 'openai')),
            label TEXT NOT NULL,
            model TEXT NOT NULL CHECK (length(trim(model)) >= 1),
            base_url TEXT,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1))
        ) WITHOUT ROWID
        """,
    ),
    (
        "index",
        "provider_profiles_order",
        """
        CREATE UNIQUE INDEX provider_profiles_order ON provider_profiles (ordinal)
        """,
    ),
    (
        "index",
        "provider_profiles_single_active",
        """
        CREATE UNIQUE INDEX provider_profiles_single_active
        ON provider_profiles (active) WHERE active = 1
        """,
    ),
    (
        "table",
        "chat_sessions",
        """
        CREATE TABLE chat_sessions (
            session_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY CHECK (
                length(trim(session_id)) >= 1
            ),
            ordinal INTEGER NOT NULL CHECK (
                typeof(ordinal) = 'integer' AND ordinal >= 0
            ),
            strategy_id TEXT COLLATE BINARY,
            revision INTEGER,
            draft_id TEXT COLLATE BINARY,
            provider_profile_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            -- `DocumentRef`와 같은 불변식이다: 저장된 전략과 초안 중 정확히 하나, revision은
            -- 저장된 전략에만. 행이 둘 다 담으면 읽을 때 값 타입 생성이 ValueError로 터져
            -- 저장 오류가 아닌 예외가 포트 밖으로 샌다.
            CHECK (
                (strategy_id IS NOT NULL AND draft_id IS NULL)
                OR (strategy_id IS NULL AND draft_id IS NOT NULL AND revision IS NULL)
            ),
            CHECK (revision IS NULL OR (typeof(revision) = 'integer' AND revision >= 1))
        ) WITHOUT ROWID
        """,
    ),
    (
        "index",
        "chat_sessions_order",
        """
        CREATE UNIQUE INDEX chat_sessions_order ON chat_sessions (ordinal)
        """,
    ),
    (
        "table",
        "chat_turns",
        """
        CREATE TABLE chat_turns (
            turn_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY CHECK (
                length(trim(turn_id)) >= 1
            ),
            session_id TEXT NOT NULL COLLATE BINARY,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'completed', 'failed', 'cancelled')
            ),
            accepted_sequence INTEGER NOT NULL CHECK (
                typeof(accepted_sequence) = 'integer' AND accepted_sequence >= -1
            ),
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        ) WITHOUT ROWID
        """,
    ),
    (
        "table",
        "chat_messages",
        """
        CREATE TABLE chat_messages (
            session_id TEXT NOT NULL COLLATE BINARY,
            ordinal INTEGER NOT NULL CHECK (
                typeof(ordinal) = 'integer' AND ordinal >= 0
            ),
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, ordinal),
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        ) WITHOUT ROWID
        """,
    ),
    (
        "table",
        "chat_events",
        """
        CREATE TABLE chat_events (
            session_id TEXT NOT NULL COLLATE BINARY,
            sequence INTEGER NOT NULL CHECK (
                typeof(sequence) = 'integer' AND sequence >= 0
            ),
            turn_id TEXT NOT NULL COLLATE BINARY,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL,
            PRIMARY KEY (session_id, sequence),
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            FOREIGN KEY (turn_id) REFERENCES chat_turns(turn_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        ) WITHOUT ROWID
        """,
    ),
    (
        "index",
        "chat_turns_by_session",
        "CREATE INDEX chat_turns_by_session ON chat_turns (session_id, started_at)",
    ),
)


# v2의 `chat_messages`. `turn_id`에는 외래 키를 걸지 않는다 — 러너는 거절(세션 없음·활성 공급자
# 없음)을 호출 스레드에서 돌려주려고 사용자 메시지를 턴 행보다 **먼저** 쓴다. 걸면 모든 첫 질문이
# 거부된다. NULL은 v1에서 올라온, 어느 턴 뒤에도 오지 않는 메시지뿐이다(`_upgrade_v2.py`).
_CHAT_MESSAGES_V2 = """
        CREATE TABLE chat_messages (
            session_id TEXT NOT NULL COLLATE BINARY,
            ordinal INTEGER NOT NULL CHECK (
                typeof(ordinal) = 'integer' AND ordinal >= 0
            ),
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            turn_id TEXT COLLATE BINARY CHECK (
                turn_id IS NULL OR length(trim(turn_id)) >= 1
            ),
            PRIMARY KEY (session_id, ordinal),
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        ) WITHOUT ROWID
        """

V2_SCHEMA_OBJECTS: tuple[tuple[str, str, str], ...] = tuple(
    ("table", "chat_messages", _CHAT_MESSAGES_V2)
    if (object_type, name) == ("table", "chat_messages")
    else (object_type, name, statement)
    for object_type, name, statement in V1_SCHEMA_OBJECTS
)

# v3의 `chat_sessions`. `DocumentRef`와 같은 불변식이다: 저장된 전략·초안·실행 중 정확히 하나,
# revision은 저장된 전략에만. 행이 둘 이상을 담으면 읽을 때 값 타입 생성이 ValueError로 터져 저장
# 오류가 아닌 예외가 포트 밖으로 샌다.
_CHAT_SESSIONS_V3 = """
        CREATE TABLE chat_sessions (
            session_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY CHECK (
                length(trim(session_id)) >= 1
            ),
            ordinal INTEGER NOT NULL CHECK (
                typeof(ordinal) = 'integer' AND ordinal >= 0
            ),
            strategy_id TEXT COLLATE BINARY,
            revision INTEGER,
            draft_id TEXT COLLATE BINARY,
            run_id TEXT COLLATE BINARY,
            provider_profile_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            CHECK (
                (strategy_id IS NOT NULL AND draft_id IS NULL AND run_id IS NULL)
                OR (
                    strategy_id IS NULL AND draft_id IS NOT NULL AND run_id IS NULL
                    AND revision IS NULL
                )
                OR (
                    strategy_id IS NULL AND draft_id IS NULL AND run_id IS NOT NULL
                    AND revision IS NULL
                )
            ),
            CHECK (revision IS NULL OR (typeof(revision) = 'integer' AND revision >= 1))
        ) WITHOUT ROWID
        """

V3_SCHEMA_OBJECTS: tuple[tuple[str, str, str], ...] = tuple(
    ("table", "chat_sessions", _CHAT_SESSIONS_V3)
    if (object_type, name) == ("table", "chat_sessions")
    else (object_type, name, statement)
    for object_type, name, statement in V2_SCHEMA_OBJECTS
)

# 지금 버전의 선언. 새 파일은 이것으로 만들고, 열 때마다 이것과 대조한다.
_CURRENT_SCHEMA_OBJECTS = V3_SCHEMA_OBJECTS


def migrate_schema(connection: sqlite3.Connection) -> None:
    """빈 파일에는 스키마를 만들고 옛 파일은 지금 버전까지 올린다. 남의 파일·미래 버전은 거부한다.

    외래 키를 끈 채로 돈다(모듈 docstring v3). in-memory DB는 이 연결을 계속 쓰므로 어떤 경로로
    끝나든 원래 값으로 되돌린다.
    """
    foreign_keys = _pragma_int(connection, "foreign_keys")
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        _migrate(connection)
    finally:
        connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")


def _migrate(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        application_id = _pragma_int(connection, "application_id")
        version = _pragma_int(connection, "user_version")
        footprint = _schema_footprint(connection)

        if application_id == 0:
            if version != 0 or footprint:
                raise AssistantStorageError(
                    "refusing to claim a non-empty SQLite database without this "
                    f"application id — user_version={version} objects={footprint}"
                )
            for _object_type, _name, statement in _CURRENT_SCHEMA_OBJECTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
        elif application_id != _APPLICATION_ID:
            raise AssistantStorageError(
                "SQLite file belongs to another application — "
                f"application_id={application_id} expected={_APPLICATION_ID}"
            )

        if version == 1:
            _validate_manifest(connection, V1_SCHEMA_OBJECTS, version=1)
            _upgrade_v1_to_v2(connection)
            version = 2
        if version == 2:
            _validate_manifest(connection, V2_SCHEMA_OBJECTS, version=2)
            _upgrade_v2_to_v3(connection)
            version = 3
        if version != SCHEMA_VERSION:
            raise AssistantStorageError(
                "SQLite assistant schema version is not supported — "
                f"stored={version} supported={SCHEMA_VERSION}"
            )
        _validate_manifest(connection, _CURRENT_SCHEMA_OBJECTS, version=SCHEMA_VERSION)
        orphans = connection.execute("PRAGMA foreign_key_check").fetchall()
        if orphans:
            raise AssistantStorageError(
                "SQLite assistant schema has rows whose parent is missing — "
                f"violations={[tuple(row) for row in orphans[:5]]} total={len(orphans)}"
            )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _upgrade_v1_to_v2(connection: sqlite3.Connection) -> None:
    """v1 파일을 제자리에서 v2로. 호출자의 트랜잭션 안에서 돈다.

    SQLite는 칼럼 추가(`ADD COLUMN`)를 원래 DDL 끝에 이어 붙여 저장하므로 v2 선언과 글자 단위로
    같아지지 않는다. 그래서 표를 새로 만들고 옮긴다. 옛 표를 먼저 다른 이름으로 비켜 두는 이유는
    새 표가 같은 이름(`chat_messages`)으로 선언과 똑같이 저장돼야 하기 때문이다 — 새 표를 다른
    이름으로 만든 뒤 바꾸면 SQLite가 이름을 따옴표로 감싸 DDL 글자가 달라진다.
    """
    connection.execute("ALTER TABLE chat_messages RENAME TO chat_messages_v1")
    connection.execute(_CHAT_MESSAGES_V2)
    copy_messages_with_turn_ids(connection, source_table="chat_messages_v1")
    connection.execute("DROP TABLE chat_messages_v1")
    rewrite_v1_search_budget_notices(connection)
    connection.execute("PRAGMA user_version = 2")


def _upgrade_v2_to_v3(connection: sqlite3.Connection) -> None:
    """v2 파일을 제자리에서 v3으로. 호출자의 트랜잭션 안에서, 외래 키를 끈 채로 돈다.

    옛 행은 전부 저장 전략이나 초안에 붙어 있으므로 `run_id`는 NULL로 옮긴다. 인덱스는 옛 표와 함께
    지워지므로 선언의 DDL로 다시 만든다(manifest 대조가 글자 단위다).
    """
    connection.execute("PRAGMA legacy_alter_table = ON")
    try:
        connection.execute("ALTER TABLE chat_sessions RENAME TO chat_sessions_v2")
    finally:
        connection.execute("PRAGMA legacy_alter_table = OFF")
    connection.execute(_CHAT_SESSIONS_V3)
    connection.execute(
        """
        INSERT INTO chat_sessions (
            session_id, ordinal, strategy_id, revision, draft_id, run_id,
            provider_profile_id, created_at, title
        )
        SELECT
            session_id, ordinal, strategy_id, revision, draft_id, NULL,
            provider_profile_id, created_at, title
        FROM chat_sessions_v2
        """
    )
    connection.execute("DROP TABLE chat_sessions_v2")
    for object_type, name, statement in V3_SCHEMA_OBJECTS:
        if (object_type, name) == ("index", "chat_sessions_order"):
            connection.execute(statement)
    connection.execute("PRAGMA user_version = 3")


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or not isinstance(row[0], int):  # pragma: no cover - SQLite 불변식
        raise AssistantStorageError(f"SQLite PRAGMA {name} returned an invalid value")
    return row[0]


def _schema_footprint(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """파일에 남아 있는 모든 스키마 객체. 이게 비어 있을 때만 claim한다."""
    return tuple((object_type, name) for (object_type, name), _sql in _objects(connection).items())


def _objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str | None]:
    rows = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_schema
        ORDER BY type COLLATE BINARY, name COLLATE BINARY
        """
    ).fetchall()
    objects: dict[tuple[str, str], str | None] = {}
    for object_type, name, sql in rows:
        if not isinstance(object_type, str) or not isinstance(name, str):
            raise AssistantStorageError(
                "SQLite assistant schema contains an invalid object identity"
            )
        if sql is not None and not isinstance(sql, str):  # pragma: no cover - SQLite 불변식
            raise AssistantStorageError(
                "SQLite assistant schema contains an object with invalid SQL"
            )
        objects[(object_type, name)] = None if sql is None else sql.strip()
    return objects


def _validate_manifest(
    connection: sqlite3.Connection,
    manifest: tuple[tuple[str, str, str], ...],
    *,
    version: int,
) -> None:
    """저장된 DDL이 이 버전의 선언과 글자 단위로 같은지 본다.

    CHECK 제약과 부분 유니크 인덱스가 이 어댑터의 불변식(활성 하나, sequence 단조)을 집행하므로,
    누가 손으로 완화한 파일을 그대로 열면 불변식이 조용히 사라진다.
    """
    expected = {(object_type, name): statement.strip() for object_type, name, statement in manifest}
    actual = _objects(connection)
    if actual == expected:
        return
    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    incompatible = sorted(
        key for key in actual.keys() & expected.keys() if actual[key] != expected[key]
    )
    raise AssistantStorageError(
        f"SQLite assistant schema does not match version {version} — "
        f"missing={missing} unexpected={unexpected} incompatible={incompatible}"
    )
