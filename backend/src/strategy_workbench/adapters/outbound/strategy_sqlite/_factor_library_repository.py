from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.factor_library.facade.ports import (
    LibraryFactorKeyConflictError,
    LibraryFactorNotFoundError,
    LibraryFactorVersionConflictError,
)
from strategy_workbench.domain.factor_library.facade.library import (
    JsonObject,
    LibraryFactor,
    LibraryFactorVersion,
)

from ._errors import StrategyRepositoryStorageError
from ._schema import migrate_schema
from ._values import optional_text, required_int, required_text


class SQLiteFactorLibraryRepository:
    """전략 저장소 파일 안의 사용자 팩터 라이브러리.

    팩터 항목·파라미터 정의는 받은 JSON 트리 그대로 저장하고 해석하지 않는다(검증·신원은
    application 이 compile 로 정한다). 버전 행은 트리거가 수정·삭제를 막고, 새 버전은 머리의
    `latest_version` CAS 로 더한다.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        self._database = SqliteDatabase(
            path,
            label="factor library repository",
            error=StrategyRepositoryStorageError,
            prepare=migrate_schema,
            busy_timeout_seconds=busy_timeout_seconds,
        )

    @property
    def database_path(self) -> Path | None:
        return self._database.database_path

    def create(self, entry: LibraryFactor) -> LibraryFactor:
        if len(entry.versions) != 1:
            raise StrategyRepositoryStorageError(
                "a new library factor must carry exactly its first version -- "
                f"library_id={entry.library_id} versions={len(entry.versions)}"
            )
        with self._database.transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT library_id FROM factor_library WHERE factor_key = ?", (entry.key,)
            ).fetchone()
            if existing is not None:
                raise LibraryFactorKeyConflictError(
                    f"library factor key already exists -- key={entry.key} "
                    f"library_id={existing['library_id']}",
                    existing_library_id=str(existing["library_id"]),
                )
            connection.execute(
                """
                INSERT INTO factor_library (
                    library_id, factor_key, name, description, hidden, latest_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.library_id,
                    entry.key,
                    entry.name,
                    entry.description,
                    int(entry.hidden),
                    1,
                    datetime_text(entry.created_at, field="library created_at"),
                ),
            )
            self._insert_version(connection, entry.library_id, entry.versions[0])
            return self._load(connection, entry.library_id)

    def append_version(
        self, library_id: str, version: LibraryFactorVersion, *, expected_latest: int
    ) -> LibraryFactor:
        if version.version != expected_latest + 1:
            raise StrategyRepositoryStorageError(
                "library version is not the expected successor -- "
                f"library_id={library_id} expected_latest={expected_latest} "
                f"candidate={version.version}"
            )
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                """
                UPDATE factor_library SET latest_version = ?
                WHERE library_id = ? AND latest_version = ?
                """,
                (version.version, library_id, expected_latest),
            )
            if updated.rowcount != 1:
                current = self._head(connection, library_id)
                raise LibraryFactorVersionConflictError(
                    "library factor version conflict -- "
                    f"library_id={library_id} expected_latest={expected_latest} "
                    f"current={current}",
                    current_version=current,
                )
            self._insert_version(connection, library_id, version)
            return self._load(connection, library_id)

    def get(self, library_id: str) -> LibraryFactor:
        with self._database.transaction(write=False) as connection:
            return self._load(connection, library_id)

    def list(self) -> tuple[LibraryFactor, ...]:
        with self._database.transaction(write=False) as connection:
            ids = [
                str(row["library_id"])
                for row in connection.execute(
                    "SELECT library_id FROM factor_library ORDER BY created_at, library_id"
                )
            ]
            return tuple(self._load(connection, library_id) for library_id in ids)

    def set_hidden(self, library_id: str, hidden: bool) -> LibraryFactor:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                "UPDATE factor_library SET hidden = ? WHERE library_id = ?",
                (int(hidden), library_id),
            )
            if updated.rowcount != 1:
                raise LibraryFactorNotFoundError(
                    f"library factor not found -- library_id={library_id}"
                )
            return self._load(connection, library_id)

    def close(self) -> None:
        self._database.close()

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection, library_id: str, version: LibraryFactorVersion
    ) -> None:
        connection.execute(
            """
            INSERT INTO factor_library_versions (
                library_id, version, identity, factor_json, parameters_json, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                library_id,
                version.version,
                version.identity,
                json.dumps(version.factor, ensure_ascii=False, sort_keys=True),
                json.dumps(list(version.parameters), ensure_ascii=False, sort_keys=True),
                version.note,
                datetime_text(version.created_at, field="library version created_at"),
            ),
        )

    @staticmethod
    def _head(connection: sqlite3.Connection, library_id: str) -> int:
        row = connection.execute(
            "SELECT latest_version FROM factor_library WHERE library_id = ?", (library_id,)
        ).fetchone()
        if row is None:
            raise LibraryFactorNotFoundError(f"library factor not found -- library_id={library_id}")
        return required_int(row, "latest_version")

    @staticmethod
    def _load(connection: sqlite3.Connection, library_id: str) -> LibraryFactor:
        head = connection.execute(
            "SELECT * FROM factor_library WHERE library_id = ?", (library_id,)
        ).fetchone()
        if head is None:
            raise LibraryFactorNotFoundError(f"library factor not found -- library_id={library_id}")
        rows = connection.execute(
            "SELECT * FROM factor_library_versions WHERE library_id = ? ORDER BY version",
            (library_id,),
        ).fetchall()
        try:
            versions = tuple(
                LibraryFactorVersion(
                    version=required_int(row, "version"),
                    identity=required_text(row, "identity"),
                    factor=_object(json.loads(required_text(row, "factor_json"))),
                    parameters=tuple(
                        _object(item)
                        for item in _list(json.loads(required_text(row, "parameters_json")))
                    ),
                    created_at=datetime.fromisoformat(required_text(row, "created_at")),
                    note=optional_text(row, "note"),
                )
                for row in rows
            )
            entry = LibraryFactor(
                library_id=required_text(head, "library_id"),
                key=required_text(head, "factor_key"),
                name=required_text(head, "name"),
                description=required_text(head, "description"),
                hidden=bool(required_int(head, "hidden")),
                created_at=datetime.fromisoformat(required_text(head, "created_at")),
                versions=versions,
            )
            if entry.latest.version != required_int(head, "latest_version"):
                raise ValueError(
                    "latest_version does not match the stored versions -- "
                    f"head={head['latest_version']} rows={len(versions)}"
                )
            return entry
        except (TypeError, ValueError) as error:
            raise StrategyRepositoryStorageError(
                "stored library factor failed integrity validation -- "
                f"library_id={library_id}: {error}"
            ) from error


def _object(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]  # reason: json.loads 결과는 타입 없는 트리다


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"expected a JSON array, got {type(value).__name__}")
    return list(value)  # pyright: ignore[reportUnknownArgumentType]  # reason: json.loads 결과는 타입 없는 트리다
