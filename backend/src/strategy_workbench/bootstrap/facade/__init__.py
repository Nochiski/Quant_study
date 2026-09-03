"""Declared dependencies for bootstrap; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.equity_workspace",
    "adapters.outbound.equity_mock",
)
