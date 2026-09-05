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
import types
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ._constraints import ScalarConstraint, scalar_constraint_index
from ._hydrate import SUPPORTED_SCHEMA_VERSIONS, _kind_of
from ._models import StrategySpec

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
DOCUMENT_TITLE = "StrategyDocument"


@dataclass(frozen=True)
class FieldContract:
    """One scalar authoring path with everything an editor needs to explain it.

    `pointer` is a JSON Pointer template: array positions are written as an asterisk
    (for example the factor weight row is `/factors/factors/<asterisk>/weight`). Rows of a
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


def strategy_document_schema() -> dict[str, Any]:
    """JSON Schema for the identity-free authoring document, derived from the model."""
    builder = _SchemaBuilder(scalar_constraint_index())
    root = builder.dataclass_schema(StrategySpec, "", exclude=("identity",))
    versions = sorted(SUPPORTED_SCHEMA_VERSIONS)
    version_schema: dict[str, Any] = (
        {"type": "string", "const": versions[0]}
        if len(versions) == 1
        else {"type": "string", "enum": versions}
    )
    properties = {"schema_version": version_schema, **root["properties"]}
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": f"urn:strategy-workbench:strategy-document:{versions[-1]}",
        "title": DOCUMENT_TITLE,
        "type": "object",
        "properties": properties,
        "required": ["schema_version", *root["required"]],
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
    builder.dataclass_schema(StrategySpec, "", exclude=("identity",))
    versions = sorted(SUPPORTED_SCHEMA_VERSIONS)
    version = FieldContract(
        pointer="/schema_version",
        type="string",
        required=True,
        const=versions[0] if len(versions) == 1 else None,
        enum=None if len(versions) == 1 else tuple(versions),
    )
    return (version, *builder.contracts)


class _SchemaBuilder:
    def __init__(self, constraints: Mapping[str, ScalarConstraint]) -> None:
        self._constraints = constraints
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
        self, tp: type, pointer: str, *, exclude: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        hints = get_type_hints(tp)
        branch = _kind_of(tp)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in dataclasses.fields(tp):
            if field.name in exclude:
                continue
            child = f"{pointer}/{field.name}"
            schema = self.schema_of(hints[field.name], child)
            has_default, default = _default_of(field)
            if has_default:
                schema = {**schema, "default": _json_value(default)}
            else:
                required.append(field.name)
            constraint = self._constraints.get(child)
            if constraint is not None:
                schema = {**schema, **_constraint_schema(constraint)}
            for marker in (
                "catalog",
                "reference",
                "defines",
                "authoring-source",
                "authoring-default",
                "authoring-identity",
            ):
                if marker in field.metadata:
                    schema = {**schema, f"x-{marker}": _json_value(field.metadata[marker])}
            properties[field.name] = schema
            self._record_contract(child, hints[field.name], schema, has_default, default, branch)
        return {
            "type": "object",
            "title": tp.__name__,
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
        self.contracts.append(
            FieldContract(
                pointer=pointer,
                branch=branch,
                type="|".join(json_type) if isinstance(json_type, list) else str(json_type),
                required="default" not in schema,
                nullable=nullable,
                default=_json_value(default) if has_default else None,
                has_default=has_default,
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
                description_key=constraint.description_key or None if constraint else None,
            )
        )


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
