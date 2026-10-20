"""키움 모의 자격증명을 공급자 비밀과 같은 `secrets.json`에 한 항목으로 둔다(S06 ②-2).

같은 파일을 쓰므로 `LocalFileProviderSecretStore` 인스턴스 하나를 공유해야 그 잠금이 동시 쓰기를
막는다. 세 값을 JSON 하나로 묶어 한 번에 바꾼다 — 따로 쓰면 중간 실패 때 키와 계좌가 엇갈린다.
"""

from __future__ import annotations

import json

from strategy_workbench.adapters.outbound.secrets_local._errors import SecretStoreStorageError
from strategy_workbench.adapters.outbound.secrets_local._store import (
    LocalFileProviderSecretStore,
)
from strategy_workbench.application.assistant_chat.facade.ports import ProviderSecretMissingError
from strategy_workbench.application.broker_connection.facade.connection import BrokerCredentials

BROKER_ENTRY = "broker.kiwoom_mock"


class LocalFileBrokerCredentialStore:
    def __init__(self, store: LocalFileProviderSecretStore) -> None:
        self._store = store

    def load(self) -> BrokerCredentials | None:
        try:
            raw = self._store.get(BROKER_ENTRY)
        except ProviderSecretMissingError:
            return None
        try:
            data = json.loads(raw)
            return BrokerCredentials(
                str(data["app_key"]), str(data["secret_key"]), str(data["account_number"])
            )
        except (ValueError, TypeError, KeyError) as error:
            # 값은 싣지 않는다. 항목 이름과 깨진 모양만 알린다.
            raise SecretStoreStorageError(
                f"stored broker credentials are unreadable — entry={BROKER_ENTRY} "
                f"error={type(error).__name__}"
            ) from error

    def save(self, credentials: BrokerCredentials) -> None:
        self._store.put(
            BROKER_ENTRY,
            json.dumps(
                {
                    "app_key": credentials.app_key,
                    "secret_key": credentials.secret_key,
                    "account_number": credentials.account_number,
                }
            ),
        )

    def delete(self) -> None:
        self._store.delete(BROKER_ENTRY)
