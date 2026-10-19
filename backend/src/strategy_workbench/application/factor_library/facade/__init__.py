"""Declared dependencies for application.factor_library; exports use named modules."""

# domain.strategy: 저장 전 검증 문서의 schema_version 을 현재 판에서 읽는다.
DEPENDS_ON: tuple[str, ...] = ("domain.factor_library", "domain.strategy")
