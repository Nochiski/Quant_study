"""키움 모의 연결 설정: 저장·조회·삭제·연결 확인(S06 ②-2).

키는 사용자가 "연결 확인"을 누를 때만 키움에 쓴다. 응답에는 저장 여부와 계좌번호 끝 4자리만 싣는다.
화면 문장은 여기서 한글로 완성하고 결과 코드와 함께 보낸다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from strategy_workbench.application.broker_connection.ports.outgoing.broker import (
    BrokerConnectionProbe,
    BrokerCredentials,
    BrokerCredentialStore,
    BrokerCredentialsUnreadableError,
    ConnectionOutcome,
)

ACCOUNT_TAIL_DIGITS = 4
ACCOUNT_NUMBER_DIGITS = 10


def is_account_number(value: str) -> bool:
    """키움 계좌번호 형식(숫자 10자리). 화면 입력 검증과 키움 어댑터가 같은 규칙을 쓴다."""
    return re.fullmatch(rf"\d{{{ACCOUNT_NUMBER_DIGITS}}}", value) is not None


logger = logging.getLogger(__name__)

OUTCOME_MESSAGES: dict[ConnectionOutcome, str] = {
    ConnectionOutcome.CONNECTED: "연결됐습니다. 키움 모의 계좌가 입력한 계좌번호와 같습니다.",
    ConnectionOutcome.NOT_SAVED: (
        "저장된 키움 모의 키가 없습니다. 먼저 키와 계좌번호를 저장해 주세요."
    ),
    ConnectionOutcome.MODE_MISMATCH: (
        "실전투자용 키로 보입니다. 키움 REST API 포털의 '모의투자 App Key 관리'에서 받은 키를 "
        "넣어 주세요."
    ),
    ConnectionOutcome.IP_NOT_ALLOWED: (
        "이 PC의 인터넷 주소(IP)가 키움에 등록되지 않았습니다. 키움 REST API 포털의 "
        "'모의투자 App Key 관리'에서 지금 IP를 등록해 주세요."
    ),
    ConnectionOutcome.AUTH_FAILED: (
        "앱 키나 시크릿이 맞지 않습니다. 키움 포털에서 받은 값을 다시 확인해 주세요."
    ),
    ConnectionOutcome.ACCOUNT_MISMATCH: (
        "이 키에 묶인 계좌가 입력한 계좌번호와 다릅니다. 계좌번호를 확인해 주세요. "
        "계좌가 다르면 주문을 보내지 않습니다."
    ),
    ConnectionOutcome.NO_RESPONSE: (
        "키움 모의 서버에서 응답을 받지 못했습니다. 잠시 뒤 다시 확인해 주세요."
    ),
    ConnectionOutcome.REFUSED: "키움이 확인 요청을 거절했습니다. 잠시 뒤 다시 확인해 주세요.",
    ConnectionOutcome.UNREADABLE: (
        "저장된 키움 모의 키를 읽을 수 없습니다. 키와 계좌번호를 다시 저장하거나 지워 주세요."
    ),
}


class InvalidBrokerCredentialsError(ValueError):
    """입력 형식이 틀렸다. 값은 문장에 싣지 않고 칸 이름만 싣는다."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class BrokerConnectionStatus:
    saved: bool
    account_tail: str | None
    unreadable: bool = False  # 저장된 항목이 깨져 읽지 못했다(다시 저장·삭제로 고친다)


@dataclass(frozen=True)
class BrokerConnectionResult:
    outcome: ConnectionOutcome
    message: str
    account_tail: str | None


class BrokerConnectionService:
    def __init__(self, store: BrokerCredentialStore, probe: BrokerConnectionProbe) -> None:
        self._store = store
        self._probe = probe

    def status(self) -> BrokerConnectionStatus:
        try:
            credentials = self._store.load()
        except BrokerCredentialsUnreadableError as error:
            logger.warning("kiwoom mock credentials unreadable — %s", error)
            return BrokerConnectionStatus(False, None, unreadable=True)
        return BrokerConnectionStatus(credentials is not None, _tail(credentials))

    def save(self, app_key: str, secret_key: str, account_number: str) -> BrokerConnectionStatus:
        """앞뒤 공백과 계좌번호의 '-'는 지운다. 키움에 묻지 않는다."""
        app_key, secret_key = app_key.strip(), secret_key.strip()
        account = re.sub(r"[\s-]", "", account_number)
        if not app_key:
            raise InvalidBrokerCredentialsError("app_key", "앱 키를 입력해 주세요.")
        if not secret_key:
            raise InvalidBrokerCredentialsError("secret_key", "시크릿을 입력해 주세요.")
        if not is_account_number(account):
            raise InvalidBrokerCredentialsError(
                "account_number", "계좌번호는 숫자 10자리입니다('-'는 빼도 됩니다)."
            )
        self._store.save(BrokerCredentials(app_key, secret_key, account))
        return self.status()

    def delete(self) -> None:
        """저장 파일을 쓸 수 없으면 `BrokerCredentialsUnwritableError`가 그대로 올라간다."""
        self._store.delete()

    def verify(self) -> BrokerConnectionResult:
        try:
            credentials = self._store.load()
        except BrokerCredentialsUnreadableError as error:
            logger.warning("kiwoom mock credentials unreadable — %s", error)
            outcome = ConnectionOutcome.UNREADABLE
            return BrokerConnectionResult(outcome, OUTCOME_MESSAGES[outcome], None)
        if credentials is None:
            outcome, detail = ConnectionOutcome.NOT_SAVED, ""
        else:
            check = self._probe.verify(credentials)
            outcome, detail = check.outcome, check.detail
        # 화면에는 정해 둔 문장만 보낸다. 원인(detail)은 운영 로그에만 남긴다
        # (비밀값은 probe 가 지운다).
        logger.log(
            logging.INFO if outcome is ConnectionOutcome.CONNECTED else logging.WARNING,
            "kiwoom mock connection check — outcome=%s account_tail=%s detail=%s",
            outcome.value,
            _tail(credentials),
            detail,
        )
        return BrokerConnectionResult(outcome, OUTCOME_MESSAGES[outcome], _tail(credentials))


def _tail(credentials: BrokerCredentials | None) -> str | None:
    return None if credentials is None else credentials.account_number[-ACCOUNT_TAIL_DIGITS:]
