"""Declared dependencies for adapters.outbound.strategy_sqlite."""

DEPENDS_ON: tuple[str, ...] = (
    "domain.canonical",
    "adapters.outbound.sqlite_store",
    "application.factor_library",
    "application.strategy_authoring",
    "application.strategy_design",
    "domain.factor_library",
    "domain.strategy",
)
