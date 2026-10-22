"""S07 주문지 알림: 텔레그램 Bot API 전송. 네트워크 없이 가짜 전송으로 확인한다. 토큰은 가짜다."""

from __future__ import annotations

import urllib.parse

import pytest

from strategy_workbench.adapters.outbound.telegram_notice.facade.sender import (
    MAX_MESSAGE_CHARS,
    TelegramNotice,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import NoticeError

TOKEN = "123456:fake-token-0123456789"
CHAT = "-1000000000001"


class FakePost:
    def __init__(self, answer: dict[str, object] | None = None) -> None:
        self.answer = answer or {"ok": True, "result": {"message_id": 1}}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, data: bytes) -> dict[str, object]:
        self.calls.append((url, dict(urllib.parse.parse_qsl(data.decode()))))
        return self.answer


def test_sends_html_to_the_chat_and_hides_the_token() -> None:
    post = FakePost()
    notice = TelegramNotice(TOKEN, CHAT, post=post)
    notice.send("<b>S07 주문지</b>\n<blockquote>알멕</blockquote>\n")
    [(url, form)] = post.calls
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert form == {
        "chat_id": CHAT,
        "text": "<b>S07 주문지</b>\n<blockquote>알멕</blockquote>",
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    assert TOKEN not in repr(notice) and CHAT not in repr(notice)


def test_refusal_is_an_error_without_the_token() -> None:
    post = FakePost({"ok": False, "description": "Bad Request: can't parse entities"})
    with pytest.raises(NoticeError) as raised:
        TelegramNotice(TOKEN, CHAT, post=post).send("<b>깨진 태그")
    assert "can't parse entities" in str(raised.value) and TOKEN not in str(raised.value)
    with pytest.raises(NoticeError):
        TelegramNotice("", CHAT, post=post)


def test_long_text_is_split_between_blocks_in_order() -> None:
    block = "<blockquote>" + "\n".join(["가" * 50] * 30) + "</blockquote>"  # 약 1,550자
    text = "\n\n".join([f"<b>{n}</b>\n{block}" for n in range(6)])
    post = FakePost()
    TelegramNotice(TOKEN, CHAT, post=post).send(text)
    sent = [form["text"] for _, form in post.calls]
    assert len(sent) > 1 and all(len(chunk) <= MAX_MESSAGE_CHARS for chunk in sent)
    assert all(chunk.count("<blockquote>") == chunk.count("</blockquote>") for chunk in sent)
    assert "\n\n".join(sent) == text
