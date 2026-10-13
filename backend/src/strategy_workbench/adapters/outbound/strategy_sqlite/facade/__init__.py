"""Declared dependencies for adapters.outbound.strategy_sqlite."""

DEPENDS_ON: tuple[str, ...] = (
    "adapters.outbound.sqlite_store",
    "application.strategy_authoring",
    "application.strategy_design",
    "domain.strategy",
)
