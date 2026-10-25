"""실시간 주문체결(`00`) 기록(S07 복기용). 체결 건마다 시각·가격·수량이 남는다.

- 계좌번호 칸(`9201`)은 끝 4자리만 남긴다. 토큰은 남기지 않는다.
- 앱 키당 웹소켓 세션은 하나다. 두 번째 접속이 첫 접속을 `R10001`로 끊는다(2026-10-21 실측).
  그래서 이 기록기는 한 곳에서만 돌린다.
- 끊기면 다시 접속한다. 끊긴 사이 알림은 다시 오지 않는다(실측). 누적 칸(`911`·`903`)이 다음 알림에
  반영되지만, 대사의 정본은 조회다.
- 토큰 발급이 실패해도 끝내지 않고 다시 시도한다. 로그인이 거절되면 그 사유를 돌려주고 멈춘다
  (호출자가 실패로 드러낸다, 2026-10-22 리뷰 #605).
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import websockets

from strategy_workbench.adapters.outbound.kiwoom_mock._http import MOCK_WEBSOCKET_URL
from strategy_workbench.application.offline_execution.facade.execution import BrokerError

_REGISTER = {
    "trnm": "REG",
    "grp_no": "1",
    "refresh": "1",
    "data": [{"item": [""], "type": ["00"]}],
}
_ACCOUNT_FIELD = "9201"


class StreamConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


Connect = Callable[[], Awaitable[StreamConnection]]


@dataclass(frozen=True)
class FillStreamResult:
    """받은 체결·접수 알림 건수. `refused`가 있으면 로그인이 거절돼 그날 기록을 멈췄다."""

    received: int
    refused: str | None = None


def mock_connect(ssl_context: ssl.SSLContext | None = None) -> Connect:
    async def connect() -> StreamConnection:
        return await websockets.connect(
            MOCK_WEBSOCKET_URL, ssl=ssl_context or True, open_timeout=10
        )

    return connect


async def record_fills(
    token: Callable[[], str],
    connect: Connect,
    out: Path,
    until: datetime,
    now: Callable[[], datetime],
    *,
    reconnect_seconds: float = 5.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FillStreamResult:
    """`until`까지 받는다."""
    received = 0
    while now() < until:
        try:
            session_token = token()
        except BrokerError as error:
            # 토큰 발급의 일시 오류(응답 없음 등)로 그날 기록을 잃지 않게 다시 시도한다.
            _write(out, now(), "token_failed", {"error": type(error).__name__})
            await sleep(reconnect_seconds)
            continue
        connection: StreamConnection | None = None
        try:
            connection = await connect()
            await connection.send(json.dumps({"trnm": "LOGIN", "token": session_token}))
            login = json.loads(await asyncio.wait_for(connection.recv(), 10))
            _write(out, now(), "login", login)
            if login.get("return_code") != 0:
                why = f"return_code={login.get('return_code')} {login.get('return_msg', '')}"
                return FillStreamResult(received, why)
            await connection.send(json.dumps(_REGISTER))
            while now() < until:
                remaining = (until - now()).total_seconds()
                try:
                    raw = await asyncio.wait_for(connection.recv(), max(0.01, min(remaining, 60)))
                except TimeoutError:
                    continue
                message = json.loads(raw)
                if message.get("trnm") == "PING":
                    await connection.send(raw if isinstance(raw, str) else raw.decode())
                    continue
                if message.get("trnm") == "REAL":
                    received += len(message.get("data") or [])
                _write(out, now(), str(message.get("trnm", "?")), message)
        except (OSError, ValueError, websockets.WebSocketException) as error:
            _write(out, now(), "disconnected", {"error": type(error).__name__})
            await sleep(reconnect_seconds)
        finally:
            if connection is not None:
                await connection.close()
    return FillStreamResult(received)


def _write(out: Path, at: datetime, kind: str, message: object) -> None:
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(
        {"at": at.isoformat(timespec="seconds"), "kind": kind, "msg": _masked(message)},
        ensure_ascii=False,
    )
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _masked(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: ("******" + str(item)[-4:] if key == _ACCOUNT_FIELD else _masked(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return value
