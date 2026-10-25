"""키움 모의 자격증명을 공급자 비밀과 같은 secrets.json에 둔다(S06 ②-2)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    BROKER_ENTRY,
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
)
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    BrokerCredentialsUnreadableError,
)

CREDENTIALS = BrokerCredentials("fake-app-key-0123", "fake-secret-0123", "5012345678")


def test_round_trip_shares_the_file_with_provider_secrets(tmp_path: Path) -> None:
    secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    secrets.put("profile-1", "sk-not-real")
    broker = LocalFileBrokerCredentialStore(secrets)
    assert broker.load() is None
    broker.save(CREDENTIALS)
    assert broker.load() == CREDENTIALS
    broker.delete()
    broker.delete()
    assert broker.load() is None
    assert secrets.get("profile-1") == "sk-not-real"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한 비트 검사")
def test_file_stays_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "secrets.json"
    LocalFileBrokerCredentialStore(LocalFileProviderSecretStore(path)).save(CREDENTIALS)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_unreadable_entry_fails_without_echoing_values(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({BROKER_ENTRY: '{"app_key": "fake-app-key-0123"}'}))
    with pytest.raises(BrokerCredentialsUnreadableError) as raised:
        LocalFileBrokerCredentialStore(LocalFileProviderSecretStore(path)).load()
    assert "fake-app-key-0123" not in str(raised.value)
