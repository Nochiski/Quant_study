"""Runtime JSON Schema and field contract derived from the StrategySpec model (WORKFLOW P1-05).

Nothing here restates a property name, enum value, default or bound. Every fact is read from the
dataclass type hints (`_models.py`, `domain.factor` node types) and the scalar constraint catalog
(`_constraints.py`). The hydrate walker (`_hydrate.py`) reads the same hints, so the schema an
editor navigates and the structural validator the server runs cannot drift.

Shape (JSON Schema 2020-12):

- root object = identity-free authoring document: `schema_version` plus every StrategySpec field
  except `identity`, `additionalProperties: false` at every depth (unknown keys fail closed);
- one `$defs` entry per dataclass, named after the class; discriminated unions (`kind`
  Literal) become `oneOf` over `$ref`s whose `kind` property is a `const`;
- no `$defs` carries a python class name any more (P1-03). Every object and every property
  publishes `x-description-key` instead: an i18n key **stem** the client resolves as `<stem>`
  (짧은 이름) and `<stem>.description` (한 줄 설명). Keys are ours, sentences belong to each
  consumer's locale dictionary — the same ownership rule as `x-applicable-when`;
- an `operator` property additionally publishes `x-operator`: `enum 값 → 설명 키 stem`, read from
  the operator registry (`domain.factor._operators`) so no client assembles a key from a value;
- catalog bounds appear as `minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum`, contract
  metadata as `x-unit`, `x-display-unit`, `x-applied-stage`, `x-description-key`, `examples`;
- identifier fields carry `x-catalog` (equity-field, factor, universe, subgraph: complete from
  that catalog) or `x-reference` (node, parameter: complete from the document itself), read from
  the dataclass field metadata declared next to the field (P3-03).
- required factor authoring fields carry `x-authoring-source` or `x-authoring-default`; this lets
  clients project a catalog row without duplicating FactorSignal field names or starter values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from strategy_workbench.domain.factor.facade.expression import EXPRESSION_NODE_KINDS
from strategy_workbench.domain.factor.facade.operators import operator_description_keys

from ._constraints import (
    FieldApplicability,
    ScalarConstraint,
    field_applicability_index,
    scalar_constraint_index,
)
from ._hydrate import SUPPORTED_SCHEMA_VERSIONS, _kind_of
from ._models import ParameterDefinition, StrategySpec

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
DOCUMENT_TITLE = "StrategyDocument"


@dataclass(frozen=True)
class FieldContract:
    """One scalar authoring path with everything an editor needs to explain it.

    `pointer` is a JSON Pointer template: array positions are written as an asterisk
    (for example the factor weight row is `/factors/<asterisk>/weight`). Rows of a
    discriminated union share the pointer and differ by `branch` (the member's `kind`).
    Bounds and metadata come from the constraint catalog; type, enum, nullability, required
    and default come from the model.
    """

    pointer: str
    type: str
    required: bool
    branch: str | None = None  # `kind` of the union member owning this row, if any
    nullable: bool = False
    default: object = None
    has_default: bool = False
    default_from: str | None = None  # `x-default-from`: sibling field hydrate copies when absent
    enum: tuple[str, ...] | None = None
    const: str | None = None
    format: str | None = None
    catalog: str | None = None  # `x-catalog`: catalog the identifier completes from
    reference: str | None = None  # `x-reference`: document-internal namespace of the identifier
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    exclusive_maximum: bool = False
    unit: str | None = None
    display_unit: str | None = None
    example: object = None
    applied_stage: str | None = None
    description_key: str | None = None
    applicable_when: ApplicableWhen | None = None  # `x-applicable-when`: mode that reads the field


@dataclass(frozen=True)
class ApplicableCondition:
    pointer: str
    equals: str | None
    not_null: bool


@dataclass(frozen=True)
class ApplicableWhen:
    """Same row as `FIELD_APPLICABILITY`, in the shape both the schema and the contract publish.

    `all_of` must all hold for the field to be read. `owned_by_error` names the blocking rule that
    reports a violation instead of the `strategy.field.inapplicable` warning.
    """

    all_of: tuple[ApplicableCondition, ...]
    description_key: str
    owned_by_error: str | None


ROOT_DESCRIPTION_KEY = "strategy.document"
# 최상위 property는 화면에서 "섹션"이다. 문서 자신(`strategy.document`)과 네임스페이스를 나눠야
# `description` 섹션 이름과 문서 설명(`strategy.document.description`)이 겹치지 않는다.
ROOT_PROPERTY_NAMESPACE = "strategy.section"


def _root_schema(builder: _SchemaBuilder) -> dict[str, Any]:
    return builder.dataclass_schema(
        StrategySpec,
        "",
        exclude=("identity",),
        stem=ROOT_DESCRIPTION_KEY,
        property_namespace=ROOT_PROPERTY_NAMESPACE,
    )


def strategy_document_schema() -> dict[str, Any]:
    """JSON Schema for the identity-free authoring document, derived from the model."""
    builder = _SchemaBuilder(scalar_constraint_index())
    root = _root_schema(builder)
    versions = sorted(SUPPORTED_SCHEMA_VERSIONS)
    version_schema: dict[str, Any] = {
        **(
            {"type": "string", "const": versions[0]}
            if len(versions) == 1
            else {"type": "string", "enum": versions}
        ),
        "x-description-key": f"{ROOT_PROPERTY_NAMESPACE}.schema_version",
    }
    properties = {"schema_version": version_schema, **root["properties"]}
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": f"urn:strategy-workbench:strategy-document:{versions[-1]}",
        "title": DOCUMENT_TITLE,
        "x-description-key": ROOT_DESCRIPTION_KEY,
        "type": "object",
        "properties": properties,
        "required": ["schema_version", *root["required"]],
        "additionalProperties": False,
        "$defs": builder.defs,
    }


def dataclass_json_schema(
    tp: type,
    *,
    schema_id: str,
    constraints: Mapping[str, ScalarConstraint] | None = None,
) -> dict[str, Any]:
    """dataclass 하나에서 유도한 JSON Schema(타입·기본값·enum·format·필드 마커).

    authoring 문서 스키마(`strategy_document_schema`)와 같은 빌더를 쓰지만 산출물이 다르다.
    전략 문서의 제약 카탈로그·적용 조건표를 자동으로 읽지 않는다 — 다른 문서의 `/fee_bps` 가
    전략 포인터와 우연히 겹쳐 남의 단위·범위를 입는 일을 막는다. 범위를 실으려면 호출자가
    자기 포인터로 다시 건 `constraints` 를 넘긴다. 소유자는 dataclass 를 가진 노드이고
    (P2-01 의 `RunEnvironment` 는 `domain.backtest`), 이 함수는 표기법만 제공한다.
    """
    builder = _SchemaBuilder(constraints or {}, {})
    root = builder.dataclass_schema(tp, "")
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": schema_id,
        "title": tp.__name__,
        "type": "object",
        "properties": root["properties"],
        "required": root["required"],
        "additionalProperties": False,
        "$defs": builder.defs,
    }


def strategy_document_schema_hash(schema: dict[str, Any]) -> str:
    """sha256 of the canonical (sorted, compact) schema JSON; doubles as the HTTP ETag."""
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def strategy_field_contracts() -> tuple[FieldContract, ...]:
    """Every scalar authoring path (pointer template order = document order)."""
    builder = _SchemaBuilder(scalar_constraint_index())
    _root_schema(builder)
    versions = sorted(SUPPORTED_SCHEMA_VERSIONS)
    version = FieldContract(
        pointer="/schema_version",
        type="string",
        required=True,
        const=versions[0] if len(versions) == 1 else None,
        enum=None if len(versions) == 1 else tuple(versions),
        description_key=f"{ROOT_PROPERTY_NAMESPACE}.schema_version",
    )
    return (version, *builder.contracts)


class _SchemaBuilder:
    def __init__(
        self,
        constraints: Mapping[str, ScalarConstraint],
        applicability: Mapping[str, FieldApplicability] | None = None,
    ) -> None:
        self._constraints = constraints
        # 전략 문서가 아닌 dataclass(`dataclass_json_schema`)는 빈 표를 넘겨 전략 포인터 표를
        # 읽지 않는다.
        self._applicability = (
            field_applicability_index() if applicability is None else applicability
        )
        self.defs: dict[str, dict[str, Any]] = {}
        self.contracts: list[FieldContract] = []
        self._seen: set[str] = set()

    # -- types --------------------------------------------------------------------------------

    def schema_of(self, tp: Any, pointer: str) -> dict[str, Any]:
        origin = get_origin(tp)
        if origin is Union or origin is types.UnionType:
            return self._union(get_args(tp), pointer)
        if origin is Literal:
            (value,) = get_args(tp)
            return {"type": _json_type(value), "const": value}
        if origin is tuple:
            (item_tp, _ellipsis) = get_args(tp)
            return {"type": "array", "items": self.schema_of(item_tp, f"{pointer}/*")}
        if dataclasses.is_dataclass(tp) and isinstance(tp, type):
            return self._ref(tp, pointer)
        if tp is type(None):
            return {"type": "null"}
        return _scalar_schema(tp)

    def _union(self, members: tuple[Any, ...], pointer: str) -> dict[str, Any]:
        nullable = type(None) in members
        others = tuple(m for m in members if m is not type(None))
        if all(dataclasses.is_dataclass(m) and isinstance(m, type) for m in others):
            schema: dict[str, Any] = {"oneOf": [self._ref(m, pointer) for m in others]}
            kinds = [_kind_of(m) for m in others]
            if all(kinds):
                schema["discriminator"] = {"propertyName": "kind"}
        elif len(others) == 1:
            schema = self.schema_of(others[0], pointer)
        else:
            json_types = {_scalar_schema(m)["type"] for m in others}
            if "number" in json_types:
                json_types.discard("integer")  # every integer is a number
            json_types = sorted(json_types)
            schema = {"type": json_types}
        if nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
        return schema

    def _ref(self, tp: type, pointer: str) -> dict[str, Any]:
        name = tp.__name__
        if name not in self._seen:
            self._seen.add(name)
            self.defs[name] = self.dataclass_schema(tp, pointer)
        return {"$ref": f"#/$defs/{name}"}

    def dataclass_schema(
        self,
        tp: type,
        pointer: str,
        *,
        exclude: tuple[str, ...] = (),
        stem: str | None = None,
        property_namespace: str | None = None,
    ) -> dict[str, Any]:
        hints = get_type_hints(tp)
        branch = _kind_of(tp)
        stem = stem or _description_stem(tp, branch)
        property_namespace = property_namespace or _property_namespace(tp, branch)
        properties: dict[str, Any] = {}
        required: list[str] = []
        # `kind` discriminator가 있으면 첫 property로 둔다(schema 1.1 S5). editor·snippet·fixture가
        # 이 순서를 따르므로 사람이 노드를 읽을 때 종류를 먼저 본다. dataclass 인자 순서는 그대로다.
        ordered_fields = sorted(
            dataclasses.fields(tp), key=lambda field: 0 if field.name == "kind" else 1
        )
        for field in ordered_fields:
            if field.name in exclude:
                continue
            child = f"{pointer}/{field.name}"
            schema = self.schema_of(hints[field.name], child)
            has_default, default = _default_of(field)
            if has_default:
                schema = {**schema, "default": _json_value(default)}
            elif "default-from" not in field.metadata:
                required.append(field.name)
            # 필드 옆에 선언한 정수 하한(`domain.factor._nodes.minimum`)을 그대로 발행한다. 검증기가
            # 같은 상수를 읽으므로 화면이 스키마로 만든 기본값을 backend가 거부할 수 없다(P1-04).
            declared_minimum = field.metadata.get("minimum")
            if isinstance(declared_minimum, int):
                schema = {**schema, "minimum": declared_minimum}
            # 파생 키를 먼저 얹고 제약 카탈로그가 자기 키를 가지면 그 쪽이 이긴다: 특정 필드의
            # 설명은 제약이 소유하고, 나머지 전부는 이름 규칙이 채운다(P1-03).
            schema = {**schema, "x-description-key": f"{property_namespace}.{field.name}"}
            operator_keys = operator_description_keys(hints[field.name])
            if operator_keys is not None:
                schema = {**schema, "x-operator": dict(operator_keys)}
            constraint = self._constraints.get(child)
            if constraint is not None:
                schema = {**schema, **_constraint_schema(constraint)}
            applicability = self._applicability.get(child)
            if applicability is not None:
                schema = {**schema, "x-applicable-when": _applicability_schema(applicability)}
            for marker in (
                "catalog",
                "reference",
                "defines",
                "authoring-source",
                "authoring-default",
                "authoring-identity",
                "default-from",
            ):
                if marker in field.metadata:
                    schema = {**schema, f"x-{marker}": _json_value(field.metadata[marker])}
            properties[field.name] = schema
            self._record_contract(child, hints[field.name], schema, has_default, default, branch)
        return {
            "type": "object",
            "x-description-key": stem,
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    # -- contracts ----------------------------------------------------------------------------

    def _record_contract(
        self,
        pointer: str,
        tp: Any,
        schema: dict[str, Any],
        has_default: bool,
        default: object,
        branch: str | None,
    ) -> None:
        origin = get_origin(tp)
        members = get_args(tp) if origin is Union or origin is types.UnionType else (tp,)
        scalar_members = tuple(m for m in members if m is not type(None))
        if any(dataclasses.is_dataclass(m) or get_origin(m) is tuple for m in scalar_members):
            return  # objects and arrays are navigated, not contracted
        nullable = "anyOf" in schema
        inner = schema["anyOf"][0] if nullable else schema
        json_type = inner.get("type")
        constraint = self._constraints.get(pointer)
        applicability = self._applicability.get(pointer)
        self.contracts.append(
            FieldContract(
                pointer=pointer,
                branch=branch,
                type="|".join(json_type) if isinstance(json_type, list) else str(json_type),
                required="default" not in schema and "x-default-from" not in schema,
                nullable=nullable,
                default=_json_value(default) if has_default else None,
                has_default=has_default,
                default_from=schema.get("x-default-from"),
                enum=tuple(inner["enum"]) if "enum" in inner else None,
                const=inner.get("const"),
                format=inner.get("format"),
                catalog=schema.get("x-catalog"),
                reference=schema.get("x-reference"),
                minimum=constraint.minimum if constraint else None,
                maximum=constraint.maximum if constraint else None,
                exclusive_minimum=constraint.exclusive_minimum if constraint else False,
                exclusive_maximum=constraint.exclusive_maximum if constraint else False,
                unit=constraint.unit.value if constraint else None,
                display_unit=constraint.display_unit if constraint else None,
                example=constraint.example if constraint else None,
                applied_stage=constraint.stage.value if constraint else None,
                applicable_when=(
                    _applicable_when(applicability) if applicability is not None else None
                ),
                description_key=schema.get("x-description-key"),
            )
        )


# 판별 union의 분기들은 화면에서 한 어휘를 이룬다: `node_id`는 어떤 노드에서도, `parameter_id`는
# 어떤 파라미터에서도 같은 뜻이다. 분기마다 다른 키를 주면 (1) 같은 문장을 12벌 두게 되고
# (2) 분기 독립 property의 스키마가 분기마다 달라져 편집기가 "kind를 먼저 고르라"고 요구한다
# (frontend `collectProperties`의 분기 동치 판정). union 하나가 stem prefix와 property
# 네임스페이스를 함께 소유한다.
_UNION_VOCABULARIES: tuple[tuple[frozenset[type], str, str], ...] = (
    (
        frozenset(EXPRESSION_NODE_KINDS.values()),
        "strategy.node",
        "strategy.field.node",
    ),
    (
        frozenset(get_args(ParameterDefinition)),
        "strategy.parameter",
        "strategy.field.parameter",
    ),
)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _vocabulary(tp: type, branch: str | None) -> tuple[str, str] | None:
    if branch is None:
        return None
    for members, stem_prefix, namespace in _UNION_VOCABULARIES:
        if tp in members:
            return f"{stem_prefix}.{branch}", namespace
    return None


def _description_stem(tp: type, branch: str | None) -> str:
    """객체 하나의 i18n 키 stem. `$defs` 이름(파이썬 클래스명)은 화면에 나가지 않는다."""
    vocabulary = _vocabulary(tp, branch)
    return vocabulary[0] if vocabulary else f"strategy.type.{_snake(tp.__name__)}"


def _property_namespace(tp: type, branch: str | None) -> str:
    vocabulary = _vocabulary(tp, branch)
    return vocabulary[1] if vocabulary else f"strategy.field.{_snake(tp.__name__)}"


def _scalar_schema(tp: Any) -> dict[str, Any]:
    if isinstance(tp, type) and issubclass(tp, Enum):
        return {"type": "string", "enum": [member.value for member in tp]}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    if tp is date:
        return {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
    raise TypeError(f"unsupported schema type — type={tp!r}")  # pragma: no cover


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _applicability_schema(row: FieldApplicability) -> dict[str, Any]:
    # 계약(FieldContract.applicable_when)과 같은 모양(JSON 값): 소비자가 두 endpoint를 같은 코드로
    # 읽는다. tuple은 list로 내려 JSON 왕복 후에도 fixture 동치가 유지되게 한다.
    return json.loads(json.dumps(dataclasses.asdict(_applicable_when(row))))


def _applicable_when(row: FieldApplicability) -> ApplicableWhen:
    return ApplicableWhen(
        all_of=tuple(
            ApplicableCondition(
                pointer=condition.pointer, equals=condition.equals, not_null=condition.not_null
            )
            for condition in row.conditions
        ),
        description_key=row.description_key,
        owned_by_error=row.owned_by_error,
    )


def _constraint_schema(constraint: ScalarConstraint) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "x-unit": constraint.unit.value,
        "x-applied-stage": constraint.stage.value,
    }
    if constraint.minimum is not None:
        key = "exclusiveMinimum" if constraint.exclusive_minimum else "minimum"
        schema[key] = constraint.minimum
    if constraint.maximum is not None:
        key = "exclusiveMaximum" if constraint.exclusive_maximum else "maximum"
        schema[key] = constraint.maximum
    if constraint.display_unit is not None:
        schema["x-display-unit"] = constraint.display_unit
    if constraint.example is not None:
        schema["examples"] = [constraint.example]
    if constraint.description_key:
        schema["x-description-key"] = constraint.description_key
    return schema


def _default_of(field: dataclasses.Field[Any]) -> tuple[bool, object]:
    if field.default is not dataclasses.MISSING:
        return True, field.default
    if field.default_factory is not dataclasses.MISSING:
        return True, field.default_factory()
    return False, None


def _json_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _json_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return value
