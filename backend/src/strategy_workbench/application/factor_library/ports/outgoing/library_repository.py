"""팩터 라이브러리 저장 포트. 버전 행은 쌓기만 하고 고치거나 지우지 않는다."""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.factor_library.facade.library import (
    LibraryFactor,
    LibraryFactorVersion,
)


class LibraryFactorNotFoundError(LookupError):
    pass


class LibraryFactorKeyConflictError(RuntimeError):
    """같은 키(꺼내 쓸 `factor_id`)의 항목이 이미 있다."""

    def __init__(self, message: str, *, existing_library_id: str) -> None:
        super().__init__(message)
        self.existing_library_id = existing_library_id


class LibraryFactorVersionConflictError(RuntimeError):
    """새 버전을 쓰는 사이 다른 쓰기가 먼저 버전을 올렸다."""

    def __init__(self, message: str, *, current_version: int) -> None:
        super().__init__(message)
        self.current_version = current_version


class FactorLibraryRepositoryPort(Protocol):
    def create(self, entry: LibraryFactor) -> LibraryFactor:
        """첫 버전과 함께 새 항목을 만든다. 같은 키가 있으면 `LibraryFactorKeyConflictError`."""
        ...

    def append_version(
        self, library_id: str, version: LibraryFactorVersion, *, expected_latest: int
    ) -> LibraryFactor:
        """최신 버전이 `expected_latest` 일 때만 다음 버전을 더한다(CAS)."""
        ...

    def get(self, library_id: str) -> LibraryFactor:
        """항목과 모든 버전. 없으면 `LibraryFactorNotFoundError`."""
        ...

    def list(self) -> tuple[LibraryFactor, ...]:
        """모든 항목(숨긴 것 포함), 만든 순서."""
        ...

    def set_hidden(self, library_id: str, hidden: bool) -> LibraryFactor:
        """숨기기·되살리기. 버전은 그대로 남는다."""
        ...
