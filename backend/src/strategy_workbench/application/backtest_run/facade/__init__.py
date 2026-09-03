"""Declared dependencies for application.backtest_run."""

DEPENDS_ON: tuple[str, ...] = (
    "application.portfolio_design",
    "domain.backtest",
    "domain.portfolio",
    "domain.strategy",
)
