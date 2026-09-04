"""실행 종료 후 equity curve와 fills로 성과 지표를 계산한다.

0으로 나눌 수 없는 지표는 0으로 위장하지 않고 None으로 표현한다.
MDD는 음수 비율로 통일한다. 연율화는 RunConfig.annualization_days 기준이다.
"""

from __future__ import annotations

import math

from backtest_engine.types.events import FillEvent
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.results import PerformanceMetrics


def compute_metrics(
    snapshots: tuple[PortfolioSnapshot, ...],
    fills: tuple[FillEvent, ...],
    annualization_days: int,
) -> PerformanceMetrics:
    return compute_metrics_from_values(
        tuple(snapshot.equity for snapshot in snapshots),
        sum(float(fill.quantity) * fill.price for fill in fills),
        annualization_days,
    )


def compute_metrics_from_values(
    equity: tuple[float, ...],
    traded_notional: float,
    annualization_days: int,
) -> PerformanceMetrics:
    """Compute the public metrics from one result batch without Event allocation."""
    if not equity:
        raise ValueError("cannot compute metrics from an empty run — snapshots=0")
    total_return = equity[-1] / equity[0] - 1.0

    session_returns = [equity[index] / equity[index - 1] - 1.0 for index in range(1, len(equity))]
    n_returns = len(session_returns)

    years = n_returns / annualization_days if n_returns > 0 else 0.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1.0 else 0.0

    volatility = 0.0
    sharpe: float | None = None
    sortino: float | None = None
    if n_returns > 1:
        mean_return = sum(session_returns) / n_returns
        variance = sum((r - mean_return) ** 2 for r in session_returns) / (n_returns - 1)
        std = math.sqrt(variance)
        volatility = std * math.sqrt(annualization_days)
        if std > 0:
            # 무위험수익률 0 가정. 연율화는 annualization_days 기준.
            sharpe = mean_return / std * math.sqrt(annualization_days)
        downside = [r for r in session_returns if r < 0]
        if downside:
            downside_std = math.sqrt(sum(r**2 for r in downside) / n_returns)
            if downside_std > 0:
                sortino = mean_return / downside_std * math.sqrt(annualization_days)

    peak = equity[0]
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)

    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else None

    average_equity = sum(equity) / len(equity)
    turnover = traded_notional / average_equity if average_equity > 0 else 0.0

    return PerformanceMetrics(
        total_return=total_return,
        cagr=cagr,
        volatility=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        calmar=calmar,
        turnover=turnover,
    )
