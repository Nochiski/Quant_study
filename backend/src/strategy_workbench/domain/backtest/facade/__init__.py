"""Declared dependencies for domain.backtest."""

# domain.factor: RunEnvironment.missing 이 읽는 MissingPolicy 의 현재 소유 노드(P2-01).
DEPENDS_ON: tuple[str, ...] = (
    "domain.analytics",
    "domain.factor",
    "domain.strategy",
)
