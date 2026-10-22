"""Declared dependencies for adapters.inbound.http_api."""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "application.backtest_run",
    "application.broker_connection",
    "application.continuous_account",
    "application.equity_workspace",
    "application.experiment_run",
    "application.factor_library",
    "application.factor_research",
    "application.portfolio_design",
    "application.strategy_authoring",
    "application.strategy_design",
    "domain.assistant",
    "domain.backtest",
    "domain.equity",
    "domain.experiment",
    "domain.factor_library",
    "domain.paging",
    "domain.portfolio",
    "domain.strategy",
)
