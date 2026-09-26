"""로컬 데이터 파일을 현재 사용자만 읽을 수 있게 한다 (A-03 리뷰가 A-04로 넘긴 항목).

비밀 파일(`secrets_local`)만 잠그고 대화 이력 DB를 열어 두면 반쪽이다. 어시스턴트 DB에는
사용자가 모델에 보낸 전략 원문·질문·모델 답변이 그대로 쌓인다. API 키만큼은 아니어도, 같은
장비의 다른 계정이 읽어도 되는 내용은 아니다.

권한 규칙은 `secrets_local`과 같다: POSIX는 0600, Windows는 상속을 끊고 현재 사용자에게만
전체 권한(`icacls`). 인자 조립(`icacls_command`)과 계정 이름 계산(`default_windows_account`)은
그 어댑터의 것을 그대로 쓴다 — 규칙이 두 곳에서 갈라지면 한쪽만 고쳐지기 때문이다.

`icacls` 실행만 여기서 따로 한다. `secrets_local.run_icacls`는 실패를 `SecretStoreStorageError`로
올리고 메시지도 "secrets file"이라고 말하는데, 그 예외가 DB 권한 실패를 나르면 운영자가 키
파일을 들여다보게 된다. 실패의 정체는 빌려 쓰지 않는다.

**남는 틈**: SQLite는 쓰기 중에 `-journal` 사이드카를 만든다. POSIX는 그 파일이 DB 파일의
권한을 따라가지만 Windows는 디렉터리 ACL을 상속한다. 시작 시점에 이미 있는 사이드카는 같이
잠그고, 그 뒤에 생기는 것은 디렉터리 권한에 달려 있다. 디렉터리째 잠그는 것은 기본 경로
(`.local/`)에 전략 DB·백테스트 산출물이 같이 사는 한 범위 밖이다.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeAlias

from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    default_windows_account,
    icacls_command,
)

__all__ = [
    "SIDECAR_SUFFIXES",
    "CommandRunner",
    "FilePermissionError",
    "restrict_to_current_user",
]

CommandRunner: TypeAlias = Callable[[Sequence[str]], None]

# SQLite가 DB 파일 옆에 만드는 파일들. 있으면 같이 잠근다.
SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


class FilePermissionError(RuntimeError):
    """파일 권한을 현재 사용자로 좁히지 못했다.

    시작 단계에서만 나는 오류라 메시지에 절대 경로를 넣는다. 운영자가 어느 파일인지 알아야
    고칠 수 있고, 이 문자열은 HTTP 응답으로 나가지 않는다
    (`.claude/rules/error-messages.md`: 로컬 로그의 절대 경로는 허용).
    """


def restrict_to_current_user(
    path: Path,
    *,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
    windows_account: str | None = None,
) -> tuple[Path, ...]:
    """`path`와 그 옆 사이드카를 현재 사용자 전용으로 만들고, 실제로 손댄 경로를 돌려준다.

    없는 파일은 조용히 넘기지 않고 오류로 올린다. "권한을 걸었다"는 보고가 사실이 아니면
    다음 사람이 그 보고를 믿고 더 확인하지 않는다.
    """
    if not path.exists():
        raise FilePermissionError(
            f"cannot restrict a file that does not exist — path={path} platform={platform}"
        )
    runner = run_command if run_command is not None else _run_icacls
    touched = [path, *_existing_sidecars(path)]
    for target in touched:
        _restrict_one(target, platform=platform, run_command=runner, account=windows_account)
    return tuple(touched)


def _existing_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        candidate
        for candidate in (path.with_name(path.name + suffix) for suffix in SIDECAR_SUFFIXES)
        if candidate.exists()
    )


def _restrict_one(
    path: Path, *, platform: str, run_command: CommandRunner, account: str | None
) -> None:
    if platform == "win32":
        run_command(icacls_command(path, account=account or default_windows_account()))
        return
    try:
        os.chmod(path, 0o600)
    except OSError as error:
        raise FilePermissionError(
            f"could not restrict the file mode — path={path} operation=chmod "
            f"errno={errno.errorcode.get(error.errno or 0, 'UNKNOWN')}"
        ) from error


def _run_icacls(command: Sequence[str]) -> None:
    """`icacls`를 돌리고 실패하면 시작을 멈춘다.

    조용히 넘어가면 대화 이력이 상속된 ACL로 남고, 아무도 그 사실을 모른다.
    """
    try:
        # shell을 거치지 않고 고정된 인자 배열만 넘긴다. 경로는 인자라 인용 문제가 없다.
        completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    except OSError as error:
        raise FilePermissionError(
            f"could not run the ACL command — command={list(command)} "
            f"errno={errno.errorcode.get(error.errno or 0, 'UNKNOWN')}"
        ) from error
    if completed.returncode != 0:
        raise FilePermissionError(
            f"could not restrict the file ACL — command={list(command)} "
            f"returncode={completed.returncode} stderr={completed.stderr.strip()!r}"
        )
