from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from statistics import NormalDist

from ._base_rate import BASE_RATE_CONFIRMED_ON, base_rate
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
# 달력과 맞는다. `annualization_days`는 변동성·샤프·소르티노 연율화에만 쓴다. 기준금리 일할도 같은
# ACT/365다.
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
    rolling_window: int = 126,
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
    # 엔진은 세션 종료 자산이 0 이하이면 실행을 멈춘다(EquityWipedOut). 그런 곡선은 계약 위반이다.
    for item in anchored:
        if item.equity <= 0:
            raise ValueError(
                f"analytics requires positive equity — session={item.session} equity={item.equity}"
            )
    anchored_equity = tuple(item.equity for item in anchored)
    equity = tuple(item.equity for item in points)
    returns = _returns(anchored_equity)
    total_return = equity[-1] / anchored_equity[0] - 1.0
    years = (points[-1].session - anchored[0].session).days / _DAYS_PER_YEAR
    # 1년 미만은 연율화하지 않는다(GIPS). 칼마도 같은 사유로 빈다.
    cagr_reason = MetricUnavailableReason.PERIOD_UNDER_ONE_YEAR if years < 1.0 else None
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if cagr_reason is None else None
    # 기준금리 이력보다 앞선 세션이 있으면 무위험수익률을 지어내지 않고 샤프·소르티노·롤링 샤프를
    # 비운다.
    excess = _excess_returns(anchored, returns)
    risk_free_reason = MetricUnavailableReason.BASE_RATE_NOT_COVERED if excess is None else None
    sharpe_reason = risk_free_reason or MetricUnavailableReason.ZERO_RETURN_VARIANCE
    volatility, sharpe, sortino = _risk_adjusted(returns, excess, annualization_days)
    if sharpe is None:
        sharpe_error = probabilistic = None
    else:
        sharpe_error = _sharpe_standard_error(
            sharpe, *_moments(returns), len(returns), annualization_days
        )
        probabilistic = probabilistic_sharpe(sharpe, sharpe_error)
    rolling_sharpe = _rolling_sharpe(anchored, returns, excess, annualization_days, rolling_window)
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
            unavailable_reason=reason if number is None else None,
        )

    metrics = (
        value("total_return", total_return),
        value("cagr", cagr, cagr_reason),
        value("volatility", volatility),
        value("sharpe", sharpe, sharpe_reason),
        value("sharpe_standard_error", sharpe_error, sharpe_reason),
        value("probabilistic_sharpe", probabilistic, sharpe_reason),
        value(
            "sortino", sortino, risk_free_reason or MetricUnavailableReason.NO_DOWNSIDE_VARIATION
        ),
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
        value("total_taxes", data.total_taxes),
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
        rolling_sharpe=rolling_sharpe[-len(points) :],
        base_rate_carried_sessions=tuple(
            item.session for item in anchored[:-1] if item.session > BASE_RATE_CONFIRMED_ON
        ),
    )


def _returns(equity: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(equity[index] / equity[index - 1] - 1.0 for index in range(1, len(equity)))


def _excess_returns(
    points: tuple[AnalysisPoint, ...], returns: tuple[float, ...]
) -> tuple[float, ...] | None:
    """세션 수익률에서 그 구간의 무위험수익률을 뺀다. 기준금리 이력보다 앞선 구간이 있으면 None이다.

    구간(직전 세션 → 세션)의 무위험수익률은 직전 세션에 유효한 한국은행 기준금리를 두 세션 사이
    달력 일수만큼 ACT/365로 일할한 값이다. 구간 중간에 금리가 바뀌어도 구간 시작일 금리를 쓴다.
    """
    excess: list[float] = []
    for (before, after), value in zip(pairwise(points), returns, strict=True):
        rate = base_rate(before.session)
        if rate is None:
            return None
        excess.append(value - rate * (after.session - before.session).days / _DAYS_PER_YEAR)
    return tuple(excess)


def _risk_adjusted(
    returns: tuple[float, ...], excess: tuple[float, ...] | None, annualization_days: int
) -> tuple[float, float | None, float | None]:
    """샤프 분모는 원수익률 표준편차다. 무위험수익률은 구간 시작에 이미 알려져 위험이 아니다.

    소르티노 목표는 무위험수익률이다. `excess`가 None이면(기준금리 이력 이전) 둘 다 비운다.
    """
    if len(returns) <= 1:
        return 0.0, None, None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    standard_deviation = math.sqrt(variance)
    volatility = standard_deviation * math.sqrt(annualization_days)
    if excess is None:
        return volatility, None, None
    excess_mean = sum(excess) / len(excess)
    sharpe = (
        excess_mean / standard_deviation * math.sqrt(annualization_days)
        if standard_deviation > 0
        else None
    )
    downside = tuple(item for item in excess if item < 0)
    downside_deviation = (
        math.sqrt(sum(item**2 for item in downside) / len(excess)) if downside else 0.0
    )
    sortino = (
        excess_mean / downside_deviation * math.sqrt(annualization_days)
        if downside_deviation > 0
        else None
    )
    return volatility, sharpe, sortino


def session_sharpe(sharpe: float, annualization_days: int) -> float:
    """연율화한 `sharpe` 지표를 세션 단위로 되돌린다 — `_risk_adjusted` 가 곱한 √A 를 나눈다.

    실행마다 연환산 거래일이 달라도 같은 척도라 계열 시도의 대표 샤프로 쓴다(검증 랩 spec D2).
    """
    return sharpe / math.sqrt(annualization_days)


def probabilistic_sharpe(sharpe: float, standard_error: float, benchmark: float = 0.0) -> float:
    """PSR(Bailey·López de Prado 2012 식 11): 진짜 샤프가 `benchmark`보다 클 확률 Φ((SR − SR*) / σ̂).

    `standard_error`는 같은 실행의 `sharpe_standard_error` 값이다. 지표의 기준은 0이고(검증 랩
    spec D8), 계열 DSR은 `benchmark`에 기대 최대 샤프를 넣어 같은 식을 쓴다.
    """
    return NormalDist().cdf((sharpe - benchmark) / standard_error)


def _moments(returns: tuple[float, ...]) -> tuple[float, float]:
    """원수익률의 왜도와 원(비초과) 첨도. 분모 n인 표본 모멘트다(논문 구현이 쓴 scipy 기본값)."""
    mean = sum(returns) / len(returns)
    central = tuple(item - mean for item in returns)
    variance = sum(item**2 for item in central) / len(central)
    skewness = sum(item**3 for item in central) / len(central) / variance**1.5
    kurtosis = sum(item**4 for item in central) / len(central) / variance**2
    return skewness, kurtosis


def _sharpe_standard_error(
    sharpe: float, skewness: float, kurtosis: float, observations: int, annualization_days: int
) -> float:
    """연 샤프의 표준오차. 일 샤프 s = SR/√A 의 σ̂ = √((1 − γ₃s + (γ₄−1)s²/4) / (n−1))을 √A 배 했다.

    Mertens(2002)·Bailey·López de Prado(2012) 식이라 왜도·두꺼운 꼬리는 넣지만 수익률이 날마다
    독립이라고 본다. 양의 자기상관이면 실제 오차가 더 크고, 음의 자기상관이면 더 작을 수 있다.
    γ₄ ≥ 1 + γ₃²(피어슨 부등식)라 근호 안은 음수가 아니다. 정규(γ₃ = 0, γ₄ = 3)면 분모만 n−1인
    Lo(2002) 식이다.
    """
    return math.sqrt(
        (
            annualization_days
            - skewness * sharpe * math.sqrt(annualization_days)
            + (kurtosis - 1) / 4 * sharpe**2
        )
        / (observations - 1)
    )


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
    returns: tuple[float, ...],
    excess: tuple[float, ...] | None,
    annualization_days: int,
    window: int,
) -> tuple[RollingMetricPoint, ...]:
    result: list[RollingMetricPoint] = []
    for index, point in enumerate(points):
        if index < window:
            result.append(RollingMetricPoint(point.session, None))
            continue
        segment = slice(index - window, index)
        _, sharpe, _ = _risk_adjusted(
            returns[segment], None if excess is None else excess[segment], annualization_days
        )
        result.append(RollingMetricPoint(point.session, sharpe))
    return tuple(result)
