"""Declared dependencies for application.portfolio_design."""

DEPENDS_ON: tuple[str, ...] = (
    "application.factor_research",
    "application.strategy_design",
    "domain.equity",
    "domain.factor",
    "domain.portfolio",
    "domain.strategy",
)
