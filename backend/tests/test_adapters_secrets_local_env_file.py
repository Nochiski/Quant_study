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

import contextlib
import errno
import os
import stat
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    AssistantStorageError,
    SQLiteProviderProfileRepository,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.env_store import (
    CredentialsEnvFile,
    EnvFileBrokerCredentialStore,
    EnvFileProviderStore,
    EnvProblem,
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
    ProviderStorageUnavailableError,
)
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    BrokerCredentialsUnreadableError,
)
from strategy_workbench.bootstrap._assistant import (
    AssistantSettings,
    scripted_provider_factories,
)
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
    # 임시 파일이 남지 않는다. 옆의 `.env.lock`은 프로세스 간 잠금 파일이다(리뷰 4).
    assert sorted(item.name for item in path.parent.iterdir()) == [".env", ".env.lock"]
    assert stat.S_IMODE(os.stat(path.parent / ".env.lock").st_mode) == 0o600


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


def test_an_incomplete_hand_edit_is_skipped_reported_and_kept(tmp_path: Path) -> None:
    """빠진 줄이 있는 항목은 목록에서 빠지고 사유만 알려진다(리뷰 1).

    다음 쓰기에도 값 그대로 남는다.
    """
    path = tmp_path / ".env"
    path.write_text(
        f"STRATEGY_WORKBENCH_AI_1_ID=p1\nSTRATEGY_WORKBENCH_AI_1_API_KEY={SECRET}\n",
        encoding="utf-8",
    )
    store = EnvFileProviderStore(CredentialsEnvFile(path))

    assert store.list() == ()
    assert store.problems() == (
        EnvProblem(
            "ai",
            "incomplete",
            1,
            (
                "STRATEGY_WORKBENCH_AI_1_KIND",
                "STRATEGY_WORKBENCH_AI_1_MODEL",
                "STRATEGY_WORKBENCH_AI_1_CREATED_AT",
            ),
        ),
    )

    store.add(_profile("p2", active=True))

    assert [item.profile_id for item in store.list()] == ["p2"]
    values = parse_env_text(path.read_text(encoding="utf-8"))
    # 읽지 못한 항목은 번호만 새로 받아 값 그대로 남는다.
    assert values["STRATEGY_WORKBENCH_AI_2_ID"] == "p1"
    assert values["STRATEGY_WORKBENCH_AI_2_API_KEY"] == SECRET


def test_unknown_lines_in_our_group_survive_a_write(tmp_path: Path) -> None:
    """우리 접두어 안의 모르는 줄은 다음 쓰기에도 남는다(SoT 리뷰 M4)."""
    path = tmp_path / ".env"
    store = EnvFileProviderStore(CredentialsEnvFile(path))
    store.add(_profile("p1", active=True))
    with path.open("a", encoding="utf-8") as stream:
        stream.write("STRATEGY_WORKBENCH_AI_1_NOTE=내 메모\nSTRATEGY_WORKBENCH_AI_THEME=dark\n")

    store.add(_profile("p2"))

    values = parse_env_text(path.read_text(encoding="utf-8"))
    assert values["STRATEGY_WORKBENCH_AI_1_NOTE"] == "내 메모"
    assert values["STRATEGY_WORKBENCH_AI_THEME"] == "dark"
    assert [item.profile_id for item in store.list()] == ["p1", "p2"]


def test_a_non_utf8_file_reads_as_no_providers_and_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    original = b"KIS_APP_KEY=\xff\xfe\n"
    path.write_bytes(original)
    file = CredentialsEnvFile(path)
    store = EnvFileProviderStore(file)

    assert store.list() == ()
    assert file.file_problem() == EnvProblem("file", "not_utf8")
    with pytest.raises(ProviderStorageUnavailableError):
        store.add(_profile("p1", active=True))
    assert path.read_bytes() == original


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
    assert body == {"path": str(env_path.resolve()), "problems": []}


def test_a_graph_without_the_env_file_reports_no_path(tmp_path: Path) -> None:
    app = build_http_app(
        assistant=AssistantSettings(db_path=None, secrets_path=tmp_path / "secrets.json")
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/settings/credentials-file").json() == {
            "path": None,
            "problems": [],
        }


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


# -- 코드 리뷰 고침 ------------------------------------------------------------------------------


def _settings(tmp_path: Path, env_path: Path) -> AssistantSettings:
    return AssistantSettings(
        db_path=tmp_path / "assistant.sqlite3",
        secrets_path=tmp_path / "secrets.json",
        credentials_env_path=env_path,
    )


def test_a_broken_file_does_not_stop_the_server_and_the_screen_says_why(tmp_path: Path) -> None:
    """리뷰 1·2: 깨진 `.env`에도 서버가 뜨고, 공급자는 없음, 키움은 읽을 수 없음, 사유가 보인다."""
    env_path = tmp_path / "auto_trading" / ".env"
    env_path.parent.mkdir()
    env_path.write_bytes(b"\xff\xfeSTRATEGY_WORKBENCH_AI_1_API_KEY=" + SECRET.encode() + b"\n")
    legacy = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    LocalFileBrokerCredentialStore(legacy).save(
        BrokerCredentials("app-key-0123", "secret-0123", "5012345678")
    )

    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        providers = client.get("/api/v1/assistant/providers").json()
        kiwoom = client.get("/api/v1/broker-connections/kiwoom-mock").json()
        body = client.get("/api/v1/settings/credentials-file").json()

    assert providers["profiles"] == []
    assert kiwoom["unreadable"] is True
    assert body["problems"] == [
        {
            "section": "file",
            "reason": "not_utf8",
            "fields": [],
            "message": "저장 파일을 UTF-8로 읽지 못했습니다. 편집기에서 UTF-8로 다시 저장하세요.",
        }
    ]
    assert SECRET not in str(body)
    # 깨진 파일 위로 이전하지 않는다. 예전 키는 그대로 남는다.
    assert LocalFileBrokerCredentialStore(legacy).load() is not None


def test_an_incomplete_kiwoom_group_is_reported_by_name(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("STRATEGY_WORKBENCH_KIWOOM_MOCK_APP_KEY=app-key-0123\n", encoding="utf-8")

    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        body = client.get("/api/v1/settings/credentials-file").json()

    assert [(item["section"], item["fields"]) for item in body["problems"]] == [
        (
            "kiwoom",
            [
                "STRATEGY_WORKBENCH_KIWOOM_MOCK_SECRET_KEY",
                "STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT",
            ],
        )
    ]
    assert "app-key-0123" not in str(body)


def test_providers_deleted_after_the_move_do_not_come_back_from_another_checkout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """리뷰 3·SoT M2/M3: 한 번 옮긴 뒤에는 어느 체크아웃의 예전 DB도 다시 읽지 않는다.

    남은 것은 개수만 경고한다.
    """
    secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    first = SQLiteProviderProfileRepository(AssistantDatabase(tmp_path / "a.sqlite3"))
    second = SQLiteProviderProfileRepository(AssistantDatabase(tmp_path / "b.sqlite3"))
    first.add(_profile("p1", active=True))
    secrets.put("p1", SECRET)
    second.add(_profile("p2", active=True))
    secrets.put("p2", "sk-other-0123456789")
    file = CredentialsEnvFile(tmp_path / "auto_trading" / ".env")
    target = EnvFileProviderStore(file)

    assert migrate_provider_profiles(first, secrets, target) == 1
    target.secrets().delete("p1")
    target.delete("p1")
    assert target.list() == ()

    with caplog.at_level("WARNING"):
        assert migrate_provider_profiles(second, secrets, target) == 0

    assert target.list() == ()
    # 다른 체크아웃의 예전 것은 지우지 않는다(되돌릴 수 없으니). 개수만 남긴다.
    assert [item.profile_id for item in second.list()] == ["p2"]
    assert "profiles=1 keys=1" in caplog.text
    assert "p2" not in caplog.text
    assert "sk-other" not in caplog.text


def test_kiwoom_keys_written_again_by_an_old_server_do_not_come_back(tmp_path: Path) -> None:
    secrets = LocalFileProviderSecretStore(tmp_path / "secrets.json")
    legacy = LocalFileBrokerCredentialStore(secrets)
    legacy.save(BrokerCredentials("app-key-0123", "secret-0123", "5012345678"))
    target = EnvFileBrokerCredentialStore(CredentialsEnvFile(tmp_path / ".env"))
    assert migrate_kiwoom_credentials(legacy, target) is True
    target.delete()
    # 옛 버전 서버가 예전 파일에 다시 썼다.
    legacy.save(BrokerCredentials("app-key-0123", "secret-0123", "5012345678"))

    assert migrate_kiwoom_credentials(legacy, target) is False
    assert target.load() is None


def test_two_writers_on_separate_handles_do_not_lose_each_others_lines(tmp_path: Path) -> None:
    """리뷰 4: 다른 객체가 동시에 써도 둘 다 남는다.

    다른 프로세스와 같은 조건이다 — 둘을 막는 것은 OS 파일 잠금뿐이다.
    """
    path = tmp_path / ".env"
    left = CredentialsEnvFile(path)
    right = CredentialsEnvFile(path)

    def write(file: CredentialsEnvFile, prefix: str) -> None:
        for number in range(30):

            def step(number: int = number) -> None:
                current = dict(file.group(prefix))
                current[f"{prefix}{number}"] = str(number)
                file.replace_group(prefix, prefix, sorted(current.items()))

            file.update(step)

    threads = [
        threading.Thread(target=write, args=(left, "LEFT_")),
        threading.Thread(target=write, args=(right, "RIGHT_")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    values = CredentialsEnvFile(path).read()
    assert len([name for name in values if name.startswith("LEFT_")]) == 30
    assert len([name for name in values if name.startswith("RIGHT_")]) == 30


def test_crlf_and_bom_are_kept_and_the_first_line_is_read(tmp_path: Path) -> None:
    """리뷰 5: BOM을 떼고 읽어 첫 줄 이름을 알아보고, 쓸 때 BOM·CRLF를 그대로 둔다."""
    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbfAPP_KEY=old\r\nKIS_APP_KEY=keep\r\n")
    file = CredentialsEnvFile(path)
    assert file.read() == {"APP_KEY": "old", "KIS_APP_KEY": "keep"}

    file.replace_group("APP_", "앱 묶음", (("APP_KEY", "new"),))

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"APP_KEY=") == 2  # 묶음이 겹치지 않는다(APP_KEY 하나 + KIS_APP_KEY 하나).
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert file.read() == {"APP_KEY": "new", "KIS_APP_KEY": "keep"}


def test_deleting_from_a_missing_file_creates_nothing(tmp_path: Path) -> None:
    """리뷰 6: 지울 게 없으면 폴더도 파일도 잠금 파일도 만들지 않는다.

    같은 내용이면 다시 쓰지 않는다.
    """
    folder = tmp_path / "auto_trading"
    file = CredentialsEnvFile(folder / ".env")
    EnvFileBrokerCredentialStore(file).delete()
    EnvFileProviderStore(file).secrets().delete("p1")
    assert not folder.exists()

    EnvFileBrokerCredentialStore(file).save(BrokerCredentials("a-0123", "s-0123", "5012345678"))
    before = (folder / ".env").stat()
    EnvFileBrokerCredentialStore(file).save(BrokerCredentials("a-0123", "s-0123", "5012345678"))
    after = (folder / ".env").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_listing_with_keys_reads_the_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리뷰 9: 목록과 각 키 조회가 파일이 그대로면 다시 읽지 않는다."""
    path = tmp_path / ".env"
    store = EnvFileProviderStore(CredentialsEnvFile(path))
    store.add(_profile("p1", active=True))
    store.secrets().put("p1", SECRET)
    store.add(_profile("p2"))
    reads: list[Path] = []
    original = Path.read_bytes

    def counting(self: Path) -> bytes:
        reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    for profile in store.list():
        with contextlib.suppress(ProviderSecretMissingError):
            store.secrets().get(profile.profile_id)

    assert len(reads) <= 1


# -- 재리뷰 고침 --------------------------------------------------------------------------------


def test_first_writes_racing_on_a_missing_folder_keep_both_profiles(tmp_path: Path) -> None:
    """3차 리뷰 2: 폴더가 없을 때 동시에 시작한 첫 쓰기끼리도 서로를 지우지 않는다.

    `update`가 들어가자마자 OS 잠금을 잡으므로(필요하면 폴더를 만든다) 두 번째 쓰기는 첫 쓰기가
    남긴 내용을 읽고 계산한다. 다른 객체 둘은 다른 프로세스와 같은 조건이다.
    """
    for attempt in range(10):
        path = tmp_path / f"run-{attempt}" / "auto_trading" / ".env"
        stores = [EnvFileProviderStore(CredentialsEnvFile(path)) for _ in range(2)]
        barrier = threading.Barrier(2)

        def add(
            store: EnvFileProviderStore,
            profile_id: str,
            barrier: threading.Barrier = barrier,
        ) -> None:
            barrier.wait()
            store.add(_profile(profile_id))

        threads = [
            threading.Thread(target=add, args=(stores[0], "p1")),
            threading.Thread(target=add, args=(stores[1], "p2")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        ids = sorted(
            item.profile_id for item in EnvFileProviderStore(CredentialsEnvFile(path)).list()
        )
        assert ids == ["p1", "p2"]


def test_nested_updates_share_one_lock_and_see_the_outer_decision(tmp_path: Path) -> None:
    """3차 리뷰 2: 바깥 `update` 안의 쓰기는 같은 잠금을 다시 들어가 바깥 결정과 어긋나지 않는다."""
    path = tmp_path / "auto_trading" / ".env"
    file = CredentialsEnvFile(path)
    store = EnvFileProviderStore(file)

    def outer() -> None:
        store.add(_profile("p1", active=True))
        store.secrets().put("p1", SECRET)
        assert [item.profile_id for item in store.list()] == ["p1"]

    file.update(outer)

    assert store.secrets().get("p1") == SECRET


def test_an_active_marker_on_a_broken_entry_is_kept(tmp_path: Path) -> None:
    """재리뷰 2: `ACTIVE`가 읽지 못한 항목을 가리키면 다음 쓰기에도 그대로 남는다.

    새 항목은 비활성으로 들어간다.
    """
    path = tmp_path / ".env"
    path.write_text(
        "STRATEGY_WORKBENCH_AI_ACTIVE=b1\n"
        "STRATEGY_WORKBENCH_AI_1_ID=b1\n"
        "STRATEGY_WORKBENCH_AI_1_KIND=anthropic\n",
        encoding="utf-8",
    )
    store = EnvFileProviderStore(CredentialsEnvFile(path))

    store.add(_profile("p2", active=True))

    values = parse_env_text(path.read_text(encoding="utf-8"))
    assert values["STRATEGY_WORKBENCH_AI_ACTIVE"] == "b1"
    assert [item.active for item in store.list()] == [False]

    store.set_active("p2")
    assert parse_env_text(path.read_text(encoding="utf-8"))["STRATEGY_WORKBENCH_AI_ACTIVE"] == "p2"


def test_values_with_unicode_line_breaks_round_trip_on_one_line(tmp_path: Path) -> None:
    """재리뷰 3: `\\x0b`·`\\x85`·`\\u2028` 같은 글자가 있어도 한 줄로 쓰고 같은 값으로 읽는다."""
    path = tmp_path / ".env"
    path.write_text("OTHER=keep\n", encoding="utf-8")
    file = CredentialsEnvFile(path)
    value = "a\x0bb\x0cc\x1cd\x85e\u2028f\u2029g"

    file.replace_group("APP_", "앱 묶음", (("APP_NOTE", value),))

    text = path.read_text(encoding="utf-8")
    assert all(char not in text for char in "\x0b\x0c\x1c\x85\u2028\u2029")
    assert CredentialsEnvFile(path).read() == {"OTHER": "keep", "APP_NOTE": value}


def test_extra_lines_in_the_kiwoom_group_survive_save_and_delete(tmp_path: Path) -> None:
    """재리뷰 5: 키움 접두어 안의 다른 줄은 저장·삭제에도 값 그대로 남는다."""
    path = tmp_path / ".env"
    path.write_text("STRATEGY_WORKBENCH_KIWOOM_MOCK_NOTE=내 모의 계좌\n", encoding="utf-8")
    store = EnvFileBrokerCredentialStore(CredentialsEnvFile(path))

    store.save(BrokerCredentials("app-key-0123", "secret-0123", "5012345678"))
    assert (
        parse_env_text(path.read_text(encoding="utf-8"))["STRATEGY_WORKBENCH_KIWOOM_MOCK_NOTE"]
        == "내 모의 계좌"
    )

    store.delete()
    values = parse_env_text(path.read_text(encoding="utf-8"))
    assert values == {"STRATEGY_WORKBENCH_KIWOOM_MOCK_NOTE": "내 모의 계좌"}
    assert store.load() is None


class _FakeMsvcrt:
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self, errors: list[int]) -> None:
        self._errors = errors
        self.calls = 0

    def locking(self, descriptor: int, mode: int, size: int) -> None:
        del descriptor, size
        if mode == self.LK_LOCK:
            self.calls += 1
            if self._errors:
                code = self._errors.pop(0)
                raise OSError(code, os.strerror(code))


def _windows_file(tmp_path: Path) -> CredentialsEnvFile:
    def runner(command: object) -> None:
        del command

    path = tmp_path / ".env"
    path.write_text("A=1\n", encoding="utf-8")
    return CredentialsEnvFile(path, platform="win32", run_command=runner, windows_account="me")


def test_windows_lock_retries_only_while_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """재리뷰 7: 남이 잡고 있다(EDEADLOCK·EACCES)면 물러나며 다시, 상한이 있다."""
    busy = getattr(errno, "EDEADLOCK", errno.EDEADLK)
    fake = _FakeMsvcrt([busy, errno.EACCES])
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    _windows_file(tmp_path).replace_group("APP_", "앱 묶음", (("APP_KEY", "v"),))

    assert fake.calls == 3


def test_windows_lock_gives_up_with_the_errno_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "msvcrt", _FakeMsvcrt([errno.EBADF]))
    with pytest.raises(SecretStoreStorageError, match="errno=EBADF"):
        _windows_file(tmp_path).replace_group("APP_", "앱 묶음", (("APP_KEY", "v"),))

    busy = getattr(errno, "EDEADLOCK", errno.EDEADLK)
    monkeypatch.setitem(sys.modules, "msvcrt", _FakeMsvcrt([busy] * 100))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(SecretStoreStorageError, match="reason=busy"):
        _windows_file(tmp_path).replace_group("APP_", "앱 묶음", (("APP_KEY", "v"),))


def test_an_in_place_edit_on_a_coarse_clock_is_not_missed(tmp_path: Path) -> None:
    """재리뷰 8: 수정 시각이 초 단위로 거친 파일 시스템에서도 제자리 수정을 놓치지 않는다."""
    path = tmp_path / ".env"
    path.write_text("A=1\n", encoding="utf-8")
    whole_second = 1_700_000_000 * 1_000_000_000
    os.utime(path, ns=(whole_second, whole_second))
    file = CredentialsEnvFile(path)
    assert file.read() == {"A": "1"}

    with path.open("r+", encoding="utf-8") as stream:
        stream.write("A=2\n")
    os.utime(path, ns=(whole_second, whole_second))

    assert file.read() == {"A": "2"}


def test_unknown_kind_message_lists_the_supported_kinds(tmp_path: Path) -> None:
    """SoT 4: 고칠 값 목록은 지원하는 공급자 종류에서 만든다."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "STRATEGY_WORKBENCH_AI_1_ID=p1\n"
        "STRATEGY_WORKBENCH_AI_1_KIND=gemini\n"
        "STRATEGY_WORKBENCH_AI_1_MODEL=m\n"
        "STRATEGY_WORKBENCH_AI_1_CREATED_AT=2026-10-24T05:00:00+00:00\n",
        encoding="utf-8",
    )
    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        body = client.get("/api/v1/settings/credentials-file").json()

    kinds = " 또는 ".join(kind.value for kind in ProviderKind)
    assert body["problems"][0]["message"].startswith(
        f"AI 공급자 1번 항목의 종류를 알 수 없습니다. {kinds}로 고치세요"
    )


# -- 3차 리뷰 고침 ------------------------------------------------------------------------------


def _scripted_settings(tmp_path: Path, env_path: Path) -> AssistantSettings:
    return AssistantSettings(
        db_path=tmp_path / "assistant.sqlite3",
        secrets_path=tmp_path / "secrets.json",
        credentials_env_path=env_path,
        provider_factories=scripted_provider_factories(),
    )


def test_a_broken_legacy_database_does_not_stop_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3차 리뷰 1: 예전 DB·키 파일을 읽다 무엇이 실패해도 이전만 건너뛰고 서버는 뜬다."""

    def broken(self: SQLiteProviderProfileRepository) -> tuple[ProviderProfile, ...]:
        del self
        raise AssistantStorageError("stored provider profile has an unknown kind — kind='x'")

    def broken_broker(self: LocalFileBrokerCredentialStore) -> BrokerCredentials | None:
        del self
        raise RuntimeError("legacy store exploded")

    monkeypatch.setattr(SQLiteProviderProfileRepository, "list", broken)
    monkeypatch.setattr(LocalFileBrokerCredentialStore, "load", broken_broker)
    env_path = tmp_path / "auto_trading" / ".env"

    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        assert client.get("/api/v1/assistant/providers").status_code == 200
        assert client.get("/api/v1/broker-connections/kiwoom-mock").status_code == 200


def test_starting_with_nothing_to_move_does_not_create_the_folder(tmp_path: Path) -> None:
    """옮길 것이 없으면 기동이 문서 폴더에 아무것도 만들지 않는다(리뷰 6 유지)."""
    env_path = tmp_path / "Documents" / "auto_trading" / ".env"

    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        client.get("/api/v1/settings/credentials-file")

    assert not env_path.parent.exists()


def test_kiwoom_save_on_an_unreadable_file_says_what_to_do(tmp_path: Path) -> None:
    """3차 리뷰 3: 파일 전체를 못 읽으면 저장은 500이 아니라 고칠 방법을 말하는 422다."""
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"\xff\xfe\n")
    app = build_http_app(assistant=_settings(tmp_path, env_path))
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/broker-connections/kiwoom-mock",
            json={
                "app_key": "app-key-0123456789",
                "secret_key": "secret-0123456789",
                "account_number": "5012345678",
            },
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "broker.storage_unavailable"
    assert "파일을 고치거나 지운 뒤" in detail["message"]
    assert "app-key-0123456789" not in response.text
    assert env_path.read_bytes() == b"\xff\xfe\n"


def test_provider_create_on_an_unreadable_file_is_refused_before_the_probe(
    tmp_path: Path,
) -> None:
    """3차 리뷰 4: 쓸 수 없는 파일이면 연결 테스트 전에 코드 있는 422로 거절한다."""
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"\xff\xfe\n")
    app = build_http_app(assistant=_scripted_settings(tmp_path, env_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/providers",
            json={"kind": "anthropic", "label": "내 Claude", "secret": SECRET},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "assistant.storage_unavailable"
    assert SECRET not in response.text


def test_create_reports_what_was_stored_when_the_active_marker_is_on_a_broken_entry(
    tmp_path: Path,
) -> None:
    """3차 리뷰 5: 저장소가 새 항목을 비활성으로 넣었으면 응답도 비활성이다."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "STRATEGY_WORKBENCH_AI_ACTIVE=b1\n"
        "STRATEGY_WORKBENCH_AI_1_ID=b1\n"
        "STRATEGY_WORKBENCH_AI_1_KIND=anthropic\n",
        encoding="utf-8",
    )
    app = build_http_app(assistant=_scripted_settings(tmp_path, env_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assistant/providers",
            json={"kind": "anthropic", "label": "내 Claude", "secret": SECRET},
        )
    assert response.status_code == 201, response.text
    assert response.json()["active"] is False


def test_a_repeated_id_in_another_block_is_reported_and_kept(tmp_path: Path) -> None:
    """3차 리뷰 6: 번호 블록 사이에 같은 id가 또 나오면 둘째부터 읽지 않고 알리며, 줄은 남긴다."""
    path = tmp_path / ".env"
    store = EnvFileProviderStore(CredentialsEnvFile(path))
    store.add(_profile("p1", active=True))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "STRATEGY_WORKBENCH_AI_7_ID=p1\n"
            "STRATEGY_WORKBENCH_AI_7_KIND=openai\n"
            "STRATEGY_WORKBENCH_AI_7_MODEL=other\n"
            "STRATEGY_WORKBENCH_AI_7_CREATED_AT=2026-10-24T05:00:00+00:00\n"
        )

    assert [item.profile_id for item in store.list()] == ["p1"]
    assert store.problems() == (
        EnvProblem("ai", "duplicate_id", 7, ("STRATEGY_WORKBENCH_AI_7_ID",)),
    )
    store.add(_profile("p2"))
    values = parse_env_text(path.read_text(encoding="utf-8"))
    assert values["STRATEGY_WORKBENCH_AI_3_MODEL"] == "other"


def test_raw_indexes_stay_apart_and_lowercase_extras_move_with_their_entry(
    tmp_path: Path,
) -> None:
    """3차 리뷰 7: `_01_`과 `_1_`은 다른 항목이고, 소문자·숫자 접미어는 그 항목을 따라 옮겨진다."""
    path = tmp_path / ".env"
    lines = []
    for raw, profile_id in (("1", "p1"), ("01", "p2")):
        lines += [
            f"STRATEGY_WORKBENCH_AI_{raw}_ID={profile_id}",
            f"STRATEGY_WORKBENCH_AI_{raw}_KIND=anthropic",
            f"STRATEGY_WORKBENCH_AI_{raw}_MODEL=m-{profile_id}",
            f"STRATEGY_WORKBENCH_AI_{raw}_CREATED_AT=2026-10-24T05:00:00+00:00",
        ]
    lines.append("STRATEGY_WORKBENCH_AI_01_note2=두 번째 메모")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = EnvFileProviderStore(CredentialsEnvFile(path))

    assert sorted(item.profile_id for item in store.list()) == ["p1", "p2"]
    store.delete("p1")

    values = parse_env_text(path.read_text(encoding="utf-8"))
    assert values["STRATEGY_WORKBENCH_AI_1_ID"] == "p2"
    assert values["STRATEGY_WORKBENCH_AI_1_note2"] == "두 번째 메모"


def test_the_read_cache_expires_even_when_file_times_do_not_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3차 리뷰 8: 시각·크기가 그대로인 제자리 수정도 읽은 지 1초가 지나면 반영된다."""
    path = tmp_path / ".env"
    path.write_text("A=1\n", encoding="utf-8")
    file = CredentialsEnvFile(path)
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    assert file.read() == {"A": "1"}
    stat_before = path.stat()

    with path.open("r+", encoding="utf-8") as stream:
        stream.write("A=2\n")
    os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *, follow_symlinks=True: (
            stat_before if self == path else os.stat(self, follow_symlinks=follow_symlinks)
        ),
    )

    assert file.read() == {"A": "1"}  # 1초 안에는 같은 요청의 캐시
    clock[0] += 1.5
    assert file.read() == {"A": "2"}
