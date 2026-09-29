from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, datetime
from enum import Enum

from ._models import BacktestRunSpec, RunEnvironment

# 엔진 체결·자본·비용 규칙의 판본. 매니페스트 `engine_version` 과 실행 지문에 실린다. 두 코어의
# 체결 결과나 비용 규칙(법정 세율표·ADV·σ 창·충격 상한·척도 공식)을 바꾸는 PR 은 이 값을 올린다 —
# `tests/test_engine_rules_version.py` 가 규칙 digest 와 짝으로 고정한다(#335). 올리지 않으면 같은
# 지문으로 옛 규칙의 결과를 공유한다.
ENGINE_RULES_VERSION = "backtest-engine-v2"


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
    # 계열(`lineage_strategy_id`)도 출처 정보라 뺀다(검증 랩 spec D2).
    # 실행 설정(`run_spec.environment`)은 run_spec 안에 통째로 실려 지문을 가른다. 같은 전략을
    # 다른 기간·유니버스·비용으로 돌린 두 실행은 결과 캐시를 공유하지 않는다(spec D6).
    # 접수가 해소한 파라미터 값(`run_spec.parameter_values`)도 함께 실려 값마다 지문이 갈린다
    # (spec D4).
    payload = {
        "run_spec": asdict(fingerprint_run_spec(spec)),
        "data_snapshot_id": data_snapshot_id,
        "target_tape_hash": target_tape_hash,
        "engine_version": engine_version,
        "metric_registry_version": metric_registry_version,
    }
    return canonical_json_hash(payload)


def fingerprint_run_spec(spec: BacktestRunSpec) -> BacktestRunSpec:
    """실행 결과를 가르는 요청 칸만 남긴 spec — 실행 지문과 같은 입력 잇기가 이것을 비교한다.

    전략 출처·계열은 출처 정보이고, 지표 창의 표시 이름(`label`)은 화면이 지은 문자열이라
    뺀다(#335).
    """
    return replace(
        spec,
        strategy_source=None,
        lineage_strategy_id=None,
        metric_windows=tuple(replace(window, label=None) for window in spec.metric_windows),
    )


def run_input_key(spec: BacktestRunSpec) -> str:
    """접수 때 아는 실행 입력의 해시 — 같은 입력 잇기(중복 실행 제거)의 기준.

    실행 지문에서 실행 뒤에야 아는 칸(target tape 해시)과 프로세스 안에서 고정인 칸(데이터 스냅샷·
    엔진 규칙·지표 레지스트리 판본)을 뺀 것이다. JSON 표기라 `1` 과 `True` 가 갈린다.
    """
    return canonical_json_hash(asdict(fingerprint_run_spec(spec)))


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
