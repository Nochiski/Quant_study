"""Outgoing port: 매매 제외 목록(S07). 주문안을 만들 때 이 목록의 종목은 사지 않고, 들고
있으면 판다.

2026-11-19 사용자 결정: 공개매수로 모멘텀 점수를 크게 받은 종목(가격이 공개매수가에 묶인다)은
자동매매에서 뺀다. 모델 점수·순위는 그대로 두고 주문안에서만 뺀다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

__all__ = ["ExclusionSource", "ExclusionsUnavailableError"]


class ExclusionsUnavailableError(Exception):
    """제외 목록이 있는데 읽지 못했다. 목록 없이 주문안을 만들지 않는다."""


class ExclusionSource(Protocol):
    def active(self, on: date) -> Mapping[str, str]:
        """신호일 `on`에 유효한 제외 종목과 사유. 목록이 없으면 빈 값, 있는데 읽지 못하면
        `ExclusionsUnavailableError`."""
        ...
