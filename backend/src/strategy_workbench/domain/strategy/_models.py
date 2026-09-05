from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal, TypeAlias

from strategy_workbench.domain.factor.facade.expression import FactorGraph

# Editor metadata for identifier fields (see domain.factor._nodes for the node-side markers).
CATALOG_UNIVERSE = {"catalog": "universe"}
CATALOG_EQUITY_FIELD = {"catalog": "equity-field"}
DEFINES_PARAMETER = {"defines": "parameter"}


def _factor_authoring(source: str, *, identity: bool = False) -> dict[str, object]:
    """Describe how a catalog row supplies one required FactorSignal authoring value."""
    metadata: dict[str, object] = {"authoring-source": source}
    if identity:
        metadata["authoring-identity"] = True
    return metadata


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
    RISK = "risk"


class SelectionMethod(StrEnum):
    TOP_N = "top_n"
    PERCENTILE = "percentile"


class RebalanceFrequency(StrEnum):
    EVERY_N_SESSIONS = "every_n_sessions"
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
    universe_id: str = field(metadata=CATALOG_UNIVERSE)
    frequency: DataFrequency = DataFrequency.DAILY


@dataclass(frozen=True)
class EligibilityRule:
    field_id: str = field(metadata=CATALOG_EQUITY_FIELD)
    operator: ComparisonOperator
    value: float


@dataclass(frozen=True)
class EligibilityStep:
    rules: tuple[EligibilityRule, ...] = ()


@dataclass(frozen=True)
class FactorSignal:
    factor_id: str = field(metadata=_factor_authoring("factor_id", identity=True))
    label: str = field(metadata=_factor_authoring("label"))
    direction: FactorDirection = field(metadata=_factor_authoring("preference"))
    weight: float = field(metadata={"authoring-default": 1.0})
    graph: FactorGraph = field(metadata=_factor_authoring("default_graph"))


@dataclass(frozen=True)
class FactorStep:
    factors: tuple[FactorSignal, ...]


@dataclass(frozen=True)
class SignalStep:
    method: SignalMethod = SignalMethod.WEIGHTED_SUM
    entry_percentile: float = 0.1
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
    parameters: tuple[ParameterDefinition, ...] = field(default=(), metadata=DEFINES_PARAMETER)
