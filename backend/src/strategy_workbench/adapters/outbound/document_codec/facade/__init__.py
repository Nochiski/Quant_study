"""Declared dependencies for adapters.outbound.document_codec."""

# domain.strategy: 1.0 → 1.1 업그레이드 step(UPGRADE_STEPS)을 round-trip CST에 그대로 적용하기 위해.
DEPENDS_ON: tuple[str, ...] = ("application.strategy_authoring", "domain.strategy")
