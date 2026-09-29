"""Declared dependencies for domain.experiment."""

# domain.strategy: 탐색 축이 읽는 파라미터 정의(`ParameterDefinition`)의 소유 노드.
# domain.backtest: trial 상태를 파생하는 실행 상태(`RunStatus`)의 소유 노드.
# domain.analytics: 이어 붙인 검증 곡선의 점(`EquityCurvePoint`) 타입.
DEPENDS_ON: tuple[str, ...] = ("domain.analytics", "domain.backtest", "domain.strategy")
