"""Declared dependencies for application.strategy_authoring; exports use named modules."""

# domain.backtest: 실행 설정 스키마(RunEnvironment)를 그 소유 노드에서 읽어 서빙한다(P2-01).
# domain.factor: 연산자 정의 카탈로그를 그 레지스트리에서 읽어 서빙한다(P1-03).
DEPENDS_ON: tuple[str, ...] = (
    "application.strategy_design",
    "domain.backtest",
    "domain.factor",
    "domain.strategy",
)
