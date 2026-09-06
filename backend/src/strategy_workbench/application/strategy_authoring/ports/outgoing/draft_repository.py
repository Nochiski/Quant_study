"""Server-draft persistence port owned by the authoring use case.

Unlike immutable StrategySpec revisions, a draft is an exact-source latest-value register guarded
by a monotonic compare-and-swap version. Adapters must never compile or normalize its source.
"""

from __future__ import annotations

from typing import Protocol

from ..._draft_models import StrategyDraft


class StrategyDraftNotFoundError(LookupError):
    pass


class StrategyDraftConflictError(RuntimeError):
    def __init__(self, message: str, *, current: StrategyDraft | None) -> None:
        super().__init__(message)
        self.current = current


class StrategyDraftRepositoryPort(Protocol):
    def get(self, draft_id: str) -> StrategyDraft:
        """Return the latest exact draft or raise StrategyDraftNotFoundError."""
        ...

    def save(self, draft: StrategyDraft, *, expected_version: int) -> StrategyDraft:
        """Create at expected 0 or replace exactly one current version atomically."""
        ...

    def delete(self, draft_id: str, *, expected_version: int) -> None:
        """Delete exactly the expected version; a newer writer must survive."""
        ...
