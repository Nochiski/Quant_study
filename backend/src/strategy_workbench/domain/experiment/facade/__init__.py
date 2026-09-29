"""Declared dependencies for domain.experiment."""

# domain.strategy: 탐색 축이 읽는 파라미터 정의(`ParameterDefinition`)의 소유 노드.
DEPENDS_ON: tuple[str, ...] = ("domain.strategy",)
