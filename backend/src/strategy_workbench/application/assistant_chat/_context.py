"""시스템 프롬프트와 도구 실행 (설계 spec D3/D8).

도구는 application이 소유하고 실행한다. 공급자 adapter는 이름조차 모른다. 여기서 하는 일은 두
가지다.

1. `system_prompt()` — runtime schema를 걸어 전략 언어 요약을 **생성**하고 템플릿에 채운다.
   필드 이름·enum 값을 손으로 적지 않는 것이 요점이다(SoT 규칙).
2. `tool_result()` — 모델이 부른 도구를 실행해 모델에게 돌려줄 텍스트를 만든다.
   `propose_strategy`만은 세션 상태(시도 횟수)와 이벤트 방출이 필요해 `AssistantChatService`가
   가로챈다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from functools import cached_property

from strategy_workbench.application.equity_workspace.facade.ports import EquityDataPort
from strategy_workbench.domain.assistant.facade.models import ToolCall, ToolResult
from strategy_workbench.domain.assistant.facade.tools import (
    LIST_EQUITY_FIELDS,
    LIST_FACTOR_CATALOG,
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
    VALIDATE_STRATEGY_YAML,
)
from strategy_workbench.domain.factor.facade.registry import FactorRegistry
from strategy_workbench.domain.strategy.facade.schema import strategy_document_schema

from ._models import TurnContext, compile_payload
from ._prompt import SYSTEM_PROMPT_TEMPLATE
from .ports.outgoing.strategy_compiler import StrategyCompilerPort

__all__ = ["AssistantContextBuilder"]

# JSON Schema 방언의 어휘. 전략 언어의 필드 이름이 아니므로 여기 적어도 SoT 규칙에 걸리지 않는다.
# 판별자 **키 이름**(현재는 "kind")은 전략 언어의 어휘라 손으로 적지 않고 스키마에서 읽는다.
_DISCRIMINATOR_MARKER = "discriminator"
_DISCRIMINATOR_NAME = "propertyName"


class AssistantContextBuilder:
    """모델이 보는 사실을 모은다: 전략 언어 요약, 데이터·팩터 카탈로그, 현재 문서, 검증 결과."""

    def __init__(
        self,
        *,
        equity_data: EquityDataPort,
        factor_registry: FactorRegistry,
        compiler: StrategyCompilerPort,
        today: Callable[[], date],
        schema: Callable[[], Mapping[str, object]] = strategy_document_schema,
    ) -> None:
        self._equity_data = equity_data
        self._factor_registry = factor_registry
        self._compiler = compiler
        self._today = today
        self._schema = schema

    @cached_property
    def _language_summary(self) -> str:
        return _summarise_schema(self._schema())

    def system_prompt(self) -> str:
        """오늘 날짜와 스키마에서 생성한 언어 요약을 채운 시스템 프롬프트."""
        return SYSTEM_PROMPT_TEMPLATE.format(
            today=self._today().isoformat(),
            tool_read=READ_CURRENT_STRATEGY,
            tool_fields=LIST_EQUITY_FIELDS,
            tool_factors=LIST_FACTOR_CATALOG,
            tool_validate=VALIDATE_STRATEGY_YAML,
            tool_propose=PROPOSE_STRATEGY,
            schema_summary=self._language_summary,
        )

    def tool_result(self, call: ToolCall, context: TurnContext) -> ToolResult:
        """도구 하나를 실행한다. 모르는 이름은 예외가 아니라 도구 오류로 돌려준다.

        모델이 없는 도구를 부르는 것은 예상된 실패다. 예외로 올리면 턴 전체가 죽지만, 오류
        결과로 돌려주면 모델이 카탈로그 안의 도구로 고쳐 부른다.
        """
        if call.name == READ_CURRENT_STRATEGY:
            return _ok(call, _current_strategy_payload(context))
        if call.name == LIST_EQUITY_FIELDS:
            return _ok(call, self._equity_fields_payload())
        if call.name == LIST_FACTOR_CATALOG:
            return _ok(call, self._factor_catalog_payload())
        if call.name == VALIDATE_STRATEGY_YAML:
            return self._validate(call)
        return ToolResult(
            call_id=call.call_id,
            ok=False,
            content=(
                f"지원하지 않는 도구입니다 — name={call.name!r}. 도구 목록에 있는 이름만 "
                "사용하세요."
            ),
        )

    def _equity_fields_payload(self) -> dict[str, object]:
        return {
            "fields": [
                {
                    "id": profile.field_id,
                    "label": profile.label,
                    "unit": profile.unit,
                    "description": profile.description,
                }
                for profile in self._equity_data.list_fields()
            ]
        }

    def _factor_catalog_payload(self) -> dict[str, object]:
        return {
            "factors": [
                {
                    "id": definition.factor_id,
                    "label": definition.label,
                    "direction": definition.preference.value,
                    "availability": definition.availability.value,
                    "required_field_ids": list(definition.required_field_ids),
                }
                for definition in self._factor_registry.all()
            ]
        }

    def _validate(self, call: ToolCall) -> ToolResult:
        source_text = call.arguments.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                content=(
                    "source_text 인자에 전략 문서 YAML 원문 전체를 넣어 다시 호출하세요 — "
                    f"name={call.name!r} received={type(source_text).__name__}"
                ),
            )
        outcome = self._compiler.compile(source_text)
        return ToolResult(
            call_id=call.call_id,
            ok=outcome.ok,
            content=json.dumps(compile_payload(outcome), ensure_ascii=False),
        )


def _ok(call: ToolCall, payload: Mapping[str, object]) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        ok=True,
        content=json.dumps(payload, ensure_ascii=False),
    )


def _current_strategy_payload(context: TurnContext) -> dict[str, object]:
    return {
        "source_text": context.source_text,
        "source_format": context.source_format,
        "environment": dict(context.environment) if context.environment is not None else None,
        "diagnostics": list(context.diagnostics),
    }


# -- runtime schema → 자연어 요약 ---------------------------------------------------------------


def _summarise_schema(schema: Mapping[str, object]) -> str:
    """스키마를 걸어 모델이 읽을 언어 요약을 만든다. 어휘를 손으로 적지 않는 유일한 경로다."""
    properties = _mapping(schema.get("properties"))
    required = {str(name) for name in _sequence(schema.get("required"))}
    definitions = _mapping(schema.get("$defs"))
    lines = [
        "- 최상위 키: "
        + ", ".join(f"{name}{' (필수)' if name in required else ''}" for name in properties)
    ]
    for name, member in properties.items():
        fixed = _mapping(member).get("const")
        if isinstance(fixed, str):
            lines.append(f"- {name} 고정값: {fixed}")
    discriminator = _discriminator_key(schema)
    kinds = (
        [
            value
            for member in definitions.values()
            if (value := _discriminator_of(member, discriminator)) is not None
        ]
        if discriminator is not None
        else []
    )
    if kinds:
        lines.append(f"- 사용할 수 있는 {discriminator} 값: {', '.join(kinds)}")
    # 같은 값 집합을 쓰는 필드는 한 줄로 묶는다. 같은 enum이 스무 번 반복되면 프롬프트만 길어지고
    # 모델이 읽어야 할 사실은 늘지 않는다. 소유자는 전부 적어 어떤 필드에 쓰는지는 잃지 않는다.
    owners_by_values: dict[tuple[str, ...], list[str]] = {}
    for owner, member in definitions.items():
        for field_name, field_schema in _mapping(_mapping(member).get("properties")).items():
            values = _enum_values(field_schema)
            if not values:
                continue
            owners_by_values.setdefault(values, []).append(f"{owner}.{field_name}")
    for values, owners in owners_by_values.items():
        lines.append(f"- {', '.join(owners)} 값: {' | '.join(values)}")
    return "\n".join(lines)


def _discriminator_key(schema: Mapping[str, object]) -> str | None:
    """스키마가 판별 union에 쓰는 속성 이름. 없으면 None.

    `"kind"`를 손으로 적지 않으려고 스키마가 스스로 붙인 `discriminator.propertyName`을 읽는다.
    리터럴로 두면 판별자 키가 바뀌는 날 요약의 "사용할 수 있는 … 값" 줄이 조용히 사라지고, 모델은
    노드 종류를 모른 채 제안을 만들어 검증 왕복만 늘린다(SoT 규칙: 필드 이름을 손으로 적지 않는다).
    """
    return _find_discriminator(schema)


def _find_discriminator(node: object) -> str | None:
    mapping = _mapping(node)
    marker = mapping.get(_DISCRIMINATOR_MARKER)
    if isinstance(marker, Mapping):
        name = marker.get(_DISCRIMINATOR_NAME)
        if isinstance(name, str) and name:
            return name
    for value in mapping.values():
        if isinstance(value, Mapping):
            found = _find_discriminator(value)
            if found is not None:
                return found
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for item in value:
                found = _find_discriminator(item)
                if found is not None:
                    return found
    return None


def _discriminator_of(member: object, discriminator: str) -> str | None:
    fixed = _mapping(_mapping(_mapping(member).get("properties")).get(discriminator)).get("const")
    return fixed if isinstance(fixed, str) else None


def _enum_values(field_schema: object) -> tuple[str, ...]:
    node = _mapping(field_schema)
    alternatives = node.get("anyOf")
    if isinstance(alternatives, Sequence) and not isinstance(alternatives, str | bytes):
        for alternative in alternatives:
            values = _enum_values(alternative)
            if values:
                return values
        return ()
    items = node.get("items")
    if items is not None:
        return _enum_values(items)
    raw = node.get("enum")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(str(value) for value in raw)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()
