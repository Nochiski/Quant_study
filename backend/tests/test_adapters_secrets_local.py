"""`secrets_local` 비밀 저장 adapter와 비밀 격리 계약 테스트 (설계 spec D5, A-03).

여기서 고정하는 사실 두 가지다.

1. 파일 권한: POSIX는 0600/0700, Windows는 상속을 끊고 현재 사용자만 F. Windows 분기는 `icacls`
   호출 인자를 검증해 플랫폼과 무관하게 결정적으로 돈다.
2. 비밀 격리: 키는 SQLite 파일 바이트·`provider_profiles` 덤프·어댑터 예외 메시지 어디에도
   평문으로 없고, 비밀 파일 경로도 DB에 남지 않으며 두 어댑터는 로깅을 하지 않는다(spec 완료
   정의 3).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    AssistantDatabase,
    SQLiteProviderProfileRepository,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    CommandRunner,
    LocalFileProviderSecretStore,
    SecretStoreStorageError,
    default_secrets_path,
    resolve_default_secrets_path,
    run_icacls,
)
from strategy_workbench.application.assistant_chat.facade.ports import ProviderSecretMissingError
from strategy_workbench.domain.assistant.facade.models import ProviderKind, ProviderProfile

SECRET = "sk-ant-not-a-real-key-0123456789"
NOW = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)

ADAPTER_SOURCES = (
    Path(__file__).resolve().parents[1] / "src" / "strategy_workbench" / "adapters" / "outbound"
)


class RecordingRunner:
    """주입한 명령 실행기. Windows ACL 분기를 실제 `icacls` 없이 검증한다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._fail = fail

    def __call__(self, command: Sequence[str]) -> None:
        self.commands.append(tuple(command))
        if self._fail:
            raise SecretStoreStorageError(
                f"could not restrict the secrets file ACL — command={list(command)} returncode=5"
            )


def _store(
    path: Path,
    *,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
    windows_account: str | None = None,
) -> LocalFileProviderSecretStore:
    """저장소 루트 밖 임시 경로를 쓰는 테스트용 생성자."""
    return LocalFileProviderSecretStore(
        path,
        forbidden_roots=(),
        platform=platform,
        run_command=run_command or run_icacls,
        windows_account=windows_account,
    )


# -- 저장·조회 -------------------------------------------------------------------------------


def test_secrets_round_trip_through_the_json_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "secrets.json"
    store = _store(path)

    store.put("profile-a", SECRET)
    store.put("profile-b", "second-secret")

    assert store.get("profile-a") == SECRET
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "profile-a": SECRET,
        "profile-b": "second-secret",
    }


def test_a_reopened_store_reads_what_the_previous_one_wrote(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    _store(path).put("profile-a", SECRET)

    assert _store(path).get("profile-a") == SECRET


def test_a_missing_secret_is_a_port_error(tmp_path: Path) -> None:
    store = _store(tmp_path / "secrets.json")

    with pytest.raises(ProviderSecretMissingError) as raised:
        store.get("profile-a")

    assert "profile-a" in str(raised.value)


def test_delete_is_idempotent_and_never_creates_the_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    store = _store(path)

    store.delete("profile-a")
    assert not path.exists()

    store.put("profile-a", SECRET)
    store.delete("profile-a")
    store.delete("profile-a")
    with pytest.raises(ProviderSecretMissingError):
        store.get("profile-a")


def test_a_corrupt_secrets_file_is_a_storage_error(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SecretStoreStorageError):
        _store(path).get("profile-a")


def test_a_replaced_secret_leaves_no_second_copy_behind(tmp_path: Path) -> None:
    """임시 파일 + `os.replace`는 원자 교체여야 한다. 잔여 임시 파일이 남으면 평문이 두 벌이다."""
    path = tmp_path / "secrets.json"
    store = _store(path)

    store.put("profile-a", SECRET)
    store.put("profile-a", "rotated-secret")

    assert sorted(item.name for item in tmp_path.iterdir()) == ["secrets.json"]
    assert store.get("profile-a") == "rotated-secret"


# -- 경로 규칙 -------------------------------------------------------------------------------


def test_a_path_inside_a_forbidden_root_is_refused(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    (repository_root / ".local").mkdir(parents=True)

    with pytest.raises(ValueError, match="forbidden_root"):
        LocalFileProviderSecretStore(
            repository_root / ".local" / "secrets.json", forbidden_roots=(repository_root,)
        )


def test_a_path_outside_every_forbidden_root_is_accepted(tmp_path: Path) -> None:
    store = LocalFileProviderSecretStore(
        tmp_path / "outside" / "secrets.json", forbidden_roots=(tmp_path / "repo",)
    )

    store.put("profile-a", SECRET)

    assert store.get("profile-a") == SECRET


def test_the_default_path_follows_the_operating_system_convention() -> None:
    windows = resolve_default_secrets_path(
        platform="win32", appdata="C:/Users/q/AppData/Roaming", home=Path("C:/Users/q")
    )
    posix = resolve_default_secrets_path(platform="linux", appdata=None, home=Path("/home/q"))
    without_appdata = resolve_default_secrets_path(
        platform="win32", appdata=None, home=Path("C:/Users/q")
    )

    roaming = "C:/Users/q/AppData/Roaming/quant-workbench/secrets.json"
    assert windows.as_posix() == roaming
    assert posix.as_posix() == "/home/q/.config/quant-workbench/secrets.json"
    assert without_appdata.as_posix() == roaming


def test_the_ambient_default_path_ends_at_the_expected_file() -> None:
    assert default_secrets_path().parts[-2:] == ("quant-workbench", "secrets.json")


# -- 권한 (플랫폼 분기) ------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 모드 비트는 Windows에 없다")
def test_posix_permissions_are_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "config" / "secrets.json"

    _store(path).put("profile-a", SECRET)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_windows_strips_inheritance_and_grants_the_current_user_only(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    runner = RecordingRunner()
    store = _store(path, platform="win32", run_command=runner, windows_account="QUANT\\sangmok")

    store.put("profile-a", SECRET)

    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[0] == "icacls"
    assert command[2:] == ("/inheritance:r", "/grant:r", "QUANT\\sangmok:F")
    assert Path(command[1]).parent == tmp_path
    assert Path(command[1]) != path  # 임시 파일에 먼저 권한을 걸고 원자 교체한다


def test_an_icacls_failure_becomes_a_storage_error(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    store = _store(
        path,
        platform="win32",
        run_command=RecordingRunner(fail=True),
        windows_account="sangmok",
    )

    with pytest.raises(SecretStoreStorageError):
        store.put("profile-a", SECRET)

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


# -- 비밀 격리 (완료 정의 3) --------------------------------------------------------------------


def test_the_secret_never_reaches_the_sqlite_file_or_a_profile_dump(tmp_path: Path) -> None:
    database_path = tmp_path / "assistant.sqlite3"
    secrets_path = tmp_path / "config" / "secrets.json"
    _store(secrets_path).put("profile-a", SECRET)

    with AssistantDatabase(database_path) as database:
        repository = SQLiteProviderProfileRepository(database)
        repository.add(
            ProviderProfile(
                profile_id="profile-a",
                kind=ProviderKind.ANTHROPIC,
                label="Claude 작업용",
                model="claude-opus-5",
                base_url=None,
                created_at=NOW,
                active=True,
            )
        )
        dump = repr(repository.list())

    assert SECRET.encode("utf-8") not in database_path.read_bytes()
    assert SECRET not in dump
    assert SECRET in secrets_path.read_text(encoding="utf-8")


def test_the_secrets_file_path_never_reaches_the_sqlite_file(tmp_path: Path) -> None:
    database_path = tmp_path / "assistant.sqlite3"
    secrets_path = tmp_path / "config" / "secrets.json"
    _store(secrets_path).put("profile-a", SECRET)

    with AssistantDatabase(database_path) as database:
        SQLiteProviderProfileRepository(database).add(
            ProviderProfile(
                profile_id="profile-a",
                kind=ProviderKind.ANTHROPIC,
                label="Claude 작업용",
                model="claude-opus-5",
                base_url=None,
                created_at=NOW,
                active=True,
            )
        )

    assert str(secrets_path).encode("utf-8") not in database_path.read_bytes()
    assert secrets_path.name.encode("utf-8") not in database_path.read_bytes()


def test_store_errors_do_not_quote_the_secret(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    store = _store(
        path,
        platform="win32",
        run_command=RecordingRunner(fail=True),
        windows_account="sangmok",
    )

    with pytest.raises(SecretStoreStorageError) as raised:
        store.put("profile-a", SECRET)

    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)


def test_neither_storage_adapter_logs_anything() -> None:
    """로그 한 줄이 비밀 파일 경로와 프로파일 행을 운영 로그로 새게 하는 가장 흔한 통로다."""
    emitter = re.compile(r"\blogging\b|\blogger\b|\bprint\s*\(")
    offenders = [
        path.name
        for node in ("assistant_sqlite", "secrets_local")
        for path in (ADAPTER_SOURCES / node).rglob("*.py")
        if emitter.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
