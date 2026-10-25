"""Outgoing port: 공급자 프로파일 저장소 (설계 spec D3/D5).

프로파일에는 비밀이 들어 있지 않다. 키는 `ProviderSecretStore`가 따로 가진다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import ProviderProfile

__all__ = [
    "ProviderProfileNotFoundError",
    "ProviderProfileRepository",
    "ProviderStorageUnavailableError",
]


class ProviderProfileNotFoundError(LookupError):
    """요청한 프로파일이 저장소에 없다."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"unknown provider profile — profile_id={profile_id!r}")
        self.profile_id = profile_id


class ProviderStorageUnavailableError(RuntimeError):
    """프로파일 저장소 자체를 읽거나 쓸 수 없다(3차 리뷰 4). 사용자가 저장 파일을 고쳐야 풀린다.

    `reason`은 저장소가 고른 사유 이름이다. 문장에 값·경로는 싣지 않는다.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"provider storage is unavailable — reason={reason!r}")
        self.reason = reason


class ProviderProfileRepository(Protocol):
    def ensure_writable(self) -> None:
        """지금 쓸 수 있는지 미리 확인한다. 못 쓰면 `ProviderStorageUnavailableError`.

        연결 테스트(공급자 호출)를 하기 전에 부른다. 테스트가 통과한 뒤에야 저장이 안 된다는 걸
        알리면 사용자는 같은 키로 같은 실패를 반복한다.
        """
        ...

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
