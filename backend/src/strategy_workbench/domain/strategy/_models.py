from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal, TypeAlias

from strategy_workbench.domain.factor.facade.expression import FactorGraph


class Market(StrEnum):
    KRX = "KRX"


class DataFrequency(StrEnum):
    DAILY = "daily"


class ComparisonOperator(StrEnum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"


class FactorDirection(StrEnum):
    HIGH = "high"
    LOW = "low"


class SignalMethod(StrEnum):
    WEIGHTED_SUM = "weighted_sum"
    RANK_THRESHOLD = "rank_threshold"


class PortfolioSide(StrEnum):
    LONG_ONLY = "long_only"
    LONG_SHORT = "long_short"


class WeightingMethod(StrEnum):
    EQUAL = "equal"
    FACTOR_SCORE = "factor_score"
    RANK = "rank"


class RebalanceFrequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class ExecutionTiming(StrEnum):
    NEXT_OPEN = "next_open"


class OrderStyle(StrEnum):
    MARKET = "market"


@dataclass(frozen=True)
class StrategyIdentity:
    strategy_id: str
    revision: int
    schema_version: str = "1.0"


@dataclass(frozen=True)
class DataStep:
    market: Market
    start: date
    end: date
    universe_id: str
    frequency: DataFrequency = DataFrequency.DAILY


@dataclass(frozen=True)
class EligibilityRule:
    field_id: str
    operator: ComparisonOperator
    value: float


@dataclass(frozen=True)
class EligibilityStep:
    rules: tuple[EligibilityRule, ...] = ()


@dataclass(frozen=True)
class FactorSignal:
    factor_id: str
    label: str
    direction: FactorDirection
    weight: float
    graph: FactorGraph


@dataclass(frozen=True)
class FactorStep:
    factors: tuple[FactorSignal, ...]


@dataclass(frozen=True)
class SignalStep:
    method: SignalMethod = SignalMethod.WEIGHTED_SUM
    entry_percentile: float = 0.1


@dataclass(frozen=True)
class PortfolioStep:
    side: PortfolioSide = PortfolioSide.LONG_ONLY
    selection_count: int = 20
    weighting: WeightingMethod = WeightingMethod.EQUAL
    rebalance: RebalanceFrequency = RebalanceFrequency.MONTHLY


@dataclass(frozen=True)
class RiskStep:
    gross_exposure: float = 1.0
    net_exposure: float = 1.0
    max_name_weight: float = 0.1
    max_sector_weight: float = 0.3


@dataclass(frozen=True)
class ExecutionStep:
    timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    order_style: OrderStyle = OrderStyle.MARKET
    participation_rate: float = 0.1
    fee_bps: float = 15.0
    slippage_bps: float = 10.0


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


@dataclass(frozen=True)
class StrategySpec:
    identity: StrategyIdentity
    title: str
    description: str
    data: DataStep
    eligibility: EligibilityStep
    factors: FactorStep
    signal: SignalStep
    portfolio: PortfolioStep
    risk: RiskStep
    execution: ExecutionStep
    parameters: tuple[ParameterDefinition, ...] = ()
