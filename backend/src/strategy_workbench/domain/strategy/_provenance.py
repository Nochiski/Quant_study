"""A strategy execution source and its resolved provenance, shared by runs and traces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeAlias

from ._models import StrategySpec


class StrategySourceKind(StrEnum):
    SAVED_REVISION = "saved_revision"
    INLINE_DRAFT = "inline_draft"


@dataclass(frozen=True)
class SavedRevisionReference:
    """Resolve an immutable revision and fail before calculation if its hash differs."""

    strategy_id: str
    revision: int
    expected_spec_hash: str
    kind: Literal["saved_revision"]


@dataclass(frozen=True)
class InlineDraft:
    """Use an unsaved typed spec while recording its authoring-source hash when known.

    An inline draft can be researched or backtested, but it is never a deployment source.
    `source_hash` is client-asserted provenance: the server cannot verify it without the text.
    """

    spec: StrategySpec
    kind: Literal["inline_draft"]
    source_hash: str | None = None


StrategySource: TypeAlias = SavedRevisionReference | InlineDraft


@dataclass(frozen=True)
class StrategyProvenance:
    """The exact strategy meaning a calculation consumed."""

    kind: StrategySourceKind
    spec_hash: str
    schema_version: str
    strategy_id: str | None = None
    revision: int | None = None
    source_hash: str | None = None
