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
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ._errors import SecretStoreStorageError

__all__ = ["atomic_write_restricted", "errno_name", "prepare_private_directory"]


def errno_name(error: OSError) -> str:
    """OSError의 errno를 이름으로. 숫자만으로는 어떤 실패인지 읽히지 않는다."""
    if error.errno is None:
        return "UNKNOWN"
    return errno_module.errorcode.get(error.errno, f"ERRNO_{error.errno}")


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
