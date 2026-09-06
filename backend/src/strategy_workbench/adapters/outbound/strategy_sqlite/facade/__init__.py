"""Declared dependencies for adapters.outbound.strategy_sqlite."""

DEPENDS_ON: tuple[str, ...] = (
    "application.strategy_authoring",
    "application.strategy_design",
    "domain.strategy",
)
