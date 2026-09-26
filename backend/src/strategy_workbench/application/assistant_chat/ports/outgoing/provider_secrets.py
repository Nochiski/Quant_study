"""Outgoing port: 공급자 비밀 저장소 (설계 spec D1/D5).

비밀은 backend를 떠나지 않는다. 이 포트를 지나는 값은 로그·응답·DB 덤프·OpenAPI 어디에도
나오면 안 되며, 예외 메시지에도 profile_id만 적는다(`.claude/rules/error-messages.md` 보안 예외).
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["ProviderSecretMissingError", "ProviderSecretStore"]


class ProviderSecretMissingError(LookupError):
    """프로파일은 있는데 그 키가 저장소에 없다. 키 값은 메시지에 적지 않는다."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"no stored secret for provider profile — profile_id={profile_id!r}")
        self.profile_id = profile_id


class ProviderSecretStore(Protocol):
    def get(self, profile_id: str) -> str:
        """없으면 `ProviderSecretMissingError`."""
        ...

    def put(self, profile_id: str, secret: str) -> None: ...

    def delete(self, profile_id: str) -> None:
        """멱등이다. 없는 키를 지워도 실패하지 않는다."""
        ...
