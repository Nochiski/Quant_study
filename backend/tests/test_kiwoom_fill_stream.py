"""S07 장중 체결 알림 기록기. 가짜 웹소켓 연결로만 돈다."""

from __future__ import annotations

import asyncio
import json
import stat
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import websockets

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomNoResponseError
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.stream import (
    StreamConnection,
    record_fills,
)

KST = timezone(timedelta(hours=9))
START = datetime(2026, 10, 26, 9, 0, tzinfo=KST)


@dataclass
class Clock:
    now_value: datetime = START

    def now(self) -> datetime:
        return self.now_value


@dataclass
class FakeConnection:
    clock: Clock
    incoming: deque[str | Exception]
    sent: list[str] = field(default_factory=list)
    closed: bool = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        self.clock.now_value += timedelta(minutes=1)
        if not self.incoming:
            raise websockets.ConnectionClosedOK(None, None)
        item = self.incoming.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _fill(cumulative: int) -> str:
    values = {"9201": "5012345678", "9203": "0000001", "913": "체결", "911": str(cumulative)}
    return json.dumps({"trnm": "REAL", "data": [{"type": "00", "values": values}]})


def test_recorder_logs_fills_masks_the_account_echoes_ping_and_reconnects(
    tmp_path: Path,
) -> None:
    clock = Clock()
    login = json.dumps({"trnm": "LOGIN", "return_code": 0})
    first = FakeConnection(clock, deque([login, _fill(1), '{"trnm": "PING"}', _fill(3)]))
    second = FakeConnection(clock, deque([login, _fill(5)]))
    connections = deque([first, second])

    async def connect() -> StreamConnection:
        return connections.popleft()

    async def no_wait(_: float) -> None:
        return None

    out = tmp_path / "records" / "fills-2026-10-26.jsonl"
    received = asyncio.run(
        record_fills(
            lambda: "fake-token-0123",
            connect,
            out,
            START + timedelta(minutes=8),
            clock.now,
            sleep=no_wait,
        )
    )
    assert (received.received, received.refused) == (3, None)
    assert json.loads(first.sent[0]) == {"trnm": "LOGIN", "token": "fake-token-0123"}
    assert json.loads(first.sent[1])["data"] == [{"item": [""], "type": ["00"]}]
    assert first.sent[2] == '{"trnm": "PING"}' and first.closed and second.closed
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["kind"] for line in lines] == [
        "login",
        "REAL",
        "REAL",
        "disconnected",
        "login",
        "REAL",
        "disconnected",
    ]
    text = out.read_text()
    assert "5012345678" not in text and "******5678" in text and "fake-token" not in text
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_recorder_stops_when_login_is_refused(tmp_path: Path) -> None:
    clock = Clock()
    refused = FakeConnection(clock, deque([json.dumps({"trnm": "LOGIN", "return_code": 1})]))

    async def connect() -> StreamConnection:
        return refused

    out = tmp_path / "fills.jsonl"
    received = asyncio.run(
        record_fills(lambda: "t", connect, out, START + timedelta(hours=6), clock.now)
    )
    # 리뷰 #605 P3: 로그인 거절도 [ok] 로 끝났다. 거절 사유를 돌려줘 실패로 드러낸다
    assert (received.received, received.refused) == (0, "return_code=1 ")
    assert len(refused.sent) == 1 and refused.closed


def test_a_token_failure_is_retried_instead_of_ending_the_day(tmp_path: Path) -> None:
    # 리뷰 #605 P3: 토큰 발급 오류(BrokerError)를 잡지 않아 첫 순간의 일시 오류로 그날 기록이 끝났다
    clock = Clock()
    login = json.dumps({"trnm": "LOGIN", "return_code": 0})
    connection = FakeConnection(clock, deque([login, _fill(2)]))
    attempts: list[int] = []

    def token() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise KiwoomNoResponseError("no response from kiwoom mock — path=/oauth2/token")
        return "fake-token"

    async def connect() -> StreamConnection:
        return connection

    async def no_wait(_: float) -> None:
        clock.now_value += timedelta(seconds=5)

    out = tmp_path / "fills.jsonl"
    result = asyncio.run(
        record_fills(token, connect, out, START + timedelta(minutes=3), clock.now, sleep=no_wait)
    )
    assert result.received == 1 and len(attempts) == 2
    kinds = [json.loads(line)["kind"] for line in out.read_text().splitlines()]
    assert kinds[0] == "token_failed" and "REAL" in kinds
