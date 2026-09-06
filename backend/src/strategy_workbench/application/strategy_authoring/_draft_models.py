from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .ports.outgoing.document_codec import SourceFormat, source_hash_of


def _exact_positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def validate_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or not draft_id.strip() or len(draft_id) > 512:
        raise ValueError("draft_id must be non-blank text no longer than 512 characters")
    return draft_id


@dataclass(frozen=True)
class SaveStrategyDraftRequest:
    """Exact editor bytes plus the immutable base they were edited from.

    Drafts deliberately accept syntactically invalid source. Compilation remains the authoring
    service's responsibility; this contract only preserves bytes and compare-and-swap identity.
    """

    expected_version: int
    source: str
    format: SourceFormat
    schema_version: str
    strategy_id: str | None = None
    base_revision: int | None = None
    base_spec_hash: str | None = None

    def __post_init__(self) -> None:
        _exact_positive_int(self.expected_version, "expected_version", allow_zero=True)
        if not isinstance(self.source, str):
            raise ValueError("source must be text")
        if not self.schema_version.strip():
            raise ValueError("schema_version must be non-blank")
        saved_fields = (self.strategy_id, self.base_revision, self.base_spec_hash)
        if all(value is None for value in saved_fields):
            return
        if any(value is None for value in saved_fields):
            raise ValueError(
                "strategy_id, base_revision and base_spec_hash must be all present or all absent"
            )
        assert self.strategy_id is not None
        assert self.base_revision is not None
        assert self.base_spec_hash is not None
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must be non-blank")
        _exact_positive_int(self.base_revision, "base_revision")
        if len(self.base_spec_hash) != 64:
            raise ValueError("base_spec_hash must be a 64-character canonical hash")


@dataclass(frozen=True)
class StrategyDraft:
    draft_id: str
    version: int
    source: str
    format: SourceFormat
    source_hash: str
    schema_version: str
    updated_at: datetime
    strategy_id: str | None = None
    base_revision: int | None = None
    base_spec_hash: str | None = None

    def __post_init__(self) -> None:
        validate_draft_id(self.draft_id)
        _exact_positive_int(self.version, "version")
        request = SaveStrategyDraftRequest(
            expected_version=self.version - 1,
            source=self.source,
            format=self.format,
            schema_version=self.schema_version,
            strategy_id=self.strategy_id,
            base_revision=self.base_revision,
            base_spec_hash=self.base_spec_hash,
        )
        del request
        expected_hash = source_hash_of(self.source)
        if self.source_hash != expected_hash:
            raise ValueError(
                "draft source_hash does not match exact source bytes -- "
                f"given={self.source_hash} expected={expected_hash}"
            )
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("draft updated_at must be timezone-aware UTC")
