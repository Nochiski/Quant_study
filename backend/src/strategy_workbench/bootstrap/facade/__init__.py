"""Declared dependencies for bootstrap; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.equity_workspace",
    "application.backtest_run",
    "application.factor_research",
    "application.portfolio_design",
    "application.strategy_authoring",
    "application.strategy_design",
    "adapters.inbound.http_api",
    "adapters.outbound.artifact_local",
    "adapters.outbound.backtest_engine",
    "adapters.outbound.document_codec",
    "adapters.outbound.equity_duckdb",
    "adapters.outbound.equity_mock",
    "adapters.outbound.engine_portfolio",
    "adapters.outbound.strategy_sqlite",
    "domain.factor",
    "domain.analytics",
)
