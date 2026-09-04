"""Declared dependencies for application.portfolio_design."""

DEPENDS_ON: tuple[str, ...] = (
    "domain.equity",
    "domain.factor",
    "domain.portfolio",
    "domain.strategy",
)
