"""동결 schema 1.0 revision 읽기 계약 (spec D2, P1-03).

1.0 row는 immutable trigger로 보호된 이력이다. codec은 dict 업그레이드 변환으로 spec을 만들고,
무결성은 (a) spec_json 바이트 sha256 == spec_hash, (b) source_text sha256 == source_hash,
(c) 업그레이드 payload가 현재 버전으로 hydrate, 세 가지로 검증한다. 변조는 전부 fail-closed다.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    StrategyRepositoryStorageError,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
    StrategyDocumentService,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    PageRequest,
    RevisionOrigin,
    RevisionProvenance,
)
from strategy_workbench.domain.strategy.facade.document import SourceFormat
from strategy_workbench.domain.strategy.facade.specification import (
    canonical_strategy_json,
    strategy_spec_hash,
)
from tests.frozen_revision_rows import (
    FIXTURES,
    FROZEN_SPEC_HASH,
    frozen_source_text,
    frozen_spec_json,
    open_repository,
    seed_frozen_rows,
    source_spec_hash,
)


def _authoring() -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )


def test_frozen_rows_are_read_through_the_upgrade_transform(tmp_path: Path) -> None:
    path = tmp_path / "frozen.sqlite3"
    seed_frozen_rows(path)

    with open_repository(path) as repository:
        document = repository.get("frozen-doc", 1)
        legacy = repository.get("frozen-legacy", 1)
        history = repository.history("frozen-doc", PageRequest())
        strategies = repository.list_strategies(PageRequest())

    for record in (document, legacy):
        assert record.requires_upgrade
        assert record.spec.identity.schema_version == "1.0"  # 동결 표시
        assert record.spec_hash == FROZEN_SPEC_HASH  # 저장된 1.0 hash, 재계산하지 않음
        assert record.spec_hash != strategy_spec_hash(record.spec)  # 1.1 모양의 hash와 다르다
        assert record.spec.factors[0].factor_id == "momentum"  # 평탄화된 factors로 읽힘
        assert record.spec.title == "퀄리티 모멘텀"
    assert document.source is not None and document.source.text == frozen_source_text()
    assert legacy.source is None
    assert {summary.spec_hash for summary in history.items} == {FROZEN_SPEC_HASH}
    assert {summary.strategy_id for summary in strategies.items} == {"frozen-doc", "frozen-legacy"}


@pytest.mark.parametrize(
    "tamper",
    ["spec_json_byte", "spec_hash", "source_hash", "payload_unhydratable"],
)
def test_tampered_frozen_rows_fail_closed(tmp_path: Path, tamper: str) -> None:
    path = tmp_path / f"tampered-{tamper}.sqlite3"
    spec_json = frozen_spec_json()
    if tamper == "spec_json_byte":
        seed_frozen_rows(
            path, spec_json=spec_json.replace('"title":"퀄리티 모멘텀"', '"title":"퀄리티 모멘텀!"')
        )
    elif tamper == "spec_hash":
        seed_frozen_rows(path, spec_hash="0" * 64)
    elif tamper == "source_hash":
        seed_frozen_rows(path, legacy_row=False, source_hash="f" * 64)
    else:
        broken = spec_json.replace(
            '"rebalance":"monthly"', '"rebalance":"never"'
        )  # 1.2 enum 밖 → hydrate 불가
        assert broken != spec_json
        seed_frozen_rows(
            path, spec_json=broken, spec_hash=hashlib.sha256(broken.encode("utf-8")).hexdigest()
        )

    with pytest.raises(StrategyRepositoryStorageError, match="integrity"):
        with open_repository(path) as repository:
            repository.get("frozen-doc", 1)


def test_frozen_records_cannot_be_written(tmp_path: Path) -> None:
    path = tmp_path / "write.sqlite3"
    seed_frozen_rows(path, legacy_row=False)
    with open_repository(path) as repository:
        frozen = repository.get("frozen-doc", 1)
        clone = replace(
            frozen,
            spec=replace(frozen.spec, identity=replace(frozen.spec.identity, strategy_id="copy")),
            provenance=RevisionProvenance(RevisionOrigin.DOCUMENT, datetime.now(UTC)),
        )
        with pytest.raises(StrategyRepositoryStorageError, match="frozen"):
            repository.add(clone)


def test_document_view_reports_requires_upgrade_and_regenerates_legacy_source_as_current(
    tmp_path: Path,
) -> None:
    path = tmp_path / "view.sqlite3"
    seed_frozen_rows(path)
    authoring = _authoring()
    with open_repository(path) as repository:
        documents = StrategyDocumentService(authoring, repository, new_id=lambda: "unused")
        document = documents.get("frozen-doc", 1)
        legacy = documents.get("frozen-legacy", 1)

    # 원문이 있는 row: 1.0 원문 그대로. 컴파일은 unsupported_schema_version이며 P2-02 업그레이드
    # 배너가 처리한다.
    assert document.requires_upgrade and not document.generated
    assert document.schema_version == "1.0" and document.spec_hash == FROZEN_SPEC_HASH
    assert document.source == frozen_source_text()
    compiled = authoring.compile(CompileRequest(document.source, SourceFormat.YAML))
    assert [d.code for d in compiled.diagnostics] == ["structure.unsupported_schema_version"]

    # legacy row: generated source는 현재 버전 canonical이라 바로 컴파일되고, hash는 저장된 1.0
    # hash와 다르다.
    assert legacy.requires_upgrade and legacy.generated and legacy.format is SourceFormat.JSON
    assert '"schema_version": "1.2"' in legacy.source
    recompiled = authoring.compile(CompileRequest(legacy.source, SourceFormat.JSON))
    assert recompiled.spec_hash is not None and recompiled.spec_hash != FROZEN_SPEC_HASH
    assert recompiled.spec_hash == source_spec_hash(legacy.source, SourceFormat.JSON)


def test_current_schema_records_keep_the_strict_source_binding(tmp_path: Path) -> None:
    """동결 완화는 은퇴 버전 row에만 적용된다: 현재 버전 row의 source drift는 여전히 거부된다."""
    path = tmp_path / "current.sqlite3"
    seed_frozen_rows(path, document_row=False, legacy_row=False)
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    compiled = _authoring().compile(CompileRequest(text, SourceFormat.YAML))
    assert compiled.spec is not None and compiled.spec_hash is not None
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)", ("cur", 1)
        )
        connection.execute(
            "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cur",
                1,
                "1.2",
                canonical_strategy_json(compiled.spec),
                compiled.spec_hash,
                "yaml",
                text.replace("max_name_weight: 0.05", "max_name_weight: 0.07"),
                hashlib.sha256(
                    text.replace("max_name_weight: 0.05", "max_name_weight: 0.07").encode("utf-8")
                ).hexdigest(),
                "document",
                "2026-09-17T00:00:00.000000+00:00",
                None,
            ),
        )
        connection.commit()

    with pytest.raises(StrategyRepositoryStorageError, match="different StrategySpec"):
        with open_repository(path) as repository:
            repository.get("cur", 1)


def test_frozen_row_source_text_is_not_rebound_to_the_stored_spec(tmp_path: Path) -> None:
    """의식적으로 포기한 검증을 계약으로 고정한다(spec D2): 1.0 row에서는 "source가 stored spec으로
    컴파일된다"를 재증명할 1.0 모델이 없다. source_hash가 맞는 한 원문이 spec과 무관해도 읽힌다.
    현재 버전 row의 같은 변조는 `test_current_schema_records_keep_the_strict_source_binding`이
    거부한다."""
    path = tmp_path / "rebound.sqlite3"
    seed_frozen_rows(path, document_row=False, legacy_row=False)
    unrelated = (
        (FIXTURES / "quality_momentum.yaml")
        .read_text(encoding="utf-8")
        .replace("max_name_weight: 0.05", "max_name_weight: 0.42")
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
            ("rebound", 1),
        )
        connection.execute(
            "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rebound",
                1,
                "1.0",
                frozen_spec_json(),
                FROZEN_SPEC_HASH,
                "yaml",
                unrelated,
                hashlib.sha256(unrelated.encode("utf-8")).hexdigest(),
                "document",
                "2026-09-17T00:00:00.000000+00:00",
                None,
            ),
        )
        connection.commit()

    with open_repository(path) as repository:
        record = repository.get("rebound", 1)

    assert record.requires_upgrade
    assert record.source is not None and "max_name_weight: 0.42" in record.source.text
    assert record.spec.risk.max_name_weight == 0.05  # spec은 stored payload, source와 무관


def test_summaries_carry_the_frozen_marker(tmp_path: Path) -> None:
    path = tmp_path / "summaries.sqlite3"
    seed_frozen_rows(path)
    with open_repository(path) as repository:
        strategies = repository.list_strategies(PageRequest())
        history = repository.history("frozen-legacy", PageRequest())

    assert all(summary.requires_upgrade for summary in strategies.items)
    assert [summary.requires_upgrade for summary in history.items] == [True]
