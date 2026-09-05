from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRepositoryPort,
)

from ._draft_models import SaveStrategyDraftRequest, StrategyDraft, validate_draft_id
from .ports.outgoing.document_codec import CodecLimits, source_hash_of
from .ports.outgoing.draft_repository import (
    StrategyDraftConflictError,
    StrategyDraftNotFoundError,
    StrategyDraftRepositoryPort,
)


class InvalidStrategyDraftError(ValueError):
    pass


class StrategyDraftService:
    """Preserve invalid or valid editor source without becoming a compile authority."""

    def __init__(
        self,
        repository: StrategyDraftRepositoryPort,
        strategy_repository: StrategyRepositoryPort,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_source_bytes: int = CodecLimits().max_bytes,
    ) -> None:
        self._repository = repository
        self._strategy_repository = strategy_repository
        self._now = now
        self._max_source_bytes = max_source_bytes

    def get(self, draft_id: str) -> StrategyDraft:
        try:
            draft = self._repository.get(validate_draft_id(draft_id))
        except ValueError as error:
            raise InvalidStrategyDraftError(str(error)) from error
        self._validate_base(
            SaveStrategyDraftRequest(
                expected_version=draft.version,
                source=draft.source,
                format=draft.format,
                schema_version=draft.schema_version,
                strategy_id=draft.strategy_id,
                base_revision=draft.base_revision,
                base_spec_hash=draft.base_spec_hash,
            )
        )
        return draft

    def save(self, draft_id: str, request: SaveStrategyDraftRequest) -> StrategyDraft:
        try:
            validated_id = validate_draft_id(draft_id)
        except ValueError as error:
            raise InvalidStrategyDraftError(str(error)) from error
        source_bytes = len(request.source.encode("utf-8"))
        if source_bytes > self._max_source_bytes:
            raise InvalidStrategyDraftError(
                "draft source exceeds authoring limit -- "
                f"bytes={source_bytes} max_bytes={self._max_source_bytes}"
            )
        self._validate_base(request)
        updated_at = self._now()
        if updated_at.tzinfo is None:
            raise InvalidStrategyDraftError("draft clock must return a timezone-aware datetime")
        return self._repository.save(
            StrategyDraft(
                draft_id=validated_id,
                version=request.expected_version + 1,
                source=request.source,
                format=request.format,
                source_hash=source_hash_of(request.source),
                schema_version=request.schema_version,
                updated_at=updated_at.astimezone(UTC),
                strategy_id=request.strategy_id,
                base_revision=request.base_revision,
                base_spec_hash=request.base_spec_hash,
            ),
            expected_version=request.expected_version,
        )

    def delete(self, draft_id: str, *, expected_version: int) -> None:
        try:
            validated_id = validate_draft_id(draft_id)
            if (
                not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 1
            ):
                raise ValueError("expected_version must be an integer >= 1")
        except ValueError as error:
            raise InvalidStrategyDraftError(str(error)) from error
        self._repository.delete(validated_id, expected_version=expected_version)

    def _validate_base(self, request: SaveStrategyDraftRequest) -> None:
        if request.strategy_id is None:
            return
        assert request.base_revision is not None
        assert request.base_spec_hash is not None
        try:
            base = self._strategy_repository.get(request.strategy_id, request.base_revision)
        except StrategyNotFoundError as error:
            raise InvalidStrategyDraftError(
                "draft base revision does not exist -- "
                f"strategy_id={request.strategy_id} revision={request.base_revision}"
            ) from error
        if base.spec_hash != request.base_spec_hash:
            raise InvalidStrategyDraftError(
                "draft base hash does not match its immutable revision -- "
                f"strategy_id={request.strategy_id} revision={request.base_revision}"
            )


__all__ = [
    "InvalidStrategyDraftError",
    "SaveStrategyDraftRequest",
    "StrategyDraft",
    "StrategyDraftConflictError",
    "StrategyDraftNotFoundError",
    "StrategyDraftRepositoryPort",
    "StrategyDraftService",
]
