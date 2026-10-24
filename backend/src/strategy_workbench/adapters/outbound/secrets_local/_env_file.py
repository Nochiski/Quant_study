"""내 문서 `auto_trading/.env` 하나를 AI 공급자·키움 모의 연결이 함께 쓴다.

2026-10-24 사용자 결정.

맥·리눅스·윈도우 어디서든 사용자가 찾을 수 있는 같은 자리(OS가 정한 "문서" 폴더)에 두려고 OS 설정
폴더의 `secrets.json`에서 옮겼다. 이 파일은 사용자의 다른 도구도 함께 쓸 수 있는 `.env`라서 다음을
지킨다.

1. **자기 묶음만 고친다.** 저장소마다 이름 접두어(`prefix`) 하나를 갖고, 쓰기는 그 접두어로 시작하는
   줄과 묶음 머리 주석만 바꾼다. 다른 줄(다른 도구의 키·주석·빈 줄·순서)은 글자 그대로 둔다.
2. **권한·원자 교체**는 `secrets.json`과 같다: 임시 파일에 POSIX 0600(윈도우는 `icacls`)을 먼저 걸고
   내용을 쓴 뒤 `os.replace`. 폴더는 **이 모듈이 만들었을 때만** 0700으로 좁힌다.
3. **경로 비노출**: 예외 문자열에 값과 경로를 싣지 않는다. 경로는 `error.path` 속성으로만 간다.
4. 읽기는 파일이 바뀌었을 때만 다시 한다(크기·수정 시각·inode가 같으면 앞서 읽은 내용, 코드 리뷰
   9). 사용자가 손으로 고친 값은 서버를 다시 켜지 않아도 반영된다.
5. BOM과 줄바꿈(CRLF/LF)을 읽은 그대로 다시 쓰고(리뷰 5), 바뀐 것이 없으면 쓰지 않는다(리뷰 6).
6. 읽고-고치고-쓰기 전체를 OS 파일 잠금으로 감싼다(리뷰 4).
"""

from __future__ import annotations

import errno
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock, get_ident
from typing import Literal, TypeVar

from ._env import (
    ENV_NAME,
    decode_env_bytes,
    encode_env_value,
    env_assignment,
    env_lines,
    parse_env_text,
)
from ._errors import SecretStoreStorageError
from ._private_file import (
    CommandRunner,
    atomic_write_restricted,
    errno_name,
    prepare_private_directory,
    restrict_owner_only,
    run_icacls,
)

__all__ = [
    "AUTO_TRADING_DIRECTORY_NAME",
    "AUTO_TRADING_ENV_FILE_NAME",
    "IMPORTED_ENV_PREFIX",
    "CredentialsEnvFile",
    "EnvProblem",
    "default_auto_trading_env_path",
    "parse_env_text",
    "resolve_documents_directory",
]

AUTO_TRADING_DIRECTORY_NAME = "auto_trading"
# 예전 저장 위치에서 한 번 옮겼다는 기록(리뷰 3). 묶음마다 이름 하나, 값은 옮긴 날짜다. 이 줄이
# 있으면 어느 체크아웃에서 켜도 예전 위치를 다시 읽지 않는다.
IMPORTED_ENV_PREFIX = "STRATEGY_WORKBENCH_IMPORTED_"
_IMPORTED_HEADER = "Quant Workbench 예전 저장 이전 기록 — 지우면 예전 위치에서 다시 가져옵니다"
AUTO_TRADING_ENV_FILE_NAME = ".env"

_LOCK_SUFFIX = ".lock"
_UPDATE_ATTEMPTS = 5
_WINDOWS_LOCK_ATTEMPTS = 12
# `msvcrt.locking`이 "남이 잡고 있다"로 실패할 때의 errno. 윈도우의
# `EDEADLOCK`은 POSIX `EDEADLK`와 같은 값이다.
_WINDOWS_LOCK_BUSY = frozenset({errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)})
_T = TypeVar("_T")
_LABEL = "credentials env file"

# Windows 알려진 폴더 "문서"(FOLDERID_Documents). OneDrive로 옮긴 문서 폴더도 이 값이 가리킨다.
_FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def resolve_documents_directory(
    *,
    platform: str,
    home: Path,
    xdg_documents: str | None = None,
    windows_documents: str | None = None,
) -> Path:
    """OS가 정한 "문서" 폴더(순수 함수).

    - 윈도우: 알려진 폴더 API 값(`windows_documents`). 못 얻으면 `~/Documents`.
    - 리눅스: `XDG_DOCUMENTS_DIR`(`user-dirs.dirs`의 `$HOME/...` 표기 포함). 없거나 홈 자체면
      `~/Documents` — XDG는 문서 폴더를 끄면 홈을 돌려주는데, 홈 바로 밑에 키를 흩뿌리지 않는다.
    - 맥: `~/Documents`.
    """
    if platform == "win32" and windows_documents:
        return Path(windows_documents)
    if platform.startswith("linux") and xdg_documents:
        expanded = Path(xdg_documents.replace("$HOME", str(home))).expanduser()
        if expanded.is_absolute() and expanded != home:
            return expanded
    return home / "Documents"


def default_auto_trading_env_path() -> Path:
    """현재 프로세스의 OS 값으로 `<문서>/auto_trading/.env`를 계산한다."""
    home = Path.home()
    documents = resolve_documents_directory(
        platform=sys.platform,
        home=home,
        xdg_documents=_xdg_documents(home) if sys.platform.startswith("linux") else None,
        windows_documents=_windows_documents() if sys.platform == "win32" else None,
    )
    return documents / AUTO_TRADING_DIRECTORY_NAME / AUTO_TRADING_ENV_FILE_NAME


def _xdg_documents(home: Path) -> str | None:
    """환경 변수가 먼저, 없으면 `~/.config/user-dirs.dirs`의 한 줄."""
    configured = os.environ.get("XDG_DOCUMENTS_DIR")
    if configured:
        return configured
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        text = (config_home / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_env_text(text).get("XDG_DOCUMENTS_DIR")


def _windows_documents() -> str | None:
    """`SHGetKnownFolderPath`로 문서 폴더를 묻는다. 실패하면 None(기본 위치로 되돌림)."""
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class _Guid(ctypes.Structure):
            _fields_ = [  # noqa: RUF012 — reason: ctypes 구조체 선언 관례
                ("data1", wintypes.DWORD),
                ("data2", wintypes.WORD),
                ("data3", wintypes.WORD),
                ("data4", wintypes.BYTE * 8),
            ]

        raw = UUID(_FOLDERID_DOCUMENTS).bytes_le
        guid = _Guid.from_buffer_copy(raw)
        buffer = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # pyright: ignore[reportAttributeAccessIssue]  # 윈도우 전용 속성
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer)) != 0:
            return None
        try:
            return buffer.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)  # pyright: ignore[reportAttributeAccessIssue]  # 윈도우 전용
    except (OSError, AttributeError, ValueError):
        return None


@dataclass(frozen=True)
class EnvProblem:
    """저장 파일에서 읽지 못한 부분 하나. 값·경로는 담지 않는다(화면이 사유로 문장을 고른다).

    `section`: `file`(파일 전체)·`ai`·`kiwoom`. `reason`: `not_utf8`·`unreadable`·`incomplete`·
    `unknown_kind`·`bad_time`. `fields`는 빠지거나 틀린 줄 이름이다.
    """

    section: Literal["file", "ai", "kiwoom"]
    reason: str
    index: int | None = None
    fields: tuple[str, ...] = ()


# -- 파일 ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    key: tuple[int, int, int, int] | None
    text: str
    bom: bool
    newline: str


class CredentialsEnvFile:
    """`.env` 한 파일. 같은 파일을 쓰는 저장소들은 이 인스턴스 하나를 나눠 써야 잠금이 맞는다."""

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
                # 조립 시점 설정 오류라 경로를 적는다(`_store.py`와 같은 예외 하나).
                raise ValueError(
                    "the credentials env file must live outside the repository — "
                    f"path={candidate} forbidden_root={resolved_root}"
                )
        self._path = candidate
        self._platform = platform
        self._run_command = run_command
        self._account = windows_account
        self._lock = _FileLock(self)
        self._snapshot: _Snapshot | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock(self) -> _FileLock:
        """읽고-고치고-쓰는 저장소가 한 번에 잡는 잠금(리뷰 4).

        스레드 잠금과 OS 파일 잠금(옆의 `.env.lock`)을 함께 잡는다. 서버와 S07 명령줄처럼 다른
        프로세스가 같은 파일을 동시에 고쳐도 한쪽 쓰기가 사라지지 않는다. 같은 스레드는 다시 들어갈
        수 있고, OS 잠금은 가장 바깥에서 한 번만 잡는다. 폴더가 아직 없으면 실제로 쓸 때 잡는다
        — 고칠 게 없는 호출이 폴더와 잠금 파일을 만들지 않게(리뷰 6).
        """
        return self._lock

    def exists(self) -> bool:
        return self._path.exists()

    def read(self) -> dict[str, str]:
        """모든 이름과 값. 파일이 없으면 빈 사전.

        읽기는 OS 잠금을 잡지 않는다. 쓰기가 임시 파일 → `os.replace`라 반쪽 파일을 볼 일이 없다.
        """
        return parse_env_text(self._read().text)

    def file_problem(self) -> EnvProblem | None:
        """파일 전체를 읽지 못하면 그 사유. 읽을 수 있으면(없는 파일 포함) None."""
        try:
            self._read()
        except SecretStoreStorageError as error:
            return EnvProblem("file", error.reason or "unreadable")
        return None

    def imported(self, name: str) -> bool:
        """예전 위치에서 `name` 묶음을 옮긴 기록이 있나."""
        return bool(self.read().get(f"{IMPORTED_ENV_PREFIX}{name}"))

    def mark_imported(self, name: str) -> None:
        """`name` 묶음을 옮겼다고 기록한다. 다른 기록 줄은 그대로 둔다."""

        def run() -> None:
            records = dict(self.group(IMPORTED_ENV_PREFIX))
            records[f"{IMPORTED_ENV_PREFIX}{name}"] = datetime.now(UTC).date().isoformat()
            self.replace_group(IMPORTED_ENV_PREFIX, _IMPORTED_HEADER, sorted(records.items()))

        self.update(run)

    def group(self, prefix: str) -> Mapping[str, str]:
        """`prefix`로 시작하는 이름만."""
        return {name: value for name, value in self.read().items() if name.startswith(prefix)}

    def replace_group(self, prefix: str, header: str, entries: Sequence[tuple[str, str]]) -> None:
        """`prefix`로 시작하는 줄과 `# {header}` 줄을 지우고 그 자리에 머리 주석과
        `entries`를 쓴다.

        `entries`가 비면 묶음을 통째로 지운다. 묶음이 처음이면 파일 끝에 빈 줄 하나를 두고 붙인다.
        결과가 지금 파일과 같으면 쓰지 않는다(없는 파일에서 지우기도 아무것도 만들지 않는다).
        """
        for name, _ in entries:
            if not name.startswith(prefix) or not ENV_NAME.fullmatch(name):
                raise ValueError(
                    f"entry name is outside its group — name={name!r} prefix={prefix!r}"
                )
        header_line = f"# {header}"
        with self._lock:
            while True:
                # 쓰기 경로는 캐시를 쓰지 않고 늘 새로 읽는다(재리뷰 8).
                snapshot = self._read(fresh=True)
                body = _merged(snapshot, prefix, header_line, entries)
                if body == snapshot.text or (snapshot.key is None and not body):
                    return
                if self._lock.ensure_os_lock():
                    # 폴더가 없어 OS 잠금을 미뤘다가 지금 잡았다. 그 사이
                    # 다른 프로세스가 먼저 썼을 수
                    # 있으니 다시 읽어 확인한다(재리뷰 1).
                    again = self._read(fresh=True)
                    if (again.key is None) != (snapshot.key is None) or again.text != snapshot.text:
                        if self._lock.depth > 1:
                            # 바깥 읽고-고치고-쓰기(`update`)가 옛 내용으로 계산했다. 처음부터 다시.
                            raise _StaleRead
                        continue
                # 원래 파일의 BOM과 줄바꿈(CRLF/LF)을 그대로 둔다(리뷰 5).
                atomic_write_restricted(
                    self._path,
                    ("\ufeff" if snapshot.bom and body else "") + body,
                    platform=self._platform,
                    restrict=self._restrict,
                    label=_LABEL,
                    temporary_prefix=".env-",
                )
                self._snapshot = None
                return

    def update(self, operation: Callable[[], _T]) -> _T:
        """읽고-고치고-쓰기 한 번. 저장소는 여러 줄을 읽어 계산한 뒤 쓰는 일을 이 안에서 한다.

        폴더가 없던 첫 쓰기에서 다른 프로세스가 먼저 쓴 것을 알게 되면 `operation`을 처음부터 다시
        돌린다(재리뷰 1). 그때는 폴더가 생겨 들어가자마자 OS 잠금을 잡으므로 두 번이면 끝난다.
        """
        for _ in range(_UPDATE_ATTEMPTS):
            try:
                with self._lock:
                    return operation()
            except _StaleRead:
                continue
        raise SecretStoreStorageError(
            f"could not update the {_LABEL} — operation=update attempts={_UPDATE_ATTEMPTS}",
            path=self._path,
            reason="unreadable",
        )

    # -- 파일 IO -----------------------------------------------------------------------------

    def _read(self, *, fresh: bool = False) -> _Snapshot:
        """파일 내용.

        읽기 전용 조회는 크기·수정 시각·변경 시각·inode가 지난번과
        같으면 다시 읽지 않는다(리뷰 9). 그
        값을 믿기 어려운 파일 시스템(inode가 0이거나 수정 시각이 초
        단위로 거친 경우)에서는 캐시를 쓰지
        않는다 — 같은 초 안의 제자리 수정을 놓친다(재리뷰 8). 쓰기 경로는 `fresh`로 늘 새로 읽는다.
        """
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return _Snapshot(None, "", False, "\n")
        except OSError as error:
            raise self._unreadable(error) from error
        key = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        trusted = stat.st_ino != 0 and stat.st_mtime_ns % 1_000_000_000 != 0
        cached = self._snapshot
        if not fresh and trusted and cached is not None and cached.key == key:
            return cached
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return _Snapshot(None, "", False, "\n")
        except OSError as error:
            raise self._unreadable(error) from error
        try:
            # 바이트로 읽는다. 텍스트 모드는 CRLF를 LF로 바꿔 원래 형식을 잃는다(리뷰 5).
            text, bom = decode_env_bytes(raw)
        except UnicodeDecodeError as error:
            raise SecretStoreStorageError(
                f"could not read the {_LABEL} — operation=read reason=not_utf8 "
                f"offset={error.start}",
                path=self._path,
                reason="not_utf8",
            ) from error
        snapshot = _Snapshot(key, text, bom, "\r\n" if "\r\n" in text else "\n")
        self._snapshot = snapshot if trusted else None
        return snapshot

    def _unreadable(self, error: OSError) -> SecretStoreStorageError:
        return SecretStoreStorageError(
            f"could not read the {_LABEL} — operation=read errno={errno_name(error)}",
            path=self._path,
            reason="unreadable",
        )

    def _restrict(self, path: Path) -> None:
        restrict_owner_only(
            path, platform=self._platform, run_command=self._run_command, account=self._account
        )

    def _lock_path(self) -> Path:
        return self._path.parent / (self._path.name + _LOCK_SUFFIX)

    def _acquire_os_lock(self, *, create: bool) -> int | None:
        """OS 잠금을 잡고 그 파일 기술자를 돌려준다. 폴더가 없고 `create`가 아니면 None."""
        if not self._path.parent.exists():
            if not create:
                return None
            prepare_private_directory(self._path, platform=self._platform, label=_LABEL)
        try:
            descriptor = os.open(self._lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not open the {_LABEL} lock — operation=open errno={errno_name(error)}",
                path=self._path,
                reason="unreadable",
            ) from error
        try:
            if self._platform == "win32":
                import msvcrt

                # 첫 바이트 하나를 잠근다. `LK_LOCK`은 1초씩 10번 재시도
                # 뒤 `EDEADLOCK`으로 실패한다.
                # 남이 잡고 있다는 뜻인 그 두 errno만 상한까지 물러나며 다시 부르고, 그 밖의 실패는
                # 바로 알린다(재리뷰 7).
                os.lseek(descriptor, 0, os.SEEK_SET)
                for attempt in range(_WINDOWS_LOCK_ATTEMPTS):
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)  # pyright: ignore[reportAttributeAccessIssue]  # 윈도우 전용
                        break
                    except OSError as error:
                        if error.errno not in _WINDOWS_LOCK_BUSY:
                            raise self._lock_failure(error) from error
                        time.sleep(min(0.05 * 2**attempt, 1.0))
                else:
                    raise SecretStoreStorageError(
                        f"could not lock the {_LABEL} — operation=lock "
                        f"attempts={_WINDOWS_LOCK_ATTEMPTS} reason=busy",
                        path=self._path,
                        reason="unreadable",
                    )
            else:
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                except OSError as error:
                    raise self._lock_failure(error) from error
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _lock_failure(self, error: OSError) -> SecretStoreStorageError:
        return SecretStoreStorageError(
            f"could not lock the {_LABEL} — operation=lock errno={errno_name(error)}",
            path=self._path,
            reason="unreadable",
        )

    def _release_os_lock(self, descriptor: int) -> None:
        try:
            if self._platform == "win32":
                import msvcrt

                with suppress(OSError):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]  # 윈도우 전용
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class _FileLock:
    """스레드 잠금 + OS 파일 잠금. 같은 스레드의 중첩 진입은 OS 잠금을 다시 잡지 않는다.

    잠금은 `.env` 옆 `.env.lock` 파일에 건다. `.env` 자체에 걸면 `os.replace`가 파일을 바꿔치기해
    다음 사람이 다른 inode를 잠그게 된다. POSIX는 `fcntl.flock`, 윈도우는 `msvcrt.locking`이다.

    폴더가 아직 없으면 들어갈 때는 OS 잠금을 미루고(고칠 게 없는 호출이 폴더를 만들지 않게),
    실제로 쓰기 직전(`ensure_os_lock`)에 폴더를 만들며 잡는다. 잡은 뒤에는 `replace_group`이 다시
    읽어, 그 사이 다른 프로세스가 쓴 것이 있으면 다시 계산한다(재리뷰 1).
    """

    def __init__(self, owner: CredentialsEnvFile) -> None:
        self._owner = owner
        self._thread_lock = RLock()
        self._depth = 0
        self._holder: int | None = None
        self._descriptor: int | None = None

    def __enter__(self) -> _FileLock:
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self._holder = get_ident()
                self._descriptor = self._owner._acquire_os_lock(create=False)  # noqa: SLF001 — reason: 짝 클래스
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    @property
    def depth(self) -> int:
        return self._depth

    def ensure_os_lock(self) -> bool:
        """쓰기 직전에 부른다. 아직 OS 잠금이 없으면 폴더를 만들며 잡고 True를 돌려준다."""
        if self._depth > 0 and self._descriptor is None:
            self._descriptor = self._owner._acquire_os_lock(create=True)  # noqa: SLF001 — reason: 짝 클래스
            return True
        return False

    def __exit__(self, *_: object) -> None:
        try:
            self._depth -= 1
            if self._depth == 0:
                self._holder = None
                if self._descriptor is not None:
                    descriptor, self._descriptor = self._descriptor, None
                    self._owner._release_os_lock(descriptor)  # noqa: SLF001 — reason: 짝 클래스
        finally:
            self._thread_lock.release()


class _StaleRead(Exception):
    """미뤘던 OS 잠금을 잡고 보니 바깥 계산이 옛 내용에 기대고 있었다. `update`가 다시 돌린다."""


def _merged(
    snapshot: _Snapshot, prefix: str, header_line: str, entries: Sequence[tuple[str, str]]
) -> str:
    """`snapshot`에서 `prefix` 묶음만 `entries`로 바꾼 본문(BOM 없이, 원래 줄바꿈으로)."""
    kept: list[str] = []
    insert_at: int | None = None
    for line in env_lines(snapshot.text):
        parsed = env_assignment(line)
        ours = line.strip() == header_line or (parsed is not None and parsed[0].startswith(prefix))
        if ours:
            if insert_at is None:
                insert_at = len(kept)
            continue
        kept.append(line)
    block = (
        [header_line, *(f"{name}={encode_env_value(value)}" for name, value in entries)]
        if entries
        else []
    )
    if insert_at is None:
        if block and kept and kept[-1].strip():
            block = ["", *block]
        insert_at = len(kept)
    updated = kept[:insert_at] + block + kept[insert_at:]
    newline = snapshot.newline
    return newline.join(updated) + newline if updated else ""
