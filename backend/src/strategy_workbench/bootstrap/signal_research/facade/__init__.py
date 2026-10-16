"""보존 DuckDB 입력만 명시적으로 조립하는 별도 composition root."""

DEPENDS_ON: tuple[str, ...] = (
    "application.portfolio_design",
    "application.signal_research",
    "adapters.inbound.signal_research_cli",
    "adapters.outbound.engine_portfolio",
    "adapters.outbound.equity_duckdb",
    "adapters.outbound.research_snapshot",
    "adapters.outbound.signal_pipeline",
    "domain.factor",
)
