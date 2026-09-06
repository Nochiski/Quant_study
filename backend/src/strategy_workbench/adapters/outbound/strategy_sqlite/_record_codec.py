from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from strategy_workbench.application.strategy_design.facade.ports import (
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyRevisionRecord,
)
from strategy_workbench.domain.strategy.facade.document import (
    SourceFormat,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    canonical_strategy_json,
)

from ._errors import StrategyRepositoryStorageError
from ._values import datetime_text, optional_text, required_int, required_text

SourceSpecHashResolver = Callable[[str, SourceFormat], str]


def encode_record(
    record: StrategyRevisionRecord, *, source_spec_hash: SourceSpecHashResolver
) -> tuple[object, ...]:
    """Map a validated envelope to columns without defining a second StrategySpec DTO."""
    try:
        _verify_source_spec(record, source_spec_hash)
        source = record.source
        return (
            record.strategy_id,
            record.revision,
            record.spec.identity.schema_version,
            canonical_strategy_json(record.spec),
            record.spec_hash,
            source.format.value if source else None,
            source.text if source else None,
            source.source_hash if source else None,
            record.provenance.origin.value,
            datetime_text(record.provenance.created_at),
            record.provenance.change_note,
        )
    except (TypeError, ValueError) as error:
        raise StrategyRepositoryStorageError(
            "strategy revision cannot be persisted because its source and spec disagree -- "
            f"strategy_id={record.strategy_id} revision={record.revision}: {error}"
        ) from error


def decode_record(
    row: sqlite3.Row, *, source_spec_hash: SourceSpecHashResolver
) -> StrategyRevisionRecord:
    """Rehydrate through the domain contract and reject every redundant-field mismatch."""
    location = "strategy_id=<invalid> revision=<invalid>"
    try:
        strategy_id = required_text(row, "strategy_id")
        revision = required_int(row, "revision")
        location = f"strategy_id={strategy_id} revision={revision}"
        schema_version = required_text(row, "schema_version")
        spec_json = required_text(row, "spec_json")
        payload: Any = json.loads(spec_json)
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise ValueError("spec_json must contain a JSON object with string keys")
        if payload.get("schema_version") != schema_version:
            raise ValueError("schema_version column does not match the canonical strategy payload")
        hydration = hydrate_strategy_document(
            _as_document(payload),
            identity=StrategyIdentity(strategy_id, revision, schema_version),
        )
        if not hydration.ok or hydration.spec is None:
            detail = ", ".join(f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5])
            raise ValueError(f"canonical strategy payload cannot hydrate — {detail}")
        spec = hydration.spec
        if canonical_strategy_json(spec) != spec_json:
            raise ValueError("spec_json is not canonical for its hydrated StrategySpec")

        origin = RevisionOrigin(required_text(row, "origin"))
        source_format_text = optional_text(row, "source_format")
        source_text = optional_text(row, "source_text")
        source_hash = optional_text(row, "source_hash")
        source: RevisionSource | None
        if source_format_text is None and source_text is None and source_hash is None:
            source = None
        elif source_format_text is not None and source_text is not None and source_hash is not None:
            source = RevisionSource(SourceFormat(source_format_text), source_text, source_hash)
        else:
            raise ValueError("source format/text/hash must be all present or all absent")

        created_at_text = required_text(row, "created_at")
        created_at = datetime.fromisoformat(created_at_text)
        if datetime_text(created_at) != created_at_text:
            raise ValueError("created_at is not canonical timezone-aware UTC text")
        record = StrategyRevisionRecord(
            spec=spec,
            spec_hash=required_text(row, "spec_hash"),
            source=source,
            provenance=RevisionProvenance(
                origin=origin,
                created_at=created_at,
                change_note=optional_text(row, "change_note"),
            ),
        )
        _verify_source_spec(record, source_spec_hash)
        return record
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise StrategyRepositoryStorageError(
            f"stored strategy revision failed integrity validation — {location}: {error}"
        ) from error


def _as_document(payload: dict[str, Any]) -> Mapping[str, object]:
    return payload


def _verify_source_spec(
    record: StrategyRevisionRecord, source_spec_hash: SourceSpecHashResolver
) -> None:
    """Bind exact document source to the canonical spec via the injected authoring SoT."""
    source = record.source
    if source is None:
        return
    compiled_hash = source_spec_hash(source.text, source.format)
    if not isinstance(compiled_hash, str):
        raise TypeError("source spec hash resolver must return text")
    if compiled_hash != record.spec_hash:
        raise ValueError(
            "source compiles to a different StrategySpec -- "
            f"stored={record.spec_hash} compiled={compiled_hash}"
        )
