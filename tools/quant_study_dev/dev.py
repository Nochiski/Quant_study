"""설치된 개발 환경으로 두 서버를 시작하고 소유한 자식만 함께 정리한다."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .rust_core import ensure_core
from .server import _repo_root

FRONTEND_URL = "http://localhost:5173"
READINESS_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class DevServer:
    name: str
    command: tuple[str, ...]
    cwd: Path
    port: int
    readiness_url: str


@dataclass(frozen=True)
class OwnedServer:
    server: DevServer
    process: subprocess.Popen[bytes]


def _servers(root: Path) -> tuple[DevServer, ...]:
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if uv is None or npm is None:
        missing = "uv" if uv is None else "npm (Node.js)"
        raise RuntimeError(
            f"실행 파일을 찾지 못했습니다: {missing}. 개발 환경을 먼저 준비해 주세요."
        )
    return (
        DevServer(
            "백엔드",
            (uv, "run", "--no-sync", "--project", str(root / "backend"), "server"),
            root / "backend",
            8000,
            "http://127.0.0.1:8000/api/v1/health",
        ),
        DevServer(
            "프런트엔드",
            (
                npm,
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
                "--strictPort",
            ),
            root,
            5173,
            "http://127.0.0.1:5173/",
        ),
    )


def _require_available_port(server: DevServer) -> None:
    # 시작 전에 검사하며 기존 포트의 프로세스는 찾아내거나 종료하지 않는다.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name != "nt":
            # 직전 서버의 TIME_WAIT를 실행 중인 리스너와 구별해 즉시 재실행을 허용한다.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", server.port))
        except OSError as error:
            raise RuntimeError(
                f"{server.name} 포트 {server.port}를 사용할 수 없습니다. "
                "기존 서버를 직접 종료한 뒤 다시 실행해 주세요."
            ) from error


def _start(server: DevServer) -> OwnedServer:
    environment = os.environ.copy()
    # 루트 uv 환경을 백엔드 프로젝트 환경으로 오인하지 않게 한다. DB·adapter 설정은 보존한다.
    environment.pop("VIRTUAL_ENV", None)
    process = subprocess.Popen(
        server.command,
        cwd=server.cwd,
        env=environment,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    return OwnedServer(server, process)


def _require_running(servers: Sequence[OwnedServer]) -> None:
    for owned in servers:
        code = owned.process.poll()
        if code is not None:
            raise RuntimeError(
                f"{owned.server.name}가 종료되었습니다(exit={code}). "
                "위 서버 로그와 사전 설치된 개발 환경을 확인해 주세요."
            )


def _ready(url: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=0.5) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_until_ready(servers: Sequence[OwnedServer]) -> None:
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    ready: set[str] = set()
    while time.monotonic() < deadline:
        _require_running(servers)
        for owned in servers:
            if owned.server.name not in ready and _ready(owned.server.readiness_url):
                ready.add(owned.server.name)
        if len(ready) == len(servers):
            _require_running(servers)
            return
        time.sleep(0.2)
    pending = ", ".join(
        owned.server.name for owned in servers if owned.server.name not in ready
    )
    raise RuntimeError(
        f"{pending} 준비 확인이 {READINESS_TIMEOUT_SECONDS:g}초 안에 끝나지 않았습니다."
    )


def _interrupt(owned: OwnedServer) -> None:
    try:
        if os.name == "nt":
            # Popen.send_signal은 리더가 끝나면 생략하므로 그룹 ID로 남은 자손에도 보낸다.
            os.kill(owned.process.pid, signal.CTRL_BREAK_EVENT)
        else:
            # npm·uv와 그 자손을 같은 전용 그룹으로 시작했으므로 함께 종료한다.
            os.killpg(owned.process.pid, signal.SIGINT)
    except (OSError, ProcessLookupError):
        pass


def _force_stop(owned: OwnedServer) -> None:
    try:
        if os.name == "nt":
            if owned.process.poll() is None:
                # Windows에는 POSIX killpg가 없다. 직접 시작한 PID 트리만 강제 정리한다.
                subprocess.run(
                    ["taskkill", "/PID", str(owned.process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            # 그룹 리더가 먼저 끝나도 남은 reload·Vite 자식은 별도로 정리한다.
            os.killpg(owned.process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _stop(servers: Sequence[OwnedServer]) -> None:
    for owned in servers:
        _interrupt(owned)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    for owned in servers:
        try:
            owned.process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        finally:
            _force_stop(owned)
        try:
            owned.process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(
                f"[dev] {owned.server.name} 종료를 확인하지 못했습니다.",
                file=sys.stderr,
            )


def run(root: Path, *, open_browser: bool, rust_build: bool = True) -> int:
    """두 서버의 준비와 생존을 감시하고 이 실행이 시작한 프로세스만 정리한다."""
    owned: list[OwnedServer] = []
    try:
        servers = _servers(root)
        for server in servers:
            _require_available_port(server)
        # 포트를 먼저 확인해 이미 떠 있는 서버가 있으면 긴 빌드 없이 바로 거절한다.
        ensure_core(root, disabled=not rust_build)
        for server in servers:
            print(f"[dev] {server.name} 시작(port={server.port})", flush=True)
            owned.append(_start(server))
        _wait_until_ready(owned)
        print(f"[dev] 준비 완료: {FRONTEND_URL} (종료: Ctrl+C)", flush=True)
        if open_browser:
            try:
                opened = webbrowser.open(FRONTEND_URL)
            except webbrowser.Error:
                opened = False
            if not opened:
                print(
                    f"[dev] 브라우저를 열지 못했습니다. {FRONTEND_URL}에 직접 접속해 주세요."
                )
        while True:
            _require_running(owned)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[dev] 두 개발 서버를 종료합니다.", flush=True)
        return 130
    except (OSError, RuntimeError) as error:
        print(f"[dev] {error}", file=sys.stderr)
        return 1
    finally:
        # 종료 중 두 번째 Ctrl+C도 소유한 나머지 자식의 정리를 건너뛰지 않게 한다.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            _stop(owned)
        finally:
            signal.signal(signal.SIGINT, previous)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="백엔드와 프런트엔드 개발 서버를 함께 실행합니다."
    )
    parser.add_argument(
        "--open", action="store_true", help="두 서버가 준비되면 브라우저를 엽니다."
    )
    parser.add_argument(
        "--no-rust-build",
        action="store_true",
        help=(
            "시작 전 Rust 백테스트 코어 자동 빌드를 건너뜁니다"
            " (환경 변수 QUANT_STUDY_DEV_SKIP_RUST_BUILD=1과 같음)."
        ),
    )
    options = parser.parse_args()
    try:
        root = _repo_root()
    except RuntimeError as error:
        print(
            f"[dev] 저장소 안에서 uv run dev를 실행해 주세요: {error}", file=sys.stderr
        )
        raise SystemExit(1) from error
    raise SystemExit(
        run(root, open_browser=options.open, rust_build=not options.no_rust_build)
    )
