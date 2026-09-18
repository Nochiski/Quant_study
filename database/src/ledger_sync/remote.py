"""원격 파일시스템 경계 — `RemoteFS` 프로토콜과 paramiko SFTP 구현.

계정이 SFTP 전용(쉘 없음)이라 rsync·원격 해시 계산이 불가하다. 따라서 원격에서 얻을 수 있는 사실은
디렉토리 목록(이름·크기·mtime)과 파일 바이트뿐이고, 동기화·검증 논리는 전부 그 위에서 돈다.
paramiko 는 이 모듈 안에서만, 그것도 접속 시점에만 import 한다 — 테스트와 오프라인 검증은 가짜
`RemoteFS` 로 돌고 backend 환경(paramiko 없음)에서도 패키지를 import 할 수 있어야 한다.
"""
from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:  # paramiko 는 접속 시점에만 import — 타입은 typeshed 스텁으로 본다
    from paramiko import SFTPClient, SSHClient

Progress = Callable[[int, int], None]
"""(전송된 바이트, 전체 바이트) 콜백."""

T = TypeVar("T")


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    size: int
    mtime: int
    is_dir: bool


class RemoteFS(Protocol):
    def listdir(self, path: str) -> list[RemoteEntry]:
        """디렉토리 항목. 없으면 FileNotFoundError."""
        ...

    def read_bytes(self, path: str) -> bytes:
        """작은 파일(MANIFEST·메타) 전체. 없으면 FileNotFoundError."""
        ...

    def download(self, path: str, local: Path, progress: Progress | None = None) -> int:
        """`path` 를 `local` 에 통째로 받고 받은 바이트 수를 돌려준다. 부모 디렉토리는 만들어
        둔다."""
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class SftpEndpoint:
    host: str
    user: str
    key_path: Path
    port: int = 22

    @property
    def label(self) -> str:
        return f"{self.user}@{self.host}:{self.port} key={self.key_path}"


class RemoteConnectError(RuntimeError):
    """접속·인증·호스트키 실패. 재시도해도 같은 결과라 즉시 중단한다."""


class RemoteTransferError(RuntimeError):
    """재시도 뒤에도 실패한 전송·목록 오류."""


def known_hosts_path() -> Path:
    return Path(os.path.expanduser("~/.ssh/known_hosts"))


def _is_missing(error: OSError) -> bool:
    return isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT


def append_known_host(known_hosts: Path, hostname: str, key_name: str, key_base64: str) -> None:
    """`--accept-new` 가 수락한 호스트키를 known_hosts 에 **한 줄만 덧붙인다**.

    paramiko `AutoAddPolicy` 는 `save_host_keys` 로 파일을 통째로 다시 써서 주석·`@cert-authority`·
    `@revoked` 마커·paramiko 가 모르는 키 타입 행을 잃는다. 우리는 기존 행을 절대 다시 쓰지 않는다.
    """
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    with known_hosts.open("a", encoding="utf-8") as handle:
        handle.write(f"{hostname} {key_name} {key_base64}\n")


class SftpRemote:
    """paramiko 기반 `RemoteFS`. 전송 오류는 재접속 + 지수 백오프로 `retries` 회 재시도한다."""

    def __init__(self, endpoint: SftpEndpoint, *, accept_new_host_key: bool = False,
                 retries: int = 3, connect_timeout_s: float = 30.0) -> None:
        self._endpoint = endpoint
        self._accept_new = accept_new_host_key
        self._retries = max(1, retries)
        self._timeout = connect_timeout_s
        self._client: SSHClient | None = None
        self._sftp: SFTPClient | None = None

    # ── 접속 ──────────────────────────────────────────────────────────────
    def _connect(self) -> SFTPClient:
        import paramiko

        client = paramiko.SSHClient()
        known_hosts = known_hosts_path()
        if known_hosts.exists():
            # `load_host_keys` 가 아니라 `load_system_host_keys` — 전자는 파일명을 기억해 뒀다가
            # `AutoAddPolicy` 가 `save_host_keys` 로 파일 전체를 다시 쓰게 만든다(주석·`@revoked`·
            # 미지원 키 행이 사라진다). 우리는 파일을 절대 다시 쓰지 않는다.
            client.load_system_host_keys(str(known_hosts))
        if self._accept_new:
            class AppendOnlyAcceptNew(paramiko.MissingHostKeyPolicy):
                """모르는 호스트키만 수락(메모리 + known_hosts 한 줄 append). 바뀐 키는
                `BadHostKeyException` 이 정책보다 먼저 나므로 여전히 거부된다 — OpenSSH
                `StrictHostKeyChecking=accept-new` 와 같은 의미."""

                def missing_host_key(self, client: paramiko.SSHClient, hostname: str,
                                     key: paramiko.PKey) -> None:
                    client.get_host_keys().add(hostname, key.get_name(), key)
                    append_known_host(known_hosts, hostname, key.get_name(), key.get_base64())

            client.set_missing_host_key_policy(AppendOnlyAcceptNew())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        ep = self._endpoint
        if not ep.key_path.exists():
            raise RemoteConnectError(f"private key not found — {ep.label}")
        try:
            client.connect(ep.host, port=ep.port, username=ep.user, key_filename=str(ep.key_path),
                           look_for_keys=False, allow_agent=False, timeout=self._timeout,
                           banner_timeout=self._timeout, auth_timeout=self._timeout)
        except paramiko.BadHostKeyException as error:
            raise RemoteConnectError(
                f"host key mismatch — {ep.label} known_hosts={known_hosts} error={error!r}"
            ) from error
        except paramiko.AuthenticationException as error:
            raise RemoteConnectError(
                f"authentication failed — {ep.label} error={error!r} "
                f"(is the private key the one registered on the server?)"
            ) from error
        except paramiko.SSHException as error:
            unknown_host = "not found in known_hosts" in str(error)
            hint = " (unknown host key — pass --accept-new once)" \
                if unknown_host and not self._accept_new else ""
            raise RemoteConnectError(
                f"ssh connect failed — {ep.label} error={error!r}{hint}"
            ) from error
        except OSError as error:
            raise RemoteConnectError(f"socket error — {ep.label} error={error!r}") from error
        sftp = client.open_sftp()
        channel = sftp.get_channel()
        if channel is not None:
            channel.settimeout(self._timeout * 4)
        self._client, self._sftp = client, sftp
        return sftp

    def _session(self) -> SFTPClient:
        if self._sftp is None:
            return self._connect()
        return self._sftp

    def close(self) -> None:
        for closable in (self._sftp, self._client):
            if closable is not None:
                try:
                    closable.close()
                except Exception:  # noqa: BLE001  # reason: 종료 중 소켓 오류는 무시해도 안전
                    pass
        self._sftp = self._client = None

    def _retry(self, what: str, operation: Callable[[], T]) -> T:
        import paramiko

        last: BaseException | None = None
        for attempt in range(1, self._retries + 1):
            try:
                return operation()
            except FileNotFoundError:
                raise
            except (OSError, EOFError, paramiko.SSHException, RemoteConnectError) as error:
                # RemoteConnectError 는 끊긴 뒤 재접속이 아직 안 되는 경우다 — 백오프 뒤 다시
                # 시도하고, 끝내 안 되면 RemoteTransferError 로 바꿔 호출부(테이블 격리)가 잡는다.
                last = error
            self.close()
            if attempt < self._retries:
                time.sleep(2 ** (attempt - 1))
        raise RemoteTransferError(
            f"{what} failed after {self._retries} attempts — {self._endpoint.label} error={last!r}"
        )

    # ── RemoteFS ──────────────────────────────────────────────────────────
    def listdir(self, path: str) -> list[RemoteEntry]:
        def op() -> list[RemoteEntry]:
            sftp = self._session()
            try:
                attrs = sftp.listdir_attr(path)
            except OSError as error:
                if _is_missing(error):
                    raise FileNotFoundError(f"remote dir not found — path={path}") from error
                raise
            return [RemoteEntry(a.filename, int(a.st_size or 0), int(a.st_mtime or 0),
                                stat.S_ISDIR(a.st_mode or 0)) for a in attrs]
        return self._retry(f"listdir {path}", op)

    def read_bytes(self, path: str) -> bytes:
        def op() -> bytes:
            sftp = self._session()
            try:
                with sftp.open(path, "rb") as handle:
                    return handle.read()
            except OSError as error:
                if _is_missing(error):
                    raise FileNotFoundError(f"remote file not found — path={path}") from error
                raise
        return self._retry(f"read {path}", op)

    def download(self, path: str, local: Path, progress: Progress | None = None) -> int:
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")

        def op() -> int:
            sftp = self._session()
            try:
                sftp.get(path, str(part), callback=progress)
            except OSError as error:
                if _is_missing(error):
                    raise FileNotFoundError(f"remote file not found — path={path}") from error
                raise
            size = part.stat().st_size
            os.replace(part, local)
            return size
        try:
            return self._retry(f"download {path}", op)
        finally:
            if part.exists():
                part.unlink()


@contextmanager
def open_sftp(endpoint: SftpEndpoint, *,
              accept_new_host_key: bool = False) -> Iterator[SftpRemote]:
    remote = SftpRemote(endpoint, accept_new_host_key=accept_new_host_key)
    try:
        yield remote
    finally:
        remote.close()
