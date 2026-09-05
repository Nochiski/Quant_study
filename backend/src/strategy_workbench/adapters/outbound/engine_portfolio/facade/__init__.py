"""Declared dependencies for adapters.outbound.engine_portfolio."""

DEPENDS_ON: tuple[str, ...] = (
    "application.portfolio_design",
    "domain.portfolio",
    "domain.strategy",
)
