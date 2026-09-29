"""Declared dependencies for adapters.outbound.research_sqlite.

`application.backtest_run`의 run 기록 포트와 값 타입, 요청 안 전략 spec 을 canonical·hydrate 로
옮기는 `domain.strategy`만 본다. 목록 페이지 값 타입은 `application.strategy_design`이 소유한다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "adapters.outbound.sqlite_store",
    "application.backtest_run",
    "application.strategy_design",
    "domain.backtest",
    "domain.strategy",
)
