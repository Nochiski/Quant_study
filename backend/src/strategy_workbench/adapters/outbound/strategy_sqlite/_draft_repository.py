from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.strategy_authoring.facade.authoring import StrategyDraft
from strategy_workbench.application.strategy_authoring.facade.ports import (
    SourceFormat,
    StrategyDraftConflictError,
    StrategyDraftNotFoundError,
)

from ._errors import StrategyRepositoryStorageError
from ._schema import migrate_schema
from ._values import optional_text, required_int, required_text


class SQLiteStrategyDraftRepository:
    """SQLite latest-value register for exact authoring source.

    This adapter never parses or compiles a draft. The authoring application owns identity/base
    validation, while this class owns atomic compare-and-swap and storage integrity.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        self._database = SqliteDatabase(
            path,
            label="strategy draft repository",
            error=StrategyRepositoryStorageError,
            prepare=migrate_schema,
            busy_timeout_seconds=busy_timeout_seconds,
        )

    @property
    def database_path(self) -> Path | None:
        return self._database.database_path

    def get(self, draft_id: str) -> StrategyDraft:
        with self._database.transaction(write=False) as connection:
            draft = self._get_optional(connection, draft_id)
            if draft is None:
                raise StrategyDraftNotFoundError(f"strategy draft not found -- draft_id={draft_id}")
            return draft

    def save(self, draft: StrategyDraft, *, expected_version: int) -> StrategyDraft:
        if draft.version != expected_version + 1:
            raise StrategyRepositoryStorageError(
                "draft version is not the expected CAS successor -- "
                f"draft_id={draft.draft_id} expected={expected_version} "
                f"candidate={draft.version}"
            )
        with self._database.transaction(write=True) as connection:
            if expected_version == 0:
                try:
                    connection.execute(
                        """
                        INSERT INTO strategy_drafts (
                            draft_id, version, source_format, source_text, source_hash,
                            schema_version, strategy_id, base_revision, base_spec_hash, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._values(draft),
                    )
                except sqlite3.IntegrityError as error:
                    current = self._get_optional(connection, draft.draft_id)
                    if current is None:
                        raise
                    raise StrategyDraftConflictError(
                        "strategy draft already exists -- "
                        f"draft_id={draft.draft_id} current_version={current.version}",
                        current=current,
                    ) from error
                return draft

            updated = connection.execute(
                """
                UPDATE strategy_drafts
                SET version = ?, source_format = ?, source_text = ?, source_hash = ?,
                    schema_version = ?, strategy_id = ?, base_revision = ?,
                    base_spec_hash = ?, updated_at = ?
                WHERE draft_id = ? AND version = ?
                """,
                (
                    draft.version,
                    draft.format.value,
                    draft.source,
                    draft.source_hash,
                    draft.schema_version,
                    draft.strategy_id,
                    draft.base_revision,
                    draft.base_spec_hash,
                    datetime_text(draft.updated_at, field="draft updated_at"),
                    draft.draft_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                current = self._get_optional(connection, draft.draft_id)
                raise StrategyDraftConflictError(
                    "strategy draft version conflict -- "
                    f"draft_id={draft.draft_id} expected={expected_version} "
                    f"current={current.version if current else None}",
                    current=current,
                )
            return draft

    def delete(self, draft_id: str, *, expected_version: int) -> None:
        with self._database.transaction(write=True) as connection:
            deleted = connection.execute(
                "DELETE FROM strategy_drafts WHERE draft_id = ? AND version = ?",
                (draft_id, expected_version),
            )
            if deleted.rowcount == 1:
                return
            current = self._get_optional(connection, draft_id)
            if current is None:
                raise StrategyDraftNotFoundError(f"strategy draft not found -- draft_id={draft_id}")
            raise StrategyDraftConflictError(
                "strategy draft version conflict during delete -- "
                f"draft_id={draft_id} expected={expected_version} current={current.version}",
                current=current,
            )

    def close(self) -> None:
        self._database.close()

    @staticmethod
    def _values(draft: StrategyDraft) -> tuple[object, ...]:
        return (
            draft.draft_id,
            draft.version,
            draft.format.value,
            draft.source,
            draft.source_hash,
            draft.schema_version,
            draft.strategy_id,
            draft.base_revision,
            draft.base_spec_hash,
            datetime_text(draft.updated_at, field="draft updated_at"),
        )

    @staticmethod
    def _get_optional(connection: sqlite3.Connection, draft_id: str) -> StrategyDraft | None:
        row = connection.execute(
            "SELECT * FROM strategy_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            updated_at_text = required_text(row, "updated_at")
            updated_at = datetime.fromisoformat(updated_at_text)
            if datetime_text(updated_at, field="draft updated_at") != updated_at_text:
                raise ValueError("updated_at is not canonical timezone-aware UTC text")
            return StrategyDraft(
                draft_id=required_text(row, "draft_id"),
                version=required_int(row, "version"),
                format=SourceFormat(required_text(row, "source_format")),
                source=required_text(row, "source_text"),
                source_hash=required_text(row, "source_hash"),
                schema_version=required_text(row, "schema_version"),
                strategy_id=optional_text(row, "strategy_id"),
                base_revision=(
                    None if row["base_revision"] is None else required_int(row, "base_revision")
                ),
                base_spec_hash=optional_text(row, "base_spec_hash"),
                updated_at=updated_at,
            )
        except (TypeError, ValueError) as error:
            raise StrategyRepositoryStorageError(
                f"stored strategy draft failed integrity validation -- draft_id={draft_id}: {error}"
            ) from error
