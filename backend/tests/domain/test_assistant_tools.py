"""domain.assistant 도구 계약 테스트 (A-01).

도구 이름과 JSON Schema는 공급자 adapter가 그대로 옮겨 쓰는 계약이다. 스키마가 strict
(`additionalProperties: false` + 모든 속성이 `required`)가 아니면 모델이 조용히 임의 키를 섞어
보내고, application이 그걸 무시하면서 제안이 lossy해진다. 그래서 여기서 모양을 고정한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

import pytest

from strategy_workbench.domain.assistant.facade.tools import (
    ASSISTANT_TOOLS,
    LIST_EQUITY_FIELDS,
    LIST_FACTOR_CATALOG,
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
    VALIDATE_STRATEGY_YAML,
)

EXPECTED_TOOL_NAMES = (
    READ_CURRENT_STRATEGY,
    LIST_EQUITY_FIELDS,
    LIST_FACTOR_CATALOG,
    VALIDATE_STRATEGY_YAML,
    PROPOSE_STRATEGY,
)


def _object_schemas(
    schema: Mapping[str, object], pointer: str
) -> Iterator[tuple[str, Mapping[str, object]]]:
    """스키마 트리에서 `type: object`인 노드를 전부(중첩 포함) 뽑는다."""
    if schema.get("type") == "object":
        yield pointer, schema
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                yield from _object_schemas(child, f"{pointer}/{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        yield from _object_schemas(items, f"{pointer}/*")


def test_assistant_tools_expose_exactly_the_five_declared_names() -> None:
    assert tuple(tool.name for tool in ASSISTANT_TOOLS) == EXPECTED_TOOL_NAMES


def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in ASSISTANT_TOOLS]

    assert len(set(names)) == len(names)


@pytest.mark.parametrize("tool", ASSISTANT_TOOLS, ids=lambda tool: tool.name)
def test_every_tool_schema_is_strict(tool: object) -> None:
    spec = next(item for item in ASSISTANT_TOOLS if item is tool)
    offenders: list[str] = []
    for pointer, node in _object_schemas(spec.input_schema, ""):
        properties = node.get("properties")
        required = node.get("required")
        names = sorted(properties) if isinstance(properties, Mapping) else []
        if node.get("additionalProperties") is not False:
            offenders.append(f"{pointer or '/'}: additionalProperties is not false")
        if not isinstance(required, list) or sorted(str(name) for name in required) != names:
            offenders.append(f"{pointer or '/'}: required={required!r} expected={names!r}")

    assert offenders == [], f"tool={spec.name} offenders={offenders}"


@pytest.mark.parametrize("tool", ASSISTANT_TOOLS, ids=lambda tool: tool.name)
def test_every_tool_schema_is_json_serialisable(tool: object) -> None:
    spec = next(item for item in ASSISTANT_TOOLS if item is tool)

    assert json.loads(json.dumps(spec.input_schema, ensure_ascii=False)) == spec.input_schema


@pytest.mark.parametrize("tool", ASSISTANT_TOOLS, ids=lambda tool: tool.name)
def test_every_tool_describes_itself_for_the_model(tool: object) -> None:
    spec = next(item for item in ASSISTANT_TOOLS if item is tool)

    assert len(spec.description) >= 20


def test_input_free_tools_take_no_arguments() -> None:
    input_free = {READ_CURRENT_STRATEGY, LIST_EQUITY_FIELDS, LIST_FACTOR_CATALOG}
    for spec in ASSISTANT_TOOLS:
        if spec.name in input_free:
            assert spec.input_schema["properties"] == {}


def test_propose_strategy_requires_the_evidence_fields() -> None:
    spec = next(item for item in ASSISTANT_TOOLS if item.name == PROPOSE_STRATEGY)
    properties = spec.input_schema["properties"]

    assert isinstance(properties, Mapping)
    assert sorted(properties) == ["rationale", "source_text", "sources", "summary", "title"]
