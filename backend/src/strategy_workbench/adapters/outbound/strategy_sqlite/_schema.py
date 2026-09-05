from __future__ import annotations

import sqlite3

from ._errors import StrategyRepositoryStorageError

SCHEMA_VERSION = 1
_APPLICATION_ID = 0x5357524B  # ASCII-ish "SWRK", scoped to this adapter's database.

_V1_SCHEMA_OBJECTS = (
    (
        "table",
        "strategy_heads",
        """
        CREATE TABLE strategy_heads (
            strategy_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY,
            latest_revision INTEGER NOT NULL CHECK (
                typeof(latest_revision) = 'integer' AND latest_revision >= 1
            )
        ) WITHOUT ROWID
        """,
    ),
    (
        "table",
        "strategy_revisions",
        """
        CREATE TABLE strategy_revisions (
            strategy_id TEXT NOT NULL COLLATE BINARY,
            revision INTEGER NOT NULL CHECK (
                typeof(revision) = 'integer' AND revision >= 1
            ),
            schema_version TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK (length(spec_hash) = 64),
            source_format TEXT CHECK (source_format IN ('yaml', 'json')),
            source_text TEXT,
            source_hash TEXT CHECK (source_hash IS NULL OR length(source_hash) = 64),
            origin TEXT NOT NULL CHECK (origin IN ('document', 'legacy_json')),
            created_at TEXT NOT NULL,
            change_note TEXT,
            PRIMARY KEY (strategy_id, revision),
            FOREIGN KEY (strategy_id) REFERENCES strategy_heads(strategy_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            CHECK (
                (origin = 'document'
                    AND source_format IS NOT NULL
                    AND source_text IS NOT NULL
                    AND source_hash IS NOT NULL)
                OR
                (origin = 'legacy_json'
                    AND source_format IS NULL
                    AND source_text IS NULL
                    AND source_hash IS NULL)
            )
        ) WITHOUT ROWID
        """,
    ),
    (
        "trigger",
        "strategy_revisions_immutable_update",
        """
        CREATE TRIGGER strategy_revisions_immutable_update
        BEFORE UPDATE ON strategy_revisions
        BEGIN
            SELECT RAISE(ABORT, 'strategy revisions are immutable');
        END
        """,
    ),
    (
        "trigger",
        "strategy_revisions_immutable_delete",
        """
        CREATE TRIGGER strategy_revisions_immutable_delete
        BEFORE DELETE ON strategy_revisions
        BEGIN
            SELECT RAISE(ABORT, 'strategy revisions are immutable');
        END
        """,
    ),
)


def migrate_schema(connection: sqlite3.Connection) -> None:
    """Create our schema atomically, while refusing to claim or weaken any foreign file."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        application_id = _pragma_int(connection, "application_id")
        version = _pragma_int(connection, "user_version")
        footprint = _schema_footprint(connection)

        if application_id == 0:
            if version != 0 or footprint:
                raise StrategyRepositoryStorageError(
                    "refusing to claim a non-empty SQLite database without this "
                    f"application id -- user_version={version} objects={footprint}"
                )
            for _object_type, _name, statement in _V1_SCHEMA_OBJECTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
        elif application_id != _APPLICATION_ID:
            raise StrategyRepositoryStorageError(
                "SQLite file belongs to another application -- "
                f"application_id={application_id} expected={_APPLICATION_ID}"
            )

        if version > SCHEMA_VERSION:
            raise StrategyRepositoryStorageError(
                "SQLite strategy schema is newer than this server -- "
                f"stored={version} supported={SCHEMA_VERSION}"
            )
        if version != SCHEMA_VERSION:  # pragma: no cover - next migration adds a branch above
            raise StrategyRepositoryStorageError(
                f"no migration path -- stored={version} supported={SCHEMA_VERSION}"
            )
        _validate_v1_manifest(connection)
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or not isinstance(row[0], int):  # pragma: no cover - SQLite invariant
        raise StrategyRepositoryStorageError(f"SQLite PRAGMA {name} returned an invalid value")
    return row[0]


def _schema_footprint(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """Return every persisted schema object, including SQLite-managed objects.

    An unowned database is claimable only when this footprint is empty. SQLite can leave
    ``sqlite_sequence`` behind after an AUTOINCREMENT table is dropped, so internal names must
    not be treated as evidence of an empty file.
    """
    rows = connection.execute(
        """
        SELECT type, name
        FROM sqlite_schema
        ORDER BY type COLLATE BINARY, name COLLATE BINARY
        """
    ).fetchall()
    footprint: list[tuple[str, str]] = []
    for object_type, name in rows:
        if not isinstance(object_type, str) or not isinstance(name, str):
            raise StrategyRepositoryStorageError(
                "SQLite strategy schema contains an invalid object identity"
            )
        footprint.append((object_type, name))
    return tuple(footprint)


def _schema_objects(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], str | None]:
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
            raise StrategyRepositoryStorageError(
                "SQLite strategy schema contains an invalid object identity"
            )
        if sql is not None and not isinstance(sql, str):  # pragma: no cover - SQLite invariant
            raise StrategyRepositoryStorageError(
                "SQLite strategy schema contains an object with invalid SQL"
            )
        objects[(object_type, name)] = None if sql is None else _normalise_sql(sql)
    return objects


def _validate_v1_manifest(connection: sqlite3.Connection) -> None:
    expected = {
        (object_type, name): _normalise_sql(statement)
        for object_type, name, statement in _V1_SCHEMA_OBJECTS
    }
    actual = _schema_objects(connection)
    if actual == expected:
        return

    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    incompatible = sorted(
        key for key in actual.keys() & expected.keys() if actual[key] != expected[key]
    )
    raise StrategyRepositoryStorageError(
        "SQLite strategy schema does not match version 1 -- "
        f"missing={missing} unexpected={unexpected} incompatible={incompatible}"
    )


def _normalise_sql(statement: str) -> str:
    # sqlite_schema preserves the submitted DDL after trimming its outer whitespace. Compare
    # that representation exactly: case and whitespace inside quoted literals are data, not
    # formatting, and may change a CHECK constraint or trigger's behaviour.
    return statement.strip()
