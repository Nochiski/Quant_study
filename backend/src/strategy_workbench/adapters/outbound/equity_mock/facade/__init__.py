"""Declared dependencies for adapters.outbound.equity_mock; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.factor_research",
    "domain.equity",
    "domain.factor",
)
