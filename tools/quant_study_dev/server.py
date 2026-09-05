from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
    environment = os.environ.copy()
    # The wrapper has its own dependency-free environment. Do not make the nested backend uv
    # process mistake it for the backend project environment (or emit a misleading warning).
    environment.pop("VIRTUAL_ENV", None)
    try:
        completed = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(backend),
                "server",
                *sys.argv[1:],
            ],
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
