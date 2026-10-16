"""파이프라인 변환 어댑터의 허용 의존성."""

DEPENDS_ON: tuple[str, ...] = (
    "application.portfolio_design",
    "application.signal_research",
    "domain.backtest",
    "domain.strategy",
)
