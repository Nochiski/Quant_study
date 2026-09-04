"""Typed hydrate: identity-free authoring payload → immutable StrategySpec.

ADR: docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md (D1, D4)

- The payload shape is derived from the dataclass type hints, so there is no second DTO.
- Unknown keys at any depth, missing required fields, type mismatches, unknown `kind`
  discriminators, bad enum/date literals and unsupported schema versions are structural
  issues with a JSON Pointer. Nothing is silently defaulted.
- Typed scalar fields normalise `1`/`1.0`, ISO date strings and enum strings; the
  `ParameterValue` union keeps bool/str as-is and folds integral floats to int.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum, StrEnum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ._models import StrategyIdentity, StrategySpec

SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("1.0",)

_MISSING: Any = object()


class HydrationStatus(StrEnum):
    OK = "ok"
    STRUCTURAL_ERROR = "structural_error"


@dataclass(frozen=True)
class StructuralIssue:
    code: str
    pointer: str
    message: str


@dataclass(frozen=True)
class StrategyHydration:
    status: HydrationStatus
    spec: StrategySpec | None
    issues: tuple[StructuralIssue, ...]

    @property
    def ok(self) -> bool:
        return self.status is HydrationStatus.OK


def hydrate_strategy_document(
    document: Mapping[str, object], *, identity: StrategyIdentity
) -> StrategyHydration:
    """Hydrate an identity-free authoring document into a StrategySpec.

    `schema_version` is read from the document; identity comes from the revision envelope.
    """
    issues: list[StructuralIssue] = []
    schema_version = document.get("schema_version", _MISSING)
    if schema_version is _MISSING:
        issues.append(
            StructuralIssue(
                "structure.missing_field",
                "/schema_version",
                f"schema_version is required — supported={SUPPORTED_SCHEMA_VERSIONS}",
            )
        )
    elif schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            StructuralIssue(
                "structure.unsupported_schema_version",
                "/schema_version",
                f"unsupported schema_version — got={schema_version!r} "
                f"supported={SUPPORTED_SCHEMA_VERSIONS}",
            )
        )
    if "identity" in document:
        issues.append(
            StructuralIssue(
                "structure.unknown_key",
                "/identity",
                "identity is owned by the revision envelope, not the authoring document",
            )
        )
    if issues:
        return StrategyHydration(HydrationStatus.STRUCTURAL_ERROR, None, tuple(issues))

    payload = {key: value for key, value in document.items() if key != "schema_version"}
    payload["identity"] = {
        "strategy_id": identity.strategy_id,
        "revision": identity.revision,
        "schema_version": str(schema_version),
    }
    spec = _hydrate(StrategySpec, payload, "", issues)
    if issues or spec is _MISSING:
        return StrategyHydration(HydrationStatus.STRUCTURAL_ERROR, None, tuple(issues))
    if not isinstance(spec, StrategySpec):  # pragma: no cover - defensive
        raise TypeError(f"hydrate produced {type(spec).__name__}, expected StrategySpec")
    return StrategyHydration(HydrationStatus.OK, spec, ())


def hydrate_saved_strategy(document: Mapping[str, object]) -> StrategyHydration:
    """Hydrate a legacy payload that carries `identity` inside the document (current JSON API)."""
    issues: list[StructuralIssue] = []
    spec = _hydrate(StrategySpec, document, "", issues)
    if issues or spec is _MISSING:
        return StrategyHydration(HydrationStatus.STRUCTURAL_ERROR, None, tuple(issues))
    if not isinstance(spec, StrategySpec):  # pragma: no cover - defensive
        raise TypeError(f"hydrate produced {type(spec).__name__}, expected StrategySpec")
    if spec.identity.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return StrategyHydration(
            HydrationStatus.STRUCTURAL_ERROR,
            None,
            (
                StructuralIssue(
                    "structure.unsupported_schema_version",
                    "/identity/schema_version",
                    f"unsupported schema_version — got={spec.identity.schema_version!r} "
                    f"supported={SUPPORTED_SCHEMA_VERSIONS}",
                ),
            ),
        )
    return StrategyHydration(HydrationStatus.OK, spec, ())


def _issue(issues: list[StructuralIssue], code: str, pointer: str, message: str) -> Any:
    issues.append(StructuralIssue(code, pointer or "/", message))
    return _MISSING


def _hydrate(tp: Any, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        return _hydrate_union(get_args(tp), value, pointer, issues)
    if origin is Literal:
        allowed = get_args(tp)
        if value in allowed and not isinstance(value, bool):
            return value
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"expected one of {allowed!r}, got {value!r}",
        )
    if origin is tuple:
        return _hydrate_sequence(get_args(tp)[0], value, pointer, issues)
    if dataclasses.is_dataclass(tp) and isinstance(tp, type):
        return _hydrate_dataclass(tp, value, pointer, issues)
    if tp is type(None):
        if value is None:
            return None
        return _issue(issues, "structure.type_mismatch", pointer, f"expected null, got {value!r}")
    return _hydrate_scalar(tp, value, pointer, issues)


def _hydrate_union(
    members: tuple[Any, ...], value: object, pointer: str, issues: list[StructuralIssue]
) -> Any:
    if value is None and type(None) in members:
        return None
    dataclass_members = [m for m in members if dataclasses.is_dataclass(m) and isinstance(m, type)]
    if dataclass_members:
        if not isinstance(value, Mapping):
            return _issue(
                issues,
                "structure.type_mismatch",
                pointer,
                f"expected a mapping, got {type(value).__name__}",
            )
        kinds = {_kind_of(m): m for m in dataclass_members}
        if None not in kinds:
            kind = value.get("kind", _MISSING)
            if kind is _MISSING:
                return _issue(
                    issues,
                    "structure.missing_field",
                    f"{pointer}/kind",
                    f"kind is required — allowed={sorted(k for k in kinds if k)}",
                )
            member = kinds.get(kind if isinstance(kind, str) else None)
            if member is None:
                return _issue(
                    issues,
                    "structure.unknown_kind",
                    f"{pointer}/kind",
                    f"unknown kind — got={kind!r} allowed={sorted(k for k in kinds if k)}",
                )
            return _hydrate_dataclass(member, value, pointer, issues)
        if len(dataclass_members) == 1:
            return _hydrate_dataclass(dataclass_members[0], value, pointer, issues)
        raise TypeError(  # pragma: no cover - model authoring error
            f"union of dataclasses without kind discriminator at pointer={pointer!r}"
        )
    # scalar union (ParameterValue): keep bool/str, fold integral floats to int.
    scalar_members = tuple(m for m in members if m is not type(None))
    if isinstance(value, bool):
        if bool in scalar_members:
            return value
    elif isinstance(value, int):
        if int in scalar_members:
            return value
        if float in scalar_members:
            return float(value)
    elif isinstance(value, float):
        if int in scalar_members and value.is_integer():
            return int(value)
        if float in scalar_members:
            return value
    elif isinstance(value, str):
        if str in scalar_members:
            return value
    names = "|".join(getattr(m, "__name__", str(m)) for m in scalar_members)
    return _issue(issues, "structure.type_mismatch", pointer, f"expected {names}, got {value!r}")


def _hydrate_sequence(
    item_tp: Any, value: object, pointer: str, issues: list[StructuralIssue]
) -> Any:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"expected a sequence, got {type(value).__name__}",
        )
    items = [
        _hydrate(item_tp, item, f"{pointer}/{index}", issues) for index, item in enumerate(value)
    ]
    if any(item is _MISSING for item in items):
        return _MISSING
    return tuple(items)


def _hydrate_dataclass(tp: type, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    if not isinstance(value, Mapping):
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"expected a mapping, got {type(value).__name__}",
        )
    hints = get_type_hints(tp)
    fields = {field.name: field for field in dataclasses.fields(tp)}
    for key in value:
        if key not in fields:
            _issue(
                issues,
                "structure.unknown_key",
                f"{pointer}/{key}",
                f"unknown key {key!r} — allowed={sorted(fields)}",
            )
    kwargs: dict[str, Any] = {}
    failed = False
    for name, field in fields.items():
        if name in value:
            hydrated = _hydrate(hints[name], value[name], f"{pointer}/{name}", issues)
            if hydrated is _MISSING:
                failed = True
            else:
                kwargs[name] = hydrated
        elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            _issue(issues, "structure.missing_field", f"{pointer}/{name}", f"{name} is required")
            failed = True
    if failed or any(issue.pointer.startswith(f"{pointer}/") for issue in issues if pointer == ""):
        return _MISSING
    if any(issue.pointer == f"{pointer}/{key}" for key in value for issue in issues):
        return _MISSING
    return tp(**kwargs)


def _hydrate_scalar(tp: Any, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    if isinstance(tp, type) and issubclass(tp, Enum):
        if isinstance(value, tp):
            return value
        if isinstance(value, str):
            try:
                return tp(value)
            except ValueError:
                pass
        allowed = [member.value for member in tp]
        return _issue(
            issues, "structure.invalid_enum", pointer, f"expected one of {allowed!r}, got {value!r}"
        )
    if tp is bool:
        if isinstance(value, bool):
            return value
        return _issue(issues, "structure.type_mismatch", pointer, f"expected bool, got {value!r}")
    if tp is int:
        if isinstance(value, bool):
            return _issue(
                issues, "structure.type_mismatch", pointer, f"expected int, got {value!r}"
            )
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return _issue(issues, "structure.type_mismatch", pointer, f"expected int, got {value!r}")
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _issue(
                issues, "structure.type_mismatch", pointer, f"expected float, got {value!r}"
            )
        return float(value)
    if tp is str:
        if isinstance(value, str):
            return value
        return _issue(issues, "structure.type_mismatch", pointer, f"expected str, got {value!r}")
    if tp is date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        return _issue(
            issues,
            "structure.invalid_date",
            pointer,
            f"expected ISO date (YYYY-MM-DD), got {value!r}",
        )
    raise TypeError(  # pragma: no cover - model authoring error
        f"unsupported hydrate type {tp!r} at pointer={pointer!r}"
    )


def _kind_of(tp: type) -> str | None:
    hint = get_type_hints(tp).get("kind")
    if hint is not None and get_origin(hint) is Literal:
        return typing.cast(str, get_args(hint)[0])
    return None
