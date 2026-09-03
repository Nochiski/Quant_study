from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from enum import Enum
from typing import Any

from ._models import StrategySpec


def canonical_strategy_payload(spec: StrategySpec) -> dict[str, Any]:
    """Return semantic content; storage identity never changes the strategy hash."""
    payload = asdict(spec)
    identity = payload.pop("identity")
    payload["schema_version"] = identity["schema_version"]
    return payload


def canonical_strategy_json(spec: StrategySpec) -> str:
    return json.dumps(
        canonical_strategy_payload(spec),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical value — type={type(value).__name__}")


def strategy_spec_hash(spec: StrategySpec) -> str:
    encoded = canonical_strategy_json(spec).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
