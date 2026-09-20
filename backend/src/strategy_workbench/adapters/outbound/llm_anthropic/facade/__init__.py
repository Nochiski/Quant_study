"""Declared dependencies for adapters.outbound.llm_anthropic; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "domain.assistant",
)
