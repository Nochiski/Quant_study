"""Declared dependencies for adapters.inbound.http_api."""

DEPENDS_ON: tuple[str, ...] = (
    "application.equity_workspace",
    "application.factor_research",
    "application.strategy_design",
    "domain.equity",
    "domain.strategy",
)
