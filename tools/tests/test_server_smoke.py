from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
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
