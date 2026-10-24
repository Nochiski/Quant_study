"""내 문서 `auto_trading/.env` 저장 파일과 AI 공급자·키움 모의 저장 테스트(2026-10-24 사용자 결정).

고정하는 사실:

1. 경로: OS "문서" 폴더 + `auto_trading/.env`(윈도우 알려진 폴더·리눅스 XDG 포함).
2. 파일: 자기 접두어 묶음만 바꾸고 다른 줄(다른 도구의 키·주석·순서)은 글자 그대로 둔다. POSIX 0600,
   만든 폴더 0700, 원자 교체. 따옴표·이스케이프 값이 그대로 돌아온다.
3. 공급자: 프로파일과 키가 같은 묶음에 있고, 서비스 흐름(생성·활성 전환·삭제)이 SQLite 구현과 같다.
4. 이전: 예전 SQLite 프로파일·`secrets.json` 키를 한 번 옮기고 예전 것을 지운다.
5. 예외 문장에 값·경로가 없다.
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    SQLiteProviderProfileRepository,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.env_store import (
    CredentialsEnvFile,
    EnvFileBrokerCredentialStore,
    EnvFileProviderStore,
    parse_env_text,
    resolve_documents_directory,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
    SecretStoreStorageError,
)
from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
)
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    BrokerCredentialsUnreadableError,
)
from strategy_workbench.bootstrap._assistant import AssistantSettings
from strategy_workbench.bootstrap._secrets import (
    migrate_kiwoom_credentials,
    migrate_provider_profiles,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.assistant.facade.models import ProviderKind, ProviderProfile

SECRET = "sk-ant-not-a-real-key-0123456789"
NOW = datetime(2026, 10, 24, 5, 0, tzinfo=UTC)

OTHER_TOOL = """# 다른 자동매매 도구의 키
KIS_APP_KEY=keep-me
export TELEGRAM_TOKEN='abc def'

DART_API_KEY="dart #1"
"""


def _profile(
    profile_id: str, *, active: bool = False, base_url: str | None = None
) -> ProviderProfile:
    return ProviderProfile(
        profile_id=profile_id,
        kind=ProviderKind.ANTHROPIC,
        label=f'내 "Claude" {profile_id}',
        model="claude-test",
        base_url=base_url,
        created_at=NOW,
        active=active,
    )


# -- 경로 ------------------------------------------------------------------------------------


def test_documents_directory_follows_each_os_rule(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert resolve_documents_directory(platform="darwin", home=home) == home / "Documents"
    assert resolve_documents_directory(
        platform="win32", home=home, windows_documents="D:\\OneDrive\\문서"
    ) == Path("D:\\OneDrive\\문서")
    assert resolve_documents_directory(platform="win32", home=home) == home / "Documents"
    assert (
        resolve_documents_directory(platform="linux", home=home, xdg_documents="$HOME/문서")
        == home / "문서"
    )
    # XDG가 문서 폴더를 끄면 홈을 돌려준다. 홈 바로 밑에 키를 두지 않는다.
    assert (
        resolve_documents_directory(platform="linux", home=home, xdg_documents="$HOME")
        == home / "Documents"
    )


# -- 파일 ------------------------------------------------------------------------------------


def test_a_group_write_keeps_other_tools_lines_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "auto_trading" / ".env"
    path.parent.mkdir()
    path.write_text(OTHER_TOOL, encoding="utf-8")
    file = CredentialsEnvFile(path)

    file.replace_group("APP_", "앱 묶음", (("APP_KEY", "v1"), ("APP_NOTE", 'a "b" #c\nd')))
    first = path.read_text(encoding="utf-8")
    assert first.startswith(OTHER_TOOL)
    assert file.group("APP_") == {"APP_KEY": "v1", "APP_NOTE": 'a "b" #c\nd'}

    file.replace_group("APP_", "앱 묶음", (("APP_KEY", "v2"),))
    second = path.read_text(encoding="utf-8")
    assert second.startswith(OTHER_TOOL)
    assert second.count("# 앱 묶음") == 1
    values = file.read()
    assert values["APP_KEY"] == "v2"
    assert "APP_NOTE" not in values
    assert values["KIS_APP_KEY"] == "keep-me"
    assert values["TELEGRAM_TOKEN"] == "abc def"
    assert values["DART_API_KEY"] == "dart #1"

    file.replace_group("APP_", "앱 묶음", ())
    assert path.read_text(encoding="utf-8").rstrip("\n") == OTHER_TOOL.rstrip("\n")


def test_the_group_stays_where_it_was(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\n# 앱 묶음\nAPP_KEY=old\nB=2\n", encoding="utf-8")

    CredentialsEnvFile(path).replace_group("APP_", "앱 묶음", (("APP_KEY", "new"),))

    assert path.read_text(encoding="utf-8") == "A=1\n# 앱 묶음\nAPP_KEY=new\nB=2\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한 비트")
def test_the_file_is_private_and_a_created_folder_is_too(tmp_path: Path) -> None:
    path = tmp_path / "Documents" / "auto_trading" / ".env"

    CredentialsEnvFile(path).replace_group("APP_", "앱 묶음", (("APP_KEY", "v"),))

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    # 임시 파일이 남지 않는다.
    assert sorted(item.name for item in path.parent.iterdir()) == [".env"]


def test_a_path_inside_the_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        CredentialsEnvFile(tmp_path / "repo" / ".env", forbidden_roots=(tmp_path / "repo",))


def test_parse_reads_export_quotes_and_trailing_comments() -> None:
    assert parse_env_text(
        'export A=\'x y\'\nB="q\\"z\\\\"\nC=plain # memo\n# D=no\nnot a line\n'
    ) == {"A": "x y", "B": 'q"z\\', "C": "plain"}


# -- 공급자 저장 ------------------------------------------------------------------------------


def test_profiles_and_keys_round_trip_through_one_group(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(OTHER_TOOL, encoding="utf-8")
    store = EnvFileProviderStore(CredentialsEnvFile(path))
    secrets = store.secrets()

    store.add(_profile("p1", active=True, base_url="https://proxy.example.com/v1"))
    secrets.put("p1", SECRET)
    store.add(_profile("p2"))

    assert [item.profile_id for item in store.list()] == ["p1", "p2"]
    assert store.get("p1") == _profile("p1", active=True, base_url="https://proxy.example.com/v1")
    assert secrets.get("p1") == SECRET
    with pytest.raises(ProviderSecretMissingError):
        secrets.get("p2")

    store.set_active("p2")
    assert [item.active for item in store.list()] == [False, True]

    # 서비스의 삭제 순서: 키 먼저(멱등), 그다음 프로파일.
    secrets.delete("p1")
    secrets.delete("p1")
    store.delete("p1")
    assert [item.profile_id for item in store.list()] == ["p2"]
    with pytest.raises(ProviderProfileNotFoundError):
        store.get("p1")
    assert path.read_text(encoding="utf-8").startswith(OTHER_TOOL)
    assert SECRET not in path.read_text(encoding="utf-8")


def test_a_second_active_profile_is_refused(tmp_path: Path) -> None:
    store = EnvFileProviderStore(CredentialsEnvFile(tmp_path / ".env"))
    store.add(_profile("p1", active=True))

    with pytest.raises(SecretStoreStorageError, match="already active"):
        store.add(_profile("p2", active=True))


def test_an_incomplete_hand_edit_is_reported_without_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(f"STRATEGY_WORKBENCH_AI_1_ID=p1\nSTRATEGY_WORKBENCH_AI_1_API_KEY={SECRET}\n")
    store = EnvFileProviderStore(CredentialsEnvFile(path))

    with pytest.raises(SecretStoreStorageError) as raised:
        store.list()

    message = str(raised.value)
    assert "missing=['KIND', 'MODEL', 'CREATED_AT']" in message
    assert SECRET not in message
    assert str(tmp_path) not in message


# -- 이전 ------------------------------------------------------------------------------------


def test_legacy_profiles_and_keys_move_once_and_the_old_copies_are_removed(
    tmp_path: Path,
) -> None:
    database = AssistantDatabase(tmp_path / "assistant.sqlite3")
    legacy_profiles = SQLiteProviderProfileRepository(database)
    legacy_secrets = LocalFileProviderSecretStore(tmp_path / "config" / "secrets.json")
    legacy_profiles.add(_profile("p1", active=True))
    legacy_profiles.add(_profile("p2"))
    legacy_secrets.put("p1", SECRET)
    legacy_secrets.put("broker.kiwoom_mock", "{}")
    target = EnvFileProviderStore(CredentialsEnvFile(tmp_path / "auto_trading" / ".env"))

    assert migrate_provider_profiles(legacy_profiles, legacy_secrets, target) == 2

    assert [item.profile_id for item in target.list()] == ["p1", "p2"]
    assert target.get("p1").active is True
    assert target.secrets().get("p1") == SECRET
    assert legacy_profiles.list() == ()
    with pytest.raises(ProviderSecretMissingError):
        legacy_secrets.get("p1")
    # 공급자 것만 옮긴다. 키움 항목은 그대로다.
    assert legacy_secrets.get("broker.kiwoom_mock") == "{}"
    # 다시 불러도 아무것도 하지 않는다.
    assert migrate_provider_profiles(legacy_profiles, legacy_secrets, target) == 0


def test_migration_skips_when_the_new_file_already_has_providers(tmp_path: Path) -> None:
    database = AssistantDatabase(tmp_path / "assistant.sqlite3")
    legacy_profiles = SQLiteProviderProfileRepository(database)
    legacy_secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    legacy_profiles.add(_profile("old", active=True))
    target = EnvFileProviderStore(CredentialsEnvFile(tmp_path / ".env"))
    target.add(_profile("new", active=True))

    assert migrate_provider_profiles(legacy_profiles, legacy_secrets, target) == 0
    assert [item.profile_id for item in legacy_profiles.list()] == ["old"]


def test_unreadable_legacy_keys_leave_everything_in_place(tmp_path: Path) -> None:
    database = AssistantDatabase(tmp_path / "assistant.sqlite3")
    legacy_profiles = SQLiteProviderProfileRepository(database)
    broken = tmp_path / "secrets.json"
    broken.write_text("not json", encoding="utf-8")
    legacy_profiles.add(_profile("p1", active=True))
    target = EnvFileProviderStore(CredentialsEnvFile(tmp_path / ".env"))

    assert (
        migrate_provider_profiles(legacy_profiles, LocalFileProviderSecretStore(broken), target)
        == 0
    )
    assert [item.profile_id for item in legacy_profiles.list()] == ["p1"]
    assert target.list() == ()


# -- 조립·화면 경로 ------------------------------------------------------------------------------


def test_the_server_stores_providers_in_the_env_file_and_reports_its_path(tmp_path: Path) -> None:
    env_path = tmp_path / "Documents" / "auto_trading" / ".env"
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            credentials_env_path=env_path,
        )
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/settings/credentials-file").json()
    assert body == {"path": str(env_path.resolve())}


def test_a_graph_without_the_env_file_reports_no_path(tmp_path: Path) -> None:
    app = build_http_app(
        assistant=AssistantSettings(db_path=None, secrets_path=tmp_path / "secrets.json")
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/settings/credentials-file").json() == {"path": None}


# -- 키움 모의 연결 -----------------------------------------------------------------------------


def test_kiwoom_credentials_round_trip_as_three_names(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(OTHER_TOOL, encoding="utf-8")
    file = CredentialsEnvFile(path)
    store = EnvFileBrokerCredentialStore(file)
    assert store.load() is None

    store.save(BrokerCredentials("app-key-0123", "secret-0123", "5012345678"))

    assert store.load() == BrokerCredentials("app-key-0123", "secret-0123", "5012345678")
    assert file.group("STRATEGY_WORKBENCH_KIWOOM_MOCK_") == {
        "STRATEGY_WORKBENCH_KIWOOM_MOCK_APP_KEY": "app-key-0123",
        "STRATEGY_WORKBENCH_KIWOOM_MOCK_SECRET_KEY": "secret-0123",
        "STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT": "5012345678",
    }
    # 공급자 묶음과 같은 파일을 나눠 쓴다. 서로의 줄을 지우지 않는다.
    EnvFileProviderStore(file).add(_profile("p1", active=True))
    assert store.load() is not None

    store.delete()
    store.delete()
    assert store.load() is None
    assert [item.profile_id for item in EnvFileProviderStore(file).list()] == ["p1"]
    assert path.read_text(encoding="utf-8").startswith(OTHER_TOOL)


def test_partial_kiwoom_names_are_unreadable_without_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("STRATEGY_WORKBENCH_KIWOOM_MOCK_APP_KEY=app-key-0123\n", encoding="utf-8")

    with pytest.raises(BrokerCredentialsUnreadableError) as raised:
        EnvFileBrokerCredentialStore(CredentialsEnvFile(path)).load()

    assert "app-key-0123" not in str(raised.value)
    assert "STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT" in str(raised.value)


def test_legacy_kiwoom_credentials_move_once(tmp_path: Path) -> None:
    secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    secrets.put("p-other", SECRET)
    legacy = LocalFileBrokerCredentialStore(secrets)
    legacy.save(BrokerCredentials("app-key-0123", "secret-0123", "5012345678"))
    target = EnvFileBrokerCredentialStore(CredentialsEnvFile(tmp_path / ".env"))

    assert migrate_kiwoom_credentials(legacy, target) is True

    assert target.load() == BrokerCredentials("app-key-0123", "secret-0123", "5012345678")
    assert legacy.load() is None
    # 같은 파일의 다른 항목은 그대로다.
    assert secrets.get("p-other") == SECRET
    assert migrate_kiwoom_credentials(legacy, target) is False


def test_broken_legacy_kiwoom_entry_is_left_in_place(tmp_path: Path) -> None:
    secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    secrets.put("broker.kiwoom_mock", "not json")
    target = EnvFileBrokerCredentialStore(CredentialsEnvFile(tmp_path / ".env"))

    assert migrate_kiwoom_credentials(LocalFileBrokerCredentialStore(secrets), target) is False
    assert secrets.get("broker.kiwoom_mock") == "not json"
    assert target.load() is None


def test_the_settings_screen_saves_kiwoom_keys_into_the_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / "Documents" / "auto_trading" / ".env"
    app = build_http_app(
        assistant=AssistantSettings(
            db_path=None,
            secrets_path=tmp_path / "secrets.json",
            credentials_env_path=env_path,
        )
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/broker-connections/kiwoom-mock",
            json={
                "app_key": "app-key-0123456789",
                "secret_key": "secret-0123456789",
                "account_number": "50123456-78",
            },
        )
    assert response.status_code == 200, response.text
    values = parse_env_text(env_path.read_text(encoding="utf-8"))
    assert values["STRATEGY_WORKBENCH_KIWOOM_MOCK_APP_KEY"] == "app-key-0123456789"
    assert not (tmp_path / "secrets.json").exists()
