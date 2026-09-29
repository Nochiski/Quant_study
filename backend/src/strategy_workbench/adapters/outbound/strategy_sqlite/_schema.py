from __future__ import annotations

import sqlite3

from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    SchemaUpgrade,
    ensure_schema,
)

from ._errors import StrategyRepositoryStorageError

SCHEMA_VERSION = 2
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

_V2_ADDED_SCHEMA_OBJECTS = (
    (
        "table",
        "strategy_drafts",
        """
        CREATE TABLE strategy_drafts (
            draft_id TEXT NOT NULL COLLATE BINARY PRIMARY KEY CHECK (
                length(draft_id) BETWEEN 1 AND 512 AND length(trim(draft_id)) >= 1
            ),
            version INTEGER NOT NULL CHECK (
                typeof(version) = 'integer' AND version >= 1
            ),
            source_format TEXT NOT NULL CHECK (source_format IN ('yaml', 'json')),
            source_text TEXT NOT NULL,
            source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
            schema_version TEXT NOT NULL CHECK (length(trim(schema_version)) >= 1),
            strategy_id TEXT COLLATE BINARY,
            base_revision INTEGER,
            base_spec_hash TEXT,
            updated_at TEXT NOT NULL,
            CHECK (
                (strategy_id IS NULL
                    AND base_revision IS NULL
                    AND base_spec_hash IS NULL)
                OR
                (strategy_id IS NOT NULL
                    AND length(trim(strategy_id)) >= 1
                    AND typeof(base_revision) = 'integer'
                    AND base_revision >= 1
                    AND length(base_spec_hash) = 64)
            )
        ) WITHOUT ROWID
        """,
    ),
)


def migrate_schema(connection: sqlite3.Connection) -> None:
    """Create our schema atomically, while refusing to claim or weaken any foreign file."""
    # 선언을 호출 시점에 읽는다 — 테스트가 모듈의 선언을 바꿔 실패하는 claim 을 재현한다.
    ensure_schema(
        connection,
        SchemaContract(
            label="strategy",
            application_id=_APPLICATION_ID,
            version=SCHEMA_VERSION,
            objects=(*_V1_SCHEMA_OBJECTS, *_V2_ADDED_SCHEMA_OBJECTS),
            error=StrategyRepositoryStorageError,
            upgrades=(SchemaUpgrade(1, _V1_SCHEMA_OBJECTS, _upgrade_v1_to_v2),),
        ),
    )


def _upgrade_v1_to_v2(connection: sqlite3.Connection) -> None:
    for _object_type, _name, statement in _V2_ADDED_SCHEMA_OBJECTS:
        connection.execute(statement)
