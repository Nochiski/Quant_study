"""P1-06 strategy repository contract: immutable revision envelopes with source and provenance.

Every adapter behind `StrategyRepositoryPort` runs this suite (today: the in-memory reference
adapter; a persistent adapter parameterises the same tests when it arrives).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
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
from strategy_workbench.domain.strategy.facade.document import SourceFormat, source_hash_of
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
GOLDEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"
T0 = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)

REPOSITORIES: list[Callable[[], StrategyRepositoryPort]] = [InMemoryStrategyRepository]
ADAPTERS = [pytest.param(factory, id=factory.__name__) for factory in REPOSITORIES]


def _authoring() -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )


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


def _document(text: str, strategy_id: str, revision: int, at: datetime = T0):
    compiled = _authoring().compile(CompileRequest(text, SourceFormat.YAML))
    assert compiled.ok and compiled.spec is not None and compiled.spec_hash is not None
    saved = replace(compiled.spec, identity=StrategyIdentity(strategy_id, revision))
    return StrategyRevisionRecord(
        spec=saved,
        spec_hash=compiled.spec_hash,
        source=RevisionSource(SourceFormat.YAML, text, compiled.source_hash),
        provenance=RevisionProvenance(RevisionOrigin.DOCUMENT, at),
    )


@pytest.mark.parametrize("factory", ADAPTERS)
def test_revision_source_is_immutable_after_the_next_revision(factory) -> None:
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


@pytest.mark.parametrize("factory", ADAPTERS)
def test_compiling_the_stored_source_reproduces_the_record_hashes(factory) -> None:
    repository = factory()
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    repository.add(_document(text, "s1", 1))

    stored = repository.get("s1", 1)
    assert stored.source is not None
    recompiled = _authoring().compile(CompileRequest(stored.source.text, stored.source.format))

    assert recompiled.spec_hash == stored.spec_hash == GOLDEN_SPEC_HASH
    assert recompiled.source_hash == stored.source.source_hash == source_hash_of(text)


@pytest.mark.parametrize("factory", ADAPTERS)
def test_legacy_revisions_have_no_source_and_keep_the_json_flow(factory) -> None:
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


@pytest.mark.parametrize("factory", ADAPTERS)
def test_stale_expected_revision_conflicts(factory) -> None:
    repository = factory()
    template = _template()
    repository.add(_legacy(template, "s1", 1))

    with pytest.raises(StrategyRevisionConflictError, match="expected=0 actual=1"):
        repository.append(_legacy(template, "s1", 2), expected_revision=0)
    with pytest.raises(StrategyRevisionConflictError, match="not monotonic"):
        repository.append(_legacy(template, "s1", 3), expected_revision=1)
    with pytest.raises(StrategyRevisionConflictError, match="already exists"):
        repository.add(_legacy(template, "s1", 1))
    with pytest.raises(StrategyRevisionConflictError, match="first revision must be 1"):
        repository.add(_legacy(template, "s2", 2))


@pytest.mark.parametrize("factory", ADAPTERS)
def test_missing_strategy_and_revision_are_not_found(factory) -> None:
    repository = factory()
    with pytest.raises(StrategyNotFoundError, match="strategy not found"):
        repository.get("missing")
    repository.add(_legacy(_template(), "s1", 1))
    with pytest.raises(StrategyNotFoundError, match="revision not found"):
        repository.get("s1", 2)
    with pytest.raises(StrategyNotFoundError, match="strategy not found"):
        repository.history("missing", PageRequest())


@pytest.mark.parametrize("factory", ADAPTERS)
def test_list_and_history_are_deterministic_and_paginated(factory) -> None:
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


@pytest.mark.parametrize("factory", ADAPTERS)
def test_history_exposes_document_source_provenance(
    factory: Callable[[], StrategyRepositoryPort],
) -> None:
    repository = factory()
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    repository.add(_document(text, "d1", 1))
    repository.append(_legacy(_template(), "d1", 2), expected_revision=1)

    first, second = repository.history("d1", PageRequest()).items
    assert first.origin is RevisionOrigin.DOCUMENT
    assert first.source_format is SourceFormat.YAML
    assert first.source_hash == source_hash_of(text)
    assert first.spec_hash == GOLDEN_SPEC_HASH
    assert second.origin is RevisionOrigin.LEGACY_JSON and second.source_hash is None


@pytest.mark.parametrize("factory", ADAPTERS)
def test_strategy_summary_is_derived_from_the_latest_revision(
    factory: Callable[[], StrategyRepositoryPort],
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
