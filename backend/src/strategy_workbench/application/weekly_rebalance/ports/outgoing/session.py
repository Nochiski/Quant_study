"""Outgoing port: 오늘 정규장 상태(S07, 2026-11-05 사용자 결정).

수능일·새해 첫 거래일처럼 장 시각이 바뀌는 날을 시각 표 없이 알아차리려고, 브로커가 실시간으로 알린
장운영 신호로 오늘 정규장이 열렸는지·끝났는지 답한다. 서버 예약 실행(`weekly_rebalance_cron.sh`)이
이 답으로 장이 열리는 순간 주문을 내고(2026-11-11), 장 마감 기록을 정규장이 끝날 때까지 미룬다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

__all__ = ["MarketPhase", "MarketSession"]


class MarketPhase(StrEnum):
    UNKNOWN = "unknown"  # 오늘 장운영 신호를 받지 못했다 — 예약 실행은 정해진 시각에 돈다
    BEFORE_OPEN = "before_open"
    OPEN = "open"
    CLOSED = "closed"  # 정규장이 끝났다


class MarketSession(Protocol):
    def phase(self, now: datetime) -> MarketPhase:
        """`now` 날짜의 정규장 상태. 기록을 읽지 못하면 `OSError`·`ValueError`."""
        ...
