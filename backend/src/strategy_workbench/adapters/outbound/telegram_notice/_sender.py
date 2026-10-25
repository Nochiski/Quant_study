"""텔레그램 Bot API `sendMessage`(HTML)로 보낸다. 토큰·채팅 ID는 어떤 메시지에도 싣지 않는다.

한 메시지 한도(4,096자)를 넘으면 인용 상자 밖의 빈 줄 단위로 나눠 차례로 보낸다. 인용 상자
하나가 한도를 넘으면 그 안의 항목 경계에서 나누고 조각마다 상자를 다시 연다 — 여는 태그와 닫는
태그가 다른 메시지로 갈라지면 Bot API 가 400 으로 거절한다(2026-10-22 리뷰 #607). 마지막 수단으로
줄을 자를 때도 태그·문자 참조(`&amp;`) 중간은 자르지 않는다.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from strategy_workbench.application.weekly_rebalance.facade.rebalance import NoticeError

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4_000

Post = Callable[[str, bytes], dict[str, object]]


class TelegramNotice:
    def __init__(self, token: str, chat_id: str, *, post: Post | None = None) -> None:
        if not token or not chat_id:
            raise NoticeError("telegram token or chat id missing")
        self._url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._post = post or _urllib_post

    def __repr__(self) -> str:
        return "TelegramNotice(token=***, chat_id=***)"

    def send(self, html: str) -> None:
        for chunk in _chunks(html.strip()):
            data = urllib.parse.urlencode(
                {
                    "chat_id": self._chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                }
            ).encode()
            body = self._post(self._url, data)
            if body.get("ok") is not True:
                raise NoticeError(f"telegram refused — description={body.get('description')!r}")


_OPEN, _CLOSE = "<blockquote>", "</blockquote>"


def _chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in _blocks(text):
        for piece in _fit(block):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= MAX_MESSAGE_CHARS:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _blocks(text: str) -> list[str]:
    """인용 상자 밖의 빈 줄에서만 나눈다(상자 안의 빈 줄은 항목 사이다)."""
    blocks: list[str] = []
    depth = 0
    for part in text.split("\n\n"):
        if depth > 0:
            blocks[-1] += "\n\n" + part
        else:
            blocks.append(part)
        depth += part.count(_OPEN) - part.count(_CLOSE)
    return blocks


def _fit(block: str) -> list[str]:
    """한도 안이면 그대로, `제목<blockquote>항목…</blockquote>` 꼴이면 항목 경계에서 상자째
    나눈다."""
    if len(block) <= MAX_MESSAGE_CHARS:
        return [block]
    head, opened, rest = block.partition(_OPEN)
    if not opened or not rest.endswith(_CLOSE) or _OPEN in rest:
        return _cut(block, MAX_MESSAGE_CHARS)
    inner = rest[: -len(_CLOSE)]
    joiner = "\n\n" if "\n\n" in inner else "\n"
    pieces: list[str] = []
    prefix, items = head + _OPEN, []
    for item in inner.split(joiner):
        room = MAX_MESSAGE_CHARS - len(prefix) - len(_CLOSE)
        if len(joiner.join([*items, item])) <= room:
            items.append(item)
            continue
        if items:
            pieces.append(prefix + joiner.join(items) + _CLOSE)
            prefix, items = _OPEN, []
            room = MAX_MESSAGE_CHARS - len(prefix) - len(_CLOSE)
        if len(item) <= room:
            items.append(item)
            continue
        # 항목 하나가 상자 한도를 넘는 드문 경우: 줄 단위로 잘라 조각마다 상자로 감싼다.
        for part in _cut(item, room):
            pieces.append(prefix + part + _CLOSE)
            prefix = _OPEN
    if items:
        pieces.append(prefix + joiner.join(items) + _CLOSE)
    return pieces


def _cut(text: str, limit: int) -> list[str]:
    """줄바꿈에서 자른다. 줄이 한도보다 길면 태그(`<…>`)·문자 참조(`&…;`) 밖에서 자른다."""
    pieces: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = _outside_markup(text, limit)
        pieces.append(text[:cut])
        text = text[cut:].lstrip("\n")
    pieces.append(text)
    return pieces


def _outside_markup(text: str, limit: int) -> int:
    cut = limit
    if text.rfind("<", 0, cut) > text.rfind(">", 0, cut):
        cut = text.rfind("<", 0, cut)
    if text.rfind("&", 0, cut) > text.rfind(";", 0, cut):
        cut = text.rfind("&", 0, cut)
    return cut if cut > 0 else limit


def _urllib_post(url: str, data: bytes) -> dict[str, object]:
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15, context=_ssl_context()) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            body = json.load(error)
        except ValueError as decode_error:
            raise NoticeError(f"telegram error — status={error.code}") from decode_error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise NoticeError(f"telegram unreachable — error={type(error).__name__}") from error
    if not isinstance(body, dict):
        raise NoticeError("telegram answered a non-object body")
    return body


def _ssl_context() -> ssl.SSLContext | None:
    """python.org macOS 빌드는 기본 인증서 목록이 비어 있어 있으면 OS 신뢰 저장소를 쓴다."""
    try:
        import truststore
    except ImportError:
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
