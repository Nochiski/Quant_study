"""연구 실행은 도메인 계약과 자체 outgoing port만 읽는다."""

DEPENDS_ON: tuple[str, ...] = (
    "domain.backtest",
    "domain.equity",
    "domain.portfolio",
    "domain.signal_evaluation",
    "domain.strategy",
)
