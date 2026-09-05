from __future__ import annotations

import sqlite3

from ._errors import StrategyRepositoryStorageError

SCHEMA_VERSION = 1
_APPLICATION_ID = 0x5357524B  # ASCII-ish "SWRK", scoped to this adapter's database.

_V1_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS strategy_revisions (
        strategy_id TEXT NOT NULL COLLATE BINARY,
        revision INTEGER NOT NULL CHECK (revision >= 1),
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
)

_REQUIRED_V1_COLUMNS = {
    "strategy_id": ("TEXT", 1, 1),
    "revision": ("INTEGER", 1, 2),
    "schema_version": ("TEXT", 1, 0),
    "spec_json": ("TEXT", 1, 0),
    "spec_hash": ("TEXT", 1, 0),
    "source_format": ("TEXT", 0, 0),
    "source_text": ("TEXT", 0, 0),
    "source_hash": ("TEXT", 0, 0),
    "origin": ("TEXT", 1, 0),
    "created_at": ("TEXT", 1, 0),
    "change_note": ("TEXT", 0, 0),
}


def migrate_schema(connection: sqlite3.Connection) -> None:
    """Advance one database atomically and reject unknown/newer storage contracts."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        application_id = _pragma_int(connection, "application_id")
        version = _pragma_int(connection, "user_version")
        if application_id not in (0, _APPLICATION_ID):
            raise StrategyRepositoryStorageError(
                "SQLite file belongs to another application — "
                f"application_id={application_id} expected={_APPLICATION_ID}"
            )
        if version > SCHEMA_VERSION:
            raise StrategyRepositoryStorageError(
                "SQLite strategy schema is newer than this server — "
                f"stored={version} supported={SCHEMA_VERSION}"
            )
        if version == 0:
            for statement in _V1_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
        if version != SCHEMA_VERSION:  # pragma: no cover - next migration adds a branch above
            raise StrategyRepositoryStorageError(
                f"no migration path — stored={version} supported={SCHEMA_VERSION}"
            )
        _validate_v1_shape(connection)
        if application_id == 0:
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
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


def _validate_v1_shape(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]: (str(row[2]).upper(), row[3], row[5])
        for row in connection.execute("PRAGMA table_info(strategy_revisions)").fetchall()
        if isinstance(row[1], str) and isinstance(row[3], int) and isinstance(row[5], int)
    }
    if columns != _REQUIRED_V1_COLUMNS:
        missing = sorted(_REQUIRED_V1_COLUMNS.keys() - columns.keys())
        unexpected = sorted(columns.keys() - _REQUIRED_V1_COLUMNS.keys())
        incompatible = sorted(
            name
            for name in columns.keys() & _REQUIRED_V1_COLUMNS.keys()
            if columns[name] != _REQUIRED_V1_COLUMNS[name]
        )
        raise StrategyRepositoryStorageError(
            "SQLite strategy schema does not match version 1 — "
            f"missing={missing} unexpected={unexpected} incompatible={incompatible}"
        )
