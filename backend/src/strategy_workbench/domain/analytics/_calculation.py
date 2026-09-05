from __future__ import annotations

import math
from collections import defaultdict

from ._models import (
    AnalysisPoint,
    AnalyticsInput,
    AnalyticsReport,
    DrawdownPoint,
    EquityCurvePoint,
    MetricScope,
    MetricValue,
    MonthlyReturnPoint,
    RollingMetricPoint,
)
from ._registry import MetricRegistry


def unavailable_metric_values(
    registry: MetricRegistry,
    *,
    scope: MetricScope,
    scope_label: str | None,
    reason: str,
) -> tuple[MetricValue, ...]:
    """Keep an explicitly requested empty scope visible instead of dropping it."""
    return tuple(
        MetricValue(
            metric_id=definition.metric_id,
            value=None,
            scope=scope,
            scope_label=scope_label,
            sample_count=0,
            unavailable_reason=reason,
        )
        for definition in registry.definitions()
    )


def compute_analytics(
    data: AnalyticsInput,
    registry: MetricRegistry,
    *,
    annualization_days: int = 252,
    scope: MetricScope = MetricScope.FULL,
    scope_label: str | None = None,
    rolling_window: int = 21,
) -> AnalyticsReport:
    if not data.points:
        raise ValueError("analytics requires at least one equity point")
    if annualization_days <= 0 or rolling_window <= 1:
        raise ValueError("annualization_days and rolling_window must be positive")
    points = tuple(sorted(data.points, key=lambda item: item.session))
    equity = tuple(item.equity for item in points)
    returns = _returns(equity)
    total_return = equity[-1] / equity[0] - 1.0
    years = len(returns) / annualization_days
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1.0 else 0.0
    volatility, sharpe, sortino = _risk_adjusted(returns, annualization_days)
    drawdowns = _drawdowns(points)
    max_drawdown = min(item.drawdown for item in drawdowns)
    max_duration, recovery = _drawdown_timing(equity)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else None
    average_equity = sum(equity) / len(equity)
    turnover = data.traded_notional / average_equity if average_equity > 0 else 0.0
    benchmark_return = _benchmark_return(points)
    winning = tuple(trade.pnl for trade in data.trades if trade.pnl > 0)
    losing = tuple(trade.pnl for trade in data.trades if trade.pnl < 0)
    win_rate = len(winning) / len(data.trades) if data.trades else None
    profit_factor = sum(winning) / abs(sum(losing)) if losing else None
    sample_count = len(returns)

    def value(metric_id: str, number: float | None, reason: str | None = None) -> MetricValue:
        registry.get(metric_id)
        return MetricValue(
            metric_id=metric_id,
            value=number,
            scope=scope,
            scope_label=scope_label,
            sample_count=sample_count,
            unavailable_reason=reason if number is None else None,
        )

    metrics = (
        value("total_return", total_return),
        value("cagr", cagr),
        value("volatility", volatility),
        value("sharpe", sharpe, "zero_return_variance"),
        value("sortino", sortino, "no_downside_variation"),
        value("max_drawdown", max_drawdown),
        value("calmar", calmar, "no_drawdown"),
        value("turnover", turnover),
        value("max_drawdown_duration_sessions", float(max_duration)),
        value(
            "max_drawdown_recovery_sessions",
            float(recovery) if recovery is not None else None,
            "maximum_drawdown_not_recovered",
        ),
        value("benchmark_return", benchmark_return, "benchmark_not_available"),
        value(
            "excess_return",
            total_return - benchmark_return if benchmark_return is not None else None,
            "benchmark_not_available",
        ),
        value("trade_count", float(len(data.trades))),
        value("win_rate", win_rate, "no_closed_trades"),
        value("profit_factor", profit_factor, "no_losing_closed_trade"),
        value(
            "average_gross_exposure",
            sum(item.gross_exposure for item in points) / len(points),
        ),
        value("maximum_gross_exposure", max(item.gross_exposure for item in points)),
        value(
            "average_net_exposure",
            sum(item.net_exposure for item in points) / len(points),
        ),
        value("total_fees", data.total_fees),
        value("total_slippage_cost", data.total_slippage_cost),
        value("total_carry_cost", data.total_carry_cost),
    )
    return AnalyticsReport(
        registry_version=registry.version,
        metrics=metrics,
        equity_curve=tuple(
            EquityCurvePoint(item.session, item.equity, item.benchmark_equity) for item in points
        ),
        drawdown_curve=drawdowns,
        monthly_returns=_monthly_returns(points),
        rolling_sharpe=_rolling_sharpe(points, annualization_days, rolling_window),
    )


def _returns(equity: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(equity[index] / equity[index - 1] - 1.0 for index in range(1, len(equity)))


def _risk_adjusted(
    returns: tuple[float, ...], annualization_days: int
) -> tuple[float, float | None, float | None]:
    if len(returns) <= 1:
        return 0.0, None, None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    standard_deviation = math.sqrt(variance)
    volatility = standard_deviation * math.sqrt(annualization_days)
    sharpe = (
        mean / standard_deviation * math.sqrt(annualization_days)
        if standard_deviation > 0
        else None
    )
    downside = tuple(item for item in returns if item < 0)
    downside_deviation = (
        math.sqrt(sum(item**2 for item in downside) / len(returns)) if downside else 0.0
    )
    sortino = (
        mean / downside_deviation * math.sqrt(annualization_days)
        if downside_deviation > 0
        else None
    )
    return volatility, sharpe, sortino


def _drawdowns(points: tuple[AnalysisPoint, ...]) -> tuple[DrawdownPoint, ...]:
    peak = points[0].equity
    result: list[DrawdownPoint] = []
    for point in points:
        peak = max(peak, point.equity)
        result.append(DrawdownPoint(point.session, point.equity / peak - 1.0))
    return tuple(result)


def _drawdown_timing(equity: tuple[float, ...]) -> tuple[int, int | None]:
    peak_value = equity[0]
    peak_index = 0
    underwater_start: int | None = None
    max_duration = 0
    worst_drawdown = 0.0
    worst_peak_index = 0
    worst_trough_index = 0
    for index, value in enumerate(equity):
        if value >= peak_value:
            peak_value = value
            peak_index = index
            underwater_start = None
        else:
            if underwater_start is None:
                underwater_start = index
            max_duration = max(max_duration, index - peak_index)
            drawdown = value / peak_value - 1.0
            if drawdown < worst_drawdown:
                worst_drawdown = drawdown
                worst_peak_index = peak_index
                worst_trough_index = index
    if worst_drawdown == 0:
        return max_duration, None
    recovery = next(
        (
            index - worst_trough_index
            for index in range(worst_trough_index + 1, len(equity))
            if equity[index] >= equity[worst_peak_index]
        ),
        None,
    )
    return max_duration, recovery


def _benchmark_return(points: tuple[AnalysisPoint, ...]) -> float | None:
    values = tuple(item.benchmark_equity for item in points if item.benchmark_equity is not None)
    if len(values) != len(points) or not values or values[0] == 0:
        return None
    return values[-1] / values[0] - 1.0


def _monthly_returns(points: tuple[AnalysisPoint, ...]) -> tuple[MonthlyReturnPoint, ...]:
    grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
    for point in points:
        grouped[(point.session.year, point.session.month)].append(point.equity)
    previous_close = points[0].equity
    result: list[MonthlyReturnPoint] = []
    for (year, month), values in sorted(grouped.items()):
        result.append(MonthlyReturnPoint(year, month, values[-1] / previous_close - 1.0))
        previous_close = values[-1]
    return tuple(result)


def _rolling_sharpe(
    points: tuple[AnalysisPoint, ...],
    annualization_days: int,
    window: int,
) -> tuple[RollingMetricPoint, ...]:
    returns = _returns(tuple(item.equity for item in points))
    result: list[RollingMetricPoint] = []
    for index, point in enumerate(points):
        if index < window:
            result.append(RollingMetricPoint(point.session, None))
            continue
        segment = returns[index - window : index]
        _, sharpe, _ = _risk_adjusted(segment, annualization_days)
        result.append(RollingMetricPoint(point.session, sharpe))
    return tuple(result)
