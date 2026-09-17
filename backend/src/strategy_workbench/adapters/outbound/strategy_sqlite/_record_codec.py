from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from strategy_workbench.application.strategy_design.facade.ports import (
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyRevisionRecord,
)
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    SourceFormat,
    hydrate_strategy_document,
    upgrade_document_1_0,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
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
        if record.requires_upgrade:
            raise ValueError(
                "a frozen retired-schema revision is read-only history and cannot be written — "
                f"schema_version={record.spec.identity.schema_version}"
            )
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
        spec_hash = required_text(row, "spec_hash")
        frozen = schema_version == LEGACY_SCHEMA_VERSION
        if frozen:
            spec = _decode_frozen_spec(payload, spec_json, spec_hash, strategy_id, revision)
        else:
            hydration = hydrate_strategy_document(
                _as_document(payload),
                identity=StrategyIdentity(strategy_id, revision, schema_version),
            )
            if not hydration.ok or hydration.spec is None:
                detail = ", ".join(
                    f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5]
                )
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
            spec_hash=spec_hash,
            source=source,
            provenance=RevisionProvenance(
                origin=origin,
                created_at=created_at,
                change_note=optional_text(row, "change_note"),
            ),
        )
        if not frozen:
            _verify_source_spec(record, source_spec_hash)
        return record
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise StrategyRepositoryStorageError(
            f"stored strategy revision failed integrity validation — {location}: {error}"
        ) from error


def _as_document(payload: dict[str, Any]) -> Mapping[str, object]:
    return payload


def _decode_frozen_spec(
    payload: dict[str, Any], spec_json: str, spec_hash: str, strategy_id: str, revision: int
) -> StrategySpec:
    """Read a retired-schema row without a model for that schema (spec D2).

    The 1.0 model no longer exists, so "the source compiles to the stored spec" cannot be
    re-proven. The row is immutable and was proven when written; what is verified here is that
    the stored bytes are what the hash column claims and that the domain upgrade transform still
    understands them. The spec keeps `schema_version` 1.0 as its frozen marker.
    """
    if hashlib.sha256(spec_json.encode("utf-8")).hexdigest() != spec_hash:
        raise ValueError("frozen spec_json bytes do not match the stored spec_hash")
    upgraded = upgrade_document_1_0(payload)
    hydration = hydrate_strategy_document(
        upgraded, identity=StrategyIdentity(strategy_id, revision, CURRENT_SCHEMA_VERSION)
    )
    if not hydration.ok or hydration.spec is None:
        detail = ", ".join(f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5])
        raise ValueError(f"frozen strategy payload cannot be upgraded — {detail}")
    spec = hydration.spec
    return replace(spec, identity=replace(spec.identity, schema_version=LEGACY_SCHEMA_VERSION))


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
