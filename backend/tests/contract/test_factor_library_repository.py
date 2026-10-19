"""사용자 팩터 라이브러리 저장소(US-CS-08): v2 파일 이관, 버전 행 불변, 키 유일, CAS."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.strategy_sqlite import _schema as sqlite_schema
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteFactorLibraryRepository,
)
from strategy_workbench.application.factor_library.facade.ports import (
    LibraryFactorKeyConflictError,
    LibraryFactorNotFoundError,
    LibraryFactorVersionConflictError,
)
from strategy_workbench.domain.factor_library.facade.library import (
    LibraryFactor,
    LibraryFactorVersion,
)

_AT = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)


def _version(number: int, identity: str) -> LibraryFactorVersion:
    return LibraryFactorVersion(
        version=number,
        identity=identity * 64,
        factor={"factor_id": "close_mean", "graph": {"nodes": [], "output_node_id": "x"}},
        parameters=({"kind": "integer", "parameter_id": "win", "default": 20},),
        created_at=_AT,
        note=f"v{number}",
    )


def _entry(library_id: str = "lib-1", key: str = "close_mean") -> LibraryFactor:
    return LibraryFactor(
        library_id=library_id,
        key=key,
        name="종가 평균",
        description="설명",
        hidden=False,
        created_at=_AT,
        versions=(_version(1, "a"),),
    )


def test_v2_file_migrates_and_versions_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _type, _name, statement in (
            *sqlite_schema._V1_SCHEMA_OBJECTS,
            *sqlite_schema._V2_ADDED_SCHEMA_OBJECTS,
        ):
            connection.execute(statement)
        connection.execute(f"PRAGMA application_id = {sqlite_schema._APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()

    repository = SQLiteFactorLibraryRepository(path)
    created = repository.create(_entry())
    appended = repository.append_version("lib-1", _version(2, "b"), expected_latest=1)

    assert created == _entry()
    assert [version.version for version in appended.versions] == [1, 2]
    assert SQLiteFactorLibraryRepository(path).get("lib-1") == appended
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_version_rows_cannot_be_rewritten_or_deleted(tmp_path: Path) -> None:
    path = tmp_path / "strategy.sqlite3"
    SQLiteFactorLibraryRepository(path).create(_entry())

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE factor_library_versions SET note = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM factor_library_versions")


def test_key_is_unique_and_appends_are_compare_and_swap(tmp_path: Path) -> None:
    repository = SQLiteFactorLibraryRepository(tmp_path / "strategy.sqlite3")
    repository.create(_entry())

    with pytest.raises(LibraryFactorKeyConflictError) as conflict:
        repository.create(_entry("lib-2"))
    assert conflict.value.existing_library_id == "lib-1"

    repository.append_version("lib-1", _version(2, "b"), expected_latest=1)
    with pytest.raises(LibraryFactorVersionConflictError) as stale:
        repository.append_version("lib-1", _version(2, "c"), expected_latest=1)
    assert stale.value.current_version == 2
    assert [version.identity[0] for version in repository.get("lib-1").versions] == ["a", "b"]


def test_hiding_keeps_versions_and_missing_entries_are_reported(tmp_path: Path) -> None:
    repository = SQLiteFactorLibraryRepository(tmp_path / "strategy.sqlite3")
    repository.create(_entry())

    hidden = repository.set_hidden("lib-1", True)

    assert hidden.hidden is True
    assert hidden.versions == _entry().versions
    with pytest.raises(LibraryFactorNotFoundError):
        repository.get("missing")
    with pytest.raises(LibraryFactorNotFoundError):
        repository.set_hidden("missing", True)
