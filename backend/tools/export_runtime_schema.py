"""Write the runtime StrategyDocument JSON Schema to the shared test fixtures (P3-03).

The frontend schema navigator tests read
`backend/tests/fixtures/strategy_documents/runtime-schema.json` so both runtimes see one shape;
`tests/domain/test_strategy_schema.py` fails when this file is stale. Run from `backend/`:

    uv run python tools/export_runtime_schema.py
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "strategy_documents"
    / "runtime-schema.json"
)


def main() -> None:
    text = json.dumps(strategy_document_schema(), indent=2, ensure_ascii=False) + "\n"
    FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {FIXTURE.relative_to(FIXTURE.parents[3])}")


if __name__ == "__main__":
    main()
