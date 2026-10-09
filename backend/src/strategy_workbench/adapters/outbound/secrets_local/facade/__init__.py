"""Declared dependencies for adapters.outbound.secrets_local.

`application.assistant_chat`의 `ProviderSecretStore` 포트와 그 예외만 본다. 도메인 타입을 보지
않는 이유는 비밀이 도메인 값이 아니기 때문이다 — `ProviderProfile`에는 키가 없다(spec D2).
"""

DEPENDS_ON: tuple[str, ...] = ("application.assistant_chat",)
