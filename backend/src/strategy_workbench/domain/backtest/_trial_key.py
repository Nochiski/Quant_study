"""시도 키 — 새로 고를 거리가 생긴 실행만 새 시도로 센다(검증 랩 spec D2, V1-05).

시도 키는 계열 시도 수 N 을 세는 데만 쓴다. 재현·중복 실행 제거는 `run_fingerprint` 가
맡는다(spec D6). 키는 세 축의 canonical 해시다.

- 전략 의미 해시(`strategy_semantic_hash`): 제목·설명·파라미터 범위 정의를 뺀 전략.
- 해소된 파라미터 값: 파라미터마다 실행이 쓴 값(`resolve_parameter_values`).
- 실행 설정: `TRIAL_KEY_ROLES` 분류표대로 고른 칸. 칸 값이 스키마 기본값이면 표기에서 빠지므로 칸을
  더해도 기존 설정의 키가 바뀌지 않는다. 스키마 기본값을 바꾸면 키가 바뀐다.

실행 요청의 나머지 칸(초기 자본·벤치마크·연환산 거래일·지표 창·실행 core)과 데이터 스냅샷·엔진·지표
레지스트리 판본은 이 함수가 읽지 않으므로 키 밖이다.
"""

from __future__ import annotations

from dataclasses import MISSING, fields
from enum import StrEnum

from strategy_workbench.domain.strategy.facade.specification import (
    ParameterValue,
    resolve_parameter_values,
    strategy_semantic_hash,
)

from ._canonical import canonical_json_hash
from ._models import BacktestRunSpec, RunEnvironment


class TrialKeyRole(StrEnum):
    """실행 설정 칸 하나가 시도 키에 드는 방식."""

    # 바뀌면 새 시도다. 비용 모델 종류(거래세 방식·참여 기준)도 여기다.
    KEY = "key"
    # 기본값보다 낮을 때만 값이 키에 든다. 기본값이거나 높으면(불리) "기본 이하"로 묶인다.
    LOWER_IS_FAVORABLE = "lower_is_favorable"
    # 기본값보다 높을 때만 값이 키에 든다.
    HIGHER_IS_FAVORABLE = "higher_is_favorable"
    # 바뀌어도 같은 시도다.
    OUTSIDE = "outside"


# `RunEnvironment` 칸마다 한 줄. architecture 테스트가 모든 칸이 여기 있는지 확인하므로 칸을 더하는
# PR 은 같은 PR 에서 분류한다.
TRIAL_KEY_ROLES: dict[str, TrialKeyRole] = {
    "market": TrialKeyRole.KEY,
    "frequency": TrialKeyRole.KEY,
    "start": TrialKeyRole.KEY,
    "end": TrialKeyRole.OUTSIDE,
    "universe_id": TrialKeyRole.KEY,
    "timing": TrialKeyRole.KEY,
    "participation_rate": TrialKeyRole.HIGHER_IS_FAVORABLE,
    "participation_basis": TrialKeyRole.KEY,
    "fee_bps": TrialKeyRole.LOWER_IS_FAVORABLE,
    "slippage_bps": TrialKeyRole.LOWER_IS_FAVORABLE,
    "sell_tax": TrialKeyRole.KEY,
    # `custom` 일 때만 값이 있고 스키마 기본값이 없다(null). 견줄 기준이 없으니 값이 늘 키에 든다.
    "sell_tax_bps": TrialKeyRole.LOWER_IS_FAVORABLE,
    "missing": TrialKeyRole.KEY,
}


def trial_key(spec: BacktestRunSpec) -> str:
    """전략·실행 설정이 채워진 실행 spec 의 시도 키.

    Raises:
        InvalidParameterValueError: 요청의 파라미터 값을 문서 정의로 해소할 수 없을 때.
    """
    strategy, environment = spec.strategy, spec.environment
    if strategy is None or environment is None:
        raise ValueError(
            "trial key needs a resolved run spec — "
            f"strategy={strategy is not None} environment={environment is not None}"
        )
    return canonical_json_hash(
        {
            "strategy": strategy_semantic_hash(strategy),
            "parameters": _resolved_parameter_values(spec),
            "environment": _environment_key(environment),
        }
    )


def _resolved_parameter_values(spec: BacktestRunSpec) -> dict[str, ParameterValue]:
    """실행이 쓴 파라미터 값(spec D4).

    해소는 멱등이라 봉인 기록·미리 계산(해소 전 요청)과 접수(해소한 값을 박은 spec)가 같은 시도 키를
    낸다. 생략한 파라미터와 기본값을 명시한 파라미터도 같은 값이다.
    """
    if spec.strategy is None:  # pragma: no cover - trial_key 가 앞에서 막는다
        return {}
    return resolve_parameter_values(spec.strategy.parameters, spec.parameter_values)


def _environment_key(environment: RunEnvironment) -> dict[str, object]:
    key: dict[str, object] = {}
    for item in fields(environment):
        role = TRIAL_KEY_ROLES[item.name]
        value, default = getattr(environment, item.name), item.default
        if role is TrialKeyRole.OUTSIDE or value == default:
            continue
        if default is not MISSING and default is not None and value is not None:
            if role is TrialKeyRole.LOWER_IS_FAVORABLE and value > default:
                continue
            if role is TrialKeyRole.HIGHER_IS_FAVORABLE and value < default:
                continue
        key[item.name] = value
    return key
