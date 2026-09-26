"""Declared dependencies for adapters.outbound.llm_openai; exports use named modules."""

DEPENDS_ON: tuple[str, ...] = (
    "application.assistant_chat",
    "domain.assistant",
)
