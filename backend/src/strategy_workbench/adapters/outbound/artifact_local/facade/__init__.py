"""Declared dependencies for adapters.outbound.artifact_local."""

DEPENDS_ON: tuple[str, ...] = (
    "domain.canonical",
    "application.backtest_run",
    "domain.backtest",
)
