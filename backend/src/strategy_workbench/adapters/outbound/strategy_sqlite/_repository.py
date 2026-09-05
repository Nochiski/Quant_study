from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from threading import RLock
from typing import ClassVar

from strategy_workbench.application.strategy_design.facade.ports import (
    Page,
    PageRequest,
    RevisionSummary,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
    StrategyRevisionRecord,
    StrategySummary,
)

from ._errors import StrategyRepositoryStorageError
from ._record_codec import SourceSpecHashResolver, decode_record, encode_record
from ._schema import SCHEMA_VERSION, migrate_schema

_LATEST_ROWS_SQL = """
SELECT revisions.*
FROM strategy_heads AS heads
INNER JOIN strategy_revisions AS revisions
    ON revisions.strategy_id = heads.strategy_id
    AND revisions.revision = heads.latest_revision
ORDER BY heads.strategy_id COLLATE BINARY ASC
LIMIT ? OFFSET ?
"""


class SQLiteStrategyRepository:
    """SQLite adapter for immutable strategy revisions.

    A file path gives durable process-restart storage. ``None`` creates an isolated in-memory
    SQLite database for composition-root tests; both modes execute the same schema and queries.
    Domain canonicalisation/hydration remains the only StrategySpec serialisation contract. The
    injected source hash resolver delegates document compilation back to the authoring SoT.
    """

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        source_spec_hash: SourceSpecHashResolver,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        if busy_timeout_seconds <= 0:
            raise ValueError("busy_timeout_seconds must be positive")
        self._source_spec_hash = source_spec_hash
        self._busy_timeout_seconds = busy_timeout_seconds
        self._busy_timeout_ms = max(1, round(busy_timeout_seconds * 1000))
        self._lock = RLock()
        self._closed = False
        self._memory_connection: sqlite3.Connection | None = None
        self._path: Path | None
        if path is None:
            self._path = None
            self._memory_connection = self._new_connection(":memory:")
            self._prepare_database(self._memory_connection)
        else:
            candidate = Path(path).expanduser()
            if candidate.exists() and candidate.is_dir():
                raise ValueError(f"strategy repository path is a directory -- path={candidate}")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            self._path = candidate.resolve()
            with closing(self._new_connection(str(self._path))) as connection:
                self._prepare_database(connection)

    @property
    def database_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None

    def __enter__(self) -> SQLiteStrategyRepository:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add(self, record: StrategyRevisionRecord) -> None:
        values = encode_record(record, source_spec_hash=self._source_spec_hash)
        with self._transaction(write=True) as connection:
            latest_revision = self._validated_latest_revision(connection, record.strategy_id)
            if latest_revision is not None:
                raise StrategyRevisionConflictError(
                    f"strategy already exists -- strategy_id={record.strategy_id}",
                    latest_revision=latest_revision,
                )
            if record.revision != 1:
                raise StrategyRevisionConflictError(
                    f"first revision must be 1 -- revision={record.revision}"
                )
            connection.execute(
                "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
                (record.strategy_id, 1),
            )
            self._insert(connection, values)

    def append(self, record: StrategyRevisionRecord, *, expected_revision: int) -> None:
        values = encode_record(record, source_spec_hash=self._source_spec_hash)
        with self._transaction(write=True) as connection:
            actual_revision = self._validated_latest_revision(connection, record.strategy_id)
            if actual_revision is None:
                raise StrategyNotFoundError(
                    f"strategy not found -- strategy_id={record.strategy_id}"
                )
            if actual_revision != expected_revision:
                raise StrategyRevisionConflictError(
                    "strategy revision conflict -- "
                    f"strategy_id={record.strategy_id} expected={expected_revision} "
                    f"actual={actual_revision}",
                    latest_revision=actual_revision,
                )
            if record.revision != expected_revision + 1:
                raise StrategyRevisionConflictError(
                    "next revision is not monotonic -- "
                    f"expected={expected_revision + 1} actual={record.revision}",
                    latest_revision=actual_revision,
                )
            self._insert(connection, values)
            updated = connection.execute(
                """
                UPDATE strategy_heads
                SET latest_revision = ?
                WHERE strategy_id = ? AND latest_revision = ?
                """,
                (record.revision, record.strategy_id, expected_revision),
            )
            if updated.rowcount != 1:  # pragma: no cover - BEGIN IMMEDIATE owns the writer slot
                raise StrategyRepositoryStorageError(
                    "strategy head changed inside a serialised append -- "
                    f"strategy_id={record.strategy_id}"
                )

    def get(self, strategy_id: str, revision: int | None = None) -> StrategyRevisionRecord:
        with self._transaction(write=False) as connection:
            latest_revision = self._validated_latest_revision(connection, strategy_id)
            if latest_revision is None:
                raise StrategyNotFoundError(f"strategy not found -- strategy_id={strategy_id}")
            requested_revision = latest_revision if revision is None else revision
            row = connection.execute(
                """
                SELECT * FROM strategy_revisions
                WHERE strategy_id = ? AND revision = ?
                """,
                (strategy_id, requested_revision),
            ).fetchone()
            if row is None:
                raise StrategyNotFoundError(
                    "strategy revision not found -- "
                    f"strategy_id={strategy_id} revision={requested_revision}"
                )
            return self._decode(row)

    def list_strategies(self, page: PageRequest) -> Page[StrategySummary]:
        with self._transaction(write=False) as connection:
            self._audit_all_revision_chains(connection)
            total_row = connection.execute("SELECT COUNT(*) FROM strategy_heads").fetchone()
            total = self._count_from_row(total_row)
            rows = connection.execute(_LATEST_ROWS_SQL, (page.limit, page.offset)).fetchall()
            records = tuple(self._decode(row) for row in rows)
        return Page(
            items=tuple(
                StrategySummary(
                    strategy_id=record.strategy_id,
                    title=record.spec.title,
                    latest_revision=record.revision,
                    spec_hash=record.spec_hash,
                    updated_at=record.provenance.created_at,
                )
                for record in records
            ),
            total=total,
            offset=page.offset,
            limit=page.limit,
        )

    def history(self, strategy_id: str, page: PageRequest) -> Page[RevisionSummary]:
        with self._transaction(write=False) as connection:
            latest_revision = self._validated_latest_revision(connection, strategy_id)
            if latest_revision is None:
                raise StrategyNotFoundError(f"strategy not found -- strategy_id={strategy_id}")
            rows = connection.execute(
                """
                SELECT * FROM strategy_revisions
                WHERE strategy_id = ?
                ORDER BY revision ASC
                LIMIT ? OFFSET ?
                """,
                (strategy_id, page.limit, page.offset),
            ).fetchall()
            records = tuple(self._decode(row) for row in rows)
        return Page(
            items=tuple(
                RevisionSummary(
                    strategy_id=record.strategy_id,
                    revision=record.revision,
                    spec_hash=record.spec_hash,
                    origin=record.provenance.origin,
                    created_at=record.provenance.created_at,
                    source_format=record.source.format if record.source else None,
                    source_hash=record.source.source_hash if record.source else None,
                    change_note=record.provenance.change_note,
                )
                for record in records
            ),
            total=latest_revision,
            offset=page.offset,
            limit=page.limit,
        )

    def _prepare_database(self, connection: sqlite3.Connection) -> None:
        try:
            migrate_schema(connection)
        except StrategyRepositoryStorageError:
            raise
        except sqlite3.DatabaseError as error:
            raise StrategyRepositoryStorageError(
                f"could not initialise SQLite strategy repository -- {error}"
            ) from error

    def _new_connection(self, database: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            database,
            timeout=self._busy_timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            if self._memory_connection is not None:
                yield self._memory_connection
                return
            assert self._path is not None
            with closing(self._new_connection(str(self._path))) as connection:
                yield connection

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield connection
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                else:
                    connection.commit()
        except (
            StrategyNotFoundError,
            StrategyRevisionConflictError,
            StrategyRepositoryStorageError,
        ):
            raise
        except sqlite3.DatabaseError as error:
            raise StrategyRepositoryStorageError(
                f"SQLite strategy repository operation failed -- {error}"
            ) from error

    @staticmethod
    def _insert(connection: sqlite3.Connection, values: tuple[object, ...]) -> None:
        connection.execute(
            """
            INSERT INTO strategy_revisions (
                strategy_id,
                revision,
                schema_version,
                spec_json,
                spec_hash,
                source_format,
                source_text,
                source_hash,
                origin,
                created_at,
                change_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def _decode(self, row: sqlite3.Row) -> StrategyRevisionRecord:
        return decode_record(row, source_spec_hash=self._source_spec_hash)

    def _audit_all_revision_chains(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT strategy_id FROM strategy_heads
            UNION
            SELECT strategy_id FROM strategy_revisions
            ORDER BY strategy_id COLLATE BINARY
            """
        ).fetchall()
        for row in rows:
            strategy_id = row[0]
            if not isinstance(strategy_id, str):  # pragma: no cover - TEXT column invariant
                raise StrategyRepositoryStorageError("stored strategy id is not text")
            self._validated_latest_revision(connection, strategy_id)

    def _validated_latest_revision(
        self, connection: sqlite3.Connection, strategy_id: str
    ) -> int | None:
        head = connection.execute(
            "SELECT latest_revision FROM strategy_heads WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        aggregate = connection.execute(
            """
            SELECT COUNT(*), MIN(revision), MAX(revision)
            FROM strategy_revisions
            WHERE strategy_id = ?
            """,
            (strategy_id,),
        ).fetchone()
        count = self._count_from_row(aggregate)
        if head is None and count == 0:
            return None

        latest = head[0] if head is not None else None
        minimum = aggregate[1] if aggregate is not None else None
        maximum = aggregate[2] if aggregate is not None else None
        if (
            not isinstance(latest, int)
            or isinstance(latest, bool)
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum != 1
            or maximum != latest
            or count != latest
        ):
            raise StrategyRepositoryStorageError(
                "stored strategy revision chain failed integrity validation -- "
                f"strategy_id={strategy_id} head={latest!r} count={count} "
                f"min={minimum!r} max={maximum!r}"
            )
        return latest

    @staticmethod
    def _count_from_row(row: sqlite3.Row | None) -> int:
        if row is None or not isinstance(row[0], int):  # pragma: no cover - COUNT invariant
            raise StrategyRepositoryStorageError("SQLite count query returned an invalid value")
        return row[0]

    def _ensure_open(self) -> None:
        if self._closed:
            raise StrategyRepositoryStorageError("SQLite strategy repository is closed")

    def __del__(self) -> None:  # pragma: no cover - defensive interpreter cleanup
        with suppress(Exception):
            self.close()
