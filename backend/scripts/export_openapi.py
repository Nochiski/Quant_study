from __future__ import annotations

import argparse
import json
from pathlib import Path

from strategy_workbench.bootstrap.facade.http import build_http_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the backend OpenAPI contract.")
    parser.add_argument("output", type=Path, nargs="?", default=Path("openapi.json"))
    args = parser.parse_args()
    app = build_http_app()
    args.output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
