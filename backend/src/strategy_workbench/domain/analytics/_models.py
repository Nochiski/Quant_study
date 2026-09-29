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


class MetricUnavailableReason(StrEnum):
    """지표 값이 없을 때(`MetricValue.value is None`) 그 이유. 값은 wire 계약이다.

    화면 문구는 frontend i18n(`backtest.metricUnavailable.<값>`)이 소유하고, 목록은 골든
    `tests/fixtures/analytics/metric_unavailable_reasons.json` 이 두 쪽을 묶는다(이슈 #241).
    """

    ZERO_RETURN_VARIANCE = "zero_return_variance"
    NO_DOWNSIDE_VARIATION = "no_downside_variation"
    NO_DRAWDOWN = "no_drawdown"
    MAXIMUM_DRAWDOWN_NOT_RECOVERED = "maximum_drawdown_not_recovered"
    BENCHMARK_NOT_AVAILABLE = "benchmark_not_available"
    NO_CLOSED_TRADES = "no_closed_trades"
    NO_LOSING_CLOSED_TRADE = "no_losing_closed_trade"
    NO_OBSERVATIONS_IN_SCOPE = "no_observations_in_scope"
    PERIOD_UNDER_ONE_YEAR = "period_under_one_year"
    BASE_RATE_NOT_COVERED = "base_rate_not_covered"


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
    unavailable_reason: MetricUnavailableReason | None = None


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
    total_taxes: float = 0.0
    total_slippage_cost: float = 0.0
    total_carry_cost: float = 0.0
    # 구간 시작 직전 세션의 점. 있으면 수익률·연수·월별 수익률·벤치마크 수익률이 여기서 시작하고,
    # 곡선·낙폭·노출에는 들어가지 않는다. 없으면 첫 점이 기준이다.
    base: AnalysisPoint | None = None


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
    # 롤링 샤프 창의 수익률 개수. 값이 모두 비어도 화면이 이유를 말할 수 있게 싣는다(#303).
    rolling_sharpe_window_sessions: int
    # 기준금리 이력 확인일 뒤라 마지막 확인 금리를 이어 쓴 수익률 구간의 시작 세션.
    base_rate_carried_sessions: tuple[date, ...]
