from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, datetime
from enum import Enum

from ._models import BacktestRunSpec, RunEnvironment


def backtest_run_fingerprint(
    spec: BacktestRunSpec,
    *,
    data_snapshot_id: str,
    target_tape_hash: str,
    engine_version: str,
    metric_registry_version: str,
) -> str:
    """Identify every semantic input needed to reproduce one engine result."""
    # How the strategy was referenced (saved revision id/hash, client source hash) is
    # provenance, recorded in RunManifest.strategy_provenance; it is not an engine input.
    # 실행 설정(`run_spec.environment`)은 run_spec 안에 통째로 실려 지문을 가른다. 같은 전략을
    # 다른 기간·유니버스·비용으로 돌린 두 실행은 결과 캐시를 공유하지 않는다(spec D6).
    payload = {
        "run_spec": asdict(replace(spec, strategy_source=None)),
        "data_snapshot_id": data_snapshot_id,
        "target_tape_hash": target_tape_hash,
        "engine_version": engine_version,
        "metric_registry_version": metric_registry_version,
    }
    return canonical_json_hash(payload)


def run_environment_canonical_json(environment: RunEnvironment) -> str:
    """실행 설정의 canonical 표기(키 정렬·최소 separator·NaN 금지)."""
    return _canonical_json(asdict(environment))


def environment_hash(environment: RunEnvironment) -> str:
    """실행 설정의 sha256. run manifest 가 `spec_hash` 와 나란히 기록하는 두 번째 축이다."""
    return canonical_json_hash(asdict(environment))


def canonical_json_hash(payload: object) -> str:
    """canonical JSON 의 sha256. 실행 지문·실행 설정 스키마 ETag 가 같은 표기를 쓴다."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical backtest value: {type(value).__name__}")
