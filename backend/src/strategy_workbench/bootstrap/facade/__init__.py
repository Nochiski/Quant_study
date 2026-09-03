"""Declared dependencies for bootstrap; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.equity_workspace",
    "application.strategy_design",
    "adapters.inbound.http_api",
    "adapters.outbound.equity_mock",
    "adapters.outbound.strategy_memory",
)
