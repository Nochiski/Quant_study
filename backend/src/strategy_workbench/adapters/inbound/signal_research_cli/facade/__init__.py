"""CLI는 입력 해석·출력 직렬화만 맡는다."""

DEPENDS_ON: tuple[str, ...] = (
    "application.signal_research",
    "domain.backtest",
    "domain.canonical",
    "domain.signal_evaluation",
    "domain.strategy",
)
