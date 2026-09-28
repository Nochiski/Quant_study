"""Declared dependencies for adapters.outbound.document_codec."""

# domain.strategy: 은퇴 버전 업그레이드 체인(UPGRADE_STEPS, apply_upgrade_steps)을 round-trip CST에
# 그대로 적용하기 위해.
DEPENDS_ON: tuple[str, ...] = ("application.strategy_authoring", "domain.strategy")
