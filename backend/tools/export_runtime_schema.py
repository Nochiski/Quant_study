"""Write the runtime authoring fixtures both runtimes read (P3-03, P1-03).

- `runtime-schema.json`: the StrategyDocument JSON Schema. The frontend schema navigator tests
  read it so both runtimes see one shape; `tests/domain/test_strategy_schema.py` fails when it
  is stale.
- `operator-catalog.json`: the `GET /api/v1/strategy-documents/operators` payload. The frontend
  i18n coverage test walks it so no operator reaches a screen without a name (P1-03).

Run from `backend/`:

    uv run python tools/export_runtime_schema.py
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from strategy_workbench.application.strategy_authoring.facade.authoring import (
    operator_catalog_hash,
)
from strategy_workbench.domain.factor.facade.operators import operator_definitions
from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "strategy_documents"

NEWLINE = "\n"


def _write(name: str, payload: Any) -> None:
    path = FIXTURES / name
    text = json.dumps(payload, indent=2, ensure_ascii=False) + NEWLINE
    path.write_text(text, encoding="utf-8", newline=NEWLINE)
    print(f"wrote {path.relative_to(path.parents[3])}")


def main() -> None:
    _write("runtime-schema.json", strategy_document_schema())
    operators = operator_definitions()
    _write(
        "operator-catalog.json",
        {
            "catalog_hash": operator_catalog_hash(operators),
            "operators": [asdict(definition) for definition in operators],
        },
    )


if __name__ == "__main__":
    main()
