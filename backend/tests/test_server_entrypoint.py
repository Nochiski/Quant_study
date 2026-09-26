from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_http_runtime_owns_the_browser_origin_allowlist(monkeypatch) -> None:
    """포트를 옮겨 e2e를 돌릴 때 preview origin을 허용 목록에 넣을 수 있어야 한다.

    목록이 기본값 하나로 고정돼 있으면 `PW_PREVIEW_PORT`로 포트를 옮긴 순간 브라우저 요청이
    CORS로 막힌다 — 서버는 멀쩡한데 화면만 비어, 원인을 짚기 어려운 조합이다.
    """
    monkeypatch.delenv(_http.ALLOWED_ORIGINS_ENV, raising=False)
    assert _http.runtime_allowed_origins() == _http.DEFAULT_ALLOWED_ORIGINS

    monkeypatch.setenv(
        _http.ALLOWED_ORIGINS_ENV, " http://localhost:15173 , http://localhost:5173 "
    )
    assert _http.runtime_allowed_origins() == (
        "http://localhost:15173",
        "http://localhost:5173",
    )

    # 값이 있는데 origin 이 하나도 없으면 조용히 기본값으로 돌아가지 않는다.
    monkeypatch.setenv(_http.ALLOWED_ORIGINS_ENV, " , ")
    with pytest.raises(ValueError, match="lists no origin"):
        _http.runtime_allowed_origins()


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
