from __future__ import annotations

from collections.abc import Mapping
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
# 문서 안 팩터 네임스페이스: `factors` 배열이 정의하고(`factor_id`) 리스크 역가중이 참조한다(P2-06).
DEFINES_FACTOR = {"defines": "factor"}
REFERENCE_FACTOR = {"reference": "factor"}
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


class AppliedStage(StrEnum):
    """전략 파이프라인의 단계 어휘 — 유니버스(`ELIGIBILITY`) → 알파(`SIGNAL`) → 포트폴리오
    구성 → 리스크 → 실행.

    두 사실이 같은 값을 쓴다. 제약 행이 적용되는 시점(`_constraints.py`, runtime schema
    `x-applied-stage`)과, 그래프 표현(파이프라인)이 필드를 보이는 단계(field metadata
    `stage`, runtime schema `x-stage`, P4-01)다. 필드는 적용되는 곳에 보인다: 자기
    `x-stage`, 없으면 자기 `x-applied-stage`, 없으면 가장 가까운 조상의 `x-stage` 다.
    `DATA`·`EXECUTION` 은 실행 설정(`domain/backtest`)이 자기 제약 행에 붙이는 단계이고,
    전략 문서에는 1.2 부터 해당 행이 없다.
    """

    DATA = "data"
    ELIGIBILITY = "eligibility"
    SIGNAL = "signal"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    EXECUTION = "execution"


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
    # 유동성 필터는 후보를 거를 때 적용돼 그래프 표현의 1단계 유니버스에 보인다(P4-01 리드 결정).
    # `minimum_liquidity` 는 제약 행의 적용 시점이 같은 단계를 말한다.
    liquidity_field_id: str | None = field(
        default=None, metadata={**CATALOG_EQUITY_FIELD, "stage": AppliedStage.ELIGIBILITY}
    )
    minimum_liquidity: float | None = None


@dataclass(frozen=True)
class RiskStep:
    gross_exposure: float = 1.0
    net_exposure: float = 1.0
    max_name_weight: float = 0.1
    max_sector_weight: float = 0.3
    sector_neutral: bool = False
    # 역가중 원천(필드·팩터)은 비중을 줄 때 쓰여 그래프 표현의 3단계 비중 카드가 편집한다(P4-01).
    risk_field_id: str | None = field(
        default=None, metadata={**CATALOG_EQUITY_FIELD, "stage": AppliedStage.PORTFOLIO}
    )
    # 역가중 원천을 데이터 필드 대신 문서의 팩터 출력으로 쓴다(schema 1.2, spec D3 S6).
    # `weighting: risk` 에서만 읽히고, 그때 참조 팩터는 합성 점수에서 빠진다
    # (`inverse_risk_factor_id`). `risk_field_id` 와 함께 쓰면 검증 error 다.
    risk_factor_id: str | None = field(
        default=None, metadata={**REFERENCE_FACTOR, "stage": AppliedStage.PORTFOLIO}
    )


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


def normalized_parameter_value(
    parameter: ParameterDefinition, value: ParameterValue
) -> ParameterValue | None:
    """값을 파라미터 선언 타입으로 맞춘 값. 타입·범위·선택지 밖이면 `None`.

    문서 hydrate 가 같은 칸에 하는 정규화와 같다: 정수 칸은 정수로 떨어지는 float 을 int 로
    (`20.0` → `20`), 실수 칸은 int 를 float 으로 바꾼다. bool 은 숫자 칸 값이 아니고, 선택지는
    `True == 1` 로 맞추지 않고 bool 끼리만 맞춘다. 선택지는 문서의 그 선택지 값을 돌려준다
    (hydrate 가 정수로 떨어지는 float 선택지를 이미 int 로 접었다).
    """
    if isinstance(parameter, ChoiceParameter):
        return next(
            (
                choice
                for choice in parameter.choices
                if choice == value and isinstance(choice, bool) is isinstance(value, bool)
            ),
            None,
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(parameter, IntegerParameter):
        if isinstance(value, float) and not value.is_integer():
            return None
        number: int | float = int(value)
    else:
        number = float(value)
    return number if parameter.minimum <= number <= parameter.maximum else None


def parameter_value_allowed(parameter: ParameterDefinition, value: ParameterValue) -> bool:
    """값이 파라미터 정의의 타입·범위·선택지 안인가(`normalized_parameter_value` 가 값을 내는가).

    검증기의 기본값 판정, 실험 탐색 값, 실행 요청의 해소 값(검증 랩 V3-02)이 이 술어 하나를 쓴다.
    `step` 은 보지 않는다. 간격은 탐색 격자를 펼치는 폭일 뿐이고, 여기서 간격 정렬을 요구하면
    간격에 맞지 않는 기본값을 가진 저장 리비전이 새로 검증 오류가 된다.
    """
    return normalized_parameter_value(parameter, value) is not None


def describe_allowed_parameter_values(parameter: ParameterDefinition) -> str:
    """진단 문장에 싣는 허용 범위(`key=value`)."""
    if isinstance(parameter, ChoiceParameter):
        return f"choices={list(parameter.choices)}"
    return f"kind={parameter.kind} minimum={parameter.minimum} maximum={parameter.maximum}"


class InvalidParameterValueError(ValueError):
    """실행에 넘긴 파라미터 값을 문서 정의로 해소할 수 없다(검증 랩 spec D4)."""

    def __init__(self, parameter_id: str, message: str) -> None:
        super().__init__(message)
        self.parameter_id = parameter_id


def resolve_parameter_values(
    parameters: tuple[ParameterDefinition, ...], requested: Mapping[str, ParameterValue]
) -> dict[str, ParameterValue]:
    """문서 파라미터마다 실행에 쓸 값. 요청 값은 선언 타입으로 맞추고, 없으면 문서 기본값이다.

    선언된 파라미터를 전부 담아 돌려준다. 값을 생략한 요청과 기본값을 명시한 요청이 같은 해소
    결과가 되어 실행 지문도 같다.

    Raises:
        InvalidParameterValueError: 문서에 없는 `parameter_id` 이거나 허용 밖 값이다.
    """
    declared = [parameter.parameter_id for parameter in parameters]
    unknown = sorted(set(requested) - set(declared))
    if unknown:
        raise InvalidParameterValueError(
            unknown[0],
            f"전략 문서에 없는 파라미터입니다 — parameter_id={unknown[0]} unknown={unknown} "
            f"declared={declared}",
        )
    resolved: dict[str, ParameterValue] = {}
    for parameter in parameters:
        value = requested.get(parameter.parameter_id, parameter.default)
        normalized = normalized_parameter_value(parameter, value)
        if normalized is None:
            raise InvalidParameterValueError(
                parameter.parameter_id,
                f"파라미터 값이 정의 밖입니다 — parameter_id={parameter.parameter_id} "
                f"value={value!r} {describe_allowed_parameter_values(parameter)}",
            )
        resolved[parameter.parameter_id] = normalized
    return resolved


@dataclass(frozen=True, kw_only=True)
class StrategySpec:
    identity: StrategyIdentity
    title: str
    description: str = ""
    # 섹션마다 그래프 표현(파이프라인)의 단계가 있고(`x-stage`, P4-01), 섹션 안 필드는 섹션을
    # 따른다. 문서 머리와 탐색 파라미터는 단계가 없다.
    eligibility: EligibilityStep = field(
        default=EligibilityStep(), metadata={"stage": AppliedStage.ELIGIBILITY}
    )
    # 생략해도 빈 배열이어도 구조 오류가 아니다(spec D3). 두 경우 모두 semantic
    # `strategy.factor.required`가 나서, 새 전략이 "구조 오류"가 아니라 "팩터를 추가하세요"로
    # 시작한다 — 실행 설정이 빠진 1.2 최상위 필수 키는 `schema_version`·`title` 둘뿐이다.
    factors: tuple[FactorSignal, ...] = field(
        default=(), metadata={**DEFINES_FACTOR, "stage": AppliedStage.SIGNAL}
    )
    signal: SignalStep = field(default=SignalStep(), metadata={"stage": AppliedStage.SIGNAL})
    portfolio: PortfolioStep = field(
        default=PortfolioStep(), metadata={"stage": AppliedStage.PORTFOLIO}
    )
    risk: RiskStep = field(default=RiskStep(), metadata={"stage": AppliedStage.RISK})
    parameters: tuple[ParameterDefinition, ...] = field(default=(), metadata=DEFINES_PARAMETER)


def inverse_risk_factor_id(spec: StrategySpec) -> str | None:
    """리스크 역가중에 쓰이는 팩터 id — 쓰이지 않으면 `None` (spec D3 S6).

    `weighting: risk` 이고 `risk_factor_id` 가 설정됐을 때만 값이 있다. 다른 모드에서는
    `risk_factor_id` 를 읽지 않으므로 그 팩터는 일반 알파로 남는다 — 적용 조건이 붙은 필드가
    조건 밖에서 알파 합성을 바꾸면 "모드별로 읽히는 필드" 계약과 어긋난다. 이 판정의 유일한
    owner 이고, 컴파일러(합성 제외·역가중)·검증(제외 warning·알파 0개 error)·설명이 같은 함수를
    읽는다.
    """
    if spec.portfolio.weighting is not WeightingMethod.RISK:
        return None
    return spec.risk.risk_factor_id


def composite_factors(spec: StrategySpec) -> tuple[FactorSignal, ...]:
    """합성 점수에 들어가는 알파 팩터.

    역가중 팩터는 `weight` 와 분모 `Σ|weight|` 양쪽에서 빠진다.
    """
    excluded = inverse_risk_factor_id(spec)
    return tuple(factor for factor in spec.factors if factor.factor_id != excluded)
