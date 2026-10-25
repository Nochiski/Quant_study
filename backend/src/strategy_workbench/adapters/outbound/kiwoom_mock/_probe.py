"""설정 화면의 "연결 확인"(S06 ②-2). 토큰 발급과 `ka00001` 계좌 대조만 하고 주문하지 않는다."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from strategy_workbench.adapters.outbound.kiwoom_mock._http import (
    KST,
    KiwoomAnsweredError,
    KiwoomBlock,
    KiwoomBlockedError,
    KiwoomClient,
    KiwoomCredentials,
    KiwoomNoResponseError,
    KiwoomTransport,
    UrllibTransport,
)
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    ConnectionCheck,
    ConnectionOutcome,
)

_BLOCK_OUTCOMES = {
    KiwoomBlock.MODE_MISMATCH: ConnectionOutcome.MODE_MISMATCH,
    KiwoomBlock.IP_NOT_ALLOWED: ConnectionOutcome.IP_NOT_ALLOWED,
    KiwoomBlock.AUTH_FAILED: ConnectionOutcome.AUTH_FAILED,
    KiwoomBlock.ACCOUNT_MISMATCH: ConnectionOutcome.ACCOUNT_MISMATCH,
    KiwoomBlock.LATCHED: ConnectionOutcome.REFUSED,
}


class KiwoomConnectionProbe:
    def __init__(
        self,
        *,
        transport: KiwoomTransport | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport or UrllibTransport()
        self._now = now or (lambda: datetime.now(KST))
        self._monotonic = monotonic
        self._sleep = sleep

    def verify(self, credentials: BrokerCredentials) -> ConnectionCheck:
        try:
            kiwoom = KiwoomCredentials.from_saved(credentials)
        except ValueError as error:
            return ConnectionCheck(ConnectionOutcome.AUTH_FAILED, str(error))
        client = KiwoomClient(
            kiwoom, self._transport, now=self._now, monotonic=self._monotonic, sleep=self._sleep
        )
        try:
            client.ensure_session()
        except KiwoomBlockedError as error:
            return ConnectionCheck(_BLOCK_OUTCOMES[error.reason], str(error))
        except KiwoomAnsweredError as error:
            return ConnectionCheck(ConnectionOutcome.REFUSED, str(error))
        except KiwoomNoResponseError as error:
            return ConnectionCheck(ConnectionOutcome.NO_RESPONSE, str(error))
        return ConnectionCheck(ConnectionOutcome.CONNECTED)


class ScriptedConnectionProbe:
    """브라우저 e2e 전용 대본. 키움을 부르지 않고 계좌번호로 결과를 고른다."""

    CONNECTED_ACCOUNT = "5012345678"

    def verify(self, credentials: BrokerCredentials) -> ConnectionCheck:
        if credentials.account_number == self.CONNECTED_ACCOUNT:
            return ConnectionCheck(ConnectionOutcome.CONNECTED, "scripted")
        return ConnectionCheck(ConnectionOutcome.ACCOUNT_MISMATCH, "scripted")
