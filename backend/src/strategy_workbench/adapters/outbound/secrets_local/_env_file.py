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
4. 읽기는 매번 파일에서 한다. 사용자가 손으로 고친 값이 서버를 다시 켜지 않아도 반영된다.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from threading import RLock

from ._errors import SecretStoreStorageError
from ._store import CommandRunner, _errno_name, default_windows_account, icacls_command, run_icacls

__all__ = [
    "AUTO_TRADING_DIRECTORY_NAME",
    "AUTO_TRADING_ENV_FILE_NAME",
    "CredentialsEnvFile",
    "default_auto_trading_env_path",
    "parse_env_text",
    "resolve_documents_directory",
]

AUTO_TRADING_DIRECTORY_NAME = "auto_trading"
AUTO_TRADING_ENV_FILE_NAME = ".env"

# 따옴표 없이 써도 다시 같은 값으로 읽히는 글자들. 그 밖의 값은 큰따옴표로 감싸고 이스케이프한다.
_BARE_VALUE = re.compile(r"[A-Za-z0-9_\-.:/+@,=]*")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r"}

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
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]  # reason: 윈도우 전용 속성
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer)) != 0:
            return None
        try:
            return buffer.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)  # type: ignore[attr-defined]  # reason: 윈도우 전용
    except (OSError, AttributeError, ValueError):
        return None


# -- .env 문법 ---------------------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """`NAME=value` 줄을 모두 읽는다. 같은 이름이 여러 번 나오면 마지막 값이다.

    `export ` 앞머리, 큰따옴표(`\\\\`·`\\"`·`\\n`·`\\r` 이스케이프), 작은따옴표(그대로),
    따옴표 없는 값의
    줄 끝 주석(` #`)을 다룬다.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        parsed = _assignment(raw)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    return values


def _assignment(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    name, separator, value = line.removeprefix("export ").partition("=")
    name = name.strip()
    if not separator or not _NAME.fullmatch(name):
        return None
    return name, _decode_value(value.strip())


def _decode_value(text: str) -> str:
    if text.startswith('"'):
        decoded: list[str] = []
        index = 1
        while index < len(text):
            char = text[index]
            if char == '"':
                return "".join(decoded)
            if char == "\\" and index + 1 < len(text):
                following = text[index + 1]
                decoded.append(_UNESCAPES.get(following, "\\" + following))
                index += 2
                continue
            decoded.append(char)
            index += 1
        # 닫는 따옴표가 없으면 그대로 둔다(손으로 고친 파일의 실수를 값으로 보이게).
        return text
    if text.startswith("'"):
        end = text.find("'", 1)
        if end > 0:
            return text[1:end]
        return text
    return text.split(" #", 1)[0].rstrip()


def _encode_value(value: str) -> str:
    if _BARE_VALUE.fullmatch(value):
        return value
    return '"' + "".join(_ESCAPES.get(char, char) for char in value) + '"'


# -- 파일 ------------------------------------------------------------------------------------


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
        self._lock = RLock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock(self) -> RLock:
        """읽고-고치고-쓰는 저장소가 한 번에 잡는 잠금. 다시 들어갈 수 있다."""
        return self._lock

    def exists(self) -> bool:
        return self._path.exists()

    def read(self) -> dict[str, str]:
        """모든 이름과 값. 파일이 없으면 빈 사전."""
        with self._lock:
            return parse_env_text(self._read_text())

    def replace_group(self, prefix: str, header: str, entries: Sequence[tuple[str, str]]) -> None:
        """`prefix`로 시작하는 줄과 `# {header}` 줄을 지우고 그 자리에 머리 주석과
        `entries`를 쓴다.

        `entries`가 비면 묶음을 통째로 지운다. 묶음이 처음이면 파일 끝에 빈 줄 하나를 두고 붙인다.
        """
        for name, _ in entries:
            if not name.startswith(prefix) or not _NAME.fullmatch(name):
                raise ValueError(
                    f"entry name is outside its group — name={name!r} prefix={prefix!r}"
                )
        header_line = f"# {header}"
        with self._lock:
            lines = self._read_text().splitlines()
            kept: list[str] = []
            insert_at: int | None = None
            for line in lines:
                parsed = _assignment(line)
                ours = line.strip() == header_line or (
                    parsed is not None and parsed[0].startswith(prefix)
                )
                if ours:
                    if insert_at is None:
                        insert_at = len(kept)
                    continue
                kept.append(line)
            block = (
                [header_line, *(f"{name}={_encode_value(value)}" for name, value in entries)]
                if entries
                else []
            )
            if insert_at is None:
                if block and kept and kept[-1].strip():
                    block = ["", *block]
                insert_at = len(kept)
            updated = kept[:insert_at] + block + kept[insert_at:]
            self._write_text("\n".join(updated) + "\n" if updated else "")

    def group(self, prefix: str) -> Mapping[str, str]:
        """`prefix`로 시작하는 이름만."""
        return {name: value for name, value in self.read().items() if name.startswith(prefix)}

    # -- 파일 IO -----------------------------------------------------------------------------

    def _read_text(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeDecodeError) as error:
            detail = (
                f"errno={_errno_name(error)}" if isinstance(error, OSError) else "reason=not_utf8"
            )
            raise SecretStoreStorageError(
                f"could not read the credentials env file — operation=read {detail}",
                path=self._path,
            ) from error

    def _write_text(self, text: str) -> None:
        directory = self._prepare_directory()
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=directory, prefix=".env-", suffix=".tmp"
            )
            os.close(descriptor)
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not create the credentials env temporary file — operation=mkstemp "
                f"errno={_errno_name(error)}",
                path=self._path,
            ) from error
        temporary = Path(temporary_name)
        try:
            # 내용보다 권한이 먼저다. 순서가 바뀌면 평문이 잠깐 느슨한 권한으로 디스크에 눕는다.
            self._restrict(temporary)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            self._discard(temporary)
            raise SecretStoreStorageError(
                f"could not write the credentials env file — operation=replace "
                f"errno={_errno_name(error)}",
                path=self._path,
            ) from error
        except BaseException:
            self._discard(temporary)
            raise
        self._sync_directory(directory)

    def _prepare_directory(self) -> Path:
        """`auto_trading` 폴더. 이 모듈이 만들었을 때만 0700으로 좁힌다.

        다른 도구가 함께 쓰는 폴더일 수 있다.
        """
        directory = self._path.parent
        try:
            directory.mkdir(parents=True)
        except FileExistsError:
            return directory
        except OSError as error:
            raise SecretStoreStorageError(
                f"could not create the credentials env directory — operation=mkdir "
                f"errno={_errno_name(error)}",
                path=self._path,
            ) from error
        if self._platform != "win32":
            try:
                os.chmod(directory, 0o700)
            except OSError as error:
                raise SecretStoreStorageError(
                    f"could not restrict the credentials env directory — operation=chmod "
                    f"errno={_errno_name(error)}",
                    path=self._path,
                ) from error
        return directory

    def _sync_directory(self, directory: Path) -> None:
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
        with suppress(OSError):
            temporary.unlink()
