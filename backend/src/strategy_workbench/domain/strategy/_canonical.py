from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from enum import Enum
from typing import Any

from ._models import StrategySpec


def canonical_strategy_payload(spec: StrategySpec) -> dict[str, Any]:
    """Return semantic content; storage identity never changes the strategy hash.

    Numeric normalisation keeps "same meaning, same hash" (authoring ADR D1): `-0.0` folds to
    `0.0`, and `ParameterValue` union members that are integral floats fold to int so a YAML
    `1.0` and a JSON `1` in a choice parameter canonicalise identically.
    """
    payload = asdict(spec)
    identity = payload.pop("identity")
    payload["schema_version"] = identity["schema_version"]
    payload["parameters"] = [_normalize_parameter(item) for item in payload["parameters"]]
    return _normalize_numbers(payload)


def _normalize_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    if parameter.get("kind") != "choice":
        return parameter
    return parameter | {
        "default": _normalize_parameter_value(parameter["default"]),
        "choices": [_normalize_parameter_value(choice) for choice in parameter["choices"]],
    }


def _normalize_parameter_value(value: object) -> object:
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        return int(value)
    return value


def _normalize_numbers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    # asdict keeps dataclass tuple fields (rules, factors, nodes) as tuples: recurse into both.
    if isinstance(value, (list, tuple)):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        return 0.0
    return value


def canonical_payload_json(payload: Mapping[str, Any], *, indent: int | None = None) -> str:
    """The one canonical JSON encoding of an already-canonical payload (ADR D3 hash algorithm).

    Compact for hashing, `indent` for a human-readable document; both carry the same payload and
    key order, so `indent` never changes meaning. `strategy_spec_hash` is the sha256 of the
    compact form. Tests pin this encoder against a literal payload so a model change and an
    algorithm change stay distinguishable.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else (",", ": "),
        indent=indent,
        default=_json_default,
    )


def canonical_strategy_json(spec: StrategySpec, *, indent: int | None = None) -> str:
    """Canonical JSON text of a spec: `canonical_strategy_payload` through the payload encoder."""
    return canonical_payload_json(canonical_strategy_payload(spec), indent=indent)


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical value — type={type(value).__name__}")


def canonical_json_spec_hash(canonical_json: str) -> str:
    """ADR D3 hash of already-canonical JSON text: sha256 of its UTF-8 bytes.

    The only place the algorithm lives. `strategy_spec_hash` feeds it the current model's
    canonical text; a stored row from a retired schema version feeds it the exact bytes it
    stored, since that version's model no longer exists to re-canonicalise.
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def strategy_spec_hash(spec: StrategySpec) -> str:
    return canonical_json_spec_hash(canonical_strategy_json(spec))
