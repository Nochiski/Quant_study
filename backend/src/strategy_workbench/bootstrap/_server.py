from __future__ import annotations

import sys

from uvicorn.main import main as uvicorn_cli

APP_TARGET = "strategy_workbench.bootstrap.facade.http:app"
DEFAULT_ARGUMENTS = (
    APP_TARGET,
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
    "--reload",
)


def main() -> None:
    """Run the canonical local ASGI server while allowing explicit CLI overrides."""
    uvicorn_cli.main(
        args=[*DEFAULT_ARGUMENTS, *sys.argv[1:]],
        prog_name="server",
    )
