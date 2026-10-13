"""Declared dependencies for domain.experiment."""

# domain.strategy: 탐색 축이 읽는 파라미터 정의(`ParameterDefinition`)의 소유 노드.
# domain.backtest: 실행 요청·상태·시도 키·원장으로 trial 상태와 실험 통계를 파생한다.
# domain.analytics: 검증 곡선·세션 샤프·지표 레지스트리와 DSR이 기준을 바꿔 넣는 PSR.
DEPENDS_ON: tuple[str, ...] = ("domain.analytics", "domain.backtest", "domain.strategy")
