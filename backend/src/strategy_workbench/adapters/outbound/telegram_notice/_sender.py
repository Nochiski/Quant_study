"""텔레그램 Bot API `sendMessage`(HTML)로 보낸다. 토큰·채팅 ID는 어떤 메시지에도 싣지 않는다.

한 메시지 한도(4,096자)를 넘으면 빈 줄 단위로 나눠 차례로 보낸다. 태그가 나뉘지 않도록 인용 상자
하나는 한 덩어리 안에 둔다.
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


def _chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= MAX_MESSAGE_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > MAX_MESSAGE_CHARS:
            cut = block.rfind("\n", 0, MAX_MESSAGE_CHARS)
            cut = cut if cut > 0 else MAX_MESSAGE_CHARS
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return chunks


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
