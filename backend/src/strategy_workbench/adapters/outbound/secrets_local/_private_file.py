"""비밀 파일을 원자적으로, 현재 사용자 전용 권한으로 쓰는 곳 하나(코드 리뷰 8).

`secrets.json`(`_store.py`)과 내 문서 `auto_trading/.env`(`_env_file.py`)가 같은 규칙을 쓴다.

1. 폴더는 **이 함수가 만들었을 때만** 0700으로 좁힌다. 이미 있던 폴더는 다른 프로세스·도구가 함께
   쓰고 있을 수 있어 건드리지 않는다. 값은 파일 권한이 지킨다.
2. 임시 파일에 권한을 **먼저** 걸고 내용을 쓴 뒤 `fsync` → `os.replace` → 폴더 `fsync`.
   중간에 죽어도
   평문이 느슨한 권한으로 남거나 반쪽 파일이 남지 않는다.
3. 예외 문장에는 경로를 싣지 않는다. 경로는 `error.path` 속성으로만 간다.
"""

from __future__ import annotations

import errno as errno_module
import getpass
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias

from ._errors import SecretStoreStorageError

__all__ = [
    "ICACLS_OUTPUT_ENCODING",
    "CommandRunner",
    "atomic_write_restricted",
    "default_windows_account",
    "errno_name",
    "icacls_command",
    "prepare_private_directory",
    "restrict_owner_only",
    "run_icacls",
    "windows_account_name",
]

CommandRunner: TypeAlias = Callable[[Sequence[str]], None]


def errno_name(error: OSError) -> str:
    """OSError의 errno를 이름으로. 숫자만으로는 어떤 실패인지 읽히지 않는다."""
    if error.errno is None:
        return "UNKNOWN"
    return errno_module.errorcode.get(error.errno, f"ERRNO_{error.errno}")


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


# `icacls` 는 콘솔 코드 페이지(기본은 시스템 OEM)로 출력한다. 로캘이나 UTF-8 모드로 읽으면 디코딩이
# 실패해 stderr 가 None 이 되고, 그 뒤 처리의 예외가 원래 권한 오류를 가린다. 콘솔 코드 페이지를
# 바꾼 환경(`chcp`)에서는 OEM 과 다를 수 있으므로 못 읽는 바이트는 바꿔 넣는다.
ICACLS_OUTPUT_ENCODING: str | None = "oem" if sys.platform == "win32" else None


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
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding=ICACLS_OUTPUT_ENCODING,
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise SecretStoreStorageError(
            f"could not run the ACL command — operation=icacls errno={errno_name(error)}"
        ) from error
    if completed.returncode != 0:
        raise SecretStoreStorageError(
            "could not restrict the secrets file ACL — "
            f"operation=icacls returncode={completed.returncode} "
            f"stderr_bytes={len(completed.stderr)}"
        )


def restrict_owner_only(
    path: Path,
    *,
    platform: str,
    run_command: CommandRunner = run_icacls,
    account: str | None = None,
) -> None:
    """파일을 현재 사용자 전용으로. POSIX는 0600, 윈도우는 상속을 끊고 현재 사용자만 F(`icacls`)."""
    if platform == "win32":
        run_command(icacls_command(path, account=account or default_windows_account()))
        return
    os.chmod(path, 0o600)


def prepare_private_directory(path: Path, *, platform: str, label: str) -> Path:
    """`path`가 놓일 폴더. 이 함수가 만들었을 때만 0700으로 좁힌다."""
    directory = path.parent
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        return directory
    except OSError as error:
        raise SecretStoreStorageError(
            f"could not create the {label} directory — operation=mkdir errno={errno_name(error)}",
            path=path,
        ) from error
    if platform != "win32":
        try:
            os.chmod(directory, 0o700)
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not restrict the {label} directory — operation=chmod "
                f"errno={errno_name(error)}",
                path=path,
            ) from error
    return directory


def atomic_write_restricted(
    path: Path,
    text: str,
    *,
    platform: str,
    restrict: Callable[[Path], None],
    label: str,
    temporary_prefix: str,
    detail: str = "",
) -> None:
    """`text`를 `path`에 글자 그대로(줄바꿈 변환 없이) 쓴다.

    `detail`은 실패 문장에 덧붙일 사실이다.
    """
    directory = prepare_private_directory(path, platform=platform, label=label)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory, prefix=temporary_prefix, suffix=".tmp"
        )
        os.close(descriptor)
    except OSError as error:
        raise SecretStoreStorageError(
            f"could not create the {label} temporary file — operation=mkstemp "
            f"errno={errno_name(error)}",
            path=path,
        ) from error
    temporary = Path(temporary_name)
    try:
        # 내용보다 권한이 먼저다. 순서가 바뀌면 평문이 잠깐 느슨한 권한으로 디스크에 눕는다.
        restrict(temporary)
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        _discard(temporary)
        suffix = f" {detail}" if detail else ""
        raise SecretStoreStorageError(
            f"could not write the {label} — operation=replace{suffix} errno={errno_name(error)}",
            path=path,
        ) from error
    except BaseException:
        _discard(temporary)
        raise
    _sync_directory(directory, platform=platform)


def _sync_directory(directory: Path, *, platform: str) -> None:
    """rename 자체를 디스크에 확정한다. 윈도우는 폴더 핸들을 이렇게 열 수 없어 건너뛴다."""
    if platform == "win32":
        return
    with suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _discard(temporary: Path) -> None:
    """실패한 임시 파일을 지운다. 남겨 두면 평문 사본이 하나 더 생긴다."""
    with suppress(OSError):
        temporary.unlink()
