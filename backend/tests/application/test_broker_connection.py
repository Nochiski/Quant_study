"""키움 모의 연결 설정 서비스(S06 ②-2): 저장 형식 검증, 끝 4자리 표시, 결과 문장."""

from __future__ import annotations

import logging

import pytest

from strategy_workbench.application.broker_connection.facade.connection import (
    OUTCOME_MESSAGES,
    BrokerConnectionService,
    BrokerCredentials,
    ConnectionCheck,
    ConnectionOutcome,
    InvalidBrokerCredentialsError,
)

APP_KEY = "fake-app-key-0123456789"
SECRET = "fake-secret-0123456789"


class MemoryStore:
    def __init__(self) -> None:
        self.value: BrokerCredentials | None = None

    def load(self) -> BrokerCredentials | None:
        return self.value

    def save(self, credentials: BrokerCredentials) -> None:
        self.value = credentials

    def delete(self) -> None:
        self.value = None


class RecordingProbe:
    def __init__(
        self, outcome: ConnectionOutcome = ConnectionOutcome.CONNECTED, detail: str = ""
    ) -> None:
        self.outcome = outcome
        self.detail = detail
        self.seen: list[BrokerCredentials] = []

    def verify(self, credentials: BrokerCredentials) -> ConnectionCheck:
        self.seen.append(credentials)
        return ConnectionCheck(self.outcome, self.detail)


def service(probe: RecordingProbe | None = None) -> tuple[BrokerConnectionService, MemoryStore]:
    store = MemoryStore()
    return BrokerConnectionService(store, probe or RecordingProbe()), store


def test_save_normalises_and_shows_only_the_account_tail() -> None:
    connections, store = service()
    assert connections.status().saved is False
    status = connections.save(f" {APP_KEY} ", f"{SECRET}\n", "5012-3456-78")
    assert (status.saved, status.account_tail) == (True, "5678")
    assert store.value == BrokerCredentials(APP_KEY, SECRET, "5012345678")
    assert APP_KEY not in repr(store.value) and "5012345678" not in repr(store.value)


@pytest.mark.parametrize(
    ("app_key", "secret_key", "account", "field"),
    [
        ("", SECRET, "5012345678", "app_key"),
        (APP_KEY, "  ", "5012345678", "secret_key"),
        (APP_KEY, SECRET, "50123", "account_number"),
        (APP_KEY, SECRET, "50123456ab", "account_number"),
    ],
)
def test_invalid_input_is_rejected_without_storing(
    app_key: str, secret_key: str, account: str, field: str
) -> None:
    connections, store = service()
    with pytest.raises(InvalidBrokerCredentialsError) as raised:
        connections.save(app_key, secret_key, account)
    assert raised.value.field == field
    assert store.value is None


def test_verify_without_saved_keys_does_not_call_kiwoom() -> None:
    probe = RecordingProbe()
    connections, _ = service(probe)
    result = connections.verify()
    assert result.outcome == ConnectionOutcome.NOT_SAVED
    assert probe.seen == []


def test_a_failed_check_logs_why_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    # 리뷰 #594 P2: 연결 확인이 실패해도 원인(detail)을 버려 운영자가 단서를 얻지 못했다
    caplog.set_level(logging.INFO)
    why = "no response from kiwoom mock — path=/oauth2/token error=URLError reason=SSLError"
    connections, _ = service(RecordingProbe(ConnectionOutcome.NO_RESPONSE, why))
    connections.save(APP_KEY, SECRET, "50123456-78")
    result = connections.verify()
    assert result.outcome == ConnectionOutcome.NO_RESPONSE and why not in result.message
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "outcome=no_response" in logged and why in logged and "account_tail=5678" in logged
    assert APP_KEY not in logged and SECRET not in logged and "5012345678" not in logged


@pytest.mark.parametrize("outcome", list(ConnectionOutcome))
def test_every_outcome_has_a_korean_message_without_secrets(outcome: ConnectionOutcome) -> None:
    connections, _ = service(RecordingProbe(outcome))
    connections.save(APP_KEY, SECRET, "5012345678")
    result = connections.verify()
    expected = ConnectionOutcome.NOT_SAVED if outcome == ConnectionOutcome.NOT_SAVED else outcome
    assert result.outcome == expected
    assert result.message == OUTCOME_MESSAGES[expected]
    assert result.account_tail == "5678"
    for secret in (APP_KEY, SECRET, "5012345678"):
        assert secret not in result.message


def test_delete_forgets_the_keys() -> None:
    connections, _ = service()
    connections.save(APP_KEY, SECRET, "5012345678")
    connections.delete()
    connections.delete()
    assert connections.status().saved is False
