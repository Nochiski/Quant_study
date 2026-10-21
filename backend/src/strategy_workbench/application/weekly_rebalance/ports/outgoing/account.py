"""Outgoing port: 주문안을 만들 때의 계좌(현금·보유)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

__all__ = ["AccountSource", "AccountState", "AccountUnavailableError"]


class AccountUnavailableError(Exception):
    """계좌를 읽지 못했다. 문장에 키·토큰·계좌번호를 싣지 않는다."""


@dataclass(frozen=True)
class AccountState:
    """`reconciled`는 실행 원장 대사 결과다. 원장을 아직 만들지 않았으면 None이다."""

    cash_krw: int
    holdings: Mapping[str, int]
    reconciled: bool | None
    detail: str = ""


class AccountSource(Protocol):
    def current(self) -> AccountState: ...
