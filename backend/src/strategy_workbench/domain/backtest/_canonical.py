from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum

from ._models import BacktestRunSpec


def backtest_run_fingerprint(
    spec: BacktestRunSpec,
    *,
    data_snapshot_id: str,
    target_tape_hash: str,
    engine_version: str,
    metric_registry_version: str,
) -> str:
    """Identify every semantic input needed to reproduce one engine result."""
    payload = {
        "run_spec": asdict(spec),
        "data_snapshot_id": data_snapshot_id,
        "target_tape_hash": target_tape_hash,
        "engine_version": engine_version,
        "metric_registry_version": metric_registry_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported run fingerprint value: {type(value).__name__}")
