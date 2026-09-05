from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class MetricCategory(StrEnum):
    RETURN = "return"
    RISK = "risk"
    RISK_ADJUSTED = "risk_adjusted"
    BENCHMARK = "benchmark"
    TRADE = "trade"
    EXPOSURE = "exposure"
    COST = "cost"


class MetricUnit(StrEnum):
    PERCENT = "percent"
    RATIO = "ratio"
    COUNT = "count"
    SESSIONS = "sessions"
    CURRENCY = "currency"


class MetricScope(StrEnum):
    FULL = "full"
    IN_SAMPLE = "in_sample"
    VALIDATION = "validation"
    OUT_OF_SAMPLE = "out_of_sample"
    WINDOW = "window"


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    label: str
    category: MetricCategory
    unit: MetricUnit
    higher_is_better: bool | None
    nullable: bool
    precision: int = 4
    version: int = 1


@dataclass(frozen=True)
class MetricValue:
    metric_id: str
    value: float | None
    scope: MetricScope
    sample_count: int
    scope_label: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class AnalysisPoint:
    session: date
    equity: float
    gross_exposure: float
    net_exposure: float
    benchmark_equity: float | None = None


@dataclass(frozen=True)
class TradeOutcome:
    security_id: str
    closed_on: date
    pnl: float
    fees: float
    slippage_cost: float


@dataclass(frozen=True)
class AnalyticsInput:
    points: tuple[AnalysisPoint, ...]
    traded_notional: float
    trades: tuple[TradeOutcome, ...] = ()
    total_fees: float = 0.0
    total_slippage_cost: float = 0.0
    total_carry_cost: float = 0.0


@dataclass(frozen=True)
class EquityCurvePoint:
    session: date
    equity: float
    benchmark_equity: float | None


@dataclass(frozen=True)
class DrawdownPoint:
    session: date
    drawdown: float


@dataclass(frozen=True)
class MonthlyReturnPoint:
    year: int
    month: int
    value: float


@dataclass(frozen=True)
class RollingMetricPoint:
    session: date
    value: float | None


@dataclass(frozen=True)
class AnalyticsReport:
    registry_version: str
    metrics: tuple[MetricValue, ...]
    equity_curve: tuple[EquityCurvePoint, ...]
    drawdown_curve: tuple[DrawdownPoint, ...]
    monthly_returns: tuple[MonthlyReturnPoint, ...]
    rolling_sharpe: tuple[RollingMetricPoint, ...]
