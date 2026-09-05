from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from strategy_workbench.bootstrap import _http, _server


class _UvicornCommand:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def main(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_server_entrypoint_owns_defaults_and_forwards_overrides(monkeypatch) -> None:
    command = _UvicornCommand()
    monkeypatch.setattr(_server, "uvicorn_cli", command)
    monkeypatch.setattr(sys, "argv", ["server", "--port", "8123"])

    _server.main()

    assert command.calls == [
        {
            "args": [
                "strategy_workbench.bootstrap.facade.http:build_runtime_http_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--reload",
                "--port",
                "8123",
            ],
            "prog_name": "server",
        }
    ]


def test_http_runtime_owns_the_durable_repository_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(_http.STRATEGY_REPOSITORY_PATH_ENV, raising=False)
    assert _http.runtime_strategy_repository_path() == _http.DEFAULT_STRATEGY_REPOSITORY_PATH

    configured = tmp_path / "runtime" / "strategies.sqlite3"
    monkeypatch.setenv(_http.STRATEGY_REPOSITORY_PATH_ENV, str(configured))

    assert _http.runtime_strategy_repository_path() == configured


def test_importing_and_building_test_app_never_opens_the_runtime_database(
    monkeypatch, tmp_path: Path
) -> None:
    configured = tmp_path / "runtime" / "strategies.sqlite3"
    monkeypatch.setenv(_http.STRATEGY_REPOSITORY_PATH_ENV, str(configured))
    environment = os.environ.copy()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from strategy_workbench.bootstrap.facade.http import build_http_app; build_http_app()",
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert not configured.exists()
    _http.build_runtime_http_app()
    assert configured.is_file()
