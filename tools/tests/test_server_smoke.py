from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


class RootServerSmokeTest(unittest.TestCase):
    def test_root_entrypoint_serves_health_over_http(self) -> None:
        root = Path(__file__).resolve().parents[2]
        port = _available_port()
        runtime_directory = tempfile.TemporaryDirectory()
        self.addCleanup(runtime_directory.cleanup)
        environment = os.environ.copy()
        environment["STRATEGY_WORKBENCH_DB_PATH"] = str(
            Path(runtime_directory.name) / "strategy-revisions.sqlite3"
        )
        environment["STRATEGY_WORKBENCH_RESEARCH_DB_PATH"] = str(
            Path(runtime_directory.name) / "research.sqlite3"
        )
        environment["STRATEGY_WORKBENCH_CONTINUOUS_DB_PATH"] = str(
            Path(runtime_directory.name) / "continuous-accounts.sqlite3"
        )
        # 서버는 부팅 때 산출물 루트의 고아 staging을 지우고 어시스턴트 DB를 연다. 개발 서버의
        # 기본 `.local` 상태를 건드리지 않도록 이 실행의 임시 폴더로 격리한다(#495).
        environment["STRATEGY_WORKBENCH_ARTIFACT_ROOT"] = str(
            Path(runtime_directory.name) / "backtest-runs"
        )
        environment["STRATEGY_WORKBENCH_ASSISTANT_DB_PATH"] = str(
            Path(runtime_directory.name) / "assistant.sqlite3"
        )
        environment["STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH"] = str(
            Path(runtime_directory.name) / "secrets.json"
        )
        # AI 공급자·키움 연결 저장 파일. 비우면 개발자의 실제 내 문서 `auto_trading/.env`를 연다.
        environment["STRATEGY_WORKBENCH_CREDENTIALS_ENV_PATH"] = str(
            Path(runtime_directory.name) / "auto_trading.env"
        )
        # 개발자 셸의 실자료·공급자 선택과 무관하게 부팅하며 중첩 uv가 extras를 지우지 않는다.
        environment["STRATEGY_WORKBENCH_EQUITY_ADAPTER"] = "mock"
        environment.pop("STRATEGY_WORKBENCH_EQUITY_ROOT", None)
        environment["STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER"] = "1"
        environment["UV_NO_SYNC"] = "1"
        process = subprocess.Popen(
            [
                "uv",
                "run",
                "server",
                "--port",
                str(port),
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            env=environment,
        )
        response_body: dict[str, str] | None = None
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout is not None else ""
                    self.fail(
                        f"root server exited with {process.returncode} before health check:\n{output}"
                    )
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/v1/health",
                        timeout=1,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        response_body = json.loads(response.read())
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.2)
            self.assertEqual(response_body, {"status": "ok"})
        finally:
            _stop_process_group(process)
            if process.stdout is not None:
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
