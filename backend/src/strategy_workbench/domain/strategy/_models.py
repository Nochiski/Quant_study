from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeAlias

from strategy_workbench.domain.factor.facade.expression import FactorGraph

# 새 문서로 받는 유일한 authoring schema 버전. 모델 기본값·hydrate·스키마·어댑터가 전부 이 상수를
# 읽는다(Phase 1 감사 DEFECT-P1X-001: 리터럴을 두 곳에 적지 않는다). 은퇴한 버전의 문서·저장 row는
# `_upgrade.py`의 변환을 거쳐서만 들어온다(spec D2·D3).
CURRENT_SCHEMA_VERSION = "1.2"

# Editor metadata for identifier fields (see domain.factor._nodes for the node-side markers).
CATALOG_EQUITY_FIELD = {"catalog": "equity-field"}
DEFINES_PARAMETER = {"defines": "parameter"}
# 생략 시 hydrate가 같은 mapping의 다른 필드 값을 넣는 파생 기본값 (schema 1.1, spec D1 S3).
DEFAULT_FROM_FACTOR_ID = {"default-from": "factor_id"}


def _factor_authoring(source: str, *, identity: bool = False) -> dict[str, object]:
    """Describe how a catalog row supplies one required FactorSignal authoring value."""
    metadata: dict[str, object] = {"authoring-source": source}
    if identity:
        metadata["authoring-identity"] = True
    return metadata


class EligibilityOperator(StrEnum):
    """유니버스 필터 한 줄의 비교 방식 (schema 1.2, spec D3 S5).

    앞의 다섯(`gt`~`eq`)은 후보 하나의 값만 보면 판정되는 **절대** 규칙이고, `top_*` 는 같은
    기준일 프레임의 **횡단면** 순위를 봐야 판정된다. `value` 의 의미도 갈린다 — 절대 규칙은
    비교 임계값, `top_percent` 는 비율, `top_count` 는 개수다.

    이 enum 은 전용이다. 값이 겹친다고 다른 비교 연산자 enum 에 얹으면 `top_*` 가 그 enum 의
    소비자(팩터 그래프 `comparison` 노드 등)로 흘러들어, 모집단 없이 판정할 수 없는 값이
    catch-all 분기에서 조용히 다른 비교로 떨어진다.
    """

    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"
    TOP_PERCENT = "top_percent"
    TOP_COUNT = "top_count"


# 절대/횡단면 갈림의 유일한 owner. 컴파일러의 1-pass·2-pass 와 validator 가 같은 집합을 읽는다 —
# 목록을 소비자마다 다시 적으면 연산자를 늘릴 때 한쪽만 바뀌어 새 연산자가 1-pass 로 샌다.
CROSS_SECTIONAL_ELIGIBILITY_OPERATORS: frozenset[EligibilityOperator] = frozenset(
    {EligibilityOperator.TOP_PERCENT, EligibilityOperator.TOP_COUNT}
)


class FactorDirection(StrEnum):
    HIGH = "high"
    LOW = "low"


class SignalNormalization(StrEnum):
    """팩터 신호를 가중 합으로 합치기 전에 적용하는 횡단면 정규화 (schema 1.2, spec D4).

    `NONE` 은 1.1 의 의미(원시값 가중 합)이고, 1.1 문서를 업그레이드할 때 명시된다. 새 문서의
    기본값은 `RANK` 다 — 단위가 다른 팩터(PBR 과 ROE 등)를 원시값으로 더하면 큰 단위 하나가
    합성 점수를 지배하기 때문이다.
    """

    NONE = "none"
    RANK = "rank"
    ZSCORE = "zscore"


class PortfolioSide(StrEnum):
    LONG_ONLY = "long_only"
    LONG_SHORT = "long_short"


class WeightingMethod(StrEnum):
    EQUAL = "equal"
    FACTOR_SCORE = "factor_score"
    RANK = "rank"
    RISK = "risk"


class SelectionMethod(StrEnum):
    TOP_N = "top_n"
    PERCENTILE = "percentile"


class RebalanceFrequency(StrEnum):
    EVERY_N_SESSIONS = "every_n_sessions"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class StrategyIdentity:
    strategy_id: str
    revision: int
    schema_version: str = CURRENT_SCHEMA_VERSION


@dataclass(frozen=True)
class EligibilityRule:
    field_id: str = field(metadata=CATALOG_EQUITY_FIELD)
    operator: EligibilityOperator
    value: float


@dataclass(frozen=True)
class EligibilityStep:
    rules: tuple[EligibilityRule, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FactorSignal:
    factor_id: str = field(metadata=_factor_authoring("factor_id", identity=True))
    # dataclass default가 아니라 파생 기본값: 문서에서 생략하면 hydrate가 factor_id를 넣는다.
    label: str = field(metadata={**_factor_authoring("label"), **DEFAULT_FROM_FACTOR_ID})
    direction: FactorDirection = field(metadata=_factor_authoring("preference"))
    weight: float = field(default=1.0, metadata={"authoring-default": 1.0})
    graph: FactorGraph = field(metadata=_factor_authoring("default_graph"))


@dataclass(frozen=True)
class SignalStep:
    # 결합 전 정규화. 기본값이 `RANK` 라서 생략한 문서도 순위 합성이 된다(spec D3 S4).
    normalization: SignalNormalization = SignalNormalization.RANK
    score_threshold: float | None = None
    regime_field_id: str | None = field(default=None, metadata=CATALOG_EQUITY_FIELD)
    regime_minimum: float | None = None


@dataclass(frozen=True)
class PortfolioStep:
    side: PortfolioSide = PortfolioSide.LONG_ONLY
    selection_count: int = 20
    weighting: WeightingMethod = WeightingMethod.EQUAL
    rebalance: RebalanceFrequency = RebalanceFrequency.MONTHLY
    selection_method: SelectionMethod = SelectionMethod.TOP_N
    short_selection_count: int = 20
    selection_percentile: float = 0.1
    rebalance_every_n_sessions: int = 21
    turnover_buffer_count: int = 0
    minimum_trade_weight: float = 0.0
    liquidity_field_id: str | None = field(default=None, metadata=CATALOG_EQUITY_FIELD)
    minimum_liquidity: float | None = None


@dataclass(frozen=True)
class RiskStep:
    gross_exposure: float = 1.0
    net_exposure: float = 1.0
    max_name_weight: float = 0.1
    max_sector_weight: float = 0.3
    sector_neutral: bool = False
    risk_field_id: str | None = field(default=None, metadata=CATALOG_EQUITY_FIELD)


ParameterValue: TypeAlias = float | int | str | bool


@dataclass(frozen=True)
class FloatParameter:
    parameter_id: str
    default: float
    minimum: float
    maximum: float
    kind: Literal["float"]
    step: float | None = None


@dataclass(frozen=True)
class IntegerParameter:
    parameter_id: str
    default: int
    minimum: int
    maximum: int
    kind: Literal["integer"]
    step: int = 1


@dataclass(frozen=True)
class ChoiceParameter:
    parameter_id: str
    default: ParameterValue
    choices: tuple[ParameterValue, ...]
    kind: Literal["choice"]


ParameterDefinition: TypeAlias = FloatParameter | IntegerParameter | ChoiceParameter


@dataclass(frozen=True, kw_only=True)
class StrategySpec:
    identity: StrategyIdentity
    title: str
    description: str = ""
    eligibility: EligibilityStep = EligibilityStep()
    # 생략해도 빈 배열이어도 구조 오류가 아니다(spec D3). 두 경우 모두 semantic
    # `strategy.factor.required`가 나서, 새 전략이 "구조 오류"가 아니라 "팩터를 추가하세요"로
    # 시작한다 — 실행 설정이 빠진 1.2 최상위 필수 키는 `schema_version`·`title` 둘뿐이다.
    factors: tuple[FactorSignal, ...] = ()
    signal: SignalStep = SignalStep()
    portfolio: PortfolioStep = PortfolioStep()
    risk: RiskStep = RiskStep()
    parameters: tuple[ParameterDefinition, ...] = field(default=(), metadata=DEFINES_PARAMETER)
