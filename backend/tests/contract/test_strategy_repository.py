"""P1-06 strategy repository contract: immutable revision envelopes with source and provenance.

Every adapter behind `StrategyRepositoryPort` runs this suite. The in-memory reference and
SQLite production adapters must preserve identical observable semantics.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.adapters.outbound.strategy_sqlite import _schema as sqlite_schema
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyRepository,
    StrategyRepositoryStorageError,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import (
    PageRequest,
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyNotFoundError,
    StrategyRepositoryPort,
    StrategyRevisionConflictError,
    StrategyRevisionRecord,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.strategy.facade.document import SourceFormat, source_hash_of
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
GOLDEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"
T0 = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)

RepositoryFactory = Callable[[], StrategyRepositoryPort]


@pytest.fixture(
    params=("memory", "sqlite"),
    ids=("InMemoryStrategyRepository", "SQLiteStrategyRepository"),
)
def factory(request: pytest.FixtureRequest, tmp_path: Path) -> RepositoryFactory:
    if request.param == "memory":
        return InMemoryStrategyRepository
    sequence = iter(range(1_000_000))

    def create_sqlite_repository() -> SQLiteStrategyRepository:
        return _sqlite(tmp_path / f"strategy-{next(sequence)}.sqlite3")

    return create_sqlite_repository


def _authoring() -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )


def _source_spec_hash(source: str, format: SourceFormat) -> str:
    compiled = _authoring().compile(CompileRequest(source, format))
    if compiled.spec_hash is None:
        raise ValueError("test source does not compile")
    return compiled.spec_hash


def _sqlite(path: Path) -> SQLiteStrategyRepository:
    return SQLiteStrategyRepository(path, source_spec_hash=_source_spec_hash)


def _template() -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: date(2026, 9, 3)
    ).template()


def _legacy(spec: StrategySpec, strategy_id: str, revision: int, at: datetime = T0):
    saved = replace(spec, identity=StrategyIdentity(strategy_id, revision))
    return StrategyRevisionRecord(
        spec=saved,
        spec_hash=strategy_spec_hash(saved),
        source=None,
        provenance=RevisionProvenance(RevisionOrigin.LEGACY_JSON, at),
    )


def _document(
    text: str,
    strategy_id: str,
    revision: int,
    at: datetime = T0,
    change_note: str | None = None,
    *,
    format: SourceFormat = SourceFormat.YAML,
):
    compiled = _authoring().compile(CompileRequest(text, format))
    assert compiled.ok and compiled.spec is not None and compiled.spec_hash is not None
    saved = replace(compiled.spec, identity=StrategyIdentity(strategy_id, revision))
    return StrategyRevisionRecord(
        spec=saved,
        spec_hash=compiled.spec_hash,
        source=RevisionSource(format, text, compiled.source_hash),
        provenance=RevisionProvenance(RevisionOrigin.DOCUMENT, at, change_note=change_note),
    )


def _document_source(format: SourceFormat, *, title: str) -> str:
    yaml_source = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    if format is SourceFormat.YAML:
        return yaml_source.replace('title: "퀄리티 모멘텀"', f'title: "{title}"')
    compiled = _authoring().compile(CompileRequest(yaml_source, SourceFormat.YAML))
    assert compiled.spec is not None
    return canonical_strategy_json(replace(compiled.spec, title=title), indent=2)


def _drop_revision_guards(connection: sqlite3.Connection) -> None:
    """Simulate storage damage after startup; production connections cannot mutate revisions."""
    connection.execute("DROP TRIGGER strategy_revisions_immutable_update")
    connection.execute("DROP TRIGGER strategy_revisions_immutable_delete")


def _revision_guard_sql(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT sql
        FROM sqlite_schema
        WHERE type = 'trigger' AND name GLOB 'strategy_revisions_immutable_*'
        ORDER BY name COLLATE BINARY
        """
    ).fetchall()
    statements = tuple(row[0] for row in rows if isinstance(row[0], str))
    assert len(statements) == 2
    return statements


def test_revision_source_is_immutable_after_the_next_revision(factory: RepositoryFactory) -> None:
    repository = factory()
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    first = _document(text, "s1", 1)
    repository.add(first)
    repository.append(
        _document(text.replace('"퀄리티 모멘텀"', '"v2"'), "s1", 2), expected_revision=1
    )

    stored = repository.get("s1", 1)
    assert stored == first
    assert stored.source is not None and stored.source.text == text
    assert repository.get("s1").revision == 2
    assert repository.get("s1").spec.title == "v2"


def test_compiling_the_stored_source_reproduces_the_record_hashes(
    factory: RepositoryFactory,
) -> None:
    repository = factory()
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    repository.add(_document(text, "s1", 1))

    stored = repository.get("s1", 1)
    assert stored.source is not None
    recompiled = _authoring().compile(CompileRequest(stored.source.text, stored.source.format))

    assert recompiled.spec_hash == stored.spec_hash == GOLDEN_SPEC_HASH
    assert recompiled.source_hash == stored.source.source_hash == source_hash_of(text)


@pytest.mark.parametrize("format", (SourceFormat.YAML, SourceFormat.JSON))
@pytest.mark.parametrize("read_path", ("get", "list", "history"))
def test_sqlite_rejects_source_that_no_longer_compiles_to_the_stored_spec(
    tmp_path: Path, format: SourceFormat, read_path: str
) -> None:
    path = tmp_path / f"source-drift-{format.value}-{read_path}.sqlite3"
    repository = _sqlite(path)
    original = _document_source(format, title="original")
    changed = _document_source(format, title="changed")
    repository.add(_document(original, "drift", 1, format=format))

    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        connection.execute(
            """
            UPDATE strategy_revisions
            SET source_text = ?, source_hash = ?
            WHERE strategy_id = ? AND revision = 1
            """,
            (changed, source_hash_of(changed), "drift"),
        )

    with pytest.raises(StrategyRepositoryStorageError, match="compiles to a different"):
        if read_path == "get":
            repository.get("drift")
        elif read_path == "list":
            repository.list_strategies(PageRequest())
        else:
            repository.history("drift", PageRequest())


def test_sqlite_rejects_a_mismatched_document_envelope_before_writing(tmp_path: Path) -> None:
    repository = _sqlite(tmp_path / "write-drift.sqlite3")
    original = _document_source(SourceFormat.YAML, title="original")
    changed = _document_source(SourceFormat.YAML, title="changed")
    record = _document(original, "drift", 1)
    mismatched = replace(
        record,
        source=RevisionSource(SourceFormat.YAML, changed, source_hash_of(changed)),
    )

    with pytest.raises(StrategyRepositoryStorageError, match="source and spec disagree"):
        repository.add(mismatched)
    assert repository.list_strategies(PageRequest()).total == 0


def test_legacy_revisions_have_no_source_and_keep_the_json_flow(
    factory: RepositoryFactory,
) -> None:
    repository = factory()
    service = StrategyDesignService(
        repository, new_id=lambda: "legacy-1", today=lambda: date(2026, 9, 3), now=lambda: T0
    )
    created = service.create(service.template())
    revised = service.revise("legacy-1", replace(created.spec, title="수정"), expected_revision=1)

    assert revised.spec.identity.revision == 2
    record = repository.get("legacy-1", 2)
    assert record.source is None
    assert record.provenance == RevisionProvenance(RevisionOrigin.LEGACY_JSON, T0)
    assert record.spec_hash == revised.spec_hash == strategy_spec_hash(record.spec)
    assert service.get("legacy-1", 1).spec.title == "새 팩터 전략"


def test_stale_expected_revision_conflicts(factory: RepositoryFactory) -> None:
    repository = factory()
    template = _template()
    repository.add(_legacy(template, "s1", 1))

    with pytest.raises(StrategyRevisionConflictError, match="expected=0 actual=1") as stale:
        repository.append(_legacy(template, "s1", 2), expected_revision=0)
    assert stale.value.latest_revision == 1
    with pytest.raises(StrategyRevisionConflictError, match="not monotonic") as monotonic:
        repository.append(_legacy(template, "s1", 3), expected_revision=1)
    assert monotonic.value.latest_revision == 1
    with pytest.raises(StrategyRevisionConflictError, match="already exists") as exists:
        repository.add(_legacy(template, "s1", 1))
    assert exists.value.latest_revision == 1
    with pytest.raises(StrategyRevisionConflictError, match="first revision must be 1") as first:
        repository.add(_legacy(template, "s2", 2))
    assert first.value.latest_revision is None


def test_missing_strategy_and_revision_are_not_found(factory: RepositoryFactory) -> None:
    repository = factory()
    with pytest.raises(StrategyNotFoundError, match="strategy not found"):
        repository.get("missing")
    repository.add(_legacy(_template(), "s1", 1))
    with pytest.raises(StrategyNotFoundError, match="revision not found"):
        repository.get("s1", 2)
    with pytest.raises(StrategyNotFoundError, match="strategy not found"):
        repository.history("missing", PageRequest())


def test_list_and_history_are_deterministic_and_paginated(factory: RepositoryFactory) -> None:
    repository = factory()
    template = _template()
    for index, strategy_id in enumerate(["s3", "s1", "s2"]):
        repository.add(_legacy(replace(template, title=f"t-{strategy_id}"), strategy_id, 1))
        for revision in range(2, 2 + index):
            repository.append(
                _legacy(
                    replace(template, title=f"t-{strategy_id}"),
                    strategy_id,
                    revision,
                    at=T0.replace(minute=revision),
                ),
                expected_revision=revision - 1,
            )

    first = repository.list_strategies(PageRequest(offset=0, limit=2))
    second = repository.list_strategies(PageRequest(offset=2, limit=2))
    assert [s.strategy_id for s in first.items] == ["s1", "s2"]
    assert [s.strategy_id for s in second.items] == ["s3"]
    assert (first.total, second.total, first.offset, first.limit) == (3, 3, 0, 2)
    assert first.items[0].latest_revision == 2 and first.items[1].latest_revision == 3
    assert first.items[0].title == "t-s1"
    assert repository.list_strategies(PageRequest(offset=9, limit=5)).items == ()

    history = repository.history("s2", PageRequest(offset=1, limit=5))
    assert [r.revision for r in history.items] == [2, 3]
    assert history.total == 3
    assert history.items[0].origin is RevisionOrigin.LEGACY_JSON
    assert history.items[0].source_hash is None and history.items[0].source_format is None
    assert repository.list_strategies(PageRequest()) == repository.list_strategies(PageRequest())


def test_record_invariants_fail_closed() -> None:
    template = replace(_template(), identity=StrategyIdentity("s1", 1))
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    provenance = RevisionProvenance(RevisionOrigin.DOCUMENT, T0)

    with pytest.raises(ValueError, match="spec_hash does not match"):
        StrategyRevisionRecord(template, "0" * 64, None, provenance)
    with pytest.raises(ValueError, match="source hash does not match"):
        RevisionSource(SourceFormat.YAML, text, "0" * 64)
    with pytest.raises(ValueError, match="document provenance requires the source"):
        StrategyRevisionRecord(template, strategy_spec_hash(template), None, provenance)
    with pytest.raises(ValueError, match="must have document provenance"):
        StrategyRevisionRecord(
            template,
            strategy_spec_hash(template),
            RevisionSource(SourceFormat.YAML, text, source_hash_of(text)),
            RevisionProvenance(RevisionOrigin.LEGACY_JSON, T0),
        )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        RevisionProvenance(RevisionOrigin.LEGACY_JSON, datetime(2026, 9, 4))
    with pytest.raises(ValueError, match="page request out of range"):
        PageRequest(limit=0)


def test_history_exposes_document_source_provenance(
    factory: RepositoryFactory,
) -> None:
    repository = factory()
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    repository.add(_document(text, "d1", 1, change_note="initial import"))
    repository.append(_legacy(_template(), "d1", 2), expected_revision=1)

    first, second = repository.history("d1", PageRequest()).items
    assert first.origin is RevisionOrigin.DOCUMENT
    assert first.change_note == "initial import" and second.change_note is None
    assert first.source_format is SourceFormat.YAML
    assert first.source_hash == source_hash_of(text)
    assert first.spec_hash == GOLDEN_SPEC_HASH
    assert second.origin is RevisionOrigin.LEGACY_JSON and second.source_hash is None


def test_strategy_summary_is_derived_from_the_latest_revision(
    factory: RepositoryFactory,
) -> None:
    repository = factory()
    template = _template()
    repository.add(_legacy(replace(template, title="t-s1-r1"), "s1", 1, at=T0.replace(minute=1)))
    repository.append(
        _legacy(replace(template, title="t-s1-r2"), "s1", 2, at=T0.replace(minute=2)),
        expected_revision=1,
    )

    (summary,) = repository.list_strategies(PageRequest()).items
    assert summary.title == "t-s1-r2"
    assert summary.latest_revision == 2
    assert summary.updated_at == T0.replace(minute=2)
    assert summary.spec_hash == repository.get("s1").spec_hash != repository.get("s1", 1).spec_hash


def test_revision_source_rejects_empty_text_and_provenance_carries_a_change_note() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        RevisionSource(SourceFormat.YAML, "   \n", source_hash_of("   \n"))
    provenance = RevisionProvenance(RevisionOrigin.LEGACY_JSON, T0, change_note="tighter cap")
    assert provenance.change_note == "tighter cap"
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        RevisionProvenance(RevisionOrigin.LEGACY_JSON, T0.astimezone(timezone(timedelta(hours=9))))


def test_sqlite_restores_exact_revision_envelopes_after_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "strategies.sqlite3"
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8").replace("\n", "\r\n")
    first = _document(text, "durable", 1, change_note="exact source")
    second = _legacy(replace(_template(), title="second"), "durable", 2, at=T0.replace(second=1))

    initial = _sqlite(path)
    initial.add(first)
    initial.append(second, expected_revision=1)
    initial.close()

    restarted = _sqlite(path)
    assert restarted.get("durable", 1) == first
    assert restarted.get("durable") == second
    assert restarted.history("durable", PageRequest()).total == 2
    assert restarted.database_path == path.resolve()


def test_sqlite_two_instances_serialize_competing_revision_appends(tmp_path: Path) -> None:
    path = tmp_path / "strategies.sqlite3"
    first = _sqlite(path)
    second = _sqlite(path)
    template = _template()
    first.add(_legacy(template, "race", 1))
    barrier = Barrier(2)

    def append(repository: SQLiteStrategyRepository, title: str) -> tuple[str, int | None]:
        record = _legacy(replace(template, title=title), "race", 2)
        barrier.wait(timeout=5)
        try:
            repository.append(record, expected_revision=1)
        except StrategyRevisionConflictError as error:
            return ("conflict", error.latest_revision)
        return ("stored", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            future.result(timeout=10)
            for future in (
                executor.submit(append, first, "writer-a"),
                executor.submit(append, second, "writer-b"),
            )
        )

    assert sorted(outcomes) == [("conflict", 2), ("stored", None)]
    assert first.get("race").revision == 2
    assert first.get("race").spec.title in {"writer-a", "writer-b"}


def test_sqlite_two_instances_serialize_competing_strategy_creates(tmp_path: Path) -> None:
    path = tmp_path / "strategies.sqlite3"
    first = _sqlite(path)
    second = _sqlite(path)
    template = _template()
    barrier = Barrier(2)

    def add(repository: SQLiteStrategyRepository, title: str) -> tuple[str, int | None]:
        record = _legacy(replace(template, title=title), "race", 1)
        barrier.wait(timeout=5)
        try:
            repository.add(record)
        except StrategyRevisionConflictError as error:
            return ("conflict", error.latest_revision)
        return ("stored", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            future.result(timeout=10)
            for future in (
                executor.submit(add, first, "writer-a"),
                executor.submit(add, second, "writer-b"),
            )
        )

    assert sorted(outcomes) == [("conflict", 1), ("stored", None)]
    assert first.get("race").spec.title in {"writer-a", "writer-b"}


def test_sqlite_serializes_concurrent_first_time_schema_initialization(tmp_path: Path) -> None:
    path = tmp_path / "new" / "strategies.sqlite3"
    barrier = Barrier(2)

    def initialize() -> int:
        barrier.wait(timeout=5)
        repository = _sqlite(path)
        version = repository.SCHEMA_VERSION
        repository.close()
        return version

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = tuple(
            future.result(timeout=10)
            for future in (executor.submit(initialize), executor.submit(initialize))
        )

    assert versions == (SQLiteStrategyRepository.SCHEMA_VERSION,) * 2
    assert _sqlite(path).list_strategies(PageRequest()).total == 0


@pytest.mark.parametrize("damage", ("first_moved", "middle_deleted", "latest_deleted"))
@pytest.mark.parametrize("operation", ("get", "list", "history", "append"))
def test_sqlite_rejects_a_damaged_revision_chain_before_every_materialization_or_append(
    tmp_path: Path, damage: str, operation: str
) -> None:
    path = tmp_path / f"chain-{damage}-{operation}.sqlite3"
    repository = _sqlite(path)
    template = _template()
    for revision in range(1, 4):
        record = _legacy(replace(template, title=f"revision-{revision}"), "damaged", revision)
        if revision == 1:
            repository.add(record)
        else:
            repository.append(record, expected_revision=revision - 1)

    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        if damage == "first_moved":
            connection.execute(
                "UPDATE strategy_revisions SET revision = 5 "
                "WHERE strategy_id = 'damaged' AND revision = 1"
            )
        elif damage == "middle_deleted":
            connection.execute(
                "DELETE FROM strategy_revisions WHERE strategy_id = 'damaged' AND revision = 2"
            )
        else:
            connection.execute(
                "DELETE FROM strategy_revisions WHERE strategy_id = 'damaged' AND revision = 3"
            )

    with pytest.raises(StrategyRepositoryStorageError, match="revision chain"):
        if operation == "get":
            repository.get("damaged")
        elif operation == "list":
            repository.list_strategies(PageRequest())
        elif operation == "history":
            repository.history("damaged", PageRequest())
        else:
            repository.append(_legacy(template, "damaged", 4), expected_revision=3)


@pytest.mark.parametrize("operation", ("reopen", "get", "list", "history", "append"))
def test_sqlite_rejects_a_real_number_that_replaces_an_integer_revision(
    tmp_path: Path, operation: str
) -> None:
    path = tmp_path / f"real-revision-{operation}.sqlite3"
    repository = _sqlite(path)
    template = _template()
    for revision in range(1, 4):
        record = _legacy(template, "real-revision", revision)
        if revision == 1:
            repository.add(record)
        else:
            repository.append(record, expected_revision=revision - 1)

    with sqlite3.connect(path) as connection:
        guard_sql = _revision_guard_sql(connection)
        _drop_revision_guards(connection)
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE strategy_revisions SET revision = 2.5 "
            "WHERE strategy_id = 'real-revision' AND revision = 2"
        )
        for statement in guard_sql:
            connection.execute(statement)
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        assert connection.execute(
            "SELECT typeof(revision) FROM strategy_revisions WHERE revision = 2.5"
        ).fetchone() == ("real",)

    with pytest.raises(StrategyRepositoryStorageError, match="non_integer=1"):
        if operation == "reopen":
            repository.close()
            _sqlite(path)
        elif operation == "get":
            repository.get("real-revision")
        elif operation == "list":
            repository.list_strategies(PageRequest())
        elif operation == "history":
            repository.history("real-revision", PageRequest())
        else:
            repository.append(_legacy(template, "real-revision", 4), expected_revision=3)


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE strategy_revisions SET revision = 1.5 WHERE strategy_id = 'typed'",
        "UPDATE strategy_heads SET latest_revision = 1.5 WHERE strategy_id = 'typed'",
    ),
)
def test_sqlite_schema_requires_integer_storage_for_revision_counters(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "typed-revision.sqlite3"
    repository = _sqlite(path)
    repository.add(_legacy(_template(), "typed", 1))

    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(statement)


def test_sqlite_schema_migration_is_idempotent_and_rejects_future_versions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    _sqlite(path).close()
    _sqlite(path).close()

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(strategy_revisions)").fetchall()
        }
    assert version == SQLiteStrategyRepository.SCHEMA_VERSION
    assert {"spec_json", "spec_hash", "source_text", "source_hash", "created_at"} <= columns

    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SQLiteStrategyRepository.SCHEMA_VERSION + 1}")
    with pytest.raises(StrategyRepositoryStorageError, match="newer than this server"):
        _sqlite(path)


def test_sqlite_refuses_to_claim_an_unowned_nonempty_database(tmp_path: Path) -> None:
    path = tmp_path / "unowned.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE someone_elses_data (value TEXT)")

    with pytest.raises(StrategyRepositoryStorageError, match="refusing to claim"):
        _sqlite(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'someone_elses_data'"
        ).fetchone() == ("someone_elses_data",)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = 'strategy_heads'"
            ).fetchone()
            is None
        )


def test_sqlite_rejects_an_owned_schema_with_the_wrong_shape(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite3"
    _sqlite(path).close()
    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        connection.execute("DROP TABLE strategy_revisions")
        connection.execute("CREATE TABLE strategy_revisions (strategy_id TEXT)")

    with pytest.raises(StrategyRepositoryStorageError, match="schema does not match"):
        _sqlite(path)


def test_sqlite_rejects_matching_columns_when_constraints_or_storage_shape_differ(
    tmp_path: Path,
) -> None:
    path = tmp_path / "weakened.sqlite3"
    _sqlite(path).close()
    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        connection.execute("DROP TABLE strategy_revisions")
        connection.execute(
            """
            CREATE TABLE strategy_revisions (
                strategy_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                schema_version TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                spec_hash TEXT NOT NULL,
                source_format TEXT,
                source_text TEXT,
                source_hash TEXT,
                origin TEXT NOT NULL,
                created_at TEXT NOT NULL,
                change_note TEXT,
                PRIMARY KEY (strategy_id, revision)
            )
            """
        )

    with pytest.raises(StrategyRepositoryStorageError, match="schema does not match"):
        _sqlite(path)


@pytest.mark.parametrize(
    ("object_name", "original", "replacement"),
    (
        ("strategy_revisions", "('yaml', 'json')", "('YAML', 'JSON')"),
        (
            "strategy_revisions_immutable_update",
            "strategy revisions are immutable",
            "strategy  revisions are immutable",
        ),
    ),
)
def test_sqlite_manifest_preserves_quoted_literal_case_and_whitespace(
    tmp_path: Path, object_name: str, original: str, replacement: str
) -> None:
    path = tmp_path / f"literal-{object_name}.sqlite3"
    _sqlite(path).close()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = ?", (object_name,)
        ).fetchone()
        assert row is not None and isinstance(row[0], str) and original in row[0]
        changed_sql = row[0].replace(original, replacement)
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE name = ?",
            (changed_sql, object_name),
        )
        connection.execute("PRAGMA writable_schema = OFF")

    with pytest.raises(StrategyRepositoryStorageError, match="schema does not match"):
        _sqlite(path)


def test_sqlite_rejects_unexpected_schema_objects(tmp_path: Path) -> None:
    path = tmp_path / "extra-object.sqlite3"
    _sqlite(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unexpected_data (value TEXT)")

    with pytest.raises(StrategyRepositoryStorageError, match="unexpected"):
        _sqlite(path)


def test_sqlite_schema_migration_rolls_back_headers_and_objects_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "migration-failure.sqlite3"
    broken_objects = sqlite_schema._V1_SCHEMA_OBJECTS + (
        ("table", "broken", "CREATE TABL broken (value TEXT)"),
    )
    monkeypatch.setattr(sqlite_schema, "_V1_SCHEMA_OBJECTS", broken_objects)

    with pytest.raises(StrategyRepositoryStorageError, match="could not initialise"):
        _sqlite(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
            ).fetchall()
            == []
        )


def test_sqlite_rejects_a_database_owned_by_another_application(tmp_path: Path) -> None:
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id = 1234")

    with pytest.raises(StrategyRepositoryStorageError, match="another application"):
        _sqlite(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 1234
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_sqlite_revisions_are_physically_immutable(tmp_path: Path) -> None:
    path = tmp_path / "immutable.sqlite3"
    repository = _sqlite(path)
    repository.add(_legacy(_template(), "immutable", 1))

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE strategy_revisions SET change_note = 'changed' "
                "WHERE strategy_id = 'immutable'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM strategy_revisions WHERE strategy_id = 'immutable'")

    assert repository.get("immutable").revision == 1


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        ("spec_hash", "0" * 64, "spec_hash does not match"),
        ("spec_json", "{}", "schema_version column does not match"),
        ("source_hash", "0" * 64, "source hash does not match"),
    ),
)
def test_sqlite_fails_closed_when_persisted_revision_is_corrupted(
    tmp_path: Path, column: str, value: str, message: str
) -> None:
    path = tmp_path / "strategies.sqlite3"
    repository = _sqlite(path)
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    repository.add(_document(text, "corrupt", 1))

    assert column in {"spec_hash", "spec_json", "source_hash"}
    with sqlite3.connect(path) as connection:
        _drop_revision_guards(connection)
        connection.execute(
            f"UPDATE strategy_revisions SET {column} = ? WHERE strategy_id = ?",
            (value, "corrupt"),
        )

    with pytest.raises(StrategyRepositoryStorageError, match=message):
        repository.get("corrupt")


def test_bootstrap_uses_sqlite_and_restores_a_strategy_with_an_explicit_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    first = build_container(strategy_repository_path=path)
    assert isinstance(first.strategy_repository, SQLiteStrategyRepository)
    created = first.strategy_design.create(first.strategy_design.template())

    restarted = build_container(strategy_repository_path=path)
    restored = restarted.strategy_design.get(created.spec.identity.strategy_id)

    assert restored == created
    isolated = build_container()
    assert isinstance(isolated.strategy_repository, SQLiteStrategyRepository)
    assert isolated.strategy_repository.database_path is None
    isolated.strategy_repository.close()
