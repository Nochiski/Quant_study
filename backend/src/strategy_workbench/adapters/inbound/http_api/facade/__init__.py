"""Declared dependencies for adapters.inbound.http_api."""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "application.backtest_run",
    "application.equity_workspace",
    "application.factor_research",
    "application.portfolio_design",
    "application.strategy_authoring",
    "application.strategy_design",
    "domain.assistant",
    "domain.equity",
    "domain.portfolio",
    "domain.strategy",
)
