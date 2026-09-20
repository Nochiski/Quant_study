"""Declared dependencies for application.strategy_authoring; exports use named modules."""

# domain.backtest: 실행 설정 스키마(RunEnvironment)를 그 소유 노드에서 읽어 서빙한다(P2-01).
DEPENDS_ON: tuple[str, ...] = (
    "application.strategy_design",
    "domain.backtest",
    "domain.strategy",
)
