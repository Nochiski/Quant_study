"""키움 모의 REST 호출. 주소는 모의 도메인 상수 하나이고 TR마다 호출 간격을 지킨다.

토큰은 메모리에만 두고, 새 토큰을 받을 때마다 `ka00001` 계좌를 설정한 계좌번호와 대조한다.
키·시크릿·토큰·계좌번호는 예외 문장과 로그에 싣지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message
from enum import StrEnum
from typing import IO, Protocol

from strategy_workbench.application.broker_connection.facade.connection import (
    ACCOUNT_NUMBER_DIGITS,
    BrokerCredentials,
    is_account_number,
)
from strategy_workbench.application.offline_execution.facade.execution import BrokerError

logger = logging.getLogger(__name__)

# 모의 전용 주소. 실전 주소는 이 어댑터에 없고, 주소를 바꾸는 인자도 두지 않는다.
MOCK_BASE_URL = "https://mockapi.kiwoom.com"
ACCOUNT_PATH = "/api/dostk/acnt"  # 계좌 TR 경로(ka00001·kt00001·kt00007·kt00018·ka10170)
# 같은 모의 호스트의 웹소켓. 실시간 주문체결(`00`) 기록에만 쓴다(S07).
MOCK_WEBSOCKET_URL = "wss://mockapi.kiwoom.com:10000/api/websocket"
# 한국은 일광절약시간이 없다. 고정 오프셋으로 Windows의 tzdata 의존을 피한다.
KST = timezone(timedelta(hours=9))
# 모의투자는 TR당 초당 1회다. 서버 도착 간격이 흔들려도 넘지 않게 여유를 둔다.
MOCK_MIN_INTERVAL_SECONDS = 1.1
_TOKEN_REFRESH_MARGIN = timedelta(minutes=10)
_MAX_PAGES = 50
# 오류 응답은 return_msg 안에 "[8030:...]"처럼 네 자리 코드를 싣는다.
_ERROR_CODE = re.compile(r"\[(\d{4})")
_MODE_MISMATCH_CODES = frozenset({"8030", "8031"})
_IP_CODES = frozenset({"8010", "8040", "8050", "8103"})
_AUTH_CODES = frozenset({"8001", "8002", "8020"})
_INVALID_TOKEN = "8005"
_RATE_LIMITED = "1700"


class KiwoomBlock(StrEnum):
    """주문 전에 연결 자체를 막는 사유. 설정 화면이 사유별 안내를 고르는 코드다."""

    MODE_MISMATCH = "mode_mismatch"
    IP_NOT_ALLOWED = "ip_not_allowed"
    AUTH_FAILED = "auth_failed"
    ACCOUNT_MISMATCH = "account_mismatch"
    LATCHED = "latched"


class KiwoomBlockedError(BrokerError):
    """연결·계좌·정지 래치 때문에 키움을 쓰지 않는다. 이 예외가 나면 주문은 나가지 않았다."""

    def __init__(self, reason: KiwoomBlock, detail: str) -> None:
        super().__init__(f"kiwoom mock blocked — reason={reason} {detail}")
        self.reason = reason


class KiwoomAnsweredError(BrokerError):
    """키움이 0이 아닌 return_code로 답했다. 그 요청을 처리하지 않았다는 키움의 답이다."""

    def __init__(self, api_id: str, return_code: int | None, error_code: str | None, message: str):
        super().__init__(
            f"kiwoom answered an error — api_id={api_id} return_code={return_code} "
            f"error_code={error_code} message={message}"
        )
        self.api_id = api_id
        self.error_code = error_code


class KiwoomNoResponseError(BrokerError):
    """응답을 받지 못했거나 읽을 수 없다. 주문 요청이면 결과를 모른다."""


@dataclass(frozen=True)
class KiwoomHttpResponse:
    status: int
    body: dict[str, object]
    continuation: str = ""
    next_key: str = ""


class KiwoomTransport(Protocol):
    """경로만 받는다. 호스트는 구현이 모의 도메인으로 고정한다."""

    def post(
        self, path: str, headers: Mapping[str, str], body: Mapping[str, object]
    ) -> KiwoomHttpResponse: ...


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가면 인증 헤더가 다른 호스트로 갈 수 있다. 따라가지 않는다."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class UrllibTransport:
    """표준 라이브러리 HTTP. 모의 도메인 밖으로는 보내지 않는다."""

    def __init__(
        self, *, timeout_seconds: float = 10.0, ssl_context: ssl.SSLContext | None = None
    ) -> None:
        """`ssl_context`는 조립하는 쪽이 고른다.

        python.org macOS 빌드는 기본 인증서 목록이 비어 있어 기본값으로는 TLS 확인에 실패한다.
        """
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive — got={timeout_seconds!r}")
        self._timeout_seconds = timeout_seconds
        # 같은 신뢰 저장소로 웹소켓(S07 체결 기록)도 연다.
        self.ssl_context = ssl_context
        handlers: list[urllib.request.BaseHandler] = [_RefuseRedirect()]
        if ssl_context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
        self._opener = urllib.request.build_opener(*handlers)

    def post(
        self, path: str, headers: Mapping[str, str], body: Mapping[str, object]
    ) -> KiwoomHttpResponse:
        if not path.startswith("/") or "//" in path or "@" in path:
            raise ValueError(f"kiwoom path must be an absolute path — path={path!r}")
        request = urllib.request.Request(
            MOCK_BASE_URL + path,
            data=json.dumps(dict(body)).encode("utf-8"),
            headers={"Content-Type": "application/json;charset=UTF-8", **headers},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return _decode(path, response.status, _headers(response.headers), response.read())
        except urllib.error.HTTPError as error:
            try:
                payload = error.read()
            except OSError as read_error:
                raise KiwoomNoResponseError(
                    f"kiwoom error body unreadable — path={path} status={error.code}"
                ) from read_error
            return _decode(path, error.code, _headers(error.headers), payload)
        except (urllib.error.URLError, OSError) as error:
            # URLError 는 원인(reason)을 감싼다. 인증서 확인 실패 같은 원인을 함께 남긴다.
            reason = getattr(error, "reason", error)
            raise KiwoomNoResponseError(
                f"no response from kiwoom mock — path={path} error={type(error).__name__} "
                f"reason={type(reason).__name__}: {reason}"
            ) from error


def system_trust_transport() -> UrllibTransport:
    """OS 신뢰 저장소로 TLS를 확인하는 전송.

    `truststore`가 설치돼 있으면 그것을 쓰고, 없으면 파이썬 기본값으로 둔다(Linux·Windows
    기본값은 OS 저장소를 읽는다). python.org macOS 빌드는 기본 인증서 목록이 비어 있어
    `truststore`가 필요하다.
    """
    try:
        import truststore
    except ImportError:
        return UrllibTransport()
    return UrllibTransport(ssl_context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def _headers(message: Message | None) -> dict[str, str]:
    return {} if message is None else {key.lower(): value for key, value in message.items()}


def _decode(
    path: str, status: int, headers: Mapping[str, str], payload: bytes
) -> KiwoomHttpResponse:
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise KiwoomNoResponseError(
            f"kiwoom response is not JSON — path={path} status={status} bytes={len(payload)}"
        ) from error
    if not isinstance(body, dict):
        raise KiwoomNoResponseError(
            f"kiwoom response is not an object — path={path} status={status}"
        )
    return KiwoomHttpResponse(
        status, body, headers.get("cont-yn", "").strip(), headers.get("next-key", "").strip()
    )


@dataclass(frozen=True)
class KiwoomCredentials:
    """사용자 한 사람의 모의 키와 계좌번호. repr에 값을 싣지 않는다."""

    app_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    account_number: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.app_key.strip() or not self.secret_key.strip():
            raise ValueError("kiwoom app key and secret must be non-empty")
        if not is_account_number(self.account_number):
            raise ValueError(
                f"kiwoom account number must be {ACCOUNT_NUMBER_DIGITS} digits — "
                f"length={len(self.account_number)}"
            )

    @classmethod
    def from_saved(cls, saved: BrokerCredentials) -> KiwoomCredentials:
        """설정 화면이 저장한 자격증명을 키움 어댑터 값으로 옮긴다(옮기는 곳은 여기 하나)."""
        return cls(saved.app_key, saved.secret_key, saved.account_number)


class _Pacer:
    """TR마다 마지막 호출 뒤 간격이 지날 때까지 기다린다."""

    def __init__(
        self,
        interval_seconds: float,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, api_id: str) -> None:
        last = self._last.get(api_id)
        if last is not None:
            remaining = last + self._interval_seconds - self._monotonic()
            if remaining > 0:
                self._sleep(remaining)
        self._last[api_id] = self._monotonic()


class KiwoomClient:
    """토큰·계좌 대조·호출 간격·여러 쪽 응답을 맡는다. 주문 판단은 하지 않는다."""

    def __init__(
        self,
        credentials: KiwoomCredentials,
        transport: KiwoomTransport,
        *,
        now: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = MOCK_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._now = now
        self._monotonic = monotonic
        self._pacer = _Pacer(min_interval_seconds, monotonic, sleep)
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def ensure_session(self) -> bool:
        """유효한 토큰이 없으면 발급하고 계좌를 대조한다. 새로 발급했으면 참이다."""
        now = self._now().astimezone(KST)
        if (
            self._token is not None
            and self._expires_at is not None
            and now < self._expires_at - _TOKEN_REFRESH_MARGIN
        ):
            return False
        self._token = None
        response = self._query(
            "au10001",
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appkey": self._credentials.app_key,
                "secretkey": self._credentials.secret_key,
            },
            token=None,
        )
        self._raise_for(response, "au10001")
        token = _text(response.body, "token")
        try:
            expires_at = datetime.strptime(_text(response.body, "expires_dt"), "%Y%m%d%H%M%S")
        except ValueError as error:
            raise KiwoomNoResponseError("kiwoom token response has no valid expires_dt") from error
        if not token:
            raise KiwoomNoResponseError("kiwoom token response has no token")
        account = self._query("ka00001", ACCOUNT_PATH, {}, token=token)
        self._raise_for(account, "ka00001")
        if _text(account.body, "acctNo") != self._credentials.account_number:
            raise KiwoomBlockedError(
                KiwoomBlock.ACCOUNT_MISMATCH,
                "the account bound to this key differs from the configured account number",
            )
        self._token = token
        self._expires_at = expires_at.replace(tzinfo=KST)
        return True

    def session_token(self) -> str:
        """웹소켓 로그인용 토큰. 이 노드 안에서만 쓰고 어디에도 남기지 않는다."""
        self.ensure_session()
        if self._token is None:
            raise KiwoomNoResponseError("kiwoom session has no token")
        return self._token

    def call(
        self,
        api_id: str,
        path: str,
        body: Mapping[str, object],
        *,
        continuation: str = "",
        next_key: str = "",
    ) -> KiwoomHttpResponse:
        """조회 TR 한 쪽. 토큰 무효(8005)면 한 번만 새 토큰으로 다시 읽는다."""
        self.ensure_session()
        response = self._query(api_id, path, body, self._token, continuation, next_key)
        if _error_code(response) == _INVALID_TOKEN:
            self._token = None
            self.ensure_session()
            response = self._query(api_id, path, body, self._token, continuation, next_key)
        self._raise_for(response, api_id)
        return response

    def rows(
        self, api_id: str, path: str, body: Mapping[str, object], list_key: str
    ) -> list[dict[str, object]]:
        """연속 조회(cont-yn·next-key)를 끝까지 따라가 목록을 합친다."""
        rows: list[dict[str, object]] = []
        continuation = next_key = ""
        for _ in range(_MAX_PAGES):
            response = self.call(api_id, path, body, continuation=continuation, next_key=next_key)
            items = response.body.get(list_key, [])
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise KiwoomNoResponseError(
                    f"kiwoom list has an unexpected shape — api_id={api_id} key={list_key}"
                )
            rows.extend(items)
            if response.continuation != "Y":
                return rows
            continuation, next_key = "Y", response.next_key
        raise KiwoomNoResponseError(
            f"kiwoom paging did not finish — api_id={api_id} pages={_MAX_PAGES}"
        )

    def order(self, api_id: str, body: Mapping[str, object]) -> dict[str, object]:
        """주문 TR 한 번. 다시 보내지 않는다. 토큰은 호출자가 직전에 확인했다."""
        if self._token is None:
            raise KiwoomBlockedError(KiwoomBlock.AUTH_FAILED, "no session token before an order")
        response = self._send(api_id, "/api/dostk/ordr", body, self._token)
        self._raise_for(response, api_id)
        return response.body

    def _query(
        self,
        api_id: str,
        path: str,
        body: Mapping[str, object],
        token: str | None,
        continuation: str = "",
        next_key: str = "",
    ) -> KiwoomHttpResponse:
        """조회 TR 전송. 호출 한도(1700)면 간격을 지켜 한 번만 다시 보낸다.

        다른 프로세스가 같은 TR을 막 불렀으면 이 프로세스의 간격 계산으로는 피할 수 없다(2026-10-21
        실측). 주문 TR에는 쓰지 않는다. 주문은 다시 보내지 않는다.
        """
        response = self._send(api_id, path, body, token, continuation, next_key)
        if _error_code(response) == _RATE_LIMITED:
            logger.warning("kiwoom mock rate limited — api_id=%s retrying once", api_id)
            response = self._send(api_id, path, body, token, continuation, next_key)
        return response

    def _send(
        self,
        api_id: str,
        path: str,
        body: Mapping[str, object],
        token: str | None,
        continuation: str = "",
        next_key: str = "",
    ) -> KiwoomHttpResponse:
        headers = {"api-id": api_id}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        if continuation:
            headers["cont-yn"] = continuation
            headers["next-key"] = next_key
        self._pacer.wait(api_id)
        started = self._monotonic()
        # 전송 전에 남긴다. 연결 자체가 실패해도(인증서·DNS) 어느 호출이었는지 기록이 남는다.
        logger.info("kiwoom mock call — api_id=%s path=%s", api_id, path)
        try:
            response = self._transport.post(path, headers, body)
        except BrokerError as error:
            logger.warning(
                "kiwoom mock call failed — api_id=%s path=%s elapsed_ms=%d error=%s",
                api_id,
                path,
                round((self._monotonic() - started) * 1000),
                self._scrub(str(error)),
            )
            raise
        logger.info(
            "kiwoom mock call — api_id=%s status=%s return_code=%s error_code=%s "
            "cont=%s elapsed_ms=%d",
            api_id,
            response.status,
            response.body.get("return_code"),
            _error_code(response),
            response.continuation,
            round((self._monotonic() - started) * 1000),
        )
        return response

    def _raise_for(self, response: KiwoomHttpResponse, api_id: str) -> None:
        """0이 아닌 return_code만 '키움이 처리하지 않았다'는 답으로 본다. 나머지는 결과를 모른다."""
        return_code = _return_code(response)
        if response.status == 200 and return_code == 0:
            return
        code = _error_code(response)
        message = self._scrub(_text(response.body, "return_msg"))
        if code in _MODE_MISMATCH_CODES:
            raise KiwoomBlockedError(
                KiwoomBlock.MODE_MISMATCH, f"api_id={api_id} error_code={code} {message}"
            )
        if code in _IP_CODES:
            raise KiwoomBlockedError(
                KiwoomBlock.IP_NOT_ALLOWED, f"api_id={api_id} error_code={code} {message}"
            )
        if code in _AUTH_CODES:
            raise KiwoomBlockedError(
                KiwoomBlock.AUTH_FAILED, f"api_id={api_id} error_code={code} {message}"
            )
        if return_code not in (None, 0):
            raise KiwoomAnsweredError(api_id, return_code, code, message)
        raise KiwoomNoResponseError(
            f"kiwoom returned no usable result code — api_id={api_id} status={response.status} "
            f"return_code={return_code}"
        )

    def _scrub(self, text: str) -> str:
        for secret in (
            self._credentials.app_key,
            self._credentials.secret_key,
            self._credentials.account_number,
            self._token,
        ):
            if secret:
                text = text.replace(secret, "***")
        return text


def _text(body: Mapping[str, object], key: str) -> str:
    value = body.get(key)
    return "" if value is None else str(value).strip()


def _return_code(response: KiwoomHttpResponse) -> int | None:
    try:
        return int(_text(response.body, "return_code"))
    except ValueError:
        return None


def _error_code(response: KiwoomHttpResponse) -> str | None:
    match = _ERROR_CODE.search(_text(response.body, "return_msg"))
    return None if match is None else match.group(1)
