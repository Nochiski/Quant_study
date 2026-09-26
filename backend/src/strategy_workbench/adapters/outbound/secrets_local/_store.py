"""`ProviderSecretStore`의 로컬 파일 구현 (설계 spec D5, D9).

키는 사용자 설정 디렉터리의 `secrets.json` 하나에 `{profile_id: secret}`로 모인다. 프로파일
메타데이터(`assistant_sqlite`)와 **다른 파일**이라야 DB를 백업·복사·공유해도 키가 따라가지
않는다.

네 가지를 이 파일이 집행한다.

1. **경로 거부**: 저장소 트리 안은 거부한다. 판단은 생성자가 받은 `forbidden_roots` 목록으로만
   하며 git을 부르거나 환경 변수를 읽지 않는다(어댑터는 순수하게, 경로 선택은 bootstrap의 몫).
2. **권한**: 파일은 POSIX 0600, Windows는 상속을 끊고 현재 사용자만 F(`icacls`). 디렉터리 0700은
   **이 adapter가 그 디렉터리를 만들었을 때만** 적용한다.
3. **원자 교체**: 임시 파일에 권한을 먼저 걸고 내용을 쓴 뒤 `os.replace`로 바꾼다. 중간에 죽어도
   평문이 느슨한 권한으로 남거나 반쪽짜리 JSON이 남지 않는다.
4. **경로 비노출**: 예외 문자열에 비밀 파일 경로를 적지 않는다. 경로는 `error.path` 속성으로만
   간다(`_errors.py` 참고).
"""

from __future__ import annotations

import errno as errno_module
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
    "default_windows_account",
    "icacls_command",
    "resolve_default_secrets_path",
    "windows_account_name",
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


def windows_account_name(*, userdomain: str | None, username: str | None, fallback: str) -> str:
    """`icacls`에 넘길 계정 이름(순수 함수). 도메인이 있으면 `DOMAIN\\user`로 한정한다.

    도메인 가입 장비에서 계정 이름만 주면 `icacls`가 로컬 계정 중에서 찾다 실패해 `put`이 통째로
    깨진다. 로컬 계정뿐인 장비에서는 `%USERDOMAIN%`이 장비 이름이라 그대로도 해석된다.
    """
    if username and userdomain:
        return f"{userdomain}\\{username}"
    return username or fallback


def default_windows_account() -> str:
    """현재 프로세스의 OS 계정 값으로 위 계산을 돌린다."""
    return windows_account_name(
        userdomain=os.environ.get("USERDOMAIN"),
        username=os.environ.get("USERNAME"),
        fallback=getpass.getuser(),
    )


def icacls_command(path: Path, *, account: str) -> tuple[str, ...]:
    """상속을 끊고 `account`에만 전체 권한을 주는 `icacls` 인자."""
    return ("icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F")


def run_icacls(command: Sequence[str]) -> None:
    """`icacls`를 돌리고 실패하면 저장 오류로 바꾼다.

    권한 설정이 조용히 실패하면 키가 상속된 ACL로 남는다. 실패는 반드시 쓰기 전체를 되돌린다.

    메시지에는 명령 배열도 `stderr`도 싣지 않는다. 둘 다 비밀 파일 경로를 그대로 담는다.
    """
    try:
        # shell을 거치지 않고 고정된 인자 배열만 넘긴다. 경로는 인자라 인용 문제가 없다.
        completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    except OSError as error:
        raise SecretStoreStorageError(
            f"could not run the ACL command — operation=icacls errno={_errno_name(error)}"
        ) from error
    if completed.returncode != 0:
        raise SecretStoreStorageError(
            "could not restrict the secrets file ACL — "
            f"operation=icacls returncode={completed.returncode} "
            f"stderr_bytes={len(completed.stderr)}"
        )


def _errno_name(error: OSError) -> str:
    """OSError의 errno를 이름으로. 숫자만으로는 어떤 실패인지 읽히지 않는다."""
    if error.errno is None:
        return "UNKNOWN"
    return errno_module.errorcode.get(error.errno, f"ERRNO_{error.errno}")


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
                # 이 하나만 경로를 적는다. 조립 시점의 설정 오류이고, 경로를 고른 당사자(bootstrap)
                # 에게 어느 경로가 거부됐는지 말해야 하며, 아직 저장된 비밀이 없다.
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
        """없는 키를 지워도 실패하지 않는다.

        파일이 **읽히지 않는 경우**는 예외다. 그때 조용히 성공을 돌려주면 실제 API 키가 디스크에
        남은 채로 시스템은 지워졌다고 믿는다. 포트가 약속한 멱등성은 "없는 키"에 대한 것이고,
        "읽을 수 없는 저장소"는 예상된 도메인 실패가 아니라 예외적 실패다.
        """
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
                f"could not read the provider secrets file — operation=read "
                f"errno={_errno_name(error)}",
                path=self._path,
            ) from error
        try:
            decoded = json.loads(raw)
        except ValueError as error:
            raise SecretStoreStorageError(
                f"the provider secrets file is not valid JSON — operation=read bytes={len(raw)}",
                path=self._path,
            ) from error
        if not isinstance(decoded, dict):
            raise SecretStoreStorageError(
                "the provider secrets file is not a JSON object of profile_id to secret — "
                f"operation=read type={type(decoded).__name__}",
                path=self._path,
            )
        secrets: dict[str, str] = {}
        for key, value in decoded.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SecretStoreStorageError(
                    "the provider secrets file has a non-text entry — operation=read "
                    f"key_type={type(key).__name__} value_type={type(value).__name__}",
                    path=self._path,
                )
            secrets[key] = value
        return secrets

    def _write(self, secrets: dict[str, str]) -> None:
        directory = self._prepare_directory()
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=directory, prefix=".secrets-", suffix=".tmp"
            )
            os.close(descriptor)
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not create the provider secrets temporary file — operation=mkstemp "
                f"errno={_errno_name(error)}",
                path=self._path,
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
                f"could not write the provider secrets file — operation=replace "
                f"entries={len(secrets)} errno={_errno_name(error)}",
                path=self._path,
            ) from error
        except BaseException:
            self._discard(temporary)
            raise
        self._sync_directory(directory)

    def _prepare_directory(self) -> Path:
        """비밀 파일이 놓일 디렉터리. **이 adapter가 만들었을 때만** 0700으로 좁힌다.

        이미 있던 디렉터리의 권한은 건드리지 않는다. 경로는 bootstrap이 고르므로 홈이나 공유
        설정 디렉터리를 가리킬 수 있고, 그걸 말없이 좁히면 같은 디렉터리를 쓰던 다른
        프로세스·사용자가 조용히 접근을 잃는다(놀람 최소화). 비밀 값 자체는 파일 권한 0600이
        지키므로 디렉터리가 느슨해도 값은 새지 않는다.
        """
        directory = self._path.parent
        try:
            directory.mkdir(parents=True)
        except FileExistsError:
            return directory
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not create the provider secrets directory — operation=mkdir "
                f"errno={_errno_name(error)}",
                path=self._path,
            ) from error
        if self._platform != "win32":
            try:
                os.chmod(directory, 0o700)
            except OSError as error:
                raise SecretStoreStorageError(
                    f"could not restrict the provider secrets directory — operation=chmod "
                    f"errno={_errno_name(error)}",
                    path=self._path,
                ) from error
        return directory

    def _sync_directory(self, directory: Path) -> None:
        """rename 자체를 디스크에 확정한다.

        내용은 `fsync`했지만 디렉터리 항목은 별개다. 이걸 빼면 갑작스러운 전원 차단에서 교체가
        통째로 유실돼 직전 키로 되돌아간다. Windows는 디렉터리 핸들을 이렇게 열 수 없어 건너뛴다.
        """
        if self._platform == "win32":
            return
        with suppress(OSError):
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _restrict(self, path: Path) -> None:
        if self._platform == "win32":
            account = self._account or default_windows_account()
            self._run_command(icacls_command(path, account=account))
            return
        os.chmod(path, 0o600)

    @staticmethod
    def _discard(temporary: Path) -> None:
        """실패한 임시 파일을 지운다. 남겨 두면 평문 사본이 하나 더 생긴다."""
        with suppress(OSError):
            temporary.unlink()
