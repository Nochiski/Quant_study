from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .backend_env import backend_process_environment, backend_uv_command


def _repo_root(start: Path | None = None) -> Path:
    """Find the checkout without duplicating backend runtime configuration here."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "backend" / "pyproject.toml").is_file() and (
            candidate / "frontend" / "package.json"
        ).is_file():
            return candidate
    raise RuntimeError("run `uv run server` from the Quant_study checkout")


def main() -> None:
    """Delegate to the backend project's canonical server entrypoint with inherited I/O."""
    root = _repo_root()
    backend = root / "backend"
    # 래퍼 환경을 backend 프로젝트 환경으로 오인하지 않게 하는 정리는 dev와 같은 helper를 쓴다.
    environment = backend_process_environment(os.environ)
    try:
        completed = subprocess.run(
            list(backend_uv_command("uv", backend, "server", *sys.argv[1:], sync=True)),
            cwd=backend,
            check=False,
            env=environment,
        )
    except KeyboardInterrupt:
        # The terminal signal reaches the inherited child process group too. Exit with the
        # conventional code without printing a Python wrapper traceback after Uvicorn stops.
        raise SystemExit(130) from None
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
