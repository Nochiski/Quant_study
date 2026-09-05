"""Declared dependencies for adapters.outbound.equity_mock; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.backtest_run",
    "application.factor_research",
    "application.portfolio_design",
    "domain.backtest",
    "domain.equity",
    "domain.factor",
    "domain.portfolio",
)
