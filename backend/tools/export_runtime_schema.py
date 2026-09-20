"""두 런타임이 함께 읽는 authoring fixture를 쓴다 (P3-03, P1-03).

- `runtime-schema.json`: StrategyDocument JSON Schema. frontend schema navigator 테스트가 이 파일을
  읽어 두 런타임이 같은 모양을 본다. 파일이 낡으면 `tests/domain/test_strategy_schema.py`가 깨진다.
- `operator-catalog.json`: `GET /api/v1/strategy-documents/operators` 응답. frontend i18n 커버리지
  테스트가 이 파일을 순회해 이름 없는 연산자가 화면에 나가지 못하게 한다(P1-03).

`backend/`에서 실행한다:

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
