from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from strategy_workbench.adapters.outbound.strategy_sqlite import _schema as sqlite_schema
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyDraftRepository,
    SQLiteStrategyRepository,
    StrategyRepositoryStorageError,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    InvalidStrategyDraftError,
    SaveStrategyDraftRequest,
    StrategyDraft,
    StrategyDraftService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    SourceFormat,
    StrategyDraftConflictError,
    StrategyDraftNotFoundError,
    source_hash_of,
)
from strategy_workbench.bootstrap._container import build_container

NOW = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)


def _draft(
    draft_id: str = "draft-one",
    *,
    version: int = 1,
    source: str = "title: [invalid\r\n",
    updated_at: datetime = NOW,
) -> StrategyDraft:
    return StrategyDraft(
        draft_id=draft_id,
        version=version,
        source=source,
        format=SourceFormat.YAML,
        source_hash=source_hash_of(source),
        schema_version="1.0",
        updated_at=updated_at,
    )


def test_sqlite_draft_round_trips_invalid_exact_source_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "drafts.sqlite3"
    first = SQLiteStrategyDraftRepository(path)
    saved = first.save(_draft(), expected_version=0)
    first.close()

    second = SQLiteStrategyDraftRepository(path)
    restored = second.get("draft-one")

    assert restored == saved
    assert restored.source == "title: [invalid\r\n"
    assert restored.source_hash == source_hash_of(restored.source)


def test_sqlite_draft_create_update_and_delete_are_versioned_cas(tmp_path: Path) -> None:
    repository = SQLiteStrategyDraftRepository(tmp_path / "cas.sqlite3")
    first = repository.save(_draft(), expected_version=0)
    second = repository.save(
        _draft(version=2, source="still: invalid: yaml", updated_at=NOW + timedelta(seconds=1)),
        expected_version=1,
    )

    with pytest.raises(StrategyDraftConflictError) as stale_write:
        repository.save(_draft(version=2, source="stale"), expected_version=1)
    assert stale_write.value.current == second

    with pytest.raises(StrategyDraftConflictError) as stale_delete:
        repository.delete("draft-one", expected_version=first.version)
    assert stale_delete.value.current == second

    repository.delete("draft-one", expected_version=second.version)
    with pytest.raises(StrategyDraftNotFoundError):
        repository.get("draft-one")


def test_two_sqlite_draft_instances_allow_exactly_one_cas_winner(tmp_path: Path) -> None:
    path = tmp_path / "draft-race.sqlite3"
    first = SQLiteStrategyDraftRepository(path)
    second = SQLiteStrategyDraftRepository(path)
    first.save(_draft(), expected_version=0)
    barrier = Barrier(2)

    def update(repository: SQLiteStrategyDraftRepository, source: str) -> str:
        barrier.wait(timeout=5)
        try:
            repository.save(
                _draft(version=2, source=source, updated_at=NOW + timedelta(seconds=1)),
                expected_version=1,
            )
        except StrategyDraftConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            future.result(timeout=10)
            for future in (
                pool.submit(update, first, "device: one"),
                pool.submit(update, second, "device: two"),
            )
        )

    assert sorted(outcomes) == ["conflict", "saved"]
    assert first.get("draft-one").source in {"device: one", "device: two"}


def test_draft_service_validates_saved_base_but_never_compiles_source(tmp_path: Path) -> None:
    path = tmp_path / "service.sqlite3"
    container = build_container(strategy_repository_path=path)
    saved = container.strategy_design.create(container.strategy_design.template())
    identity = saved.spec.identity
    invalid_source = "factors: [definitely: invalid"

    draft = container.strategy_drafts.save(
        "saved-base",
        SaveStrategyDraftRequest(
            expected_version=0,
            source=invalid_source,
            format=SourceFormat.YAML,
            schema_version=identity.schema_version,
            strategy_id=identity.strategy_id,
            base_revision=identity.revision,
            base_spec_hash=saved.spec_hash,
        ),
    )

    assert draft.source == invalid_source
    with pytest.raises(InvalidStrategyDraftError, match="base hash"):
        container.strategy_drafts.save(
            "wrong-base",
            SaveStrategyDraftRequest(
                expected_version=0,
                source="",
                format=SourceFormat.YAML,
                schema_version=identity.schema_version,
                strategy_id=identity.strategy_id,
                base_revision=identity.revision,
                base_spec_hash="0" * 64,
            ),
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE strategy_drafts SET base_spec_hash = ? WHERE draft_id = ?",
            ("0" * 64, "saved-base"),
        )
    with pytest.raises(InvalidStrategyDraftError, match="base hash"):
        container.strategy_drafts.get("saved-base")


def test_draft_service_rejects_a_naive_clock(tmp_path: Path) -> None:
    path = tmp_path / "naive-clock.sqlite3"
    service = StrategyDraftService(
        SQLiteStrategyDraftRepository(path),
        SQLiteStrategyRepository(path, source_spec_hash=lambda _source, _format: "0" * 64),
        now=lambda: datetime(2026, 9, 5),
    )

    with pytest.raises(InvalidStrategyDraftError, match="timezone-aware"):
        service.save(
            "naive-clock",
            SaveStrategyDraftRequest(0, "", SourceFormat.YAML, "1.0"),
        )


def test_draft_service_reuses_authoring_source_size_limit(tmp_path: Path) -> None:
    strategy_repository = SQLiteStrategyRepository(
        tmp_path / "limit.sqlite3", source_spec_hash=lambda _source, _format: "0" * 64
    )
    service = StrategyDraftService(
        SQLiteStrategyDraftRepository(tmp_path / "limit.sqlite3"),
        strategy_repository,
        max_source_bytes=4,
    )

    with pytest.raises(InvalidStrategyDraftError, match="max_bytes=4"):
        service.save(
            "oversized",
            SaveStrategyDraftRequest(0, "한글", SourceFormat.YAML, "1.0"),
        )


def test_sqlite_draft_rejects_corrupt_redundant_source_hash(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.sqlite3"
    repository = SQLiteStrategyDraftRepository(path)
    repository.save(_draft(), expected_version=0)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE strategy_drafts SET source_hash = ? WHERE draft_id = ?",
            ("f" * 64, "draft-one"),
        )

    with pytest.raises(StrategyRepositoryStorageError, match="source_hash"):
        SQLiteStrategyDraftRepository(path).get("draft-one")


def test_schema_v1_is_validated_then_migrated_atomically_to_v2(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _object_type, _name, statement in sqlite_schema._V1_SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA application_id = {sqlite_schema._APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    repository = SQLiteStrategyDraftRepository(path)
    repository.save(_draft(), expected_version=0)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'strategy_drafts'"
        ).fetchone() == ("strategy_drafts",)


def test_schema_v1_mutation_rolls_back_v2_migration(tmp_path: Path) -> None:
    path = tmp_path / "mutated-v1.sqlite3"
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _object_type, name, statement in sqlite_schema._V1_SCHEMA_OBJECTS:
            changed = (
                statement.replace("('yaml', 'json')", "('YAML', 'JSON')")
                if name == "strategy_revisions"
                else statement
            )
            connection.execute(changed)
        connection.execute(f"PRAGMA application_id = {sqlite_schema._APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    with pytest.raises(StrategyRepositoryStorageError, match="version 1"):
        SQLiteStrategyDraftRepository(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = 'strategy_drafts'"
            ).fetchone()
            is None
        )
