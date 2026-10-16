"""Declared dependencies for adapters.outbound.equity_duckdb; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.backtest_run",
    "application.factor_research",
    "application.portfolio_design",
    "application.signal_research",
    "domain.backtest",
    "domain.equity",
    "domain.factor",
    "domain.signal_evaluation",
)
