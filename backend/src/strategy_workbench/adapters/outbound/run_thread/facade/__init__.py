"""스레드 전송 어댑터의 허용 의존성."""

DEPENDS_ON: tuple[str, ...] = ("application.backtest_run",)
