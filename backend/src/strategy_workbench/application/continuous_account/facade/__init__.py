"""연속 계좌 평가의 선언된 의존."""

DEPENDS_ON: tuple[str, ...] = (
    "application.backtest_run",
    "domain.backtest",
    "domain.analytics",
    "domain.canonical",
    "domain.experiment",
    "domain.portfolio",
    "domain.strategy",
)
