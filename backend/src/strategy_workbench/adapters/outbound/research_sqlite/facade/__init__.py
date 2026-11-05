"""Declared dependencies for adapters.outbound.research_sqlite.

`application.backtest_run`의 run 기록 포트와 `application.experiment_run`의 실험 기록 포트, 그 값
타입(`domain.backtest`·`domain.experiment`), 요청 안 전략 spec 을 canonical·hydrate 로 옮기는
`domain.strategy`만 본다. 목록 페이지 값 타입은 `application.strategy_design`이 소유한다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "adapters.outbound.sqlite_store",
    "application.backtest_run",
    "application.experiment_run",
    "application.strategy_design",
    "domain.backtest",
    "domain.experiment",
    "domain.strategy",
)
