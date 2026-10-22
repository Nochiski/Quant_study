"""Outgoing ports: 키움 모의 자격증명 저장소와 연결 확인.

자격증명은 backend를 떠나지 않는다. 이 포트를 지나는 값은 로그·응답·예외 문장에 싣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

__all__ = [
    "BrokerConnectionProbe",
    "BrokerCredentialStore",
    "BrokerCredentials",
    "ConnectionCheck",
    "ConnectionOutcome",
]


@dataclass(frozen=True)
class BrokerCredentials:
    """사용자 한 사람의 모의 앱 키·시크릿·계좌번호. repr에 값을 싣지 않는다."""

    app_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    account_number: str = field(repr=False)


class BrokerCredentialStore(Protocol):
    def load(self) -> BrokerCredentials | None:
        """저장된 것이 없으면 None."""
        ...

    def save(self, credentials: BrokerCredentials) -> None: ...

    def delete(self) -> None:
        """멱등이다."""
        ...


class ConnectionOutcome(StrEnum):
    CONNECTED = "connected"
    NOT_SAVED = "not_saved"
    MODE_MISMATCH = "mode_mismatch"
    IP_NOT_ALLOWED = "ip_not_allowed"
    AUTH_FAILED = "auth_failed"
    ACCOUNT_MISMATCH = "account_mismatch"
    NO_RESPONSE = "no_response"
    REFUSED = "refused"


@dataclass(frozen=True)
class ConnectionCheck:
    """`detail`은 진단용 내부 문장이다. 키·토큰·계좌번호를 담지 않는다."""

    outcome: ConnectionOutcome
    detail: str = ""


class BrokerConnectionProbe(Protocol):
    def verify(self, credentials: BrokerCredentials) -> ConnectionCheck:
        """토큰 발급과 계좌 대조만 한다. 주문하지 않는다."""
        ...
