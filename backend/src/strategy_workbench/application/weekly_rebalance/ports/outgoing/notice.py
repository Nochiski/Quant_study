"""Outgoing port: 주문지 알림(S07). 로그성 알림에는 쓰지 않는다."""

from __future__ import annotations

from typing import Protocol

__all__ = ["NoticeError", "NoticeSender"]


class NoticeError(Exception):
    """알림을 보내지 못했다. 문장에 토큰·채팅 ID를 싣지 않는다."""


class NoticeSender(Protocol):
    def send(self, html: str) -> None:
        """텔레그램 HTML(굵게·기울임·인용)로 쓴 글을 보낸다. 실패하면 `NoticeError`."""
        ...
