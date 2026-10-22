"""Declared dependencies for adapters.outbound.secrets_local.

`application.assistant_chat`의 `ProviderSecretStore` 포트와 그 예외만 본다. 도메인 타입을 보지
않는 이유는 비밀이 도메인 값이 아니기 때문이다 — `ProviderProfile`에는 키가 없다(spec D2).
같은 파일에 키움 모의 자격증명도 둔다. 그 포트는 `application.broker_connection`이 소유한다
(S06 ②-2).
"""

DEPENDS_ON: tuple[str, ...] = ("application.assistant_chat", "application.broker_connection")
