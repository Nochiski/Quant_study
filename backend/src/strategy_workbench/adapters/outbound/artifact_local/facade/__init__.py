"""Declared dependencies for adapters.outbound.artifact_local."""

DEPENDS_ON: tuple[str, ...] = (
    "application.backtest_run",
    "domain.backtest",
)
