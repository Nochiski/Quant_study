"""Declared dependencies for adapters.outbound.equity_mock; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.factor_research",
    "application.portfolio_design",
    "domain.equity",
    "domain.factor",
    "domain.portfolio",
)
