"""`ProviderSecretStore`의 로컬 파일 구현 (설계 spec D5, D9).

키는 사용자 설정 디렉터리의 `secrets.json` 하나에 `{profile_id: secret}`로 모인다. 프로파일
메타데이터(`assistant_sqlite`)와 **다른 파일**이라야 DB를 백업·복사·공유해도 키가 따라가지
않는다.

세 가지를 이 파일이 집행한다.

1. **경로 거부**: 저장소 트리 안은 거부한다. 판단은 생성자가 받은 `forbidden_roots` 목록으로만
   하며 git을 부르거나 환경 변수를 읽지 않는다(어댑터는 순수하게, 경로 선택은 bootstrap의 몫).
2. **권한**: POSIX는 디렉터리 0700·파일 0600, Windows는 상속을 끊고 현재 사용자만 F(`icacls`).
3. **원자 교체**: 임시 파일에 권한을 먼저 걸고 내용을 쓴 뒤 `os.replace`로 바꾼다. 중간에 죽어도
   평문이 느슨한 권한으로 남거나 반쪽짜리 JSON이 남지 않는다.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import TypeAlias

from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderSecretMissingError,
)

from ._errors import SecretStoreStorageError

__all__ = [
    "CommandRunner",
    "LocalFileProviderSecretStore",
    "SECRETS_DIRECTORY_NAME",
    "SECRETS_FILE_NAME",
    "default_secrets_path",
    "icacls_command",
    "resolve_default_secrets_path",
    "run_icacls",
]

SECRETS_DIRECTORY_NAME = "quant-workbench"
SECRETS_FILE_NAME = "secrets.json"

CommandRunner: TypeAlias = Callable[[Sequence[str]], None]


def resolve_default_secrets_path(*, platform: str, appdata: str | None, home: Path) -> Path:
    """OS 규칙만으로 기본 비밀 파일 경로를 계산한다(순수 함수).

    Windows는 `%APPDATA%/quant-workbench/secrets.json`, 그 밖은
    `~/.config/quant-workbench/secrets.json`이다. `%APPDATA%`가 비어 있으면 그 표준 위치인
    `~/AppData/Roaming`으로 되돌린다 — 서비스 계정처럼 변수가 없는 환경에서 홈이 아닌 곳에
    키를 쓰지 않기 위해서다.
    """
    if platform == "win32":
        base = Path(appdata).expanduser() if appdata else home / "AppData" / "Roaming"
    else:
        base = home / ".config"
    return base / SECRETS_DIRECTORY_NAME / SECRETS_FILE_NAME


def default_secrets_path() -> Path:
    """현재 프로세스의 OS 값으로 위 계산을 돌린다.

    앱 설정 환경 변수(`STRATEGY_WORKBENCH_*`)는 읽지 않는다. 그건 bootstrap이 읽어 경로로 넘긴다.
    여기서 보는 것은 OS가 정한 `%APPDATA%`와 홈 디렉터리뿐이다.
    """
    return resolve_default_secrets_path(
        platform=sys.platform, appdata=os.environ.get("APPDATA"), home=Path.home()
    )


def icacls_command(path: Path, *, account: str) -> tuple[str, ...]:
    """상속을 끊고 `account`에만 전체 권한을 주는 `icacls` 인자."""
    return ("icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F")


def run_icacls(command: Sequence[str]) -> None:
    """`icacls`를 돌리고 실패하면 저장 오류로 바꾼다.

    권한 설정이 조용히 실패하면 키가 상속된 ACL로 남는다. 실패는 반드시 쓰기 전체를 되돌린다.
    """
    try:
        # shell을 거치지 않고 고정된 인자 배열만 넘긴다. 경로는 인자라 인용 문제가 없다.
        completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    except OSError as error:
        raise SecretStoreStorageError(
            f"could not run the ACL command — command={list(command)} ({error})"
        ) from error
    if completed.returncode != 0:
        raise SecretStoreStorageError(
            "could not restrict the secrets file ACL — "
            f"command={list(command)} returncode={completed.returncode} "
            f"stderr={completed.stderr.strip()!r}"
        )


class LocalFileProviderSecretStore:
    """`secrets.json` 하나를 소유하는 비밀 저장소."""

    def __init__(
        self,
        path: str | Path,
        *,
        forbidden_roots: Sequence[str | Path] = (),
        platform: str = sys.platform,
        run_command: CommandRunner = run_icacls,
        windows_account: str | None = None,
    ) -> None:
        candidate = Path(path).expanduser().resolve()
        for root in forbidden_roots:
            resolved_root = Path(root).expanduser().resolve()
            if candidate == resolved_root or resolved_root in candidate.parents:
                raise ValueError(
                    "the provider secrets file must live outside the repository — "
                    f"path={candidate} forbidden_root={resolved_root}"
                )
        self._path = candidate
        self._platform = platform
        self._run_command = run_command
        self._account = windows_account
        self._lock = RLock()

    @property
    def secrets_path(self) -> Path:
        return self._path

    def get(self, profile_id: str) -> str:
        with self._lock:
            stored = self._read()
        try:
            return stored[profile_id]
        except KeyError as error:
            raise ProviderSecretMissingError(profile_id) from error

    def put(self, profile_id: str, secret: str) -> None:
        with self._lock:
            stored = self._read()
            stored[profile_id] = secret
            self._write(stored)

    def delete(self, profile_id: str) -> None:
        """멱등이다. 없는 키를 지워도 실패하지 않고 파일을 새로 만들지도 않는다."""
        with self._lock:
            stored = self._read()
            if stored.pop(profile_id, None) is None:
                return
            self._write(stored)

    # -- 파일 IO -----------------------------------------------------------------------------

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not read the provider secrets file — path={self._path} ({error})"
            ) from error
        try:
            decoded = json.loads(raw)
        except ValueError as error:
            raise SecretStoreStorageError(
                f"the provider secrets file is not valid JSON — path={self._path} bytes={len(raw)}"
            ) from error
        if not isinstance(decoded, dict):
            raise SecretStoreStorageError(
                "the provider secrets file is not a JSON object of profile_id to secret — "
                f"path={self._path} type={type(decoded).__name__}"
            )
        secrets: dict[str, str] = {}
        for key, value in decoded.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SecretStoreStorageError(
                    "the provider secrets file has a non-text entry — "
                    f"path={self._path} key_type={type(key).__name__} "
                    f"value_type={type(value).__name__}"
                )
            secrets[key] = value
        return secrets

    def _write(self, secrets: dict[str, str]) -> None:
        directory = self._path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if self._platform != "win32":
                os.chmod(directory, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=directory, prefix=".secrets-", suffix=".tmp"
            )
            os.close(descriptor)
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not prepare the provider secrets directory — path={directory} ({error})"
            ) from error

        temporary = Path(temporary_name)
        try:
            # 내용보다 권한이 먼저다. 순서가 바뀌면 평문이 잠깐 느슨한 권한으로 디스크에 눕는다.
            self._restrict(temporary)
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(secrets, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            self._discard(temporary)
            raise SecretStoreStorageError(
                f"could not write the provider secrets file — path={self._path} "
                f"entries={len(secrets)} ({error})"
            ) from error
        except BaseException:
            self._discard(temporary)
            raise

    def _restrict(self, path: Path) -> None:
        if self._platform == "win32":
            account = self._account or getpass.getuser()
            self._run_command(icacls_command(path, account=account))
            return
        os.chmod(path, 0o600)

    @staticmethod
    def _discard(temporary: Path) -> None:
        """실패한 임시 파일을 지운다. 남겨 두면 평문 사본이 하나 더 생긴다."""
        with suppress(OSError):
            temporary.unlink()
