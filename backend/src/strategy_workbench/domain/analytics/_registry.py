from __future__ import annotations

from ._models import MetricCategory, MetricDefinition, MetricUnit

# v2(#274): CAGR 연수를 달력 일수로 세고 1년 미만은 None으로 둔다. 구간 지표는 직전 세션이
# 기준이다. v2 지표 집합에 total_taxes 포함(#281 V2-01).
# v3(#274): 샤프·소르티노·롤링 샤프를 한국은행 기준금리 초과수익으로 잰다.
# v4(#274): 샤프 표준오차 지표를 더하고 롤링 샤프 창을 21세션에서 126세션(6개월)으로 늘린다.
# v5(#281 V4-01): 샤프 표준오차에 왜도·첨도를 넣고 분모를 n−1로 두며, PSR(기준 0)을 더한다.
REGISTRY_VERSION = "metric-registry-v5"


class MetricRegistry:
    def __init__(self, version: str, definitions: tuple[MetricDefinition, ...]) -> None:
        if not version.strip():
            raise ValueError("metric registry version must not be empty")
        ids = tuple(item.metric_id for item in definitions)
        if len(ids) != len(set(ids)):
            raise ValueError("metric definition ids must be unique")
        self._definitions = definitions
        self._by_id = {item.metric_id: item for item in definitions}
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def definitions(self) -> tuple[MetricDefinition, ...]:
        return self._definitions

    def get(self, metric_id: str) -> MetricDefinition:
        try:
            return self._by_id[metric_id]
        except KeyError:
            raise KeyError(f"unknown metric definition: {metric_id}") from None


def build_default_metric_registry() -> MetricRegistry:
    percent = MetricUnit.PERCENT
    ratio = MetricUnit.RATIO
    count = MetricUnit.COUNT
    sessions = MetricUnit.SESSIONS
    currency = MetricUnit.CURRENCY
    definitions = (
        MetricDefinition(
            "total_return", "Total return", MetricCategory.RETURN, percent, True, False
        ),
        MetricDefinition("cagr", "CAGR", MetricCategory.RETURN, percent, True, True, version=2),
        MetricDefinition("volatility", "Volatility", MetricCategory.RISK, percent, False, False),
        MetricDefinition(
            "sharpe", "Sharpe ratio", MetricCategory.RISK_ADJUSTED, ratio, True, True, version=2
        ),
        MetricDefinition(
            "sharpe_standard_error",
            "Sharpe standard error",
            MetricCategory.RISK_ADJUSTED,
            ratio,
            None,
            True,
        ),
        MetricDefinition(
            "probabilistic_sharpe",
            "Probabilistic Sharpe ratio",
            MetricCategory.RISK_ADJUSTED,
            percent,
            True,
            True,
        ),
        MetricDefinition(
            "sortino", "Sortino ratio", MetricCategory.RISK_ADJUSTED, ratio, True, True, version=2
        ),
        MetricDefinition(
            "max_drawdown", "Maximum drawdown", MetricCategory.RISK, percent, True, False
        ),
        MetricDefinition(
            "calmar", "Calmar ratio", MetricCategory.RISK_ADJUSTED, ratio, True, True, version=2
        ),
        MetricDefinition("turnover", "Turnover", MetricCategory.TRADE, ratio, False, False),
        MetricDefinition(
            "max_drawdown_duration_sessions",
            "Maximum drawdown duration",
            MetricCategory.RISK,
            sessions,
            False,
            False,
            precision=0,
        ),
        MetricDefinition(
            "max_drawdown_recovery_sessions",
            "Maximum drawdown recovery",
            MetricCategory.RISK,
            sessions,
            False,
            True,
            precision=0,
        ),
        MetricDefinition(
            "benchmark_return", "Benchmark return", MetricCategory.BENCHMARK, percent, True, True
        ),
        MetricDefinition(
            "excess_return", "Excess return", MetricCategory.BENCHMARK, percent, True, True
        ),
        MetricDefinition(
            "trade_count", "Closed trades", MetricCategory.TRADE, count, None, False, precision=0
        ),
        MetricDefinition("win_rate", "Win rate", MetricCategory.TRADE, percent, True, True),
        MetricDefinition("profit_factor", "Profit factor", MetricCategory.TRADE, ratio, True, True),
        MetricDefinition(
            "average_gross_exposure",
            "Average gross exposure",
            MetricCategory.EXPOSURE,
            percent,
            None,
            False,
        ),
        MetricDefinition(
            "maximum_gross_exposure",
            "Maximum gross exposure",
            MetricCategory.EXPOSURE,
            percent,
            False,
            False,
        ),
        MetricDefinition(
            "average_net_exposure",
            "Average net exposure",
            MetricCategory.EXPOSURE,
            percent,
            None,
            False,
        ),
        MetricDefinition(
            "total_fees", "Total fees", MetricCategory.COST, currency, False, False, precision=2
        ),
        MetricDefinition(
            "total_taxes", "Sell taxes", MetricCategory.COST, currency, False, False, precision=2
        ),
        MetricDefinition(
            "total_slippage_cost",
            "Slippage cost",
            MetricCategory.COST,
            currency,
            False,
            False,
            precision=2,
        ),
        MetricDefinition(
            "total_carry_cost",
            "Borrow and margin cost",
            MetricCategory.COST,
            currency,
            False,
            False,
            precision=2,
        ),
    )
    return MetricRegistry(REGISTRY_VERSION, definitions)
