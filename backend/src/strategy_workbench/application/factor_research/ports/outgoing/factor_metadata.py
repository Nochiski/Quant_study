from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.factor.facade.expression import FieldMetadata


@dataclass(frozen=True)
class FactorMetadataSnapshot:
    data_snapshot_id: str
    fields: tuple[FieldMetadata, ...]


class FactorMetadataPort(Protocol):
    """Backend-owned field contracts used to validate and plan factor graphs."""

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot: ...
