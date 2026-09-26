"""두 런타임이 함께 읽는 authoring fixture를 쓴다 (P3-03, P1-03).

- `runtime-schema.json`: StrategyDocument JSON Schema. frontend schema navigator 테스트가 이 파일을
  읽어 두 런타임이 같은 모양을 본다. 파일이 낡으면 `tests/domain/test_strategy_schema.py`가 깨진다.
- `operator-catalog.json`: `GET /api/v1/strategy-documents/operators` 응답. frontend i18n 커버리지
  테스트가 이 파일을 순회해 이름 없는 연산자가 화면에 나가지 못하게 한다(P1-03).
- `parameter-seeds.json`: `default: null`인 property마다 "화면이 새 노드를 만들 때 넣을 값"(씨앗).
  규칙이 TypeScript와 Python 양쪽에 한 벌씩 있어(`schema-navigator.ts`의 `nullDefaultSeed`,
  아래 `_seed_of`) 한쪽만 바뀌면 조용히 어긋난다 — 이 golden이 둘을 묶는다. 두 런타임의 테스트가
  자기 구현으로 같은 표를 만들어 이 파일과 대조한다(P1-04 2차 리뷰 P3).

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


def _declared_type(node: dict[str, Any]) -> str | None:
    """nullable(`anyOf`) 포장을 벗긴 선언 타입."""
    declared = node.get("type")
    if isinstance(declared, str):
        return declared
    for member in node.get("anyOf", []):
        if isinstance(member, dict) and member.get("type") != "null":
            inner = member.get("type")
            return inner if isinstance(inner, str) else None
    return None


def _seed_of(node: dict[str, Any]) -> int | None:
    """`default: null`인 property의 씨앗. 정수이고 하한이 있으면 그 하한, 아니면 None.

    `number`는 제외한다 — null이 "제한 없음"을 뜻하는 선택 값(`portfolio.minimum_liquidity`,
    하한 `0`)을 하한으로 채우면 문서의 뜻이 바뀐다.
    """
    bound = node.get("minimum")
    if not isinstance(bound, int) or isinstance(bound, bool):
        return None
    return bound if _declared_type(node) == "integer" else None


def parameter_seeds(schema: dict[str, Any]) -> dict[str, int | None]:
    """`$defs`의 모든 `default: null` property → 씨앗(없으면 null). 이름은 `<정의>.<property>`."""
    seeds: dict[str, int | None] = {}
    for name, definition in schema.get("$defs", {}).items():
        for property_name, node in definition.get("properties", {}).items():
            if isinstance(node, dict) and node.get("default", _MISSING) is None:
                seeds[f"{name}.{property_name}"] = _seed_of(node)
    return dict(sorted(seeds.items()))


_MISSING = object()


def main() -> None:
    schema = strategy_document_schema()
    _write("runtime-schema.json", schema)
    _write("parameter-seeds.json", {"seeds": parameter_seeds(schema)})
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
