"""Declared dependencies for adapters.outbound.engine_portfolio."""

# domain.backtest: 능력 판정이 읽는 참여율의 owner 가 `RunEnvironment` 다(P2-03).
DEPENDS_ON: tuple[str, ...] = (
    "application.portfolio_design",
    "domain.backtest",
    "domain.portfolio",
    "domain.strategy",
)
