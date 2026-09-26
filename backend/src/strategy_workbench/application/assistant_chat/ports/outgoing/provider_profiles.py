"""Outgoing port: 공급자 프로파일 저장소 (설계 spec D3/D5).

프로파일에는 비밀이 들어 있지 않다. 키는 `ProviderSecretStore`가 따로 가진다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import ProviderProfile

__all__ = ["ProviderProfileNotFoundError", "ProviderProfileRepository"]


class ProviderProfileNotFoundError(LookupError):
    """요청한 프로파일이 저장소에 없다."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"unknown provider profile — profile_id={profile_id!r}")
        self.profile_id = profile_id


class ProviderProfileRepository(Protocol):
    def list(self) -> tuple[ProviderProfile, ...]:
        """등록 순서대로 전부. 활성 프로파일은 최대 하나다."""
        ...

    def get(self, profile_id: str) -> ProviderProfile:
        """없으면 `ProviderProfileNotFoundError`."""
        ...

    def add(self, profile: ProviderProfile) -> None: ...

    def delete(self, profile_id: str) -> None:
        """없으면 `ProviderProfileNotFoundError`."""
        ...

    def set_active(self, profile_id: str) -> None:
        """이 프로파일만 활성으로 만들고 나머지는 비활성으로 내린다."""
        ...
