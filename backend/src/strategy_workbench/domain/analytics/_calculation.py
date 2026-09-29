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
    MetricUnavailableReason,
    MetricValue,
    MonthlyReturnPoint,
    RollingMetricPoint,
)
from ._registry import MetricRegistry

# 연수는 기준일부터 마지막 세션까지의 달력 일수 / 365(ACT/365)다. 원화 금리의 일할 관행과 같고,
# 달력 1년이 윤년과 무관하게 1년 이상으로 세어져 "1년 미만은 연율화하지 않는다"(GIPS) 경계가
# 달력과 맞는다. `annualization_days`는 변동성·샤프·소르티노 연율화에만 쓴다.
_DAYS_PER_YEAR = 365


def unavailable_metric_values(
    registry: MetricRegistry,
    *,
    scope: MetricScope,
    scope_label: str | None,
    reason: MetricUnavailableReason,
) -> tuple[MetricValue, ...]:
    """Keep an explicitly requested empty scope visible instead of dropping it."""
    return tuple(
        MetricValue(
            metric_id=definition.metric_id,
            value=None,
            scope=scope,
            scope_label=scope_label,
            sample_count=0,
            unavailable_reason=reason.value,
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
    if data.base is not None and data.base.session >= points[0].session:
        raise ValueError("analytics base must precede the first equity point")
    # 수익률·연수·낙폭·월별·벤치마크는 기준점(구간 직전 세션, 없으면 첫 점)에서 시작한다. 기준점은
    # 곡선 점이 아니라서 곡선·노출·평균 자산에는 들어가지 않는다.
    anchored = points if data.base is None else (data.base, *points)
    anchored_equity = tuple(item.equity for item in anchored)
    equity = tuple(item.equity for item in points)
    returns = _returns(anchored_equity)
    total_return = equity[-1] / anchored_equity[0] - 1.0
    years = (points[-1].session - anchored[0].session).days / _DAYS_PER_YEAR
    # 1년 미만은 연율화하지 않는다(GIPS). 자산이 음수로 끝나면 음수의 거듭제곱근이라 CAGR 이 없다.
    # 정확히 0 으로 끝나면 CAGR 은 -100% 다. 칼마도 같은 사유로 빈다.
    cagr_reason = (
        MetricUnavailableReason.PERIOD_UNDER_ONE_YEAR
        if years < 1.0
        else MetricUnavailableReason.NEGATIVE_EQUITY
        if total_return < -1.0
        else None
    )
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if cagr_reason is None else None
    volatility, sharpe, sortino = _risk_adjusted(returns, annualization_days)
    drawdowns = _drawdowns(anchored)[-len(points) :]
    max_drawdown = min(item.drawdown for item in drawdowns)
    max_duration, recovery = _drawdown_timing(anchored_equity)
    calmar = cagr / abs(max_drawdown) if cagr is not None and max_drawdown < 0 else None
    average_equity = sum(equity) / len(equity)
    turnover = data.traded_notional / average_equity if average_equity > 0 else 0.0
    benchmark_return = _benchmark_return(anchored)
    winning = tuple(trade.pnl for trade in data.trades if trade.pnl > 0)
    losing = tuple(trade.pnl for trade in data.trades if trade.pnl < 0)
    win_rate = len(winning) / len(data.trades) if data.trades else None
    profit_factor = sum(winning) / abs(sum(losing)) if losing else None
    sample_count = len(returns)

    def value(
        metric_id: str, number: float | None, reason: MetricUnavailableReason | None = None
    ) -> MetricValue:
        registry.get(metric_id)
        return MetricValue(
            metric_id=metric_id,
            value=number,
            scope=scope,
            scope_label=scope_label,
            sample_count=sample_count,
            unavailable_reason=reason.value if number is None and reason is not None else None,
        )

    metrics = (
        value("total_return", total_return),
        value("cagr", cagr, cagr_reason),
        value("volatility", volatility),
        value("sharpe", sharpe, MetricUnavailableReason.ZERO_RETURN_VARIANCE),
        value("sortino", sortino, MetricUnavailableReason.NO_DOWNSIDE_VARIATION),
        value("max_drawdown", max_drawdown),
        value("calmar", calmar, cagr_reason or MetricUnavailableReason.NO_DRAWDOWN),
        value("turnover", turnover),
        value("max_drawdown_duration_sessions", float(max_duration)),
        value(
            "max_drawdown_recovery_sessions",
            float(recovery) if recovery is not None else None,
            MetricUnavailableReason.MAXIMUM_DRAWDOWN_NOT_RECOVERED,
        ),
        value(
            "benchmark_return", benchmark_return, MetricUnavailableReason.BENCHMARK_NOT_AVAILABLE
        ),
        value(
            "excess_return",
            total_return - benchmark_return if benchmark_return is not None else None,
            MetricUnavailableReason.BENCHMARK_NOT_AVAILABLE,
        ),
        value("trade_count", float(len(data.trades))),
        value("win_rate", win_rate, MetricUnavailableReason.NO_CLOSED_TRADES),
        value("profit_factor", profit_factor, MetricUnavailableReason.NO_LOSING_CLOSED_TRADE),
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
        monthly_returns=_monthly_returns(points, anchored_equity[0]),
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


def _monthly_returns(
    points: tuple[AnalysisPoint, ...], previous_close: float
) -> tuple[MonthlyReturnPoint, ...]:
    grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
    for point in points:
        grouped[(point.session.year, point.session.month)].append(point.equity)
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
