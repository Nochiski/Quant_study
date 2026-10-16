"""Declared dependencies for adapters.outbound.backtest_engine."""

DEPENDS_ON: tuple[str, ...] = (
    "adapters.outbound.engine_portfolio",
    "application.backtest_run",
    "application.continuous_account",
    "domain.analytics",
    "domain.backtest",
    "domain.portfolio",
    "domain.strategy",
)
